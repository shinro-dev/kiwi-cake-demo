#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_cake_showcase_resident.py: the four Cake beats against a REAL resident, as an opt-in
# integration test, driven through capability_check.CapabilityCheck with the real capability adapter, ValidatePlan and
# the flight-ring state path.
#
# OPT IN. Skipped unless the environment names a running, freshly prepared resident, as
# autonomy/tests/test_cake_thin_path.py is:
#   KC_CAKE_PROBE    the admin-probe binary
#   KC_CAKE_SOCKET   the admin socket of a running resident
#   KC_CAKE_PLANS    the directory holding its prepared mobile-demo*.cakepln (demo-plan build-mobile)
# The resident's capability slot must still carry the inert capsule, or the test is skipped with the reason. Nothing
# here starts a resident, opens a socket of its own or touches a device. The host read is a stand-in that says HELD:
# a fixture resident supervises no motor host.
#
# DEVELOPMENT EVIDENCE, NOT A HARDWARE RESULT. On the x86-64 plan-resident fixture profile the update engine,
# dispatcher, transaction coordinator and admin socket are the shipped code; the LeKiwi resident is a separate check.
# RESIDENT UPTIME: on the Cake build from 68f0911 a swap is ABORTED once the resident has been up for more than about
# 30 s (test_cake_thin_path.py), so run this within that window of starting it.
#
# The beats, in this order: the tampered twin (the swapped Plan, one byte flipped here) refused before any transaction
# with the slot, session and generation unchanged; abort-swap ABORTED with its path and the slot unchanged; the swapped
# Plan COMPLETED and OBSERVED at generation + 1 with its path from CREATED; undo (the STARTING Plan) back to the inert
# capsule.
# Run directly: python3 packages/demo-runtime/tests/test_cake_showcase_resident.py
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from demo_runtime import cake_activation, cake_admin, capability_check, mission  # noqa: E402
from demo_runtime.mission_service import capability_adapter  # noqa: E402

PROBE, SOCKET, PLANS = (os.environ.get(name) for name in ("KC_CAKE_PROBE", "KC_CAKE_SOCKET", "KC_CAKE_PLANS"))
CAPABILITY_SLOT = os.environ.get("KC_CAKE_CAPABILITY_SLOT", "505054304050806000016d6f62696c65")
INERT_CAPSULE = os.environ.get("KC_CAKE_INERT_CAPSULE", "b9b1f7e4d274e289635930042022e0b6abc77c1f3b6b5ecd3bf5fad76d148f8e")
MOBILE_CAPSULE = os.environ.get("KC_CAKE_MOBILE_CAPSULE", "87dee37bc60fd377ed43bb5ebc7070deb7c8d0350fb4f29b4034ad4686f341e4")


