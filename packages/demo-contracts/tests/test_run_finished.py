# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""run.finished keeps two facts apart: what physically happened (outcome, predicates) and how complete the evidence for it
is (evidence_complete, evidence_gaps). Every outcome validates with complete evidence and with incomplete evidence; the
contract has no field that folds the two into one "success". The single cross rule is arithmetic, not judgment:
evidence_complete true means evidence_gaps is 0.
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

BASE = json.loads((PACKAGE / "fixtures" / "event" / "run.finished.json").read_text(encoding="utf-8"))


def finished(**payload) -> dict:
    record = copy.deepcopy(BASE)
    record["payload"].update(payload)
    return record


class OutcomeAndEvidenceAreIndependent(unittest.TestCase):
    def test_every_outcome_validates_with_complete_and_with_incomplete_evidence(self):
        # The outcome vocabulary is one named definition now, because the history list names the same outcomes. The payload
        # must still be the thing that points at it, or a run and its history row could come to mean two different sets.
        self.assertEqual(demo_contracts.load_schema("event#/$defs/payload_run_finished")["properties"]["outcome"], {"$ref": "#/$defs/run_outcome"})
        outcomes = demo_contracts.load_schema("event#/$defs/run_outcome")["enum"]
        self.assertEqual(outcomes, ["placed_new", "already_placed_noop", "stopped", "refused", "failed", "unknown"])
        for outcome in outcomes:
            for complete, gaps in ((True, 0), (False, 0), (False, 4)):
                with self.subTest(outcome=outcome, evidence_complete=complete, evidence_gaps=gaps):
                    self.assertEqual(demo_contracts.validate("event", finished(outcome=outcome, evidence_complete=complete, evidence_gaps=gaps)), [])

    def test_predicates_do_not_constrain_the_evidence_fields_either(self):
        for value in (True, False, None):
            predicates = {"object_secured": value, "placed": value, "clearance_target_met": value, "base_returned": value}
            for complete, gaps in ((True, 0), (False, 2)):
                with self.subTest(predicates=value, evidence_complete=complete):
                    self.assertEqual(demo_contracts.validate("event", finished(predicates=predicates, evidence_complete=complete, evidence_gaps=gaps)), [])

    def test_the_two_facts_are_separate_required_fields(self):
        schema = demo_contracts.load_schema("event#/$defs/payload_run_finished")
        for name in ("outcome", "predicates", "evidence_complete", "evidence_gaps", "automatic_retries", "task_control_interventions", "exit_status"):
            self.assertIn(name, schema["required"])
        self.assertEqual(schema["properties"]["evidence_complete"], {"type": "boolean"})
        for name in ("evidence_complete", "evidence_gaps"):
            record = finished()
            del record["payload"][name]
            self.assertEqual(demo_contracts.validate("event", record), [f"$.payload: missing required property {name!r}"])
        self.assertEqual(len(demo_contracts.validate("event", finished(evidence_complete=None))), 1, "evidence completeness is never unknown-by-omission")

    def test_complete_evidence_with_gaps_is_a_contradiction(self):
        errors = demo_contracts.validate("event", finished(outcome="placed_new", evidence_complete=True, evidence_gaps=1))
        self.assertEqual(errors, ["$.payload.evidence_gaps: expected 0, got 1 (0 when evidence_complete is true)"])

    def test_counts_are_non_negative_integers_and_predicates_are_all_present(self):
        for name in ("automatic_retries", "task_control_interventions", "evidence_gaps"):
            with self.subTest(field=name):
                self.assertTrue(demo_contracts.validate("event", finished(evidence_complete=False, **{name: -1})))
                self.assertTrue(demo_contracts.validate("event", finished(evidence_complete=False, **{name: 1.5})))
        record = finished()
        del record["payload"]["predicates"]["base_returned"]
        self.assertEqual(demo_contracts.validate("event", record), ["$.payload.predicates: missing required property 'base_returned'"])
        self.assertTrue(demo_contracts.validate("event", finished(predicates={**BASE["payload"]["predicates"], "cube_is_happy": True})))

    def test_the_snapshot_carries_the_same_payload_under_the_same_rules(self):
        snapshot = json.loads((PACKAGE / "fixtures" / "snapshot" / "running.json").read_text(encoding="utf-8"))
        snapshot["last_outcome"]["result"].update(evidence_complete=True, evidence_gaps=3)
        self.assertEqual(demo_contracts.validate("snapshot", snapshot), ["$.last_outcome.result.evidence_gaps: expected 0, got 3 (0 when evidence_complete is true)"])


if __name__ == "__main__":
    unittest.main()
