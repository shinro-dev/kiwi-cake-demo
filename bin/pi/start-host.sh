#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# bin/pi/start-host.sh [scan] [boundary] [check]: start the CAMERA-LESS motor host on the robot computer through the verify-first
# safe attach (bin/pi/lekiwi_host_safe_attach.py). It lives under bin/pi because what it starts runs there, but it is RUN
# ON THE LAPTOP: it reaches the robot computer over ssh and the host stays in the FOREGROUND of THIS terminal. Keep the
# terminal open; stop the host with Ctrl-C: the arm KEEPS HOLDING afterwards (disable_torque_on_disconnect=false).
#
#   bin/pi/start-host.sh                  normal mode: six arm joints holding
#   bin/pi/start-host.sh scan             scan mode: five joints held, the gripper limp and never written by the host
#   bin/pi/start-host.sh boundary         normal mode WITH the host boundary (docs/autonomous-demo/host-boundary.md)
#   bin/pi/start-host.sh scan boundary    both, in either order
#   bin/pi/start-host.sh check            steps 1 and 3 ONLY: the preflight and the source check, then stop. It copies
#                                         nothing (step 2 is skipped) and STARTS NO HOST (step 4 is skipped); exit 0
#                                         means both held. bin/pi/lekiwi-deploy.sh check runs it this way. It combines
#                                         with 'boundary' (which adds the stock host loop pass) and with 'scan'.
#
# The words are the only arguments; each may appear once. EVERY start takes the same four steps. WHAT TRAVELS TO THE
# ROBOT COMPUTER IS DECIDED BY THE MODE, and by nothing else:
#   - a NORMAL start copies ONE file into the new remote directory, the wrapper, which is exactly the one file it copied
#     before the source check of step 3 existed. Step 3 adds nothing to that list, because it deploys nothing.
#   - a start with the word 'boundary' copies TWO, the wrapper and bin/pi/host_boundary.py, because on that start the
#     wrapper IMPORTS that module on the robot computer when KC_HOST_BOUNDARY=1, and a module that is imported has to be
#     present. That copy is checksummed there and used there, as it always was.
# Without the word nothing imports that module on the robot computer and nothing puts KC_HOST_BOUNDARY in the host's
# environment, so the host that starts is the host that always started.
#
# STEP 3, THE SOURCE CHECK, AND WHERE IT RUNS: read-only passes over the lerobot INSTALLED ON THE ROBOT COMPUTER, made
# HERE, on this laptop. The robot computer is only READ, in two kinds of ssh call that write nothing and leave nothing
# behind: one asks the interpreter the host will run with where lerobot is installed, and one per file copies that
# file's text back here with cat. The laptop then runs its own python3 over this checkout's own bin/pi/host_boundary.py,
# against the copies. The interpreter the host will run with still decides WHICH lerobot is read, because it is the one
# asked where lerobot is; what is no longer true is that the reading program has to be deployed there to read it.
# ONE DIFFERENCE THIS MAKES, stated because it is real: the text is now parsed by this laptop's python3 rather than by
# the robot computer's. The bytes are the same either way, so a mismatch a pass reports is still a mismatch. But an
# installed file this laptop's python could not parse AT ALL, because that lerobot uses syntax this python does not
# know, would come back as "the source does not parse", which this launcher would report as a source that changed. If
# that ever happens, the two interpreters are far enough apart that it is worth knowing about on its own.
# The two passes govern different things, so they are asked for separately and each one's verdict is printed on its own:
#   - host_boundary.py --verify-robot on lekiwi.py, where LeKiwi.send_action lives. THIS PASS RUNS ON EVERY START, with
#     the word 'boundary' or without it, because what it governs is the base path itself. That file carries local
#     changes no lerobot commit has, and the base path depends on one of them: without the empty-arm guard a
#     velocity-only command, which is what every base move sends, reaches sync_write("Goal_Position", {}), the motors
#     bus raises StopIteration before Goal_Velocity is written, the stock loop swallows it, and its watchdog stops the
#     base half a second later. Nothing is raised on the laptop, so it presents as a base that does not move, with no
#     error anywhere. A checkout, a pull or a reinstall on the robot computer takes that guard away again in silence,
#     which is why the file is read EVERY start: having applied a local patch and the file the host will import still
#     carrying it are two facts, and only the second one keeps the base moving.
#   - host_boundary.py --verify-stock-host on lekiwi_host.py, the loop the boundary replaces a socket in. THIS PASS RUNS
#     ONLY WITH THE WORD 'boundary', because what it governs is the assumptions the boundary makes when it patches that
#     loop. Without the word the boundary is never installed, nothing patches the loop, the loop's shape decides
#     nothing, and a start must not be refused because it drifted: a plain start refused over a loop it will not touch
#     is a refusal for the wrong reason, and an operator who cannot start a base that is fine learns to ignore this
#     launcher's refusals.
# Every pass that was asked for runs, whatever an earlier one found, so the operator is told everything that drifted at
# once, and the host is not started unless every pass that ran holds. A pass that could not be run at all refuses the
# start too, but it is reported as a check that did not happen, never as a source that changed.
#
# WHAT A PASS OF STEP 3 PROVES, AND WHAT IT DOES NOT. It proves one thing: the lerobot source installed on the robot
# computer is still the source this repository expects. It is text, read as text. IT IS NOT A STATEMENT THAT THE HOST
# WILL ATTACH, and it must never be read as one. Whether the host attaches is decided afterwards, in step 4, by
# bin/pi/lekiwi_host_safe_attach.py, against the LIVE REGISTERS of the arm: it refuses when a joint's torque is not ON,
# and when a joint's goal and present positions disagree by more than ATTACH_TOLERANCE_TICKS, which is 12 ticks, about
# one degree. A power cycle leaves exactly that state behind, because it drops torque and resets every Goal_Position to
# 0 while the arm stays physically where it is, so the difference on every joint is then hundreds of ticks. A step 3
# that passes and an attach that refuses are therefore both ordinary, and they can happen on the same start: the source
# is fine and the arm has simply not been acquired yet. Acquire it first (docs/autonomous-demo/robot-host-setup.md).
#
# THE BOUNDARY, and only with the word 'boundary':
#   - the copy of bin/pi/host_boundary.py that step 2 checksummed is the one the wrapper imports at load time when
#     KC_HOST_BOUNDARY=1, so a missing or altered file must be caught HERE: an import that fails takes the process down
#     before main(), after the servo bus was opened. A missing file refuses with status 5, a mismatch with 4.
#   - step 3 adds the --verify-stock-host pass described above, and refuses the start when it does not hold.
#   - KC_HOST_BOUNDARY=1 is put in the host process's environment. Nothing else in this repository sets that variable.
# The boundary rejects every command that carries no live lease metadata. The controller attaches that metadata whenever
# KC_HOST_LEASE and KC_HOST_SESSION are set, and the console runtime acquires the lease and puts exactly those two into
# the environment of the controller it starts: a 'boundary' host driven by that runtime is the console's physical cycle,
# not a combination reserved for a qualification session. What still accepts no command is a controller started by hand
# from a shell that names no lease: it attaches nothing and every one of its commands is rejected, which is what the
# qualification session (bin/laptop/qualify-robot.sh, docs/autonomous-demo/hardware-qualification.md) exists to show.
#
# Precondition: the arm was acquired and HOLDS its pose (docs/autonomous-demo/robot-host-setup.md). The attach verifies
# by reads first; if any invariant fails it exits before any write and the arm keeps holding. When everything holds it
# writes NO arm register, but it DOES write to the base: a wheel whose torque is off gets Goal_Velocity 0 and then
# Torque_Enable 1 (the zero-speed hold). This startup is therefore not read-only.
#
# Every identity comes from the environment, never from this file and never from a default, because this file is
# committed and a robot's address, account, calibration id and serial adapter are private:
#   KC_PI_SSH      the ssh target of the robot computer (key-based login; BatchMode never asks for a password)
#   KC_ROBOT_ID    the id the LeKiwi was calibrated with
#   KC_SERVO_PORT  the servo bus device on the robot computer, by its stable /dev/serial/by-id/ entry
# Optional: KC_PI_PYTHON, an absolute path to the python of the lerobot install on the robot computer (default: the
# miniconda python in the remote account's home directory).
# The camera identities are not needed here: the camera preflight refuses when ANY video node has an owner.
# Exit status, and it says the same thing whichever step produced it: 3 a preflight refused, or a pass of step 3 ran and
# the installed lerobot source does not hold what that pass reads it for, or step 3 could NOT BE RUN at all, whether
# because the robot computer could not answer where its lerobot is, because a file did not come back, or because this
# laptop has no python3 to read it with. Those last two both refuse, and the refusal says which one it was, because a
# check that never reached a verdict is no verdict about the source. 4 a copied file does not match. 5 usage, or a
# missing or unsafe identity, or bin/pi/host_boundary.py is missing from this checkout. 255 ssh could not reach the
# robot computer, in ANY of the three steps before the host: step 3 turns that status into a refusal of its own rather
# than reading it as drift, so 255 is never a statement about the installed lerobot. Any other status before the host
# belongs to the ssh or the scp that failed, a copy that did not go through among them.
# Once the host is reached the status is the status of the ssh session that ran it, and THAT LAST STATUS SAYS NOTHING
# ABOUT THE ATTACH: the remote command is a pipeline that ends in tee (as it did in the demonstrated launcher), so a
# host that printed SAFE ATTACH REFUSED and exited 3 still gives 0 here. Whether the attach passed is read from the
# output ('SAFE ATTACH OK' or 'SAFE ATTACH REFUSED'), never from this status.
set -euo pipefail

