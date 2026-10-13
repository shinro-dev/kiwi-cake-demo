#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""vla.runner: the robot-side process of the console's VLA beat. It runs ON THE ROBOT, beside (never inside) the
installed runtime, the Cake resident and the supervised host, none of which it changes (operator, 2026-10-08).

STANDBY until `swap`: then it loads the checkpoint (its sha256 must be vla/envelope.json's) and warms it up, READY.
`run` starts one episode, refused unless the runtime is idle (its own snapshot: no active run, a free owner, no mission
running) and unless the last inference has finished computing; the runtime is asked again at every chunk, and a run
nobody polls the status of for DEADMAN_S (the console polls every second) is stopped, the arm holding:
a LeKiwi client to the supervised host on 127.0.0.1 (built exactly as the runtime's controller builds it; base 0 on
every command) and the camera service's local client, both opened for the episode and closed after it. `stop` ends
the episode at the next tick or hold-loop pass. Before the swap, `carry` runs mission 1's scripted carry (vla/carry.py)
once the mission ended holding the block, and says which mission it finished. Control: ZMQ REP on 127.0.0.1:5593 only.

  PYTHONPATH=~/vla-dev/app:~/vla-dev/lerobot-src HF_HOME=~/vla-dev/hf HF_HUB_OFFLINE=1 \
    ~/vla-dev/venv/bin/python -m vla.runner --checkpoint DIR --runtime-release DIR [--threads 3] [--num-steps N]
"""
import argparse
import base64
import contextlib
import json
import sys
import threading
import time
from collections import deque
from pathlib import Path

from vla.carry import base_command, carry as carry_sequence
from vla.envelope import ENVELOPE
from vla.episode import KEYS, START_REFUSE_DEG, Episode, Io
from vla.gates import ActionGate

CONTROL = "tcp://127.0.0.1:5593"
# Where the scripted mission starts. The mission refuses to start its pregrasp from the VLA start pose (2026-10-08: wrist
# flex ceiling), so the arm is brought here before a mission and to the VLA start pose (vla/envelope.json start_pose_deg)
# by the swap. Since 2026-10-09 the wrist camera points about 2 deg of elbow lower at equal readings: elbow 33.45 gives
# the view of 2026-10-08 (pad edge row 162 vs 165 of 360), and the missions that picked their block on 2026-10-09
# (mission-848917da2f46, elbow 33.4) and 2026-10-11 (mission-31e2813f6d21, elbow 33.45) started here; at elbow 35.65 SEARCH
# could not fit the pad. The servos stop 1-2 deg short of a goal, so the arm goes MISSION_APPROACH_DEG past it first and
# then onto it (the move both of those starts were set with).
MISSION_START_POSE_DEG = (-9.4, -79.341, 33.5, 67.3)
MISSION_APPROACH_DEG = (0.0, 0.0, -3.5, -1.0)
DEADMAN_S = 90.0  # a running episode with no status poll for this long is stopped: the forward or the console is gone (90 s: a console tab Chrome throttles in the background still polls about once a minute; 6 s cut a run on 2026-10-12)
# The runtime's snapshot takes over a second under a console page-load burst (measured 2026-10-11: 1.14 s; its owner()
# scans /proc for external controllers), and a 2 s single read timed out on three panel presses out of five. A read gets
# this long, and one timed-out read is tried again before the arm is called busy; the console's forward waits for both.
RUNTIME_READ_TIMEOUT_S = 4.0
RUNTIME_READ_ATTEMPTS = 2


class _NoPolicy:
    """The policy slot of a pose move: nothing is ever inferred while the arm only moves to a named pose."""
    sha256 = ""

    def chunk(self, frame, state):
        raise RuntimeError("a pose move runs no policy")


class _GuardedPolicy:
    """The policy behind one lock: a stopped episode's inference worker keeps computing until it returns, and while it
    does no other inference may start on the same policy object (or on the robot's four cores)."""

    def __init__(self, policy, lock):
        self.policy, self.lock, self.sha256 = policy, lock, policy.sha256

    def chunk(self, frame, state):
        with self.lock:
            return self.policy.chunk(frame, state)


class Runner:
    def __init__(self, policy_factory, io_factory, envelope, runtime, runtime_idle, deadman_s: float = DEADMAN_S,
                 carry_link_factory=None, runtime_mission_id=lambda: None, carry_sleep=time.sleep, carry_clock=time.monotonic):
        self.policy_factory, self.io_factory, self.env, self.rt, self.runtime_idle = policy_factory, io_factory, envelope, runtime, runtime_idle
        self.carry_link_factory, self.runtime_mission_id = carry_link_factory, runtime_mission_id
        self.carry_sleep, self.carry_clock = carry_sleep, carry_clock  # the carry's pulses are measured over this clock
        self.deadman_s, self._last_status = deadman_s, time.monotonic()
        self._infer_lock = threading.Lock()
        self.state, self.error, self.policy, self.warm_ms, self.carried = "standby", None, None, None, None
        self.events, self.outcome, self.preview = deque(maxlen=40), None, None
        self._stop, self._thread, self._lock = threading.Event(), None, threading.Lock()

    def status(self) -> dict:
        self._last_status = time.monotonic()
        with self._lock:
            return {"state": self.state, "model_sha256": self.env["model_sha256"], "warm_inference_ms": self.warm_ms,
                    "events": list(self.events), "outcome": self.outcome, "preview_jpeg_b64": self.preview, "error": self.error,
                    "carry": self.carried}

    def _emit(self, event):
        with self._lock:
            self.events.append(event)
        print(json.dumps(event), flush=True)

    def swap(self) -> dict:
        with self._lock:
            if self.state == "moving":  # a carry or a pose move holds the arm: its end would overwrite the swap's state
                return {"accepted": False, "reason": "moving"}
            if self.state in ("loading", "ready", "running"):
                return {"accepted": False, "reason": "loading" if self.state == "loading" else "already_loaded"}
            self.state, self.error = "loading", None
        threading.Thread(target=self._load, daemon=True).start()
        return {"accepted": True}

    def _load(self):
        try:
            policy = self.policy_factory()
            if policy.sha256 != self.env["model_sha256"]:
                raise RuntimeError(f"vla_model_sha_mismatch: checkpoint {policy.sha256[:16]} is not the envelope's {self.env['model_sha256'][:16]}")
            warm = policy.warm_up()
            with self._lock:
                self.policy, self.warm_ms = policy, warm
        except Exception as exc:
            with self._lock:
                self.state, self.error = "error", str(exc)
            return
        # THE SWAP BRINGS THE ARM TO THE VLA START POSE, when the runtime leaves it free; otherwise it says it did not.
        idle, detail = self.runtime_idle()
        if idle:
            with self._lock:
                self.state = "moving"
            self._go(self.env["start_pose_deg"], "vla")
        else:
            self._emit({"t": None, "phase": "pose_skipped", "pose": "vla", "message": f"the arm was not moved: {detail}"})
        with self._lock:
            self.state = "ready"

    def _go(self, target, name):
        """One pose move on the robot (a fresh client for it, closed after); a failure is said, never raised."""
        try:
            with self.io_factory(_NoPolicy(), self._emit, self._stop.is_set, self._set_preview) as io:
                gate = ActionGate(self.env, self.rt["fk"], gripper_latch=self.rt["latch"], gripper_open=self.rt["open"])
                return Episode(io, gate, self.env["start_pose_deg"], retention_min=self.rt["retention_min"]).go_to(target, name)
        except Exception as exc:
            self._emit({"t": None, "phase": "refused", "code": "vla_pose_failed", "message": f"{type(exc).__name__}: {exc}"})
            return None

    def to_mission_pose(self) -> dict:
        """Bring the arm to where the scripted mission starts (MISSION_START_POSE_DEG): refused unless the runner is idle
        (standby or ready, no inference computing) and the runtime leaves the arm free."""
        with self._lock:
            if self.state not in ("standby", "ready"):
                return {"accepted": False, "reason": self.state}
            previous = self.state
        if self._infer_lock.locked():
            return {"accepted": False, "reason": "vla_still_thinking"}
        idle, detail = self.runtime_idle()
        if not idle:
            return {"accepted": False, "reason": "vla_runtime_busy", "detail": detail}
        with self._lock:
            self.state = "moving"
        self._stop.clear()

        def move():
            self._to_mission()
            with self._lock:
                self.state = previous
        threading.Thread(target=move, daemon=True).start()
        return {"accepted": True}

    def _to_mission(self):
        """Past the mission start pose, then onto it; the second move only when the first finished (a stop holds the arm)."""
        approach = tuple(round(a + d, 3) for a, d in zip(MISSION_START_POSE_DEG, MISSION_APPROACH_DEG))
        first = self._go(approach, "mission")
        if first is not None and first.get("result") == "done":
            self._go(MISSION_START_POSE_DEG, "mission")

    def carry(self) -> dict:
        """Mission 1's scripted carry, BEFORE the swap: refused once the policy is loaded or loading, while the arm moves,
        and unless the runtime leaves the arm free (the operator stopped the mission that holds the block). The carry
        itself refuses before any motion unless the jaws hold something and the arm is at the mission's start pose."""
        with self._lock:
            if self.state != "standby":
                return {"accepted": False, "reason": "swapped" if self.state == "ready" else self.state}
        if self.carry_link_factory is None or self.rt.get("held") is None:
            return {"accepted": False, "reason": "carry_unavailable"}
        idle, detail = self.runtime_idle()
        if not idle:
            return {"accepted": False, "reason": "vla_runtime_busy", "detail": detail}
        mission_id = self.runtime_mission_id()
        with self._lock:
            self.state, self.carried = "moving", None
        self._stop.clear()
        threading.Thread(target=self._carry, args=(mission_id,), daemon=True).start()
        return {"accepted": True}

    def _carry(self, mission_id):
        self._emit({"t": None, "phase": "moving_to_pose", "pose": "pad"})
        try:
            with self.carry_link_factory() as link:
                outcome = carry_sequence(link, latch=self.rt["latch"], open_=self.rt["open"], held=self.rt["held"],
                                         start_ref=MISSION_START_POSE_DEG, tolerance_deg=START_REFUSE_DEG, stopped=self._stop.is_set,
                                         emit=self._emit, sleep=self.carry_sleep, monotonic=self.carry_clock)
        except Exception as exc:
            outcome = {"result": "refused", "code": "carry_failed", "message": f"{type(exc).__name__}: {exc}"}
            self._emit({"t": None, "phase": "carry_refused", "code": outcome["code"], "message": outcome["message"]})
        with self._lock:
            self.carried, self.state = {**outcome, "mission_id": mission_id}, "standby"

    def unswap(self) -> dict:
        """Swap back: the policy is dropped and, when the runtime leaves the arm free, the arm goes back to where the
        scripted mission starts."""
        with self._lock:
            if self.state in ("running", "moving", "loading"):
                return {"accepted": False, "reason": self.state}
        idle, _detail = self.runtime_idle()
        with self._lock:
            self.policy, self.warm_ms = None, None
            self.state = "moving" if idle else "standby"
        if idle:
            self._stop.clear()

            def move():
                self._to_mission()
                with self._lock:
                    self.state = "standby"
            threading.Thread(target=move, daemon=True).start()
        return {"accepted": True}

    def run(self) -> dict:
        with self._lock:
            if self.state == "running":
                return {"accepted": False, "reason": "already_running"}
            if self.state != "ready":
                return {"accepted": False, "reason": "not_ready"}
        if self._infer_lock.locked():
            return {"accepted": False, "reason": "vla_still_thinking"}
        idle, detail = self.runtime_idle()
        if not idle:
            return {"accepted": False, "reason": "vla_runtime_busy", "detail": detail}
        with self._lock:
            self.state, self.outcome = "running", None
            self.events.clear()
        self._stop.clear()
        self._last_status = time.monotonic()
        self._thread = threading.Thread(target=self._episode, daemon=True)
        self._thread.start()
        threading.Thread(target=self._deadman, args=(self._thread,), daemon=True).start()
        return {"accepted": True}

    def _deadman(self, episode_thread):
        while episode_thread.is_alive():
            if time.monotonic() - self._last_status > self.deadman_s and not self._stop.is_set():
                self._emit({"t": None, "phase": "deadman", "message": f"no status poll for {self.deadman_s:.0f} s: the console or its forward is gone"})
                self._stop.set()
            time.sleep(0.1)

    def _episode(self):
        try:
            with self.io_factory(_GuardedPolicy(self.policy, self._infer_lock), self._emit, self._stop.is_set, self._set_preview) as io:
                io.still_allowed = self.runtime_idle  # asked again at every chunk: the runtime may take the arm mid-episode
                gate = ActionGate(self.env, self.rt["fk"], gripper_latch=self.rt["latch"], gripper_open=self.rt["open"])
                end_region = (self.env["end_pose_min_deg"], self.env["end_pose_max_deg"]) if "end_pose_min_deg" in self.env else None
                outcome = Episode(io, gate, self.env["start_pose_deg"], retention_min=self.rt["retention_min"], end_region=end_region).run()
        except Exception as exc:
            outcome = {"result": "refused", "code": "vla_runner_failed", "message": f"{type(exc).__name__}: {exc}"}
            self._emit({"t": None, "phase": "refused", "code": outcome["code"], "message": outcome["message"]})
        with self._lock:
            self.outcome, self.state = outcome, "ready"

    def _set_preview(self, jpeg_bytes):
        with self._lock:
            self.preview = base64.b64encode(jpeg_bytes).decode()

    def stop(self) -> dict:
        self._stop.set()
        return {"stopping": self.state == "running"}


def _runtime_idle_check(socket_path, client_factory=None, terminal=None):
    """(idle, detail) from the runtime's own READ surface. IDLE only when the snapshot names no active run AND a free
    owner (not unresolved, not an external controller, not a run of its own), and the mission is either in a terminal
    state or not recorded at all (mission_state answers the view with its state at the top level, or a refusal). Any
    other answer, a refusal, an empty answer or a failure to read counts as busy: the runner never guesses the arm free."""
    def read(client):
        """(snapshot, mission_state) in one go, a timed-out read tried again up to RUNTIME_READ_ATTEMPTS times."""
        for attempt in range(1, RUNTIME_READ_ATTEMPTS + 1):
            try:
                snapshot = client.snapshot()
                return snapshot, (client.mission_state() if isinstance(snapshot, dict) else None)
            except (TimeoutError, OSError):
                if attempt == RUNTIME_READ_ATTEMPTS:
                    raise

    def check():
        try:
            factory = client_factory
            if factory is None:
                from demo_runtime.rpc import RuntimeClient
                factory = lambda path: RuntimeClient(str(path), client_session="vla-runner", timeout_s=RUNTIME_READ_TIMEOUT_S)  # noqa: E731
            ends = terminal
            if ends is None:
                from demo_runtime.mission import MISSION_TERMINAL as ends
            snapshot, mission = read(factory(socket_path))
            if not isinstance(snapshot, dict) or "active_run" not in snapshot:
                return False, "the runtime snapshot could not be read"
            if snapshot["active_run"]:
                return False, "a mission run is active"
            owner = (snapshot.get("owner") or {}).get("kind")
            if owner != "free":
                return False, f"the runtime owner is {owner or 'not stated'}"
            if not isinstance(mission, dict):
                return False, "the mission could not be read"
            state = mission.get("state")
            if state is not None:
                return (True, "") if state in ends else (False, f"the mission is {state}")
            if mission.get("reason_code") == "no_mission_recorded":
                return True, ""
            return False, f"the mission could not be read ({mission.get('reason_code') or 'no state'})"
        except Exception as exc:
            return False, f"the runtime could not be read ({type(exc).__name__})"
    return check


def _runtime_mission_id(socket_path, client_factory=None):
    """The id of the mission the runtime has on record (its own mission_state), or None when there is none or it could not
    be read: the carry names the mission it finished, so the console says "finished" only of that mission."""
    def read():
        try:
            factory = client_factory
            if factory is None:
                from demo_runtime.rpc import RuntimeClient
                factory = lambda path: RuntimeClient(str(path), client_session="vla-runner", timeout_s=2.0)  # noqa: E731
            mission = factory(socket_path).mission_state()
            value = mission.get("mission_id") if isinstance(mission, dict) else None
            return value if isinstance(value, str) and value else None
        except Exception:
            return None
    return read


ARM_JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex")


def _runtime_lift_step(cycle):
    """The grip check's lift: the runtime's own straight-up step (cartesian_step_pose, holding) from the measured joints,
    pan held, accepted only by the runtime's gate, which raises with its reason otherwise."""
    def lift(arm, dz_m):
        pose, _report = cycle.cartesian_step_pose(dict(zip(ARM_JOINTS, (float(v) for v in arm))), 0.0, dz_m, True)
        return [float(pose.joints[k]) for k in ARM_JOINTS]
    return lift


def _runtime_closed_on_something(cycle):
    """The runtime's own held-or-empty rule on the gripper readback after the latch (policy_grasp._close)."""
    def held(gripper_after_latch):
        return bool(cycle.closed_on_something(float(gripper_after_latch)))
    return held


def _robot_io_factory(robot_id: str):
    import cv2
    from action_adapter import BASE_ZERO_FIELDS
    from lerobot.robots.lekiwi import LeKiwiClient, LeKiwiClientConfig
    from run_stage1_scan import ServiceCamera

    import cartesian_lift
    import run_single_cube_cycle as cycle
    from vla.frame import training_frame

    @contextlib.contextmanager
    def factory(policy, emit, stopped, set_preview):
        robot = LeKiwiClient(LeKiwiClientConfig(remote_ip="127.0.0.1", id=robot_id))  # run_single_cube_cycle.py:3297
        robot.connect()
        captures = Path.home() / "vla-dev/captures"
        camera = ServiceCamera(None, captures, local=True)

        def capture():
            result = camera.capture("wrist")
            frame = getattr(result, "frame", None)
            if frame is None:
                raise RuntimeError(f"the camera service gave no wrist frame: {getattr(result, 'record', None)}")
            view = training_frame(frame)
            ok, jpeg = cv2.imencode(".jpg", cv2.cvtColor(cv2.resize(view, (240, 320)), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                set_preview(jpeg.tobytes())
            return frame

        def read_state():
            obs = robot.get_observation()
            return [float(obs[k]) for k in KEYS]

        def send(cmd):
            robot.send_action({**BASE_ZERO_FIELDS, **cmd})

        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

        def keep(name, frame):  # every frame the grip check scores, beside the camera's own captures
            cv2.imwrite(str(captures / f"grip-{stamp}-{name}.jpg"), frame)

        try:
            yield Io(read_state=read_state, capture=capture, send=send, infer=policy.chunk, fk=cartesian_lift.forward_kinematics,
                     retention=cycle.retention_score, emit=emit, stopped=stopped, lift_step=_runtime_lift_step(cycle), keep=keep,
                     closed_on_something=_runtime_closed_on_something(cycle))
        finally:
            with contextlib.suppress(Exception):
                robot.send_action(dict(BASE_ZERO_FIELDS))
            with contextlib.suppress(Exception):
                robot.disconnect()
            close = getattr(camera, "close", None)
            if close:
                with contextlib.suppress(Exception):
                    close()
    return factory


def _carry_link_factory(robot_id: str):
    """The carry's own link to the supervised host: the episode's Io keeps the base at zero on every command; this one sends
    the carry's ruled base pulses (vla.carry.base_command refuses any other velocity) and stops the base on the way out."""
    from action_adapter import BASE_ZERO_FIELDS
    from lerobot.robots.lekiwi import LeKiwiClient, LeKiwiClientConfig

    class Link:
        def __init__(self, robot):
            self.robot = robot

        def read_state(self):
            obs = self.robot.get_observation()
            return [float(obs[k]) for k in KEYS]

        def read_base(self):
            return float(self.robot.get_observation().get("x.vel", 0.0))  # the host's own measured forward velocity

        def send(self, arm, roll, grip, vx):
            self.robot.send_action({**base_command(vx), **dict(zip(KEYS, (*arm, roll, grip)))})

    @contextlib.contextmanager
    def factory():
        robot = LeKiwiClient(LeKiwiClientConfig(remote_ip="127.0.0.1", id=robot_id))
        robot.connect()
        try:
            yield Link(robot)
        finally:
            with contextlib.suppress(Exception):
                robot.send_action(dict(BASE_ZERO_FIELDS))
            with contextlib.suppress(Exception):
                robot.disconnect()
    return factory


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--runtime-release", required=True)
    ap.add_argument("--runtime-socket", default=str(Path.home() / "kc-runtime-data/runtime.sock"))
    ap.add_argument("--robot-id", default="my_awesome_kiwi")
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--num-steps", type=int, default=None)
    a = ap.parse_args()
    release = Path(a.runtime_release).expanduser()
    for sub in ("packages/demo-runtime", "bin/laptop/manipulation", "bin/laptop/perception", "autonomy/manipulation", "autonomy/perception", "autonomy/motion"):
        sys.path.insert(0, str(release / sub))
    import cartesian_lift
    import run_single_cube_cycle as cycle

    from vla.policy import VlaPolicy

    runtime = {"latch": cycle.GRIPPER_LATCH, "open": cycle.GRIPPER_OPEN, "retention_min": cycle.RETENTION_MIN_SCORE, "fk": cartesian_lift.forward_kinematics,
               "held": _runtime_closed_on_something(cycle)}
    runner = Runner(lambda: VlaPolicy(a.checkpoint, device="cpu", checkpoint_only=True, num_steps=a.num_steps, threads=a.threads),
                    _robot_io_factory(a.robot_id), ENVELOPE, runtime, _runtime_idle_check(Path(a.runtime_socket)),
                    carry_link_factory=_carry_link_factory(a.robot_id), runtime_mission_id=_runtime_mission_id(Path(a.runtime_socket)))
    import zmq
    sock = zmq.Context.instance().socket(zmq.REP)
    sock.bind(CONTROL)
    print(json.dumps({"phase": "listening", "control": CONTROL, "state": "standby"}), flush=True)
    ops = {"status": runner.status, "swap": runner.swap, "unswap": runner.unswap, "run": runner.run, "stop": runner.stop,
           "to_mission_pose": runner.to_mission_pose, "carry": runner.carry}
    while True:
        try:
            request = json.loads(sock.recv())
        except ValueError:
            sock.send_json({"error": "not_json"})
            continue
        op = request.get("op") if isinstance(request, dict) else None
        if op == "shutdown":
            runner.stop()
            if runner._thread:
                runner._thread.join(15)
            sock.send_json({"bye": True})
            return 0
        sock.send_json(ops[op]() if op in ops else {"error": "unknown_op"})


if __name__ == "__main__":
    sys.exit(main())
