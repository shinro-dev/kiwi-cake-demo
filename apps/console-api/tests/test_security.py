#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# apps/console-api/tests/test_security.py: the guard, from the outside. Every check is proved by a request that fails ONLY
# that check, so a test cannot pass because some other check happened to catch it. The runtime behind the API is a stub:
# nothing here starts a process, and a request that is refused never reaches the stub at all.
# Run directly: python3 apps/console-api/tests/test_security.py
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (CREDENTIAL, ApiCase, command_object, event_object, find_leaks, make_app, read_frames)  # noqa: E402

from console_api import repository_root  # noqa: E402
from console_api.security import (CONSOLE_HEADER, CREDENTIAL_HEADER, SESSION_CREDENTIAL_PREFIX, CredentialError, CredentialStore, Guard,  # noqa: E402
                                  default_credential_path, mint_credential, read_credential, write_credential)

READ_PATHS = ("/api/hello", "/api/snapshot", "/api/history", "/api/commands/abcd1234ef0123456789", "/")


class TestCommandGuard(ApiCase):
    """A POST passes five checks. Each test here breaks exactly one of them."""

    async def test_a_complete_command_is_forwarded(self):
        response = await self.post("/api/commands", command_object())
        self.assertEqual(response.status, 200)
        self.assertEqual(len(self.stub.submits), 1)

    async def test_cross_origin_post_is_rejected(self):
        response = await self.post("/api/commands", command_object(), origin="http://evil.example")
        self.assertEqual(response.status, 403)
        self.assertEqual((await self.json_of(response))["reason_code"], "origin_not_allowed")
        self.assertEqual(self.stub.submits, [])

    async def test_a_post_without_an_origin_is_rejected(self):
        headers = self.headers()
        headers.pop("Origin")
        response = await self.client.post("/api/commands", data=json.dumps(command_object()), headers=headers)
        self.assertEqual(response.status, 403)
        self.assertEqual((await self.json_of(response))["reason_code"], "origin_not_allowed")

    async def test_a_wrong_host_is_rejected(self):
        response = await self.post("/api/commands", command_object(), host="console.example:80", origin=f"http://127.0.0.1:{self.port}")
        self.assertEqual(response.status, 403)
        self.assertEqual((await self.json_of(response))["reason_code"], "host_not_allowed")
        self.assertEqual(self.stub.submits, [])

    async def test_a_wrong_host_is_rejected_on_a_read_as_well(self):
        # The defence against a name that resolves to this loopback address covers reads too: a page that reached this
        # port under another name sends that name in Host, whatever method it uses.
        for path in READ_PATHS:
            response = await self.client.get(path, headers={"Host": "console.example:80"})
            self.assertEqual(response.status, 403, path)
            self.assertEqual((await self.json_of(response))["reason_code"], "host_not_allowed", path)

    async def test_a_missing_custom_header_is_rejected(self):
        response = await self.post("/api/commands", command_object(), header=None)
        self.assertEqual(response.status, 403)
        self.assertEqual((await self.json_of(response))["reason_code"], "console_header_missing")
        self.assertEqual(self.stub.submits, [])

    async def test_a_missing_credential_is_rejected(self):
        response = await self.post("/api/commands", command_object(), credential=None)
        self.assertEqual(response.status, 403)
        self.assertEqual((await self.json_of(response))["reason_code"], "credential_missing")

    async def test_a_wrong_credential_is_rejected(self):
        response = await self.post("/api/commands", command_object(), credential=CREDENTIAL[:-1] + "0")
        self.assertEqual(response.status, 403)
        self.assertEqual((await self.json_of(response))["reason_code"], "credential_invalid")
        self.assertEqual(self.stub.submits, [])

    async def test_a_credential_that_is_not_ascii_is_rejected(self):
        # The comparison raises on a string that is not ASCII; the credential always is. Built at run time: this file is ASCII.
        response = await self.post("/api/commands", command_object(), credential="cred" + chr(0xE9) + "ntial")
        self.assertEqual(response.status, 403)
        self.assertEqual((await self.json_of(response))["reason_code"], "credential_invalid")

    async def test_a_form_content_type_is_rejected(self):
        for media_type in ("application/x-www-form-urlencoded", "text/plain", "multipart/form-data", None):
            response = await self.post("/api/commands", command_object(), content_type=media_type)
            self.assertEqual(response.status, 403, media_type)
            self.assertEqual((await self.json_of(response))["reason_code"], "content_type_not_json", media_type)
        self.assertEqual(self.stub.submits, [])

    async def test_a_json_content_type_with_a_charset_is_accepted(self):
        response = await self.post("/api/commands", command_object(), content_type="application/json; charset=utf-8")
        self.assertEqual(response.status, 200)

    async def test_a_body_that_is_not_json_is_rejected(self):
        response = await self.client.post("/api/commands", data="not json at all", headers=self.headers())
        self.assertEqual(response.status, 400)
        self.assertEqual((await self.json_of(response))["reason_code"], "body_not_json")
        self.assertEqual(self.stub.submits, [])

    async def test_a_json_body_that_is_not_an_object_is_rejected(self):
        response = await self.post("/api/commands", [1, 2, 3])
        self.assertEqual(response.status, 400)
        self.assertEqual((await self.json_of(response))["reason_code"], "body_not_json")

    async def test_stop_passes_the_same_guard(self):
        refused = await self.post("/api/stop", {}, credential=None)
        self.assertEqual(refused.status, 403)
        accepted = await self.post("/api/stop", {})
        self.assertEqual(accepted.status, 200)


