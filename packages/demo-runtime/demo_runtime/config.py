# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.config: the explicit runtime configuration of the demo.

WHY THIS EXISTS. The controller and its perception tools resolve every piece of mutable machine data relative to their own
__file__ (<repo>/.local-dev/...): the saved scan pose, the pad template, the observation store. The robot address, the robot
id and the ssh target arrive as CLI arguments, the camera ids as environment variables read at capture time. Nothing states
the whole set in one place, so nothing can check it before the arm moves and nothing can say which inputs a run used.

RuntimeConfig is that one place. It is loaded from a PRIVATE JSON file whose path comes from --config or from the
KC_RUNTIME_CONFIG environment variable. There is deliberately NO default path inside the repository: the file names a
robot, an address and device ids, and none of those may ever be committed (AGENTS.md).

WHAT IS PROTECTED, AND WHAT IS NOT.
  - repr()/str() of the config and of its sections never show the host address, the robot id, the ssh target, a camera id
    or a path (a path names a home directory, so it is treated the same way);
  - every ConfigError names a FIELD or a SOURCE ("--config", the environment variable), never a value and never the file's
    own path, and carries no underlying exception, neither as __cause__ nor as __context__ (json.JSONDecodeError keeps the
    whole document in .doc);
  - sanitized_summary() is the only form meant for browser events and exports;
  - config_digest() covers the FULL config, so a changed identity is detectable without being revealed.
  NOT protected: the attributes themselves. They are plain values because the consumers need them; vars(), to_private_dict()
  and dataclasses.asdict() show everything and must never be logged. The digest is UNSALTED: someone who already knows
  every other field could search a low-entropy field (an address inside a known /24) against it.

This module validates STRUCTURE only (types, enum choices, port ranges, an absolute data root). Whether the identities are
PRESENT and the files EXIST is the job of demo_runtime.preflight, which reports each problem with a reason code instead of
failing on the first one.

