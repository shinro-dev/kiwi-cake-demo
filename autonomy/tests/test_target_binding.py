#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_target_binding.py: which cube a mission target is, decided from measurements, and the two files that
# carry a mission target between the runtime and the controller. Every cube reading below is made with the PROVEN
# controller's own functions (placed_margin_mm, placement_plan, PLACED_MIN_EDGE_MARGIN_MM), imported here and never by
# the module under test, so what these tests pin is the proven predicate fed through the new seams. The pad geometry is
# the controller's own test geometry: image-up is pad +y, the near pad edge is at y = -0.1475 m. No robot, no camera, no
# network, no lerobot. Run directly: python3 autonomy/tests/test_target_binding.py
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import run_single_cube_cycle as cycle  # noqa: E402  (the proven functions the caller passes in)
import target_binding as tb  # noqa: E402

NEAR_EDGE_Y = -cycle.PAD_SIZE_M[1] / 2.0
UP = (0.0, 1.0)  # the reach direction in PAD_FRAME of the controller's own tests
GRASP_OUT_MM = cycle.LOW_POSE_REF_GRASP["out_mm"]  # the reference grasp radius
# TEST VALUES: the design's starting radii (operator decision D3), used here to exercise the predicates. They are not
# qualified and nothing in target_binding defaults to them; a mission gets its radii from the operator's configuration.
TEST_R_CLAIM_MM, TEST_R_MATCH_MM = 30.0, 40.0
TEST_RADII = {"r_claim_mm": TEST_R_CLAIM_MM, "r_match_mm": TEST_R_MATCH_MM, "basis": "test values only (design D3 starting points)"}
RULES = tb.Rules(placed_min_margin_mm=cycle.PLACED_MIN_EDGE_MARGIN_MM, r_claim_mm=TEST_R_CLAIM_MM, r_match_mm=TEST_R_MATCH_MM)

# The staged two-cube scene. A lies on the pad edge (6 mm inside the outline, less than the half-diagonal), B 30 mm outside
# it; after the first target A stands 45 mm inside, after the second B stands 40 mm inside, 80 mm from A.
A_START = (0.020, NEAR_EDGE_Y + 0.006)
B_START = (-0.060, NEAR_EDGE_Y - 0.030)
A_PLACED = (0.020, NEAR_EDGE_Y + 0.045)
B_PLACED = (-0.060, NEAR_EDGE_Y + 0.040)

MISSION_ID = "mission-0123456789ab"
RUN_A, RUN_B = "console-20260923T120000Z-0a1b2c3d", "console-20260923T121500Z-4e5f6a7b"
# The mission's own target records, as demo_runtime.mission_service declares its default two-cube mission.
MISSION_TARGETS = ({"target_id": "cube_near", "label": "the reachable cube", "reachable": True, "requires_capability": False},
                   {"target_id": "cube_far", "label": "the far cube", "reachable": False, "requires_capability": True})
DECLARED = (tb.DeclaredTarget("cube_near", False), tb.DeclaredTarget("cube_far", True))


def seen(point, offset_mm=(0.0, 0.0), grasp_out_mm=GRASP_OUT_MM):
    """One cube as SEARCH measures it, through the proven functions: its margin and its placement_plan mode."""
    centre = (point[0] + offset_mm[0] / 1000.0, point[1] + offset_mm[1] / 1000.0)
    try:
        mode = cycle.placement_plan(centre, UP, grasp_out_mm)["mode"]
    except cycle.CycleFailed:
        mode = None
    return tb.SeenCube(centre, cycle.placed_margin_mm(centre), mode)


def release_at(point):
    """This run's release segment reduced to one point (an FK travel of zero): THE RELEASE AGREEMENT's input."""
    return cycle.release_segment_pad(point, UP, 0.0)


def capability_block(directory):
    return {"admin_probe": os.path.join(directory, "admin-probe"), "admin_socket": os.path.join(directory, "admin.sock"),
            "slot_uuid": "5a" * 16, "capsule": "c0" * 32,
            "observation_endpoint": "ipc://" + os.path.join(directory, "observations.ipc"),
            "command_endpoint": "ipc://" + os.path.join(directory, "commands.ipc")}


def first_document(directory):
    return tb.mission_target_document(mission_id=MISSION_ID, target_id="cube_near", run_id=RUN_A, targets=tb.declared_targets(MISSION_TARGETS),
                                      capability=None, result_path=os.path.join(directory, "result-a.json"), radii=TEST_RADII)


def later_document(directory, a_placed=A_PLACED):
    targets = [{"target_id": "cube_near", "requires_capability": False, "effective_mode": None, "bound_pad_m": list(A_START), "placed_pad_m": list(a_placed)},
               {"target_id": "cube_far", "requires_capability": True, "effective_mode": None, "bound_pad_m": list(B_START), "placed_pad_m": None}]
    return tb.mission_target_document(mission_id=MISSION_ID, target_id="cube_far", run_id=RUN_B, targets=targets,
                                      capability=capability_block(directory), result_path=os.path.join(directory, "result-b.json"), radii=TEST_RADII)


def scratch(test) -> str:
    directory = tempfile.mkdtemp(prefix="kc-target-binding-")
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


# =====================================================================================================================
class ProvenGeometryTest(unittest.TestCase):
    """The fixtures really are what their names say, by the proven functions, and the shared vocabulary is theirs."""

    def test_the_staged_scene_measures_as_the_design_describes(self):
        self.assertEqual((tb.MODE_ARM_ONLY, tb.MODE_BASE_ADVANCE), (seen(A_START).mode, seen(B_START).mode))
        self.assertTrue(tb.is_candidate(seen(A_START), RULES))
        self.assertTrue(tb.is_candidate(seen(B_START), RULES))
        self.assertTrue(tb.is_placed(seen(A_PLACED), RULES) and tb.is_placed(seen(B_PLACED), RULES))

    def test_the_modes_are_the_words_placement_plan_returns(self):
        self.assertEqual(tb.MODE_ARM_ONLY, cycle.placement_plan((0.0, NEAR_EDGE_Y + 0.012), UP, 30.0)["mode"])
        self.assertEqual(tb.MODE_BASE_ADVANCE, cycle.placement_plan((0.013, NEAR_EDGE_Y - 0.0206), UP, 29.0)["mode"])

    def test_placed_is_the_proven_placed_predicate(self):
        # The proven PLACED: inside the outline (static_world_observation's test) AND the centre margin at least the half-diagonal.
        half_x, half_y = cycle.PAD_SIZE_M[0] / 2.0, cycle.PAD_SIZE_M[1] / 2.0
        for x_mm in range(-230, 231, 7):
            for y_mm in range(-170, 171, 7):
                centre = (x_mm / 1000.0, y_mm / 1000.0)
                proven = abs(centre[0]) <= half_x and abs(centre[1]) <= half_y and cycle.placed_margin_mm(centre) >= cycle.PLACED_MIN_EDGE_MARGIN_MM
                self.assertEqual(proven, tb.is_placed(tb.SeenCube(centre, cycle.placed_margin_mm(centre)), RULES), centre)

    def test_an_edge_cube_is_a_candidate(self):
        # Inside the outline by 0 to 17.9 mm: not placed, a candidate, and carried by the arm alone at the reference grasp radius.
        for margin_mm in (0.0, 0.1, 6.0, 9.0, 17.9):
            cube = seen((0.0, NEAR_EDGE_Y + margin_mm / 1000.0))
            self.assertFalse(tb.is_placed(cube, RULES), margin_mm)
            self.assertTrue(tb.is_candidate(cube, RULES), margin_mm)
            self.assertEqual(tb.MODE_ARM_ONLY, cube.mode, margin_mm)
        just_placed = seen((0.0, NEAR_EDGE_Y + (cycle.PLACED_MIN_EDGE_MARGIN_MM + 0.01) / 1000.0))
        self.assertTrue(tb.is_placed(just_placed, RULES))
        self.assertFalse(tb.is_candidate(just_placed, RULES))

    def test_the_outcome_words_are_the_contract_s_and_the_controller_s(self):
        schema = json.loads((ROOT / "packages" / "demo-contracts" / "schema" / "v1" / "event.schema.json").read_text())
        self.assertEqual(set(schema["$defs"]["run_outcome"]["enum"]), set(tb.OUTCOMES))
        produced = {cycle._terminal_outcome(status, already, {name: value for name in cycle._PLACEMENT_PREDICATES}, stop)
                    for status in (0, 3, 4, 5, 9) for already in (True, False) for value in (True, False, None) for stop in (True, False)}
        self.assertLessEqual(produced, set(tb.OUTCOMES))
        self.assertEqual(tuple(cycle._PLACEMENT_PREDICATES), tb.PLACEMENT_PREDICATES)

    def test_readings_and_rules_refuse_malformed_values(self):
        for bad in (dict(center_pad_m=(0.0,), margin_mm=1.0), dict(center_pad_m=(0.0, float("nan")), margin_mm=1.0),
                    dict(center_pad_m=(0.0, 0.0), margin_mm=None), dict(center_pad_m=(0.0, 0.0), margin_mm=True),
                    dict(center_pad_m=(0.0, 0.0), margin_mm=1.0, mode="BASE"), dict(center_pad_m=None, margin_mm=1.0),
                    dict(center_pad_m=None, mode=tb.MODE_ARM_ONLY)):
            with self.assertRaises(ValueError, msg=bad):
                tb.SeenCube(**bad)
        radii = dict(r_claim_mm=TEST_R_CLAIM_MM, r_match_mm=TEST_R_MATCH_MM)
        for bad in (dict(placed_min_margin_mm=0.0, **radii), dict(placed_min_margin_mm=-1.0, **radii), dict(placed_min_margin_mm=float("inf"), **radii),
                    dict(placed_min_margin_mm=17.96, r_claim_mm=0.0, r_match_mm=40.0), dict(placed_min_margin_mm=17.96, r_claim_mm=30.0, r_match_mm=float("nan"))):
            with self.assertRaises(ValueError, msg=bad):
                tb.Rules(**bad)

    def test_the_rules_have_no_default_and_the_one_derived_value_carries_its_basis(self):
        """The operator: "do not silently bless the defaults". Rules holds no radius a caller could fall back on; the one
        value the module DOES carry (radii_from_cube_geometry) is half a cube edge, the controller's CUBE_EDGE_MM over two,
        and it never travels without the basis text that says so."""
        with self.assertRaises(TypeError):
            tb.Rules(placed_min_margin_mm=cycle.PLACED_MIN_EDGE_MARGIN_MM)
        with self.assertRaises(TypeError):
            tb.Rules(placed_min_margin_mm=cycle.PLACED_MIN_EDGE_MARGIN_MM, r_claim_mm=30.0)
        self.assertFalse(hasattr(tb, "R_CLAIM_MM") or hasattr(tb, "R_MATCH_MM"))
        tree = ast.parse((ROOT / "autonomy" / "manipulation" / "target_binding.py").read_text())
        rules = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Rules")
        self.assertEqual([], [node.target.id for node in rules.body if isinstance(node, ast.AnnAssign) and node.value is not None])
        radii = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Radii")
        self.assertEqual([], [node.target.id for node in radii.body if isinstance(node, ast.AnnAssign) and node.value is not None])
        derived = tb.radii_from_cube_geometry()
        self.assertEqual(cycle.CUBE_EDGE_MM / 2.0, tb.CUBE_HALF_EDGE_MM)
        self.assertEqual((tb.CUBE_HALF_EDGE_MM, tb.CUBE_HALF_EDGE_MM, tb.CUBE_HALF_EDGE_BASIS), (derived.r_claim_mm, derived.r_match_mm, derived.basis))
        self.assertIn("half a cube edge", derived.basis)
        self.assertIn("25.4", derived.basis)
        self.assertIsNotNone(tb.RADII_BASIS_RE.fullmatch(derived.basis), "the basis must pass the file's own alphabet")
        self.assertIsNone(tb._DOTTED_QUAD.search(derived.basis))
        evidence = " ".join(tb.CUBE_HALF_EDGE_EVIDENCE)
        for figure in ("25.4", "12.7", "5.2", "0.3", "1.6", "1.8", "2.7", "17.96", "37", "45", "four radii"):
            self.assertIn(figure, evidence, figure)


