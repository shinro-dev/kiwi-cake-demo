#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_cake_capability_adapter.py: what decides that mobile_approach is available, and the four
# ways a careless reader would get it wrong. No socket, no resident, no hardware.
#
# The listings below are the real shape of `admin-probe request SOCK list-slots`, taken from a live run against
# the mobile-approach deployment.
#
# Run directly: python3 autonomy/tests/test_cake_capability_adapter.py
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "motion"))

from cake_capability_adapter import (  # noqa: E402
    ACCEPTED,
    PreparedCandidate,
    CakeCapabilityAdapter,
    PENDING,
    REFUSED_BEFORE_TRANSACTION,
    REJECTED_BY_CAKE,
    REQUEST_FAILED,
    ActivationRequest,
    activation_confirmed,
    parse_activation_answer,
    parse_slots,
)

CAPABILITY_SLOT = "505054304050806000016d6f62696c65"
HOLDER_SLOT = "50505430405080600000686f6c646572"
SUPERVISOR_SLOT = "50505430405080500000737570657276"
INERT_CAPSULE = "b9b1f7e4d274e289635930042022e0b6abc77c1f3b6b5ecd3bf5fad76d148f8e"
MOBILE_CAPSULE = "87dee37bc60fd377ed43bb5ebc7070deb7c8d0350fb4f29b4034ad4686f341e4"


def listing(*, capability_capsule=INERT_CAPSULE, capability_state="ACTIVE", generation=1):
    """A listing in which the HOLDER always carries the mobile capsule, which is the deployment's real shape."""
    return "\n".join([
        "status ok",
        "epoch 2",
        f"slot capsule={INERT_CAPSULE} config=aa generation=1 instance=1 name=supervisor.lekiwi state=ACTIVE uuid={SUPERVISOR_SLOT}",
        f"slot capsule={MOBILE_CAPSULE} config=bb generation=1 instance=2 name=mobile_approach.holder state=ACTIVE uuid={HOLDER_SLOT}",
        f"slot capsule={capability_capsule} config=cc generation={generation} instance=3 name=mobile_approach state={capability_state} uuid={CAPABILITY_SLOT}",
    ])


def adapter(text):
    made = CakeCapabilityAdapter(capability="mobile_approach", slot_uuid=CAPABILITY_SLOT,
                                 active_capsule=MOBILE_CAPSULE, probe=lambda: text)
    made.observe()
    return made


class WhatDecidesAvailability(unittest.TestCase):
    def test_the_inert_capability_slot_is_not_available(self):
        self.assertFalse(adapter(listing()).has_capability("mobile_approach"))

    def test_the_holder_holding_the_active_capsule_does_not_make_it_available(self):
        """The deployment pre-admits the active build in a holder slot so the engine has a mapping to replace
        onto. A reader that asked 'is this capsule present anywhere?' would answer yes before anything was
        activated, which would erase the wait the demonstration exists to show."""
        text = listing()
        self.assertIn(MOBILE_CAPSULE, text, "the holder really does carry the active capsule")
        self.assertFalse(adapter(text).has_capability("mobile_approach"))

    def test_the_capability_slot_on_the_active_capsule_is_available(self):
        self.assertTrue(adapter(listing(capability_capsule=MOBILE_CAPSULE, generation=2)).has_capability("mobile_approach"))

    def test_the_active_capsule_in_a_slot_that_is_not_active_is_not_available(self):
        for state in ("QUIESCING", "FAILED", "INACTIVE", "UNKNOWN"):
            with self.subTest(state=state):
                self.assertFalse(adapter(listing(capability_capsule=MOBILE_CAPSULE, capability_state=state))
                                 .has_capability("mobile_approach"))

    def test_it_answers_for_no_other_capability_name(self):
        made = adapter(listing(capability_capsule=MOBILE_CAPSULE))
        self.assertTrue(made.has_capability("mobile_approach"))
        self.assertFalse(made.has_capability("something_else"))

    def test_returning_the_slot_to_the_inert_capsule_withdraws_availability(self):
        made = adapter(listing(capability_capsule=MOBILE_CAPSULE, generation=2))
        self.assertTrue(made.usable_output_allowed())
        made._probe = lambda: listing(capability_capsule=INERT_CAPSULE, generation=3)  # noqa: SLF001
        made.observe()
        self.assertFalse(made.usable_output_allowed(), "an inert slot must allow no usable output")


