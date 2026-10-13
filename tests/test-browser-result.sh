#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# RENDERED-PAGE REGRESSION: a run's result reads the same however the tab arrived.
#
# The defect this exists for: the figures behind a run's result reached the screen only for a tab that folded the
# predicate record live. A tab that opened afterwards said the record was not in its fold, while the measurement sat
# in the retained run the whole time. Three tabs must agree, and all three must show the run's measured figures:
#
#   A. a tab that WATCHED the run finish;
#   B. a tab that opened LATE, after the run was over;
#   C. a RELOAD, which is a fresh page against the same finished run.
#
# THE RUN IS A SYNTHETIC ONE, AND THE SCREEN SAYS SO. The runtime here is --synthetic, so the result is a
# rehearsal's, and by the operator's ruling a fixture result is never worded as a physical placement: the line each
# tab draws carries the REHEARSAL mark and "nothing was physically moved", and it must never say "Placed and
# returned". The figures are the rehearsal's own records and are shown as such. What is compared across the three
# tabs is that whole line, unchanged.
#
# THE LINE IS HISTORY'S. The design review moved every completion statement off the main screen, and the finished
# run's one line now lives in the History view of the Diagnostics drawer. So each tab opens History the way an
# operator does, by pressing its button once the page is up, and the line is read there. The button press is the only
# thing the script does; it reads nothing and commands nothing.
#
# It is not in tests/run-all.sh on purpose: it needs Chrome and it starts a runtime. Run it by hand, from the
# repository root. Everything it starts is synthetic, on an ephemeral port, in a temporary directory, and is torn
# down at the end. No robot, no camera, no hardware command.
#
# usage: bash tests/test-browser-result.sh
#   KC_WEB_BUILD   the built console to serve (default apps/console-web/build)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WORK="$(mktemp -d)"
DAEMON_PID=""
API_PID=""

cleanup() {
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "$DAEMON_PID" ] && kill "$DAEMON_PID" 2>/dev/null || true
  chmod -R u+w "$WORK" 2>/dev/null || true
  rm -rf "$WORK" 2>/dev/null || true
}
trap cleanup EXIT

fail() { echo "FAIL $*"; exit 1; }

command -v node >/dev/null 2>&1 || fail "node is not on PATH; this suite needs node 22"
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[ "$NODE_MAJOR" -ge 22 ] || fail "node $NODE_MAJOR is too old; use node 22 (nvm use)"
[ -x "${KC_CHROME:-/opt/google/chrome/chrome}" ] || fail "no chrome at ${KC_CHROME:-/opt/google/chrome/chrome}; set KC_CHROME"
# WHICH BUILD IS UNDER TEST. The default is the directory the console is normally served from. It is overridable
# because a replacement presentation is built into a directory of its own while it is being developed: `vite build`
# empties its output directory, and emptying the deployed one would change what the operator's console is serving,
# live. A regression that can only run against the deployed assets is one that gets run after the cutover instead of
# before it. Nothing else changes: the same three tabs, against the same comparison.
WEB_BUILD="${KC_WEB_BUILD:-apps/console-web/build}"
[ -d "$WEB_BUILD" ] || fail "$WEB_BUILD is missing; run: pnpm -C apps/console-web build"

export PYTHONPATH="apps/console-api:packages/demo-runtime:packages/demo-contracts/python"
PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
SOCKET="$WORK/data/runtime.sock"

# The scenario is the shape of the 2026-09-22 physical run: placed and returned, clearance target missed.
KC_FAKE_SCENARIO=placed_short KC_FAKE_STEP_S=0.4 \
  python3 -m demo_runtime.daemon --data-dir "$WORK/data" --synthetic > "$WORK/daemon.log" 2>&1 &
DAEMON_PID=$!
for _ in $(seq 1 40); do [ -S "$SOCKET" ] && break; sleep 0.25; done
[ -S "$SOCKET" ] || fail "the runtime never created its socket; see $WORK/daemon.log"

python3 -m console_api --runtime-socket "$SOCKET" --port "$PORT" --web-build "$WEB_BUILD" > "$WORK/api.log" 2>&1 &
API_PID=$!
for _ in $(seq 1 40); do curl -sf "http://127.0.0.1:$PORT/" -o /dev/null && break; sleep 0.25; done
curl -sf "http://127.0.0.1:$PORT/" -o /dev/null || fail "the console API never answered; see $WORK/api.log"

