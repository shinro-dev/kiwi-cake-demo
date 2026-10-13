#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_mission_retry_live.py: the Retry of a LIVE mission, and the endings a hold offers no
# Retry for, against a REAL Supervisor in LIVE mode (hardware commands on) running the FAKE controller
# (tests/fake_controller.py) through the ProvenTargetRunner and the MissionCoordinator. No robot, camera or network.
#
# A LIVE runtime offers a Retry only under mission.retry AND the attended label (placement_envelope_attended). Without
# them the hold offers only the Stop, a Retry request is refused before anything is written or started, and the park the
# Retry would run is refused by the supervisor itself. The endings that leave the arm where no recorded exit started, and
# the target binding's refusals, hold for the operator with only the Stop, whatever the configuration.
# Run directly: python3 packages/demo-runtime/tests/test_mission_retry_live.py
from __future__ import annotations

import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
import sys

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
sys.path.insert(0, str(REPO_ROOT))

from demo_runtime import mission, mission_coordinator  # noqa: E402
from demo_runtime import supervisor as sv  # noqa: E402
from demo_runtime.config import RuntimeConfig  # noqa: E402
from tests.test_mission_retry import RetryStack  # noqa: E402

LABEL = "retry-check-1"
BINDING_SENTENCE = ("BINDING: the one cube for target cube has no bounded placement from this SEARCH: the right edge along the reach "
                    "axis is 14.5 mm short of 58.96 mm after 120 mm of advance; move the block 15 mm to the left as seen from the "
                    "robot and 2 mm toward the pad; nothing was picked up")


def live_mapping(root, *, retry=None, attended=None, envelope=None) -> dict:
    """A LIVE runtime config of reserved documentation values only (tests.test_supervisor.live_config's), on the baseline
    backend, with the mission section and the attended label as asked."""
    address = ".".join(("192", "0", "2", "13"))
    mapping = {
        "data_root": str(Path(root) / "demo-data"), "saved_pose_file": "calibration/scan-home-current.json",
        "pad_template_dir": "calibration/pad-template", "observation_root": "observations",
        "host": {"address": address, "robot_id": "unit" + "-0042"}, "pi_ssh_target": "operator" + "name" + "@" + address,
        "cameras": {"wrist_id": "usb-" + "Vendor_Cam_" + "SN" + "1234567" + "-video-index0", "front_id": None},
        "model": {"identity": "google/owlv2-base-patch16-ensemble", "revision": None},
        "execution_backend": "baseline_normal_host"}
    if retry is not None:
        mapping["mission"] = {"retry": retry}
    if attended is not None:
        mapping["placement_envelope_attended"] = attended
    if envelope is not None:
        mapping["placement_envelope"] = list(envelope)
    return mapping


class LiveStack(RetryStack):
    """RetryStack with a REAL Supervisor in LIVE mode, hardware commands on and an admitting motion gate."""

    def __init__(self, test, *, retry=None, attended=None, envelope=None, **kwargs):
        holder = Path(tempfile.mkdtemp(prefix="kc-live-retry-config-"))
        test.addCleanup(shutil.rmtree, holder, True)
        config = RuntimeConfig.from_mapping(live_mapping(holder, retry=retry, attended=attended, envelope=envelope))
        super().__init__(test, supervisor_options={"mode": sv.LIVE, "config": config, "hardware_enabled": True,
                                                   "motion_gate": lambda: (None, {"session_uuid": "ab" * 8})}, **kwargs)


