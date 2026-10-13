#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_cake_thin_path.py: the thin path, with actuation disabled, as an opt-in integration test.
# Cake lifecycle -> the capability process -> the stable motion gateway -> an in-memory recorder.
#
# OPT IN. Every case that needs a resident is skipped unless the environment names a running one, in the same
# way packages/demo-runtime/tests/test_cake_admin.py gates its live case. Nothing here starts a resident,
# prepares a Plan, opens a socket of its own or touches a device.
#
# DEVELOPMENT EVIDENCE, NOT A HARDWARE RESULT. The hosting process is the x86-64 plan-resident fixture profile,
# which the Cake repository declares a test tool. The update engine, dispatcher, transaction coordinator and
# admin socket are the real shipped code; only the profile expression differs. The production target is the
# LeKiwi aarch64 resident and profile, and that verification is a separate requirement.
#
# GATEWAY ACCEPTANCE IS NOT PHYSICAL EXECUTION. An APPLIED command is recorded by
# transport.FakeBaseMotionTransport, which holds it in memory, imports no lerobot and touches no device. The
# real transport, lerobot_transport.LeKiwiTransport, is never constructed, and this file asserts that rather
# than assuming it.
#
# WHAT IT NEEDS, all read from the environment so that no machine path is committed here:
#
#   KC_CAKE_PROBE    the admin-probe binary
#   KC_CAKE_SOCKET   the admin socket of a running resident
#   KC_CAKE_PLANS    a directory holding the prepared mobile-demo.cakepln and mobile-demo-swapped.cakepln
#
# The resident must be freshly prepared and still in its starting state, that is, its capability slot carries
# the inert capsule. The sequence below moves that slot and then returns it, so a run against the same resident
# is normally repeatable; when the starting state does not hold it is skipped rather than failed, with the
# reason reported.
#
# TWO MEASURED CONDITIONS THAT MAKE A HEALTHY PATH LOOK BROKEN:
#   - a resident that STARTED while another resident was running comes up with no snapshot. `list-slots` then
#     answers `status unavailable` and an ApplyPlan is ABORTED. Restarting it alone against the same store
#     fixes it, so start one resident at a time. This one is reported as a skip;
#   - RESIDENT UPTIME. On the Cake build from 68f0911 the swap is ABORTED once the resident has been up for more
#     than about 30 s: measured on fresh stores, COMPLETED at 5 s and 20 s of uptime, ABORTED at 40 s, 60 s, 40 s
#     and 90 s. The cause is in Cake, not in the store or in this path: the transaction barrier passes a fixed
#     30 s as an absolute deadline that the supervisor module compares with time since resident start. An
#     earlier version of this comment blamed a store reused across activate and revert cycles; that was wrong,
#     and every abort it described happened on a resident that had been up for minutes. Run this test within
#     about 30 s of starting the resident, or against a Cake build that carries the fix.
#
# The deployment identifiers default to the same tracked fixture values that
# autonomy/tests/test_cake_capability_adapter.py already uses, and each can be overridden by the environment
# for a differently prepared deployment:
#
#   KC_CAKE_CAPABILITY_SLOT, KC_CAKE_INERT_CAPSULE, KC_CAKE_MOBILE_CAPSULE
#
# Run directly: python3 autonomy/tests/test_cake_thin_path.py
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "motion"))
sys.path.insert(0, ROOT)

from cake_capability_adapter import (  # noqa: E402
    ACCEPTED,
    REJECTED_BY_CAKE,
    ActivationRequest,
    CakeCapabilityAdapter,
    PreparedCandidate,
    activation_confirmed,
    parse_activation_answer,
)
from clock import FakeClock  # noqa: E402
from stable_motion_gateway import GatewayLimits, StableMotionGateway  # noqa: E402
from transport import FakeBaseMotionTransport  # noqa: E402

from autonomy.motion.mobile_approach import DesiredBaseMotion  # noqa: E402
from autonomy.motion.mobile_approach_service import ApproachService  # noqa: E402

CAPABILITY_SLOT = os.environ.get("KC_CAKE_CAPABILITY_SLOT", "505054304050806000016d6f62696c65")
INERT_CAPSULE = os.environ.get("KC_CAKE_INERT_CAPSULE",
                               "b9b1f7e4d274e289635930042022e0b6abc77c1f3b6b5ecd3bf5fad76d148f8e")
MOBILE_CAPSULE = os.environ.get("KC_CAKE_MOBILE_CAPSULE",
                                "87dee37bc60fd377ed43bb5ebc7070deb7c8d0350fb4f29b4034ad4686f341e4")