class TrafficIsNotLifecycle(unittest.TestCase):
    def test_an_active_capability_that_has_published_nothing_is_still_available(self):
        """A capability legitimately publishes nothing while it waits for an observation. Absence of traffic
        does not establish inactivity, and nothing here reads a command, a count or a rate."""
        made = adapter(listing(capability_capsule=MOBILE_CAPSULE, generation=2))
        self.assertTrue(made.usable_output_allowed())

    def test_the_command_prefix_is_a_label_and_never_the_authority(self):
        """A stale publisher can still be emitting after its slot went inert. Output is refused because the
        SLOT says so, not because the traffic stopped."""
        made = adapter(listing(capability_capsule=INERT_CAPSULE))
        self.assertFalse(made.usable_output_allowed())
        stale = "mobile_approach.87dee37bc60fd377.gen2.cmd_9"
        self.assertFalse(made.accepts_command_from(stale), "an inert slot accepts nothing, whatever is labelled")

    def test_a_label_from_the_running_generation_is_accepted_and_an_older_one_is_not(self):
        made = adapter(listing(capability_capsule=MOBILE_CAPSULE, generation=2))
        identity = made.running_identity()
        self.assertIsNotNone(identity)
        self.assertTrue(made.accepts_command_from(f"{identity}.cmd_1"))
        self.assertFalse(made.accepts_command_from("mobile_approach.87dee37bc60fd377.gen1.cmd_1"))

    def test_a_label_check_never_grants_availability_on_its_own(self):
        made = adapter(listing())
        self.assertIsNone(made.running_identity())
        self.assertFalse(made.accepts_command_from("anything.at.all.cmd_1"))


class ReadingFailures(unittest.TestCase):
    def test_a_failed_read_is_reported_and_does_not_invent_an_absent_slot(self):
        made = CakeCapabilityAdapter(capability="mobile_approach", slot_uuid=CAPABILITY_SLOT,
                                     active_capsule=MOBILE_CAPSULE,
                                     probe=lambda: (_ for _ in ()).throw(OSError("socket gone")))
        made.observe()
        self.assertEqual(made.last_error, "OSError")

    def test_an_answer_that_is_not_ok_is_not_read_as_a_slot_listing(self):
        made = CakeCapabilityAdapter(capability="mobile_approach", slot_uuid=CAPABILITY_SLOT,
                                     active_capsule=MOBILE_CAPSULE, probe=lambda: "status unavailable")
        made.observe()
        self.assertEqual(made.last_error, "admin_read_not_ok")
        self.assertFalse(made.usable_output_allowed())

    def test_a_slot_line_that_cannot_be_read_whole_is_skipped_not_half_read(self):
        broken = "status ok\nslot capsule=aa generation=notanumber name=mobile_approach state=ACTIVE uuid=" + CAPABILITY_SLOT
        self.assertEqual(parse_slots(broken), {})


class TheOperationIdentityRule(unittest.TestCase):
    def setUp(self):
        self.request = ActivationRequest(operation_uuid="0f" * 16, slot_uuid=CAPABILITY_SLOT, capsule=MOBILE_CAPSULE)

    def test_the_payload_is_the_slot_then_the_capsule_and_is_48_bytes(self):
        self.assertEqual(len(self.request.payload()), 48)
        self.assertEqual(self.request.payload()[:16].hex(), CAPABILITY_SLOT)
        self.assertEqual(self.request.payload()[16:].hex(), MOBILE_CAPSULE)

    def test_a_retry_is_the_same_identity_and_identical_bytes(self):
        again = ActivationRequest(operation_uuid="0f" * 16, slot_uuid=CAPABILITY_SLOT, capsule=MOBILE_CAPSULE)
        self.assertTrue(again.is_retry_of(self.request))

    def test_the_same_identity_with_different_bytes_is_not_a_retry(self):
        """The resident answers operation_identity_mismatch and mutates nothing; encoding it here keeps a
        caller from sending it in the first place."""
        other = ActivationRequest(operation_uuid="0f" * 16, slot_uuid=CAPABILITY_SLOT, capsule=INERT_CAPSULE)
        self.assertFalse(other.is_retry_of(self.request))

    def test_a_new_intended_operation_is_a_different_identity(self):
        fresh = ActivationRequest(operation_uuid="a1" * 16, slot_uuid=CAPABILITY_SLOT, capsule=MOBILE_CAPSULE)
        self.assertFalse(fresh.is_retry_of(self.request))


