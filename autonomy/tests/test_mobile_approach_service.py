#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_mobile_approach_service.py: the capability process that Cake supervises, driven without a
# transport. No hardware, no socket, no real time.
#
# WHY THIS PROCESS EXISTS AT ALL. autonomy/motion/mobile_approach.py is pure compute with no process and no
# socket, so nothing about it can be activated; a Cake activation of it would be decorative. This wrapper is what
# a capability slot spawns, so that "the capability is not active" and "no DesiredBaseMotion is published" are the
# same fact, and a mission waiting for the capability is waiting for something real.
#
# Run directly: python3 autonomy/tests/test_mobile_approach_service.py
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from autonomy.motion.mobile_approach_service import (  # noqa: E402
    V2_FIELDS,
    ApproachService,
    command_document,
    parse_observation,
    parse_observation_v2,
)


def observation(target_id="cube_b", *, timestamp_ns=1_000_000_000, x_m=0.40, y_m=0.02, quality=0.9, **over):
    document = {"target_id": target_id, "timestamp_ns": timestamp_ns, "frame": "base",
                "x_m": x_m, "y_m": y_m, "quality": quality}
    document.update(over)
    return json.dumps(document).encode("utf-8")


def observation_v2(target_id="cube_b", *, capture_ns=998_000_000_000, published_ns=1_000_000_000_000, x_m=0.050,
                   max_age_ns=30_000_000_000, **over):
    """A v2 document as the relay publishes it: the capture 2 s before the publication, inside a 30 s bound. `over`
    may also set a key to REMOVE (value `...`) or add an unknown one."""
    document = {"contract": "kc.target_observation/2", "target_id": target_id, "capture_ns": capture_ns,
                "published_ns": published_ns, "motion_epoch": 3, "frame": "anchor_reach", "x_m": x_m, "y_m": 0.0,
                "max_age_ns": max_age_ns, "stationary": True, "observation_id": "20260926T120000Z-0001"}
    for key, value in over.items():
        if value is ...:
            document.pop(key)
        else:
            document[key] = value
    return json.dumps(document).encode("utf-8")


class Wire:
    """The two sockets, as lists. The loop cannot tell the difference and no transport is involved.

    A DRAINED WIRE STOPS THE SERVICE. The real loop waits forever for the next observation, which is what a
    supervised capability process must do; a test driving a finite script would otherwise never return.
    """

    def __init__(self, inbound):
        self.inbound, self.published = list(inbound), []
        self.service = None

    def receive(self, _timeout_ms):
        if self.inbound:
            return self.inbound.pop(0)
        if self.service is not None:
            self.service.stop()
        return None

    def publish(self, payload):
        self.published.append(json.loads(payload.decode("utf-8")))


def drive(inbound, **kwargs):
    """Run the service over a finite script and hand back both sides."""
    wire = Wire(inbound)
    service = ApproachService(wire.receive, wire.publish, **kwargs)
    wire.service = service
    service.run()
    return wire, service


class ReadingAnObservation(unittest.TestCase):
    def test_it_reads_the_committed_target_observation_shape(self):
        parsed, reason = parse_observation(observation())
        self.assertIsNone(reason)
        self.assertEqual((parsed.target_id, parsed.x_m, parsed.quality), ("cube_b", 0.40, 0.9))

    def test_a_message_it_cannot_read_is_counted_and_dropped_never_guessed_at(self):
        """An approach computed from a half-understood observation is worse than no approach."""
        cases = {
            b"not json at all": "not_json",
            b"[1,2,3]": "not_an_object",
            json.dumps({"target_id": "a", "timestamp_ns": 1}).encode(): "missing_fields",
            observation(frame="map"): "frame_not_base",
            observation(x_m="sideways"): "field_not_a_number",
        }
        for payload, expected in cases.items():
            with self.subTest(reason=expected):
                parsed, reason = parse_observation(payload)
                self.assertIsNone(parsed)
                self.assertEqual(reason, expected)

    def test_a_dropped_message_publishes_nothing_and_is_counted_by_reason(self):
        wire, service = drive([b"not json at all", observation(frame="map")])
        self.assertEqual(wire.published, [])
        self.assertEqual(service.dropped, {"not_json": 1, "frame_not_base": 1})