class TestNoCrossOriginReads(ApiCase):
    async def test_no_response_carries_a_cors_header(self):
        responses = [await self.client.get(path) for path in READ_PATHS]
        responses.append(await self.post("/api/commands", command_object()))
        responses.append(await self.post("/api/commands", command_object(), origin="http://evil.example"))
        for response in responses:
            offending = [name for name in response.headers if name.lower().startswith("access-control-")]
            self.assertEqual(offending, [], f"{response.url} sent {offending}")

    async def test_a_preflight_is_not_answered(self):
        response = await self.client.options("/api/commands", headers={
            "Host": f"127.0.0.1:{self.port}", "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": CONSOLE_HEADER})
        self.assertEqual(response.status, 405)
        self.assertEqual([name for name in response.headers if name.lower().startswith("access-control-")], [])

    async def test_responses_carry_the_hardening_headers(self):
        response = await self.client.get("/api/snapshot")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["Cache-Control"], "no-store")


class TestCredentialSecrecy(ApiCase):
    async def test_the_credential_is_in_no_json_body_and_in_no_header(self):
        responses = [await self.client.get(path) for path in READ_PATHS]
        responses.append(await self.post("/api/commands", command_object()))
        for response in responses:
            body = await response.text()
            self.assertNotIn(CREDENTIAL, body, f"{response.url} put the credential in its body")
            for name, value in response.headers.items():
                self.assertNotIn(CREDENTIAL, value, f"{response.url} put the credential in {name}")

    async def test_a_reflected_credential_would_be_scrubbed(self):
        # The last line of defence: even a body that somehow carried the credential leaves without it.
        self.stub.history_runs = [{"run_id": "sc-1", "note": CREDENTIAL}]
        response = await self.client.get("/api/history")
        self.assertNotIn(CREDENTIAL, await response.text())

    async def test_no_response_body_carries_a_path_an_address_or_a_user(self):
        from support import HOME_PATH, PRIVATE_ADDRESS, SSH_TARGET, USER_NAME

        self.stub.history_runs = [{"run_id": "sc-1", "note": f"{HOME_PATH} {PRIVATE_ADDRESS} {SSH_TARGET}"}]
        self.stub.observations[("obs-20260101T000000Z-wrist-0001", "record")] = {"raw_frame_path": HOME_PATH, "address": PRIVATE_ADDRESS}
        for path in (*READ_PATHS, "/api/history", "/api/observations/obs-20260101T000000Z-wrist-0001/record"):
            body = await (await self.client.get(path)).text()
            self.assertEqual(find_leaks(body), [], f"{path} leaked")
            self.assertNotIn(USER_NAME, body, f"{path} leaked a user name")

    async def test_the_user_of_a_pair_goes_with_the_address_and_not_after_it(self):
        # The defect this covers: the base sanitizer removes the dotted quad first, and 'name@[redacted]' then matches no
        # generic shape, so the user name survives. The pair is taken whole, before anything else.
        from support import SSH_TARGET, USER_NAME

        self.stub.history_runs = [{"run_id": "sc-1", "note": SSH_TARGET}]
        body = await (await self.client.get("/api/history")).text()
        self.assertNotIn(USER_NAME, body)

    async def test_a_device_id_is_the_runtime_s_job_and_this_layer_does_not_claim_it(self):
        # Stated rather than hidden: a camera id has no generic shape, so nothing here can recognise one. The runtime's
        # allowlist drops it (and its config-derived tokens remove it) before it could ever reach this process; the live
        # test proves that end at the far side, on the fake controller's own trap fields.
        from support import CAMERA_ID

        self.stub.history_runs = [{"run_id": "sc-1", "note": CAMERA_ID}]
        body = await (await self.client.get("/api/history")).text()
        self.assertIn(CAMERA_ID, body)


