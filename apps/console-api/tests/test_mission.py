#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# apps/console-api/tests/test_mission.py: the mission routes over a stub runtime. What is checked here is what is
# checked of every other route: this process FORWARDS and decides nothing, a refusal keeps the runtime's own
# reason_code, and the three POSTs pass exactly the guard POST /api/commands passes. Plus the one rule that belongs
# to this surface alone: the browser supplies a candidate KEY and there is no field for anything else.
# Run directly: python3 apps/console-api/tests/test_mission.py
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import ApiCase, StubRuntime, command_object, contract_errors, mission_view_object  # noqa: E402

from console_api.security import (CONSOLE_HEADER_MISSING, CONTENT_TYPE_NOT_JSON, CREDENTIAL_INVALID,  # noqa: E402
                                  CREDENTIAL_MISSING, HOST_NOT_ALLOWED, ORIGIN_NOT_ALLOWED)

CANDIDATE_KEY = "mobile_approach.activate"
MISSION_POSTS = ("/api/mission/start", "/api/mission/stop", "/api/mission/activate")


def activate_body() -> dict:
    return {"candidate_key": CANDIDATE_KEY}


def body_for(path: str) -> dict:
    return activate_body() if path.endswith("/activate") else {}


class TheTwoReads(ApiCase):
    async def test_the_mission_view_is_forwarded_whole(self):
        answer = await self.json_of(await self.client.get("/api/mission"))
        self.assertEqual(answer, mission_view_object())
        self.assertEqual(answer["waiting_reason"], "capability_unavailable")
        self.assertIn("mission_record_version", answer)

    async def test_the_mission_view_carries_no_contract_version(self):
        """It is its own surface. A reader must not be able to mistake it for a contract object, and a contract bump
        must not look like a mission change."""
        answer = await self.json_of(await self.client.get("/api/mission"))
        self.assertNotIn("contract_version", answer)
        self.assertNotIn("schema_version", answer)

    async def test_a_runtime_that_has_no_mission_surface_keeps_its_own_reason_code(self):
        self.stub.mission_state_answer = {"ok": False, "reason_code": "mission_unavailable", "detail": "cake_not_configured"}
        response = await self.client.get("/api/mission")
        answer = await self.json_of(response)
        self.assertEqual(response.status, 409)
        self.assertEqual(answer["reason_code"], "mission_unavailable")
        self.assertEqual(answer["detail"], "cake_not_configured")

    async def test_a_runtime_that_recorded_no_mission_is_a_404(self):
        self.stub.mission_state_answer = {"ok": False, "reason_code": "no_mission_recorded"}
        response = await self.client.get("/api/mission")
        self.assertEqual(response.status, 404)

    async def test_the_candidates_are_a_key_and_a_digest_and_nothing_else(self):
        answer = await self.json_of(await self.client.get("/api/mission/candidates"))
        self.assertTrue(answer["ok"])
        self.assertEqual([sorted(entry) for entry in answer["candidates"]], [["key", "payload_digest"]])
        self.assertEqual(answer["candidates"][0]["key"], CANDIDATE_KEY)

    async def test_neither_read_reaches_a_mutating_op(self):
        await self.client.get("/api/mission")
        await self.client.get("/api/mission/candidates")
        self.assertEqual(self.stub.mission_starts, [])
        self.assertEqual(self.stub.mission_activations, [])
        self.assertEqual(self.stub.mission_stops, 0)

    async def test_a_runtime_that_is_gone_is_reported_and_not_guessed(self):
        self.stub.unavailable = True
        for path in ("/api/mission", "/api/mission/candidates"):
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertEqual(response.status, 503)
                self.assertEqual((await self.json_of(response))["reason_code"], "runtime_unavailable")


class TheRoutesAreReachableAtAll(ApiCase):
    async def test_the_mission_routes_are_registered_above_the_catch_all(self):
        """The asset route is `/{path:.*}` and it matches everything. A mission route registered after it would be
        answered with the built page instead, which is a 404 here because no web build is attached."""
        self.assertNotEqual((await self.client.get("/api/mission")).status, 404)
        self.assertNotEqual((await self.client.get("/api/mission/candidates")).status, 404)
        for path in MISSION_POSTS:
            with self.subTest(path=path):
                response = await self.post(path, body_for(path))
                self.assertEqual(response.status, 200, path)


