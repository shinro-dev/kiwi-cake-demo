#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""StableMotionGateway: the single enforcement point between mobile_approach
(or any other DesiredBaseMotion producer) and the real base transport.

Implements the contract at docs/autonomous-demo/stable-motion-gateway-contract.md.
mobile_approach never talks to a transport directly; this module is the only
thing that does, and only through the BaseMotionTransport interface
(transport.py) -- never a direct LeRobot import.

Design summary (see class docstring for the exact rules):
- default-safe on construction: autonomous mode off, nothing sent;
- explicit enable_autonomous()/disable_autonomous()/operator_stop()/reset();
- every submit_command() is validated (finite numbers, freshness vs the last
  ACCEPTED command, not expired on arrival) before its velocity is even
  considered; a rejected command never changes what is currently being sent;
- accepted velocity is hard-clamped to configured limits and slew-limited
  against the last APPLIED velocity (reset to zero baseline on a target
  change), then pushed to the transport;
- tick() resolves everything that can happen without a new command arriving:
  TTL expiry, transport disconnect;
- any transport exception is caught, treated as a failure, and degrades to
  zero -- it never propagates as an uncontrolled continuation.
"""

import math
from dataclasses import dataclass, field
from enum import Enum


class GatewayOutcome(str, Enum):
    APPLIED = "APPLIED"                    # command accepted, velocity (possibly clamped) forwarded
    ZERO_AUTONOMOUS_DISABLED = "ZERO_AUTONOMOUS_DISABLED"
    ZERO_OPERATOR_STOPPED = "ZERO_OPERATOR_STOPPED"
    ZERO_EXPIRED = "ZERO_EXPIRED"
    ZERO_DISCONNECTED = "ZERO_DISCONNECTED"
    ZERO_TRANSPORT_FAILURE = "ZERO_TRANSPORT_FAILURE"
    REJECTED_INVALID_NUMBERS = "REJECTED_INVALID_NUMBERS"
    REJECTED_EXPIRED_ON_ARRIVAL = "REJECTED_EXPIRED_ON_ARRIVAL"
    REJECTED_STALE_OR_REPLAYED = "REJECTED_STALE_OR_REPLAYED"
    DUPLICATE_IGNORED = "DUPLICATE_IGNORED"


@dataclass(frozen=True)
class GatewayLimits:
    """Deliberately low, pre-hardware-validation demo defaults. Configuration-
    driven on purpose -- these are NOT physically validated and must be
    revisited before any real-speed test."""
    vx_max_mps: float = 0.15
    vy_max_mps: float = 0.15
    omega_max_rps: float = 0.3
    max_dvx_per_update_mps: float = 0.05
    max_dvy_per_update_mps: float = 0.05
    max_domega_per_update_rps: float = 0.2

    def __post_init__(self):
        for name in ("vx_max_mps", "vy_max_mps", "omega_max_rps",
                     "max_dvx_per_update_mps", "max_dvy_per_update_mps", "max_domega_per_update_rps"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


def _clamp(value, limit):
    return max(-limit, min(limit, value))


def _finite(*values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


class StableMotionGateway:
    def __init__(self, transport, clock, limits=None):
        self.transport = transport
        self.clock = clock
        self.limits = limits or GatewayLimits()

        self._autonomous_enabled = False
        self._operator_stopped = False

        self._last_accepted_command_id = None
        self._last_accepted_timestamp_ns = None
        self._last_accepted_target_id = None
        self._current_expiry_ns = None

        self._applied_vx = 0.0
        self._applied_vy = 0.0
        self._applied_omega = 0.0

        # Nothing is sent here. Construction only establishes the default-safe
        # LOGICAL state (autonomous_enabled=False); a transport call happens
        # only in response to an explicit action (tick()/submit_command()),
        # never as a side effect of __init__ itself.

    @property
    def autonomous_enabled(self) -> bool:
        return self._autonomous_enabled

    @property
    def operator_stopped(self) -> bool:
        return self._operator_stopped

    @property
    def applied_velocity(self):
        return (self._applied_vx, self._applied_vy, self._applied_omega)

    # -- state transitions -------------------------------------------------

    def enable_autonomous(self):
        self._autonomous_enabled = True
        self._invalidate_active_command()  # a fresh command is required; nothing replays

    def disable_autonomous(self):
        self._autonomous_enabled = False
        self._invalidate_active_command()
        self._push_zero(GatewayOutcome.ZERO_AUTONOMOUS_DISABLED)

    def operator_stop(self):
        self._operator_stopped = True
        self._invalidate_active_command()
        self._push_zero(GatewayOutcome.ZERO_OPERATOR_STOPPED)

    def reset(self):
        """Explicit re-arm after operator stop. Only clears the operator-stop
        latch -- autonomous mode is a separate, independent gate and is not
        touched here, matching operator-stop being the dominant, orthogonal
        safety layer."""
        self._operator_stopped = False

    def _invalidate_active_command(self):
        self._last_accepted_command_id = None
        self._last_accepted_timestamp_ns = None
        self._last_accepted_target_id = None
        self._current_expiry_ns = None
        self._applied_vx = self._applied_vy = self._applied_omega = 0.0

    # -- command submission --------------------------------------------------

    def submit_command(self, command):
        """command: a DesiredBaseMotion (or any object with the same fields).
        Returns (applied_vx, applied_vy, applied_omega, GatewayOutcome, reason: str)."""
        now_ns = self.clock.now_ns()

        if self._operator_stopped:
            return self._result(*self._push_zero(GatewayOutcome.ZERO_OPERATOR_STOPPED), "operator stop is latched")
        if not self._autonomous_enabled:
            return self._result(*self._push_zero(GatewayOutcome.ZERO_AUTONOMOUS_DISABLED), "autonomous mode is disabled")

        if command.command_id == self._last_accepted_command_id:
            # Exact duplicate delivery: idempotent no-op, whatever is already applied stands.
            return self._result(self._applied_vx, self._applied_vy, self._applied_omega, GatewayOutcome.DUPLICATE_IGNORED, "duplicate command_id, already applied")

        if not _finite(command.vx_mps, command.vy_mps, command.omega_rps, command.timestamp_ns, command.expiry_ns):
            return self._result(self._applied_vx, self._applied_vy, self._applied_omega, GatewayOutcome.REJECTED_INVALID_NUMBERS, "non-finite value in command")

        if self._last_accepted_timestamp_ns is not None and command.timestamp_ns <= self._last_accepted_timestamp_ns:
            # Older (or same-timestamp-different-id) than the last accepted command:
            # reject without disturbing whatever is currently applied -- a stale/
            # replayed command must never become active, but it also must not be
            # able to interrupt an already-valid newer command by forcing zero.
            return self._result(self._applied_vx, self._applied_vy, self._applied_omega, GatewayOutcome.REJECTED_STALE_OR_REPLAYED, "timestamp not newer than last accepted command")

        if now_ns >= command.expiry_ns:
            return self._result(self._applied_vx, self._applied_vy, self._applied_omega, GatewayOutcome.REJECTED_EXPIRED_ON_ARRIVAL, "command already expired on arrival")

        if not self.transport.is_connected():
            self._invalidate_active_command()
            return self._result(*self._push_zero(GatewayOutcome.ZERO_DISCONNECTED), "transport not connected")

        vx = _clamp(command.vx_mps, self.limits.vx_max_mps)
        vy = _clamp(command.vy_mps, self.limits.vy_max_mps)
        omega = _clamp(command.omega_rps, self.limits.omega_max_rps)

        if command.target_id != self._last_accepted_target_id:
            # A different target must never inherit the previous target's velocity.
            self._applied_vx = self._applied_vy = self._applied_omega = 0.0

        vx = self._applied_vx + _clamp(vx - self._applied_vx, self.limits.max_dvx_per_update_mps)
        vy = self._applied_vy + _clamp(vy - self._applied_vy, self.limits.max_dvy_per_update_mps)
        omega = self._applied_omega + _clamp(omega - self._applied_omega, self.limits.max_domega_per_update_rps)

        try:
            if vx == 0.0 and vy == 0.0 and omega == 0.0:
                self.transport.send_zero()
            else:
                self.transport.send_velocity(vx, vy, omega)
        except Exception as e:  # noqa: BLE001 -- any transport fault degrades to zero, never propagates as uncontrolled continuation
            self._invalidate_active_command()
            return self._result(self._applied_vx, self._applied_vy, self._applied_omega, GatewayOutcome.ZERO_TRANSPORT_FAILURE, f"transport raised: {e!r}")

        self._applied_vx, self._applied_vy, self._applied_omega = vx, vy, omega
        self._last_accepted_command_id = command.command_id
        self._last_accepted_timestamp_ns = command.timestamp_ns
        self._last_accepted_target_id = command.target_id
        self._current_expiry_ns = command.expiry_ns
        return self._result(vx, vy, omega, GatewayOutcome.APPLIED, "applied")

    def tick(self):
        """Resolve time/connection-based transitions that can happen with no
        new command arriving: TTL expiry and transport disconnect. Safe to
        call as often as desired; a no-op unless something needs to change."""
        if self._operator_stopped:
            return self._result(*self._push_zero(GatewayOutcome.ZERO_OPERATOR_STOPPED), "operator stop is latched")
        if not self._autonomous_enabled:
            return self._result(*self._push_zero(GatewayOutcome.ZERO_AUTONOMOUS_DISABLED), "autonomous mode is disabled")
        if not self.transport.is_connected():
            self._invalidate_active_command()
            return self._result(*self._push_zero(GatewayOutcome.ZERO_DISCONNECTED), "transport not connected")
        if self._current_expiry_ns is not None and self.clock.now_ns() >= self._current_expiry_ns:
            self._invalidate_active_command()
            return self._result(*self._push_zero(GatewayOutcome.ZERO_EXPIRED), "active command's lease expired")
        return self._result(self._applied_vx, self._applied_vy, self._applied_omega, GatewayOutcome.APPLIED, "no change")

    # -- internals -----------------------------------------------------------

    def _push_zero(self, outcome):
        try:
            self.transport.send_zero()
        except Exception:  # noqa: BLE001 -- zero is the fallback; if even that fails there is nothing more this layer can do but keep its own state at zero
            pass
        self._applied_vx = self._applied_vy = self._applied_omega = 0.0
        return self._applied_vx, self._applied_vy, self._applied_omega, outcome

    @staticmethod
    def _result(vx, vy, omega, outcome, reason):
        return vx, vy, omega, outcome, reason
