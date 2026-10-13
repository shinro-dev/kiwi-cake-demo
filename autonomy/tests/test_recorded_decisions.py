#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_recorded_decisions.py: LOCAL ONLY. Every other test of this controller drives it with numbers a test
# wrote. This one takes the numbers the ROBOT produced and asks whether the pure decision functions in the controller today
# still reproduce the decisions that were recorded when the cycle physically passed. It is the regression test a refactor of
# a threshold or a formula would otherwise not have: the scripted worlds would keep passing, and this would not.
#
# It is SKIPPED unless KC_EVIDENCE_ROOT names a directory of recorded sessions (manipulation-*/cycle-*.json, with the
# observation records and a perception/ directory beside them). That data is private machine data: it is READ here and
# never copied into this repository, and no path of it is ever printed, so a failure names a session by its position in the
# root and a transition by its index, never by where either lives.
#
# What is checked, and what each check needs beyond the cycle record itself:
#   placed_margin_mm          the record alone (DONE_ALREADY_PLACED, HOLD_OBSERVE, PLACEMENT_PLAN, PLACED)
#   release_margin_worst_mm   the record alone (RELEASE_ADMISSION, and the prediction PLACED repeats)
#   base_advance_needed_mm    the record alone (the FIRST base pulse, where the anchor and the axis are both stated)
#   placement_plan            the record alone, plus the grasp radius read out of the STRADDLING line
#   coarse_pregrasp_from_low_pose   the SEARCH perception record (the contact pixel)
#   alignment_from_bbox       the perception record of each ALIGN view (the cube box)
#   base_displacement         the two pad frames of the first measured pulse (their homographies)
# A check whose inputs cannot be identified in a given session is not run, and the run fails if NOTHING could be checked:
# a local test that silently verifies nothing is worse than no test.
import calendar
import json
import math
import os
import re
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "bin", "laptop", "manipulation"))

import run_single_cube_cycle as cycle  # noqa: E402

EVIDENCE_ROOT_ENV = "KC_EVIDENCE_ROOT"
OBSERVATION_TIME = re.compile(r"^obs-(\d{8}T\d{6}Z)-")
TOOL_LINE = re.compile(r"^(?P<out>[-+][\d.]+) mm out / (?P<up>[-+][\d.]+) mm up vs the proven grasp, pitch (?P<pitch>[-+]?[\d.]+)$")
# Every input below is read back from a number that was ROUNDED before it was written. A pad coordinate is stated to 4
# decimal places of a metre and a margin to 0.1 mm, so a margin recomputed from the stated coordinate can differ from the
# stated margin by half of each. The wider bounds are the same argument over a longer chain and are justified where used.
MARGIN_TOLERANCE_MM = 0.11
PLAN_TOLERANCE_MM = 0.31  # the reach direction is stated to 3 decimal places, which over a travel of about 100 mm moves the end point by about 0.1 mm
STEP_TOLERANCE_MM = 1.01  # base_advance_needed_mm and placement_plan search in steps of 1 mm: a rounded input can tip one step


def instant(text: str):
    """The UTC second a recorded timestamp names, as a comparable number. None when it is not one of the two shapes this
    repository writes (the transition clock, and the clock inside an observation id).

    timegm, not mktime: both shapes end in Z and are UTC, while mktime would read them in whatever zone the laptop is set
    to. That is not only a constant offset. Where the local zone springs forward, two stamps five minutes apart in UTC come
    back 3300 s apart in the WRONG order, and last_before / first_after would then match a transition to a neighbouring
    frame and compare a recorded decision against evidence that is not the evidence it was taken from."""
    for pattern in ("%Y-%m-%dT%H:%M:%SZ", "%Y%m%dT%H%M%SZ"):
        try:
            return float(calendar.timegm(time.strptime(text, pattern)))
        except (TypeError, ValueError):
            continue
    return None


def tool_of(line: str):
    """The tool state a [STATE] line carries as text, back as numbers. fmt_tool prints 0.1 mm and 0.1 deg, so this is what
    the record preserved of it, not what the controller held."""
    found = TOOL_LINE.match(str(line or ""))
    return None if found is None else {"out_mm": float(found["out"]), "up_mm": float(found["up"]), "pitch_deg": float(found["pitch"])}


