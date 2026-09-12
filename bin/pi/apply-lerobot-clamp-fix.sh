#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# apply-lerobot-clamp-fix.sh: apply lerobot's own fix f66e512 (issue 4309)
# to a lerobot checkout at commit b4e2d0b, where LeKiwi.send_action raises
# KeyError on every command whenever --robot.max_relative_target is set.
# The fix is one line: the present positions are keyed by bare motor name,
# the goal by "<motor>.pos". Idempotent; a checkout that already carries
# the fixed line (b4e2d0b patched, or any commit at or after f66e512) is
# reported as already applied; a file without either line is refused.
#
#   bin/pi/apply-lerobot-clamp-fix.sh --check LEROBOT_DIR   exit 0 applied, 1 not yet, 3 unknown file
#   bin/pi/apply-lerobot-clamp-fix.sh --apply LEROBOT_DIR   exit 0 applied (now or before), 3 unknown file
set -uo pipefail
MODE="${1:-}"; DIR="${2:-}"
[ "$MODE" = "--check" ] || [ "$MODE" = "--apply" ] || { echo "usage: $0 --check|--apply LEROBOT_DIR" >&2; exit 5; }
F="$DIR/src/lerobot/robots/lekiwi/lekiwi.py"
[ -f "$F" ] || { echo "clamp-fix: $F does not exist" >&2; exit 5; }
OLD='goal_present_pos = {key: (g_pos, present_pos[key]) for key, g_pos in arm_goal_pos.items()}'
NEW='goal_present_pos = {key: (g_pos, present_pos[key.removesuffix(".pos")]) for key, g_pos in arm_goal_pos.items()}'
if grep -qF -- 'present_pos[key.removesuffix(".pos")]' "$F"; then echo "clamp-fix: already applied"; exit 0; fi
if ! grep -qF -- "$OLD" "$F"; then echo "clamp-fix: the expected line is not in $F; this lerobot is not b4e2d0b, nothing applied" >&2; exit 3; fi
[ "$MODE" = "--check" ] && { echo "clamp-fix: not applied (run with --apply)"; exit 1; }
python3 - "$F" "$OLD" "$NEW" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
assert s.count(sys.argv[2]) == 1
p.write_text(s.replace(sys.argv[2], sys.argv[3]))
PY
python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$F" && echo "clamp-fix: applied to $F"
