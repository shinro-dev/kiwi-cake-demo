#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# bin/demo-segment1.sh: segment 1, the stub-child integrity, telemetry and
# recovery demo. Torque-free: the supervised child is a line-printing stub
# that demo-plan writes out, and nothing here names or opens a device.
#
# Thirteen steps, each printing `KIWI-CAKE step N/13 <name>: ok` and stopping
# the run at the first one that does not hold. The transcript is kept under
# state/runs/<id>/transcript.txt. docs/segment-1-stub.md explains each step.
#
# Options:
#   --unsupported-target  skip demo-preflight on a board that is not the tested
#                         Pi 5 and run the doctor's own checks instead
#                         (docs/targets.md); rejected on the tested board
#   --keep                keep the built bundle and store under the run directory
#   --pause               wait for Enter on standard input between steps, to narrate
#                         the run; Ctrl-C at a pause stops everything and exits 130
#
# Exit codes: 0 PASS; 1 a step did not hold; 3 the board is refused; 5 usage.
set -uo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

UNSUPPORTED=0
KEEP=0
PAUSE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --unsupported-target) UNSUPPORTED=1; shift ;;
    --keep) KEEP=1; shift ;;
    --pause) PAUSE=1; shift ;;
    -h | --help) sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) kc_fail "unrecognised argument '$1'" 5 ;;
  esac
done
for t in openssl getconf ldd od dd sha256sum timeout; do kc_need_tool "$t" "it is needed by segment 1"; done

BIN="$(kc_binaries_dir)"
kc_check_binaries "$BIN"
kc_new_run_dir
exec > >(tee -a "$KC_RUN/transcript.txt") 2>&1
RUN_ID="$(basename "$KC_RUN")"
kc_say "segment 1, run $RUN_ID, binaries $BIN"
kc_say "torque-free: the child is a line-printing stub; nothing in this segment can move anything"

STEP=0
step_ok() {
  STEP=$((STEP + 1)); echo "KIWI-CAKE step $STEP/13 $1: ok"
  if [ "$PAUSE" -eq 1 ] && [ "$STEP" -lt 13 ]; then
    echo "PAUSE: press Enter for step $((STEP + 1))/13"
    IFS= read -r _ || step_fail pause "--pause needs a readable standard input (it was closed)" 5
  fi
}
step_fail() { echo "KIWI-CAKE step $((STEP + 1))/13 $1: FAILED" >&2; kc_fail "$2" "${3:-1}"; }

RES_PID=""
RUN_TAG="kiwi-cake-child-$RUN_ID"
SOCK="$KC_RUNTIME/admin-$RUN_ID.sock"
cleanup() {
  local pid
  if [ -n "$RES_PID" ] && kill -0 "$RES_PID" 2>/dev/null; then
    kill -TERM "$RES_PID" 2>/dev/null
    for _ in $(seq 1 100); do kill -0 "$RES_PID" 2>/dev/null || break; sleep 0.05; done
    kill -9 "$RES_PID" 2>/dev/null
    wait "$RES_PID" 2>/dev/null
  fi
  for pid in $(kc_pids_carrying "$RUN_TAG"); do kill -9 "$pid" 2>/dev/null; done
  rm -f -- "$SOCK"
  if [ "$KEEP" -eq 0 ]; then rm -rf -- "$KC_RUN/bundle" "$KC_RUN/preflight" "$KC_RUN/verify-"*; fi
}
on_interrupt() { trap - EXIT INT TERM HUP; echo; echo "interrupted: stopping the resident and the child"; cleanup; exit 130; }
on_term() { trap - EXIT INT TERM HUP; cleanup; exit 143; }
trap cleanup EXIT
trap on_interrupt INT
trap on_term TERM HUP

# --- 1. doctor ---------------------------------------------------------------------
kc_facts
CLASS="$(kc_classify)"
echo "board: $KC_MODEL ($KC_ARCH), page size $KC_PAGE_SIZE, glibc $KC_GLIBC, memfd_noexec $KC_MEMFD, classification $CLASS"
for b in cake-resident admin-probe demo-plan demo-preflight; do
  kc_probe_binary "$BIN/$b" || step_fail doctor "$b did not start under this board's dynamic loader" 3
