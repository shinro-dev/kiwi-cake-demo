#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# apps/console-api/tests/test_robot.py: the robot routes over a stub runtime. GET /api/robot forwards the runtime's
# robot_status whole and is a READ. POST /api/robot/confirm-session GRANTS permission to move, so it passes exactly the
# guard POST /api/commands passes, through the same function, takes a body of exactly two keys (the robot resident's
# session and its supervisor host instance, itself exactly slot_instance, spawn_sequence and pid), and forwards the
# runtime's refusal with the runtime's own reason code. GET and POST /api/capability/check are the capability slot check:
# the read is forwarded whole and submits nothing; the apply moves a Cake slot and no motor, so it passes the same guard,
# the same replay block and the same runtime-session check, and takes a body of exactly three keys.
# Run directly: python3 apps/console-api/tests/test_robot.py
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import ROBOT_HOST_INSTANCE, ROBOT_SESSION, ApiCase, StubRuntime, make_app, robot_status_object  # noqa: E402

from console_api.security import (CONSOLE_HEADER_MISSING, CONTENT_TYPE_NOT_JSON, CREDENTIAL_INVALID,  # noqa: E402
                                  CREDENTIAL_MISSING, HOST_NOT_ALLOWED, ORIGIN_NOT_ALLOWED, CredentialStore)

import demo_contracts  # noqa: E402

CONFIRM = "/api/robot/confirm-session"
BODY = {"session_uuid": ROBOT_SESSION, "host_instance": dict(ROBOT_HOST_INSTANCE)}
CHECK = "/api/capability/check"
CHECK_BODY = {"candidate_key": "mobile-demo-swapped", "payload_digest": "sha256:" + "ab" * 32, "session_uuid": ROBOT_SESSION}


class TheRead(ApiCase):
    async def test_it_is_forwarded_whole_and_validates_against_its_own_schema(self):
        answer = await self.json_of(await self.client.get("/api/robot"))
        self.assertEqual(answer, self.stub.robot_status_answer, "forwarded whole, nothing added and nothing stripped")
        self.assertEqual(demo_contracts.validate_robot_status(answer), [])
        self.assertEqual(answer["robot_status_version"], demo_contracts.ROBOT_STATUS_VERSION)
        self.assertNotIn("contract_version", answer)
        self.assertEqual((answer["identity"]["display_label"], answer["identity"]["is_robot"]), ("bench LeKiwi", True))
        self.assertNotIn("robot_id_digest", answer["connection"])
        self.assertNotIn("unit-" + "0042", str(answer), "the robot id is never sent, in any form")
        motion = answer["motion_permission"]
        self.assertEqual((motion["current_session_uuid"], motion["current_host_instance"], motion["confirmable"]),
                         (ROBOT_SESSION, ROBOT_HOST_INSTANCE, True), "the values a confirmation has to name are on the read")
        self.assertIsInstance(motion["current_host_instance"]["slot_instance"], str, "a u64 handle is a string on the wire")

    async def test_a_lost_robot_is_forwarded_as_the_runtime_said_it(self):
        self.stub.robot_status_answer = robot_status_object(reachable=False)
        answer = await self.json_of(await self.client.get("/api/robot"))
        self.assertEqual((answer["reachable"], answer["reason_code"]), (False, "robot_unreachable"))

    async def test_reading_it_submits_nothing_and_confirms_nothing(self):
        for _ in range(3):
            self.assertEqual((await self.client.get("/api/robot")).status, 200)
        self.assertEqual((self.stub.submits, self.stub.robot_confirmations), ([], []))

    async def test_a_runtime_that_is_gone_is_a_service_error(self):
        self.stub.unavailable = True
        response = await self.client.get("/api/robot")
        self.assertEqual(response.status, 503)
        self.assertEqual((await self.json_of(response))["reason_code"], "runtime_unavailable")

    async def test_it_refuses_a_write(self):
        for method in (self.client.post, self.client.put, self.client.delete):
            self.assertIn((await method("/api/robot")).status, (404, 405))


