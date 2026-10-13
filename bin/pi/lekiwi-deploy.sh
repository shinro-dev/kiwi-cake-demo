#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# bin/pi/lekiwi-deploy.sh SUBCOMMAND: deploy the LeKiwi robot computer in its original architecture, where an aarch64
# Cake resident runs as a systemd USER unit and its supervisor slot LAUNCHES AND SUPERVISES the camera-less motor host
# (run-host.sh), and its mobile_approach capability slot launches the capability process (run-capability.sh). Like
# bin/pi/start-host.sh it lives under bin/pi because what it deploys runs there, and it is RUN ON THE LAPTOP.
#
#   check             read-only: refuses when either legacy unit (kiwi-cake-demo.service, cake-resident.service) or
#                     the deployed unit is not inactive, when anything listens on 5555 or 5556, or when the servo device
#                     or any /dev/video node has an owner; then runs `bin/pi/start-host.sh check`, the console's own
#                     preflight and installed-lerobot source check, which starts no host and copies nothing.
#   install           builds a NEW RELEASE in a staging directory, ~/kc-lekiwi/releases/<stamp>, and verifies all of it
#                     there before anything live changes: copies the four aarch64 Cake binaries and the application
#                     files into it (sha256-verified on the robot), generates the deployment key ON THE ROBOT when absent
#                     (kept in ~/kc-lekiwi/keys, never in a release), renders the wrappers (the host wrapper with the
#                     wheel stop's lock, which it takes before its owner check and hands to the host for its whole life;
#                     the safe-stop with this deployment's wheel stop, and refuses unless bin/pi/wheels_safe_stop.py
#                     --self-check passes under the robot's python: it opens no device, and fails when that python gets
#                     to the servo bus too late for the wheel stop's time budget), refuses unless the staged host carries the
#                     safe attach's one-count wheel rule (attach_rule_verdict, by its exact lines), runs the robot's own
#                     demo-plan build-mobile, refuses unless the manifest names the rendered wrappers and the stub
#                     holder, runs demo-preflight, prepares the Cake showcase's candidates (the tampered twin of the
#                     swapped Plan with one byte flipped, the STARTING Plan as the undo candidate, a candidates.txt naming
#                     each role, key and sha256, and an unsigned capsule twin when build-mobile names none), writes the
#                     release's resident.conf and unit file, and marks the release VERIFIED. Only then does it COMMIT: the link ~/kc-lekiwi/current is switched to the release by one
#                     atomic rename, the user unit (whose ExecStart and --config go through current) is written, and
#                     daemon-reload runs. A refusal before the commit leaves the previous install byte for byte and still
#                     wired, or, on a first install, nothing wired; the REFUSE output ends with a STATE LEFT line saying
#                     which. IT NEVER STARTS AND NEVER ENABLES THE UNIT.
#   arm               THE ONLY SUBCOMMAND THAT STARTS THE UNIT, with the operator present at the robot: refuses unless the
#                     unit is installed and the current release carries the wheel stop and a host with the safe attach's
#                     one-count wheel rule (a release installed before them, or left current by an install that refused,
#                     does not: run install again), stops the unit when it
#                     runs, writes the one-shot arm token (mode 600) in ~/kc-lekiwi/run, runs systemctl --user restart,
#                     and waits up to KC_ARM_WAIT_S seconds (default 90) for the host wrapper to claim the token. The
#                     token arms ONE host start: a Cake child restart or a systemd restart of the resident finds none
#                     and is held (bin/templates/run-host.sh.in). Whatever happens, no token is left behind when arm
#                     ends.
#   stop              systemctl --user stop, then removes any arm token.
#   status            read-only evidence: the current release, whether it carries the wheel stop, whether its host carries
#                     the safe attach's one-count wheel rule (with the host file's sha256), and the arm token,
#                     unit state and MainPID, the resident's children, list-slots, get-health, read-flight, the
#                     listeners on 5555/5556, the host's descriptors on the servo device and on video nodes, each
#                     slot's supervisor.log tail, the tail of each safe-stop log (what the wheel stop wrote and read
#                     back), and the unit's journal (kc_unit_journal's query).
#   prove-signature   offline, on the robot, touching no device and no running resident: verifies every deployed
#                     capsule under require against the deployment key (admitted) and a fresh throwaway key
#                     (signature_key_id_not_trusted), any unsigned twin (signature_missing), then shows a scratch
#                     resident refusing a stub capsule signed by the throwaway key, with its own reason, in a scratch
#                     directory with its own store, socket and Plan, deleted afterwards.
#   fetch-candidates  copies the prepared candidate Plans back into KC_CANDIDATE_DIR (mode 700), sha256-verified: the
#                     swapped Plan and the abort-swap successor always, and the tampered twin and the STARTING Plan (the
#                     undo candidate) when the release holds them (a release installed before them does not).
#   approach-probe    runs bin/pi/approach_probe.py on the robot: a NON-MOTION probe of the capability Cake launched,
#                     whose only motion transport is an in-memory recorder (see that file). Any further arguments
#                     (`approach-probe [args...]`) are passed to the probe as they are, so the v2 mode can be run:
#                     `approach-probe --contract v2 --record PATH`, where PATH is a stored perception record ON THE
#                     ROBOT COMPUTER (the caller's path; nothing is copied) and --executor-dir names the directory
#                     holding run_single_cube_cycle.py there (that closure, and the observation source
#                     autonomy/motion/target_observation_source.py the probe imports through it, are not in APP_FILES).
#                     The probe expands a leading '~' in both paths itself; nothing else is interpreted.
#
# NOTHING HERE MOVES OR TORQUES ANYTHING EXCEPT THROUGH arm. No subcommand opens the servo bus or a camera, and none but
# arm starts or restarts the unit; none enables it. Starting kiwi-cake-lekiwi.service starts the motor host, which
# attaches to the arm, and that is the explicit operator step `arm` exists for (robot on its stand, hand near power).
# One thing the INSTALLED release does on its own, and only while the unit runs: after every host or capability exit the
# supervisor observes (a crash, a kill, or the stop that `stop` and `arm` make of a running unit), its safe-stop writes
# goal velocity 0 to the three wheel servos and reads them back (bin/pi/wheels_safe_stop.py), only while it holds the
# wheel stop's lock, which a host started by run-host.sh holds for its whole life. It never reads or writes an arm servo.
#
# THE OWNERSHIP CHECKS FAIL CLOSED, here and in the host wrapper: a missing or failing fuser or ss, or a fuser that does
# not see a file the check itself holds open, refuses rather than reading as "no owner". fuser runs without root, so it
# sees only this account's processes: a device held by a process of another account is not detected.
#
# Every identity comes from the environment, as start-host.sh requires it, and is never printed:
#   KC_PI_SSH            the ssh target of the robot computer (key-based login; BatchMode never asks for a password)
#   KC_ROBOT_ID          the id the LeKiwi was calibrated with (check, install)
#   KC_SERVO_PORT        the servo bus device on the robot computer, by its /dev/serial/by-id/ entry (check, install)
#   KC_CAKE_AARCH64_DIR  a directory on THIS laptop holding the aarch64 cake-resident, admin-probe, demo-plan and
#                        demo-preflight (install)
#   KC_CANDIDATE_DIR     the laptop directory the candidate Plans are copied into (fetch-candidates)
# Optional: KC_PI_PYTHON, an absolute path to the robot's python (default: the miniconda python in the remote home);
# KC_LEKIWI_DIR, the deployment directory RELATIVE to the remote home (default kc-lekiwi); KC_ARM_WAIT_S (arm).
# Output from the robot has the remote home replaced by '~', and anything shaped like a private IPv4 address is masked.
#
# Exit status: 0 done; 3 a check or a verification refused (the REFUSE lines say which); 4 a copied file does not match;
# 5 usage or a missing or unsafe identity; 255 ssh could not reach the robot computer. prove-signature and
# approach-probe exit 1 when an expectation did not hold; arm exits 1 when it started the unit but the host wrapper did
# not claim the token in time (the token is removed then).
set -euo pipefail

usage() { echo "usage: $0 check|install|arm|stop|status|prove-signature|fetch-candidates|approach-probe [probe args...]" >&2; exit 5; }
[ $# -ge 1 ] || usage
CMD="$1"
shift
case "$CMD" in
  approach-probe) ;;  # the remaining arguments are the probe's, passed through below
  check | install | arm | stop | status | prove-signature | fetch-candidates) [ $# -eq 0 ] || usage ;;
  -h | --help) sed -n '3,/^set -euo pipefail$/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) usage ;;
esac

# Fail closed on identities, exactly as start-host.sh does: a value goes inside single quotes in a command line the
# REMOTE shell parses and nothing here escapes it, so a value outside this character set is refused, never sent.
MISSING=0
need() {
  local name="$1" what="$2"
  if [ -z "${!name:-}" ]; then echo "REFUSE: $name is not set ($what). Set it in your shell from your private, uncommitted runtime configuration." >&2; MISSING=1; return 0; fi
  case "${!name}" in
    -* | *[!A-Za-z0-9_./:@+=-]*) echo "REFUSE: $name starts with '-' or holds a character outside A-Za-z0-9_./:@+=- ; it is used inside a remote command line and is not escaped." >&2; MISSING=1 ;;
  esac
}
need KC_PI_SSH "the ssh target of the robot computer"
case "$CMD" in
  check | install)
    need KC_ROBOT_ID "the id the LeKiwi was calibrated with"
    need KC_SERVO_PORT "the servo bus device on the robot computer, a /dev/serial/by-id/ entry" ;;
