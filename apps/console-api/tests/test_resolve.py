#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# apps/console-api/tests/test_resolve.py: the uncertain-command path over a stub runtime.
#
# Two things are proved here and nothing else is claimed. FIRST, the snapshot reaches the browser WHOLE: the runtime's
# additive 'unresolved_command' key, with the machine evidence inside it, is forwarded untouched, and the contract part
# of the same body still validates once that key is stripped. Without the key a browser cannot say why commands are
# blocked and the operator is back at a terminal. SECOND, POST /api/commands/{id}/resolve is a FORWARD of a question,
# never a retry: the stub records every call it gets, and the test asserts that no submit was made on any path through
# this route, including the paths where the runtime refuses.
#
# Run directly: python3 apps/console-api/tests/test_resolve.py
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import ApiCase, StubRuntime, command_object, contract_errors, unresolved_object  # noqa: E402

COMMAND_ID = "abcd1234ef0123456789"
OTHER_ID = "0f1e2d3c4b5a69788796"


class TestSnapshotCarriesTheUnresolvedCommand(ApiCase):
    async def test_the_additive_key_and_its_evidence_reach_the_client_whole(self):
        self.stub.unresolved_command = unresolved_object(command_id=COMMAND_ID)
        body = await self.json_of(await self.client.get("/api/snapshot"))
        self.assertIn("unresolved_command", body, "the key the browser needs to explain the block was dropped")
        self.assertEqual(body["unresolved_command"], self.stub.unresolved_command, "the answer was not forwarded whole")
        detail = body["unresolved_command"]
        self.assertEqual(detail["block_reason_code"], "owner_unresolved")
        self.assertEqual(detail["classification"], "ran_and_was_interrupted")
        self.assertTrue(detail["findings"], "the findings the runtime accepts must reach the browser")
        self.assertIn("start_cycle", detail["blocked_commands"])
        self.assertTrue(detail["evidence"]["run_files"]["readable"])

    async def test_the_contract_part_still_validates_once_the_additive_keys_are_stripped(self):
        from demo_runtime.daemon import ADDITIVE_SNAPSHOT_KEYS

        self.stub.unresolved_command = unresolved_object()
        body = await self.json_of(await self.client.get("/api/snapshot"))
        contract_part = {key: value for key, value in body.items() if key not in ADDITIVE_SNAPSHOT_KEYS}
        self.assertEqual(contract_errors("snapshot", contract_part), [])

    async def test_a_snapshot_with_nothing_unresolved_carries_no_key_at_all(self):
        body = await self.json_of(await self.client.get("/api/snapshot"))
        self.assertNotIn("unresolved_command", body, "an absent block must not be reported as an empty one")

    async def test_the_whole_body_validates_now_that_both_keys_are_contract_properties(self):
        """What the forwarded snapshot is, now that the workaround is gone.

        'unresolved_command' and 'lease' were carried BESIDE the contract object while the schema refused unknown
        keys, so the only thing a test could check was the body with those keys removed. They are optional properties
        of snapshot.schema.json now, so the thing the browser actually receives is checked, whole and unstripped. A
        client that validates no longer has to know which keys to take off first, which is the point of the change.
        """
        self.stub.unresolved_command = unresolved_object(command_id=COMMAND_ID)
        self.stub.lease = {"held": False, "generation": None, "host_session": None, "expires_in_ms": None,
                           "reason": "lease_not_required_in_this_mode", "required": False, "attached": False,
                           "renew_failures": 0, "run_id": None, "adopted": None, "last_reply_age_ms": None}
        body = await self.json_of(await self.client.get("/api/snapshot"))
        self.assertIn("unresolved_command", body)
        self.assertIn("lease", body)
        self.assertEqual(contract_errors("snapshot", body), [], "the body the browser receives is not a valid snapshot")

    async def test_a_command_result_is_still_readable_by_id_after_this_process_restarts(self):
        """The read that makes a lost response recoverable. It answers from the RUNTIME's journal and holds nothing of
        its own, so a console that lost an answer can ask again across a restart of this process."""
        self.stub.results[COMMAND_ID] = {"command_id": COMMAND_ID, "status": "finished", "reason_code": None,
                                         "run_id": "sc-20260101T000000Z-abcd1234"}
        first = await self.json_of(await self.client.get(f"/api/commands/{COMMAND_ID}"))
        self.assertEqual(first["status"], "finished")
        self.assertEqual(contract_errors("command_result", first), [])
        await self.client.close()
        await self.start_api()  # a new API process over the SAME runtime: nothing of the answer lived here
        again = await self.json_of(await self.client.get(f"/api/commands/{COMMAND_ID}"))
        self.assertEqual(again, first, "the same question got a different answer after a restart of this process")