class PublishingACommand(unittest.TestCase):
    def test_a_good_observation_produces_a_command_in_the_committed_shape(self):
        wire, _ = drive([observation()], identity="mobile_approach.gen2")
        self.assertEqual(len(wire.published), 1)
        command = wire.published[0]
        self.assertEqual(sorted(command), ["command_id", "expiry_ns", "omega_rps", "target_id",
                                           "timestamp_ns", "vx_mps", "vy_mps"])
        self.assertEqual(command["target_id"], "cube_b")

    def test_the_module_identity_is_attributed_to_every_command(self):
        """A command has to be traceable to the module generation that produced it, and the frozen schema has no
        field for one: it travels inside command_id, which the schema already bounds."""
        wire, _ = drive([observation(), observation(timestamp_ns=1_100_000_000)],
                        identity="mobile_approach.87dee37b.gen2")
        self.assertTrue(wire.published, "the fixture should produce at least one command")
        for command in wire.published:
            self.assertTrue(command["command_id"].startswith("mobile_approach.87dee37b.gen2."), command["command_id"])

    def test_an_unnamed_identity_is_reported_as_unknown_and_never_invented(self):
        self.assertTrue(command_document(_command(), "")["command_id"].startswith("unidentified."))

    def test_the_identity_never_widens_the_frozen_schema(self):
        document = command_document(_command(), "gen2")
        self.assertNotIn("identity", document)
        self.assertNotIn("module_identity", document)

    def test_the_controller_declining_to_command_is_not_an_error(self):
        """A cycle that produced no command is the controller declining, which is what the deadband means."""
        _, service = drive([observation(x_m=0.0, y_m=0.0)])
        self.assertIsNotNone(service.last_state)
        self.assertEqual(service.dropped, {})


class ReadingAV2Observation(unittest.TestCase):
    """parse_observation_v2 is strict: the schema's eleven fields, their types, consts and ranges, nothing else."""

    def test_it_reads_the_v2_shape_and_keeps_both_stamps(self):
        parsed, reason = parse_observation_v2(observation_v2())
        self.assertIsNone(reason)
        self.assertEqual((parsed.target_id, parsed.capture_ns, parsed.published_ns, parsed.motion_epoch, parsed.x_m,
                          parsed.y_m, parsed.max_age_ns, parsed.stationary, parsed.observation_id, parsed.frame, parsed.contract),
                         ("cube_b", 998_000_000_000, 1_000_000_000_000, 3, 0.050, 0.0, 30_000_000_000, True,
                          "20260926T120000Z-0001", "anchor_reach", "kc.target_observation/2"))

    def test_the_field_list_is_the_schema_file(self):
        with open(os.path.join(ROOT, "autonomy", "schemas", "target_observation.v2.schema.json"), encoding="utf-8") as handle:
            schema = json.load(handle)
        self.assertEqual(schema["title"], "TargetObservationV2")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(sorted(schema["required"]), sorted(V2_FIELDS))
        self.assertEqual(sorted(schema["properties"]), sorted(V2_FIELDS))
        self.assertEqual(schema["properties"]["contract"]["const"], "kc.target_observation/2")
        self.assertEqual(schema["properties"]["frame"]["const"], "anchor_reach")
        self.assertEqual(schema["properties"]["y_m"]["const"], 0.0)
        self.assertIs(schema["properties"]["stationary"]["const"], True)
        self.assertEqual((schema["properties"]["x_m"]["minimum"], schema["properties"]["x_m"]["maximum"]), (0, 0.12))
        self.assertEqual(schema["properties"]["max_age_ns"]["exclusiveMinimum"], 0)

    def test_every_drop_reason_in_its_order(self):
        """One case per reason, each a document that fails exactly that check and every earlier one passes."""
        cases = [
            ("not_json", b"not json at all"),
            ("not_an_object", b"[1,2,3]"),
            ("contract_mismatch", observation()),  # a v1 document under v2
            ("contract_mismatch", observation_v2(contract="kc.target_observation/1")),
            ("missing_fields", observation_v2(observation_id=...)),
            ("unknown_fields", observation_v2(quality=0.9)),
            ("unknown_fields", observation_v2(need_mm=50.0)),
            ("field_not_a_number", observation_v2(x_m="sideways")),
            ("field_not_a_number", observation_v2(capture_ns=1.5)),
            ("field_not_a_number", observation_v2(capture_ns=True)),
            ("field_not_a_number", observation_v2(capture_ns=-1)),
            ("field_not_a_number", observation_v2(max_age_ns=0)),
            ("field_not_a_number", observation_v2(motion_epoch="3")),
            # The two id fields outside their schema pattern fall under the contract's fixed field_not_a_number
            # code (the brief names no separate code for them); the document is still dropped, never answered.
            ("field_not_a_number", observation_v2(target_id="")),
            ("field_not_a_number", observation_v2(target_id="cube b")),
            ("field_not_a_number", observation_v2(target_id=7)),
            ("field_not_a_number", observation_v2(observation_id="-leading-dash")),
            ("field_not_a_number", observation_v2(observation_id=None)),
            # A trailing newline: '$' would let it through with re.match; the reader uses fullmatch.
            ("field_not_a_number", observation_v2(target_id="cube_b\n")),
            ("field_not_a_number", observation_v2(observation_id="20260926T120000Z-0001\n")),
            ("frame_not_anchor_reach", observation_v2(frame="base")),
            ("y_not_zero", observation_v2(y_m=0.001)),
            ("x_out_of_bounds", observation_v2(x_m=0.121)),
            ("x_out_of_bounds", observation_v2(x_m=-0.001)),
            ("capture_after_publication", observation_v2(capture_ns=1_000_000_000_001)),
            ("capture_older_than_bound", observation_v2(capture_ns=1_000_000_000_000 - 30_000_000_001)),
            ("not_stationary", observation_v2(stationary=False)),
            ("not_stationary", observation_v2(stationary=1)),
        ]
        for expected, payload in cases:
            with self.subTest(reason=expected, payload=payload):
                parsed, reason = parse_observation_v2(payload)
                self.assertIsNone(parsed)
                self.assertEqual(reason, expected)

    def test_nan_is_refused_by_the_reader_not_by_the_controller(self):
        """json.loads accepts NaN and Infinity; the schema declares numbers, which JSON has none of those in."""
        for bad in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(bad=bad):
                payload = observation_v2().replace(b'"x_m": 0.05', b'"x_m": ' + bad.encode())
                self.assertIn(bad.encode(), payload)
                parsed, reason = parse_observation_v2(payload)
                self.assertIsNone(parsed)
                self.assertEqual(reason, "field_not_a_number")

    def test_the_bounds_are_inclusive_and_freshness_is_inclusive(self):
        for payload in (observation_v2(x_m=0.0), observation_v2(x_m=0.120), observation_v2(y_m=0),
                        observation_v2(capture_ns=1_000_000_000_000), observation_v2(capture_ns=1_000_000_000_000 - 30_000_000_000)):
            with self.subTest(payload=payload):
                parsed, reason = parse_observation_v2(payload)
                self.assertIsNone(reason)
                self.assertIsNotNone(parsed)

    def test_a_v2_document_under_v1_is_dropped_for_its_missing_quality(self):
        parsed, reason = parse_observation(observation_v2())
        self.assertIsNone(parsed)
        self.assertEqual(reason, "missing_fields")