@unittest.skipUnless(PROBE and SOCKET and PLANS, "no resident named (KC_CAKE_PROBE, KC_CAKE_SOCKET, KC_CAKE_PLANS)")
class TheFourBeatsAgainstAResident(unittest.TestCase):
    def setUp(self):
        status = cake_admin.run_probe(PROBE, SOCKET, "get-status")
        if not status.get("ok"):
            self.skipTest(f"the resident did not answer get-status ({status.get('reason_code')})")
        self.session = status["fields"].get("session_uuid")
        self.build = status["fields"].get("build_identity")
        start = cake_activation.slot_reading(cake_activation.read_slots_text(PROBE, SOCKET), slot_uuid=CAPABILITY_SLOT,
                                             active_capsule=MOBILE_CAPSULE)
        if start["read"] != cake_activation.READ_OK or start["capsule"] != INERT_CAPSULE:
            self.skipTest(f"the capability slot is not in its starting state (read {start['read']}, capsule {start['capsule']})")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with open(os.path.join(PLANS, "mobile-demo-swapped.cakepln"), "rb") as handle:
            swapped = bytearray(handle.read())
        swapped[len(swapped) // 2] ^= 0x01
        tampered = os.path.join(self.tmp.name, "mobile-demo-swapped-tampered.cakepln")
        with open(tampered, "wb") as handle:
            handle.write(bytes(swapped))
        paths = {"mobile-demo-swapped": os.path.join(PLANS, "mobile-demo-swapped.cakepln"),
                 "mobile-demo-active-successor-abort-swap": os.path.join(PLANS, "mobile-demo-active-successor-abort-swap.cakepln"),
                 "mobile-demo-swapped-tampered": tampered, "mobile-demo": os.path.join(PLANS, "mobile-demo.cakepln")}
        roles = {"mobile-demo-swapped": "activate", "mobile-demo-active-successor-abort-swap": "refused-demo",
                 "mobile-demo-swapped-tampered": "tampered-demo", "mobile-demo": "undo"}
        self.candidates = cake_activation.PreparedCandidates(paths, roles=roles)
        adapter = capability_adapter(probe_path=PROBE, socket_path=SOCKET, slot_uuid=CAPABILITY_SLOT,
                                     active_capsule=MOBILE_CAPSULE, candidates=self.candidates,
                                     fixture_build_identity=self.build)
        held = {"session_uuid": self.session, "build_identity": self.build, "host_running": False,
                "reason_code": "supervisor_slot_absent", "supervisor_slot": {"slot_present": False, "health_read": True}}
        self.check = capability_check.CapabilityCheck(
            os.path.join(self.tmp.name, capability_check.CHECKS_FILE_NAME), capability=adapter, candidates=self.candidates,
            read_binding=lambda: dict(held), runtime_session="showcase-test", close_wait_s=30.0,
            validate=cake_activation.plan_validator(PROBE, SOCKET), read_flight=cake_activation.flight_reader(PROBE, SOCKET))
        self.addCleanup(self.check.close)

    def apply(self, key):
        answer = self.check.apply(key, self.candidates.digest(key), self.session, claim=lambda: None)
        self.assertTrue(answer.get("ok"), answer)
        return answer

    def test_the_four_beats(self):
        tampered = self.apply("mobile-demo-swapped-tampered")
        self.assertEqual(tampered["activation_state"], cake_activation.REFUSED_BEFORE_TRANSACTION, tampered)
        self.assertEqual(tampered["refusal"]["kind"], "integrity", tampered)
        self.assertEqual(tampered["validation"]["verdict"], cake_activation.PLAN_INVALID, tampered)
        self.assertEqual(tampered["state_path"]["read"], cake_activation.PATH_NO_TRANSACTION)
        self.assertIs(tampered["slot_changed"], False)
        self.assertFalse(tampered["resident_session_changed"])

        aborted = self.apply("mobile-demo-active-successor-abort-swap")
        self.assertEqual(aborted["activation_state"], mission.ACTIVATION_REJECTED, aborted)
        self.assertEqual(aborted["state_path"]["terminal"], "ABORTED", aborted["state_path"])
        self.assertIs(aborted["slot_changed"], False)

        swapped = self.apply("mobile-demo-swapped")
        self.assertEqual(swapped["validation"]["verdict"], cake_activation.PLAN_VALID, swapped)
        self.assertEqual(swapped["activation_state"], mission.ACTIVATION_OBSERVED, swapped)
        self.assertEqual(swapped["state_path"]["states"][0], "CREATED", swapped["state_path"])
        self.assertEqual(swapped["state_path"]["terminal"], "COMPLETED", swapped["state_path"])
        self.assertEqual(int(swapped["slot_after"]["generation"]), int(swapped["slot_before"]["generation"]) + 1)
        self.assertEqual(swapped["slot_after"]["capsule"], MOBILE_CAPSULE)

        undo = self.apply("mobile-demo")
        self.assertEqual(undo["activation_state"], mission.ACTIVATION_COMPLETED_NOT_OBSERVED, undo)
        self.assertEqual(undo["slot_after"]["capsule"], INERT_CAPSULE)
        self.assertIs(undo["slot_changed"], True)
        self.assertEqual(undo["state_path"]["terminal"], "COMPLETED")

        # Every beat met its role's expectation, and the session never changed.
        view = {entry["key"]: entry for entry in self.candidates.view()}
        for answer in (tampered, aborted, swapped, undo):
            self.assertEqual(answer["activation_state"], view[answer["candidate_key"]]["expect"])
            self.assertFalse(answer["resident_session_changed"])


if __name__ == "__main__":
    unittest.main()