class Session:
    """One recorded session directory, read lazily and never written to."""

    def __init__(self, path: str, index: int):
        self.path, self.index = path, index
        self.cycles = sorted(name for name in os.listdir(path) if name.startswith("cycle-") and name.endswith(".json"))
        self._perception = None

    def transitions(self, name: str):
        with open(os.path.join(self.path, name)) as handle:
            record = json.load(handle)
        return record if isinstance(record, list) and all(isinstance(entry, dict) and "state" in entry for entry in record) else None

    @property
    def perception(self) -> list:
        """(instant, observation id, the perception record), oldest first. The id carries the capture clock, so the order
        is the order the frames were taken, which is what lets a record be matched to the transition that used it."""
        if self._perception is None:
            out = []
            directory = os.path.join(self.path, "perception")
            for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
                found = OBSERVATION_TIME.match(name)
                if found is None or not name.endswith(".json"):
                    continue
                when = instant(found.group(1))
                try:
                    with open(os.path.join(directory, name)) as handle:
                        record = json.load(handle)
                except (OSError, ValueError):
                    continue
                if when is not None and isinstance(record, dict):
                    out.append((when, name[: -len(".json")], record))
            self._perception = sorted(out, key=lambda item: (item[0], item[1]))
        return self._perception

    def last_before(self, when, wanted):
        return next((record for moment, _id, record in reversed(self.perception) if moment <= when and wanted(record)), None)

    def first_after(self, when, wanted):
        return next((record for moment, _id, record in self.perception if moment > when and wanted(record)), None)


def has_cube_contact(record: dict) -> bool:
    cube = record.get("cube") or {}
    return bool(cube.get("detected") and cube.get("contact_px"))

def has_cube_box(record: dict) -> bool:
    cube = record.get("cube") or {}
    return bool(cube.get("detected") and cube.get("bbox_xyxy"))

def has_pad_frame(record: dict) -> bool:
    template = record.get("pad_by_template") or {}
    return bool(template.get("pad_frame_valid") and template.get("homography_pad_to_image") and template.get("homography_image_to_pad"))


def sessions() -> list:
    root = os.environ.get(EVIDENCE_ROOT_ENV)
    if not root or not os.path.isdir(root):
        return []
    found = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if name.startswith("manipulation-") and os.path.isdir(path):
            session = Session(path, len(found))
            if session.cycles:
                found.append(session)
    return found