class PublishingAV2Command(unittest.TestCase):
    """The service on --contract v2: the command is anchored to the publication, at the proven pulse speed."""

    def test_a_remaining_advance_of_50_mm_gives_the_pulse_speed_stamped_with_the_publication(self):
        wire, service = drive([observation_v2(x_m=0.050)], identity="mobile_approach.pid4242", contract="v2")
        self.assertEqual(service.dropped, {})
        self.assertEqual(len(wire.published), 1)
        command = wire.published[0]
        self.assertEqual(sorted(command), ["command_id", "expiry_ns", "omega_rps", "target_id", "timestamp_ns", "vx_mps", "vy_mps"])
        self.assertEqual(command["command_id"], "mobile_approach.pid4242.cmd_1")
        self.assertEqual(command["target_id"], "cube_b")
        self.assertEqual((command["vx_mps"], command["vy_mps"], command["omega_rps"]), (0.05, 0.0, 0.0))
        self.assertEqual(command["timestamp_ns"], 1_000_000_000_000)
        self.assertEqual(command["expiry_ns"], 1_000_000_000_000 + 250_000_000)

    def test_inside_the_tolerance_and_beyond_the_bound_are_zero(self):
        wire, service = drive([observation_v2(x_m=0.005), observation_v2(x_m=0.120, published_ns=1_000_080_000_000)], contract="v2")
        self.assertEqual(service.dropped, {})
        self.assertEqual([c["vx_mps"] for c in wire.published], [0.0, 0.05])
        wire, service = drive([observation_v2(x_m=0.121)], contract="v2")
        self.assertEqual(service.dropped, {"x_out_of_bounds": 1})
        self.assertEqual(wire.published, [])

    def test_a_stale_capture_is_dropped_and_counted(self):
        stale = observation_v2(capture_ns=1_000_000_000_000 - 31_000_000_000, max_age_ns=30_000_000_000)
        wire, service = drive([stale], contract="v2")
        self.assertEqual(wire.published, [])
        self.assertEqual(service.dropped, {"capture_older_than_bound": 1})

    def test_two_publications_80_ms_apart_give_strictly_newer_commands(self):
        """The same capture republished by the relay every period: each command is newer than the last for the
        gateway, and each carries its own publication stamp."""
        wire, service = drive([observation_v2(published_ns=1_000_000_000_000), observation_v2(published_ns=1_000_080_000_000),
                               observation_v2(published_ns=1_000_160_000_000)], contract="v2")
        self.assertEqual(service.dropped, {})
        stamps = [c["timestamp_ns"] for c in wire.published]
        self.assertEqual(stamps, [1_000_000_000_000, 1_000_080_000_000, 1_000_160_000_000])
        self.assertEqual([c["expiry_ns"] - c["timestamp_ns"] for c in wire.published], [250_000_000] * 3)
        self.assertEqual([c["vx_mps"] for c in wire.published], [0.05] * 3)

    def test_a_v1_document_under_v2_is_dropped_contract_mismatch(self):
        wire, service = drive([observation()], contract="v2")
        self.assertEqual(wire.published, [])
        self.assertEqual(service.dropped, {"contract_mismatch": 1})

    def test_a_v2_document_under_the_default_v1_is_dropped_missing_fields(self):
        wire, service = drive([observation_v2()])
        self.assertEqual(service.contract, "v1")
        self.assertEqual(wire.published, [])
        self.assertEqual(service.dropped, {"missing_fields": 1})

    def test_the_v2_service_uses_the_v2_configuration(self):
        _, service = drive([], contract="v2")
        self.assertEqual((service.controller.config.vx_max_mps, service.controller.config.quality_min), (0.05, 0.0))
        _, service = drive([])
        self.assertEqual((service.controller.config.vx_max_mps, service.controller.config.quality_min), (0.15, 0.5))

    def test_an_unknown_contract_is_refused_at_construction(self):
        with self.assertRaises(ValueError):
            ApproachService(lambda _t: None, lambda _p: None, contract="v3")

    def test_the_ready_line_names_the_contract(self):
        with open(os.path.join(ROOT, "autonomy", "motion", "mobile_approach_service.py"), encoding="utf-8") as handle:
            source = handle.read()
        ready = [line for line in source.splitlines() if "ready (" in line and "print(" in line]
        self.assertEqual(len(ready), 1)
        self.assertIn("contract {service.contract}", ready[0])
        self.assertIn('choices=CONTRACTS, default=CONTRACT_V1_NAME', source)