# =====================================================================================================================
class OnThePadTest(unittest.TestCase):
    """The pad policy of 2026-09-29: a block ON the pad (the proven PLACED margin) is never a candidate, whoever put it
    there; a block OFF it is one. The retired claim predicate is gone (ClaimTest pinned it until the ruling)."""

    def test_a_block_on_the_pad_is_never_a_candidate_whoever_placed_it(self):
        cube = seen(A_PLACED, offset_mm=(3.0, 4.0))
        self.assertTrue(tb.is_placed(cube, RULES))
        self.assertFalse(tb.is_candidate(cube, RULES))
        self.assertFalse(hasattr(tb, "is_claimed"))

    def test_a_placed_block_re_read_short_of_the_margin_is_off_the_pad_and_a_candidate(self):
        # CHANGED by the ruling of 2026-09-29. An earlier target recorded PLACED 20 mm inside, re-read 5 mm nearer the edge:
        # no longer on the pad by the margin. It used to stay that target's (claimed, never a candidate); the robot now
        # picks it up and places it like any block off the pad.
        cube = seen((0.0, NEAR_EDGE_Y + 0.020), offset_mm=(0.0, -5.0))
        self.assertFalse(tb.is_placed(cube, RULES))
        self.assertTrue(tb.is_candidate(cube, RULES))

    def test_a_cube_without_a_centre_is_neither_placed_nor_a_candidate(self):
        cube = tb.SeenCube(None)
        self.assertFalse(tb.is_placed(cube, RULES) or tb.is_candidate(cube, RULES))


# =====================================================================================================================
class BindingTest(unittest.TestCase):
    def test_the_first_search_binds_every_declared_target(self):
        earlier = seen((0.15, 0.05))  # a cube already on the pad from before this mission: placed, so nobody's candidate
        cubes = [seen(B_START), earlier, seen(A_START)]
        bindings = tb.bind_targets(DECLARED, cubes, RULES)
        self.assertEqual(["cube_near", "cube_far"], [b.target_id for b in bindings])
        self.assertEqual([2, 0], [b.index for b in bindings])
        self.assertEqual([A_START, B_START], [b.bound_pad_m for b in bindings])
        self.assertEqual([tb.MODE_ARM_ONLY, tb.MODE_BASE_ADVANCE], [b.mode for b in bindings])

    def test_two_base_advance_candidates_are_not_unique(self):
        cubes = [seen(A_START), seen(B_START), seen((0.080, NEAR_EDGE_Y - 0.035))]
        self.assertEqual(tb.MODE_BASE_ADVANCE, cubes[2].mode)
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.bind_targets(DECLARED, cubes, RULES)
        self.assertEqual(tb.REFUSED_CANDIDATE_NOT_UNIQUE, refused.exception.reason_code)
        self.assertEqual(2, refused.exception.values["matching_candidates"])

    def test_an_arm_only_target_that_measures_base_advance_is_refused(self):
        # Decision D5: the target that needs no capability is never driven. Its only cube measures BASE_ADVANCE, so it has none.
        cubes = [seen((0.020, NEAR_EDGE_Y - 0.020)), seen(B_START)]
        self.assertEqual([tb.MODE_BASE_ADVANCE] * 2, [cube.mode for cube in cubes])
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.bind_targets(DECLARED, cubes, RULES)
        self.assertEqual(tb.REFUSED_NO_CANDIDATE, refused.exception.reason_code)

    def test_a_cube_already_on_the_pad_is_refused_never_done_already_placed(self):
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.bind_targets(DECLARED, [seen(A_PLACED), seen(B_START)], RULES)
        self.assertEqual(tb.REFUSED_NO_CANDIDATE, refused.exception.reason_code)
        self.assertEqual(1, refused.exception.values["placed_cubes"])

    def test_targets_that_share_a_mode_cannot_be_bound(self):
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.bind_targets((tb.DeclaredTarget("cube_1", False), tb.DeclaredTarget("cube_2", False)), [seen(A_START), seen((-0.1, NEAR_EDGE_Y + 0.004))], RULES)
        self.assertEqual(tb.REFUSED_TARGETS_SHARE_A_MODE, refused.exception.reason_code)

    def test_a_cube_with_no_bounded_placement_binds_to_nothing(self):
        unreachable = seen((0.0, NEAR_EDGE_Y - 0.200))
        self.assertIsNone(unreachable.mode)
        self.assertEqual(2, len(tb.bind_targets(DECLARED, [unreachable, seen(A_START), seen(B_START)], RULES)))
        with self.assertRaises(tb.TargetRefused):
            tb.bind_targets(DECLARED, [seen(A_START), unreachable], RULES)


# =====================================================================================================================
class SelectionTest(unittest.TestCase):
    def test_a_placed_and_b_outside_b_is_selected(self):
        a_now, b_now = seen(A_PLACED, offset_mm=(3.0, -2.0)), seen(B_START, offset_mm=(4.0, -6.0))
        for cubes, index in (([a_now, b_now], 1), ([b_now, a_now], 0)):
            selection = tb.select_target(B_START, tb.MODE_BASE_ADVANCE, cubes, RULES)
            self.assertEqual(index, selection.index)
            self.assertEqual(b_now.center_pad_m, selection.seen_pad_m)
            self.assertAlmostEqual(52.0 ** 0.5, selection.distance_mm, places=6)
            self.assertEqual(tb.MODE_BASE_ADVANCE, selection.mode)

    def test_only_a_visible_is_a_refusal_never_done_already_placed(self):
        # The proven SEARCH would read this scene as DONE_ALREADY_PLACED, a clean exit, and finish on the cube A placed.
        only_a = [seen(A_PLACED, offset_mm=(1.0, 1.0))]
        self.assertGreaterEqual(only_a[0].margin_mm, cycle.PLACED_MIN_EDGE_MARGIN_MM)
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.select_target(B_START, tb.MODE_BASE_ADVANCE, only_a, RULES)
        self.assertEqual(tb.REFUSED_TARGET_NOT_FOUND, refused.exception.reason_code)
        self.assertEqual((0, 1), (refused.exception.values["candidates"], refused.exception.values["placed_cubes"]))

    def test_a_mode_mismatch_is_refused(self):
        # A cube 30 mm from B's bound position that measures ARM_ONLY is not B, whatever else it is.
        impostor = seen((B_START[0], NEAR_EDGE_Y))
        self.assertEqual(tb.MODE_ARM_ONLY, impostor.mode)
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.select_target(B_START, tb.MODE_BASE_ADVANCE, [seen(A_PLACED), impostor], RULES)
        self.assertEqual(tb.REFUSED_MODE_MISMATCH, refused.exception.reason_code)

    def test_a_second_candidate_inside_the_match_radius_is_ambiguity_not_a_choice(self):
        cubes = [seen(B_START), seen((B_START[0], NEAR_EDGE_Y))]  # the second one even measures the wrong mode
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.select_target(B_START, tb.MODE_BASE_ADVANCE, cubes, RULES)
        self.assertEqual(tb.REFUSED_TARGET_NOT_UNIQUE, refused.exception.reason_code)

    def test_the_target_beyond_the_match_radius_is_not_found(self):
        moved = seen(B_START, offset_mm=(RULES.r_match_mm + 1.0, 0.0))
        self.assertEqual(tb.MODE_BASE_ADVANCE, moved.mode)
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.select_target(B_START, tb.MODE_BASE_ADVANCE, [moved], RULES)
        self.assertEqual(tb.REFUSED_TARGET_NOT_FOUND, refused.exception.reason_code)
        self.assertAlmostEqual(RULES.r_match_mm + 1.0, refused.exception.values["nearest_candidate_mm"], places=6)

    def test_each_execution_selects_through_its_mission_target(self):
        directory = scratch(self)
        first = tb.parse_mission_target(first_document(directory))
        bindings, selection = tb.select_for_execution(first, [seen(B_START), seen(A_START)], RULES)
        self.assertEqual((1, 0.0, A_START), (selection.index, selection.distance_mm, selection.seen_pad_m))
        self.assertEqual(["cube_near", "cube_far"], [b.target_id for b in bindings])
        later = tb.parse_mission_target(later_document(directory))
        bindings, selection = tb.select_for_execution(later, [seen(A_PLACED), seen(B_START, offset_mm=(2.0, 2.0))], RULES)
        self.assertIsNone(bindings)
        self.assertEqual(1, selection.index)
        with self.assertRaises(tb.TargetRefused):
            tb.select_for_execution(later, [seen(A_PLACED)], RULES)


