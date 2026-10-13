#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The /api/vla/* routes FORWARD and decide nothing: every POST passes the command guard, every answer is the
runner's (or unreachable), and without --vla-endpoint the routes say so."""
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import ApiCase  # noqa: E402

from console_api import app as app_module  # noqa: E402
from console_api import vla as vla_module  # noqa: E402


class FakeForward:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    async def call(self, op):
        self.calls.append(op)
        return self.answers.get(op)


class VlaRoutes(ApiCase):
    async def asyncSetUp(self) -> None:
        self.stub = self.make_stub()  # each test starts its own API with its own forward

    async def start_with(self, forward):
        self.forward = forward
        await self.start_api(vla=forward)

    async def get(self, path):
        return await self.client.get(path, headers=self.headers())

    async def test_status_is_forwarded(self):
        await self.start_with(FakeForward({"status": {"state": "ready", "model_sha256": "a" * 64}}))
        response = await self.get("/api/vla/status")
        self.assertEqual(response.status, 200)
        self.assertEqual((await self.json_of(response))["state"], "ready")
        self.assertEqual(self.forward.calls, ["status"])

    async def test_unanswered_status_reads_unreachable(self):
        await self.start_with(FakeForward({}))
        answer = await self.json_of(await self.get("/api/vla/status"))
        self.assertEqual(answer["state"], "unreachable")

    async def test_a_post_without_the_console_header_and_credential_is_refused_and_not_forwarded(self):
        await self.start_with(FakeForward({"run": {"accepted": True}}))
        response = await self.post("/api/vla/run", {}, header=None, credential=None)
        self.assertEqual(response.status, 403)
        self.assertEqual(self.forward.calls, [])

    async def test_a_guarded_post_is_forwarded_and_answered_whole(self):
        await self.start_with(FakeForward({"run": {"accepted": True}}))
        response = await self.post("/api/vla/run", {})
        self.assertEqual(response.status, 200)
        self.assertEqual(await self.json_of(response), {"accepted": True})
        self.assertEqual(self.forward.calls, ["run"])

    async def test_an_unanswered_post_is_503(self):
        await self.start_with(FakeForward({}))
        response = await self.post("/api/vla/swap", {})
        self.assertEqual(response.status, 503)
        self.assertEqual((await self.json_of(response))["reason_code"], "vla_runner_unreachable")

    async def test_the_mission_pose_press_is_forwarded(self):
        await self.start_with(FakeForward({"to_mission_pose": {"accepted": True}}))
        response = await self.post("/api/vla/to_mission_pose", {})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.forward.calls, ["to_mission_pose"])

    async def test_the_carry_press_is_forwarded(self):
        await self.start_with(FakeForward({"carry": {"accepted": True}}))
        response = await self.post("/api/vla/carry", {})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.forward.calls, ["carry"])

    async def test_an_unknown_op_never_reaches_the_runner(self):
        await self.start_with(FakeForward({"shutdown": {"bye": True}}))
        response = await self.post("/api/vla/shutdown", {})
        self.assertNotEqual(response.status, 200)
        self.assertEqual(self.forward.calls, [])

    async def test_without_an_endpoint_the_routes_say_not_configured(self):
        await self.start_api()
        response = await self.get("/api/vla/status")
        self.assertEqual(response.status, 404)

    def test_every_vla_post_uses_the_shared_guard(self):
        self.assertIn("_command_body(request)", inspect.getsource(app_module.handle_post_vla))

    def test_the_forward_waits_for_the_runners_two_runtime_reads(self):
        self.assertGreaterEqual(vla_module.VlaForward.DEFAULT_TIMEOUT_S, 10.0)
        self.assertEqual(vla_module.VlaForward("tcp://127.0.0.1:5593").timeout_s, vla_module.VlaForward.DEFAULT_TIMEOUT_S)

    def test_the_forward_is_loopback_only(self):
        with self.assertRaises(ValueError):
            vla_module.VlaForward("tcp://192.0.2.5:5593")


if __name__ == "__main__":
    unittest.main()
