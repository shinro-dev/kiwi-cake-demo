#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Camera-only time-multiplex probe, run ON THE PI: repeat a FRONT / WRIST
open -> settle -> capture -> close order for N cycles and write the evidence.

Read-only with respect to the robot: this never imports the robot package,
never opens the motor bus, never enables torque, never sends an action. It
only opens video devices, one at a time.

IMPORTANT: a camera that the running motor host already owns cannot be opened
here (the open fails and is recorded as evidence). To measure time-
multiplexing, run this while the motor host is either stopped or is a host
that owns no camera.

Camera identities come ONLY from the environment (never from a tracked file,
never /dev/videoN):

    KC_FRONT_CAMERA_ID=/dev/v4l/by-id/<front camera entry>
    KC_WRIST_CAMERA_ID=/dev/v4l/by-id/<wrist camera entry>

    python3 bin/pi/camera_multiplex_probe.py --list          # show by-id entries
    python3 bin/pi/camera_multiplex_probe.py --cycles 20

Writes <out-dir>/report.json (fingerprints only, no device identity) and
<out-dir>/frames/*.png. Keep the output directory outside any git checkout:
raw captures are never committed. Exit status: 0 when every cycle was clean,
1 otherwise, 3 when the probe REFUSED to open anything because a camera lock is
held (the report is written in all three cases).

SHARED LOCK: before any device is opened, the probe takes the per-role camera
lock (autonomy/perception/camera_lock.py) of every role in --order, without
blocking beyond --lock-wait-s. While bin/pi/camera_service.py owns a camera the
lock is held and the probe fails closed: no device is opened, every attempt of
the report carries the refusal as its error, and the exit status is 3. Stop the
service to use this path; the two never own a camera at the same time.

NEVER RUN ON HARDWARE as of 2026-09-19.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "perception"))

from camera_cycle_probe import run_multiplex_cycles  # noqa: E402
from camera_lifecycle import (  # noqa: E402
    DEFAULT_SETTLE_FRAMES,
    ROLES,
    CameraIdentityUnavailable,
    CameraMultiplexer,
    InvalidCameraIdentity,
    OpenCvBackend,
    resolve_camera_identity,
)
try:
    from camera_lock import DEFAULT_WAIT_S, CameraLock, CameraLockError, CameraLockHeld  # noqa: E402
except ImportError:  # camera_lock.py was not copied next to camera_lifecycle.py: refuse with a report, never open without the lock
    DEFAULT_WAIT_S, CameraLock, CameraLockHeld = 2.0, None, ()

    class CameraLockError(RuntimeError):
        pass

LOCK_HOLDER = "camera_multiplex_probe"
LOCK_MODULE_MISSING = ("autonomy/perception/camera_lock.py is missing next to camera_lifecycle.py on this machine; the probe never opens a "
                       "camera without the shared lock. Copy it together with the other probe files")
EXIT_REFUSED = 3

BY_ID_DIR = "/dev/v4l/by-id"


def build_parser():
    p = argparse.ArgumentParser(prog="camera_multiplex_probe.py", description="Camera-only FRONT/WRIST time-multiplex probe.")
    p.add_argument("--list", action="store_true", help=f"list {BY_ID_DIR} entries and exit (terminal only, nothing is written)")
    p.add_argument("--cycles", type=int, default=10)
    p.add_argument("--order", default="front,wrist,front", help="comma-separated camera roles per cycle")
    p.add_argument("--settle-frames", type=int, default=DEFAULT_SETTLE_FRAMES)
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--out-dir", default=None, help="default: ~/kc-camera-probe/<UTC timestamp>")
    p.add_argument("--lock-wait-s", type=float, default=DEFAULT_WAIT_S, help="longest wait for the shared per-role camera lock before refusing")
    return p


def _opencv_backend(role, width, height):
    return OpenCvBackend(width=width, height=height)


class _RefusedBackend:
    """Stands in for every camera when a lock was refused: open() raises the
    refusal, so the report keeps its normal shape (one failed attempt per role,
    the refusal as its error) and no device is ever touched."""

    def __init__(self, refusal: str):
        self.refusal = refusal

    def open(self, identity):
        raise CameraLockError(self.refusal)

    def read(self):
        return None

    def close(self):
        pass


def _acquire_locks(roles, env, wait_s):
    """(locks, refusal or None), refusal = {"reason", "detail"}. All or nothing:
    on a refusal every lock already taken is released and nothing may be
    opened. A lock that cannot be used at all refuses exactly like a held one."""
    locks = []
    if CameraLock is None:
        return [], {"reason": "camera_lock_unavailable", "detail": LOCK_MODULE_MISSING}
    try:
        for role in roles:
            locks.append(CameraLock(role, LOCK_HOLDER, env=env).acquire(wait_s=wait_s))
    except CameraLockError as error:
        for lock in locks:
            lock.release()
        reason = "camera_lock_held" if isinstance(error, CameraLockHeld) else "camera_lock_unavailable"
        return [], {"reason": reason, "detail": str(error)}
    return locks, None


def main(argv=None, env=None, backend_factory=_opencv_backend) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env

    if args.list:
        for name in sorted(os.listdir(BY_ID_DIR)) if os.path.isdir(BY_ID_DIR) else []:
            print(os.path.join(BY_ID_DIR, name))
        return 0

    order = tuple(role.strip() for role in args.order.split(",") if role.strip())
    unknown = [role for role in order if role not in ROLES]
    if unknown or not order:
        sys.exit(f"camera_multiplex_probe: --order must name roles from {ROLES}, got {args.order!r}")
    try:
        identities = {role: resolve_camera_identity(role, env=env) for role in dict.fromkeys(order)}
    except (CameraIdentityUnavailable, InvalidCameraIdentity) as error:
        sys.exit(f"camera_multiplex_probe: {error}")

    out_dir = args.out_dir or os.path.join(os.path.expanduser("~"), "kc-camera-probe", time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    frames_dir = os.path.join(out_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    def keep_frame(role, index, frame):
        import cv2

        cv2.imwrite(os.path.join(frames_dir, f"{index:04d}-{role}.png"), frame)

    locks, refusal = _acquire_locks(identities, env, args.lock_wait_s)
    try:
        if refusal is None:
            backends, cycles = {role: backend_factory(role, args.width, args.height) for role in identities}, args.cycles
        else:
            # All or nothing, so EVERY role carries the one refusal, also a role whose own lock was free: the text says
            # so, or the front camera's attempt would read as though the front lock had been the one that was held.
            backends, cycles = {role: _RefusedBackend(f"no camera was opened ({refusal['reason']}): {refusal['detail']}") for role in identities}, 1
        mux = CameraMultiplexer(backends=backends, identities=identities)
        report = run_multiplex_cycles(mux, order=order, cycles=cycles, settle_frames=args.settle_frames, on_frame=keep_frame)
    finally:
        for lock in locks:
            lock.release()
    report["refused"] = refusal
    report["requested_resolution"] = [args.width, args.height]
    report["started_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(os.path.join(out_dir, "report.json"), "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    for role, summary in report["summary"].items():
        print(f"{role}: {summary['frame_valid']}/{summary['attempts']} valid, open_ok {summary['open_ok']}, close_ok {summary['close_ok']}, "
              f"stale {summary['stale_suspect']}, switch latency median {summary['switch_latency_s']['median']}, errors {summary['errors']}")
    print(f"all_cycles_clean: {report['all_cycles_clean']}  report: {os.path.join(out_dir, 'report.json')}")
    if refusal is not None:
        print(f"camera_multiplex_probe: REFUSED ({refusal['reason']}), no camera was opened: {refusal['detail']}", file=sys.stderr)
        return EXIT_REFUSED
    return 0 if report["all_cycles_clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
