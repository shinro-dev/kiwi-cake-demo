#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-walkthrough.sh: docs/demo-walkthrough.md stays traceable. Every
# cited claims row exists, every capability block carries its four labels,
# every quoted output fragment is printed or matched by a shipped script or
# listed in the event registry, and the README's machine table (when present)
# names the scripts it must.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
W="$ROOT/docs/demo-walkthrough.md"
FAILS=0
[ -f "$W" ] || { echo "test-walkthrough: $W is missing"; exit 1; }
# 1. claims rows
ROWS="$(grep -oE '^\| [0-9]+ \|' "$ROOT/docs/claims.md" | tr -dc '0-9\n' | sort -n | paste -sd ' ')"
# Citations may wrap across a line break, so the document is read with its lines joined.
CITED="$(tr '\n' ' ' <"$W" | grep -oE 'claims\.md`?[^0-9]{0,4}row [0-9]+' | grep -oE '[0-9]+$' | sort -un | paste -sd ' ')"
for r in $CITED; do
  case " $ROWS " in *" $r "*) : ;; *) echo "FAIL claims row $r is cited but docs/claims.md has no such row"; FAILS=$((FAILS + 1)) ;; esac
done
[ -n "$CITED" ] || { echo "FAIL the walkthrough cites no claims row at all"; FAILS=$((FAILS + 1)); }
echo "ok   every cited claims row exists (rows cited: $CITED)"
# 2. the four labels per capability block
NB=0
while IFS= read -r n; do
  NB=$((NB + 1))
  block="$(awk -v n="$n" '$0 ~ "^### " n "\\. " {p=1; next} p && /^(###|## )/ {exit} p' "$W")"
  for label in 'Proves:' 'Shown by:' 'Point at:' 'Caveat:'; do
    printf '%s\n' "$block" | grep -q "^$label" || { echo "FAIL block $n lacks the label $label"; FAILS=$((FAILS + 1)); }
  done
done < <(grep -oE '^### [0-9]+\.' "$W" | tr -dc '0-9\n')
echo "ok   $NB capability blocks checked for their four labels"
# 3. quoted output fragments exist in a shipped script or the event registry
SOURCES=("$ROOT/bin/demo-segment1.sh" "$ROOT/bin/demo-segment2.sh" "$ROOT/bin/telemetry.sh" "$ROOT/bin/demo-stop.sh" "$ROOT/bin/laptop/teleop.py" "$ROOT/docs/event-codes.md")
for f in "$ROOT"/tests/mock-bin/*; do [ -f "$f" ] && SOURCES+=("$f"); done
NF=0
# A "Point at:" block is joined into one line (literals wrap across lines), then each
# backticked literal is split at every <placeholder> and at "..." into the fragments
# the script must print verbatim.
while IFS= read -r lit; do
  while IFS= read -r frag; do
    [ "${#frag}" -ge 12 ] || continue
    NF=$((NF + 1))
    grep -qF -- "$frag" "${SOURCES[@]}" || { echo "FAIL fragment not printed by any shipped script: [$frag]"; FAILS=$((FAILS + 1)); }
  done < <(printf '%s\n' "$lit" | sed -E 's/<[^>]*>/\n/g; s/\.\.\./\n/g')
done < <(awk '/^Point at:/ {if (p) print ""; p=1} p && /^(Proves:|Shown by:|Caveat:|###|## )/ {p=0; print ""} p {printf "%s ", $0}' "$W" | grep -oE '`[^`]+`' | tr -d '`')
echo "ok   $NF output fragments (12+ characters) found in the scripts or the registry"
# 4. the README's machine table names the required scripts
if grep -q '^## Which machine runs what' "$ROOT/README.md"; then
  tbl="$(awk '/^## Which machine runs what/ {p=1; next} p && /^## / {exit} p' "$ROOT/README.md")"
  for want in fetch-release.sh doctor.sh demo-segment1.sh demo-segment2.sh demo-stop.sh run-child.sh safe-stop.sh teleop.py lerobot b4e2d0b; do
    printf '%s\n' "$tbl" | grep -q -- "$want" || { echo "FAIL the README machine table does not name $want"; FAILS=$((FAILS + 1)); }
  done
  echo "ok   the README machine table names every required script"
else
  echo "     (README has no 'Which machine runs what' section yet; skipped)"
fi
[ "$FAILS" -eq 0 ] && echo "test-walkthrough: PASS" || { echo "test-walkthrough: $FAILS failure(s)"; exit 1; }
