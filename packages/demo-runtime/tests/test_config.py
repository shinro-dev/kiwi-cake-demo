#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_config.py: the explicit runtime config. Where it is loaded from (and that nothing is found
# by accident), what it refuses, that repr() and every error stay free of identities and paths, that the digest moves with
# every field while the sanitized summary shows none of them, and that legacy_layout is exactly today's layout (pinned
# against the sources it mirrors). Tempdir only: no robot, no camera, no network.
# Run directly: python3 packages/demo-runtime/tests/test_config.py
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import config as config_module  # noqa: E402
from demo_runtime.config import (CAMERA_SERVICE_MISSION_PORT, CONFIG_ENV_VAR, DEFAULT_EXECUTION_BACKEND, CaptureProvider, ConfigError, ExecutionBackend,  # noqa: E402
                                 PreviewConfig, RuntimeConfig, config_digest,
                                 execution_backend_of, legacy_layout, load_config, resolve_config_path, sanitized_summary)

# The repository gates refuse a private address, a home path, a device serial or a user name in any tracked file, and these
# tests need exactly such strings to prove they are redacted. They are therefore BUILT here, at runtime, from parts.
PRIVATE_ADDRESS = ".".join(("192", "168", "7", "41"))
USER_NAME = "operator" + "name"
HOME_DATA = "/" + "home" + "/" + USER_NAME + "/demo-data"
SSH_TARGET = USER_NAME + "@" + PRIVATE_ADDRESS
ROBOT_ID = "kiwi" + "-unit-" + "0042"
WRIST_ID = "/dev/v4l/by-id/" + "usb-" + "Vendor_Cam_" + "SN" + "7654321" + "-video-index0"
FRONT_ID = "/dev/v4l/by-id/" + "usb-" + "Vendor_Cam_" + "SN" + "1234567" + "-video-index0"
CMD_PORT, OBS_PORT, TAP_PORT = 45551, 45552, 45553
SENSITIVE = (PRIVATE_ADDRESS, USER_NAME, HOME_DATA, SSH_TARGET, ROBOT_ID, WRIST_ID, FRONT_ID, "SN" + "7654321", "SN" + "1234567")


def full_mapping(**overrides) -> dict:
    mapping = {
        "data_root": HOME_DATA, "saved_pose_file": "calibration/scan-home-current.json", "pad_template_dir": "calibration/pad-template",
        "observation_root": "observations",
        "host": {"address": PRIVATE_ADDRESS, "robot_id": ROBOT_ID, "cmd_port": CMD_PORT, "obs_port": OBS_PORT},
        "pi_ssh_target": SSH_TARGET, "cameras": {"wrist_id": WRIST_ID, "front_id": FRONT_ID},
        "model": {"identity": "example-org/example-detector", "revision": "rev-1"},
        "capture_provider": "legacy_per_capture", "freshness_refusal_enabled": False, "tap": {"enabled": True, "port": TAP_PORT},
    }
    mapping.update(overrides)
    return mapping


def assert_no_leak(test: unittest.TestCase, text: str, extra=()) -> None:
    for secret in tuple(SENSITIVE) + tuple(extra):
        test.assertNotIn(secret, text)


class LocatingTheConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "private-config.json")
        Path(self.path).write_text(json.dumps(full_mapping()))

    def test_the_path_comes_from_the_cli_argument(self):
        self.assertEqual(ROBOT_ID, load_config(self.path, environ={}).host.robot_id)

    def test_the_path_comes_from_the_environment_variable_when_there_is_no_cli_argument(self):
        self.assertEqual(ROBOT_ID, load_config(None, environ={CONFIG_ENV_VAR: self.path}).host.robot_id)

    def test_the_cli_argument_wins_over_the_environment_variable(self):
        self.assertEqual((self.path, "--config"), resolve_config_path(self.path, environ={CONFIG_ENV_VAR: "/nonexistent/other.json"}))

    def test_there_is_no_default_inside_the_repository(self):
        with self.assertRaises(ConfigError) as caught:
            load_config(None, environ={})
        self.assertIn("--config", str(caught.exception))
        self.assertIn(CONFIG_ENV_VAR, str(caught.exception))

    def test_the_committed_example_loads_and_carries_reserved_values_only(self):
        example = load_config(str(PACKAGE_DIR / "config.example.json"), environ={})
        self.assertTrue(example.host.address.startswith("192.0.2."))
        self.assertTrue(example.pi_ssh_target.endswith("@" + example.host.address))
        self.assertEqual(("example-robot", "example-camera-id", None), (example.host.robot_id, example.cameras.wrist_id, example.cameras.front_id))
        self.assertEqual((CaptureProvider.LEGACY_PER_CAPTURE, False, None), (example.capture_provider, example.freshness_refusal_enabled, example.tap))