usage() { echo "usage: $0 [scan] [boundary] [check]" >&2; exit 5; }
SCAN_ENV=""
BOUNDARY=0
CHECK=0
for word in "$@"; do
  case "$word" in
    scan) [ -z "$SCAN_ENV" ] || usage; SCAN_ENV="KC_SCAN_MODE=1" ;;  # five joints held, gripper limp and never written
    boundary) [ "$BOUNDARY" -eq 0 ] || usage; BOUNDARY=1 ;;
    check) [ "$CHECK" -eq 0 ] || usage; CHECK=1 ;;  # preflight and source check only: nothing copied, no host
    *) usage ;;
  esac
done
# With neither word this is the empty string, so the remote command line is the one this script always sent.
ENV_PREFIX="$SCAN_ENV"
[ "$BOUNDARY" -eq 0 ] || ENV_PREFIX="${ENV_PREFIX:+$ENV_PREFIX }KC_HOST_BOUNDARY=1"
# The same four steps on every start, with or without a word: preflight, copy, the source check, the host. What the
# word changes is inside step 3, which reads one file without 'boundary' and two with it, never the count of steps.
STEPS=4

# Fail closed on identities. The values below are placed inside single quotes in a command line that the REMOTE shell
# parses, and nothing here escapes them: a value with a quote, a space or a shell metacharacter is refused rather than
# sent. The value itself is never printed (a terminal log must not collect identities).
MISSING=0
need() {
  local name="$1" what="$2"
  if [ -z "${!name:-}" ]; then echo "REFUSE: $name is not set ($what). Set it in your shell from your private, uncommitted runtime configuration." >&2; MISSING=1; return 0; fi
  case "${!name}" in
    -* | *[!A-Za-z0-9_./:@+=-]*) echo "REFUSE: $name starts with '-' or holds a character outside A-Za-z0-9_./:@+=- ; it is used inside a remote command line and is not escaped." >&2; MISSING=1 ;;
  esac
}
need KC_PI_SSH "the ssh target of the robot computer"
need KC_ROBOT_ID "the id the LeKiwi was calibrated with"
need KC_SERVO_PORT "the servo bus device on the robot computer, a /dev/serial/by-id/ entry"
[ -z "${KC_PI_PYTHON:-}" ] || need KC_PI_PYTHON "the python of the lerobot install on the robot computer"
[ "$MISSING" -eq 0 ] || exit 5
case "$KC_SERVO_PORT" in /dev/*) ;; *) echo "REFUSE: KC_SERVO_PORT must be a device path under /dev/ on the robot computer." >&2; exit 5 ;; esac
case "${KC_PI_PYTHON:-/}" in /*) ;; *) echo "REFUSE: KC_PI_PYTHON must be an absolute path on the robot computer." >&2; exit 5 ;; esac

# The repository root comes from where this script is, never from a fixed checkout path.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WRAPPER="$ROOT/bin/pi/lekiwi_host_safe_attach.py"
[ -f "$WRAPPER" ] || { echo "REFUSE: $WRAPPER is missing" >&2; exit 5; }
# Step 3 runs this file HERE, out of this checkout, so it has to be present on every start whether or not it travels.
BOUNDARY_FILE="$ROOT/bin/pi/host_boundary.py"
[ -f "$BOUNDARY_FILE" ] || { echo "REFUSE: bin/pi/host_boundary.py is missing from this checkout; step 3 has nothing to read the installed lerobot source with, and with 'boundary' the wrapper on the robot computer would fail at import with KC_HOST_BOUNDARY=1" >&2; exit 5; }
command -v python3 >/dev/null 2>&1 || { echo "REFUSE: python3 is not on PATH on this laptop, and step 3 reads the installed lerobot source here; that check could NOT BE RUN, which is no verdict about the installed lerobot; NOTHING was started" >&2; exit 3; }
# Parallel arrays: what is copied, under which name on the robot computer, and how a mismatch names it. WITHOUT the word
# this is the one file it has always been. WITH it the boundary module travels as well, and only because the wrapper
# imports it there when KC_HOST_BOUNDARY=1; step 3 never needs it there, because step 3 reads on this laptop.
SRC=("$WRAPPER"); DST=("lekiwi_host_safe_attach.py"); LABEL=("wrapper")
if [ "$BOUNDARY" -eq 1 ]; then
  SRC+=("$BOUNDARY_FILE"); DST+=("host_boundary.py"); LABEL+=("boundary module")
fi
TS=$(date -u +%Y%m%dT%H%M%SZ)
REMOTE_DIR="m1-host-attach/$TS"  # relative to the remote account's home: a NEW directory per start, nothing is overwritten
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=6 "$KC_PI_SSH")
# 'check' (which bin/pi/lekiwi-deploy.sh check runs) never prints an unmasked private address: in that mode ssh's own
# complaints, which name the address when a connection drops, go through the same redaction lekiwi-deploy.sh uses. The
# other modes are exactly as before.
redact() { sed -u -E 's/\b(10(\.[0-9]{1,3}){3}|192\.168(\.[0-9]{1,3}){2}|172\.(1[6-9]|2[0-9]|3[01])(\.[0-9]{1,3}){2})\b/<private-ip>/g'; }
if [ "$CHECK" -eq 1 ]; then
  ssh_redacted() { ssh -o BatchMode=yes -o ConnectTimeout=6 "$KC_PI_SSH" "$@" 2> >(redact >&2); }
  SSH=(ssh_redacted)
fi
if [ -n "${KC_PI_PYTHON:-}" ]; then REMOTE_PYTHON="'$KC_PI_PYTHON'"; else REMOTE_PYTHON="\"\$HOME/miniconda3/bin/python3\""; fi

echo "== 1/$STEPS preflight (read-only)"
# Two hosts on one servo bus corrupt each other's packets, and a host that finds a camera owner would not be the
# camera-less host the perception side expects: refuse all three before anything is copied or started.
# shellcheck disable=SC2029  # KC_SERVO_PORT is meant to expand here; it was validated above and is single-quoted for the remote shell
"${SSH[@]}" "PORT='$KC_SERVO_PORT' bash -s" <<'PRE'
set -euo pipefail
if pgrep -af 'lekiwi_host|lerobot' >/dev/null; then echo "REFUSE: a robot/motor host process already exists"; exit 3; fi
[ -e "$PORT" ] || { echo "REFUSE: the servo bus path given in KC_SERVO_PORT is missing on the robot computer"; exit 3; }
if fuser "$(readlink -f "$PORT")" >/dev/null 2>&1; then echo "REFUSE: the servo bus already has an owner"; exit 3; fi
for n in /dev/video*; do if fuser "$n" >/dev/null 2>&1; then echo "REFUSE: $n has an owner (this host must start with every camera free)"; exit 3; fi; done
echo "preflight ok: no host, servo bus unowned, no camera owner"
PRE

if [ "$CHECK" -eq 1 ]; then
  echo "== 2/$STEPS copy: SKIPPED ('check' deploys nothing to the robot computer)"
elif [ "${#SRC[@]}" -eq 1 ]; then
  echo "== 2/$STEPS copy ONE file into a NEW directory on the robot computer and verify it"
else
  echo "== 2/$STEPS copy ${#SRC[@]} files into a NEW directory on the robot computer and verify each of them"
fi
# shellcheck disable=SC2029  # REMOTE_DIR is built here from a UTC timestamp
[ "$CHECK" -eq 1 ] || "${SSH[@]}" "mkdir -p m1-host-attach && mkdir '$REMOTE_DIR'"
for i in "${!SRC[@]}"; do
  [ "$CHECK" -eq 0 ] || break
  scp -q -o BatchMode=yes -o ConnectTimeout=6 "${SRC[$i]}" "$KC_PI_SSH:$REMOTE_DIR/${DST[$i]}"
  LOCAL_SUM="$(sha256sum "${SRC[$i]}" | cut -d' ' -f1)"
  # shellcheck disable=SC2029
  REMOTE_SUM="$("${SSH[@]}" "sha256sum '$REMOTE_DIR/${DST[$i]}'" | cut -d' ' -f1)"
  if [ -z "$LOCAL_SUM" ] || [ "$LOCAL_SUM" != "$REMOTE_SUM" ]; then echo "REFUSE: checksum mismatch between the local ${LABEL[$i]} and the copy on the robot computer"; exit 4; fi
done

if [ "$BOUNDARY" -eq 0 ]; then
  echo "== 3/$STEPS the installed lerobot source, copied here and read as text ON THIS LAPTOP (reads ONE file, the robot source the base path depends on, and the configuration beside it; writes nothing on the robot computer)"
else
  echo "== 3/$STEPS the installed lerobot source, copied here and read as text ON THIS LAPTOP (reads TWO files, the robot source and the stock host loop the boundary patches, and the configuration beside the first; writes nothing on the robot computer)"
fi
# A directory of this laptop's own, holding nothing but the copies, removed again whichever way this script ends.
READ_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kc-installed-source-XXXXXX")"
trap 'rm -rf "$READ_DIR"' EXIT

# One ssh call per file, reading it with cat: the bytes come HERE and nothing goes there. The remote cat's own stderr is
# dropped, because the message it prints for a file it cannot open carries the path, and that path names the account the
# robot computer runs as; a file that did not arrive is named below by its basename instead. A file that did not arrive
# leaves no half-written copy behind either, so nothing downstream can read one.
fetch_installed() {  # fetch_installed <directory on the robot computer> <basename>
  local status=0
  # shellcheck disable=SC2029  # the directory came from the robot computer and was checked against the same character set the identities are
  "${SSH[@]}" "cat '$1/$2'" >"$READ_DIR/$2" 2>/dev/null || status=$?
  [ "$status" -eq 0 ] || rm -f "$READ_DIR/$2"
  return "$status"
}

# One pass, run here: 0 the installed source holds what this mode reads it for, 1 it does not, 3 the pass could not be
# run at all. A pass that reaches a verdict exits 0 or 1; any other status means host_boundary.py never got as far as a
# verdict, which is a different thing to tell the operator than a source that changed, so it is never counted as drift.
run_pass() {  # run_pass <mode> <basename of the copy it reads>
  local mode="$1" copy="$READ_DIR/$2" status=0
  [ -f "$copy" ] || { echo "REFUSE: $2, the file $mode reads, did not come back from the robot computer: that pass could not be run" >&2; return 3; }
  python3 "$BOUNDARY_FILE" "$mode" "$copy" || status=$?
  case "$status" in
    0 | 1) return "$status" ;;
    *) echo "REFUSE: $mode exited $status without printing a verdict: that pass could not be run, which says nothing about the installed source" >&2; return 3 ;;
  esac
}

# The whole of step 3, and it leaves behind the status the case below reads: 4 the robot source drifted, 8 the stock
# host loop drifted, 12 both of them, 3 a pass could NOT BE RUN at all, 255 ssh could not reach the robot computer. No
# path prints, because those paths name the account the robot computer runs as.
read_installed_source() {
  local located=0 lekiwi_dir="" drift=0 could_not_run=0 status=0 name
  local wanted=(lekiwi.py config_lekiwi.py)
  # Where the installed lerobot is, asked of the interpreter the host will run with, because the copy of lerobot on this
  # laptop is not the code that will execute. The path comes back on stdout and is CAPTURED, never printed; everything
  # this call has to tell the operator goes to stderr instead, where it reaches the terminal unread by this script.
  # shellcheck disable=SC2029  # REMOTE_PYTHON is built in this script and is never printed
  lekiwi_dir="$(
    "${SSH[@]}" "PYBIN=$REMOTE_PYTHON bash -s" <<'LOCATE'
set -uo pipefail
[ -x "$PYBIN" ] || { echo "REFUSE: the interpreter the host runs with was not found on the robot computer (set KC_PI_PYTHON)" >&2; exit 3; }
INSTALLED="$("$PYBIN" -c 'import importlib.util as u; s = u.find_spec("lerobot"); print(s.submodule_search_locations[0] if s else "")' 2>/dev/null)" || INSTALLED=""
[ -n "$INSTALLED" ] || { echo "REFUSE: no lerobot is installed under the interpreter the host runs with" >&2; exit 3; }
printf '%s\n' "$INSTALLED/robots/lekiwi"
LOCATE
  )" || located=$?
  [ "$located" -eq 0 ] || return "$located"
  # That path is about to be placed inside single quotes in a command line the REMOTE shell parses, exactly as the
  # identities are, and nothing here escapes it either. It came from the robot computer rather than from a person, so
  # it is held to the same character set and refused rather than sent. The value itself is never printed.
  case "$lekiwi_dir" in
    "" | -* | *[!A-Za-z0-9_./:@+=-]*)
      echo "REFUSE: the directory the robot computer named for the installed lerobot is empty, starts with '-' or holds a character outside A-Za-z0-9_./:@+=- ; it would go unescaped into a remote command line, so no file was read and no pass could be run" >&2
      return 3 ;;
  esac
  # The robot source and the configuration beside it are what --verify-robot reads, so both are fetched on every start;
  # the stock host loop only when the boundary is going to patch it. A config_lekiwi.py that is not there is NOT handled
  # here: the verifier says so itself, in the words it has always said it in, which is what it did when it read the two
  # files where they lie.
  [ "$BOUNDARY" -eq 0 ] || wanted+=(lekiwi_host.py)
  for name in "${wanted[@]}"; do
    status=0
    fetch_installed "$lekiwi_dir" "$name" || status=$?
    [ "$status" -ne 255 ] || { echo "REFUSE: ssh stopped reaching the robot computer while the installed lerobot source was being read" >&2; return 255; }
  done
  # The robot source first: it is the one the base path depends on, and the one a checkout on that computer silently
  # reverts, so it is read on every start. Every pass that was asked for runs, whatever an earlier one found.
  status=0
  run_pass --verify-robot lekiwi.py || status=$?
  case "$status" in 1) drift=$((drift + 4)) ;; 3) could_not_run=1 ;; esac
  if [ "$BOUNDARY" -ne 0 ]; then
    status=0
    run_pass --verify-stock-host lekiwi_host.py || status=$?
    case "$status" in 1) drift=$((drift + 8)) ;; 3) could_not_run=1 ;; esac
  fi
  [ "$could_not_run" -eq 0 ] || return 3
  return "$drift"
}

VERIFY=0
read_installed_source || VERIFY=$?
rm -rf "$READ_DIR"
if [ "$VERIFY" -ne 0 ]; then
  # Which of these it is decides what the operator should go and look at, so each one is said in its own words. Only
  # the first case is a statement about the installed lerobot; the rest are statements about this check. A pass added
  # to the function above adds its bit to this list and a line of its own here, or its drift would arrive as "no verdict".
  case "$VERIFY" in
    4 | 8 | 12)
      [ "$((VERIFY & 4))" -eq 0 ] || echo "REFUSE: the lerobot robot source installed on the robot computer no longer holds what the base path depends on (the MISMATCH lines above name it); NOTHING was started" >&2
      [ "$((VERIFY & 8))" -eq 0 ] || echo "REFUSE: the stock host loop installed on the robot computer no longer holds the assumptions the boundary makes when it patches that loop (the MISMATCH lines above name them); NOTHING was started" >&2
      exit 3 ;;
    3)
      echo "REFUSE: the source check could NOT BE RUN (the REFUSE lines above say why); that is not a verdict about the installed lerobot, which was never read; NOTHING was started" >&2
      exit 3 ;;
    255)
      echo "REFUSE: ssh could not reach the robot computer for the source check, so no pass ran; that is not a verdict about the installed lerobot; NOTHING was started" >&2
      exit 255 ;;
    *)
      echo "REFUSE: the source check ended with status $VERIFY and reached no verdict of its own; that is not a verdict about the installed lerobot; NOTHING was started" >&2
      exit 3 ;;
  esac
fi

if [ "$CHECK" -eq 1 ]; then
  echo "== $STEPS/$STEPS HOST: NOT STARTED ('check' stops here). Check ok: the preflight and the source check both held."
  exit 0
fi
echo "== $STEPS/$STEPS HOST (foreground). Expect 'BASE ZERO-SPEED HOLD', 'SAFE ATTACH OK', then 'Waiting for commands...'. Ctrl-C stops it; the arm keeps holding."
echo "   Step 3 passing says the installed SOURCE is the one this repository expects. It does not say this attach will pass: the attach reads the LIVE registers and refuses when a joint's torque is off, or when its goal and present positions disagree by more than about a degree, which is the state a power cycle leaves behind. Acquire the arm first."
[ "$BOUNDARY" -eq 0 ] || echo "   BOUNDARY ON: expect 'HOST BOUNDARY ON' as well. Every command without live lease metadata is REJECTED: the console runtime attaches it to what it starts, a controller run by hand from a shell that names no lease does not."
# -t gives the host a real terminal so that Ctrl-C reaches it as SIGINT; the log pipeline ignores INT so that the last
# lines of the host are still written. The wrapper itself refuses to start without the two safety flags below.
# shellcheck disable=SC2029  # every interpolated value was validated above or is built in this script
ssh -t -o BatchMode=yes -o ConnectTimeout=6 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 "$KC_PI_SSH" \
  "cd '$REMOTE_DIR' && $ENV_PREFIX $REMOTE_PYTHON -u lekiwi_host_safe_attach.py --robot.id='$KC_ROBOT_ID' --robot.port='$KC_SERVO_PORT' --robot.max_relative_target=15.0 --robot.disable_torque_on_disconnect=false '--robot.cameras={}' --host.connection_time_s=86400 2>&1 | (trap '' INT; grep --line-buffered -vE 'Warning|warn\(' | tee host.txt)"
