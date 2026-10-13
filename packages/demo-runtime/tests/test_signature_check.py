#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_signature_check.py: the signature policy check, the release's own verifier run on the
# robot for every deployed capsule under the deployment key, a throwaway key and any staged unsigned twin, with NO MOTION,
# no slot touched and no resident asked anything. NO ROBOT, NO RESIDENT, NO NETWORK, NO CONTROLLER, NO REAL VERIFIER.
#
# What is proved, and it is software behaviour only:
#   1. the verifier's printed lines are read as it prints them (the samples are the REAL output of the release's
#      demo-plan verify, recorded offline 2026-09-26 against a scratch bundle), the manifest and resident.conf likewise;
#   2. the throwaway key is a real Ed25519 key (RFC 8032 test vector 1), written only inside the scratch directory, which
#      is removed after every run, on a refusal too;
#   3. every verifier invocation is the ONE allowlisted shape prove-signature uses: verify, the capsule from the deployed
#      store, --policy require, --trusted the resident's own trusted key list, --scratch inside the scratch directory;
#   4. the verdict table: admitted under the deployment key and refused signature_key_id_not_trusted under the throwaway
#      key read as expected; an unsigned twin refused signature_missing reads as expected; with no twin staged the row is
#      SKIPPED, never a pass, and the check is not complete; a verifier that fails, times out or prints no verdict is an
#      ERROR row, and a capsule missing from the store is one too;
#   5. every refusal before the verifier writes nothing: a policy that is not require, an unreadable manifest or
#      resident.conf, no capsule named, no verifier binary, a check already in flight, the claim (a mission carried, a run
#      active, an unresolved command);
#   6. the record: one JSON line per run beside capability-checks.jsonl, folded newest last and bounded for the answer;
#   7. the daemon: the check exists only in a LIVE runtime on the robot (local link) that names the release's verifier,
#      manifest and resident.conf; the run op needs a hello and the read does not; a body beyond the op is refused; the
#      verifier is really spawned as a child (a test double, never demo-plan itself) and a log line says no motion was
#      commanded and no slot touched.
# What is NOT proved here: anything about the release verifier or the resident's own admission. The operator's run on the
# robot, through the console, is what shows the release verifier's verdicts on the deployed capsules.
# Run directly: python3 packages/demo-runtime/tests/test_signature_check.py
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import daemon, forwards, signature_check, sources  # noqa: E402
from demo_runtime.bundle import make_tree_writable  # noqa: E402
from demo_runtime.config import ConfigError, RuntimeConfig, signature_paths  # noqa: E402
from demo_runtime.preflight import readiness_of  # noqa: E402
from demo_runtime.rpc import MUTATING_OPS  # noqa: E402

ROBOT_BUILD = "d3" * 32
DEPLOY_KEY = "821c43a0c15918f4fd3e2931927041a41796e8159aaac46e8498c11534a30be4"
OTHER_KEY = "a1" * 32
SUPERVISOR_DIGEST = "18d9332f438b8444e5f21f1a4715ef811851f43e117d4962a8b4b4e61c344721"
MOBILE_DIGEST = "a384e7607b7db45dfe2d201211364cb6dee8d2bd375c89aaa46f6066c0ebb79b"
# RFC 8032 section 7.1, test 1.
RFC_SEED = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
RFC_PUBLIC = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"

# The four outputs the release's demo-plan verify printed, verbatim, for a signed capsule under its own key, under a key
# it was not signed with, for its unsigned twin, and for a capsule path that does not exist (exit 1, nothing on stdout).
VERIFY_ADMITTED = "policy require\ntrusted_keys 1\nverdict admitted\n"
VERIFY_NOT_TRUSTED = "policy require\ntrusted_keys 1\nverdict refused\nreason signature_key_id_not_trusted\n"
VERIFY_MISSING = "policy require\ntrusted_keys 1\nverdict refused\nreason signature_missing\n"
VERIFY_UNREADABLE = ""


def manifest_text(*, twin: bool = False, store: str = "/opt/example/release/state-root") -> str:
    """A build-mobile manifest as the deploy script reads it: the lines this check consumes, in the shape the real
    demo-plan prints them (name, one space, value), among lines it does not."""
    lines = [f"supervisor_capsule_digest {SUPERVISOR_DIGEST}", "supervisor_capsule_path /opt/example/release/artifacts/supervisor.cakecap",
             f"mobile_capsule_digest {MOBILE_DIGEST}", "plan_artifact /opt/example/release/plans/demo.cakepln",
             f"public_key {DEPLOY_KEY}", "signature_policy require", "slot_name supervisor.lekiwi", f"store_root {store}"]
    if twin:
        lines.append("supervisor_unsigned_capsule_path /opt/example/release/artifacts/supervisor-unsigned.cakecap")
    return "\n".join(lines) + "\n"