THE EXECUTION BACKEND is the one switch here that decides how a run reaches the robot, so it is spelled out rather than
inferred. baseline_normal_host runs the proven controller against the motor host in its NORMAL mode, exactly as
bin/laptop/run-cycle.sh has run it on hardware, and takes no lease of any kind. boundary_lease_host is the deferred path:
it needs the robot computer to run the host boundary, and the runtime takes that boundary's command lease before it
starts anything. A configuration that names neither gets the baseline, because that is the path with a record on
hardware; the runtime says which one it is using instead of leaving it to be read out of a missing key.
"""
import enum
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

CONFIG_ENV_VAR = "KC_RUNTIME_CONFIG"
CONFIG_SCHEMA = "demo-runtime-config/1"
SUMMARY_SCHEMA = "demo-runtime-config-summary/1"
REDACTED, UNSET = "<redacted>", "<unset>"
LAYOUT_EXPLICIT, LAYOUT_LEGACY = "explicit", "legacy"

# The LeRobot host's documented defaults (docs/diagnosing-a-run.md names TCP 5555/5556). A config may override them.
DEFAULT_CMD_PORT, DEFAULT_OBS_PORT = 5555, 5556
# bin/pi/camera_service.py's mission socket. It is not configurable from here, so it is named here only to be REFUSED
# as a preview or tap port: a hand-edited file that picks it would take the socket the controller's captures go through.
CAMERA_SERVICE_MISSION_PORT = 5570
# The robot computer's OWN fixed service ports: the host's command and observation sockets, the host boundary's control and
# tap sockets, the camera service's mission and preview sockets. The desktop perception service's robot-side port (the
# listener of its reverse forward) may be none of them, because a listener there would stand in for that service.
# demo_runtime.forwards.ROBOT_SERVICE_PORTS is the same set; tests/test_forwards.py keeps the two equal (this module does
# not import that one: forwards imports the read side of the runtime, and a configuration must load without it).
ROBOT_FIXED_SERVICE_PORTS = (5555, 5556, 5557, 5558, 5570, 5571, 5572)
# The longest the robot waits for one desktop perception answer. A bound on a typo, not a qualified value: the deadline
# itself has NO default and is the operator's, set from the measured round trip (bin/pi/offboard_perception_probe.py).
PERCEPTION_DEADLINE_MAX_S = 600.0

# legacy_layout: exactly where today's code looks, relative to the repository root. Pinned against the sources by
# tests/test_config.py, so a moved constant in the controller fails a test here instead of silently splitting the layouts.
LEGACY_DATA_DIR = ".local-dev"
LEGACY_SAVED_POSE = ("calibration", "scan-home-current.json")  # HOME_FILE in run_single_cube_cycle.py and run_stage1_scan.py
LEGACY_PAD_TEMPLATE = ("calibration", "pad-template")  # TEMPLATE_DIR in tools/static_world_observation.py
LEGACY_OBSERVATIONS = ("observations",)  # DEFAULT_ROOT in autonomy/perception/observation_store.py
LEGACY_MODEL_IDENTITY = "google/owlv2-base-patch16-ensemble"  # MODEL_NAME in autonomy/perception/zero_shot_detector.py
# The environment variables today's code reads for identities (run_stage1_scan.py: --pi-ssh default, PiCamera.capture).
LEGACY_ENV_PI_SSH, LEGACY_ENV_WRIST_ID, LEGACY_ENV_FRONT_ID = "KC_PI_SSH", "KC_WRIST_CAMERA_ID", "KC_FRONT_CAMERA_ID"


class ConfigError(ValueError):
    """The runtime config could not be located, read or validated. The message never contains a config value."""


class CaptureProvider(str, enum.Enum):
    LEGACY_PER_CAPTURE = "legacy_per_capture"  # today: one ssh + one camera lifecycle per frame (PiCamera)
    CAMERA_SERVICE = "camera_service"


_PROVIDER_BY_VALUE = {choice.value: choice for choice in CaptureProvider}


class ExecutionBackend(str, enum.Enum):
    """HOW a run reaches the robot. The two values have to be told apart at a glance, in a log line and on a screen,
    because reading one for the other would mean reading "no lease is taken here" as "a lease was taken". So they begin
    with different words, neither is a prefix or a substring of the other, the word that decides their meaning (lease)
    is in one of them and nowhere in the other, and neither is spelled as a negation of the other: a reader who skims
    past a "no" would take the opposite of what happened. A test pins all four of those rules."""
    BASELINE_NORMAL_HOST = "baseline_normal_host"  # the proven path: the NORMAL motor host, no lease anywhere
    BOUNDARY_LEASE_HOST = "boundary_lease_host"  # deferred: the host boundary, and its command lease on every action


_BACKEND_BY_VALUE = {choice.value: choice for choice in ExecutionBackend}
DEFAULT_EXECUTION_BACKEND = ExecutionBackend.BASELINE_NORMAL_HOST


def _mask(value) -> str:
    """Whether a sensitive value is set is a boolean and safe to show; the value is not."""
    return REDACTED if value not in (None, "") else UNSET


# An ALLOWLIST of shapes, not a list of path shapes to refuse: what passes here is shown to a browser. A hub name is "name" or
# "org/name"; a revision is a commit hash, a tag or a branch ("refs/pr/7"). Everything else an operator can put in these
# fields is withheld: a local checkpoint path (it names a home directory), the URL of a served model (an address and a port),
# a URL that carries a credential. A dotted quad is withheld although it fits the hub alphabet.
_HUB_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,95})?")
_HUB_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}")
_DOTTED_QUAD = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
# A candidate key and a capability name are chosen by a deployment and travel to a browser, so their alphabet is an
# allowlist: no separator, no space, nothing that could read as a path. A slot uuid and a capsule digest are the
# resident's own hex identities and are compared against what it prints.
_CANDIDATE_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_HEX_IDENTITY = re.compile(r"[0-9a-fA-F]{8,128}")


def public_model_field(value: Optional[str], *, revision: bool = False) -> Optional[str]:
    """The model identity is the one identity the summary may show, and only while it has the shape of a public hub name
    (see above). None means "nothing to show": unset, or withheld."""
    if not isinstance(value, str) or not value:
        return None
    shape = _HUB_REVISION if revision else _HUB_NAME
    return value if shape.fullmatch(value) and ".." not in value and not _DOTTED_QUAD.search(value) else None


def _show_model(value: Optional[str], *, revision: bool = False) -> str:
    shown = public_model_field(value, revision=revision)
    return repr(shown) if shown is not None else _mask(value)


@dataclass(frozen=True, repr=False)
class HostConfig:
    address: str = ""
    robot_id: str = ""
    cmd_port: int = DEFAULT_CMD_PORT
    obs_port: int = DEFAULT_OBS_PORT

    def __repr__(self) -> str:
        return f"HostConfig(address={_mask(self.address)}, robot_id={_mask(self.robot_id)}, cmd_port={self.cmd_port}, obs_port={self.obs_port})"


@dataclass(frozen=True, repr=False)
class CameraConfig:
    wrist_id: str = ""
    front_id: Optional[str] = None  # null is the EXPLICIT way to run without the front camera; today an unset variable omits it silently

    def __repr__(self) -> str:
        return f"CameraConfig(wrist_id={_mask(self.wrist_id)}, front_id={_mask(self.front_id)})"


@dataclass(frozen=True, repr=False)
class ModelConfig:
    identity: str = ""
    revision: Optional[str] = None

    def __repr__(self) -> str:
        return f"ModelConfig(identity={_show_model(self.identity)}, revision={_show_model(self.revision, revision=True)})"


@dataclass(frozen=True, repr=False)
class TapConfig:
    enabled: bool = False
    port: int = 0

    def __repr__(self) -> str:  # the port is an endpoint: shown to nobody
        return f"TapConfig(enabled={self.enabled}, port={_mask(self.port or None)})"


@dataclass(frozen=True, repr=False)
class PreviewConfig:
    """Where each camera role's preview is published, named ONE ROLE AT A TIME.

    There is no offset arithmetic here, on purpose. The endpoint used to be derived by adding a per-role offset to one
    number, while an explicitly given port skipped the offset entirely, so both roles resolved to the SAME endpoint and
    one port silently served two cameras. A role that is not named here has no preview, which is a state a screen can
    say out loud; a role that is named carries its own port and nothing computes a neighbour's from it.

    It is INDEPENDENT of the tap. A preview is a camera picture, the tap is the host's observation stream, and a
    deployment may want either, both or neither: the baseline NORMAL host supports preview with no tap at all.
    """

    enabled: bool = False
    wrist_port: Optional[int] = None
    front_port: Optional[int] = None

    def port_for(self, role: str) -> Optional[int]:
        # An absent port parses to 0, so "no preview for this role" is normalised to one value: None.
        port = {"wrist": self.wrist_port, "front": self.front_port}.get(role)
        return port if port else None

    def roles(self) -> tuple:
        """The roles this configuration actually names, in the order a screen reads them."""
        return tuple(role for role in ("wrist", "front") if self.port_for(role))

    def __repr__(self) -> str:  # ports are endpoints: shown to nobody
        return (f"PreviewConfig(enabled={self.enabled}, wrist_port={_mask(self.wrist_port or None)}, "
                f"front_port={_mask(self.front_port or None)})")


@dataclass(frozen=True, repr=False)
class CakeCapabilityBinding:
    """WHICH slot decides that a mission's capability is available, and on WHICH capsule identity.

    Both halves are needed and neither is derivable. The deployment pre-admits the active build in a HOLDER slot so the
    update engine has an admitted mapping to activate onto, so the capsule is present somewhere before anything is
    activated; and the capability slot is reported ACTIVE while it still runs the inert build, so a lifecycle word on
    its own says nothing either. Availability is bound to ONE slot and ONE capsule, which is the rule
    autonomy/motion/cake_capability_adapter.py states and has a test per near-miss for.

    The values come from `demo-plan build-mobile`, which prints the slot uuids and the capsule digests of the
    deployment it prepares. They are deployment identities and live in the PRIVATE runtime config with everything else
    that names a machine.
    """

    name: str
    slot_uuid: str
    active_capsule: str


# How this runtime reaches the robot's resident. ssh_forward is the desktop deployment: an ssh unix-socket forward this
# runtime owns (demo_runtime.forwards.UnixSocketForward). local is the ROBOT-SIDE deployment: this runtime runs on the
# robot itself, beside the resident, and reads its admin socket directly (robot_link.LocalRobotLink); there is no forward
# and so no remote path. Absent means ssh_forward, which is what every configuration written before this key meant.
ROBOT_LINK_SSH_FORWARD, ROBOT_LINK_LOCAL = "ssh_forward", "local"
ROBOT_LINKS = (ROBOT_LINK_SSH_FORWARD, ROBOT_LINK_LOCAL)

# The one execution a LIVE mission may be built with, named by mission.execution. ABSENT MEANS UNSET, and unset is a
# runtime that answers mission_unavailable for every mission op, exactly as before this key existed. It is written by an
# operator after the attended checks, never by a default (demo_runtime.mission_service.build_hardware_mission_coordinator).
MISSION_EXECUTION_PROVEN = "proven_controller"
MISSION_EXECUTIONS = (MISSION_EXECUTION_PROVEN,)


@dataclass(frozen=True)
class MissionRadii:
    """The OPTIONAL `mission.radii` section: the claim and match radii of target binding, in millimetres, and the BASIS
    stated for them. There is NO default for any of the three. A radius is a physical seam (whether a cube read again is
    the one an earlier target placed, whether a cube is the one a target was bound to), and a value nobody justified must
    not become the value a mission runs with because it was written first. Absent: None, and whatever consumes the radii
    (autonomy/manipulation/target_binding.py through the mission service) has to treat the seam as unavailable.

    The section is either the numeric object {r_claim_mm, r_match_mm, basis}, whose basis is the operator's own statement
    of where the qualification is written down, or the one word RADII_CUBE_HALF_EDGE ("cube_half_edge"): the radii the
    cubes' own geometry fixes, half a cube edge for both, resolved through target_binding.radii_from_cube_geometry so the
    numbers and the basis have exactly one spelling (target_binding, THE TWO RADII, THE DERIVED VALUE, says what the
    recorded evidence is). Either way the resolved numbers and basis enter config_digest. This module checks SHAPE only."""

    r_claim_mm: float
    r_match_mm: float
    basis: str


@dataclass(frozen=True, repr=False)
class MissionConfig:
    """The OPTIONAL `mission` section: what a LIVE mission would run with, and nothing that turns one on by itself.

    `execution` is None (unset) or "proven_controller". The two endpoints are where the Cake-launched mobile_approach
    capability publishes and subscribes (bin/templates/run-capability.sh.in: <run dir>/approach-observations.ipc and
    <run dir>/approach-commands.ipc on the robot). They are handed to a capability target's controller through its mission
    target file; they are absolute ipc:// endpoints and must differ. `radii` is MissionRadii or None (absent).

    `retry` switches the Retry of a held LIVE mission on, and ships OFF. A Retry parks the arm (`--do exit`) and starts a
    fresh `--do cycle`, which is motion that has no qualification record: a LIVE runtime offers it only when this is true
    AND the configuration names the attended label (placement_envelope_attended), exactly as a LIVE run naming an
    unqualified envelope word needs that label. Without both, a LIVE hold offers only the Stop and the supervisor refuses
    the park (demo_runtime.supervisor.Supervisor.mission_retry_refusal). A synthetic runtime is not held to it."""

    execution: Optional[str] = None
    capability_observation_endpoint: Optional[str] = None
    capability_command_endpoint: Optional[str] = None
    radii: Optional[MissionRadii] = None
    retry: bool = False


@dataclass(frozen=True, repr=False)
class RemotePerceptionConfig:
    """`perception.remote`: the ONE offboard exception, as the robot-side controller uses it. Every field is REQUIRED and
    none has a default; the whole section is off when it is absent, and then perception is the controller's own, as before.

      port                the robot-side loopback port the desktop's reverse forward listens on (never a robot service port)
      deadline_s          the robot's own bound on one answer, on its monotonic clock: a missing, late or mismatched answer
                          holds the arm (autonomy/perception/remote_perception.py)
      model_revision      the model snapshot the answer must name: 40 lowercase hex characters, the hub commit
      model_files_sha256  {file name: sha256} of that snapshot; the controller receives its digest (model_files_digest)

    Consumed only by a LIVE runtime whose robot link is local: the supervisor hands these to the controller's environment
    then and never otherwise (demo_runtime.supervisor._child_environment)."""

    port: int
    deadline_s: float
    model_revision: str
    model_files_sha256: Mapping[str, str]

    def __repr__(self) -> str:  # the port is an endpoint: shown to nobody
        return (f"RemotePerceptionConfig(port={_mask(self.port or None)}, deadline_s={self.deadline_s!r}, "
                f"model_revision={_show_model(self.model_revision, revision=True)}, model_files={len(self.model_files_sha256)})")


@dataclass(frozen=True, repr=False)
class PerceptionConfig:
    """The OPTIONAL `perception` section. It holds one thing today, `remote`, which is required when the section is present."""

    remote: RemotePerceptionConfig

    def __repr__(self) -> str:
        return f"PerceptionConfig(remote={self.remote!r})"


@dataclass(frozen=True, repr=False)
class CakeRobotConfig:
    """The ROBOT's resident, reached through an ssh unix-socket forward this runtime owns.

    The resident runs ON THE ROBOT as a systemd user unit and binds its admin socket there, mode 0600, for its own user.
    This runtime reaches it the only way that keeps that socket off every network: `ssh -N -L <local>:<remote>` to the
    configured pi_ssh_target, owned and restarted by demo_runtime.forwards.UnixSocketForward. `local_admin_socket_path`
    is where that forward binds on THIS machine and is what cake_admin and cake_activation are pointed at;
    `remote_admin_socket_path` is the resident's own socket on the robot and is only ever handed to ssh.

    WHEN THIS IS PRESENT, NOTHING LOCAL STANDS IN FOR THE ROBOT. The local probes (a release directory under the
    checkout, a process table, runtime-directory sockets) are never consulted: they describe this laptop, and a
    resident answering here is not the robot's. See demo_runtime.robot_link.

    THE ANSWERING RESIDENT HAS TO REPORT THE PINNED BUILD. A socket path says where a forward binds, not what answers
    through it. `expected_build_identity` is REQUIRED: the build_identity the robot's own resident reports in get-status,
    pinned by the operator from the deployed robot. A resident reporting any other build identity, or none, is reported
    as not the pinned build (reason robot_resident_not_the_robot, a kept name) and moves nothing. The build identity
    identifies the SOFTWARE the resident runs; the pin refuses a different build and nothing more: a resident built from
    the same source elsewhere reports the same value. Which deployment is observed is established by the authenticated
    connection to the intended robot (the ssh forward to that robot's admin socket, or the local socket on the robot) and
    the session that endpoint's resident reports now. The target profile refuses nothing either: every build for this
    profile shares it, so it is shown as information and decides nothing.

    `display_label` is the operator's own PUBLIC name for this robot, shown to answer "which LeKiwi". It is optional:
    absent, the robot is reported as unlabelled. The robot id is never sent in its place, in any form.
    """

    remote_admin_socket_path: str
    local_admin_socket_path: str
    expected_build_identity: str = ""
    display_label: Optional[str] = None
    # ssh_forward (default) or local. With local there is no forward: `local_admin_socket_path` IS the resident's own
    # socket on this machine and `remote_admin_socket_path` is empty (the loader refuses one).
    link: str = ROBOT_LINK_SSH_FORWARD


@dataclass(frozen=True, repr=False)
class CakeSignatureConfig:
    """Where the deployed RELEASE's own signature verifier and the files it is run against are, on the robot.

    `demo_plan_path` is the release's demo-plan binary, `manifest_path` the manifest build-mobile wrote at install (every
    capsule digest, the store root, any unsigned twin) and `resident_conf_path` the resident's own configuration (its
    signature policy and trusted key list). All three absolute and all three named explicitly: the verifier is never
    searched for, and the keys the check verifies under are the RESIDENT'S, read from its file, so the check answers
    under the resident's policy and never a policy of its own. Read by demo_runtime.signature_check, which runs the
    verifier and nothing else; it commands no motion and touches no slot.
    """

    demo_plan_path: str
    manifest_path: str
    resident_conf_path: str


@dataclass(frozen=True, repr=False)
class CakeConfig:
    """Where a REAL Cake resident's read-only admin interface is, when one is being read at all.

    Both paths are absolute and both are named explicitly. Nothing is searched for on PATH and nothing is defaulted:
    a Cake connection that appeared because a binary happened to be somewhere would be a connection nobody chose, and
    `tests/mock-bin` holds doubles of exactly these tool names.

    `activation_candidates` names the prepared Plans this runtime is willing to activate, BY KEY. It is optional and
    empty by default, which means this runtime activates nothing. A browser selects among these keys and supplies no
    bytes and no path; see demo_runtime.cake_activation.PreparedCandidates, which reads each one once and refuses a key
    it was not given.

    This is RUNTIME configuration and is not part of the public v1 contract. The contract's `cake` object is what the
    console reads; this only says where to look.
    """

    admin_probe_path: str
    admin_socket_path: str
    poll_interval_s: float = 5.0
    timeout_s: float = 2.0
    activation_candidates: Mapping[str, str] = field(default_factory=dict)
    capability: Optional[CakeCapabilityBinding] = None
    # OPTIONAL. What each prepared candidate is FOR, by key: {key: role} or {key: {role, label}}, validated against the
    # keys above by cake_activation.roles_refusal. A role is a label and an expectation shown beside the key, never a
    # permission. Absent: the candidates are served by key and digest only, as before.
    candidate_roles: Mapping[str, object] = field(default_factory=dict)
    # OPTIONAL. The build_identity of the resident a SYNTHETIC rehearsal is allowed to submit an ApplyPlan to (the
    # laptop's own fixture resident). Absent: a rehearsal submits nothing. Checked against what ANSWERS before every
    # ApplyPlan (demo_runtime.mission_service), never against a key's mere presence.
    rehearsal_fixture_build_identity: Optional[str] = None
    # OPTIONAL. Present: the resident is the ROBOT's, reached through a forward this runtime owns, and
    # admin_socket_path IS the forward's local socket (the loader makes it so). Absent: exactly as before.
    robot: Optional[CakeRobotConfig] = None
    # OPTIONAL. The deployed release's verifier, manifest and resident.conf, for the signature policy check on the robot.
    # Absent: that check is not offered, and nothing else changes.
    signature: Optional[CakeSignatureConfig] = None


@dataclass(frozen=True, repr=False)
class RuntimeConfig:
    data_root: Path
    saved_pose_file: Path
    pad_template_dir: Path
    observation_root: Path
    host: HostConfig = field(default_factory=HostConfig)
    pi_ssh_target: str = ""
    cameras: CameraConfig = field(default_factory=CameraConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    capture_provider: CaptureProvider = CaptureProvider.LEGACY_PER_CAPTURE
    freshness_refusal_enabled: bool = False
    tap: Optional[TapConfig] = None
    preview: Optional[PreviewConfig] = None
    cake: Optional[CakeConfig] = None
    execution_backend: ExecutionBackend = DEFAULT_EXECUTION_BACKEND
    mission: Optional[MissionConfig] = None
    perception: Optional[PerceptionConfig] = None
    # THE PLACEMENT ENVELOPE (docs/autonomous-demo/one-block-demo-2026-09-26.md, T6), which carries every stage's words
    # (docs/autonomous-demo/overnight-plan-2026-09-26.md section 2.6): the words a run is started with, exported to the
    # child as KC_PLACEMENT_ENVELOPE, and the attended label of a qualification run. Empty is the proven behaviour of every
    # stage, and a word reaches a LIVE child through this key only.
    placement_envelope: tuple = ()
    placement_envelope_attended: Optional[str] = None
    # The forced release offset of side_offset's qualification runs, in degrees of pan, exported as
    # KC_PLACEMENT_SIDE_OFFSET_FORCE_DEG from this key only. None is no forced offset.
    placement_side_offset_force_deg: Optional[float] = None
    layout: str = LAYOUT_EXPLICIT  # set by the constructor used, never read from the file

    def __post_init__(self):
        """Direct construction (legacy_layout, a caller that already holds the values) gets the normal form the loader
        produces. Without it a str path and a Path of the same place gave two digests, and capture_provider="camera_service"
        (equal to the enum member, because the enum is a str) made repr() and sanitized_summary() raise on .value."""
        for name in ("data_root", "saved_pose_file", "pad_template_dir", "observation_root"):
            object.__setattr__(self, name, Path(os.fspath(getattr(self, name))))
        if not isinstance(self.capture_provider, CaptureProvider):
            choice = _PROVIDER_BY_VALUE.get(self.capture_provider) if isinstance(self.capture_provider, str) else None  # a lookup: CaptureProvider(x) would put x in its ValueError
            if choice is None:
                raise ConfigError(f"capture_provider must be one of {sorted(_PROVIDER_BY_VALUE)}")
            object.__setattr__(self, "capture_provider", choice)
        if not isinstance(self.execution_backend, ExecutionBackend):
            backend = _BACKEND_BY_VALUE.get(self.execution_backend) if isinstance(self.execution_backend, str) else None
            if backend is None:
                raise ConfigError(f"execution_backend must be one of {sorted(_BACKEND_BY_VALUE)}")
            object.__setattr__(self, "execution_backend", backend)
        object.__setattr__(self, "placement_envelope", _placement_envelope(self.placement_envelope))
        object.__setattr__(self, "placement_envelope_attended", _attended_label(self.placement_envelope_attended, self.placement_envelope,
                                                                                 retry=self.mission is not None and self.mission.retry is True))
        object.__setattr__(self, "placement_side_offset_force_deg", _side_offset_force(self.placement_side_offset_force_deg, self.placement_envelope))

    def __repr__(self) -> str:
        return (f"RuntimeConfig(layout={self.layout!r}, data_root={REDACTED}, saved_pose_file={REDACTED}, pad_template_dir={REDACTED}, "
                f"observation_root={REDACTED}, host={self.host!r}, pi_ssh_target={_mask(self.pi_ssh_target)}, cameras={self.cameras!r}, "
                f"model={self.model!r}, capture_provider={self.capture_provider.value!r}, freshness_refusal_enabled={self.freshness_refusal_enabled}, "
                f"tap={self.tap!r}, execution_backend={self.execution_backend.value!r}, digest={config_digest(self)[:12]})")

    def to_private_dict(self) -> dict:
        """The FULL config as plain JSON values. PRIVATE: it exists for config_digest() and for a consumer that must hand
        the values on. It must never be logged, printed, sent to a browser or written into a run bundle."""
        return {
            "layout": self.layout,
            "data_root": str(self.data_root), "saved_pose_file": str(self.saved_pose_file),
            "pad_template_dir": str(self.pad_template_dir), "observation_root": str(self.observation_root),
            "host": {"address": self.host.address, "robot_id": self.host.robot_id, "cmd_port": self.host.cmd_port, "obs_port": self.host.obs_port},
            "pi_ssh_target": self.pi_ssh_target,
            "cameras": {"wrist_id": self.cameras.wrist_id, "front_id": self.cameras.front_id},
            "model": {"identity": self.model.identity, "revision": self.model.revision},
            "capture_provider": self.capture_provider.value,
            "freshness_refusal_enabled": self.freshness_refusal_enabled,
            "placement_envelope": list(self.placement_envelope), "placement_envelope_attended": self.placement_envelope_attended,
            "placement_side_offset_force_deg": self.placement_side_offset_force_deg,
            "tap": None if self.tap is None else {"enabled": self.tap.enabled, "port": self.tap.port},
            "preview": None if self.preview is None else {
                "enabled": self.preview.enabled, "wrist_port": self.preview.wrist_port, "front_port": self.preview.front_port},
            "cake": None if self.cake is None else {
                "admin_probe_path": self.cake.admin_probe_path, "admin_socket_path": self.cake.admin_socket_path,
                "poll_interval_s": self.cake.poll_interval_s, "timeout_s": self.cake.timeout_s,
                # Both are part of the digest: a runtime that would activate a different candidate, or read a
                # different slot, is a different configuration and must not share a digest with this one.
                "activation_candidates": dict(self.cake.activation_candidates),
                # Only when present, so a configuration without roles keeps the digest it had before.
                **({"candidate_roles": _plain_roles(self.cake.candidate_roles)} if self.cake.candidate_roles else {}),
                "capability": None if self.cake.capability is None else {
                    "name": self.cake.capability.name, "slot_uuid": self.cake.capability.slot_uuid,
                    "active_capsule": self.cake.capability.active_capsule},
                # Only when present, so a configuration without a robot section keeps the digest it had before.
                **({} if self.cake.robot is None else {"robot": {
                    "remote_admin_socket_path": self.cake.robot.remote_admin_socket_path,
                    "local_admin_socket_path": self.cake.robot.local_admin_socket_path,
                    "expected_build_identity": self.cake.robot.expected_build_identity,
                    "display_label": self.cake.robot.display_label,
                    # Only when not the default, so a configuration without the key keeps the digest it had before.
                    **({} if self.cake.robot.link == ROBOT_LINK_SSH_FORWARD else {"link": self.cake.robot.link})}}),
                **({} if self.cake.rehearsal_fixture_build_identity is None else {
                    "rehearsal_fixture_build_identity": self.cake.rehearsal_fixture_build_identity}),
                # Only when present, so a configuration without the section keeps the digest it had before.
                **({} if self.cake.signature is None else {"signature": {
                    "demo_plan_path": self.cake.signature.demo_plan_path, "manifest_path": self.cake.signature.manifest_path,
                    "resident_conf_path": self.cake.signature.resident_conf_path}})},
            "execution_backend": self.execution_backend.value,
            # Only when present, so a configuration without a mission section keeps the digest it had before.
            **({} if self.mission is None else {"mission": {
                "execution": self.mission.execution,
                "capability_observation_endpoint": self.mission.capability_observation_endpoint,
                "capability_command_endpoint": self.mission.capability_command_endpoint,
                # Only when present, so a mission section without radii keeps the digest it had before.
                **({} if self.mission.radii is None else {"radii": {
                    "r_claim_mm": self.mission.radii.r_claim_mm, "r_match_mm": self.mission.radii.r_match_mm,
                    "basis": self.mission.radii.basis}}),
                # Only when on, so a mission section that leaves the Retry off keeps the digest it had before.
                **({"retry": True} if self.mission.retry else {})}}),
            # Only when present, so a configuration without a perception section keeps the digest it had before.
            **({} if self.perception is None else {"perception": {"remote": {
                "port": self.perception.remote.port, "deadline_s": self.perception.remote.deadline_s,
                "model_revision": self.perception.remote.model_revision,
                "model_files_sha256": dict(self.perception.remote.model_files_sha256)}}}),
        }

    @classmethod
    def from_mapping(cls, raw) -> "RuntimeConfig":
        """Validate the parsed JSON document. Unknown keys are refused (a typo must not turn into a silent default), and
        every refusal names the field only."""
        top = _Section("config", raw)
        data_root = _absolute_root(top.text("data_root", required=True))
        paths = {name: _under(data_root, top.text(name, required=True)) for name in ("saved_pose_file", "pad_template_dir", "observation_root")}
        host = _Section("host", top.take("host", {}))
        cameras = _Section("cameras", top.take("cameras", {}))
        model = _Section("model", top.take("model", {}))
        tap_raw = top.take("tap", None)
        tap = None
        if tap_raw is not None:
            tap_section = _Section("tap", tap_raw)
            tap = TapConfig(enabled=tap_section.boolean("enabled", False), port=tap_section.port("port", None))
            if tap.enabled and not tap.port:
                raise ConfigError("tap.port is required when tap.enabled is true")
            tap_section.finish()
        preview_raw = top.take("preview", None)
        preview = None
        if preview_raw is not None:
            preview_section = _Section("preview", preview_raw)
            preview = PreviewConfig(enabled=preview_section.boolean("enabled", False),
                                    wrist_port=preview_section.port("wrist_port", None),
                                    front_port=preview_section.port("front_port", None))
            if preview.enabled and not preview.roles():
                raise ConfigError("preview.enabled is true but neither preview.wrist_port nor preview.front_port is set")
            preview_section.finish()
        # OPTIONAL, and absent means exactly what it meant before: this runtime reads no resident.
        cake_raw = top.take("cake", None)
        cake = None
        if cake_raw is not None:
            cake_section = _Section("cake", cake_raw)
            probe_path = cake_section.text("admin_probe_path")
            socket_path = cake_section.text("admin_socket_path")
            # OPTIONAL: the ROBOT's resident, through a forward this runtime owns. When it is present the admin socket
            # every reader uses IS the forward's local end, so admin_socket_path may be left out, and when it is given
            # it has to name that same local end: two sockets for one resident would be a connection nobody chose.
            robot = _cake_robot(cake_section.take("robot", None))
            if robot is not None:
                if socket_path and socket_path != robot.local_admin_socket_path:
                    raise ConfigError("cake.admin_socket_path must be left out or equal cake.robot.local_admin_socket_path when cake.robot is set")
                socket_path = robot.local_admin_socket_path
            for name, value in (("admin_probe_path", probe_path), ("admin_socket_path", socket_path)):
                if not value.startswith("/"):
                    raise ConfigError(f"cake.{name} must be an absolute path")
            # OPTIONAL, and empty means this runtime activates nothing. A key names a candidate; the path is where its
            # prepared bytes are read from ONCE, by the runtime, and it never leaves this process.
            candidates = _activation_candidates(cake_section.take("activation_candidates", None))
            # OPTIONAL: what each of those candidates is for (track C's roles); refused whole by its first reason.
            roles = _candidate_roles(cake_section.take("candidate_roles", None), candidates)
            # OPTIONAL as well, and absent means nothing here can decide that the capability is available: a mission
            # that needs one then says so instead of guessing from a slot count.
            capability_raw = cake_section.take("capability", None)
            capability = None
            if capability_raw is not None:
                capability_section = _Section("cake.capability", capability_raw)
                capability = CakeCapabilityBinding(name=capability_section.text("name", required=True),
                                                   slot_uuid=capability_section.text("slot_uuid", required=True),
                                                   active_capsule=capability_section.text("active_capsule", required=True))
                capability_section.finish()
                _refuse_unshaped_capability(capability)
            # The poll interval and the timeout are NOT read from the file. They bound how often a child process is
            # started and how long it may take, and those are this runtime's own limits rather than a deployment's.
            # OPTIONAL: the build identity of the fixture resident a rehearsal may submit to. It can never name the robot's.
            fixture = cake_section.text("rehearsal_fixture_build_identity", default=None, nullable=True)
            if fixture is not None and _BUILD_IDENTITY.fullmatch(fixture) is None:
                raise ConfigError("cake.rehearsal_fixture_build_identity must be 64 lowercase hex characters")
            if fixture is not None and robot is not None and fixture == robot.expected_build_identity:
                raise ConfigError("cake.rehearsal_fixture_build_identity must differ from cake.robot.expected_build_identity")
            # OPTIONAL: the deployed release's verifier, manifest and resident.conf, for the signature policy check.
            signature = _cake_signature(cake_section.take("signature", None))
            cake = CakeConfig(admin_probe_path=probe_path, admin_socket_path=socket_path,
                              activation_candidates=candidates, capability=capability, candidate_roles=roles,
                              rehearsal_fixture_build_identity=fixture, robot=robot, signature=signature)
            cake_section.finish()
        mission = _mission(top.take("mission", None))
        perception = _perception(top.take("perception", None))
        provider = top.text("capture_provider", default=CaptureProvider.LEGACY_PER_CAPTURE.value)
        if provider not in _PROVIDER_BY_VALUE:
            raise ConfigError(f"capture_provider must be one of {sorted(_PROVIDER_BY_VALUE)}")
        # A file that says nothing gets the baseline: the path that has run on hardware, and the one that takes no lease.
        backend = top.text("execution_backend", default=DEFAULT_EXECUTION_BACKEND.value)
        if backend not in _BACKEND_BY_VALUE:
            raise ConfigError(f"execution_backend must be one of {sorted(_BACKEND_BY_VALUE)}")
        envelope = _placement_envelope(top.take("placement_envelope", []))
        attended = _attended_label(top.text("placement_envelope_attended", default=None, nullable=True), envelope,
                                   retry=mission is not None and mission.retry)
        force = _side_offset_force(top.take("placement_side_offset_force_deg", None), envelope)
        config = cls(
            data_root=data_root, **paths, placement_envelope=envelope, placement_envelope_attended=attended, placement_side_offset_force_deg=force,
            host=HostConfig(address=host.text("address"), robot_id=host.text("robot_id"), cmd_port=host.port("cmd_port", DEFAULT_CMD_PORT), obs_port=host.port("obs_port", DEFAULT_OBS_PORT)),
            pi_ssh_target=top.text("pi_ssh_target"),
            cameras=CameraConfig(wrist_id=cameras.text("wrist_id"), front_id=cameras.text("front_id", default=None, nullable=True)),
            model=ModelConfig(identity=model.text("identity"), revision=model.text("revision", default=None, nullable=True)),
            capture_provider=_PROVIDER_BY_VALUE[provider],
            freshness_refusal_enabled=top.boolean("freshness_refusal_enabled", False),
            tap=tap, preview=preview, cake=cake, execution_backend=_BACKEND_BY_VALUE[backend], mission=mission, perception=perception,
            layout=LAYOUT_EXPLICIT)
        for section in (host, cameras, model, top):
            section.finish()
        _refuse_port_collisions(config)
        _refuse_private_label(config)
        return config


# The PLACEMENT words the controller knows (run_single_cube_cycle.PLACEMENT_ENVELOPE_WORDS; a test keeps the two equal): T6
# steps 1b, 2b, 3a and 3b. Every later placement word builds on projected, and the controller refuses a list without it.
PLACEMENT_ENVELOPE_WORDS = ("projected", "near_reverse", "side_offset", "base_lateral")
# THE ONE WORD MECHANISM (docs/autonomous-demo/overnight-plan-2026-09-26.md section 2.6). The key keeps the placement track's
# name and carries every stage's words (scene_scan, align_v2, near_grasp, return_2d, ...). This runtime checks a word's
# SHAPE only: lower-case letters, digits and underscores, a letter first, at most 32. MEMBERSHIP is the controller's
# refusal, before the robot is constructed (placement_envelope_unknown_word), because each stage module declares its own
# words and the runtime must not hold a second copy of that vocabulary that could drift from it. The one rule kept here
# is the placement track's: a later placement word needs projected among the words.
_ENVELOPE_WORD = re.compile(r"[a-z][a-z0-9_]{0,31}")
_ATTENDED_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,47}")


def _placement_envelope(raw) -> tuple:
    """The validated tuple of words: a list of distinct words of the right shape, with projected among them when any later
    placement word is. The refusal names the field and the rule, never the value."""
    if isinstance(raw, tuple):
        raw = list(raw)
    if not isinstance(raw, list) or any(not isinstance(word, str) for word in raw):
        raise ConfigError("placement_envelope must be a list of strings")
    if any(_ENVELOPE_WORD.fullmatch(word) is None for word in raw) or len(set(raw)) != len(raw):
        raise ConfigError("placement_envelope must name distinct words of 1 to 32 lower-case letters, digits or underscores, starting with a letter")
    if any(word in PLACEMENT_ENVELOPE_WORDS[1:] for word in raw) and "projected" not in raw:
        raise ConfigError(f"placement_envelope must include projected when it names a later placement word ({', '.join(PLACEMENT_ENVELOPE_WORDS[1:])}): every later placement word builds on it")
    return tuple(raw)


def _attended_label(raw, envelope: tuple, *, retry: bool = False):
    """The attended label of a qualification run: a plain label (letters, digits, dot, underscore, hyphen; at most 48), and
    only beside at least one envelope word or mission.retry (the attended check of a LIVE mission's Retry). None is no
    label. Without an envelope word the supervisor hands the label to no controller: it only admits the Retry."""
    if raw is None:
        return None
    if not isinstance(raw, str) or _ATTENDED_LABEL.fullmatch(raw) is None:
        raise ConfigError("placement_envelope_attended must be a label of 1 to 48 letters, digits, dots, underscores or hyphens, starting with a letter or digit")
    if not envelope and not retry:
        raise ConfigError("placement_envelope_attended names an attended qualification run and needs at least one placement_envelope word or mission.retry")
    return raw


def _side_offset_force(raw, envelope: tuple):
    """The forced release offset of side_offset's qualification runs: None, or a finite number of degrees beside the word
    side_offset. The cap (run_single_cube_cycle.PLACE_SIDE_PAN_MAX_DEG) and the attended label a LIVE run needs are the
    controller's refusals, before the robot is constructed: this runtime holds no copy of that cap to drift from it."""
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        raise ConfigError("placement_side_offset_force_deg must be a finite number of degrees, or null")
    if "side_offset" not in envelope:
        raise ConfigError("placement_side_offset_force_deg belongs to the qualification runs of side_offset and needs that word in placement_envelope")
    return float(raw)


class _Section:
    """One JSON object being consumed key by key, so that what is left over at the end is exactly the unknown keys."""

    def __init__(self, name: str, raw):
        if not isinstance(raw, dict):
            raise ConfigError(f"{name} must be a JSON object")
        self.name, self.left, self.known = name, dict(raw), set()

    def _label(self, key: str) -> str:
        return key if self.name == "config" else f"{self.name}.{key}"

    def take(self, key: str, default):
        self.known.add(key)
        return self.left.pop(key, default)

    def text(self, key: str, default="", required: bool = False, nullable: bool = False):
        self.known.add(key)
        if key not in self.left:
            if required:
                raise ConfigError(f"{self._label(key)} is required")
            return default
        value = self.left.pop(key)
        if value is None and nullable:
            return None
        if not isinstance(value, str):
            raise ConfigError(f"{self._label(key)} must be a string" + (" or null" if nullable else ""))
        if required and not value:
            raise ConfigError(f"{self._label(key)} must not be empty")
        return value

    def boolean(self, key: str, default: bool) -> bool:
        self.known.add(key)
        value = self.left.pop(key, default)
        if not isinstance(value, bool):
            raise ConfigError(f"{self._label(key)} must be true or false")
        return value

    def port(self, key: str, default):
        self.known.add(key)
        value = self.left.pop(key, default)
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:  # bool is an int in Python
            raise ConfigError(f"{self._label(key)} must be an integer from 1 to 65535")
        return value

    def finish(self) -> None:
        if self.left:
            # A key is normally a harmless typo, but a badly nested file can put a VALUE where a key belongs (a robot id made
            # of lowercase letters and digits looks exactly like a field name). So an unknown key is never echoed: the
            # message is built from this module's own literals only, and lists what the section TAKES instead.
            raise ConfigError(f"{len(self.left)} unknown key(s) in {self.name}; the keys it takes are: {', '.join(self._label(key) for key in sorted(self.known))}")


# A unix socket path has to fit sun_path (108 bytes on Linux, 104 elsewhere). ssh refuses a longer one and would exit,
# so a path that cannot be bound is a configuration error at load instead of a forward that dies on every restart.
UNIX_SOCKET_PATH_MAX = 103

# A resident's build_identity as get-status prints it: 64 lowercase hex characters. It identifies the SOFTWARE a resident
# runs, and it is what the pins here compare (cake.robot.expected_build_identity, cake.rehearsal_fixture_build_identity):
# a pin refuses a different build and nothing more. Which deployment answers is established by the authenticated
# connection to it and the session its resident reports now. The target profile refuses nothing: the laptop's x86
# cake-resident was captured reporting the robot's own LeKiwi aarch64 target_profile_digest.
_BUILD_IDENTITY = re.compile(r"[0-9a-f]{64}")
# An operator's PUBLIC name for the robot: letters, digits, space, dot, underscore and hyphen, nothing that can spell a
# path, an address or a user@host. It must also pass the runtime's own sanitizer unchanged (_refuse_private_label).
DISPLAY_LABEL_MAX = 48
_DISPLAY_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,%d}" % (DISPLAY_LABEL_MAX - 1))


