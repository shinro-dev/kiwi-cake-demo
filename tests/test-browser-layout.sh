#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# RENDERED-PAGE REGRESSION: STABLE POSITIONS (operator layout ruling 2026-09-28; docs/autonomous-demo/
# console-redesign-2026-09-27.md, section 2.1).
#
# The defect this exists for: the mission card grew with the account of a run that ended (what the robot did, why it
# stopped, where the robot was left), and the stages, the scene and the wrist camera under it dropped by 580 px at
# 1440 x 900 and by 1007 px at 390 px, off the first screen at the moment the operator looks for where the block is.
# They also moved when a run started. Now the mission card and Cake keep one footprint and the account scrolls within
# the card.
#
# One page, in a real headless Chrome, watches one synthetic stage walk through the three states: idle (yesterday's
# FAILED mission, which is History's), a run of this session running, and that run ended FAILED with the long account.
# 1039 x 1030 is the narrow layout at the width the verifier's real Chrome had, where the top bar's rows are closest to
# rewrapping. Each width is watched twice: from "Not ready" (robot motion held) and from Ready (motion permitted, no readiness
# sentence), the normal state before Start on the robot; the bar grew 55 px at Start from Ready (verify-r5 M1).
# A probe samples the page y of every region once a second. The script asserts, at 1440 x 900, 1039 x 1030 and 390 x 844:
#   - all three states were seen;
#   - the strip (stages, scene, wrist) and the mission card and Cake keep their top within 4 px across every sample,
#     at 390 px too, where the strip stacks and the scene keeps one height;
#   - at 1440 the scene and the wrist sit beside the stages, and their top is on the first screen;
#   - the ended account holds what the robot did, why it stopped, where the robot was left and the steps, and when it
#     is longer than the card, the card says there is more below;
#   - Stop is the element under its own centre in every sample, and its top holds within 4 px;
#   - the idle state read the readiness the stage set up (Ready or Not ready).
#
# It is not in tests/run-all.sh: it needs Chrome. Run it by hand, from the repository root. The stage is the
# console-api tests' StubRuntime behind the real console_api on a free loopback port; nothing talks to a robot, and
# nothing is sent to the operator's consoles on 8770 or 8771.
#
# usage: bash tests/test-browser-layout.sh
#   KC_WEB_BUILD   the built console to serve (default apps/console-web/build)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WORK="$(mktemp -d)"
STAGE_PID=""
cleanup() {
  [ -n "$STAGE_PID" ] && kill "$STAGE_PID" 2>/dev/null || true
  rm -rf "$WORK" 2>/dev/null || true
}
trap cleanup EXIT

fail() { echo "FAIL $*"; exit 1; }

command -v node >/dev/null 2>&1 || fail "node is not on PATH; this suite needs node 22"
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[ "$NODE_MAJOR" -ge 22 ] || fail "node $NODE_MAJOR is too old; use node 22 (nvm use)"
[ -x "${KC_CHROME:-/opt/google/chrome/chrome}" ] || fail "no chrome at ${KC_CHROME:-/opt/google/chrome/chrome}; set KC_CHROME"
WEB_BUILD="${KC_WEB_BUILD:-apps/console-web/build}"
[ -d "$WEB_BUILD" ] || fail "$WEB_BUILD is missing; run: pnpm -C apps/console-web build"

export PYTHONPATH="apps/console-api:packages/demo-runtime:packages/demo-contracts/python"
free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'; }