def conf_text(*, policy: str = "require", trusted: str = DEPLOY_KEY) -> str:
    return ("store_root = /opt/example/release/state-root\nadmin_socket_path = /run/example/cake-admin.sock\n"
            "admin_socket_mode = 0600\n" + f"signature_policy = {policy}\n" + f"trusted_keys = {trusted}\n"
            "target_profile = lekiwi-mvp\nplan_artifact = /opt/example/release/plans/demo.cakepln\n")


# ---- 1. what the tool prints is read as it prints it --------------------------------------------------------------------

class Parsing(unittest.TestCase):
    def test_the_verifier_s_real_outputs(self):
        self.assertEqual(signature_check.parse_verify_output(VERIFY_ADMITTED),
                         {"verdict": "admitted", "reason": None, "policy": "require", "trusted_keys": 1})
        self.assertEqual(signature_check.parse_verify_output(VERIFY_NOT_TRUSTED)["reason"], "signature_key_id_not_trusted")
        self.assertEqual(signature_check.parse_verify_output(VERIFY_MISSING),
                         {"verdict": "refused", "reason": "signature_missing", "policy": "require", "trusted_keys": 1})
        self.assertEqual(signature_check.parse_verify_output(VERIFY_UNREADABLE), {"verdict": None, "reason": None, "policy": None, "trusted_keys": None})

    def test_only_the_first_verdict_line_counts_and_a_reason_that_is_not_a_code_is_dropped(self):
        parsed = signature_check.parse_verify_output("verdict refused\nreason Not A Code\nverdict admitted\n")
        self.assertEqual((parsed["verdict"], parsed["reason"]), ("refused", None))

    def test_the_manifest_yields_every_capsule_digest_once_the_store_and_the_twins(self):
        parsed = signature_check.parse_manifest(manifest_text(twin=True) + f"mobile_capsule_digest {MOBILE_DIGEST}\n")
        self.assertEqual(parsed["capsule_digests"], sorted((SUPERVISOR_DIGEST, MOBILE_DIGEST)))
        self.assertEqual(parsed["store_root"], "/opt/example/release/state-root")
        self.assertEqual(parsed["unsigned_twin_paths"], ["/opt/example/release/artifacts/supervisor-unsigned.cakecap"])
        self.assertEqual(signature_check.parse_manifest(manifest_text())["unsigned_twin_paths"], [])
        self.assertEqual(signature_check.parse_manifest("")["capsule_digests"], [])

    def test_the_resident_conf_yields_the_policy_and_the_trusted_key_list(self):
        parsed = signature_check.parse_resident_conf(conf_text(trusted=f"{DEPLOY_KEY},{OTHER_KEY}"))
        self.assertEqual((parsed["signature_policy"], parsed["trusted_keys"]), ("require", [DEPLOY_KEY, OTHER_KEY]))
        self.assertEqual(signature_check.parse_resident_conf(conf_text(policy="warn"))["signature_policy"], "warn")
        self.assertEqual(signature_check.parse_resident_conf("trusted_keys = zz\n")["trusted_keys"], [])


# ---- 2. the throwaway key ------------------------------------------------------------------------------------------------

class TheThrowawayKey(unittest.TestCase):
    def test_the_public_key_derivation_is_ed25519(self):
        self.assertEqual(signature_check.ed25519_public_key(bytes.fromhex(RFC_SEED)).hex(), RFC_PUBLIC)

    def test_it_agrees_with_openssl_when_openssl_is_here(self):
        openssl = shutil.which("openssl")
        if openssl is None:
            self.skipTest("openssl is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            der, pub = os.path.join(tmp, "k.der"), os.path.join(tmp, "k.pub.der")
            subprocess.run([openssl, "genpkey", "-algorithm", "ed25519", "-outform", "DER", "-out", der], check=True, capture_output=True)
            subprocess.run([openssl, "pkey", "-inform", "DER", "-in", der, "-pubout", "-outform", "DER", "-out", pub], check=True, capture_output=True)
            seed, public = Path(der).read_bytes()[-32:], Path(pub).read_bytes()[-32:]
        self.assertEqual(signature_check.ed25519_public_key(seed), public)

    def test_the_key_file_has_the_two_lines_demo_plan_reads_and_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "control.keypair")
            public = signature_check.write_throwaway_key(path)
            lines = Path(path).read_text(encoding="ascii").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertRegex(lines[0], r"^ed25519-seed [0-9a-f]{64}$")
            self.assertEqual(lines[1], f"ed25519-public {public}")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(signature_check.ed25519_public_key(bytes.fromhex(lines[0].split()[1])).hex(), public)
        self.assertNotEqual(signature_check.write_throwaway_key.__doc__, None)


# ---- 3. the check with a fake invoker ------------------------------------------------------------------------------------