# =====================================================================================================================
class PlacementTest(unittest.TestCase):
    """THE RELEASE AGREEMENT (the ruling of 2026-09-29): PLACED is the block on the pad at THIS run's release; every other
    block on the pad is ignored. The claim-rule pins this class held until the ruling are changed where the ruling changes
    the answer, each one saying so."""

    def test_placed_ignores_the_earlier_a(self):
        exit_view = [seen(A_PLACED, offset_mm=(-4.0, 2.0)), seen(B_PLACED)]
        placement = tb.attribute_placement(exit_view, release_at(B_PLACED), RULES)
        self.assertTrue(placement.placed)
        self.assertEqual((1, B_PLACED, 2, None), (placement.placed_index, placement.placed_pad_m, placement.on_pad_count, placement.reason_code))

    def test_the_run_never_finishes_on_the_already_placed_cube(self):
        short = seen((B_PLACED[0], NEAR_EDGE_Y + 0.010))  # B released 10 mm inside: on the pad outline, not placed
        placement = tb.attribute_placement([seen(A_PLACED), short], release_at(B_PLACED), RULES)
        self.assertFalse(placement.placed)
        self.assertEqual((tb.PLACED_NO_CUBE_AT_RELEASE, 1, None), (placement.reason_code, placement.on_pad_count, placement.placed_pad_m))
        self.assertGreater(placement.release_distance_mm, tb.RELEASE_AGREEMENT_MM, "A is 80 mm from B's release")

    def test_a_disturbed_or_removed_earlier_block_never_fails_the_run(self):
        # CHANGED by the ruling ("Human can subtract blocks from the pad"): these scenes were mission_earlier_target_disturbed.
        pushed = seen(A_PLACED, offset_mm=(45.0, 0.0))
        knocked = seen((A_PLACED[0], NEAR_EDGE_Y + 0.012))
        for exit_view in ([pushed, seen(B_PLACED)], [knocked, seen(B_PLACED)], [seen(B_PLACED)]):
            with self.subTest(blocks=len(exit_view)):
                placement = tb.attribute_placement(exit_view, release_at(B_PLACED), RULES)
                self.assertEqual((True, B_PLACED, None), (placement.placed, placement.placed_pad_m, placement.reason_code))

    def test_several_blocks_on_the_pad_are_not_an_ambiguity(self):
        # CHANGED by the ruling: three placed blocks used to be mission_placed_more_than_one_unclaimed_cube.
        placement = tb.attribute_placement([seen(A_PLACED), seen(B_PLACED), seen((0.12, 0.0))], release_at(B_PLACED), RULES)
        self.assertEqual((True, 1, 3), (placement.placed, placement.placed_index, placement.on_pad_count))

    def test_named_residual_a_block_left_on_the_pad_at_this_runs_release_is_credited_when_this_runs_block_left_the_pad(self):
        """THE ACCEPTED RESIDUAL (operator ruling (ii) of 2026-09-29): this run's block ends off the pad and a block an earlier
        run placed stands within the tolerance of this run's release. It is credited: a false PLACED the operator accepted."""
        earlier = seen(B_PLACED, offset_mm=(0.0, 20.0))
        fell = seen((B_PLACED[0], NEAR_EDGE_Y - 0.010))
        placement = tb.attribute_placement([earlier, fell], release_at(B_PLACED), RULES)
        self.assertEqual((True, earlier.center_pad_m), (placement.placed, placement.placed_pad_m))


# =====================================================================================================================
class CubeGeometryRadiiTest(unittest.TestCase):
    """The derived radii (THE TWO RADII, THE DERIVED VALUE): half a cube edge for both, and every recorded figure the
    basis names is fed through the proven functions and the predicates WITH that value. The figures are the ones the
    evidence documents record (5.2 mm, 0.3 mm, 1.6 mm, 1.8 mm along and 2.7 mm sideways, 17.96 mm inside, 37 to 45 mm
    outside); nothing here measures anything, and observed variation is not a calibrated confidence interval."""

    RADII = tb.radii_from_cube_geometry()
    RULES = tb.Rules(placed_min_margin_mm=cycle.PLACED_MIN_EDGE_MARGIN_MM, r_claim_mm=RADII.r_claim_mm, r_match_mm=RADII.r_match_mm)

    def test_the_value_is_half_the_controllers_cube_edge_for_both_radii(self):
        self.assertEqual(cycle.CUBE_EDGE_MM / 2.0, self.RADII.r_claim_mm)
        self.assertEqual(cycle.CUBE_EDGE_MM / 2.0, self.RADII.r_match_mm)
        self.assertEqual(12.7, tb.CUBE_HALF_EDGE_MM)
        self.assertIs(tb.Radii, type(self.RADII))
        self.assertEqual(self.RADII, tb.radii_from_cube_geometry(), "a pure constructor: the same answer every time")

    def test_two_touching_blocks_are_never_both_within_the_match_radius(self):
        # Face to face, the centres are one edge apart: two blocks off the pad can never both lie within half an edge of one
        # bound position, except at the one degenerate boundary (touching, diametrically opposed, each EXACTLY 12.7 mm away).
        # Off that boundary by 0.01 mm, exactly one of the touching pair is the bound block. (Until the ruling of 2026-09-29
        # this pinned the retired claim radius the same way.)
        for shift in (0.01, -0.01, 3.0):
            pair = [seen(B_START, offset_mm=(-tb.CUBE_HALF_EDGE_MM + shift, 0.0)), seen(B_START, offset_mm=(tb.CUBE_HALF_EDGE_MM + shift, 0.0))]
            self.assertAlmostEqual(cycle.CUBE_EDGE_MM, tb.distance_mm(pair[0].center_pad_m, pair[1].center_pad_m), places=6)
            self.assertEqual(1, sum(tb.distance_mm(cube.center_pad_m, B_START) <= self.RULES.r_match_mm for cube in pair), shift)
            selection = tb.select_target(B_START, tb.MODE_BASE_ADVANCE, pair, self.RULES)
            self.assertLessEqual(selection.distance_mm, self.RULES.r_match_mm)

    def test_every_recorded_re_reading_of_an_unmoved_block_is_still_the_bound_one(self):
        recorded = {"advanced vs returned viewpoint": (5.2, 0.0), "same viewpoint": (0.3, 0.0), "hem effect": (1.6, 0.0),
                    "base return residual along and sideways": (1.8, 2.7), "residual sideways alone": (0.0, 2.7)}
        for name, offset in recorded.items():
            with self.subTest(name=name):
                again = seen(B_START, offset_mm=offset)
                self.assertLessEqual(tb.distance_mm(again.center_pad_m, B_START), tb.CUBE_HALF_EDGE_MM)
                self.assertEqual(0, tb.select_target(B_START, tb.MODE_BASE_ADVANCE, [again], self.RULES).index)
        # The largest recorded figure, 5.2 mm, leaves 7.5 mm of the radius; a reading that far off is not in the record.
        self.assertLess(5.2, tb.CUBE_HALF_EDGE_MM)

    def test_the_two_cube_scene_has_no_ambiguity_at_four_radii(self):
        # A placed cube exactly at the PLACED margin (the nearest a placed cube can stand to the outline) and a BASE_ADVANCE
        # cube at each recorded start distance outside it: at least 54.96 mm apart, more than four radii (50.8 mm).
        placed_at = (0.0, NEAR_EDGE_Y + cycle.PLACED_MIN_EDGE_MARGIN_MM / 1000.0)
        placed = seen(placed_at)
        self.assertTrue(tb.is_placed(placed, self.RULES))
        for outside_mm in (37.0, 41.0, 45.0):
            with self.subTest(outside_mm=outside_mm):
                start = (0.0, NEAR_EDGE_Y - outside_mm / 1000.0)
                far = seen(start)
                self.assertEqual(tb.MODE_BASE_ADVANCE, far.mode, "the recorded starts measure BASE_ADVANCE by the proven placement_plan")
                self.assertGreater(tb.distance_mm(placed_at, start), 4.0 * tb.CUBE_HALF_EDGE_MM)
                self.assertGreaterEqual(tb.distance_mm(placed_at, start), cycle.PLACED_MIN_EDGE_MARGIN_MM + 37.0)
                # A later SEARCH: the placed cube is barred (it is on the pad); the far cube is the one
                # candidate within the match radius of its binding, and the placed cube alone is a refusal.
                selection = tb.select_target(start, tb.MODE_BASE_ADVANCE, [placed, seen(start, offset_mm=(5.2, 0.0))], self.RULES)
                self.assertEqual((1, tb.MODE_BASE_ADVANCE), (selection.index, selection.mode))
                self.assertAlmostEqual(5.2, selection.distance_mm, places=6)
                with self.assertRaises(tb.TargetRefused) as refused:
                    tb.select_target(start, tb.MODE_BASE_ADVANCE, [placed], self.RULES)
                self.assertEqual(tb.REFUSED_TARGET_NOT_FOUND, refused.exception.reason_code)
                # The first execution binds both from this scene (the edge cube A and the far cube), never the placed one.
                bindings = tb.bind_targets(DECLARED, [placed, far, seen(A_START)], self.RULES)
                self.assertEqual([2, 1], [b.index for b in bindings])

    def test_the_derived_radii_travel_through_the_mission_target_file(self):
        directory = scratch(self)
        document = tb.mission_target_document(mission_id=MISSION_ID, target_id="cube_near", run_id=RUN_A, targets=tb.declared_targets(MISSION_TARGETS),
                                              capability=None, result_path=os.path.join(directory, "result.json"), radii=self.RADII.to_document())
        path = os.path.join(directory, "mission-target.json")
        tb.write_mission_target(path, document)
        loaded = tb.load_mission_target(path)
        self.assertEqual(self.RADII, loaded.radii)
        self.assertEqual(self.RULES, loaded.rules(cycle.PLACED_MIN_EDGE_MARGIN_MM))
        self.assertLessEqual(len(tb.CUBE_HALF_EDGE_BASIS), 160)


# =====================================================================================================================
class APrestagedBlockTest(unittest.TestCase):
    """A block on the pad before the mission (the gap G1 of the WP2 plan) under the pad policy of 2026-09-29: it is never
    bookkept (OCCUPIED is retired), it is never credited to a run whose own block is not at its release, and moving or
    removing it never fails a run. OccupiedTest pinned the retired bookkeeping until the ruling."""

    PRESTAGED = (0.100, 0.020)  # well inside the pad, 160 mm from B's release

    def test_a_dropped_block_is_not_credited_with_the_prestaged_one(self):
        dropped = seen((B_PLACED[0], NEAR_EDGE_Y + 0.010))
        placement = tb.attribute_placement([seen(self.PRESTAGED), dropped], release_at(B_PLACED), RULES)
        self.assertEqual((False, tb.PLACED_NO_CUBE_AT_RELEASE, 1), (placement.placed, placement.reason_code, placement.on_pad_count))

    def test_the_block_placed_is_credited_and_a_moved_or_removed_prestaged_block_changes_nothing(self):
        # CHANGED by the ruling: a pre-staged block moved 60 mm was mission_earlier_target_disturbed.
        for view in ([seen(self.PRESTAGED, offset_mm=(2.0, -1.0)), seen(B_PLACED)], [seen(self.PRESTAGED, offset_mm=(60.0, 0.0)), seen(B_PLACED)], [seen(B_PLACED)]):
            with self.subTest(blocks=len(view)):
                placed = tb.attribute_placement(view, release_at(B_PLACED), RULES)
                self.assertEqual((True, B_PLACED), (placed.placed, placed.placed_pad_m))


