#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""A tiny injectable monotonic-clock abstraction so TTL/expiry logic in
stable_motion_gateway.py never needs real time.sleep() in tests."""

import time
from abc import ABC, abstractmethod


class Clock(ABC):
    @abstractmethod
    def now_ns(self) -> int:
        ...


class MonotonicClock(Clock):
    """The real clock -- for actual (future) hardware integration only."""

    def now_ns(self) -> int:
        return time.monotonic_ns()


class FakeClock(Clock):
    """Deterministic clock for tests: time only moves when advance() is called."""

    def __init__(self, start_ns: int = 0):
        self._now_ns = start_ns

    def now_ns(self) -> int:
        return self._now_ns

    def advance(self, delta_ns: int) -> int:
        if delta_ns < 0:
            raise ValueError("delta_ns must be non-negative")
        self._now_ns += delta_ns
        return self._now_ns

    def set(self, now_ns: int) -> None:
        self._now_ns = now_ns
