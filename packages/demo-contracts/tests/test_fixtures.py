# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Every valid fixture validates, every invalid fixture fails with the message that names its broken rule, and the
fixture set covers the contract: one example per event kind, per command type and per command_result status.

A fixture's schema is the name of its directory under fixtures/ (event, command, command_result, snapshot, capability,
component, cake, cake_ladder, ...). Invalid fixtures live under fixtures/invalid/<schema>/ and expectations.json maps
each of them to a fragment of the error it must produce, so an invalid fixture cannot pass by failing for another reason.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "python"))

import demo_contracts  # noqa: E402

FIXTURES = PACKAGE / "fixtures"
SCHEMA_NAMES = {"event", "command", "snapshot", "capability", "component", "cake", "manifest"} | set(demo_contracts.ALIASES)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def valid_fixtures() -> list[Path]:
    return sorted(path for path in FIXTURES.rglob("*.json") if path.relative_to(FIXTURES).parts[0] != "invalid")


class ValidFixtures(unittest.TestCase):
    def test_every_valid_fixture_validates(self):
        paths = valid_fixtures()
        self.assertGreater(len(paths), 40, "the fixture set went missing")
        for path in paths:
            schema = path.relative_to(FIXTURES).parts[0]
            with self.subTest(fixture=str(path.relative_to(FIXTURES))):
                self.assertIn(schema, SCHEMA_NAMES, "a fixture directory must be named after a schema")
                self.assertEqual(demo_contracts.validate(schema, load(path)), [])

    def test_every_schema_has_a_valid_fixture(self):
        present = {path.relative_to(FIXTURES).parts[0] for path in valid_fixtures()}
        self.assertEqual(present, SCHEMA_NAMES)

    def test_one_example_per_event_kind(self):
        kinds = demo_contracts.load_schema("event#/$defs/kind")["enum"]
        self.assertEqual(len(kinds), 19)
        for kind in kinds:
            with self.subTest(kind=kind):
                record = load(FIXTURES / "event" / f"{kind}.json")
                self.assertEqual(record["kind"], kind)

    def test_one_example_per_command_type_and_result_status(self):
        for type_ in demo_contracts.load_schema("common#/$defs/command_type")["enum"]:
            with self.subTest(type=type_):
                self.assertEqual(load(FIXTURES / "command" / f"{type_}.json")["type"], type_)
        for status in demo_contracts.load_schema("command_result")["properties"]["status"]["enum"]:
            with self.subTest(status=status):
                self.assertEqual(load(FIXTURES / "command_result" / f"{status}.json")["status"], status)

    def test_every_cake_object_is_marked_synthetic(self):
        # Nothing connects the console to a resident today, so a Cake fixture that did not say synthetic would be a false
        # claim. Every Cake view model declares the flag, at every depth, and every fixture sets it to true.
        def walk(value, where):
            if isinstance(value, dict):
                if "synthetic" in value:
                    self.assertIs(value["synthetic"], True, where)
                for key, item in value.items():
                    walk(item, f"{where}.{key}")
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, f"{where}[{index}]")

        cake_paths = [path for path in valid_fixtures() if path.relative_to(FIXTURES).parts[0].startswith("cake")]
        self.assertGreaterEqual(len(cake_paths), 10)
        for path in cake_paths:
            with self.subTest(fixture=path.name):
                record = load(path)
                self.assertIs(record.get("synthetic"), True)
                walk(record, "$")
        for path in (FIXTURES / "snapshot").glob("*.json"):
            with self.subTest(fixture=path.name):
                self.assertIs(load(path)["cake"]["synthetic"], True)
                walk(load(path)["cake"], "$.cake")

    def test_the_ladder_claims_nothing_today(self):
        ladder = load(FIXTURES / "cake_ladder" / "today-nothing-claimed.json")
        rungs = {name: rung["value"] for name, rung in ladder.items() if name != "synthetic"}
        self.assertEqual(len(rungs), 5)
        self.assertTrue(all(value in (False, None) for value in rungs.values()), rungs)

    def test_a_rung_nobody_checked_is_null_never_false(self):
        # false is a finding: somebody looked and the answer was no. A rung whose own evidence says it was not checked is
        # "not determined", which the schema spells null. A console is built against these fixtures, and a false here
        # would teach it to print a definite "no" for a question nobody asked.
        ladders = [(FIXTURES / "cake_ladder" / "today-nothing-claimed.json", lambda record: record)]
        ladders += [(path, lambda record: record["ladder"]) for path in sorted((FIXTURES / "cake").glob("*.json"))]
        ladders += [(path, lambda record: record["cake"]["ladder"]) for path in sorted((FIXTURES / "snapshot").glob("*.json"))]
        self.assertGreaterEqual(len(ladders), 6)
        seen = set()
        for path, pick in ladders:
            for name, rung in pick(load(path)).items():
                if name == "synthetic":
                    continue
                seen.add(rung["value"])
                if "not checked" in rung["evidence"]:
                    with self.subTest(fixture=path.name, rung=name):
                        self.assertIsNone(rung["value"])
        self.assertEqual(seen, {True, False, None}, "the fixtures show a console every value a rung can take")

    def test_cake_story_fixtures_cover_the_required_beats(self):
        transactions = [load(path) for path in sorted((FIXTURES / "cake_transaction").glob("sequence-*.json"))]
        self.assertGreaterEqual(len(transactions), 4)
        self.assertEqual({item["kind"] for item in transactions}, {"update", "rollback"})
        self.assertTrue(any(item["committed"] and not item["durable"] for item in transactions), "committed and durable are separate facts")
        rejected = load(FIXTURES / "cake_transaction" / "rejected-incompatible-candidate.json")
        self.assertEqual((rejected["committed"], rejected["durable"]), (False, False))
        self.assertTrue(rejected["native_events"])
        preview = load(FIXTURES / "cake_update_preview" / "one-slot.json")
        self.assertTrue({"candidate_digest", "plan_digest", "epoch"} <= set(preview))

    def test_a_disconnected_live_view_is_not_replay(self):
        snapshot = load(FIXTURES / "snapshot" / "live-disconnected.json")
        self.assertEqual((snapshot["data_mode"], snapshot["link"]["state"]), ("live", "disconnected"))
        self.assertEqual(demo_contracts.validate("snapshot", snapshot), [])
        for mode in demo_contracts.load_schema("common#/$defs/data_mode")["enum"]:
            for state in ("connected", "disconnected", "unknown"):
                with self.subTest(data_mode=mode, link=state):
                    candidate = {**snapshot, "data_mode": mode, "link": {**snapshot["link"], "state": state}}
                    self.assertEqual(demo_contracts.validate("snapshot", candidate), [])


class InvalidFixtures(unittest.TestCase):
    def test_every_invalid_fixture_fails_for_its_own_reason(self):
        expectations = load(FIXTURES / "invalid" / "expectations.json")
        on_disk = {str(path.relative_to(FIXTURES / "invalid")) for path in (FIXTURES / "invalid").rglob("*.json")} - {"expectations.json"}
        self.assertEqual(on_disk, set(expectations), "expectations.json and the invalid fixtures must list the same files")
        self.assertGreaterEqual(len(expectations), 20)
        for relative, fragment in sorted(expectations.items()):
            with self.subTest(fixture=relative):
                errors = demo_contracts.validate(relative.split("/")[0], load(FIXTURES / "invalid" / relative))
                self.assertTrue(errors, "an invalid fixture validated")
                self.assertTrue(any(fragment in error for error in errors), f"no error mentions {fragment!r}: {errors}")
                self.assertTrue(all(error.startswith("$") for error in errors), f"every error names the path it is about: {errors}")


if __name__ == "__main__":
    unittest.main()
