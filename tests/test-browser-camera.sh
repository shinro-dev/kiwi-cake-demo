#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# RENDERED-PAGE REGRESSION: a camera that shows the same picture over and over must not take Stop away.
#
# The defect this exists for. A camera panel says LIVE only over a picture its own <img> decoded, and it notices a
# stream that ended by sampling the decoded pixels: a picture that stops changing while the runtime keeps reporting new
# frames is an attempt that has ended, and a new attempt, a new element and a new request, is made after a backoff.
# The dropped element was taken off the page with its `src` still set, and Chrome keeps such a multipart request open.
# The page holds four long-lived connections for as long as it is open (the event stream, the telemetry stream and
# two cameras) and Chrome allows six to one server, so two dropped attempts were enough: every later request queued,
# a snapshot read timed out, and Stop sat on "Stopping" with no receipt. MEASURED in the real build, before the fix.
#
# THE SCENE IS A FROZEN CAMERA. A stub stage publishes the SAME picture bytes for both roles while the frame metadata
# keeps advancing, which is what a camera looking at a black lens cap, or a stalled capture, can look like from here.
# The page is left alone long enough for each camera to be dropped and retried at least three times, which is more
# than the spare connections the leak needed. Then, in the page itself: a snapshot read must answer, and a press of
# Stop must come back with a receipt the API gave (taken or refused), not with "got no answer".
#
# THE STAGE IS THE REAL API OVER A STUB. It is apps/console-api's own app, over the StubRuntime its tests use, answering
# like an observe-only LIVE runtime (commands blocked, no mission surface), and its pictures are drawn by
# tools/fake_preview_publisher.py, which writes SYNTHETIC on every one. No daemon, no resident, no robot, no camera
# device and no hardware command. Everything is on ephemeral ports and in a temporary directory, and is torn down.
#
# It is not in tests/run-all.sh on purpose: it needs Chrome, python3 with aiohttp, numpy and cv2, and about a minute
# and a half. Run it by hand, from the repository root, against a build written OUTSIDE apps/console-web/build:
#
#   pnpm -C apps/console-web exec vite build --outDir "$TMPDIR/kc-web" --emptyOutDir
#   KC_WEB_BUILD="$TMPDIR/kc-web" bash tests/test-browser-camera.sh
#
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
[ -d "$WEB_BUILD" ] || fail "$WEB_BUILD is missing; build the console into a directory of its own and set KC_WEB_BUILD"

free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'; }
PORT="$(free_port)"
CDP_PORT="$(free_port)"

# The stage. Written here, not committed beside the suite, because nothing else runs it.
cat > "$WORK/stage.py" <<'PY'
import base64, os, sys, threading, time
REPO, PORT, WEB = sys.argv[1], int(sys.argv[2]), sys.argv[3]
sys.path[:0] = [os.path.join(REPO, "apps/console-api/tests"), os.path.join(REPO, "apps/console-api"),
                os.path.join(REPO, "packages/demo-runtime"), os.path.join(REPO, "packages/demo-contracts/python"),
                os.path.join(REPO, "tools")]
import support
from aiohttp import web
from console_api.app import Options, build_app
from demo_runtime import read_models
from demo_runtime.journal import RUNTIME_VERSION
from fake_preview_publisher import frame_jpeg


class FrozenCameraRuntime(support.StubRuntime):
    """An observe-only LIVE runtime whose two cameras keep delivering the same picture."""

    def __init__(self):
        super().__init__(data_mode="live")
        refusal = {"ok": False, "reason_code": "mission_unavailable", "detail": "rehearsal_requires_synthetic_mode"}
        self.mission_state_answer = dict(refusal)
        self.mission_candidates_answer = dict(refusal)
        self.mission_start_answer = dict(refusal)
        self.robot_status_answer = support.robot_status_object(reachable=True)
        self._latest, self._count, self._cond = [], 0, threading.Condition()

    def snapshot(self):
        stats = read_models.SessionStats()
        return read_models.build_snapshot(
            runtime_session=self.runtime_session, seq_cursor=1, retained_from_seq=1, data_mode="live",
            link={"state": "unknown", "source_stamp": None, "receive_age_ms": None},
            owner={"kind": "free", "run_id": None, "lease": None}, active_run=None, last_outcome=None,
            capability_list=read_models.capabilities(block_code="hardware_commands_not_enabled", run_active=False, replay_only=False),
            component_list=read_models.components(stats=stats, run_active=False, last_exit=None, replay_only=False, runtime_version=RUNTIME_VERSION),
            cake=read_models.cake_view({"artifacts": False, "resident": False, "admin_socket": False}), evidence=stats.evidence())

    def publish(self, record):
        with self._cond:
            self._count += 1
            self._latest = (self._latest + [(self._count, record)])[-8:]
            self._cond.notify_all()

    def telemetry(self, *, idle_timeout_s=None):
        # Latest-only per subscriber, like the runtime: a new subscriber starts at the newest record.
        with self._cond:
            seen = self._count
        while True:
            with self._cond:
                pending = [record for index, record in self._latest if index > seen]
                if not pending:
                    self._cond.wait(timeout=0.05)
                    continue
                seen = self._count
            yield from pending


stub = FrozenCameraRuntime()
SAME_PICTURE = {role: base64.b64encode(frame_jpeg(role, 1)).decode("ascii") for role in ("wrist", "front")}


