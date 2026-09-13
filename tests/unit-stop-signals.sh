#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# tests/unit-stop-signals.sh: a board test of the user unit's stop path. It
# installs kiwi-cake-demo.service from the template (or from --template PATH)
# around a real resident whose child is a torque-free stub that logs every
# signal it receives, stops the unit with systemctl --user stop, and asserts
# that the stub saw the resident's SIGINT and ran its exit path and never a
# SIGTERM from systemd, that the resident printed its stopped line, and that
# systemd killed nothing of the stub. Opens no device. It prints the journal
# excerpt and the stub log for docs/stopping-and-cleanup.md.
#
#   tests/unit-stop-signals.sh [--template PATH]
#
# Exit codes: 0 PASS; 1 an assertion failed; 3 the unit is active (refused);
# 5 usage; 75 the test cannot run here (no user manager, no release binaries,
# or a board the resident refuses by construction). KC_UNIT_TEST_ALLOW_MOCK=1
# lets it dry-run the script itself against tests/mock-bin (proves nothing
# about Cake).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"
TEMPLATE="$KC_SCRIPTS_DIR/templates/kiwi-cake-demo.service.in"
while [ $# -gt 0 ]; do
  case "$1" in
    --template) TEMPLATE="${2:?--template needs a path}"; shift 2 ;;
    -h | --help) sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) kc_fail "unrecognised argument '$1'" 5 ;;
  esac
done
[ -f "$TEMPLATE" ] || kc_fail "template $TEMPLATE does not exist" 5
for t in systemctl journalctl openssl od timeout; do command -v "$t" >/dev/null 2>&1 || { echo "unit-stop-signals: $t missing"; exit 75; }; done
systemctl --user show-environment >/dev/null 2>&1 || { echo "unit-stop-signals: systemctl --user is not available in this session"; exit 75; }
BIN="$(kc_binaries_dir 2>/dev/null)" || { echo "unit-stop-signals: no release binaries (run bin/fetch-release.sh or set KC_RELEASE_DIR)"; exit 75; }
kc_check_binaries "$BIN"
if [ "$(head -c 4 "$BIN/cake-resident" | od -An -c | tr -d ' ')" = "177ELF" ]; then
  kc_facts; CLASS="$(kc_classify)"
  [ "$CLASS" = "pi5-tested" ] || { echo "unit-stop-signals: this board is classified $CLASS; the resident refuses it by construction (docs/targets.md)"; exit 75; }
elif [ "${KC_UNIT_TEST_ALLOW_MOCK:-0}" != "1" ]; then
  echo "unit-stop-signals: $BIN/cake-resident is not an ELF binary; set KC_UNIT_TEST_ALLOW_MOCK=1 to dry-run this script against the mocks"; exit 75
fi
UNIT="kiwi-cake-demo.service"; UNIT_PATH="$HOME/.config/systemd/user/$UNIT"
systemctl --user is-active --quiet "$UNIT" && kc_fail "$UNIT is active; stop it first (bin/demo-stop.sh)" 3
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-unit-stop.XXXXXX")"; chmod 700 "$T"
SOCK="$KC_RUNTIME/admin-unit-stop.sock"
case "$T$BIN$KC_RUNTIME" in *[[:space:]]* | *'|'* | *'&'* | *'\'* | *'%'* | *';'*) kc_fail "a path contains a character a systemd unit cannot carry" 5 ;; esac
BACKUP=""; if [ -f "$UNIT_PATH" ]; then BACKUP="$T/unit.backup"; cp -- "$UNIT_PATH" "$BACKUP"; fi
cleanup() {
  systemctl --user stop "$UNIT" 2>/dev/null; systemctl --user disable "$UNIT" 2>/dev/null
  if [ -n "$BACKUP" ] && [ -f "$BACKUP" ]; then cp -- "$BACKUP" "$UNIT_PATH"; else rm -f -- "$UNIT_PATH"; fi
  systemctl --user daemon-reload 2>/dev/null; systemctl --user reset-failed "$UNIT" 2>/dev/null
  rm -f -- "$SOCK"; rm -rf -- "$T"
}
trap cleanup EXIT; trap 'exit 130' INT; trap 'exit 143' TERM HUP
{ mkdir -p "$KC_RUNTIME" && chmod 700 "$KC_RUNTIME"; } || kc_fail "cannot create $KC_RUNTIME"
# The child: a /bin/sh stub that logs INT and TERM with an epoch, runs one
# exit-path line as a stand-in for a host's disconnect, and holds while its
# parent lives, like segment 1's stub. It ignores the supervisor's positionals.
LOG="$T/stub.log"; STUB="$T/stub-child.sh"
cat >"$STUB" <<'STUB'
#!/bin/sh
# signal-logging stub child for tests/unit-stop-signals.sh: torque-free, opens no device.
log="__LOG__"
exit_path() { echo "stub exit path ran $(date +%s)" >>"$log"; exit 0; }
trap 'echo "stub got SIGINT $(date +%s)" >>"$log"; exit_path' INT
trap 'echo "stub got SIGTERM $(date +%s)" >>"$log"; exit_path' TERM
echo "stub running pid $$ $(date +%s)" >>"$log"
while kill -0 "$PPID" 2>/dev/null; do sleep 0.1; done
echo "stub parent gone $(date +%s)" >>"$log"
exit 0
STUB
sed -i "s|__LOG__|$LOG|" "$STUB"; chmod 755 "$STUB"
SAFE="$T/safe-stop.sh"; printf '#!/bin/sh\nprintf "safe-stop ran\\n" >>"$1"\n' >"$SAFE"; chmod 755 "$SAFE"
KEY="$T/keys/demo.keypair"; kc_generate_keypair "$KEY"; PUB="$(kc_keypair_public "$KEY")"
"$BIN/demo-plan" build --out-dir "$T/bundle" --signing-key-file "$KEY" --run-tag "kiwi-cake-unit-stop-$$" \
  --child-command "$STUB" --child-stop-signal SIGINT --safe-stop-command "$SAFE" --safe-stop-command "$T/safe-stop.log" \
  >"$T/manifest.txt" 2>"$T/demo-plan.err" || { cat "$T/demo-plan.err" >&2; kc_fail "demo-plan build did not produce its artifacts"; }
