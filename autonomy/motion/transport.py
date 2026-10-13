#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""BaseMotionTransport: the narrow boundary between stable_motion_gateway.py
and whatever actually moves the base. The gateway only ever talks to this
interface -- see autonomy/motion/lerobot_transport.py for the one
source-verified, not-yet-instantiated real implementation, and
FakeBaseMotionTransport below for tests."""

from abc import ABC, abstractmethod


class BaseMotionTransport(ABC):
    @abstractmethod
    def send_velocity(self, vx_mps: float, vy_mps: float, omega_rps: float) -> None:
        """Command a holonomic base velocity. Must be a no-op or raise if
        not connected -- never silently do something else."""

    @abstractmethod
    def send_zero(self) -> None:
        """Command zero velocity. Must always be safe to call, connected or not."""

    @abstractmethod
    def is_connected(self) -> bool:
        ...


class FakeBaseMotionTransport(BaseMotionTransport):
    """Deterministic, in-memory transport for tests. Records every call so
    tests can assert on exactly what the gateway tried to send, and can be
    made to fail/disconnect on demand."""

    def __init__(self, connected: bool = True):
        self._connected = connected
        self.calls = []  # list of ("velocity", vx, vy, omega) or ("zero",)
        self.last_sent = (0.0, 0.0, 0.0)
        self.raise_on_send = None  # set to an Exception instance/class to simulate a transport fault

    def send_velocity(self, vx_mps, vy_mps, omega_rps) -> None:
        if self.raise_on_send is not None:
            raise self.raise_on_send
        self.last_sent = (vx_mps, vy_mps, omega_rps)
        self.calls.append(("velocity", vx_mps, vy_mps, omega_rps))

    def send_zero(self) -> None:
        if self.raise_on_send is not None:
            raise self.raise_on_send
        self.last_sent = (0.0, 0.0, 0.0)
        self.calls.append(("zero",))

    def is_connected(self) -> bool:
        return self._connected

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