done
echo "shipped binaries: all four start under this board's dynamic loader"
if [ "$UNSUPPORTED" -eq 1 ] && [ "$CLASS" = "pi5-tested" ]; then
  step_fail doctor "--unsupported-target is not needed on the tested board; run without it" 5
fi
if [ "$CLASS" != "pi5-tested" ] && [ "$UNSUPPORTED" -eq 0 ]; then
  echo "This board is not the tested Raspberry Pi 5 configuration (classification $CLASS)."
  echo "demo-preflight will refuse it by construction; run bin/doctor.sh for the explanation,"
  echo "and read docs/targets.md before deciding whether to run with --unsupported-target."
  step_fail doctor "board refused: $CLASS" 3
fi
step_ok doctor

# --- 2. keys -------------------------------------------------------------------------
KEY="$KC_STATE/keys/demo.keypair"
if [ ! -f "$KEY" ]; then
  kc_generate_keypair "$KEY"
  echo "generated a new Ed25519 demo key at $KEY (mode 600)"
else
  echo "using the existing demo key at $KEY"
fi
CONTROL_KEY="$KC_RUN/control.keypair"
kc_generate_keypair "$CONTROL_KEY"
PUB="$(kc_keypair_public "$KEY")"
CONTROL_PUB="$(kc_keypair_public "$CONTROL_KEY")"
[ "${#PUB}" -eq 64 ] && [ "${#CONTROL_PUB}" -eq 64 ] || step_fail keys "could not read the public halves"
echo "trusted key (yours): $PUB"
echo "control key (never trusted): $CONTROL_PUB"
step_ok keys

# --- 3. build ------------------------------------------------------------------------
SAFE_STOP="$KC_RUN/safe-stop-stub.sh"
printf '#!/bin/sh\nprintf "safe-stop ran\\n" >>"$1"\n' >"$SAFE_STOP"
chmod 755 "$SAFE_STOP"
"$BIN/demo-plan" build --out-dir "$KC_RUN/bundle" --signing-key-file "$KEY" --run-tag "$RUN_TAG" \
  --child-stop-signal SIGINT --safe-stop-command "$SAFE_STOP" --safe-stop-command "$KC_RUN/safe-stop.log" \
  >"$KC_RUN/manifest.txt" 2>"$KC_RUN/demo-plan.err" ||
  { cat "$KC_RUN/demo-plan.err" >&2; step_fail build "demo-plan build did not produce its artifacts"; }
M="$KC_RUN/manifest.txt"
STORE="$(kc_manifest_value "$M" store_root)"
PLAN="$(kc_manifest_value "$M" plan_artifact)"
CAPSULE_DIGEST="$(kc_manifest_value "$M" capsule_digest)"
CAPSULE="$(kc_manifest_value "$M" capsule_path)"
UNSIGNED="$(kc_manifest_value "$M" unsigned_capsule_path)"
MANIFEST_PUB="$(kc_manifest_value "$M" public_key)"
CHILD="$(kc_manifest_value "$M" child_command)"
SLOT="$(kc_manifest_value "$M" slot_name)"
[ -d "$STORE" ] && [ -f "$PLAN" ] && [ -f "$CAPSULE" ] && [ -f "$UNSIGNED" ] || step_fail build "the manifest names files that do not exist"
[ "$MANIFEST_PUB" = "$PUB" ] || step_fail build "the manifest's public key is not the key file's"
[ "$CHILD" = "$KC_RUN/bundle/child/stub-child.sh" ] && [ -x "$CHILD" ] || step_fail build "the child command is not the stub demo-plan writes out ($CHILD)"
[ "$SLOT" = "supervisor.lekiwi" ] || step_fail build "unexpected slot name $SLOT"
echo "store $STORE"
echo "plan $PLAN"
echo "capsule content identity $CAPSULE_DIGEST"
echo "child $CHILD (the stub)"
step_ok build