class TheGuardEveryMissionPostPasses(ApiCase):
    """The SAME guard POST /api/commands passes, through the same function. Each case removes one of the five things
    a cross-origin page cannot produce and expects the guard's own reason code."""

    async def test_each_missing_piece_is_refused_by_its_own_name(self):
        cases = {
            HOST_NOT_ALLOWED: {"host": "kiwi.example:80"},
            ORIGIN_NOT_ALLOWED: {"origin": "http://evil.example"},
            CONTENT_TYPE_NOT_JSON: {"content_type": "text/plain"},
            CONSOLE_HEADER_MISSING: {"header": None},
            CREDENTIAL_MISSING: {"credential": None},
            CREDENTIAL_INVALID: {"credential": "not-the-credential"},
        }
        for path in MISSION_POSTS:
            for reason, headers in cases.items():
                with self.subTest(path=path, reason=reason):
                    response = await self.post(path, body_for(path), **headers)
                    self.assertEqual(response.status, 403, f"{path} {reason}")
                    self.assertEqual((await self.json_of(response))["reason_code"], reason)
        self.assertEqual(self.stub.mission_starts, [])
        self.assertEqual(self.stub.mission_activations, [])
        self.assertEqual(self.stub.mission_stops, 0)

    async def test_the_guard_is_the_one_a_command_passes_and_not_a_copy(self):
        """Pinned in the source: these routes call the same helper POST /api/commands calls, so the two cannot
        drift apart while both keep passing their own tests."""
        import inspect

        from console_api import app as app_module

        self.assertIn("_command_body(request)", inspect.getsource(app_module._mission_post))
        for handler in (app_module.handle_post_mission_start, app_module.handle_post_mission_stop,
                        app_module.handle_post_mission_activate):
            self.assertIn("_mission_post(request)", inspect.getsource(handler))
        self.assertIn("check_command", inspect.getsource(app_module._command_body))

    async def test_a_body_that_is_not_json_is_refused_before_the_runtime_is_touched(self):
        for path in MISSION_POSTS:
            with self.subTest(path=path):
                response = await self.client.post(path, data="not json", headers=self.headers())
                self.assertEqual(response.status, 400)
                self.assertEqual((await self.json_of(response))["reason_code"], "body_not_json")
        self.assertEqual(self.stub.mission_activations, [])


class TheBrowserSuppliesACandidateKeyAndNothingElse(ApiCase):
    async def test_the_key_is_forwarded_and_the_answer_comes_back_whole(self):
        answer = await self.json_of(await self.post("/api/mission/activate", activate_body()))
        self.assertEqual(self.stub.mission_activations, [CANDIDATE_KEY])
        self.assertEqual(answer["activation_state"], "OBSERVED")
        self.assertEqual(answer["transaction_phase"], "COMPLETED")
        self.assertEqual(answer["operation_uuid"], "0f" * 16)
        self.assertFalse(answer["stopped_during_activation"])

    async def test_a_body_carrying_anything_besides_the_key_is_refused_whole(self):
        bodies = [
            {"candidate_key": CANDIDATE_KEY, "payload": "AAAA"},
            {"candidate_key": CANDIDATE_KEY, "candidate_path": "/opt/cake/plans/swapped.cakepln"},
            {"candidate_key": CANDIDATE_KEY, "socket_path": "/run/kc/admin.sock"},
            {"candidate_key": CANDIDATE_KEY, "operation": "apply-plan"},
            {"candidate_key": CANDIDATE_KEY, "operation_uuid": "0f" * 16},
            {"payload": "AAAA"},
            {},
            {"candidate_key": ""},
            {"candidate_key": 7},
            {"candidate_key": ["mobile_approach.activate"]},
        ]
        for body in bodies:
            with self.subTest(body=body):
                response = await self.post("/api/mission/activate", body)
                self.assertEqual(response.status, 400)
                self.assertEqual((await self.json_of(response))["reason_code"], "bad_request")
        self.assertEqual(self.stub.mission_activations, [], "nothing reached the runtime at all")

    async def test_a_key_the_runtime_did_not_prepare_keeps_the_runtime_s_own_refusal(self):
        self.stub.mission_activate_answer = {"ok": False, "reason_code": "unknown_candidate", "detail": "candidate_unreadable"}
        response = await self.post("/api/mission/activate", {"candidate_key": "something.else"})
        answer = await self.json_of(response)
        self.assertEqual(response.status, 400)
        self.assertEqual(answer["reason_code"], "unknown_candidate")
        self.assertEqual(self.stub.mission_activations, ["something.else"], "the runtime is what refuses a key")

    async def test_a_mission_that_is_not_waiting_is_a_conflict_and_not_a_bad_request(self):
        self.stub.mission_activate_answer = {"ok": False, "reason_code": "mission_not_waiting_for_capability"}
        response = await self.post("/api/mission/activate", activate_body())
        self.assertEqual(response.status, 409)

    async def test_an_answer_that_says_the_mission_was_stopped_is_forwarded_as_it_is(self):
        self.stub.mission_activate_answer = {**self.stub.mission_activate_answer, "stopped_during_activation": True}
        answer = await self.json_of(await self.post("/api/mission/activate", activate_body()))
        self.assertTrue(answer["stopped_during_activation"])
        self.assertEqual(answer["activation_state"], "OBSERVED", "the answer is still recorded and still reported")


