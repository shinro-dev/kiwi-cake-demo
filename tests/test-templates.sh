#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-templates.sh: the segment-2 templates are refused while a
# placeholder remains outside their comments and accepted once filled as
# documented; the process helpers find a child by parent pid and notice it going.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-test-templates.XXXXXX")"; trap 'rm -rf -- "$T"' EXIT
FAILS=0
cp "$ROOT/bin/templates/run-child.sh.in" "$T/run-child.sh"
cp "$ROOT/bin/templates/safe-stop.sh.in" "$T/safe-stop.sh"
kc_template_has_placeholder "$T/run-child.sh" && echo "ok   unfilled run-child.sh is refused" || { echo "FAIL unfilled run-child.sh accepted"; FAILS=$((FAILS + 1)); }
kc_template_has_placeholder "$T/safe-stop.sh" && { echo "FAIL safe-stop.sh has a placeholder to fill"; FAILS=$((FAILS + 1)); } || echo "ok   safe-stop.sh needs nothing filled"
# Fill exactly the six documented placeholders and nothing else.
sed -i -e 's|@@PYTHON@@|/usr/bin/python3|' -e 's|@@HOST_ENTRY@@|/opt/lerobot/host.py|' -e 's|@@ROBOT_ID@@|kiwi|' \
  -e 's|@@ROBOT_PORT@@|/dev/serial/by-id/example|' -e 's|@@CAMERAS_JSON@@|{}|' -e 's|@@CONNECTION_TIME_S@@|86400|' "$T/run-child.sh"
grep -q '@@' "$T/run-child.sh" && echo "     (comment lines still mention @@PLACEHOLDER@@, as documented)"
kc_template_has_placeholder "$T/run-child.sh" && { echo "FAIL filled run-child.sh still refused"; FAILS=$((FAILS + 1)); } || echo "ok   filled run-child.sh is accepted"
grep -q '^exec /usr/bin/python3 /opt/lerobot/host.py' "$T/run-child.sh" && echo "ok   the wrapper execs the host directly" || { echo "FAIL exec line"; FAILS=$((FAILS + 1)); }
grep -vE '^[[:space:]]*#' "$T/run-child.sh" | grep -q '"\$@"' && { echo "FAIL the wrapper forwards the supervisor positionals"; FAILS=$((FAILS + 1)); } || echo "ok   the wrapper ignores the supervisor positionals"
# The filled example: no placeholder, parses, keeps the safety flags, valid cameras JSON.
EX="$ROOT/bin/templates/run-child.sh.example"
kc_template_has_placeholder "$EX" && { echo "FAIL the example still carries a placeholder"; FAILS=$((FAILS + 1)); } || echo "ok   the example carries no placeholder"
bash -n "$EX" && echo "ok   the example parses" || { echo "FAIL the example does not parse"; FAILS=$((FAILS + 1)); }
grep -vE '^[[:space:]]*#' "$EX" | grep -q '"\$@"' && { echo "FAIL the example forwards the supervisor positionals"; FAILS=$((FAILS + 1)); } || echo "ok   the example ignores the supervisor positionals"
for want in '^exec /' 'lekiwi_host_noninteractive.py' '--robot.max_relative_target=15.0' '--robot.disable_torque_on_disconnect=true' '--host.connection_time_s=86400' '/dev/serial/by-id/'; do
  grep -q -- "$want" "$EX" && echo "ok   example has $want" || { echo "FAIL example lacks $want"; FAILS=$((FAILS + 1)); }
done
CAMS="$(grep -o "robot.cameras='[^']*'" "$EX" | sed "s/^robot.cameras='//; s/'\$//")"
printf '%s' "$CAMS" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d and all(v.get("type")=="opencv" and v["index_or_path"].startswith("/dev/v4l/by-id/") and {"width","height","fps","rotation"} <= set(v) for v in d.values())' \
  && echo "ok   example cameras JSON parses and every entry carries type opencv" || { echo "FAIL example cameras JSON"; FAILS=$((FAILS + 1)); }
