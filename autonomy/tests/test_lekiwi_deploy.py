#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_lekiwi_deploy.py: bin/pi/lekiwi-deploy.sh, RUN against a FAKE ROBOT COMPUTER that is a directory
# on this machine. ssh and scp are stubs first on PATH: the ssh stub logs every call and the script it was handed, then
# runs that remote command locally with HOME set to the fake robot's home and PATH set to robot-side stubs
# (systemctl, journalctl, ss, fuser, pgrep, loginctl, uname) ahead of /usr/bin:/bin; the scp stub copies into and out
# of that home. The systemctl stub starts nothing: it records each call, answers show/is-active from the test's
# settings, and on restart only records whether the arm token is there (and, when asked, deletes it the way the host
# wrapper claims it). The four Cake binaries are shell stubs with the manifest and verdict shapes of the real tools. So the
# remote scripts really execute (render, keygen with the real openssl, manifest checks, preflight classification), and
# nothing reaches a network, a robot, a real unit manager, a servo bus or a camera. The servo "device" is /dev/null,
# which is only ever tested for existence. Every identity-shaped value is made up here.
# Run directly: python3 autonomy/tests/test_lekiwi_deploy.py
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEPLOY = os.path.join(ROOT, "bin", "pi", "lekiwi-deploy.sh")

SSH_STUB = r"""#!/bin/bash
printf 'ssh\t%s\n' "$*" >>"$FAKE_LOG"
args=("$@"); i=0
while [ "$i" -lt "${#args[@]}" ]; do
  case "${args[$i]}" in -o) i=$((i + 2)) ;; -*) i=$((i + 1)) ;; *) break ;; esac
done
i=$((i + 1))
cmd="${args[*]:$i}"
script="$(cat)"
{ printf '=== %s\n' "$cmd"; printf '%s\n' "$script"; } >>"$FAKE_SCRIPTS"
n=$(( $(cat "$FAKE_SSH_COUNT" 2>/dev/null || echo 0) + 1 )); echo "$n" >"$FAKE_SSH_COUNT"
if [ -n "${STUB_SSH_FAIL:-}" ] && [ "$n" -gt "${STUB_SSH_FAIL_AFTER:-0}" ]; then
  printf 'ssh: connect to host %s port 22: Connection timed out\n' "$STUB_SSH_FAIL" >&2; exit 255
fi
case "$cmd" in
  *PYBIN=*) printf '%s\n' /opt/example/lerobot/robots/lekiwi; exit 0 ;;
  *"cat '/opt/example/"*) echo "stands in for an installed lerobot file"; exit 0 ;;
esac
cd "$FAKE_HOME" || exit 1
printf '%s\n' "$script" | HOME="$FAKE_HOME" PATH="$ROBOT_STUBS:${STUB_ROBOT_SYSPATH:-/usr/bin:/bin}" TMPDIR="$FAKE_TMP" bash -c "$cmd"
"""
SCP_STUB = r"""#!/bin/bash
printf 'scp\t%s\n' "$*" >>"$FAKE_LOG"
args=(); for a in "$@"; do case "$a" in -q) ;; -o) skip=1 ;; *) if [ -n "${skip:-}" ]; then skip=; else args+=("$a"); fi ;; esac; done
last="${args[${#args[@]}-1]}"; unset 'args[${#args[@]}-1]'
remote() { case "$1" in *:*) printf '%s/%s' "$FAKE_HOME" "${1#*:}" ;; *) printf '%s' "$1" ;; esac; }
dest="$(remote "$last")"
for src in "${args[@]}"; do
  cp -- "$(remote "$src")" "$dest" || exit 1
  if [ -n "${STUB_SCP_CORRUPT:-}" ] && [ "$(basename "$src")" = "$STUB_SCP_CORRUPT" ]; then
    if [ -d "$dest" ]; then echo corrupted >>"$dest/$(basename "$src")"; else echo corrupted >>"$dest"; fi
  fi
done
"""
# The verifier start-host.sh check runs on the laptop: a pass, recorded.
PYTHON3_STUB = """#!/bin/sh
printf '%s\\n' "$*" >>"$FAKE_PASSES"
exit 0
"""
SYSTEMCTL_STUB = r"""#!/bin/bash
printf '%s\n' "$*" >>"$FAKE_SYSTEMCTL"
verb=""; props=(); value=0; args=("$@"); i=0
while [ "$i" -lt "${#args[@]}" ]; do
  case "${args[$i]}" in
    -p) i=$((i + 1)); props+=("${args[$i]}") ;;
    --value) value=1 ;;
    -*) ;;
    *) [ -n "$verb" ] || verb="${args[$i]}" ;;
  esac
  i=$((i + 1))
done
unit="${*: -1}"
token="$HOME/${DIR_REL:-kc-lekiwi}/run/arm-token"
active() { case " ${STUB_ACTIVE_UNITS:-} " in *" $unit "*) return 0 ;; esac; return 1; }
prop() {
  case "$1" in
    ActiveState) if active; then echo active; else echo inactive; fi ;;
    SubState) if active; then echo running; else echo dead; fi ;;
    LoadState) if [ -f "$HOME/.config/systemd/user/$unit" ]; then echo loaded; else echo not-found; fi ;;
    MainPID) echo "${STUB_MAIN_PID:-0}" ;;
    NRestarts) echo 0 ;;
    UnitFileState) echo static ;;
  esac
}
case "$verb" in
  is-active)
    case " $* " in *" --quiet "*) say() { :; } ;; *) say() { echo "$1"; } ;; esac
    if active; then say active; exit 0; else say inactive; exit 3; fi ;;
  is-enabled) echo static; exit 0 ;;
  show)
    [ "${#props[@]}" -gt 0 ] || props=(LoadState ActiveState SubState MainPID)
    for p in "${props[@]}"; do if [ "$value" = 1 ]; then prop "$p"; else echo "$p=$(prop "$p")"; fi; done ;;
  restart | start)
    if [ -e "$token" ]; then printf '%s %s\n' "$verb" "$(stat -c %a "$token")" >>"$FAKE_TOKENS"; else printf '%s absent\n' "$verb" >>"$FAKE_TOKENS"; fi
    [ -z "${STUB_RESTART_FAIL:-}" ] || exit 1
    [ -z "${STUB_CLAIM_TOKEN:-}" ] || rm -f "$token" ;;
  stop) [ -z "${STUB_STOP_FAIL:-}" ] || exit 1 ;;
  daemon-reload) [ -z "${STUB_RELOAD_FAIL:-}" ] || exit 1 ;;
esac
exit 0
"""
# ss: the table the test sets, or a failure. fuser: a /dev path is owned or not as the test says; any other path (the
# check's own positive-control file) reads as owned, as the real fuser reports a file its caller holds open.
SS_STUB = r"""#!/bin/bash
case "${STUB_SS:-}" in
  cannot-run) echo "ss: command not found" >&2; exit 127 ;;
  error) echo "Cannot open netlink socket: Permission denied" >&2; exit 1 ;;
esac
# STUB_SS_SEQUENCE: one table per call, separated by '||'; the last one repeats once the sequence is used up.
if [ -n "${STUB_SS_SEQUENCE:-}" ]; then
  calls="${TMPDIR:-/tmp}/kc-stub-ss-calls"; n="$(cat "$calls" 2>/dev/null || echo 0)"; echo $((n + 1)) >"$calls"
  IFS='|' read -r -a parts <<<"${STUB_SS_SEQUENCE//||/|}"
  i="$n"; [ "$i" -lt "${#parts[@]}" ] || i=$((${#parts[@]} - 1))
  [ -z "${parts[$i]}" ] || printf '%b\n' "${parts[$i]}"
  exit 0
fi
[ -z "${STUB_SS_OUTPUT:-}" ] || printf '%s
' "$STUB_SS_OUTPUT"
"""
FUSER_STUB = r"""#!/bin/bash
case "${STUB_FUSER:-}" in
  cannot-run) echo "fuser: command not found" >&2; exit 127 ;;
  error) echo "fuser: cannot read the process table" >&2; exit 1 ;;
  blind) exit 1 ;;
esac
case "${1:-}" in
  /dev/*) [ "${STUB_FUSER:-}" = owned ] && { echo " 4242"; echo "$1:" >&2; exit 0; }; exit 1 ;;
esac
exit 0
"""
ROBOT_STUBS = {
    "systemctl": SYSTEMCTL_STUB,
    "journalctl": "#!/bin/sh\necho '-- No entries --'\n",
    "ss": SS_STUB,
    "fuser": FUSER_STUB,
    "pgrep": "#!/bin/sh\nexit 1\n",
    "loginctl": "#!/bin/sh\necho yes\n",
    "uname": "#!/bin/sh\nif [ \"$1\" = -m ]; then echo \"${STUB_ARCH:-aarch64}\"; else exec /usr/bin/uname \"$@\"; fi\n",
}
DEMO_PLAN_STUB = r"""#!/bin/bash
printf '%s\n' "$*" >>"$FAKE_DEMO_PLAN"
[ $# -gt 0 ] || { echo "usage: demo-plan build ..." >&2; exit 2; }
mode="$1"; shift
declare -A o=()
while [ $# -gt 0 ]; do case "$1" in --*) o["$1"]="${2:-}"; shift 2 ;; *) shift ;; esac; done
sum() { printf '%s' "$1" | sha256sum | cut -c1-64; }
case "$mode" in
  build-mobile | build)
    [ -z "${STUB_BUILD_FAIL:-}" ] || { echo "demo-plan: stands in for a build that failed" >&2; exit 1; }
    out="${o[--out-dir]}"; pub="$(sed -nE 's/^ed25519-public (.*)$/\1/p' "${o[--signing-key-file]}")"
    mkdir -p "$out/state-root/objects" "$out/plans" "$out/child" "$out/child-cwd"
    echo 'echo stub' >"$out/child/stub-child.sh"
    if [ "$mode" = build ]; then
      d="$(sum "stub$pub")"; echo "signed-by $pub" >"$out/state-root/objects/$d"; echo plan >"$out/plans/demo.cakepln"
      printf 'capsule_digest %s\nplan_artifact %s\npublic_key %s\nstore_root %s\n' "$d" "$out/plans/demo.cakepln" "$pub" "$out/state-root"
      exit 0
    fi
    for p in mobile-demo mobile-demo-swapped mobile-demo-active-successor-abort-start mobile-demo-active-successor-abort-swap; do echo "plan $p" >"$out/plans/$p.cakepln"; done
    for k in supervisor inert mobile; do echo "signed-by $pub" >"$out/state-root/objects/$(sum "$k$pub")"; done
    echo "unsigned" >"$out/unsigned-mobile.cakecap"
    printf 'capability_child_command %s\n' "${STUB_MANIFEST_CAPABILITY_CHILD:-${o[--capability-child-command]}}"
    printf 'child_command %s\n' "${STUB_MANIFEST_CHILD:-${o[--child-command]}}"
    printf 'holder_child_command %s\n' "${STUB_MANIFEST_HOLDER:-$out/child/stub-child.sh}"
    printf 'inert_capsule_digest %s\nmobile_capsule_digest %s\n' "$(sum "inert$pub")" "$(sum "mobile$pub")"
    printf 'mobile_slot_uuid %s\n' 0123456789abcdef0123456789abcdef
    printf 'plan_artifact %s\npublic_key %s\nstore_root %s\n' "$out/plans/mobile-demo.cakepln" "$pub" "$out/state-root"
    printf 'supervisor_capsule_digest %s\n' "$(sum "supervisor$pub")"
    printf 'unsigned_mobile_capsule_path %s\n' "$out/unsigned-mobile.cakecap" ;;
  verify)
    echo "policy require"; echo "trusted_keys 1"
    if grep -q '^unsigned' "${o[--capsule]}"; then echo "verdict refused"; echo "reason signature_missing"
    elif [ "$(sed -nE 's/^signed-by (.*)$/\1/p' "${o[--capsule]}")" = "${o[--trusted]}" ]; then echo "verdict admitted"
    else echo "verdict refused"; echo "reason signature_key_id_not_trusted"; fi ;;
esac
"""
DEMO_PREFLIGHT_STUB = r"""#!/bin/bash
[ $# -gt 0 ] || { echo "usage: demo-preflight ..." >&2; exit 2; }
printf 'demo-preflight: page size 16384\ndemo-preflight: glibc 2.41\ndemo-preflight: vm.memfd_noexec_scope 0\n'
if [ "${STUB_PREFLIGHT:-}" = refuse ]; then echo "demo-preflight: REFUSED: check admission: stands in for a refusal" >&2; exit 1; fi
printf 'demo-preflight: check admission the staged capsule is admitted against the active profile\n'
printf 'demo-preflight: check load-probe mapped through the sealed loader path with 3 seals and resolved the entry symbol\n'
echo "demo-preflight: REFUSED: check observer: --observer-module was not given, so the module this board builds for itself would first be exercised by the resident; that is the divergence this check exists to close" >&2
exit 1
"""
RESIDENT_STUB = r"""#!/bin/bash
[ "${1:-}" = --config ] && [ -f "${2:-}" ] || { echo "cake-resident: cannot read the configuration" >&2; exit 1; }
conf="$2"; val() { sed -nE "s/^$1 = (.*)$/\1/p" "$conf"; }
signer="$(sed -nE 's/^signed-by (.*)$/\1/p' "$(val store_root)"/objects/* | head -n 1)"
if [ "$signer" != "$(val trusted_keys)" ]; then
  echo "cake-resident: admission refused: the signature key id $(printf '%s' "$signer" | sha256sum | cut -c1-64) is not in the configured trusted key list" >&2
  exit 1
fi
echo "cake-resident: stands in for a resident that started" >&2
exit 0
"""
ADMIN_PROBE_STUB = "#!/bin/sh\n[ $# -gt 0 ] || { echo 'usage: admin-probe request SOCKET OP' >&2; exit 2; }\necho 'status ok'\n"
LIVE_WRITES = ("start", "restart", "enable", "reenable", "link", "mask", "unmask", "kill")
UNIT_NAME = "kiwi-cake-lekiwi.service"
# The tools a minimal robot computer without fuser or ss still has, for the checks that must refuse when those two are
# missing (every other tool of /usr/bin:/bin is left out of that PATH, fuser and ss among them).
MINIMAL_TOOLS = ("bash", "sh", "sed", "grep", "awk", "cat", "mktemp", "rm", "readlink", "date", "stat", "id", "sort",
                 "head", "tail", "tr", "seq", "sleep", "mkdir", "chmod", "mv", "ln", "cp", "dirname", "basename", "cut",
                 "env", "uname", "ls", "sha256sum", "timeout", "od", "openssl")


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def write_exec(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o755)


