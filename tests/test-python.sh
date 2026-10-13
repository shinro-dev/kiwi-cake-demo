#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-python.sh: the operator scripts parse, teleop.py's usage works
# without lerobot, its lerobot imports resolve where lerobot is installed,
# and each script states the facts it exists for, and its shutdown paths
# close both devices independently against stub modules.
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
  for d in README.md docs/reproduce-end-to-end.md; do
    grep -qF 'arm-only bench example' "$ROOT/$d" && echo "ok   $d labels teleop.py an arm-only bench example" || { echo "FAIL $d does not label teleop.py an arm-only bench example"; FAILS=$((FAILS + 1)); }
  done
  grep -q 'finally:' "$T" && grep -q 'disconnect()' "$T" && echo "ok   teleop.py disconnects in finally" || { echo "FAIL teleop.py has no finally/disconnect"; FAILS=$((FAILS + 1)); }
  # The shutdown and usage paths, against stub lerobot modules (no device, no lerobot).
  if OUT="$("${CLEAN[@]}" python3 "$ROOT/tests/test_teleop.py" 2>&1)"; then
    echo "ok   tests/test_teleop.py: $(printf '%s\n' "$OUT" | grep -E '^Ran [0-9]+ tests? ' | head -n 1)"
  else
    echo "FAIL tests/test_teleop.py"; printf '%s\n' "$OUT" | sed 's/^/  /'; FAILS=$((FAILS + 1))
  fi
  # The lerobot imports themselves, where lerobot is installed (exit 3 = not installed here, skipped).
  OUT="$("${CLEAN[@]}" python3 "$T" --check-imports 2>&1)"; RC=$?
  case "$RC" in
    0) echo "ok   teleop.py --check-imports: $(printf '%s\n' "$OUT" | tail -n 1)" ;;
    3) echo "SKIP teleop.py --check-imports: lerobot is not installed in this python3 (rc 3)" ;;
    *) echo "FAIL teleop.py --check-imports rc=$RC: $OUT"; FAILS=$((FAILS + 1)) ;;
  esac