# A digest no resident admitted, so the candidate carrying it is refused. It is not an upload and not a
# corrupted binary; it names a capsule this resident does not hold.
UNADMITTED_CAPSULE = "ab" * 32

# THE NONZERO FIXTURE, explicitly labelled. cube_far sits inside the trusted localization envelope
# (x 0.30 to 1.10, |y| <= 0.40) and OUTSIDE the handoff region (x 0.30 to 0.40, |y| <= 0.12), so the controller
# must drive rather than report manipulation ready. A fixture at x_m 0.40 is inside the handoff box and its
# zero command is the controller correctly declining, not a broken path.
FIXTURE = {"target_id": "cube_far", "timestamp_ns": 1_000_000_000, "frame": "base",
           "x_m": 0.95, "y_m": 0.10, "quality": 0.92}


def _deployment():
    """The three machine-specific values, or None when the environment does not name a running resident."""
    probe = os.environ.get("KC_CAKE_PROBE")
    socket_path = os.environ.get("KC_CAKE_SOCKET")
    plans = os.environ.get("KC_CAKE_PLANS")
    if not probe or not socket_path or not plans:
        return None
    if not os.path.exists(socket_path) or not os.path.isdir(plans):
        return None
    return probe, socket_path, plans


def _request(probe, socket_path, operation, payload=None, uuid=None):
    """One admin-probe request. The payload is passed as a file, which is what the tool takes."""
    command = [probe, "request", socket_path, operation]
    if payload is not None:
        command += ["--payload", payload]
    if uuid is not None:
        command += ["--uuid", uuid]
    return subprocess.run(command, capture_output=True, text=True, timeout=30).stdout


def _submit(probe, socket_path, candidate, tmpdir):
    """Submit a prepared candidate under its own operation uuid, carrying exactly its payload bytes.

    The bytes are written out unchanged, so a retry built from the same PreparedCandidate carries a
    byte-identical payload under the same uuid, which is what the resident requires of a retry.
    """
    blob = os.path.join(tmpdir, f"{candidate.operation_uuid}.payload")
    with open(blob, "wb") as handle:
        handle.write(candidate.payload())
    return parse_activation_answer(
        _request(probe, socket_path, candidate.operation, payload=blob, uuid=candidate.operation_uuid))


class TheOperationIdentityRule(unittest.TestCase):
    """The retry rule, which needs no resident: same uuid and same bytes is a retry, anything else is not."""

    def test_a_retry_is_the_same_identity_and_the_same_bytes(self):
        plan = b"prepared-candidate-bytes"
        first = PreparedCandidate(operation_uuid="a" * 32, candidate_plan=plan)
        retry = PreparedCandidate(operation_uuid="a" * 32, candidate_plan=plan)
        self.assertTrue(retry.is_retry_of(first))
        self.assertEqual(retry.payload(), first.payload())

    def test_the_same_identity_with_different_bytes_is_not_a_retry(self):
        first = PreparedCandidate(operation_uuid="a" * 32, candidate_plan=b"one")
        other = PreparedCandidate(operation_uuid="a" * 32, candidate_plan=b"two")
        self.assertFalse(other.is_retry_of(first))

    def test_a_new_intended_operation_gets_a_fresh_identity(self):
        plan = b"prepared-candidate-bytes"
        first = PreparedCandidate(operation_uuid="a" * 32, candidate_plan=plan)
        fresh = PreparedCandidate(operation_uuid="b" * 32, candidate_plan=plan)
        self.assertFalse(fresh.is_retry_of(first))


