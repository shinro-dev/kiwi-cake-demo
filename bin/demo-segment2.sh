#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# bin/demo-segment2.sh: segment 2, supervising your own LeKiwi host. Opt-in;
# it moves the arm. Read SAFETY.md first: this script prints its
# preconditions and refuses to continue until you type the acknowledgment
# exactly. It runs only on the tested Pi 5 configuration, only after the
# preflight accepts the board, and only from an interactive terminal. There
# is no override.
#
# Requires bin/run-child.sh and bin/safe-stop.sh, written by you from the
# templates in bin/templates/ (docs/segment-2-live.md). The host is expected
# to listen on ports 5555 and 5556 (KC_HOST_PORTS="5555 5556" to change).
#
# Exit codes: 0 all beats held; 1 a beat did not hold; 3 the board, the files
# or the acknowledgment refused the run; 5 usage or a missing tool; 130 on
# Ctrl-C (the unit is stopped first).
set -uo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

if [ $# -gt 0 ]; then
  case "$1" in
    -h | --help) sed -n '3,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) kc_fail "segment 2 takes no arguments (there is no override)" 5 ;;
  esac
fi
for t in openssl getconf ldd systemctl ss od dd; do kc_need_tool "$t" "it is needed by segment 2"; done
[ -t 0 ] && [ -r /dev/tty ] || kc_fail "segment 2 needs an interactive terminal; it will not run unattended" 3
ME="$(id -un)"
HOST_PORTS="${KC_HOST_PORTS:-5555 5556}"
for p in $HOST_PORTS; do
  case "$p" in *[!0-9]* | "") kc_fail "KC_HOST_PORTS must list port numbers, got '$p'" 5 ;; esac
  [ "$p" -ge 1 ] && [ "$p" -le 65535 ] || kc_fail "KC_HOST_PORTS port $p is out of range" 5
done

kc_facts
CLASS="$(kc_classify)"
[ "$CLASS" = "pi5-tested" ] || kc_fail "segment 2 runs only on the tested Raspberry Pi 5 configuration; this board is classified $CLASS (docs/targets.md). There is no override." 3
BIN="$(kc_binaries_dir)"
kc_check_binaries "$BIN"
RUN_CHILD="$KC_SCRIPTS_DIR/run-child.sh"
SAFE_STOP="$KC_SCRIPTS_DIR/safe-stop.sh"
for f in "$RUN_CHILD" "$SAFE_STOP"; do
  [ -f "$f" ] || kc_fail "$f is missing; copy it from bin/templates/ and fill in the placeholders (docs/segment-2-live.md)" 3
  [ -x "$f" ] || kc_fail "$f is not executable (chmod 755)" 3
  kc_template_has_placeholder "$f" && kc_fail "$f still carries a @@PLACEHOLDER@@ outside its comments; fill in every placeholder first" 3
done
case "$BIN$KC_STATE$KC_RUNTIME" in
  *[[:space:]]* | *'|'* | *'&'* | *'\'* | *'%'* | *';'*) kc_fail "the checkout, state or runtime path contains a character a systemd unit cannot carry (space, |, &, \\, %, ;); move the checkout" 3 ;;
esac
systemctl --user show-environment >/dev/null 2>&1 || kc_fail "systemctl --user is not available in this session" 3

ports_listening() {
  local p
  for p in $HOST_PORTS; do ss -tln 2>/dev/null | grep -qE "[:.]$p " || return 1; done
  return 0
}
if ports_listening; then
  kc_fail "something already listens on $HOST_PORTS; stop your existing host first (segment 2 must be the only host)" 3
fi

# --- the acknowledgment -----------------------------------------------------------------
ask_ack() {
  local a
  echo
  echo "SEGMENT 2 MOVES THE ARM. Read SAFETY.md. Confirm every precondition now:"
  echo "  - The robot is on a stand."
  echo "  - The wheels are off the ground and cannot touch anything if they turn."
  echo "  - The arm is parked low and physically supported."
  echo "  - The leader arm is connected to the laptop, if you intend to teleoperate."
  echo "  - One hand stays near the power switch for the whole run."
  echo "  - Your own eyes stay on the arm through every torque transition."
  echo
  echo "The LeRobot host enables torque on every connect, including after every recovery this demo performs."
  echo "Cake does not disarm anything."
  echo
  echo "Type exactly:  $KC_SEGMENT2_ACK"
  printf '> '
  IFS= read -r a </dev/tty || return 1
  [ "$a" = "$KC_SEGMENT2_ACK" ]
}
ask_ack || kc_fail "acknowledgment did not match; nothing was started" 3

