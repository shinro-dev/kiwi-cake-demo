#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/reach_policies_replay.py: THE RECORDED STARTS THROUGH THE PLACEMENT POLICIES (the reach policies of
# 2026-09-30). Not a test file: test_reach_policies_replay.py uses it, and run as a script it writes the replay table.
#
# WHAT IT HOLDS. RECORDED_STARTS: every start of the run table of the 2026-09-30 reach evidence report (its row numbers),
# the two blocks the controller logs of 2026-09-19/20 saw already inside the pad, and the operator's block of 2026-09-30,
# each as the numbers SEARCH decided from: the block's PAD_FRAME centre, the reach axis, the coarse cube_out and the coarse
# pregrasp pan's distance from the pan SEARCH stood at. The axis is the SEARCH frame's pad homography at the contact pixel
# (reach_direction_pad) wherever a record carries that homography; the operator's block has the axis its refusal's own
# numbers fix up to its sign (both signs are replayed); a run whose record carries neither has the axes the recorded runs
# spanned (d_x from -0.10 to +0.10), every one replayed, and the table states the spread. A row with no SEARCH frame at
# all says so and is not replayed. The numbers are the robot's, rounded as pinned below; nothing else of a record is here.
#
# WHAT IT READS (evidence_starts, only with KC_EVIDENCE_ROOT, the layout of the 2026-09-30 evidence root): the controller
# logs of 2026-09-19/20 (observations-kcd/cycle-run-*-cycle_.txt) with the perception records they name; the console runs
# of 2026-09-22 (observations-kcd/console-20260922T*: the state file and the first perception record with a cube); the
# one-block session of 2026-09-26 (one-block-20260926: the output JSON and the archives' perception records and stdout,
# read in memory); the phantom set of 2026-09-28 (phantom-20260928: the output JSON and the offline records on the
# console's rendition). Nothing is written there and no path of it is printed. The runs of 2026-09-27 and 2026-09-30 are
# in the robot's history only; their rows are pinned from GET reads of it and are not re-derived here.
#
# WHAT IT DECIDES (replay). SEARCH's own order: the return pan band (search_cube_beyond_return_bound), the pickup band
# (TOO_NEAR, the coarse cube_out nearer than PREGRASP_MIN_CUBE_OUT_MM), then the placement. BEFORE is the installed rule
# (placement_plan with no word, as 31c546c decided); AFTER is the placement selector under both placement words
# (arm_reach, advance_then_reach) beside the proven policy, from the same inputs SEARCH hands placement_plan, planned as a
# run naming those words plans it: the controller is read with its PLACEMENT_ENVELOPE naming them, because the loaded
# reach cap reads the run's words (placement_policy.capped_plan_fits, the operator's ruling of 2026-10-02, Q8). The table
# also shows two answers that decide nothing, for the operator: what arm_reach alone would say if the release admission's
# FK/real ratio were 1.2 (the reach design's qualification target, unmeasured) and what the capped-loss travel model
# expects the block's margin to be under the chosen plan.
import glob
import io
import json
import math
import os
import re
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
for _path in (ROOT / "autonomy" / "manipulation", ROOT / "bin" / "laptop" / "manipulation"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

EVIDENCE_ROOT_ENV = "KC_EVIDENCE_ROOT"
SPREAD_AXES = tuple((d_x, math.sqrt(1.0 - d_x * d_x)) for d_x in (-0.10, -0.05, 0.0, 0.05, 0.10))
HOMOGRAPHY, BACK_SOLVED, SPREAD = "homography", "back-solved from the refusal, sign unknown", "not recorded: the recorded range"
STATE_LINE = re.compile(r"^\[STATE\] ([A-Z_]+) (\{.*\})\s*$", re.M)
OBSERVATION = re.compile(r"observation (obs-\d{8}T\d{6}Z-wrist-\d{4})")


@dataclass(frozen=True)
class Start:
    """One recorded start, as SEARCH decided from it (module docstring, WHAT IT HOLDS)."""

    row: str
    run: str
    anchor: Optional[tuple]  # PAD_FRAME metres; None: no SEARCH frame on record
    axes: tuple
    axis_source: str
    cube_out_mm: Optional[float]
    pan_from_search_deg: Optional[float]
    hardware: str


# ---- reading the evidence root ------------------------------------------------------------------------------------------
def _states(text: str) -> list:
    out = []
    for name, body in STATE_LINE.findall(text or ""):
        try:
            out.append((name, json.loads(body)))
        except ValueError:
            continue
    return out


def _search_pan(text: str):
    for name, fields in _states(text):
        if name == "SEARCH" and isinstance(fields.get("pose"), dict):
            return float(fields["pose"]["shoulder_pan"])
    return None


def _search_observation(text: str):
    at = (text or "").find("[STATE] SEARCH")
    found = OBSERVATION.search(text, at) if at >= 0 else None
    return None if found is None else found.group(1)


def _rounded(value: float, digits):
    return float(value) if digits is None else round(float(value), digits)


def _start_from(controller, row: str, run: str, record: dict, search_pan: float, hardware: str, digits=4) -> Optional[Start]:
    """A Start from a SEARCH perception record and the pan SEARCH stood at, rounded as pinned (digits 4 for the anchor and the
    axis, 1 for the cube_out, 2 for the pan) or not rounded at all (digits None); None when the record lacks what SEARCH
    needs."""
    cube = (record or {}).get("cube") or {}
    centre = ((record or {}).get("cube_by_template") or {}).get("cube_center_pad_m")
    homography = ((record or {}).get("pad_by_template") or {}).get("homography_image_to_pad")
    if not cube.get("contact_px") or not centre or homography is None or search_pan is None:
        return None
    coarse = controller.coarse_pregrasp_from_low_pose(cube["contact_px"], search_pan)
    axis = controller.reach_direction_pad(homography, cube["contact_px"])
    small = None if digits is None else digits - 3
    tenth = None if digits is None else digits - 2
    return Start(row, run, (_rounded(centre[0], digits), _rounded(centre[1], digits)), ((_rounded(axis[0], digits), _rounded(axis[1], digits)),), HOMOGRAPHY,
                 _rounded(coarse["cube_out_mm"], small), _rounded(float(coarse["pan_deg"]) - search_pan, tenth), hardware)


def evidence_starts(controller, root, digits=4) -> dict:
    """{run: Start} for every recorded SEARCH frame the evidence root at `root` carries (module docstring, WHAT IT READS),
    rounded as pinned (digits 4) or not rounded (digits None)."""
    root, out = Path(root), {}
    observations = root / "observations-kcd"
    for log in sorted(observations.glob("cycle-run-*-cycle_.txt")):
        text = log.read_text(errors="replace")
        name = _search_observation(text)
        hits = glob.glob(str(observations / "*" / "perception" / f"{name}.json")) + glob.glob(str(observations / "perception" / f"{name}.json")) if name else []
        if hits:
            start = _start_from(controller, "", log.name[: -len("-cycle_.txt")], json.loads(Path(hits[0]).read_text()), _search_pan(text), "", digits)
            if start is not None:
                out[start.run] = start
    for session in sorted(observations.glob("console-20260922T*")):
        states = [json.loads(path.read_text()) for path in sorted(session.glob("cycle-*.json"))]
        pan = next((float(entry["pose"]["shoulder_pan"]) for record in states for entry in record if entry.get("state") == "SEARCH" and isinstance(entry.get("pose"), dict)), None)
        for path in sorted((session / "perception").glob("obs-*-wrist-*.json")):
            if path.name.endswith("-overlay.json") or "-candidates" in path.name or "-remote" in path.name:
                continue
            start = _start_from(controller, "", session.name, json.loads(path.read_text()), pan, "", digits)
            if start is not None:
                out[start.run] = start
                break
    texts, records = {}, {}
    block = root / "one-block-20260926"
    for path in sorted(block.glob("*.output.json")):
        texts[path.name[: -len(".output.json")]] = json.loads(path.read_text()).get("text", "")
    for archive in sorted(block.glob("*.tgz")):
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                if member.name.startswith("history/") and member.name.endswith("/stdout.log"):
                    texts.setdefault(member.name.split("/")[1], tar.extractfile(member).read().decode(errors="replace"))
                elif "/perception/" in member.name and member.name.endswith(".json") and "-remote" not in member.name and "-candidates" not in member.name:
                    records[os.path.basename(member.name)[: -len(".json")]] = json.loads(tar.extractfile(member).read())
    phantom = root / "phantom-20260928"
    for path in sorted(phantom.glob("*.output.json")):
        texts[path.name[: -len(".output.json")]] = json.loads(path.read_text()).get("text", "")
    for path in sorted((phantom / "records").glob("obs-*.json")):
        records.setdefault(path.name[: -len(".json")], json.loads(path.read_text()))
    for run, text in texts.items():
        name = _search_observation(text)
        if name in records:
            start = _start_from(controller, "", run, records[name], _search_pan(text), "", digits)
            if start is not None:
                out[run] = start
    return out


# ---- the recorded starts, pinned -----------------------------------------------------------------------------------------
# Row numbers are the run table's of the 2026-09-30 reach evidence report; "in" marks the two starts the controller logs saw
# inside the pad. The (anchor, axis, cube_out, pan) of every row with a homography are what evidence_starts derives from the
# evidence root (test_reach_policies_replay.py holds them to it); the two runs of 2026-09-27 and the operator's block of
# 2026-09-30 are pinned from GET reads of the robot's history (its SEARCH perception and state lines); the 19:19 run of
# 2026-09-26 has no SEARCH perception record, and its row is its state lines (TARGET_SELECTED, PREGRASP).
_ONE = HOMOGRAPHY
RECORDED_STARTS = (
    Start("in", "cycle-run-20260919T213745Z", (0.0095, -0.115), ((-0.0168, 0.9999),), _ONE, 58.5, 0.21, "a block inside the pad"),
    Start("1", "2026-09-19 single actions", None, (), "no controller SEARCH frame", None, None, "agent single actions, placed 32.5 inside"),
    Start("2", "cycle-run-20260919T214535Z", (0.0234, -0.196), ((-0.004, 1.0),), _ONE, -24.9, 3.36, "refused before motion (approach travel bound)"),
    Start("3", "cycle-run-20260919T214827Z", (0.0244, -0.1967), ((0.0076, 1.0),), _ONE, -6.7, 2.54, "arm-only carry, PLACED false (overhangs)"),
    Start("4", "cycle-run-20260919T215644Z", (0.0233, -0.1407), ((0.017, 0.9999),), _ONE, 40.0, 1.53, "refused before motion (non-contact guard)"),
    Start("4", "cycle-run-20260919T215752Z", (0.0235, -0.1409), ((0.0173, 0.9998),), _ONE, 40.0, 1.57, "refused before motion (non-contact guard)"),
    Start("5", "cycle-run-20260920T092656Z", (0.0069, -0.1642), ((-0.0105, 0.9999),), _ONE, 18.1, -2.33, "grasp missed"),
    Start("6", "cycle-run-20260920T093143Z", (0.0067, -0.1637), ((-0.0228, 0.9997),), _ONE, 35.0, -1.35, "arm-only to FK +112, agent opened the gripper"),
    Start("7", "cycle-run-20260920T095543Z", (0.0068, -0.1787), ((-0.0263, 0.9997),), _ONE, 3.8, -1.17, "base-zero guard before the lift"),
    Start("8", "cycle-run-20260920T103136Z", (0.0113, -0.1681), ((-0.0396, 0.9992),), _ONE, 7.3, 2.94, "arm-only, PLACED 21.4 inside"),
    Start("9", "cycle-run-20260920T113604Z", (0.0448, -0.1779), ((0.0107, 0.9999),), _ONE, -3.0, 9.58, "grasp missed"),
    Start("10", "cycle-run-20260920T115018Z", (0.0121, -0.1927), ((-0.0228, 0.9997),), _ONE, 3.6, 1.40, "base advance 109.3, PLACED 52.4"),
    Start("in", "cycle-run-20260920T115912Z", (0.0114, -0.0954), ((-0.0157, 0.9999),), _ONE, 104.7, 1.23, "a block inside the pad"),
    Start("11", "cycle-run-20260920T120226Z", (-0.0387, -0.1844), ((-0.0584, 0.9983),), _ONE, 13.5, -11.85, "base advance 97.0, PLACED 47.0"),
    Start("12", "cycle-run-20260920T121205Z", (0.0619, -0.1883), ((0.0206, 0.9998),), _ONE, 9.2, 13.73, "base-zero guard after PREGRASP"),
    Start("13", "cycle-run-20260920T121517Z", (0.0689, -0.1869), ((0.0343, 0.9994),), _ONE, 9.2, 14.94, "base advance 99.9, PLACED 51.1"),
    Start("14", "cycle-run-20260920T133924Z", (-0.0053, -0.1818), ((-0.029, 0.9996),), _ONE, 15.1, -3.68, "base advance 94.1, PLACED 45.3"),
    Start("15", "console-20260922T083804Z-zxkydauq", (0.0065, -0.1557), ((-0.0166, 0.9999),), _ONE, 7.9, -1.35, "base advance 59.9, PLACED 35.6"),
    Start("16", "console-20260922T180557Z-5xeetlmj", (0.0061, -0.1667), ((-0.0001, 1.0),), _ONE, -16.0, -2.50, "base advance 79.3, PLACED 55.4"),
    Start("17", "console-20260922T194137Z-gellh5f4", (-0.0351, -0.1664), ((-0.0483, 0.9988),), _ONE, -2.3, -8.49, "ALIGN failure (recognizer size ceiling)"),
    Start("18", "console-20260926T191919Z-m8c5f9e9", (-0.0205, -0.18), SPREAD_AXES, SPREAD, -44.5, -4.83, "grasp missed (near block)"),
    Start("19", "console-20260926T201540Z-mb3c1048", (-0.0201, -0.196), ((-0.0418, 0.9991),), _ONE, 7.1, -1.59, "base advance 106.4, placed_new"),
    Start("20", "console-20260926T203744Z-m2476137", (-0.0936, -0.1698), ((-0.0863, 0.9963),), _ONE, 34.1, -20.33, "base advance 80.5, placed_new"),
    Start("21", "console-20260926T205849Z-m4dd17ec", (0.1604, -0.211), ((0.1194, 0.9928),), _ONE, -11.2, 42.68, "refused at SEARCH, not placeable (start 3)"),
    Start("21", "console-20260926T210818Z-m4a7665e", (0.1604, -0.2107), ((0.1212, 0.9926),), _ONE, -10.4, 42.65, "refused at SEARCH, not placeable (start 3)"),
    Start("22", "console-20260926T211727Z-mfa2a8c1", (0.0598, -0.2182), ((0.0406, 0.9992),), _ONE, -18.8, 17.42, "refused at SEARCH, not placeable"),
    Start("23", "console-20260926T212418Z-m76fe86c", (0.0757, -0.2047), ((0.0535, 0.9986),), _ONE, -3.4, 21.31, "ALIGN, cube not detected"),
    Start("24", "console-20260926T213112Z-m66738ff", (0.0821, -0.179), ((0.0665, 0.9978),), _ONE, 28.0, 21.82, "ALIGN, cube not detected"),
    Start("25", "console-20260927T141638Z-m2fc135a", (-0.0083, -0.1789), SPREAD_AXES, SPREAD, 10.7, 0.96, "capability required, respawned"),
    Start("25", "console-20260927T142146Z-m6ed2dd9", (-0.0081, -0.1794), SPREAD_AXES, SPREAD, 10.3, 0.97, "held, base-zero guard at the clearance lift"),
    Start("26", "console-20260928T173618Z-m79fa902", (0.0035, -0.1758), ((-0.0319, 0.9995),), _ONE, 47.6, 1.31, "capability required, respawned"),
    Start("26", "console-20260928T174150Z-m255e4fc", (0.0021, -0.1859), ((-0.0334, 0.9994),), _ONE, 37.2, 0.92, "capability required, respawned"),
    Start("27", "console-20260928T174207Z-m915d3e9", (0.0021, -0.1857), ((-0.0331, 0.9995),), _ONE, 37.4, 0.96, "base advance 97.8, on the pad, PLACED false by attribution"),
    Start("28", "console-20260930T071429Z-m0c6fb2a", (-0.0358, -0.213), ((0.0616, 0.9981), (-0.0616, 0.9981)), BACK_SOLVED, -5.2, -8.24,
          "refused at SEARCH, not placeable (along 124.7 against 120)"),
)
LIVE_RUN = "console-20260930T071429Z-m0c6fb2a"


# ---- the decisions ------------------------------------------------------------------------------------------------------
def _unit(axis) -> tuple:
    norm = math.hypot(float(axis[0]), float(axis[1]))
    return (float(axis[0]) / norm, float(axis[1]) / norm)


def _proven(controller, start: Start, axis) -> dict:
    """The installed rule's placement answer for one axis: {admitted, mode, advance, travel} or {admitted False, why}."""
    import placement_envelope

    grasp = max(float(start.cube_out_mm), 0.0)
    try:
        plan = controller.placement_plan(start.anchor, axis, grasp, controller.PLACE_SIDE_DRIFT_ALLOWANCE_MM)
    except controller.CycleFailed:
        why = placement_envelope.explain_refusal(controller, start.anchor, axis, grasp, controller.PLACE_SIDE_DRIFT_ALLOWANCE_MM)
        return {"admitted": False, "why": f"{why.get('constraint')} {why.get('deficit_mm')}"}
    return {"admitted": True, "policy": "proven", "mode": plan["mode"], "advance": float(plan["base_advance_mm"]), "travel": float(plan["fk_travel_mm"])}


def _portfolio(controller, start: Start, axis, policies) -> dict:
    """The placement selector's answer under `policies` beside the proven one, from the inputs SEARCH hands placement_plan,
    as a run naming the policies' words plans it: the controller read through a view whose PLACEMENT_ENVELOPE names them,
    since the loaded reach cap reads the run's words (module comment, WHAT IT DECIDES). The proven policy is the controller's
    own placement_plan, as SEARCH hands it to the selector."""
    import collections

    import placement_policy
    import placement_selector
    import stage_policy

    view = stage_policy.ControllerView(collections.ChainMap({"PLACEMENT_ENVELOPE": tuple(module.WORD for module in policies)}, vars(controller)))
    situation = placement_policy.Situation(start.anchor, axis, max(float(start.cube_out_mm), 0.0), False, controller.PLACE_SIDE_DRIFT_ALLOWANCE_MM)
    decision = placement_selector.decide(view, situation, policies, controller.placement_plan)
    plan = decision.selected
    if plan is None:
        why = decision.explanation
        return {"admitted": False, "why": f"{why.get('near_miss_policy')} {why.get('constraint')} {why.get('deficit_mm')}", "hint": why.get("hint")}
    return {"admitted": True, "policy": plan.policy, "mode": plan.mode, "advance": float(plan.base_advance_mm), "travel": float(plan.fk_travel_mm),
            "out": float(plan.arm_out_mm), "margin": float(plan.worst_margin_mm), "capped": float(plan.capped_loss_margin_mm)}


def _k_1_2_arm_reach(controller, start: Start, axis) -> bool:
    """WHAT-IF, deciding nothing: would arm_reach admit the block if the release admission's FK/real ratio were 1.2?"""
    import collections

    import placement_policy
    import policy_arm_reach
    import stage_policy

    view = stage_policy.ControllerView(collections.ChainMap({"FK_TRAVEL_OVERSTATEMENT": 1.2}, vars(controller)))
    return policy_arm_reach.plan(view, placement_policy.Situation(start.anchor, axis, max(float(start.cube_out_mm), 0.0), False, controller.PLACE_SIDE_DRIFT_ALLOWANCE_MM)).admitted


def verdict(controller, start: Start, placement: dict, mission: bool) -> str:
    """SEARCH's first refusal in the run's own order, else the placement: a mission run binds (placement) before the pan and
    pickup bands; a run without a mission target checks the pan band, the pickup band, then the placement."""
    pan = start.pan_from_search_deg is not None and abs(start.pan_from_search_deg) > controller.RETURN_PAN_BOUND_DEG
    near = start.cube_out_mm is not None and start.cube_out_mm < controller.PREGRASP_MIN_CUBE_OUT_MM
    placed = placement["admitted"]
    order = (("placement", not placed), ("pan band", pan), ("TOO_NEAR", near)) if mission else (("pan band", pan), ("TOO_NEAR", near), ("placement", not placed))
    for name, refused in order:
        if refused:
            return f"refused: {placement['why']}" if name == "placement" else f"refused: {name}"
    return f"{placement['policy']} {placement['mode']} a{placement['advance']:.0f} t{placement['travel']:.0f}"


def replay(controller, start: Start) -> list:
    """For each axis of `start`: {axis, before, after, before_placement, after_placement, arm_reach_at_k_1_2}."""
    import placement_selector

    mission = start.run.startswith("console-")
    out = []
    for axis in start.axes:
        axis = _unit(axis)
        before, after = _proven(controller, start, axis), _portfolio(controller, start, axis, placement_selector.POLICIES)
        out.append({"axis": axis, "before": verdict(controller, start, before, mission), "after": verdict(controller, start, after, mission), "before_placement": before,
                    "after_placement": after, "arm_reach_at_k_1_2": _k_1_2_arm_reach(controller, start, axis)})
    return out


def change(result: dict) -> str:
    """What the words change for one axis, read from the two verdicts: kept (the same proven plan), improved (a smaller
    advance), new (placed only with the words), LOST (placed only without them: never, test_reach_policies_replay.py), or
    still refused (by the band or the placement, before and after)."""
    before, after = result["before_placement"], result["after_placement"]
    placed_before, placed_after = not result["before"].startswith("refused"), not result["after"].startswith("refused")
    if placed_before and not placed_after:
        return "LOST"
    if not placed_before:
        return "new" if placed_after else "still refused"
    if after["policy"] == "proven":
        return "kept"
    return f"improved by {before['advance'] - after['advance']:.0f} mm of advance" if after["advance"] < before["advance"] else f"changed to {after['policy']}"


def _cells(controller, start: Start) -> list:
    if start.anchor is None:
        return [start.row, start.run, "-", "-", "-", start.axis_source, "not replayed", "not replayed", "-", "-", "-", start.hardware]
    results = replay(controller, start)
    outside = -controller.placed_margin_mm(start.anchor)
    def joined(key):
        values = sorted({result[key] for result in results})
        return values[0] if len(values) == 1 else " / ".join(values)
    changes = sorted({change(result) for result in results})
    placed = [result["after_placement"] for result in results if not result["after"].startswith("refused")]
    detail = " / ".join(sorted({f"out {plan['out']:.0f} margin {plan['margin']:.1f} capped-loss {plan['capped']:.1f}" for plan in placed})) or "-"
    k12 = sorted({"yes" if result["arm_reach_at_k_1_2"] else "no" for result in results})
    axes = f"{start.axes[0][0]:+.3f}, {start.axes[0][1]:.3f}" if len(start.axes) == 1 else f"{len(start.axes)} axes ({start.axis_source})"
    return [start.row, start.run, f"{start.anchor[0] * 1000:+.1f}, {start.anchor[1] * 1000:+.1f}", f"{outside:+.1f}", f"{start.cube_out_mm:+.1f}", axes, joined("before"),
            joined("after"), " / ".join(changes), detail, " / ".join(k12), start.hardware]


HEADER = ["row", "run", "start x, y (mm)", "outside (mm)", "cube_out", "reach axis", "before: 31c546c", "after: arm_reach + advance_then_reach", "change",
          "chosen plan", "arm_reach if k were 1.2", "hardware"]


def table(controller, starts=RECORDED_STARTS) -> str:
    """The replay table as Markdown, one row per start."""
    lines = ["| " + " | ".join(HEADER) + " |", "|" + "---|" * len(HEADER)]
    for start in starts:
        lines.append("| " + " | ".join(str(cell).replace("|", "/") for cell in _cells(controller, start)) + " |")
    return "\n".join(lines) + "\n"


def summary(controller, starts=RECORDED_STARTS) -> dict:
    """How many (start, axis) pairs each change covers, and the base advance the improved ones save (min, max)."""
    counts, saved = {}, []
    for start in starts:
        if start.anchor is None:
            counts["not replayed"] = counts.get("not replayed", 0) + 1
            continue
        for result in replay(controller, start):
            word = change(result)
            key = "improved" if word.startswith("improved") else word
            counts[key] = counts.get(key, 0) + 1
            if key == "improved":
                saved.append(result["before_placement"]["advance"] - result["after_placement"]["advance"])
    return {"counts": counts, "advance_saved_mm": (min(saved), max(saved)) if saved else None}


LEGEND = """Legend. before: what the installed controller (31c546c, no word) decides at SEARCH; after: the same inputs under the words
arm_reach and advance_then_reach, beside the proven policy, as a run naming them plans them (the loaded reach cap reads the
run's words), chosen by the least base motion (a tie within the advance loop's 8 mm tolerance goes to the proven policy). A verdict is SEARCH's first refusal in the run's own order (a mission run binds
before its pan and pickup bands, a run without a mission target checks the bands first) or the chosen plan: policy, mode,
advance a (mm), FK travel t (mm). chosen plan: the planned measured out (mm beyond the proven grasp radius), the release
admission's worst-case centre margin at the plan (mm; required 37.96) and the margin the capped-loss travel model expects.
arm_reach if k were 1.2: decides nothing; whether arm_reach alone would admit the block if the release admission's FK/real
ratio were 1.2 instead of 1.6 (unmeasured). A row with several axes replays each; its cells join the distinct answers.
"""


def main(argv=None) -> int:
    """python3 autonomy/tests/reach_policies_replay.py OUTPUT.md: the replay table of every recorded start, written there."""
    import subprocess

    import run_single_cube_cycle as controller

    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("usage: reach_policies_replay.py OUTPUT.md", file=sys.stderr)
        return 2
    controller.set_placement_envelope(())
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip() or "unknown"
    counted = summary(controller)
    lines = ["# Replay of every recorded start through the placement policies", "",
             f"Generated by autonomy/tests/reach_policies_replay.py at commit {head}: {len(RECORDED_STARTS)} rows, "
             f"changes per start and axis {json.dumps(counted['counts'], sort_keys=True)}, base advance saved by the improved ones "
             f"{counted['advance_saved_mm']} mm.", "", LEGEND, table(controller)]
    Path(argv[0]).write_text("\n".join(lines))
    print(f"wrote the replay of {len(RECORDED_STARTS)} recorded starts: {counted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