def _cake_robot(raw) -> Optional["CakeRobotConfig"]:
    """The optional cake.robot section. Both paths absolute, both short enough to bind, and not the same path: the local
    end of a forward that named the robot's own path would be this machine's socket passed off as the robot's."""
    if raw is None:
        return None
    section = _Section("cake.robot", raw)
    link = section.text("link", default=ROBOT_LINK_SSH_FORWARD)
    if link not in ROBOT_LINKS:
        raise ConfigError(f"cake.robot.link must be one of {list(ROBOT_LINKS)}")
    # A LOCAL link has no forward, so it has no remote path: one given anyway would be a forward nobody opens, and a
    # reader could take it for where the resident is.
    remote = section.text("remote_admin_socket_path", required=link == ROBOT_LINK_SSH_FORWARD)
    if link == ROBOT_LINK_LOCAL and remote:
        raise ConfigError("cake.robot.remote_admin_socket_path must be left out when cake.robot.link is local")
    local = section.text("local_admin_socket_path", required=True)
    expected = section.text("expected_build_identity", required=True)
    label = section.text("display_label", default=None, nullable=True)
    section.finish()
    paths = (("local_admin_socket_path", local),) if link == ROBOT_LINK_LOCAL else \
        (("remote_admin_socket_path", remote), ("local_admin_socket_path", local))
    for name, value in paths:
        if not value.startswith("/"):
            raise ConfigError(f"cake.robot.{name} must be an absolute path")
        if len(value.encode("utf-8")) > UNIX_SOCKET_PATH_MAX:
            raise ConfigError(f"cake.robot.{name} is longer than a unix socket path can be ({UNIX_SOCKET_PATH_MAX} bytes)")
    if link == ROBOT_LINK_SSH_FORWARD and remote == local:
        raise ConfigError("cake.robot.local_admin_socket_path must differ from cake.robot.remote_admin_socket_path")
    if _BUILD_IDENTITY.fullmatch(expected) is None:
        raise ConfigError("cake.robot.expected_build_identity must be 64 lowercase hex characters")
    if label is not None and (_DISPLAY_LABEL.fullmatch(label) is None or _DOTTED_QUAD.search(label)):
        raise ConfigError(f"cake.robot.display_label must be 1 to {DISPLAY_LABEL_MAX} letters, digits, spaces, dots, "
                          "underscores or hyphens, starting with a letter or digit, and must not spell an address")
    return CakeRobotConfig(remote_admin_socket_path=remote, local_admin_socket_path=local,
                           expected_build_identity=expected, display_label=label, link=link)



