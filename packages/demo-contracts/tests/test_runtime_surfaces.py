# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The objects this contract grew to describe surfaces the runtime already produces must stay EQUAL to those surfaces.

A contract that drifts from its producer is worse than no contract: it typechecks a console against a record nobody
sends. So these tests read the runtime's own source and compare it field for field with the schema. They PARSE it and
never import it: this package depends on nothing that touches hardware, the network or the robot stack, and importing
demo_runtime would pull that in through a contracts test. A checkout without the runtime skips instead of failing.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parents[1]
RUNTIME = REPO / "packages" / "demo-runtime" / "demo_runtime"
sys.path.insert(0, str(PACKAGE / "python"))

import demo_contracts  # noqa: E402


def module(name: str):
    path = RUNTIME / name
    if not path.is_file():
        raise unittest.SkipTest(f"{name} is not in this checkout")
    return ast.parse(path.read_text(encoding="utf-8"))


def function(tree, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"the runtime no longer defines {name}")


def returned_keys(tree, name: str) -> list:
    """The string keys of the dict literal a function returns."""
    for node in ast.walk(function(tree, name)):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            return [key.value for key in node.value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)]
    raise AssertionError(f"{name} no longer returns a dict literal")


def literal_keys(tree, name: str) -> list:
    """The string keys of the one dict literal inside a function that builds its record in place."""
    dicts = [node for node in ast.walk(function(tree, name)) if isinstance(node, ast.Dict)]
    assert len(dicts) == 1, f"{name} now holds {len(dicts)} dict literals and this test can no longer tell which is the record"
    return [key.value for key in dicts[0].keys if isinstance(key, ast.Constant) and isinstance(key.value, str)]


def constant(tree, name: str):
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"the runtime no longer defines {name}")


def declared(schema_name: str) -> tuple:
    schema = demo_contracts.load_schema(schema_name)
    return list(schema["properties"]), list(schema["required"])


class SourceSlots(unittest.TestCase):
    def test_the_tap_slot_carries_exactly_what_the_tap_source_publishes(self):
        tree = module("sources.py")
        properties, required = declared("tap_slot")
        self.assertEqual(sorted(required), sorted(returned_keys(tree, "unavailable_tap")))
        self.assertEqual(sorted(properties), sorted(required), "the tap slot has no optional field: an unattached tap already publishes every one")
        # The connected slot is the same object with values in it, so it may not carry a field the unattached one lacks.
        self.assertEqual(sorted(set(returned_keys(tree, "slot")) - set(required)), [])

    def test_the_preview_slot_carries_what_the_preview_source_publishes_plus_the_three_it_does_not_yet(self):
        tree = module("sources.py")
        properties, required = declared("preview_slot")
        self.assertEqual(sorted(required), sorted(returned_keys(tree, "unavailable_preview")))
        # skipped_for_mission distinguishes a preview that stood aside for a decision capture from one that stalled, and
        # clock_relation is the only thing that would ever license a capture age. Neither is published today, so both are
        # optional; bytes_b64 rides on the transport that serves the picture and is stripped from the console's copy.
        self.assertEqual(sorted(set(properties) - set(required)), ["bytes_b64", "clock_relation", "skipped_for_mission"])

    def test_the_slot_states_are_the_source_state_machine(self):
        tree = module("sources.py")
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Tuple):
                names = [element.id for element in node.targets[0].elts if isinstance(element, ast.Name)]
                if names == ["DISCONNECTED", "CONNECTING", "CONNECTED", "STALLED", "FAILED"]:
                    states = list(ast.literal_eval(node.value))
                    self.assertEqual(demo_contracts.load_schema("source#/$defs/source_state")["enum"], states)
                    return
        self.fail("the runtime no longer names its five source states in one assignment")

    def test_the_capture_age_reason_is_the_one_the_runtime_states(self):
        # The age of the picture cannot be computed without a clock relation. The reason travels as a reason_code, so the
        # console never has to parse a sentence to know why the number is missing.
        reason = constant(module("sources.py"), "CAPTURE_AGE_REASON")
        self.assertEqual(demo_contracts.validate("common#/$defs/reason_code", reason), [])