# =====================================================================================================================
class MissionTargetFileTest(unittest.TestCase):
    def setUp(self):
        self.dir = scratch(self)
        self.path = os.path.join(self.dir, "mission-target.json")

    def refused(self, document, code):
        with self.assertRaises(tb.MissionFileRefused) as caught:
            tb.parse_mission_target(document)
        self.assertEqual(code, caught.exception.reason_code, caught.exception)
        return caught.exception

    def test_a_first_execution_file_round_trips_with_nothing_bound(self):
        written = tb.write_mission_target(self.path, first_document(self.dir))
        loaded = tb.load_mission_target(self.path)
        self.assertEqual(written, loaded)
        self.assertEqual(("cube_near", 0, True, tb.MODE_ARM_ONLY, None, ()), (loaded.target_id, loaded.index, loaded.first_execution, loaded.mode,
                                                                               loaded.bound_pad_m, loaded.earlier_placed))
        self.assertIsNone(loaded.capability)
        self.assertEqual(["mission-target.json"], os.listdir(self.dir))  # the temporary sibling was renamed, not left behind

    def test_a_later_file_carries_the_bindings_the_placed_positions_and_the_capability(self):
        tb.write_mission_target(self.path, later_document(self.dir))
        loaded = tb.load_mission_target(self.path)
        self.assertEqual((1, False, tb.MODE_BASE_ADVANCE, B_START, (A_PLACED,)), (loaded.index, loaded.first_execution, loaded.mode, loaded.bound_pad_m,
                                                                                    loaded.earlier_placed))
        self.assertEqual("5a" * 16, loaded.capability.slot_uuid)

    def test_the_variable_absent_is_the_legacy_path_and_reads_nothing(self):
        self.assertIsNone(tb.mission_target_from_environ({}))
        self.assertIsNone(tb.mission_target_from_environ({"KC_HOST_LEASE": "7", "KC_RUN_ID": RUN_A}))
        tb.write_mission_target(self.path, first_document(self.dir))
        self.assertEqual("cube_near", tb.mission_target_from_environ({tb.MISSION_TARGET_ENV: self.path}).target_id)

    def test_a_set_variable_that_is_empty_relative_or_missing_is_a_refusal(self):
        for value, code in (("", "binding_path_not_absolute"), ("mission-target.json", "binding_path_not_absolute"),
                            (self.dir + "/../x.json", "binding_path_not_absolute"), (os.path.join(self.dir, "absent.json"), "binding_file_missing")):
            with self.assertRaises(tb.MissionFileRefused) as caught:
                tb.mission_target_from_environ({tb.MISSION_TARGET_ENV: value})
            self.assertEqual(code, caught.exception.reason_code, value)

    def test_unknown_and_missing_keys_are_refused(self):
        document = first_document(self.dir)
        self.refused({**document, "extra": 1}, "binding_unknown_key")
        self.refused({k: v for k, v in document.items() if k != "run_id"}, "binding_missing_key")
        self.refused({**document, "targets": [{**document["targets"][0], "label": "x"}, document["targets"][1]]}, "binding_target_unknown_key")
        self.refused({**document, "targets": [{"target_id": "cube_near", "requires_capability": False, "bound_pad_m": None, "placed_pad_m": None}, document["targets"][1]]},
                     "binding_target_missing_key")
        later = later_document(self.dir)
        self.refused({**later, "capability": {**later["capability"], "pid": 4}}, "binding_capability_unknown_key")
        self.refused({**later, "capability": {k: v for k, v in later["capability"].items() if k != "capsule"}}, "binding_capability_missing_key")
        self.refused([document], "binding_not_an_object")

    def test_types_and_shapes_are_checked_exactly(self):
        document = first_document(self.dir)
        near, far = document["targets"]
        for change, code in (({"schema": "kc.mission"}, "binding_schema_not_known"), ({"v": True}, "binding_version_not_known"),
                             ({"v": 1}, "binding_version_not_known"), ({"v": 2}, "binding_version_not_known"), ({"v": "3"}, "binding_version_not_known"),
                             ({"requires_capability": 0}, "binding_requires_capability_not_allowed"), ({"requires_capability": "false"}, "binding_requires_capability_not_allowed"),
                             ({"requires_capability": "Auto"}, "binding_requires_capability_not_allowed"), ({"requires_capability": None}, "binding_requires_capability_not_allowed"),
                             ({"mission_id": "mission-0123456789AB"}, "binding_mission_id_not_allowed"),
                             ({"mission_id": "0123456789ab"}, "binding_mission_id_not_allowed"),
                             ({"target_id": "cube/near"}, "binding_target_id_not_allowed"), ({"run_id": "../run"}, "binding_run_id_not_allowed"),
                             ({"run_id": ""}, "binding_run_id_not_allowed"), ({"targets": []}, "binding_targets_not_a_list"),
                             ({"targets": {"cube_near": near}}, "binding_targets_not_a_list"), ({"targets": [near, near]}, "binding_target_id_repeated"),
                             ({"targets": [dict(near, requires_capability=1), far]}, "binding_requires_capability_not_allowed"),
                             ({"targets": [dict(near, effective_mode="auto"), far]}, "binding_effective_mode_not_allowed"),
                             ({"targets": [dict(near, effective_mode=tb.MODE_ARM_ONLY), far]}, "binding_effective_mode_inconsistent"),
                             ({"target_id": "cube_other"}, "binding_target_not_declared")):
            self.refused({**document, **change}, code)
        self.refused({**document, "targets": [dict(near, requires_capability=False)] + [dict(far, target_id=f"cube_{i}") for i in range(8)]},
                     "binding_targets_not_a_list")
        later = later_document(self.dir)
        near_l, far_l = later["targets"]
        for bad in ([True, 0.0], [0.1], [0.1, 0.2, 0.3], "0.1,0.2", [tb.MAX_PAD_COORD_M + 0.1, 0.0], [float("nan"), 0.0], {"x": 0.1, "y": 0.2}):
            self.refused({**later, "targets": [dict(near_l, bound_pad_m=bad), far_l]}, "binding_position_not_allowed")
        self.refused({**later, "capability": {**later["capability"], "slot_uuid": "5A" * 16}}, "binding_slot_uuid_not_allowed")
        self.refused({**later, "capability": {**later["capability"], "capsule": "c0" * 31}}, "binding_capsule_not_allowed")

    def test_paths_are_absolute_and_normalized(self):
        document, later = first_document(self.dir), later_document(self.dir)
        for bad in ("result.json", "./result.json", self.dir + "/../result.json", self.dir + "/./result.json", self.dir + "//result.json",
                    self.dir + "/", "//" + self.dir.lstrip("/") + "/r.json", self.dir + "/r\x00.json", "/", "/" + "x" * tb.MAX_PATH_CHARS, 7, None):
            self.refused({**document, "result_path": bad}, "binding_path_not_absolute")
        for field in ("admin_probe", "admin_socket"):
            self.refused({**later, "capability": {**later["capability"], field: "admin.sock"}}, "binding_path_not_absolute")
        for bad in ("ipc://observations.ipc", "ipc://" + self.dir + "/../o.ipc", "tcp://*:5561", self.dir + "/o.ipc"):
            self.refused({**later, "capability": {**later["capability"], "observation_endpoint": bad}}, "binding_endpoint_not_allowed")
        self.refused({**later, "capability": {**later["capability"], "command_endpoint": later["capability"]["observation_endpoint"]}},
                     "binding_endpoints_identical")

    def test_the_sequence_the_coordinator_guarantees_is_checked(self):
        document, later = first_document(self.dir), later_document(self.dir)
        near, far = document["targets"]
        near_l, far_l = later["targets"]
        self.refused({**document, "targets": [dict(near, bound_pad_m=list(A_START)), far]}, "binding_bound_positions_inconsistent")
        self.refused({**document, "targets": [dict(near, placed_pad_m=list(A_PLACED)), far]}, "binding_placed_positions_inconsistent")
        self.refused({**later, "targets": [dict(near_l, bound_pad_m=None), far_l]}, "binding_bound_positions_inconsistent")
        self.refused({**later, "targets": [dict(near_l, placed_pad_m=None), far_l]}, "binding_placed_positions_inconsistent")
        self.refused({**later, "targets": [near_l, dict(far_l, placed_pad_m=list(B_PLACED))]}, "binding_placed_positions_inconsistent")

    def test_a_run_carries_the_capability_only_for_a_target_that_declares_it(self):
        document, later = first_document(self.dir), later_document(self.dir)
        self.refused({**document, "capability": capability_block(self.dir)}, "binding_capability_presence_disagrees")
        self.refused({**later, "requires_capability": False, "capability": None}, "binding_requires_capability_disagrees")
        # Declared and NOT carried is accepted: a run that has not measured its cube yet is started without the
        # capability, and its own SEARCH refuses when the cube measures BASE_ADVANCE (the controller's MISSION MODE).
        uncarried = tb.parse_mission_target({**later, "capability": None})
        self.assertEqual((True, False, tb.MODE_BASE_ADVANCE), (uncarried.requires_capability, uncarried.carries_capability, uncarried.mode))
        self.assertTrue(tb.parse_mission_target(later).carries_capability)

    def test_the_radii_are_carried_checked_and_never_filled_in(self):
        document = first_document(self.dir)
        loaded = tb.parse_mission_target(document)
        self.assertEqual(tb.Radii(TEST_R_CLAIM_MM, TEST_R_MATCH_MM, TEST_RADII["basis"]), loaded.radii)
        self.assertEqual(RULES, loaded.rules(cycle.PLACED_MIN_EDGE_MARGIN_MM))
        unqualified = tb.parse_mission_target({**document, "radii": None})
        self.assertIsNone(unqualified.radii)
        with self.assertRaises(ValueError):
            unqualified.rules(cycle.PLACED_MIN_EDGE_MARGIN_MM)
        self.refused({k: v for k, v in document.items() if k != "radii"}, "binding_missing_key")
        for bad in (0, 0.0, -1.0, float("nan"), True, "30", None, tb.RADIUS_MAX_MM + 0.1):
            with self.subTest(radius=bad):
                self.refused({**document, "radii": {**TEST_RADII, "r_claim_mm": bad}}, "binding_radius_not_allowed")
                self.refused({**document, "radii": {**TEST_RADII, "r_match_mm": bad}}, "binding_radius_not_allowed")
        for basis in ("", " leading space", "/srv/qualification/radii.md", "see 192.0.2.10", "x" * 161, 7, None, "ok\nnewline"):
            with self.subTest(basis=basis):
                self.refused({**document, "radii": {**TEST_RADII, "basis": basis}}, "binding_radii_basis_not_allowed")
        self.refused({**document, "radii": {**TEST_RADII, "default": True}}, "binding_radii_unknown_key")
        self.refused({**document, "radii": {"r_claim_mm": 30.0, "r_match_mm": 40.0}}, "binding_radii_missing_key")
        self.refused({**document, "radii": [30.0, 40.0]}, "binding_radii_not_an_object")

    def test_the_radius_bound_and_the_basis_alphabet_are_the_configurations(self):
        source = ROOT / "packages" / "demo-runtime" / "demo_runtime" / "config.py"
        self.assertEqual(_assigned(source, "RADIUS_MAX_MM").value, tb.RADIUS_MAX_MM)
        self.assertEqual(_assigned(source, "_RADII_BASIS").args[0].value, tb.RADII_BASIS_RE.pattern)
        self.assertEqual(_assigned(source, "_DOTTED_QUAD").args[0].value, tb._DOTTED_QUAD.pattern)

    def test_the_file_itself_is_checked(self):
        text = json.dumps(first_document(self.dir))
        cases = (("nan.json", text.replace('"placed_pad_m": null', '"placed_pad_m": NaN', 1), "binding_number_not_finite"),
                 ("dup.json", text[:-1] + ', "run_id": "' + RUN_B + '"}', "binding_duplicate_key"),
                 ("torn.json", text[:-5], "binding_not_json"),
                 ("large.json", text[:-1] + ', "pad": "' + "x" * tb.MAX_FILE_BYTES + '"}', "binding_file_too_large"))
        for name, body, code in cases:
            with self.assertRaises(tb.MissionFileRefused) as caught:
                tb.load_mission_target(write_text(os.path.join(self.dir, name), body))
            self.assertEqual(code, caught.exception.reason_code, name)
        with open(os.path.join(self.dir, "latin.json"), "wb") as handle:
            handle.write(b'{"schema": "\xff"}')
        with self.assertRaises(tb.MissionFileRefused) as caught:
            tb.load_mission_target(os.path.join(self.dir, "latin.json"))
        self.assertEqual("binding_not_json", caught.exception.reason_code)
        os.mkfifo(os.path.join(self.dir, "fifo.json"))
        os.mkdir(os.path.join(self.dir, "dir.json"))
        real = write_text(os.path.join(self.dir, "real.json"), text)
        os.symlink(real, os.path.join(self.dir, "link.json"))
        for name, code in (("fifo.json", "binding_not_a_regular_file"), ("dir.json", "binding_not_a_regular_file"), ("link.json", "binding_file_unreadable")):
            with self.assertRaises(tb.MissionFileRefused) as caught:
                tb.load_mission_target(os.path.join(self.dir, name))
            self.assertEqual(code, caught.exception.reason_code, name)

    def test_the_result_path_is_never_the_binding_file(self):
        document = {**first_document(self.dir), "result_path": self.path}
        with self.assertRaises(tb.MissionFileRefused) as caught:
            tb.write_mission_target(self.path, document)
        self.assertEqual("binding_result_path_is_the_binding_file", caught.exception.reason_code)
        write_text(self.path, json.dumps(document))
        with self.assertRaises(tb.MissionFileRefused) as caught:
            tb.load_mission_target(self.path)
        self.assertEqual("binding_result_path_is_the_binding_file", caught.exception.reason_code)

    def test_a_refusal_never_echoes_a_value(self):
        marker = "private-" + uuid.uuid4().hex
        error = self.refused({**first_document(self.dir), "result_path": marker + "/result.json"}, "binding_path_not_absolute")
        self.assertNotIn(marker, str(error))
        error = self.refused({**first_document(self.dir), marker: 1}, "binding_unknown_key")
        self.assertNotIn(marker, str(error))

    def test_the_builder_refuses_what_the_parser_refuses(self):
        with self.assertRaises(tb.MissionFileRefused) as caught:
            tb.mission_target_document(mission_id=MISSION_ID, target_id="cube_other", run_id=RUN_A, targets=tb.declared_targets(MISSION_TARGETS),
                                       capability=None, result_path=os.path.join(self.dir, "r.json"))
        self.assertEqual("binding_target_not_declared", caught.exception.reason_code)
        with self.assertRaises(tb.MissionFileRefused):
            tb.write_mission_target("relative.json", first_document(self.dir))
        self.assertEqual([], os.listdir(self.dir))