class Release:
    """A deployed release as the check reads it: the verifier path (never run here), the manifest, resident.conf and a
    store holding the capsules the manifest names, in a temporary directory."""

    def __init__(self, test, *, twin: bool = False, capsules=(SUPERVISOR_DIGEST, MOBILE_DIGEST), policy: str = "require"):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-sig-"))
        test.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.store = self.tmp / "state-root"
        (self.store / "objects").mkdir(parents=True)
        for digest in capsules:
            (self.store / "objects" / digest).write_bytes(b"capsule " + digest.encode("ascii"))
        self.twin = self.tmp / "supervisor-unsigned.cakecap"
        manifest = manifest_text(store=str(self.store)).replace("/opt/example/release/artifacts/supervisor-unsigned.cakecap", str(self.twin))
        if twin:
            self.twin.write_bytes(b"an unsigned twin")
            manifest += f"supervisor_unsigned_capsule_path {self.twin}\n"
        self.manifest = self.tmp / "manifest.txt"
        self.manifest.write_text(manifest, encoding="ascii")
        self.conf = self.tmp / "resident.conf"
        self.conf.write_text(conf_text(policy=policy), encoding="ascii")
        self.demo_plan = self.tmp / "demo-plan"
        self.demo_plan.write_text("#!/bin/sh\nexit 7\n", encoding="ascii")
        os.chmod(self.demo_plan, 0o755)
        self.data = self.tmp / "data"
        self.data.mkdir()


class FakeVerifier:
    """Answers by the trusted key list and the capsule path, like the release verifier does, and keeps every argv."""

    def __init__(self, *, deployment=DEPLOY_KEY, outputs=None):
        self.deployment, self.calls, self.outputs = deployment, [], outputs or {}
        self.on_call = None

    def __call__(self, argv, timeout_s):
        self.calls.append((list(argv), timeout_s))
        if self.on_call is not None:
            self.on_call(len(self.calls))
        options = {argv[i]: argv[i + 1] for i in range(2, len(argv) - 1, 2)}
        capsule, trusted = options.get("--capsule", ""), options.get("--trusted", "")
        if os.path.basename(capsule) in self.outputs:
            return self.outputs[os.path.basename(capsule)]
        if capsule.endswith("unsigned.cakecap"):
            return {"returncode": 0, "stdout": VERIFY_MISSING, "reason_code": None}
        if self.deployment in trusted.split(","):
            return {"returncode": 0, "stdout": VERIFY_ADMITTED, "reason_code": None}
        return {"returncode": 0, "stdout": VERIFY_NOT_TRUSTED, "reason_code": None}


class CheckCase(unittest.TestCase):
    def check(self, release: Release, verifier=None, **kwargs) -> signature_check.SignatureCheck:
        self.verifier = verifier or FakeVerifier()
        return signature_check.SignatureCheck(release.data / signature_check.CHECKS_FILE_NAME, demo_plan_path=str(release.demo_plan),
                                              manifest_path=str(release.manifest), resident_conf_path=str(release.conf),
                                              scratch_root=release.data, runtime_session="rt-test", invoke=self.verifier, **kwargs)

    def records(self, release: Release) -> list:
        path = release.data / signature_check.CHECKS_FILE_NAME
        return [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()] if path.exists() else []

    @staticmethod
    def rows(answer, kind=None, key=None):
        return [row for row in answer["rows"] if (kind is None or row["kind"] == kind) and (key is None or row["key"] == key)]


class EveryInvocationIsTheAllowlistedShape(CheckCase):
    def test_verify_of_each_store_object_under_the_resident_s_keys_and_the_throwaway_key_in_a_scratch_directory(self):
        release = Release(self)
        check = self.check(release)
        answer = check.run(claim=lambda: None)
        self.assertTrue(answer["ok"], answer)
        calls = [argv for argv, _timeout in self.verifier.calls]
        self.assertEqual(len(calls), 4, "two capsules, two keys each")
        for argv in calls:
            self.assertEqual(argv[0], str(release.demo_plan))
            self.assertEqual(argv[1], "verify")
            self.assertEqual(argv[2:4:2] + argv[4:8:2] + argv[8:9], ["--capsule", "--policy", "--trusted", "--scratch"])
            self.assertEqual(argv[5], "require")
            self.assertTrue(argv[3].startswith(str(release.store / "objects") + os.sep), argv[3])
            self.assertTrue(argv[9].startswith(str(release.data) + os.sep), "the scratch directory is under the runtime's own data")
            self.assertNotIn(argv[9], (str(release.data), str(release.store)))
        keys = sorted({argv[7] for argv in calls})
        self.assertIn(DEPLOY_KEY, keys, "the deployment key is the resident's trusted list, as resident.conf names it")
        self.assertEqual(len(keys), 2)
        throwaway = next(key for key in keys if key != DEPLOY_KEY)
        self.assertRegex(throwaway, r"^[0-9a-f]{64}$")
        self.assertEqual(answer["throwaway_public_key"], throwaway)
        self.assertEqual(answer["trusted_keys"], [DEPLOY_KEY])
        self.assertEqual({timeout for _argv, timeout in self.verifier.calls}, {signature_check.VERIFY_TIMEOUT_S})

    def test_the_scratch_directory_and_the_throwaway_key_are_gone_afterwards(self):
        release = Release(self)
        seen = {}
        verifier = FakeVerifier()

        def peek(_n):
            for argv, _t in verifier.calls:
                scratch = Path(argv[9]).parent
                seen[scratch] = sorted(item.name for item in scratch.iterdir())
        verifier.on_call = peek
        self.check(release, verifier).run(claim=lambda: None)
        self.assertEqual(len(seen), 1)
        scratch, names = next(iter(seen.items()))
        self.assertIn("control.keypair", names, "the key was written inside the scratch directory")
        self.assertFalse(scratch.exists(), "the scratch directory is removed after the run")
        self.assertEqual([item.name for item in release.data.iterdir()], [signature_check.CHECKS_FILE_NAME])

    def test_the_scratch_directory_is_removed_when_the_invoker_raises(self):
        release = Release(self)
        verifier = FakeVerifier()
        verifier.on_call = lambda n: (_ for _ in ()).throw(RuntimeError("boom"))
        check = self.check(release, verifier)
        with self.assertRaises(RuntimeError):
            check.run(claim=lambda: None)
        self.assertEqual([item.name for item in release.data.iterdir() if item.name != signature_check.CHECKS_FILE_NAME], [])
        self.assertFalse(check.in_flight())