def file_identity(path):
    """The bytes of a file and the file itself: a file that was rewritten or replaced, even with the same bytes, differs."""
    if not os.path.lexists(path):
        return "missing"
    st = os.stat(path)
    with open(path, "rb") as handle:
        return (hashlib.sha256(handle.read()).hexdigest(), st.st_ino, st.st_mtime_ns, stat.S_IMODE(st.st_mode))


@unittest.skipUnless(shutil.which("bash") and shutil.which("sha256sum") and os.path.exists("/usr/bin/openssl"), "bash, sha256sum or /usr/bin/openssl is missing")
class FakeRobotHarness(unittest.TestCase):
    """The harness the classes below share; it has no tests of its own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.home, self.laptop_stubs, self.robot_stubs = (os.path.join(t, n) for n in ("robot-home", "laptop-stubs", "robot-stubs"))
        self.cake_bin, self.candidates, self.fake_tmp = (os.path.join(t, n) for n in ("cake-aarch64", "candidates", "robot-tmp"))
        for d in (self.home, self.laptop_stubs, self.robot_stubs, self.cake_bin, self.fake_tmp):
            os.mkdir(d)
        write_exec(os.path.join(self.laptop_stubs, "ssh"), SSH_STUB)
        write_exec(os.path.join(self.laptop_stubs, "scp"), SCP_STUB)
        write_exec(os.path.join(self.laptop_stubs, "python3"), PYTHON3_STUB)
        for name, text in ROBOT_STUBS.items():
            write_exec(os.path.join(self.robot_stubs, name), text)
        for name, text in (("demo-plan", DEMO_PLAN_STUB), ("demo-preflight", DEMO_PREFLIGHT_STUB),
                           ("cake-resident", RESIDENT_STUB), ("admin-probe", ADMIN_PROBE_STUB)):
            write_exec(os.path.join(self.cake_bin, name), text)
        # The robot's python: says yes to pyzmq and to the feetech SDK with pyserial (the preflight's import checks), and is
        # otherwise this interpreter: it hashes the public key, and it runs bin/pi/wheels_safe_stop.py --self-check against
        # the stand-in SDK below, which carries the four names the self-check asks for and can open nothing.
        self.robot_sdk = os.path.join(t, "robot-sdk")
        os.makedirs(os.path.join(self.robot_sdk, "scservo_sdk"))
        with open(os.path.join(self.robot_sdk, "scservo_sdk", "__init__.py"), "w", encoding="utf-8") as handle:
            handle.write("PortHandler = PacketHandler = GroupSyncWrite = object\nCOMM_SUCCESS = 0\n")
        self.robot_python = os.path.join(t, "robot-python")
        write_exec(self.robot_python, self.robot_python_text())
        self.logs = {n: os.path.join(t, n) for n in ("calls", "scripts", "systemctl", "demo-plan", "passes", "tokens", "ssh-count")}
        self.identities = {"KC_PI_SSH": "operator" + "@" + "robot.invalid", "KC_ROBOT_ID": "example_robot",
                           "KC_SERVO_PORT": "/dev/null", "KC_PI_PYTHON": self.robot_python,
                           "KC_CAKE_AARCH64_DIR": self.cake_bin, "KC_CANDIDATE_DIR": self.candidates}

    def robot_python_text(self, before=""):
        """The robot python's script; BEFORE is shell text run first, for a test that makes it refuse something."""
        return ("#!/bin/bash\n" + before + 'case "$*" in *"import zmq"* | *"import scservo_sdk"*) exit 0 ;; esac\n'
                f'export PYTHONPATH="{self.robot_sdk}"\nexec "{sys.executable}" "$@"\n')

    def run_deploy(self, subcommand, deploy=DEPLOY, **env):
        base = {k: v for k, v in os.environ.items() if not k.startswith(("KC_", "STUB_"))}
        base["PATH"] = self.laptop_stubs + os.pathsep + base.get("PATH", "")
        fake = {"FAKE_HOME": self.home, "FAKE_TMP": self.fake_tmp, "ROBOT_STUBS": self.robot_stubs,
                "FAKE_LOG": self.logs["calls"], "FAKE_SCRIPTS": self.logs["scripts"], "FAKE_SYSTEMCTL": self.logs["systemctl"],
                "FAKE_DEMO_PLAN": self.logs["demo-plan"], "FAKE_PASSES": self.logs["passes"],
                "FAKE_TOKENS": self.logs["tokens"], "FAKE_SSH_COUNT": self.logs["ssh-count"]}
        return subprocess.run(["bash", deploy, subcommand], env={**base, **fake, **self.identities, **env},
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, cwd=self.tmp.name)

    def lines(self, name):
        path = self.logs[name]
        return read_text(path).splitlines() if os.path.exists(path) else []

    def calls(self):
        return [line.split("\t", 1) for line in self.lines("calls")]

    def remote_scripts(self):
        text = read_text(self.logs["scripts"]) if os.path.exists(self.logs["scripts"]) else ""
        return [(r.split("\n", 1)[0], r.split("\n", 1)[1] if "\n" in r else "") for r in re.split(r"(?m)^=== ", text) if r]

    def systemctl_verbs(self):
        verbs = []
        for line in self.lines("systemctl"):
            words = [w for w in line.split() if not w.startswith("-")]
            verbs.append(words[0] if words else "")
        return verbs

    def forget(self):
        for path in self.logs.values():
            if os.path.exists(path):
                os.remove(path)

    def kroot(self, *parts):
        return os.path.join(self.home, "kc-lekiwi", *parts)

    def release(self, *parts):
        return os.path.join(os.path.realpath(self.kroot("current")), *parts)

    def unit_path(self):
        return os.path.join(self.home, ".config", "systemd", "user", UNIT_NAME)

    def token(self):
        return self.kroot("run", "arm-token")

    def install(self, **env):
        result = self.run_deploy("install", **env)
        return result, result.stdout + result.stderr

    def live_wiring(self):
        """What the live install IS, found the way systemd and the resident find it, whatever the layout: the unit file,
        the resident binary and the configuration its ExecStart names, the Plan and every store object that
        configuration names, the manifest beside the bundle, and the three child programs that manifest names. None
        when no unit file exists, which is the state of a robot computer with nothing wired."""
        if not os.path.lexists(self.unit_path()):
            return None
        unit = read_text(self.unit_path())
        found = re.search(r"(?m)^ExecStart=(\S+) --config (\S+)$", unit)
        wiring = {"unit": file_identity(self.unit_path()), "resident": file_identity(found.group(1)),
                  "config": file_identity(found.group(2)), "current": os.path.realpath(found.group(2))}
        if os.path.exists(found.group(2)):
            conf = dict(line.split(" = ", 1) for line in read_text(found.group(2)).splitlines())
            wiring["plan"] = file_identity(conf["plan_artifact"])
            for dirpath, _, names in os.walk(conf["store_root"]):
                for name in names:
                    wiring["store:" + os.path.join(dirpath, name)] = file_identity(os.path.join(dirpath, name))
            manifest = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(conf["plan_artifact"]))), "manifest.txt")
            wiring["manifest"] = file_identity(manifest)
            if os.path.exists(manifest):
                for key in ("child_command", "capability_child_command", "holder_child_command"):
                    value = re.search(rf"(?m)^{key} (.*)$", read_text(manifest)).group(1)
                    wiring[key] = (value, file_identity(value))
        return wiring

    def assert_nothing_wired(self):
        self.assertIsNone(self.live_wiring(), "a unit file was written")
        self.assertFalse(os.path.lexists(self.kroot("current")), "a current link was made")
        self.assertFalse(os.path.lexists(self.kroot("resident.conf")))
        self.assertNotIn("daemon-reload", self.systemctl_verbs())


