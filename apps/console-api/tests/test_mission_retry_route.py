#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# apps/console-api/tests/test_mission_retry_route.py: POST /api/mission/retry over a stub runtime. It passes the guard
# every mission POST passes, takes a body of at most {mission_id} and forwards that id (or none) to the runtime's
# mission_retry; a refusal keeps the runtime's reason_code, and the refusals about the state of the mission are
# conflicts (409), not malformed requests.
# Run directly: python3 apps/console-api/tests/test_mission_retry_route.py
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import ApiCase  # noqa: E402

from console_api.security import CONSOLE_HEADER_MISSING, CREDENTIAL_MISSING  # noqa: E402

MISSION_ID = "mission-0123456789ab"
PATH = "/api/mission/retry"


class TheRouteForwardsTheHoldTheOperatorSaw(ApiCase):
    async def test_the_mission_id_is_forwarded_and_the_answer_comes_back_whole(self):
        response = await self.post(PATH, {"mission_id": MISSION_ID})
        answer = await self.json_of(response)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.stub.mission_retries, [MISSION_ID])
        self.assertEqual(answer, self.stub.mission_retry_answer)

    async def test_an_empty_body_names_no_mission(self):
        response = await self.post(PATH, {})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.stub.mission_retries, [None])

    async def test_a_body_with_anything_else_is_refused_whole(self):
        for body in ({"mission_id": MISSION_ID, "target_id": "cube"}, {"attempt": 2}, {"mission_id": ""}, {"mission_id": 7},
                     {"mission_id": None}, {"mission_id": [MISSION_ID]}):
            with self.subTest(body=body):
                response = await self.post(PATH, body)
                self.assertEqual(response.status, 400)
                self.assertEqual((await self.json_of(response))["reason_code"], "bad_request")
        self.assertEqual(self.stub.mission_retries, [], "nothing reached the runtime")

    async def test_it_passes_the_guard_every_mission_post_passes(self):
        for reason, headers in ((CONSOLE_HEADER_MISSING, {"header": None}), (CREDENTIAL_MISSING, {"credential": None})):
            with self.subTest(reason=reason):
                response = await self.post(PATH, {"mission_id": MISSION_ID}, **headers)
                self.assertEqual(response.status, 403)
                self.assertEqual((await self.json_of(response))["reason_code"], reason)
        self.assertEqual(self.stub.mission_retries, [])

    async def test_the_guard_is_the_shared_helper(self):
        import inspect

        from console_api import app as app_module

        self.assertIn("_mission_post(request)", inspect.getsource(app_module.handle_post_mission_retry))

    async def test_the_runtime_s_refusals_about_the_hold_are_conflicts(self):
        for code in ("mission_not_held", "mission_not_carried", "mission_retry_not_offered", "mission_retry_already_requested",
                     "mission_park_unavailable", "mission_retry_not_enabled", "mission_retry_not_attended", "mission_stopping",
                     "mission_unavailable"):
            with self.subTest(code=code):
                self.stub.mission_retry_answer = {"ok": False, "reason_code": code, "mission_id": MISSION_ID}
                response = await self.post(PATH, {"mission_id": MISSION_ID})
                self.assertEqual(response.status, 409)
                self.assertEqual((await self.json_of(response))["reason_code"], code)

    async def test_the_runtime_s_admission_refusals_of_a_press_are_conflicts(self):
        for code in ("owner_unresolved", "owner_external", "run_active", "hardware_commands_not_enabled"):
            with self.subTest(code=code):
                self.stub.mission_retry_answer = {"ok": False, "reason_code": code}
                response = await self.post(PATH, {"mission_id": MISSION_ID})
                self.assertEqual(response.status, 409)
                self.assertEqual((await self.json_of(response))["reason_code"], code)

    async def test_an_unknown_mission_is_not_found(self):
        self.stub.mission_retry_answer = {"ok": False, "reason_code": "unknown_mission"}
        response = await self.post(PATH, {"mission_id": MISSION_ID})
        self.assertEqual(response.status, 404)


if __name__ == "__main__":
    unittest.main()
