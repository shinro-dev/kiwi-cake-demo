# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Fakes for driving demo_runtime.capability_check.CapabilityCheck directly, with no daemon, no resident and no socket.

A check is built around a scripted capability adapter (what the resident answers to one ApplyPlan, what the slot reads
before and after) and a scripted binding read (the resident's session and a HELD host). Everything it was asked is kept
in `calls`, so a test can say what was and was not sent.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from demo_runtime import cake_activation, capability_check  # noqa: E402
from demo_runtime.mission_coordinator import CapabilityAdapter  # noqa: E402

SESSION = "01a0d8320c9c7b09b03c1f945f489104"
CAPABILITY_SLOT = "6d6f62696c655f617070726f61636801"
MOBILE_CAPSULE, INERT_CAPSULE = "b2" * 32, "c3" * 32
KEYS = {"swapped": b"the valid prepared swap plan", "abort-swap": b"the successor plan the resident aborts",
        "tampered": b"the swap plan with one byte flipped", "inert": b"the starting plan"}


def slot(capsule: str, generation: int, instance: int, *, active: bool) -> dict:
    return {"read": cake_activation.READ_OK, "slot_uuid": CAPABILITY_SLOT, "name": "mobile_approach", "state": "ACTIVE",
            "capsule": capsule, "generation": str(generation), "instance": str(instance), "session_uuid": SESSION,
            "plan_digest": "c9" * 32, "epoch": "3", "capability_active": active}


def held_binding() -> dict:
    return {"session_uuid": SESSION, "build_identity": "d3" * 32, "host_running": False,
            "reason_code": "supervisor_slot_absent", "observed_wall_utc": "2026-09-27T00:00:00.000Z",
            "supervisor_slot": {"slot_present": False, "health_read": True, "health_row": False}}


class ScriptedCheck:
    """One CapabilityCheck over prepared candidate files, a scripted answer and scripted slot readings."""

    def __init__(self, answer_text: str, slots: list, *, roles=None, **check_kwargs):
        self.tmp = tempfile.TemporaryDirectory()
        paths = {}
        for key, payload in KEYS.items():
            path = os.path.join(self.tmp.name, key + ".cakepln")
            with open(path, "wb") as handle:
                handle.write(payload)
            paths[key] = path
        self.candidates = cake_activation.PreparedCandidates(paths, roles=roles) if roles is not None else \
            cake_activation.PreparedCandidates(paths)
        self.calls: list = []
        self._slots = list(slots)
        self._answer_text = answer_text

        def submit(operation_uuid, payload):
            self.calls.append(("submit", operation_uuid, payload))
            return cake_activation.parse_activation_answer(self._answer_text)

        def read_slot():
            self.calls.append(("read_slot",))
            return dict(self._slots.pop(0)) if self._slots else None

        adapter = CapabilityAdapter(available=lambda: False, submit=submit, observe=lambda: False,
                                    candidates=self.candidates, read_slot=read_slot)
        self.check = capability_check.CapabilityCheck(os.path.join(self.tmp.name, capability_check.CHECKS_FILE_NAME),
                                                      capability=adapter, candidates=self.candidates,
                                                      read_binding=held_binding, runtime_session="runtime-session-1",
                                                      close_wait_s=1.0, **check_kwargs)

    def apply(self, key: str) -> dict:
        return self.check.apply(key, self.candidates.digest(key), SESSION, claim=lambda: None)

    def close(self):
        self.check.close()
        self.tmp.cleanup()