def _cake_signature(raw) -> Optional["CakeSignatureConfig"]:
    """The optional cake.signature section: three absolute paths, every one required."""
    if raw is None:
        return None
    section = _Section("cake.signature", raw)
    values = {name: section.text(name, required=True) for name in ("demo_plan_path", "manifest_path", "resident_conf_path")}
    section.finish()
    for name, value in values.items():
        if not value.startswith("/"):
            raise ConfigError(f"cake.signature.{name} must be an absolute path")
    return CakeSignatureConfig(**values)


_IPC_ENDPOINT = re.compile(r"ipc:///[A-Za-z0-9._/-]{1,200}")


def _mission(raw) -> Optional["MissionConfig"]:
    """The optional `mission` section. Absent: None, and nothing about a mission changes. Present, `execution` is still
    unset unless it names the one execution there is; an endpoint is an absolute ipc:// path of a plain alphabet."""
    if raw is None:
        return None
    section = _Section("mission", raw)
    execution = section.text("execution", default=None, nullable=True)
    observation = section.text("capability_observation_endpoint", default=None, nullable=True)
    command = section.text("capability_command_endpoint", default=None, nullable=True)
    radii = _radii(section.take("radii", None))
    retry = section.boolean("retry", False)
    section.finish()
    if execution is not None and execution not in MISSION_EXECUTIONS:
        raise ConfigError(f"mission.execution must be null or one of {list(MISSION_EXECUTIONS)}")
    for name, value in (("capability_observation_endpoint", observation), ("capability_command_endpoint", command)):
        if value is not None and (_IPC_ENDPOINT.fullmatch(value) is None or ".." in value or "//" in value[len("ipc://"):]):
            raise ConfigError(f"mission.{name} must be an absolute ipc:// endpoint")
    if observation is not None and observation == command:
        raise ConfigError("mission.capability_observation_endpoint must differ from mission.capability_command_endpoint")
    return MissionConfig(execution=execution, capability_observation_endpoint=observation, capability_command_endpoint=command, radii=radii,
                         retry=retry)


