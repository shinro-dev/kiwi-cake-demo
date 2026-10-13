#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_cake_validate_and_path.py: ValidatePlan of the exact prepared bytes before an apply,
# and the transaction's state path rebuilt from the resident's flight ring right after the answer.
#
# The answer shapes are admin-probe's: ValidatePlan prints `plan_verdict valid|invalid`, `plan_reason_code N` and, for a
# valid one, `plan_artifact_digest <hex>`, under `status ok` or `status plan_invalid`; read-flight prints one
# `record arg0=.. arg1=.. correlation=.. epoch=.. event=0x.. flags=.. sequence=.. severity=.. subject=..` line per record.
# A transaction transition is event 0x03000006 (refused: 0x03000007) with arg0 the state left and arg1 the state
# entered, numbered 1 CREATED to 12 FAILED, and its correlation is the first eight bytes of the transaction uuid read
# little-endian. A fake admin-probe written at run time stands in for the tool; no resident and no socket.
# Run directly: python3 packages/demo-runtime/tests/test_cake_validate_and_path.py
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cake_check_fakes import INERT_CAPSULE, MOBILE_CAPSULE, ScriptedCheck, slot  # noqa: E402

from demo_runtime import cake_activation, capability_check  # noqa: E402

TX = "22398968749b77310d8643700bfb61d3"
OTHER_TX = "57688d24e10913aa9ab0fe08f6b4720b"
COMPLETED = f"status ok\nreplay 0\nsnapshot_present 0\ntransaction_uuid {TX}\ntransaction_handle 9\ntransaction_phase COMPLETED\n"
ABORTED = f"status ok\nreplay 0\nsnapshot_present 0\ntransaction_uuid {TX}\ntransaction_handle 9\ntransaction_phase ABORTED\n"


def correlation(uuid_hex: str) -> int:
    return int.from_bytes(bytes.fromhex(uuid_hex)[:8], "little")


def record(sequence: int, *, event: int, arg0: int = 0, arg1: int = 0, corr: int = 0, flags: int = 0) -> str:
    return (f"record arg0={arg0} arg1={arg1} correlation={corr} epoch=3 event=0x{event:08x} flags={flags} "
            f"sequence={sequence} severity=0 subject=0")


def flight(*lines: str) -> str:
    return "status ok\nreplay 0\nsnapshot_present 0\nflight_records " + str(len(lines)) + "\n" + "\n".join(lines) + "\n"


def transitions(uuid_hex: str, codes: list, start: int) -> list:
    corr = correlation(uuid_hex)
    return [record(start + index, event=0x03000006, arg0=left, arg1=right, corr=corr)
            for index, (left, right) in enumerate(zip(codes, codes[1:]))]


ADMIN = 0x0B000001