# --- 4. admission verdicts --------------------------------------------------------------
verdict_of() {
  # verdict_of CAPSULE TRUSTED LABEL: prints "<verdict> <reason>"
  "$BIN/demo-plan" verify --capsule "$1" --policy require --trusted "$2" --scratch "$KC_RUN/verify-$3" >"$KC_RUN/verify-$3.txt" 2>&1
  printf '%s %s\n' "$(kc_manifest_value "$KC_RUN/verify-$3.txt" verdict)" "$(kc_manifest_value "$KC_RUN/verify-$3.txt" reason)"
}
V="$(verdict_of "$CAPSULE" "$PUB" signed)"
echo "signed capsule under require, your key trusted: $V"
[ "${V%% *}" = "admitted" ] || step_fail verify "the signed capsule was not admitted"
V="$(verdict_of "$UNSIGNED" "$PUB" unsigned)"
echo "unsigned twin under require: $V"
[ "$V" = "refused signature_missing" ] || step_fail verify "the unsigned twin was not refused as signature_missing"
V="$(verdict_of "$CAPSULE" "$CONTROL_PUB" untrusted)"
echo "signed capsule with only the control key trusted: $V"
[ "$V" = "refused signature_key_id_not_trusted" ] || step_fail verify "the capsule was not refused as signature_key_id_not_trusted"
step_ok verify

# --- 5. preflight ---------------------------------------------------------------------------
OBJECT="$STORE/objects/$CAPSULE_DIGEST"
[ -f "$OBJECT" ] || step_fail preflight "the store holds no object under the capsule's content identity"
if [ "$UNSUPPORTED" -eq 1 ]; then
  echo "demo-preflight SKIPPED: --unsupported-target on a $CLASS board; it would refuse by construction (docs/targets.md)"
  { [ "$KC_GLIBC" != "unknown" ] && kc_version_ge "$KC_GLIBC" "2.34"; } || step_fail preflight "glibc $KC_GLIBC is below the binaries' floor 2.34" 3
  [ "$KC_MEMFD" != "2" ] || step_fail preflight "vm.memfd_noexec_scope is 2, which the sealed loader cannot work with" 3
  echo "doctor checks instead: glibc $KC_GLIBC at or above 2.34, memfd_noexec $KC_MEMFD, binaries start; the resident's own admission still decides at start"
else
  "$BIN/demo-preflight" --capsule "$OBJECT" --trusted-key "$PUB" --store-scratch "$KC_RUN/preflight" \
    --binary "$BIN/cake-resident" --binary "$BIN/admin-probe" --binary "$BIN/demo-plan" --binary "$BIN/demo-preflight" \
    >"$KC_RUN/preflight.out" 2>"$KC_RUN/preflight.err"
  RC=$?
  sed 's/^/  /' "$KC_RUN/preflight.out"
  sed 's/^/  /' "$KC_RUN/preflight.err"
  VERDICT="$(kc_classify_preflight "$KC_RUN/preflight.out" "$KC_RUN/preflight.err" "$RC")"
  case "$VERDICT" in
    pass) echo "preflight: PASS on every check" ;;
    five-green-observer-refused) echo "preflight: five checks green (page-size, glibc, memfd-noexec, admission, load-probe); observer refused as documented (LIMITATIONS.md)" ;;
    refused:*)
      check="${VERDICT#refused:}"; check="${check%%:*}"; msg="${VERDICT#refused:*:}"
      kc_explain_refusal "$check" "$msg"
      step_fail preflight "demo-preflight refused this board at check $check; nothing was started" 3 ;;
    *) kc_explain_unparsed "$VERDICT"; step_fail preflight "demo-preflight output could not be parsed (${VERDICT#unparsed:}; see $KC_RUN/preflight.out and .err)" ;;
  esac
fi
step_ok preflight

# --- 6. configuration ------------------------------------------------------------------------
CONF="$KC_RUN/resident.conf"
kc_write_resident_conf "$CONF" "$STORE" "$SOCK" "$PUB" "$PLAN"
echo "resident.conf: signature_policy require, trusted_keys $PUB, socket $SOCK"
step_ok configuration