# The basis of the radii names WHERE their qualification is written down (a document and its section, a record id), so its
# alphabet is plain: it is configuration text an operator reads, and it may never spell a path, an address or a user@host.
_RADII_BASIS = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:#()-]{0,159}")
RADIUS_MAX_MM = 1000.0  # a bound on a typo (a metre), not a qualified value
#: The one word `mission.radii` accepts in place of the numeric object (MissionRadii). It names a derivation, not a number:
#: the value and its basis are target_binding's, read when the configuration is loaded and never spelled here.
RADII_CUBE_HALF_EDGE = "cube_half_edge"


def _target_binding_module():
    """autonomy/manipulation/target_binding.py, imported HERE and only when mission.radii names the derived value, so a
    configuration that spells its own numbers loads no more than it did. `demo_runtime` is a packaged runtime and
    `autonomy` is a checkout layout; the robot runs this daemon from a pinned git-archive release, which has both.
    target_binding imports the standard library only (its own test keeps it so): no numpy, no OpenCV, no controller."""
    directory = str(Path(__file__).resolve().parents[3] / "autonomy" / "manipulation")
    if directory not in sys.path:
        sys.path.insert(0, directory)
    import target_binding  # noqa: E402

    return target_binding


def _required_number(section: "_Section", key: str, *, above: float, at_most: float) -> float:
    """A REQUIRED finite number in (above, at_most]. The message names the field and the bounds, never the value."""
    label = section._label(key)
    if key not in section.left:
        section.known.add(key)
        raise ConfigError(f"{label} is required")
    value = section.take(key, None)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not above < value <= at_most:
        raise ConfigError(f"{label} must be a number greater than {above:g} and at most {at_most:g}")
    return float(value)