class TestCredentialFile(unittest.TestCase):
    def test_the_file_is_private_and_holds_the_value(self):
        credential = mint_credential()
        with tempfile.TemporaryDirectory(prefix="kc-console-api-") as directory:
            path = write_credential(Path(directory) / "nested" / "credential", credential)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(path.parent).st_mode), 0o700)
            self.assertEqual(read_credential(path), credential)

    def test_a_path_inside_the_repository_is_refused(self):
        for candidate in (repository_root() / "credential", repository_root() / "apps" / "console-api" / "credential"):
            with self.assertRaises(CredentialError):
                write_credential(candidate, mint_credential())
            self.assertFalse(candidate.exists())

    def test_the_default_path_is_outside_the_repository(self):
        path = default_credential_path(8770)
        self.assertNotEqual(path, repository_root())
        self.assertNotIn(repository_root(), path.parents)

    def test_two_launches_do_not_share_a_credential(self):
        self.assertNotEqual(mint_credential(), mint_credential())
        self.assertGreaterEqual(len(mint_credential()), 32)


class TestGuardUnit(unittest.TestCase):
    """The guard without a server: the checks are pure, and their ORDER is what a client sees."""

    class _Request:
        def __init__(self, headers, authority="127.0.0.1:8770"):
            self.headers = headers
            self.transport = type("T", (), {"get_extra_info": staticmethod(lambda _name: tuple(authority.rsplit(":", 1)[:1]) + (int(authority.rsplit(":", 1)[1]),))})()

    def _request(self, **overrides):
        headers = {"Host": "127.0.0.1:8770", "Origin": "http://127.0.0.1:8770", "Content-Type": "application/json",
                   CONSOLE_HEADER: "1", CREDENTIAL_HEADER: CREDENTIAL}
        headers.update({key: value for key, value in overrides.items() if value is not None})
        for key, value in overrides.items():
            if value is None:
                headers.pop(key, None)
        return self._Request(headers)

    def test_a_complete_request_passes(self):
        self.assertIsNone(Guard(CREDENTIAL).check_command(self._request()))

    def test_the_order_is_host_origin_type_header_credential(self):
        guard = Guard(CREDENTIAL)
        broken = {"Host": "evil.example", "Origin": "http://evil.example", "Content-Type": "text/plain", CONSOLE_HEADER: None, CREDENTIAL_HEADER: None}
        self.assertEqual(guard.check_command(self._request(**broken)), "host_not_allowed")
        broken.pop("Host")
        self.assertEqual(guard.check_command(self._request(**broken)), "origin_not_allowed")
        broken.pop("Origin")
        self.assertEqual(guard.check_command(self._request(**broken)), "content_type_not_json")
        broken.pop("Content-Type")
        self.assertEqual(guard.check_command(self._request(**broken)), "console_header_missing")
        broken.pop(CONSOLE_HEADER)
        self.assertEqual(guard.check_command(self._request(**broken)), "credential_missing")

    def test_the_bound_address_decides_and_not_the_configured_one(self):
        guard = Guard(CREDENTIAL, host="127.0.0.1", port=1)  # a configured port that is NOT the one bound
        self.assertIsNone(guard.check_command(self._request()))


