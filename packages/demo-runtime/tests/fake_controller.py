#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""fake_controller.py: a stand-in for bin/laptop/manipulation/run_single_cube_cycle.py that touches NOTHING.

It takes the same argv (--do, --holding, --plan-only, --remote-ip, --robot-id, --pi-ssh, --scan-id), reads the same
environment (KC_EVENTS_PATH, KC_RUN_ID, KC_ARTIFACT_DIGEST) and writes contract v1 events to that file, one JSON object per
line, one write call per line. It opens no socket, no camera, no serial port and no image file: everything it reports is
SYNTHETIC and scripted. The runtime daemon runs it, and ONLY it, in --synthetic mode; the runtime tests run it as the child.

The script for `--do cycle` is a full base-advance placement: about 26 state transitions under the real controller's state
names, observation ids, perception results, predicates with thresholds and measurements, timing spans, and a run.finished
with outcome placed_new. `--do scan` (the scan_scene command) plays a scene scan of five pan-only views with its
SCENE_SCAN [STATE] lines and the scene-view, scene-pad and scene-block results, and ends with outcome unknown.

SIGINT is handled like the real controller: the handler only sets a flag; the script stops at the next step, records
stop.requested (origin operator_signal) and stop.acknowledged, prints the controller's terminal line and exits 4.

Scenarios, by the environment variable KC_FAKE_SCENARIO:
  crash               dies in the middle of the cycle with status 70, leaving a TORN last line in the event file
  already_placed      SEARCH finds the cube on the pad: DONE_ALREADY_PLACED, outcome already_placed_noop, exit 0
  refused             refused before any motion: action.refused, outcome refused, exit 3
  slow                the same script with a long pause per step
  never_acknowledges  SIGINT is ignored; the script runs to its normal end
  placed_short        the shape of the 2026-09-22 physical run: the cube is secured, placed and the base returns, and the
                      footprint clearance falls short of its target, so clearance_target_met measures FALSE and the run
                      ends placed_new with one false predicate. The case a console has to state without calling it unknown.