class TheVerdictTable(CheckCase):
    def test_admitted_under_the_deployment_key_and_refused_under_the_throwaway_key_as_expected(self):
        release = Release(self)
        answer = self.check(release).run(claim=lambda: None)
        deployment = self.rows(answer, "signed_capsule", "deployment")
        throwaway = self.rows(answer, "signed_capsule", "throwaway")
        self.assertEqual([row["capsule"] for row in deployment], sorted((SUPERVISOR_DIGEST, MOBILE_DIGEST)))
        for row in deployment:
            self.assertEqual((row["expected"], row["expected_reason"], row["observed"], row["reason_code"], row["admitted"], row["as_expected"]),
                             ("admitted", None, "admitted", None, True, True))
        for row in throwaway:
            self.assertEqual((row["expected"], row["expected_reason"], row["observed"], row["reason_code"], row["admitted"], row["as_expected"]),
                             ("refused", "signature_key_id_not_trusted", "refused", "signature_key_id_not_trusted", False, True))
        self.assertEqual((answer["policy"], answer["capsules"]), ("require", 2))

    def test_with_no_unsigned_twin_staged_the_row_is_skipped_and_the_check_is_not_complete(self):
        answer = self.check(Release(self)).run(claim=lambda: None)
        twins = self.rows(answer, "unsigned_twin")
        self.assertEqual(len(twins), 1)
        self.assertEqual(twins[0], {"capsule": None, "kind": "unsigned_twin", "key": "deployment", "expected": "refused",
                                    "expected_reason": "signature_missing", "observed": "skipped", "reason_code": "unsigned_twin_not_staged",
                                    "admitted": None, "as_expected": None})
        self.assertEqual((answer["as_expected"], answer["complete"]), (True, False))
        self.assertEqual((answer["rows_as_expected"], answer["rows_not_as_expected"], answer["rows_skipped"], answer["rows_error"]), (4, 0, 1, 0))

    def test_a_staged_unsigned_twin_is_refused_signature_missing_as_expected_and_named_by_its_digest(self):
        release = Release(self, twin=True)
        answer = self.check(release).run(claim=lambda: None)
        twins = self.rows(answer, "unsigned_twin")
        self.assertEqual(len(twins), 1)
        self.assertEqual(twins[0]["capsule"], hashlib.sha256(b"an unsigned twin").hexdigest())
        self.assertEqual((twins[0]["observed"], twins[0]["reason_code"], twins[0]["admitted"], twins[0]["as_expected"]),
                         ("refused", "signature_missing", False, True))
        self.assertEqual((answer["as_expected"], answer["complete"]), (True, True))
        argv = self.verifier.calls[-1][0]
        self.assertEqual((argv[3], argv[7]), (str(release.twin), DEPLOY_KEY))

    def test_a_verdict_that_is_not_the_expected_one_is_recorded_as_such(self):
        release = Release(self, capsules=(SUPERVISOR_DIGEST,))
        verifier = FakeVerifier(outputs={SUPERVISOR_DIGEST: {"returncode": 0, "stdout": VERIFY_ADMITTED, "reason_code": None}})
        answer = self.check(release, verifier).run(claim=lambda: None)
        throwaway = self.rows(answer, "signed_capsule", "throwaway")[0]
        self.assertEqual((throwaway["observed"], throwaway["admitted"], throwaway["as_expected"]), ("admitted", True, False))
        self.assertEqual((answer["as_expected"], answer["rows_not_as_expected"]), (False, 1))

    def test_a_verifier_that_fails_times_out_or_prints_no_verdict_is_an_error_row(self):
        for output, code in (({"returncode": 1, "stdout": VERIFY_UNREADABLE, "reason_code": None}, "verifier_failed"),
                             ({"returncode": None, "stdout": "", "reason_code": "verifier_timeout"}, "verifier_timeout"),
                             ({"returncode": None, "stdout": "", "reason_code": "verifier_unrunnable"}, "verifier_unrunnable"),
                             ({"returncode": 0, "stdout": "policy require\n", "reason_code": None}, "verdict_unreadable")):
            with self.subTest(code=code):
                release = Release(self, capsules=(SUPERVISOR_DIGEST,))
                verifier = FakeVerifier(outputs={SUPERVISOR_DIGEST: output})
                answer = self.check(release, verifier).run(claim=lambda: None)
                staged = [row for row in self.rows(answer, "signed_capsule") if row["capsule"] == SUPERVISOR_DIGEST]
                self.assertEqual(len(staged), 2)
                for row in staged:
                    self.assertEqual((row["observed"], row["reason_code"], row["admitted"], row["as_expected"]), ("error", code, None, False))
                # The manifest also names the mobile capsule, which this release does not stage: two more error rows.
                self.assertEqual((answer["as_expected"], answer["complete"], answer["rows_error"]), (False, False, 4))

    def test_a_capsule_the_manifest_names_but_the_store_lacks_is_an_error_row_and_the_verifier_is_not_run_for_it(self):
        release = Release(self, capsules=(SUPERVISOR_DIGEST,))
        answer = self.check(release).run(claim=lambda: None)
        missing = [row for row in self.rows(answer, "signed_capsule") if row["capsule"] == MOBILE_DIGEST]
        self.assertEqual(len(missing), 2)
        for row in missing:
            self.assertEqual((row["observed"], row["reason_code"], row["as_expected"]), ("error", "capsule_not_in_store", False))
        self.assertEqual(len(self.verifier.calls), 2)