class TestWebBuildContainment(ApiCase):
    async def asyncSetUp(self):
        self.directory = tempfile.mkdtemp(prefix="kc-console-web-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory, ignore_errors=True))
        Path(self.directory, "index.html").write_text("<html><head></head><body>console</body></html>", encoding="utf-8")
        Path(self.directory, "app.js").write_text("export const ready = true;\n", encoding="utf-8")
        Path(self.directory, ".hidden").write_text("private\n", encoding="utf-8")
        outside = Path(self.directory).parent / "outside-the-build.txt"
        outside.write_text("must not be served\n", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        await super().asyncSetUp()

    async def start_api(self, **kwargs):
        await super().start_api(web_build=self.directory, **kwargs)

    async def test_index_carries_the_credential_and_is_never_cached(self):
        response = await self.client.get("/")
        self.assertEqual(response.status, 200)
        body = await response.text()
        self.assertIn(CREDENTIAL, body)
        self.assertIn('id="kc-console-boot"', body)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])

    async def test_the_boot_block_is_json_a_page_can_parse(self):
        body = await (await self.client.get("/")).text()
        opening = body.index('<script type="application/json" id="kc-console-boot">') + len('<script type="application/json" id="kc-console-boot">')
        payload = json.loads(body[opening:body.index("</script>", opening)])
        self.assertEqual(payload["contract_version"], "1")
        self.assertEqual(payload["credential"], CREDENTIAL)

    async def test_an_asset_is_served_and_a_hidden_file_is_not(self):
        self.assertEqual((await self.client.get("/app.js")).status, 200)
        self.assertEqual((await self.client.get("/.hidden")).status, 404)

    async def test_a_climbing_path_is_refused(self):
        for path in ("/../outside-the-build.txt", "/%2e%2e/outside-the-build.txt", "/a/../../outside-the-build.txt"):
            response = await self.client.get(path)
            self.assertIn(response.status, (400, 403, 404), path)
            self.assertNotIn("must not be served", await response.text())


class TestContentHashedAssets(unittest.TestCase):
    """Only a name that carries a content hash may be cached forever. The names are REAL ones from a built console."""

    def test_a_hashed_name_is_immutable_and_a_plain_one_is_not(self):
        from console_api.web import CONTENT_HASHED

        for name in ("index-AriOrVho", "index--tvhiCVh", "index-CLQPFPBd", "geist-sans-latin-400-normal-gapTbOY8",
                     "jetbrains-mono-latin-wght-normal-B9CIFXIH"):
            self.assertTrue(CONTENT_HASHED.fullmatch(name), f"{name} carries a hash and should be cacheable")
        for name in ("index", "app", "manifest", "robots", "index-short"):
            self.assertIsNone(CONTENT_HASHED.fullmatch(name), f"{name} carries no hash and must not be cached forever")

    def test_a_digit_is_not_the_signal(self):
        # The defect this covers: an earlier rule looked for a digit in the stem. Two of the three names a real build
        # produced have none, so their assets were served no-cache on every load.
        from console_api.web import CONTENT_HASHED

        for name in ("index-AriOrVho", "index--tvhiCVh"):
            self.assertFalse(any(character.isdigit() for character in name))
            self.assertTrue(CONTENT_HASHED.fullmatch(name))