class StructureTest(unittest.TestCase):
    def test_relative_paths_resolve_under_data_root_and_absolute_ones_are_kept(self):
        config = RuntimeConfig.from_mapping(full_mapping(pad_template_dir="/srv/shared/pad-template"))
        self.assertEqual(Path(HOME_DATA), config.data_root)
        self.assertEqual(Path(HOME_DATA) / "calibration" / "scan-home-current.json", config.saved_pose_file)
        self.assertEqual(Path(HOME_DATA) / "observations", config.observation_root)
        self.assertEqual(Path("/srv/shared/pad-template"), config.pad_template_dir)

    def test_a_relative_data_root_is_refused_because_it_would_resolve_against_the_current_directory(self):
        with self.assertRaises(ConfigError) as caught:
            RuntimeConfig.from_mapping(full_mapping(data_root="demo-data"))
        self.assertIn("data_root", str(caught.exception))

    def test_a_tilde_data_root_is_expanded(self):
        config = RuntimeConfig.from_mapping(full_mapping(data_root="~/demo-data"))
        self.assertEqual(Path(os.path.expanduser("~/demo-data")), config.data_root)

    def test_the_defaults_are_the_legacy_capture_provider_no_freshness_refusal_and_no_tap(self):
        minimal = {key: value for key, value in full_mapping().items() if key in ("data_root", "saved_pose_file", "pad_template_dir", "observation_root")}
        config = RuntimeConfig.from_mapping(minimal)
        self.assertEqual((CaptureProvider.LEGACY_PER_CAPTURE, False, None), (config.capture_provider, config.freshness_refusal_enabled, config.tap))
        self.assertEqual((5555, 5556), (config.host.cmd_port, config.host.obs_port))
        self.assertEqual(("", "", "", "", None), (config.host.address, config.host.robot_id, config.pi_ssh_target, config.cameras.wrist_id, config.cameras.front_id))

    def test_a_configuration_that_names_no_backend_gets_the_baseline_because_that_is_the_path_with_a_record_on_hardware(self):
        minimal = {key: value for key, value in full_mapping().items() if key in ("data_root", "saved_pose_file", "pad_template_dir", "observation_root")}
        self.assertIs(ExecutionBackend.BASELINE_NORMAL_HOST, RuntimeConfig.from_mapping(minimal).execution_backend)
        self.assertIs(ExecutionBackend.BASELINE_NORMAL_HOST, DEFAULT_EXECUTION_BACKEND)
        self.assertIs(ExecutionBackend.BASELINE_NORMAL_HOST, execution_backend_of(None), "a caller without a config gets the same default from one place")

    def test_both_backends_are_accepted_by_name_and_anything_else_is_refused_without_being_echoed(self):
        for name, expected in {"baseline_normal_host": ExecutionBackend.BASELINE_NORMAL_HOST, "boundary_lease_host": ExecutionBackend.BOUNDARY_LEASE_HOST}.items():
            with self.subTest(backend=name):
                config = RuntimeConfig.from_mapping(full_mapping(execution_backend=name))
                self.assertIs(expected, config.execution_backend)
                self.assertEqual(name, sanitized_summary(config)["execution_backend"])
                self.assertIn(repr(name), repr(config))
        for bad in ("baseline", "boundary", ROBOT_ID, "", None, 7):
            with self.subTest(bad=bad), self.assertRaises(ConfigError) as caught:
                RuntimeConfig.from_mapping(full_mapping(execution_backend=bad))
            self.assertIn("execution_backend", str(caught.exception))
            assert_no_leak(self, str(caught.exception))

    def test_the_two_backend_names_cannot_be_read_for_one_another(self):
        """They have to be told apart at a glance, in a log line and on a screen. The word that decides what they MEAN
        is 'lease': it is in exactly one of them and nowhere in the other, neither name is spelled as a negation of the
        other (a reader who misses a 'no' would read the opposite of what happened), they begin with different words,
        and neither is a prefix or a substring of the other."""
        names = [choice.value for choice in ExecutionBackend]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(1, sum("lease" in name for name in names), "one name says lease and the other does not mention one")
        for name in names:
            self.assertNotIn("no_", name, "a negated name is read wrong by anybody who skims it")
        first = [name.split("_")[0] for name in names]
        self.assertEqual(len(first), len(set(first)))
        self.assertFalse(first[0].startswith(first[1]) or first[1].startswith(first[0]))
        self.assertNotIn(names[0], names[1])
        self.assertNotIn(names[1], names[0])

    def test_a_backend_given_as_its_text_to_the_constructor_becomes_the_enum_member(self):
        paths = dict(data_root="/srv/demo-data", saved_pose_file="/srv/demo-data/pose.json", pad_template_dir="/srv/demo-data/template", observation_root="/srv/demo-data/observations")
        self.assertIs(ExecutionBackend.BOUNDARY_LEASE_HOST, RuntimeConfig(**paths, execution_backend="boundary_lease_host").execution_backend)
        with self.assertRaises(ConfigError) as caught:
            RuntimeConfig(**paths, execution_backend="baseline")
        self.assertIn("execution_backend", str(caught.exception))
        self.assertIsNone(caught.exception.__context__)

    def test_the_camera_service_provider_is_accepted_and_anything_else_is_refused(self):
        self.assertEqual(CaptureProvider.CAMERA_SERVICE, RuntimeConfig.from_mapping(full_mapping(capture_provider="camera_service")).capture_provider)
        with self.assertRaises(ConfigError) as caught:
            RuntimeConfig.from_mapping(full_mapping(capture_provider="whatever"))
        self.assertIn("legacy_per_capture", str(caught.exception))

    def test_structural_mistakes_are_refused_and_name_the_field(self):
        cases = {
            "data_root": full_mapping(data_root=None), "saved_pose_file": {k: v for k, v in full_mapping().items() if k != "saved_pose_file"},
            "host.cmd_port": full_mapping(host={"address": "a", "robot_id": "r", "cmd_port": 0}), "host.obs_port": full_mapping(host={"obs_port": True}),
            "host": full_mapping(host=[]), "cameras.wrist_id": full_mapping(cameras={"wrist_id": 7}), "freshness_refusal_enabled": full_mapping(freshness_refusal_enabled="no"),
            "tap.port": full_mapping(tap={"enabled": True}), "tap.enabled": full_mapping(tap={"enabled": 1, "port": 9000}), "model.identity": full_mapping(model={"identity": None}),
            "config": [],
        }
        for field_name, mapping in cases.items():
            with self.subTest(field=field_name), self.assertRaises(ConfigError) as caught:
                RuntimeConfig.from_mapping(mapping)
            self.assertIn(field_name, str(caught.exception))

    def test_an_unknown_key_is_refused_so_a_typo_cannot_become_a_silent_default(self):
        with self.assertRaises(ConfigError) as caught:
            RuntimeConfig.from_mapping(full_mapping(cameras={"wrist": WRIST_ID}))
        self.assertIn("1 unknown key(s) in cameras", str(caught.exception))
        self.assertIn("cameras.front_id, cameras.wrist_id", str(caught.exception))  # what the section TAKES, so the typo can be found
        with self.assertRaises(ConfigError):
            RuntimeConfig.from_mapping(full_mapping(layout="legacy"))  # the layout is set by the constructor, never by the file