class EveryRefusalWritesNothing(CheckCase):
    def refused(self, release, expected, **kwargs):
        answer = self.check(release, **kwargs).run(claim=kwargs.pop("claim", lambda: None))
        self.assertEqual((answer["ok"], answer["reason_code"]), (False, expected), answer)
        self.assertEqual(self.verifier.calls, [], "the verifier ran")
        self.assertEqual(self.records(release), [], "a refusal was recorded")
        return answer

    def test_a_resident_policy_that_is_not_require(self):
        answer = self.refused(Release(self, policy="warn"), "resident_policy_not_require")
        self.assertEqual(answer["detail"], "warn")

    def test_an_unreadable_manifest_or_resident_conf(self):
        release = Release(self)
        release.manifest.unlink()
        self.refused(release, "release_manifest_unreadable")
        release = Release(self)
        release.conf.unlink()
        self.refused(release, "resident_conf_unreadable")

    def test_a_manifest_that_names_no_capsule_and_a_conf_that_names_no_trusted_key(self):
        release = Release(self)
        release.manifest.write_text("store_root " + str(release.store) + "\n", encoding="ascii")
        self.refused(release, "manifest_names_no_capsule")
        release = Release(self)
        release.conf.write_text(conf_text(trusted="not-a-key"), encoding="ascii")
        self.refused(release, "resident_trusted_keys_unreadable")

    def test_a_verifier_binary_that_is_not_there_or_not_executable(self):
        release = Release(self)
        release.demo_plan.unlink()
        self.refused(release, "verifier_binary_unavailable")
        release = Release(self)
        os.chmod(release.demo_plan, 0o644)
        self.refused(release, "verifier_binary_unavailable")

    def test_the_claim_refuses_before_anything_is_read(self):
        release = Release(self)
        release.manifest.unlink()  # would refuse release_manifest_unreadable, but the claim comes first
        check = self.check(release)
        answer = check.run(claim=lambda: "mission_running")
        self.assertEqual((answer["ok"], answer["reason_code"]), (False, "mission_running"))
        self.assertEqual(self.records(release), [])

    def test_one_check_at_a_time(self):
        release = Release(self)
        verifier = FakeVerifier()
        started, release_it = threading.Event(), threading.Event()

        def hold(n):
            if n == 1:
                started.set()
                release_it.wait(timeout=10.0)
        verifier.on_call = hold
        check = self.check(release, verifier)
        first = {}
        thread = threading.Thread(target=lambda: first.update(check.run(claim=lambda: None)))
        thread.start()
        self.assertTrue(started.wait(timeout=10.0))
        self.assertTrue(check.in_flight())
        second = check.run(claim=lambda: None)
        self.assertEqual((second["ok"], second["reason_code"]), (False, "signature_check_in_flight"))
        release_it.set()
        thread.join(timeout=10.0)
        self.assertTrue(first["ok"])
        self.assertFalse(check.in_flight())
        self.assertEqual(len(self.records(release)), 1)

    def test_a_runtime_that_began_to_close_refuses_and_a_run_in_flight_skips_what_is_left(self):
        release = Release(self)
        check = self.check(release)
        check.begin_close()
        answer = check.run(claim=lambda: None)
        self.assertEqual((answer["ok"], answer["reason_code"], answer["detail"]), (False, "signature_check_unavailable", "runtime_closing"))
        release = Release(self)
        verifier = FakeVerifier()
        check = self.check(release, verifier)
        verifier.on_call = lambda n: check.begin_close() if n == 1 else None
        answer = check.run(claim=lambda: None)
        self.assertTrue(answer["ok"])
        self.assertEqual(len(verifier.calls), 1, "no verifier is started once the runtime is closing")
        skipped = [row for row in answer["rows"] if row["observed"] == "skipped"]
        self.assertEqual({row["reason_code"] for row in skipped}, {"runtime_closing", "unsigned_twin_not_staged"})
        self.assertEqual((answer["complete"], answer["rows_skipped"]), (False, 4))
        check.close()


