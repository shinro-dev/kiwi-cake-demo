#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Capture one settled BODY/WRIST frame from the LeKiwi Pi's camera devices for a
physical measurement, with metadata recorded alongside it.

Camera-only: this runs `ffmpeg -f v4l2 ...` over SSH against /dev/video0 or
/dev/video2 on the Pi. It never imports lerobot, never touches the motor bus,
never enables torque, never sends a robot action.

The Pi address is never hardcoded here and never written to any output file:
it is read from the KC_REMOTE_IP environment variable (same convention as
bin/laptop/teleop.py).

Usage:
    KC_REMOTE_IP=<pi-address> python3 tools/capture_measurement.py \\
        --camera body --out .local-dev/captures/.../frame.png \\
        --label horizontal-left [--x 1.20] [--y 0.55] [--settle-frames 6]

Writes <out> (PNG) and <out>.json (metadata: label, x, y, camera, device,
resolution, settle_frames, stream start/end timestamps -- no Pi
address/hostname/username/path).
"""

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

CAMERA_DEVICE = {"body": "/dev/video0", "wrist": "/dev/video2"}
RESOLUTION = "1280x720"


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--camera", choices=sorted(CAMERA_DEVICE), default="body")
    p.add_argument("--out", type=Path, required=True, help="output PNG path (parent dirs created if needed)")
    p.add_argument("--label", required=True, help="experiment/measurement label, e.g. horizontal-left")
    p.add_argument("--x", type=float, default=None, help="operator-supplied cube x position, meters")
    p.add_argument("--y", type=float, default=None, help="operator-supplied cube y position, meters")
    p.add_argument("--settle-frames", type=int, default=6, help="frames to sample at 1fps before keeping the last one (default 6, ~5-6s settle)")
    p.add_argument("--remote-ip", default=None, help="Pi LAN address; defaults to $KC_REMOTE_IP, never written to any output file")
    return p


def run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, **kwargs)


def main(argv=None):
    import os

    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    remote_ip = args.remote_ip or os.environ.get("KC_REMOTE_IP")
    if not remote_ip:
        sys.exit("capture_measurement: no Pi address -- set KC_REMOTE_IP or pass --remote-ip (never store this in a tracked file)")

    device = CAMERA_DEVICE[args.camera]
    out_path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if args.settle_frames < 1:
        sys.exit("capture_measurement: --settle-frames must be >= 1")

    remote_dir = "/tmp/capture_measurement"
    remote_pattern = f"{remote_dir}/frame_%02d.png"
    last_frame = f"{remote_dir}/frame_{args.settle_frames:02d}.png"

    ssh_base = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", remote_ip]

    remote_capture_cmd = (
        f"rm -rf {shlex.quote(remote_dir)} && mkdir -p {shlex.quote(remote_dir)} && "
        f"date -u +%Y-%m-%dT%H:%M:%S.%3NZ && "
        f"ffmpeg -hide_banner -loglevel error -f v4l2 -input_format mjpeg "
        f"-video_size {RESOLUTION} -i {shlex.quote(device)} -vf fps=1 "
        f"-frames:v {args.settle_frames} -y {shlex.quote(remote_pattern)} && "
        f"date -u +%Y-%m-%dT%H:%M:%S.%3NZ"
    )

    print(f"capture_measurement: streaming {args.camera} ({device}) for ~{args.settle_frames}s to settle exposure...", file=sys.stderr)
    result = run(ssh_base + [remote_capture_cmd], capture_output=True, text=True)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    stream_start = lines[0] if len(lines) >= 1 else None
    stream_end = lines[-1] if len(lines) >= 2 else None

    run(["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-q", f"{remote_ip}:{last_frame}", str(out_path)])
    run(ssh_base + [f"rm -rf {shlex.quote(remote_dir)}"])

    from PIL import Image
    with Image.open(out_path) as im:
        width, height = im.size

    meta = {
        "label": args.label,
        "camera": args.camera,
        "device_logical_name": args.camera,
        "x_m": args.x,
        "y_m": args.y,
        "width_px": width,
        "height_px": height,
        "settle_frames": args.settle_frames,
        "stream_start_utc": stream_start,
        "stream_end_utc": stream_end,
        "capture_method": "direct v4l2 device capture (ffmpeg) over SSH, bypassing LeRobot/LeKiwi/ZMQ; no motor bus contact, no torque, no actions sent",
    }
    meta_path = out_path.with_suffix(out_path.suffix + ".json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    print(f"capture_measurement: saved {out_path} ({width}x{height}) and {meta_path}")


if __name__ == "__main__":
    main()