class WhatItWillNotDo(unittest.TestCase):
    def test_it_reaches_no_device_no_host_and_no_cake(self):
        """The spec rejects the admin socket as a data channel and an in-process call as decorative. Neither the
        module nor anything it imports may reach a device, the host, or Cake."""
        source = open(os.path.join(ROOT, "autonomy", "motion", "mobile_approach_service.py"), encoding="utf-8").read()
        body = "\n".join(line for line in source.splitlines() if not line.strip().startswith(("#", '"""', "*")))
        for forbidden in ("lerobot", "admin-probe", "cake_admin", "/dev/", "serial", "cv2", "5557", "5571"):
            self.assertNotIn(forbidden, body, f"the capability process must not reach {forbidden}")

    def test_no_control_value_is_ever_printed(self):
        """stdout is captured into the slot's bounded diagnostics log and is never a data channel."""
        source = open(os.path.join(ROOT, "autonomy", "motion", "mobile_approach_service.py"), encoding="utf-8").read()
        printed = [line for line in source.splitlines() if "print(" in line and not line.strip().startswith("#")]
        self.assertTrue(printed, "the process does announce itself")
        for line in printed:
            for value in ("vx_mps", "vy_mps", "omega_rps", "command_document", "payload"):
                self.assertNotIn(value, line, "a control value must not be printed")


def _command():
    from autonomy.motion.mobile_approach import DesiredBaseMotion

    return DesiredBaseMotion(command_id="cmd_1", target_id="cube_b", timestamp_ns=1, vx_mps=0.0,
                             vy_mps=0.0, omega_rps=0.0, expiry_ns=2)


if __name__ == "__main__":
    unittest.main()