def _radii(raw) -> Optional["MissionRadii"]:
    """The optional `mission.radii` section. Present: the word RADII_CUBE_HALF_EDGE, resolved through target_binding and
    then checked exactly as a spelled object is, or the object itself, whose three fields are all required, none with a
    default. Any other word, and any other shape, is refused by name."""
    if raw is None:
        return None
    if isinstance(raw, str):
        if raw != RADII_CUBE_HALF_EDGE:
            raise ConfigError(f"mission.radii must be the word {RADII_CUBE_HALF_EDGE} or an object with r_claim_mm, r_match_mm and basis")
        return _radii(_target_binding_module().radii_from_cube_geometry().to_document())
    if not isinstance(raw, dict):
        raise ConfigError(f"mission.radii must be the word {RADII_CUBE_HALF_EDGE} or an object with r_claim_mm, r_match_mm and basis")
    section = _Section("mission.radii", raw)
    claim = _required_number(section, "r_claim_mm", above=0.0, at_most=RADIUS_MAX_MM)
    match = _required_number(section, "r_match_mm", above=0.0, at_most=RADIUS_MAX_MM)
    basis = section.text("basis", required=True)
    section.finish()
    if _RADII_BASIS.fullmatch(basis) is None or _DOTTED_QUAD.search(basis):
        raise ConfigError("mission.radii.basis must be 1 to 160 letters, digits, spaces or . _ : # ( ) -, starting with a letter or digit, "
                          "and must not spell an address")
    return MissionRadii(r_claim_mm=claim, r_match_mm=match, basis=basis)