# One viewport: start a fresh stage (idle now, running after 14 s, ended after 26 s), watch it for 36 s, then judge.
check() {
  local width="$1" height="$2" start="$3" port cdp out
  port="$(free_port)"
  cdp="$(free_port)"
  out="$WORK/probe-$width-$start.txt"
  python3 tests/browser-layout/stage.py "$port" "$WEB_BUILD" 14 26 "$start" > "$WORK/stage-$width.log" 2>&1 &
  STAGE_PID=$!
  for _ in $(seq 1 40); do curl -sf "http://127.0.0.1:$port/" -o /dev/null && break; sleep 0.25; done
  curl -sf "http://127.0.0.1:$port/" -o /dev/null || fail "the stage never answered; see $WORK/stage-$width.log"
  node tools/cdp_page.mjs --url "http://127.0.0.1:$port/" --wait Timeline --width "$width" --height "$height" --port "$cdp" \
    --script tests/browser-layout/sample.js --after 36000 > "$out" || fail "the page never loaded at $width px"
  kill "$STAGE_PID" 2>/dev/null || true
  wait "$STAGE_PID" 2>/dev/null || true
  STAGE_PID=""
  python3 - "$out" "$width" "$start" <<'PY'
import json, sys
text = open(sys.argv[1]).read()
width = int(sys.argv[2])
start = sys.argv[3]
line = next((l for l in text.splitlines() if l.startswith("PROBE ")), None)
if line is None:
    sys.exit(f"FAIL {width}: no probe line in the page")
rows = json.loads(line[len("PROBE "):])
def state(r):
    if r["past"]: return "idle"
    if r["verdict"] == "failed": return "ended"
    if r["verdict"] is not None and r["verdict"] != "none": return "running"
    return None
read = [r for r in rows if state(r) is not None]
seen = {state(r) for r in read}
problems = []
if seen != {"idle", "running", "ended"}:
    problems.append(f"states seen {sorted(seen)}, want idle, running and ended")
# EVERY REGION IS HELD AT EVERY WIDTH: at 390 px the strip stacks, and the wrist camera under the scene moved with the
# scene's caption and legend (verify-r5 browser must_fix 1) until the scene kept one height below 1100 px.
held = ("mission", "cake", "stages", "scene", "wrist")
for region in held:
    tops = [r["at"].get(region) for r in read]
    if None in tops:
        problems.append(f"{region} missing in a sample")
        continue
    if max(tops) - min(tops) > 4:
        by = {s: sorted({r["at"][region] for r in read if state(r) == s}) for s in ("idle", "running", "ended")}
        problems.append(f"{region} moved {max(tops) - min(tops)} px: {by}")
idle = [r for r in read if state(r) == "idle"]
if idle and not all(r["readiness"] == start for r in idle):
    problems.append(f"idle read readiness {sorted({str(r['readiness']) for r in idle})}, the stage set up {start}")
stops = [r["stopTop"] for r in read if r["stopTop"] is not None]
if stops and max(stops) - min(stops) > 4:
    problems.append(f"Stop moved {max(stops) - min(stops)} px: {sorted(set(stops))}")
for r in read:
    if not r["stopHit"]:
        problems.append(f"Stop was not the element under its centre ({state(r)})")
        break
if width >= 1100:
    for r in read:
        if not (r["at"]["stages"] == r["at"]["scene"] == r["at"]["wrist"]):
            problems.append(f"at {width} the scene and the wrist are not beside the stages: {r['at']}")
            break
    ended = [r for r in read if state(r) == "ended"]
    if ended and not all(r["at"]["scene"] < r["innerHeight"] and r["at"]["wrist"] < r["innerHeight"] for r in ended):
        problems.append(f"at {width} the scene or the wrist is off the first screen after the end: {ended[-1]['at']}")
ended = [r for r in read if state(r) == "ended"]
if ended:
    last = ended[-1]["account"]
    if last is None:
        problems.append("no bounded account in the ended card")
    else:
        for key in ("did", "why", "where", "steps"):
            if not last[key]:
                problems.append(f"the ended account lacks {key}")
        if last["scroll"] > last["client"] + 2 and not last["more"]:
            problems.append("the account is longer than the card and the card does not say there is more below")
summary = {s: {k: sorted({r["at"][k] for r in read if state(r) == s}) for k in ("mission", "cake", "stages", "scene", "wrist")} for s in ("idle", "running", "ended")}
print(f"{width} from {start}: samples {len(read)}, tops by state {json.dumps(summary)}")
if problems:
    for p in problems:
        print(f"FAIL {width} from {start}: {p}")
    sys.exit(1)
print(f"PASS {width} from {start}: stable positions across idle, running and ended")
PY
}

check 1440 900 not_ready
check 1440 900 ready
check 1039 1030 not_ready
check 1039 1030 ready
check 390 844 not_ready
check 390 844 ready
echo "PASS test-browser-layout"
