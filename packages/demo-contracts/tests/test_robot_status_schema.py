# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The robot_status surface's schema: versioned apart from contract v1 and from cake_status, self-contained, closed.

It reads files only. The runtime's own answers are validated against this schema by packages/demo-runtime/tests/
test_robot_link.py; here the schema itself is held to the rules every schema of this package follows.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "python"))

import demo_contracts  # noqa: E402

SCHEMA_FILE = demo_contracts.ROBOT_STATUS_SCHEMA_DIR / "robot_status.schema.json"


def refs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                yield value
            else:
                yield from refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from refs(item)


def closed_objects(node, path="$"):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield path, node
        for key, value in node.items():
            yield from closed_objects(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from closed_objects(item, f"{path}[{index}]")


class TheRobotStatusSchema(unittest.TestCase):
    def test_it_loads_through_the_fail_closed_validator(self):
        schema = demo_contracts.load_robot_status_schema()
        self.assertEqual(schema["properties"]["robot_status_version"]["const"], demo_contracts.ROBOT_STATUS_VERSION)

    def test_it_is_self_contained_and_referenced_by_neither_other_surface(self):
        raw = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        self.assertTrue(all(ref.startswith("#/$defs/") for ref in refs(raw)), "a $ref stays inside this file")
        for other in list(demo_contracts.SCHEMA_DIR.glob("*.json")) + list(demo_contracts.CAKE_STATUS_SCHEMA_DIR.glob("*.json")):
            self.assertNotIn("robot_status", other.read_text(encoding="utf-8"), other.name)
        self.assertNotEqual(demo_contracts.ROBOT_STATUS_SCHEMA_DIR, demo_contracts.SCHEMA_DIR)

    def test_every_object_is_closed(self):
        raw = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        for path, node in closed_objects(raw):
            with self.subTest(path=path):
                self.assertIs(node.get("additionalProperties"), False)

    def test_it_is_not_reachable_through_the_contract_validator(self):
        with self.assertRaises(demo_contracts.SchemaError):
            demo_contracts.validate("robot_status", {})

    def test_free_text_refuses_a_home_directory_and_an_address(self):
        errors = demo_contracts.validate_robot_status({"robot_status_version": "2"})
        self.assertTrue(errors, "an incomplete answer is refused")
        home = "/" + "home" + "/someone/socket"
        address = ".".join(("192", "0", "2", "7"))
        for text in (home, "reached " + address):
            with self.subTest(text=text):
                self.assertTrue(demo_contracts._ROBOT_STATUS.validate("robot_status#/$defs/safe_text", text))
        self.assertEqual(demo_contracts._ROBOT_STATUS.validate("robot_status#/$defs/safe_text", "the robot's resident answered"), [])

    def test_version_2_identifies_by_build_and_binds_a_whole_host_instance(self):
        schema = demo_contracts.load_robot_status_schema()
        self.assertEqual(schema["properties"]["robot_status_version"]["const"], "2")
        identity = schema["properties"]["identity"]["properties"]
        self.assertIn("expected_build_identity", identity)
        self.assertNotIn("expected_target_profile_digest", identity, "the target profile identifies nothing")
        motion = schema["properties"]["motion_permission"]["properties"]
        self.assertNotIn("current_host_pid", motion)
        self.assertIn("current_host_instance", motion)
        check = lambda value: demo_contracts._ROBOT_STATUS.validate("robot_status#/$defs/host_instance", value)  # noqa: E731
        good = {"slot_instance": "6003298303284871169", "spawn_sequence": 2, "pid": 4242}
        self.assertEqual(check(good), [])
        for bad in ({**good, "slot_instance": 6003298303284871169}, {**good, "slot_instance": "0"}, {**good, "pid": 0},
                    {**good, "spawn_sequence": 0}, {**good, "extra": 1}, {"slot_instance": "1", "pid": 4242}):
            with self.subTest(bad=bad):
                self.assertTrue(check(bad), "a u64 handle is a string, and a host instance is whole and closed")

    def test_the_updates_description_says_poll_traffic_is_excluded(self):
        description = demo_contracts.load_robot_status_schema()["properties"]["updates"]["description"]
        self.assertIn("POLL TRAFFIC IS EXCLUDED", description)
        self.assertIn("0x0B", description)

    def test_the_identity_descriptions_say_what_a_build_identity_does_and_does_not_establish(self):
        """The operator's correction: build identity identifies SOFTWARE; the authenticated connection to the intended
        robot and that endpoint's current resident session establish which deployment is observed. The codes keep their
        names (is_robot, robot_resident_not_the_robot); only what they are said to mean changed."""
        schema = demo_contracts.load_robot_status_schema()
        identity = schema["properties"]["identity"]
        self.assertIn("is_robot", identity["required"], "the code keeps its name")
        text = identity["description"]
        self.assertIn("identifies the SOFTWARE", text)
        self.assertIn("authenticated connection to the intended robot", text)
        self.assertIn("the session that endpoint's resident reports now", text)
        whole = json.dumps(schema)
        for claim in ("PROVED it is the robot", "proves it is the robot", "proved it is the robot"):
            self.assertNotIn(claim, whole, "a build identity does not prove which robot answered")


if __name__ == "__main__":
    unittest.main()