class RecordedDecisionTest(unittest.TestCase):
    """Each check appends to self.checked so that the last test in this class can insist something really was checked."""

    @classmethod
    def setUpClass(cls):
        cls.sessions = sessions()
        if not cls.sessions:
            raise unittest.SkipTest(f"{EVIDENCE_ROOT_ENV} does not name a directory of recorded sessions (manipulation-*/cycle-*.json beside a perception directory), so there are no recorded decisions to reproduce")
        cls.checked = []

    def setUp(self):
        self.checked = type(self).checked

    # ------------------------------------------------------------------ walking the records
    def passing_cycles(self):
        """(a name for failures, the session, the transitions) for every recorded cycle that REACHED a placement. A cycle
        that failed recorded a decision too, but the value it was supposed to reproduce may never have been written."""
        for session in self.sessions:
            for order, name in enumerate(session.cycles):
                transitions = session.transitions(name)
                if transitions is None:
                    continue
                placed = [entry for entry in transitions if entry["state"] == "PLACED" and entry.get("value") is True]
                noop = [entry for entry in transitions if entry["state"] == "DONE_ALREADY_PLACED"]
                if placed or noop:
                    yield f"session {session.index}, cycle {order}", session, transitions

    def states(self, transitions, name: str):
        for index, entry in enumerate(transitions):
            if entry["state"] == name:
                yield index, entry

    def close(self, expected, actual, tolerance: float, where: str) -> None:
        self.checked.append(where)
        self.assertIsNotNone(expected, f"{where}: the record holds no value to reproduce")
        self.assertLessEqual(abs(float(expected) - float(actual)), tolerance, f"{where}: the record says {float(expected):.3f} and this code now computes {float(actual):.3f}")

    # ------------------------------------------------------------------ the checks
    def test_every_recorded_margin_is_reproduced_from_the_pad_coordinate_beside_it(self):
        """placed_margin_mm, four times over, against the four states that state a margin and the point it was taken at."""
        for where, _session, transitions in self.passing_cycles():
            for field, margin_field, state in (("cube_pad_m", "margin_mm", "DONE_ALREADY_PLACED"), ("anchor_pad_m", "anchor_margin_mm", "HOLD_OBSERVE"),
                                               ("anchor_pad_m", "anchor_margin_mm", "PLACEMENT_PLAN"), ("cube_pad_m", "centre_to_edge_mm", "PLACED")):
                for index, entry in self.states(transitions, state):
                    point, recorded = entry.get(field), entry.get(margin_field)
                    if point is None or recorded is None:
                        continue
                    self.close(recorded, cycle.placed_margin_mm(point), MARGIN_TOLERANCE_MM, f"{where}, transition {index}, {state}.{margin_field}")
                    if state == "PLACED" and entry.get("footprint_margin_any_yaw_mm") is not None:
                        self.close(entry["footprint_margin_any_yaw_mm"], cycle.placed_margin_mm(point) - cycle.PLACED_MIN_EDGE_MARGIN_MM, MARGIN_TOLERANCE_MM,
                                   f"{where}, transition {index}, PLACED.footprint_margin_any_yaw_mm")

    def test_every_recorded_release_margin_is_reproduced_from_the_anchor_the_direction_and_the_travel(self):
        for where, _session, transitions in self.passing_cycles():
            direction = next((entry.get("reach_direction_pad") for _index, entry in self.states(transitions, "PLACEMENT_PLAN")), None)
            if direction is None:
                continue
            for index, entry in self.states(transitions, "RELEASE_ADMISSION"):
                anchor, travel, recorded = entry.get("anchor_pad_m"), entry.get("fk_travel_from_grasp_mm"), entry.get("worst_case_centre_margin_mm")
                if anchor is None or travel is None or recorded is None:
                    continue
                worst = cycle.release_margin_worst_mm(anchor, direction, float(travel))
                self.close(recorded, worst, PLAN_TOLERANCE_MM, f"{where}, transition {index}, RELEASE_ADMISSION.worst_case_centre_margin_mm")
                self.checked.append("release admission")
                self.assertEqual(entry.get("admitted"), worst >= cycle.PLACE_REQUIRED_CENTRE_MM and float(entry.get("release_height_mm", 0.0)) <= cycle.RELEASE_MAX_HEIGHT_MM,
                                 f"{where}, transition {index}: the admission this code would reach from the recorded evidence is not the one that was recorded")
                for later, placed in self.states(transitions, "PLACED"):
                    if later > index and placed.get("predicted_worst_case_centre_margin_mm") is not None:
                        self.close(placed["predicted_worst_case_centre_margin_mm"], worst, PLAN_TOLERANCE_MM, f"{where}, transition {later}, PLACED.predicted_worst_case_centre_margin_mm")

    def test_the_first_recorded_base_advance_is_the_advance_this_code_would_ask_for(self):
        """Only the FIRST pulse: after it the anchor has been carried forward by a measured displacement that the cycle
        record does not state, so a later pulse's need cannot be recomputed from the record alone."""
        for where, _session, transitions in self.passing_cycles():
            anchor = next((entry.get("anchor_pad_m") for _index, entry in self.states(transitions, "HOLD_OBSERVE")), None)
            first = next(((index, entry) for index, entry in self.states(transitions, "BASE_ADVANCE") if entry.get("pulse") == 0), None)
            if anchor is None or first is None or first[1].get("axis_pad") is None or first[1].get("needed_mm") is None:
                continue
            index, entry = first
            need = cycle.base_advance_needed_mm(anchor, entry["axis_pad"])
            self.checked.append("first base advance")
            self.assertIsNotNone(need, f"{where}, transition {index}: the recorded run found a bounded advance and this code finds none")
            self.close(entry["needed_mm"], need, STEP_TOLERANCE_MM, f"{where}, transition {index}, BASE_ADVANCE.needed_mm")

    def test_every_recorded_placement_plan_is_the_plan_this_code_would_make(self):
        """The grasp radius the plan used is the tool radius of the STRADDLING line, but only while nothing re-approached
        after it: an empty close moves the arm again, and that later radius was never written down."""
        for where, _session, transitions in self.passing_cycles():
            for index, entry in self.states(transitions, "PLACEMENT_PLAN"):
                anchor, direction = entry.get("anchor_pad_m"), entry.get("reach_direction_pad")
                straddling = [(order, tool_of(item.get("tool"))) for order, item in self.states(transitions, "STRADDLING") if order < index]
                retried = any(order < index for order, _item in self.states(transitions, "GRASP_RETRY_ADVANCE"))
                if anchor is None or direction is None or retried or not straddling or straddling[-1][1] is None:
                    continue
                plan = cycle.placement_plan(anchor, direction, straddling[-1][1]["out_mm"])
                self.checked.append("placement plan")
                self.assertEqual(entry.get("mode"), plan["mode"], f"{where}, transition {index}: the recorded plan chose {entry.get('mode')} and this code chooses {plan['mode']}")
                for field in ("fk_travel_mm", "base_advance_mm"):
                    if entry.get(field) is not None:
                        self.close(entry[field], plan[field], STEP_TOLERANCE_MM, f"{where}, transition {index}, PLACEMENT_PLAN.{field}")

    def test_the_recorded_coarse_pregrasp_is_reproduced_from_the_search_frame(self):
        for where, session, transitions in self.passing_cycles():
            search = next((entry for _index, entry in self.states(transitions, "SEARCH")), None)
            pregrasp = next(((index, entry) for index, entry in self.states(transitions, "PREGRASP")), None)
            when = instant((search or {}).get("utc", ""))
            if search is None or pregrasp is None or when is None or not (search.get("pose") or {}).get("shoulder_pan"):
                continue
            index, entry = pregrasp
            seen = session.first_after(when - 1, lambda record: has_cube_contact(record) and has_pad_frame(record))
            recorded = entry.get("coarse") or {}
            if seen is None or not recorded:
                continue
            coarse = cycle.coarse_pregrasp_from_low_pose(seen["cube"]["contact_px"], float(search["pose"]["shoulder_pan"]))
            # the pan READING is stated to 2 decimal places and enters the answer one for one, so its own rounding is all
            # that separates the two; the two lengths rest on the contact pixel, which the perception record holds exactly
            self.close(recorded.get("pan_deg"), coarse["pan_deg"], 0.011, f"{where}, transition {index}, PREGRASP.coarse.pan_deg")
            for field in ("forward_mm", "cube_out_mm"):
                self.close(recorded.get(field), coarse[field], 0.051, f"{where}, transition {index}, PREGRASP.coarse.{field}")

    def test_every_recorded_alignment_is_reproduced_from_the_cube_box_of_the_view_it_judged(self):
        for where, session, transitions in self.passing_cycles():
            for index, entry in self.states(transitions, "ALIGN"):
                when, tool = instant(entry.get("utc", "")), tool_of(entry.get("tool"))
                if when is None or tool is None or entry.get("lateral_mm") is None:
                    continue
                view = session.last_before(when, has_cube_box)
                if view is None:
                    continue
                box = view["cube"]["bbox_xyxy"]
                decision = cycle.alignment_from_bbox(box, tool["pitch_deg"])
                # the lateral value does not depend on the tool pitch at all, so only the box's own exactness limits it
                self.close(entry["lateral_mm"], decision["lateral_mm"], 0.051, f"{where}, transition {index}, ALIGN.lateral_mm")
                self.checked.append("alignment")
                self.assertEqual(entry.get("aligned"), decision["aligned"], f"{where}, transition {index}: the recorded run called this view {entry.get('aligned')} and this code calls it {decision['aligned']}")
                if entry.get("radial_mm") is not None:
                    # the pitch was written to 0.1 deg and the radial value follows the nadir row, so the answer is bracketed
                    # by the ends of that rounding rather than compared against its middle
                    ends = sorted(cycle.alignment_from_bbox(box, tool["pitch_deg"] + offset)["radial_mm"] for offset in (-0.05, 0.05))
                    self.checked.append("alignment radial")
                    self.assertGreaterEqual(float(entry["radial_mm"]), ends[0] - 0.051, f"{where}, transition {index}, ALIGN.radial_mm is below what any pitch in the recorded rounding gives")
                    self.assertLessEqual(float(entry["radial_mm"]), ends[1] + 0.051, f"{where}, transition {index}, ALIGN.radial_mm is above what any pitch in the recorded rounding gives")

    def test_the_first_measured_base_displacement_is_reproduced_from_the_two_pad_frames(self):
        """The two homographies are in the perception records; the anchor and the axis of the first pulse are in the cycle
        record. A later pulse is measured from an anchor that was never written down, so only the first one is checked."""
        for where, session, transitions in self.passing_cycles():
            anchor = next((entry.get("anchor_pad_m") for _index, entry in self.states(transitions, "HOLD_OBSERVE")), None)
            hold_at = instant(next((entry.get("utc", "") for _index, entry in self.states(transitions, "HOLD_OBSERVE")), ""))
            first = next(((index, entry) for index, entry in self.states(transitions, "BASE_ADVANCE") if entry.get("pulse") == 0), None)
            if anchor is None or hold_at is None or first is None:
                continue
            pulse_at = instant(first[1].get("utc", ""))
            measured = next(((index, entry) for index, entry in self.states(transitions, "BASE_MEASURED") if index > first[0]), None)
            if pulse_at is None or measured is None or first[1].get("axis_pad") is None:
                continue
            index, entry = measured
            measured_at = instant(entry.get("utc", ""))
            before = session.last_before(hold_at, has_pad_frame)
            after = None if measured_at is None else next((record for moment, _id, record in reversed(session.perception) if pulse_at < moment <= measured_at and has_pad_frame(record)), None)
            if before is None or after is None:
                continue
            moved = cycle.base_displacement(anchor, first[1]["axis_pad"], before["pad_by_template"]["homography_pad_to_image"], after["pad_by_template"]["homography_image_to_pad"])
            for field in ("advance_mm", "lateral_mm", "distortion_mm"):
                if entry.get(field) is not None:
                    self.close(entry[field], moved[field], PLAN_TOLERANCE_MM, f"{where}, transition {index}, BASE_MEASURED.{field}")
            if entry.get("duration_s"):
                self.close(entry.get("rate_mm_s"), abs(moved["advance_mm"]) / float(entry["duration_s"]), 1.01, f"{where}, transition {index}, BASE_MEASURED.rate_mm_s")

    def test_zz_something_was_really_checked(self):
        """Named so that it runs last. Every check above walks away quietly when its inputs are not in a given session; if
        they were never in ANY session, this test ran on data that does not hold recorded decisions and proved nothing."""
        self.assertTrue(self.checked, f"{len(self.sessions)} session(s) were read and not one recorded decision could be reproduced: the directory does not hold cycle records of the shape this test reads")
        self.assertGreater(len(self.checked), 3, f"only {len(self.checked)} recorded value(s) could be reproduced, which is too thin to call a regression test")