class Install(FakeRobotHarness):
    def test_install_runs_to_the_end_and_never_starts_or_enables_the_unit(self):
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("daemon-reload", self.systemctl_verbs())
        for verb in self.systemctl_verbs():
            self.assertNotIn(verb, LIVE_WRITES, f"install sent 'systemctl {verb}'")
        everything = "\n".join(line for _, line in self.calls()) + "\n".join(s for _, s in self.remote_scripts())
        self.assertNotRegex(everything, r"systemctl\s+(--user\s+)?(start|enable|restart)\b")
        self.assertIn("NOTHING WAS STARTED OR ENABLED", result.stdout)
        self.assertEqual(self.lines("tokens"), [])
        self.assertFalse(os.path.exists(self.token()))

    def test_no_ssh_call_asks_for_a_terminal_and_every_call_is_batch_mode_to_the_given_target(self):
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        ssh_calls = [line for tool, line in self.calls() if tool == "ssh"]
        self.assertTrue(ssh_calls)
        for line in ssh_calls:
            self.assertNotRegex(line, r"(^|\s)-t{1,2}(\s|$)", "install asked for a terminal")
            self.assertTrue(line.startswith("-o BatchMode=yes -o ConnectTimeout=6 " + self.identities["KC_PI_SSH"] + " "), line)
        for tool, line in self.calls():
            if tool == "scp":
                self.assertIn("-o BatchMode=yes -o ConnectTimeout=6", line)

    def test_the_host_wrapper_carries_both_safety_flags_exactly_the_identities_and_the_arm_token(self):
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        wrapper = read_text(self.release("bin", "run-host.sh")).splitlines()
        for line in ("  --robot.disable_torque_on_disconnect=false \\", "  '--robot.cameras={}' \\",
                     "  --robot.max_relative_target=15.0 \\", "  --host.connection_time_s=86400",
                     f"PYTHON='{self.robot_python}'", "ROBOT_ID='example_robot'", "ROBOT_PORT='/dev/null'",
                     "HOST_ENTRY='" + self.release("app", "bin", "pi", "lekiwi_host_safe_attach.py") + "'",
                     f"ARM_TOKEN='{self.token()}'", "FUSER='" + os.path.join(self.robot_stubs, "fuser") + "'",
                     "SS='" + os.path.join(self.robot_stubs, "ss") + "'",
                     "FLOCK='" + shutil.which("flock", path="/usr/bin:/bin") + "'",
                     "WHEELS_LOCK='" + self.kroot("run", "wheels-safe-stop.lock") + "'"):
            self.assertIn(line, wrapper)
        self.assertNotIn("--robot.disable_torque_on_disconnect=true", "\n".join(wrapper))
        for name in ("run-host.sh", "run-capability.sh", "safe-stop.sh"):
            mode = os.stat(self.release("bin", name)).st_mode
            self.assertTrue(mode & stat.S_IXUSR, f"{name} is not executable")
            self.assertNotRegex("\n".join(l for l in read_text(self.release("bin", name)).splitlines() if not l.lstrip().startswith("#")), r"@@[A-Z_]+@@")
        capability = read_text(self.release("bin", "run-capability.sh"))
        self.assertIn('--identity "mobile_approach.pid$$"', capability)

    def test_build_mobile_runs_in_the_staging_release_signed_with_the_robot_key_and_never_the_gate_fixture_key(self):
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        builds = [l for l in self.lines("demo-plan") if l.startswith("build-mobile ")]
        self.assertEqual(len(builds), 1)
        self.assertNotIn("--gate-fixture-key", "\n".join(self.lines("demo-plan")))
        self.assertNotIn("--gate-fixture-key", read_text(DEPLOY))
        call = builds[0]
        self.assertTrue(self.release().startswith(self.kroot("releases") + os.sep))
        for fragment in ("--signing-key-file " + self.kroot("keys", "deployment.keypair"),
                         "--child-command " + self.release("bin", "run-host.sh"), "--child-stop-signal SIGINT",
                         "--capability-child-command " + self.release("bin", "run-capability.sh"),
                         "--capability-child-stop-signal SIGINT", "--out-dir " + self.release("bundle"),
                         "--safe-stop-command " + self.release("bin", "safe-stop.sh"),
                         "--capability-safe-stop-command " + self.release("bin", "safe-stop.sh")):
            self.assertIn(fragment, call)

    def test_the_key_seed_never_leaves_the_robot_and_is_never_printed(self):
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        key = self.kroot("keys", "deployment.keypair")
        self.assertEqual(stat.S_IMODE(os.stat(key).st_mode), 0o600)
        seed = re.search(r"(?m)^ed25519-seed ([0-9a-f]{64})$", read_text(key)).group(1)
        public = re.search(r"(?m)^ed25519-public ([0-9a-f]{64})$", read_text(key)).group(1)
        self.assertNotIn(seed, out, "the seed was printed")
        self.assertIn("deployment public key " + public, out)
        self.assertIn("deployment key id " + hashlib.sha256(bytes.fromhex(public)).hexdigest(), out)
        scp_calls = [line for tool, line in self.calls() if tool == "scp"]
        self.assertTrue(scp_calls)
        for line in scp_calls:
            words = line.split()
            sources = [w for i, w in enumerate(words[:-1]) if not w.startswith("-") and words[i - 1] != "-o"]
            self.assertTrue(sources, line)
            for source in sources:
                self.assertNotIn(":", source, "install copied something FROM the robot computer: " + line)
            self.assertIn(":", words[-1])
        for command, script in self.remote_scripts():
            self.assertNotRegex(command + script, r"cat [^|\n]*keypair|ed25519-seed", "a remote command reads the key file out")

    def test_resident_conf_and_unit_are_the_documented_ones_and_go_through_current(self):
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        self.assertTrue(os.path.islink(self.kroot("current")))
        self.assertTrue(os.readlink(self.kroot("current")).startswith("releases/"))
        public = re.search(r"(?m)^ed25519-public ([0-9a-f]{64})$", read_text(self.kroot("keys", "deployment.keypair"))).group(1)
        conf = dict(line.split(" = ", 1) for line in read_text(self.kroot("current", "resident.conf")).splitlines())
        self.assertEqual(conf["signature_policy"], "require")
        self.assertEqual(conf["trusted_keys"], public)
        self.assertEqual(conf["admin_socket_mode"], "0600")
        self.assertEqual(conf["admin_socket_path"], f"/run/user/{os.getuid()}/kc-lekiwi.sock")
        self.assertEqual(conf["target_profile"], "lekiwi-mvp")
        self.assertEqual(conf["plan_artifact"], self.release("bundle", "plans", "mobile-demo.cakepln"))
        self.assertEqual(conf["store_root"], self.release("bundle", "state-root"))
        self.assertEqual(stat.S_IMODE(os.stat(self.release("bundle", "state-root")).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.kroot("current", "resident.conf")).st_mode), 0o600)
        unit = read_text(self.unit_path()).splitlines()
        self.assertIn("ExecStart=" + self.kroot("current", "bin", "cake-resident") + " --config " + self.kroot("current", "resident.conf"), unit)
        for line in ("Type=simple", "Restart=on-abnormal", "RestartSec=2", "KillMode=mixed", "KillSignal=SIGTERM", "TimeoutStopSec=60"):
            self.assertIn(line, unit)
        self.assertNotIn("[Install]", unit)
        self.assertTrue(os.path.exists(self.release("VERIFIED")))
        check = subprocess.run(["sha256sum", "--quiet", "-c", "VERIFIED"], cwd=self.release(), capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_a_refused_first_install_leaves_nothing_wired_and_says_so(self):
        for env in ({"STUB_MANIFEST_CHILD": "/opt/elsewhere/run-child.sh"},
                    {"STUB_MANIFEST_CAPABILITY_CHILD": "/opt/elsewhere/run-capability.sh"},
                    {"STUB_MANIFEST_HOLDER": "/opt/elsewhere/holder.sh"},
                    {"STUB_PREFLIGHT": "refuse"}, {"STUB_BUILD_FAIL": "1"}, {"STUB_SCP_CORRUPT": "cake-resident"}):
            with self.subTest(env=env):
                self.forget()
                result, out = self.install(**env)
                self.assertNotEqual(result.returncode, 0, out)
                self.assertIn("REFUSE:", out)
                self.assertIn("STATE LEFT: nothing is wired", out)
                self.assert_nothing_wired()
                self.assertEqual([n for n in os.listdir(self.kroot("releases")) if os.path.exists(self.kroot("releases", n, "VERIFIED"))], [])

    def test_a_refused_reinstall_leaves_the_previous_install_byte_for_byte_and_still_wired(self):
        first, out = self.install()
        self.assertEqual(first.returncode, 0, out)
        before = self.live_wiring()
        previous = os.readlink(self.kroot("current"))
        for env, words, status in (({"STUB_MANIFEST_CHILD": "/opt/elsewhere/run-child.sh"}, "REFUSE: the manifest's child_command", 3),
                                   ({"STUB_MANIFEST_CAPABILITY_CHILD": "/opt/elsewhere/run-capability.sh"}, "REFUSE: the manifest's capability_child_command", 3),
                                   ({"STUB_MANIFEST_HOLDER": "/opt/elsewhere/holder.sh"}, "REFUSE: the manifest's holder_child_command", 3),
                                   ({"STUB_PREFLIGHT": "refuse"}, "demo-preflight refused capsule", 3),
                                   ({"STUB_BUILD_FAIL": "1"}, "demo-plan build-mobile did not produce", 1),
                                   ({"STUB_SCP_CORRUPT": "cake-resident"}, "checksum mismatch for bin/cake-resident", 4)):
            with self.subTest(env=env):
                self.forget()
                result, out = self.install(**env)
                self.assertEqual(result.returncode, status, out)
                self.assertIn(words, out)
                self.assertIn(f"STATE LEFT: the previous install is untouched and still wired: current -> {previous}", out)
                self.assertEqual(self.live_wiring(), before, "the refused reinstall changed the live install")
                self.assertEqual(os.readlink(self.kroot("current")), previous)
                self.assertNotIn("daemon-reload", self.systemctl_verbs())
                for verb in self.systemctl_verbs():
                    self.assertNotIn(verb, LIVE_WRITES)

    def test_an_active_legacy_unit_or_a_running_deployment_refuses_before_anything_is_copied(self):
        for unit in ("kiwi-cake-demo.service", "cake-resident.service", UNIT_NAME):
            self.forget()
            result, out = self.install(STUB_ACTIVE_UNITS=unit)
            self.assertEqual(result.returncode, 3, out)
            self.assertIn("REFUSE:", out)
            self.assertIn(unit, out)
            self.assertIn("STATE LEFT: nothing was changed on the robot computer", out)
            self.assertEqual([tool for tool, _ in self.calls()], ["ssh"], "anything beyond the read-only preflight ran")
            self.assertFalse(os.path.exists(self.kroot()))

    def test_an_install_in_the_earlier_layout_is_refused_and_left_alone(self):
        os.makedirs(self.kroot("bundle"))
        with open(self.kroot("resident.conf"), "w", encoding="utf-8") as handle:
            handle.write("plan_artifact = elsewhere\n")
        result, out = self.install()
        self.assertEqual(result.returncode, 3, out)
        self.assertIn("earlier single-directory layout", out)
        self.assertEqual(read_text(self.kroot("resident.conf")), "plan_artifact = elsewhere\n")
        self.assertFalse(os.path.lexists(self.kroot("current")))

    def test_a_board_that_is_not_aarch64_is_refused(self):
        result, out = self.install(STUB_ARCH="x86_64")
        self.assertEqual(result.returncode, 3, out)
        self.assertIn("not aarch64", out)

    def test_a_reinstall_keeps_the_key_and_the_previous_release_and_switches_current(self):
        first, out = self.install()
        self.assertEqual(first.returncode, 0, out)
        key = read_text(self.kroot("keys", "deployment.keypair"))
        previous = os.readlink(self.kroot("current"))
        previous_files = {os.path.join(d, n): file_identity(os.path.join(d, n))
                          for d, _, names in os.walk(self.kroot(previous)) for n in names}
        second, out = self.install()
        self.assertEqual(second.returncode, 0, out)
        self.assertEqual(read_text(self.kroot("keys", "deployment.keypair")), key)
        self.assertIn("deployment key: present, kept", out)
        self.assertIn("previous install: " + previous, out)
        self.assertNotEqual(os.readlink(self.kroot("current")), previous)
        self.assertEqual(len(os.listdir(self.kroot("releases"))), 2)
        self.assertEqual({p: file_identity(p) for p in previous_files}, previous_files, "the previous release was modified")

    def test_nothing_printed_names_the_remote_home(self):
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        self.assertNotIn(self.home, out)


# What the kernel lists for a host whose command socket is on loopback only (ss -H -ltn columns). WITHOUT a process
# column: arm then cannot tell whose listener it is and refuses; host_listener() below adds the supervised host's pid.
LOOPBACK_LISTENER = "LISTEN 0 100 127.0.0.1:5555 0.0.0.0:*\nLISTEN 0 100 0.0.0.0:5556 0.0.0.0:*"


class ArmAndStop(FakeRobotHarness):
    def host_listener(self, parent_is_main=True):
        """A stand-in for the supervised host: a process whose command line names the safe-attach wrapper and whose parent
        is this test (reported as the unit's MainPID when parent_is_main). Returns the env for run_deploy."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", "/opt/example/app/bin/pi/lekiwi_host_safe_attach.py"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        table = f'LISTEN 0 100 127.0.0.1:5555 0.0.0.0:* users:(("python3",pid={proc.pid},fd=7))\nLISTEN 0 100 0.0.0.0:5556 0.0.0.0:*'
        main = os.getpid() if parent_is_main else os.getpid() + 1000000
        return {"STUB_SS_OUTPUT": table, "STUB_MAIN_PID": str(main)}, proc.pid, table

    def installed(self):
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        self.forget()

    def test_arm_refuses_unless_the_unit_is_installed(self):
        result = self.run_deploy("arm", STUB_CLAIM_TOKEN="1")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, out)
        self.assertIn("is not installed", out)
        self.assertIn("Nothing was armed or started", out)
        self.assertEqual([v for v in self.systemctl_verbs() if v in LIVE_WRITES + ("stop",)], [])
        self.assertFalse(os.path.exists(self.token()))

    def test_arm_says_the_operator_must_be_present_writes_a_600_token_then_restarts(self):
        self.installed()
        env, host_pid, _ = self.host_listener()
        result = self.run_deploy("arm", STUB_CLAIM_TOKEN="1", **env)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(f"held by the supervised host (pid {host_pid})", out)
        self.assertIn("The host itself checks nothing", out)
        self.assertIn("THE OPERATOR MUST BE PRESENT", out)
        self.assertIn("WILL ATTACH TO THE SERVOS", out)
        self.assertIn("ARMED: the host wrapper claimed the arm token", out)
        self.assertIn("the wrapper still refuses on the wheel stop's lock held past its 2 s wait, a servo owner or a 5555/5556 "
                      "listener", out)
        # arm says, before it arms, that the release it arms carries the wheel stop that line relies on.
        self.assertLess(out.index("wheel stop: configured in the current release"), out.index("ARM: THE OPERATOR MUST BE PRESENT"))
        self.assertEqual(self.lines("tokens"), ["restart 600"], "the token was not in place, mode 600, when the unit restarted")
        live = [v for v in self.systemctl_verbs() if v in LIVE_WRITES]
        self.assertEqual(live, ["restart"])
        self.assertIn(f"--user restart {UNIT_NAME}", self.lines("systemctl"))
        self.assertFalse(os.path.exists(self.token()))
        self.assertIn(f"ARM_TOKEN='{self.token()}'", read_text(self.kroot("current", "bin", "run-host.sh")).splitlines())
        self.assertIn("CONFIGURED AND ACTUAL: run-host.sh exports KC_HOST_COMMAND_LOOPBACK=1 and the kernel lists 5555 on 127.0.0.1 only", out)

    def test_arm_stops_a_running_unit_before_it_writes_the_token(self):
        self.installed()
        env, _, _ = self.host_listener()
        result = self.run_deploy("arm", STUB_CLAIM_TOKEN="1", STUB_ACTIVE_UNITS=UNIT_NAME, **env)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        verbs = [v for v in self.systemctl_verbs() if v in ("stop", "restart", "start")]
        self.assertEqual(verbs, ["stop", "restart"])
        self.assertEqual(self.lines("tokens"), ["restart 600"])

    def test_arm_leaves_no_token_when_nothing_claims_it_or_the_restart_fails(self):
        self.installed()
        for env, status, words in (({"KC_ARM_WAIT_S": "1"}, 1, "NOT ARMED"),
                                   ({"STUB_RESTART_FAIL": "1"}, 3, "restart kiwi-cake-lekiwi.service failed")):
            with self.subTest(env=env):
                self.forget()
                result = self.run_deploy("arm", **env)
                out = result.stdout + result.stderr
                self.assertEqual(result.returncode, status, out)
                self.assertIn(words, out)
                self.assertEqual(self.lines("tokens"), ["restart 600"])
                self.assertFalse(os.path.exists(self.token()), "arm left a token behind")
                self.assertEqual(os.listdir(self.kroot("run")), [])

    def test_arm_refuses_a_deployment_whose_launch_does_not_put_the_command_socket_on_loopback(self):
        self.installed()
        wrapper = self.kroot("current", "bin", "run-host.sh")
        text = read_text(wrapper)
        self.assertIn("\nexport KC_HOST_COMMAND_LOOPBACK=1\n", text)
        os.chmod(wrapper, 0o755)
        with open(wrapper, "w", encoding="utf-8") as handle:
            handle.write(text.replace("\nexport KC_HOST_COMMAND_LOOPBACK=1\n", "\n"))
        result = self.run_deploy("arm", STUB_CLAIM_TOKEN="1", STUB_SS_OUTPUT=LOOPBACK_LISTENER)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, out)
        self.assertIn("does not put the command socket on loopback", out)
        self.assertEqual(self.lines("tokens"), [], "a token was written for a launch that would expose the command socket")
        self.assertEqual([v for v in self.systemctl_verbs() if v in LIVE_WRITES], [])

    def test_arm_stops_the_unit_when_the_kernel_lists_the_command_socket_on_another_address(self):
        self.installed()
        for listener in ("LISTEN 0 100 0.0.0.0:5555 0.0.0.0:*", "LISTEN 0 100 *:5555 *:*", "LISTEN 0 100 [::]:5555 [::]:*",
                         LOOPBACK_LISTENER + "\nLISTEN 0 100 0.0.0.0:5555 0.0.0.0:*"):
            with self.subTest(listener=listener):
                self.forget()
                result = self.run_deploy("arm", STUB_CLAIM_TOKEN="1", STUB_SS_OUTPUT=listener)
                out = result.stdout + result.stderr
                self.assertEqual(result.returncode, 3, out)
                self.assertIn("REFUSE: the command socket is reachable from the network, or did not settle on loopback; stopping", out)
                self.assertIn("command socket: EXPOSED on", out)
                self.assertEqual([v for v in self.systemctl_verbs() if v in ("restart", "stop")], ["restart", "stop"])

    def test_arm_refuses_a_deployment_whose_host_wrapper_lacks_the_loopback_block(self):
        self.installed()
        wrapper = self.kroot("current", "app", "bin", "pi", "lekiwi_host_safe_attach.py")
        text = read_text(wrapper)
        os.chmod(wrapper, 0o644)
        with open(wrapper, "w", encoding="utf-8") as handle:
            handle.write(text.replace("# --- HOST COMMAND LOOPBACK begin", "# --- (removed) begin"))
        env, _, _ = self.host_listener()
        result = self.run_deploy("arm", STUB_CLAIM_TOKEN="1", **env)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, out)
        self.assertIn("the deployed host has no command loopback block", out)
        self.assertEqual(self.lines("tokens"), [])

    def test_arm_refuses_a_loopback_listener_that_is_not_the_supervised_host(self):
        self.installed()
        for variant in ("no process column", "another parent"):
            with self.subTest(variant=variant):
                self.forget()
                if variant == "no process column":
                    env = {"STUB_SS_OUTPUT": LOOPBACK_LISTENER, "STUB_MAIN_PID": str(os.getpid())}
                else:
                    env, _, _ = self.host_listener(parent_is_main=False)
                result = self.run_deploy("arm", STUB_CLAIM_TOKEN="1", **env)
                out = result.stdout + result.stderr
                self.assertEqual(result.returncode, 3, out)
                self.assertIn("is not the host Cake supervises", out)
                self.assertEqual([v for v in self.systemctl_verbs() if v in ("restart", "stop")], ["restart", "stop"])

    def test_an_exposed_first_reading_is_cleared_only_by_a_clean_loopback_reading(self):
        self.installed()
        env, host_pid, table = self.host_listener()
        exposed = "LISTEN 0 100 0.0.0.0:5555 0.0.0.0:*"
        # arm reads: the wait loop, then the re-read one second later, then the printed verdict, then the ownership read.
        for sequence, status, words in (((exposed, table), 0, f"held by the supervised host (pid {host_pid})"),
                                        ((exposed, ""), 3, "did not settle on loopback"),
                                        ((exposed, exposed), 3, "did not settle on loopback")):
            with self.subTest(sequence=[x[:20] for x in sequence]):
                self.forget()
                seq_env = dict(env)
                seq_env.pop("STUB_SS_OUTPUT")
                seq_env["STUB_SS_SEQUENCE"] = "||".join(x.replace("\n", "\\n") for x in sequence)
                result = self.run_deploy("arm", STUB_CLAIM_TOKEN="1", **seq_env)
                out = result.stdout + result.stderr
                self.assertEqual(result.returncode, status, out)
                self.assertIn(words, out)

    def test_status_prints_the_kernel_verdict_and_the_configured_binding(self):
        self.installed()
        env, _, _ = self.host_listener()
        result = self.run_deploy("status", **env)
        out = result.stdout + result.stderr
        self.assertIn("command socket: loopback only (127.0.0.1:5555), as the kernel lists it", out)
        self.assertIn("configured: run-host.sh puts the command socket on loopback", out)
        result = self.run_deploy("status", STUB_SS_OUTPUT="LISTEN 0 100 0.0.0.0:5555 0.0.0.0:*")
        self.assertIn("command socket: EXPOSED on 0.0.0.0:5555", result.stdout + result.stderr)

    def test_arm_reports_a_host_that_never_bound_its_command_socket(self):
        self.installed()
        result = self.run_deploy("arm", STUB_CLAIM_TOKEN="1")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn("NO COMMAND SOCKET within 60 s", out)

    def test_arm_stops_the_unit_when_the_listeners_cannot_be_read(self):
        self.installed()
        result = self.run_deploy("arm", STUB_CLAIM_TOKEN="1", STUB_SS="error")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, out)
        self.assertIn("REFUSE: the listeners could not be read back", out)
        self.assertEqual([v for v in self.systemctl_verbs() if v in ("restart", "stop")], ["restart", "stop"])

    def test_stop_stops_the_unit_then_removes_any_token(self):
        self.installed()
        with open(self.token(), "w", encoding="utf-8") as handle:
            handle.write("armed\n")
        result = self.run_deploy("stop")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(f"--user stop {UNIT_NAME}", self.lines("systemctl"))
        self.assertEqual([v for v in self.systemctl_verbs() if v in LIVE_WRITES], [])
        self.assertFalse(os.path.exists(self.token()))
        self.assertIn("no arm token remains", out)

    def test_arm_is_the_only_subcommand_that_starts_the_unit(self):
        self.installed()
        for subcommand in ("check", "status", "prove-signature", "fetch-candidates", "approach-probe", "stop", "install"):
            self.run_deploy(subcommand)
        self.assertEqual([v for v in self.systemctl_verbs() if v in LIVE_WRITES], [])
        self.assertEqual(self.lines("tokens"), [])
        self.run_deploy("arm", STUB_CLAIM_TOKEN="1")
        self.assertEqual([v for v in self.systemctl_verbs() if v in LIVE_WRITES], ["restart"])


class OtherSubcommands(FakeRobotHarness):
    def test_identities_are_required(self):
        for missing in ("KC_PI_SSH", "KC_ROBOT_ID", "KC_SERVO_PORT"):
            env = dict(self.identities)
            env.pop(missing)
            saved, self.identities = self.identities, env
            try:
                result = self.run_deploy("install")
            finally:
                self.identities = saved
            self.assertEqual(result.returncode, 5, missing)
            self.assertEqual(self.calls(), [])
        self.assertEqual(self.run_deploy("install", KC_PI_SSH="x;rm").returncode, 5)
        self.assertEqual(self.run_deploy("status", KC_LEKIWI_DIR="../elsewhere").returncode, 5)
        self.assertEqual(self.run_deploy("arm", KC_ARM_WAIT_S="0").returncode, 5)
        self.assertEqual(self.run_deploy("arm", KC_ARM_WAIT_S="5s").returncode, 5)

    def test_check_refuses_on_an_active_legacy_unit_a_listener_or_an_owner(self):
        for env, words in (({"STUB_ACTIVE_UNITS": "cake-resident.service"}, "cake-resident.service is active"),
                           ({"STUB_SS_OUTPUT": "LISTEN 0 100 0.0.0.0:5556 0.0.0.0:*"}, "listens on 5555 or 5556"),
                           ({"STUB_FUSER": "owned"}, "servo bus already has an owner")):
            self.forget()
            result = self.run_deploy("check", **env)
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertIn(words, result.stdout + result.stderr)
            self.assertEqual(self.lines("passes"), [], "start-host.sh check ran after a refusal")

    def test_check_fails_closed_when_fuser_or_ss_cannot_run(self):
        for env in ({"STUB_FUSER": "cannot-run"}, {"STUB_FUSER": "error"}, {"STUB_FUSER": "blind"},
                    {"STUB_SS": "cannot-run"}, {"STUB_SS": "error"}):
            with self.subTest(env=env):
                self.forget()
                result = self.run_deploy("check", **env)
                out = result.stdout + result.stderr
                self.assertEqual(result.returncode, 3, out)
                self.assertIn("cannot run", out)
                self.assertRegex(out, r"fails? closed")
                self.assertNotIn("deployment preflight ok", out)
                self.assertEqual(self.lines("passes"), [], "start-host.sh check ran after a refusal")

    def test_check_fails_closed_when_fuser_or_ss_is_not_installed(self):
        sysbin = os.path.join(self.tmp.name, "minimal-bin")
        os.mkdir(sysbin)
        for tool in MINIMAL_TOOLS:
            found = shutil.which(tool, path="/usr/bin:/bin")
            if found:
                os.symlink(found, os.path.join(sysbin, tool))
        for missing, words in (("fuser", "fuser is not installed"), ("ss", "ss is not installed")):
            with self.subTest(missing=missing):
                self.forget()
                os.rename(os.path.join(self.robot_stubs, missing), os.path.join(self.tmp.name, missing + ".aside"))
                try:
                    result = self.run_deploy("check", STUB_ROBOT_SYSPATH=sysbin)
                finally:
                    os.rename(os.path.join(self.tmp.name, missing + ".aside"), os.path.join(self.robot_stubs, missing))
                out = result.stdout + result.stderr
                self.assertEqual(result.returncode, 3, out)
                self.assertIn(words, out)
                self.assertRegex(out, r"fails? closed")
                self.assertEqual(self.lines("passes"), [])

    def test_check_runs_the_start_host_check_deploys_nothing_and_states_what_fuser_sees(self):
        result = self.run_deploy("check")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("HOST: NOT STARTED", out)
        self.assertIn("fuser runs here without root, so it sees only this account's processes", out)
        self.assertEqual([p.split()[1] for p in self.lines("passes")], ["--verify-robot"])
        self.assertNotIn("scp", [tool for tool, _ in self.calls()])
        for _, line in self.calls():
            self.assertNotRegex(line, r"(^|\s)-t(\s|$)")
            self.assertNotIn("mkdir", line)
        self.assertFalse(os.path.exists(os.path.join(self.home, "m1-host-attach")))
        self.assertEqual([v for v in self.systemctl_verbs() if v not in ("is-active", "show")], [])

    def test_no_path_of_check_prints_an_unmasked_private_address(self):
        address = ".".join(["192", "168", "7", "42"])
        # The deployment preflight, start-host's preflight, its locate call, then its first read, whose ssh stderr
        # start-host.sh drops (so nothing is printed there to mask).
        for after in (0, 1, 2, 3):
            with self.subTest(fail_after=after):
                self.forget()
                result = self.run_deploy("check", STUB_SSH_FAIL=address, STUB_SSH_FAIL_AFTER=str(after))
                out = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0, out)
                self.assertNotIn(address, out)
                if after < 3:
                    self.assertIn("<private-ip>", out)

    def test_start_host_redacts_with_the_same_expression_as_lekiwi_deploy(self):
        start_host = os.path.join(ROOT, "bin", "pi", "start-host.sh")
        mine = [l for l in read_text(DEPLOY).splitlines() if l.startswith("redact() {")]
        theirs = [l for l in read_text(start_host).splitlines() if l.startswith("redact() {")]
        self.assertEqual(len(mine), 1)
        self.assertEqual(theirs, mine)

    def test_status_is_read_only_and_shows_the_release_and_the_token(self):
        self.assertEqual(self.install()[0].returncode, 0)
        self.forget()
        result = self.run_deploy("status")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        for want in ("== install", "current -> releases/", "(VERIFIED)", "arm token: none", "== unit",
                     "== admin-probe list-slots", "== admin-probe get-health", "== admin-probe read-flight",
                     "== listeners on 5555/5556", "== device descriptors", "== supervisor.log per slot", "== journal"):
            self.assertIn(want, out)
        self.assertTrue(set(self.systemctl_verbs()) <= {"show", "is-active", "is-enabled"}, self.systemctl_verbs())
        self.assertNotIn("scp", [tool for tool, _ in self.calls()])

    def test_prove_signature_shows_each_verdict_and_the_resident_refusal_in_a_scratch_directory(self):
        self.assertEqual(self.install()[0].returncode, 0)
        store_before = sorted(os.listdir(self.kroot("current", "bundle", "state-root", "objects")))
        conf_before = read_text(self.kroot("current", "resident.conf"))
        self.forget()
        result = self.run_deploy("prove-signature")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertEqual(out.count("verdict admitted"), 3)
        self.assertEqual(out.count("reason signature_key_id_not_trusted"), 3)
        self.assertIn("reason signature_missing", out)
        self.assertIn("is not in the configured trusted key list", out)
        self.assertIn("prove-signature: PASS", out)
        self.assertEqual([n for n in os.listdir(self.kroot("run")) if n.startswith("prove.")], [], "the scratch directory was left behind")
        self.assertEqual(sorted(os.listdir(self.kroot("current", "bundle", "state-root", "objects"))), store_before)
        self.assertEqual(read_text(self.kroot("current", "resident.conf")), conf_before)
        self.assertNotIn("--gate-fixture-key", "\n".join(self.lines("demo-plan")))
        self.assertEqual([v for v in self.systemctl_verbs()], [])

    def test_fetch_candidates_copies_both_plans_back_verified(self):
        self.assertEqual(self.install()[0].returncode, 0)
        result = self.run_deploy("fetch-candidates")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertEqual(stat.S_IMODE(os.stat(self.candidates).st_mode), 0o700)
        for name in ("mobile-demo-swapped.cakepln", "mobile-demo-active-successor-abort-swap.cakepln"):
            with open(os.path.join(self.candidates, name), "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            self.assertIn(f"candidate {name} sha256 {digest}", out)

    def test_approach_probe_runs_the_deployed_probe_and_it_refuses_when_no_slot_is_reported(self):
        self.assertEqual(self.install()[0].returncode, 0)
        self.forget()
        result = self.run_deploy("approach-probe")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, out)
        record = __import__("json").loads(result.stdout.strip().splitlines()[-1])
        self.assertIn("not ACTIVE on the expected mobile capsule", record["refused"])
        self.assertEqual(record["recording_destination"], "transport.FakeBaseMotionTransport")
        self.assertEqual(record["lerobot_modules_loaded"], [])
        self.assertNotIn(self.home, out)
        self.assertEqual(self.systemctl_verbs(), [])

    def test_an_unreachable_robot_never_prints_its_private_address(self):
        address = ".".join(["192", "168", "7", "42"])
        for subcommand in ("status", "arm", "stop", "install"):
            with self.subTest(subcommand=subcommand):
                result = self.run_deploy(subcommand, STUB_SSH_FAIL=address)
                self.assertEqual(result.returncode, 255)
                self.assertNotIn(address, result.stdout + result.stderr)
                self.assertIn("<private-ip>", result.stderr)


class WheelStop(FakeRobotHarness):
    """The safe-stop's wheel stop as install deploys it: bin/pi/wheels_safe_stop.py staged and verified with the other
    application files, the four settings of bin/templates/safe-stop.sh.in rendered from this deployment's identities, the
    helper's self-check run under the robot's python before anything is committed, and status showing its logs."""

    def rendered(self):
        return read_text(self.release("bin", "safe-stop.sh")).splitlines()

    def test_the_safe_stop_is_rendered_with_the_wheel_stop_of_this_deployment(self):
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        helper = self.release("app", "bin", "pi", "wheels_safe_stop.py")
        lines = self.rendered()
        for line in (f"WHEELS_PYTHON='{self.robot_python}'", f"WHEELS_HELPER='{helper}'", "WHEELS_PORT='/dev/null'",
                     "WHEELS_LOCK='" + self.kroot("run", "wheels-safe-stop.lock") + "'",
                     'timeout -s KILL 0.42 "$WHEELS_PYTHON" -u "$WHEELS_HELPER" --port "$WHEELS_PORT" --lock "$WHEELS_LOCK" >>"$out" 2>&1 </dev/null'):
            self.assertIn(line, lines)
        for name in ("WHEELS_PYTHON", "WHEELS_HELPER", "WHEELS_PORT", "WHEELS_LOCK"):
            self.assertEqual([l for l in lines if l.startswith(name + "=")], [l for l in lines if l.startswith(name + "='/")])
        with open(os.path.join(ROOT, "bin", "pi", "wheels_safe_stop.py"), "rb") as handle:
            want = hashlib.sha256(handle.read()).hexdigest()
        with open(helper, "rb") as handle:
            self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), want)
        verified = read_text(self.release("VERIFIED"))
        for path in ("bin/safe-stop.sh", "app/bin/pi/wheels_safe_stop.py"):
            self.assertRegex(verified, rf"(?m)^[0-9a-f]{{64}}  {re.escape(path)}$")
        self.assertIn("SELF-CHECK ok", out)
        self.assertIn("no device was opened", out)

    def test_the_host_wrapper_takes_the_lock_the_safe_stop_takes(self):
        # A wheel stop writes only while it holds its lock, and the host wrapper takes that same lock before its owner
        # check and hands it to the host: both files must name one lock file, and the wrapper the robot's flock.
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        lock = self.kroot("run", "wheels-safe-stop.lock")
        wrapper = read_text(self.release("bin", "run-host.sh")).splitlines()
        self.assertEqual([l for l in wrapper if l.startswith("WHEELS_LOCK=")], [f"WHEELS_LOCK='{lock}'"])
        self.assertEqual([l for l in self.rendered() if l.startswith("WHEELS_LOCK=")], [f"WHEELS_LOCK='{lock}'"])
        self.assertEqual([l for l in wrapper if l.startswith("FLOCK=")], ["FLOCK='" + shutil.which("flock", path="/usr/bin:/bin") + "'"])

    def install_from_a_copy_with(self, edits):
        """install, run from a copy of this checkout in which each (PATH, LINE, REPLACEMENT) of EDITS replaces the one line
        LINE of PATH, a path relative to the checkout, with the lines REPLACEMENT (an empty list removes it)."""
        tree = os.path.join(self.tmp.name, "checkout-copy")
        shutil.rmtree(tree, ignore_errors=True)
        for part in ("bin", os.path.join("autonomy", "motion")):
            shutil.copytree(os.path.join(ROOT, part), os.path.join(tree, part), ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(os.path.join(ROOT, "VERSION"), os.path.join(tree, "VERSION"))
        for relative, line, replacement in edits:
            path = os.path.join(tree, relative)
            lines = read_text(path).splitlines(keepends=True)
            self.assertEqual(lines.count(line + "\n"), 1, f"{relative} has no single line {line!r}")
            at = lines.index(line + "\n")
            lines[at:at + 1] = [new + "\n" for new in replacement]
            with open(path, "w", encoding="utf-8") as handle:
                handle.writelines(lines)
        result = self.run_deploy("install", deploy=os.path.join(tree, "bin", "pi", "lekiwi-deploy.sh"))
        return result, result.stdout + result.stderr

    def install_from_a_copy_without(self, template, line):
        """install, run from a copy of this checkout in which bin/templates/TEMPLATE lacks LINE: a template that still
        renders and parses, so the install's own check of the rendered lines is all that stands in the way."""
        return self.install_from_a_copy_with([(os.path.join("bin", "templates", template), line, [])])

    # A checkout from before the wheel stop, as far as the release it installs shows: a safe-stop template without its four
    # wheel settings, so that the rendered safe-stop only logs; a host wrapper template that names neither flock nor the
    # wheel stop's lock; and an installer that looks for none of them in what it rendered.
    WITHOUT_THE_WHEEL_STOP = [(os.path.join("bin", "templates", "safe-stop.sh.in"), f"WHEELS_{name}=''", [])
                              for name in ("PYTHON", "HELPER", "PORT", "LOCK")] + [
        (os.path.join("bin", "templates", "run-host.sh.in"), "FLOCK='@@FLOCK@@'", []),
        (os.path.join("bin", "templates", "run-host.sh.in"), "WHEELS_LOCK='@@WHEELS_LOCK@@'", []),
        (os.path.join("bin", "pi", "lekiwi-deploy.sh"), "  \"FLOCK='$FLOCK_BIN'\" \"WHEELS_LOCK='$WHEELS_LOCK'\" \\", []),
        (os.path.join("bin", "pi", "lekiwi-deploy.sh"),
         '  grep -qxF -- "$want" "$SAFESTOP" || refuse "the rendered safe-stop lacks the line: $want"', ["  :"]),
    ]

    def assert_arm_refuses_for_want_of_the_wheel_stop(self, gap):
        """arm, on the current release: refused before any token or unit change, with GAP as the reason, and no claim
        that the wrapper waits on the wheel stop's lock."""
        result = self.run_deploy("arm", STUB_CLAIM_TOKEN="1")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, out)
        lines = out.splitlines()
        said = [i for i, line in enumerate(lines) if line.startswith("wheel stop: ")]
        self.assertEqual(len(said), 1, out)
        self.assertTrue(lines[said[0]].startswith("wheel stop: MISSING from the current release: " + gap), out)
        self.assertIn("arm refuses this release: run install again", lines[said[0]])
        self.assertEqual(lines[said[0] + 1], "REFUSE: the current release has no complete wheel stop (the line above says what is missing).")
        self.assertIn("Nothing was armed or started.", out)
        self.assertNotIn("ARMED:", out)
        self.assertNotIn("THE OPERATOR MUST BE PRESENT", out)
        self.assertNotIn("the wrapper still refuses on the wheel stop's lock", out)
        self.assertEqual(self.lines("tokens"), [], "the unit was restarted for a release without a complete wheel stop")
        self.assertEqual([v for v in self.systemctl_verbs() if v in LIVE_WRITES + ("stop",)], [])
        self.assertFalse(os.path.exists(self.token()), "arm left a token behind")

    def assert_status_says_the_wheel_stop_is_missing(self, gap):
        result = self.run_deploy("status")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("wheel stop: MISSING from the current release: " + gap, out)
        self.assertNotIn("wheel stop: configured", out)
        self.assertTrue(set(self.systemctl_verbs()) <= {"show", "is-active", "is-enabled"}, self.systemctl_verbs())

    def test_arm_refuses_a_release_without_the_wheel_stop_and_status_names_it_also_after_a_refused_reinstall(self):
        # verify-r4 MF1. A release installed from a checkout without the wheel stop is current: its safe-stop only logs
        # and its host wrapper takes no lock. Then the install of THIS checkout is refused (the robot's python is too slow
        # for the wheel stop), which leaves that release current, the likeliest way a robot gets there. Both times arm
        # must refuse it, before any token, and must not claim the wheel stop's lock; status must say it has none.
        result, out = self.install_from_a_copy_with(self.WITHOUT_THE_WHEEL_STOP)
        self.assertEqual(result.returncode, 0, out)
        self.assertEqual([l for l in self.rendered() if l.startswith("WHEELS_")], [])
        wrapper = read_text(self.release("bin", "run-host.sh")).splitlines()
        self.assertEqual([l for l in wrapper if l.startswith(("FLOCK=", "WHEELS_LOCK="))], [])
        gap = "its safe-stop does not run the wheel stop"
        self.forget()
        self.assert_arm_refuses_for_want_of_the_wheel_stop(gap)
        self.forget()
        self.assert_status_says_the_wheel_stop_is_missing(gap)
        previous = os.readlink(self.kroot("current"))
        self.slow_robot_sdk()
        self.forget()
        result, out = self.install()
        self.assertEqual(result.returncode, 3, out)
        self.assertIn("SELF-CHECK TOO SLOW", out)
        self.assertIn(f"STATE LEFT: the previous install is untouched and still wired: current -> {previous}", out)
        self.assertEqual(os.readlink(self.kroot("current")), previous)
        self.forget()
        self.assert_arm_refuses_for_want_of_the_wheel_stop(gap)
        self.forget()
        self.assert_status_says_the_wheel_stop_is_missing(gap)

    def test_arm_refuses_a_current_release_whose_wheel_stop_is_incomplete(self):
        # Each piece arm and status look for, taken from a release this checkout installed: the four settings that make the
        # safe-stop run the wheel stop, the wheel stop they name, and the one lock both files must name.
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        self.forget()
        status = self.run_deploy("status")
        self.assertIn("wheel stop: configured in the current release", status.stdout + status.stderr)
        stop, wrapper = self.release("bin", "safe-stop.sh"), self.release("bin", "run-host.sh")
        helper = self.release("app", "bin", "pi", "wheels_safe_stop.py")
        lock = self.kroot("run", "wheels-safe-stop.lock")
        originals = {path: read_text(path) for path in (stop, wrapper, helper)}
        runs, missing = "its safe-stop does not run the wheel stop", "the wheel stop its safe-stop names is missing"
        one_lock = "its safe-stop and its host wrapper do not both name the wheel stop's lock"
        for name, path, line, replacement, gap in (
                ("the port setting left empty", stop, "WHEELS_PORT='/dev/null'", ["WHEELS_PORT=''"], runs),
                ("the python setting left empty", stop, f"WHEELS_PYTHON='{self.robot_python}'", ["WHEELS_PYTHON=''"], runs),
                ("the helper setting left empty", stop, f"WHEELS_HELPER='{helper}'", ["WHEELS_HELPER=''"], runs),
                ("the lock setting left empty", stop, f"WHEELS_LOCK='{lock}'", ["WHEELS_LOCK=''"], runs),
                ("a relative port", stop, "WHEELS_PORT='/dev/null'", ["WHEELS_PORT='null'"], runs),
                ("a port outside /dev/", stop, "WHEELS_PORT='/dev/null'", ["WHEELS_PORT='/tmp/not-a-device'"], runs),
                ("the helper file removed", helper, None, None, missing),
                ("the safe-stop names another lock", stop, f"WHEELS_LOCK='{lock}'", ["WHEELS_LOCK='/tmp/another.lock'"], one_lock),
                ("the host wrapper names no lock", wrapper, f"WHEELS_LOCK='{lock}'", [], one_lock),
                ("the host wrapper names another lock", wrapper, f"WHEELS_LOCK='{lock}'", ["WHEELS_LOCK='/tmp/another.lock'"], one_lock)):
            with self.subTest(case=name):
                for original, text in originals.items():
                    with open(original, "w", encoding="utf-8") as handle:
                        handle.write(text)
                if line is None:
                    os.remove(path)
                else:
                    lines = originals[path].splitlines(keepends=True)
                    self.assertEqual(lines.count(line + "\n"), 1, f"{path} has no single line {line!r}")
                    at = lines.index(line + "\n")
                    lines[at:at + 1] = [new + "\n" for new in replacement]
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.writelines(lines)
                self.forget()
                self.assert_arm_refuses_for_want_of_the_wheel_stop(gap)
                self.forget()
                self.assert_status_says_the_wheel_stop_is_missing(gap)

    def test_a_wrapper_or_safe_stop_that_does_not_name_the_lock_is_refused_and_nothing_is_wired(self):
        # Without its lock line the host wrapper would start a host that holds no lock, and a safe-stop without its lock
        # line would not run its wheel stop at all: each must stop the install before anything is wired.
        lock = "~/kc-lekiwi/run/wheels-safe-stop.lock"  # the robot's output shows its home as '~'
        for template, line, words in (("run-host.sh.in", "WHEELS_LOCK='@@WHEELS_LOCK@@'",
                                       f"REFUSE: the rendered host wrapper lacks the line: WHEELS_LOCK='{lock}'"),
                                      ("safe-stop.sh.in", "WHEELS_LOCK=''", f"REFUSE: the rendered safe-stop lacks the line: WHEELS_LOCK='{lock}'")):
            with self.subTest(template=template):
                self.forget()
                shutil.rmtree(os.path.join(self.tmp.name, "checkout-copy"), ignore_errors=True)
                result, out = self.install_from_a_copy_without(template, line)
                self.assertEqual(result.returncode, 3, out)
                self.assertIn(words, out)
                self.assertIn("STATE LEFT: nothing is wired", out)
                self.assert_nothing_wired()

    def test_a_robot_computer_without_flock_is_refused_before_anything_is_copied(self):
        sysbin = os.path.join(self.tmp.name, "minimal-bin")
        os.mkdir(sysbin)
        for tool in MINIMAL_TOOLS:
            found = shutil.which(tool, path="/usr/bin:/bin")
            if found:
                os.symlink(found, os.path.join(sysbin, tool))
        self.assertIsNone(shutil.which("flock", path=sysbin))
        result, out = self.install(STUB_ROBOT_SYSPATH=sysbin)
        self.assertEqual(result.returncode, 3, out)
        self.assertIn("REFUSE: flock is not installed on the robot computer", out)
        self.assertIn("STATE LEFT: nothing was changed on the robot computer", out)
        self.assertEqual([tool for tool, _ in self.calls()], ["ssh"], "anything beyond the read-only preflight ran")
        self.assertFalse(os.path.exists(self.kroot()))

    def test_a_robot_python_without_the_feetech_sdk_is_refused_before_anything_is_copied(self):
        write_exec(self.robot_python, self.robot_python_text(
            before='case "$*" in *"import scservo_sdk"*) echo "ModuleNotFoundError: No module named scservo_sdk" >&2; exit 1 ;; esac\n'))
        result, out = self.install()
        self.assertEqual(result.returncode, 3, out)
        self.assertIn("REFUSE: the interpreter cannot import scservo_sdk and serial", out)
        self.assertIn("STATE LEFT: nothing was changed on the robot computer", out)
        self.assertEqual([tool for tool, _ in self.calls()], ["ssh"], "anything beyond the read-only preflight ran")
        self.assertFalse(os.path.exists(self.kroot()))

    def test_a_failing_self_check_refuses_the_release_and_leaves_nothing_wired(self):
        write_exec(self.robot_python, self.robot_python_text(
            before='case "$*" in *"--self-check"*) echo "wheels-safe-stop: +80 ms SELF-CHECK FAILED: stands in for a broken interpreter"; exit 4 ;; esac\n'))
        result, out = self.install()
        self.assertEqual(result.returncode, 3, out)
        self.assertIn("SELF-CHECK FAILED: stands in for a broken interpreter", out)
        self.assertIn("REFUSE: the wheel stop's self-check failed under this interpreter", out)
        self.assertIn("STATE LEFT: nothing is wired", out)
        self.assert_nothing_wired()

    def slow_robot_sdk(self):
        """The robot's feetech SDK takes 0.4 s to import: a stop from this interpreter could not read back what it writes
        inside its budget, and could be ended by the safe-stop's hard limit before it writes at all. The real helper runs
        its self-check against it, with its own process age as its clock."""
        with open(os.path.join(self.robot_sdk, "scservo_sdk", "__init__.py"), "w", encoding="utf-8") as handle:
            handle.write("import time\ntime.sleep(0.4)\nPortHandler = PacketHandler = GroupSyncWrite = object\nCOMM_SUCCESS = 0\n")

    def test_a_self_check_too_slow_for_the_budget_refuses_the_release_and_leaves_nothing_wired(self):
        self.slow_robot_sdk()
        result, out = self.install()
        self.assertEqual(result.returncode, 3, out)
        self.assertIn("SELF-CHECK TOO SLOW", out)
        self.assertNotIn("SELF-CHECK ok", out)
        self.assertIn("REFUSE: the wheel stop's self-check failed under this interpreter", out)
        self.assertIn("STATE LEFT: nothing is wired", out)
        self.assert_nothing_wired()
        self.assertEqual([n for n in os.listdir(self.kroot("releases")) if os.path.exists(self.kroot("releases", n, "VERIFIED"))], [])

    def test_a_self_check_too_slow_for_the_budget_refuses_a_reinstall_and_leaves_the_previous_install_wired(self):
        first, out = self.install()
        self.assertEqual(first.returncode, 0, out)
        before = self.live_wiring()
        previous = os.readlink(self.kroot("current"))
        self.slow_robot_sdk()
        self.forget()
        result, out = self.install()
        self.assertEqual(result.returncode, 3, out)
        self.assertIn("SELF-CHECK TOO SLOW", out)
        self.assertIn("REFUSE: the wheel stop's self-check failed under this interpreter", out)
        self.assertIn(f"STATE LEFT: the previous install is untouched and still wired: current -> {previous}", out)
        self.assertEqual(self.live_wiring(), before, "the refused reinstall changed the live install")
        self.assertEqual(os.readlink(self.kroot("current")), previous)
        self.assertNotIn("daemon-reload", self.systemctl_verbs())

    def test_the_rendered_release_file_runs_as_the_supervisor_runs_it_and_writes_nothing_to_a_busy_device(self):
        # The fake robot's servo "device" is /dev/null. This test holds it open itself, so the helper MUST find an owner
        # and write nothing; the stand-in SDK could not write anything either.
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        log = os.path.join(self.tmp.name, "safe-stop-host.log")
        cwd = self.release("bundle", "child-cwd")
        with open("/dev/null", "rb"):
            start = time.monotonic()
            run = subprocess.run([self.release("bin", "safe-stop.sh"), log], env={"PATH": "/usr/bin:/bin"}, cwd=cwd,
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
            elapsed = time.monotonic() - start
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        text = read_text(log)
        self.assertRegex(text.splitlines()[0], r"^safe-stop ran at ")
        self.assertIn("NOT RUN: the servo device has an owner (pid ", text)
        self.assertIn("safe-stop: wheel stop exited 3 (not run: see the NOT RUN line above; nothing was written)", text)
        self.assertLess(elapsed, 0.5, text)

    def test_status_shows_the_tail_of_each_safe_stop_log(self):
        self.assertEqual(self.install()[0].returncode, 0)
        with open(self.kroot("logs", "safe-stop-host.log"), "w", encoding="utf-8") as handle:
            handle.write("safe-stop ran at 2026-09-30T12:00:00Z\nwheels-safe-stop: +80 ms DONE: stands in for a wheel stop\n")
        self.forget()
        result = self.run_deploy("status")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("wheel stop: configured in the current release", out)
        self.assertIn("== safe-stop logs (the last 25 lines of each)", out)
        self.assertIn("-- logs/safe-stop-host.log", out)
        self.assertIn("wheels-safe-stop: +80 ms DONE: stands in for a wheel stop", out)
        self.assertTrue(set(self.systemctl_verbs()) <= {"show", "is-active", "is-enabled"}, self.systemctl_verbs())


class SafeAttachWheelRule(FakeRobotHarness):
    """The safe attach's one-count wheel rule (operator ruling 2026-09-27) as install deploys it: the host this release
    starts must carry it, read by its two exact lines from the staged copy before anything is committed, with the host file
    in the VERIFIED marker; arm refuses a current release whose host lacks it; status says which rule the current host
    carries, with the host file's sha256, so that an attended re-proof can tell the new host from the old one."""

    HOST = os.path.join("bin", "pi", "lekiwi_host_safe_attach.py")
    RULE = "        if abs(int(velocity[m])) > WHEEL_REST_MAX_RAW or int(goal_velocity[m]) != 0:"
    COUNT = "WHEEL_REST_MAX_RAW = 50"
    EXACT_ZERO = "        if int(velocity[m]) != 0 or int(goal_velocity[m]) != 0:"  # the host as it was before the rule
    CARRIES = "safe attach: the host carries the one-count wheel rule"
    LACKS = "safe attach: the host does NOT carry the one-count wheel rule"
    install_from_a_copy_with = WheelStop.install_from_a_copy_with

    def local_sum(self):
        with open(os.path.join(ROOT, self.HOST), "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def test_the_rule_lines_are_this_checkout_s_host(self):
        lines = read_text(os.path.join(ROOT, self.HOST)).splitlines()
        self.assertEqual(lines.count(self.RULE), 1)
        self.assertEqual(lines.count(self.COUNT), 1)
        deploy = read_text(DEPLOY)
        for line in (self.RULE, self.COUNT):
            self.assertIn(line.strip(), deploy, "lekiwi-deploy.sh looks for a line this checkout's host does not have")

    def test_install_verifies_the_staged_host_carries_the_rule_and_arm_and_status_say_so(self):
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        said = [line for line in out.splitlines() if line.startswith("safe attach: ")]
        self.assertEqual(said, [f"{self.CARRIES} (a wheel at rest may read one velocity count, 50 raw, of either sign; its goal "
                                f"velocity must read 0; more refuses the attach); host file sha256 {self.local_sum()}"], out)
        verified = read_text(self.release("VERIFIED"))
        self.assertIn(f"{self.local_sum()}  app/bin/pi/lekiwi_host_safe_attach.py", verified.splitlines())
        self.forget()
        status = self.run_deploy("status")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertIn(said[0], status.stdout.splitlines())
        self.assertTrue(set(self.systemctl_verbs()) <= {"show", "is-active", "is-enabled"}, self.systemctl_verbs())
        self.forget()
        armed = self.run_deploy("arm", STUB_CLAIM_TOKEN="1")
        out = armed.stdout + armed.stderr
        self.assertIn(said[0], out.splitlines())
        self.assertLess(out.index(said[0]), out.index("ARM: THE OPERATOR MUST BE PRESENT"))
        self.assertIn("the safe attach refuses a wheel reading more than one velocity count or a goal velocity other than 0, or "
                      "a joint without torque", out)
        self.assertIn("ARMED: the host wrapper claimed the arm token", out)

    def test_install_refuses_a_host_without_the_rule_and_leaves_nothing_wired(self):
        for name, line, replacement in (("the exact-zero check of before", self.RULE, [self.EXACT_ZERO]),
                                        ("a larger count", self.COUNT, ["WHEEL_REST_MAX_RAW = 100"])):
            with self.subTest(case=name):
                self.forget()
                result, out = self.install_from_a_copy_with([(self.HOST, line, replacement)])
                self.assertEqual(result.returncode, 3, out)
                self.assertIn(self.LACKS, out)
                self.assertIn("REFUSE: the staged host lacks the safe attach's one-count wheel rule", out)
                self.assertIn("STATE LEFT: nothing is wired", out)
                self.assert_nothing_wired()

    def test_a_refused_reinstall_leaves_the_previous_release_current_and_wired(self):
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        previous, wiring = os.readlink(self.kroot("current")), self.live_wiring()
        self.forget()
        result, out = self.install_from_a_copy_with([(self.HOST, self.RULE, [self.EXACT_ZERO])])
        self.assertEqual(result.returncode, 3, out)
        self.assertIn("REFUSE: the staged host lacks the safe attach's one-count wheel rule", out)
        self.assertIn(f"STATE LEFT: the previous install is untouched and still wired: current -> {previous}", out)
        self.assertEqual(os.readlink(self.kroot("current")), previous)
        self.assertEqual(self.live_wiring(), wiring)

    def test_arm_refuses_a_current_release_whose_host_lacks_the_rule_and_status_says_so(self):
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        host = self.release("app", self.HOST)
        original = read_text(host)
        for name, line, replacement in (("the exact-zero check of before", self.RULE, self.EXACT_ZERO),
                                        ("a larger count", self.COUNT, "WHEEL_REST_MAX_RAW = 100"),
                                        ("no rule line at all", self.RULE, None)):
            with self.subTest(case=name):
                lines = original.splitlines(keepends=True)
                self.assertEqual(lines.count(line + "\n"), 1)
                at = lines.index(line + "\n")
                lines[at:at + 1] = [] if replacement is None else [replacement + "\n"]
                with open(host, "w", encoding="utf-8") as handle:
                    handle.writelines(lines)
                self.forget()
                armed = self.run_deploy("arm", STUB_CLAIM_TOKEN="1")
                out = armed.stdout + armed.stderr
                self.assertEqual(armed.returncode, 3, out)
                said = [line for line in out.splitlines() if line.startswith("safe attach: ")]
                self.assertEqual(len(said), 1, out)
                self.assertTrue(said[0].startswith(self.LACKS), out)
                self.assertIn("REFUSE: the current release's host lacks the safe attach's one-count wheel rule (the line above says "
                              "which host); run install again.", out)
                self.assertIn("Nothing was armed or started.", out)
                self.assertNotIn("ARMED:", out)
                self.assertNotIn("THE OPERATOR MUST BE PRESENT", out)
                self.assertEqual(self.lines("tokens"), [])
                self.assertEqual([v for v in self.systemctl_verbs() if v in LIVE_WRITES + ("stop",)], [])
                self.assertFalse(os.path.exists(self.token()))
                self.forget()
                status = self.run_deploy("status")
                self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
                self.assertIn(self.LACKS, status.stdout)
                self.assertNotIn(self.CARRIES, status.stdout)
        with open(host, "w", encoding="utf-8") as handle:
            handle.write(original)
        self.forget()
        status = self.run_deploy("status")
        self.assertIn(self.CARRIES, status.stdout)


if __name__ == "__main__":
    unittest.main()