kc_new_run_dir
exec > >(tee -a "$KC_RUN/transcript.txt") 2>&1
RUN_ID="$(basename "$KC_RUN")"
kc_say "segment 2, run $RUN_ID, binaries $BIN"
echo "acknowledged: $KC_SEGMENT2_ACK"

UNIT="kiwi-cake-demo.service"
UNIT_PATH="$HOME/.config/systemd/user/$UNIT"
SEG_DIR="$KC_STATE/segment2"
CONF="$SEG_DIR/resident.conf"
SOCK="$KC_RUNTIME/admin.sock"
RUN_TAG="kiwi-cake-child-$RUN_ID"
BEAT=0
beat_ok() { BEAT=$((BEAT + 1)); echo "KIWI-CAKE beat $BEAT $1: ok"; }
beat_fail() { echo "KIWI-CAKE beat $((BEAT + 1)) $1: FAILED" >&2; kc_fail "$2" "${3:-1}"; }
cleanup() { systemctl --user stop "$UNIT" 2>/dev/null; }
on_interrupt() { echo; echo "interrupted: stopping $UNIT"; cleanup; exit 130; }
trap on_interrupt INT
trap 'cleanup; exit 143' TERM HUP
trap cleanup EXIT
press_enter() {
  echo
  echo "$1"
  echo "Hand near power. Eyes on the arm. Press Enter to continue, or Ctrl-C to stop everything."
  IFS= read -r _ </dev/tty || on_interrupt
}
main_pid() { systemctl --user show --property MainPID --value "$UNIT" 2>/dev/null; }
host_pid() {
  # The host is the child the resident spawned: its parent is the unit's MainPID.
  # The wrapper execs the host without the run tag, so the tag cannot be used here.
  local m
  m="$(main_pid)"
  [ -n "$m" ] && [ "$m" != "0" ] || return 1
  kc_children_of "$m"
}
wait_host() {
  local _ h
  for _ in $(seq 1 "$1"); do
    h="$(host_pid)"
    [ -n "$h" ] && { printf '%s' "${h%% *}"; return 0; }
    sleep 0.05
  done
  return 1
}
wait_ready() {
  local _
  for _ in $(seq 1 600); do
    [ -S "$SOCK" ] && kc_query "$BIN/admin-probe" "$SOCK" get-status | grep -qx 'status ok' && return 0
    systemctl --user is-active --quiet "$UNIT" || return 1
    sleep 0.05
  done
  return 1
}
wait_ports() {
  local _
  for _ in $(seq 1 240); do ports_listening && return 0; sleep 0.5; done
  return 1
}
flight_max_seq() { kc_query "$BIN/admin-probe" "$SOCK" read-flight | sed -nE 's/.* sequence=([0-9]+) .*/\1/p' | sort -n | tail -n 1; }

# --- key, build, preflight -------------------------------------------------------------------
KEY="$KC_STATE/keys/demo.keypair"
[ -f "$KEY" ] || kc_generate_keypair "$KEY"
PUB="$(kc_keypair_public "$KEY")"
"$BIN/demo-plan" build --out-dir "$KC_RUN/bundle" --signing-key-file "$KEY" --run-tag "$RUN_TAG" \
  --child-command "$RUN_CHILD" --child-stop-signal SIGINT \
  --safe-stop-command "$SAFE_STOP" --safe-stop-command "$KC_RUN/safe-stop.log" \
  >"$KC_RUN/manifest.txt" 2>"$KC_RUN/demo-plan.err" ||
  { cat "$KC_RUN/demo-plan.err" >&2; beat_fail build "demo-plan build did not produce its artifacts"; }