# =====================================================================================================================
class MissionResultFileTest(unittest.TestCase):
    """The whole two-target exchange through the files, then each way a result is refused or claims nothing."""

    def setUp(self):
        self.dir = scratch(self)
        self.first = tb.parse_mission_target(first_document(self.dir))
        self.later = tb.parse_mission_target(later_document(self.dir))

    def first_result(self, **changes):
        bindings, selection = tb.select_for_execution(self.first, [seen(A_START), seen(B_START)], RULES)
        placement = tb.attribute_placement([seen(A_PLACED), seen(B_START)], release_at(A_PLACED), RULES)
        fields = dict(stage=tb.STAGE_FINISHED, bindings=bindings, selection=selection, search_observation_id="obs-0001", outcome="placed_new",
                      final_state="PLACED", predicates={"object_secured": True, "placed": True, "clearance_target_met": False, "base_returned": True},
                      placement=placement, exit_observation_id="obs-0042")
        fields.update(changes)
        return tb.result_document(self.first, **fields)

    def later_result(self, capability=None, **changes):
        _bindings, selection = tb.select_for_execution(self.later, [seen(A_PLACED), seen(B_START)], RULES)
        placement = tb.attribute_placement([seen(A_PLACED), seen(B_PLACED)], release_at(B_PLACED), RULES)
        fields = dict(stage=tb.STAGE_FINISHED, selection=selection, search_observation_id="obs-0101", outcome="placed_new", final_state="BASE_RETURNED",
                      predicates={"object_secured": True, "placed": True, "base_returned": True}, placement=placement, exit_observation_id="obs-0142",
                      capability=capability if capability is not None else {"generation": 3, "pid": 4242, "accepted": 17, "applied": 15, "dropped": {"stale_timestamp": 2}})
        fields.update(changes)
        return tb.result_document(self.later, **fields)

    def refused(self, document, expected, code):
        with self.assertRaises(tb.MissionFileRefused) as caught:
            tb.parse_result(document, expected)
        self.assertEqual(code, caught.exception.reason_code, caught.exception)

    def test_the_two_target_exchange_through_the_files(self):
        # FIRST execution: bound before motion, then finished, then the next target's file is made from it.
        bindings, selection = tb.select_for_execution(self.first, [seen(A_START), seen(B_START)], RULES)
        bound = tb.write_result(self.first, tb.result_document(self.first, stage=tb.STAGE_BOUND, bindings=bindings, selection=selection,
                                                               search_observation_id="obs-0001"))
        self.assertEqual(bound, tb.load_result(self.first))
        self.assertFalse(tb.result_claims_placement(tb.load_result(self.first), self.first))
        tb.write_result(self.first, self.first_result())
        finished = tb.load_result(self.first)
        self.assertTrue(tb.result_claims_placement(finished, self.first))
        self.assertEqual({"object_secured": True, "placed": True, "base_returned": True}, finished["predicates"])
        self.assertEqual(["result-a.json"], os.listdir(self.dir))
        targets = tb.targets_after_placement(self.first, finished)
        self.assertEqual([list(A_START), list(B_START)], [t["bound_pad_m"] for t in targets])
        self.assertEqual([list(A_PLACED), None], [t["placed_pad_m"] for t in targets])
        second = tb.mission_target_document(mission_id=MISSION_ID, target_id="cube_far", run_id=RUN_B, targets=targets,
                                            capability=capability_block(self.dir), result_path=os.path.join(self.dir, "result-b.json"))
        path = os.path.join(self.dir, "mission-target-b.json")
        tb.write_mission_target(path, second)
        second = tb.load_mission_target(path)
        self.assertEqual((B_START, (A_PLACED,)), (second.bound_pad_m, second.earlier_placed))
        # SECOND execution: the placed A is on the pad and ignored, B is carried, and PLACED is the block at B's release.
        no_bindings, selection = tb.select_for_execution(second, [seen(A_PLACED), seen(B_START)], RULES)
        self.assertIsNone(no_bindings)
        tb.write_result(second, tb.result_document(
            second, stage=tb.STAGE_FINISHED, selection=selection, outcome="placed_new", predicates={"object_secured": True, "placed": True, "base_returned": True},
            placement=tb.attribute_placement([seen(A_PLACED), seen(B_PLACED)], release_at(B_PLACED), RULES),
            capability={"generation": 3, "pid": 4242, "accepted": 17, "applied": 17, "dropped": {}}))
        result = tb.load_result(second)
        self.assertTrue(tb.result_claims_placement(result, second))
        self.assertEqual(list(B_PLACED), result["placement"]["placed_pad_m"])

    def test_an_echo_mismatch_is_refused(self):
        document = self.first_result()
        self.refused({**document, "mission_id": "mission-ba9876543210"}, self.first, "result_mission_mismatch")
        self.refused({**document, "target_id": "cube_far"}, self.first, "result_target_mismatch")
        self.refused({**document, "run_id": RUN_B}, self.first, "result_run_mismatch")
        self.refused(self.later_result(), self.first, "result_target_mismatch")

    def test_the_shape_is_checked_exactly(self):
        document = self.first_result()
        self.refused({**document, "extra": 1}, self.first, "result_unknown_key")
        self.refused({**document, "schema": tb.MISSION_TARGET_SCHEMA}, self.first, "result_schema_not_known")
        self.refused({**document, "v": 1}, self.first, "result_version_not_known")
        self.refused({**document, "v": 2}, self.first, "result_version_not_known")  # CHANGED: version 3 since the release agreement
        self.assertEqual(3, document["v"])
        self.refused({**document, "stage": "done"}, self.first, "result_stage_not_known")
        self.refused({**document, "outcome": "placed"}, self.first, "result_outcome_not_allowed")
        self.refused({**document, "final_state": "placed"}, self.first, "result_state_not_allowed")
        self.refused({**document, "reason_code": "Search_No_Cube"}, self.first, "result_reason_code_not_allowed")
        self.refused({**document, "predicates": {**document["predicates"], "placed": 1}}, self.first, "result_predicate_not_boolean")
        self.refused({**document, "predicates": {**document["predicates"], "clearance_target_met": True}}, self.first, "result_predicates_unknown_key")
        self.refused({**document, "selection": {**document["selection"], "distance_mm": -1.0}}, self.first, "result_distance_not_allowed")

    def test_the_bound_stage_carries_a_selection_and_nothing_an_ending_knows(self):
        bindings, selection = tb.select_for_execution(self.first, [seen(A_START), seen(B_START)], RULES)
        with self.assertRaises(tb.MissionFileRefused) as caught:
            tb.result_document(self.first, stage=tb.STAGE_BOUND, bindings=bindings, selection=selection, outcome="placed_new")
        self.assertEqual("result_outcome_not_allowed", caught.exception.reason_code)
        for changes in (dict(selection=None), dict(final_state="SEARCH"), dict(predicates={"placed": True})):
            fields = dict(stage=tb.STAGE_BOUND, bindings=bindings, selection=selection)
            fields.update(changes)
            with self.assertRaises(tb.MissionFileRefused) as caught:
                tb.result_document(self.first, **fields)
            self.assertEqual("result_bound_stage_inconsistent", caught.exception.reason_code, changes)

    def test_bindings_belong_to_the_first_execution_and_match_its_declaration(self):
        document = self.first_result()
        self.refused({**self.later_result(), "bindings": document["bindings"]}, self.later, "result_bindings_on_a_later_execution")
        self.refused({**document, "bindings": document["bindings"][:1]}, self.first, "result_bindings_do_not_cover_the_targets")
        self.refused({**document, "bindings": list(reversed(document["bindings"]))}, self.first, "result_binding_does_not_match_the_declaration")
        wrong_mode = [dict(document["bindings"][0], mode=tb.MODE_BASE_ADVANCE), document["bindings"][1]]
        self.refused({**document, "bindings": wrong_mode}, self.first, "result_binding_does_not_match_the_declaration")

    def test_two_bindings_at_one_position_are_one_cube_bound_twice_and_are_refused(self):
        """The reader's side of one cube, one target: a result whose bindings record one position for two targets is not
        read any further, whatever child wrote it."""
        document = self.first_result()
        shared = [document["bindings"][0], dict(document["bindings"][1], bound_pad_m=document["bindings"][0]["bound_pad_m"])]
        self.refused({**document, "bindings": shared}, self.first, "result_bindings_share_a_cube")
        self.refused({**document, "bindings": None}, self.first, "result_selection_without_bindings")

    def test_a_selection_that_is_not_the_binding_or_not_the_declared_mode_is_refused(self):
        document = self.later_result()
        self.refused({**document, "selection": {**document["selection"], "bound_pad_m": [B_START[0] + 0.001, B_START[1]]}}, self.later,
                     "result_selection_not_the_binding")
        self.refused({**document, "selection": {**document["selection"], "mode": tb.MODE_ARM_ONLY}}, self.later, "result_selection_mode_not_declared")
        first = self.first_result()
        moved = [dict(first["bindings"][0], bound_pad_m=[A_START[0], A_START[1] + 0.002]), first["bindings"][1]]
        self.refused({**first, "bindings": moved}, self.first, "result_selection_not_the_binding")

    def test_a_placement_record_must_be_consistent(self):
        document = self.later_result()
        placement = document["placement"]
        for changes in (dict(on_pad_count=0), dict(release_distance_mm=None), dict(release_distance_mm=tb.RELEASE_AGREEMENT_MM + 1.0),
                        dict(release_tolerance_mm=None), dict(placed_pad_m=None), dict(reason_code=tb.PLACED_NO_CUBE_AT_RELEASE), dict(placed=False)):
            self.refused({**document, "placement": {**placement, **changes}}, self.later, "result_placement_inconsistent")
        for changes in (dict(unclaimed_placed_count=1), dict(earlier_targets_undisturbed=True)):  # the retired keys are unknown in version 3
            self.refused({**document, "placement": {**placement, **changes}}, self.later, "result_placement_unknown_key")
        for changes in (dict(release_distance_mm=-1.0), dict(release_tolerance_mm=float("nan")), dict(release_distance_mm=True)):
            self.refused({**document, "placement": {**placement, **changes}}, self.later, "result_distance_not_allowed")

    def test_a_refusal_inside_the_tolerance_is_never_consistent(self):
        # CHANGED (operator ruling (ii) of 2026-09-29): a block on the pad inside the tolerance is always credited, so no
        # refusal code, the retired one for a block listed at SEARCH included, may carry a distance inside it.
        document = self.later_result()
        refused = {**document["placement"], "placed": False, "placed_pad_m": None, "release_distance_mm": 9.5, "release_tolerance_mm": tb.RELEASE_AGREEMENT_MM}
        for code in (tb.PLACED_NO_CUBE_AT_RELEASE, "mission_candidates_incomplete", "mission_placed_only_a_block_" + "listed_at_search"):
            with self.subTest(code=code):
                self.refused({**document, "placement": {**refused, "reason_code": code}}, self.later, "result_placement_inconsistent")

    def test_capability_evidence_belongs_to_a_capability_target_and_is_checked(self):
        evidence = {"generation": 3, "pid": 4242, "accepted": 17, "applied": 17, "dropped": {}}
        self.refused({**self.first_result(), "capability": evidence}, self.first, "result_capability_evidence_without_capability")
        # a run of a capability target that did NOT carry the capability has no relay, so it has no relay evidence either
        uncarried = tb.parse_mission_target({**later_document(self.dir), "capability": None})
        self.refused({**self.later_result(), "capability": evidence}, uncarried, "result_capability_evidence_without_capability")
        self.assertFalse(tb.result_claims_placement(tb.parse_result({**self.later_result(), "capability": None}, uncarried), uncarried),
                         "a capability target is never placed without the capability's own output")
        document = self.later_result()
        for changes, code in ((dict(pid=1), "result_count_not_allowed"), (dict(pid=tb.PID_MAX + 1), "result_count_not_allowed"),
                              (dict(generation=True), "result_count_not_allowed"), (dict(accepted=-1), "result_count_not_allowed"),
                              (dict(applied=-1), "result_count_not_allowed"), (dict(applied=1.0), "result_count_not_allowed"),
                              (dict(applied=18), "result_capability_applied_exceeds_accepted"),
                              (dict(dropped={"Bad Reason": 1}), "result_reason_code_not_allowed"), (dict(dropped={"late": 1.5}), "result_count_not_allowed"),
                              (dict(dropped=[]), "result_capability_dropped_not_allowed"), (dict(uid=1), "result_capability_unknown_key")):
            self.refused({**document, "capability": {**evidence, **changes}}, self.later, code)
        # Version 1 evidence (no `applied`) is not read as "applied 0": the version refuses it before the keys do.
        without_applied = {name: value for name, value in evidence.items() if name != "applied"}
        self.refused({**document, "capability": without_applied}, self.later, "result_capability_missing_key")
        self.refused({**document, "v": 1, "capability": without_applied}, self.later, "result_version_not_known")

    def test_what_does_not_claim_a_placement(self):
        self.assertTrue(tb.result_claims_placement(tb.parse_result(self.later_result(), self.later), self.later))
        self.assertTrue(tb.result_claims_placement(tb.parse_result(self.first_result(), self.first), self.first))
        not_placed = tb.attribute_placement([seen(A_PLACED)], release_at(B_PLACED), RULES)
        cases = ((self.first, self.first_result(outcome="already_placed_noop")), (self.first, self.first_result(outcome="unknown")),
                 (self.first, self.first_result(predicates={"object_secured": True, "placed": True, "base_returned": None})),
                 (self.first, self.first_result(selection=None, bindings=None)),
                 (self.later, self.later_result(placement=not_placed)),
                 (self.later, self.later_result(capability={"generation": 3, "pid": 4242, "accepted": 0, "applied": 0, "dropped": {"wrong_target": 40}})),
                 # C2: the relay counts a command as accepted when it submits it, so a pulse whose every command the gateway
                 # rejected (expired on arrival, replayed) still reads accepted 17; only an APPLIED command is the capability's output.
                 (self.later, self.later_result(capability={"generation": 3, "pid": 4242, "accepted": 17, "applied": 0, "dropped": {}})),
                 (self.later, {**self.later_result(), "capability": None}))
        for expected, document in cases:
            result = tb.parse_result(document, expected)
            self.assertFalse(tb.result_claims_placement(result, expected), document)
            with self.assertRaises(tb.MissionFileRefused):
                tb.targets_after_placement(expected, result)

    def test_a_missing_or_foreign_result_file_is_refused(self):
        with self.assertRaises(tb.MissionFileRefused) as caught:
            tb.load_result(self.first)
        self.assertEqual("result_file_missing", caught.exception.reason_code)
        write_text(self.first.result_path, json.dumps(self.later_result()))
        with self.assertRaises(tb.MissionFileRefused) as caught:
            tb.load_result(self.first)
        self.assertEqual("result_target_mismatch", caught.exception.reason_code)