class ALiveRuntimeOffersNoRetryWithoutTheSwitch(unittest.TestCase):
    def assert_refused_with_nothing_started(self, stack, reason_code):
        refused = stack.coordinator.request_retry()
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["reason_code"], reason_code)
        time.sleep(0.4)
        self.assertEqual(len(stack.launches()), 1, "a refused Retry starts nothing")
        self.assertEqual(stack.kinds(), ["start_cycle"])
        self.assertEqual(stack.supervisor.parks, [], "no park was asked for")
        self.assertFalse(stack.view()["hold"]["retry_requested"], "nothing was written for the refused Retry")
        stack.stop()
        view = stack.wait_for_end()
        self.assertEqual(view["state"], mission.MISSION_STOPPED)
        self.assertEqual(len(stack.launches()), 1)

    def test_no_mission_section_holds_with_only_the_stop_and_refuses_a_retry(self):
        stack = LiveStack(self, fail_cycles=1)
        stack.start()
        hold = stack.wait_for_hold()["hold"]
        self.assertEqual((hold["outcome_class"], hold["reason_code"]), (mission.OUTCOME_RETRYABLE, "align_cube_not_detected"))
        self.assertEqual(hold["next_actions"], ["stop"])
        self.assert_refused_with_nothing_started(stack, mission_coordinator.RETRY_NOT_ENABLED)

    def test_retry_false_holds_with_only_the_stop_and_refuses_a_retry(self):
        stack = LiveStack(self, fail_cycles=1, retry=False)
        stack.start()
        self.assertEqual(stack.wait_for_hold()["hold"]["next_actions"], ["stop"])
        self.assert_refused_with_nothing_started(stack, mission_coordinator.RETRY_NOT_ENABLED)

    def test_retry_on_without_the_attended_label_holds_with_only_the_stop(self):
        stack = LiveStack(self, fail_cycles=1, retry=True)
        stack.start()
        self.assertEqual(stack.wait_for_hold()["hold"]["next_actions"], ["stop"])
        self.assert_refused_with_nothing_started(stack, mission_coordinator.RETRY_NOT_ATTENDED)

    def test_an_envelope_word_and_its_label_alone_do_not_switch_the_retry_on(self):
        stack = LiveStack(self, fail_cycles=1, attended=LABEL, envelope=("projected",))
        stack.start()
        self.assertEqual(stack.wait_for_hold()["hold"]["next_actions"], ["stop"])
        self.assert_refused_with_nothing_started(stack, mission_coordinator.RETRY_NOT_ENABLED)

    def test_retry_on_with_the_attended_label_parks_and_runs_a_fresh_cycle(self):
        stack = LiveStack(self, fail_cycles=1, retry=True, attended=LABEL)
        stack.start()
        hold = stack.wait_for_hold()["hold"]
        self.assertEqual(hold["next_actions"], ["retry", "stop"])
        answer = stack.coordinator.request_retry()
        self.assertTrue(answer["ok"], answer)
        view = stack.wait_for_end()
        self.assertEqual(view["state"], mission.MISSION_COMPLETE, view)
        self.assertEqual(stack.kinds(), ["start_cycle", "return_arm", "start_cycle"])

    def test_a_move_block_hold_offers_no_retry_without_the_switch_either(self):
        stack = LiveStack(self, fail_cycles=1, env={"KC_FAKE_FAIL_AT": "APPROACH", "KC_FAKE_FAIL_CODE": "approach_cube_beyond_grasp_range"})
        stack.start()
        hold = stack.wait_for_hold()["hold"]
        self.assertEqual(hold["outcome_class"], mission.OUTCOME_OPERATOR_NEEDED)
        self.assertEqual(hold["next_actions"], ["stop"])
        self.assert_refused_with_nothing_started(stack, mission_coordinator.RETRY_NOT_ENABLED)


class TheSupervisorRefusesTheParkItself(unittest.TestCase):
    def park(self, **config):
        stack = LiveStack(self, **config)
        run = sv.MissionRun(mission_id="m-park-gate", target_id="cube", cancel=threading.Event(), write_target=None)
        return stack, stack.supervisor.start_mission_park(run)

    def test_a_live_park_without_the_switch_is_refused_before_any_spawn(self):
        for config, reason_code in (({}, sv.MISSION_RETRY_NOT_ENABLED), ({"retry": False}, sv.MISSION_RETRY_NOT_ENABLED),
                                    ({"retry": True}, sv.MISSION_RETRY_NOT_ATTENDED)):
            with self.subTest(config=config):
                stack, answer = self.park(**config)
                self.assertFalse(answer["ok"], answer)
                self.assertEqual(answer["reason_code"], reason_code)
                time.sleep(0.2)
                self.assertEqual(stack.launches(), [])
                self.assertIsNone(stack.supervisor.active_run())

    def test_a_live_park_under_the_switch_and_the_label_is_started(self):
        stack, answer = self.park(retry=True, attended=LABEL)
        self.assertTrue(answer["ok"], answer)
        stack.wait_until(lambda: stack.supervisor.active_run() is None and stack.launches(), "the park run")
        self.assertEqual(stack.kinds(), ["return_arm"])


