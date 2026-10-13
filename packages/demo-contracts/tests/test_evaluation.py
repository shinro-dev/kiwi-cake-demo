# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""A predicate record says which of the four ways it has no value, and a verdict cannot be rewritten by a later threshold.

value null used to mean three different things at once: nobody asked, the question does not arise here, and it was asked
and no answer could be obtained. state separates them and the last two carry their own reason_code, in the same shape
capability.schema.json already uses for a disabled or blocked capability. The older reading is untouched: a record
without state means what it always meant, so nothing recorded before this became invalid or changed meaning.

The other half is identity. rule_ref names the rule and rule_digest pins the threshold set the verdict was taken with,
both written INTO the record, so changing a threshold later mints a different digest beside the new verdict and leaves
the old one saying what it said. And the time of the verdict (the envelope) stays a different fact from the freshness of
the evidence it consumed (consumed_source_stamp), which is what lets a console show an old verdict on fresh evidence, or
the reverse, without either one being adjusted to fit the other.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "python"))

import demo_contracts  # noqa: E402

FIXTURES = PACKAGE / "fixtures"
BASE = json.loads((FIXTURES / "event" / "predicate.evaluated.json").read_text(encoding="utf-8"))


def predicate(**payload) -> dict:
    """The recorded fixture with the named payload fields replaced. The fixture carries none of the new fields, so a
    record this builds carries exactly the ones a test names and nothing else."""
    record = copy.deepcopy(BASE)
    record["payload"].update(payload)
    return record


class TheFourStates(unittest.TestCase):
    def test_the_states_are_the_four_the_plan_names_and_no_more(self):
        schema = demo_contracts.load_schema("event#/$defs/payload_predicate_evaluated")
        self.assertEqual(schema["properties"]["state"]["enum"], ["not_evaluated", "not_applicable", "unavailable", "evaluated"])
        self.assertNotIn("state", schema["required"], "a recorded predicate that carries no state must stay valid")
        for name in ("state", "reason_code", "rule_ref", "rule_digest"):
            with self.subTest(property=name):
                self.assertNotIn(name, schema["required"])

    def test_a_record_without_a_state_is_still_valid_and_still_means_what_it_meant(self):
        self.assertEqual(demo_contracts.validate("event", BASE), [])
        self.assertNotIn("state", BASE["payload"], "the fixture recorded before this extension carries no state")
        self.assertEqual(demo_contracts.validate("event", predicate(value=None, measured=None)), [], "a null value without a state is the old not evaluated")

    def test_not_applicable_and_unavailable_must_say_why_and_the_other_two_need_not(self):
        for state in ("not_applicable", "unavailable"):
            with self.subTest(state=state):
                self.assertEqual(demo_contracts.validate("event", predicate(state=state, value=None, measured=None, reason_code="pad_frame_not_resolved")), [])
                errors = demo_contracts.validate("event", predicate(state=state, value=None, measured=None))
                self.assertEqual(errors, ["$.payload: missing required property 'reason_code'"], "an absent reason is as refused as a null one")
                errors = demo_contracts.validate("event", predicate(state=state, value=None, measured=None, reason_code=None))
                self.assertIn("required when state is not_applicable or unavailable", errors[0])
        for state in ("not_evaluated", "evaluated"):
            with self.subTest(state=state):
                value = None if state == "not_evaluated" else True
                record = predicate(state=state, value=value)
                self.assertNotIn("reason_code", record["payload"], "these two states carry no reason of their own")
                self.assertEqual(demo_contracts.validate("event", record), [])

    def test_a_predicate_that_says_it_was_evaluated_carries_a_value(self):
        self.assertEqual(demo_contracts.validate("event", predicate(state="evaluated", value=False)), [])
        errors = demo_contracts.validate("event", predicate(state="evaluated", value=None))
        self.assertEqual(errors, ["$.payload: must not be a predicate that says it was evaluated and carries no value"])

    def test_the_reason_stays_inside_the_payload_and_leaves_the_envelope_alone(self):
        # The envelope's reason_code is what a console routes the RECORD on, as it already does for a refused action. The
        # payload's reason is about the predicate. A record that carries both carries two different facts, not one twice.
        record = predicate(state="unavailable", value=None, measured=None, reason_code="pad_frame_not_resolved")
        record["reason_code"] = "evidence_unreadable"
        self.assertEqual(demo_contracts.validate("event", record), [])
        self.assertEqual((record["reason_code"], record["payload"]["reason_code"]), ("evidence_unreadable", "pad_frame_not_resolved"))