# =====================================================================================================================
class AutoTargetTest(unittest.TestCase):
    """requires_capability "auto": the target takes whichever placement mode its own SEARCH measures (the ruling of
    2026-09-26: one block, from anywhere, the robot decides). Bound to the nearest feasible block off the pad (THE
    SELECTION RULE, the ruling of 2026-09-29); the measured mode is the target's effective mode from then on."""

    AUTO = (tb.DeclaredTarget("cube", tb.AUTO),)

    def setUp(self):
        self.dir = scratch(self)

    def auto_document(self, *, capability=None, bound=None, effective=None, run_id=RUN_A):
        """The one-auto-target mission's first execution or, once `bound` is given, a later execution of the auto target
        behind a declared target already placed (a mission's index 0 is always a first execution)."""
        targets = [{"target_id": "cube", "requires_capability": tb.AUTO, "effective_mode": effective, "bound_pad_m": bound, "placed_pad_m": None}]
        if bound is not None:
            targets.insert(0, {"target_id": "cube_near", "requires_capability": False, "effective_mode": None, "bound_pad_m": list(A_START), "placed_pad_m": list(A_PLACED)})
        return tb.mission_target_document(mission_id=MISSION_ID, target_id="cube", run_id=run_id, targets=targets, capability=capability,
                                          result_path=os.path.join(self.dir, "result-auto.json"), radii=TEST_RADII)

    def test_the_third_value_and_its_declared_mode(self):
        self.assertEqual("auto", tb.AUTO)
        self.assertEqual((True, False, tb.AUTO), tb.REQUIRES_CAPABILITY_VALUES)
        self.assertEqual(tb.AUTO, tb.declared_mode(tb.AUTO))
        self.assertNotIn(tb.AUTO, tb.MODES, "auto is never a measured mode")
        self.assertEqual(tb.AUTO, self.AUTO[0].mode, "unbound, an auto target has no measured mode yet")
        self.assertEqual(tb.MODE_BASE_ADVANCE, tb.DeclaredTarget("cube", tb.AUTO, B_START, None, tb.MODE_BASE_ADVANCE).mode)

    def test_auto_binds_the_single_arm_only_candidate(self):
        (binding,) = tb.bind_targets(self.AUTO, [seen(A_START)], RULES)
        self.assertEqual(("cube", A_START, tb.MODE_ARM_ONLY, 0), (binding.target_id, binding.bound_pad_m, binding.mode, binding.index))

    def test_auto_binds_the_single_base_advance_candidate(self):
        (binding,) = tb.bind_targets(self.AUTO, [seen(B_START)], RULES)
        self.assertEqual(("cube", B_START, tb.MODE_BASE_ADVANCE, 0), (binding.target_id, binding.bound_pad_m, binding.mode, binding.index))

    def test_two_candidates_the_nearest_feasible_one_is_bound(self):
        # CHANGED by the ruling of 2026-09-29 (many blocks off the pad): these scenes were mission_binding_candidate_not_unique.
        for cubes, expected in (([seen(A_START), seen(B_START)], A_START), ([seen(B_START), seen((0.080, NEAR_EDGE_Y - 0.035))], B_START),
                                ([seen(A_START), seen((-0.1, NEAR_EDGE_Y + 0.004))], A_START)):
            with self.subTest(modes=[cube.mode for cube in cubes]):
                (binding,) = tb.bind_targets(self.AUTO, cubes, RULES)
                self.assertEqual(expected, binding.bound_pad_m)

    def test_a_placed_cube_is_barred_and_alone_it_is_no_candidate(self):
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.bind_targets(self.AUTO, [seen(A_PLACED)], RULES)
        self.assertEqual(tb.REFUSED_NO_CANDIDATE, refused.exception.reason_code)
        self.assertEqual((0, 1, 1), (refused.exception.values["candidates"], refused.exception.values["placed_cubes"], refused.exception.values["cubes_seen"]))
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.bind_targets(self.AUTO, [], RULES)
        self.assertEqual(tb.REFUSED_NO_CANDIDATE, refused.exception.reason_code)
        (binding,) = tb.bind_targets(self.AUTO, [seen(A_PLACED), seen(B_START)], RULES)  # the placed cube does not count against the one candidate
        self.assertEqual((B_START, tb.MODE_BASE_ADVANCE, 1), (binding.bound_pad_m, binding.mode, binding.index))

    def test_a_candidate_with_no_bounded_placement_is_refused_with_its_own_reason(self):
        unreachable = seen((0.0, NEAR_EDGE_Y - 0.200))
        self.assertIsNone(unreachable.mode)
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.bind_targets(self.AUTO, [unreachable], RULES)
        self.assertEqual(tb.REFUSED_CANDIDATE_NOT_PLACEABLE, refused.exception.reason_code)
        # CHANGED by the ruling of 2026-09-29: a feasible second block is bound (this was mission_binding_candidate_not_unique)
        (binding,) = tb.bind_targets(self.AUTO, [unreachable, seen(A_START)], RULES)
        self.assertEqual((A_START, 1), (binding.bound_pad_m, binding.index))

    def test_two_auto_targets_cannot_be_told_apart(self):
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.bind_targets((tb.DeclaredTarget("cube_1", tb.AUTO), tb.DeclaredTarget("cube_2", tb.AUTO)), [seen(A_START), seen(B_START)], RULES)
        self.assertEqual(tb.REFUSED_TARGETS_SHARE_A_MODE, refused.exception.reason_code)

    NEAR_THEN_AUTO = (tb.DeclaredTarget("cube_near", False), tb.DeclaredTarget("cube", tb.AUTO))
    AUTO_THEN_FAR = (tb.DeclaredTarget("cube", tb.AUTO), tb.DeclaredTarget("cube_far", True))

    def test_an_auto_target_beside_a_declared_one_never_takes_the_cube_the_other_bound(self):
        """One cube, one target: a mission that mixes a declared target with an auto one (mission_service.normalize_targets
        admits the mix) and sees ONE cube is refused before anything is picked up, in whichever order it declares them;
        the cube the earlier target bound is nobody else's candidate, so the record never carries two bindings at one
        position."""
        for targets, cube, refused_id in ((self.NEAR_THEN_AUTO, seen(A_START), "cube"), (self.AUTO_THEN_FAR, seen(B_START), "cube_far")):
            with self.subTest(refused=refused_id):
                with self.assertRaises(tb.TargetRefused) as refused:
                    tb.bind_targets(targets, [cube], RULES)
                self.assertEqual(tb.REFUSED_NO_CANDIDATE, refused.exception.reason_code)
                self.assertIn(f"target {refused_id} ", str(refused.exception))
                self.assertEqual((0, 1, 1), (refused.exception.values["candidates"], refused.exception.values["bound_earlier"], refused.exception.values["cubes_seen"]))

    def test_an_auto_target_beside_a_declared_one_binds_what_the_earlier_targets_left(self):
        """Two cubes: the declared target takes its one candidate and the auto target the cube that is left, whatever it
        measures; the two bindings are two cubes. Declaration order decides: an auto target declared FIRST takes the
        nearest feasible block (CHANGED by the ruling of 2026-09-29; it was ambiguity), here the declared target's one
        ARM_ONLY candidate, and the declared target is then refused for want of one."""
        near, auto = tb.bind_targets(self.NEAR_THEN_AUTO, [seen(A_START), seen(B_START)], RULES)
        self.assertEqual(("cube_near", A_START, tb.MODE_ARM_ONLY, 0), (near.target_id, near.bound_pad_m, near.mode, near.index))
        self.assertEqual(("cube", B_START, tb.MODE_BASE_ADVANCE, 1), (auto.target_id, auto.bound_pad_m, auto.mode, auto.index))
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.bind_targets((tb.DeclaredTarget("cube", tb.AUTO), tb.DeclaredTarget("cube_near", False)), [seen(A_START), seen(B_START)], RULES)
        self.assertEqual(tb.REFUSED_NO_CANDIDATE, refused.exception.reason_code)
        self.assertIn("target cube_near ", str(refused.exception))
        self.assertEqual((1, 1), (refused.exception.values["candidates"], refused.exception.values["bound_earlier"]))

    def test_the_first_execution_selects_in_the_mode_its_binding_measured(self):
        first = tb.parse_mission_target(self.auto_document())
        self.assertEqual((tb.AUTO, tb.AUTO, True, True), (first.requires_capability, first.mode, first.auto, first.first_execution))
        for cube, mode in ((seen(A_START), tb.MODE_ARM_ONLY), (seen(B_START), tb.MODE_BASE_ADVANCE)):
            bindings, selection = tb.select_for_execution(first, [cube], RULES)
            self.assertEqual((mode, mode, 0.0), (bindings[0].mode, selection.mode, selection.distance_mm))
            self.assertEqual(mode, first.run_mode(selection))

    def test_a_later_execution_selects_in_the_effective_mode_and_a_mismatch_is_refused(self):
        later = tb.parse_mission_target(self.auto_document(bound=list(B_START), effective=tb.MODE_BASE_ADVANCE, capability=capability_block(self.dir), run_id=RUN_B))
        self.assertEqual((tb.MODE_BASE_ADVANCE, True, True, 1, (A_PLACED,)), (later.mode, later.auto, later.carries_capability, later.index, later.earlier_placed))
        bindings, selection = tb.select_for_execution(later, [seen(A_PLACED), seen(B_START, offset_mm=(2.0, 2.0))], RULES)
        self.assertIsNone(bindings)
        self.assertEqual((1, tb.MODE_BASE_ADVANCE), (selection.index, selection.mode))
        self.assertEqual(tb.MODE_BASE_ADVANCE, later.run_mode(selection))
        impostor = seen((B_START[0], NEAR_EDGE_Y))  # within the match radius, measuring ARM_ONLY
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.select_for_execution(later, [seen(A_PLACED), impostor], RULES)
        self.assertEqual(tb.REFUSED_MODE_MISMATCH, refused.exception.reason_code)

    def test_the_file_is_version_3_and_carries_the_effective_mode_once_bound(self):
        self.assertEqual(3, tb.MISSION_TARGET_VERSION)
        document = self.auto_document()
        self.assertEqual((3, tb.AUTO, None), (document["v"], document["requires_capability"], document["targets"][0]["effective_mode"]))
        self.assertEqual(["bound_pad_m", "effective_mode", "placed_pad_m", "requires_capability", "target_id"], sorted(document["targets"][0]))
        path = os.path.join(self.dir, "mission-target.json")
        self.assertEqual(tb.write_mission_target(path, document), tb.load_mission_target(path))
        bound = self.auto_document(bound=list(B_START), effective=tb.MODE_BASE_ADVANCE, run_id=RUN_B)
        self.assertEqual(tb.MODE_BASE_ADVANCE, tb.parse_mission_target(bound).mode)
        earlier, target = bound["targets"]
        for change, code in (({"effective_mode": None}, "binding_effective_mode_inconsistent"), ({"effective_mode": tb.AUTO}, "binding_effective_mode_not_allowed"),
                             ({"effective_mode": "base_advance"}, "binding_effective_mode_not_allowed"), ({"bound_pad_m": None}, "binding_effective_mode_inconsistent")):
            with self.subTest(change=change), self.assertRaises(tb.MissionFileRefused) as caught:
                tb.parse_mission_target({**bound, "targets": [earlier, {**target, **change}]})
            self.assertEqual(code, caught.exception.reason_code)
        with self.assertRaises(tb.MissionFileRefused) as caught:  # a declared target never carries an effective mode: its declaration is its mode
            tb.parse_mission_target({**bound, "requires_capability": True, "targets": [earlier, {**target, "requires_capability": True}]})
        self.assertEqual("binding_effective_mode_inconsistent", caught.exception.reason_code)
        with self.assertRaises(tb.MissionFileRefused) as caught:
            tb.parse_mission_target({**document, "targets": [{k: v for k, v in document["targets"][0].items() if k != "effective_mode"}]})
        self.assertEqual("binding_target_missing_key", caught.exception.reason_code)

    def test_an_auto_run_may_carry_the_capability_or_not(self):
        # The measuring run carries nothing; the respawn after a measured BASE_ADVANCE carries it, still a first execution.
        self.assertFalse(tb.parse_mission_target(self.auto_document()).carries_capability)
        self.assertTrue(tb.parse_mission_target(self.auto_document(capability=capability_block(self.dir))).carries_capability)
        document = first_document(self.dir)  # a target declared as needing nothing still never carries it
        with self.assertRaises(tb.MissionFileRefused) as caught:
            tb.parse_mission_target({**document, "capability": capability_block(self.dir)})
        self.assertEqual("binding_capability_presence_disagrees", caught.exception.reason_code)

    def test_declared_targets_start_with_no_effective_mode(self):
        self.assertEqual([{"target_id": "cube", "requires_capability": tb.AUTO, "effective_mode": None, "bound_pad_m": None, "placed_pad_m": None}],
                         tb.declared_targets([{"target_id": "cube", "label": "the block", "reachable": True, "requires_capability": tb.AUTO}]))

    def auto_result(self, cube, placed_at, *, capability=None, **changes):
        first = tb.parse_mission_target(self.auto_document(capability=capability_block(self.dir) if capability is not None else None))
        bindings, selection = tb.select_for_execution(first, [cube], RULES)
        placement = tb.attribute_placement([seen(placed_at)], release_at(placed_at), RULES)
        fields = dict(stage=tb.STAGE_FINISHED, bindings=bindings, selection=selection, search_observation_id="obs-0001", outcome="placed_new",
                      final_state="PLACED", predicates={"object_secured": True, "placed": True, "base_returned": True}, placement=placement,
                      exit_observation_id="obs-0042", capability=capability)
        fields.update(changes)
        return first, tb.result_document(first, **fields)

    def test_the_result_records_the_measured_mode_and_the_selection_must_agree_with_it(self):
        first, arm_only = self.auto_result(seen(A_START), A_PLACED)
        self.assertEqual((tb.MODE_ARM_ONLY, tb.MODE_ARM_ONLY), (arm_only["bindings"][0]["mode"], arm_only["selection"]["mode"]))
        _first, base = self.auto_result(seen(B_START), B_PLACED)
        self.assertEqual((tb.MODE_BASE_ADVANCE, tb.MODE_BASE_ADVANCE), (base["bindings"][0]["mode"], base["selection"]["mode"]))
        for change, code in ((dict(bindings=[{**arm_only["bindings"][0], "mode": tb.AUTO}]), "result_binding_does_not_match_the_declaration"),
                             (dict(selection={**arm_only["selection"], "mode": tb.MODE_BASE_ADVANCE}), "result_selection_mode_not_declared")):
            with self.subTest(code=code), self.assertRaises(tb.MissionFileRefused) as caught:
                tb.parse_result({**arm_only, **change}, first)
            self.assertEqual(code, caught.exception.reason_code)

    def test_placement_is_claimed_by_the_mode_the_run_measured(self):
        first, arm_only = self.auto_result(seen(A_START), A_PLACED)
        self.assertTrue(tb.result_claims_placement(arm_only, first), "an ARM_ONLY run has no relay and needs none")
        carried, base_without_relay = self.auto_result(seen(B_START), B_PLACED, capability={"generation": 3, "pid": 4242, "accepted": 4, "applied": 0, "dropped": {}})
        self.assertFalse(tb.result_claims_placement(base_without_relay, carried), "a BASE_ADVANCE run is placed only through the capability's own output")
        _carried, base_with_relay = self.auto_result(seen(B_START), B_PLACED, capability={"generation": 3, "pid": 4242, "accepted": 4, "applied": 4, "dropped": {}})
        self.assertTrue(tb.result_claims_placement(base_with_relay, carried))
        following = tb.targets_after_placement(first, arm_only)
        self.assertEqual([{"target_id": "cube", "requires_capability": tb.AUTO, "effective_mode": tb.MODE_ARM_ONLY, "bound_pad_m": list(A_START), "placed_pad_m": list(A_PLACED)}], following)
        self.assertEqual(tb.MODE_BASE_ADVANCE, tb.targets_after_placement(carried, base_with_relay)[0]["effective_mode"])
        declared = tb.parse_mission_target(first_document(self.dir))
        bindings, selection = tb.select_for_execution(declared, [seen(A_START), seen(B_START)], RULES)
        result = tb.result_document(declared, stage=tb.STAGE_FINISHED, bindings=bindings, selection=selection, outcome="placed_new", final_state="PLACED",
                                    predicates={"object_secured": True, "placed": True, "base_returned": True}, placement=tb.attribute_placement([seen(A_PLACED), seen(B_START)], release_at(A_PLACED), RULES))
        self.assertEqual([None, None], [entry["effective_mode"] for entry in tb.targets_after_placement(declared, result)], "a declared target never gains one")