class TheConfirmation(ApiCase):
    async def test_the_session_and_host_the_operator_saw_are_forwarded_and_the_answer_comes_back_whole(self):
        response = await self.post(CONFIRM, BODY)
        self.assertEqual(response.status, 200)
        self.assertEqual(await self.json_of(response), self.stub.robot_confirm_answer)
        self.assertEqual(self.stub.robot_confirmations, [(ROBOT_SESSION, ROBOT_HOST_INSTANCE)])

    async def test_the_body_is_exactly_the_two_keys(self):
        instance = ROBOT_HOST_INSTANCE
        for body in ({}, {"session_uuid": ROBOT_SESSION}, {"host_instance": instance}, {**BODY, "force": True},
                     {"session_uuid": ROBOT_SESSION, "host_pid": instance["pid"]},
                     {"session_uuid": 7, "host_instance": instance}, {"session_uuid": "", "host_instance": instance},
                     {"session_uuid": "a" * 65, "host_instance": instance}, {"session_uuid": ROBOT_SESSION, "host_instance": None},
                     {"session_uuid": ROBOT_SESSION, "host_instance": {**instance, "force": True}},
                     {"session_uuid": ROBOT_SESSION, "host_instance": {"slot_instance": instance["slot_instance"], "pid": instance["pid"]}},
                     {"session_uuid": ROBOT_SESSION, "host_instance": {**instance, "slot_instance": int(instance["slot_instance"])}},
                     {"session_uuid": ROBOT_SESSION, "host_instance": {**instance, "slot_instance": "0"}},
                     {"session_uuid": ROBOT_SESSION, "host_instance": {**instance, "spawn_sequence": 0}},
                     {"session_uuid": ROBOT_SESSION, "host_instance": {**instance, "spawn_sequence": 2.0}},
                     {"session_uuid": ROBOT_SESSION, "host_instance": {**instance, "pid": True}},
                     {"session_uuid": ROBOT_SESSION, "host_instance": {**instance, "pid": 0}},
                     {"session_uuid": ROBOT_SESSION, "host_instance": {**instance, "pid": -4}},
                     {"session_uuid": ROBOT_SESSION, "host_instance": {**instance, "pid": "4242"}},
                     {"session_uuid": ROBOT_SESSION, "host_instance": {**instance, "pid": (1 << 22) + 1}}, ["x"]):
            with self.subTest(body=body):
                response = await self.post(CONFIRM, body)
                self.assertEqual(response.status, 400)
        response = await self.client.post(CONFIRM, data="not json", headers=self.headers())
        self.assertEqual((await self.json_of(response))["reason_code"], "body_not_json")
        self.assertEqual(self.stub.robot_confirmations, [], "nothing malformed reached the runtime")

    async def test_the_runtime_s_refusal_keeps_its_reason_and_is_a_conflict(self):
        for reason in ("robot_session_mismatch", "robot_host_pid_mismatch", "robot_host_spawn_sequence_mismatch",
                       "robot_host_slot_instance_mismatch", "robot_host_not_running", "robot_host_unreadable",
                       "robot_resident_not_the_robot", "robot_build_identity_unreadable", "robot_unreachable", "robot_not_configured"):
            with self.subTest(reason=reason):
                self.stub.robot_confirm_answer = {"ok": False, "reason_code": reason}
                response = await self.post(CONFIRM, BODY)
                self.assertEqual(response.status, 409)
                self.assertEqual((await self.json_of(response))["reason_code"], reason)

    async def test_each_missing_piece_of_the_guard_is_refused_by_its_own_name(self):
        cases = {
            HOST_NOT_ALLOWED: {"host": "kiwi.example:80"},
            ORIGIN_NOT_ALLOWED: {"origin": "http://evil.example"},
            CONTENT_TYPE_NOT_JSON: {"content_type": "text/plain"},
            CONSOLE_HEADER_MISSING: {"header": None},
            CREDENTIAL_MISSING: {"credential": None},
            CREDENTIAL_INVALID: {"credential": "not-the-credential"},
        }
        for reason, headers in cases.items():
            with self.subTest(reason=reason):
                response = await self.post(CONFIRM, BODY, **headers)
                self.assertEqual(response.status, 403)
                self.assertEqual((await self.json_of(response))["reason_code"], reason)
        self.assertEqual(self.stub.robot_confirmations, [])

    async def test_the_guard_is_the_one_a_command_passes_and_not_a_copy(self):
        import inspect

        from console_api import app as app_module

        source = inspect.getsource(app_module.handle_post_robot_confirm_session)
        self.assertIn("_command_body(request)", source)
        self.assertIn("_command_block(request)", source)
        self.assertIn("_verify_runtime_session(request)", source)
        self.assertIn("check_command", inspect.getsource(app_module._command_body))