# --- 7. tamper --------------------------------------------------------------------------------
cp -- "$OBJECT" "$KC_RUN/capsule.object.orig" || step_fail tamper "cannot back up the store object"
SIZE="$(stat -c %s "$OBJECT")"
OFFSET=$((SIZE / 2))
ORIG_BYTE="$(dd if="$OBJECT" bs=1 skip="$OFFSET" count=1 status=none | od -An -tu1 | tr -d ' \n')"
if [ "$ORIG_BYTE" -eq 0 ]; then NEW_BYTE=255; else NEW_BYTE=0; fi
# shellcheck disable=SC2059
printf "$(printf '\\%03o' "$NEW_BYTE")" | dd of="$OBJECT" bs=1 seek="$OFFSET" conv=notrunc count=1 status=none
cmp -s -- "$OBJECT" "$KC_RUN/capsule.object.orig" && step_fail tamper "the byte flip changed nothing"
echo "flipped one byte of the stored object at offset $OFFSET; starting the resident against it"
rm -f -- "$SOCK"
timeout 60 "$BIN/cake-resident" --config "$CONF" >"$KC_RUN/tampered.out" 2>"$KC_RUN/tampered.err"
TRC=$?
[ "$TRC" -ne 0 ] || step_fail tamper "the start against the tampered object exited 0"
grep -q '^cake-resident: plan active ' "$KC_RUN/tampered.out" && step_fail tamper "the tampered start reached plan active"
grep -Fq 'store-object-mismatch' "$KC_RUN/tampered.err" && grep -Fq "$CAPSULE_DIGEST" "$KC_RUN/tampered.err" ||
  { cat "$KC_RUN/tampered.err" >&2; step_fail tamper "the refusal did not name store-object-mismatch and the content identity"; }
echo "refused (exit $TRC): $(grep -F 'store-object-mismatch' "$KC_RUN/tampered.err" | head -n 1)"
cp -- "$KC_RUN/capsule.object.orig" "$OBJECT" || step_fail tamper "cannot restore the store object"
rm -f -- "$STORE/quarantine/"* 2>/dev/null
echo "restored the original byte"
step_ok tamper

# --- 8. intact start --------------------------------------------------------------------------
start_resident() {
  # start_resident LABEL: background the resident, wait up to 40 s for ready,
  # require plan active, then up to 10 s for the socket to answer. Both waits
  # are wall-clock deadlines, so a probe that hangs cannot stretch them past
  # the budget plus one probe timeout.
  local label="$1" t0
  rm -f -- "$SOCK"
  "$BIN/cake-resident" --config "$CONF" >"$KC_RUN/$label.out" 2>"$KC_RUN/$label.err" &
  RES_PID=$!
  t0=$SECONDS
  until grep -q '^cake-resident: ready pid=' "$KC_RUN/$label.out"; do
    kill -0 "$RES_PID" 2>/dev/null || { cat "$KC_RUN/$label.out" "$KC_RUN/$label.err" >&2; return 1; }
    kc_deadline_passed "$t0" 40 && return 1
    sleep 0.05
  done
  grep -q '^cake-resident: plan active .*resources=1 slots=1' "$KC_RUN/$label.out" || return 1
  t0=$SECONDS
  until [ -S "$SOCK" ] && kc_query "$BIN/admin-probe" "$SOCK" get-status | grep -qx 'status ok'; do
    kc_deadline_passed "$t0" 10 && { [ -z "$KC_QUERY_ERR" ] || printf '%s\n' "$KC_QUERY_ERR" | sed 's/^/kiwi-cake: admin-probe: /' >&2; return 1; }
    sleep 0.05
  done
  return 0
}
start_resident first || step_fail start "the resident did not reach plan active against the intact store"
sed 's/^/  /' "$KC_RUN/first.out"
step_ok start

# --- 9. telemetry --------------------------------------------------------------------------------
"$KC_SCRIPTS_DIR/telemetry.sh" --probe "$BIN/admin-probe" --socket "$SOCK" --registry "$KC_ROOT/docs/event-codes.md" --rounds 1 |
  tee "$KC_RUN/poll-before.txt"