class ShapeTest(unittest.TestCase):
    """The parsing this file rests on, checked without any recorded data, so that a skipped run is not a silent one."""

    def test_the_two_recorded_clock_shapes_are_read_and_anything_else_is_refused(self):
        self.assertEqual(instant("2026-09-20T13:30:50Z"), instant("20260920T133050Z"))
        self.assertIsNone(instant("not a time"))
        self.assertIsNone(instant(None))

    def test_the_tool_line_is_read_back_as_the_numbers_fmt_tool_printed(self):
        line = cycle.fmt_tool({"out_mm": 11.44, "up_mm": -2.31, "pitch_deg": 63.72, "lowest_link_m": -0.099})
        self.assertEqual({"out_mm": 11.4, "up_mm": -2.3, "pitch_deg": 63.7}, tool_of(line))
        self.assertIsNone(tool_of("PLACED"))
        self.assertIsNone(tool_of(None))

    def test_the_recorded_root_is_only_read_when_it_is_named(self):
        """The guard really exercised, on directories this test makes itself. Comparing [] with [] proves nothing, and a
        run of this file with no recorded data is exactly the run that has nothing else to prove."""
        named = os.environ.pop(EVIDENCE_ROOT_ENV, None)
        self.addCleanup(lambda: os.environ.__setitem__(EVIDENCE_ROOT_ENV, named) if named is not None else None)
        self.assertEqual([], sessions(), "nothing names a root, so no directory may be read")
        with tempfile.TemporaryDirectory(prefix="kc-no-recorded-data-") as empty:
            os.environ[EVIDENCE_ROOT_ENV] = empty
            self.assertEqual([], sessions(), "a root that holds no session directory yields no session")
            os.makedirs(os.path.join(empty, "manipulation-none"))
            self.assertEqual([], sessions(), "a session directory without a cycle record is not a session to read decisions from")
            os.environ[EVIDENCE_ROOT_ENV] = os.path.join(empty, "there-is-no-such-directory")
            self.assertEqual([], sessions(), "a root that is not a directory is not read")
        os.environ.pop(EVIDENCE_ROOT_ENV, None)
        self.assertTrue(math.isfinite(cycle.PLACE_REQUIRED_CENTRE_MM))


if __name__ == "__main__":
    unittest.main()