class TheRecord(CheckCase):
    def test_one_line_per_run_beside_the_slot_check_s_file_folded_newest_last_and_bounded(self):
        release = Release(self)
        check = self.check(release)
        answers = [check.run(claim=lambda: None) for _ in range(3)]
        records = self.records(release)
        self.assertEqual(len(records), 3)
        self.assertEqual({(record["rec"], record["v"]) for record in records}, {("signature_check", 1)})
        self.assertEqual([record["check_uuid"] for record in records], [answer["check_uuid"] for answer in answers])
        views, dropped, damaged = check.checks()
        self.assertEqual(([view["check_uuid"] for view in views], dropped, damaged), ([answer["check_uuid"] for answer in answers], 0, 0))
        self.assertEqual(views[-1]["rows"], answers[-1]["rows"])
        self.assertEqual(views[-1]["runtime_session"], "rt-test")
        self.assertNotIn("rec", views[-1])
        self.assertEqual(signature_check.CHECKS_FILE_NAME, "signature-checks.jsonl")
        for _ in range(signature_check.MAX_CHECKS_SHOWN):
            check.run(claim=lambda: None)
        views, dropped, _damaged = check.checks()
        self.assertEqual((len(views), dropped), (signature_check.MAX_CHECKS_SHOWN, 3))

    def test_a_damaged_line_is_counted_and_a_record_of_another_shape_is_left_out(self):
        release = Release(self)
        check = self.check(release)
        check.run(claim=lambda: None)
        with open(release.data / signature_check.CHECKS_FILE_NAME, "a", encoding="ascii") as handle:
            handle.write("not json\n" + json.dumps({"rec": "capability_check", "v": 1, "operation_uuid": "x"}) + "\n")
        views, dropped, damaged = check.checks()
        self.assertEqual((len(views), dropped, damaged), (1, 0, 1))

    def test_no_answer_carries_a_path(self):
        release = Release(self, twin=True)
        answer = self.check(release).run(claim=lambda: None)
        self.assertNotIn(str(release.tmp), json.dumps(answer))
        self.assertNotIn(str(release.tmp), json.dumps(self.records(release)))


# ---- 4. the configuration -----------------------------------------------------------------------------------------------

def base_mapping(tmp: str, **cake_extra) -> dict:
    return {"data_root": os.path.join(tmp, "demo-data"), "saved_pose_file": "pose.json", "pad_template_dir": "pad",
            "observation_root": "observations", "host": {"address": "127.0.0.1", "robot_id": "example_robot"},
            "pi_ssh_target": "", "cameras": {"wrist_id": "usb-test-wrist", "front_id": None},
            "cake": {"admin_probe_path": "/opt/example/admin-probe",
                     "robot": {"link": "local", "local_admin_socket_path": "/run/example/cake-admin.sock", "expected_build_identity": ROBOT_BUILD},
                     **cake_extra}}


SIGNATURE = {"demo_plan_path": "/opt/example/release/bin/demo-plan", "manifest_path": "/opt/example/release/manifest.txt",
             "resident_conf_path": "/opt/example/release/resident.conf"}


class TheConfiguration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kc-sig-cfg-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_section_names_the_three_files_and_is_optional(self):
        config = RuntimeConfig.from_mapping(base_mapping(self.tmp, signature=dict(SIGNATURE)))
        self.assertEqual(signature_paths(config), (SIGNATURE["demo_plan_path"], SIGNATURE["manifest_path"], SIGNATURE["resident_conf_path"]))
        self.assertEqual(config.to_private_dict()["cake"]["signature"], SIGNATURE)
        without = RuntimeConfig.from_mapping(base_mapping(self.tmp))
        self.assertIsNone(signature_paths(without))
        self.assertNotIn("signature", without.to_private_dict()["cake"])
        self.assertEqual(signature_paths({"cake": {"signature": dict(SIGNATURE)}}), tuple(SIGNATURE.values()))
        self.assertIsNone(signature_paths(None))

    def test_every_path_is_required_and_absolute_and_an_unknown_key_is_refused(self):
        for name in SIGNATURE:
            with self.subTest(missing=name):
                with self.assertRaises(ConfigError):
                    RuntimeConfig.from_mapping(base_mapping(self.tmp, signature={key: value for key, value in SIGNATURE.items() if key != name}))
            with self.subTest(relative=name):
                with self.assertRaises(ConfigError):
                    RuntimeConfig.from_mapping(base_mapping(self.tmp, signature={**SIGNATURE, name: "relative/path"}))
        with self.assertRaises(ConfigError):
            RuntimeConfig.from_mapping(base_mapping(self.tmp, signature={**SIGNATURE, "deployment_key_path": "/opt/example/key"}))