class SnapshotAdditions(unittest.TestCase):
    def test_the_snapshot_now_declares_the_keys_the_daemon_had_to_carry_beside_it(self):
        additive = constant(module("daemon.py"), "ADDITIVE_SNAPSHOT_KEYS")
        properties = demo_contracts.load_schema("snapshot")["properties"]
        for key in additive:
            with self.subTest(key=key):
                self.assertIn(key, properties, "the daemon carried this beside the contract object because the object had no room for it")
        self.assertEqual(sorted(set(properties) - set(demo_contracts.load_schema("snapshot")["required"])), sorted(additive))

    def test_the_latest_retained_observation_is_the_captured_record_and_nothing_invented(self):
        """A console has to fold the snapshot's frame reference and a folded observation.captured record into ONE shape,
        or a card is left deciding which of two descriptions of the same capture to believe. This keeps the two equal:
        everything the payload states, plus the two fields the ENVELOPE carries for that record and the payload cannot
        (its sequence number in the log and the wall time the runtime wrote on it)."""
        payload = demo_contracts.load_schema("event#/$defs/payload_observation_captured")["required"]
        declared_keys, required_keys = declared("snapshot#/$defs/latest_retained_observation")
        self.assertEqual(sorted(declared_keys), sorted(required_keys), "a runtime that names a frame states every field of it")
        self.assertEqual(sorted(required_keys), sorted([*payload, "observation_id", "seq", "t_wall_utc"]))

    def test_the_latest_measured_state_is_the_measured_record_and_nothing_invented(self):
        """The same rule the frame reference is held to, for the pose. A console folds measured.state off the stream and
        a tab that reloaded after the command folded none of it, so the snapshot names the newest one: everything the
        payload states, plus the two fields the ENVELOPE carries for that record and the payload cannot."""
        payload = demo_contracts.load_schema("event#/$defs/payload_measured_state")["required"]
        declared_keys, required_keys = declared("snapshot#/$defs/latest_measured_state")
        self.assertEqual(sorted(declared_keys), sorted(required_keys), "a runtime that names a measurement states every field of it")
        self.assertEqual(sorted(required_keys), sorted([*payload, "seq", "t_wall_utc"]))

    def test_the_measured_state_reference_is_one_record_and_never_a_continuous_feed(self):
        """It is a RECORD a controller wrote, not a tap. A rate, a sample count or a liveness flag on this object would
        invite exactly the reading that threw a real measurement away: that an absent tap means nothing was measured.
        The sequence number is what a consumer decides between this and its own fold with, in both directions."""
        declared_keys, _ = declared("snapshot#/$defs/latest_measured_state")
        for forbidden in ("rate_hz", "measured_rate_hz", "live", "receive_age_ms", "count", "stream"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, declared_keys)
        self.assertIn("seq", declared_keys)

    def test_the_runtime_remembers_the_newest_measured_state_for_that_property(self):
        # The property is only as good as the producer behind it, and the producer is SessionStats: it observes
        # measured.state and publishes the newest one. A parse keeps the two from moving apart without an import.
        tree = module("read_models.py")
        self.assertTrue(any(isinstance(node, ast.FunctionDef) and node.name == "latest_measured_state" for node in ast.walk(tree)),
                        "read_models no longer publishes the newest measured state for the snapshot")
        self.assertIn('kind == "measured.state"', (RUNTIME / "read_models.py").read_text(encoding="utf-8"),
                      "SessionStats no longer observes the record the snapshot property names")

    def test_the_frame_reference_carries_no_bytes_and_no_path_to_the_picture(self):
        # It is a REFERENCE. The picture is fetched by observation id through the endpoint that already serves stored
        # frames, which is what keeps reading it from adding a second consumer of the camera.
        declared_keys, _ = declared("snapshot#/$defs/latest_retained_observation")
        for forbidden in ("bytes_b64", "path", "file", "url", "endpoint", "frame_path"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, declared_keys)

    def test_the_media_kinds_it_may_name_are_the_ones_with_a_picture_in_them(self):
        kinds = demo_contracts.load_schema("snapshot#/$defs/latest_retained_observation")["properties"]["media"]["enum"]
        allowed = demo_contracts.load_schema("event#/$defs/payload_observation_captured")["properties"]["media"]["enum"]
        self.assertEqual(sorted(kinds), ["frame", "frame_pair"])
        self.assertTrue(set(kinds) < set(allowed), "a capture kind the event vocabulary does not have could never arrive")

    def test_the_unresolved_command_carries_exactly_what_the_supervisor_reports(self):
        keys = returned_keys(module("supervisor.py"), "unresolved_detail")
        self.assertEqual(sorted(demo_contracts.load_schema("snapshot#/$defs/unresolved_command")["required"]), sorted(keys))

    def test_the_execution_backend_is_the_runtimes_allowlist_and_names_no_machine(self):
        keys = list(constant(module("read_models.py"), "EXECUTION_VIEW_KEYS"))
        declared_keys, required_keys = declared("snapshot#/$defs/execution")
        self.assertEqual(sorted(required_keys), sorted(keys))
        self.assertEqual(sorted(declared_keys), sorted(keys), "this object has no optional field: a runtime that states a backend states all four")
        # It says which path a command takes and what owns the robot on it. Where the robot is, and how this runtime
        # reaches it, is nobody's business on the other side of the socket.
        for forbidden in ("endpoint", "address", "port", "ssh", "target", "host_session", "generation"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, declared_keys)

    def test_the_backend_is_never_claimed_to_be_enforced_on_the_robot_by_the_shape_alone(self):
        """robot_side_enforcement has to be able to say three things: it is enforced there, it is NOT (a finding, not an
        absence) and nothing has answered so nothing is established. A boolean alone could not say the third."""
        field = demo_contracts.load_schema("snapshot#/$defs/execution")["properties"]["robot_side_enforcement"]
        self.assertEqual(sorted(field["type"]), ["boolean", "null"])
        backends = demo_contracts.load_schema("snapshot#/$defs/execution")["properties"]["backend"]["enum"]
        self.assertEqual(1, sum("lease" in name for name in backends), "one backend name says lease and the other does not mention one")

    def test_the_lease_status_is_the_runtimes_allowlist_and_carries_no_token(self):
        keys = list(constant(module("read_models.py"), "LEASE_VIEW_KEYS"))
        declared_keys, required_keys = declared("snapshot#/$defs/lease_status")
        self.assertEqual(sorted(required_keys), sorted(keys))
        self.assertEqual(sorted(declared_keys), sorted(keys))
        for forbidden in ("request_id", "token", "endpoint", "address", "port", "ssh"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, declared_keys)


