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
# 1. claims rows: every citation in the walkthrough, the positioning page and the
#    README names a row that exists; the walkthrough and the positioning page cite
#    at least one. Citations may wrap across a line break, so each document is read
#    with its lines joined.
ROWS="$(grep -oE '^\| [0-9]+ \|' "$ROOT/docs/claims.md" | tr -dc '0-9\n' | sort -n | paste -sd ' ')"
cited_rows() { tr '\n' ' ' | grep -oE 'claims\.md`?[^0-9]{0,4}row [0-9]+' | grep -oE '[0-9]+$' | sort -un | paste -sd ' '; }
# The nonzero control: a constructed citation of a row that does not exist must be found by the scan.
case " $ROWS " in *" 99 "*) echo "FAIL docs/claims.md has a row 99; the control below needs a row that does not exist"; FAILS=$((FAILS + 1)) ;; esac
[ "$(printf 'see `docs/claims.md`\nrow 99 here\n' | cited_rows)" = "99" ] || { echo "FAIL the citation scan did not find the control citation"; FAILS=$((FAILS + 1)); }
for doc in docs/demo-walkthrough.md docs/why-cake-under-lerobot.md README.md; do
  [ -f "$ROOT/$doc" ] || { echo "FAIL $doc is missing"; FAILS=$((FAILS + 1)); continue; }
  CITED="$(cited_rows <"$ROOT/$doc")"
  for r in $CITED; do
    case " $ROWS " in *" $r "*) : ;; *) echo "FAIL $doc cites claims row $r but docs/claims.md has no such row"; FAILS=$((FAILS + 1)) ;; esac
  done
  if [ -z "$CITED" ] && [ "$doc" != "README.md" ]; then
    echo "FAIL $doc cites no claims row at all"; FAILS=$((FAILS + 1))
  else
    echo "ok   $doc: every cited claims row exists (rows cited: ${CITED:-none})"
  fi
done
# The positioning page states the two corrected facts and no longer states the retracted ones.
Y="$ROOT/docs/why-cake-under-lerobot.md"
for want in 'Message fetching failed' 'Cycle time reached.' 'lerobot-teleoperate' 'nothing here stops the wheels' 'a supervisor admitted only from a signed package'; do
  grep -qF -- "$want" "$Y" && echo "ok   why-cake states: $want" || { echo "FAIL why-cake does not state: $want"; FAILS=$((FAILS + 1)); }
done
for gone in 'no supervision and no recovery' 'needs no modification' 'the wheels stop because the host stops them' 'a host that runs only from a signed package'; do
  if grep -qF -- "$gone" "$Y"; then echo "FAIL why-cake still says: $gone"; FAILS=$((FAILS + 1)); fi
done
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
# 5. every "bin/<script> --flag" the walkthrough mentions is a flag that script parses
NFL=0
while IFS= read -r m; do
  script="${m%% *}"; flags="${m#* }"
  for flag in $flags; do
    NFL=$((NFL + 1))
    grep -qE -- "(^|[[:space:]|])$flag\)|add_argument\(\"$flag\"" "$ROOT/$script" || { echo "FAIL $script does not accept $flag"; FAILS=$((FAILS + 1)); }
  done
done < <(grep -oE 'bin/[A-Za-z0-9_./-]+\.(sh|py)( --[a-z-]+( [0-9]+)?)+' "$W" | sed -E 's/ [0-9]+//g' | sort -u)
echo "ok   $NFL script flags mentioned are parsed by their scripts"
# 6. the stopping document and SAFETY.md state the unit's own stop settings
U="$ROOT/bin/templates/kiwi-cake-demo.service.in"; SD="$ROOT/docs/stopping-and-cleanup.md"
for key in KillMode TimeoutStopSec; do
  val="$(sed -nE "s/^$key=(.*)$/\\1/p" "$U" | head -n 1)"
  if [ -z "$val" ]; then echo "FAIL the unit template sets no $key"; FAILS=$((FAILS + 1))
  elif grep -qF -- "\`$key=$val\`" "$SD"; then echo "ok   docs/stopping-and-cleanup.md states $key=$val as the unit does"
  else echo "FAIL docs/stopping-and-cleanup.md does not state \`$key=$val\`"; FAILS=$((FAILS + 1)); fi
done
grep -qF 'signals the resident only' "$ROOT/SAFETY.md" && echo "ok   SAFETY.md says systemd's stop signals the resident only" || { echo "FAIL SAFETY.md lacks the resident-only stop sentence"; FAILS=$((FAILS + 1)); }
[ "$FAILS" -eq 0 ] && echo "test-walkthrough: PASS" || { echo "test-walkthrough: $FAILS failure(s)"; exit 1; }
