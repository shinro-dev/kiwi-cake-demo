# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""console_api.vla: the VLA beat's door, a FORWARD like every other route here: it hands one JSON operation to the
robot-side VLA runner (vla/runner.py) through a loopback port that an ssh forward connects to the robot, and returns
the runner's answer. It decides nothing, starts nothing, and holds nothing between requests: the runner on the robot
is the authority for whether a swap, a run or a stop is taken."""
import asyncio
import json
from typing import Optional

OPS = ("status", "swap", "unswap", "run", "stop", "to_mission_pose", "carry")


class VlaForward:
    # The runner answers a press only after reading the runtime, which it may read twice (vla/runner.py
    # RUNTIME_READ_TIMEOUT_S, RUNTIME_READ_ATTEMPTS): this forward waits for that, and a little more.
    DEFAULT_TIMEOUT_S = 12.0

    def __init__(self, endpoint: str, timeout_s: float = DEFAULT_TIMEOUT_S):
        if not (endpoint.startswith("tcp://127.0.0.1:") or endpoint.startswith("ipc://")):
            raise ValueError("the VLA runner is reached through a loopback forward only")
        self.endpoint, self.timeout_s = endpoint, timeout_s

    def _call_blocking(self, op: str) -> Optional[dict]:
        import zmq
        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.REQ)
        sock.setsockopt(zmq.LINGER, 0)
        sock.setsockopt(zmq.RCVTIMEO, int(self.timeout_s * 1000))
        sock.setsockopt(zmq.SNDTIMEO, int(self.timeout_s * 1000))
        try:
            sock.connect(self.endpoint)
            sock.send(json.dumps({"op": op}).encode())
            answer = json.loads(sock.recv())
            return answer if isinstance(answer, dict) else None
        except Exception:
            return None
        finally:
            sock.close(0)

    async def call(self, op: str) -> Optional[dict]:
        if op not in OPS:
            raise ValueError(op)
        return await asyncio.to_thread(self._call_blocking, op)