# The target slug: one tarball for every aarch64 Pi, none elsewhere.
slug_for() { KC_ARCH="$1" KC_MODEL="$2" KC_PAGE_SIZE="$3" KC_GLIBC="$4" kc_target_slug; }
[ "$(slug_for aarch64 'Raspberry Pi 4 Model B Rev 1.5' 4096 2.41)" = "pi5-aarch64" ] && echo "ok   a Pi 4 maps to the pi5-aarch64 tarball" || { echo "FAIL Pi 4 slug"; FAILS=$((FAILS + 1)); }
[ "$(slug_for aarch64 'Raspberry Pi 5 Model B Rev 1.0' 16384 2.41)" = "pi5-aarch64" ] && echo "ok   a Pi 5 maps to the pi5-aarch64 tarball" || { echo "FAIL Pi 5 slug"; FAILS=$((FAILS + 1)); }
[ -z "$(slug_for x86_64 unknown 4096 2.39)" ] && echo "ok   an x86-64 machine has no tarball" || { echo "FAIL x86-64 slug"; FAILS=$((FAILS + 1)); }
# The user unit: rendered as the runner renders it, every field the stop path rests on present.
U="$T/kiwi-cake-demo.service"
sed -e "s|@@CAKE_RESIDENT@@|$ROOT/tests/mock-bin/cake-resident|" -e "s|@@RESIDENT_CONF@@|$T/resident.conf|" \
  "$ROOT/bin/templates/kiwi-cake-demo.service.in" >"$U"
kc_template_has_placeholder "$U" && { echo "FAIL the rendered unit still carries a placeholder"; FAILS=$((FAILS + 1)); } || echo "ok   the rendered unit carries no placeholder"
for want in "ExecStart=$ROOT/tests/mock-bin/cake-resident --config $T/resident.conf" 'Restart=on-abnormal' 'RestartSec=2' 'KillMode=mixed' 'KillSignal=SIGTERM' 'TimeoutStopSec=60'; do
  grep -qxF -- "$want" "$U" && echo "ok   unit has $want" || { echo "FAIL unit lacks $want"; FAILS=$((FAILS + 1)); }
done
grep -qx 'KillMode=process' "$U" && { echo "FAIL unit uses KillMode=process (cgroup cleanup lost)"; FAILS=$((FAILS + 1)); } || echo "ok   unit does not use KillMode=process"
[ "$(grep -c '^KillMode=' "$U")" -eq 1 ] && echo "ok   KillMode is set exactly once" || { echo "FAIL KillMode is set $(grep -c '^KillMode=' "$U") times"; FAILS=$((FAILS + 1)); }
if command -v systemd-analyze >/dev/null 2>&1; then
  if OUT="$(systemd-analyze --user verify "$U" 2>&1)" || OUT="$(systemd-analyze verify "$U" 2>&1)"; then
    echo "ok   systemd-analyze verify accepts the rendered unit"; [ -z "$OUT" ] || printf '%s\n' "$OUT" | sed 's/^/     /'
  else
    case "$OUT" in
      *"Failed to connect"* | *"No such file"* | *"cgroup"*) echo "SKIP systemd-analyze verify: could not run here ($(printf '%s' "$OUT" | head -n 1))" ;;
      *) echo "FAIL systemd-analyze verify rejected the rendered unit:"; printf '%s\n' "$OUT" | sed 's/^/     /'; FAILS=$((FAILS + 1)) ;;
    esac
  fi
else
  echo "SKIP systemd-analyze: not installed; the rendered unit was not verified by systemd"
fi

# The LeKiwi deployment's templates, rendered as bin/pi/lekiwi-deploy.sh renders them on the robot computer: both
# wrappers refused unfilled, exec'ing an absolute interpreter once filled, the host keeping its two safety flags,
# and the unit keeping the stop path with no [Install] section, so it can never be enabled.
for w in run-host.sh run-capability.sh; do
  cp "$ROOT/bin/templates/$w.in" "$T/$w"
  kc_template_has_placeholder "$T/$w" && echo "ok   unfilled $w is refused" || { echo "FAIL unfilled $w accepted"; FAILS=$((FAILS + 1)); }
