# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""A synthetic stage for tests/test-browser-layout.sh: the console-api tests' StubRuntime behind the real console_api,
on loopback, with no robot and no runtime. It walks one page through the three states the layout ruling names:

  idle     a runtime restarted today whose record still holds yesterday's FAILED mission (History's, not current)
  running  after T1 seconds, a mission of this session starts a run (snapshot active_run, events emitted live)
  ended    after T2 seconds, that run ends FAILED on a mission check refusal, with the long account: what the robot
           did, what the check refused, why it stopped with its measurements, where the robot was left, and the steps

With START=ready the robot's motion is permitted, so idle reads Ready, the normal state before Start on the robot, which
draws no readiness sentence (verify-r5 M1); with START=not_ready (the default) idle reads "Not ready: Robot motion is held".

usage: python3 tests/browser-layout/stage.py PORT WEB_BUILD [T1 [T2 [START]]]
"""
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "apps/console-api/tests")
from aiohttp import web  # noqa: E402
import support as S  # noqa: E402

port, build = int(sys.argv[1]), sys.argv[2]
T1 = float(sys.argv[3]) if len(sys.argv) > 3 else 15.0
T2 = float(sys.argv[4]) if len(sys.argv) > 4 else 30.0
START = sys.argv[5] if len(sys.argv) > 5 else "not_ready"
assert START in ("ready", "not_ready"), START
assert port not in (8770, 8771), "never the operator's console"
CODE = "mission_earlier_target_disturbed"

now = datetime.now(timezone.utc)
session = "rt-" + (now - timedelta(minutes=10)).strftime("%Y%m%dT%H%M%SZ") + "-1a70c701"
yday = now - timedelta(days=1)
OLD_RUN = "console-" + yday.strftime("%Y%m%dT%H%M%SZ") + "-m0ld0001"


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def ev(run, seq, kind, t, payload, reason=None, source="rt-synthetic-earlier"):
    return {"schema_version": "1", "seq": seq, "run_id": run, "action_id": None, "observation_id": None,
            "producer": {"name": "demo-runtime", "version": "0.1.0", "artifact_digest": None, "contract_version": "1"},
            "source_session": source, "t_mono_ns": seq * 1000, "clock_domain": "laptop_monotonic", "t_wall_utc": t,
            "reason_code": reason, "provenance": None, "kind": kind, "payload": payload}


def row(run, t0, outcome, seconds=20):
    return {"run_id": run, "command_id": run[-8:], "command_type": "start_cycle", "started_wall_utc": iso(t0),
            "finished_wall_utc": iso(t0 + timedelta(seconds=seconds)), "outcome": outcome, "exit_status": 5, "evidence_complete": True,
            "evidence_gaps": 0, "artifact_digest": None, "adopted": False, "data_mode": "synthetic", "bundle_digest": None}


def mission(mid, started, run, state, reason, phase, next_actions, attempts=True):
    attempt = {"attempt": 1, "run_id": run, "park_run_id": None, "outcome_class": "fatal", "reason_code": reason, "phase": phase,
               "measurements": [], "observation_ids": [], "stop_state": {"holding": False, "base_ahead": False, "moved": True},
               "next_actions": next_actions, "budget_left": 0, "t_wall_utc": iso(started + timedelta(seconds=30))}
    return {"mission_record_version": "1", "mission_id": mid, "state": state, "data_mode": "synthetic", "performs_motion": False,
            "execution_label": "synthetic-layout-stage", "started_wall_utc": iso(started), "selected_target": "cube", "waiting_reason": None,
            "detail": None, "stop_requested": False, "stop_requested_wall_utc": None, "records_after_end": 0,
            "targets": [{"target_id": "cube", "label": "the block", "reachable": True, "requires_capability": "auto", "state": state, "runs": [run],
                         "attempts": [attempt] if attempts else [], "retries": [], "park_runs": []}],
            "activations": [], "hold": None, "next_actions": next_actions}


stub = S.StubRuntime(data_mode="synthetic")
stub.runtime_session = session
stub.history_runs = [row(OLD_RUN, yday, "stopped")]
stub.run_events = {OLD_RUN: [
    ev(OLD_RUN, 3700, "run.started", iso(yday), {"command_id": "m0ld0001", "command_type": "start_cycle", "data_mode": "synthetic", "plan_only": False, "holding": None}),
    ev(OLD_RUN, 3701, "run.finished", iso(yday + timedelta(seconds=20)), {"outcome": "stopped", "exit_status": 5, "predicates": {}, "automatic_retries": 0,
       "task_control_interventions": 0, "evidence_complete": True, "evidence_gaps": 0, "reason_code": "base_not_zero_before_segment", "phase": "GRASP_CLOSE",
       "failure": {"detail": "base velocity not confirmed zero before an arm segment", "measurements": [], "observation_ids": []}}, "base_not_zero_before_segment")]}
stub.mission_state_answer = mission("mission-0ld000000001", yday, OLD_RUN, "FAILED", "base_not_zero_before_segment", "GRASP_CLOSE", ["return_arm", "start"])
if START == "ready":
    status = S.robot_status_object()
    permission = status["motion_permission"]
    permission.update(permitted=True, reason_code=None, detail=None, confirmable=False, confirmed_session_uuid=permission["current_session_uuid"],
                      confirmed_host_instance=permission["current_host_instance"], confirmed_wall_utc=iso(now - timedelta(minutes=9)))
    stub.robot_status_answer = status


def walk():
    time.sleep(T1)
    ts = datetime.now(timezone.utc)
    run = "console-" + ts.strftime("%Y%m%dT%H%M%SZ") + "-mlay0ut1"

    def t(s):
        return iso(ts + timedelta(seconds=s))

    def E(seq, s, kind, payload, reason=None):
        return ev(run, seq, kind, t(s), payload, reason, source=session)

    def st(seq, s, frm, to):
        return E(seq, s, "state.transition", {"machine": "cycle", "from_state": frm, "to_state": to})

    def pred(seq, s, name, value, measured=None, threshold=None, unit=None):
        return E(seq, s, "predicate.evaluated", {"predicate": name, "value": value, "state": "evaluated", "threshold": threshold, "measured": measured,
                                                 "unit": unit, "evidence": [], "consumed_source_stamp": None, "measurements": [], "rule_ref": None, "rule_digest": None})

    def snap(active, cursor):
        def answer():
            s = S.snapshot_object(data_mode="synthetic", run_active=active, seq_cursor=cursor)
            s["runtime_session"] = session
            if active:
                s["active_run"]["run_id"] = run
                s["owner"]["run_id"] = run
            return s
        return answer

    stub.snapshot = snap(True, 46)
    running = mission("mission-l4y0ut000001", ts, run, "RUNNING", None, "SEARCH", ["stop"], attempts=False)
    stub.mission_state_answer = running
    first = [E(41, 0, "run.started", {"command_id": run[-8:], "command_type": "start_cycle", "data_mode": "synthetic", "plan_only": False, "holding": None}),
             st(42, 1, None, "SEARCH"), st(43, 3, "SEARCH", "TARGET_SELECTED"), st(44, 5, "STRADDLING", "GRASP_CLOSE"),
             st(45, 7, "GRASP_CLOSE", "OBJECT_SECURED"), pred(46, 7, "object_secured", True, 0.86, 0.5)]
    stub.run_events[run] = list(first)
    for e in first:
        stub.emit(e)
    print("stage: running", run, flush=True)

    time.sleep(max(0.0, T2 - T1))
    meas = [{"name": "unclaimed_placed_count", "value": 1, "unit": None}, {"name": "earlier_targets_undisturbed", "value": False, "unit": None},
            {"name": "predicted_worst_case_centre_margin_mm", "value": 40.5, "unit": "mm"}]
    second = [pred(47, 20, "release_margin", True, 40.5, 37.96, "mm"), st(48, 21, "RELEASE_ADMISSION", "RELEASED"), st(49, 25, "RELEASED", "PLACED"),
              pred(50, 25, "placed", False, None, 17.96, "mm"), st(51, 27, "BASE_MEASURED", "BASE_RETURNED"), pred(52, 27, "base_returned", True, 3.1, 10, "mm"),
              E(53, 28, "run.finished", {"outcome": "failed", "exit_status": 5, "predicates": {"object_secured": True, "placed": False, "clearance_target_met": False,
                                         "base_returned": True}, "automatic_retries": 0, "task_control_interventions": 0, "evidence_complete": True, "evidence_gaps": 0,
                                         "reason_code": CODE, "phase": "BASE_RETURNED",
                                         "failure": {"detail": f"PLACED not demonstrated for this mission target ({CODE}): exactly one placed cube that no earlier "
                                                               "target claims is needed, with every earlier target still where it was placed",
                                                     "measurements": meas, "observation_ids": []}}, CODE)]
    stub.run_events[run] += second
    finished = row(run, ts, "failed", seconds=28)
    stub.history_runs = [row(OLD_RUN, yday, "stopped"), finished]
    ended = mission("mission-l4y0ut000001", ts, run, "FAILED", CODE, "BASE_RETURNED", ["return_arm", "start"])
    ended["targets"][0]["attempts"][0].update(measurements=meas, t_wall_utc=t(29))
    for e in second:
        stub.emit(e)
    stub.snapshot = snap(False, 53)
    stub.mission_state_answer = ended
    print("stage: ended", flush=True)


threading.Thread(target=walk, daemon=True).start()
print(f"stage: idle, session {session}, on 127.0.0.1:{port}", flush=True)
web.run_app(S.make_app(stub, web_build=build), host="127.0.0.1", port=port, print=None)
