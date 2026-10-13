#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# apps/console-api/tests/test_api.py: the read models and the command path over a stub runtime. What is checked here is
# that the API FORWARDS and never decides: a refusal keeps the runtime's own reason_code, a contract object arrives
# byte-identical, and every object that leaves validates against the contract it claims to be.
# Run directly: python3 apps/console-api/tests/test_api.py
import asyncio
import base64
import concurrent.futures
import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (CONTRACT_VERSION, ApiCase, StubRuntime, cake_status_object, command_object, contract_errors,  # noqa: E402
                     event_object, snapshot_object)

import demo_contracts  # noqa: E402

OBSERVATION_ID = "obs-20260101T000000Z-wrist-0001"
JPEG = base64.b64encode(bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")).decode("ascii")


class TestHandshake(ApiCase):
    async def test_hello_states_the_contract_and_reaches_the_runtime(self):
        answer = await self.json_of(await self.client.get("/api/hello?contract_version=1"))
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["contract_version"], CONTRACT_VERSION)
        self.assertFalse(answer["read_only"])
        self.assertTrue(answer["commands_enabled"])
        self.assertTrue(answer["runtime"]["reachable"])
        self.assertEqual(answer["runtime"]["data_mode"], "synthetic")
        self.assertEqual(answer["runtime_session"], answer["runtime"]["session"], "a page reads the session at the top level")
        self.assertTrue(answer["runtime_session"].startswith("rt-"))

    async def test_a_tab_on_another_contract_version_becomes_read_only(self):
        response = await self.client.get("/api/hello?contract_version=2")
        answer = await self.json_of(response)
        self.assertEqual(response.status, 200)  # the tab must be able to READ this answer
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "contract_incompatible")
        self.assertTrue(answer["read_only"])
        self.assertFalse(answer["commands_enabled"])
        self.assertNotIn("runtime", answer)  # a tab that cannot speak the contract is told nothing else

    async def test_a_runtime_that_is_gone_is_reported_and_not_guessed(self):
        self.stub.unavailable = True
        answer = await self.json_of(await self.client.get("/api/hello?contract_version=1"))
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "runtime_unavailable")
        self.assertTrue(answer["read_only"])
        self.assertFalse(answer["runtime"]["reachable"])


class TestIncompatibleRuntime(ApiCase):
    def make_stub(self):
        return StubRuntime(hello_ok=False)

    async def test_the_api_becomes_read_only_and_refuses_to_command(self):
        answer = await self.json_of(await self.client.get("/api/hello?contract_version=1"))
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "contract_incompatible")
        result = await self.json_of(await self.post("/api/commands", command_object()))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason_code"], "contract_incompatible")
        self.assertEqual(self.stub.submits, [], "a read-only API must not send the command at all")


class TestReadModels(ApiCase):
    async def test_the_snapshot_arrives_whole_and_validates(self):
        answer = await self.json_of(await self.client.get("/api/snapshot"))
        self.assertEqual(contract_errors("snapshot", answer), [])
        self.assertEqual(answer, snapshot_object())

    async def test_history_and_its_events_are_forwarded(self):
        self.stub.history_runs = [{"run_id": "sc-20260101T000000Z-abcd1234", "outcome": "placed_new", "evidence_complete": True, "artifact_digest": "ab" * 32}]
        self.stub.run_events["sc-20260101T000000Z-abcd1234"] = [event_object(1, run_id="sc-20260101T000000Z-abcd1234")]
        listing = await self.json_of(await self.client.get("/api/history"))
        self.assertEqual(listing["runs"], self.stub.history_runs)
        events = await self.json_of(await self.client.get("/api/history/sc-20260101T000000Z-abcd1234/events"))
        self.assertEqual(contract_errors("event", events["events"][0]), [])

    async def test_an_unknown_run_is_a_not_found_with_the_runtime_s_reason(self):
        response = await self.client.get("/api/history/sc-20260101T000000Z-nothing/events")
        self.assertEqual(response.status, 404)
        self.assertEqual((await self.json_of(response))["reason_code"], "unknown_run")

    async def test_a_run_id_that_is_not_an_id_never_reaches_the_runtime(self):
        response = await self.client.get("/api/history/not%20an%20id/events")
        self.assertEqual(response.status, 400)
        self.assertEqual((await self.json_of(response))["reason_code"], "bad_request")

    async def test_the_runtime_being_unreachable_is_a_service_error(self):
        self.stub.unavailable = True
        for path in ("/api/snapshot", "/api/cake", "/api/history", "/api/history/sc-1/events", f"/api/observations/{OBSERVATION_ID}/record",
                     "/api/commands/abcd1234ef01"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 503, path)
            self.assertEqual((await self.json_of(response))["reason_code"], "runtime_unavailable", path)