done
sed -i -e 's|@@PYTHON@@|/usr/bin/python3|g' -e 's|@@HOST_ENTRY@@|/opt/kc/app/bin/pi/lekiwi_host_safe_attach.py|g' \
  -e 's|@@ROBOT_ID@@|kiwi|g' -e 's|@@ROBOT_PORT@@|/dev/serial/by-id/example|g' -e 's|@@ARM_TOKEN@@|/opt/kc/run/arm-token|g' \
  -e 's|@@FUSER@@|/usr/bin/fuser|g' -e 's|@@SS@@|/usr/bin/ss|g' -e 's|@@FLOCK@@|/usr/bin/flock|g' \
  -e 's|@@WHEELS_LOCK@@|/opt/kc/run/wheels-safe-stop.lock|g' "$T/run-host.sh"
sed -i -e 's|@@PYTHON@@|/usr/bin/python3|g' -e 's|@@APP_DIR@@|/opt/kc/app|g' -e 's|@@RUN_DIR@@|/opt/kc/run|g' "$T/run-capability.sh"
for w in run-host.sh run-capability.sh; do
  kc_template_has_placeholder "$T/$w" && { echo "FAIL filled $w still refused"; FAILS=$((FAILS + 1)); } || echo "ok   filled $w is accepted"
  bash -n "$T/$w" && echo "ok   filled $w parses" || { echo "FAIL filled $w does not parse"; FAILS=$((FAILS + 1)); }
  grep -qxF "PYTHON='/usr/bin/python3'" "$T/$w" && grep -q '^exec "\$PYTHON" -u ' "$T/$w" && echo "ok   $w execs an absolute interpreter" || { echo "FAIL $w exec line"; FAILS=$((FAILS + 1)); }
  grep -vE '^[[:space:]]*#' "$T/$w" | grep -qE '"\$@"|\$[1-9*]' && { echo "FAIL $w forwards the supervisor positionals"; FAILS=$((FAILS + 1)); } || echo "ok   $w ignores the supervisor positionals"
done
for want in '  --robot.max_relative_target=15.0 \' '  --robot.disable_torque_on_disconnect=false \' "  '--robot.cameras={}' \\" '  --host.connection_time_s=86400'; do
  grep -qxF -- "$want" "$T/run-host.sh" && echo "ok   run-host.sh has $want" || { echo "FAIL run-host.sh lacks $want"; FAILS=$((FAILS + 1)); }
done
grep -q -- 'disable_torque_on_disconnect=true' "$T/run-host.sh" && { echo "FAIL run-host.sh drops torque on disconnect"; FAILS=$((FAILS + 1)); } || echo "ok   run-host.sh never drops torque on disconnect"
# The capability wrapper launches the service on contract v2 (TargetObservationV2), once, as a flag of the one exec.
grep -qxF -- '  --contract v2 \' "$T/run-capability.sh" && echo "ok   run-capability.sh launches the service on contract v2" || { echo "FAIL run-capability.sh does not pass --contract v2"; FAILS=$((FAILS + 1)); }
[ "$(grep -c -- '--contract' "$T/run-capability.sh")" -eq 1 ] && echo "ok   run-capability.sh names the contract exactly once" || { echo "FAIL run-capability.sh names the contract $(grep -c -- '--contract' "$T/run-capability.sh") times"; FAILS=$((FAILS + 1)); }
grep -q -- '--contract v1' "$T/run-capability.sh" && { echo "FAIL run-capability.sh still launches v1"; FAILS=$((FAILS + 1)); } || echo "ok   run-capability.sh does not launch v1"
# The one-shot arm token is claimed, the wheel stop's lock taken, and the fail-closed ownership check run, BEFORE the one
# exec, which stays last; the lock's descriptor is never closed on the way, so the exec hands it to the host.
line_of() { grep -nF -- "$1" "$T/run-host.sh" | head -n 1 | cut -d: -f1; }
CLAIM="$(line_of 'mv -T -- "$ARM_TOKEN" "$CLAIMED"')"; LOCK="$(line_of '"$FLOCK" -x -w "$LOCK_WAIT_S" 8')"
OWNER="$(line_of 'fuser_on_target')"; PORTS="$(line_of '*:5555 | *:5556')"
EXECL="$(line_of 'exec "$PYTHON" -u "$HOST_ENTRY"')"
if [ -n "$CLAIM" ] && [ -n "$LOCK" ] && [ -n "$OWNER" ] && [ -n "$PORTS" ] && [ -n "$EXECL" ] && [ "$CLAIM" -lt "$LOCK" ] && [ "$LOCK" -lt "$OWNER" ] && [ "$OWNER" -lt "$PORTS" ] && [ "$PORTS" -lt "$EXECL" ]; then
  echo "ok   run-host.sh claims the arm token, takes the wheel stop's lock, then checks the servo owner and 5555/5556, then execs"
