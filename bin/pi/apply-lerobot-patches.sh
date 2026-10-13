#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# apply-lerobot-patches.sh: apply the local patches this demo needs in a
# lerobot checkout at commit b4e2d0b. They are local modifications of an
# installed checkout: a git checkout, a pull or a reinstall silently takes
# them away again, which is why this script exists rather than a note.
#
# clamp-fix is lerobot's own fix f66e512 (issue 4309), where send_action
# raises KeyError on every command whenever --robot.max_relative_target is
# set. The fix is one line: the present positions are keyed by bare motor
# name, the goal by "<motor>.pos". A checkout at or after f66e512 already
# carries it and is reported as already applied.
#
# empty-arm-guard puts "if arm_goal_pos_raw:" in front of the
# sync_write("Goal_Position", ...) call, so that a command carrying base
# velocities and no arm positions writes the velocities only. Without the
# guard that call reaches the motor bus with an empty dictionary, the bus
# raises StopIteration while looking up the motor model of the first entry,
# and it raises before the Goal_Velocity write underneath it ever happens.
# The host catches the exception and keeps listening, so nothing fails
# loudly: the dispatch clock is never refreshed, the base watchdog sees a
# stale command and stops the base, and the laptop is told nothing at all.
# It presents as a base that will not move, with no error anywhere the
# operator is looking. This one is not an upstream fix; this repository is
# the only place it is written down.
#
# configurable-arm-gains is the third one, and it CHANGES NO GAIN. The stock
# LeKiwi.configure() writes the literals 16, 0 and 32 to P_Coefficient,
# I_Coefficient and D_Coefficient of every arm motor. This patch gives
# LeKiwiConfig three fields whose defaults are exactly those three numbers
# and makes configure() write the fields instead, so a robot that sets none
# of them is configured precisely as an unpatched lerobot configures it.
# What it buys is the reading end: bin/pi/lekiwi_host_safe_attach.py checks
# the live P/I/D registers of every arm motor against those same three
# fields before it attaches to an arm that is already holding its pose, and
# on a stock LeKiwiConfig that read raises "AttributeError: 'LeKiwiConfig'
# object has no attribute 'arm_p_coefficient'" after the arm has been read
# and before any register is written. It is written here as two entries
# because it touches two files, and each of them is classified, reported and
# applied on its own; together they are one dependency.
#
# A scalar field applies to every arm motor and a dictionary maps motor name
# to value, the shape max_relative_target already has in that file, and the
# shape both configure() and the attach resolve.
#
# Idempotent, and safe to run twice: a patch whose fixed text is already in
# the file is reported as already applied and nothing is written; a file
# that carries neither the fixed nor the expected unpatched text of a patch
# is refused whole, and no patch is written to it. Nothing else in the
# checkout is read or rewritten, so local edits beside these are preserved.
#
# THE ONE STATE THAT IS NEITHER. A config_lekiwi.py that already declares the
# three fields with DIFFERENT values is not the configuration recorded here,
# and writing over it would conceal that. It is refused with exit 3 and the
# values it found are printed. A declaration REPEATED with the recorded
# values is a different matter: the robot computer carries the three fields
# twice, in two adjacent blocks left by two overlapping hand edits, Python
# keeps the last, and the resolved values are 16, 0 and 32 either way. That
# is reported as already applied with the duplication named, and nothing is
# written to it: this script writes the block once, and LIMITATIONS.md
# records the duplication so that a clean install differing from the robot
# in that one way is not read later as a configuration change.
#
# The classification of config_lekiwi.py is done by bin/pi/host_boundary.py,
# which must sit next to this script, so that the rule for "what this
# install resolves the gains to" has ONE implementation: the same function
# answers here and in the --verify-robot pass that every host start runs.
#
#   bin/pi/apply-lerobot-patches.sh --check LEROBOT_DIR   exit 0 all applied, 1 one of them is not yet, 3 unknown file
#   bin/pi/apply-lerobot-patches.sh --apply LEROBOT_DIR   exit 0 all applied (now or before), 3 unknown file
set -uo pipefail
MODE="${1:-}"; DIR="${2:-}"
[ "$MODE" = "--check" ] || [ "$MODE" = "--apply" ] || { echo "usage: $0 --check|--apply LEROBOT_DIR" >&2; exit 5; }
PKG="src/lerobot/robots/lekiwi"
F="$DIR/$PKG/lekiwi.py"
[ -f "$F" ] || { echo "lerobot-patch: $F does not exist" >&2; exit 5; }
HB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/host_boundary.py"
[ -f "$HB" ] || { echo "lerobot-patch: $HB does not exist; it reads the configuration this script writes, and the two must agree" >&2; exit 5; }