class TestResolveIsAForwardAndNeverARetry(ApiCase):
    async def test_the_finding_and_the_note_reach_the_runtime_and_the_answer_comes_back_whole(self):
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve",
                                   {"finding": "controller_stopped_by_operator", "note": "the arm was at rest and the gripper was closed"})
        self.assertEqual(response.status, 200)
        answer = await self.json_of(response)
        self.assertEqual(self.stub.resolves, [{"command_id": COMMAND_ID, "finding": "controller_stopped_by_operator",
                                               "note": "the arm was at rest and the gripper was closed"}])
        self.assertTrue(answer["cleared"])
        self.assertEqual(answer["cleared_by"], "operator_attestation")
        self.assertEqual(answer["classification"], "ran_and_was_interrupted")
        self.assertEqual(answer["attestation"]["finding"], "controller_stopped_by_operator")
        self.assertIn("process", answer["evidence"], "the machine evidence must reach the browser with the answer")
        self.assertEqual(self.stub.submits, [], "a resolve must never submit anything")

    async def test_a_note_is_optional(self):
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", {"finding": "no_controller_running"})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.stub.resolves[0]["note"], None)

    async def test_the_runtime_s_refusal_keeps_its_reason_and_becomes_a_conflict(self):
        self.stub.resolve_answer = {"ok": False, "reason_code": "controller_alive", "command_id": COMMAND_ID, "classification": None,
                                    "contradiction": {"declared": "no_controller_running", "found": "a process with this command's identity is alive now",
                                                      "found_by": "recorded_pid"},
                                    "evidence": unresolved_object()["evidence"], "cleared": False}
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", {"finding": "no_controller_running"})
        self.assertEqual(response.status, 409)
        answer = await self.json_of(response)
        self.assertEqual(answer["reason_code"], "controller_alive")
        self.assertFalse(answer["cleared"])
        self.assertIn("contradiction", answer, "the contradiction is what the operator has to be shown")
        self.assertEqual(self.stub.submits, [])

    async def test_a_command_that_is_not_uncertain_is_a_conflict_and_not_a_bad_request(self):
        self.stub.resolve_answer = {"ok": False, "reason_code": "command_not_uncertain"}
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", {"finding": "no_controller_running"})
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["reason_code"], "command_not_uncertain")

    async def test_an_inconclusive_resolve_is_an_answer_and_not_an_error(self):
        # The evidence could not be read: the runtime records the statement and leaves the block on. That is a 200 with
        # cleared false, because the request was taken; reporting it as a failure would invite a second try.
        self.stub.resolve_answer = {"ok": True, "command_id": COMMAND_ID, "owner": "unresolved", "cleared": False, "cleared_by": None,
                                    "reason_code": "evidence_unreadable", "classification": "still_unknown",
                                    "evidence": unresolved_object(classification="still_unknown")["evidence"],
                                    "attestation": {"finding": "no_controller_running", "note": None, "classification": "still_unknown",
                                                    "clears_block": False, "refused": None, "t_wall_utc": "2026-01-01T00:01:00.000Z"}}
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", {"finding": "no_controller_running"})
        self.assertEqual(response.status, 200)
        answer = await self.json_of(response)
        self.assertFalse(answer["cleared"])
        self.assertIsNone(answer["cleared_by"])
        self.assertEqual(answer["classification"], "still_unknown")

    async def test_a_finding_that_is_not_a_string_never_reaches_the_runtime(self):
        for body in ({"finding": None}, {"finding": 3}, {"finding": ""}, {}, {"finding": {"kind": "guess"}}):
            response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", body)
            self.assertEqual(response.status, 400, body)
            self.assertEqual((await self.json_of(response))["reason_code"], "finding_not_allowed", body)
        self.assertEqual(self.stub.resolves, [])

    async def test_a_note_that_is_not_text_never_reaches_the_runtime(self):
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", {"finding": "no_controller_running", "note": {"a": 1}})
        self.assertEqual(response.status, 400)
        self.assertEqual((await self.json_of(response))["reason_code"], "note_not_allowed")
        self.assertEqual(self.stub.resolves, [])

    async def test_a_command_id_that_is_not_an_id_never_reaches_the_runtime(self):
        response = await self.post("/api/commands/not an id/resolve", {"finding": "no_controller_running"})
        self.assertEqual(response.status, 400)
        self.assertEqual((await self.json_of(response))["reason_code"], "bad_request")
        self.assertEqual(self.stub.resolves, [])

    async def test_the_id_in_the_path_is_the_one_that_is_used_and_a_body_cannot_name_another(self):
        await self.post(f"/api/commands/{COMMAND_ID}/resolve", {"finding": "no_controller_running", "command_id": OTHER_ID})
        self.assertEqual(self.stub.resolves[0]["command_id"], COMMAND_ID)

    async def test_an_unreachable_runtime_is_a_service_error_and_not_a_cleared_block(self):
        self.stub.unavailable = True
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", {"finding": "no_controller_running"})
        self.assertEqual(response.status, 503)
        self.assertEqual((await self.json_of(response))["reason_code"], "runtime_unavailable")