esac
[ -z "${KC_PI_PYTHON:-}" ] || need KC_PI_PYTHON "the python on the robot computer"
[ -z "${KC_LEKIWI_DIR:-}" ] || need KC_LEKIWI_DIR "the deployment directory, relative to the remote home"
[ "$MISSING" -eq 0 ] || exit 5
case "${KC_SERVO_PORT:-/dev/}" in /dev/*) ;; *) echo "REFUSE: KC_SERVO_PORT must be a device path under /dev/ on the robot computer." >&2; exit 5 ;; esac
case "${KC_PI_PYTHON:-/}" in /*) ;; *) echo "REFUSE: KC_PI_PYTHON must be an absolute path on the robot computer." >&2; exit 5 ;; esac
ARM_WAIT_S="${KC_ARM_WAIT_S:-90}"
case "$ARM_WAIT_S" in '' | *[!0-9]*) echo "REFUSE: KC_ARM_WAIT_S must be a whole number of seconds." >&2; exit 5 ;; esac
[ "$ARM_WAIT_S" -ge 1 ] && [ "$ARM_WAIT_S" -le 600 ] || { echo "REFUSE: KC_ARM_WAIT_S must be between 1 and 600 seconds." >&2; exit 5; }
DIR_REL="${KC_LEKIWI_DIR:-kc-lekiwi}"
case "/$DIR_REL/" in
  //* | */../* | */./*) echo "REFUSE: KC_LEKIWI_DIR must be a path RELATIVE to the remote home, without '.' or '..' components." >&2; exit 5 ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNIT="kiwi-cake-lekiwi.service"
LEGACY_UNITS="kiwi-cake-demo.service cake-resident.service"
BINARIES=(cake-resident admin-probe demo-plan demo-preflight)
if [ -n "${KC_PI_PYTHON:-}" ]; then REMOTE_PYTHON="'$KC_PI_PYTHON'"; else REMOTE_PYTHON="\"\$HOME/miniconda3/bin/python3\""; fi

# The one place a private address could reach this terminal from is ssh's own complaint, or a remote line quoting one.
redact() { sed -u -E 's/\b(10(\.[0-9]{1,3}){3}|192\.168(\.[0-9]{1,3}){2}|172\.(1[6-9]|2[0-9]|3[01])(\.[0-9]{1,3}){2})\b/<private-ip>/g'; }
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=6)
# EVERY remote command goes through rsh, and every copy through rcp: BatchMode, a connect timeout, the target from the
# environment, never a terminal (-t), and ssh's own stderr masked before it reaches this terminal.
rsh() { ssh "${SSH_OPTS[@]}" "$KC_PI_SSH" "$@" 2> >(redact >&2); }
rcp() { scp -q "${SSH_OPTS[@]}" "$@" 2> >(redact >&2); }
REMOTE_ENV="DIR_REL='$DIR_REL' UNIT='$UNIT' LEGACY_UNITS='$LEGACY_UNITS' PY=$REMOTE_PYTHON"
# What every remote script below starts with: the remote home shown as '~', the deployment's fixed paths, and the
# helpers more than one script needs. The installed layout: releases/<stamp>/ holds a whole install (bin/, app/, the
# bundle, manifest.txt, resident.conf, the unit file and the VERIFIED marker) and never moves, because the Plan names
# paths inside it; current is the ONE live pointer, a link to the release the unit runs; keys/, run/ and logs/ are
# shared by every release.
PREAMBLE="$(cat <<'LIB'
set -uo pipefail
exec > >(sed -u "s|$HOME|~|g") 2>&1
KROOT="$HOME/$DIR_REL"; CUR="$KROOT/current"; RUN="$KROOT/run"; TOKEN="$RUN/arm-token"
UNIT_PATH="$HOME/.config/systemd/user/$UNIT"
unit_state() { local s; s="$(systemctl --user show -p ActiveState --value "$1" 2>/dev/null)"; printf '%s\n' "${s:-unreadable}"; }
# Idle means inactive or failed. Active, activating (a Restart= pending among them), deactivating, or a state that
# could not be read at all, is not idle.
unit_idle() { case "$(unit_state "$1")" in inactive | failed) return 0 ;; esac; return 1; }
# command_socket_verdict: the host's command socket as the KERNEL lists it (ss), never as the host says it bound it.
# Prints one line; returns 0 listening on loopback only, 1 listening on any other address (EXPOSED), 2 no listener,
# 3 the listeners could not be read. Loopback limits who can reach the socket; the host itself checks nothing.
command_socket_verdict() {
  local table addr others="" loop=0 _a _b _c _rest
  table="$(ss -H -ltn 2>/dev/null)" || { echo "command socket: UNKNOWN (ss could not list the listeners)"; return 3; }
  while read -r _a _b _c addr _rest; do
    case "$addr" in
      127.0.0.1:5555 | '[::1]:5555') loop=1 ;;
      *:5555) others="$others $addr" ;;
    esac
  done <<<"$table"
  if [ -n "$others" ]; then echo "command socket: EXPOSED on$others (5555 must listen on 127.0.0.1 only)"; return 1; fi
  if [ "$loop" -eq 1 ]; then echo "command socket: loopback only (127.0.0.1:5555), as the kernel lists it"; return 0; fi
  echo "command socket: no listener on 5555"; return 2
}
# wheel_stop_verdict: whether the CURRENT release carries the wheel stop, read from the files install rendered in it. It
# does when its safe-stop has the four WHEELS_ settings that make it run the wheel stop (the python, the wheel stop and
# the lock as absolute paths, the port under /dev/), the wheel stop it names exists, and the safe-stop and the host
# wrapper both name the wheel stop's lock. A release installed before the wheel stop has none of this: its safe-stop only
# logs and its host wrapper takes no lock; an install that refuses leaves such a release current. Prints one line, which
# says what is missing when something is; returns 0 when the release carries the wheel stop, 1 when it does not.
wheel_stop_verdict() {
  local lock="$RUN/wheels-safe-stop.lock" stop="$CUR/bin/safe-stop.sh" helper gap=""
  helper="$(sed -nE "s/^WHEELS_HELPER='(\/[^']*)'\$/\\1/p" "$stop" 2>/dev/null | tail -n 1)"
  if ! grep -qE "^WHEELS_PYTHON='/[^']*'\$" "$stop" 2>/dev/null || ! grep -qE "^WHEELS_PORT='/dev/[^']+'\$" "$stop" 2>/dev/null ||
    ! grep -qE "^WHEELS_LOCK='/[^']*'\$" "$stop" 2>/dev/null || [ -z "$helper" ]; then
    gap="its safe-stop does not run the wheel stop (its four wheel stop settings are not all filled in the way install renders them; a release installed before the wheel stop has none), so nothing writes the wheels after a host dies without its disconnect path"
  elif [ ! -f "$helper" ]; then
    gap="the wheel stop its safe-stop names is missing, so nothing writes the wheels after a host dies without its disconnect path"
  elif ! grep -qxF "WHEELS_LOCK='$lock'" "$stop" 2>/dev/null || ! grep -qxF "WHEELS_LOCK='$lock'" "$CUR/bin/run-host.sh" 2>/dev/null; then
    gap="its safe-stop and its host wrapper do not both name the wheel stop's lock $lock, so a wheel stop could write the bus while a host starts"
  fi
  if [ -n "$gap" ]; then
    echo "wheel stop: MISSING from the current release: $gap; arm refuses this release: run install again (an install that refuses leaves the previous release current)"
    return 1
  fi
  echo "wheel stop: configured in the current release: after each host or capability exit the supervisor observes, its safe-stop runs the wheel stop (goal velocity 0 to wheels 7 8 9, only while it holds the wheel stop's lock), and its host wrapper takes that lock for the host's whole life"
}
# attach_rule_verdict HOST: whether the host file HOST (a release's app/bin/pi/lekiwi_host_safe_attach.py) carries the safe
# attach's one-count wheel rule (operator ruling 2026-09-27): a wheel at rest may read one velocity count, 50 raw, of either
# sign, and its goal velocity must read exactly 0. A host from before the rule refuses an arm on that one-count dither of a
# wheel standing still. It is read by its exact lines (the count bound once, and the check), never run: best effort
# against an edit, which the sha256 at the end of the line settles. Prints one line; returns 0 when the host carries the
# rule, 1 when it does not.
attach_rule_verdict() {
  local sum
  sum="$(sha256sum -- "$1" 2>/dev/null | cut -d' ' -f1)"
  if grep -qxF 'WHEEL_REST_MAX_RAW = 50' "$1" 2>/dev/null && [ "$(grep -cE '^[[:space:]]*WHEEL_REST_MAX_RAW[[:space:]]*=' "$1" 2>/dev/null)" = 1 ] &&
    grep -qxF '        if abs(int(velocity[m])) > WHEEL_REST_MAX_RAW or int(goal_velocity[m]) != 0:' "$1" 2>/dev/null; then
    echo "safe attach: the host carries the one-count wheel rule (a wheel at rest may read one velocity count, 50 raw, of either sign; its goal velocity must read 0; more refuses the attach); host file sha256 ${sum:-unreadable}"
    return 0
  fi
  echo "safe attach: the host does NOT carry the one-count wheel rule (it refuses an arm on a wheel at rest that reads one velocity count, 50 raw, the dither of a wheel standing still); host file sha256 ${sum:-unreadable}"
  return 1
}
ports_held() {  # ports_held: 0 when the ss -ltn table on stdin has a listener on 5555 or 5556
  local _a _b _c addr _rest
  while read -r _a _b _c addr _rest; do
    case "$addr" in *:5555 | *:5556 | *.5555 | *.5556) return 0 ;; esac
  done
  return 1
}
FUSER=""
fuser_on() {  # fuser_on PATH: 0 an owner, 1 no owner, 2 the check could not run (fuser exits 1 for "no owner" AND for
  # an error; only an error prints to stderr)
  local err rc=0
  err="$("$FUSER" "$1" 2>&1 >/dev/null)" || rc=$?
  case "$rc" in 0) return 0 ;; 1) [ -n "$err" ] || return 1 ;; esac
  return 2
}
fuser_ready() {  # finds fuser and proves, with a file this shell holds open, that it sees an owner; says why when not
  local control rc=2
  FUSER="$(command -v fuser 2>/dev/null)" || { echo "REFUSE: fuser is not installed on the robot computer, so the owner checks cannot run; they fail closed"; return 1; }
  control="$(mktemp "${TMPDIR:-/tmp}/kc-fuser-control.XXXXXX")" || { echo "REFUSE: no temporary file for fuser's positive control, so the owner checks cannot run; they fail closed"; return 1; }
  if { exec 9<"$control"; } 2>/dev/null; then fuser_on "$control"; rc=$?; exec 9<&-; fi
  rm -f -- "$control"
  [ "$rc" -eq 0 ] || { echo "REFUSE: fuser did not see a file this check holds open, so the owner checks cannot run; they fail closed"; return 1; }
  echo "note: fuser runs here without root, so it sees only this account's processes; a device held by a process of another account is NOT detected"
}
LIB
)"
# remote ENV_PREFIX: runs the script on this function's stdin on the robot computer, after the preamble.
# shellcheck disable=SC2029  # every interpolated value was validated above or is built in this script
remote() { { printf '%s\n' "$PREAMBLE"; cat; } | rsh "$1 bash -s"; }

# --- check ------------------------------------------------------------------------------------------------------------
if [ "$CMD" = "check" ]; then
  echo "== 1/2 deployment preflight (read-only): legacy units, the deployed unit, ports 5555/5556, servo and camera owners"
  remote "$REMOTE_ENV PORT='$KC_SERVO_PORT'" <<'CHECK' | redact
refused=0
for u in $LEGACY_UNITS; do
  unit_idle "$u" || { echo "REFUSE: the legacy unit $u is $(unit_state "$u" || true); stop it by hand (it is never started or enabled by this tooling)"; refused=1; }
done
unit_idle "$UNIT" || { echo "REFUSE: $UNIT is $(unit_state "$UNIT" || true); check is for a robot computer with no host"; refused=1; }
if ! command -v ss >/dev/null 2>&1; then echo "REFUSE: ss is not installed on the robot computer, so the 5555/5556 check cannot run; it fails closed"; refused=1
elif ! LISTENERS="$(ss -H -ltn 2>&1)"; then echo "REFUSE: ss could not list the listeners, so the 5555/5556 check cannot run; it fails closed"; refused=1
elif ports_held <<<"$LISTENERS"; then echo "REFUSE: something listens on 5555 or 5556"; refused=1; fi
if ! fuser_ready; then refused=1
else
  if [ ! -e "$PORT" ]; then echo "REFUSE: the servo bus path given in KC_SERVO_PORT is missing on the robot computer"; refused=1
  else
    st=0; fuser_on "$(readlink -f "$PORT")" || st=$?
    case "$st" in
      0) echo "REFUSE: the servo bus already has an owner"; refused=1 ;;
      2) echo "REFUSE: fuser failed on the servo bus, so its owner check cannot run; it fails closed"; refused=1 ;;
    esac
  fi
  for n in /dev/video*; do
    [ -e "$n" ] || continue
    st=0; fuser_on "$n" || st=$?
    case "$st" in
      0) echo "REFUSE: $n has an owner"; refused=1 ;;
      2) echo "REFUSE: fuser failed on $n, so its owner check cannot run; it fails closed"; refused=1 ;;
    esac
  done
fi
[ "$refused" -eq 0 ] || exit 3
echo "deployment preflight ok: legacy units idle, $UNIT idle, 5555/5556 free, servo bus and cameras unowned (as far as this account can see)"
CHECK
  echo "== 2/2 bin/pi/start-host.sh check (its own preflight and the installed-lerobot source check; starts no host)"
  exec bash "$ROOT/bin/pi/start-host.sh" check
fi

# --- fetch-candidates -------------------------------------------------------------------------------------------------
if [ "$CMD" = "fetch-candidates" ]; then
  [ -n "${KC_CANDIDATE_DIR:-}" ] || { echo "REFUSE: KC_CANDIDATE_DIR is not set (a directory on this laptop for the two candidate Plans)" >&2; exit 5; }
  { mkdir -p -- "$KC_CANDIDATE_DIR" && chmod 700 -- "$KC_CANDIDATE_DIR"; } || { echo "REFUSE: cannot create KC_CANDIDATE_DIR with mode 700" >&2; exit 5; }
  PLANS=(mobile-demo-swapped.cakepln mobile-demo-active-successor-abort-swap.cakepln)
  OPTIONAL=(mobile-demo-swapped-tampered.cakepln mobile-demo.cakepln)
  # shellcheck disable=SC2029  # DIR_REL was validated above
  REMOTE_SUMS="$(rsh "cd '$DIR_REL/current/bundle/plans' && sha256sum ${PLANS[*]} && for p in ${OPTIONAL[*]}; do if [ -f \"\$p\" ]; then sha256sum \"\$p\"; fi; done")"
  for p in "${OPTIONAL[@]}"; do
    if printf '%s\n' "$REMOTE_SUMS" | awk -v f="$p" '$2 == f {found=1} END {exit !found}'; then PLANS+=("$p")
    else echo "candidate $p is not in this release (installed before the showcase candidates); not fetched"; fi
  done
  for p in "${PLANS[@]}"; do
    rcp "$KC_PI_SSH:$DIR_REL/current/bundle/plans/$p" "$KC_CANDIDATE_DIR/$p"
    chmod 600 -- "$KC_CANDIDATE_DIR/$p"
    LOCAL_SUM="$(sha256sum -- "$KC_CANDIDATE_DIR/$p" | cut -d' ' -f1)"
    REMOTE_SUM="$(printf '%s\n' "$REMOTE_SUMS" | awk -v f="$p" '$2 == f {print $1}')"
    if [ -z "$REMOTE_SUM" ] || [ "$LOCAL_SUM" != "$REMOTE_SUM" ]; then echo "REFUSE: checksum mismatch between the robot's $p and the copy here" >&2; exit 4; fi
    echo "candidate $p sha256 $LOCAL_SUM (verified equal on the robot computer)"
  done
  exit 0
fi

# --- arm --------------------------------------------------------------------------------------------------------------
if [ "$CMD" = "arm" ]; then
  echo "== arm: the operator must be present at the robot; the host this starts WILL ATTACH TO THE SERVOS"
  remote "$REMOTE_ENV WAIT_S='$ARM_WAIT_S'" <<'ARM' | redact
STOPPED=""
refuse() { echo "REFUSE: $1"; echo "Nothing was armed or started.${STOPPED:+ $UNIT was running and was stopped first; it stays stopped.}"; exit 3; }
[ -f "$UNIT_PATH" ] || refuse "$UNIT is not installed (no unit file); run install first."
[ -L "$CUR" ] && [ -f "$CUR/VERIFIED" ] || refuse "no verified release is current; run install first."
[ "$(systemctl --user show -p LoadState --value "$UNIT" 2>/dev/null)" = loaded ] || refuse "systemctl --user has not loaded $UNIT (run install again)."
grep -qxF "ARM_TOKEN='$TOKEN'" "$CUR/bin/run-host.sh" || refuse "the current host wrapper does not read this deployment's arm token."
# The CONFIGURED binding: the launch sets the variable and the deployed host wrapper carries the block that honours it.
grep -qxF "export KC_HOST_COMMAND_LOOPBACK=1" "$CUR/bin/run-host.sh" || refuse "the current host wrapper does not put the command socket on loopback; run install again."
grep -qF -- "# --- HOST COMMAND LOOPBACK begin" "$CUR/app/bin/pi/lekiwi_host_safe_attach.py" || refuse "the deployed host has no command loopback block; run install again."
# The wheel stop, which the lines after ARMED rely on: a release without it, installed before it or left current by an
# install that refused, is not armed.
WHEEL_STOP="$(wheel_stop_verdict)" || { echo "$WHEEL_STOP"; refuse "the current release has no complete wheel stop (the line above says what is missing)."; }
# The safe attach's one-count wheel rule: a release whose host refuses an arm on the one-count dither of a wheel at rest,
# installed before the rule or left current by an install that refused, is not armed.
ATTACH="$(attach_rule_verdict "$CUR/app/bin/pi/lekiwi_host_safe_attach.py")" ||
  { echo "$ATTACH"; refuse "the current release's host lacks the safe attach's one-count wheel rule (the line above says which host); run install again."; }
for u in $LEGACY_UNITS; do
  unit_idle "$u" || refuse "the legacy unit $u is $(unit_state "$u" || true); stop it by hand first."
done
echo "$WHEEL_STOP"
echo "$ATTACH"
echo "ARM: THE OPERATOR MUST BE PRESENT at the robot, with the robot on its stand and a hand near power."
echo "ARM: starting $UNIT starts the motor host, which WILL ATTACH TO THE SERVOS: the arm holds its pose (the safe attach refuses a wheel reading more than one velocity count or a goal velocity other than 0, or a joint without torque) and the base gets the zero-speed hold."
# Whatever ends this script, no arm token is left behind: a token is only ever claimed by the start this arm made.
trap 'rm -f -- "$TOKEN" "$TOKEN.new"' EXIT
trap 'exit 130' HUP INT TERM PIPE
if ! unit_idle "$UNIT"; then
  echo "$UNIT is $(unit_state "$UNIT" || true): stopping it first, so that no spawn of the running activation can claim the token"
  systemctl --user stop "$UNIT" || refuse "systemctl --user stop $UNIT failed."
  STOPPED=1
fi
rm -f -- "$TOKEN" "$TOKEN.new"
{ ( umask 077 && printf 'armed %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$TOKEN.new" ) && chmod 600 -- "$TOKEN.new" && mv -T -- "$TOKEN.new" "$TOKEN"; } ||
  refuse "the arm token could not be written."
[ "$(stat -c %a -- "$TOKEN")" = "600" ] || refuse "the arm token is not mode 600."
echo "arm token written (mode 600; it arms ONE host start and expires after 120 s); now: systemctl --user restart $UNIT"
if ! systemctl --user restart "$UNIT"; then
  echo "REFUSE: systemctl --user restart $UNIT failed; the arm token is removed, so no later start arms the host"
  exit 3
fi
ticks=0
while [ -e "$TOKEN" ] && [ "$ticks" -lt $((WAIT_S * 10)) ]; do sleep 0.1; ticks=$((ticks + 1)); done
if [ -e "$TOKEN" ]; then
  rm -f -- "$TOKEN"
  echo "NOT ARMED: the host wrapper did not claim the arm token within $WAIT_S s; the token is removed, so no later start arms the host"
  systemctl --user show -p ActiveState -p SubState -p MainPID "$UNIT"
  exit 1
fi
echo "ARMED: the host wrapper claimed the arm token. This one host start is armed; every later restart is held until the next arm."
echo "The claim is not the attach: the wrapper still refuses on the wheel stop's lock held past its 2 s wait, a servo owner or a 5555/5556 listener, and the safe attach can refuse."
# The ACTUAL binding, read back from the kernel once the host has attached and bound its sockets. A listener anywhere
# but loopback stops the unit (the arm keeps holding: the host never drops torque on disconnect). A first EXPOSED
# reading is read again one second later, because the stock constructor binds every interface for the few
# milliseconds before the wrapper replaces that socket, and no command is read from it; only a clean loopback reading
# clears it. An unreadable table is read again until the wait ends. A loopback listener must also BELONG to the host
# Cake supervises: its process runs the safe-attach wrapper and its parent is the unit's MainPID, so a foreign
# 127.0.0.1:5555 listener is not taken for the host.
LISTEN_WAIT_S=60
listener_is_the_supervised_host() {
  local line pid main
  line="$(ss -H -ltnp 2>/dev/null | awk '$4 == "127.0.0.1:5555" || $4 == "[::1]:5555"')"
  pid="$(printf '%s\n' "$line" | sed -nE 's/.*pid=([0-9]+).*/\1/p' | head -n 1)"
  main="$(systemctl --user show -p MainPID --value "$UNIT" 2>/dev/null)"
  [ -n "$pid" ] && [ -n "$main" ] && [ "$main" != 0 ] || return 1
  tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -q 'lekiwi_host_safe_attach[.]py' || return 1
  [ "$(awk '/^PPid:/ {print $2}' "/proc/$pid/status" 2>/dev/null)" = "$main" ] || return 1
  HOST_LISTENER_PID="$pid"
}
echo "== the command socket, read back from the kernel (waiting up to $LISTEN_WAIT_S s for the host to attach and bind)"
ticks=0; verdict=2
while [ "$ticks" -lt $((LISTEN_WAIT_S * 2)) ]; do
  command_socket_verdict >/dev/null; verdict=$?
  case "$verdict" in 0 | 1) break ;; esac
  unit_idle "$UNIT" && break
  sleep 0.5; ticks=$((ticks + 1))
