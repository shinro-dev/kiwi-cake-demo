#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# bin/laptop/run-cycle.sh ACTION [--holding] [--plan-only]: the single-cube local cycle, ONE bounded action per call (or
# the whole cycle), against the running NORMAL-mode motor host (bin/pi/start-host.sh). Runs on the laptop.
#
#   bin/laptop/run-cycle.sh cycle      THE WHOLE CYCLE from the low scan pose (gripper acquired open, NORMAL host): states and predicates are printed as [STATE]
#   bin/laptop/run-cycle.sh observe
#   bin/laptop/run-cycle.sh pan:+2.2 | move:-4,-20 | grip:latch | grip:open | exit   [--holding] [--plan-only]
#   bin/laptop/run-cycle.sh base:+0.3 | base:-0.3   ONE measured base pulse (s, signed) from the low scan pose
# The actions are those of bin/laptop/manipulation/run_single_cube_cycle.py (docs/autonomous-demo/single-cube-local-cycle.md).
# Ctrl-C stops the waypoints (the arm holds).
# EMERGENCY STOP = the servo power switch. The `cycle` and `base:` actions MOVE THE BASE (bounded pulses through the
# gateway, arm folded at the low scan pose, up to 12 cm toward the pad and back): keep 15 cm of free floor in front of
# and behind the base.
#
# Every identity comes from the environment, never from this file and never from a default, because this file is
# committed and a robot's address, account, calibration id and cameras are private:
#   KC_PI_SSH            the ssh target of the robot computer (host preflight here; one-frame captures by the runner)
#   KC_PI_IP             the address the LeKiwi client connects to
#   KC_ROBOT_ID          the id the LeKiwi was calibrated with
#   KC_WRIST_CAMERA_ID   the stable identity of the wrist camera on the robot computer
#   KC_FRONT_CAMERA_ID   the stable identity of the front camera on the robot computer
# The two camera identities are not used by this script itself: they are exported to the runner, which hands them to
# the capture process on the robot computer; both are required because the demonstrated launcher always set both.
# Optional: KC_CYCLE_LOG_DIR, where the text log of the call is written (default: .local-dev/observations under this
# checkout, which git ignores).
# Exit status: 3 the host preflight refused, 5 usage or a missing identity, otherwise the status of the runner.
set -euo pipefail
[ "$#" -ge 1 ] && [ -n "$1" ] || { echo "usage: $0 ACTION [--holding] [--plan-only]" >&2; exit 5; }
ACTION="$1"; shift

# Fail closed on identities: all missing names are reported at once, and no value is ever printed.
MISSING=0
need() {
  local name="$1" what="$2"
  if [ -z "${!name:-}" ]; then echo "REFUSE: $name is not set ($what). Set it in your shell from your private, uncommitted runtime configuration." >&2; MISSING=1; fi
}
need KC_PI_SSH "the ssh target of the robot computer"
need KC_PI_IP "the address the LeKiwi client connects to"
need KC_ROBOT_ID "the id the LeKiwi was calibrated with"
need KC_WRIST_CAMERA_ID "the stable identity of the wrist camera on the robot computer"
need KC_FRONT_CAMERA_ID "the stable identity of the front camera on the robot computer"
[ "$MISSING" -eq 0 ] || exit 5
case "$KC_PI_SSH" in -*) echo "REFUSE: KC_PI_SSH starts with '-': ssh would read it as an option." >&2; exit 5 ;; esac
export KC_PI_SSH KC_WRIST_CAMERA_ID KC_FRONT_CAMERA_ID

# The repository root comes from where this script is, never from a fixed checkout path.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=6 "$KC_PI_SSH")
TS=$(date -u +%Y%m%dT%H%M%SZ)
# Resolved ONCE to an absolute path, so that the directory that is checked below is the directory that is created: a
# relative KC_CYCLE_LOG_DIR means "relative to where the operator stands", which is not where git -C would look for it.
LOG_DIR="$(realpath -m "${KC_CYCLE_LOG_DIR:-$ROOT/.local-dev/observations}")"
# The log holds real robot output: inside this checkout it may only go where git ignores it.
case "$LOG_DIR/" in
  "$(realpath -m "$ROOT")"/*) git -C "$ROOT" check-ignore -q "$LOG_DIR" || { echo "REFUSE: the log directory is inside this checkout and git does not ignore it" >&2; exit 5; } ;;
esac
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/cycle-run-$TS-$(printf '%s' "$ACTION" | tr -c 'A-Za-z0-9.+-' '_').txt"

# Host preflight, in ONE ssh call so that a dropped connection cannot pass for an answer: only an explicit 0 from the
# robot computer continues. A scan-mode host leaves the gripper limp and strips the gripper key from every action,
# so a grasp against it would close nothing: the cycle is refused outright. The mode is read from the environment of
# the running host process, which is where bin/pi/start-host.sh puts it.
pre=0
"${SSH[@]}" 'bash -s' <<'PRE' || pre=$?
set -u
pids=$(pgrep -f '[l]ekiwi_host_safe_attach.py') || { echo "REFUSE: the safe-attach motor host is not running (bin/pi/start-host.sh)"; exit 3; }
checked=0
for pid in $pids; do
  [ -e "/proc/$pid" ] || continue  # gone since pgrep listed it
  # THE READ ITSELF IS THE TEST. The permission bits of /proc/PID/environ can allow what the kernel then denies (a process
  # that is not dumpable), and a read that failed silently would pass for "KC_SCAN_MODE is not set". Which of the matched
  # processes is the host is not known here, so ONE that cannot be read leaves the mode unknown and refuses the call.
  if ! vars=$({ tr '\0' '\n' <"/proc/$pid/environ"; } 2>/dev/null); then echo "REFUSE: the environment of a process that names the motor host cannot be read, so its mode is unknown"; exit 3; fi
  checked=$((checked + 1))
  if printf '%s\n' "$vars" | grep -qx 'KC_SCAN_MODE=1'; then echo "REFUSE: the motor host runs in SCAN MODE (gripper limp)"; exit 3; fi
done
[ "$checked" -ge 1 ] || { echo "REFUSE: the safe-attach motor host is not running any more (bin/pi/start-host.sh)"; exit 3; }
echo "preflight ok: the safe-attach motor host runs in normal mode"
PRE
[ "$pre" -eq 0 ] || { echo "REFUSE: the host preflight did not pass (status $pre); nothing was commanded" >&2; exit 3; }

rc=0
python3 "$ROOT/bin/laptop/manipulation/run_single_cube_cycle.py" --do "$ACTION" --remote-ip "$KC_PI_IP" --robot-id "$KC_ROBOT_ID" "$@" 2>&1 | (trap '' INT; grep --line-buffered -vE 'Warning|warn\(|Loading weights' | tee -a "$LOG") || rc=$?
echo "exit status: $rc   log: $LOG"
exit "$rc"