class TestStreamsGetTheSameLastLine(ApiCase):
    """A stream is a JSON body served one line at a time, and it used to skip the sanitizer entirely.

    The defect these cover, MEASURED: the same diagnostic text came back scrubbed from GET /api/history/<run>/events and
    verbatim from GET /api/events, because the SSE frames went straight to json.dumps. The stream is the live path, so it
    was the less protected of the two.
    """

    async def test_the_events_stream_scrubs_what_a_unary_body_scrubs(self):
        from support import HOME_PATH, PRIVATE_ADDRESS, SSH_TARGET, USER_NAME

        text = f"{HOME_PATH} {PRIVATE_ADDRESS} {SSH_TARGET}"
        self.stub.emit(event_object(1, payload={"stream": "log", "level": "info", "text": text, "redacted": False}))
        response = await self.client.get("/api/events?since=0")
        try:
            frames = await read_frames(response, 1)
        finally:
            response.close()
        body = json.dumps(frames)
        self.assertEqual(find_leaks(body), [], "the events stream leaked what the unary path removes")
        self.assertNotIn(USER_NAME, body)
        self.assertEqual(frames[0]["id"], "1", "scrubbing must not move a reader's cursor")

    async def test_the_telemetry_stream_scrubs_what_a_unary_body_scrubs(self):
        from support import HOME_PATH, PRIVATE_ADDRESS

        self.stub.publish({"tap": {"available": True, "reason": f"{HOME_PATH} {PRIVATE_ADDRESS}"}})
        response = await self.client.get("/api/telemetry")
        try:
            frames = await read_frames(response, 1)
        finally:
            response.close()
        self.assertEqual(find_leaks(json.dumps(frames)), [], "the telemetry stream leaked what the unary path removes")

    async def test_a_stream_end_notice_is_scrubbed_too(self):
        from support import HOME_PATH

        self.stub.break_stream = True
        response = await self.client.get("/api/events?since=0")
        try:
            frames = await read_frames(response, 1)
        finally:
            response.close()
        self.assertIn("stream_ended", frames[0]["data"])
        self.assertEqual(find_leaks(json.dumps(frames) + HOME_PATH[:0]), [])

    def test_a_pump_cannot_be_opened_without_naming_its_sanitizer(self):
        # The guarantee is structural, not a habit: sanitize has no default, so a new stream that forgets it fails here.
        import inspect

        from console_api.sse import pump

        parameter = inspect.signature(pump).parameters["sanitize"]
        self.assertIs(parameter.default, inspect.Parameter.empty)
        self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)