# The stock text of the two blocks the third patch replaces, and what they become. They are held in variables rather
# than in the table below only because they are many lines long. Neither one ends in a newline: each is replaced
# inside the file, and the line that followed it stays where it was.
GAINS_FIELDS_OLD="$(cat <<'EOF'
    # Set to `True` for backward compatibility with previous policies/dataset
    use_degrees: bool = True
EOF
)"
GAINS_FIELDS_NEW="$(cat <<'EOF'
    # Set to `True` for backward compatibility with previous policies/dataset
    use_degrees: bool = True

    # The three servo PID gains LeKiwi.configure writes to every arm motor. These defaults are the literals that
    # stood in configure() itself, so a robot that sets none of them is configured exactly as an unpatched lerobot
    # configures it: making them configurable is the whole of the change, and no gain is different because of it.
    # They are fields because a host has to be able to READ what the arm was configured with: the verify-first
    # attach used by this demo compares the live P/I/D registers of every arm motor against these three values
    # before it takes over an arm that is already holding its pose, and refuses rather than write anything.
    # A scalar applies to every arm motor; a dictionary maps motor name to value, as max_relative_target does.
    arm_p_coefficient: int | dict[str, int] = 16
    arm_i_coefficient: int | dict[str, int] = 0
    arm_d_coefficient: int | dict[str, int] = 32
EOF
)"
GAINS_WRITES_OLD="$(cat <<'EOF'
            # Set P_Coefficient to lower value to avoid shakiness (Default is 32)
            self.bus.write("P_Coefficient", name, 16)
            # Set I_Coefficient and D_Coefficient to default value 0 and 32
            self.bus.write("I_Coefficient", name, 0)
            self.bus.write("D_Coefficient", name, 32)
EOF
)"
GAINS_WRITES_NEW="$(cat <<'EOF'
            # The same three numbers the literals 16, 0 and 32 wrote here, taken from the configuration instead, so
            # that what this host writes and what a host reading the configuration expects are ONE fact and not two.
            # The defaults of those fields are those literals, so this writes what it has always written.
            for register, setting in (
                ("P_Coefficient", self.config.arm_p_coefficient),
                ("I_Coefficient", self.config.arm_i_coefficient),
                ("D_Coefficient", self.config.arm_d_coefficient),
            ):
                self.bus.write(register, name, setting[name] if isinstance(setting, dict) else setting)
EOF
)"

# One entry per patch step, by index: its name, the file it belongs to, the text that proves it is already applied,
# the exact unpatched text it expects to find, and what that text becomes. The guarded write keeps the line it had and
# gains one level of indentation, so its replacement carries the deeper indent of its own line. CLASSIFY names the
# steps whose state is more than "the applied text is there or it is not": the configuration field block is the one
# such step, because a file may also declare those fields with values this script did not write.
NAME=('clamp-fix' 'empty-arm-guard' 'configurable-arm-gains-fields' 'configurable-arm-gains-writes')
REL=("$PKG/lekiwi.py" "$PKG/lekiwi.py" "$PKG/config_lekiwi.py" "$PKG/lekiwi.py")
APPLIED=('present_pos[key.removesuffix(".pos")]' 'if arm_goal_pos_raw:' '' 'self.config.arm_p_coefficient')
OLD=('goal_present_pos = {key: (g_pos, present_pos[key]) for key, g_pos in arm_goal_pos.items()}' 'self.bus.sync_write("Goal_Position", arm_goal_pos_raw)' "$GAINS_FIELDS_OLD" "$GAINS_WRITES_OLD")
NEW=('goal_present_pos = {key: (g_pos, present_pos[key.removesuffix(".pos")]) for key, g_pos in arm_goal_pos.items()}' $'if arm_goal_pos_raw:\n            self.bus.sync_write("Goal_Position", arm_goal_pos_raw)' "$GAINS_FIELDS_NEW" "$GAINS_WRITES_NEW")
CLASSIFY=('' '' 'arm-gains' '')
# One empty slot per patch, filled in by the classification with whatever the reader has to be told about that patch.
# It is built from the table rather than written out, so that adding a patch cannot leave it one entry short.
DETAIL=(); for i in "${!NAME[@]}"; do DETAIL[$i]=''; done

# Whether TEXT appears in FILE at least once. python3 rather than grep -F because two of these texts are several lines
# long, and grep -F reads an embedded newline as the start of another pattern, which would match any one of the lines
# on its own.
contains() {
  python3 -c 'import pathlib, sys; sys.exit(0 if sys.argv[2] in pathlib.Path(sys.argv[1]).read_text() else 1)' "$1" "$2"
}