def publisher():
    seq = 0
    while True:
        seq += 1
        for role in ("wrist", "front"):
            stub.publish({f"preview:{role}": {
                "role": role, "state": "connected", "available": True, "reason": None, "seq": seq, "source_seq": seq,
                "capture_clock": {"session": "frozen-camera-0001", "seq": seq, "sample_mono_ns": seq * 125_000_000, "clock_domain": "pi_monotonic"},
                "receive_age_ms": 12.0, "measured_fps": 8.0, "content_type": "image/jpeg", "bytes_b64": SAME_PICTURE[role]}})
        time.sleep(0.125)


threading.Thread(target=publisher, daemon=True).start()
app = build_app(client=stub, credential=support.CREDENTIAL, web_build=WEB, host="127.0.0.1", port=PORT, options=Options())
web.run_app(app, host="127.0.0.1", port=PORT, access_log=None, print=None, shutdown_timeout=1.0)
PY

python3 "$WORK/stage.py" "$ROOT" "$PORT" "$WEB_BUILD" > "$WORK/stage.log" 2>&1 &
STAGE_PID=$!
for _ in $(seq 1 60); do curl -sf "http://127.0.0.1:$PORT/" -o /dev/null && break; sleep 0.25; done
curl -sf "http://127.0.0.1:$PORT/" -o /dev/null || fail "the stage never answered; see $WORK/stage.log"

# What the page does, once it has drawn: watch both cameras' attempts for 48 s, then read a snapshot and press Stop,
# and write what came back as one line of text, which cdp_page.mjs prints with the rest of the page.
cat > "$WORK/regression.js" <<'JS'
(() => {
  const attempts = { wrist: 0, front: 0 };
  const watch = setInterval(() => {
    for (const panel of document.querySelectorAll('[data-camera-panel]')) {
      const role = panel.getAttribute('data-camera-panel');
      const image = panel.querySelector('img[data-camera-stream]');
      const match = image === null ? null : /[?&]attempt=([0-9]+)/.exec(image.getAttribute('src') || '');
      if (match !== null && role in attempts) attempts[role] = Math.max(attempts[role], Number(match[1]));
    }
  }, 250);
  const out = document.createElement('pre');
  out.style.cssText = 'position:fixed;left:0;bottom:0;width:1px;height:1px;overflow:hidden;margin:0';
  document.body.appendChild(out);
  setTimeout(() => {
    const started = performance.now();
    const ping = Promise.race([
      fetch('/api/snapshot', { cache: 'no-store' }).then((response) => `answered ${String(response.status)}`),
      new Promise((resolve) => setTimeout(() => resolve('TIMEOUT'), 4000)),
    ]).then((word) => `${word} after ${String(Math.round(performance.now() - started))} ms`);
    const stop = document.querySelector('[data-control="stop"]');
    if (stop !== null) stop.click();
    setTimeout(async () => {
      clearInterval(watch);
      const receipt = document.querySelector('[data-receipt="stop"]');
      out.textContent = `CAMERA_REGRESSION ${JSON.stringify({
        attempts,
        ping: await ping,
        stop_pressed: stop !== null,
        stop_label: stop === null ? null : stop.innerText.trim(),
        stop_receipt: receipt === null ? null : receipt.innerText.trim(),
      })}`;
    }, 20000);
  }, 48000);
  return true;
})();
JS

# The connections the server holds, sampled while the page runs, for the record. It is not asserted: Chrome keeps idle
# connections open for reuse, so a count cannot tell a leaked stream from a spare socket. The page's own requests can.
( for _ in $(seq 1 16); do sleep 5; ss -Htn state established "( sport = :$PORT )" 2>/dev/null | wc -l; done ) > "$WORK/established.txt" 2>/dev/null &
SAMPLER=$!

node tools/cdp_page.mjs --url "http://127.0.0.1:$PORT/" --wait "Stop ends the program in software" --script "$WORK/regression.js" \
  --after 72000 --port "$CDP_PORT" --screenshot "$WORK/frozen.png" > "$WORK/page.txt" 2>&1 \
  || fail "the page never drew the screen; see $WORK/page.txt"
wait "$SAMPLER" 2>/dev/null || true

LINE="$(grep -o 'CAMERA_REGRESSION {.*}' "$WORK/page.txt" | head -1 | cut -d' ' -f2-)"
[ -n "$LINE" ] || fail "the page wrote no result line; see $WORK/page.txt"
echo "     page: $LINE"
echo "     server-side established connections, every 5 s: $(tr '\n' ' ' < "$WORK/established.txt")"

python3 - "$LINE" <<'PY' || fail "see the line above"
import json, sys
result = json.loads(sys.argv[1])
problems = []
for role in ("wrist", "front"):
    if result["attempts"].get(role, 0) < 3:
        problems.append(f"the {role} camera reached only attempt {result['attempts'].get(role)}, so the frozen scene was not exercised")
if not result["ping"].startswith("answered 200"):
    problems.append(f"a snapshot read after the retries did not answer: {result['ping']}")
if not result["stop_pressed"]:
    problems.append("there was no Stop control to press")
receipt = result["stop_receipt"] or ""
if not receipt:
    problems.append(f"Stop gave no receipt 20 s after the press (the button reads {result['stop_label']!r})")
elif "got no answer" in receipt:
    problems.append(f"Stop's request got no answer: {receipt}")
elif "Stop taken" not in receipt and "Stop refused" not in receipt:
    problems.append(f"Stop's receipt is not an answer from the API: {receipt}")
if result["stop_label"] != "Stop":
    problems.append(f"the Stop button still reads {result['stop_label']!r}")
for problem in problems:
    print(f"FAIL {problem}")
sys.exit(1 if problems else 0)
PY

echo "ok   after both cameras were dropped and retried on a frozen picture, a snapshot read answered and Stop came back with a receipt"
echo "test-browser-camera: PASS"