class TestTheCredentialBelongsToTheRuntimeSession(unittest.TestCase):
    """A credential minted per API launch logs an open tab out every time the API is restarted, for no reason anybody
    can point at: the runtime never stopped, the controller never stopped, and a random string in a process that owns
    neither of them changed. The store keys it to the runtime SESSION instead."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="kc-credential-store-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory, ignore_errors=True))
        self.store = CredentialStore(self.directory)

    def test_the_same_session_gets_the_same_value_back(self):
        first = self.store.credential_for("rt-20260101T000000Z-0badc0de")
        second = CredentialStore(self.directory).credential_for("rt-20260101T000000Z-0badc0de")
        self.assertEqual(first, second, "a second API process on the same runtime session must serve the same credential")
        self.assertNotEqual(first, self.store.credential_for("rt-20260101T000000Z-0badc0df"), "a different session is a different credential")

    def test_the_file_is_born_private_and_is_never_overwritten(self):
        value = self.store.credential_for("rt-20260101T000000Z-0badc0de")
        path = self.directory / f"{SESSION_CREDENTIAL_PREFIX}rt-20260101T000000Z-0badc0de"
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(path.read_text(encoding="ascii").strip(), value)
        self.assertEqual(self.store.credential_for("rt-20260101T000000Z-0badc0de"), value)
        # Nothing is left behind by the atomic placement: a temporary file that stayed would be a second copy of it.
        self.assertEqual(sorted(item.name for item in self.directory.iterdir()), [path.name])

    def test_a_session_that_is_not_a_contract_id_names_no_file(self):
        for bad in ("../escape", "has/slash", "", "x" * 65):
            with self.assertRaises(CredentialError, msg=bad):
                self.store.credential_for(bad)

    def test_the_store_refuses_to_put_a_credential_in_the_repository(self):
        with self.assertRaises(CredentialError):
            CredentialStore(repository_root() / "state").credential_for("rt-20260101T000000Z-0badc0de")


class TestASupersededCredentialIsNotAGuess(unittest.TestCase):
    """An expired credential and a wrong one are different facts, and only one of them means somebody is guessing."""

    def test_the_guard_names_the_value_it_served_itself(self):
        guard = Guard("first-credential-value-0123456789")
        self.assertTrue(guard.rotate("second-credential-value-0123456789"))
        self.assertFalse(guard.rotate("second-credential-value-0123456789"), "rotating to the same value changes nothing")
        self.assertTrue(guard._was_served("first-credential-value-0123456789"))
        self.assertFalse(guard._was_served("a-value-nobody-was-ever-given"))


class TestARestartedApiDoesNotLogTheTabOut(ApiCase):
    """The API is restarted in front of the SAME runtime session and the credential the page is holding still works.
    This is the whole point of the store: the tab was never wrong about anything."""

    async def asyncSetUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="kc-credential-continuity-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory, ignore_errors=True))
        self.store = CredentialStore(self.directory)
        self.session = "rt-20260101T000000Z-0badc0de"
        self.credential = self.store.credential_for(self.session)
        await super().asyncSetUp()

    async def start_api(self, **kwargs):
        self.app = make_app(self.stub, credential=self.credential, credential_store=self.store, runtime_session=self.session, **kwargs)
        from aiohttp.test_utils import TestClient, TestServer

        self.server = TestServer(self.app, host="127.0.0.1")
        self.client = TestClient(self.server)
        await self.client.start_server()
        self.port = self.server.port
        self.addAsyncCleanup(self.client.close)

    async def test_a_second_api_process_serves_and_accepts_the_same_credential(self):
        response = await self.post("/api/commands", command_object(), credential=self.credential)
        self.assertEqual(response.status, 200)
        # A second process on the same runtime session: it reads the value out of the store rather than minting one.
        second = CredentialStore(self.directory).credential_for(self.session)
        self.assertEqual(second, self.credential)
        hello = await self.json_of(await self.client.get("/api/hello?contract_version=1"))
        self.assertEqual(hello["credential_owner"], "runtime_session")
        self.assertEqual(hello["bound_runtime_session"], self.session)

    async def test_a_replaced_runtime_rotates_the_credential_and_says_the_old_one_expired(self):
        self.stub.runtime_session = "rt-20260101T000100Z-0badc0df"
        await self.client.get("/api/hello?contract_version=1")  # the API learns the new session and follows it
        refused = await self.post("/api/commands", command_object(), credential=self.credential)
        self.assertEqual(refused.status, 403)
        self.assertEqual((await self.json_of(refused))["reason_code"], "credential_superseded")
        self.assertEqual(self.stub.submits, [], "a superseded credential never reaches the runtime")
        # A value nobody was ever given is still just invalid: the two are not merged into one comforting word.
        invalid = await self.post("/api/commands", command_object(), credential="a-value-nobody-was-ever-given")
        self.assertEqual((await self.json_of(invalid))["reason_code"], "credential_invalid")
        # And the new session's credential, which a reloaded page would be served, works.
        rotated = self.store.credential_for("rt-20260101T000100Z-0badc0df")
        self.assertEqual((await self.post("/api/commands", command_object(), credential=rotated)).status, 200)

    async def test_a_command_is_refused_when_the_runtime_changed_under_a_still_valid_credential(self):
        """The credential is checked first and this is the case it cannot catch: the API has not noticed the new
        session yet, so the value in hand is still the accepted one. The handshake before the command is what catches
        it, and the command is refused rather than sent to a runtime nothing did readiness against."""
        self.stub.runtime_session = "rt-20260101T000200Z-0badc0e0"
        response = await self.post("/api/commands", command_object(), credential=self.credential)
        self.assertEqual(response.status, 409)
        self.assertEqual((await self.json_of(response))["reason_code"], "runtime_session_changed")
        self.assertEqual(self.stub.submits, [], "the command was never sent to a runtime nothing did readiness against")


class TestTheFirstPageAfterARuntimeRestart(ApiCase):
    """The runtime is replaced and the FIRST request after it is a page load (redesign r4 verify-r2 must_fix 1).

    The API is bound to session S1. S1's runtime is replaced by S2 with no request to the API in between, so the API has
    not heard of S2 yet. The page served now must carry the credential the API will accept once that page's own
    handshake has made the API follow S2: the credential of S2, not the one it held for S1. Otherwise the page draws a
    working Stop and its first press, Stop included, is refused credential_superseded."""

    S1 = "rt-20260101T000000Z-0badc0de"
    S2 = "rt-20260101T000300Z-0badc0e1"

    async def asyncSetUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="kc-first-page-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory, ignore_errors=True))
        self.build = Path(tempfile.mkdtemp(prefix="kc-first-page-web-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.build, ignore_errors=True))
        (self.build / "index.html").write_text("<html><head></head><body>console</body></html>", encoding="utf-8")
        self.store = CredentialStore(self.directory)
        self.credential = self.store.credential_for(self.S1)
        await super().asyncSetUp()

    async def start_api(self, **kwargs):
        self.app = make_app(self.stub, credential=self.credential, credential_store=self.store, runtime_session=self.S1,
                            web_build=str(self.build), **kwargs)
        from aiohttp.test_utils import TestClient, TestServer

        self.server = TestServer(self.app, host="127.0.0.1")
        self.client = TestClient(self.server)
        await self.client.start_server()
        self.port = self.server.port
        self.addAsyncCleanup(self.client.close)

    async def served_boot(self) -> dict:
        body = await (await self.client.get("/")).text()
        marker = '<script type="application/json" id="kc-console-boot">'
        opening = body.index(marker) + len(marker)
        return json.loads(body[opening:body.index("</script>", opening)])

    async def test_the_first_page_after_the_restart_holds_the_credential_its_own_handshake_leaves_accepted(self):
        self.stub.runtime_session = self.S2  # the restart; nothing asked the API anything in between
        boot = await self.served_boot()
        hello = await self.json_of(await self.client.get("/api/hello?contract_version=1"))
        self.assertEqual(hello["bound_runtime_session"], self.S2)
        stop = await self.post("/api/stop", {}, credential=boot["credential"])
        answer = await self.json_of(stop)
        self.assertNotEqual(answer.get("reason_code"), "credential_superseded", "the page's first Stop is refused with the credential it was served")
        self.assertEqual(stop.status, 200)
        self.assertEqual(boot["credential"], self.store.credential_for(self.S2))
        # The page is told which session its credential belongs to, so it can tell a later rotation from a refusal.
        self.assertEqual(boot["bound_runtime_session"], self.S2)
        self.assertEqual(boot["credential_owner"], "runtime_session")

    async def test_a_runtime_that_does_not_answer_the_page_load_leaves_the_credential_and_says_whose_it_is(self):
        self.stub.unavailable = True
        boot = await self.served_boot()
        self.assertEqual(boot["credential"], self.credential, "nothing is invented when the runtime cannot name a session")
        self.assertEqual(boot["bound_runtime_session"], self.S1)
        # The runtime comes back as S2: the page's handshake makes the API follow it, and the page, told S1 at load, can
        # see that its credential is no longer the one served.
        self.stub.unavailable = False
        self.stub.runtime_session = self.S2
        hello = await self.json_of(await self.client.get("/api/hello?contract_version=1"))
        self.assertNotEqual(hello["bound_runtime_session"], boot["bound_runtime_session"])
        refused = await self.post("/api/stop", {}, credential=boot["credential"])
        self.assertEqual((await self.json_of(refused))["reason_code"], "credential_superseded")

    async def test_a_page_served_before_any_runtime_named_a_session_says_the_api_minted_its_credential(self):
        app = make_app(self.stub, credential="api-minted-" + "credential-0123456789", credential_store=self.store, runtime_session=None,
                       web_build=str(self.build))
        from aiohttp.test_utils import TestClient, TestServer

        client = TestClient(TestServer(app, host="127.0.0.1"))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        self.stub.unavailable = True
        body = await (await client.get("/")).text()
        marker = '<script type="application/json" id="kc-console-boot">'
        opening = body.index(marker) + len(marker)
        boot = json.loads(body[opening:body.index("</script>", opening)])
        self.assertIsNone(boot["bound_runtime_session"])
        self.assertEqual(boot["credential_owner"], "api_process")


class TestNoWebBuild(ApiCase):
    async def test_the_root_says_so_instead_of_inventing_a_page(self):
        response = await self.client.get("/")
        self.assertEqual(response.status, 404)
        self.assertEqual((await self.json_of(response))["reason_code"], "web_build_not_attached")


if __name__ == "__main__":
    unittest.main(verbosity=2)