else
  echo "FAIL run-host.sh order: claim line ${CLAIM:-none}, lock ${LOCK:-none}, owner check ${OWNER:-none}, port check ${PORTS:-none}, exec ${EXECL:-none}"; FAILS=$((FAILS + 1))
fi
grep -vE '^[[:space:]]*#' "$T/run-host.sh" | grep -qE '(^|[^0-9])8[<>]&-' && { echo "FAIL run-host.sh closes the lock's descriptor 8 before its exec"; FAILS=$((FAILS + 1)); } || echo "ok   run-host.sh keeps the lock's descriptor 8 open through its exec"
[ "$(grep -c '^exec ' "$T/run-host.sh")" -eq 1 ] && echo "ok   run-host.sh has exactly one exec of a program" || { echo "FAIL run-host.sh exec count"; FAILS=$((FAILS + 1)); }
grep -qxF "ARM_TOKEN='/opt/kc/run/arm-token'" "$T/run-host.sh" && echo "ok   run-host.sh names the rendered arm token" || { echo "FAIL run-host.sh arm token line"; FAILS=$((FAILS + 1)); }
grep -qxF "WHEELS_LOCK='/opt/kc/run/wheels-safe-stop.lock'" "$T/run-host.sh" && grep -qxF "FLOCK='/usr/bin/flock'" "$T/run-host.sh" && echo "ok   run-host.sh names the rendered flock and the wheel stop's lock" || { echo "FAIL run-host.sh flock or lock line"; FAILS=$((FAILS + 1)); }
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -S warning "$T/run-host.sh" "$T/run-capability.sh" && echo "ok   shellcheck accepts both rendered wrappers" || { echo "FAIL shellcheck on the rendered wrappers"; FAILS=$((FAILS + 1)); }
else
  echo "SKIP shellcheck of the rendered wrappers: not installed"
fi
# The safe-stop as the LeKiwi install renders it: its four wheel settings filled in, line for line as lekiwi-deploy.sh's
# render() fills them, and nothing else changed. It parses as POSIX sh, and its hard limit stays under the half second
# after which the supervisor ends a safe-stop by force.
SS="$T/safe-stop-lekiwi.sh"
sed -e "s|^WHEELS_PYTHON=''\$|WHEELS_PYTHON='/usr/bin/python3'|" -e "s|^WHEELS_HELPER=''\$|WHEELS_HELPER='/opt/kc/app/bin/pi/wheels_safe_stop.py'|" \
  -e "s|^WHEELS_PORT=''\$|WHEELS_PORT='/dev/serial/by-id/example'|" -e "s|^WHEELS_LOCK=''\$|WHEELS_LOCK='/opt/kc/run/wheels-safe-stop.lock'|" \
  "$ROOT/bin/templates/safe-stop.sh.in" >"$SS"
