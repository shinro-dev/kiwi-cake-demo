# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""autonomy/tests/geometric_world.py: a floor cube the ALIGN stage can be run against, so that a policy is tested for
convergence from a start and not only on scripted boxes (track A of docs/autonomous-demo/overnight-plan-2026-09-26.md).

THE WORLD. One cube stands still on the floor. It is described the way ALIGN reads it: `cube_out_mm`, where it lies beyond
the proven grasp radius, and `cube_pan_deg`, the pan reading that would put it on the tool's radial line. From a measured
arm pose the World computes:

  lateral_mm = (the proven grasp range + cube_out_mm) x sin(cube_pan_deg - pan)
  radial_mm  = cube_out_mm - tool out + RADIAL_READ_PER_UP_MM x tool up    (alignment_from_bbox's recorded height bias)
  width_px   = WIDTH_K_PX_MM / (VIEW_DEPTH_OFFSET_MM + tool up)            (mb3c1048's pregrasp view: 264 px at +69.0 mm)

and the box that alignment_from_bbox reads back as exactly that lateral and radial (policy_align.box_from_alignment, the
formula run backwards), clipped at the 1280 x 720 border. A box the border cuts carries the recognizer's clipped flags; a
box of which less than `min_visible` of the height or width is in the frame is not detected (the frozen recognizer
returned nothing for the top-clipped block of 21:31).

THE ARM. WorldCycle is a stage_harness.HarnessCycle whose run_pose arrives at the planned target, except that the ALIGN
descent is realized with `descent_drift` (out, up, in mm: the recorded descents realized -13.2 / -5.7 and -5.0 / -3.2) and
a pan step with `pan_shortfall_deg` less than it was commanded (lateral_trim_deg's recorded 0.5 deg). Every view is the
World's perception of the realized pose; observe() is a stationary view of the pose the arm holds. `hide` names view
numbers (1-based, every run_pose and observe counted) whose perception is empty, to lose the block on purpose, or is a
callable of the view's label ("observe" for a re-capture) that says so.
"""
import math

import stage_harness as harness
import policy_align
import run_single_cube_cycle as cycle
import cartesian_lift

WIDTH_K_PX_MM = 264.0 * (policy_align.VIEW_DEPTH_OFFSET_MM + 68.97)


class World:
    def __init__(self, cube_out_mm: float, cube_pan_deg: float, min_visible: float = 0.5):
        self.cube_out_mm, self.cube_pan_deg, self.min_visible = float(cube_out_mm), float(cube_pan_deg), float(min_visible)

    def reading(self, measured: dict) -> dict:
        """What alignment_from_bbox reads of the cube from `measured`, and the box width, before any clipping."""
        tool = cycle.tool_state(measured)
        reach_mm = cycle.GRASP_TOOL_RANGE_M * 1000.0 + self.cube_out_mm
        lateral = reach_mm * math.sin(math.radians(self.cube_pan_deg - float(measured["shoulder_pan"])))
        radial = self.cube_out_mm - tool["out_mm"] + policy_align.RADIAL_READ_PER_UP_MM * tool["up_mm"]
        width = WIDTH_K_PX_MM / (policy_align.VIEW_DEPTH_OFFSET_MM + tool["up_mm"])
        return {"lateral_mm": lateral, "radial_mm": radial, "width_px": width, "tool": tool}

    def whole_box(self, measured: dict) -> list:
        read = self.reading(measured)
        return policy_align.box_from_alignment(cycle._CONTROLLER_VIEW, read["lateral_mm"], read["radial_mm"], read["width_px"], read["tool"]["pitch_deg"])

    def perceive(self, measured: dict) -> list:
        """The recognizer's records of this view: one cube, clipped at the border, or none."""
        x0, y0, x1, y1 = self.whole_box(measured)
        cx0, cy0 = max(x0, 0.0), max(y0, 0.0)
        cx1, cy1 = min(x1, policy_align.FRAME_WIDTH_PX - 1.0), min(y1, policy_align.FRAME_HEIGHT_PX - 1.0)
        if (cy1 - cy0) < self.min_visible * (y1 - y0) or (cx1 - cx0) < self.min_visible * (x1 - x0):
            return [harness.cube_record(None, observation_id="world-view")]
        clipped = {"top": y0 < 0.0, "bottom": y1 > policy_align.FRAME_HEIGHT_PX - 1.0, "left": x0 < 0.0, "right": x1 > policy_align.FRAME_WIDTH_PX - 1.0}
        clipped["any_border"] = any(clipped.values())
        record = harness.cube_record([round(cx0), round(cy0), round(cx1), round(cy1)], observation_id="world-view")
        record["cube"].update(clipped=clipped, candidates_verified=1)
        return [record]


class WorldCycle(harness.HarnessCycle):
    """A HarnessCycle whose arm moves in `world` (the module docstring)."""

    def __init__(self, world: World, pose=None, descent_drift=(0.0, 0.0), pan_shortfall_deg: float = 0.0, hide=(), **kwargs):
        super().__init__(pose=pose or harness.LOW_POSE, **kwargs)
        self.world, self.descent_drift, self.pan_shortfall_deg = world, tuple(descent_drift), float(pan_shortfall_deg)
        self.hide = hide if callable(hide) else set(hide)
        self.views = 0

    def _view(self) -> dict:
        self.views += 1
        hidden = self.hide(self.calls[-1][1]) if callable(self.hide) else self.views in self.hide
        perception = [harness.cube_record(None, observation_id="world-hidden")] if hidden else self.world.perceive(self.current)
        return {"measured": dict(self.current), "tool": cycle.tool_state(self.current), "perception": perception}

    def run_pose(self, target, holding: bool, floor_m, pan_allowance: float = 0.0, label: str = "", manipulation_step: bool = True) -> dict:
        self.calls.append(("run_pose", label))
        last = target[-1] if isinstance(target, (list, tuple)) else target
        before = dict(self.current)
        joints = {**self.current, **{name: float(value) for name, value in last.joints.items() if name in self.current and name != "gripper"}}
        if "descent" in label and label.startswith("ALIGN") and any(self.descent_drift):
            solved = cartesian_lift.solve_cartesian_forward_and_z({**{k: joints[k] for k in cycle.SCAN_JOINTS}, "wrist_roll": 0.0}, self.descent_drift[0] / 1000.0, self.descent_drift[1] / 1000.0)
            joints.update({k: float(solved["joint_deg"][k]) for k in cycle.LIFT_JOINTS})
        pan_step = joints["shoulder_pan"] - float(before["shoulder_pan"])
        if self.pan_shortfall_deg and abs(pan_step) > self.pan_shortfall_deg:
            joints["shoulder_pan"] -= math.copysign(self.pan_shortfall_deg, pan_step)
        self.current = joints
        return self._view()

    def observe(self, holding: bool) -> dict:
        self.calls.append(("observe", "observe"))
        return self._view()


def pregrasp_pose_over(world: World, forward_mm: float) -> dict:
    """The measured pose of today's pregrasp at `forward_mm`, at the pan that puts the World's cube on the tool's line."""
    poses = cycle.cli.pregrasp_poses(harness.LOW_POSE, world.cube_pan_deg, forward_mm / 1000.0, 0.070)
    return {**harness.LOW_POSE, **poses[-1].joints}