URL="http://127.0.0.1:$PORT/"
SENTENCE="Cube-centre edge margin"
READY="Stop ends the program in software"

# Opens the History view by its own button, which is what an operator presses. Written here, not committed beside the
# suite, because it is two lines of this suite and nothing else runs it.
OPEN_HISTORY="$WORK/open-history.js"
cat > "$OPEN_HISTORY" <<'JS'
(() => { const button = Array.from(document.querySelectorAll('button')).find((node) => node.textContent.trim() === 'History'); if (button) button.click(); return button !== undefined; })();
JS

# Tab A watches: it is opened first, with History open, and the cycle is started while it is attached. It is read
# once the run has had time to finish; the line it must then show is checked below with the other two.
node tools/cdp_page.mjs --url "$URL" --wait "$READY" --script "$OPEN_HISTORY" --after 30000 --port 9401 --screenshot "$WORK/watched.png" > "$WORK/a.txt" 2>&1 &
WATCHER=$!
sleep 6
python3 -m demo_runtime.cli --socket "$SOCKET" run cycle > "$WORK/run.txt" 2>&1 || fail "the cycle was not accepted"
wait "$WATCHER" || fail "the watching tab never drew the screen; see $WORK/a.txt"

# Tabs B and C open AFTER the run is over. Each is a fresh browser and a fresh page: nothing of the run was folded
# live by either, so both must read the figures back from the run's retained evidence.
node tools/cdp_page.mjs --url "$URL" --wait "$READY" --script "$OPEN_HISTORY" --after 6000 --port 9402 > "$WORK/b.txt" 2>&1 \
  || fail "the late-opening tab did not draw the screen; see $WORK/b.txt"
node tools/cdp_page.mjs --url "$URL" --wait "$READY" --script "$OPEN_HISTORY" --after 6000 --port 9403 --screenshot "$WORK/reloaded.png" > "$WORK/c.txt" 2>&1 \
  || fail "the reloaded tab did not draw the screen; see $WORK/c.txt"

# The one line each tab DRAWS for the result, compared across all three. The findings line this tool prints first is
# JSON and it echoes the string being waited for, so it is excluded: comparing it would compare the harness to itself.
line_of() { grep -F "$SENTENCE" "$1" | grep -v '^{' | head -1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'; }
A="$(line_of "$WORK/a.txt")"
B="$(line_of "$WORK/b.txt")"
C="$(line_of "$WORK/c.txt")"

[ -n "$A" ] || fail "the watching tab drew no result line"
[ "$A" = "$B" ] || fail "a late-opening tab disagrees with the tab that watched:
  watched: $A
  late:    $B"
[ "$A" = "$C" ] || fail "a reloaded tab disagrees with the tab that watched:
  watched: $A
  reload:  $C"

case "$A" in
  *"not in this fold"*) fail "the result fell back to the not-in-fold sentence: $A" ;;
  *footprint*) fail "the screen called the centre margin a footprint clearance: $A" ;;
  *"Placed and returned"*) fail "a synthetic run was worded as a physical placement: $A" ;;
esac
# THE FIGURES THEMSELVES, not only their label: the measured margin and the required one, as numbers. The three tabs
# drew the identical line (checked above), so all three show them.
printf '%s\n' "$A" | grep -Eq 'Cube-centre edge margin: [0-9]+(\.[0-9]+)? ?[a-z]*; required: [0-9]+(\.[0-9]+)?' \
  || fail "the result line carries no measured figures: $A"
# THE HONEST SYNTHETIC WORDING: the rehearsal mark, and the statement that nothing physical was moved.
case "$A" in
  *REHEARSAL*"nothing was physically moved"*) ;;
  *) fail "the synthetic run's line does not say it is a rehearsal that moved nothing: $A" ;;
esac

grep -q '"overflows":false' "$WORK/a.txt" || fail "the page overflows horizontally at 1440"

# AND NOT ON THE MAIN SCREEN: the action card says where the line is and does not restate it.
grep -q 'its outcome, duration and evidence are in History' "$WORK/c.txt" || fail "the action card does not point at History for the finished run"

echo "ok   a watching tab, a late-opening tab and a reload all read the same rehearsal line in History, with its figures:"
echo "     $A"
echo "test-browser-result: PASS"