_MODEL_REVISION_PIN = re.compile(r"[0-9a-f]{40}")
_MODEL_FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _perception(raw) -> Optional["PerceptionConfig"]:
    """The optional `perception` section. Absent: None, and perception stays the controller's own. Present: `remote` is
    required, and every one of its fields is required; nothing here has a default."""
    if raw is None:
        return None
    section = _Section("perception", raw)
    remote_raw = section.take("remote", None)
    section.finish()
    if remote_raw is None:
        raise ConfigError("perception.remote is required when perception is present")
    remote = _Section("perception.remote", remote_raw)
    if "port" not in remote.left:
        remote.known.update({"port", "deadline_s", "model_revision", "model_files_sha256"})
        raise ConfigError("perception.remote.port is required")
    port = remote.port("port", None)
    deadline = _required_number(remote, "deadline_s", above=0.0, at_most=PERCEPTION_DEADLINE_MAX_S)
    revision = remote.text("model_revision", required=True)
    files = remote.take("model_files_sha256", None)
    remote.finish()
    if not port:  # an explicit null parses to 0 (_Section.port): it is a port that is not given
        raise ConfigError("perception.remote.port is required")
    if port in ROBOT_FIXED_SERVICE_PORTS:
        raise ConfigError("perception.remote.port must not be one of the robot's own service ports (5555-5558, 5570-5572)")
    if _MODEL_REVISION_PIN.fullmatch(revision) is None:
        raise ConfigError("perception.remote.model_revision must be 40 lowercase hex characters (the model snapshot's commit)")
    if files is None:
        raise ConfigError("perception.remote.model_files_sha256 is required")
    if not isinstance(files, dict) or not files or not all(isinstance(name, str) and _MODEL_FILE_NAME.fullmatch(name) and isinstance(value, str)
                                                          and _SHA256.fullmatch(value) for name, value in files.items()):
        raise ConfigError("perception.remote.model_files_sha256 must be a non-empty object of plain file name to 64 lowercase hex characters")
    return PerceptionConfig(remote=RemotePerceptionConfig(port=port, deadline_s=deadline, model_revision=revision, model_files_sha256=dict(files)))


def perception_remote_of(config) -> Optional["RemotePerceptionConfig"]:
    """The `perception.remote` section of a configuration, or None. ONE reader of it, like robot_of."""
    perception = getattr(config, "perception", None) if config is not None else None
    remote = getattr(perception, "remote", None) if isinstance(perception, PerceptionConfig) else None
    return remote if isinstance(remote, RemotePerceptionConfig) else None


