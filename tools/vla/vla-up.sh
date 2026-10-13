#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# vla-up.sh start|stop|status: the robot-side VLA runner (vla/runner.py) in STANDBY, detached on the robot so neither
# this shell nor the console API owns it, and a loopback forward 127.0.0.1:5593 -> the robot's 127.0.0.1:5593 for the
# console API. The runner loads nothing until the console's Swap. stop asks it to shut down (it stops a running episode
# first, the arm holding) and closes the forward.
set -euo pipefail
: "${KC_PI_SSH:?set KC_PI_SSH (source the console env file)}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SHA=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["model_sha256"])' "$ROOT/vla/envelope.json")
PORT=5593
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 "$KC_PI_SSH")
fwd_pid() { pgrep -f -- "ssh -N .*-L 127.0.0.1:$PORT:127.0.0.1:$PORT" || true; }
case "${1:-}" in
  start)
    "${SSH[@]}" "SHA=$SHA bash -s" <<'EOF'
set -euo pipefail
if pgrep -f "[v]la.runner" >/dev/null; then echo "runner already running: pid $(pgrep -f '[v]la.runner')"; exit 0; fi
REL=$(sed -nE 's/^WorkingDirectory=(.*)$/\1/p' ~/.config/systemd/user/kiwi-cake-runtime.service | sed "s#%h#$HOME#")
[ -d "$REL" ] || { echo "REFUSE: the runtime release directory was not found"; exit 3; }
cd ~/vla-dev
LOG=~/vla-dev/runner-$(date -u +%Y%m%dT%H%M%SZ).log
PYTHONPATH=~/vla-dev/app:~/vla-dev/lerobot-src HF_HOME=~/vla-dev/hf HF_HUB_OFFLINE=1 \
  setsid -f nohup ~/vla-dev/venv/bin/python -u -m vla.runner --checkpoint ~/vla-dev/models/"$SHA" --runtime-release "$REL" \
  >"$LOG" 2>&1 </dev/null
for _ in $(seq 1 40); do grep -q '"listening"' "$LOG" && break; sleep 0.25; done
grep -q '"listening"' "$LOG" || { echo "RUNNER DID NOT START"; tail -n 20 "$LOG"; exit 1; }
echo "runner up in standby: pid $(pgrep -f '[v]la.runner')"
EOF
    [ -n "$(fwd_pid)" ] || setsid -f nohup ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 \
      -L "127.0.0.1:$PORT:127.0.0.1:$PORT" "$KC_PI_SSH" >/dev/null 2>&1 </dev/null
    sleep 1
    if [ -n "$(fwd_pid)" ]; then echo "forward up: 127.0.0.1:$PORT"; else echo "the forward did not start"; exit 1; fi ;;
  stop)
    python3 - "$PORT" <<'EOF' || true
import json, sys, zmq
s = zmq.Context.instance().socket(zmq.REQ); s.setsockopt(zmq.RCVTIMEO, 20000); s.setsockopt(zmq.LINGER, 0)
s.connect(f"tcp://127.0.0.1:{sys.argv[1]}"); s.send(json.dumps({"op": "shutdown"}).encode()); print(s.recv().decode())
EOF
    for p in $(fwd_pid); do kill "$p"; done; echo "stopped" ;;
  status)
    echo "forward: $(fwd_pid)"; "${SSH[@]}" 'pgrep -af "[v]la.runner" || echo "runner: none"' ;;
  *) echo "usage: vla-up.sh start|stop|status"; exit 2 ;;
esac
