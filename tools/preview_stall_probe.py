#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""preview_stall_probe: one sample a second of what the live view is doing, into a local JSON-lines file.

WHY THIS EXISTS. The desktop console's preview stalled during missions and nothing recorded when, for how long, or what
the camera service said at the time. This is the instrument that records it. Each sample holds, side by side:

  runtime   what the runtime's preview slot says (state, reason, the kept frame's number and RECEIVE age, the measured
            rate, gaps, skipped_for_mission), what its status says about that camera (yield notices seen, the camera
            service's own counters, transport faults) and what its mission surface says (mission id and state);
  service   optionally, what the camera service's own preview stream says to a subscriber of this process (frames
            received, rate, gaps, the longest interval, yield notices and the service's counters).

Read together they attribute a gap: the service standing aside for a capture (skipped_for_mission moves, notices
arrive, the slot says paused_for_decision_capture), the service or camera going silent (the service stream stops as
well), or the path between the service and the runtime (the service stream is live while the slot stalls).

READ-ONLY, BY CONSTRUCTION. The runtime is asked three reads, one per connection and with no handshake: `preview`
WITHOUT the picture, `status` and `mission_state`. None of them is in demo_runtime.rpc.MUTATING_OPS and none starts,
stops or submits anything. The camera service is read through a SUB socket, which cannot send, on a loopback endpoint
only (demo_runtime.preview_client.check_endpoint refuses anything else); a second subscriber gets its own copy of the
stream and takes nothing from the runtime's. Nothing here reaches a motor, a camera device or a Cake slot.

CLOCKS. Every time and age the probe measures is this process's monotonic clock and is labelled laptop_monotonic,
whatever machine it runs on. The runtime's receive ages are the runtime's own clock and are copied as it states them.

THE OUTPUT is created new (an existing file is never overwritten) and holds no address, no socket path and no device
name: sample lines, then one summary line. It belongs in the ignored evidence directory, never in the repository.

  python3 tools/preview_stall_probe.py --runtime-socket <runtime data dir>/runtime.sock --seconds 600 --out <evidence>/stall.jsonl
  python3 tools/preview_stall_probe.py --runtime-socket ... --camera-endpoint tcp://127.0.0.1:5571 --seconds 600 --out ...

Exit status: 0 the samples were taken, 2 usage, 3 the output file exists.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "demo-runtime"))

from demo_runtime.preview_client import CLOCK_LAPTOP, check_endpoint  # noqa: E402
from demo_runtime.rpc import RuntimeClient, RuntimeUnavailable  # noqa: E402

PROBE_SCHEMA = "kc.preview_stall_probe/1"
READ_OPS = ("preview", "status", "mission_state")
PREVIEW_KEYS = ("state", "reason", "available", "seq", "source_seq", "receive_age_ms", "measured_fps", "gaps", "skipped_for_mission")
CAMERA_KEYS = ("state", "reason", "yield_notices", "transport_faults", "open_failures", "service")


def runtime_reader(socket_path: str, timeout_s: float = 2.0) -> Callable[[dict], dict]:
    """One read per call, over the runtime's own client: a new connection each time and no hello, so nothing here is a
    session the runtime has to keep. Only the three reads are ever sent."""
    client = RuntimeClient(socket_path, timeout_s=timeout_s)

    def call(request: dict) -> dict:
        if request.get("op") not in READ_OPS:
            raise ValueError("the stall probe sends reads only")
        return client._call(request)  # the same single-request path RuntimeClient.status() and .preview() use

    return call


def _ms(value_ns) -> Optional[float]:
    return round(value_ns / 1e6, 1) if isinstance(value_ns, int) and not isinstance(value_ns, bool) else None


def _read(runtime: Callable[[dict], dict], request: dict):
    try:
        answer = runtime(request)
    except RuntimeUnavailable:
        return None, "runtime_unavailable"
    except (OSError, ValueError, StopIteration):
        return None, "runtime_read_failed"
    return (answer, None) if isinstance(answer, dict) else (None, "runtime_answer_not_an_object")


def sample_runtime(runtime: Callable[[dict], dict], role: str) -> dict:
    preview, error = _read(runtime, {"op": "preview", "with_bytes": False})
    if error is not None:
        return {"error": error}
    clock = preview.get("capture_clock") if isinstance(preview.get("capture_clock"), dict) else {}
    out = {"preview": {**{key: preview.get(key) for key in PREVIEW_KEYS}, "session": clock.get("session")}}
    status, error = _read(runtime, {"op": "status"})
    previews = ((status or {}).get("sources") or {}).get("previews") or {}
    camera = previews.get(role) if isinstance(previews, dict) else None
    out["camera"] = {key: camera.get(key) for key in CAMERA_KEYS} if isinstance(camera, dict) else {"error": error or "camera_not_reported"}
    mission, error = _read(runtime, {"op": "mission_state"})
    if mission is None:
        out["mission"] = {"error": error}
    elif mission.get("ok") is False:
        out["mission"] = {"reason_code": mission.get("reason_code")}
    else:
        out["mission"] = {"mission_id": mission.get("mission_id"), "state": mission.get("state")}
    return out