for want in "WHEELS_PYTHON='/usr/bin/python3'" "WHEELS_HELPER='/opt/kc/app/bin/pi/wheels_safe_stop.py'" \
  "WHEELS_PORT='/dev/serial/by-id/example'" "WHEELS_LOCK='/opt/kc/run/wheels-safe-stop.lock'"; do
  grep -qxF -- "$want" "$SS" && echo "ok   rendered safe-stop.sh has $want" || { echo "FAIL rendered safe-stop.sh lacks $want"; FAILS=$((FAILS + 1)); }
done
[ "$(grep -E "^WHEELS_LOCK='" "$SS")" = "$(grep -E "^WHEELS_LOCK='" "$T/run-host.sh")" ] && echo "ok   the rendered safe-stop and host wrapper name one lock file" || { echo "FAIL the rendered safe-stop and host wrapper name different lock files"; FAILS=$((FAILS + 1)); }
CHANGED="$(diff "$ROOT/bin/templates/safe-stop.sh.in" "$SS" | grep -c '^>')"
[ "$CHANGED" -eq 4 ] && echo "ok   rendering the safe-stop changes exactly its four wheel settings" || { echo "FAIL rendering the safe-stop changed $CHANGED lines, not 4"; FAILS=$((FAILS + 1)); }
kc_template_has_placeholder "$SS" && { echo "FAIL the rendered safe-stop.sh carries a placeholder"; FAILS=$((FAILS + 1)); } || echo "ok   the rendered safe-stop.sh carries no placeholder"
sh -n "$SS" && echo "ok   the rendered safe-stop.sh parses as sh" || { echo "FAIL the rendered safe-stop.sh does not parse as sh"; FAILS=$((FAILS + 1)); }
LIMIT="$(sed -nE 's/^timeout -s KILL ([0-9.]+) .*/\1/p' "$SS")"
if [ -n "$LIMIT" ] && awk -v l="$LIMIT" 'BEGIN { exit !(l > 0 && l < 0.5) }'; then
  echo "ok   the wheel stop runs under a hard limit of $LIMIT s, under the supervisor's half second"
else
  echo "FAIL the wheel stop's hard limit is '${LIMIT:-missing}', not under 0.5 s"; FAILS=$((FAILS + 1))
fi
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -S warning "$SS" && echo "ok   shellcheck accepts the rendered safe-stop.sh (sh)" || { echo "FAIL shellcheck on the rendered safe-stop.sh"; FAILS=$((FAILS + 1)); }
else
  echo "SKIP shellcheck of the rendered safe-stop.sh: not installed"
fi
U2="$T/kiwi-cake-lekiwi.service"
sed -e "s|@@CAKE_RESIDENT@@|$ROOT/tests/mock-bin/cake-resident|" -e "s|@@RESIDENT_CONF@@|$T/resident.conf|" \
  "$ROOT/bin/templates/kiwi-cake-lekiwi.service.in" >"$U2"
kc_template_has_placeholder "$U2" && { echo "FAIL the rendered LeKiwi unit still carries a placeholder"; FAILS=$((FAILS + 1)); } || echo "ok   the rendered LeKiwi unit carries no placeholder"
for want in "ExecStart=$ROOT/tests/mock-bin/cake-resident --config $T/resident.conf" 'Restart=on-abnormal' 'RestartSec=2' 'KillMode=mixed' 'KillSignal=SIGTERM' 'TimeoutStopSec=60'; do
  grep -qxF -- "$want" "$U2" && echo "ok   LeKiwi unit has $want" || { echo "FAIL LeKiwi unit lacks $want"; FAILS=$((FAILS + 1)); }