class StartingAndStopping(ApiCase):
    async def test_a_start_with_no_body_takes_the_runtime_s_own_targets(self):
        answer = await self.json_of(await self.post("/api/mission/start", {}))
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["mission_id"], "mission-0123456789ab")
        self.assertEqual(self.stub.mission_starts, [None])

    async def test_targets_are_forwarded_for_the_runtime_to_validate(self):
        targets = [{"target_id": "cube_near", "reachable": True}]
        await self.post("/api/mission/start", {"targets": targets})
        self.assertEqual(self.stub.mission_starts, [targets])

    async def test_targets_that_are_not_a_list_never_reach_the_runtime(self):
        for targets in ("cube_near", {"target_id": "cube_near"}, 7):
            with self.subTest(targets=targets):
                response = await self.post("/api/mission/start", {"targets": targets})
                self.assertEqual(response.status, 400)
        self.assertEqual(self.stub.mission_starts, [])

    async def test_a_second_mission_keeps_the_runtime_s_own_conflict(self):
        self.stub.mission_start_answer = {"ok": False, "reason_code": "mission_already_running", "mission_id": "mission-0123456789ab"}
        response = await self.post("/api/mission/start", {})
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["reason_code"], "mission_already_running")

    async def test_the_stop_is_forwarded_and_says_it_was_taken(self):
        answer = await self.json_of(await self.post("/api/mission/stop", {}))
        self.assertTrue(answer["ok"])
        self.assertTrue(answer["stopping"])
        self.assertEqual(self.stub.mission_stops, 1)

    async def test_the_stop_goes_out_on_the_dedicated_stop_worker(self):
        """A stop that waits behind a snapshot poll, or behind an activation that is holding a default executor
        thread for as long as the resident takes, is a stop that did not happen when it was pressed."""
        import inspect

        from console_api.bridge import Bridge

        self.assertIn("_call_urgently", inspect.getsource(Bridge.mission_stop))

    async def test_a_runtime_that_is_gone_is_reported_on_every_post(self):
        self.stub.unavailable = True
        for path in MISSION_POSTS:
            with self.subTest(path=path):
                response = await self.post(path, body_for(path))
                self.assertEqual(response.status, 503)
                self.assertEqual((await self.json_of(response))["reason_code"], "runtime_unavailable")