class AReplacedRuntimeIsNotConfirmedBlindly(ApiCase):
    """The same handshake a command gets: the credential is still the accepted one, the runtime behind it is not the
    one this API bound to, and the confirmation is refused rather than sent to a runtime nobody looked at."""

    async def asyncSetUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="kc-robot-confirm-"))
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.store = CredentialStore(self.directory)
        self.session = "rt-20260101T000000Z-0badc0de"
        self.credential = self.store.credential_for(self.session)
        await super().asyncSetUp()

    async def start_api(self, **kwargs):
        self.app = make_app(self.stub, credential=self.credential, credential_store=self.store, runtime_session=self.session, **kwargs)
        self.server = TestServer(self.app, host="127.0.0.1")
        self.client = TestClient(self.server)
        await self.client.start_server()
        self.port = self.server.port
        self.addAsyncCleanup(self.client.close)

    async def test_it_is_refused_when_the_runtime_changed_under_a_still_valid_credential(self):
        self.stub.runtime_session = "rt-20260101T000200Z-0badc0e0"
        response = await self.post(CONFIRM, BODY, credential=self.credential)
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["reason_code"], "runtime_session_changed")
        self.assertEqual(self.stub.robot_confirmations, [])


class AReplayRuntimeConfirmsNothing(ApiCase):
    def make_stub(self) -> StubRuntime:
        return StubRuntime(data_mode="replay")

    async def test_it_is_refused_before_the_runtime(self):
        await self.client.get("/api/snapshot")  # this is what tells the API the runtime is in replay
        response = await self.post(CONFIRM, BODY)
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["reason_code"], "replay_only")
        self.assertEqual(self.stub.robot_confirmations, [])


class TheCapabilityCheckRead(ApiCase):
    async def test_it_is_forwarded_whole_and_submits_nothing(self):
        for _ in range(3):
            response = await self.client.get(CHECK)
            self.assertEqual(response.status, 200)
            self.assertEqual(await self.json_of(response), self.stub.capability_check_state_answer)
        self.assertEqual((self.stub.capability_check_applies, self.stub.submits), ([], []))

    async def test_a_runtime_without_a_check_says_why_as_a_conflict(self):
        self.stub.capability_check_state_answer = {"ok": False, "reason_code": "capability_check_unavailable",
                                                   "detail": "no_activation_candidate_prepared"}
        response = await self.client.get(CHECK)
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["detail"], "no_activation_candidate_prepared")

    async def test_a_runtime_that_is_gone_is_a_service_error(self):
        self.stub.unavailable = True
        self.assertEqual((await self.client.get(CHECK)).status, 503)