class TheThinPathAgainstARealResident(unittest.TestCase):
    """Skipped unless KC_CAKE_PROBE, KC_CAKE_SOCKET and KC_CAKE_PLANS name a prepared, running resident.

    One test method walks the whole sequence because the sequence is stateful: the activation moves the slot,
    so the steps are not independent and must not be reordered by the runner.
    """

    def test_the_thin_path_from_cake_lifecycle_to_the_recording_transport(self):
        deployment = _deployment()
        if deployment is None:
            raise unittest.SkipTest(
                "KC_CAKE_PROBE, KC_CAKE_SOCKET and KC_CAKE_PLANS do not name a prepared running resident")
        probe, socket_path, plans = deployment
        starting_plan = os.path.join(plans, "mobile-demo.cakepln")
        swapped_plan = os.path.join(plans, "mobile-demo-swapped.cakepln")
        for artifact in (starting_plan, swapped_plan):
            if not os.path.exists(artifact):
                raise unittest.SkipTest(f"KC_CAKE_PLANS holds no {os.path.basename(artifact)}")

        # THE RESIDENT MUST HAVE A SNAPSHOT. A plan-resident that STARTED while another plan-resident was running
        # comes up without one, and then `list-slots` answers `status unavailable` and an ApplyPlan is ABORTED.
        # Restarting it on its own, against the same store, fixes it. That is an environment fault rather than a
        # failure of this path, so it is reported as the reason for a skip and not as a broken thin path.
        listing = _request(probe, socket_path, "list-slots")
        if not listing.startswith("status ok"):
            raise unittest.SkipTest(
                "the resident did not answer list-slots, which is what a resident started alongside another one "
                "does; restart it on its own against the same store")

        adapter = CakeCapabilityAdapter(capability="mobile_approach", slot_uuid=CAPABILITY_SLOT,
                                        active_capsule=MOBILE_CAPSULE,
                                        probe=lambda: _request(probe, socket_path, "list-slots"))

        # 1. The inert capability slot provides no usable approach output.
        adapter.observe()
        if adapter.last is None or adapter.last.capsule != INERT_CAPSULE:
            raise unittest.SkipTest(
                "the capability slot is not on the inert capsule, so this resident is not in its starting "
                "state; this sequence needs a freshly prepared one")
        self.assertFalse(adapter.usable_output_allowed())
        self.assertIsNone(adapter.running_identity())

        # THE TRAP. The holder slot carries the active capsule by design, so the engine has an admitted
        # mapping to activate onto. A reader that asked whether the capsule is present anywhere would answer
        # yes before anything was activated and erase the wait this sequence exists to show.
        listing = _request(probe, socket_path, "list-slots")
        self.assertIn(MOBILE_CAPSULE, listing)
        self.assertFalse(adapter.usable_output_allowed())

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1b. A candidate naming a capsule the resident did not admit is refused, and the slot is untouched.
            before = (adapter.last.capsule, adapter.last.generation)
            refused = ActivationRequest(operation_uuid=os.urandom(16).hex(), slot_uuid=CAPABILITY_SLOT,
                                        capsule=UNADMITTED_CAPSULE)
            blob = os.path.join(tmpdir, "refused.payload")
            with open(blob, "wb") as handle:
                handle.write(refused.payload())
            answer = parse_activation_answer(
                _request(probe, socket_path, "replace-slot", payload=blob, uuid=refused.operation_uuid))
            self.assertEqual(answer["outcome"], REJECTED_BY_CAKE)
            # `status ok` DOES NOT MEAN THE UPDATE HAPPENED. An ABORTED transaction arrives under status ok,
            # so transaction_phase is the only thing that decides the outcome.
            self.assertEqual(answer["status"], "ok")
            self.assertEqual(answer["transaction_phase"], "ABORTED")
            adapter.observe()
            self.assertEqual((adapter.last.capsule, adapter.last.generation), before)
            self.assertFalse(adapter.usable_output_allowed())

            # 2. ApplyPlan carrying the prepared candidate is the supported activation.
            with open(swapped_plan, "rb") as handle:
                candidate = PreparedCandidate(operation_uuid=os.urandom(16).hex(), candidate_plan=handle.read())
            answer = _submit(probe, socket_path, candidate, tmpdir)
            self.assertEqual(answer["outcome"], ACCEPTED)
            self.assertEqual(answer["transaction_phase"], "COMPLETED")
            # CONFIRMED BY THE OBSERVED SLOT, never by the answer.
            self.assertTrue(activation_confirmed(adapter, answer))
            self.assertEqual(adapter.last.capsule, MOBILE_CAPSULE)
            self.assertEqual(adapter.last.generation, before[1] + 1)
            identity = adapter.running_identity()
            self.assertIsNotNone(identity)

            # The candidate moves the capability slot and the holder slot together, which is why one operation
            # reaches it and a single-slot replacement does not. The holder takes the inert capsule in exchange.
            slots = _request(probe, socket_path, "list-slots")
            self.assertIn(f"capsule={INERT_CAPSULE} ", slots)
            self.assertIn("name=mobile_approach.holder state=ACTIVE", slots)
            self.assertIn("name=supervisor.lekiwi state=ACTIVE", slots)

            # 3. A labelled input whose geometry requires an approach, through the real capability process.
            published = []
            script = [json.dumps(FIXTURE).encode()]
            service = ApproachService(lambda _timeout: script.pop(0) if script else service.stop(),
                                      lambda payload: published.append(json.loads(payload.decode())),
                                      identity=identity)
            service.run()
            self.assertEqual(len(published), 1)
            command = dict(published[0])
            # The command_id prefix is a CORRELATION LABEL. Availability was decided from the slot above; the
            # prefix then catches output from a generation that is no longer running. It is never the authority.
            self.assertTrue(adapter.accepts_command_from(command["command_id"]))
            self.assertNotEqual((command["vx_mps"], command["vy_mps"]), (0.0, 0.0))

            # THE DESTINATION, NAMED. FakeBaseMotionTransport records in memory and touches no device.
            recorder = FakeBaseMotionTransport()
            self.assertEqual(type(recorder).__module__, "transport")
            # The real transport is never constructed on this path. The instance check is kept from the
            # harness, and the sys.modules check is what actually proves lerobot was never loaded.
            import lerobot_transport  # noqa: E402
            real = [getattr(lerobot_transport, name, None) for name in dir(lerobot_transport)]
            self.assertFalse(any(isinstance(recorder, candidate_type) for candidate_type in real
                                 if isinstance(candidate_type, type) and candidate_type is not object
                                 and "LeKiwi" in getattr(candidate_type, "__name__", "")))
            self.assertFalse([name for name in sys.modules if name == "lerobot" or name.startswith("lerobot.")])

            parsed = DesiredBaseMotion(**command)
            clock = FakeClock()
            clock.set(command["timestamp_ns"])
            gateway = StableMotionGateway(recorder, clock, GatewayLimits())
            outcome = gateway.submit_command(parsed)[3]
            self.assertEqual(getattr(outcome, "value", outcome), "ZERO_AUTONOMOUS_DISABLED")
            # A ZERO IS FORWARDED WHILE AUTONOMOUS IS DISABLED. No velocity is, which is the claim that matters.
            self.assertEqual(recorder.last_sent, (0.0, 0.0, 0.0))

            enabled_recorder = FakeBaseMotionTransport()
            enabled_clock = FakeClock()
            enabled_clock.set(command["timestamp_ns"])
            enabled = StableMotionGateway(enabled_recorder, enabled_clock, GatewayLimits())
            enabled.enable_autonomous()
            outcome = enabled.submit_command(parsed)[3]
            self.assertEqual(getattr(outcome, "value", outcome), "APPLIED")
            applied = enabled.applied_velocity
            # GATEWAY ACCEPTANCE IS NOT PHYSICAL EXECUTION: this is where an APPLIED command went.
            self.assertEqual(enabled_recorder.last_sent, applied)
            self.assertNotEqual(applied[:2], (0.0, 0.0))
            limits = GatewayLimits()
            # THE BOUND THAT ACTED IS THE PER-UPDATE SLEW LIMIT, not the velocity ceiling. The capability
            # published vx 0.10 and vy 0.06, both already under the 0.15 ceiling; the gateway ramps from a zero
            # baseline by at most max_dvx_per_update_mps and max_dvy_per_update_mps each update.
            self.assertLessEqual(abs(applied[0]), limits.max_dvx_per_update_mps)
            self.assertLessEqual(abs(applied[1]), limits.max_dvy_per_update_mps)
            self.assertLessEqual(abs(applied[0]), limits.vx_max_mps)
            self.assertLessEqual(abs(applied[1]), limits.vy_max_mps)

            # 4. Returning the slot to the inert capsule stops usable output, and output still labelled with
            # the old generation is refused.
            with open(starting_plan, "rb") as handle:
                back = PreparedCandidate(operation_uuid=os.urandom(16).hex(), candidate_plan=handle.read())
            answer = _submit(probe, socket_path, back, tmpdir)
            self.assertEqual(answer["outcome"], ACCEPTED)
            adapter.observe()
            self.assertFalse(adapter.usable_output_allowed())
            self.assertIsNone(adapter.running_identity())
            self.assertFalse(adapter.accepts_command_from(command["command_id"]))

            # 5. The two-slot swap did not disturb the supervisor or the holder. The supervisor slot is outside
            # every closure this deployment can compute, and that is checked here rather than assumed.
            after = _request(probe, socket_path, "list-slots")
            self.assertIn("name=supervisor.lekiwi state=ACTIVE", after)
            self.assertIn("name=mobile_approach.holder state=ACTIVE", after)


if __name__ == "__main__":
    unittest.main()