done
if [ "$verdict" -eq 1 ]; then
  sleep 1
  command_socket_verdict >/dev/null && verdict=0 || verdict=1
fi
command_socket_verdict || true
HOST_LISTENER_PID=""
if [ "$verdict" -eq 0 ] && ! listener_is_the_supervised_host; then verdict=4; fi
case "$verdict" in
  0) echo "CONFIGURED AND ACTUAL: run-host.sh exports KC_HOST_COMMAND_LOOPBACK=1 and the kernel lists 5555 on 127.0.0.1 only, held by the supervised host (pid $HOST_LISTENER_PID)."
     echo "This limits who can REACH the command socket to processes on the robot. The host itself checks nothing: the session, ownership and admission checks apply only to commands routed through the robot-side service." ;;
  1) echo "REFUSE: the command socket is reachable from the network, or did not settle on loopback; stopping $UNIT now (the arm keeps holding)"; systemctl --user stop "$UNIT"; exit 3 ;;
  2) echo "NO COMMAND SOCKET within $LISTEN_WAIT_S s: the host has not attached (the safe attach may have refused, or the wrapper held it). Read the result with: status"; systemctl --user show -p ActiveState -p SubState -p MainPID "$UNIT"; exit 1 ;;
  4) echo "REFUSE: the 127.0.0.1:5555 listener is not the host Cake supervises (its process is not the safe-attach wrapper child of the unit's MainPID); stopping $UNIT now (the arm keeps holding)"; systemctl --user stop "$UNIT"; exit 3 ;;
  *) echo "REFUSE: the listeners could not be read back; stopping $UNIT now (the arm keeps holding)"; systemctl --user stop "$UNIT"; exit 3 ;;