done
grep -qE '^\[Install\]|^WantedBy=' "$U2" && { echo "FAIL the LeKiwi unit can be enabled"; FAILS=$((FAILS + 1)); } || echo "ok   the LeKiwi unit has no [Install] section"
if command -v systemd-analyze >/dev/null 2>&1; then
  if OUT="$(systemd-analyze --user verify "$U2" 2>&1)" || OUT="$(systemd-analyze verify "$U2" 2>&1)"; then
    echo "ok   systemd-analyze verify accepts the rendered LeKiwi unit"; [ -z "$OUT" ] || printf '%s\n' "$OUT" | sed 's/^/     /'
  else
    case "$OUT" in
      *"Failed to connect"* | *"No such file"* | *"cgroup"*) echo "SKIP systemd-analyze verify of the LeKiwi unit: could not run here ($(printf '%s' "$OUT" | head -n 1))" ;;
      *) echo "FAIL systemd-analyze verify rejected the rendered LeKiwi unit:"; printf '%s\n' "$OUT" | sed 's/^/     /'; FAILS=$((FAILS + 1)) ;;
    esac
  fi
else
  echo "SKIP systemd-analyze: not installed; the rendered LeKiwi unit was not verified by systemd"
fi

# The remote scripts: shellcheck does not look inside a heredoc, so each quoted heredoc of the two robot-side launchers
# is extracted and checked on its own, with lekiwi-deploy.sh's shared preamble (LIB) in front of its scripts, as it is
# sent. The variables their remote command lines hand them are exported first, and so are the preamble's own paths,
# which only some of the scripts use (without that, shellcheck reports those as unused in every other script).
if command -v shellcheck >/dev/null 2>&1; then
  extract() {  # extract FILE NAME: the body of the heredoc <<'NAME' in FILE
    awk -v n="$2" 'index($0, "<<'"'"'" n "'"'"'") { on = 1; next } on && $0 == n { exit } on { print }' "$1"
  }
  HEAD='#!/bin/bash
export DIR_REL UNIT LEGACY_UNITS PY PORT ROBOT_ID STAMP WAIT_S PYBIN'
  for pair in lekiwi-deploy.sh:CHECK lekiwi-deploy.sh:ARM lekiwi-deploy.sh:STOP lekiwi-deploy.sh:STATUS \
    lekiwi-deploy.sh:PROVE lekiwi-deploy.sh:PROBE lekiwi-deploy.sh:PRE lekiwi-deploy.sh:BUILD lekiwi-deploy.sh:COMMIT \
    start-host.sh:PRE start-host.sh:LOCATE; do
    f="${pair%%:*}"; n="${pair#*:}"; out="$T/remote-${f%.sh}-$n.sh"
    body="$(extract "$ROOT/bin/pi/$f" "$n")"
    [ -n "$body" ] || { echo "FAIL the heredoc $n was not found in bin/pi/$f"; FAILS=$((FAILS + 1)); continue; }
    if [ "$f" = lekiwi-deploy.sh ]; then
      printf '%s\n%s\n%s\n%s\n' "$HEAD" "$(extract "$ROOT/bin/pi/$f" LIB)" 'export KROOT CUR RUN TOKEN UNIT_PATH' "$body" >"$out"
    else
      printf '%s\n%s\n' "$HEAD" "$body" >"$out"
    fi
    shellcheck -S warning "$out" && echo "ok   shellcheck accepts the remote script $n of bin/pi/$f" || { echo "FAIL shellcheck on the remote script $n of bin/pi/$f"; FAILS=$((FAILS + 1)); }
  done
else
  echo "SKIP shellcheck of the remote scripts: not installed"
fi

# Process helpers.
sleep 30 &
CHILD=$!
case " $(kc_children_of $$) " in *" $CHILD "*) echo "ok   kc_children_of finds the child by parent pid" ;; *) echo "FAIL kc_children_of"; FAILS=$((FAILS + 1)) ;; esac
kill "$CHILD"; wait "$CHILD" 2>/dev/null
kc_wait_gone "$CHILD" 100 && echo "ok   kc_wait_gone sees the child leave" || { echo "FAIL kc_wait_gone"; FAILS=$((FAILS + 1)); }
[ "$FAILS" -eq 0 ] && echo "test-templates: PASS" || { echo "test-templates: $FAILS failure(s)"; exit 1; }