class HistoryAndManifest(unittest.TestCase):
    def test_the_history_entry_carries_what_the_journal_projects_plus_the_two_it_does_not_record_yet(self):
        keys = literal_keys(module("journal.py"), "history_entries")
        properties, required = declared("history_entry")
        self.assertEqual(sorted(required), sorted(keys))
        # A journal written before these existed carries neither, and absent means NOT RECORDED. Neither may ever be
        # filled in from the mode the runtime happens to be in when the list is read.
        self.assertEqual(sorted(set(properties) - set(required)), ["bundle_digest", "data_mode"])

    def test_the_run_manifest_matches_the_dict_the_runtime_already_writes(self):
        tree = module("bundle.py")
        keys = returned_keys(tree, "run_manifest")
        properties, required = declared("manifest")
        self.assertEqual(sorted(required), sorted(keys))
        # deployed and generation_changes are the two the plan asks for and the runtime does not write yet. A generation
        # change is APPENDED beside a pinned run, which is what keeps the manifest of a finished run immutable.
        self.assertEqual(sorted(set(properties) - set(required)), ["deployed", "generation_changes"])

    def test_the_manifest_names_the_same_key_packages_the_runtime_records(self):
        packages = list(constant(module("bundle.py"), "KEY_PACKAGES"))
        self.assertEqual(sorted(demo_contracts.load_schema("manifest#/$defs/packages")["required"]), sorted(packages))

    def test_the_manifest_states_what_a_run_ran_and_never_where_it_lives(self):
        for forbidden in ("path", "argv", "address", "host", "data_root", "ssh", "endpoint"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, demo_contracts.load_schema("manifest")["properties"])


if __name__ == "__main__":
    unittest.main()