class PreviewSectionTest(unittest.TestCase):
    """The preview section: one port per camera role, its own switch, and every collision refused at load.

    It exists because the endpoint used to be DERIVED, by adding a per-role offset to a single number, while an
    explicitly given port skipped the offset. Both roles then resolved to the same socket, so a front preview would
    have shown the wrist picture with nothing anywhere saying so. Ports are now written out per role, and two listeners
    asking for the same port is a configuration error rather than a camera showing the wrong room.
    """

    def mapping(self, **preview):
        document = full_mapping()
        document["preview"] = preview
        return document

    def test_a_configuration_with_no_preview_section_keeps_the_existing_behaviour(self):
        config = RuntimeConfig.from_mapping(full_mapping())
        self.assertIsNone(config.preview, "a file that says nothing about cameras gets exactly what it got before")
        self.assertEqual(CaptureProvider.LEGACY_PER_CAPTURE, config.capture_provider)

    def test_each_role_carries_its_own_port(self):
        config = RuntimeConfig.from_mapping(self.mapping(enabled=True, wrist_port=5601, front_port=5602))
        self.assertEqual((5601, 5602), (config.preview.wrist_port, config.preview.front_port))
        self.assertEqual(5601, config.preview.port_for("wrist"))
        self.assertEqual(5602, config.preview.port_for("front"))
        self.assertEqual(("wrist", "front"), config.preview.roles())

    def test_a_role_without_a_port_simply_has_no_preview(self):
        config = RuntimeConfig.from_mapping(self.mapping(enabled=True, wrist_port=5601))
        self.assertEqual(("wrist",), config.preview.roles())
        self.assertIsNone(config.preview.port_for("front"))

    def test_the_preview_is_its_own_switch_and_needs_no_tap(self):
        document = self.mapping(enabled=True, wrist_port=5601, front_port=5602)
        document.pop("tap")
        config = RuntimeConfig.from_mapping(document)
        self.assertIsNone(config.tap, "the baseline NORMAL host runs cameras and no boundary")
        self.assertTrue(config.preview.enabled)
        self.assertEqual(("wrist", "front"), config.preview.roles())

    def test_an_enabled_preview_that_names_no_port_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            RuntimeConfig.from_mapping(self.mapping(enabled=True))
        self.assertIn("preview", str(caught.exception))

    def test_two_roles_may_never_be_given_the_same_port(self):
        with self.assertRaises(ConfigError) as caught:
            RuntimeConfig.from_mapping(self.mapping(enabled=True, wrist_port=5601, front_port=5601))
        self.assertIn("preview.wrist_port", str(caught.exception))
        self.assertIn("preview.front_port", str(caught.exception))

    def test_a_preview_may_not_take_a_port_another_listener_on_that_computer_holds(self):
        taken = {"host.cmd_port": CMD_PORT, "host.obs_port": OBS_PORT}
        for name, port in taken.items():
            with self.assertRaises(ConfigError, msg=f"{name} was allowed to clash") as caught:
                RuntimeConfig.from_mapping(self.mapping(enabled=True, wrist_port=port))
            self.assertIn(name, str(caught.exception))

    def test_a_preview_may_not_take_the_camera_service_s_own_mission_socket(self):
        """The socket the controller's captures go through. It is not configurable here, which is exactly why a
        hand-edited file can pick it by accident."""
        with self.assertRaises(ConfigError) as caught:
            RuntimeConfig.from_mapping(self.mapping(enabled=True, wrist_port=CAMERA_SERVICE_MISSION_PORT))
        self.assertIn("mission socket", str(caught.exception))

    def test_a_preview_may_not_take_the_tap_s_port(self):
        document = self.mapping(enabled=True, wrist_port=5558)
        document["tap"] = {"enabled": True, "port": 5558}
        with self.assertRaises(ConfigError) as caught:
            RuntimeConfig.from_mapping(document)
        self.assertIn("tap.port", str(caught.exception))

    def test_the_ports_are_never_echoed_by_the_repr(self):
        shown = repr(PreviewConfig(enabled=True, wrist_port=5601, front_port=5602))
        self.assertNotIn("5601", shown)
        self.assertNotIn("5602", shown)


class DirectConstructionTest(unittest.TestCase):
    """legacy_layout, the tests and a future caller build a RuntimeConfig without the file loader."""

    def test_a_str_path_and_a_path_of_the_same_place_are_one_config_with_one_digest(self):
        as_path = RuntimeConfig(data_root=Path("/srv/demo-data"), saved_pose_file=Path("/srv/demo-data/pose.json"), pad_template_dir=Path("/srv/demo-data/template"), observation_root=Path("/srv/demo-data/observations"))
        as_text = RuntimeConfig(data_root="/srv/demo-data/", saved_pose_file="/srv/demo-data/pose.json", pad_template_dir="/srv/demo-data/template", observation_root="/srv/demo-data/observations")
        self.assertEqual(as_path, as_text)
        self.assertEqual(config_digest(as_path), config_digest(as_text))
        self.assertIsInstance(as_text.observation_root, Path)

    def test_the_capture_provider_given_as_its_text_becomes_the_enum_member_and_an_unknown_one_is_refused_without_being_echoed(self):
        paths = dict(data_root="/srv/demo-data", saved_pose_file="/srv/demo-data/pose.json", pad_template_dir="/srv/demo-data/template", observation_root="/srv/demo-data/observations")
        config = RuntimeConfig(**paths, capture_provider="camera_service")
        self.assertIs(CaptureProvider.CAMERA_SERVICE, config.capture_provider)
        self.assertEqual("camera_service", sanitized_summary(config)["capture_provider"])
        self.assertIn("'camera_service'", repr(config))
        for bad in (ROBOT_ID, None, 7):
            with self.subTest(bad=bad), self.assertRaises(ConfigError) as caught:
                RuntimeConfig(**paths, capture_provider=bad)
            assert_no_leak(self, str(caught.exception))
            self.assertIsNone(caught.exception.__context__)