fi
C="$ROOT/bin/pi/apply-lerobot-patches.sh"
if [ -f "$C" ]; then
  CT="$(mktemp -d "${TMPDIR:-/tmp}/kc-clamp.XXXXXX")"
  LK=src/lerobot/robots/lekiwi/lekiwi.py
  CFG=src/lerobot/robots/lekiwi/config_lekiwi.py
  # Seven synthetic lerobot trees. a is stock b4e2d0b and carries no patch at
  # all, b is not lerobot at all, c carries all three already, and d carries the
  # clamp fix without the empty-arm guard, which is what a checkout patched
  # before the guard was tracked here looks like. The three for the arm gains:
  # e is the DEPLOYED form, which declares the three fields twice with the same
  # values, f declares them with a value this repository never recorded, and g
  # has no config_lekiwi.py beside lekiwi.py at all. The indentation is the real
  # file's, because the guard patch indents the line it guards one level more.
  for t in a b c d e f g; do mkdir -p "$CT/$t/$(dirname "$LK")"; done
  HEAD='def send_action(self):'
  CAP='    if self.config.max_relative_target is not None:'
  CLAMP_OLD='        goal_present_pos = {key: (g_pos, present_pos[key]) for key, g_pos in arm_goal_pos.items()}'
  CLAMP_NEW='        goal_present_pos = {key: (g_pos, present_pos[key.removesuffix(".pos")]) for key, g_pos in arm_goal_pos.items()}'
  WRITE='        self.bus.sync_write("Goal_Position", arm_goal_pos_raw)'
  GUARD='        if arm_goal_pos_raw:'
  WRITE_GUARDED='            self.bus.sync_write("Goal_Position", arm_goal_pos_raw)'
  KEPT='# a local edit of this checkout that belongs to nobody here'
  # configure(), stock and patched. The stock block is the five lines the third
  # patch replaces, byte for byte; the patched one resolves a scalar or a dict.
  CONF_DEF='    def configure(self):'
  CONF_FOR='        for name in self.arm_motors:'
  G1='            # Set P_Coefficient to lower value to avoid shakiness (Default is 32)'
  G2='            self.bus.write("P_Coefficient", name, 16)'
  G3='            # Set I_Coefficient and D_Coefficient to default value 0 and 32'
  G4='            self.bus.write("I_Coefficient", name, 0)'
  G5='            self.bus.write("D_Coefficient", name, 32)'
  P1='            for register, setting in ('
  P2='                ("P_Coefficient", self.config.arm_p_coefficient),'
  P3='                ("I_Coefficient", self.config.arm_i_coefficient),'
  P4='                ("D_Coefficient", self.config.arm_d_coefficient),'
  P5='            ):'
  P6='                self.bus.write(register, name, setting[name] if isinstance(setting, dict) else setting)'
  CFG_CLASS='class LeKiwiConfig(RobotConfig):'
  CFG_COMMENT='    # Set to `True` for backward compatibility with previous policies/dataset'
  CFG_LAST='    use_degrees: bool = True'
  FIELD_P='    arm_p_coefficient: int | dict[str, int] = 16'
  FIELD_I='    arm_i_coefficient: int | dict[str, int] = 0'
  FIELD_D='    arm_d_coefficient: int | dict[str, int] = 32'
  AGAIN_P='    arm_p_coefficient: int = 16'
  AGAIN_I='    arm_i_coefficient: int = 0'
  AGAIN_D='    arm_d_coefficient: int = 32'
  OTHER_P='    arm_p_coefficient: int = 20'
  printf '%s\n' "$KEPT" "$HEAD" "$CAP" "$CLAMP_OLD" "$WRITE" "$CONF_DEF" "$CONF_FOR" "$G1" "$G2" "$G3" "$G4" "$G5" >"$CT/a/$LK"
  printf '%s\n' 'x = 1' >"$CT/b/$LK"
  printf '%s\n' "$HEAD" "$CAP" "$CLAMP_NEW" "$GUARD" "$WRITE_GUARDED" "$CONF_DEF" "$CONF_FOR" "$P1" "$P2" "$P3" "$P4" "$P5" "$P6" >"$CT/c/$LK"
  printf '%s\n' "$HEAD" "$CAP" "$CLAMP_NEW" "$WRITE" "$CONF_DEF" "$CONF_FOR" "$G1" "$G2" "$G3" "$G4" "$G5" >"$CT/d/$LK"
  cp "$CT/c/$LK" "$CT/e/$LK"; cp "$CT/c/$LK" "$CT/f/$LK"; cp "$CT/a/$LK" "$CT/g/$LK"
  printf '%s\n' "$CFG_CLASS" "$CFG_COMMENT" "$CFG_LAST" >"$CT/a/$CFG"
  printf '%s\n' "$CFG_CLASS" "$CFG_COMMENT" "$CFG_LAST" >"$CT/d/$CFG"
  printf '%s\n' "$CFG_CLASS" "$CFG_COMMENT" "$CFG_LAST" '' "$FIELD_P" "$FIELD_I" "$FIELD_D" >"$CT/c/$CFG"
  printf '%s\n' "$CFG_CLASS" "$CFG_COMMENT" "$CFG_LAST" '' "$FIELD_P" "$FIELD_I" "$FIELD_D" '' "$AGAIN_P" "$AGAIN_I" "$AGAIN_D" >"$CT/e/$CFG"
  printf '%s\n' "$CFG_CLASS" "$CFG_COMMENT" "$CFG_LAST" '' "$OTHER_P" "$FIELD_I" "$FIELD_D" >"$CT/f/$CFG"
  "$C" --check "$CT/a" >/dev/null 2>&1; RC=$?; [ "$RC" -eq 1 ] && echo "ok   clamp-fix --check reports not applied (1)" || { echo "FAIL clamp-fix --check rc=$RC"; FAILS=$((FAILS + 1)); }
  OUT="$("$C" --check "$CT/a" 2>&1)"
  printf '%s\n' "$OUT" | grep -q '^clamp-fix: not applied' && echo "ok   --check names the clamp fix" || { echo "FAIL --check does not name the clamp fix"; FAILS=$((FAILS + 1)); }
  printf '%s\n' "$OUT" | grep -q '^empty-arm-guard: not applied' && echo "ok   --check names the empty-arm guard" || { echo "FAIL --check does not name the empty-arm guard"; FAILS=$((FAILS + 1)); }
  printf '%s\n' "$OUT" | grep -q '^configurable-arm-gains-fields: not applied' && printf '%s\n' "$OUT" | grep -q '^configurable-arm-gains-writes: not applied' && echo "ok   --check names both halves of the arm gains patch" || { echo "FAIL --check does not name the arm gains patch: $OUT"; FAILS=$((FAILS + 1)); }
  "$C" --apply "$CT/a" >/dev/null 2>&1 && grep -q 'present_pos\[key.removesuffix(".pos")\]' "$CT/a/$LK" && echo "ok   clamp-fix --apply patches the line" || { echo "FAIL clamp-fix --apply"; FAILS=$((FAILS + 1)); }
  grep -qxF -- "$GUARD" "$CT/a/$LK" && grep -qxF -- "$WRITE_GUARDED" "$CT/a/$LK" && echo "ok   --apply guards the arm write one level deeper" || { echo "FAIL --apply did not guard the arm write"; FAILS=$((FAILS + 1)); }
  # The two files of the third patch, and the three defaults that reproduce the
  # values the stock configure() wrote as literals: no gain is different.
  grep -qxF -- "$FIELD_P" "$CT/a/$CFG" && grep -qxF -- "$FIELD_I" "$CT/a/$CFG" && grep -qxF -- "$FIELD_D" "$CT/a/$CFG" && echo "ok   --apply writes the three gain fields with the stock values as their defaults" || { echo "FAIL --apply did not write the gain fields"; FAILS=$((FAILS + 1)); }
  grep -qxF -- "$P6" "$CT/a/$LK" && ! grep -qxF -- "$G2" "$CT/a/$LK" && echo "ok   --apply makes configure write the fields and leaves no literal behind" || { echo "FAIL --apply did not rewrite configure"; FAILS=$((FAILS + 1)); }
  grep -qxF -- "$KEPT" "$CT/a/$LK" && echo "ok   --apply leaves an unrelated local edit where it was" || { echo "FAIL --apply dropped an unrelated local edit"; FAILS=$((FAILS + 1)); }
  python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])' "$CT/a/$CFG" && echo "ok   the patched configuration still parses" || { echo "FAIL the patched configuration does not parse"; FAILS=$((FAILS + 1)); }
  python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])' "$CT/a/$LK" && echo "ok   the patched file still parses" || { echo "FAIL the patched file does not parse"; FAILS=$((FAILS + 1)); }
  cp "$CT/a/$LK" "$CT/a-patched.py"; cp "$CT/a/$CFG" "$CT/a-patched-config.py"
  # The output is captured rather than piped: the script now prints a line per
  # patch, and a grep -q that closes the pipe after the first one would take
  # the script down with it under pipefail.
  OUT="$("$C" --apply "$CT/a" 2>&1)"
  printf '%s\n' "$OUT" | grep -q '^clamp-fix: already applied' && printf '%s\n' "$OUT" | grep -q '^empty-arm-guard: already applied' && echo "ok   clamp-fix is idempotent" || { echo "FAIL clamp-fix second apply: $OUT"; FAILS=$((FAILS + 1)); }
  printf '%s\n' "$OUT" | grep -q '^configurable-arm-gains-fields: already applied' && printf '%s\n' "$OUT" | grep -q '^configurable-arm-gains-writes: already applied' && echo "ok   the arm gains patch is idempotent across both files" || { echo "FAIL arm gains second apply: $OUT"; FAILS=$((FAILS + 1)); }
  cmp -s "$CT/a-patched.py" "$CT/a/$LK" && echo "ok   a second --apply leaves the file byte for byte" || { echo "FAIL a second --apply rewrote the file"; FAILS=$((FAILS + 1)); }
  cmp -s "$CT/a-patched-config.py" "$CT/a/$CFG" && echo "ok   a second --apply leaves the configuration byte for byte" || { echo "FAIL a second --apply rewrote the configuration"; FAILS=$((FAILS + 1)); }
  "$C" --check "$CT/c" >/dev/null 2>&1 && echo "ok   clamp-fix reports a fixed lerobot as already applied (0)" || { echo "FAIL clamp-fix on a fixed file"; FAILS=$((FAILS + 1)); }
  "$C" --check "$CT/b" >/dev/null 2>&1; RC=$?; [ "$RC" -eq 3 ] && echo "ok   clamp-fix refuses a file without the line (3)" || { echo "FAIL clamp-fix refusal rc=$RC"; FAILS=$((FAILS + 1)); }
  "$C" --apply "$CT/b" >/dev/null 2>&1; RC=$?; [ "$RC" -eq 3 ] && grep -qxF 'x = 1' "$CT/b/$LK" && echo "ok   a refused file is left alone (3)" || { echo "FAIL --apply on an unrecognized file rc=$RC"; FAILS=$((FAILS + 1)); }
  # A checkout carrying the clamp fix and not the guard: each patch is reported
  # on its own, and --apply writes only the one that is missing.
  OUT="$("$C" --check "$CT/d" 2>&1)"; RC=$?
  [ "$RC" -eq 1 ] && printf '%s\n' "$OUT" | grep -q '^clamp-fix: already applied' && printf '%s\n' "$OUT" | grep -q '^empty-arm-guard: not applied' && echo "ok   --check reports each patch on its own (1)" || { echo "FAIL --check on a half-patched tree rc=$RC: $OUT"; FAILS=$((FAILS + 1)); }
  "$C" --apply "$CT/d" >/dev/null 2>&1 && grep -qxF -- "$GUARD" "$CT/d/$LK" && grep -qxF -- "$CLAMP_NEW" "$CT/d/$LK" && echo "ok   --apply adds only the guard the tree is missing" || { echo "FAIL --apply on a half-patched tree"; FAILS=$((FAILS + 1)); }
  # The DEPLOYED form: the three fields declared twice with the same values. The
  # robot computer carries exactly this and the operator has ruled it stays, so
  # it must read as already applied, be named as duplicated, and be left alone.
  OUT="$("$C" --check "$CT/e" 2>&1)"; RC=$?
  [ "$RC" -eq 0 ] && printf '%s\n' "$OUT" | grep -q '^configurable-arm-gains-fields: already applied, .*duplicat' && echo "ok   --check accepts the duplicated declaration the robot carries and names it (0)" || { echo "FAIL --check on the deployed duplicated form rc=$RC: $OUT"; FAILS=$((FAILS + 1)); }
  cp "$CT/e/$CFG" "$CT/e-before.py"
  "$C" --apply "$CT/e" >/dev/null 2>&1; RC=$?
  [ "$RC" -eq 0 ] && cmp -s "$CT/e-before.py" "$CT/e/$CFG" && echo "ok   --apply writes nothing to the duplicated declaration (0)" || { echo "FAIL --apply rewrote the duplicated declaration rc=$RC"; FAILS=$((FAILS + 1)); }
  # A configuration carrying a value nobody recorded here: refused, and the
  # value it found is named, because applying over it would conceal it.
  OUT="$("$C" --check "$CT/f" 2>&1)"; RC=$?
  [ "$RC" -eq 3 ] && printf '%s\n' "$OUT" | grep -q '20' && echo "ok   --check refuses a configuration with a value this repository never recorded (3)" || { echo "FAIL --check on an unrecognized configuration rc=$RC: $OUT"; FAILS=$((FAILS + 1)); }
  cp "$CT/f/$CFG" "$CT/f-before.py"; cp "$CT/f/$LK" "$CT/f-before-lk.py"
  "$C" --apply "$CT/f" >/dev/null 2>&1; RC=$?
  [ "$RC" -eq 3 ] && cmp -s "$CT/f-before.py" "$CT/f/$CFG" && cmp -s "$CT/f-before-lk.py" "$CT/f/$LK" && echo "ok   an unrecognized configuration refuses the whole checkout and writes nothing (3)" || { echo "FAIL --apply on an unrecognized configuration rc=$RC"; FAILS=$((FAILS + 1)); }
  # And a checkout with no configuration beside lekiwi.py: also neither state.
  cp "$CT/g/$LK" "$CT/g-before.py"
  OUT="$("$C" --apply "$CT/g" 2>&1)"; RC=$?
  [ "$RC" -eq 3 ] && cmp -s "$CT/g-before.py" "$CT/g/$LK" && printf '%s\n' "$OUT" | grep -q 'config_lekiwi.py' && echo "ok   a checkout without config_lekiwi.py is refused before anything is written (3)" || { echo "FAIL --apply without a configuration rc=$RC: $OUT"; FAILS=$((FAILS + 1)); }
  "$C" >/dev/null 2>&1; RC=$?; [ "$RC" -eq 5 ] && echo "ok   the patch script refuses a call without arguments (5)" || { echo "FAIL patch script usage rc=$RC"; FAILS=$((FAILS + 1)); }
  rm -rf "$CT"
fi
[ "$FAILS" -eq 0 ] && echo "test-python: PASS" || { echo "test-python: $FAILS failure(s)"; exit 1; }