class TestCakeStatus(ApiCase):
    """The Cake resident status surface: forwarded whole, versioned apart from the contract, and a READ."""

    async def test_it_is_forwarded_whole_and_validates_against_its_own_schema(self):
        self.stub.cake_status_answer = cake_status_object(reachable=True, slots=0)
        answer = await self.json_of(await self.client.get("/api/cake"))
        self.assertEqual(answer, self.stub.cake_status_answer, "forwarded whole, with nothing added and nothing stripped")
        self.assertEqual(demo_contracts.validate_cake_status(answer), [])

    async def test_it_carries_its_own_version_and_not_the_contract_s(self):
        answer = await self.json_of(await self.client.get("/api/cake"))
        self.assertEqual(answer["cake_status_version"], demo_contracts.CAKE_STATUS_VERSION)
        self.assertNotIn("contract_version", answer)

    async def test_the_snapshot_s_cake_object_is_untouched_by_this_surface(self):
        """Contract v1 is sealed. This surface exists so that it did not have to be widened."""
        snapshot = await self.json_of(await self.client.get("/api/snapshot"))
        self.assertEqual(sorted(snapshot["cake"]), ["continuity", "ladder", "slots", "synthetic", "transactions", "update_preview"])
        self.assertEqual(contract_errors("snapshot", snapshot), [])

    async def test_a_lost_resident_is_stale_and_keeps_what_it_last_reported(self):
        self.stub.cake_status_answer = cake_status_object(stale=True, slots=2)
        answer = await self.json_of(await self.client.get("/api/cake"))
        self.assertFalse(answer["reachable"])
        self.assertTrue(answer["stale"])
        self.assertEqual(answer["observation"]["counts"]["slots"], 2, "retained, not emptied")

    async def test_reading_it_submits_no_command(self):
        """A page load and a reconnect both fetch this. Neither may reach the command path."""
        for _ in range(3):
            self.assertEqual((await self.client.get("/api/cake")).status, 200)
        self.assertEqual(self.stub.submits, [])
        self.assertEqual(self.stub.resolves, [])

    async def test_it_refuses_a_write(self):
        for method in (self.client.post, self.client.put, self.client.delete):
            response = await method("/api/cake")
            self.assertIn(response.status, (404, 405))


class TestObservations(ApiCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.stub.observations[(OBSERVATION_ID, "record")] = {"observation_id": OBSERVATION_ID, "gripper_state": "open", "cube_held": "no"}
        self.stub.observations[(OBSERVATION_ID, "perception")] = {"camera": "wrist", "pad": {"detected": True}}
        self.stub.frames[(OBSERVATION_ID, "raw")] = {"content_type": "image/jpeg", "bytes_b64": JPEG, "width_px": 320, "height_px": 240}

    async def test_a_record_and_a_perception_are_served_and_cacheable(self):
        for what in ("record", "perception"):
            response = await self.client.get(f"/api/observations/{OBSERVATION_ID}/{what}")
            self.assertEqual(response.status, 200, what)
            self.assertIn("immutable", response.headers["Cache-Control"], what)
            self.assertEqual((await self.json_of(response))["what"], what)

    async def test_an_unknown_kind_never_reaches_the_runtime(self):
        response = await self.client.get(f"/api/observations/{OBSERVATION_ID}/secrets")
        self.assertEqual(response.status, 400)

    async def test_a_frame_arrives_as_image_bytes(self):
        response = await self.client.get(f"/api/observations/{OBSERVATION_ID}/frame?what=raw&w=320")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "image/jpeg")
        self.assertIn("immutable", response.headers["Cache-Control"])
        body = await response.read()
        self.assertEqual(body, base64.b64decode(JPEG))
        self.assertTrue(body.startswith(b"\xff\xd8\xff"))

    async def test_a_frame_the_runtime_does_not_have_is_a_not_found(self):
        response = await self.client.get(f"/api/observations/{OBSERVATION_ID}/frame?what=overlay")
        self.assertEqual(response.status, 404)

    async def test_a_bad_width_or_kind_is_refused_before_the_runtime(self):
        for query in ("what=raw&w=-1", "what=raw&w=wide", "what=secret"):
            response = await self.client.get(f"/api/observations/{OBSERVATION_ID}/frame?{query}")
            self.assertEqual(response.status, 400, query)