# ---- 5. the daemon ---------------------------------------------------------------------------------------------------------

class FakeLink:
    def __init__(self):
        self.up = True

    def start(self):
        return self

    def check(self):
        return forwards.CONNECTED if self.up else forwards.FAILED

    def connected(self):
        return self.up

    def report(self):
        return {"state": self.check(), "reason": None, "since_wall_utc": "2026-09-26T10:00:00.000Z", "since_age_ms": 5.0,
                "starts": 1, "deaths": 0, "restarts": 0, "next_attempt_in_ms": None}

    def stop(self):
        pass


class FakeWatch:
    def start(self):
        return self

    def stop(self):
        pass

    def reading(self):
        return readiness_of("never_read", None)

    def host_pids(self):
        return None


class StubCoordinator:
    performs_motion = False
    running = False

    def cancel_now(self, origin="mission_stop"):
        return None

    def stop(self, origin="mission_stop"):
        return {"ok": True, "stopping": False, "reason_code": "no_running_mission"}

    def close(self):
        pass


FAKE_VERIFIER = """#!/usr/bin/env bash
# A test double for the release verifier: answers by the trusted key list and the capsule path. Not Cake.
set -u
printf '%s\\n' "$*" >>"$(dirname "$0")/verifier-argv.log"
CAP=""; TRUSTED=""
while [ $# -gt 0 ]; do
  case "$1" in --capsule) CAP="$2"; shift 2 ;; --trusted) TRUSTED="$2"; shift 2 ;; --policy|--scratch) shift 2 ;; *) shift ;; esac
done
echo "policy require"; echo "trusted_keys 1"
case "$CAP" in *unsigned*) echo "verdict refused"; echo "reason signature_missing"; exit 0 ;; esac
if [ "$TRUSTED" = "__DEPLOY__" ]; then echo "verdict admitted"; else echo "verdict refused"; echo "reason signature_key_id_not_trusted"; fi
"""


class DaemonCase(unittest.TestCase):
    def setUp(self):
        self.release = Release(self, twin=True)
        self.release.demo_plan.write_text(FAKE_VERIFIER.replace("__DEPLOY__", DEPLOY_KEY), encoding="ascii")
        os.chmod(self.release.demo_plan, 0o755)
        self.probe = self.release.tmp / "admin-probe"
        shutil.copy(PACKAGE_DIR / "tests" / "fake_robot_probe.py", self.probe)
        os.chmod(self.probe, 0o755)
        self.link, self.watch = FakeLink(), FakeWatch()
        self.launches = self.release.tmp / "controller-launches.log"
        self.addCleanup(lambda: self.assertFalse(self.launches.exists(), "a controller was launched"))
        self.addCleanup(lambda: make_tree_writable(self.release.tmp))

    def mapping(self, *, signature=True, local=True, robot=True):
        raw = base_mapping(str(self.release.tmp))
        raw["cake"]["admin_probe_path"] = str(self.probe)
        raw["cake"]["robot"]["local_admin_socket_path"] = str(self.release.tmp / "a.sock")
        if not robot:
            # A SYNTHETIC runtime refuses cake.robot outright (a rehearsal never reaches the robot's resident).
            raw["cake"].pop("robot")
            raw["cake"]["admin_socket_path"] = str(self.release.tmp / "a.sock")
        if not local:
            raw["cake"]["robot"].pop("link")
            raw["cake"]["robot"]["remote_admin_socket_path"] = "/run/user/1000/kc-lekiwi.sock"
        if signature:
            raw["cake"]["signature"] = {"demo_plan_path": str(self.release.demo_plan), "manifest_path": str(self.release.manifest),
                                        "resident_conf_path": str(self.release.conf)}
        return raw

    def core(self, raw=None, *, mode=daemon.LIVE):
        core = daemon.RuntimeCore(data_dir=self.release.data, mode=mode, config=RuntimeConfig.from_mapping(raw or self.mapping()),
                                  source_root=REPO_ROOT, robot_link=self.link, robot_host_watch=self.watch, readiness=self.watch,
                                  controller_entry=daemon.FAKE_CONTROLLER_ENTRY, extra_env={"KC_FAKE_LAUNCH_LOG": str(self.launches)},
                                  source_supervisor=lambda hub: sources.SourceSupervisor(hub))
        self.addCleanup(core.close)
        return core

    @staticmethod
    def ask(core, op, **request):
        return core.handle(op, {"op": op, **request}, None)

    def argv_log(self) -> list:
        path = self.release.tmp / "verifier-argv.log"
        return path.read_text(encoding="ascii").splitlines() if path.exists() else []