class TheCapabilityCheckApply(ApiCase):
    async def test_the_key_digest_and_session_are_forwarded_and_the_answer_comes_back_whole(self):
        response = await self.post(CHECK, CHECK_BODY)
        self.assertEqual(response.status, 200)
        self.assertEqual(await self.json_of(response), self.stub.capability_check_apply_answer)
        self.assertEqual(self.stub.capability_check_applies, [(CHECK_BODY["candidate_key"], CHECK_BODY["payload_digest"], ROBOT_SESSION)])

    async def test_the_body_is_exactly_the_three_keys_in_their_shapes(self):
        for body in ({}, {**CHECK_BODY, "payload": "AAAA"}, {**CHECK_BODY, "path": "/tmp/x"},
                     {key: value for key, value in CHECK_BODY.items() if key != "session_uuid"},
                     {**CHECK_BODY, "candidate_key": ""}, {**CHECK_BODY, "candidate_key": "k" * 65}, {**CHECK_BODY, "candidate_key": 3},
                     {**CHECK_BODY, "payload_digest": "ab" * 32}, {**CHECK_BODY, "payload_digest": "sha256:" + "AB" * 32},
                     {**CHECK_BODY, "session_uuid": "not a session"}, {**CHECK_BODY, "session_uuid": "a" * 65},
                     {**CHECK_BODY, "session_uuid": None}, ["x"]):
            with self.subTest(body=body):
                self.assertEqual((await self.post(CHECK, body)).status, 400)
        self.assertEqual(self.stub.capability_check_applies, [], "nothing malformed reached the runtime")

    async def test_the_runtime_s_refusals_keep_their_reason_and_those_about_the_world_are_conflicts(self):
        for reason, status in (("capability_check_unavailable", 409), ("capability_check_in_flight", 409), ("mission_running", 409),
                               ("owner_unresolved", 409), ("run_active", 409), ("payload_digest_mismatch", 409),
                               ("robot_host_not_held", 409), ("robot_session_mismatch", 409), ("capability_slot_session_mismatch", 409),
                               ("robot_host_unreadable", 409), ("robot_resident_not_the_robot", 409), ("robot_unreachable", 409),
                               ("unknown_candidate", 400), ("bad_request", 400)):
            with self.subTest(reason=reason):
                self.stub.capability_check_apply_answer = {"ok": False, "reason_code": reason}
                response = await self.post(CHECK, CHECK_BODY)
                self.assertEqual(response.status, status)
                self.assertEqual((await self.json_of(response))["reason_code"], reason)

    async def test_each_missing_piece_of_the_guard_is_refused_by_its_own_name_and_nothing_is_sent(self):
        cases = {
            HOST_NOT_ALLOWED: {"host": "kiwi.example:80"},
            ORIGIN_NOT_ALLOWED: {"origin": "http://evil.example"},
            CONTENT_TYPE_NOT_JSON: {"content_type": "text/plain"},
            CONSOLE_HEADER_MISSING: {"header": None},
            CREDENTIAL_MISSING: {"credential": None},
            CREDENTIAL_INVALID: {"credential": "not-the-credential"},
        }
        for reason, headers in cases.items():
            with self.subTest(reason=reason):
                response = await self.post(CHECK, CHECK_BODY, **headers)
                self.assertEqual(response.status, 403)
                self.assertEqual((await self.json_of(response))["reason_code"], reason)
        self.assertEqual(self.stub.capability_check_applies, [])

    async def test_the_guard_is_the_one_a_command_passes_and_not_a_copy(self):
        import inspect

        from console_api import app as app_module

        source = inspect.getsource(app_module.handle_post_capability_check)
        self.assertIn("_command_body(request)", source)
        self.assertIn("_command_block(request)", source)
        self.assertIn("_verify_runtime_session(request)", source)

    async def test_it_refuses_the_methods_it_does_not_have(self):
        for method in (self.client.put, self.client.delete):
            self.assertIn((await method(CHECK)).status, (404, 405))


class AReplacedRuntimeIsNotCheckedBlindly(AReplacedRuntimeIsNotConfirmedBlindly):
    async def test_it_is_refused_when_the_runtime_changed_under_a_still_valid_credential(self):
        self.stub.runtime_session = "rt-20260101T000200Z-0badc0e0"
        response = await self.post(CHECK, CHECK_BODY, credential=self.credential)
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["reason_code"], "runtime_session_changed")
        self.assertEqual(self.stub.capability_check_applies, [])


class AReplayRuntimeChecksNothing(AReplayRuntimeConfirmsNothing):
    async def test_it_is_refused_before_the_runtime(self):
        await self.client.get("/api/snapshot")
        response = await self.post(CHECK, CHECK_BODY)
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["reason_code"], "replay_only")
        self.assertEqual(self.stub.capability_check_applies, [])


if __name__ == "__main__":
    unittest.main()
