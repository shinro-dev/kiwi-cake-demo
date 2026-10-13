#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""observation_cadence_probe: measure how evenly observations ARRIVE at one receiver.

WHY THIS EXISTS. The numbers that put the front camera in question (longest observation gaps of 246.46, 243.92 and
291.79 ms in a controlled A/B/A) were produced by an instrument that was never committed. A measurement whose
instrument is not under version control cannot be repeated, and a result nobody can repeat cannot settle anything.
This is that instrument, written down. It is NOT the original: results from it are its own and must be reported as
such, never merged into the earlier series.

WHAT IT MEASURES, AND WHAT IT CANNOT. It records the RECEIVER's monotonic clock at the moment each observation is
delivered to this process, and reports the distribution of the intervals between those moments. That is RECEIVER
CADENCE. It is not the host's internal loop rate, and it is not the age of any sample:

  - the stock host neither stamps nor sequences its observations, so a gap here cannot be attributed to the host
    rather than to the transport, the scheduler, or this process;
  - a long interval means this receiver waited. It does not establish that anything was dropped, because without a
    sequence number a dropped sample and a late one look identical from here;
  - run on the laptop, every interval also contains the network and the laptop's scheduling. Run on the robot
    computer over loopback, those two are removed, which NARROWS where a delay can come from without qualifying
    anything: loopback is a diagnosis step, not a substitute for qualifying the path the controller actually uses.

THE ONE THING THAT MAKES IT UNSAFE TO RUN CARELESSLY. The host's observation socket is ZeroMQ PUSH, and PUSH/PULL
distributes: a second consumer does not copy the stream, it TAKES samples out of it. While a controller is running,
attaching this would silently starve the controller of observations it is steering on. So it refuses to start unless
the caller states that nothing else is consuming (--sole-consumer), and the refusal says why. A tap socket, where one
is configured, is PUB/SUB and has no such property; this tool does not assume one exists, because the baseline NORMAL
host has none.

  python3 tools/observation_cadence_probe.py --endpoint tcp://127.0.0.1:PORT --seconds 60 --sole-consumer
  python3 tools/observation_cadence_probe.py --endpoint ... --socket sub --seconds 60 --sole-consumer --out report.json

Exit status: 0 a measurement was taken, 2 usage, 3 the caller did not state sole consumption, 4 nothing arrived.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

# Intervals at or above this are listed individually in the report, so the tail can be read and not only summarised.
NOTABLE_INTERVAL_MS = 100.0


def percentile(values, fraction: float) -> float:
    """The value at `fraction` through a sorted copy, by nearest rank. Plain and dependency-free on purpose."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def summarise(arrivals_ns, *, endpoint_stated: str, socket_kind: str, seconds: float) -> dict:
    """The report. Every number in it is about ARRIVALS AT THIS PROCESS and says so in its own name."""
    intervals_ms = [(b - a) / 1e6 for a, b in zip(arrivals_ns, arrivals_ns[1:])]
    span_s = (arrivals_ns[-1] - arrivals_ns[0]) / 1e9 if len(arrivals_ns) >= 2 else 0.0
    report = {
        "schema": "observation-cadence-probe/1",
        "instrument": "tools/observation_cadence_probe.py",
        "instrument_note": (
            "receiver cadence only; this is NOT the instrument that produced the 2026-09-22 A/B/A series and its "
            "results must not be merged with that series"
        ),
        "socket_kind": socket_kind,
        "endpoint_kind": endpoint_stated,
        "requested_seconds": seconds,
        "observed_span_s": round(span_s, 3),
        "received": len(arrivals_ns),
        "receiver_clock": "CLOCK_MONOTONIC of the machine running this probe",
        "host_provides_sequence": False,
        "host_provides_timestamp": False,
        "attribution_limit": (
            "without a sequence number a dropped sample and a late one are indistinguishable from here, so no gap "
            "below is attributed to the host, the transport or this process"
        ),
    }
    if not intervals_ms:
        report["intervals_ms"] = None
        return report
    report["rate_hz_from_arrivals"] = round((len(arrivals_ns) - 1) / span_s, 3) if span_s > 0 else None
    report["intervals_ms"] = {
        "count": len(intervals_ms),
        "min": round(min(intervals_ms), 3),
        "median": round(statistics.median(intervals_ms), 3),
        "mean": round(statistics.fmean(intervals_ms), 3),
        "p95": round(percentile(intervals_ms, 0.95), 3),
        "p99": round(percentile(intervals_ms, 0.99), 3),
        "longest": round(max(intervals_ms), 3),
    }
    report["notable_intervals_ms"] = sorted((round(v, 3) for v in intervals_ms if v >= NOTABLE_INTERVAL_MS), reverse=True)[:50]
    report["notable_threshold_ms"] = NOTABLE_INTERVAL_MS
    return report


def collect(endpoint: str, socket_kind: str, seconds: float) -> list:
    """Arrival times in this process's monotonic clock. Receives only: neither socket type here can send."""
    import zmq  # imported here so --help works on a machine without pyzmq

    context = zmq.Context()
    kind = zmq.PULL if socket_kind == "pull" else zmq.SUB
    socket = context.socket(kind)
    if kind == zmq.SUB:
        socket.setsockopt(zmq.SUBSCRIBE, b"")
    socket.setsockopt(zmq.LINGER, 0)
    socket.setsockopt(zmq.RCVTIMEO, 250)
    socket.connect(endpoint)
    arrivals = []
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            try:
                socket.recv(copy=False)
            except zmq.Again:
                continue
            arrivals.append(time.monotonic_ns())  # stamped the instant it is in hand, before anything is parsed
    finally:
        socket.close()
        context.term()
    return arrivals


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="observation_cadence_probe.py", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--endpoint", required=True, help="the ZeroMQ endpoint to attach to, for example tcp://127.0.0.1:PORT")
    parser.add_argument("--socket", choices=("pull", "sub"), default="pull",
                        help="pull for the host's observation socket (TAKES samples from any other consumer), sub for a tap")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--sole-consumer", action="store_true",
                        help="state that no controller and no other consumer is attached; required for --socket pull")
    parser.add_argument("--out", help="write the report here as JSON; it is printed either way")
    args = parser.parse_args(argv)

    if args.seconds <= 0:
        print("--seconds must be positive", file=sys.stderr)
        return 2
    if args.socket == "pull" and not args.sole_consumer:
        print(
            "REFUSE: the observation socket is PUSH/PULL, so attaching here TAKES samples from any other consumer. "
            "Stop the controller, prevent new submissions, and pass --sole-consumer to state that this is the only "
            "one attached.",
            file=sys.stderr,
        )
        return 3

    arrivals = collect(args.endpoint, args.socket, args.seconds)
    # The endpoint can name a machine, so the report records its SHAPE and never the string itself.
    stated = "loopback" if "127.0.0.1" in args.endpoint or "localhost" in args.endpoint else "remote"
    report = summarise(arrivals, endpoint_stated=stated, socket_kind=args.socket, seconds=args.seconds)
    text = json.dumps(report, indent=1, sort_keys=True)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    if not arrivals:
        print("nothing arrived: the endpoint published no observation in the window", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