grep -q '^poll queries answered 3 of 3$' "$KC_RUN/poll-before.txt" || step_fail telemetry "the poll did not answer all three queries"
grep -q '^slot name=supervisor.lekiwi state=ACTIVE ' "$KC_RUN/poll-before.txt" || step_fail telemetry "the supervisor slot is not listed ACTIVE"
STATUS_A="$(kc_query "$BIN/admin-probe" "$SOCK" get-status)"
SESSION_A="$(kc_field "$STATUS_A" session_uuid)"
PLAN_A="$(kc_field "$STATUS_A" plan_digest)"
CONFIG_A="$(kc_field "$STATUS_A" config_identity)"
BUILD_A="$(kc_field "$STATUS_A" build_identity)"
PROFILE_A="$(kc_field "$STATUS_A" target_profile_digest)"
[ "${#SESSION_A}" -eq 32 ] || step_fail telemetry "get-status reported no session identity"
step_ok telemetry

# --- 10. child kill -------------------------------------------------------------------------------
kc_wait_for_tag "$RUN_TAG" 400 || step_fail child-kill "no child carrying this run's tag is in the process table"
CHILD_BEFORE="$(kc_pids_carrying "$RUN_TAG")"
CHILD_PID="${CHILD_BEFORE%% *}"
kill -9 "$CHILD_PID" || step_fail child-kill "cannot send SIGKILL to the child"
echo "sent SIGKILL to the stub child, pid $CHILD_PID"
kc_wait_gone "$CHILD_PID" 400 || step_fail child-kill "the killed child $CHILD_PID is still in the process table"
kc_wait_for_tag "$RUN_TAG" 400 || step_fail child-kill "no child was running after the kill"
case " $(kc_pids_carrying "$RUN_TAG") " in *" $CHILD_PID "*) step_fail child-kill "the killed child is still in the process table" ;; esac
EXITED=""; STARTED=""
for _ in $(seq 1 40); do
  FLIGHT="$(kc_query "$BIN/admin-probe" "$SOCK" read-flight)"
  EXITED="$(printf '%s\n' "$FLIGHT" | sed -nE 's/^record arg0=([0-9]+) arg1=([0-9]+) .* event=0x05fffffb .* sequence=([0-9]+) .*$/\1 \2 \3/p' |
    while read -r st ord seq; do [ $((st & 127)) -eq 9 ] && echo "status=$st ordinal=$ord sequence=$seq"; done | head -n 1)"
  STARTED="$(printf '%s\n' "$FLIGHT" | sed -nE 's/^record arg0=([0-9]+) arg1=([0-9]+) .* event=0x05fffffc .* sequence=([0-9]+) .*$/\1 \2 \3/p' |
    while read -r ord cnt seq; do [ "$ord" -ge 1 ] && echo "ordinal=$ord spawn_count=$cnt sequence=$seq"; done | head -n 1)"
  [ -n "$EXITED" ] && [ -n "$STARTED" ] && break
  sleep 0.1
done
[ -n "$EXITED" ] && [ -n "$STARTED" ] || step_fail child-kill "the flight ring shows no exit by signal 9 followed by a restart"
echo "flight: EVT_SUPERVISOR_CHILD_EXITED $EXITED (signal 9)"
echo "flight: EVT_SUPERVISOR_CHILD_STARTED $STARTED"
echo "new child pid(s): $(kc_pids_carrying "$RUN_TAG")"
CHILD_AFTER="$(kc_pids_carrying "$RUN_TAG")"
step_ok child-kill

# --- 11. resident kill and relaunch --------------------------------------------------------------
kill -9 "$RES_PID" || step_fail resident-kill "cannot send SIGKILL to the resident"
wait "$RES_PID" 2>/dev/null
RSTATUS=$?
echo "sent SIGKILL to the resident, pid $RES_PID; it ended with status $RSTATUS"
RES_PID=""
[ "$RSTATUS" -eq 137 ] || step_fail resident-kill "the resident ended with status $RSTATUS, want 137"
kc_settle_absent "$RUN_TAG" 400 || step_fail resident-kill "$(kc_pids_carrying "$RUN_TAG" | wc -w) child process(es) of the killed resident survived"
echo "no child of the killed resident survives (five consecutive clean scans)"
echo "relaunching from the same configuration file (the runner's own loop, as the development-host gate does)"
start_resident second || step_fail resident-kill "the relaunch from the same configuration did not reach plan active"
STATUS_B="$(kc_query "$BIN/admin-probe" "$SOCK" get-status)"
SESSION_B="$(kc_field "$STATUS_B" session_uuid)"
PLAN_B="$(kc_field "$STATUS_B" plan_digest)"
CONFIG_B="$(kc_field "$STATUS_B" config_identity)"
BUILD_B="$(kc_field "$STATUS_B" build_identity)"
PROFILE_B="$(kc_field "$STATUS_B" target_profile_digest)"
[ "${#SESSION_B}" -eq 32 ] || step_fail resident-kill "the relaunched resident reported no session identity"
[ "$SESSION_B" != "$SESSION_A" ] || step_fail resident-kill "the relaunched resident reused the killed one's session identity"
[ "$PLAN_A" = "$PLAN_B" ] && [ "$CONFIG_A" = "$CONFIG_B" ] && [ "$BUILD_A" = "$BUILD_B" ] && [ "$PROFILE_A" = "$PROFILE_B" ] ||
  step_fail resident-kill "a declared identity changed across the relaunch"
