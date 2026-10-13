#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""tools/fake_preview_publisher.py: a LABELLED stand-in for the camera service's preview tap.

WHAT IT IS FOR. The role-aware path (config, forwards, two sources, two topics, the API's per-role reader, the
browser's per-role slots) has to be provable without a robot, and a stage that proves it must never be mistaken for a
camera. So every frame this publishes is drawn with the word SYNTHETIC across it, carries the role it belongs to, and
names this tool in its session id. It is not a camera and it does not pretend to be one: it exists so that a failure
seen later on real hardware is known to be the hardware and not the plumbing.

It speaks the camera service's own wire format (magic, big-endian header length, JSON header, JPEG) on one PUB socket
PER ROLE, which is exactly what the role-aware sources subscribe to.

    python3 tools/fake_preview_publisher.py --wrist-port 5601 --front-port 5602 [--fps 8] [--seconds 60]

Nothing here touches a robot, a camera or a motor.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import time

# Taken from the SUBSCRIBER rather than restated here, so this stand-in cannot drift from the thing it stands in for.
# demo_runtime.preview_client imports no zmq at module scope, and it mirrors bin/pi/camera_service.py's own constants.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "packages" / "demo-runtime"))
from demo_runtime.preview_client import PREVIEW_MAGIC, PREVIEW_SCHEMA  # noqa: E402

ROLES = ("wrist", "front")


def pack_preview(header: dict, jpeg: bytes) -> bytes:
    blob = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return PREVIEW_MAGIC + struct.pack(">I", len(blob)) + blob + jpeg


def frame_jpeg(role: str, seq: int, width: int = 320, height: int = 180) -> bytes:
    """A picture that says what it is. Two roles get two different backgrounds so that a stream served under the wrong
    role is visible AS A PICTURE, not only in a field: this is the defect the per-role ports exist to prevent."""
    import numpy as np
    import cv2

    base = (40, 60, 90) if role == "wrist" else (90, 55, 35)
    image = np.full((height, width, 3), base, dtype=np.uint8)
    cv2.putText(image, "SYNTHETIC", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(image, role.upper(), (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 230, 255), 2, cv2.LINE_AA)
    cv2.putText(image, f"seq {seq}", (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 1, cv2.LINE_AA)
    cv2.putText(image, "NOT A CAMERA", (10, 155), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
    if not ok:
        raise RuntimeError("the synthetic frame could not be encoded")
    return buffer.tobytes()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="publish SYNTHETIC preview frames, one PUB socket per role")
    parser.add_argument("--wrist-port", type=int, default=5601)
    parser.add_argument("--front-port", type=int, default=5602)
    parser.add_argument("--fps", type=float, default=8.0)
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--only", choices=ROLES, default=None, help="publish just this role, to prove one camera failing leaves the other alone")
    args = parser.parse_args(argv)

    import zmq

    if args.wrist_port == args.front_port:
        parser.error("each role needs its own port; one socket cannot serve two cameras")

    context = zmq.Context.instance()
    ports = {"wrist": args.wrist_port, "front": args.front_port}
    roles = ROLES if args.only is None else (args.only,)
    sockets = {}
    for role in roles:
        socket = context.socket(zmq.PUB)
        socket.setsockopt(zmq.LINGER, 0)
        socket.bind(f"tcp://127.0.0.1:{ports[role]}")
        sockets[role] = socket
        print(f"fake-preview: {role} publishing on 127.0.0.1:{ports[role]} (SYNTHETIC)", flush=True)

    session = f"fake-preview-{int(time.time())}"
    period = 1.0 / max(args.fps, 0.1)
    deadline = time.monotonic() + args.seconds
    seq = 0
    try:
        while time.monotonic() < deadline:
            seq += 1
            now_ns = time.monotonic_ns()
            for role, socket in sockets.items():
                header = {
                    "schema": PREVIEW_SCHEMA, "role": role, "session": session, "preview_seq": seq, "source_seq": seq,
                    "capture_mono_ns": now_ns, "dequeue_mono_ns": now_ns + 2_000_000, "clock_domain": "pi_monotonic",
                    "timestamp_semantics": "synthetic_publisher", "encoding": "jpeg", "width": 320, "height": 180,
                    "source_width": 1280, "source_height": 720, "pixel_format": "SYNTH",
                    "source_fps_reported": args.fps, "source_fps_measured": args.fps,
                }
                socket.send(pack_preview(header, frame_jpeg(role, seq)))
            time.sleep(period)
    except KeyboardInterrupt:
        pass
    finally:
        for socket in sockets.values():
            socket.close(0)
    print(f"fake-preview: published {seq} frame(s) per role", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
