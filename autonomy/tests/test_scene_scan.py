# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""autonomy/perception/scene_scan.py: the pan-axis fit, the pad frame carried by pan, the clustering of blocks and the
fused scene, on synthetic pinhole views with a known axis and on the recorded pair of run m2476137 (2026-09-26).

The recorded numbers below are the two print-landmark homographies and the measured joints of the SEARCH frame
(obs-20260926T203748Z-wrist-0001, pan -9.670) and the PREGRASP pan waypoint (obs-20260926T203808Z-wrist-0001, pan
-29.626, the same lift, elbow and wrist_flex) of that run, as the archive robot-runtime-data-20260926T2037.tgz holds them;
test_scene_replay.py reads them back from the archive when KC_EVIDENCE_ROOT is set. The fit thresholds of the module are
set from this pair.
"""
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "perception"))

import scene_scan  # noqa: E402

# The recorded pair (see the module docstring).
SEARCH_2037 = {"observation_id": "obs-20260926T203748Z-wrist-0001", "pan_deg": -9.67032967032967,
               "joints": {"shoulder_pan": -9.67032967032967, "shoulder_lift": -79.42857142857143, "elbow_flex": 38.02197802197802, "wrist_flex": 67.20879120879121},
               "homography_image_to_pad": [[0.00038714668050373024, 5.9928657134633315e-06, -0.22960601432803404], [7.572637581811669e-06, -0.0004557613588898732, 0.00936424365564251],
                                           [-1.7301756207373846e-05, 0.0003089560069970002, 1.0]], "landmark_inliers": 96}
WAYPOINT_2037 = {"observation_id": "obs-20260926T203808Z-wrist-0001", "pan_deg": -29.626373626373628,
                 "joints": {"shoulder_pan": -29.626373626373628, "shoulder_lift": -79.42857142857143, "elbow_flex": 38.37362637362637, "wrist_flex": 67.20879120879121},
                 "homography_image_to_pad": [[0.00037209368071304755, 0.00011946079973623334, -0.369429599085805], [0.00014149362611992413, -0.0004487135181266504, -0.0901481814197612],
                                             [-7.782220596473989e-06, 0.0003673665459255353, 1.0]], "landmark_inliers": 102}
HOME_2037_PAN_DEG = -9.054945054945055  # the saved low scan pose of that run
START_2_CUBE = (-0.09364062895048128, -0.16982668229192513)  # its SEARCH frame's cube, placed by the frozen perception
START_2_PREGRASP_PAN_DEG = -29.53846153846154  # the pan its PREGRASP reached (the coarse pregrasp of that cube)
START_3 = (0.163, -0.210)  # refused at 20:58 and 21:08 UTC (test_recorded_pins_20260926.START_3)
LATE_2117 = (0.064, -0.220)  # refused at 21:17 UTC


def recorded_view(pinned: dict, name: str, cubes=()) -> dict:
    record = {"observation_id": pinned["observation_id"], "pad_by_template": {"pad_frame_valid": True, "homography_image_to_pad": pinned["homography_image_to_pad"],
                                                                               "landmark_inliers": pinned["landmark_inliers"]}, "cube": {"detected": False}, "cube_by_template": None}
    return {"name": name, "pan_deg": pinned["pan_deg"], "joints": dict(pinned["joints"]), "observation_id": pinned["observation_id"], "record": record, "entries": list(cubes)}


# ---------------------------------------------------------------------------------------------------------------------
# A synthetic pinhole camera turning about a known axis: its homographies are built from projection, not from the module.
# ---------------------------------------------------------------------------------------------------------------------
AXIS = np.array([-0.012, -0.414])
LOW_JOINTS = {"shoulder_lift": -79.4, "elbow_flex": 38.0, "wrist_flex": 67.2}


def _rodrigues(axis, angle_deg):
    axis = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    angle = math.radians(angle_deg)
    return np.eye(3) + math.sin(angle) * k + (1 - math.cos(angle)) * k @ k


def pinhole_image_to_pad(pan_deg: float, tilt_axis_deg: float = 0.0, pitch_deg: float = 55.0) -> list:
    """The image-to-PAD_FRAME homography of a camera 0.28 m above the pad, 0.05 m out from the axis, pitched pitch_deg
    down along +y at pan 0, turned about the axis by the pan. A positive shoulder pan turns the view clockwise in PAD_FRAME
    (the recorded pair: the rotation from a view to the reference equals pan_view - pan_ref)."""
    focal, cx, cy = 900.0, 640.0, 360.0
    intrinsics = np.array([[focal, 0, cx], [0, focal, cy], [0, 0, 1.0]])
    pitch = math.radians(pitch_deg)
    x_c, z_c = np.array([1.0, 0, 0]), np.array([0, math.cos(pitch), -math.sin(pitch)])
    y_c = np.cross(z_c, x_c)
    centre = np.array([AXIS[0], AXIS[1] + 0.05, 0.28])
    turn_axis = _rodrigues([1.0, 0, 0], tilt_axis_deg) @ np.array([0, 0, 1.0])
    turn = _rodrigues(turn_axis, -pan_deg)
    pivot = np.array([AXIS[0], AXIS[1], 0.0])
    centre = pivot + turn @ (centre - pivot)
    world_to_camera = np.vstack([turn @ x_c, turn @ y_c, turn @ z_c])
    pad_to_image = intrinsics @ np.c_[world_to_camera[:, 0], world_to_camera[:, 1], -world_to_camera @ centre]
    image_to_pad = np.linalg.inv(pad_to_image)
    return (image_to_pad / image_to_pad[2, 2]).tolist()


def synthetic_view(name, pan_deg, direct=True, joints=None, cubes=(), inliers=100, tilt_axis_deg=0.0) -> dict:
    homography = pinhole_image_to_pad(pan_deg, tilt_axis_deg)
    template = {"pad_frame_valid": True, "homography_image_to_pad": homography, "landmark_inliers": inliers} if direct else {"pad_frame_valid": False}
    record = {"observation_id": f"obs-{name}", "pad_by_template": template, "cube": {"detected": False}, "cube_by_template": None}
    return {"name": name, "pan_deg": pan_deg, "joints": {"shoulder_pan": pan_deg, **(joints or LOW_JOINTS)}, "observation_id": f"obs-{name}", "record": record, "entries": list(cubes)}


def seen_cube(view_pan_deg: float, pad_point, direct=True, clipped=False) -> dict:
    """A verified cube at pad_point as the view at view_pan_deg sees it: its contact pixel projected through that view."""
    pad_to_image = np.linalg.inv(np.asarray(pinhole_image_to_pad(view_pan_deg)))
    q = pad_to_image @ np.array([pad_point[0], pad_point[1], 1.0])
    contact = [float(q[0] / q[2]), float(q[1] / q[2])]
    return {"cube": {"detected": True, "contact_px": contact, "bbox_xyxy": [contact[0] - 40, contact[1] - 90, contact[0] + 40, contact[1]], "clipped": {"top": clipped}},
            "cube_by_template": {"cube_center_pad_m": list(pad_point)} if direct else None}


def classify_stub(record, pan_deg):
    cube = record.get("cube") or {}
    if not (record.get("cube_by_template") or {}).get("cube_center_pad_m"):
        return {"class": "NO_PAD_FRAME", "reason_code": "search_cube_not_in_pad_frame", "values": {}}
    return {"class": "ARM_ONLY", "reason_code": None, "values": {"pan_deg": pan_deg, "contact_px": cube.get("contact_px")}}


class ThePanAxisFit(unittest.TestCase):
    def test_synthetic_views_recover_the_axis_and_every_pan_change(self):
        fit = scene_scan.fit_pan_axis([synthetic_view("home", 0.0, inliers=120), synthetic_view("L15", -15.0), synthetic_view("R15", 15.0)])
        self.assertTrue(fit["accepted"], fit)
        self.assertEqual("home", fit["reference"], "the view with the most landmark inliers is the reference")
        self.assertLess(math.dist(fit["axis_pad_m"], AXIS), 0.0005)
        for pair in fit["pairs"]:
            self.assertTrue(pair["used"] and pair["passed"] and pair["locates_axis"], pair)
            self.assertLess(pair["rotation_error_deg"], 0.01)

    def test_a_pan_reading_one_degree_off_is_refused(self):
        wrong = synthetic_view("L15", -15.0)
        wrong["pan_deg"] = -16.0
        fit = scene_scan.fit_pan_axis([synthetic_view("home", 0.0, inliers=120), wrong])
        self.assertFalse(fit["accepted"])
        self.assertEqual(["pair_L15_outside_thresholds"], fit["reasons"])

    def test_an_axis_tilted_three_degrees_is_refused(self):
        views = [synthetic_view("home", 0.0, inliers=120, tilt_axis_deg=3.0), synthetic_view("L15", -15.0, tilt_axis_deg=3.0), synthetic_view("R15", 15.0, tilt_axis_deg=3.0)]
        fit = scene_scan.fit_pan_axis(views)
        self.assertFalse(fit["accepted"], fit)

    def test_a_view_whose_wrist_differs_by_one_degree_is_not_paired(self):
        fit = scene_scan.fit_pan_axis([synthetic_view("home", 0.0, inliers=120), synthetic_view("L15", -15.0, joints={**LOW_JOINTS, "wrist_flex": 68.2}), synthetic_view("R15", 15.0)])
        self.assertEqual({"view": "L15", "pan_change_deg": -15.0, "used": False, "reason": "joints_disagree"}, fit["pairs"][0])
        self.assertTrue(fit["accepted"], "the other pair still places the axis")

    def test_fewer_than_two_direct_views_give_no_fit_and_nothing_is_carried(self):
        views = [synthetic_view("home", 0.0), synthetic_view("L15", -15.0, direct=False)]
        fit = scene_scan.fit_pan_axis(views)
        self.assertEqual((False, ["fewer_than_two_direct_views"], None), (fit["accepted"], fit["reasons"], fit["axis_pad_m"]))
        scene = scene_scan.fuse(views, classify_stub, home_pan_deg=0.0)
        self.assertEqual(["direct", "none"], [view["pad_frame_source"] for view in scene["views"]])

    def test_a_pair_close_in_pan_is_checked_but_does_not_place_the_axis(self):
        fit = scene_scan.fit_pan_axis([synthetic_view("home", 0.0, inliers=120), synthetic_view("centre", 0.4)])
        self.assertFalse(fit["accepted"])
        self.assertEqual(["no_pair_changes_the_pan_enough"], fit["reasons"])
        self.assertFalse(fit["pairs"][0]["locates_axis"])


class ThePadFrameCarriedByPan(unittest.TestCase):
    def test_a_carried_frame_maps_the_view_as_its_own_print_would(self):
        axis = scene_scan.fit_pan_axis([synthetic_view("home", 0.0, inliers=120), synthetic_view("R15", 15.0)])["axis_pad_m"]
        carried = scene_scan.transfer_homography(pinhole_image_to_pad(0.0), axis, -15.0)
        grid = scene_scan.frame_grid(80)
        expected, _ = scene_scan.apply_homography(pinhole_image_to_pad(-15.0), grid)
        got, _ = scene_scan.apply_homography(carried, grid)
        self.assertLess(float(np.max(np.linalg.norm(got - expected, axis=1))), 0.001)

    def test_a_block_seen_only_through_a_carried_frame_is_inferred_and_never_placed(self):
        block = (0.15, -0.20)
        views = [synthetic_view("home", 0.0, inliers=120), synthetic_view("L15", -15.0, direct=False, cubes=[seen_cube(-15.0, block, direct=False)]), synthetic_view("R15", 15.0)]
        scene = scene_scan.fuse(views, classify_stub, home_pan_deg=0.0)
        self.assertEqual("pan_transfer", scene["views"][1]["pad_frame_source"])
        (found,) = scene["blocks"]
        self.assertEqual("inferred_contact_point", found["position_source"])
        self.assertLess(math.dist(found["position_pad_m"], block), 0.001)
        self.assertEqual("NO_PAD_FRAME", found["class"], "a carried frame never gives a placement anchor")

    def test_a_view_whose_held_joints_disagree_is_not_carried(self):
        views = [synthetic_view("home", 0.0, inliers=120), synthetic_view("R15", 15.0), synthetic_view("L15", -15.0, direct=False, joints={**LOW_JOINTS, "elbow_flex": 39.0})]
        scene = scene_scan.fuse(views, classify_stub, home_pan_deg=0.0)
        self.assertTrue(scene["fit"]["accepted"])
        self.assertEqual("none", scene["views"][2]["pad_frame_source"])


class TheBlocks(unittest.TestCase):
    def test_sightings_within_the_radius_are_one_block_and_farther_ones_two(self):
        near = [synthetic_view("home", 0.0, inliers=120, cubes=[seen_cube(0.0, (0.02, -0.20))]), synthetic_view("R15", 15.0, cubes=[seen_cube(15.0, (0.03, -0.20))])]
        self.assertEqual(1, len(scene_scan.fuse(near, classify_stub, home_pan_deg=0.0)["blocks"]))
        far = [synthetic_view("home", 0.0, inliers=120, cubes=[seen_cube(0.0, (0.02, -0.20))]), synthetic_view("R15", 15.0, cubes=[seen_cube(15.0, (0.06, -0.20))])]
        self.assertEqual(2, len(scene_scan.fuse(far, classify_stub, home_pan_deg=0.0)["blocks"]))

    def test_a_block_states_its_position_spread_views_and_best_view(self):
        views = [synthetic_view("home", 0.0, inliers=120, cubes=[seen_cube(0.0, (0.05, -0.20), clipped=True)]), synthetic_view("L15", -15.0, cubes=[seen_cube(-15.0, (0.052, -0.20))]),
                 synthetic_view("R15", 15.0, cubes=[seen_cube(15.0, (0.054, -0.20))])]
        (block,) = scene_scan.fuse(views, classify_stub, home_pan_deg=0.0)["blocks"]
        self.assertEqual(["home", "L15", "R15"], block["views_seen"])
        self.assertEqual([0.052, -0.2], block["position_pad_m"])
        self.assertEqual(2.0, block["spread_mm"])
        self.assertNotEqual("home", block["best_view"], "a clipped sighting is never the best view when an unclipped one exists")
        self.assertEqual("ARM_ONLY", block["class"])
        self.assertEqual(block["best_pan_deg"], block["values"]["pan_deg"], "the class is taken at the best view's pan")

    def test_the_inferred_bearing_is_the_pan_that_brings_the_reference_pixel_onto_the_block(self):
        views = [synthetic_view("home", 0.0, inliers=120), synthetic_view("R15", 15.0)]
        reference = scene_scan.apply_homography(pinhole_image_to_pad(0.0), [(553.0, 545.0)])[0][0]
        for pan in (-20.0, 10.0, 30.0):
            turned = scene_scan.apply_homography(pinhole_image_to_pad(pan), [(553.0, 545.0)])[0][0]  # the floor point the reference pixel sees at that pan
            views[0]["entries"] = [seen_cube(0.0, turned)]
            (block,) = scene_scan.fuse(views, classify_stub, home_pan_deg=0.0)["blocks"]
            self.assertAlmostEqual(pan, block["inferred_pan_from_home_deg"], delta=0.05)
            self.assertEqual(abs(pan) > 25.0, block["outside_pan_band_inferred"])
        self.assertIsNotNone(reference)

    def test_every_view_states_its_footprint_and_pad_evidence(self):
        scene = scene_scan.fuse([synthetic_view("home", 0.0, inliers=120), synthetic_view("L15", -15.0, direct=False)], classify_stub, home_pan_deg=0.0)
        self.assertGreater(len(scene["views"][0]["footprint_pad_m"]), 8)
        self.assertEqual([], scene["views"][1]["footprint_pad_m"], "no frame, no footprint")
        self.assertEqual(120, scene["views"][0]["landmark_inliers"])
        self.assertEqual(scene_scan.SCHEMA, scene["schema"])


class TheRecordedPairOf2037(unittest.TestCase):
    """The replay pin the thresholds are set from (overnight plan, track S, offline acceptance)."""

    def setUp(self):
        self.fit = scene_scan.fit_pan_axis([recorded_view(SEARCH_2037, "search"), recorded_view(WAYPOINT_2037, "waypoint")])
        self.pair = self.fit["pairs"][0]

    def test_the_rotation_is_within_0_2_deg_of_the_measured_pan_change_and_the_median_residual_under_1_5_mm(self):
        self.assertTrue(self.fit["accepted"], self.fit)
        self.assertEqual("waypoint", self.fit["reference"], "the waypoint frame has more landmark inliers (102 against 96)")
        self.assertAlmostEqual(19.956, self.pair["pan_change_deg"], places=3)
        self.assertAlmostEqual(20.07, self.pair["rotation_deg"], delta=0.02)
        self.assertLess(self.pair["rotation_error_deg"], 0.2)
        self.assertLess(self.pair["residual_median_mm"], 1.5)
        self.assertAlmostEqual(1.15, self.pair["residual_median_mm"], delta=0.05)

    def test_the_axis_is_where_the_search_review_measured_it(self):
        self.assertLess(math.dist(self.fit["axis_pad_m"], (-0.0125, -0.4132)), 0.003)

    def test_the_thresholds_are_the_pinned_ones(self):
        self.assertEqual((0.2, 1.5, 0.5), (scene_scan.FIT_ROTATION_TOLERANCE_DEG, scene_scan.FIT_RESIDUAL_MEDIAN_MAX_MM, scene_scan.TRANSFER_JOINT_TOLERANCE_DEG))

    def _pan_from_home(self, block):
        reference = scene_scan.apply_homography(SEARCH_2037["homography_image_to_pad"], [(553.0, 545.0)])[0][0]
        return scene_scan.inferred_pan_offset_deg(self.fit["axis_pad_m"], reference, block) + (SEARCH_2037["pan_deg"] - HOME_2037_PAN_DEG)

    def test_start_2s_block_lies_where_its_pregrasp_went_within_1_5_deg(self):
        self.assertAlmostEqual(START_2_PREGRASP_PAN_DEG - HOME_2037_PAN_DEG, self._pan_from_home(START_2_CUBE), delta=1.5)

    def test_start_3_lies_outside_the_pan_band_and_2117_inside_it(self):
        start_3, late = self._pan_from_home(START_3), self._pan_from_home(LATE_2117)
        self.assertGreater(start_3, 25.0)
        self.assertAlmostEqual(39.4, start_3, delta=1.5)
        self.assertLess(abs(late), 25.0)


if __name__ == "__main__":
    unittest.main()