# What config_lekiwi.py declares for the three gain fields, in the words of bin/pi/host_boundary.py, which is also what
# reads them before every host start. One token, then whatever the reader has to be told about it.
config_state() {
  python3 - "$HB" "$1" <<'PY'
import importlib.util
import pathlib
import sys

spec = importlib.util.spec_from_file_location("host_boundary", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
declared, problem = module.arm_gain_configuration(pathlib.Path(sys.argv[2]).read_text())
if problem is not None:
    print("unreadable %s" % problem)
    raise SystemExit(0)
missing = [name for name, _, _ in module.ARM_GAIN_FIELDS if not declared[name]]
if len(missing) == len(module.ARM_GAIN_FIELDS):
    print("absent")
    raise SystemExit(0)
if missing:
    print("partial it declares %s and not %s, so it is neither a stock configuration nor the one recorded here"
          % (sorted(set(declared) - set(missing)), missing))
    raise SystemExit(0)
different = ["%s is %r where the recorded configuration has %r" % (name, declared[name][-1], expected)
             for name, _, expected in module.ARM_GAIN_FIELDS if declared[name][-1] != expected]
if different:
    print("different %s; applying over it would conceal a configuration nobody wrote down" % "; ".join(different))
    raise SystemExit(0)
repeated = {name: declared[name] for name, _, _ in module.ARM_GAIN_FIELDS if len(declared[name]) > 1}
if repeated:
    print("duplicated with a duplicated declaration this script would write once (%s); Python keeps the last, so this "
          "install resolves to the recorded values, and nothing is written to it" % repeated)
    raise SystemExit(0)
print("applied")
PY
}

classify_config() {
  local i="$1"
  local f="$DIR/${REL[$i]}" out
  out="$(config_state "$f")" || { DETAIL[$i]="its configuration could not be read at all"; return 3; }
  case "${out%% *}" in
    applied) DETAIL[$i]=""; return 0 ;;
    duplicated) DETAIL[$i]="${out#* }"; return 0 ;;
    absent)
      contains "$f" "${OLD[$i]}" && { DETAIL[$i]=""; return 1; }
      DETAIL[$i]="it declares none of the three fields and does not carry the stock line they are written after either"
      return 3 ;;
    *) DETAIL[$i]="${out#* }"; return 3 ;;
  esac
}

# 0 this patch is already in the file, 1 the file is the text it expects and the patch is not in it yet, 3 the file is
# neither. The applied text is looked for first because the guard leaves the line it guards in place.
state_of() {
  local i="$1"
  local f="$DIR/${REL[$i]}"
  [ -f "$f" ] || { DETAIL[$i]="$(basename "${REL[$i]}") is not in this checkout"; return 3; }
  if [ -n "${CLASSIFY[$i]}" ]; then
    classify_config "$i"
    return "$?"
  fi
  contains "$f" "${APPLIED[$i]}" && return 0
  contains "$f" "${OLD[$i]}" && return 1
  return 3
}

TODO=()
WORST=0
for i in "${!NAME[@]}"; do
  state_of "$i"; S=$?
  case "$S" in
    0) if [ -n "${DETAIL[$i]}" ]; then echo "${NAME[$i]}: already applied, ${DETAIL[$i]}"; else echo "${NAME[$i]}: already applied"; fi ;;
    1) TODO+=("$i"); [ "$WORST" -lt 1 ] && WORST=1
       [ "$MODE" = "--check" ] && echo "${NAME[$i]}: not applied (run with --apply)" ;;
    *) WORST=3
       echo "${NAME[$i]}: ${DETAIL[$i]:-the expected line is not in $DIR/${REL[$i]}}; this lerobot is not b4e2d0b, nothing applied" >&2 ;;
  esac
done
[ "$MODE" = "--check" ] && exit "$WORST"
# One unrecognized patch site refuses the whole checkout: a half-patched send_action is worse than an unpatched one, a
# configuration this script does not recognize is worse than either, and the operator can see which patch refused.
[ "$WORST" -eq 3 ] && exit 3
[ "${#TODO[@]}" -eq 0 ] && exit 0
for i in "${TODO[@]}"; do
  python3 - "$DIR/${REL[$i]}" "${OLD[$i]}" "${NEW[$i]}" <<'PY' || { echo "${NAME[$i]}: the text it replaces does not appear exactly once in $DIR/${REL[$i]}; stopping here, and any patch written before it in this run is already in the file" >&2; exit 3; }
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
assert s.count(sys.argv[2]) == 1, "expected exactly one occurrence, found %d" % s.count(sys.argv[2])
p.write_text(s.replace(sys.argv[2], sys.argv[3]))
PY
done
for i in "${TODO[@]}"; do
  python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$DIR/${REL[$i]}" || { echo "lerobot-patch: $DIR/${REL[$i]} does not parse after patching; look at it before running the host" >&2; exit 3; }
done
for i in "${TODO[@]}"; do echo "${NAME[$i]}: applied to $DIR/${REL[$i]}"; done
