# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.preflight: everything that can be known to be wrong BEFORE the arm moves, checked before the arm moves.

WHY. Today the saved scan pose is first read in the middle of a cycle, with the cube already held (run_single_cube_cycle.py
reads HOME_FILE inside the placement and return steps); a missing pad template makes static_world_observation.py record
"pad_by_template": null and carry on; an unset camera id variable is dropped from the capture command without a word. Each
of those is a fact about files and configuration, available at start. run_preflight() evaluates ALL of them, every time,
for every action, and reports each with a stable reason code.

WHAT IT TOUCHES. The local filesystem (reads the saved pose and the template files; for the observation root one probe file
that is removed at once, and only when the directory already exists) and the local `git` binary (one check-ignore call).
Never a camera, never a motor, never a robot client.

ONE CHECK ASKS THE ROBOT COMPUTER, and only when the caller hands it something to ask with. bin/laptop/run-cycle.sh, the
launcher every recorded hardware run went through, refuses to run at all unless the safe-attach motor host is already up
and NOT in scan mode, and it establishes that in one ssh call before it commands anything. The runtime had no equivalent,
so a browser Start with no host running spawned a controller that failed at connect. run_preflight takes that check as an
optional `motor_host` probe: absent, the report is exactly what it always was; present, it adds the same question, asked
the same way. The probe READS a process list and an environment over ssh. It starts nothing, stops nothing, sends no
command to a motor, and `ssh` is given an argv, never a shell string.

THE SAME QUESTION, ASKED IN THE BACKGROUND, FOR THE SCREEN. A capability list is recomputed on every snapshot and an open
console asks for one every few seconds, so that remote check cannot be run there: a robot computer that is off would make
every poll wait out the ssh timeout. ReadinessWatch asks it on its own schedule instead and hands out the LAST answer with
its age, so a snapshot can say WHY a control is off (the computer could not be reached, no host is running, the host is in
scan mode) instead of showing it as ready and refusing only when it is pressed. An answer too old to stand for the robot
now is reported as no answer, never as the answer it was. It does not replace the check above: a submit asks again, fresh.

WHAT IT REPORTS. PreflightReport.to_dict() is safe for browser events: a detail names a config FIELD, a required file's
fixed public name, a joint name or an exception CLASS. It never contains a path, an address, an id or the text of the
requested action (the action can come from a browser, so echoing it back would be an injection surface).