class TheStopAndTheMissionTogether(ApiCase):
    """The runtime's one stop ends the mission as well as the run. What this process adds is that the mission's part
    reaches the browser beside the run's, and that the command_result it hands on stays exactly the contract object."""

    MISSION_PART = {"ok": True, "mission_id": "mission-0123456789ab", "stopping": True, "reason_code": None,
                    "state": "WAITING_FOR_CAPABILITY", "stop_requested": True, "carried": True}

    async def test_a_submitted_stop_carries_the_mission_part_and_a_clean_command_result(self):
        self.stub.stop_command_mission = dict(self.MISSION_PART)
        self.stub.stop_command_result = {"status": "finished", "reason_code": "no_active_run", "run_id": None}
        answer = await self.json_of(await self.post("/api/stop", command_object("stop", {}, command_id="stopmission012345678")))
        self.assertEqual(answer["mission"], self.MISSION_PART)
        self.assertNotIn("mission", answer["command"])
        self.assertEqual(contract_errors("command_result", answer["command"]), [], "the receipt is exactly the contract object")
        # A STOP THAT ENDED A MISSION WAS TAKEN, run or no run. `ok` is whether the runtime took the stop, and it did:
        # the mission part says stopping. The run's own facts are kept where they are true: the receipt still says
        # no_active_run and the stop object names no run. Nothing claims that motion ceased.
        self.assertTrue(answer["ok"])
        self.assertIsNone(answer["reason_code"])
        self.assertEqual(answer["stop"], {"requested_mono_ns": None, "run_id": None})
        self.assertEqual(answer["command"]["reason_code"], "no_active_run")
        self.assertNotIn("motion_ceased", json.dumps(answer))

    async def test_a_submitted_stop_that_stopped_neither_a_run_nor_a_mission_is_still_refused(self):
        """The other half of the rule: a mission part that says stopping false (nothing was running) takes nothing, so a
        press with no run and no mission reads as refused exactly as it did before there was a mission surface."""
        self.stub.stop_command_mission = {"ok": True, "mission_id": None, "stopping": False, "reason_code": "no_running_mission",
                                          "state": None, "stop_requested": False, "carried": False}
        self.stub.stop_command_result = {"status": "finished", "reason_code": "no_active_run", "run_id": None}
        answer = await self.json_of(await self.post("/api/stop", command_object("stop", {}, command_id="stopnothing012345678")))
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "no_active_run")
        self.assertIsNone(answer["stop"])
        self.assertFalse(answer["mission"]["stopping"])

    async def test_a_submitted_stop_that_stopped_a_run_and_a_mission_names_the_run(self):
        self.stub.stop_command_mission = dict(self.MISSION_PART)
        self.stub.stop_command_result = {"status": "finished", "reason_code": None, "run_id": "sc-20260101T000000Z-abcd1234"}
        answer = await self.json_of(await self.post("/api/stop", command_object("stop", {}, command_id="stopboth012345678901")))
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["stop"], {"requested_mono_ns": None, "run_id": "sc-20260101T000000Z-abcd1234"})
        self.assertEqual(answer["mission"], self.MISSION_PART)

    async def test_a_runtime_without_a_mission_surface_answers_mission_null(self):
        answer = await self.json_of(await self.post("/api/stop", command_object("stop", {}, command_id="stopnomission0123456")))
        self.assertIn("mission", answer)
        self.assertIsNone(answer["mission"])
        self.assertTrue(answer["ok"])

    async def test_a_stop_submitted_as_a_command_is_answered_with_the_command_result_alone(self):
        self.stub.stop_command_mission = dict(self.MISSION_PART)
        response = await self.post("/api/commands", command_object("stop", {}, command_id="stopascommand0123456"))
        self.assertEqual(response.status, 200)
        answer = await self.json_of(response)
        self.assertNotIn("mission", answer)
        self.assertEqual(contract_errors("command_result", answer), [])

    async def test_the_plain_stop_op_answer_is_forwarded_with_its_mission_part(self):
        self.stub.stop_answer = {"ok": True, "stop": None, "mission": dict(self.MISSION_PART)}
        answer = await self.json_of(await self.post("/api/stop", {}))
        self.assertEqual(answer, self.stub.stop_answer)

    async def test_an_activation_for_a_mission_being_stopped_is_a_conflict(self):
        self.stub.mission_activate_answer = {"ok": False, "reason_code": "mission_stopping", "mission_id": "mission-0123456789ab"}
        response = await self.post("/api/mission/activate", activate_body())
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["reason_code"], "mission_stopping")

    async def test_an_activation_while_one_is_in_flight_is_a_conflict(self):
        """A browser whose first press timed out and presses again reaches this: nothing was submitted, and the answer
        names the operation still in flight so the console can ask about it rather than press a third time."""
        self.stub.mission_activate_answer = {"ok": False, "reason_code": "activation_in_flight",
                                             "mission_id": "mission-0123456789ab", "operation_uuid": "0f" * 16}
        response = await self.post("/api/mission/activate", activate_body())
        self.assertEqual(response.status, 409)
        answer = await self.json_of(response)
        self.assertEqual((answer["reason_code"], answer["operation_uuid"]), ("activation_in_flight", "0f" * 16))

    async def test_a_mission_stop_with_nothing_to_stop_is_a_200_that_says_stopping_false(self):
        self.stub.mission_stop_answer = {"ok": True, "mission_id": None, "stopping": False, "reason_code": "no_running_mission",
                                         "state": None, "stop_requested": False, "carried": False, "run_stop": None}
        response = await self.post("/api/mission/stop", {})
        self.assertEqual(response.status, 200)
        answer = await self.json_of(response)
        self.assertFalse(answer["stopping"])
        self.assertEqual(answer["reason_code"], "no_running_mission")

    async def test_a_mission_start_while_a_run_holds_the_slot_is_a_conflict(self):
        self.stub.mission_start_answer = {"ok": False, "reason_code": "run_active"}
        response = await self.post("/api/mission/start", {})
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["reason_code"], "run_active")