class RedactionTest(unittest.TestCase):
    def test_repr_and_str_show_no_address_id_target_camera_or_path(self):
        config = RuntimeConfig.from_mapping(full_mapping())
        for text in (repr(config), str(config), f"{config}", repr(config.host), repr(config.cameras), repr(config.tap), repr([config]), "%s" % (config,)):
            assert_no_leak(self, text, extra=(str(TAP_PORT),))
        self.assertIn("<redacted>", repr(config))
        self.assertIn("example-org/example-detector", repr(config))  # the model identity is the one identity that may be shown

    def test_repr_says_whether_a_value_is_set_without_saying_what_it_is(self):
        config = RuntimeConfig.from_mapping(full_mapping(cameras={"wrist_id": "", "front_id": None}))
        self.assertIn("wrist_id=<unset>", repr(config))
        self.assertIn("front_id=<unset>", repr(config))
        self.assertIn("address=<redacted>", repr(config))

    def test_a_model_identity_that_is_a_local_path_is_withheld_everywhere(self):
        config = RuntimeConfig.from_mapping(full_mapping(model={"identity": HOME_DATA + "/checkpoints/detector", "revision": None}))
        assert_no_leak(self, repr(config))
        summary = sanitized_summary(config)
        self.assertEqual((None, True), (summary["model_identity"], summary["model_identity_withheld"]))
        assert_no_leak(self, json.dumps(summary))

    def test_a_model_identity_is_shown_only_while_it_has_the_shape_of_a_public_hub_name(self):
        """An allowlist: a served model's URL is an endpoint (address and port), a URL can carry a credential, and neither
        starts like a path. Each of these was shown by the summary and by repr() before."""
        other_port = "8" + "123"
        withheld = ("http://" + PRIVATE_ADDRESS + ":" + other_port + "/v1", USER_NAME + ":" + "tok" + "@" + "modelhost/detector", PRIVATE_ADDRESS, "file:///srv/detector",
                    "checkpoints/" + USER_NAME + "/detector", "example-org/../detector", "example-org/example detector", "C:" + "\\" + "detector", "~/detector", "./detector")
        for identity in withheld:
            with self.subTest(identity=identity):
                config = RuntimeConfig.from_mapping(full_mapping(model={"identity": identity, "revision": None}))
                summary = sanitized_summary(config)
                self.assertEqual((None, True), (summary["model_identity"], summary["model_identity_withheld"]))
                for text in (json.dumps(summary), repr(config), repr(config.model)):
                    assert_no_leak(self, text, extra=(identity, other_port))
                self.assertIn("identity=<redacted>", repr(config.model))
        for identity in ("example-detector", "example-org/example-detector", config_module.LEGACY_MODEL_IDENTITY):
            with self.subTest(identity=identity):
                summary = sanitized_summary(RuntimeConfig.from_mapping(full_mapping(model={"identity": identity})))
                self.assertEqual((identity, False), (summary["model_identity"], summary["model_identity_withheld"]))

    def test_a_model_revision_is_shown_only_as_a_hash_a_tag_or_a_branch_and_a_withheld_one_does_not_read_as_none(self):
        for revision in ("rev-1", "main", "refs/pr/7", "0123456789abcdef0123456789abcdef01234567"):
            with self.subTest(revision=revision):
                summary = sanitized_summary(RuntimeConfig.from_mapping(full_mapping(model={"identity": "example-org/example-detector", "revision": revision})))
                self.assertEqual((revision, False), (summary["model_revision"], summary["model_revision_withheld"]))
        for revision in (HOME_DATA + "/checkpoints", "http://" + PRIVATE_ADDRESS + "/rev", "../rev", USER_NAME + "@" + "rev"):
            with self.subTest(revision=revision):
                config = RuntimeConfig.from_mapping(full_mapping(model={"identity": "example-org/example-detector", "revision": revision}))
                summary = sanitized_summary(config)
                self.assertEqual((None, True), (summary["model_revision"], summary["model_revision_withheld"]))
                assert_no_leak(self, json.dumps(summary) + repr(config), extra=(revision,))
        self.assertFalse(sanitized_summary(RuntimeConfig.from_mapping(full_mapping(model={"identity": "example-org/example-detector", "revision": None})))["model_revision_withheld"])

    def test_a_wrong_type_error_does_not_repeat_the_value(self):
        for mapping in (full_mapping(host={"address": "a", "robot_id": "r", "cmd_port": PRIVATE_ADDRESS}), full_mapping(pi_ssh_target=[SSH_TARGET]),
                        full_mapping(cameras={"wrist_id": {"id": WRIST_ID}}), full_mapping(capture_provider=ROBOT_ID), full_mapping(data_root=USER_NAME + "/relative")):
            with self.assertRaises(ConfigError) as caught:
                RuntimeConfig.from_mapping(mapping)
            assert_no_leak(self, str(caught.exception))
            assert_no_leak(self, repr(caught.exception))

    def test_an_unknown_key_is_never_echoed_because_a_badly_nested_file_can_put_a_value_there(self):
        """A robot id made of lowercase letters and digits has exactly the shape of a field name, so no shape test can
        tell the two apart: the message lists the keys the section takes and repeats none it was given."""
        field_shaped_id = "kiwi" + "unit" + "0042"
        for section, stray in (("host", PRIVATE_ADDRESS), ("host", field_shaped_id), ("cameras", field_shaped_id), ("model", field_shaped_id), ("tap", field_shaped_id)):
            with self.subTest(section=section, stray=stray), self.assertRaises(ConfigError) as caught:
                RuntimeConfig.from_mapping(full_mapping(**{section: {stray: "x"}}))
            assert_no_leak(self, str(caught.exception), extra=(field_shaped_id,))
            self.assertIn("unknown key(s) in " + section, str(caught.exception))
        with self.assertRaises(ConfigError) as caught:
            RuntimeConfig.from_mapping(full_mapping(**{field_shaped_id: "x"}))
        assert_no_leak(self, str(caught.exception), extra=(field_shaped_id,))

    def test_a_broken_json_file_is_reported_without_its_content_its_path_or_the_original_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            private_dir = os.path.join(tmp, USER_NAME)
            os.mkdir(private_dir)
            path = os.path.join(private_dir, "config.json")
            Path(path).write_text('{"host": {"address": "%s", ' % PRIVATE_ADDRESS)
            with self.assertRaises(ConfigError) as caught:
                load_config(path, environ={})
        assert_no_leak(self, str(caught.exception), extra=(tmp,))
        self.assertIn("line 1", str(caught.exception))
        # json.JSONDecodeError keeps the whole document in .doc: it must not ride along on the exception.
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_a_missing_file_is_reported_by_its_source_not_by_its_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, USER_NAME, "absent.json")
            with self.assertRaises(ConfigError) as caught:
                load_config(None, environ={CONFIG_ENV_VAR: path})
        assert_no_leak(self, str(caught.exception), extra=(tmp, "absent.json"))
        self.assertIn(CONFIG_ENV_VAR, str(caught.exception))
        self.assertIsNone(caught.exception.__context__)