STORE="$(kc_manifest_value "$T/manifest.txt" store_root)"; PLAN="$(kc_manifest_value "$T/manifest.txt" plan_artifact)"
CONF="$T/resident.conf"; kc_write_resident_conf "$CONF" "$STORE" "$SOCK" "$PUB" "$PLAN"
mkdir -p "$(dirname "$UNIT_PATH")" || kc_fail "cannot create $(dirname "$UNIT_PATH")"
sed -e "s|@@CAKE_RESIDENT@@|$BIN/cake-resident|" -e "s|@@RESIDENT_CONF@@|$CONF|" "$TEMPLATE" >"$UNIT_PATH" || kc_fail "could not write $UNIT_PATH"
kc_template_has_placeholder "$UNIT_PATH" && kc_fail "the rendered unit still carries a placeholder"
systemctl --user daemon-reload || kc_fail "systemctl --user daemon-reload failed"
echo "unit-stop-signals: template $TEMPLATE, binaries $BIN"
grep -E '^(Restart|RestartSec|KillMode|KillSignal|TimeoutStopSec)=' "$UNIT_PATH" | sed 's/^/  /'
grep -qE '^KillMode=' "$UNIT_PATH" || echo "  (no KillMode line: systemd's default, control-group, applies)"
START="$(date '+%Y-%m-%d %H:%M:%S')"
rm -f -- "$SOCK"
systemctl --user start "$UNIT" || kc_fail "systemctl --user start $UNIT failed"
READY=0
for _ in $(seq 1 600); do
  if [ -S "$SOCK" ] && kc_query "$BIN/admin-probe" "$SOCK" get-status | grep -qx 'status ok'; then READY=1; break; fi
  systemctl --user is-active --quiet "$UNIT" || break
  sleep 0.05
done
[ "$READY" -eq 1 ] || { systemctl --user status --no-pager "$UNIT" >&2; kc_fail "the resident did not reach a state answering get-status"; }
for _ in $(seq 1 200); do grep -q '^stub running pid ' "$LOG" 2>/dev/null && break; sleep 0.05; done
STUB_PID="$(sed -nE 's/^stub running pid ([0-9]+) .*/\1/p' "$LOG" 2>/dev/null | head -n 1)"
[ -n "$STUB_PID" ] || kc_fail "the stub never logged that it was running"
MAIN="$(systemctl --user show --property MainPID --value "$UNIT")"
echo "resident MainPID $MAIN answering get-status; stub pid $STUB_PID running; stopping the unit"
systemctl --user stop "$UNIT"; STOP_RC=$?
JOURNAL=""
for _ in $(seq 1 50); do
  JOURNAL="$(journalctl --user -u "$UNIT" --since "$START" -o short-monotonic --no-pager 2>&1)"
  printf '%s\n' "$JOURNAL" | grep -q 'cake-resident: stopped' && break
  sleep 0.1
done
FAILS=0; ok() { echo "ok   $1"; }; bad() { echo "FAIL $1"; FAILS=$((FAILS + 1)); }
[ "$STOP_RC" -eq 0 ] && ok "systemctl --user stop returned 0" || bad "systemctl --user stop returned $STOP_RC"
grep -q '^stub got SIGINT ' "$LOG" && ok "the stub received SIGINT (the resident's configured stop signal)" || bad "the stub did not log SIGINT"
grep -q '^stub exit path ran ' "$LOG" && ok "the stub ran its exit path" || bad "the stub's exit path did not run"
grep -q '^stub got SIGTERM ' "$LOG" && bad "the stub received SIGTERM (systemd signalled the child)" || ok "the stub received no SIGTERM"
printf '%s\n' "$JOURNAL" | grep -q 'cake-resident: stopped' && ok "the journal has the resident's stopped line" || bad "the journal has no 'cake-resident: stopped' line"
printf '%s\n' "$JOURNAL" | grep -qE "Killing process $STUB_PID " && bad "systemd killed the stub (pid $STUB_PID)" || ok "systemd killed nothing of the stub"
ACTIVE="$(systemctl --user is-active "$UNIT")"
[ "$ACTIVE" = "inactive" ] && ok "the unit is inactive" || bad "the unit is $ACTIVE"
if pgrep -f -- "$T/" >/dev/null; then bad "a process naming the run directory remains: $(pgrep -af -- "$T/" | paste -sd ';')"; else ok "no process naming the run directory remains"; fi
[ ! -S "$SOCK" ] && ok "the admin socket is gone" || bad "the admin socket is still present"
echo "journal excerpt ($UNIT since $START, short-monotonic):"; printf '%s\n' "$JOURNAL" | sed 's/^/  /'
echo "stub log:"; sed 's/^/  /' "$LOG"
[ "$FAILS" -eq 0 ] && echo "unit-stop-signals: PASS" || { echo "unit-stop-signals: $FAILS failure(s)"; exit 1; }