esac
systemctl --user show -p ActiveState -p SubState -p MainPID "$UNIT"
ARM
  exit "${PIPESTATUS[0]}"
fi

# --- stop -------------------------------------------------------------------------------------------------------------
if [ "$CMD" = "stop" ]; then
  remote "$REMOTE_ENV" <<'STOP' | redact
rm -f -- "$TOKEN" "$TOKEN.new"
RC=0
if [ "$(systemctl --user show -p LoadState --value "$UNIT" 2>/dev/null)" != "loaded" ]; then
  echo "$UNIT is not loaded (not installed): nothing to stop"
else
  systemctl --user stop "$UNIT" || RC=$?
fi
rm -f -- "$TOKEN" "$TOKEN.new"
[ ! -e "$TOKEN" ] || { echo "REFUSE: the arm token could not be removed"; exit 3; }
echo "$UNIT: $(unit_state "$UNIT" || true); no arm token remains, so no later start arms the host"
[ "$RC" -eq 0 ] || { echo "REFUSE: systemctl --user stop $UNIT exited $RC"; exit 3; }
STOP
  exit "${PIPESTATUS[0]}"
fi

# --- status -----------------------------------------------------------------------------------------------------------
if [ "$CMD" = "status" ]; then
  remote "$REMOTE_ENV" <<'STATUS' | redact
BIN="$CUR/bin"; M="$CUR/manifest.txt"
[ -L "$CUR" ] && [ -r "$CUR/app/bin/lib/common.sh" ] || { echo "not installed: there is no current release"; exit 3; }
. "$CUR/app/bin/lib/common.sh"
SOCK="/run/user/$(id -u)/kc-lekiwi.sock"
echo "== install"
if [ -f "$CUR/VERIFIED" ]; then echo "current -> $(readlink "$CUR") (VERIFIED)"; else echo "current -> $(readlink "$CUR") (NO VERIFIED MARKER)"; fi
if [ -e "$TOKEN" ]; then echo "arm token: PRESENT, written $(( $(date +%s) - $(stat -c %Y -- "$TOKEN") )) s ago"
else echo "arm token: none (a host start now is held until the operator runs arm)"; fi
wheel_stop_verdict || true
attach_rule_verdict "$CUR/app/bin/pi/lekiwi_host_safe_attach.py" || true
echo "== unit"
systemctl --user show -p LoadState -p ActiveState -p SubState -p MainPID -p NRestarts -p UnitFileState "$UNIT"
for u in $LEGACY_UNITS; do echo "legacy $u: $(systemctl --user is-active "$u" 2>/dev/null) / $(systemctl --user is-enabled "$u" 2>/dev/null)"; done
MAIN="$(systemctl --user show --property MainPID --value "$UNIT" 2>/dev/null)"
echo "== the resident's children (their parent is MainPID ${MAIN:-none})"
HOST=""; CAP=""
if [ -n "$MAIN" ] && [ "$MAIN" != "0" ]; then
  for c in $(kc_children_of "$MAIN"); do
    role="other"
    cmd="$(tr '\0' ' ' <"/proc/$c/cmdline" 2>/dev/null)"
    case "$cmd" in *lekiwi_host_safe_attach.py*) role="host"; HOST="$c" ;; *mobile_approach_service.py*) role="capability"; CAP="$c" ;; esac
    echo "child pid=$c ppid=$MAIN role=$role comm=$(cat "/proc/$c/comm" 2>/dev/null)"
  done
fi
for op in list-slots get-health read-flight; do
  echo "== admin-probe $op"
  if [ -S "$SOCK" ]; then kc_query "$BIN/admin-probe" "$SOCK" "$op" | { [ "$op" = read-flight ] && tail -n 30 || cat; }; else echo "(no admin socket: the resident is not running)"; fi
