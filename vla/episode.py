# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""vla.episode: one learned pick-and-place on the arm, chunk-then-pause (operator ruling R-VLA-2: inference on the
robot CPU, about 12 s per chunk measured 2026-10-08).

  1. move to the start pose (the demonstrations' first pose): rise joints first with the pan held, then the pan,
     cosine-eased at 20 deg/s, 30 Hz; refuse when any arm joint is still more than 8 deg away (the recorder's rule);
  2. per chunk: ask the runtime whether the arm is still free (io.still_allowed), capture, convert to the training
     view, ask the policy in a worker thread while the arm holds its last command; then read the arm AGAIN (it caught up
     with its last command during the pause) and send the chunk's actions through the gate at 30 Hz from that reading;
  3. at the first chunk boundary with the jaws latched, the grip is measured the runtime's way, in its two halves. First
     policy_grasp._close: the gripper readback, once the jaws settled, must say they stalled on something
     (io.closed_on_something, the runtime's closed_on_something). An EMPTY close is said ("grip_empty"), the jaws are
     opened until they read open, and the gate takes no new latch until the policy itself asks open: the policy may grasp
     again within the stage time bound (operator ruling 2026-10-09: on the robot that day the retention score alone passed
     an empty close at 0.883 and the run reported done). Then OBJECT_SECURED (policy_grasp._secure): three straight-up lifts solved and gated by the runtime (io.lift_step: +15, +28, +8 mm), the arm held still before
     each frame, the frames after the upper two scored with the runtime's retention_score (each one kept, io.keep). Below
     retention_min the episode is REFUSED with the runtime's own code retention_not_demonstrated and, as the runtime
     does, the arm holds with the jaws closed (an imagined grasp never reaches the pad; a held block is never dropped where
     the arm happens to be). A lift the runtime refuses ends the episode the same way (vla_grip_check_lift_refused).
     Measured on the robot 2026-10-08: the first two frames after the latch, near the floor, scored 0.468 and 0.465 on a
     HELD block; the runtime itself records that first pair as the weak one (0.50..0.63) and scores the upper two;
  4. a RELEASE counts only once the grip is CONFIRMED (object_secured). The policy reopening the jaws before that is a
     regrasp: said ("reopened"), the latched frames forgotten, the episode goes on. The recorded takes contain NO release
     (every one ends with the block held at the pad; the recorder's return opened the jaws, unrecorded), so the policy
     holds the block at the pad: RELEASE BY RULE when, at a chunk boundary, the grip is confirmed, the arm is inside
     end_region (where every take ended, vla/envelope.json) and it moved less than SETTLE_CHUNK_DEG since the previous
     boundary. At rest there WITHOUT a confirmed grip is refused (vla_grip_not_verified), the block held. Either release
     holds GRIPPER_OPEN until the jaws READ open (or RELEASE_WAIT_S) before the arm returns to the start pose: DONE.
The gripper is only ever GRIPPER_LATCH or GRIPPER_OPEN and the wrist roll only the recorded value, on every command:
the moves and the holds carry the arm joints and the gate's current jaw value, never a reading. The operator's stop
is read every tick and during every pause: the arm holds its present joints with the jaws as they were commanded and
nothing else is sent. Every other end is REFUSED with a code."""
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from vla.frame import training_frame
from vla.gates import GateRefusal

KEYS = ("arm_shoulder_pan.pos", "arm_shoulder_lift.pos", "arm_elbow_flex.pos", "arm_wrist_flex.pos", "arm_wrist_roll.pos", "arm_gripper.pos")
RISE = (1, 2, 3)  # lift, elbow, wrist flex move first with the pan held; roll and jaws are fixed values, never eased
PAN = (0,)
GRIPPER = 5
SPEED_DEG_S, MIN_MOVE_S, SETTLE_DEG, SETTLE_S, START_REFUSE_DEG = 20.0, 0.6, 3.0, 2.0, 8.0
SETTLE_CHUNK_DEG = 3.0  # an arm that moved less than this over a whole chunk has stopped
OPEN_TOLERANCE_DEG = 3.0  # jaws within this of GRIPPER_OPEN read open (run_single_cube_cycle GRIPPER_OPEN_TOLERANCE)
RELEASE_WAIT_S = 1.5  # the longest the arm waits at the release pose for the jaws to read open
VERIFY_LIFTS_M = (0.015, 0.028, 0.008)  # policy_grasp._secure: VERIFICATION, CLEARANCE and CONFIRM lifts, scored between the upper two
STILL_DEG, STILL_READ_S, STILL_MAX_S = 0.3, 0.1, 1.5  # a frame for the grip check waits until two readings this far apart agree
RELEASE_LOWER_M, RELEASE_LOWER_STEPS = 0.008, 5  # the rule's release sets the block down: straight-down steps before the jaws open
LATCH_SETTLE_S = 0.75  # the jaws are read this long after the latch at the earliest (run_single_cube_cycle GRIPPER_HOLD_S)


class _End(Exception):
    def __init__(self, result, code=None, message=""):
        super().__init__(message)
        self.result, self.code, self.message = result, code, message


def _always_allowed():
    return True, ""


@dataclass
class Io:
    read_state: Callable
    capture: Callable
    send: Callable
    infer: Callable
    fk: Callable
    retention: Callable
    emit: Callable
    stopped: Callable
    monotonic: Callable = field(default=time.monotonic)
    sleep: Callable = field(default=time.sleep)
    still_allowed: Callable = field(default=_always_allowed)  # () -> (allowed, detail): is the arm still free of the runtime
    lift_step: Callable = None  # (arm[4] measured, dz_m) -> arm[4]: the runtime's straight-up step; raises with its reason when refused
    keep: Callable = field(default=lambda name, frame: None)  # (name, raw frame): every frame the grip check scores, for diagnosis
    closed_on_something: Callable = None  # (gripper reading after the latch) -> bool: the runtime's own held-or-empty rule


class Episode:
    def __init__(self, io: Io, gate, start_pose, retention_min: float, max_s: float = 600.0, infer_timeout_s: float = 45.0,
                 horizon: int = 50, fps: int = 30, end_region=None):
        self.io, self.gate, self.start = io, gate, [float(v) for v in start_pose]
        self.retention_min, self.max_s, self.infer_timeout_s = retention_min, max_s, infer_timeout_s
        self.horizon, self.dt = horizon, 1.0 / fps
        self.end_region = end_region  # (min[4], max[4]) of pan, lift, elbow, wrist flex, or None
        self.roll = float(gate.env["wrist_roll_deg"])
        self.grip = gate.open_value  # the jaw value every move and hold carries: GRIPPER_LATCH or GRIPPER_OPEN, nothing else
        self.t0 = 0.0
        self.last_cmd = None

    # -- helpers ---------------------------------------------------------------------------------------------------------
    def _t(self):
        return round(self.io.monotonic() - self.t0, 2)

    def _emit(self, phase, **kw):
        self.io.emit({"t": self._t(), "phase": phase, **kw})

    def _check(self):
        if self.io.stopped():
            raise _End("stopped", None, "stopped by the operator")
        if self.io.monotonic() - self.t0 > self.max_s:
            raise _End("refused", "vla_time_bound", f"the stage time bound ({self.max_s:.0f} s) passed")

    def _send_arm(self, arm):
        """One command: the four arm joints given, the recorded wrist roll, the commanded jaw value."""
        cmd = dict(zip(KEYS, (float(arm[0]), float(arm[1]), float(arm[2]), float(arm[3]), self.roll, float(self.grip))))
        self.io.send(cmd)
        self.last_cmd = cmd

    def _hold_present(self):
        self._send_arm(self.io.read_state()[:4])

    def _tool_z(self, s):
        return float(self.io.fk({"shoulder_pan": s[0], "shoulder_lift": s[1], "elbow_flex": s[2], "wrist_flex": s[3], "wrist_roll": 0.0})[2, 3])

    def _move_to(self, target, check=True):
        current = [float(v) for v in self.io.read_state()[:4]]
        for phase in (RISE, PAN):
            start = list(current)
            travel = max(abs(target[i] - start[i]) for i in phase)
            steps = max(1, math.ceil(max(travel / SPEED_DEG_S, MIN_MOVE_S) / self.dt))
            for k in range(1, steps + 1):
                if check:
                    self._check()
                s = 0.5 - 0.5 * math.cos(math.pi * k / steps)
                pose = list(current)
                for i in phase:
                    pose[i] = start[i] + s * (target[i] - start[i])
                self._send_arm(pose)
                self.io.sleep(self.dt)
            for i in phase:
                current[i] = target[i]
        deadline = self.io.monotonic() + SETTLE_S
        while True:
            if check:
                self._check()
            residual = [a - b for a, b in zip(self.io.read_state()[:4], target[:4])]
            if all(abs(r) <= SETTLE_DEG for r in residual) or self.io.monotonic() > deadline:
                return residual
            self.io.sleep(0.05)

    def _at_rest_in_end_region(self, state, previous) -> bool:
        if self.end_region is None or previous is None:
            return False
        lo, hi = self.end_region
        inside = all(lo[i] <= state[i] <= hi[i] for i in range(4))
        return inside and max(abs(state[i] - previous[i]) for i in range(4)) < SETTLE_CHUNK_DEG

    def _open_jaws(self):
        """GRIPPER_OPEN where the arm is, held until the jaws READ open (or RELEASE_WAIT_S). Returns the last reading."""
        self.grip = self.gate.open_value
        deadline = self.io.monotonic() + RELEASE_WAIT_S
        while True:
            reading = self.io.read_state()
            self._send_arm(reading[:4])
            if reading[GRIPPER] >= self.gate.open_value - OPEN_TOLERANCE_DEG or self.io.monotonic() > deadline:
                return reading
            self.io.sleep(0.05)

    def _set_down(self):
        """Before the RULE opens the jaws, set the block down: the tool goes straight down in RELEASE_LOWER_M steps (the
        runtime's own step, holding) until the runtime's gate refuses one (its floor) or RELEASE_LOWER_STEPS were taken.
        2026-10-12: twice the jaws opened where the policy came to rest above the pad's near edge; the block dropped,
        bounced off the edge and rolled back to the floor. The recorded takes end resting on the pad, so the policy itself
        never lowers further."""
        taken = 0
        for _ in range(RELEASE_LOWER_STEPS):
            self._check()
            arm = [float(v) for v in self.io.read_state()[:4]]
            try:
                target = self.io.lift_step(arm, -RELEASE_LOWER_M)
            except Exception as exc:  # noqa: BLE001 -- the runtime's gate refused the step: the block is as low as it may go
                self._emit("set_down", steps=taken, stopped_by=f"{type(exc).__name__}: {exc}"[:120])
                return
            self._move_to(target)
            taken += 1
        self._emit("set_down", steps=taken, stopped_by=None)

    def _release(self, by: str, out: dict):
        """Open the jaws where the arm is and hold them open until they READ open (or RELEASE_WAIT_S), so the block is
        let go here and not carried by the return. The rule's release sets the block down first (_set_down)."""
        if by == "rule" and self.io.lift_step is not None:
            self._set_down()
        keep = getattr(self.io, "keep", None)
        if keep is not None:
            try:
                keep("release", self.io.capture())  # what the jaws held, or did not, the moment they opened
            except Exception:  # noqa: BLE001 -- a missing frame never stops a release
                pass
        reading = self._open_jaws()
        out["released"], out["release_by"] = True, by
        self._emit("released", by=by, jaw_deg=round(float(reading[GRIPPER]), 1))

    def _settled_jaws(self) -> float:
        """The gripper readback after the latch, as the runtime reads it: the last command held LATCH_SETTLE_S, then until
        the arm and the jaws are still (_wait_still)."""
        end = self.io.monotonic() + LATCH_SETTLE_S
        while self.io.monotonic() < end:
            self._check()
            if self.last_cmd is not None:
                self.io.send(self.last_cmd)
            self.io.sleep(self.dt)
        return float(self._wait_still()[GRIPPER])

    def _wait_still(self):
        """Hold the last command until two readings STILL_READ_S apart agree within STILL_DEG on every joint (the runtime
        takes its retention frames with the arm settled at each lift), or STILL_MAX_S passed. Returns the last reading."""
        deadline = self.io.monotonic() + STILL_MAX_S
        before = [float(v) for v in self.io.read_state()]
        while True:
            self._check()
            if self.last_cmd is not None:
                self.io.send(self.last_cmd)
            self.io.sleep(STILL_READ_S)
            now = [float(v) for v in self.io.read_state()]
            if max(abs(a - b) for a, b in zip(now, before)) <= STILL_DEG or self.io.monotonic() > deadline:
                return now
            before = now

    def _verify_grip(self):
        """OBJECT_SECURED the runtime's way, from where the policy left the arm: three straight-up lifts solved and gated by
        the runtime, each held still; the frames after the upper two are kept and scored. The jaws stay latched. Returns
        (score, tool heights of the scored frames in mm)."""
        frames, heights = [], []
        for k, dz in enumerate(VERIFY_LIFTS_M, 1):
            try:
                target = self.io.lift_step([float(v) for v in self.io.read_state()[:4]], dz)
            except Exception as exc:  # the runtime's refusal, said with its reason
                raise _End("refused", "vla_grip_check_lift_refused", f"the runtime refused grip-check lift {k} (+{dz * 1000:.0f} mm): {exc}; the block stays held") from exc
            self._move_to([float(v) for v in target[:4]])
            state = self._wait_still()
            if k >= 2:
                frame = self.io.capture()
                self.io.keep(f"grip-{k}", frame)
                frames.append(frame)
                heights.append(round(self._tool_z(state) * 1000.0, 1))
        return float(self.io.retention(frames[0], frames[1])), heights

    def _infer(self, frame, state):
        box = {}

        def work():
            try:
                box["chunk"] = self.io.infer(frame, state)
            except Exception as exc:  # reported, never swallowed
                box["error"] = exc

        started = self.io.monotonic()
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        while worker.is_alive():
            self._check()
            if self.io.monotonic() - started > self.infer_timeout_s:
                raise _End("refused", "vla_inference_timeout", f"no chunk within {self.infer_timeout_s:.0f} s")
            if self.last_cmd is not None:
                self.io.send(self.last_cmd)  # the arm holds its last command; the host sees a live client
            worker.join(0.1)
            self.io.sleep(0.0)
        if "error" in box:
            raise _End("refused", "vla_inference_failed", f"the policy raised {type(box['error']).__name__}: {box['error']}")
        chunk = np.asarray(box["chunk"], dtype=float)
        if chunk.ndim != 2 or chunk.shape[1] != 6 or len(chunk) == 0:
            raise _End("refused", "vla_chunk_malformed", f"the policy returned shape {chunk.shape}")
        return chunk, (self.io.monotonic() - started) * 1000.0

    # -- a pose move, no policy ------------------------------------------------------------------------------------------
    def go_to(self, target, name: str) -> dict:
        """Move the arm to a named pose (the mission's start, the VLA's start) with the jaws open and the roll at the
        recorded value, the same eased rise-then-pan move as the episode's; the operator's stop holds it where it is."""
        self.t0 = self.io.monotonic()
        self.grip = self.gate.open_value
        out = {"result": None, "code": None, "message": "", "pose": name}
        try:
            self._emit("moving_to_pose", pose=name)
            residual = self._move_to([float(v) for v in target[:4]])
            if any(abs(r) > START_REFUSE_DEG for r in residual):
                raise _End("refused", "vla_pose_not_reached", f"the arm did not reach the {name} pose (residual {[round(r, 1) for r in residual]})")
            out["result"] = "done"
            self._emit("at_pose", pose=name, residual_deg=[round(r, 1) for r in residual])
        except _End as end:
            self._hold_present()
            out.update(result=end.result, code=end.code, message=end.message)
            self._emit(end.result, code=end.code, message=end.message)
        out["seconds"] = self._t()
        return out

    # -- the episode -----------------------------------------------------------------------------------------------------
    def run(self) -> dict:
        self.t0 = self.io.monotonic()
        out = {"result": None, "code": None, "message": "", "object_secured": None, "retention_score": None, "released": False,
               "release_by": None, "chunks": 0}
        try:
            self._emit("moving_to_start")
            residual = self._move_to(self.start[:4])
            if any(abs(r) > START_REFUSE_DEG for r in residual):
                raise _End("refused", "vla_start_pose_not_reached", f"the arm did not reach the start pose (residual {[round(r, 1) for r in residual]})")
            self._emit("at_start", residual_deg=[round(r, 1) for r in residual])
            was_latched = False
            previous = None  # the arm reading at the previous chunk boundary
            while not out["released"]:
                self._check()
                allowed, detail = self.io.still_allowed()
                if not allowed:
                    raise _End("refused", "vla_runtime_busy", f"the runtime took the arm: {detail}")
                raw = self.io.capture()
                state = [float(v) for v in self.io.read_state()]
                if self.gate.latched and out["object_secured"] is None and self.io.lift_step is not None:
                    jaws = None
                    if self.io.closed_on_something is not None:
                        jaws = self._settled_jaws()
                        if not self.io.closed_on_something(jaws):
                            self._emit("grip_empty", gripper=round(jaws, 3))
                            self.gate.force_open()
                            self._open_jaws()
                            was_latched = False
                            previous = [float(v) for v in self.io.read_state()]
                            continue  # the policy goes on with the jaws open; a new latch waits for it to ask open
                    score, heights = self._verify_grip()
                    out["object_secured"], out["retention_score"] = score >= self.retention_min, round(score, 3)
                    self._emit("object_secured", value=out["object_secured"], score=round(score, 3), tool_z_mm=heights, gripper=None if jaws is None else round(jaws, 3))
                    if not out["object_secured"]:
                        raise _End("refused", "retention_not_demonstrated", f"retention NOT demonstrated (score {score:.2f} < {self.retention_min}): "
                                   "the image between the jaws changed with height; the arm holds with the jaws closed")
                    previous = [float(v) for v in self.io.read_state()]
                    continue  # the next chunk is asked from a fresh frame where the lifts left the arm
                # THE GRIP IS WATCHED AT EVERY CHUNK BOUNDARY AFTER IT WAS CONFIRMED (2026-10-12: the block was firmly held through the
                # lifts, slipped out during the carry and was found at its start spot after a reported placement). A jaw reading
                # that no longer says closed-on-something means the block left: said, the jaws reopened, and the policy may grasp again.
                if self.gate.latched and out["object_secured"] is True and self.io.closed_on_something is not None and not self.io.closed_on_something(state[GRIPPER]):
                    self._emit("grip_lost", gripper=round(state[GRIPPER], 3))
                    out["object_secured"], out["retention_score"] = None, None
                    self.gate.force_open()
                    self._open_jaws()
                    was_latched = False
                    previous = [float(v) for v in self.io.read_state()]
                    continue
                if self.gate.latched and self._at_rest_in_end_region(state, previous):
                    if out["object_secured"] is not True:
                        raise _End("refused", "vla_grip_not_verified", "the arm stopped at the pad holding the jaws closed, but the grip was never measured: the block stays held")
                    self._release("rule", out)
                    break
                previous = state
                out["chunks"] += 1
                self._emit("thinking", chunk=out["chunks"])
                chunk, ms = self._infer(training_frame(raw), state)
                self._emit("moving", chunk=out["chunks"], inference_ms=round(ms))
                self.gate.begin_chunk(self.io.read_state())  # where the arm IS after the pause, not where it was before it
                for action in chunk[: self.horizon]:
                    self._check()
                    cmd = self.gate.step(action)
                    self.grip = cmd["arm_gripper.pos"]
                    self._send_arm([cmd[k] for k in KEYS[:4]])
                    if self.gate.latched and not was_latched:
                        self._emit("latched")
                    elif was_latched and not self.gate.latched:
                        if out["object_secured"] is True:
                            was_latched = False
                            self._release("policy", out)
                            break
                        self._emit("reopened")  # a regrasp: the grip is measured after the next latch
                    was_latched = self.gate.latched
                    self.io.sleep(self.dt)
            self._emit("returning")
            self._move_to(self.start[:4])
            out["result"] = "done"
            self._emit("done")
        except GateRefusal as refusal:
            self._hold_present()
            out.update(result="refused", code=refusal.code, message=str(refusal))
            self._emit("refused", code=refusal.code, message=str(refusal))
        except _End as end:
            if end.result == "stopped":
                self._hold_present()
                out.update(result="stopped", message=end.message)
                self._emit("stopped")
            else:
                self._hold_present()  # the jaws as commanded: a refused grip keeps them closed, as the runtime's does
                out.update(result="refused", code=end.code, message=end.message)
                self._emit("refused", code=end.code, message=end.message)
        out["floor_holds"] = getattr(self.gate, "floor_holds", 0)
        out["seconds"] = self._t()
        return out
