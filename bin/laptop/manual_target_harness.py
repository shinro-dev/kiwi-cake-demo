#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Manual-target development harness: manual TargetObservation -> mobile_approach
-> stable_motion_gateway -> transport. Built for a FUTURE physically-supervised
session -- NOT executed by anything in this repository, and not run against
hardware as part of building it.

Defaults (all must be true before this ever commands a real base):
  - autonomous mode DISABLED until you type ENABLE;
  - zero motion at startup and after every stop/disconnect/expiry;
  - a very short command TTL (150ms, well under the 250ms baseline
    elsewhere in this repo) so any hang or dropped keystroke zeroes fast;
  - the TTL is enforced by THIS harness, not only by the LeKiwi host's own
    ~500ms watchdog: the main loop polls stdin with a short timeout
    (TICK_INTERVAL_S) and calls gateway.tick() on every pass, so an
    unrefreshed lease is actively zeroed well inside its own expiry
    window, with no dependency on the host. The host watchdog remains an
    independent second layer, not the only one.
  - --dry-run is the DEFAULT: a FakeBaseMotionTransport is used and no
    device is ever opened. Only --execute switches to the real
    LeKiwiTransport (autonomy/motion/lerobot_transport.py), and even then
    nothing moves until you separately type ENABLE.
  - --vx-max/--vy-max/--omega-max override the gateway's hard velocity
    ceilings for this session only; any flag left unset keeps the
    production GatewayLimits() default for that field. All three must be
    > 0 (GatewayLimits' own invariant) -- an override of 0 or less is
    rejected with a clear message, the invariant itself is never weakened.
  - Ctrl-C or typing STOP is the emergency stop; it latches immediately and
    requires typing RESET before ENABLE can do anything again.

The command that WOULD launch this against the real robot (DO NOT RUN):
    KC_REMOTE_IP=<pi-address> python3 bin/laptop/manual_target_harness.py \\
        --robot-id <robot-id> --execute --command-ttl-ms 150 \\
        --vx-max 0.05 --vy-max 0.05 --omega-max 0.01

Dry-run (safe, no device, what this repository's own tests exercise the
same logic as): python3 bin/laptop/manual_target_harness.py

Commands at the prompt:
  x y        submit one TargetObservation at (x, y) meters for the current target
  TARGET id  change the current target_id (velocity does not carry over)
  ENABLE     arm autonomous mode (required before anything can move)
  STOP       operator stop -- immediate zero, latched
  RESET      clear the operator-stop latch (does not re-enable autonomous mode)
  QUIT       exit (implicit operator stop first)
"""

import argparse
import os
import select
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "motion"))

from clock import MonotonicClock  # noqa: E402
from mobile_approach import MobileApproachController, TargetObservation  # noqa: E402
from stable_motion_gateway import GatewayLimits, StableMotionGateway  # noqa: E402
from transport import FakeBaseMotionTransport  # noqa: E402

DEFAULT_TTL_MS = 150
TICK_INTERVAL_S = 0.03  # 30ms: several ticks per TTL window, well under any sane command TTL
PROMPT = "target (x y) or command> "

EOF = object()  # sentinel distinct from None (poll timed out) and "" (blank line typed)


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--execute", action="store_true", help="use the REAL LeKiwiTransport instead of the fake one (still requires typing ENABLE to move anything)")
    p.add_argument("--remote-ip", default=os.environ.get("KC_REMOTE_IP"), help="Pi LAN address, only used with --execute (env KC_REMOTE_IP)")
    p.add_argument("--robot-id", default=os.environ.get("KC_ROBOT_ID"), help="LeKiwi calibration id, only used with --execute (env KC_ROBOT_ID)")
    p.add_argument("--command-ttl-ms", type=int, default=DEFAULT_TTL_MS, help=f"command lease, milliseconds (default {DEFAULT_TTL_MS}, deliberately short)")
    p.add_argument("--vx-max", type=float, default=None, help="override GatewayLimits.vx_max_mps for this session only (must be > 0; default: production GatewayLimits default)")
    p.add_argument("--vy-max", type=float, default=None, help="override GatewayLimits.vy_max_mps for this session only (must be > 0; default: production GatewayLimits default)")
    p.add_argument("--omega-max", type=float, default=None, help="override GatewayLimits.omega_max_rps for this session only (must be > 0; default: production GatewayLimits default)")
    return p


def build_transport(args):
    if not args.execute:
        print("harness: --dry-run (default) -- using FakeBaseMotionTransport, no device will be opened")
        return FakeBaseMotionTransport()
    if not args.remote_ip or not args.robot_id:
        sys.exit("harness: --execute requires --remote-ip/KC_REMOTE_IP and --robot-id/KC_ROBOT_ID")
    from lerobot_transport import LeKiwiTransport  # deferred: only imported/needed in --execute mode

    print(f"harness: --execute -- constructing the REAL LeKiwiTransport (robot-id={args.robot_id!r})")
    transport = LeKiwiTransport(remote_ip=args.remote_ip, robot_id=args.robot_id)
    transport.connect()
    return transport


def build_limits(args):
    """GatewayLimits() production defaults, per-field-overridden only where a
    flag was given. GatewayLimits.__post_init__ already rejects any field
    <= 0 -- that invariant is never bypassed here, only surfaced with a
    clearer, harness-level message instead of a raw dataclass traceback."""
    overrides = {}
    if args.vx_max is not None:
        overrides["vx_max_mps"] = args.vx_max
    if args.vy_max is not None:
        overrides["vy_max_mps"] = args.vy_max
    if args.omega_max is not None:
        overrides["omega_max_rps"] = args.omega_max
    try:
        return GatewayLimits(**overrides)
    except ValueError as e:
        sys.exit(f"harness: invalid limit override: {e}")


class StdinLineSource:
    """Polls real stdin with a short timeout instead of blocking on input(),
    so run_session's loop can also call gateway.tick() on every pass without
    waiting for a full line of operator input to arrive first.

    Deliberately does NOT call select() on sys.stdin (a buffered
    TextIOWrapper) and then sys.stdin.readline(): readline() can read ahead
    past one line in a single underlying read(), after which select()
    correctly reports the OS-level fd as not-ready even though a complete
    line is already sitting in that hidden buffer -- poll() would then
    report a timeout, gateway.tick() would keep running, and an
    already-typed command would sit unprocessed (its TTL clock not even
    started) until unrelated further input happened to make select() ready
    again. Confirmed live: two lines sent together, then a pause, then a
    third -- the second line was not processed until the third arrived,
    with none of the intervening ticks visible to it. Reading the raw fd
    directly and doing line-buffering here instead closes that gap: a
    complete line already sitting in OUR buffer is always drained before
    select() is consulted at all, so it can never go unnoticed."""

    def __init__(self, fd=None):
        self._fd = fd if fd is not None else sys.stdin.fileno()
        self._buf = b""
        self._eof = False

    def poll(self, timeout_s):
        line = self._pop_buffered_line()
        if line is not None:
            return line
        if self._eof:
            return EOF

        ready, _, _ = select.select([self._fd], [], [], timeout_s)
        if not ready:
            return None

        chunk = os.read(self._fd, 4096)
        if chunk == b"":
            self._eof = True
            if self._buf:
                line, self._buf = self._buf.decode(errors="replace").rstrip("\r"), b""
                return line
            return EOF

        self._buf += chunk
        line = self._pop_buffered_line()
        return line  # None is correct here too: bytes arrived but no full line yet -- go tick again

    def _pop_buffered_line(self):
        if b"\n" not in self._buf:
            return None
        line, _, self._buf = self._buf.partition(b"\n")
        return line.decode(errors="replace").rstrip("\r")


def _handle_line(line, gateway, controller, clock, command_ttl_ms, target_id, print_fn):
    """Process one operator input line. Returns (quit_requested, target_id)."""
    upper = line.upper()
    if upper == "QUIT":
        return True, target_id
    if upper == "STOP":
        gateway.operator_stop()
        print_fn("harness: OPERATOR STOP latched -- zero, suppressed until RESET")
        return False, target_id
    if upper == "RESET":
        gateway.reset()
        print_fn("harness: operator-stop latch cleared (autonomous mode unchanged, still requires ENABLE)")
        return False, target_id
    if upper == "ENABLE":
        gateway.enable_autonomous()
        print_fn("harness: autonomous mode ENABLED -- a fresh target observation is required before any motion")
        return False, target_id
    if upper.startswith("TARGET "):
        target_id = line.split(None, 1)[1]
        print_fn(f"harness: target_id set to {target_id!r} (velocity will not carry over from the previous target)")
        return False, target_id

    parts = line.split()
    if len(parts) != 2:
        print_fn("harness: type 'x y', ENABLE, STOP, RESET, TARGET <id>, or QUIT")
        return False, target_id
    try:
        x, y = float(parts[0]), float(parts[1])
    except ValueError:
        print_fn("harness: x and y must be numbers")
        return False, target_id

    now_ns = clock.now_ns()
    obs = TargetObservation(target_id=target_id, timestamp_ns=now_ns, x_m=x, y_m=y, quality=0.9)
    command, mobile_state, reason = controller.step(obs, now_ns=now_ns)
    # command_ttl_ns is per-controller config, not per-call -- honor the harness's short TTL here.
    command = command.__class__(**{**command.__dict__, "expiry_ns": now_ns + command_ttl_ms * 1_000_000})
    vx, vy, omega, outcome, gw_reason = gateway.submit_command(command)
    print_fn(f"  mobile_approach: {mobile_state.value} ({reason})")
    print_fn(f"  gateway: {outcome.value} vx={vx:.3f} vy={vy:.3f} omega={omega:.3f}  ({gw_reason})")
    return False, target_id


def run_session(transport, clock, gateway, controller, line_source, *, command_ttl_ms=DEFAULT_TTL_MS, tick_interval_s=TICK_INTERVAL_S, print_fn=print):
    """Single-threaded: poll for input for at most tick_interval_s, then call
    gateway.tick() regardless of whether a line arrived. This is what makes
    the gateway's own TTL actually fire on an unrefreshed lease -- previously
    nothing called tick() outside the test suite, so only the LeKiwi host's
    independent ~500ms watchdog would ever have stopped the base. One call
    site, one call at a time -- no thread, no concurrent access to gateway
    state, so no new race is introduced into the safety layer.

    Returns the number of tick() calls made (tests assert against this).
    """
    target_id = "manual_target"
    last_tick_outcome = None
    tick_count = 0

    print_fn("harness: autonomous mode DISABLED, effective command ZERO. Type ENABLE to arm, STOP for emergency stop.")
    print_fn(f"harness: command TTL = {command_ttl_ms}ms, tick interval = {tick_interval_s * 1000:.0f}ms")
    print_fn(PROMPT, end="", flush=True)

    try:
        while True:
            _, _, _, outcome, reason = gateway.tick()
            tick_count += 1
            if outcome != last_tick_outcome:
                print_fn(f"\nharness: tick -> {outcome.value} ({reason})")
                last_tick_outcome = outcome
                print_fn(PROMPT, end="", flush=True)

            line = line_source.poll(tick_interval_s)
            if line is EOF:
                break
            if line is None:
                continue  # timed out with no input -- loop back to tick() again
            line = line.strip()
            if not line:
                print_fn(PROMPT, end="", flush=True)
                continue

            quit_requested, target_id = _handle_line(line, gateway, controller, clock, command_ttl_ms, target_id, print_fn)
            if quit_requested:
                break
            print_fn(PROMPT, end="", flush=True)
    finally:
        gateway.operator_stop()
        if hasattr(transport, "disconnect"):
            try:
                transport.disconnect()
            except Exception as e:  # noqa: BLE001 -- best-effort shutdown, never mask the real exit path
                print_fn(f"harness: transport disconnect raised (ignored on exit): {e!r}")
        print_fn("harness: stopped, exiting")

    return tick_count


def main(argv=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    transport = build_transport(args)
    clock = MonotonicClock()
    limits = build_limits(args)
    gateway = StableMotionGateway(transport, clock, limits)
    controller = MobileApproachController()

    print(f"harness: gateway limits vx_max={limits.vx_max_mps} vy_max={limits.vy_max_mps} omega_max={limits.omega_max_rps}")
    run_session(transport, clock, gateway, controller, StdinLineSource(), command_ttl_ms=args.command_ttl_ms)


if __name__ == "__main__":
    main()