def model_files_digest(files_sha256) -> str:
    """"sha256:<hex>" over the canonical JSON of {file name: sha256}. autonomy/perception/remote_perception.py computes the
    same (a test keeps the two equal): the configuration pins the map, the controller's environment carries this digest,
    and the desktop service states it for the snapshot it loaded."""
    canonical = json.dumps({str(name): str(value) for name, value in dict(files_sha256).items()}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(canonical.encode("ascii")).hexdigest()


def mission_of(config) -> Optional["MissionConfig"]:
    """The `mission` section of a configuration, or None. ONE reader of it, like robot_of."""
    value = getattr(config, "mission", None) if config is not None else None
    return value if isinstance(value, MissionConfig) else None


def robot_link_is_local(config) -> bool:
    """Whether this runtime reaches the robot's resident on the same machine (cake.robot.link local)."""
    robot = robot_of(config)
    return robot is not None and robot.link == ROBOT_LINK_LOCAL


def _refuse_private_label(config) -> None:
    """A display label is PUBLIC: it has to come through the runtime's own sanitizer unchanged. A label that contained
    the robot id, the host address, the ssh user or host, or any other private value of this configuration would be
    that value sent to every client, or a label the sanitizer silently mangles. The message names no value."""
    robot = robot_of(config)
    if robot is None or robot.display_label is None:
        return
    from .read_models import Sanitizer  # imported HERE: read_models imports this module

    if Sanitizer.for_runtime(config).text(robot.display_label, generic=True) != robot.display_label:
        raise ConfigError("cake.robot.display_label contains a private value of this configuration (the robot id, the "
                          "host address or the ssh target); the label is public and must name none of them")


def signature_paths(config) -> Optional[tuple]:
    """(demo-plan path, manifest path, resident.conf path) from cake.signature, or None. Read the same way from a
    CakeConfig or from a test's plain mapping, exactly as cake_admin.configured_paths reads the two admin paths."""
    cake = getattr(config, "cake", None) if not isinstance(config, dict) else config.get("cake")
    if cake is None:
        return None
    signature = cake.get("signature") if isinstance(cake, dict) else getattr(cake, "signature", None)
    if signature is None:
        return None
    if isinstance(signature, dict):
        values = tuple(signature.get(name) for name in ("demo_plan_path", "manifest_path", "resident_conf_path"))
    else:
        values = (signature.demo_plan_path, signature.manifest_path, signature.resident_conf_path)
    if not all(isinstance(value, str) and value for value in values):
        return None
    return values


def robot_of(config) -> Optional["CakeRobotConfig"]:
    """The cake.robot section of a configuration, or None. ONE reader of it, so a caller cannot read a robot where the
    loader did not put one."""
    cake = getattr(config, "cake", None) if config is not None else None
    robot = getattr(cake, "robot", None) if cake is not None and not isinstance(cake, dict) else None
    return robot if isinstance(robot, CakeRobotConfig) else None


def _activation_candidates(raw) -> dict:
    """The prepared activation candidates, key -> ABSOLUTE path. Optional; absent is an empty mapping.

    Every refusal names the FIELD and nothing else, like every other refusal in this module: a candidate key is chosen
    by a deployment and a path names a home directory, and neither may appear in a message that is shown.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("cake.activation_candidates must be a JSON object of candidate key to absolute path")
    out: dict = {}
    for key, value in raw.items():
        if not isinstance(key, str) or _CANDIDATE_KEY.fullmatch(key) is None:
            raise ConfigError("every key in cake.activation_candidates must be letters, digits, dot, dash or underscore, at most 64 characters")
        if not isinstance(value, str) or not value.startswith("/"):
            raise ConfigError("every value in cake.activation_candidates must be an absolute path")
        out[key] = value
    return out


def _candidate_roles(raw, candidates: dict) -> dict:
    """What each prepared candidate is for, key -> role or {role, label}. Optional; absent is an empty mapping. The table
    is checked by cake_activation.roles_refusal against the configured keys, and a refusal names the field and the
    reason code, never a key."""
    if raw is None:
        return {}
    from . import cake_activation  # imported here: the loader needs it only for a configuration that names roles

    refused = cake_activation.roles_refusal(raw, list(candidates))
    if refused is not None:
        raise ConfigError(f"cake.candidate_roles is refused: {refused}")
    return _plain_roles(raw)


def _plain_roles(roles) -> dict:
    return {key: (dict(entry) if isinstance(entry, dict) else entry) for key, entry in roles.items()}


def _refuse_unshaped_capability(binding: "CakeCapabilityBinding") -> None:
    """A slot uuid and a capsule digest are the resident's own hex identities and are compared character for character
    against what it prints. A value of another shape could never match one, so it is a configuration error rather than
    a capability that silently never becomes available."""
    if _CANDIDATE_KEY.fullmatch(binding.name) is None:
        raise ConfigError("cake.capability.name must be letters, digits, dot, dash or underscore, at most 64 characters")
    for field_name, value in (("slot_uuid", binding.slot_uuid), ("active_capsule", binding.active_capsule)):
        if _HEX_IDENTITY.fullmatch(value) is None:
            raise ConfigError(f"cake.capability.{field_name} must be a hexadecimal identity of 8 to 128 characters")


def _refuse_port_collisions(config: "RuntimeConfig") -> None:
    """No two listeners on the robot computer may be given the same port.

    This is the check that would have caught the defect it exists for: one port served both camera roles, so the front
    preview silently carried the wrist picture and nothing anywhere said so. A collision is a CONFIGURATION error and is
    refused at load, because the alternative is discovering it as a camera showing the wrong room.

    It covers the ports this configuration names AND the fixed ones the services on that computer already hold: the
    motor host's command and observation sockets, and the camera service's mission socket, which is not configurable
    here and is therefore exactly the kind of clash a hand-edited file produces. Only ports that are actually set are
    compared; a role with no preview holds nothing.
    """
    owners = [("host.cmd_port", config.host.cmd_port), ("host.obs_port", config.host.obs_port),
              ("the camera service's mission socket", CAMERA_SERVICE_MISSION_PORT)]
    if config.tap is not None and config.tap.port:
        owners.append(("tap.port", config.tap.port))
    if config.perception is not None:
        owners.append(("perception.remote.port", config.perception.remote.port))
    if config.preview is not None:
        for role in ("wrist", "front"):
            port = config.preview.port_for(role)
            if port:
                owners.append((f"preview.{role}_port", port))
    seen: dict = {}
    for name, port in owners:
        if port in seen:
            # The names are field names and a fixed service, never an address: what is wrong is WHICH TWO clash.
            raise ConfigError(f"{seen[port]} and {name} are the same port; every listener needs its own")
        seen[port] = name


def _absolute_root(text: str) -> Path:
    expanded = os.path.expanduser(text)
    if not os.path.isabs(expanded):
        raise ConfigError("data_root must be an absolute path (or start with ~): a relative root would resolve against the current directory, the same hidden dependency this config replaces")
    return Path(os.path.normpath(expanded))


def _under(data_root: Path, text: str) -> Path:
    """Absolute as given, otherwise relative to data_root (never to the current directory, never to __file__)."""
    expanded = os.path.expanduser(text)
    return Path(os.path.normpath(expanded if os.path.isabs(expanded) else os.path.join(str(data_root), expanded)))


def legacy_layout(repo_root, *, environ: Optional[Mapping[str, str]] = None, host_address: str = "", robot_id: str = "", pi_ssh_target: Optional[str] = None,
                  wrist_id: Optional[str] = None, front_id: Optional[str] = None, model_identity: str = LEGACY_MODEL_IDENTITY, model_revision: Optional[str] = None,
                  cmd_port: int = DEFAULT_CMD_PORT, obs_port: int = DEFAULT_OBS_PORT) -> RuntimeConfig:
    """The compatibility constructor: today's <repo>/.local-dev paths, so a caller without a config file behaves as before.

    The identities have no file to come from in the legacy layout. The address and the robot id are today's required CLI
    arguments, so they are keyword arguments here; the ssh target and the camera ids are read from `environ` (pass
    os.environ) under the names today's code uses, unless given explicitly. An unset front camera becomes an explicit null;
    an unset wrist camera stays empty, and the preflight refuses it."""
    environ = {} if environ is None else environ
    data_root = Path(os.path.normpath(os.path.abspath(os.fspath(repo_root)))) / LEGACY_DATA_DIR
    return RuntimeConfig(
        data_root=data_root, saved_pose_file=data_root.joinpath(*LEGACY_SAVED_POSE), pad_template_dir=data_root.joinpath(*LEGACY_PAD_TEMPLATE),
        observation_root=data_root.joinpath(*LEGACY_OBSERVATIONS),
        host=HostConfig(address=host_address, robot_id=robot_id, cmd_port=cmd_port, obs_port=obs_port),
        pi_ssh_target=environ.get(LEGACY_ENV_PI_SSH, "") if pi_ssh_target is None else pi_ssh_target,
        cameras=CameraConfig(wrist_id=environ.get(LEGACY_ENV_WRIST_ID, "") if wrist_id is None else wrist_id,
                             front_id=(environ.get(LEGACY_ENV_FRONT_ID) or None) if front_id is None else front_id),
        model=ModelConfig(identity=model_identity, revision=model_revision), layout=LAYOUT_LEGACY)


def execution_backend_of(config) -> ExecutionBackend:
    """The backend of a config, and the default for a caller that has none (a synthetic runtime builds no config and
    still has to say which path it would take). The default is stated in ONE place, here."""
    backend = getattr(config, "execution_backend", None)
    return backend if isinstance(backend, ExecutionBackend) else DEFAULT_EXECUTION_BACKEND


def add_config_argument(parser) -> None:
    parser.add_argument("--config", default=None, help=f"path of the PRIVATE runtime config JSON (default: ${CONFIG_ENV_VAR}; there is no default inside the repository)")


def resolve_config_path(cli_value: Optional[str] = None, environ: Optional[Mapping[str, str]] = None):
    """(path, source). --config wins over the environment variable. No fallback: a config that is found by accident is the
    failure this module removes."""
    environ = os.environ if environ is None else environ
    if cli_value:
        return os.path.expanduser(cli_value), "--config"
    if environ.get(CONFIG_ENV_VAR):
        return os.path.expanduser(environ[CONFIG_ENV_VAR]), CONFIG_ENV_VAR
    raise ConfigError(f"no runtime config: pass --config or set {CONFIG_ENV_VAR}. There is deliberately no default inside the repository")


def load_config(cli_value: Optional[str] = None, environ: Optional[Mapping[str, str]] = None) -> RuntimeConfig:
    """Every refusal is raised AFTER its except block has ended, not inside it: `raise ... from None` only hides the original
    exception from the traceback, it stays reachable as __context__, and an OSError carries the path while a JSONDecodeError
    carries the whole document (.doc), identities included."""
    path, source = resolve_config_path(cli_value, environ)
    text, problem = None, None
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as error:
        problem = f"the runtime config named by {source} cannot be read ({type(error).__name__})"
    if problem is None:
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as error:
            problem = f"the runtime config named by {source} is not valid JSON at line {error.lineno} column {error.colno}"
        except ValueError:
            problem = f"the runtime config named by {source} is not valid JSON"
    if problem is not None:
        raise ConfigError(problem)
    return RuntimeConfig.from_mapping(raw)


def config_digest(config: RuntimeConfig) -> str:
    """sha256 over a canonical serialization (sorted keys, no whitespace, ASCII) of the FULL config and its schema tag. Two
    runs with the same digest used the same robot, cameras, paths and switches; a different digest says something changed
    without saying what."""
    canonical = json.dumps({"schema": CONFIG_SCHEMA, "config": config.to_private_dict()}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def sanitized_summary(config: RuntimeConfig) -> dict:
    """The ONLY view of the config meant for browser events, exports and run bundles: booleans, enum choices, the model
    identity and digests. No endpoint, port, path, id or credential."""
    identity, revision = public_model_field(config.model.identity), public_model_field(config.model.revision, revision=True)
    return {
        "schema": SUMMARY_SCHEMA,
        "config_digest": config_digest(config),
        "layout": config.layout,
        "capture_provider": config.capture_provider.value,
        "execution_backend": config.execution_backend.value,
        "freshness_refusal_enabled": config.freshness_refusal_enabled,
        "placement_envelope": ",".join(config.placement_envelope) or "proven",  # the words, as the child receives them; a string, so the summary stays free of numbers
        "placement_envelope_attended_named": config.placement_envelope_attended is not None,
        "placement_side_offset_forced": config.placement_side_offset_force_deg is not None,  # whether, never the value: the summary carries no number
        "tap_enabled": bool(config.tap and config.tap.enabled),
        "model_identity": identity,
        "model_revision": revision,
        "model_identity_withheld": bool(config.model.identity) and identity is None,
        "model_revision_withheld": bool(config.model.revision) and revision is None,  # without it a withheld revision would read as "none"
        "host_configured": bool(config.host.address and config.host.robot_id),
        "pi_ssh_configured": bool(config.pi_ssh_target),
        "wrist_camera_configured": bool(config.cameras.wrist_id),
        "front_camera_configured": bool(config.cameras.front_id),
    }