kc_wait_for_tag "$RUN_TAG" 400 || step_fail resident-kill "the relaunched resident supervises no child"
FRESH=0
for p in $(kc_pids_carrying "$RUN_TAG"); do
  case " $CHILD_AFTER " in *" $p "*) : ;; *) FRESH=$((FRESH + 1)) ;; esac
done
[ "$FRESH" -ge 1 ] || step_fail resident-kill "every child after the relaunch was already running before the kill"
echo "BEFORE/AFTER:"
echo "  session_uuid           $SESSION_A -> $SESSION_B (fresh)"
echo "  plan_digest            $PLAN_A (identical)"
echo "  config_identity        $CONFIG_A (identical)"
echo "  build_identity         $BUILD_A (identical)"
echo "  target_profile_digest  $PROFILE_A (identical)"
echo "  child                  fresh pid(s): $(kc_pids_carrying "$RUN_TAG")"
"$KC_SCRIPTS_DIR/telemetry.sh" --probe "$BIN/admin-probe" --socket "$SOCK" --registry "$KC_ROOT/docs/event-codes.md" --rounds 1 |
  tee "$KC_RUN/poll-after.txt" >/dev/null
grep -q '^poll queries answered 3 of 3$' "$KC_RUN/poll-after.txt" || step_fail resident-kill "the post-relaunch poll did not answer all three queries"
step_ok resident-kill

# --- 12. clean stop --------------------------------------------------------------------------------
kill -TERM "$RES_PID" || step_fail clean-stop "cannot send SIGTERM to the resident"
wait "$RES_PID" 2>/dev/null
STOP_STATUS=$?
RES_PID=""
echo "the resident ended with status $STOP_STATUS after SIGTERM"
[ "$STOP_STATUS" -eq 0 ] || step_fail clean-stop "the resident ended with status $STOP_STATUS after SIGTERM, want 0"
# wait only guarantees the process has exited, not that a redirected fd's
# buffered writes have already landed where a separate `grep` will look for
# them: poll briefly rather than checking once immediately after wait.
FOUND_STOPPED=0
for _ in $(seq 1 20); do
  grep -q '^cake-resident: stopped$' "$KC_RUN/second.out" 2>/dev/null && { FOUND_STOPPED=1; break; }
  sleep 0.1
done
[ "$FOUND_STOPPED" -eq 1 ] || step_fail clean-stop "the resident did not print its stopped line within 2s of exiting"
[ ! -S "$SOCK" ] || step_fail clean-stop "the admin socket is still present after the stop"
kc_settle_absent "$RUN_TAG" 400 || step_fail clean-stop "a child survived the clean stop"
echo "cake-resident: stopped; socket removed; no child"
if [ -f "$KC_RUN/safe-stop.log" ]; then
  echo "safe-stop command ran $(grep -c 'safe-stop ran' "$KC_RUN/safe-stop.log") time(s) (the stub logger, one line per observed child exit)"
fi
step_ok clean-stop

# --- 13. summary -------------------------------------------------------------------------------------
echo "run directory: $KC_RUN"
echo "nothing in this segment could move anything: the child was a line-printing stub and no device was named or opened"
step_ok summary
echo "KIWI-CAKE SEGMENT 1: PASS"
exit 0