class WhatAnAnswerMeans(unittest.TestCase):
    def test_status_ok_with_an_aborted_phase_is_a_rejection_not_a_success(self):
        """Observed live: the dispatcher answers STATUS_OK for a transaction record without consulting its
        phase. A caller that read `status` would call a refusal a success."""
        answer = parse_activation_answer("status ok\ntransaction_uuid ab\ntransaction_phase ABORTED")
        self.assertEqual(answer["outcome"], REJECTED_BY_CAKE)
        self.assertIn("not a capsule this resident admitted", answer["detail"])

    def test_a_completed_phase_is_an_acceptance(self):
        self.assertEqual(parse_activation_answer("status ok\ntransaction_phase COMPLETED")["outcome"], ACCEPTED)

    def test_a_request_level_failure_is_not_a_rejection_by_cake(self):
        for status in ("operation_identity_mismatch", "unavailable"):
            with self.subTest(status=status):
                answer = parse_activation_answer(f"status {status}\nrefusal_code 2")
                self.assertEqual(answer["outcome"], REQUEST_FAILED)
                self.assertNotEqual(answer["outcome"], REJECTED_BY_CAKE)

    def test_a_mutation_refused_with_its_class_is_cake_refusing_before_any_transaction(self):
        """The resident judged the candidate (refusal class 100: the bytes do not read as a Plan artifact whose digests
        hold) and opened no transaction. Not a request that failed to land, and not an aborted transaction."""
        answer = parse_activation_answer("status mutation_refused\nrefusal_code 100")
        self.assertEqual(answer["outcome"], REFUSED_BEFORE_TRANSACTION)
        self.assertEqual(answer["reason_code"], "100")
        self.assertIsNone(answer["transaction_phase"])
        self.assertEqual(parse_activation_answer("status mutation_refused")["outcome"], REQUEST_FAILED)

    def test_an_answer_with_no_phase_is_a_request_failure_not_a_silent_success(self):
        self.assertEqual(parse_activation_answer("status ok")["outcome"], REQUEST_FAILED)

    def test_a_phase_still_in_flight_is_pending_and_not_an_outcome(self):
        self.assertEqual(parse_activation_answer("status ok\ntransaction_phase PREPARED")["outcome"], PENDING)

    def test_activation_is_confirmed_by_the_observed_slot_and_not_by_the_answer(self):
        """A completed transaction that did not leave the slot active is not a confirmed activation."""
        lying = adapter(listing(capability_capsule=INERT_CAPSULE))
        self.assertFalse(activation_confirmed(lying, {"outcome": ACCEPTED}))
        real = adapter(listing(capability_capsule=MOBILE_CAPSULE, generation=2))
        self.assertTrue(activation_confirmed(real, {"outcome": ACCEPTED}))

    def test_a_rejected_answer_is_never_confirmed_however_the_slot_reads(self):
        real = adapter(listing(capability_capsule=MOBILE_CAPSULE, generation=2))
        self.assertFalse(activation_confirmed(real, {"outcome": REJECTED_BY_CAKE}))


class ThePreparedCandidate(unittest.TestCase):
    """The supported activation is ApplyPlan with the prepared candidate Plan, measured against a real resident:
    ReplaceSlot onto the mobile capsule aborts on a fresh resident and leaves the slot untouched, while the
    prepared candidate completes and moves the slot."""

    def test_the_payload_is_the_candidate_plan_itself(self):
        candidate = PreparedCandidate(operation_uuid="0f" * 16, candidate_plan=b"CAKEPLN\x00rest")
        self.assertEqual(candidate.payload(), b"CAKEPLN\x00rest")
        self.assertEqual(candidate.operation, "apply-plan")

    def test_a_retry_is_the_same_identity_and_identical_bytes(self):
        first = PreparedCandidate(operation_uuid="0f" * 16, candidate_plan=b"same")
        self.assertTrue(PreparedCandidate(operation_uuid="0f" * 16, candidate_plan=b"same").is_retry_of(first))
        self.assertFalse(PreparedCandidate(operation_uuid="0f" * 16, candidate_plan=b"other").is_retry_of(first))
        self.assertFalse(PreparedCandidate(operation_uuid="a1" * 16, candidate_plan=b"same").is_retry_of(first))


if __name__ == "__main__":
    unittest.main()