# =====================================================================================================================
def _assigned(path: Path, name: str):
    """The value node assigned to NAME at module level in PATH, parsed and never imported: those modules belong to another
    change and may be mid-edit, and what has to agree is a literal, not a running import."""
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
            return node.value
    return None


class ImportSafetyAndPinsTest(unittest.TestCase):
    def test_importing_it_loads_no_robot_camera_array_or_controller_module(self):
        probe = ("import json, sys; sys.path.insert(0, sys.argv[1]); import target_binding; "
                 "print(json.dumps(sorted(sys.modules)))")
        done = subprocess.run([sys.executable, "-c", probe, str(ROOT / "autonomy" / "manipulation")], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        loaded = {name.split(".")[0] for name in json.loads(done.stdout)}
        self.assertFalse(loaded & {"numpy", "cv2", "lerobot", "torch", "zmq", "serial", "run_single_cube_cycle", "run_stage1_scan", "demo_runtime"})
        tree = ast.parse((ROOT / "autonomy" / "manipulation" / "target_binding.py").read_text())
        imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        self.assertLessEqual(imported, set(sys.stdlib_module_names) | {"__future__"})

    def test_the_target_id_allowlist_and_the_target_limit_are_mission_service_s(self):
        source = ROOT / "packages" / "demo-runtime" / "demo_runtime" / "mission_service.py"
        pattern = _assigned(source, "_TARGET_ID")
        self.assertIsNotNone(pattern, "mission_service._TARGET_ID not found")
        self.assertEqual(pattern.args[0].value, tb.TARGET_ID_RE.pattern)
        self.assertEqual(_assigned(source, "_MAX_TARGETS").value, tb.MAX_TARGETS)

    def test_mission_ids_are_what_the_mission_record_mints(self):
        tree = ast.parse((ROOT / "packages" / "demo-runtime" / "demo_runtime" / "mission.py").read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "new_mission_id")
        namespace = {"uuid_module": uuid}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "mission.new_mission_id", "exec"), namespace)
        for _ in range(50):
            self.assertIsNotNone(tb.MISSION_ID_RE.fullmatch(namespace["new_mission_id"]()))

    def test_the_pid_bound_is_the_capability_adapter_s(self):
        self.assertEqual(_assigned(ROOT / "autonomy" / "motion" / "cake_capability_adapter.py", "PID_MAX_LIMIT").value, tb.PID_MAX)

    def test_the_variable_is_named_where_the_design_names_it(self):
        self.assertEqual("KC_MISSION_TARGET", tb.MISSION_TARGET_ENV)