class TestCommands(ApiCase):
    async def test_a_command_result_comes_back_whole_and_validates(self):
        response = await self.post("/api/commands", command_object())
        self.assertEqual(response.status, 200)
        result = await self.json_of(response)
        self.assertEqual(contract_errors("command_result", result), [])
        self.assertEqual(result["status"], "accepted")

    async def test_the_same_command_id_gets_the_same_answer(self):
        command = command_object()
        first = await self.json_of(await self.post("/api/commands", command))
        second = await self.json_of(await self.post("/api/commands", command))
        self.assertEqual(first, second)
        self.assertEqual(len(self.stub.results), 1, "the runtime keyed one result, not two")

    async def test_a_rejection_is_forwarded_and_never_softened(self):
        self.stub.results["rejected0123456789ab"] = {"command_id": "rejected0123456789ab", "status": "rejected", "reason_code": "run_active", "run_id": None}
        response = await self.post("/api/commands", command_object(command_id="rejected0123456789ab"))
        self.assertEqual(response.status, 200)  # a refusal is an answer, not a transport failure
        result = await self.json_of(response)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason_code"], "run_active")
        self.assertEqual(contract_errors("command_result", result), [])

    async def test_a_command_with_no_usable_id_is_refused_before_the_runtime(self):
        for command in ({"type": "observe"}, command_object(command_id="not an id"), command_object(command_id="")):
            response = await self.post("/api/commands", command)
            self.assertEqual(response.status, 400, command)
            self.assertEqual((await self.json_of(response))["reason_code"], "command_invalid")
        self.assertEqual(self.stub.submits, [])

    async def test_a_command_on_another_contract_version_is_rejected_here(self):
        command = command_object()
        command["contract_version"] = "2"
        result = await self.json_of(await self.post("/api/commands", command))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason_code"], "contract_incompatible")
        command = command_object()
        command["client"]["contract_version"] = "2"
        result = await self.json_of(await self.post("/api/commands", command))
        self.assertEqual(result["reason_code"], "contract_incompatible")
        self.assertEqual(self.stub.submits, [])

    async def test_a_lost_answer_becomes_uncertain_and_the_command_is_not_resent(self):
        self.stub.lose_submit = True
        result = await self.json_of(await self.post("/api/commands", command_object()))
        self.assertEqual(contract_errors("command_result", result), [])
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["reason_code"], "response_lost")
        self.assertEqual(len(self.stub.submits), 1, "the API must never resend a command by itself")

    async def test_the_result_lookup_is_how_a_lost_answer_is_resolved(self):
        self.stub.lose_submit = True
        result = await self.json_of(await self.post("/api/commands", command_object()))
        command_id = result["command_id"]
        missing = await self.client.get(f"/api/commands/{command_id}")
        self.assertEqual(missing.status, 404)
        self.assertEqual((await self.json_of(missing))["reason_code"], "unknown_command")
        self.stub.results[command_id] = {"command_id": command_id, "status": "running", "reason_code": None, "run_id": "sc-20260101T000000Z-abcd1234"}
        found = await self.json_of(await self.client.get(f"/api/commands/{command_id}"))
        self.assertEqual(found["status"], "running")
        self.assertEqual(contract_errors("command_result", found), [])

    async def test_a_client_that_wraps_the_command_is_understood(self):
        # The body IS the contract command; a client that sends {"command_id": id, "command": {...}} names the same
        # idempotency key, so it is unwrapped rather than refused, and what reaches the runtime is the command itself.
        command = command_object()
        response = await self.post("/api/commands", {"command_id": command["command_id"], "command": command})
        self.assertEqual(response.status, 200)
        self.assertEqual((await self.json_of(response))["status"], "accepted")
        self.assertEqual(self.stub.submits, [command], "the runtime is handed the contract command, not the wrapper")

    async def test_two_different_ids_in_one_body_are_refused_and_never_guessed(self):
        command = command_object()
        response = await self.post("/api/commands", {"command_id": "another0123456789ab", "command": command})
        self.assertEqual(response.status, 400)
        self.assertEqual((await self.json_of(response))["reason_code"], "command_id_mismatch")
        self.assertEqual(self.stub.submits, [])

    async def test_a_wrapper_supplies_the_id_when_the_command_has_none(self):
        command = command_object()
        inner = {key: value for key, value in command.items() if key != "command_id"}
        response = await self.post("/api/commands", {"command_id": command["command_id"], "command": inner})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.stub.submits[0]["command_id"], command["command_id"])

    async def test_stop_is_forwarded(self):
        self.stub.stop_answer = {"ok": True, "stop": {"requested_mono_ns": 123, "run_id": "sc-20260101T000000Z-abcd1234"}}
        answer = await self.json_of(await self.post("/api/stop", {}))
        self.assertEqual(answer, self.stub.stop_answer)

    async def test_a_reused_id_with_a_different_body_is_refused_instead_of_being_answered_for_the_first_one(self):
        """Idempotency by id alone answers a DIFFERENT request with the first one's outcome, silently. The runtime's
        receipt carries the digest of the command it recorded, so the two can be seen to differ; nothing is started by
        the second submit either way, because the runtime answered out of its journal."""
        first = command_object("start_cycle", {"plan_only": False}, command_id="shared0123456789abcd")
        accepted = await self.json_of(await self.post("/api/commands", first))
        self.assertEqual(accepted["status"], "accepted")

        other = command_object("start_cycle", {"plan_only": True}, command_id="shared0123456789abcd")
        response = await self.post("/api/commands", other)
        self.assertEqual(response.status, 409)
        body = await self.json_of(response)
        self.assertEqual(body["reason_code"], "command_id_reused")
        self.assertEqual(body["recorded_payload_digest"], accepted["payload_digest"])
        self.assertNotEqual(body["submitted_payload_digest"], accepted["payload_digest"])
        self.assertEqual(len(self.stub.results), 1, "the runtime keyed one command under that id and started nothing else")

    async def test_the_same_id_with_the_same_body_still_returns_the_recorded_receipt(self):
        command = command_object("start_cycle", {"plan_only": False}, command_id="repeat0123456789abcd")
        first = await self.json_of(await self.post("/api/commands", command))
        second = await self.json_of(await self.post("/api/commands", command))
        self.assertEqual(first, second)
        self.assertEqual(first["payload_digest"], second["payload_digest"])
        self.assertEqual(contract_errors("command_result", second), [])

    async def test_a_receipt_that_carries_no_digest_is_not_read_as_agreement(self):
        """A command the runtime rejected records no command object, so its receipt has no digest. Refusing on that
        would refuse every honest repeat of a rejected command, so the repeat is answered and the console sees the
        recorded rejection, which is what it asked for."""
        self.stub.results["norecord0123456789ab"] = {"command_id": "norecord0123456789ab", "status": "rejected", "reason_code": "run_active", "run_id": None}
        response = await self.post("/api/commands", command_object(command_id="norecord0123456789ab"))
        self.assertEqual(response.status, 200)
        self.assertEqual((await self.json_of(response))["reason_code"], "run_active")