KC_FAKE_AUTO_MODE is the placement mode this fake's SEARCH "measures" for a target declared "auto" (ARM_ONLY, the default,
or BASE_ADVANCE); a target declared true measures BASE_ADVANCE and one declared false ARM_ONLY, as before. With
BASE_ADVANCE an auto run that carries no capability refuses at SEARCH exactly as a declared capability target's does.
With KC_FAKE_ARM_ALONE_FALLBACK=1 an auto target that measures ARM_ONLY is bound to the arm alone with an in-run fallback
that moves the base (the controller's additive placement words): a run of it that carries no capability refuses at SEARCH
with mission_fallback_capability_required, the selection ARM_ONLY, and a run that carries it is placed as before.
A SCRIPTED FAILURE MID-CYCLE, for the mission's hold: KC_FAKE_FAIL_CYCLES=N makes the first N `--do cycle` launches (counted
in the file KC_FAKE_FAIL_STATE, which is required with it) fail at the state KC_FAKE_FAIL_AT (ALIGN unless named) with the
reason code KC_FAKE_FAIL_CODE (align_cube_not_detected unless named): the script runs up to that state, emits run.finished
with outcome failed, the reason code, the phase and a failure carrying two synthetic measurements and the last observation,
writes the mission result naming the same code and state, and exits 5 like a CycleFailed (the arm holds). With
KC_FAKE_FAIL_HOLDING=1 the jaws closed on something first (a closed_on_something predicate that measured true), which is
the state of a cube held. KC_FAKE_FAIL_JAWS names what the jaws did before the failure, in the controller's own events:
"held" (as KC_FAKE_FAIL_HOLDING=1), "empty" (a close measured empty, then the jaws measured open), "set_down" (a close on
something, retention not shown, GRASP_SET_DOWN, then the jaws measured open: grasp_recover's set-down) or "set_down_refused"
(the same up to GRASP_SET_DOWN, the jaws never opened). KC_FAKE_FAIL_DETAIL, when set, is the failure's sentence in place
of the synthetic one. Launches after the Nth run the script as before.
KC_FAKE_STEP_S sets the pause per step. KC_FAKE_PRE_HANDLER_S waits that long BEFORE the SIGINT handler is installed (the
window in which a stop ends the process without an acknowledgement). KC_FAKE_LAUNCH_LOG names a file that gets one line per
launch, so a test can count how often the controller was started. KC_FAKE_GROUP_SENTINEL names a file: the fake then starts
one idle helper process INSIDE ITS OWN PROCESS GROUP (the way the real controller's ssh and scp children share its group) and
writes the helper's pid there. A stop that signalled the group would end the helper; a stop to the PID alone leaves it
running, and the test that asked for it ends it afterwards.

Frames: synthetic_frame() builds an image in memory with numpy, only when somebody asks for it. Nothing is read from or
written to disk. synthetic_record() deliberately carries fields a client must never see (a camera identity fingerprint, a
frame path): the runtime's allowlist has to drop them.
"""
import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time

CONTRACT_VERSION = "1"
SCENARIOS = ("default", "crash", "already_placed", "refused", "slow", "never_acknowledges", "placed_short")
OBSERVATION_ID = re.compile(r"obs-[0-9]{8}T[0-9]{6}Z-(wrist|front)-[0-9]{4}")
CYCLE_STATES = ("SEARCH", "PREGRASP", "ALIGN", "ALIGN", "APPROACH", "STRADDLING", "GRASP_CLOSE", "OBJECT_SECURED", "PLACEMENT_PLAN", "CARRY", "CARRY_REACH_LIMIT",
                "ARM_ONLY_INSUFFICIENT", "HOLD_OBSERVE", "BASE_MEASURED", "BASE_ADVANCE", "BASE_MEASURED", "HOLD_OBSERVE", "BASE_ADVANCE", "BASE_MEASURED", "CARRY",
                "RELEASE_ADMISSION", "RELEASED", "PLACED", "BASE_RETURN", "BASE_MEASURED", "BASE_RETURNED")
#: What the jaws did before a scripted failure (KC_FAKE_FAIL_JAWS); None is nothing recorded about them.
JAWS = (None, "held", "empty", "set_down", "set_down_refused")
OBSERVING_STATES = frozenset({"SEARCH", "ALIGN", "STRADDLING", "OBJECT_SECURED", "HOLD_OBSERVE", "BASE_MEASURED", "RELEASE_ADMISSION", "PLACED"})
MOVING_STATES = {"PREGRASP": "arm_pose", "APPROACH": "arm_pose", "GRASP_CLOSE": "gripper", "CARRY": "arm_pose", "BASE_ADVANCE": "base_pulse", "RELEASED": "gripper", "BASE_RETURN": "base_pulse"}
PREDICATES = {"OBJECT_SECURED": ("object_secured", 8.0, 14.2, "deg"), "RELEASE_ADMISSION": ("release_admitted", 3.4, 31.0, "mm"), "PLACED": ("placed", 3.4, 29.5, "mm"),
              "BASE_RETURNED": ("base_returned", 15.0, 6.5, "mm")}
_stop = {"set": False, "t_mono_ns": None}


class Writer:
    def __init__(self, path, run_id, artifact_digest):
        self.fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600) if path else None
        self.run_id, self.seq, self.gaps = run_id, 0, 0
        self.session = f"fake-controller-{os.getpid()}"
        self.producer = {"name": "fake-controller", "version": "0.0.0", "artifact_digest": artifact_digest or None, "contract_version": CONTRACT_VERSION}

    def emit(self, kind, payload, *, action_id=None, observation_id=None, reason_code=None, provenance=None):
        self.seq += 1
        record = {"schema_version": CONTRACT_VERSION, "seq": self.seq, "run_id": self.run_id, "action_id": action_id, "observation_id": observation_id, "kind": kind,
                  "producer": self.producer, "source_session": self.session, "t_mono_ns": time.monotonic_ns(), "clock_domain": "laptop_monotonic",
                  "t_wall_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "reason_code": reason_code, "payload": payload, "provenance": provenance}
        if self.fd is not None:
            os.write(self.fd, (json.dumps(record, separators=(",", ":")) + "\n").encode("ascii"))

    def torn(self):
        if self.fd is not None:
            os.write(self.fd, b'{"schema_version":"1","seq":' + str(self.seq + 1).encode("ascii") + b',"kind":"state.trans')


def joints(step):
    return {"shoulder_pan": round(-14.8 + 0.1 * step, 3), "shoulder_lift": round(-40.0 + 1.5 * step, 3), "elbow_flex": round(60.0 - 0.8 * step, 3),
            "wrist_flex": round(70.0 - 0.5 * step, 3), "wrist_roll": 0.0, "gripper": 12.0 if step < 6 or step > 21 else 38.5}


def stamp(step):
    return {"session": "synthetic-host", "seq": 1000 + step, "sample_mono_ns": None, "clock_domain": "pi_monotonic"}


def finish(writer, outcome, predicates, exit_status, retries=0, failure=None):
    full = {"object_secured": None, "placed": None, "clearance_target_met": None, "base_returned": None, **predicates}
    payload = {"outcome": outcome, "exit_status": None, "predicates": full, "automatic_retries": retries, "task_control_interventions": 0,
               "evidence_complete": writer.gaps == 0, "evidence_gaps": writer.gaps}
    if failure is not None:  # the controller's optional extension: why, in which state, on which values and frames
        payload.update({"reason_code": failure["reason_code"], "phase": failure["phase"],
                        "failure": {"detail": failure["detail"], "measurements": failure["measurements"], "observation_ids": failure["observation_ids"]}})
    writer.emit("run.finished", payload, reason_code=None if failure is None else failure["reason_code"])
    return exit_status


def scripted_failure():
    """(state, reason code, holding) for THIS cycle launch, or None. Counts the launch in KC_FAKE_FAIL_STATE first."""
    count = int(os.environ.get("KC_FAKE_FAIL_CYCLES") or 0)
    if count <= 0:
        return None
    state_file = os.environ["KC_FAKE_FAIL_STATE"]
    with open(state_file, "a", encoding="ascii") as handle:
        handle.write("cycle\n")
    with open(state_file, encoding="ascii") as handle:
        launched = len(handle.read().splitlines())
    if launched > count:
        return None
    state = os.environ.get("KC_FAKE_FAIL_AT") or "ALIGN"
    if state not in CYCLE_STATES:
        raise SystemExit(f"KC_FAKE_FAIL_AT must name a state of the script, not {state!r}")
    jaws = os.environ.get("KC_FAKE_FAIL_JAWS") or ("held" if os.environ.get("KC_FAKE_FAIL_HOLDING") == "1" else None)
    if jaws not in JAWS:
        raise SystemExit(f"KC_FAKE_FAIL_JAWS must be one of {sorted(item for item in JAWS if item)}, not {jaws!r}")
    return state, os.environ.get("KC_FAKE_FAIL_CODE") or "align_cube_not_detected", jaws


def stopped(writer):
    latency = max(0, time.monotonic_ns() - (_stop["t_mono_ns"] or time.monotonic_ns()))
    writer.emit("stop.requested", {"origin": "operator_signal", "command_id": None})
    writer.emit("stop.acknowledged", {"acknowledged_by": "fake-controller", "command_id": None, "latency_ns": latency})
    print("STOPPED: stopped by the operator (synthetic). The arm HOLDS.", flush=True)
    return finish(writer, "stopped", {}, 4)


def observe(writer, step, stamp_text, counter, role="wrist"):
    observation_id = f"obs-{stamp_text}-{role}-{counter:04d}"
    writer.emit("timing.span", {"span": "capture", "duration_ns": 180_000_000 + step * 1_000_000, "clock_domain": "laptop_monotonic"})
    writer.emit("observation.captured", {"source_role": role, "media": "frame", "width_px": 640, "height_px": 480, "content_digest": None, "source_stamp": stamp(step), "retained": True},
                observation_id=observation_id)
    writer.emit("timing.span", {"span": "inference", "duration_ns": 95_000_000, "clock_domain": "laptop_monotonic"})
    writer.emit("perception.result", {"detector": "synthetic-detector", "observation_ids": [observation_id], "result": "detected", "confidence": 0.91,
                                      "measurements": [{"name": "cube_center_pad_x", "value": round(0.041 + 0.001 * step, 4), "unit": "m"}, {"name": "cube_center_pad_y", "value": -0.012, "unit": "m"}]},
                provenance={"observation_ids": [observation_id], "model_identity": "synthetic/no-model", "calibration_digest": None, "config_digest": None, "module_generation": None, "reconstructed": False})
    return observation_id


def act(writer, step, action_type, plan_only, pause):
    action_id = f"act-{step:03d}"
    writer.emit("action.planned", {"action_type": action_type, "plan_only": plan_only, "parameters": [{"name": "step", "value": step, "unit": None}]}, action_id=action_id)
    if plan_only:
        return
    writer.emit("action.started", {"action_type": action_type}, action_id=action_id)
    time.sleep(pause)
    writer.emit("action.completed", {"action_type": action_type, "result": "ok", "duration_ns": int(pause * 1e9)}, action_id=action_id)
    writer.emit("timing.span", {"span": "motion", "duration_ns": int(pause * 1e9), "clock_domain": "laptop_monotonic"})
    writer.emit("measured.state", {"joints": joints(step), "base_velocity": {"x_mps": 0.0, "y_mps": 0.0, "theta_dps": 0.0}, "consumed_source_stamp": stamp(step)})


def run_cycle(writer, scenario, plan_only, pause, failing=None):
    stamp_text, previous, captures, last_observation = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), None, 0, None
    if scenario == "refused":
        writer.emit("action.refused", {"action_type": "cycle", "before_motion": True, "detail": "synthetic refusal: the measured pose is outside the start band"}, action_id="act-000", reason_code="start_pose_out_of_band")
        print("REFUSED before any motion: synthetic refusal", flush=True)
        return finish(writer, "refused", {}, 3)
    for step, state in enumerate(CYCLE_STATES):
        if _stop["set"]:
            return stopped(writer)
        writer.emit("state.transition", {"machine": "cycle", "from_state": previous, "to_state": state})
        print(f"[STATE] {state} {{}}", flush=True)
        previous = state
        if state in OBSERVING_STATES:
            captures += 1
            last_observation = observe(writer, step, stamp_text, captures)
        if state == "SEARCH" and scenario == "already_placed":
            writer.emit("state.transition", {"machine": "cycle", "from_state": "SEARCH", "to_state": "DONE_ALREADY_PLACED"})
            writer.emit("predicate.evaluated", {"predicate": "placed", "value": True, "threshold": 3.4, "measured": 30.2, "unit": "mm",
                                                "evidence": [{"type": "frame", "observation_ids": [last_observation], "scalar_name": None}], "consumed_source_stamp": None})
            print("CYCLE DONE: the cube is already on the pad (synthetic)", flush=True)
            return finish(writer, "already_placed_noop", {"placed": True, "clearance_target_met": True}, 0)
        if failing is not None and state == failing[0]:
            return failed_at(writer, state, failing[1], failing[2], last_observation)
        if state in MOVING_STATES:
            act(writer, step, MOVING_STATES[state], plan_only, pause)
        else:
            time.sleep(pause)
        if state in PREDICATES:
            name, threshold, measured, unit = PREDICATES[state]
            evidence = [{"type": "frame", "observation_ids": [last_observation], "scalar_name": None}] if state != "BASE_RETURNED" else [{"type": "pad_measurement", "observation_ids": [last_observation], "scalar_name": None}]
            writer.emit("predicate.evaluated", {"predicate": name, "value": True, "threshold": threshold, "measured": measured, "unit": unit, "evidence": evidence, "consumed_source_stamp": stamp(step)})
        if state == "PLACED" and scenario == "placed_short":
            # Measured where the real controller measures it: the placed cube's edge margin against the clearance the
            # footprint target asks for. The figures are the 2026-09-22 run's, so a console shows the shortfall it showed.
            writer.emit("predicate.evaluated", {"predicate": "clearance_target_met", "value": False, "threshold": 37.96, "measured": 35.57, "unit": "mm",
                                                "evidence": [{"type": "pad_measurement", "observation_ids": [last_observation], "scalar_name": None}],
                                                "consumed_source_stamp": stamp(step)})
        if scenario == "crash" and step == 8:
            writer.torn()
            os._exit(70)
    if _stop["set"]:
        return stopped(writer)
    writer.emit("state.transition", {"machine": "cycle", "from_state": previous, "to_state": "DONE"})
    if scenario == "placed_short":
        print("CYCLE DONE: placed_new, clearance target missed (synthetic)", flush=True)
        return finish(writer, "placed_new", {"object_secured": True, "placed": True, "clearance_target_met": False, "base_returned": True}, 0)
    print("CYCLE DONE: placed_new (synthetic)", flush=True)
    return finish(writer, "placed_new", {"object_secured": True, "placed": True, "clearance_target_met": True, "base_returned": True}, 0)


def _jaw_predicate(writer, name, value):
    writer.emit("predicate.evaluated", {"predicate": name, "value": value, "threshold": 1.2, "measured": 14.2 if value else 1.0, "unit": "gripper_units",
                                        "evidence": [], "consumed_source_stamp": None})


def failed_at(writer, state, reason_code, jaws, last_observation):
    """The scripted CycleFailed: the arm holds where it is, exit 5. Synthetic values: nothing was measured."""
    if jaws in ("held", "set_down", "set_down_refused"):
        _jaw_predicate(writer, "closed_on_something", True)
    if jaws == "empty":
        _jaw_predicate(writer, "closed_on_something", False)
        _jaw_predicate(writer, "gripper_opened", True)
    if jaws in ("set_down", "set_down_refused"):
        _jaw_predicate(writer, "object_secured", False)
        writer.emit("state.transition", {"machine": "cycle", "from_state": state, "to_state": "GRASP_SET_DOWN"})
    if jaws == "set_down":
        _jaw_predicate(writer, "gripper_opened", True)
    observation_ids = [last_observation] if last_observation else []
    print(f"CYCLE FAILED: {state}: {reason_code} (synthetic). The arm HOLDS.", flush=True)
    return finish(writer, "failed", {}, 5, failure={"reason_code": reason_code, "phase": state, "detail": os.environ.get("KC_FAKE_FAIL_DETAIL") or f"{state}: synthetic failure",
                                                    "measurements": [{"name": "radial_mm", "value": 14.5, "unit": "mm"}, {"name": "lateral_mm", "value": -2.6, "unit": "mm"}],
                                                    "observation_ids": observation_ids})


def run_single(writer, kind, plan_only, pause):
    if _stop["set"]:
        return stopped(writer)
    if kind == "observe":
        observe(writer, 0, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), 1)
    else:
        act(writer, 0, {"pan": "arm_pose", "move": "arm_pose", "grasp": "arm_pose", "grip": "gripper", "base": "base_pulse", "exit": "arm_pose"}[kind], plan_only, pause)
    if _stop["set"]:
        return stopped(writer)
    return finish(writer, "unknown", {}, 0)


# ---- the scene scan (--do scan): the shape of the console's scene-scan scenario (track U2), SYNTHETIC values ----------------

SCAN_HOME_PAN_DEG = -14.8
SCAN_VIEWS = (("home", 0.0), ("minus_15", -15.0), ("home", 0.0), ("plus_15", 15.0), ("home", 0.0))


def _named(pairs):
    return [{"name": name, "value": value, "unit": unit} for name, value, unit in pairs]


def _scene(writer, detector, observation_ids, pairs):
    writer.emit("perception.result", {"detector": detector, "observation_ids": list(observation_ids), "result": "detected", "confidence": None,
                                      "measurements": _named(pairs)},
                provenance={"observation_ids": list(observation_ids), "model_identity": "synthetic/no-model", "calibration_digest": None,
                            "config_digest": None, "module_generation": None, "reconstructed": True})


def run_scan(writer, plan_only, pause):
    """Pan-only views from the low scan pose: each a SCENE_SCAN transition, its [STATE] line (the fields travel only
    there), a pan (planned only under --plan-only), a frame and a scene-view result; then the fused scene as one
    scene-pad result and one scene-block result per block, spelled as model/sceneMap.ts reads them. Every value is
    SYNTHETIC: nothing was measured."""
    stamp_text, previous, seen = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), None, []
    for index, (view, offset) in enumerate(SCAN_VIEWS):
        if _stop["set"]:
            return stopped(writer)
        pan = round(SCAN_HOME_PAN_DEG + offset, 2)
        writer.emit("state.transition", {"machine": "cycle", "from_state": previous, "to_state": "SCENE_SCAN"})
        previous = "SCENE_SCAN"
        if index > 0:
            act(writer, index, "arm_pose", plan_only, pause)
        observation_id = observe(writer, index, stamp_text, index + 1)
        line = f"[STATE] SCENE_SCAN {json.dumps({'view': view, 'pan_deg': pan, 'observation_id': observation_id})}"
        print(line, flush=True)
        writer.emit("diagnostic.text", {"stream": "stdout", "level": "info", "text": line, "redacted": False})
        seen.append(observation_id)
        half = 0.12
        centre_x = round(0.3 * offset / 15.0 * half, 4)
        corners = ((centre_x - half, -0.05), (centre_x + half, -0.05), (centre_x + half, -0.25), (centre_x - half, -0.25))
        _scene(writer, "scene-view", [observation_id],
               [("view_index", index, None), ("view", view, None), ("pan_deg", pan, "deg"), ("pad_frame_valid", True, None), ("landmark_inliers", 97, None)]
               + [(f"footprint_{corner}_{axis}", round(value, 4), "m") for corner, point in enumerate(corners) for axis, value in zip(("x", "y"), point)]
               + [("accepted", True, None)])
    _scene(writer, "scene-pad", seen,
           [("axis_x", 0.0, "m"), ("axis_y", -0.37, "m"), ("home_pan_deg", SCAN_HOME_PAN_DEG, "deg"), ("home_bearing_deg", 0.0, "deg"),
            ("pan_band_deg", 25.0, "deg"), ("fit_rotation_deg", 15.0, "deg"), ("measured_pan_change_deg", 15.0, "deg"),
            ("fit_residual_mm", 0.8, "mm"), ("fit_accepted", True, None), ("views_seen", len(seen), None)])
    _scene(writer, "scene-block", seen[:1],
           [("pad_x", -0.0201, "m"), ("pad_y", -0.196, "m"), ("class", "ARM_ONLY", None), ("best_observation", seen[0], None),
            ("views_seen", 3, None), ("spread_mm", 1.4, "mm"), ("inferred", False, None)])
    if _stop["set"]:
        return stopped(writer)
    print("SCAN DONE: the scene of five views (synthetic)", flush=True)
    return finish(writer, "unknown", {}, 0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="fake_controller.py", description="SYNTHETIC controller: scripted contract events, no hardware.")
    parser.add_argument("--do", required=True)
    parser.add_argument("--holding", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--remote-ip", required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--pi-ssh", default=None)
    parser.add_argument("--scan-id", default="fake")
    args = parser.parse_args(argv)
    scenario = os.environ.get("KC_FAKE_SCENARIO") or "default"
    if scenario not in SCENARIOS:
        parser.error(f"unknown scenario; one of {list(SCENARIOS)}")
    if os.environ.get("KC_FAKE_LAUNCH_LOG"):
        with open(os.environ["KC_FAKE_LAUNCH_LOG"], "a", encoding="ascii") as handle:
            handle.write(f"{os.getpid()} {args.scan_id}\n")
    if os.environ.get("KC_FAKE_GROUP_SENTINEL"):  # same session, same process group as this process: NOT start_new_session
        helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(os.environ["KC_FAKE_GROUP_SENTINEL"], "w", encoding="ascii") as handle:
            handle.write(f"{helper.pid} {os.getpgid(helper.pid)} {os.getpgid(0)}\n")
    time.sleep(float(os.environ.get("KC_FAKE_PRE_HANDLER_S") or 0.0))
    if scenario == "never_acknowledges":
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    else:
        signal.signal(signal.SIGINT, lambda *_: _stop.update(set=True, t_mono_ns=time.monotonic_ns()))
    pause = float(os.environ.get("KC_FAKE_STEP_S") or (0.25 if scenario == "slow" else 0.03))
    writer = Writer(os.environ.get("KC_EVENTS_PATH"), os.environ.get("KC_RUN_ID") or args.scan_id, os.environ.get("KC_ARTIFACT_DIGEST"))
    kind = args.do.partition(":")[0]
    writer.emit("run.started", {"command_id": None, "command_type": None, "data_mode": "synthetic", "plan_only": args.plan_only, "holding": args.holding})
    writer.emit("run.config", {"config_digest": hashlib.sha256(b"synthetic fake controller config").hexdigest(),
                               "summary": [{"name": "scenario", "value": scenario, "unit": None}, {"name": "plan_only", "value": args.plan_only, "unit": None}]})
    if kind == "cycle":
        refusal = _refuses_at_search(args.scan_id)
        if refusal is not None:
            return _search_refusal(writer, args.scan_id, refusal)
        failing = scripted_failure()
        status = run_cycle(writer, scenario, args.plan_only, pause, failing)
        write_mission_result(status, args.scan_id, failing)
        return status
    if kind == "scan":
        return run_scan(writer, args.plan_only, pause)
    if kind in ("observe", "pan", "move", "grasp", "grip", "base", "exit"):
        return run_single(writer, kind, args.plan_only, pause)
    parser.error("unknown action")
    return 2


# ---- the mission result, when the runtime named a mission target ----------------------------------------------------------------

def write_mission_result(status: int, run_id: str, failing=None) -> None:
    """A mission's run answers in the MISSION RESULT file the target file names (autonomy/manipulation/target_binding.py
    owns both schemas; the runtime parses what is written here with it, so a document this writes wrongly fails there).

    Only when KC_MISSION_TARGET is set; a legacy command never writes one. Written with json alone, because the fake
    imports nothing from the robot stack (test_bundle pins that). What it writes is chosen by KC_FAKE_MISSION_RESULT:
      placed (default)  an exit-0 run whose document claims the placement: bound, selected, placed, earlier targets
                        undisturbed and, for a capability target, three relayed commands accepted and all three
                        APPLIED by the gateway (result version 2 counts both; the claim rests on applied);
      echo_mismatch     the same document naming ANOTHER mission;
      absent            nothing is written;
      capability_required_always  the SEARCH refusal below even for a run that CARRIES the capability (a second refusal).
    A run that did not exit 0 writes a finished document with the outcome its exit status says and nothing claimed.
    Capability evidence is written for a run that CARRIES the capability (target_binding refuses it otherwise).
    Synthetic positions: nothing here was measured and nothing is ever read as a measurement."""
    path = os.environ.get("KC_MISSION_TARGET")
    choice = os.environ.get("KC_FAKE_MISSION_RESULT") or "placed"
    if not path or choice == "absent":
        return
    with open(path, encoding="utf-8") as handle:
        target = json.load(handle)
    if target.get("run_id") != run_id:
        return
    declared = target["targets"]
    index = [entry["target_id"] for entry in declared].index(target["target_id"])
    modes = _measured_modes(declared)
    positions = [[0.30 + 0.05 * position, 0.0] for position in range(len(declared))]
    bound = positions[index] if index == 0 else declared[index]["bound_pad_m"]
    outcome = {0: "placed_new", 3: "refused", 4: "stopped", 5: "failed"}.get(status, "unknown")
    placed = outcome == "placed_new"
    document = {
        "schema": "kc.mission_result", "v": 3, "mission_id": target["mission_id"], "target_id": target["target_id"],
        "run_id": run_id, "stage": "finished",
        "bindings": [{"target_id": entry["target_id"], "bound_pad_m": positions[position], "mode": modes[position],
                      "observation_id": "wrist-0001"} for position, entry in enumerate(declared)] if index == 0 else None,
        "selection": {"observation_id": "wrist-0001", "seen_pad_m": bound, "bound_pad_m": bound, "distance_mm": 0.0,
                      "mode": modes[index]},
        "outcome": outcome, "final_state": "DONE" if placed else (failing[0] if failing and status == 5 else "ABORTED"),
        "reason_code": None if placed else (failing[1] if failing and status == 5 else "fake_" + outcome),
        "predicates": {"object_secured": placed, "placed": placed, "base_returned": placed},
        "placement": {"placed": True, "placed_pad_m": [0.0, 0.03 * index], "on_pad_count": 1, "release_distance_mm": 4.0,
                      "release_tolerance_mm": 26.0, "reason_code": None, "observation_id": "wrist-0009"} if placed else None,
        "capability": {"generation": 7, "pid": 4242, "accepted": 3, "applied": 3, "dropped": {}} if target["capability"] is not None and placed else None,
    }
    if choice == "echo_mismatch":
        document["mission_id"] = "mission-000000000000"
    temporary = target["result_path"] + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(document, handle, sort_keys=True)
    os.replace(temporary, target["result_path"])


def _measured_modes(declared: list) -> list:
    """What this fake's SEARCH "measures" per declared target: true is BASE_ADVANCE, false is ARM_ONLY, and "auto" is
    KC_FAKE_AUTO_MODE (ARM_ONLY unless a scenario says BASE_ADVANCE). Synthetic: nothing is measured."""
    auto = os.environ.get("KC_FAKE_AUTO_MODE") or "ARM_ONLY"
    if auto not in ("ARM_ONLY", "BASE_ADVANCE"):
        raise SystemExit(f"KC_FAKE_AUTO_MODE must be ARM_ONLY or BASE_ADVANCE, not {auto!r}")
    return [auto if entry["requires_capability"] == "auto" else ("BASE_ADVANCE" if entry["requires_capability"] else "ARM_ONLY") for entry in declared]


def _mission_target(run_id: str):
    """The mission target file this run was given, or None (no KC_MISSION_TARGET, or a file of another run)."""
    path = os.environ.get("KC_MISSION_TARGET")
    if not path:
        return None
    with open(path, encoding="utf-8") as handle:
        target = json.load(handle)
    return target if target.get("run_id") == run_id else None


def _refuses_at_search(run_id: str):
    """The code of the real controller's SEARCH refusal that asks for the capability, or None. For a target whose cube
    measures BASE_ADVANCE (a declared capability target's always, an auto target's when KC_FAKE_AUTO_MODE says so): a run
    that carries no capability (or, with KC_FAKE_MISSION_RESULT=capability_required_always, any run) refuses with
    mission_capability_required. With KC_FAKE_ARM_ALONE_FALLBACK=1, for an auto target that measures ARM_ONLY: a run that
    carries no capability refuses with mission_fallback_capability_required. Both after TARGET_SELECTED and before anything
    is picked up (run_single_cube_cycle.py)."""
    target = _mission_target(run_id)
    if target is None:
        return None
    index = [entry["target_id"] for entry in target["targets"]].index(target["target_id"])
    mode = _measured_modes(target["targets"])[index]
    if mode == "ARM_ONLY" and target["targets"][index]["requires_capability"] == "auto" and os.environ.get("KC_FAKE_ARM_ALONE_FALLBACK") == "1":
        return "mission_fallback_capability_required" if target["capability"] is None else None
    if mode != "BASE_ADVANCE":
        return None
    return "mission_capability_required" if target["capability"] is None or os.environ.get("KC_FAKE_MISSION_RESULT") == "capability_required_always" else None


def _search_refusal(writer, run_id: str, code: str = "mission_capability_required") -> int:
    """SEARCH, TARGET_SELECTED, then the refusal: exit 5 (the arm holds), the bound selection written back, nothing placed."""
    stamp_text = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    writer.emit("state.transition", {"machine": "cycle", "from_state": None, "to_state": "SEARCH"})
    observe(writer, 0, stamp_text, 1)
    writer.emit("state.transition", {"machine": "cycle", "from_state": "SEARCH", "to_state": "TARGET_SELECTED"})
    if code == "mission_capability_required":
        print("CYCLE FAILED: SEARCH: this target's cube measures BASE_ADVANCE (synthetic). The arm HOLDS.", flush=True)
    else:
        print("CYCLE FAILED: SEARCH: this target's cube is placed by the arm alone and its fallback moves the base (synthetic). The arm HOLDS.", flush=True)
    target = _mission_target(run_id)
    declared = target["targets"]
    index = [entry["target_id"] for entry in declared].index(target["target_id"])
    modes = _measured_modes(declared)
    positions = [[0.30 + 0.05 * position, 0.0] for position in range(len(declared))]
    bound = positions[index] if index == 0 else declared[index]["bound_pad_m"]
    document = {
        "schema": "kc.mission_result", "v": 3, "mission_id": target["mission_id"], "target_id": target["target_id"], "run_id": run_id,
        "stage": "finished",
        "bindings": [{"target_id": entry["target_id"], "bound_pad_m": positions[position], "mode": modes[position],
                      "observation_id": "wrist-0001"} for position, entry in enumerate(declared)] if index == 0 else None,
        "selection": {"observation_id": "wrist-0001", "seen_pad_m": bound, "bound_pad_m": bound, "distance_mm": 0.0, "mode": modes[index]},
        "outcome": "failed", "final_state": "TARGET_SELECTED", "reason_code": code,
        "predicates": {"object_secured": None, "placed": None, "base_returned": None}, "placement": None, "capability": None,
    }
    temporary = target["result_path"] + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(document, handle, sort_keys=True)
    os.replace(temporary, target["result_path"])
    return finish(writer, "failed", {}, 5)


# ---- synthetic observations, built in memory and only on request ----------------------------------------------------------------

def _seed(observation_id: str) -> int:
    return int(hashlib.sha256(observation_id.encode("ascii")).hexdigest()[:8], 16)


def _cube_box(observation_id: str) -> tuple:
    seed = _seed(observation_id)
    left, top = 260 + seed % 90, 200 + (seed >> 8) % 70
    return left, top, left + 46, top + 46


def synthetic_frame(observation_id: str, what: str = "raw"):
    """A 640x480 BGR image: a floor, a light pad, a yellow cube. 'overlay' adds the outlines perception would draw."""
    import numpy as np

    if not OBSERVATION_ID.fullmatch(observation_id):
        return None
    image = np.full((480, 640, 3), (52, 56, 60), dtype=np.uint8)
    image[150:420, 140:520] = (205, 210, 214)
    left, top, right, bottom = _cube_box(observation_id)
    image[top:bottom, left:right] = (40, 200, 235)
    if what == "overlay":
        for (x0, y0, x1, y1), color in (((140, 150, 520, 420), (80, 220, 80)), ((left, top, right, bottom), (60, 60, 240))):
            image[y0:y0 + 3, x0:x1], image[y1 - 3:y1, x0:x1], image[y0:y1, x0:x0 + 3], image[y0:y1, x1 - 3:x1] = color, color, color, color
    return image


def synthetic_record(observation_id: str):
    match = OBSERVATION_ID.fullmatch(observation_id)
    if not match:
        return None
    return {"observation_id": observation_id, "schema_version": "world-observation/1", "timestamp_utc": "2026-01-01T00:00:00Z",
            "camera": {"role": match.group(1), "identity_fingerprint": "synthetic-fingerprint-must-not-leave", "image_orientation": "upright", "width_px": 640, "height_px": 480},
            "raw_frame_path": f"frames/{observation_id}.png",
            "robot_state": {"source": "synthetic", "measured_joints_deg": joints(3), "measured_joints_post_capture_deg": joints(3), "joint_stability_max_delta_deg": 0.0,
                            "base_velocity": {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}},
            "camera_pose": {"status": "UNCALIBRATED", "frame": "camera", "T_base_camera": None, "missing_calibration": ["hand_eye"], "reason": "synthetic"},
            "scan": None, "gripper_state": "open", "cube_held": "no", "notes": "synthetic"}


def synthetic_perception(observation_id: str):
    if not OBSERVATION_ID.fullmatch(observation_id):
        return None
    left, top, right, bottom = _cube_box(observation_id)
    return {"camera": "wrist", "pad": {"detected": True, "confidence": 0.93, "visible_fraction": 1.0, "clipped_sides": [], "orientation_resolved": True, "boundary_rms_px": 1.4,
                                       "camera_height_m": 0.21, "view_elevation_deg": 58.0, "corners_px": [[140, 150], [520, 150], [520, 420], [140, 420]]},
            "pad_by_template": {"pad_identity": True, "landmark_inliers": 41, "pad_frame_valid": True, "reason": "synthetic"},
            "cube": {"detected": True, "confidence": 0.91, "clipped": False, "box_px": [left, top, right, bottom], "contact_px": [[left, bottom], [right, bottom]]},
            "cube_by_template": {"cube_center_pad_m": [0.041, -0.012], "inside_pad_outline": True}, "source_file": "synthetic-path-must-not-leave"}


if __name__ == "__main__":
    sys.exit(main())
