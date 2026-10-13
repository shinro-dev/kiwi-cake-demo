#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_bundle.py: the pinned run bundle. The import closure is checked on a small synthetic source
# tree built in a temporary directory (function-level imports, a relative import, a package, a name that exists twice), and
# once against THIS checkout's real controller, read-only. Nothing is imported from the controller and nothing is run.
# Run directly: python3 packages/demo-runtime/tests/test_bundle.py
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import bundle as bd  # noqa: E402
from demo_runtime import journal as jn  # noqa: E402
from demo_runtime.config import RuntimeConfig  # noqa: E402

import demo_contracts  # noqa: E402

GIT = shutil.which("git")
ENTRY = "bin/laptop/manipulation/run_single_cube_cycle.py"
PRIVATE_ADDRESS = ".".join(("192", "168", "7", "41"))
ROBOT_ID = "kiwi" + "-unit-" + "0042"
USER_NAME = "operator" + "name"


class SyntheticTree(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-bundle-"))
        self.addCleanup(self._remove)
        self.root, self.data = self.tmp / "checkout", self.tmp / "data"
        files = {
            ENTRY: "import os\nimport helper_a\nfrom helper_b import thing\n\ndef late():\n    import helper_late  # function-level\n    from pack import inner\n    import numpy\n",
            "bin/laptop/perception/run_stage1_scan.py": "import cv2\nPI_FILES = ('bin/pi/probe.py', 'autonomy/perception/lifecycle.py')\nOTHER = 1\n",
            "bin/laptop/perception/helper_a.py": "import run_stage1_scan\n",
            "autonomy/manipulation/helper_b.py": "thing = 1\n",
            "autonomy/perception/helper_b.py": "thing = 2  # the same module name in a second source directory\n",
            "tools/helper_late.py": "from . import sibling\n",
            "tools/sibling.py": "x = 1\n",
            "autonomy/pack/__init__.py": "", "autonomy/pack/inner.py": "from .deep import leaf\n", "autonomy/pack/deep/__init__.py": "", "autonomy/pack/deep/leaf.py": "",
            "bin/pi/probe.py": "print('pi side')\n", "autonomy/perception/lifecycle.py": "",
            "autonomy/tests/test_helper_b.py": "import helper_b\n", "tools/never_imported.py": "",
            "packages/demo-runtime/demo_runtime/__init__.py": "", "packages/demo-runtime/demo_runtime/emit.py": "", "packages/demo-runtime/tests/test_x.py": "",
            "packages/demo-contracts/python/demo_contracts/__init__.py": "", "packages/demo-contracts/schema/v1/event.schema.json": "{}", "packages/demo-contracts/ts/generated.ts": "",
        }
        for relative, text in files.items():
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.root / relative).write_text(text)

    def _remove(self):
        bd.make_tree_writable(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_closure_follows_function_level_relative_and_package_imports(self):
        closure = bd.import_closure(self.root / ENTRY, self.root)
        self.assertEqual(closure, sorted([ENTRY, "bin/laptop/perception/helper_a.py", "bin/laptop/perception/run_stage1_scan.py", "autonomy/manipulation/helper_b.py",
                                          "autonomy/perception/helper_b.py", "tools/helper_late.py", "tools/sibling.py", "autonomy/pack/__init__.py", "autonomy/pack/inner.py",
                                          "autonomy/pack/deep/__init__.py", "autonomy/pack/deep/leaf.py"]))
        self.assertNotIn("tools/never_imported.py", closure)
        self.assertFalse([path for path in closure if "/tests/" in path], "a test directory is never a source of modules")

    def test_the_bundle_list_adds_the_pi_files_the_runtime_and_the_contracts(self):
        self.assertEqual(bd.pi_files(self.root), ("bin/pi/probe.py", "autonomy/perception/lifecycle.py"), "read with ast: the module imports OpenCV and is never imported")
        files = bd.bundle_file_list(self.root)
        for expected in ("bin/pi/probe.py", "autonomy/perception/lifecycle.py", "packages/demo-runtime/demo_runtime/emit.py", "packages/demo-contracts/python/demo_contracts/__init__.py",
                         "packages/demo-contracts/schema/v1/event.schema.json"):
            self.assertIn(expected, files)
        self.assertNotIn("packages/demo-runtime/tests/test_x.py", files)
        self.assertNotIn("packages/demo-contracts/ts/generated.ts", files)
        (self.root / "bin/laptop/perception/run_stage1_scan.py").write_text("PI_FILES = ('../outside.py',)\n")
        with self.assertRaises(bd.BundleError):
            bd.bundle_file_list(self.root)

    def test_a_bundle_is_content_addressed_read_only_and_built_once(self):
        first = bd.build_bundle(self.root, self.data)
        self.assertEqual((first.reused, first.root, len(first.digest)), (False, self.data / "bundles" / first.digest, 64))
        self.assertEqual(first.digest, bd.digest_of(first.files), "sha256 over the sorted (relative path, file sha256) list")
        self.assertEqual((first.root / ENTRY).read_text(), (self.root / ENTRY).read_text(), "repository-relative paths are kept")
        index = json.loads((first.root / bd.BUNDLE_INDEX).read_text())
        self.assertEqual((index["digest"], index["entry"], len(index["files"])), (first.digest, ENTRY, len(first.files)))
        modes = {stat.S_IMODE(os.stat(path).st_mode) & 0o222 for path in [first.root, *first.root.rglob("*")]}
        self.assertEqual(modes, {0}, "nothing in a bundle is writable")
        with self.assertRaises(PermissionError):
            (first.root / ENTRY).write_text("tampered")
        again = bd.build_bundle(self.root, self.data)
        self.assertEqual((again.reused, again.digest), (True, first.digest))
        self.assertEqual([path.name for path in (self.data / "bundles").iterdir()], [first.digest], "no staging directory is left behind")
        (self.root / "tools/sibling.py").write_text("x = 2\n")
        changed = bd.build_bundle(self.root, self.data)
        self.assertNotEqual(changed.digest, first.digest)
        self.assertEqual((first.root / "tools/sibling.py").read_text(), "x = 1\n", "the old bundle still holds the old code: a run stays pinned to what it started with")
        (self.root / "tools/never_imported.py").write_text("changed, and not part of any bundle\n")
        self.assertEqual(bd.build_bundle(self.root, self.data).digest, changed.digest)

    def test_a_bundle_whose_content_does_not_match_its_name_is_refused(self):
        built = bd.build_bundle(self.root, self.data)
        bd.make_tree_writable(built.root)
        (built.root / "tools/sibling.py").write_text("x = 'tampered'\n")
        with self.assertRaises(bd.BundleError):
            bd.build_bundle(self.root, self.data)

    def test_a_file_that_does_not_parse_fails_the_build_and_names_no_absolute_path(self):
        (self.root / "tools/sibling.py").write_text("def broken(:\n")
        with self.assertRaises(bd.BundleError) as refused:
            bd.build_bundle(self.root, self.data)
        self.assertIn("tools/sibling.py", str(refused.exception))
        self.assertNotIn(str(self.tmp), str(refused.exception))
        self.assertFalse((self.data / "bundles").exists() and list((self.data / "bundles").iterdir()))
        with self.assertRaises(bd.BundleError):
            bd.build_bundle(self.root, self.data, "bin/laptop/manipulation/no_such_entry.py")


class ThisCheckout(unittest.TestCase):
    def test_the_real_controllers_bundle_plan(self):
        files = bd.bundle_file_list(REPO_ROOT)
        for expected in (ENTRY, "bin/laptop/perception/run_stage1_scan.py", "autonomy/perception/scan_world.py", "autonomy/perception/observation_store.py",
                         "autonomy/manipulation/cartesian_lift.py", "tools/static_world_observation.py", "autonomy/perception/zero_shot_detector.py",
                         "packages/demo-runtime/demo_runtime/supervisor.py", "packages/demo-runtime/demo_runtime/config.py", "packages/demo-contracts/python/demo_contracts/__init__.py",
                         "packages/demo-contracts/schema/v1/event.schema.json", "packages/demo-contracts/schema/v1/common.schema.json"):
            self.assertIn(expected, files, "static_world_observation and zero_shot_detector are imported inside functions only")
        self.assertEqual(bd.pi_files(REPO_ROOT), tuple(path for path in bd.pi_files(REPO_ROOT) if path in files))
        self.assertGreaterEqual(len(bd.pi_files(REPO_ROOT)), 3)
        self.assertFalse([path for path in files if "/tests/" in path or path.startswith("tests/") or not (REPO_ROOT / path).is_file()])
        self.assertFalse([path for path in files if not path.endswith((".py", ".json"))], "source and schemas only: no weights, images or binaries")

    def test_the_fake_controllers_bundle_is_small(self):
        files = bd.bundle_file_list(REPO_ROOT, "packages/demo-runtime/tests/fake_controller.py")
        self.assertIn("packages/demo-runtime/tests/fake_controller.py", files)
        self.assertFalse([path for path in files if path.startswith(("bin/", "autonomy/", "tools/"))], "the fake imports nothing from the robot stack")


class Manifest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-manifest-"))
        self.addCleanup(lambda: (bd.make_tree_writable(self.tmp), shutil.rmtree(self.tmp, ignore_errors=True)))

    def _bundle(self):
        return bd.build_bundle(REPO_ROOT, self.tmp / "data", "packages/demo-runtime/tests/fake_controller.py")

    def test_the_manifest_states_digests_versions_and_a_model_reference_and_nothing_private(self):
        holder = self.tmp / ("private-" + USER_NAME)
        config = RuntimeConfig.from_mapping({"data_root": str(holder / "demo-data"), "saved_pose_file": "pose.json", "pad_template_dir": "pad-template", "observation_root": "observations",
                                             "host": {"address": PRIVATE_ADDRESS, "robot_id": ROBOT_ID}, "pi_ssh_target": USER_NAME + "@" + PRIVATE_ADDRESS,
                                             "cameras": {"wrist_id": "usb-" + "SN" + "7654321", "front_id": None}, "model": {"identity": "google/owlv2-base-patch16-ensemble", "revision": "refs/pr/7"}})
        manifest = bd.run_manifest(bundle=self._bundle(), source_root=REPO_ROOT, contract_version="1", config=config, run_id="console-x", command_id="c1", command_type="start_cycle", data_mode="synthetic")
        self.assertEqual((manifest["contract_version"], len(manifest["bundle_digest"]), len(manifest["config_digest"]), manifest["interpreter"]["version"]),
                         ("1", 64, 64, ".".join(str(part) for part in sys.version_info[:3])))
        self.assertEqual(manifest["model"], {"identity": "google/owlv2-base-patch16-ensemble", "revision": "refs/pr/7", "identity_withheld": False}, "a REFERENCE to the model, never its weights")
        self.assertIsNone(manifest["input_digests"], "the inputs do not exist here: a manifest must not carry a digest of nothing")
        self.assertEqual(set(manifest["packages"]), set(bd.KEY_PACKAGES))
        self.assertIsNotNone(manifest["packages"]["numpy"])
        text = json.dumps(manifest)
        for private in (PRIVATE_ADDRESS, ROBOT_ID, USER_NAME, str(self.tmp), str(REPO_ROOT), os.path.expanduser("~")):
            self.assertNotIn(private, text)
        local = RuntimeConfig.from_mapping({"data_root": str(holder), "saved_pose_file": "p", "pad_template_dir": "t", "observation_root": "o", "model": {"identity": str(holder / "checkpoint")}})
        withheld = bd.run_manifest(bundle=self._bundle(), source_root=REPO_ROOT, contract_version="1", config=local, run_id="r", command_id="c", command_type="observe", data_mode="synthetic")
        self.assertEqual(withheld["model"], {"identity": None, "revision": None, "identity_withheld": True}, "a local checkpoint path names a home directory: withheld, and said to be")

    def test_input_digests_are_recorded_when_the_inputs_exist(self):
        root = self.tmp / "demo-data"
        (root / "pad-template").mkdir(parents=True)
        (root / "pose.json").write_text("{}")
        for name in ("template.json", "template.png", "valid.png"):
            (root / "pad-template" / name).write_bytes(name.encode())
        config = RuntimeConfig.from_mapping({"data_root": str(root), "saved_pose_file": "pose.json", "pad_template_dir": "pad-template", "observation_root": "observations"})
        manifest = bd.run_manifest(bundle=self._bundle(), source_root=REPO_ROOT, contract_version="1", config=config, run_id="r", command_id="c", command_type="observe", data_mode="synthetic")
        self.assertEqual(sorted(manifest["input_digests"]), ["pad_template_sha256", "saved_pose_sha256"])
        bare = bd.run_manifest(bundle=self._bundle(), source_root=REPO_ROOT, contract_version="1", config=None, run_id="r", command_id="c", command_type="observe", data_mode="synthetic")
        self.assertEqual((bare["config_digest"], bare["input_digests"], bare["model"]), (None, None, None))

    @unittest.skipUnless(GIT, "git is not on the PATH")
    def test_the_git_revision_and_the_dirty_flag_come_from_the_source_checkout(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

        def git(*arguments):
            subprocess.run([GIT, "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", *arguments], check=True, capture_output=True, env=env)

        git("init", "-q")
        (repo / "file.txt").write_text("one\n")
        git("add", "file.txt")
        git("commit", "-q", "-m", "first")
        clean = bd.git_identity(repo)
        self.assertEqual((len(clean["revision"]), clean["dirty"]), (40, False))
        (repo / "file.txt").write_text("two\n")
        self.assertEqual(bd.git_identity(repo), {"revision": clean["revision"], "dirty": True})
        self.assertEqual(bd.git_identity(self.tmp / "not-a-repository"), {"revision": None, "dirty": None}, "git could not answer: nulls, never a guess and never an exception")

    def test_the_manifest_the_runtime_writes_is_a_contract_object(self):
        manifest = bd.run_manifest(bundle=self._bundle(), source_root=REPO_ROOT, contract_version="1", config=None, run_id="console-x", command_id="c1",
                                   command_type="start_cycle", data_mode="synthetic")
        self.assertEqual(demo_contracts.validate("manifest", manifest), [], "the dict the runtime already wrote is what the schema describes")


class RunExport(unittest.TestCase):
    """One run packaged for somebody else. It carries what the runtime already keeps and nothing more, it is bounded, and
    anything it could not carry is STATED on the index rather than quietly missing."""

    maxDiff = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-export-"))
        self.addCleanup(lambda: (bd.make_tree_writable(self.tmp), shutil.rmtree(self.tmp, ignore_errors=True)))
        pinned = bd.build_bundle(REPO_ROOT, self.tmp / "data", "packages/demo-runtime/tests/fake_controller.py")
        self.manifest = bd.run_manifest(bundle=pinned, source_root=REPO_ROOT, contract_version="1", config=None, run_id="console-1", command_id="c1",
                                        command_type="start_cycle", data_mode="synthetic")

    def _records(self):
        def made(kind, payload, seq, **fields):
            record = jn.make_event(kind, payload, source_session="fake-controller-0001", run_id="console-1", **fields)
            record["seq"] = seq
            return record

        return [
            made("run.started", {"command_id": "c1", "command_type": "start_cycle", "data_mode": "synthetic", "plan_only": False, "holding": None}, 1),
            made("observation.captured", {"source_role": "wrist", "media": "frame", "width_px": 640, "height_px": 480, "content_digest": "cd" * 32,
                                          "source_stamp": None, "retained": True}, 2, observation_id="obs-0001"),
            made("predicate.evaluated", {"predicate": "placed", "value": True, "threshold": 1.0, "measured": 1.0,
                                         "evidence": [{"type": "observation", "observation_ids": ["obs-0001", "obs-0002"], "scalar_name": None}],
                                         "consumed_source_stamp": None}, 3),
            made("run.finished", {"outcome": "placed_new", "exit_status": 0, "predicates": {"object_secured": True, "placed": True, "clearance_target_met": True, "base_returned": True},
                                  "automatic_retries": 0, "task_control_interventions": 0, "evidence_complete": False, "evidence_gaps": 1}, 4),
            made("telemetry.dropped", {"stage": "emitter_queue", "count": 7, "window_ms": 1000}, 5),
        ]

    def _export(self, image_of, **bounds):
        return bd.run_export(run_id="console-1", manifest=self.manifest, records=self._records(), image_of=image_of, contract_version="1", **bounds)

    def test_the_index_is_a_contract_object_that_states_the_window_the_sessions_and_the_summaries(self):
        picture = {"content_type": "image/png", "bytes_b64": "QUJD"}
        export = self._export(lambda observation_id: picture if observation_id == "obs-0001" else None)
        index = export["index"]
        self.assertEqual(demo_contracts.validate("run_export_index", index), [])
        self.assertEqual((index["records"]["count"], index["records"]["first_seq"], index["records"]["last_seq"]), (5, 1, 5))
        self.assertEqual(index["records"]["source_sessions"], ["fake-controller-0001"], "a reader is told which producer sessions the records came from")
        self.assertEqual(index["summaries"], {"evidence_complete": False, "evidence_gaps": 1, "dropped_telemetry": 7, "cake_continuity": None})
        self.assertEqual([image["observation_id"] for image in index["images"]], ["obs-0001", "obs-0002"], "every id the records point at is named")
        self.assertEqual({image["observation_id"] for image in export["images"]}, {"obs-0001"})
        self.assertEqual([(image["included"], image["reason_code"]) for image in index["images"]], [(True, None), (False, "frame_not_available")])
        self.assertEqual(index["gaps"], [{"gap": "frame_missing", "detail": bd.EXPORT_GAP_REASONS["frame_not_available"], "observation_ids": ["obs-0002"]}],
                         "the frame the store could not resolve is a STATED gap, not a failed export")
        self.assertEqual(index["images"][0]["content_digest"], "cd" * 32, "the identity comes from the record, which is what a reader compares against")

    def test_the_size_bound_is_measured_and_a_frame_that_does_not_fit_is_named(self):
        big = {"content_type": "image/png", "bytes_b64": "Q" * 4096}
        export = self._export(lambda observation_id: big, size_limit_bytes=1024)
        index = export["index"]
        self.assertEqual(demo_contracts.validate("run_export_index", index), [])
        self.assertEqual(export["images"], [], "a frame that does not fit is not carried anyway")
        self.assertEqual({image["reason_code"] for image in index["images"]}, {"export_size_limit"})
        self.assertEqual((index["size_limit_bytes"], len(index["gaps"])), (1024, 2))
        self.assertGreater(index["size_bytes"], 0, "what was measured is the manifest and the records the export does carry")

    def test_the_number_of_frames_is_bounded_and_the_rest_are_named(self):
        export = self._export(lambda observation_id: {"content_type": "image/png", "bytes_b64": "QUJD"}, max_images=1)
        self.assertEqual([(image["observation_id"], image["included"], image["reason_code"]) for image in export["index"]["images"]],
                         [("obs-0001", True, None), ("obs-0002", False, "export_image_limit")])
        self.assertEqual(demo_contracts.validate("run_export_index", export["index"]), [])

    def test_a_run_that_never_finished_is_summarised_as_unknown_and_not_as_complete(self):
        summaries = bd.export_summaries([record for record in self._records() if record["kind"] != "run.finished"])
        self.assertEqual((summaries["evidence_complete"], summaries["evidence_gaps"]), (None, None), "a run that did not report cannot be summarised as complete")

    def test_every_observation_the_records_point_at_is_found_once_in_the_order_it_was_referenced(self):
        records = self._records()
        records.append({"kind": "action.planned", "payload": {"evidence_refs": ["obs-0003", "obs-0001"]}, "provenance": {"observation_ids": ["obs-0004"]}, "seq": 6})
        self.assertEqual(bd.referenced_observations(records), ["obs-0001", "obs-0002", "obs-0003", "obs-0004"])
        self.assertEqual(bd.referenced_observations([]), [], "nothing is invented for a run with no record")


if __name__ == "__main__":
    unittest.main()