def sample_service(service, now_ns: int) -> dict:
    stats = service.stats() or {}
    gaps = stats.get("gaps") if isinstance(stats.get("gaps"), dict) else {}
    malformed = stats.get("malformed") if isinstance(stats.get("malformed"), dict) else {}
    frame, notice = service.latest(), service.latest_yield()
    return {"received": stats.get("received"), "measured_fps": stats.get("measured_fps"), "rx_age_ms": _ms(stats.get("rx_age_ns")),
            "gap_events": gaps.get("events"), "missing_previews": gaps.get("missing_previews"),
            "longest_interval_ms": _ms(stats.get("longest_interval_ns")), "yield_notices": stats.get("yield_notices"),
            "malformed": sum(value for value in malformed.values() if isinstance(value, int)),
            "preview_seq": getattr(frame, "preview_seq", None), "session": getattr(frame, "session", None),
            "last_notice_age_ms": None if notice is None else _ms(now_ns - notice.rx_mono_ns),
            "last_notice_yielding_for_ms": getattr(notice, "yielding_for_ms", None), "counters": stats.get("service")}


def _runtime_state(sample: Optional[dict]) -> Optional[str]:
    if sample is None:
        return None
    if "error" in sample:
        return sample["error"]
    return (sample.get("preview") or {}).get("state") or "unstated"


def run(*, role: str, samples: int, interval_s: float, out_path: Path, runtime: Optional[Callable[[dict], dict]], service,
        clock_ns: Callable[[], int] = time.monotonic_ns, sleep: Callable[[float], None] = time.sleep,
        wall: Callable[[], str] = lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())) -> int:
    """Take `samples` samples, one every `interval_s` on this process's monotonic clock, and write them to a NEW file."""
    descriptor = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # FileExistsError: earlier evidence is kept
    states: dict = {}
    paused = longest = current = 0
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        started = clock_ns()
        for index in range(samples):
            due = started + int(index * interval_s * 1e9)
            now = clock_ns()
            if now < due:
                sleep((due - now) / 1e9)
                now = clock_ns()
            runtime_sample = sample_runtime(runtime, role) if runtime is not None else None
            service_sample = sample_service(service, now) if service is not None else None
            line = {"probe": PROBE_SCHEMA, "i": index, "t_mono_ns": now, "clock_domain": CLOCK_LAPTOP, "t_wall_utc": wall(), "role": role,
                    "runtime": runtime_sample, "service": service_sample}
            handle.write(json.dumps(line, sort_keys=True) + "\n")
            handle.flush()
            state = _runtime_state(runtime_sample)
            if state is not None:
                states[state] = states.get(state, 0) + 1
                current = 0 if state == "connected" else current + 1
                longest = max(longest, current)
                if (runtime_sample.get("preview") or {}).get("reason") == "paused_for_decision_capture":
                    paused += 1
        summary = {"samples": samples, "interval_s": interval_s, "runtime_states": states, "paused_samples": paused,
                   "longest_not_connected_run": longest}
        handle.write(json.dumps({"probe": PROBE_SCHEMA, "summary": summary}, sort_keys=True) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="preview_stall_probe.py", description="Sample the live view once a second, read-only, into a new JSON-lines file.")
    parser.add_argument("--runtime-socket", help="the runtime's unix socket (reads only: preview without the picture, status, mission_state)")
    parser.add_argument("--camera-endpoint", help="the camera service's preview, loopback only: tcp://127.0.0.1:<port>")
    parser.add_argument("--role", default="wrist", choices=("wrist", "front"))
    parser.add_argument("--seconds", type=float, default=600.0, help="how long to sample (ignored with --samples)")
    parser.add_argument("--samples", type=int, default=None)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--out", required=True, help="a NEW file; an existing one is never overwritten")
    return parser


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exit_:
        return 2 if exit_.code else 0
    if not args.runtime_socket and not args.camera_endpoint:
        print("preview_stall_probe: name --runtime-socket, --camera-endpoint or both", file=sys.stderr)
        return 2
    if args.interval <= 0 or (args.samples is not None and args.samples < 1) or args.seconds <= 0:
        print("preview_stall_probe: --interval, --seconds and --samples must be positive", file=sys.stderr)
        return 2
    subscriber = None
    if args.camera_endpoint:
        try:
            check_endpoint(args.camera_endpoint)
        except ValueError as error:
            print(f"preview_stall_probe: {error}", file=sys.stderr)
            return 2
        from demo_runtime.preview_client import PreviewSubscriber

        subscriber = PreviewSubscriber(args.camera_endpoint, args.role)
        subscriber.start()
    samples = args.samples if args.samples is not None else max(1, int(args.seconds / args.interval))
    try:
        return run(role=args.role, samples=samples, interval_s=args.interval, out_path=Path(args.out),
                   runtime=runtime_reader(args.runtime_socket) if args.runtime_socket else None, service=subscriber)
    except FileExistsError:
        print("preview_stall_probe: the output file exists; it is evidence and is not overwritten", file=sys.stderr)
        return 3
    finally:
        if subscriber is not None:
            subscriber.stop()


if __name__ == "__main__":
    sys.exit(main())