class TheStatePathIsRebuiltFromTheFlightRing(unittest.TestCase):
    def test_a_completed_transaction_reads_its_whole_path_in_sequence_order(self):
        ours = transitions(TX, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 10)
        theirs = transitions(OTHER_TX, [1, 2, 11], 5)
        text = flight(record(1, event=ADMIN), *theirs, *reversed(ours), record(40, event=ADMIN))
        path = cake_activation.transaction_state_path(text, TX)
        self.assertEqual(path["read"], cake_activation.READ_OK)
        self.assertEqual(path["states"], ["CREATED", "VALIDATED", "PREPARED", "QUIESCING", "ACTIVATING", "COMMITTING",
                                          "COMMITTED", "DURABLE", "RETIRING", "COMPLETED"])
        self.assertTrue(path["starts_at_created"])
        self.assertEqual(path["terminal"], "COMPLETED")
        self.assertEqual(path["transitions"], 9)
        self.assertEqual(path["refused_transitions"], 0)

    def test_an_aborted_transaction_ends_aborted(self):
        path = cake_activation.transaction_state_path(flight(*transitions(TX, [1, 2, 3, 11], 3)), TX)
        self.assertEqual(path["states"], ["CREATED", "VALIDATED", "PREPARED", "ABORTED"])
        self.assertEqual(path["terminal"], "ABORTED")

    def test_a_ring_that_lost_the_start_says_so(self):
        path = cake_activation.transaction_state_path(flight(*transitions(TX, [3, 4, 5, 6, 7, 8, 9, 10], 200)), TX)
        self.assertEqual(path["states"][0], "PREPARED")
        self.assertFalse(path["starts_at_created"])

    def test_a_refused_transition_is_counted_and_not_drawn_as_a_state(self):
        corr = correlation(TX)
        lines = transitions(TX, [1, 2, 11], 3) + [record(9, event=0x03000007, arg0=11, arg1=10, corr=corr, flags=1)]
        path = cake_activation.transaction_state_path(flight(*lines), TX)
        self.assertEqual(path["states"], ["CREATED", "VALIDATED", "ABORTED"])
        self.assertEqual(path["refused_transitions"], 1)

    def test_a_transaction_the_ring_no_longer_holds_is_not_an_empty_path(self):
        path = cake_activation.transaction_state_path(flight(record(1, event=ADMIN)), TX)
        self.assertEqual(path["read"], cake_activation.PATH_NOT_IN_RING)
        self.assertEqual(path["states"], [])
        self.assertIsNone(path["terminal"])

    def test_no_transaction_and_a_failed_read_are_said_to_be_those(self):
        self.assertEqual(cake_activation.transaction_state_path(flight(), None)["read"], cake_activation.PATH_NO_TRANSACTION)
        self.assertEqual(cake_activation.transaction_state_path(flight(), "not-a-uuid")["read"],
                         cake_activation.PATH_NO_TRANSACTION)
        self.assertEqual(cake_activation.transaction_state_path("", TX)["read"], cake_activation.READ_FAILED)
        self.assertEqual(cake_activation.transaction_state_path("status unavailable\n", TX)["read"], cake_activation.READ_FAILED)

    def test_a_state_code_it_does_not_name_is_printed_as_its_number(self):
        path = cake_activation.transaction_state_path(flight(*transitions(TX, [1, 44], 1)), TX)
        self.assertEqual(path["states"], ["CREATED", "44"])


class TheValidationAnswer(unittest.TestCase):
    def test_valid(self):
        answer = cake_activation.parse_validation_answer(
            "status ok\nreplay 0\nsnapshot_present 0\nplan_verdict valid\nplan_reason_code 0\nplan_artifact_digest " + "ab" * 32)
        self.assertEqual(answer["verdict"], cake_activation.PLAN_VALID)
        self.assertEqual(answer["plan_artifact_digest"], "ab" * 32)
        self.assertEqual(answer["plan_reason_code"], "0")

    def test_invalid(self):
        answer = cake_activation.parse_validation_answer("status plan_invalid\nplan_verdict invalid\nplan_reason_code 7\n")
        self.assertEqual(answer["verdict"], cake_activation.PLAN_INVALID)
        self.assertEqual(answer["plan_reason_code"], "7")
        self.assertIsNone(answer["plan_artifact_digest"])

    def test_no_verdict_is_not_a_verdict(self):
        for text in ("status unavailable\n", "", "status ok\n", "status ok\nplan_verdict maybe\n"):
            with self.subTest(text=text):
                answer = cake_activation.parse_validation_answer(text)
                self.assertEqual(answer["verdict"], cake_activation.PLAN_UNANSWERED)


