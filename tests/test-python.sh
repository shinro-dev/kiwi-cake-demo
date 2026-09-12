#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-python.sh: the operator scripts parse, teleop.py's usage works
# without lerobot, its lerobot imports resolve where lerobot is installed,
# and each script states the facts it exists for.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v python3 >/dev/null 2>&1 || { echo "test-python: python3 missing"; exit 75; }
FAILS=0
CLEAN=(env -u KC_REMOTE_IP -u KC_ROBOT_ID -u KC_LEADER_PORT -u KC_LEADER_ID)
for f in bin/pi/lekiwi_host_noninteractive.py bin/laptop/teleop.py; do
  [ -f "$ROOT/$f" ] || continue
  if python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])' "$ROOT/$f"; then echo "ok   $f parses"; else echo "FAIL $f does not parse"; FAILS=$((FAILS + 1)); fi
done
W="$ROOT/bin/pi/lekiwi_host_noninteractive.py"
if [ -f "$W" ]; then
  grep -q '^LeKiwi.calibrate = ' "$W" && echo "ok   wrapper replaces LeKiwi.calibrate" || { echo "FAIL wrapper does not replace LeKiwi.calibrate"; FAILS=$((FAILS + 1)); }
  grep -q 'sys.exit(' "$W" && echo "ok   wrapper refuses without a calibration file" || { echo "FAIL wrapper has no refusal"; FAILS=$((FAILS + 1)); }
  grep -q 'from lerobot.robots.lekiwi.lekiwi_host import main' "$W" && echo "ok   wrapper runs the stock main" || { echo "FAIL wrapper does not import the stock main"; FAILS=$((FAILS + 1)); }
fi
T="$ROOT/bin/laptop/teleop.py"
if [ -f "$T" ]; then
  OUT="$("${CLEAN[@]}" python3 "$T" --help 2>&1)"; RC=$?
  [ "$RC" -eq 0 ] && echo "ok   teleop.py --help exits 0" || { echo "FAIL teleop.py --help rc=$RC"; FAILS=$((FAILS + 1)); }
  for opt in --remote-ip --robot-id --leader-port --leader-id --no-rerun --check-imports; do
    printf '%s\n' "$OUT" | grep -q -- "$opt" && echo "ok   usage names $opt" || { echo "FAIL usage lacks $opt"; FAILS=$((FAILS + 1)); }
  done
  "${CLEAN[@]}" python3 "$T" >/dev/null 2>&1; RC=$?
  [ "$RC" -eq 2 ] && echo "ok   teleop.py without arguments exits 2" || { echo "FAIL teleop.py without arguments rc=$RC"; FAILS=$((FAILS + 1)); }
  for k in '"x.vel": 0.0' '"y.vel": 0.0' '"theta.vel": 0.0'; do
    grep -qF "$k" "$T" && echo "ok   teleop.py sends $k" || { echo "FAIL teleop.py lacks $k"; FAILS=$((FAILS + 1)); }
  done
  grep -q 'finally:' "$T" && grep -q 'disconnect()' "$T" && echo "ok   teleop.py disconnects in finally" || { echo "FAIL teleop.py has no finally/disconnect"; FAILS=$((FAILS + 1)); }
  # The lerobot imports themselves, where lerobot is installed (exit 3 = not installed here, skipped).
  OUT="$("${CLEAN[@]}" python3 "$T" --check-imports 2>&1)"; RC=$?
  case "$RC" in
    0) echo "ok   teleop.py --check-imports: $(printf '%s\n' "$OUT" | tail -n 1)" ;;
    3) echo "     (lerobot is not installed in this python3; --check-imports skipped)" ;;
    *) echo "FAIL teleop.py --check-imports rc=$RC: $OUT"; FAILS=$((FAILS + 1)) ;;
  esac
fi
C="$ROOT/bin/pi/apply-lerobot-clamp-fix.sh"
if [ -f "$C" ]; then
  CT="$(mktemp -d "${TMPDIR:-/tmp}/kc-clamp.XXXXXX")"
  mkdir -p "$CT/a/src/lerobot/robots/lekiwi" "$CT/b/src/lerobot/robots/lekiwi" "$CT/c/src/lerobot/robots/lekiwi"
  printf 'def f():\n    goal_present_pos = {key: (g_pos, present_pos[key]) for key, g_pos in arm_goal_pos.items()}\n' >"$CT/a/src/lerobot/robots/lekiwi/lekiwi.py"
  printf 'x = 1\n' >"$CT/b/src/lerobot/robots/lekiwi/lekiwi.py"
  printf 'def f():\n    goal_present_pos = {key: (g_pos, present_pos[key.removesuffix(".pos")]) for key, g_pos in arm_goal_pos.items()}\n' >"$CT/c/src/lerobot/robots/lekiwi/lekiwi.py"
  "$C" --check "$CT/a" >/dev/null 2>&1; RC=$?; [ "$RC" -eq 1 ] && echo "ok   clamp-fix --check reports not applied (1)" || { echo "FAIL clamp-fix --check rc=$RC"; FAILS=$((FAILS + 1)); }
  "$C" --apply "$CT/a" >/dev/null 2>&1 && grep -q 'present_pos\[key.removesuffix(".pos")\]' "$CT/a/src/lerobot/robots/lekiwi/lekiwi.py" && echo "ok   clamp-fix --apply patches the line" || { echo "FAIL clamp-fix --apply"; FAILS=$((FAILS + 1)); }
  "$C" --apply "$CT/a" 2>&1 | grep -q 'already applied' && echo "ok   clamp-fix is idempotent" || { echo "FAIL clamp-fix second apply"; FAILS=$((FAILS + 1)); }
  "$C" --check "$CT/c" >/dev/null 2>&1 && echo "ok   clamp-fix reports a fixed lerobot as already applied (0)" || { echo "FAIL clamp-fix on a fixed file"; FAILS=$((FAILS + 1)); }
  "$C" --check "$CT/b" >/dev/null 2>&1; RC=$?; [ "$RC" -eq 3 ] && echo "ok   clamp-fix refuses a file without the line (3)" || { echo "FAIL clamp-fix refusal rc=$RC"; FAILS=$((FAILS + 1)); }
  rm -rf "$CT"
fi
[ "$FAILS" -eq 0 ] && echo "test-python: PASS" || { echo "test-python: $FAILS failure(s)"; exit 1; }
