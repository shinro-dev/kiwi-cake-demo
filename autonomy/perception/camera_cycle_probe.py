#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""camera_cycle_probe: repeat a FRONT / WRIST open-settle-capture-close order
for N cycles through the CameraMultiplexer and aggregate the evidence.

This is the instrument for the first M1 hardware question
(autonomous-single-cube-plan.md section 6): is time-multiplexing the two
cameras stable on this rig, and what does a switch cost? It answers with
counts and latencies, per camera role:

  attempts / open_ok / frame_valid / close_ok / stale_suspect / errors
  open_latency_s, total_latency_s   median and max
  switch_latency_s                  total latency of the captures that
                                    directly followed the OTHER camera

A failed cycle is counted and the run continues: the point is to expose
instability, not to stop at the first symptom. The motor host is never
involved; this only opens cameras.

Read-only with respect to the robot. NEVER RUN ON HARDWARE as of 2026-09-19:
the offline tests prove the bookkeeping, not the cameras.
"""
import statistics

from camera_lifecycle import DEFAULT_SETTLE_FRAMES


def _stats(values: list) -> dict:
    if not values:
        return {"count": 0, "median": None, "max": None}
    return {"count": len(values), "median": float(statistics.median(values)), "max": float(max(values))}


def _summarize(records: list) -> dict:
    return {
        "attempts": len(records),
        "open_ok": sum(r.open_ok for r in records),
        "frame_valid": sum(r.frame_valid for r in records),
        "close_ok": sum(r.close_ok for r in records),
        "stale_suspect": sum(r.stale_suspect for r in records),
        "errors": sorted({r.error for r in records if r.error}),
        "open_latency_s": _stats([r.open_latency_s for r in records if r.open_latency_s is not None]),
        "total_latency_s": _stats([r.total_latency_s for r in records if r.total_latency_s is not None]),
        "switch_latency_s": _stats([r.total_latency_s for r in records if r.switched_from not in (None, r.role)]),
    }


def run_multiplex_cycles(mux, order=("front", "wrist", "front"), cycles: int = 10, settle_frames: int = DEFAULT_SETTLE_FRAMES, on_frame=None) -> dict:
    """on_frame(role, attempt_index, frame) is called for every valid frame,
    so the caller can keep the images in an ignored local directory."""
    if cycles < 1:
        raise ValueError("cycles must be >= 1")
    for role in order:
        mux.backends[role]  # KeyError before any camera is opened

    records = []
    for _ in range(cycles):
        for role in order:
            result = mux.capture(role, settle_frames=settle_frames)
            if result.frame is not None and on_frame is not None:
                on_frame(role, len(records), result.frame)
            records.append(result.record)

    clean = all(r.open_ok and r.frame_valid and r.close_ok and not r.stale_suspect and not r.error for r in records)
    return {
        "order": list(order),
        "cycles": cycles,
        "settle_frames": settle_frames,
        "attempts": [r.to_dict() for r in records],
        "summary": {role: _summarize([r for r in records if r.role == role]) for role in dict.fromkeys(order)},
        "all_cycles_clean": clean,
    }