class TestStopIsACommandWithAnIdentity(ApiCase):
    """The browser has always sent a whole contract stop command to /api/stop and the route threw it away, so the
    console's Stop was the one command outside the runtime's journal. It is submitted now."""

    async def test_a_body_that_is_a_stop_command_is_submitted_under_its_own_id(self):
        command = command_object("stop", {}, command_id="stopid0123456789abcd")
        answer = await self.json_of(await self.post("/api/stop", command))
        self.assertTrue(answer["ok"])
        self.assertEqual(self.stub.submits[-1]["command_id"], "stopid0123456789abcd")
        self.assertEqual(contract_errors("command_result", answer["command"]), [])
        self.assertEqual(answer["command"]["status"], "finished")
        self.assertEqual(answer["stop"]["run_id"], "sc-20260101T000000Z-abcd1234")
        self.assertIsNone(answer["stop"]["requested_mono_ns"], "no number is invented here; stop.requested carries the real one")

    async def test_the_same_stop_id_twice_is_one_stop_and_the_recorded_answer(self):
        command = command_object("stop", {}, command_id="stoptwice0123456789")
        first = await self.json_of(await self.post("/api/stop", command))
        second = await self.json_of(await self.post("/api/stop", command))
        self.assertEqual(first, second)
        self.assertEqual(len(self.stub.results), 1, "one recorded stop, whatever the browser did")

    async def test_a_stop_that_found_no_run_is_never_reported_as_taken(self):
        self.stub.stop_command_result = {"status": "finished", "reason_code": "no_active_run", "run_id": None}
        answer = await self.json_of(await self.post("/api/stop", command_object("stop", {}, command_id="stopnone0123456789a")))
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "no_active_run")
        self.assertIsNone(answer["stop"])

    async def test_a_body_that_is_not_a_command_keeps_the_plain_stop_op(self):
        self.stub.stop_answer = {"ok": True, "stop": {"requested_mono_ns": 99, "run_id": "sc-20260101T000000Z-abcd1234"}}
        for body in ({}, {"type": "stop"}, command_object("observe", {"holding": False})):
            answer = await self.json_of(await self.post("/api/stop", body))
            self.assertEqual(answer, self.stub.stop_answer, body)
        self.assertEqual(self.stub.submits, [], "nothing was journaled for a body that named no command")

    async def test_a_lost_stop_answer_is_uncertain_and_the_stop_is_not_sent_again(self):
        self.stub.lose_submit = True
        response = await self.post("/api/stop", command_object("stop", {}, command_id="stoplost0123456789a"))
        answer = await self.json_of(response)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["command"]["status"], "uncertain")
        self.assertEqual(answer["command"]["reason_code"], "response_lost")
        self.assertEqual(len(self.stub.submits), 1)