class TestResolvePassesTheSameGuard(ApiCase):
    """Every check POST /api/commands passes, in the same order, with the same reason codes. A route that could clear a
    block is a mutating route, and a page on another origin must reach it no more easily than it reaches a command."""

    BODY = {"finding": "no_controller_running"}

    async def test_a_cross_origin_post_is_rejected(self):
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", self.BODY, origin="http://example.invalid")
        self.assertEqual(response.status, 403)
        self.assertEqual((await self.json_of(response))["reason_code"], "origin_not_allowed")
        self.assertEqual(self.stub.resolves, [])

    async def test_a_wrong_host_is_rejected(self):
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", self.BODY, host="console.invalid")
        self.assertEqual(response.status, 403)
        self.assertEqual((await self.json_of(response))["reason_code"], "host_not_allowed")

    async def test_a_form_content_type_is_rejected(self):
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", self.BODY, content_type="application/x-www-form-urlencoded")
        self.assertEqual(response.status, 403)
        self.assertEqual((await self.json_of(response))["reason_code"], "content_type_not_json")

    async def test_a_missing_console_header_is_rejected(self):
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", self.BODY, header=None)
        self.assertEqual(response.status, 403)
        self.assertEqual((await self.json_of(response))["reason_code"], "console_header_missing")

    async def test_a_missing_credential_is_rejected(self):
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", self.BODY, credential=None)
        self.assertEqual(response.status, 403)
        self.assertEqual((await self.json_of(response))["reason_code"], "credential_missing")

    async def test_a_wrong_credential_is_rejected(self):
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", self.BODY, credential="not-the-credential")
        self.assertEqual(response.status, 403)
        self.assertEqual((await self.json_of(response))["reason_code"], "credential_invalid")
        self.assertEqual(self.stub.resolves, [])

    async def test_a_body_that_is_not_json_is_rejected(self):
        response = await self.client.post(f"/api/commands/{COMMAND_ID}/resolve", data="{", headers=self.headers())
        self.assertEqual(response.status, 400)
        self.assertEqual((await self.json_of(response))["reason_code"], "body_not_json")


class TestResolveOnAReplayOnlyRuntime(ApiCase):
    def make_stub(self):
        return StubRuntime(data_mode="replay")

    async def test_it_is_refused_and_never_forwarded_once_the_mode_is_known(self):
        await self.client.get("/api/hello?contract_version=1")  # the API learns the mode from the runtime
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", {"finding": "no_controller_running"})
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["reason_code"], "replay_only")
        self.assertEqual(self.stub.resolves, [])

    async def test_the_runtime_s_own_refusal_is_reported_when_the_api_has_not_learned_the_mode(self):
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", {"finding": "no_controller_running"})
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["reason_code"], "replay_only")


class TestResolveOnAnIncompatibleRuntime(ApiCase):
    def make_stub(self):
        return StubRuntime(hello_ok=False)

    async def test_a_read_only_api_refuses_to_resolve_at_all(self):
        response = await self.post(f"/api/commands/{COMMAND_ID}/resolve", {"finding": "no_controller_running"})
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["reason_code"], "contract_incompatible")
        self.assertEqual(self.stub.resolves, [])


class TestNothingOnThisRouteResends(ApiCase):
    async def test_a_submit_and_a_resolve_are_two_different_things(self):
        await self.post("/api/commands", command_object("observe", {"holding": False}, command_id=COMMAND_ID))
        self.assertEqual(len(self.stub.submits), 1)
        for _ in range(3):
            await self.post(f"/api/commands/{COMMAND_ID}/resolve", {"finding": "controller_finished"})
        self.assertEqual(len(self.stub.submits), 1, "resolving asked the runtime again and must not have commanded again")
        self.assertEqual(len(self.stub.resolves), 3)

    async def test_the_route_answers_nothing_on_GET(self):
        # A read of a command is GET /api/commands/{id}; resolving is a statement and needs the guard. A GET here must
        # not fall through to the page-serving catch-all and answer with the index.
        response = await self.client.get(f"/api/commands/{COMMAND_ID}/resolve", headers={"Host": f"127.0.0.1:{self.port}"})
        self.assertIn(response.status, (404, 405))
        body = json.loads(await response.text())
        self.assertFalse(body["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
