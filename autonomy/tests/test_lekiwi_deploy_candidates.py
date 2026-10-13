#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_lekiwi_deploy_candidates.py: bin/pi/lekiwi-deploy.sh install prepares the Cake showcase's
# candidates, against the FAKE ROBOT COMPUTER of test_lekiwi_deploy.py (its harness, subclassed; nothing reaches a
# robot): the tampered twin of the swapped Plan (exactly one byte flipped), the STARTING Plan as the undo candidate, a
# candidates listing with each role, key and sha256, and an unsigned capsule twin when build-mobile names none, so the
# signature check's twin row runs. fetch-candidates copies all four candidate Plans back, verified.
# Run directly: python3 autonomy/tests/test_lekiwi_deploy_candidates.py
import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_lekiwi_deploy import DEMO_PLAN_STUB, FakeRobotHarness, read_text, write_exec  # noqa: E402

# The shipped build-mobile names no unsigned twin (demo-plan at the deployed Cake revision prints none), and the shipped
# `build` names its twin as `unsigned_capsule_path`. This stub is the harness's, with both of those made true.
NO_TWIN_STUB = DEMO_PLAN_STUB.replace(
    """    printf 'unsigned_mobile_capsule_path %s\\n' "$out/unsigned-mobile.cakecap" ;;""", "    ;;").replace(
    """      printf 'capsule_digest %s\\nplan_artifact %s\\npublic_key %s\\nstore_root %s\\n' "$d" "$out/plans/demo.cakepln" "$pub" "$out/state-root"
      exit 0""",
    """      printf 'capsule_digest %s\\nplan_artifact %s\\npublic_key %s\\nstore_root %s\\n' "$d" "$out/plans/demo.cakepln" "$pub" "$out/state-root"
      mkdir -p "$out/artifacts"; echo "unsigned" >"$out/artifacts/supervisor-unsigned.cakecap"
      printf 'unsigned_capsule_path %s\\n' "$out/artifacts/supervisor-unsigned.cakecap"
      exit 0""")

ROLES = {"activate": "mobile-demo-swapped", "refused-demo": "mobile-demo-active-successor-abort-swap",
         "tampered-demo": "mobile-demo-swapped-tampered", "undo": "mobile-demo"}


def sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class TheShowcaseCandidates(FakeRobotHarness):
    def plans(self, name):
        return self.release("bundle", "plans", name + ".cakepln")

    def test_the_stubs_were_rewritten(self):
        self.assertNotEqual(NO_TWIN_STUB, DEMO_PLAN_STUB)
        self.assertNotIn("unsigned_mobile_capsule_path", NO_TWIN_STUB)
        self.assertIn("unsigned_capsule_path", NO_TWIN_STUB)

    def test_the_tampered_twin_differs_from_the_swapped_plan_in_exactly_one_byte(self):
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        with open(self.plans("mobile-demo-swapped"), "rb") as handle:
            swapped = handle.read()
        with open(self.plans("mobile-demo-swapped-tampered"), "rb") as handle:
            tampered = handle.read()
        self.assertEqual(len(swapped), len(tampered))
        self.assertEqual(sum(1 for a, b in zip(swapped, tampered) if a != b), 1)
        self.assertIn("tampered twin", out)
        self.assertNotIn("signature", [line for line in out.splitlines() if "tampered twin" in line][0].replace("not a signature", ""))

    def test_the_listing_names_each_role_its_plan_and_its_sha256(self):
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        listing = read_text(self.release("candidates.txt")).splitlines()
        self.assertEqual(len(listing), 4)
        for line in listing:
            word, role, key, digest, path = line.split(" ", 4)
            self.assertEqual(word, "candidate")
            self.assertEqual(ROLES[role], key)
            self.assertEqual(path, self.plans(key))
            self.assertEqual(digest, sha(path))
        self.assertIn(self.release("bundle", "plans", "mobile-demo.cakepln"), read_text(self.release("candidates.txt")))
        self.assertIn("candidates.txt", read_text(self.release("VERIFIED")))
        self.assertIn("bundle/plans/mobile-demo-swapped-tampered.cakepln", read_text(self.release("VERIFIED")))

    def test_a_manifest_that_names_a_twin_is_left_as_it_is(self):
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        self.assertEqual([line for line in self.lines("demo-plan") if line.startswith("build ")], [])
        self.assertFalse(os.path.exists(self.release("unsigned-twin")))
        self.assertIn("unsigned twin: the manifest names one", out)

    def test_an_unsigned_twin_is_built_and_named_when_build_mobile_names_none(self):
        write_exec(os.path.join(self.cake_bin, "demo-plan"), NO_TWIN_STUB)
        result, out = self.install()
        self.assertEqual(result.returncode, 0, out)
        builds = [line for line in self.lines("demo-plan") if line.startswith("build ")]
        self.assertEqual(len(builds), 1)
        self.assertIn("--signing-key-file " + self.kroot("keys", "deployment.keypair"), builds[0])
        self.assertIn("--out-dir " + self.release("unsigned-twin"), builds[0])
        self.assertNotIn("--gate-fixture-key", builds[0])
        twin = self.release("unsigned-twin", "artifacts", "supervisor-unsigned.cakecap")
        self.assertIn(f"unsigned_capsule_path {twin}", read_text(self.release("manifest.txt")).splitlines())
        self.assertTrue(os.path.isfile(twin))
        # The twin is never admitted to the deployment's store.
        self.assertFalse(any(read_text(os.path.join(dirpath, name)).startswith("unsigned")
                             for dirpath, _, names in os.walk(self.release("bundle", "state-root")) for name in names))

    def test_prove_signature_then_runs_the_twin_row(self):
        write_exec(os.path.join(self.cake_bin, "demo-plan"), NO_TWIN_STUB)
        self.assertEqual(self.install()[0].returncode, 0)
        self.forget()
        result = self.run_deploy("prove-signature")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("unsigned twin 1 under require, deployment key: verdict refused reason signature_missing", out)
        self.assertNotIn("the manifest names no unsigned twin", out)

    def test_fetch_candidates_copies_all_four_back_verified(self):
        self.assertEqual(self.install()[0].returncode, 0)
        result = self.run_deploy("fetch-candidates")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        for key in ROLES.values():
            name = key + ".cakepln"
            self.assertIn(f"candidate {name} sha256 {sha(os.path.join(self.candidates, name))}", out)

    def test_fetch_candidates_from_a_release_without_the_showcase_plans_still_fetches_the_two(self):
        self.assertEqual(self.install()[0].returncode, 0)
        os.remove(self.plans("mobile-demo-swapped-tampered"))
        result = self.run_deploy("fetch-candidates")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("mobile-demo-swapped-tampered.cakepln is not in this release", out)
        self.assertTrue(os.path.exists(os.path.join(self.candidates, "mobile-demo-swapped.cakepln")))


if __name__ == "__main__":
    unittest.main()