M="$KC_RUN/manifest.txt"
STORE="$(kc_manifest_value "$M" store_root)"
PLAN="$(kc_manifest_value "$M" plan_artifact)"
CAPSULE_DIGEST="$(kc_manifest_value "$M" capsule_digest)"
CHILD="$(kc_manifest_value "$M" child_command)"
[ "$CHILD" = "$RUN_CHILD" ] || beat_fail build "the Plan names child $CHILD, not $RUN_CHILD"
echo "real bundle built: child $CHILD, stop signal SIGINT, safe-stop $SAFE_STOP"
OBJECT="$STORE/objects/$CAPSULE_DIGEST"
"$BIN/demo-preflight" --capsule "$OBJECT" --trusted-key "$PUB" --store-scratch "$KC_RUN/preflight" \
  --binary "$BIN/cake-resident" --binary "$BIN/admin-probe" --binary "$BIN/demo-plan" --binary "$BIN/demo-preflight" \
  >"$KC_RUN/preflight.out" 2>"$KC_RUN/preflight.err"
RC=$?
sed 's/^/  /' "$KC_RUN/preflight.out"; sed 's/^/  /' "$KC_RUN/preflight.err"
VERDICT="$(kc_classify_preflight "$KC_RUN/preflight.out" "$KC_RUN/preflight.err" "$RC")"
case "$VERDICT" in
  pass | five-green-observer-refused) echo "preflight accepted this board ($VERDICT)" ;;
  refused:*)
    check="${VERDICT#refused:}"; check="${check%%:*}"; msg="${VERDICT#refused:*:}"
    kc_explain_refusal "$check" "$msg"
    beat_fail preflight "demo-preflight refused this board at check $check; nothing was started" 3 ;;
  *) beat_fail preflight "demo-preflight output could not be parsed" ;;
esac
beat_ok preflight

# --- configuration and the user unit --------------------------------------------------------------
{ mkdir -p "$SEG_DIR" && chmod 700 "$SEG_DIR"; } || beat_fail unit "cannot create $SEG_DIR"
kc_write_resident_conf "$CONF" "$STORE" "$SOCK" "$PUB" "$PLAN"
mkdir -p "$(dirname "$UNIT_PATH")" || beat_fail unit "cannot create $(dirname "$UNIT_PATH")"
sed -e "s|@@CAKE_RESIDENT@@|$BIN/cake-resident|" -e "s|@@RESIDENT_CONF@@|$CONF|" \
  "$KC_SCRIPTS_DIR/templates/kiwi-cake-demo.service.in" >"$UNIT_PATH" || beat_fail unit "could not write $UNIT_PATH"
grep -qxF "ExecStart=$BIN/cake-resident --config $CONF" "$UNIT_PATH" || beat_fail unit "the unit's ExecStart did not render as expected"
systemctl --user daemon-reload || beat_fail unit "systemctl --user daemon-reload failed"
if systemctl --user is-active --quiet "$UNIT"; then systemctl --user stop "$UNIT"; fi
echo "installed $UNIT_PATH (Restart=on-abnormal, RestartSec=2)"
if command -v loginctl >/dev/null 2>&1 && [ "$(loginctl show-user "$ME" -p Linger --value 2>/dev/null)" != "yes" ]; then
  echo "note: linger is not enabled for $ME; the unit stops when your last session ends (loginctl enable-linger $ME)"
fi
beat_ok unit

# --- beat 1 -------------------------------------------------------------------------------------------
press_enter "BEAT 1: start the resident under $UNIT. Your host will connect and ENABLE TORQUE."
rm -f -- "$SOCK"
systemctl --user start "$UNIT" || beat_fail start "systemctl --user start $UNIT failed"
wait_ready || { systemctl --user status --no-pager "$UNIT" >&2; beat_fail start "the resident did not reach a state answering get-status"; }
echo "resident MainPID $(main_pid): plan active, admin socket answering"
HOST_1="$(wait_host 400)" || beat_fail start "the resident spawned no host process"
wait_ports || beat_fail start "the host is not listening on $HOST_PORTS (the arm may be under torque; stopping)"
echo "host pid $HOST_1 listening on $HOST_PORTS"
STATUS_A="$(kc_query "$BIN/admin-probe" "$SOCK" get-status)"
SESSION_A="$(kc_field "$STATUS_A" session_uuid)"
"$KC_SCRIPTS_DIR/telemetry.sh" --probe "$BIN/admin-probe" --socket "$SOCK" --registry "$KC_ROOT/docs/event-codes.md" --rounds 1 | tee "$KC_RUN/poll-1.txt"
beat_ok start