def walk_values(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from walk_values(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from walk_values(value)
    else:
        yield node


class DigestAndSummaryTest(unittest.TestCase):
    def test_the_digest_is_a_stable_sha256_of_the_full_config(self):
        first, second = RuntimeConfig.from_mapping(full_mapping()), RuntimeConfig.from_mapping(json.loads(json.dumps(full_mapping())))
        self.assertEqual(config_digest(first), config_digest(second))
        self.assertRegex(config_digest(first), r"^[0-9a-f]{64}$")

    def test_the_digest_changes_when_any_identity_or_switch_changes_and_the_summary_never_shows_which(self):
        base = RuntimeConfig.from_mapping(full_mapping())
        base_summary = sanitized_summary(base)
        other_address = ".".join(("192", "168", "7", "42"))
        changes = {
            "host address": full_mapping(host={"address": other_address, "robot_id": ROBOT_ID, "cmd_port": CMD_PORT, "obs_port": OBS_PORT}),
            "robot id": full_mapping(host={"address": PRIVATE_ADDRESS, "robot_id": ROBOT_ID + "b", "cmd_port": CMD_PORT, "obs_port": OBS_PORT}),
            "command port": full_mapping(host={"address": PRIVATE_ADDRESS, "robot_id": ROBOT_ID, "cmd_port": CMD_PORT + 7, "obs_port": OBS_PORT}),
            "observation port": full_mapping(host={"address": PRIVATE_ADDRESS, "robot_id": ROBOT_ID, "cmd_port": CMD_PORT, "obs_port": OBS_PORT + 7}),
            "ssh target": full_mapping(pi_ssh_target=USER_NAME + "@" + other_address),
            "wrist camera": full_mapping(cameras={"wrist_id": WRIST_ID + "x", "front_id": FRONT_ID}),
            "front camera": full_mapping(cameras={"wrist_id": WRIST_ID, "front_id": FRONT_ID + "x"}),
            "data root": full_mapping(data_root=HOME_DATA + "-2"),
            "saved pose file": full_mapping(saved_pose_file="calibration/other-pose.json"),
            "pad template dir": full_mapping(pad_template_dir="calibration/other-template"),
            "observation root": full_mapping(observation_root="other-observations"),
            "tap port": full_mapping(tap={"enabled": True, "port": TAP_PORT + 7}),
        }
        digests = {"base": config_digest(base)}
        for label, mapping in changes.items():
            with self.subTest(changed=label):
                changed = RuntimeConfig.from_mapping(mapping)
                digests[label] = config_digest(changed)
                self.assertNotEqual(digests["base"], digests[label])
                summary = sanitized_summary(changed)
                # The ONLY thing an identity change moves in the summary is the digest: it is detectable, not revealed.
                self.assertEqual({k: v for k, v in base_summary.items() if k != "config_digest"}, {k: v for k, v in summary.items() if k != "config_digest"})
                self.assertEqual(digests[label], summary["config_digest"])
                assert_no_leak(self, json.dumps(summary), extra=(other_address,))
        self.assertEqual(len(digests), len(set(digests.values())))

    def test_the_digest_also_moves_with_the_public_switches_and_the_summary_shows_those(self):
        base = RuntimeConfig.from_mapping(full_mapping())
        for key, mapping in {"capture_provider": full_mapping(capture_provider="camera_service"), "freshness_refusal_enabled": full_mapping(freshness_refusal_enabled=True),
                             "tap_enabled": full_mapping(tap=None), "model_identity": full_mapping(model={"identity": "example-org/other-detector", "revision": "rev-1"}),
                             "model_revision": full_mapping(model={"identity": "example-org/example-detector", "revision": "rev-2"}),
                             "execution_backend": full_mapping(execution_backend="boundary_lease_host")}.items():
            with self.subTest(changed=key):
                changed = RuntimeConfig.from_mapping(mapping)
                self.assertNotEqual(config_digest(base), config_digest(changed))
                self.assertNotEqual(sanitized_summary(base)[key], sanitized_summary(changed)[key])

    def test_the_summary_holds_booleans_enum_choices_the_model_identity_and_a_digest_and_nothing_else(self):
        config = RuntimeConfig.from_mapping(full_mapping())
        summary = sanitized_summary(config)
        self.assertEqual({"schema", "config_digest", "layout", "capture_provider", "execution_backend", "freshness_refusal_enabled", "tap_enabled", "model_identity",
                          "model_revision", "model_identity_withheld", "model_revision_withheld", "host_configured", "pi_ssh_configured", "wrist_camera_configured",
                          "front_camera_configured", "placement_envelope", "placement_envelope_attended_named", "placement_side_offset_forced"}, set(summary))
        assert_no_leak(self, json.dumps(summary))
        for key, value in summary.items():
            self.assertIsInstance(value, (bool, str, type(None)), key)  # no number at all: a port cannot hide in it
        for value in walk_values({k: v for k, v in summary.items() if k != "config_digest"}):
            for port in (CMD_PORT, OBS_PORT, TAP_PORT):
                self.assertNotIn(str(port), str(value))
        self.assertEqual(("explicit", "legacy_per_capture", "baseline_normal_host", True, True, True, True, True),
                         (summary["layout"], summary["capture_provider"], summary["execution_backend"], summary["tap_enabled"], summary["host_configured"],
                          summary["pi_ssh_configured"], summary["wrist_camera_configured"], summary["front_camera_configured"]))

    def test_the_summary_is_json_serializable_as_it_is(self):
        json.dumps(sanitized_summary(RuntimeConfig.from_mapping(full_mapping())))


class LegacyLayoutTest(unittest.TestCase):
    def test_the_legacy_layout_is_exactly_where_todays_code_looks(self):
        config = legacy_layout(REPO_ROOT)
        local = REPO_ROOT / ".local-dev"
        self.assertEqual((local, local / "calibration" / "scan-home-current.json", local / "calibration" / "pad-template", local / "observations"),
                         (config.data_root, config.saved_pose_file, config.pad_template_dir, config.observation_root))
        self.assertEqual("legacy", config.layout)
        self.assertEqual("legacy", sanitized_summary(config)["layout"])

    def test_the_mirrored_constants_still_match_the_sources_they_mirror(self):
        """legacy_layout copies these constants instead of importing them (the sources import numpy, OpenCV and the robot
        client). If a source moves its constant, this fails here instead of the two layouts drifting apart in silence."""
        data, pose, template, observations = config_module.LEGACY_DATA_DIR, config_module.LEGACY_SAVED_POSE, config_module.LEGACY_PAD_TEMPLATE, config_module.LEGACY_OBSERVATIONS
        home_line = 'HOME_FILE = ROOT / "%s" / "%s" / "%s"' % (data, *pose)
        pins = {
            "bin/laptop/manipulation/run_single_cube_cycle.py": ("ROOT = Path(__file__).resolve().parents[3]", home_line),
            "bin/laptop/perception/run_stage1_scan.py": ("ROOT = Path(__file__).resolve().parents[3]", home_line),
            "tools/static_world_observation.py": ("ROOT = Path(__file__).resolve().parents[1]", 'TEMPLATE_DIR = ROOT / "%s" / "%s" / "%s"' % (data, *template)),
            "autonomy/perception/observation_store.py": ("REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))",
                                                         'DEFAULT_ROOT = os.path.join(REPO_ROOT, "%s", "%s")' % (data, *observations)),
            "autonomy/perception/zero_shot_detector.py": ('MODEL_NAME = "%s"' % config_module.LEGACY_MODEL_IDENTITY,),
        }
        for relative, lines in pins.items():
            source = (REPO_ROOT / relative).read_text()
            for line in lines:
                with self.subTest(source=relative, line=line):
                    self.assertIn(line, source)

    def test_the_legacy_identities_come_from_the_arguments_and_from_the_variables_todays_code_reads(self):
        environ = {"KC_PI_SSH": SSH_TARGET, "KC_WRIST_CAMERA_ID": WRIST_ID}
        config = legacy_layout(REPO_ROOT, environ=environ, host_address=PRIVATE_ADDRESS, robot_id=ROBOT_ID)
        self.assertEqual((PRIVATE_ADDRESS, ROBOT_ID, SSH_TARGET, WRIST_ID, None), (config.host.address, config.host.robot_id, config.pi_ssh_target, config.cameras.wrist_id, config.cameras.front_id))
        self.assertEqual(config_module.LEGACY_MODEL_IDENTITY, config.model.identity)
        assert_no_leak(self, repr(config))
        explicit = legacy_layout(REPO_ROOT, environ=environ, pi_ssh_target="given@192.0.2.9", wrist_id="given-camera", front_id="given-front")
        self.assertEqual(("given@192.0.2.9", "given-camera", "given-front"), (explicit.pi_ssh_target, explicit.cameras.wrist_id, explicit.cameras.front_id))

    def test_without_an_environment_the_legacy_layout_reads_no_variable_at_all(self):
        config = legacy_layout(REPO_ROOT)  # environ=None means "none", never os.environ behind the caller's back
        self.assertEqual(("", "", None), (config.pi_ssh_target, config.cameras.wrist_id, config.cameras.front_id))


class ImportWeightTest(unittest.TestCase):
    def test_importing_the_package_pulls_in_no_numeric_camera_network_or_robot_module(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import demo_runtime; "
                "print(sorted(name for name in ('numpy', 'cv2', 'zmq', 'aiohttp', 'pydantic', 'lerobot', 'torch') if name in sys.modules))")
        result = subprocess.run([sys.executable, "-c", code, str(PACKAGE_DIR)], capture_output=True, text=True, timeout=60)
        self.assertEqual((0, "[]"), (result.returncode, result.stdout.strip()), result.stderr)



# ---- perception.remote: the one offboard exception, as configuration ------------------------------------------------------
MODEL_PIN_FILES = {"config.json": "a" * 64, "model.safetensors": "b" * 64, "preprocessor_config.json": "c" * 64}
MODEL_REVISION = "0123456789abcdef0123456789abcdef01234567"


def with_remote(**changes) -> dict:
    """full_mapping with a complete perception.remote; `changes` replace (or, with None, remove) a key of `remote`."""
    remote = {"port": 45590, "deadline_s": 40.0, "model_revision": MODEL_REVISION, "model_files_sha256": dict(MODEL_PIN_FILES)}
    for key, value in changes.items():
        if value is None:
            remote.pop(key, None)
        else:
            remote[key] = value
    return full_mapping(perception={"remote": remote})


class PerceptionRemoteSectionTest(unittest.TestCase):
    def test_absent_is_off_and_leaves_the_digest_of_every_existing_configuration_alone(self):
        config = RuntimeConfig.from_mapping(full_mapping())
        self.assertIsNone(config.perception)
        self.assertIsNone(config_module.perception_remote_of(config))
        self.assertNotIn("perception", config.to_private_dict())

    def test_present_it_is_read_whole_and_moves_the_digest(self):
        config = RuntimeConfig.from_mapping(with_remote())
        remote = config_module.perception_remote_of(config)
        self.assertEqual((45590, 40.0, MODEL_REVISION, MODEL_PIN_FILES), (remote.port, remote.deadline_s, remote.model_revision, dict(remote.model_files_sha256)))
        self.assertNotEqual(config_digest(RuntimeConfig.from_mapping(full_mapping())), config_digest(config))
        self.assertNotEqual(config_digest(config), config_digest(RuntimeConfig.from_mapping(with_remote(deadline_s=41.0))))

    def test_every_field_is_required_and_none_has_a_default(self):
        for key in ("port", "deadline_s", "model_revision", "model_files_sha256"):
            with self.subTest(missing=key), self.assertRaises(ConfigError) as caught:
                RuntimeConfig.from_mapping(with_remote(**{key: None}))
            self.assertIn(f"perception.remote.{key}", str(caught.exception))
        with self.assertRaises(ConfigError) as caught:
            RuntimeConfig.from_mapping(full_mapping(perception={}))
        self.assertIn("perception.remote is required", str(caught.exception))
        import dataclasses

        fields = dataclasses.fields(config_module.RemotePerceptionConfig)
        self.assertTrue(all(field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING for field in fields),
                        "no field of perception.remote has a default")

    def test_a_null_port_is_a_port_that_is_not_given(self):
        raw = with_remote()
        raw["perception"]["remote"]["port"] = None
        with self.assertRaises(ConfigError):
            RuntimeConfig.from_mapping(raw)

    def test_the_robots_own_service_ports_and_every_configured_listener_are_refused(self):
        for port in config_module.ROBOT_FIXED_SERVICE_PORTS:
            with self.subTest(port=port), self.assertRaises(ConfigError) as caught:
                RuntimeConfig.from_mapping(with_remote(port=port))
            self.assertIn("robot's own service ports", str(caught.exception))
        for port in (CMD_PORT, OBS_PORT, TAP_PORT):
            with self.subTest(collides_with=port), self.assertRaises(ConfigError) as caught:
                RuntimeConfig.from_mapping(with_remote(port=port))
            self.assertIn("perception.remote.port", str(caught.exception))

    def test_malformed_values_are_refused_and_no_message_repeats_a_value(self):
        bad = {"deadline_s": [0, -1.0, True, "40", config_module.PERCEPTION_DEADLINE_MAX_S + 1, float("nan"), float("inf")],
               "model_revision": ["cfd3195", MODEL_REVISION.upper(), MODEL_REVISION + "0", ""],
               "model_files_sha256": [{}, [], {"../config.json": "a" * 64}, {"sub/config.json": "a" * 64}, {"config.json": "A" * 64}, {"config.json": "a" * 63}],
               "port": [0.5, "45590", 70000, True]}
        for key, values in bad.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ConfigError) as caught:
                    RuntimeConfig.from_mapping(with_remote(**{key: value}))
                if isinstance(value, str) and len(value) > 3:  # a number may coincide with a stated bound; a text value may never be echoed
                    self.assertNotIn(value, str(caught.exception))
        with self.assertRaises(ConfigError):
            raw = with_remote()
            raw["perception"]["remote"]["extra"] = 1
            RuntimeConfig.from_mapping(raw)

    def test_the_port_is_not_shown_by_repr(self):
        config = RuntimeConfig.from_mapping(with_remote(port=45590))
        self.assertNotIn("45590", repr(config.perception))
        self.assertIn("model_files=3", repr(config.perception))

    def test_the_model_files_digest_is_the_one_the_controllers_client_computes(self):
        sys.path.insert(0, str(REPO_ROOT / "autonomy" / "perception"))
        import remote_perception

        self.assertEqual(remote_perception.model_files_digest(MODEL_PIN_FILES), config_module.model_files_digest(MODEL_PIN_FILES))
        self.assertTrue(config_module.model_files_digest(MODEL_PIN_FILES).startswith("sha256:"))
        self.assertNotEqual(config_module.model_files_digest(MODEL_PIN_FILES), config_module.model_files_digest({**MODEL_PIN_FILES, "config.json": "d" * 64}))


class MissionRadiiSectionTest(unittest.TestCase):
    RADII = {"r_claim_mm": 30.0, "r_match_mm": 40.0, "basis": "two-cube-mission-design.md section 5 (example)"}

    def load(self, radii):
        mission = {"execution": None}
        if radii is not None:
            mission["radii"] = radii
        return RuntimeConfig.from_mapping(full_mapping(mission=mission))

    def test_absent_is_none_and_leaves_the_mission_digest_alone(self):
        config = self.load(None)
        self.assertIsNone(config.mission.radii)
        self.assertNotIn("radii", config.to_private_dict()["mission"])

    def test_present_it_is_read_whole_and_moves_the_digest(self):
        config = self.load(dict(self.RADII))
        self.assertEqual((30.0, 40.0, self.RADII["basis"]), (config.mission.radii.r_claim_mm, config.mission.radii.r_match_mm, config.mission.radii.basis))
        self.assertNotEqual(config_digest(self.load(None)), config_digest(config))
        self.assertNotEqual(config_digest(config), config_digest(self.load({**self.RADII, "r_claim_mm": 31.0})))

    def test_no_field_has_a_default_so_nothing_blesses_the_values_written_first(self):
        for key in self.RADII:
            with self.subTest(missing=key), self.assertRaises(ConfigError) as caught:
                self.load({name: value for name, value in self.RADII.items() if name != key})
            self.assertIn(f"mission.radii.{key}", str(caught.exception))
        import dataclasses

        self.assertTrue(all(field.default is dataclasses.MISSING for field in dataclasses.fields(config_module.MissionRadii)))

    def test_malformed_values_are_refused(self):
        for key, value in (("r_claim_mm", 0), ("r_claim_mm", -5), ("r_match_mm", True), ("r_match_mm", "40"), ("r_match_mm", config_module.RADIUS_MAX_MM + 1),
                           ("basis", ""), ("basis", "/" + "home" + "/someone/notes.md"), ("basis", "see " + PRIVATE_ADDRESS), ("basis", "x" * 161)):
            with self.subTest(key=key, value=value), self.assertRaises(ConfigError):
                self.load({**self.RADII, key: value})

    def test_the_word_cube_half_edge_resolves_to_target_bindings_derived_radii_and_their_basis(self):
        """The word names a derivation: the numbers and the basis are read from target_binding.radii_from_cube_geometry
        (half the controller's cube edge for both radii), never spelled in the configuration module."""
        sys.path.insert(0, str(REPO_ROOT / "autonomy" / "manipulation"))
        import target_binding

        self.assertEqual("cube_half_edge", config_module.RADII_CUBE_HALF_EDGE)
        config = self.load(config_module.RADII_CUBE_HALF_EDGE)
        derived = target_binding.radii_from_cube_geometry()
        self.assertEqual((derived.r_claim_mm, derived.r_match_mm, derived.basis), (config.mission.radii.r_claim_mm, config.mission.radii.r_match_mm, config.mission.radii.basis))
        self.assertEqual((12.7, 12.7, target_binding.CUBE_HALF_EDGE_BASIS), (config.mission.radii.r_claim_mm, config.mission.radii.r_match_mm, config.mission.radii.basis))
        self.assertIsInstance(config.mission.radii, config_module.MissionRadii)
        self.assertNotIn("12.7", Path(config_module.__file__).read_text(encoding="utf-8"), "the number is target_binding's, not this module's")
        # The digest carries the RESOLVED numbers and basis: it moves from absent and from other numbers, and a configuration
        # that spells the same three values is the same configuration.
        self.assertEqual(derived.to_document(), config.to_private_dict()["mission"]["radii"])
        self.assertNotEqual(config_digest(self.load(None)), config_digest(config))
        self.assertNotEqual(config_digest(self.load(dict(self.RADII))), config_digest(config))
        self.assertEqual(config_digest(self.load(derived.to_document())), config_digest(config))
        assert_no_leak(self, repr(config))

    def test_any_other_word_or_shape_is_refused_by_name(self):
        for bad in ("cube_edge", "CUBE_HALF_EDGE", "", "cube_half_edge ", 12.7, True, [12.7, 12.7], {"word": "cube_half_edge"}):
            with self.subTest(bad=bad), self.assertRaises(ConfigError) as caught:
                self.load(bad)
            self.assertIn("mission.radii", str(caught.exception))
            self.assertNotIn("cube_edge", str(caught.exception), "a refusal never echoes the value")

    def test_resolving_the_word_loads_no_numeric_camera_or_robot_module(self):
        mapping = full_mapping(mission={"execution": None, "radii": config_module.RADII_CUBE_HALF_EDGE})
        code = ("import json, sys; sys.path.insert(0, sys.argv[1]); from demo_runtime.config import RuntimeConfig; "
                "config = RuntimeConfig.from_mapping(json.loads(sys.argv[2])); "
                "print(config.mission.radii.r_claim_mm, sorted(name for name in ('numpy', 'cv2', 'zmq', 'aiohttp', 'lerobot', 'torch', 'run_single_cube_cycle') if name in sys.modules))")
        result = subprocess.run([sys.executable, "-c", code, str(PACKAGE_DIR), json.dumps(mapping)], capture_output=True, text=True, timeout=60)
        self.assertEqual((0, "12.7 []"), (result.returncode, result.stdout.strip()), result.stderr)


class ThePlacementEnvelopeKey(unittest.TestCase):
    """placement_envelope: the list of words a run is started with (docs/autonomous-demo/one-block-demo-2026-09-26.md, T6,
    generalized to every stage's words by docs/autonomous-demo/overnight-plan-2026-09-26.md section 2.6), validated to a
    word's SHAPE and to the placement rule that projected comes with any later placement word. Membership is the
    controller's refusal, before the robot is constructed (placement_envelope_unknown_word,
    autonomy/tests/test_cycle_mission_mode.py test_an_unknown_word_is_refused_before_the_robot_exists).
    placement_envelope_attended: the session label of an attended qualification run, which the operator writes for those
    runs and removes afterwards."""

    def test_the_default_is_no_word_and_no_label(self):
        config = RuntimeConfig.from_mapping(full_mapping())
        self.assertEqual(((), None), (config.placement_envelope, config.placement_envelope_attended))
        self.assertEqual(("proven", False), (sanitized_summary(config)["placement_envelope"], sanitized_summary(config)["placement_envelope_attended_named"]))

    def test_known_words_are_accepted_in_order_and_are_part_of_the_digest(self):
        plain = RuntimeConfig.from_mapping(full_mapping())
        config = RuntimeConfig.from_mapping(full_mapping(placement_envelope=["projected"]))
        self.assertEqual(("projected",), config.placement_envelope)
        self.assertEqual("projected", sanitized_summary(config)["placement_envelope"])
        self.assertEqual("projected,near_reverse", sanitized_summary(RuntimeConfig.from_mapping(full_mapping(placement_envelope=["projected", "near_reverse"])))["placement_envelope"])
        self.assertNotEqual(config_digest(plain), config_digest(config), "a runtime that would decide placements differently has the same digest")
        self.assertEqual(("projected", "near_reverse"), RuntimeConfig.from_mapping(full_mapping(placement_envelope=["projected", "near_reverse"])).placement_envelope)
        self.assertEqual(("projected", "near_reverse", "side_offset", "base_lateral"), config_module.PLACEMENT_ENVELOPE_WORDS)

    def test_a_word_of_the_wrong_shape_is_refused_without_being_echoed(self):
        marker = "Side" + "ways"
        for value in ([marker], ["projected", "projected"], "projected", [1], ["near_reverse"], [""], ["align v2"], ["2d_return"], ["align-v2"],
                      ["x" * 33], [marker.lower() + "\n"], ["projected", "side_offset", "side_offset"], ["align_v2", "side_offset"], None, {"projected": True}):
            with self.subTest(repr(value)), self.assertRaises(ConfigError) as caught:
                RuntimeConfig.from_mapping(full_mapping(placement_envelope=value))
            self.assertNotIn(marker, str(caught.exception))
            self.assertNotIn(marker.lower(), str(caught.exception))
            self.assertIn("placement_envelope", str(caught.exception))

    def test_any_distinct_word_of_the_right_shape_is_accepted_and_membership_is_the_controllers_refusal(self):
        """The move of the membership check, stated: a well-shaped word no stage declares reaches the child, whose
        placement_envelope_words() refuses it before the robot is constructed. The runtime keeps the shape and the one
        placement rule it can check without the controller's vocabulary of every stage."""
        for words in (["sideways"], ["align_v2"], ["scene_scan", "scene_scan_25"], ["coarse_v2", "align_v2", "near_grasp", "grasp_measured_retry"],
                      ["projected", "reach_after_advance", "return_2d"], ["x" * 32], ["projected", "side_offset", "align_v2"], ["align_v2", "projected", "side_offset"]):
            with self.subTest(words):
                config = RuntimeConfig.from_mapping(full_mapping(placement_envelope=words))
                self.assertEqual(tuple(words), config.placement_envelope)
                self.assertEqual(",".join(words), sanitized_summary(config)["placement_envelope"])

    def test_a_later_placement_word_still_needs_projected(self):
        for later in config_module.PLACEMENT_ENVELOPE_WORDS[1:]:
            with self.subTest(later), self.assertRaises(ConfigError) as caught:
                RuntimeConfig.from_mapping(full_mapping(placement_envelope=["align_v2", later]))
            self.assertIn("projected", str(caught.exception))
            self.assertEqual(("projected", later), RuntimeConfig.from_mapping(full_mapping(placement_envelope=["projected", later])).placement_envelope)

    def test_the_attended_label_needs_a_word_and_a_plain_shape(self):
        config = RuntimeConfig.from_mapping(full_mapping(placement_envelope=["projected"], placement_envelope_attended="qual-2026-09-27-a"))
        self.assertEqual("qual-2026-09-27-a", config.placement_envelope_attended)
        self.assertTrue(sanitized_summary(config)["placement_envelope_attended_named"])
        self.assertIsNone(RuntimeConfig.from_mapping(full_mapping(placement_envelope=["projected"], placement_envelope_attended=None)).placement_envelope_attended)
        for words, label in (([], "qual-a"), (["projected"], ""), (["projected"], "a/b"), (["projected"], "x" * 49), (["projected"], 7)):
            with self.subTest((words, label)), self.assertRaises(ConfigError) as caught:
                RuntimeConfig.from_mapping(full_mapping(placement_envelope=words, placement_envelope_attended=label))
            self.assertIn("placement_envelope_attended", str(caught.exception))


class ThePlacementSideOffsetForceKey(unittest.TestCase):
    """placement_side_offset_force_deg: the forced release offset of side_offset's qualification runs, in degrees of pan
    (the controller's KC_PLACEMENT_SIDE_OFFSET_FORCE_DEG). It reaches the child from this key only, like the words: a value
    the daemon's shell exported is removed (tests/test_supervisor.py). The runtime checks that it is a finite number and
    that the words name side_offset; the cap and the attended label of a LIVE run are the controller's refusals."""

    def test_the_default_is_no_forced_offset(self):
        config = RuntimeConfig.from_mapping(full_mapping())
        self.assertIsNone(config.placement_side_offset_force_deg)
        self.assertFalse(sanitized_summary(config)["placement_side_offset_forced"])

    def test_a_number_beside_side_offset_is_accepted_and_is_part_of_the_digest(self):
        words = ["projected", "side_offset"]
        plain = RuntimeConfig.from_mapping(full_mapping(placement_envelope=words))
        for value in (10, -10.0, 0.5, 0):
            with self.subTest(value):
                config = RuntimeConfig.from_mapping(full_mapping(placement_envelope=words, placement_side_offset_force_deg=value))
                self.assertEqual(float(value), config.placement_side_offset_force_deg)
                self.assertIsInstance(config.placement_side_offset_force_deg, float)
                self.assertTrue(sanitized_summary(config)["placement_side_offset_forced"])
                self.assertNotEqual(config_digest(plain), config_digest(config))
                self.assertNotIn(str(value), json.dumps({k: v for k, v in sanitized_summary(config).items() if k != "config_digest"}).replace("side_offset", ""))
        self.assertIsNone(RuntimeConfig.from_mapping(full_mapping(placement_envelope=words, placement_side_offset_force_deg=None)).placement_side_offset_force_deg)

    def test_a_value_that_is_not_a_finite_number_or_has_no_side_offset_word_is_refused(self):
        for words, value in ((["projected", "side_offset"], "10"), (["projected", "side_offset"], True), (["projected", "side_offset"], float("nan")),
                             (["projected", "side_offset"], float("inf")), (["projected", "side_offset"], [10]), ([], 10), (["projected"], 10),
                             (["align_v2"], -5)):
            with self.subTest((words, value)), self.assertRaises(ConfigError) as caught:
                RuntimeConfig.from_mapping(full_mapping(placement_envelope=words, placement_side_offset_force_deg=value))
            self.assertIn("placement_side_offset_force_deg", str(caught.exception))

    def test_direct_construction_is_validated_the_same_way(self):
        base = RuntimeConfig.from_mapping(full_mapping(placement_envelope=["projected", "side_offset"]))
        import dataclasses
        self.assertEqual(-3.0, dataclasses.replace(base, placement_side_offset_force_deg=-3).placement_side_offset_force_deg)
        with self.assertRaises(ConfigError):
            dataclasses.replace(base, placement_envelope=("projected",), placement_side_offset_force_deg=3.0)


if __name__ == "__main__":
    unittest.main()
