#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# apps/console-api/tests/test_signature.py: the signature policy check routes over a stub runtime. GET
# /api/capability/signature forwards the runtime's capability_signature_state whole and is a READ. POST
# /api/capability/signature runs the release's own verifier on the robot, with no motion and no slot touched; it passes
# exactly the guard POST /api/commands passes, through the same function, the same replay block and the same
# runtime-session check as the slot check, and takes a body of exactly nothing beyond the guard's needs: there is no
# field for a capsule, a key, a path or a policy to arrive in. The runtime's refusal comes back with its own reason code.
# Run directly: python3 apps/console-api/tests/test_signature.py
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import ApiCase  # noqa: E402
from test_robot import AReplacedRuntimeIsNotConfirmedBlindly, AReplayRuntimeConfirmsNothing  # noqa: E402

from console_api.security import (CONSOLE_HEADER_MISSING, CONTENT_TYPE_NOT_JSON, CREDENTIAL_INVALID,  # noqa: E402
                                  CREDENTIAL_MISSING, HOST_NOT_ALLOWED, ORIGIN_NOT_ALLOWED)

SIGNATURE = "/api/capability/signature"


class TheRead(ApiCase):
    async def test_it_is_forwarded_whole_and_runs_nothing(self):
        for _ in range(3):
            response = await self.client.get(SIGNATURE)
            self.assertEqual(response.status, 200)
            self.assertEqual(await self.json_of(response), self.stub.signature_state_answer)
        self.assertEqual((self.stub.signature_runs, self.stub.capability_check_applies, self.stub.submits), (0, [], []))

    async def test_a_runtime_without_a_check_says_why_as_a_conflict(self):
        self.stub.signature_state_answer = {"ok": False, "reason_code": "signature_check_unavailable", "detail": "robot_link_not_local"}
        response = await self.client.get(SIGNATURE)
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["detail"], "robot_link_not_local")

    async def test_a_runtime_that_is_gone_is_a_service_error(self):
        self.stub.unavailable = True
        self.assertEqual((await self.client.get(SIGNATURE)).status, 503)


class TheRun(ApiCase):
    async def test_an_empty_body_runs_it_once_and_the_answer_comes_back_whole(self):
        response = await self.post(SIGNATURE, {})
        self.assertEqual(response.status, 200)
        self.assertEqual(await self.json_of(response), self.stub.signature_check_answer)
        self.assertEqual(self.stub.signature_runs, 1)
        self.assertEqual((self.stub.capability_check_applies, self.stub.submits), ([], []), "nothing else was sent")

    async def test_a_body_with_any_key_is_refused_and_nothing_runs(self):
        for body in ({"capsule": "x"}, {"path": "/tmp/x"}, {"policy": "allow"}, {"trusted": "ab" * 32}, {"candidate_key": "k"}, ["x"], "x", 3):
            with self.subTest(body=body):
                self.assertEqual((await self.post(SIGNATURE, body)).status, 400)
        self.assertEqual(self.stub.signature_runs, 0)

    async def test_the_runtime_s_refusals_keep_their_reason_and_those_about_the_world_are_conflicts(self):
        for reason, status in (("signature_check_unavailable", 409), ("signature_check_in_flight", 409), ("mission_running", 409),
                               ("owner_unresolved", 409), ("run_active", 409), ("capability_check_in_flight", 409),
                               ("verifier_binary_unavailable", 409), ("release_manifest_unreadable", 409), ("resident_conf_unreadable", 409),
                               ("resident_policy_not_require", 409), ("resident_trusted_keys_unreadable", 409), ("manifest_names_no_capsule", 409),
                               ("scratch_directory_unavailable", 409), ("bad_request", 400)):
            with self.subTest(reason=reason):
                self.stub.signature_check_answer = {"ok": False, "reason_code": reason}
                response = await self.post(SIGNATURE, {})
                self.assertEqual(response.status, status)
                self.assertEqual((await self.json_of(response))["reason_code"], reason)

    async def test_each_missing_piece_of_the_guard_is_refused_by_its_own_name_and_nothing_runs(self):
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
                response = await self.post(SIGNATURE, {}, **headers)
                self.assertEqual(response.status, 403)
                self.assertEqual((await self.json_of(response))["reason_code"], reason)
        self.assertEqual(self.stub.signature_runs, 0)

    async def test_the_guard_is_the_one_a_command_passes_and_not_a_copy(self):
        import inspect

        from console_api import app as app_module

        source = inspect.getsource(app_module.handle_post_capability_signature)
        self.assertIn("_command_body(request)", source)
        self.assertIn("_command_block(request)", source)
        self.assertIn("_verify_runtime_session(request)", source)

    async def test_it_refuses_the_methods_it_does_not_have(self):
        for method in (self.client.put, self.client.delete):
            self.assertIn((await method(SIGNATURE)).status, (404, 405))

    async def test_no_answer_carries_a_private_value(self):
        from support import find_leaks

        response = await self.post(SIGNATURE, {})
        self.assertEqual(find_leaks(await response.text()), [])


class AReplacedRuntimeIsNotRunBlindly(AReplacedRuntimeIsNotConfirmedBlindly):
    async def test_it_is_refused_when_the_runtime_changed_under_a_still_valid_credential(self):
        self.stub.runtime_session = "rt-20260101T000200Z-0badc0e0"
        response = await self.post(SIGNATURE, {}, credential=self.credential)
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["reason_code"], "runtime_session_changed")
        self.assertEqual(self.stub.signature_runs, 0)


class AReplayRuntimeRunsNothing(AReplayRuntimeConfirmsNothing):
    async def test_it_is_refused_before_the_runtime(self):
        await self.client.get("/api/snapshot")
        response = await self.post(SIGNATURE, {})
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["reason_code"], "replay_only")
        self.assertEqual(self.stub.signature_runs, 0)


if __name__ == "__main__":
    unittest.main()