done
echo "== listeners on 5555/5556"
ss -H -ltnp 2>/dev/null | grep -E '[:.](5555|5556)[[:space:]]' || echo "(none)"
command_socket_verdict || true
if grep -qxF "export KC_HOST_COMMAND_LOOPBACK=1" "$CUR/bin/run-host.sh" 2>/dev/null; then echo "configured: run-host.sh puts the command socket on loopback"; else echo "configured: run-host.sh does NOT put the command socket on loopback"; fi
echo "== device descriptors"
SERVO="$(sed -nE "s/^ROBOT_PORT='(.*)'\$/\\1/p" "$BIN/run-host.sh" 2>/dev/null)"
SERVO_DEV="$(readlink -f "$SERVO" 2>/dev/null)"
for pair in "host:$HOST" "capability:$CAP"; do
  role="${pair%%:*}"; pid="${pair#*:}"
  [ -n "$pid" ] || { echo "$role: no process"; continue; }
  servo_fds=0; video_fds=0
  for fd in /proc/"$pid"/fd/*; do
    target="$(readlink "$fd" 2>/dev/null)" || continue
    [ -n "$SERVO_DEV" ] && [ "$target" = "$SERVO_DEV" ] && servo_fds=$((servo_fds + 1))
    case "$target" in /dev/video*) video_fds=$((video_fds + 1)) ;; esac
  done
  echo "$role pid=$pid servo_device_fds=$servo_fds video_fds=$video_fds (servo device resolves to ${SERVO_DEV:-unknown})"
done
echo "== supervisor.log per slot (the last 15 lines each)"
STORE="$(kc_manifest_value "$M" store_root 2>/dev/null)"
if [ -n "$STORE" ] && [ -d "$STORE" ]; then
  find "$STORE" -name supervisor.log -type f 2>/dev/null | sort | while IFS= read -r log; do
    echo "-- ${log#"$STORE"/} ($(stat -c %s "$log") bytes)"; tail -n 15 "$log"
  done
else
  echo "(no store_root in the manifest)"
fi
echo "== safe-stop logs (the last 25 lines of each)"
found=0
for log in "$KROOT"/logs/safe-stop-*.log; do
  [ -f "$log" ] || continue
  found=1; echo "-- ${log#"$KROOT"/} ($(stat -c %s "$log") bytes)"; tail -n 25 "$log"
done
[ "$found" -eq 1 ] || echo "(none yet: the supervisor has run no safe-stop since the logs were created)"
echo "== journal (kc_unit_journal: the per-user read, else the four-match system read), the last 30 lines of 6 hours"
J="$(mktemp "${TMPDIR:-/tmp}/kc-lekiwi-journal.XXXXXX")"
if kc_unit_journal "$UNIT" "$(date -d '-6 hours' '+%Y-%m-%d %H:%M:%S')" "$J"; then
  echo "read with: $KC_JOURNAL_SOURCE"; tail -n 30 "$J"
else
  echo "the journal could not be read: $KC_JOURNAL_ERR"
fi
rm -f -- "$J"
STATUS
  exit "${PIPESTATUS[0]}"
fi

# --- prove-signature --------------------------------------------------------------------------------------------------
if [ "$CMD" = "prove-signature" ]; then
  remote "$REMOTE_ENV" <<'PROVE' | redact
BIN="$CUR/bin"; M="$CUR/manifest.txt"; KEY="$KROOT/keys/deployment.keypair"
[ -r "$CUR/app/bin/lib/common.sh" ] && [ -r "$M" ] && [ -r "$KEY" ] || { echo "REFUSE: not installed (the current release's common.sh or manifest, or the deployment key, is missing)"; exit 3; }
. "$CUR/app/bin/lib/common.sh"
PUB="$(kc_keypair_public "$KEY")"; STORE="$(kc_manifest_value "$M" store_root)"; PLAN="$(kc_manifest_value "$M" plan_artifact)"
[ -n "$PUB" ] && [ -n "$STORE" ] && [ -n "$PLAN" ] || { echo "REFUSE: the deployment key or the manifest is unreadable"; exit 3; }
mkdir -p "$KROOT/run" && chmod 700 "$KROOT/run"
SCR="$(mktemp -d "$KROOT/run/prove.XXXXXX")" || { echo "REFUSE: no scratch directory"; exit 3; }
trap 'rm -rf -- "$SCR"' EXIT
FAILS=0
expect() {  # expect LABEL WANT_VERDICT WANT_REASON FILE
  local verdict reason
  verdict="$(sed -nE 's/^verdict (.*)$/\1/p' "$4" | head -n 1)"; reason="$(sed -nE 's/^reason (.*)$/\1/p' "$4" | head -n 1)"
  if [ "$verdict" = "$2" ] && [ "$reason" = "$3" ]; then echo "ok   $1: verdict $verdict${reason:+ reason $reason}"
  else echo "FAIL $1: verdict '${verdict:-none}' reason '${reason:-none}', expected '$2'${3:+ reason '$3'}"; FAILS=$((FAILS + 1)); fi
}
( kc_generate_keypair "$SCR/control.keypair" ) || { echo "REFUSE: could not generate the throwaway control key"; exit 3; }
CONTROL="$(kc_keypair_public "$SCR/control.keypair")"
[ "$CONTROL" != "$PUB" ] || { echo "REFUSE: the throwaway key equals the deployment key"; exit 3; }
echo "deployment public key $PUB"
echo "throwaway control public key $CONTROL (generated here, deleted afterwards)"
DIGESTS="$(sed -nE 's/^([a-z_]*capsule_digest) ([0-9a-f]{64})$/\2/p' "$M" | sort -u)"
[ -n "$DIGESTS" ] || { echo "REFUSE: the manifest names no capsule digest"; exit 3; }
n=0
for d in $DIGESTS; do
  n=$((n + 1)); OBJ="$STORE/objects/$d"
  [ -f "$OBJ" ] || { echo "FAIL capsule $d is not in the deployed store"; FAILS=$((FAILS + 1)); continue; }
  "$BIN/demo-plan" verify --capsule "$OBJ" --policy require --trusted "$PUB" --scratch "$SCR/v$n-trusted" >"$SCR/v$n-trusted.out" 2>&1
  expect "capsule $d under require, deployment key" admitted "" "$SCR/v$n-trusted.out"
  "$BIN/demo-plan" verify --capsule "$OBJ" --policy require --trusted "$CONTROL" --scratch "$SCR/v$n-control" >"$SCR/v$n-control.out" 2>&1
  expect "capsule $d under require, throwaway key only" refused signature_key_id_not_trusted "$SCR/v$n-control.out"
done
TWINS="$(sed -nE 's/^[a-z_]*unsigned[a-z_]*path (.*)$/\1/p' "$M")"
if [ -z "$TWINS" ]; then
  echo "skip the manifest names no unsigned twin: the signature_missing verification was NOT run"
else
  n=0
  while IFS= read -r twin; do
    n=$((n + 1))
    "$BIN/demo-plan" verify --capsule "$twin" --policy require --trusted "$PUB" --scratch "$SCR/u$n" >"$SCR/u$n.out" 2>&1
    expect "unsigned twin $n under require, deployment key" refused signature_missing "$SCR/u$n.out"
  done <<<"$TWINS"
fi
echo "-- the resident's own refusal: a SEPARATE stub-only bundle signed by the throwaway key, a scratch store, socket and Plan"
if ! "$BIN/demo-plan" build --out-dir "$SCR/stub" --signing-key-file "$SCR/control.keypair" >"$SCR/stub-manifest.txt" 2>"$SCR/stub.err"; then
  sed 's/^/  /' "$SCR/stub.err"; echo "FAIL demo-plan build of the stub bundle"; exit 1
fi
SSTORE="$(kc_manifest_value "$SCR/stub-manifest.txt" store_root)"; SPLAN="$(kc_manifest_value "$SCR/stub-manifest.txt" plan_artifact)"
case "$SSTORE:$SPLAN" in "$SCR"/*:"$SCR"/*) ;; *) echo "FAIL the stub bundle is not inside the scratch directory"; exit 1 ;; esac
[ "$SSTORE" != "$STORE" ] && [ "$SPLAN" != "$PLAN" ] || { echo "FAIL the stub bundle would reuse the deployment's store or Plan"; exit 1; }
chmod 700 "$SSTORE"
{
  echo "store_root = $SSTORE"
  echo "admin_socket_path = $SCR/a.sock"
  echo "admin_socket_group = $(id -gn)"
  echo "admin_socket_mode = 0600"
  echo "admin_max_connections = 4"
  echo "signature_policy = require"
  echo "trusted_keys = $PUB"
  echo "flight_ring_capacity = 256"
  echo "target_profile = lekiwi-mvp"
  echo "plan_artifact = $SPLAN"
} >"$SCR/resident.conf"
RC=0
timeout 60 "$BIN/cake-resident" --config "$SCR/resident.conf" >"$SCR/resident.out" 2>&1 || RC=$?
LINE="$(grep -m 1 'is not in the configured trusted key list' "$SCR/resident.out")"
if [ "$RC" -ne 0 ] && [ "$RC" -ne 124 ] && [ -n "$LINE" ]; then
  echo "ok   cake-resident refused the stub capsule and exited $RC:"; echo "     $LINE"
else
  echo "FAIL cake-resident exited $RC$([ "$RC" -eq 124 ] && echo ' (killed after 60 s)'); its output:"; sed 's/^/     /' "$SCR/resident.out" | tail -n 20
  FAILS=$((FAILS + 1))
fi
echo "the scratch directory is removed now; the deployment's store, socket and Plan were not used"
[ "$FAILS" -eq 0 ] && echo "prove-signature: PASS" || { echo "prove-signature: $FAILS failure(s)"; exit 1; }
PROVE
  exit "${PIPESTATUS[0]}"
fi

# --- approach-probe ---------------------------------------------------------------------------------------------------
if [ "$CMD" = "approach-probe" ]; then
  # The probe's own arguments travel as ONE shell-quoted word in the remote environment (each argument quoted by
  # printf %q, joined by spaces, the whole quoted once more for the ssh command line); the remote script splits them
  # back with set --. Nothing in them is interpreted on this side.
  PROBE_ARGS=""
  for a in "$@"; do PROBE_ARGS="$PROBE_ARGS $(printf '%q' "$a")"; done
  remote "$REMOTE_ENV PROBE_ARGS=$(printf '%q' "$PROBE_ARGS")" <<'PROBE' | redact
eval "set -- ${PROBE_ARGS:-}"
exec "$PY" -u "$CUR/app/bin/pi/approach_probe.py" --admin-probe "$CUR/bin/admin-probe" \
  --admin-socket "/run/user/$(id -u)/kc-lekiwi.sock" --manifest "$CUR/manifest.txt" \
  --observations "ipc://$RUN/approach-observations.ipc" --commands "ipc://$RUN/approach-commands.ipc" "$@"
PROBE
  exit "${PIPESTATUS[0]}"
fi

# --- install ----------------------------------------------------------------------------------------------------------
[ -n "${KC_CAKE_AARCH64_DIR:-}" ] || { echo "REFUSE: KC_CAKE_AARCH64_DIR is not set (the laptop directory holding the four aarch64 Cake binaries)" >&2; exit 5; }
for b in "${BINARIES[@]}"; do
  [ -f "$KC_CAKE_AARCH64_DIR/$b" ] && [ -x "$KC_CAKE_AARCH64_DIR/$b" ] || { echo "REFUSE: KC_CAKE_AARCH64_DIR has no executable $b" >&2; exit 5; }
done
# What travels into app/, by its path in this checkout, which is also its path under app/ on the robot computer.
# The host and the capability need the first three and the service's one import, and the safe-stop needs the wheel
# stop, wheels_safe_stop.py; the rest serve approach_probe.py (its imports) and the robot-side scripts (common.sh, which
# needs VERSION beside bin/, and the four templates).
APP_FILES=(
  bin/pi/lekiwi_host_safe_attach.py
  bin/pi/wheels_safe_stop.py
  autonomy/motion/mobile_approach_service.py
  autonomy/motion/mobile_approach.py
  bin/pi/approach_probe.py
  autonomy/motion/cake_capability_adapter.py
  autonomy/motion/capability_registry.py
  autonomy/motion/clock.py
  autonomy/motion/stable_motion_gateway.py
  autonomy/motion/transport.py
  VERSION
  bin/lib/common.sh
  bin/templates/run-host.sh.in
  bin/templates/run-capability.sh.in
  bin/templates/safe-stop.sh.in
  bin/templates/kiwi-cake-lekiwi.service.in
)
for f in "${APP_FILES[@]}"; do [ -f "$ROOT/$f" ] || { echo "REFUSE: $f is missing from this checkout" >&2; exit 5; }; done
# The release's name. The laptop's pid makes two installs within one second distinct; the remote mkdir refuses a
# name that exists all the same.
STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
RELEASE="releases/$STAMP"
PREVIOUS=""

# Every refusal before the commit point says what it left, and it is always the same thing: nothing live was touched.
state_left() {
  case "$PREVIOUS" in
    "") echo "STATE LEFT: nothing was changed on the robot computer (the preflight did not complete)" >&2 ;;
    none) echo "STATE LEFT: nothing is wired: there was no previous install, and no 'current' link, resident.conf or unit was written" >&2 ;;
    *) echo "STATE LEFT: the previous install is untouched and still wired: current -> $PREVIOUS, with its resident.conf, its manifest and the unit exactly as they were" >&2 ;;
  esac
  [ -z "$PREVIOUS" ] || echo "            this build's staging directory ~/$DIR_REL/$RELEASE, if it was created, carries no VERIFIED marker and nothing refers to it; remove it by hand when it is no longer needed" >&2
}
refused() { state_left; exit "$1"; }

echo "== 1/5 install preflight on the robot computer (read-only)"
PRE_LOG="$(mktemp "${TMPDIR:-/tmp}/kc-lekiwi-pre.XXXXXX")"
trap 'rm -f -- "$PRE_LOG"' EXIT
RC=0
remote "$REMOTE_ENV PORT='$KC_SERVO_PORT'" <<'PRE' | redact | tee "$PRE_LOG" || RC=$?
refused=0
[ "$(uname -m)" = "aarch64" ] || { echo "REFUSE: the robot computer is $(uname -m), not aarch64"; refused=1; }
for u in $LEGACY_UNITS; do
  unit_idle "$u" || { echo "REFUSE: the legacy unit $u is $(unit_state "$u" || true); stop it by hand first (this tooling never starts or enables it)"; refused=1; }
done
unit_idle "$UNIT" || { echo "REFUSE: $UNIT is $(unit_state "$UNIT" || true); stop it (bin/pi/lekiwi-deploy.sh stop) before reinstalling"; refused=1; }
systemctl --user show-environment >/dev/null 2>&1 || { echo "REFUSE: systemctl --user is not available in this session"; refused=1; }
[ -x "$PY" ] || { echo "REFUSE: the interpreter was not found on the robot computer (set KC_PI_PYTHON)"; refused=1; }
[ ! -x "$PY" ] || "$PY" -c 'import zmq' >/dev/null 2>&1 || { echo "REFUSE: the interpreter cannot import zmq; the capability process needs pyzmq"; refused=1; }
[ ! -x "$PY" ] || "$PY" -c 'import scservo_sdk, serial' >/dev/null 2>&1 || { echo "REFUSE: the interpreter cannot import scservo_sdk and serial; the safe-stop's wheel stop needs the feetech SDK the host itself uses"; refused=1; }
for t in openssl sha256sum timeout od sed fuser ss flock; do command -v "$t" >/dev/null 2>&1 || { echo "REFUSE: $t is not installed on the robot computer"; refused=1; }; done
[ -e "$PORT" ] || { echo "REFUSE: the servo bus path given in KC_SERVO_PORT is missing on the robot computer"; refused=1; }
# The layout: current is a link to a VERIFIED release, or there is no install at all.
if [ -L "$CUR" ]; then
  [ -f "$CUR/VERIFIED" ] || { echo "REFUSE: current points at a release with no VERIFIED marker; it is left untouched"; refused=1; }
elif [ -e "$CUR" ]; then
  echo "REFUSE: ~/$DIR_REL/current exists and is not a link; it is left untouched"; refused=1
else
  for old in resident.conf manifest.txt bundle; do
    [ ! -e "$KROOT/$old" ] || { echo "REFUSE: ~/$DIR_REL/$old is an install in the earlier single-directory layout; it is left untouched; move ~/$DIR_REL aside by hand"; refused=1; }
  done
fi
[ "$refused" -eq 0 ] || exit 3
echo "linger for this account: $(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null || echo unknown)"
if [ -L "$CUR" ]; then echo "previous install: $(readlink "$CUR")"; else echo "previous install: none"; fi
echo "install preflight ok"
PRE
[ "$RC" -eq 0 ] || { echo "STATE LEFT: nothing was changed on the robot computer" >&2; exit "$RC"; }
PREVIOUS="$(sed -n 's/^previous install: //p' "$PRE_LOG" | tail -n 1)"
[ -n "$PREVIOUS" ] || { echo "REFUSE: the preflight did not say whether an install exists" >&2; echo "STATE LEFT: nothing was changed on the robot computer" >&2; exit 3; }

echo "== 2/5 stage ~/$DIR_REL/$RELEASE: copy the four binaries and the application files into it, and verify every one there"
RC=0
# shellcheck disable=SC2029  # DIR_REL was validated above and RELEASE is built here from a timestamp and a pid
rsh "d='$DIR_REL'; r=\"\$d/$RELEASE\"; mkdir -p \"\$d/releases\" \"\$d/run\" \"\$d/logs\" \"\$d/keys\" && chmod 700 \"\$d\" \"\$d/run\" \"\$d/keys\" && mkdir \"\$r\" && mkdir -p \"\$r/bin\" \"\$r/app/bin/pi\" \"\$r/app/bin/lib\" \"\$r/app/bin/templates\" \"\$r/app/autonomy/motion\"" || RC=$?
[ "$RC" -eq 0 ] || { echo "REFUSE: the staging directory could not be created" >&2; refused "$RC"; }
SRC_PATHS=(); DST_PATHS=()
for b in "${BINARIES[@]}"; do SRC_PATHS+=("$KC_CAKE_AARCH64_DIR/$b"); DST_PATHS+=("bin/$b"); done
for f in "${APP_FILES[@]}"; do SRC_PATHS+=("$ROOT/$f"); DST_PATHS+=("app/$f"); done
# One copy per destination directory, so a directory that already holds a file is never refused for it.
declare -A BY_DIR=()
for i in "${!SRC_PATHS[@]}"; do BY_DIR["$(dirname "${DST_PATHS[$i]}")"]+="$i "; done
for dir in "${!BY_DIR[@]}"; do
  srcs=()
  for i in ${BY_DIR[$dir]}; do srcs+=("${SRC_PATHS[$i]}"); done
  rcp "${srcs[@]}" "$KC_PI_SSH:$DIR_REL/$RELEASE/$dir/" || { RC=$?; echo "REFUSE: a copy into the staging directory failed" >&2; refused "$RC"; }
done
RC=0
# shellcheck disable=SC2029  # the paths are built in this script from fixed names
REMOTE_SUMS="$(rsh "cd '$DIR_REL/$RELEASE' && sha256sum ${DST_PATHS[*]}")" || RC=$?
[ "$RC" -eq 0 ] || { echo "REFUSE: the staged copies could not be checksummed on the robot computer" >&2; refused "$RC"; }
for i in "${!SRC_PATHS[@]}"; do
  LOCAL_SUM="$(sha256sum -- "${SRC_PATHS[$i]}" | cut -d' ' -f1)"
  REMOTE_SUM="$(printf '%s\n' "$REMOTE_SUMS" | awk -v f="${DST_PATHS[$i]}" '$2 == f {print $1}')"
  if [ -z "$LOCAL_SUM" ] || [ "$LOCAL_SUM" != "$REMOTE_SUM" ]; then echo "REFUSE: checksum mismatch for ${DST_PATHS[$i]} between this laptop and the robot computer" >&2; refused 4; fi
done
echo "verified ${#SRC_PATHS[@]} staged files by sha256 on the robot computer"

echo "== 3/5 in the staging directory: the deployment key, the wrappers, demo-plan build-mobile, the manifest, demo-preflight, resident.conf and the unit"
RC=0
remote "$REMOTE_ENV ROBOT_ID='$KC_ROBOT_ID' PORT='$KC_SERVO_PORT' STAMP='$STAMP'" <<'BUILD' | redact || RC=$?
set -e
REL="$KROOT/releases/$STAMP"; APP="$REL/app"; BIN="$REL/bin"; BUNDLE="$REL/bundle"; LOGS="$KROOT/logs"
KEY="$KROOT/keys/deployment.keypair"
refuse() { echo "REFUSE: $1"; exit "${2:-3}"; }
[ -d "$REL" ] && [ ! -e "$REL/VERIFIED" ] && [ ! -e "$BUNDLE" ] || refuse "the staging directory is missing, or already holds a build"
. "$APP/bin/lib/common.sh"
for b in cake-resident admin-probe demo-plan demo-preflight; do
  kc_probe_binary "$BIN/$b" || refuse "$b does not start under this board's loader (not an aarch64 build?)"
done
echo "the four binaries start under this board's loader"
# The deployment key: generated HERE, only when absent, by the recipe the demo has always used. The seed never leaves
# this computer and is never printed; only the public half and its key id are. It lives outside every release.
if [ -f "$KEY" ]; then echo "deployment key: present, kept"; else kc_generate_keypair "$KEY"; echo "deployment key: generated on this robot computer"; fi
chmod 600 "$KEY"
[ "$(stat -c %a "$KEY")" = "600" ] || refuse "the deployment key is not mode 600"
PUB="$(kc_keypair_public "$KEY")"
[ -n "$PUB" ] || refuse "the deployment key has no public half"
KEYID="$("$PY" -c 'import hashlib, sys; print(hashlib.sha256(bytes.fromhex(sys.argv[1])).hexdigest())' "$PUB")"
echo "deployment public key $PUB"
echo "deployment key id $KEYID"
# The host wrapper names fuser, ss and flock by absolute path: Cake runs it with PATH=/usr/bin:/bin and nothing else.
FUSER_BIN="$(command -v fuser)" || refuse "fuser is not installed; the host wrapper's servo owner check needs it"
SS_BIN="$(command -v ss)" || refuse "ss is not installed; the host wrapper's 5555/5556 check needs it"
FLOCK_BIN="$(command -v flock)" || refuse "flock is not installed; the host wrapper's hold on the wheel stop's lock needs it"
for t in "$FUSER_BIN" "$SS_BIN" "$FLOCK_BIN"; do case "$t" in /* ) ;; *) refuse "$t is not an absolute path" ;; esac; case "$t" in *[!A-Za-z0-9_./+-]*) refuse "$t holds a character this installer does not render" ;; esac; done
# The wrappers, rendered from the templates that were copied and verified with everything else.
RUNHOST="$BIN/run-host.sh"; RUNCAP="$BIN/run-capability.sh"; SAFESTOP="$BIN/safe-stop.sh"
# The safe-stop template carries no placeholder (segment 2 copies it as it is, and it then only logs). Its four wheel
# settings are empty lines of their own, WHEELS_NAME='', which only it has: filled here, they turn its wheel stop on.
# The wheel stop's lock is one file for both: the host wrapper takes it (@@WHEELS_LOCK@@), the wheel stop writes only
# while it holds it (WHEELS_LOCK).
WHEELS_HELPER="$APP/bin/pi/wheels_safe_stop.py"; WHEELS_LOCK="$RUN/wheels-safe-stop.lock"
render() {  # render TEMPLATE DESTINATION
  sed -e "s|@@PYTHON@@|$PY|g" -e "s|@@HOST_ENTRY@@|$APP/bin/pi/lekiwi_host_safe_attach.py|g" \
      -e "s|@@ROBOT_ID@@|$ROBOT_ID|g" -e "s|@@ROBOT_PORT@@|$PORT|g" -e "s|@@APP_DIR@@|$APP|g" -e "s|@@RUN_DIR@@|$RUN|g" \
      -e "s|@@ARM_TOKEN@@|$TOKEN|g" -e "s|@@FUSER@@|$FUSER_BIN|g" -e "s|@@SS@@|$SS_BIN|g" \
      -e "s|@@FLOCK@@|$FLOCK_BIN|g" -e "s|@@WHEELS_LOCK@@|$WHEELS_LOCK|g" \
      -e "s|^WHEELS_PYTHON=''\$|WHEELS_PYTHON='$PY'|" -e "s|^WHEELS_HELPER=''\$|WHEELS_HELPER='$WHEELS_HELPER'|" \
      -e "s|^WHEELS_PORT=''\$|WHEELS_PORT='$PORT'|" -e "s|^WHEELS_LOCK=''\$|WHEELS_LOCK='$WHEELS_LOCK'|" \
      "$APP/bin/templates/$1" >"$2.tmp"
  ! kc_template_has_placeholder "$2.tmp" || refuse "$(basename "$2") still carries a placeholder after rendering"
  bash -n "$2.tmp" || refuse "$(basename "$2") does not parse"
  chmod 755 "$2.tmp" && mv -f "$2.tmp" "$2"
}
render run-host.sh.in "$RUNHOST"
render run-capability.sh.in "$RUNCAP"
render safe-stop.sh.in "$SAFESTOP"
for want in "PYTHON='$PY'" "ROBOT_ID='$ROBOT_ID'" "ROBOT_PORT='$PORT'" "ARM_TOKEN='$TOKEN'" "FUSER='$FUSER_BIN'" "SS='$SS_BIN'" \
  "FLOCK='$FLOCK_BIN'" "WHEELS_LOCK='$WHEELS_LOCK'" \
  'exec "$PYTHON" -u "$HOST_ENTRY" \' '  --robot.max_relative_target=15.0 \' \
  '  --robot.disable_torque_on_disconnect=false \' "  '--robot.cameras={}' \\" '  --host.connection_time_s=86400'; do
  grep -qxF -- "$want" "$RUNHOST" || refuse "the rendered host wrapper lacks the line: $want"
done
# The safe-stop's wheel stop: all four settings rendered from this deployment, the hard limit in place, and the helper's
# self-check under the robot's python, in the environment the supervisor gives a safe-stop (PATH=/usr/bin:/bin and
# nothing else): it runs what a stop runs before it opens the bus (the /proc owner scan, the feetech SDK import), timed
# by its own process age, and fails (SELF-CHECK TOO SLOW) when this interpreter gets there later than the stop's budget
# lets a readback begin, or when the scan cannot see its own process. It opens no device.
for want in "WHEELS_PYTHON='$PY'" "WHEELS_HELPER='$WHEELS_HELPER'" "WHEELS_PORT='$PORT'" "WHEELS_LOCK='$WHEELS_LOCK'" \
  'timeout -s KILL 0.42 "$WHEELS_PYTHON" -u "$WHEELS_HELPER" --port "$WHEELS_PORT" --lock "$WHEELS_LOCK" >>"$out" 2>&1 </dev/null'; do
  grep -qxF -- "$want" "$SAFESTOP" || refuse "the rendered safe-stop lacks the line: $want"
done
SELFCHECK="$(env -i PATH=/usr/bin:/bin "$PY" -u "$WHEELS_HELPER" --self-check 2>&1)" ||
  { printf '%s\n' "$SELFCHECK" | sed 's/^/  /'; refuse "the wheel stop's self-check failed under this interpreter"; }
printf '%s\n' "$SELFCHECK" | sed 's/^/  /'
echo "rendered run-host.sh, run-capability.sh and safe-stop.sh (host flags: max_relative_target 15.0, torque kept on disconnect, cameras {}; one-shot arm token ~/${TOKEN#"$HOME"/}; the wheel stop's lock ~/${WHEELS_LOCK#"$HOME"/}, held by the host for its whole life; fail-closed ownership check; safe-stop: goal velocity 0 to wheels 7 8 9 under a 0.42 s limit, only while it holds that lock)"
# The safe attach of the host this release starts (operator ruling 2026-09-27): the one-count wheel rule, read by its exact
# lines from the staged copy step 2 verified by sha256. The host file is in the VERIFIED marker below as well.
ATTACH="$(attach_rule_verdict "$APP/bin/pi/lekiwi_host_safe_attach.py")" ||
  { echo "$ATTACH"; refuse "the staged host lacks the safe attach's one-count wheel rule; install from a checkout whose bin/pi/lekiwi_host_safe_attach.py carries it"; }
echo "$ATTACH"
# The Plan is built HERE, by this board's demo-plan, INTO THE STAGING DIRECTORY, so its target profile is this
# binary's architecture and every path it names (the wrappers, the holder stub, child-cwd, the store) is inside this
# release, which never moves. Nothing live refers to it until the commit step.
M="$REL/manifest.txt"
if ! "$BIN/demo-plan" build-mobile --out-dir "$BUNDLE" --signing-key-file "$KEY" --run-tag "kc-lekiwi-$STAMP" \
  --child-command "$RUNHOST" --child-stop-signal SIGINT \
  --safe-stop-command "$SAFESTOP" --safe-stop-command "$LOGS/safe-stop-host.log" \
  --capability-child-command "$RUNCAP" --capability-child-stop-signal SIGINT \
  --capability-safe-stop-command "$SAFESTOP" --capability-safe-stop-command "$LOGS/safe-stop-capability.log" \
  >"$M" 2>"$REL/demo-plan.err"; then
  sed 's/^/  /' "$REL/demo-plan.err"; refuse "demo-plan build-mobile did not produce its artifacts" 1
fi
CHILD="$(kc_manifest_value "$M" child_command)"
CAPCHILD="$(kc_manifest_value "$M" capability_child_command)"
HOLDER="$(kc_manifest_value "$M" holder_child_command)"
[ "$CHILD" = "$RUNHOST" ] || refuse "the manifest's child_command is '$CHILD', not the rendered host wrapper $RUNHOST"
[ "$CAPCHILD" = "$RUNCAP" ] || refuse "the manifest's capability_child_command is '$CAPCHILD', not the rendered capability wrapper $RUNCAP"
[ "$HOLDER" = "$BUNDLE/child/stub-child.sh" ] && [ -f "$HOLDER" ] || refuse "the manifest's holder_child_command is '$HOLDER', not the bundle's stub child"
[ "$(kc_manifest_value "$M" public_key)" = "$PUB" ] || refuse "the manifest's public_key is not the deployment key"
STORE="$(kc_manifest_value "$M" store_root)"; PLAN="$(kc_manifest_value "$M" plan_artifact)"
[ "$PLAN" = "$BUNDLE/plans/mobile-demo.cakepln" ] || refuse "the manifest's plan_artifact is '$PLAN', not the STARTING Plan mobile-demo.cakepln"
[ "$STORE" = "$BUNDLE/state-root" ] || refuse "the manifest's store_root is '$STORE', not the bundle's state-root"
for p in mobile-demo mobile-demo-swapped mobile-demo-active-successor-abort-start mobile-demo-active-successor-abort-swap; do
  [ -f "$BUNDLE/plans/$p.cakepln" ] || refuse "$p.cakepln was not written"
done
[ -d "$STORE" ] && [ -d "$BUNDLE/child-cwd" ] || refuse "the store or child-cwd was not written"
chmod 700 "$STORE"
echo "manifest ok: child_command is run-host.sh, capability_child_command is run-capability.sh, holder_child_command is the stub; state-root is mode 700"
DIGESTS="$(sed -nE 's/^([a-z_]*capsule_digest) ([0-9a-f]{64})$/\2/p' "$M" | sort -u)"
[ -n "$DIGESTS" ] || refuse "the manifest names no capsule digest"
PF="$REL/preflight"
for d in $DIGESTS; do
  mkdir -p "$PF/$d"; RC=0
  "$BIN/demo-preflight" --capsule "$STORE/objects/$d" --trusted-key "$PUB" --store-scratch "$PF/$d/scratch" \
    --binary "$BIN/cake-resident" --binary "$BIN/admin-probe" --binary "$BIN/demo-plan" --binary "$BIN/demo-preflight" \
    >"$PF/$d/out" 2>"$PF/$d/err" || RC=$?
  sed 's/^/  /' "$PF/$d/out"; sed 's/^/  /' "$PF/$d/err"
  VERDICT="$(kc_classify_preflight "$PF/$d/out" "$PF/$d/err" "$RC")"
  case "$VERDICT" in
    pass | five-green-observer-refused) echo "preflight accepted capsule $d ($VERDICT)" ;;
    refused:*)
      check="${VERDICT#refused:}"; check="${check%%:*}"; msg="${VERDICT#refused:*:}"
      kc_explain_refusal "$check" "$msg"; refuse "demo-preflight refused capsule $d at check $check" ;;
    *) kc_explain_unparsed "$VERDICT"; refuse "demo-preflight output for capsule $d could not be parsed (${VERDICT#unparsed:})" ;;
  esac
done
rm -rf -- "$PF"
# THE CAKE SHOWCASE'S CANDIDATES, prepared from what build-mobile wrote here and from nothing else:
#   - the TAMPERED TWIN of the swapped Plan: its bytes with ONE byte flipped at the middle. That breaks the artifact's
#     digests, so the resident refuses it before opening any transaction: an integrity refusal, never a signature one;
#   - the UNDO candidate: the STARTING Plan mobile-demo.cakepln itself, which returns the capability slot to the inert
#     capsule;
#   - candidates.txt: one `candidate ROLE KEY SHA256 PATH` line per candidate, the table a runtime configuration's roles
#     are written from (activate, refused-demo, tampered-demo, undo);
#   - an UNSIGNED capsule twin for the signature check's signature_missing row, only when the manifest names none: a
#     separate `demo-plan build` into its own directory, signed with the same deployment key, whose unsigned capsule is
#     written beside it and never admitted to any store. Its path is added to the manifest as unsigned_capsule_path.
SWAP="$BUNDLE/plans/mobile-demo-swapped.cakepln"; TAMPER="$BUNDLE/plans/mobile-demo-swapped-tampered.cakepln"
"$PY" -c 'import sys
data = bytearray(open(sys.argv[1], "rb").read())
if len(data) < 2: sys.exit(1)
data[len(data) // 2] ^= 0x01
open(sys.argv[2], "wb").write(bytes(data))
original = open(sys.argv[1], "rb").read(); twin = open(sys.argv[2], "rb").read()
sys.exit(0 if len(original) == len(twin) and sum(a != b for a, b in zip(original, twin)) == 1 else 1)' "$SWAP" "$TAMPER" \
  || refuse "the tampered twin could not be written with exactly one byte flipped"
echo "tampered twin: mobile-demo-swapped-tampered.cakepln, one byte flipped at the middle (the resident refuses it before any transaction, an integrity refusal)"
LISTING="$REL/candidates.txt"; : >"$LISTING"
for pair in activate:mobile-demo-swapped refused-demo:mobile-demo-active-successor-abort-swap \
            tampered-demo:mobile-demo-swapped-tampered undo:mobile-demo; do
  key="${pair#*:}"; path="$BUNDLE/plans/$key.cakepln"
  [ -f "$path" ] || refuse "the candidate $key.cakepln is missing"
  printf 'candidate %s %s %s %s\n' "${pair%%:*}" "$key" "$(sha256sum "$path" | cut -d' ' -f1)" "$path" >>"$LISTING"
done
chmod 600 "$LISTING"
echo "candidates: activate mobile-demo-swapped, refused-demo the abort-swap successor, tampered-demo its twin, undo the STARTING Plan (candidates.txt)"
if grep -qE '^[a-z_]*unsigned[a-z_]*path ' "$M"; then
  echo "unsigned twin: the manifest names one"
else
  TWIN_DIR="$REL/unsigned-twin"; RC=0
  "$BIN/demo-plan" build --out-dir "$TWIN_DIR" --signing-key-file "$KEY" --run-tag "kc-lekiwi-$STAMP-twin" \
    >"$REL/unsigned-twin.txt" 2>"$REL/unsigned-twin.err" || RC=$?
  TWIN="$(kc_manifest_value "$REL/unsigned-twin.txt" unsigned_capsule_path)"
  case "$TWIN" in "$TWIN_DIR"/*) ;; *) TWIN="" ;; esac
  if [ "$RC" -eq 0 ] && [ -n "$TWIN" ] && [ -f "$TWIN" ]; then
    printf 'unsigned_capsule_path %s\n' "$TWIN" >>"$M"
    echo "unsigned twin: built beside the release (never admitted to a store) and named in the manifest"
  else
    echo "unsigned twin: NOT built (demo-plan build exited $RC or named no unsigned capsule); the signature check's twin row stays skipped"
  fi
fi
# The release's resident.conf: kc_write_resident_conf's keys, with the admin socket owner-only (0600) rather than that
# helper's group-readable 0660, and every path inside this release.
CONF="$REL/resident.conf"; SOCK="/run/user/$(id -u)/kc-lekiwi.sock"
( umask 077
  {
    echo "store_root = $STORE"
    echo "admin_socket_path = $SOCK"
    echo "admin_socket_group = $(id -gn)"
    echo "admin_socket_mode = 0600"
    echo "admin_max_connections = 4"
    echo "signature_policy = require"
    echo "trusted_keys = $PUB"
    echo "flight_ring_capacity = 256"
    echo "target_profile = lekiwi-mvp"
    echo "plan_artifact = $PLAN"
  } >"$CONF" )
chmod 600 "$CONF"
# The release's unit file. Its ExecStart and --config go through current, so the unit runs whichever release current
# names, and current only ever names a VERIFIED release.
STAGED_UNIT="$REL/$UNIT"
sed -e "s|@@CAKE_RESIDENT@@|$CUR/bin/cake-resident|" -e "s|@@RESIDENT_CONF@@|$CUR/resident.conf|" "$APP/bin/templates/kiwi-cake-lekiwi.service.in" >"$STAGED_UNIT"
! kc_template_has_placeholder "$STAGED_UNIT" || refuse "the rendered unit still carries a placeholder"
grep -qxF "ExecStart=$CUR/bin/cake-resident --config $CUR/resident.conf" "$STAGED_UNIT" || refuse "the unit's ExecStart did not render as expected"
grep -qx 'KillMode=mixed' "$STAGED_UNIT" && grep -qE '^TimeoutStopSec=[0-9]+$' "$STAGED_UNIT" || refuse "the rendered unit lacks KillMode=mixed or TimeoutStopSec"
! grep -q '^\[Install\]' "$STAGED_UNIT" || refuse "the rendered unit carries an [Install] section; it must not be enablable"
# The VERIFIED marker, written last: the checksums of what the commit wires up and of what the Plan runs.
( cd "$REL" && sha256sum manifest.txt resident.conf "$UNIT" bin/cake-resident bin/run-host.sh bin/run-capability.sh \
    bin/safe-stop.sh app/bin/pi/wheels_safe_stop.py app/bin/pi/lekiwi_host_safe_attach.py bundle/plans/mobile-demo.cakepln \
    bundle/plans/mobile-demo-swapped-tampered.cakepln candidates.txt >VERIFIED.tmp && mv -f VERIFIED.tmp VERIFIED )
echo "staged and VERIFIED: releases/$STAMP (resident.conf: signature_policy require, trusted_keys the deployment key, admin socket 0600, the STARTING Plan); nothing live refers to it yet"
BUILD
[ "$RC" -eq 0 ] || refused "$RC"

echo "== 4/5 commit: switch current to the verified release, write the user unit, daemon-reload (the unit is NOT started or enabled)"
RC=0
remote "$REMOTE_ENV STAMP='$STAMP'" <<'COMMIT' | redact || RC=$?
REL="$KROOT/releases/$STAMP"; UNIT_DIR="$HOME/.config/systemd/user"
PREV="$(readlink "$CUR" 2>/dev/null || true)"
before() {
  if [ -n "$PREV" ]; then echo "the previous install is untouched and still wired: current -> $PREV, with its resident.conf, its manifest and the unit exactly as they were"
  else echo "nothing is wired: there was no previous install, and no 'current' link or unit was written"; fi
}
refuse() { echo "REFUSE: $1"; echo "STATE LEFT: $2"; exit 3; }
[ -f "$REL/VERIFIED" ] || refuse "releases/$STAMP carries no VERIFIED marker" "$(before)"
( cd "$REL" && sha256sum --quiet -c VERIFIED >/dev/null 2>&1 ) || refuse "a file of releases/$STAMP changed after it was verified" "$(before)"
for u in $LEGACY_UNITS "$UNIT"; do
  unit_idle "$u" || refuse "$u is $(unit_state "$u" || true)" "$(before)"
done
rm -f -- "$UNIT_PATH.new" "$KROOT/current.new"
{ mkdir -p "$UNIT_DIR" && cp -f -- "$REL/$UNIT" "$UNIT_PATH.new"; } || { rm -f -- "$UNIT_PATH.new"; refuse "the unit file could not be staged" "$(before)"; }
# THE COMMIT POINT: one rename. Before it everything live is the previous install; after it, current names this
# verified release, and so do the resident.conf and the Plan the unit runs through it.
if ! { ln -s "releases/$STAMP" "$KROOT/current.new" && mv -T -- "$KROOT/current.new" "$CUR"; }; then
  rm -f -- "$KROOT/current.new" "$UNIT_PATH.new"
  refuse "the current link could not be switched" "$(before)"
fi
AFTER="current -> releases/$STAMP, a VERIFIED release"
echo "committed: $AFTER"
if ! mv -f -T -- "$UNIT_PATH.new" "$UNIT_PATH"; then
  rm -f -- "$UNIT_PATH.new"
  if [ -f "$UNIT_PATH" ]; then was="the previous unit file, which also runs whatever current names"; else was="absent, so nothing can start the release"; fi
  echo "REFUSE: the unit file could not be written"; echo "STATE LEFT: $AFTER; the unit file is $was"; exit 3
fi
systemctl --user daemon-reload || { echo "REFUSE: systemctl --user daemon-reload failed"; echo "STATE LEFT: $AFTER, and the unit file names it, but systemd has not reloaded it: run systemctl --user daemon-reload on the robot computer"; exit 3; }
STATE="$(unit_state "$UNIT" || true)"
ENABLED="$(systemctl --user is-enabled "$UNIT" 2>/dev/null || true)"
echo "installed ~/.config/systemd/user/$UNIT: active state '${STATE:-unknown}', enablement '${ENABLED:-unknown}'"
unit_idle "$UNIT" || { echo "REFUSE: $UNIT reports '$STATE'; this tooling never starts it outside arm, so something else did"; echo "STATE LEFT: $AFTER"; exit 3; }
COMMIT
if [ "$RC" -eq 255 ]; then
  echo "REFUSE: ssh was lost during the commit step" >&2
  echo "STATE LEFT: UNKNOWN: current names either the previous install ($PREVIOUS) or releases/$STAMP, both VERIFIED; read it with: $0 status" >&2
  exit 255
fi
[ "$RC" -eq 0 ] || exit "$RC"

echo "== 5/5 done: current -> $RELEASE. NOTHING WAS STARTED OR ENABLED, and nothing moved."
echo "   Starting ${UNIT} starts the motor host: it attaches to the arm (the safe attach refuses a wheel reading more than"
echo "   one velocity count, a goal velocity other than 0 or a joint without torque) and establishes the base zero-speed"
echo "   hold. That is a separate operator step, with the robot on its stand and a hand near power, and the ONLY way this"
echo "   tooling takes it: $0 arm"
echo "   Evidence afterwards: $0 status"