class TestTheStopDoesNotWaitBehindOtherWork(ApiCase):
    """asyncio.to_thread runs on the loop's default executor and that executor has a QUEUE. Fill it with reads and a
    stop submitted afterwards would sit in that queue with the robot moving. The stop path has a worker of its own.

    The default executor is set to ONE worker here so the queue is visible at all; the real one has more, and the
    point is the same either way: the stop never shares it."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.released = threading.Event()
        self.addCleanup(self.released.set)
        loop = asyncio.get_running_loop()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.addCleanup(lambda: self.executor.shutdown(wait=False))
        loop.set_default_executor(self.executor)

        blocking = self.released
        original = self.stub.snapshot

        def slow_snapshot():
            blocking.wait(10.0)
            return original()

        self.stub.snapshot = slow_snapshot

    async def test_a_stop_is_answered_while_every_shared_worker_is_busy(self):
        reads = [asyncio.create_task(self.client.get("/api/snapshot")) for _ in range(3)]
        await asyncio.sleep(0.1)  # the one shared worker is inside a snapshot and two more are queued behind it
        self.assertFalse(any(task.done() for task in reads), "the reads were supposed to be stuck")

        stop = command_object("stop", {}, command_id="stopurgent012345678")
        response = await asyncio.wait_for(self.post("/api/stop", stop), timeout=5.0)
        self.assertEqual(response.status, 200)
        answer = await self.json_of(response)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["command"]["command_id"], "stopurgent012345678")

        self.released.set()
        for task in reads:
            (await task).close()


class TestReplayOnly(ApiCase):
    def make_stub(self):
        return StubRuntime(data_mode="replay")

    async def test_every_command_is_rejected_and_reading_still_works(self):
        self.assertEqual((await self.client.get("/api/snapshot")).status, 200)
        await self.client.get("/api/hello?contract_version=1")  # the API learns the mode from the runtime
        for command_type, args in (("start_cycle", {"plan_only": False}), ("observe", {"holding": False}), ("return_arm", {"plan_only": True})):
            result = await self.json_of(await self.post("/api/commands", command_object(command_type, args)))
            self.assertEqual(result["status"], "rejected", command_type)
            self.assertEqual(result["reason_code"], "replay_only", command_type)
        stop = await self.post("/api/stop", {})
        self.assertEqual(stop.status, 409)
        self.assertEqual((await self.json_of(stop))["reason_code"], "replay_only")
        self.assertEqual(self.stub.submits, [], "a replay-only API must not send a command at all")

    async def test_the_runtime_s_own_refusal_is_reported_when_the_api_has_not_learned_the_mode(self):
        # No hello yet: the API does not know the mode, so it forwards and the RUNTIME refuses. The answer is the same.
        result = await self.json_of(await self.post("/api/commands", command_object()))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason_code"], "replay_only")
        self.assertEqual(len(self.stub.submits), 1)

    async def test_the_capabilities_of_a_replay_snapshot_say_disabled(self):
        snapshot = await self.json_of(await self.client.get("/api/snapshot"))
        states = {item["id"]: (item["state"], item["reason_code"]) for item in snapshot["capabilities"]}
        self.assertEqual(states["start_cycle"], ("disabled", "replay_only"))
        self.assertEqual(states["stop"], ("disabled", "replay_only"))


class TestSanitizerKeepsContractObjectsIntact(unittest.TestCase):
    """The last line must not damage what it protects: a real snapshot, event and history entry go through unchanged."""

    def test_nothing_is_rewritten(self):
        from console_api import repository_root
        from console_api.app import OutgoingSanitizer

        sanitizer = OutgoingSanitizer.for_runtime(None, extra=["a-credential-value-that-is-long", str(repository_root())])
        for name, obj in (("snapshot", snapshot_object()), ("active", snapshot_object(run_active=True)), ("replay", snapshot_object(data_mode="replay")),
                          ("event", event_object(7)), ("result", {"command_id": "abcd1234ef0123456789", "status": "finished", "reason_code": None, "run_id": "sc-1",
                                      "payload_digest": "sha256:" + "0123456789abcdef" * 4, "first_seen_wall_utc": "2026-01-01T00:00:00.000Z"})):
            clean, changed = sanitizer.scrub(json.loads(json.dumps(obj)), generic=True)
            self.assertFalse(changed, f"{name} was rewritten by the sanitizer")
            self.assertEqual(clean, obj, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestRunOutput(ApiCase):
    """The controller's own stdout and stderr, which the supervisor has always written and nothing ever served."""

    RUN = "sc-20260101T000000Z-abcd1234"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.stub.run_output_text[self.RUN] = {"stdout": "[STATE] SEARCH\nCYCLE FAILED: ALIGN\n", "stderr": ""}

    async def test_the_output_of_a_run_is_served_with_what_it_is_and_how_much_of_it_there_is(self):
        answer = await self.json_of(await self.client.get(f"/api/history/{self.RUN}/output"))
        self.assertEqual(answer["run_id"], self.RUN)
        self.assertEqual(answer["stream"], "stdout")
        self.assertIn("CYCLE FAILED: ALIGN", answer["text"])
        self.assertFalse(answer["truncated"], "a reader is told whether it was shown the whole of it")
        self.assertEqual(answer["total_bytes"], len(answer["text"].encode("utf-8")))

    async def test_stderr_is_a_separate_stream_and_an_empty_one_is_not_an_error(self):
        answer = await self.json_of(await self.client.get(f"/api/history/{self.RUN}/output?stream=stderr"))
        self.assertEqual((answer["stream"], answer["text"], answer["truncated"]), ("stderr", "", False))

    async def test_a_stream_this_api_does_not_serve_never_reaches_the_runtime(self):
        for stream in ("manifest.json", "../journal.jsonl", "", "STDOUT"):
            response = await self.client.get(f"/api/history/{self.RUN}/output?stream={stream}")
            self.assertEqual(response.status, 400, stream)
            self.assertEqual((await self.json_of(response))["reason_code"], "bad_request", stream)

    async def test_a_run_id_that_is_not_an_id_never_reaches_the_runtime(self):
        for run_id in ("not%20an%20id", "..%2f..%2fjournal.jsonl", "a%2fb"):
            response = await self.client.get(f"/api/history/{run_id}/output")
            self.assertIn(response.status, (400, 404), run_id)
            if response.status == 400:
                self.assertEqual((await self.json_of(response))["reason_code"], "bad_request", run_id)

    async def test_an_unknown_run_is_a_not_found(self):
        response = await self.client.get("/api/history/sc-20260101T000000Z-nothing/output")
        self.assertEqual(response.status, 404)
        self.assertEqual((await self.json_of(response))["reason_code"], "unknown_run")

    async def test_a_bound_that_is_not_a_positive_number_is_refused(self):
        for value in ("0", "-5", "many"):
            response = await self.client.get(f"/api/history/{self.RUN}/output?max_bytes={value}")
            self.assertEqual(response.status, 400, value)

    async def test_the_runtime_being_unreachable_is_a_service_error(self):
        self.stub.unavailable = True
        response = await self.client.get(f"/api/history/{self.RUN}/output")
        self.assertEqual(response.status, 503)