# --- beat 2 ---------------------------------------------------------------------------------------------
echo
echo "BEAT 2: from the laptop, run bin/laptop/teleop.py against this host (docs/reproduce-end-to-end.md, the laptop side)."
echo "This beat is your observation; the runner measures nothing here."
echo "Type CONFIRMED when the follower mirrors the leader, or SKIP to go on without teleoperating."
printf '> '
IFS= read -r TELEOP </dev/tty || on_interrupt
case "$TELEOP" in
  CONFIRMED) echo "teleoperation through the supervised host: operator-confirmed" ;;
  SKIP) echo "teleoperation: skipped by the operator" ;;
  *) beat_fail teleop "expected CONFIRMED or SKIP" ;;
esac
beat_ok teleop

# --- beat 3 ------------------------------------------------------------------------------------------------
press_enter "BEAT 3: SIGTERM to the host only. Your safe-stop runs, then a fresh host connects and ENABLES TORQUE again."
RES_PID_3="$(main_pid)"
CHILD_3="$(wait_host 100)" || beat_fail child-restart "no host process to signal"
BASE_SEQ="$(flight_max_seq)"; BASE_SEQ="${BASE_SEQ:-0}"
kill -TERM "$CHILD_3" || beat_fail child-restart "cannot send SIGTERM to the host"
echo "sent SIGTERM to host pid $CHILD_3 (flight baseline: sequence $BASE_SEQ)"
kc_wait_gone "$CHILD_3" 600 || beat_fail child-restart "the signalled host $CHILD_3 is still in the process table"
CHILD_3B="$(wait_host 600)" || beat_fail child-restart "no host was running after the restart"
wait_ports || beat_fail child-restart "the fresh host is not listening on $HOST_PORTS"
EXITED=""; SAFE=""; STARTED=""
for _ in $(seq 1 40); do
  FLIGHT="$(kc_query "$BIN/admin-probe" "$SOCK" read-flight)"
  EXITED="$(printf '%s\n' "$FLIGHT" | sed -nE 's/^record arg0=([0-9]+) arg1=[0-9]+ .* event=0x05fffffb .* sequence=([0-9]+) .*$/\1 \2/p' |
    while read -r st seq; do [ "$seq" -gt "$BASE_SEQ" ] && [ $((st & 127)) -eq 15 ] && echo "$seq"; done | head -n 1)"
  SAFE="$(printf '%s\n' "$FLIGHT" | sed -nE 's/^record .* event=0x05fffff6 .* sequence=([0-9]+) .*$/\1/p' |
    while read -r seq; do [ "$seq" -gt "$BASE_SEQ" ] && echo "$seq"; done | head -n 1)"
  STARTED="$(printf '%s\n' "$FLIGHT" | sed -nE 's/^record arg0=([1-9][0-9]*) .* event=0x05fffffc .* sequence=([0-9]+) .*$/\1 \2/p' |
    while read -r ord seq; do [ "$seq" -gt "$BASE_SEQ" ] && echo "$ord"; done | head -n 1)"
  [ -n "$EXITED" ] && [ -n "$SAFE" ] && [ -n "$STARTED" ] && break
  sleep 0.1
done
[ -n "$EXITED" ] || beat_fail child-restart "no EVT_SUPERVISOR_CHILD_EXITED carrying signal 15 after the baseline"
[ -n "$SAFE" ] || beat_fail child-restart "no EVT_SUPERVISOR_SAFE_STOP_RAN after the baseline"
[ -n "$STARTED" ] || beat_fail child-restart "no EVT_SUPERVISOR_CHILD_STARTED with a restart ordinal above 0 after the baseline"
[ "$(main_pid)" = "$RES_PID_3" ] || beat_fail child-restart "the resident's pid changed during a child-only restart"
SESSION_3="$(kc_field "$(kc_query "$BIN/admin-probe" "$SOCK" get-status)" session_uuid)"
[ "$SESSION_3" = "$SESSION_A" ] || beat_fail child-restart "the session identity changed during a child-only restart"
echo "host exited with signal 15 (sequence $EXITED), safe-stop ran (sequence $SAFE), fresh host pid $CHILD_3B with restart ordinal $STARTED; resident pid $RES_PID_3 and session unchanged"
beat_ok child-restart