class WhereTheCheckExists(DaemonCase):
    def test_a_live_runtime_on_the_robot_that_names_the_release_has_one(self):
        core = self.core()
        self.assertIsNotNone(core.signature_check)
        state = self.ask(core, "capability_signature_state")
        self.assertEqual((state["ok"], state["signature_check_version"], state["available"], state["in_flight"], state["off_reason"], state["policy"]),
                         (True, signature_check.SIGNATURE_CHECK_VERSION, True, False, None, "require"))
        self.assertEqual((state["checks"], state["checks_dropped"], state["damaged_lines"]), ([], 0, 0))
        self.assertEqual(self.argv_log(), [], "a read ran the verifier")

    def test_each_missing_condition_is_named_in_its_order(self):
        for raw, mode, reason in ((self.mapping(robot=False), daemon.SYNTHETIC, signature_check.NOT_LIVE),
                                  (self.mapping(local=False), daemon.LIVE, signature_check.LINK_NOT_LOCAL),
                                  (self.mapping(signature=False), daemon.LIVE, signature_check.NOT_CONFIGURED)):
            with self.subTest(reason=reason):
                core = self.core(raw, mode=mode)
                self.assertIsNone(core.signature_check)
                self.assertEqual(core.signature_check_unavailable, reason)
                for op in ("capability_signature_state", "capability_signature_check"):
                    answer = self.ask(core, op)
                    self.assertEqual((answer["ok"], answer["reason_code"], answer["detail"]), (False, signature_check.UNAVAILABLE, reason))
                core.close()
        robot = type("Robot", (), {"expected_build_identity": ROBOT_BUILD})()
        reason = signature_check.unavailable_reason
        self.assertEqual(reason(live=False, robot=robot, local=True, paths=tuple(SIGNATURE.values())), signature_check.NOT_LIVE)
        self.assertEqual(reason(live=True, robot=None, local=True, paths=tuple(SIGNATURE.values())), signature_check.ROBOT_NOT_CONFIGURED)
        self.assertEqual(reason(live=True, robot=robot, local=False, paths=tuple(SIGNATURE.values())), signature_check.LINK_NOT_LOCAL)
        self.assertEqual(reason(live=True, robot=robot, local=True, paths=None), signature_check.NOT_CONFIGURED)
        self.assertIsNone(reason(live=True, robot=robot, local=True, paths=tuple(SIGNATURE.values())))

    def test_the_run_needs_a_hello_and_the_read_does_not(self):
        self.assertIn("capability_signature_check", MUTATING_OPS)
        self.assertNotIn("capability_signature_state", MUTATING_OPS)


class TheRunThroughTheDaemon(DaemonCase):
    def test_it_spawns_the_verifier_as_a_child_records_the_table_and_says_so_in_the_log(self):
        core = self.core()
        before = core.log.seq_cursor
        answer = self.ask(core, "capability_signature_check")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual((answer["as_expected"], answer["complete"], answer["capsules"]), (True, True, 2))
        self.assertEqual(len(self.argv_log()), 5, "two capsules under two keys, and the twin under the deployment key")
        self.assertTrue(all(line.startswith("verify --capsule ") for line in self.argv_log()))
        state = self.ask(core, "capability_signature_state")
        self.assertEqual([view["check_uuid"] for view in state["checks"]], [answer["check_uuid"]])
        self.assertEqual(state["checks"][0]["rows"], answer["rows"])
        events = [event for event in core.log.subscribe(before).backlog if event.get("reason_code") == "signature_check_answered"]
        self.assertEqual(len(events), 1)
        text = events[0]["payload"]["text"]
        self.assertIn("no motion command was sent", text)
        self.assertIn("no slot was touched", text)
        self.assertNotIn(str(self.release.tmp), json.dumps(answer))

    def test_a_body_beyond_the_op_is_refused_and_nothing_runs(self):
        core = self.core()
        for extra in ({"capsule": "x"}, {"path": "/tmp/x"}, {"policy": "allow"}):
            with self.subTest(extra=extra):
                answer = self.ask(core, "capability_signature_check", **extra)
                self.assertEqual((answer["ok"], answer["reason_code"]), (False, "bad_request"))
        self.assertEqual(self.argv_log(), [])

    def test_it_is_refused_while_a_mission_is_carried_and_the_state_says_so(self):
        core = self.core()
        core.mission = StubCoordinator()
        core.mission.running = True
        answer = self.ask(core, "capability_signature_check")
        self.assertEqual((answer["ok"], answer["reason_code"]), (False, "mission_running"))
        self.assertEqual(self.ask(core, "capability_signature_state")["off_reason"], "mission_running")
        self.assertEqual(self.argv_log(), [])
        core.mission.running = False
        self.assertIsNone(self.ask(core, "capability_signature_state")["off_reason"])

    def test_it_is_refused_while_a_capability_slot_check_is_in_flight(self):
        core = self.core()

        class Busy:
            def in_flight(self):
                return True

            @property
            def pending_operation(self):
                return None

            def begin_close(self):
                pass

            def close(self):
                pass

        core.capability_check = Busy()
        answer = self.ask(core, "capability_signature_check")
        self.assertEqual((answer["ok"], answer["reason_code"]), (False, "capability_check_in_flight"))
        self.assertEqual(self.ask(core, "capability_signature_state")["off_reason"], "capability_check_in_flight")
        self.assertEqual(self.argv_log(), [])


if __name__ == "__main__":
    unittest.main()