def fake_probe(directory: str) -> str:
    """An admin-probe stand-in that logs its argv and payload and answers validate-plan and read-flight."""
    path = os.path.join(directory, "admin-probe")
    with open(path, "w", encoding="ascii") as handle:
        handle.write(f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
payload = None
if "--payload" in args:
    with open(args[args.index("--payload") + 1], "rb") as source:
        payload = source.read().decode("latin-1")
with open(os.environ["FAKE_PROBE_LOG"], "a") as log:
    log.write(json.dumps({{"argv": args, "payload": payload}}) + "\\n")
op = args[2]
if op == "validate-plan":
    if "flipped" in (payload or ""):
        print("status plan_invalid\\nreplay 0\\nsnapshot_present 0\\nplan_verdict invalid\\nplan_reason_code 7")
    else:
        print("status ok\\nreplay 0\\nsnapshot_present 0\\nplan_verdict valid\\nplan_reason_code 0\\nplan_artifact_digest " + "ab" * 32)
elif op == "read-flight":
    print("status ok\\nreplay 0\\nsnapshot_present 0\\nflight_records 0")
else:
    print("status unknown_operation")
""")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


class ValidatePlanAsksTheResidentAboutTheExactBytes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.probe = fake_probe(self.tmp.name)
        self.socket = os.path.join(self.tmp.name, "admin.sock")
        open(self.socket, "w").close()
        self.log = os.path.join(self.tmp.name, "probe.log")
        patcher = mock.patch.dict(os.environ, {"FAKE_PROBE_LOG": self.log})
        patcher.start()
        self.addCleanup(patcher.stop)

    def calls(self) -> list:
        with open(self.log, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def test_the_request_carries_the_exact_bytes_and_no_operation_identity(self):
        answer = cake_activation.validate_plan(self.probe, self.socket, b"the valid prepared swap plan")
        self.assertEqual(answer["verdict"], cake_activation.PLAN_VALID)
        (call,) = self.calls()
        self.assertEqual(call["argv"][:3], ["request", self.socket, "validate-plan"])
        self.assertIn("--payload", call["argv"])
        self.assertNotIn("--uuid", call["argv"], "ValidatePlan is a question: it takes no operation identity")
        self.assertEqual(call["payload"], "the valid prepared swap plan")

    def test_the_payload_file_is_removed(self):
        before = set(os.listdir(tempfile.gettempdir()))
        cake_activation.validate_plan(self.probe, self.socket, b"the valid prepared swap plan")
        self.assertEqual({name for name in set(os.listdir(tempfile.gettempdir())) - before if name.startswith("cake-")}, set())

    def test_a_tampered_twin_reads_invalid(self):
        answer = cake_activation.validate_plan(self.probe, self.socket, b"the swap plan with one byte flipped")
        self.assertEqual(answer["verdict"], cake_activation.PLAN_INVALID)
        self.assertEqual(answer["plan_reason_code"], "7")

    def test_it_never_raises_and_says_why_it_was_not_answered(self):
        self.assertEqual(cake_activation.validate_plan("relative/probe", self.socket, b"x")["reason_code"],
                         "probe_binary_unavailable")
        self.assertEqual(cake_activation.validate_plan(self.probe, self.socket + ".absent", b"x")["reason_code"],
                         "admin_socket_absent")
        self.assertEqual(cake_activation.validate_plan(self.probe, self.socket, b"")["reason_code"], "candidate_empty")
        for answer in (cake_activation.validate_plan("relative/probe", self.socket, b"x"),):
            self.assertEqual(answer["verdict"], cake_activation.PLAN_UNANSWERED)

    def test_the_readers_bind_the_paths_once(self):
        validate = cake_activation.plan_validator(self.probe, self.socket)
        self.assertEqual(validate(b"the valid prepared swap plan")["verdict"], cake_activation.PLAN_VALID)
        read_flight = cake_activation.flight_reader(self.probe, self.socket)
        self.assertIn("flight_records 0", read_flight())


class TheCheckRecordsBoth(unittest.TestCase):
    def build(self, answer_text, slots, **kwargs):
        scripted = ScriptedCheck(answer_text, slots, **kwargs)
        self.addCleanup(scripted.close)
        return scripted

    def test_the_validation_is_recorded_before_the_submit_and_the_path_after_the_answer(self):
        order = []

        def validate(payload):
            order.append(("validate", payload))
            return {"verdict": cake_activation.PLAN_VALID, "plan_reason_code": "0", "plan_artifact_digest": "ab" * 32,
                    "status": "ok", "reason_code": None}

        def read_flight():
            order.append(("read_flight",))
            return flight(*transitions(TX, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 1))

        scripted = self.build(COMPLETED, [slot(INERT_CAPSULE, 1, 1, active=False), slot(MOBILE_CAPSULE, 2, 2, active=True)],
                              validate=validate, read_flight=read_flight)
        answer = scripted.apply("swapped")
        self.assertEqual(answer["activation_state"], "OBSERVED")
        self.assertEqual(answer["validation"]["verdict"], cake_activation.PLAN_VALID)
        self.assertEqual(answer["state_path"]["terminal"], "COMPLETED")
        self.assertEqual(answer["state_path"]["states"][0], "CREATED")
        submitted = [call for call in scripted.calls if call[0] == "submit"]
        self.assertEqual(order[0], ("validate", submitted[0][2]), "the exact bytes submitted were the bytes validated")
        self.assertEqual(order[1], ("read_flight",))
        (check,) = scripted.check.checks()[0]
        self.assertEqual(check["validation"]["verdict"], cake_activation.PLAN_VALID)
        self.assertEqual(check["state_path"]["states"][-1], "COMPLETED")

    def test_an_aborted_answer_shows_its_path(self):
        scripted = self.build(ABORTED, [slot(INERT_CAPSULE, 1, 1, active=False)] * 2,
                              validate=lambda payload: {"verdict": cake_activation.PLAN_VALID},
                              read_flight=lambda: flight(*transitions(TX, [1, 2, 3, 4, 11], 1)))
        answer = scripted.apply("abort-swap")
        self.assertEqual(answer["activation_state"], "REJECTED_BY_CAKE")
        self.assertEqual(answer["state_path"]["states"], ["CREATED", "VALIDATED", "PREPARED", "QUIESCING", "ABORTED"])

    def test_an_invalid_verdict_is_evidence_and_does_not_stop_the_resident_judging(self):
        refused = "status mutation_refused\nrefusal_code 100\n"
        scripted = self.build(refused, [slot(INERT_CAPSULE, 1, 1, active=False)] * 2,
                              validate=lambda payload: {"verdict": cake_activation.PLAN_INVALID, "plan_reason_code": "7"},
                              read_flight=lambda: flight())
        answer = scripted.apply("tampered")
        self.assertEqual(answer["validation"]["verdict"], cake_activation.PLAN_INVALID)
        self.assertEqual(answer["activation_state"], cake_activation.REFUSED_BEFORE_TRANSACTION)
        self.assertEqual(answer["state_path"]["read"], cake_activation.PATH_NO_TRANSACTION)
        self.assertEqual(len([call for call in scripted.calls if call[0] == "submit"]), 1)

    def test_without_readers_both_say_not_asked(self):
        scripted = self.build(COMPLETED, [slot(INERT_CAPSULE, 1, 1, active=False), slot(MOBILE_CAPSULE, 2, 2, active=True)])
        answer = scripted.apply("swapped")
        self.assertEqual(answer["validation"]["verdict"], cake_activation.PLAN_NOT_ASKED)
        self.assertEqual(answer["state_path"]["read"], cake_activation.PATH_NOT_ASKED)

    def test_readers_that_raise_are_recorded_as_failed_and_lose_no_record(self):
        def boom(*_args):
            raise RuntimeError("reader failed")

        scripted = self.build(COMPLETED, [slot(INERT_CAPSULE, 1, 1, active=False), slot(MOBILE_CAPSULE, 2, 2, active=True)],
                              validate=boom, read_flight=boom)
        answer = scripted.apply("swapped")
        self.assertEqual(answer["activation_state"], "OBSERVED")
        self.assertEqual(answer["validation"]["verdict"], cake_activation.PLAN_UNANSWERED)
        self.assertEqual(answer["state_path"]["read"], cake_activation.READ_FAILED)
        (check,) = scripted.check.checks()[0]
        self.assertTrue(check["answered"])

    def test_the_close_wait_covers_the_flight_read(self):
        base = capability_check.close_wait_bound_s(read_timeout_s=3.0, submit_timeout_s=8.0, binding_timeout_s=2.0)
        self.assertEqual(capability_check.close_wait_bound_s(read_timeout_s=3.0, submit_timeout_s=8.0, binding_timeout_s=2.0,
                                                             flight_timeout_s=3.0), base + 3.0)


if __name__ == "__main__":
    unittest.main()