# --- beat 4 ---------------------------------------------------------------------------------------------------
echo
echo "BEAT 4 is the crash-recovery beat. Re-confirm the preconditions."
ask_ack || beat_fail crash-recovery "acknowledgment did not match; stopping" 3
press_enter "BEAT 4: SIGKILL to the resident itself. The host dies with it; systemd relaunches the resident; a fresh host connects and ENABLES TORQUE again. Cake will NOT disarm the motors."
RES_PID_4="$(main_pid)"
[ -n "$RES_PID_4" ] && [ "$RES_PID_4" != "0" ] || beat_fail crash-recovery "systemd reports no MainPID"
CHILD_4="$(wait_host 100)" || beat_fail crash-recovery "no host process before the kill"
STATUS_B="$(kc_query "$BIN/admin-probe" "$SOCK" get-status)"
kill -9 "$RES_PID_4" || beat_fail crash-recovery "cannot send SIGKILL to the resident"
echo "sent SIGKILL to resident pid $RES_PID_4 (host pid $CHILD_4)"
kc_wait_gone "$CHILD_4" 100 || beat_fail crash-recovery "host $CHILD_4 survived the resident's death (orphan)"
echo "host $CHILD_4 died with the resident (no orphan)"
RELAUNCHED=0
for _ in $(seq 1 800); do
  NEW_PID="$(main_pid)"
  if [ -n "$NEW_PID" ] && [ "$NEW_PID" != "0" ] && [ "$NEW_PID" != "$RES_PID_4" ] && [ -S "$SOCK" ] &&
    kc_query "$BIN/admin-probe" "$SOCK" get-status | grep -qx 'status ok'; then RELAUNCHED=1; break; fi
  sleep 0.1
done
[ "$RELAUNCHED" -eq 1 ] || { systemctl --user status --no-pager "$UNIT" >&2; beat_fail crash-recovery "$UNIT did not relaunch the resident"; }
STATUS_C="$(kc_query "$BIN/admin-probe" "$SOCK" get-status)"
SESSION_C="$(kc_field "$STATUS_C" session_uuid)"
[ "$SESSION_C" != "$(kc_field "$STATUS_B" session_uuid)" ] || beat_fail crash-recovery "the relaunched resident reused the session identity"
for k in plan_digest config_identity build_identity target_profile_digest; do
  [ "$(kc_field "$STATUS_B" "$k")" = "$(kc_field "$STATUS_C" "$k")" ] || beat_fail crash-recovery "$k changed across the relaunch"
done
CHILD_4B="$(wait_host 400)" || beat_fail crash-recovery "the relaunched resident spawned no host"
wait_ports || beat_fail crash-recovery "the relaunched resident's host is not listening on $HOST_PORTS"
echo "BEFORE/AFTER:"
echo "  resident pid   $RES_PID_4 -> $NEW_PID (new)"
echo "  host pid       $CHILD_4 -> $CHILD_4B (new)"
echo "  session_uuid   $(kc_field "$STATUS_B" session_uuid) -> $SESSION_C (fresh)"
echo "  plan_digest, config_identity, build_identity, target_profile_digest: IDENTICAL"
echo "  host           listening again on $HOST_PORTS, TORQUE ENABLED BY THE HOST"
echo "Cake-level recovery held; actuator-safe recovery is not something this demo provides (LIMITATIONS.md)."
beat_ok crash-recovery

# --- stop ------------------------------------------------------------------------------------------------------
press_enter "STOP: the resident's own clean shutdown, which quiesces the host (SIGINT to the host, then a bounded deadline)."
systemctl --user stop "$UNIT" || beat_fail stop "systemctl --user stop failed"
kc_wait_gone "$CHILD_4B" 400 || beat_fail stop "the host survived the clean stop"
[ ! -S "$SOCK" ] || beat_fail stop "the admin socket is still present"
ports_listening && beat_fail stop "a listener is still on $HOST_PORTS"
echo "stopped: no resident, no host, no socket, no listener"
echo "whether the motors are now unpowered is your host's own disconnect behaviour, not Cake's; check the robot"
beat_ok stop
echo "run directory: $KC_RUN"
echo "KIWI-CAKE SEGMENT 2: DONE"
exit 0