class TheNotPlaceableRefusalExplainsItself(unittest.TestCase):
    """A candidate with no bounded placement carries the placement stage's explanation (policy_placement.classify_for_binding)
    as SeenCube.explanation, and the binding's refusal names the constraint, the deficit and the hint beside its counts.
    The scripted mission: one auto target, one cube at start 3's anchor of 2026-09-26 on start 1's recorded axis."""

    AUTO = (tb.DeclaredTarget("cube", tb.AUTO),)
    START_3, LATE_2117, AXIS_1 = (0.163, -0.210), (0.064, -0.220), (-0.042, 0.999)

    def explained(self, anchor):
        import types

        import policy_placement
        import stage_policy

        ctx = types.SimpleNamespace(events=types.SimpleNamespace(enter_policy=lambda identity: None, exit_policy=lambda: None))
        mode, why = policy_placement.classify_for_binding(ctx, stage_policy.ControllerView(vars(cycle)), list(anchor), self.AXIS_1, 0.0)
        return tb.SeenCube(anchor, cycle.placed_margin_mm(anchor), mode, why)

    def test_start_3_is_refused_with_the_side_edge_the_deficit_and_the_hint(self):
        cube = self.explained(self.START_3)
        self.assertIsNone(cube.mode)
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.bind_targets(self.AUTO, [cube], RULES)
        error = refused.exception
        self.assertEqual(tb.REFUSED_CANDIDATE_NOT_PLACEABLE, error.reason_code)
        self.assertEqual("side_edge", error.values["constraint"])
        self.assertAlmostEqual(44.5, error.values["side_margin_mm"], delta=0.01)
        self.assertAlmostEqual(121.6, error.values["axis_advance_needed_unbounded_mm"], delta=0.05)
        self.assertGreater(error.values["deficit_mm"], 0.0)
        self.assertEqual((1, 0, 1, 0), (error.values["candidates"], error.values["bound_earlier"], error.values["cubes_seen"], error.values["placed_cubes"]))
        self.assertIn("the right edge along the reach axis is 9.4 mm short", str(error))
        self.assertIn(error.values["hint"], str(error))
        self.assertNotIn("sentence", error.values)
        self.assertEqual("move the block 10 mm to the left as seen from the robot and 3 mm toward the pad", error.values["hint"])

    def test_every_text_the_refusal_records_is_kept_whole_by_the_record(self):
        # the controller's cycle_failed passes each value through its named-value sanitizer: a text it would strip or cut
        # would reach the console changed
        for anchor in (self.START_3, self.LATE_2117):
            with self.subTest(anchor=anchor), self.assertRaises(tb.TargetRefused) as refused:
                tb.bind_targets(self.AUTO, [self.explained(anchor)], RULES)
            texts = {name: value for name, value in refused.exception.values.items() if isinstance(value, str)}
            self.assertIn("explanation", texts)
            for name, value in texts.items():
                self.assertEqual(value, cycle._named(name, value)["value"], name)

    def test_2117_is_refused_as_past_the_along_axis_budget(self):
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.bind_targets(self.AUTO, [self.explained(self.LATE_2117)], RULES)
        self.assertEqual("along_budget", refused.exception.values["constraint"])
        self.assertIn("11.6 mm past the along-axis budget", str(refused.exception))
        self.assertIn("move the block 12 mm toward the pad", str(refused.exception))

    def test_a_declared_base_advance_target_names_the_limit_of_the_one_cube_it_could_not_bind(self):
        # start 3 under a target that declares the capability: no cube measures BASE_ADVANCE, and the one seen cube has no
        # bounded placement; the refusal names that cube's limit and its shortfall beside the counts
        for anchor, constraint, words in ((self.START_3, "side_edge", "the right edge along the reach axis is 9.4 mm short"),
                                          (self.LATE_2117, "along_budget", "11.6 mm past the along-axis budget of 120 mm")):
            with self.subTest(anchor=anchor), self.assertRaises(tb.TargetRefused) as refused:
                tb.bind_targets((tb.DeclaredTarget("cube", True),), [self.explained(anchor)], RULES)
            error = refused.exception
            self.assertEqual(tb.REFUSED_NO_CANDIDATE, error.reason_code)
            self.assertEqual(constraint, error.values["constraint"])
            self.assertGreater(error.values["deficit_mm"], 0.0)
            self.assertIn(words, str(error))
            self.assertIn(error.values["hint"], str(error))
            self.assertEqual(0, error.values["matching_candidates"])
            self.assertNotIn("sentence", error.values)

    def test_a_declared_target_keeps_its_counts_only_refusal_unless_the_explained_cube_is_the_one_left(self):
        # the explanation is carried only when it belongs to the one candidate the refusal is about
        for other in (self.explained((self.START_3[0] - 0.3, self.START_3[1])), tb.SeenCube((0.0, -0.2), cycle.placed_margin_mm((0.0, -0.2)), tb.MODE_ARM_ONLY)):
            with self.subTest(other=other), self.assertRaises(tb.TargetRefused) as refused:
                tb.bind_targets((tb.DeclaredTarget("cube", True),), [self.explained(self.START_3), other], RULES)
            self.assertEqual(tb.REFUSED_NO_CANDIDATE, refused.exception.reason_code)
            self.assertNotIn("constraint", refused.exception.values)
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.bind_targets((tb.DeclaredTarget("cube", True),), [tb.SeenCube(self.START_3, cycle.placed_margin_mm(self.START_3), None)], RULES)
        self.assertEqual({"matching_candidates", "candidates", "bound_earlier", "cubes_seen", "placed_cubes"}, set(refused.exception.values))

    def test_without_an_explanation_the_refusal_is_the_one_it_was(self):
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.bind_targets(self.AUTO, [tb.SeenCube(self.START_3, cycle.placed_margin_mm(self.START_3), None)], RULES)
        self.assertEqual({"candidates", "bound_earlier", "cubes_seen", "placed_cubes"}, set(refused.exception.values))
        self.assertEqual("BINDING: the one cube for target cube has no bounded placement from this SEARCH (neither the arm alone nor the bounded base "
                         "advance reaches the pad from it); nothing was picked up", str(refused.exception))

    def test_an_explanation_is_named_scalars_on_a_cube_with_a_centre_and_no_mode(self):
        cube = tb.SeenCube((0.0, -0.3), -152.5, None, {"constraint": "along_budget", "deficit_mm": 3.0})
        self.assertEqual((("constraint", "along_budget"), ("deficit_mm", 3.0)), cube.explanation)
        hash(cube)
        for bad in (lambda: tb.SeenCube(None, None, None, {"constraint": "x"}), lambda: tb.SeenCube((0.0, 0.0), 147.5, tb.MODE_ARM_ONLY, {"constraint": "x"}),
                    lambda: tb.SeenCube((0.0, -0.3), -152.5, None, {"values": [1.0]})):
            with self.assertRaises(ValueError):
                bad()

if __name__ == "__main__":
    unittest.main()