class AReplayRuntimeCommandsNothing(ApiCase):
    def make_stub(self):
        return StubRuntime(data_mode="replay")

    async def test_every_mission_post_is_refused_before_the_runtime_is_touched(self):
        await self.client.get("/api/snapshot")  # this is what tells the API the runtime is in replay
        for path in MISSION_POSTS:
            with self.subTest(path=path):
                response = await self.post(path, body_for(path))
                self.assertEqual(response.status, 409)
                self.assertEqual((await self.json_of(response))["reason_code"], "replay_only")
        self.assertEqual(self.stub.mission_starts, [])
        self.assertEqual(self.stub.mission_activations, [])
        self.assertEqual(self.stub.mission_stops, 0)

    async def test_the_reads_still_answer(self):
        await self.client.get("/api/snapshot")
        self.assertEqual((await self.client.get("/api/mission")).status, 200)


class AnIncompatibleRuntimeIsReadOnlyHereToo(ApiCase):
    def make_stub(self):
        return StubRuntime(hello_ok=False)

    async def test_no_mission_post_is_sent_at_all(self):
        await self.client.get("/api/hello?contract_version=1")
        for path in MISSION_POSTS:
            with self.subTest(path=path):
                response = await self.post(path, body_for(path))
                self.assertEqual(response.status, 409)
                self.assertEqual((await self.json_of(response))["reason_code"], "contract_incompatible")
        self.assertEqual(self.stub.mission_starts, [])
        self.assertEqual(self.stub.mission_activations, [])


class NothingPrivateLeaves(ApiCase):
    async def test_a_path_a_runtime_let_through_is_removed_by_the_last_line(self):
        """Every JSON body leaves through the outgoing sanitizer with the generic shapes on. The runtime already
        removes these; this is the second layer, and it is the one this process owns."""
        self.stub.mission_activate_answer = {"ok": False, "reason_code": "unknown_candidate",
                                             "detail": "/opt/cake/plans/mobile-demo-swapped.cakepln"}
        text = await (await self.post("/api/mission/activate", activate_body())).text()
        self.assertNotIn("/opt/cake/plans/mobile-demo-swapped.cakepln", text)
        self.stub.mission_candidates_answer = {"ok": True, "candidates": [{"key": "k", "payload_digest": "sha256:" + "ab" * 32,
                                                                           "path": "/opt/cake/plans/swapped.cakepln"}]}
        text = await (await self.client.get("/api/mission/candidates")).text()
        self.assertNotIn("/opt/cake/plans/swapped.cakepln", text)
        self.assertIn("sha256:" + "ab" * 32, text, "a digest is not a path and is what a console needs")

    async def test_the_hardening_headers_are_on_these_answers_too(self):
        response = await self.client.get("/api/mission")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["Cache-Control"], "no-store")


class TheAnswersAreJson(ApiCase):
    async def test_every_route_answers_a_json_object(self):
        for path in ("/api/mission", "/api/mission/candidates"):
            body = json.loads(await (await self.client.get(path)).text())
            self.assertIsInstance(body, dict)
        for path in MISSION_POSTS:
            body = json.loads(await (await self.post(path, body_for(path))).text())
            self.assertIsInstance(body, dict)


if __name__ == "__main__":
    unittest.main()