class EndingsThatHoldForTheOperatorWithOnlyTheStop(unittest.TestCase):
    """The switch and the label are ON here, so the hold's list is the ending's own: these offer no Retry at all."""

    def hold_for(self, env, **config):
        stack = LiveStack(self, fail_cycles=1, env=env, **{"retry": True, "attended": LABEL, **config})
        stack.start()
        return stack, stack.wait_for_hold()["hold"]

    def test_endings_whose_park_would_start_from_an_unrecorded_pose(self):
        for code, state, jaws in (("grasp_set_down_lift_refused", "OBJECT_SECURED", "set_down"),
                                  ("grasp_retry_lift_refused", "GRASP_CLOSE", "empty"),
                                  ("grasp_retry_approach_refused", "GRASP_CLOSE", "empty"),
                                  ("align_inside_grasp_radius", "ALIGN", None)):
            with self.subTest(code=code):
                env = {"KC_FAKE_FAIL_AT": state, "KC_FAKE_FAIL_CODE": code, **({"KC_FAKE_FAIL_JAWS": jaws} if jaws else {})}
                stack, hold = self.hold_for(env)
                self.assertEqual((hold["reason_code"], hold["outcome_class"]), (code, mission.OUTCOME_OPERATOR_NEEDED))
                self.assertIs(hold["stop_state"]["holding"], False)
                self.assertEqual(hold["next_actions"], ["stop"])
                self.assertEqual(stack.coordinator.request_retry()["reason_code"], mission_coordinator.RETRY_NOT_OFFERED)
                time.sleep(0.3)
                self.assertEqual(stack.kinds(), ["start_cycle"])
                stack.stop()
                self.assertEqual(stack.wait_for_end()["state"], mission.MISSION_STOPPED)

    def test_the_binding_refusals_hold_with_the_controllers_sentence_and_only_the_stop(self):
        for code in ("mission_binding_candidate_not_placeable", "mission_binding_no_candidate"):
            for config in ({}, {"retry": None, "attended": None}):
                with self.subTest(code=code, config=config):
                    stack, hold = self.hold_for({"KC_FAKE_FAIL_AT": "SEARCH", "KC_FAKE_FAIL_CODE": code, "KC_FAKE_FAIL_DETAIL": BINDING_SENTENCE}, **config)
                    self.assertEqual((hold["reason_code"], hold["outcome_class"]), (code, mission.OUTCOME_OPERATOR_NEEDED))
                    self.assertEqual(hold["next_actions"], ["stop"])
                    self.assertEqual(hold["detail"], BINDING_SENTENCE, "the hold says the deciding limit, the shortfall and the hint")
                    view = stack.view()
                    self.assertEqual(view["next_actions"], ["stop"])
                    self.assertNotEqual(stack.coordinator.request_retry()["ok"], True)
                    stack.stop()
                    self.assertEqual(stack.wait_for_end()["state"], mission.MISSION_STOPPED)
                    self.assertEqual(stack.kinds(), ["start_cycle"])


class TheSceneEndingsAskForTheBlockToBeMoved(unittest.TestCase):
    def test_the_scene_scan_endings_hold_for_the_block_and_retry_only_under_the_switch(self):
        for code in ("search_scene_no_block", "search_scene_not_one_unplaced_block"):
            for config, actions in (({"retry": True, "attended": LABEL}, ["move_block_then_retry", "stop"]), ({}, ["stop"])):
                with self.subTest(code=code, config=config):
                    stack = LiveStack(self, fail_cycles=1, env={"KC_FAKE_FAIL_AT": "SEARCH", "KC_FAKE_FAIL_CODE": code}, **config)
                    stack.start()
                    hold = stack.wait_for_hold()["hold"]
                    self.assertEqual((hold["reason_code"], hold["outcome_class"]), (code, mission.OUTCOME_OPERATOR_NEEDED))
                    self.assertEqual(hold["next_actions"], actions)
                    stack.stop()
                    self.assertEqual(stack.wait_for_end()["state"], mission.MISSION_STOPPED)


if __name__ == "__main__":
    unittest.main()
