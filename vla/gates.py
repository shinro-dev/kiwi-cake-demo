# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""vla.gates: every policy action passes here before it reaches the arm (operator ruling R-VLA-3, 2026-10-08).

Order, per tick: refuse a non-finite action; freeze wrist roll; smooth the four arm joints (EMA, alpha) from the
measured pose at the chunk start, so a new chunk never jumps (the 2026-10-08 trials jumped up to 22 deg at chunk
boundaries and stepped up to 2.6 deg per tick: recorded hand tremor plus sampling noise); bound the per-tick change to
max_step_deg; clamp to the recorded envelope; and never go below the recorded floor: a target whose tool height (forward
kinematics) is below it keeps the previous arm target instead (counted in floor_holds; the 2026-10-08 trials pressed the
block 5 to 35 mm below the deepest demonstration at the pad). The
gripper is never a policy value: it is GRIPPER_OPEN until the policy asks below latch_below for debounce_ticks ticks in a
row, then GRIPPER_LATCH, held at least min_latch_ticks, released only after debounce_ticks ticks above open_above (the
trials' jaws flipped up to 15 times on a single 73/76 threshold). After an empty close the episode forces the jaws open
(force_open); no new latch then until the policy itself has asked open for debounce_ticks ticks.
"""
import math

ARM = (0, 1, 2, 3)
WRIST_ROLL, GRIPPER = 4, 5


class GateRefusal(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ActionGate:
    def __init__(self, envelope: dict, fk, gripper_latch: float, gripper_open: float, alpha: float = 0.35,
                 max_step_deg: float = 5.0, latch_below: float = 73.0, open_above: float = 77.0, debounce_ticks: int = 4,
                 min_latch_ticks: int = 30):
        self.env, self.fk = envelope, fk
        self.keys = tuple(f"arm_{j}.pos" for j in envelope["joints"])
        self.lo, self.hi = envelope["joint_min_deg"], envelope["joint_max_deg"]
        self.latch_value, self.open_value = gripper_latch, gripper_open
        self.alpha, self.max_step = alpha, max_step_deg
        self.latch_below, self.open_above = latch_below, open_above
        self.debounce, self.min_latch = debounce_ticks, min_latch_ticks
        self.latched, self.released_after_latch = False, False
        self._armed = True  # a latch may start; False after force_open until the policy asks open
        self.floor_holds = 0
        self._below = self._above = self._latched_for = 0
        self._last = None

    def begin_chunk(self, present_deg) -> None:
        self._last = [float(v) for v in present_deg]

    def force_open(self) -> None:
        """Open the jaws now (the close found nothing between them): no new latch until the policy asks open."""
        self.latched, self._armed, self._below, self._above = False, False, 0, 0

    def _gripper(self, asked: float) -> float:
        if not self.latched and not self._armed:
            self._above = self._above + 1 if asked > self.open_above else 0
            if self._above >= self.debounce:
                self._armed, self._below = True, 0
        elif not self.latched:
            self._below = self._below + 1 if asked < self.latch_below else 0
            if self._below >= self.debounce:
                self.latched, self._latched_for, self._above = True, 0, 0
        else:
            self._latched_for += 1
            self._above = self._above + 1 if asked > self.open_above else 0
            if self._latched_for >= self.min_latch and self._above >= self.debounce:
                self.latched, self.released_after_latch, self._below = False, True, 0
        return self.latch_value if self.latched else self.open_value

    def step(self, raw_deg) -> dict:
        if self._last is None:
            raise GateRefusal("vla_gate_not_anchored", "begin_chunk(present) must run before the first step")
        raw = [float(v) for v in raw_deg]
        if len(raw) != 6 or not all(math.isfinite(v) for v in raw):
            raise GateRefusal("vla_action_not_finite", f"policy action is not six finite numbers: {raw_deg}")
        out = list(self._last)
        for i in ARM:
            target = self._last[i] + self.alpha * (raw[i] - self._last[i])
            target = self._last[i] + max(-self.max_step, min(self.max_step, target - self._last[i]))
            out[i] = max(self.lo[i], min(self.hi[i], target))
        out[WRIST_ROLL] = float(self.env["wrist_roll_deg"])
        out[GRIPPER] = self._gripper(raw[GRIPPER])
        z = float(self.fk({"shoulder_pan": out[0], "shoulder_lift": out[1], "elbow_flex": out[2], "wrist_flex": out[3], "wrist_roll": 0.0})[2, 3])
        if z < self.env["floor_tool_z_m"]:
            self.floor_holds += 1
            for i in ARM:
                out[i] = self._last[i]
        self._last = out
        return dict(zip(self.keys, out))