class RuleIdentityAndFreshness(unittest.TestCase):
    def test_the_rule_and_the_thresholds_it_used_are_written_into_the_record(self):
        digest = "sha256:" + "0" * 63 + "3"
        record = predicate(state="evaluated", value=True, rule_ref="clearance_target_met.v3", rule_digest=digest)
        self.assertEqual(demo_contracts.validate("event", record), [])
        # A threshold set that changed mints a different digest. The earlier verdict keeps the earlier one, so nothing a
        # console draws from the old record can be rewritten by the new rule.
        later = predicate(state="evaluated", value=False, rule_ref="clearance_target_met.v3", rule_digest="sha256:" + "0" * 63 + "4")
        self.assertEqual(demo_contracts.validate("event", later), [])
        self.assertNotEqual(record["payload"]["rule_digest"], later["payload"]["rule_digest"])

    def test_a_rule_digest_that_is_a_file_name_is_refused(self):
        errors = demo_contracts.validate("event", predicate(rule_digest="thresholds.json"))
        self.assertTrue(any(error.startswith("$.payload.rule_digest") for error in errors), errors)

    def test_the_verdicts_time_and_the_evidences_freshness_are_two_fields_not_one(self):
        record = predicate(state="evaluated", value=True)
        self.assertEqual(record["clock_domain"], "laptop_monotonic", "the verdict is stamped in the emitter's clock")
        self.assertEqual(record["payload"]["consumed_source_stamp"]["clock_domain"], "pi_monotonic", "the evidence is stamped in the source's clock")
        self.assertNotEqual(record["source_session"], record["payload"]["consumed_source_stamp"]["session"], "two sessions, two sequence spaces")
        self.assertEqual(demo_contracts.validate("event", record), [])


class EvidenceItemsCarryTheirOwnMeasurement(unittest.TestCase):
    def test_a_piece_of_evidence_can_carry_its_value_its_unit_and_its_own_stamp(self):
        record = predicate()
        record["payload"]["evidence"] = [{
            "type": "pad_measurement", "observation_ids": ["synthetic-obs-0001"], "scalar_name": None,
            "measurement": {"name": "footprint_clearance_mm", "value": 30.0, "unit": "mm"},
            "source_stamp": {"session": "synthetic-host-session-0001", "seq": 41, "sample_mono_ns": 2000000000, "clock_domain": "pi_monotonic"},
        }]
        self.assertEqual(demo_contracts.validate("event", record), [])

    def test_both_are_optional_and_null_is_how_a_piece_says_it_has_none(self):
        item = {"type": "scalar", "observation_ids": [], "scalar_name": "footprint_clearance_mm"}
        for evidence in ([item], [{**item, "measurement": None, "source_stamp": None}]):
            with self.subTest(carries=sorted(evidence[0])):
                record = predicate()
                record["payload"]["evidence"] = evidence
                self.assertEqual(demo_contracts.validate("event", record), [])

    def test_a_measurement_still_cannot_spell_a_path_or_an_endpoint(self):
        # named_value is the tripwire, and an evidence item does not get a weaker one because it is nested deeper.
        record = predicate()
        record["payload"]["evidence"] = [{"type": "scalar", "observation_ids": [], "scalar_name": "clearance",
                                          "measurement": {"name": "clearance", "value": "/" + "home/somebody/pad.json", "unit": None}, "source_stamp": None}]
        errors = demo_contracts.validate("event", record)
        self.assertTrue(any("$.payload.evidence[0].measurement.value" in error for error in errors), errors)


class ActionsNameWhatTheyConsumed(unittest.TestCase):
    def test_a_planned_action_and_a_refusal_can_both_name_their_evidence(self):
        for kind, extra in (("action.planned", {"evidence_refs": ["synthetic-obs-0001"], "rule_ref": "approach_when_the_pad_is_resolved.v2"}),
                            ("action.refused", {"evidence_refs": ["synthetic-obs-0001"]})):
            with self.subTest(kind=kind):
                record = json.loads((FIXTURES / "event" / f"{kind}.json").read_text(encoding="utf-8"))
                self.assertEqual(demo_contracts.validate("event", record), [], "the record without the extension is still valid")
                record["payload"].update(extra)
                self.assertEqual(demo_contracts.validate("event", record), [])

    def test_an_empty_list_says_nothing_was_consumed_and_an_absent_one_says_nothing_at_all(self):
        record = json.loads((FIXTURES / "event" / "action.planned.json").read_text(encoding="utf-8"))
        self.assertNotIn("evidence_refs", record["payload"])
        record["payload"]["evidence_refs"] = []
        self.assertEqual(demo_contracts.validate("event", record), [])


class CommandReceipts(unittest.TestCase):
    def test_a_receipt_can_be_bound_to_its_payload_and_to_the_runtime_that_admitted_it(self):
        receipt = {"command_id": "synthetic-command-0001", "status": "accepted", "reason_code": None, "run_id": None}
        self.assertEqual(demo_contracts.validate("command_result", receipt), [], "a receipt without the extension is still valid")
        bound = {**receipt, "payload_digest": "sha256:" + "0" * 63 + "1", "runtime_session": "synthetic-session-0001", "first_seen_wall_utc": "2026-01-01T00:00:00Z"}
        self.assertEqual(demo_contracts.validate("command_result", bound), [])

    def test_the_three_new_fields_are_optional_and_the_reason_rule_is_untouched(self):
        schema = demo_contracts.load_schema("command_result")
        self.assertEqual(sorted(set(schema["properties"]) - set(schema["required"])), ["first_seen_wall_utc", "payload_digest", "runtime_session"])
        rejected = {"command_id": "synthetic-command-0001", "status": "rejected", "reason_code": None, "run_id": None, "runtime_session": "synthetic-session-0001"}
        self.assertIn("required when status is rejected or uncertain", demo_contracts.validate("command_result", rejected)[0])


if __name__ == "__main__":
    unittest.main()