WHAT IT DOES NOT OWN. Motion bounds, step limits and every physical gate stay in the controller. An action that passes here
is WELL FORMED and its inputs exist; it is not thereby safe to execute.
"""
import enum
import functools
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, Union

from .config import RuntimeConfig, config_digest, robot_link_is_local

REPORT_SCHEMA = "demo-runtime-preflight/1"
OK = "ok"

# Mirrors, pinned against their sources by tests/test_preflight.py. They are copied, not imported, because the sources pull
# in numpy and OpenCV at import time and a preflight must be able to run (and refuse) on a machine where those are broken.
SCAN_JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex")  # autonomy/perception/scan_poses.py
# The motor host, as bin/laptop/run-cycle.sh looks for it. The bracket is the usual pgrep trick: without it the pattern
# matches the very shell that carries it. KC_SCAN_MODE=1 is how bin/pi/start-host.sh marks a host whose gripper is limp
# and whose every gripper key is stripped, which is why the proven launcher refuses a cycle against one.
MOTOR_HOST_PATTERN = "[l]ekiwi_host_safe_attach.py"
SCAN_MODE_MARK = "KC_SCAN_MODE=1"
REQUIRED_TEMPLATE_FILES = ("template.json", "template.png", "valid.png")  # load_template() in pad_template_localizer.py; kept sorted
SAVED_POSE_KEY = "measured_joints_deg"
TEMPLATE_MATRIX_KEY, TEMPLATE_SIZE_KEY = "pad_to_raster", "pad_size_m"  # the first is read unconditionally by load_template()
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ActionKind(str, enum.Enum):
    """The typed allowlist: the action kinds run_single_cube_cycle.py dispatches on (--do). Pinned by a test."""
    OBSERVE = "observe"
    PAN = "pan"
    MOVE = "move"
    GRASP = "grasp"
    GRIP = "grip"
    BASE = "base"
    EXIT = "exit"
    CYCLE = "cycle"
    SCAN = "scan"  # scan_scene (overnight plan 2026-09-26, P0b): pan-only views from the low scan pose; no argument


_KIND_BY_NAME = {kind.value: kind for kind in ActionKind}
GRIP_CHOICES = ("latch", "open")
# How many finite numbers each kind takes after the colon. grip takes one of GRIP_CHOICES instead. The VALUES are not bounded
# here: the step limits belong to the controller and live next to the physics that justifies them.
_NUMERIC_ARITY = {ActionKind.OBSERVE: 0, ActionKind.PAN: 1, ActionKind.MOVE: 2, ActionKind.GRASP: 2, ActionKind.BASE: 1, ActionKind.EXIT: 0, ActionKind.CYCLE: 0, ActionKind.SCAN: 0}


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    values: Tuple = ()

    def to_text(self) -> str:
        return self.kind.value if not self.values else f"{self.kind.value}:{','.join(str(v) for v in self.values)}"


class ActionRefused(ValueError):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code, self.detail = reason_code, detail


def parse_action(text) -> Action:
    """'pan:-4.5' -> Action(PAN, (-4.5,)). Refusals carry a reason code and never repeat the text."""
    if isinstance(text, Action):
        text = text.to_text()  # a hand-built Action goes through the same gate as a string
    if not isinstance(text, str) or not text:
        raise ActionRefused("action_missing", "no action was requested")
    name, separator, argument = text.partition(":")
    kind = _KIND_BY_NAME.get(name)  # a lookup, not ActionKind(name): that ValueError would carry the raw text as __context__
    if kind is None:
        raise ActionRefused("action_not_allowed", f"the requested action is not one of {sorted(_KIND_BY_NAME)}")
    if separator and not argument:
        raise ActionRefused("action_argument_invalid", f"{kind.value} is followed by a colon and nothing after it")
    if kind is ActionKind.GRIP:
        if argument not in GRIP_CHOICES:
            raise ActionRefused("action_argument_invalid", f"grip takes one of {list(GRIP_CHOICES)}")
        return Action(kind, (argument,))
    arity = _NUMERIC_ARITY[kind]
    parts = argument.split(",") if argument else []
    if len(parts) != arity:
        raise ActionRefused("action_argument_invalid", f"{kind.value} takes {arity} number(s), {len(parts)} given")
    values = tuple(_finite_float(part) for part in parts)
    if any(value is None for value in values):
        raise ActionRefused("action_argument_invalid", f"{kind.value} takes {arity} finite number(s); an argument is not one")
    return Action(kind, values)


# float() alone is too generous for text that can come from a browser: it takes surrounding whitespace (a trailing newline
# included), "1_0", digits of any script, "nan" and "inf". A number here is plain ASCII decimal, optionally with an exponent.
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _finite_float(text: str) -> Optional[float]:
    if not _NUMBER.fullmatch(text):
        return None
    value = float(text)
    return value if math.isfinite(value) else None  # "1e999" fits the grammar and is inf


@dataclass(frozen=True)
class PreflightCheck:
    id: str
    ok: bool
    reason_code: str
    detail: str

    def to_dict(self) -> dict:
        return {"id": self.id, "ok": self.ok, "reason_code": self.reason_code, "detail": self.detail}


@dataclass(frozen=True)
class PreflightReport:
    ok: bool
    checks: Tuple[PreflightCheck, ...]
    action_kind: Optional[str]  # the enum value when the action parsed, else None; never the raw text
    config_digest: str

    def failed(self) -> Tuple[PreflightCheck, ...]:
        return tuple(check for check in self.checks if not check.ok)

    def reason_codes(self) -> Tuple[str, ...]:
        return tuple(check.reason_code for check in self.failed())

    def to_dict(self) -> dict:
        return {"schema": REPORT_SCHEMA, "ok": self.ok, "action_kind": self.action_kind, "config_digest": self.config_digest, "checks": [check.to_dict() for check in self.checks]}


class PreflightInputError(RuntimeError):
    """An input named by the config could not be digested. The message names the field, never the path."""


def _passed(check_id: str, detail: str) -> PreflightCheck:
    return PreflightCheck(check_id, True, OK, detail)


def _failed(check_id: str, reason_code: str, detail: str) -> PreflightCheck:
    return PreflightCheck(check_id, False, reason_code, detail)


# ---- saved pose -------------------------------------------------------------------------------------------------------

def _check_saved_pose(config: RuntimeConfig) -> PreflightCheck:
    path = os.fspath(config.saved_pose_file)
    if not os.path.isfile(path):
        return _failed("saved_pose", "saved_pose_missing", "saved_pose_file does not exist: the controller reads it in the middle of a cycle, with the cube held")
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as error:
        return _failed("saved_pose", "saved_pose_unreadable", f"saved_pose_file cannot be read ({type(error).__name__})")
    try:
        record = json.loads(text)
    except ValueError:
        return _failed("saved_pose", "saved_pose_not_json", "saved_pose_file is not valid JSON")
    joints = record.get(SAVED_POSE_KEY) if isinstance(record, dict) else None
    if not isinstance(joints, dict):
        return _failed("saved_pose", "saved_pose_joints_missing", f"saved_pose_file has no {SAVED_POSE_KEY} object")
    missing = [name for name in SCAN_JOINTS if name not in joints]
    if missing:
        return _failed("saved_pose", "saved_pose_joints_missing", f"{SAVED_POSE_KEY} lacks {missing}")
    bad = [name for name in SCAN_JOINTS if isinstance(joints[name], bool) or not isinstance(joints[name], (int, float)) or not math.isfinite(joints[name])]
    if bad:
        return _failed("saved_pose", "saved_pose_joint_not_numeric", f"{SAVED_POSE_KEY} is not a finite number for {bad}")
    return _passed("saved_pose", f"saved_pose_file has {SAVED_POSE_KEY} for {list(SCAN_JOINTS)}")


# ---- pad template -----------------------------------------------------------------------------------------------------

def _png_size(path: str):
    """(width, height) from the IHDR chunk, or None when the file is not a PNG. Eight signature bytes, then the first chunk
    is always IHDR: length(4) type(4) width(4) height(4). Reading 24 bytes keeps OpenCV out of the preflight."""
    with open(path, "rb") as handle:
        head = handle.read(24)
    if len(head) < 24 or head[:8] != _PNG_SIGNATURE or head[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return (width, height) if width and height else None


def _is_number(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _check_pad_template(config: RuntimeConfig) -> PreflightCheck:
    directory = os.fspath(config.pad_template_dir)
    if not os.path.isdir(directory):
        return _failed("pad_template", "pad_template_dir_missing", "pad_template_dir is not a directory: without it the pad is localized WITHOUT the template and nothing says so")
    missing = [name for name in REQUIRED_TEMPLATE_FILES if not os.path.isfile(os.path.join(directory, name))]
    if missing:
        return _failed("pad_template", "pad_template_file_missing", f"pad_template_dir lacks {missing}")
    try:
        with open(os.path.join(directory, "template.json"), encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, ValueError) as error:
        return _failed("pad_template", "pad_template_json_invalid", f"template.json cannot be parsed ({type(error).__name__})")
    matrix = meta.get(TEMPLATE_MATRIX_KEY) if isinstance(meta, dict) else None
    if not (isinstance(matrix, list) and len(matrix) == 3 and all(isinstance(row, list) and len(row) == 3 and all(_is_number(v) for v in row) for row in matrix)):
        return _failed("pad_template", "pad_template_json_invalid", f"template.json has no 3x3 numeric {TEMPLATE_MATRIX_KEY}: the localizer reads it unconditionally")
    size = meta.get(TEMPLATE_SIZE_KEY)
    if size is not None and not (isinstance(size, list) and len(size) == 2 and all(_is_number(v) and v > 0 for v in size)):
        return _failed("pad_template", "pad_template_json_invalid", f"template.json {TEMPLATE_SIZE_KEY} is not two positive numbers")
    try:
        sizes = {name: _png_size(os.path.join(directory, name)) for name in ("template.png", "valid.png")}
    except OSError as error:
        return _failed("pad_template", "pad_template_image_invalid", f"a template image cannot be read ({type(error).__name__})")
    not_png = sorted(name for name, size in sizes.items() if size is None)
    if not_png:
        return _failed("pad_template", "pad_template_image_invalid", f"not a PNG: {not_png}")
    if sizes["template.png"] != sizes["valid.png"]:
        return _failed("pad_template", "pad_template_size_mismatch", "template.png and valid.png differ in size: the localizer refuses that pair at the first capture")
    return _passed("pad_template", f"pad_template_dir has {list(REQUIRED_TEMPLATE_FILES)}; template.json parses; the two images are PNGs of one size (headers only, pixels are not decoded)")


# ---- observation root -------------------------------------------------------------------------------------------------

# A caller running under a git hook inherits these; they would make `git -C <repo>` answer for the hook's repository.
_GIT_LOCATION_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_PREFIX", "GIT_NAMESPACE")


def git_is_ignored(path: str, repo_root: str) -> bool:
    """Same question and same fail-closed reading of the exit status as observation_store._git_is_ignored."""
    env = {key: value for key, value in os.environ.items() if key not in _GIT_LOCATION_ENV}
    result = subprocess.run(["git", "-C", repo_root, "check-ignore", "-q", path], capture_output=True, env=env, timeout=15)
    if result.returncode not in (0, 1):
        raise OSError(f"git check-ignore failed with status {result.returncode}")
    return result.returncode == 0


def _nearest_existing_dir(path: str) -> str:
    while not os.path.isdir(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def enclosing_git_repo(real_path: str) -> Optional[str]:
    """The innermost directory at or above real_path that holds a .git entry (a directory, or the FILE a worktree has).
    Found by walking, not by asking git: `git rev-parse` answers 128 both for "not a repository" and for a repository it
    refuses to open, and telling those apart means parsing localized text."""
    current = _nearest_existing_dir(real_path)
    while True:
        if os.path.lexists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _check_observation_local_only(config: RuntimeConfig, is_ignored: Callable[[str, str], bool]) -> PreflightCheck:
    """ObservationStore.ensure_local_only, generalized from "the checkout the code lives in" to ANY repository: raw captures
    may only land outside every git repository or somewhere git ignores. By real path, so a symlink cannot stand in for an
    ignored or outside location. If git cannot answer, the root is refused."""
    real = os.path.realpath(os.fspath(config.observation_root))
    repo = enclosing_git_repo(real)
    if repo is None:
        return _passed("observation_root_local_only", "observation_root is outside every git repository")
    try:
        ignored = is_ignored(real, repo)
    except Exception as error:  # noqa: BLE001 -- fail closed, whatever went wrong
        return _failed("observation_root_local_only", "observation_root_git_unavailable", f"observation_root is inside a git repository and git could not say whether it is ignored ({type(error).__name__}); refused")
    if not ignored:
        return _failed("observation_root_local_only", "observation_root_not_local_only", "observation_root is inside a git repository and not ignored by git: raw captures must stay local")
    return _passed("observation_root_local_only", "observation_root is inside a git repository and ignored by it")


def _check_observation_writable(config: RuntimeConfig) -> PreflightCheck:
    root = os.fspath(config.observation_root)
    if os.path.lexists(root) and not os.path.isdir(root):
        return _failed("observation_root_writable", "observation_root_not_writable", "observation_root exists and is not a directory")
    if os.path.isdir(root):
        try:  # the honest test of "writable" is a write; the probe is removed at once and cannot be read as evidence
            with tempfile.NamedTemporaryFile(dir=root, prefix=".preflight-probe-"):
                pass
        except OSError as error:
            return _failed("observation_root_writable", "observation_root_not_writable", f"a probe file could not be created in observation_root ({type(error).__name__})")
        return _passed("observation_root_writable", "a probe file was created in observation_root and removed")
    parent = _nearest_existing_dir(root)  # the store creates the root on first use; a preflight leaves no directory behind
    if not os.access(parent, os.W_OK | os.X_OK):
        return _failed("observation_root_writable", "observation_root_not_writable", "observation_root does not exist and its nearest existing parent is not writable")
    return _passed("observation_root_writable", "observation_root does not exist yet; its nearest existing parent is writable (permission bits only, no probe)")


# ---- identities -------------------------------------------------------------------------------------------------------

# Today a camera id is pasted between single quotes into a shell command run on the Pi, and the ssh target is an argv entry of
# ssh (a leading "-" would be read as an option). None of the real values needs any of these characters.
_UNSAFE_CHARACTERS = frozenset("'\"`$\\;&|<>(){}*?!\n\r\t ")


def _identity_problem(value) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return "missing"
    if value.startswith("-") or any(ch in _UNSAFE_CHARACTERS or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return "unsafe"
    return None


def usable_ssh_target(value) -> bool:
    """Whether this value may be handed to ssh at all, by the same rule _check_identity applies. A caller that wants to
    ask the robot computer something asks this first, so it either asks with the configured target or does not ask."""
    return _identity_problem(value) is None


def _check_identity(check_id: str, field_name: str, value, why: str) -> PreflightCheck:
    problem = _identity_problem(value)
    if problem == "missing":
        return _failed(check_id, f"{check_id}_missing", f"{field_name} is empty: {why}")
    if problem == "unsafe":
        return _failed(check_id, f"{check_id}_unsafe", f"{field_name} starts with '-' or contains whitespace, a quote or a shell metacharacter; it is handed to ssh and to a remote shell")
    return _passed(check_id, f"{field_name} is set")


def _check_pi_ssh_target(config: RuntimeConfig) -> PreflightCheck:
    """The ssh target of the robot computer, needed by a DESKTOP runtime for every capture and every remote probe. A
    runtime ON THE ROBOT (cake.robot.link local) asks nothing over ssh: the controller captures through the camera
    service on this computer and reads its frames from the store here (the supervisor hands it the local mode), and the
    motor host is probed by local_runner. An empty target is then not a missing identity; a target that is set is still
    checked for safety, because it is handed to ssh wherever something does use it."""
    if robot_link_is_local(config) and _identity_problem(config.pi_ssh_target) == "missing":
        return _passed("pi_ssh_target", "pi_ssh_target is empty and cake.robot.link is local: captures and probes stay on this computer, nothing is asked over ssh")
    return _check_identity("pi_ssh_target", "pi_ssh_target", config.pi_ssh_target, "no capture can be taken")


def _check_front_camera(config: RuntimeConfig) -> PreflightCheck:
    if config.cameras.front_id is None:
        return _passed("front_camera_id", "cameras.front_id is null: the front camera is left out ON PURPOSE")
    if config.cameras.front_id == "":
        return _failed("front_camera_id", "front_camera_id_empty", "cameras.front_id is an empty string; use null to run without the front camera")
    return _check_identity("front_camera_id", "cameras.front_id", config.cameras.front_id, "")


# ---- the motor host on the robot computer -----------------------------------------------------------------------------

# The remote script, step for step the host preflight of bin/laptop/run-cycle.sh. It differs from that one in ONE way, on
# purpose: it reports what it found through its EXIT STATUS instead of a sentence, so this side names every reason from
# its own literals and never repeats text a remote machine sent back.
#   0 the host is up and not in scan mode   3 no host process   4 scan mode   5 a matched process could not be read
# THE READ IS THE TEST, exactly as in the launcher: the permission bits of an environment file can allow what the kernel
# then denies, so one process that cannot be read leaves the mode unknown and refuses the call instead of passing for
# "the mark is not set".
_MOTOR_HOST_SCRIPT = (
    "set -u\n"
    "pids=$(pgrep -f '" + MOTOR_HOST_PATTERN + "') || exit 3\n"
    "checked=0\n"
    "for pid in $pids; do\n"
    '  [ -e "/proc/$pid" ] || continue\n'
    "  if ! vars=$({ tr '\\0' '\\n' <\"/proc/$pid/environ\"; } 2>/dev/null); then exit 5; fi\n"
    "  checked=$((checked + 1))\n"
    '  echo "pid $pid"\n'
    "  if printf '%s\\n' \"$vars\" | grep -qx '" + SCAN_MODE_MARK + "'; then exit 4; fi\n"
    "done\n"
    '[ "$checked" -ge 1 ] || exit 3\n'
    "exit 0\n"
)
MOTOR_HOST_STATES = ("running_normal", "not_running", "scan_mode", "mode_unknown", "unreachable")
_MOTOR_HOST_BY_STATUS = {0: "running_normal", 3: "not_running", 4: "scan_mode", 5: "mode_unknown"}
SSH_CONNECT_TIMEOUT_S = 6  # the launcher's own; a robot computer that is off must not hold a submit for a minute
MOTOR_HOST_TIMEOUT_S = 20.0


def probe_motor_host(target: str, *, runner: Callable = subprocess.run, timeout_s: float = MOTOR_HOST_TIMEOUT_S) -> str:
    """Ask the robot computer whether the safe-attach motor host is up and out of scan mode. One of MOTOR_HOST_STATES.

    Anything that is not one of the script's own exit statuses (ssh could not connect, ssh is not installed, the call
    timed out) is 'unreachable': a connection that did not answer proves nothing, and the proven launcher refuses on it
    too. It NEVER raises, because a check that raised would be reported as check_error, which says less than a named
    reason. ssh is given an argv and the script arrives on stdin, so nothing here is a shell string."""
    return probe_motor_host_detail(target, runner=runner, timeout_s=timeout_s)[0]


def _host_pids(text) -> tuple:
    """The `pid <n>` lines the script prints for every host process whose environment it READ, as positive ints. A line
    of any other shape is ignored: the script prints nothing else, and a number this cannot read is not a pid."""
    pids = []
    for line in (text or "").splitlines() if isinstance(text, str) else ():
        name, _, value = line.strip().partition(" ")
        if name == "pid" and value.isdigit() and int(value) > 0:
            pids.append(int(value))
    return tuple(pids)


def probe_motor_host_detail(target: str, *, runner: Callable = subprocess.run, timeout_s: float = MOTOR_HOST_TIMEOUT_S) -> tuple:
    """(state, pids): the same question as probe_motor_host, plus the pid of every safe-attach host process whose
    environment the script read on the robot computer. The pids are what lets a reader tell whether the host that is
    running is the one the robot's Cake supervisor slot says it spawned; they are ROBOT pids, meaningful only there.
    Same argv, same script, same states; the pids are () whenever the state is not an answer."""
    argv = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT_S}", target, "bash -s"]
    try:
        result = runner(argv, input=_MOTOR_HOST_SCRIPT, capture_output=True, text=True, timeout=timeout_s)
    except Exception:  # noqa: BLE001 -- a missing ssh, a timeout, a refused key: none of them is an answer
        return "unreachable", ()
    state = _MOTOR_HOST_BY_STATUS.get(getattr(result, "returncode", None), "unreachable")
    return state, (_host_pids(getattr(result, "stdout", "")) if state != "unreachable" else ())


def local_runner(argv, *, runner: Callable = subprocess.run, **options):
    """The runner of probe_motor_host_detail for a runtime that runs ON THE ROBOT (cake.robot.link local).

    The same script and the same states; only the transport goes. The ssh argv that function builds ends in the remote
    command "bash -s" with the script on stdin, so this runs exactly that command HERE, with the same stdin, the same
    capture and the same timeout: nothing leaves the machine, no key is used, and the robot computer the script reads is
    the one this runtime runs on. An argv of any other shape is refused rather than run, so this can never become a
    general command runner."""
    if not (isinstance(argv, (list, tuple)) and len(argv) >= 2 and argv[0] == "ssh" and argv[-1] == "bash -s"):
        raise ValueError("the local runner runs only the motor host probe's own script")
    return runner(["bash", "-s"], **options)


def probe_motor_host_local(target: str = "", *, runner: Callable = subprocess.run, timeout_s: float = MOTOR_HOST_TIMEOUT_S) -> str:
    """probe_motor_host on THIS machine. `target` is accepted and not used: there is no ssh target to ask."""
    return probe_motor_host_detail_local(target, runner=runner, timeout_s=timeout_s)[0]


def probe_motor_host_detail_local(target: str = "", *, runner: Callable = subprocess.run, timeout_s: float = MOTOR_HOST_TIMEOUT_S) -> tuple:
    """probe_motor_host_detail on THIS machine, through local_runner: (state, pids), the same states and the same pids."""
    return probe_motor_host_detail(target or "local", runner=functools.partial(local_runner, runner=runner), timeout_s=timeout_s)


# A local probe asks even when pi_ssh_target names nothing: it has no target to ask through (see _check_motor_host).
probe_motor_host_local.asks_locally = True
probe_motor_host_detail_local.asks_locally = True


_MOTOR_HOST_REFUSALS = {
    "not_running": ("motor_host_not_running", "the safe-attach motor host is not running on the robot computer; the proven launcher refuses the same way, "
                                              "and a controller started now would fail at connect with the run already accepted"),
    "scan_mode": ("motor_host_in_scan_mode", "the motor host runs in scan mode, where the gripper is left limp and every gripper key is stripped from an action, "
                                             "so a grasp would close nothing"),
    "mode_unknown": ("motor_host_mode_unknown", "a process that names the motor host could not be read on the robot computer, so its mode is unknown; refused"),
    "unreachable": ("motor_host_unreachable", "the robot computer could not be asked whether the motor host is running; a connection that did not answer is not an answer"),
}


def _check_motor_host(config: RuntimeConfig, probe: Callable[[str], str]) -> PreflightCheck:
    """The one check that leaves this machine. With no usable ssh target it asks nothing and says so: inventing a target
    is worse than letting the controller's own connect fail, which is what happens today. pi_ssh_target already reports
    a missing or unsafe target as its own failure, so this check does not repeat it as a second one."""
    target = config.pi_ssh_target
    if _identity_problem(target) is not None and not getattr(probe, "asks_locally", False):
        return _passed("motor_host", "pi_ssh_target names nothing that can be asked, so the robot computer was not asked at all and the controller's own connect decides, as it does today")
    state = probe(target)
    if state == "running_normal":
        return _passed("motor_host", "the robot computer answered: the safe-attach motor host is running and not in scan mode (a process list and one environment were READ; nothing was started, stopped or commanded)")
    reason_code, detail = _MOTOR_HOST_REFUSALS.get(state, ("motor_host_unreachable", "the motor host probe answered with no state this module knows; refused"))
    return _failed("motor_host", reason_code, detail)


# ---- the cached reading of whether the ROBOT is ready -------------------------------------------------------------------

# WHY A BACKGROUND READING AND NOT A CHECK ON THE REQUEST PATH. The capability list is computed on every snapshot and an
# open console asks for a snapshot every five seconds. The question above cannot be asked there: an ssh that has to time
# out against a robot computer that is off would stall every one of those polls for as long as it takes ssh to give up,
# and every open console with it. So the question is asked on its OWN schedule, by a thread nothing ever waits for, and a
# snapshot is served the LAST answer together with how old that answer is.
#
# WHAT THE AGE IS FOR. An answer older than READINESS_MAX_AGE_S is reported as its own state and never as the answer it
# used to be. Staleness must not read as readiness, and this is also what a probe that hangs looks like from here: no new
# answer arrives, the last one ages out, and the runtime keeps answering instead of blocking on a call that never returns.
#
# WHAT THIS IS NOT. It is not a substitute for the check a submit runs. A snapshot may act on an answer a few seconds old
# because all it does is draw a control; starting a robot may not. The submit path asks again, fresh, through
# _check_motor_host, and nothing below is consulted there.
ROBOT_READY = "running_normal"  # the one state of MOTOR_HOST_STATES on which a command may be offered
NEVER_READ, READING_STALE = "never_read", "reading_stale"
READINESS_STATES = MOTOR_HOST_STATES + (NEVER_READ, READING_STALE)
READINESS_INTERVAL_S = 5.0  # between readings. How long ONE reading may take is the probe's own timeout, not this
READINESS_MAX_AGE_S = 30.0  # older than this and a reading says nothing about the robot now
READINESS_JOIN_S = 1.0  # how long a shutdown waits for the reader. A probe mid-ssh is left to its own timeout instead

# The two states that are not answers: this runtime has nothing to report, which is a different fact from every entry of
# _MOTOR_HOST_REFUSALS. Those are things the robot computer SAID; these two are things nobody said. A copy is taken so the
# refusals of the submit path keep exactly the four members its own test pins them to.
_READINESS_REASONS = dict(_MOTOR_HOST_REFUSALS)
_READINESS_REASONS[NEVER_READ] = ("robot_readiness_not_read", "the robot computer has not answered yet in this runtime session, so nothing here knows whether its motor host "
                                                              "is running; a control that would command the robot stays off until the first reading arrives")
_READINESS_REASONS[READING_STALE] = ("robot_readiness_stale", "the last answer from the robot computer is too old to stand for the robot now, so it is reported as no answer "
                                                              "at all rather than as the answer it was; an old reading is never shown as readiness")
_READY_DETAIL = ("the robot computer answered that the safe-attach motor host is running and not in scan mode. That is a reading taken in the background and it is NOT what a "
                 "start rests on: a submit asks the same question again, fresh, and the host can still refuse to attach the arm for reasons no reading from here can see")


@dataclass(frozen=True)
class RobotReadiness:
    """One reading about the robot computer: what was found, how old the finding is, and the sentence that says it.

    ready is true for exactly ONE state, and it says that the motor host was running normally when the reading was taken.
    It does not say that a command would be accepted: bin/pi/lekiwi_host_safe_attach.py refuses attachment when torque is
    off or when a joint's goal and present positions are further apart than its own tolerance, and neither of those is
    visible from here. A power cycle alone puts a robot in exactly that state with its host perfectly healthy."""
    state: str
    age_ms: Optional[float]
    reason_code: Optional[str]
    detail: str

    @property
    def ready(self) -> bool:
        return self.state == ROBOT_READY

    def to_dict(self) -> dict:
        return {"state": self.state, "ready": self.ready, "age_ms": self.age_ms, "reason_code": self.reason_code, "detail": self.detail}


def _age_phrase(age_ms: Optional[float]) -> str:
    """Whole seconds, on purpose: a figure that moved every hundred milliseconds would rewrite the sentence of every
    snapshot and say nothing more than this does."""
    return "" if age_ms is None else f"; this rests on a reading taken {int(age_ms // 1000)} s ago"


def readiness_of(state: str, age_ms: Optional[float]) -> RobotReadiness:
    """One reading, named. A state this module has never heard of is reported as unreachable, never as an answer: the
    passing state is spelled out and everything else has to be in the table to be reported as itself."""
    if state != ROBOT_READY and state not in _READINESS_REASONS:
        state = "unreachable"
    reason_code, base = (None, _READY_DETAIL) if state == ROBOT_READY else _READINESS_REASONS[state]
    return RobotReadiness(state=state, age_ms=age_ms, reason_code=reason_code, detail=base + _age_phrase(age_ms))


class ReadinessWatch:
    """Runs a probe on its own schedule and hands out the last answer. reading() NEVER probes and never waits for one.

    The thread is a daemon and holds no lock while it probes, so the only thing reading() ever waits for is the handful
    of instructions that copy two fields. A probe that hangs therefore costs a stale reading, which is reported as such,
    and never a snapshot that does not come back."""

    def __init__(self, target: str, *, probe: Callable[[str], str] = probe_motor_host, interval_s: float = READINESS_INTERVAL_S,
                 max_age_s: float = READINESS_MAX_AGE_S, clock: Callable[[], float] = time.monotonic):
        self._target, self._probe = target, probe
        self._interval, self._max_age, self._clock = interval_s, max_age_s, clock
        self._lock = threading.Lock()
        self._state: Optional[str] = None
        self._pids: tuple = ()
        self._taken: Optional[float] = None
        self.readings = 0
        self._halt = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "ReadinessWatch":
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="robot-readiness", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        """Ask the reader to end and wait a moment for it. A reader that is inside an ssh is NOT waited out: it is a
        daemon thread whose probe has its own timeout, and a shutdown that took the ssh timeout to happen would be a
        worse thing than a thread that ends a few seconds after the process stopped using it."""
        self._halt.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=READINESS_JOIN_S)

    def _loop(self) -> None:
        while True:
            self.read_once()
            if self._halt.wait(self._interval):
                return

    def read_once(self) -> str:
        """One probe, stored with the time it was taken. It never raises: probe_motor_host does not, and a probe handed
        in by a caller might, in which case there is no answer and 'unreachable' is what no answer means here."""
        try:
            answer = self._probe(self._target)
        except Exception:  # noqa: BLE001 -- whatever went wrong, a probe that did not answer is not an answer
            answer = "unreachable"
        # probe_motor_host answers a state; probe_motor_host_detail answers (state, pids). Both are accepted, so the
        # pids are there for a reader that asks for them and nothing changes for one that does not.
        state, pids = (answer[0], tuple(answer[1])) if isinstance(answer, tuple) and len(answer) == 2 else (answer, ())
        with self._lock:
            self._state, self._pids, self._taken, self.readings = state, pids, self._clock(), self.readings + 1
        return state

    def host_pids(self) -> Optional[tuple]:
        """The robot-side pids the last reading found, or None when there is no CURRENT reading to take them from: never
        read, or older than the bound. A stale list of pids is not evidence about the host now."""
        with self._lock:
            pids, taken = self._pids, self._taken
        if taken is None or (self._clock() - taken) > self._max_age:
            return None
        return pids

    def reading(self) -> RobotReadiness:
        with self._lock:
            state, taken = self._state, self._taken
        if state is None or taken is None:
            return readiness_of(NEVER_READ, None)
        age_ms = max(0.0, self._clock() - taken) * 1000.0
        return readiness_of(READING_STALE if age_ms > self._max_age * 1000.0 else state, age_ms)


def _check_action(action) -> Tuple[PreflightCheck, Optional[Action]]:
    try:
        parsed = parse_action(action)
    except ActionRefused as refusal:
        return _failed("action", refusal.reason_code, refusal.detail), None
    return _passed("action", f"{parsed.kind.value} is an allowed action and its arguments are well formed (bounds are the controller's)"), parsed


def run_preflight(config: RuntimeConfig, action: Union[str, Action], *, is_ignored: Callable[[str, str], bool] = git_is_ignored,
                  motor_host: Optional[Callable[[str], str]] = None) -> PreflightReport:
    """Every check, always, in a fixed order; none stops the others, so one report lists everything that is wrong. A check
    that raises is a FAILED check (reason check_error), never a skipped one.

    motor_host is the probe of the check that asks the robot computer (probe_motor_host, or a test's own). Without one
    the report is exactly the local report it always was, and nothing leaves this machine: the caller decides when a
    remote question is worth asking, because this function is also read by every snapshot."""
    parsed_action = [None]

    def action_check() -> PreflightCheck:
        check, parsed_action[0] = _check_action(action)
        return check

    remote = (("motor_host", lambda: _check_motor_host(config, motor_host)),) if motor_host is not None else ()
    plan = (
        ("saved_pose", lambda: _check_saved_pose(config)),
        ("pad_template", lambda: _check_pad_template(config)),
        ("observation_root_writable", lambda: _check_observation_writable(config)),
        ("observation_root_local_only", lambda: _check_observation_local_only(config, is_ignored)),
        ("wrist_camera_id", lambda: _check_identity("wrist_camera_id", "cameras.wrist_id", config.cameras.wrist_id, "today an unset id is dropped from the capture command silently")),
        ("front_camera_id", lambda: _check_front_camera(config)),
        ("host_address", lambda: _check_identity("host_address", "host.address", config.host.address, "the robot client cannot connect")),
        ("robot_id", lambda: _check_identity("robot_id", "host.robot_id", config.host.robot_id, "the robot client cannot select its calibration")),
        ("pi_ssh_target", lambda: _check_pi_ssh_target(config)),
        *remote,
        ("model_identity", lambda: _failed("model_identity", "model_identity_missing", "model.identity is empty: a run must say which detector produced its perception") if not config.model.identity
            else _passed("model_identity", "model.identity is set")),
        ("action", action_check),
    )
    checks = []
    for check_id, run in plan:
        try:
            checks.append(run())
        except Exception as error:  # noqa: BLE001 -- an exception text can carry a path: only its class is reported
            checks.append(_failed(check_id, "check_error", f"the check itself failed ({type(error).__name__}); treated as not passed"))
    return PreflightReport(ok=all(check.ok for check in checks), checks=tuple(checks), action_kind=parsed_action[0].kind.value if parsed_action[0] else None, config_digest=config_digest(config))


# ---- input digests ----------------------------------------------------------------------------------------------------

def _read_input(path: str, field_name: str) -> bytes:
    failure = None
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as error:
        failure = type(error).__name__
    raise PreflightInputError(f"{field_name} cannot be read for its digest ({failure})")  # raised outside the except block: the OSError names the path


def input_digests(config: RuntimeConfig) -> dict:
    """What the run bundle records about the two inputs that shape a run: the saved pose, and the pad template as ONE digest
    over its required files in sorted name order. Each file is framed as name, NUL, 8-byte big-endian length, content, so no
    two different sets of files share a byte stream. A missing input RAISES: a bundle must not carry a digest of nothing."""
    template = hashlib.sha256()
    for name in sorted(REQUIRED_TEMPLATE_FILES):
        content = _read_input(os.path.join(os.fspath(config.pad_template_dir), name), f"pad_template_dir/{name}")
        template.update(name.encode("ascii") + b"\0" + struct.pack(">Q", len(content)) + content)
    return {"saved_pose_sha256": hashlib.sha256(_read_input(os.fspath(config.saved_pose_file), "saved_pose_file")).hexdigest(), "pad_template_sha256": template.hexdigest()}
