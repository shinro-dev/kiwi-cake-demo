# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""vla.carry: mission 1's scripted carry (operator ruling 2026-10-08), run by the runner when the operator presses
"Carry the block to the pad" in the VLA panel (operator, 2026-10-11). The scripted mission picks the block and stops at
HOLD_OBSERVE holding it (the pad is too far to measure with the block in view); this carry finishes the beat.

With the block held at the low scan pose: two forward base pulses (BASE_V_MPS for PULSE_S, REST_S at rest), the arm eased
to RELEASE_POSE_DEG (the demonstrations' median release, IK'd 10 cm nearer and 15 mm higher), the jaws opened, the arm
eased back, two backward pulses. It is the sequence of the operator's carry_place.py, which set the block down on the pad
on 2026-10-09 and 2026-10-11, at the same rate (HZ) and with the same timings.

Before any motion it refuses unless the jaws read closed on something (the runtime's own held-or-empty rule) and the arm
is within the tolerance of the mission's start pose. The operator's stop ends it at the next tick: the wheels stopped, the
arm and jaws holding their last command. The base moves only here and only by the ruled pulses (base_command).

EVERY PULSE IS MEASURED. On 2026-10-11 15:50Z the host took the arm commands and the jaws opened, but the forward pulses
moved nothing (cause not established; the same link moved the base minutes later), and the block was set down on the
floor where it was picked. A pulse is now commanded until the base's own velocity reading integrates to PULSE_M, at most
PULSE_CAP_S, and the forward distance is reported; a forward pulse that measured less than STALL_FRACTION of PULSE_M
ends the carry REFUSED with the block still held and the arm at the start pose, before anything is released."""
import math
import time

BASE_V_MPS, PULSE_S, REST_S, PULSES = 0.05, 1.0, 0.8, 2
PULSE_M = BASE_V_MPS * PULSE_S  # what one ruled pulse moves: 0.05 m, now measured on the base's own velocity reading
PULSE_CAP_S = 2.5  # a pulse is commanded at most this long; a base that measured less than PULSE_M by then stalled
STALL_FRACTION = 0.7  # a forward pulse below this fraction of PULSE_M is a stall: the block is not released
RELEASE_POSE_DEG = (-10.73, 45.49, 34.54, -17.44)
LATCH_HOLD_S, MOVE_S, SETTLE_S, OPEN_HOLD_S = 0.5, 4.0, 0.8, 1.5
HZ = 30


def base_command(vx: float) -> dict:
    """The base fields of one carry command: forward or backward at BASE_V_MPS, or stopped; never sideways, never a turn,
    never another speed."""
    if not any(vx == allowed for allowed in (0.0, BASE_V_MPS, -BASE_V_MPS)):
        raise ValueError(f"the carry moves the base only at +-{BASE_V_MPS} m/s or not at all, not {vx}")
    return {"x.vel": float(vx), "y.vel": 0.0, "theta.vel": 0.0}


class _Stopped(Exception):
    pass


def carry(link, *, latch, open_, held, start_ref, tolerance_deg, stopped, emit, sleep=time.sleep, monotonic=time.monotonic) -> dict:
    """link: read_state() -> [pan, lift, elbow, wrist_flex, wrist_roll, gripper]; read_base() -> the measured forward
    velocity (m/s, the host's own x.vel reading); send(arm4, roll, grip, vx).
    Returns {"result": "done" | "refused" | "stopped", "code", "message", "jaws_after_release", "forward_m", "backward_m",
    "seconds"}."""
    t0 = monotonic()
    out = {"result": None, "code": None, "message": "", "jaws_after_release": None, "forward_m": None, "backward_m": None}

    def said(**event):
        emit({"t": round(monotonic() - t0, 2), **event})

    def finish(result, code=None, message=""):
        out.update(result=result, code=code, message=message, seconds=round(monotonic() - t0, 2))
        said(phase=f"carry_{result}", code=code, message=message, forward_m=out["forward_m"], backward_m=out["backward_m"])
        return out

    state = [float(v) for v in link.read_state()]
    start, roll, jaws = state[:4], state[4], state[5]
    if not held(jaws):
        return finish("refused", "carry_nothing_held", f"the jaws read {jaws:.2f}: they hold nothing, so nothing is carried")
    off = [a - b for a, b in zip(start, start_ref)]
    if any(abs(d) > tolerance_deg for d in off):
        return finish("refused", "carry_not_at_low_pose",
                      f"the arm is not at the mission's start pose (off by {[round(d, 1) for d in off]} deg), where the carry starts")
    last = {"arm": list(start), "grip": latch}

    def tick(arm, grip, vx):
        if stopped():
            raise _Stopped
        link.send(arm, roll, grip, vx)
        last.update(arm=list(arm), grip=grip)
        sleep(1 / HZ)

    def hold(arm, grip, secs, vx=0.0):
        for _ in range(max(1, round(secs * HZ))):
            tick(arm, grip, vx)

    def move(a, b, grip, secs):
        n = max(1, round(secs * HZ))
        for i in range(1, n + 1):
            s = 0.5 - 0.5 * math.cos(math.pi * i / n)
            tick([x + s * (y - x) for x, y in zip(a, b)], grip, 0.0)

    def base(vx, grip, direction):
        """The ruled pulses, each commanded until the base measured PULSE_M (at most PULSE_CAP_S), then a rest. Returns
        the measured distance; None as soon as a forward pulse stalled (the wheels are stopped by then)."""
        total = 0.0
        stalled["m"] = None
        for pulse in range(1, PULSES + 1):
            moved, began = 0.0, monotonic()
            last = began
            while True:
                tick(start, grip, vx)
                now = monotonic()
                moved += abs(float(link.read_base())) * (now - last)
                last = now
                if moved >= PULSE_M or now - began >= PULSE_CAP_S:
                    break
            hold(start, grip, REST_S, 0.0)
            total += moved
            said(phase="carry_base", direction=direction, pulse=pulse, measured_m=round(moved, 3), commanded_s=round(now - began, 2))
            if vx > 0 and moved < STALL_FRACTION * PULSE_M:
                stalled["m"] = moved
                return None
        return total

    release = list(RELEASE_POSE_DEG)
    stalled = {"m": None}
    try:
        said(phase="carry_start", jaws=round(jaws, 3))
        hold(start, latch, LATCH_HOLD_S)
        forward = base(BASE_V_MPS, latch, "forward")
        if forward is None:
            return finish("refused", "carry_base_stalled",
                          f"the wheels did not take the forward pulse (the base measured {stalled['m'] or 0.0:.3f} m of the "
                          f"{PULSE_M:.2f} m one pulse moves), so the block was not released: it is still held at the start pose")
        out["forward_m"] = round(forward, 3)
        move(start, release, latch, MOVE_S)
        hold(release, latch, SETTLE_S)
        hold(release, open_, OPEN_HOLD_S)
        out["jaws_after_release"] = round(float(link.read_state()[5]), 3)
        said(phase="carry_released", jaws=out["jaws_after_release"])
        move(release, start, open_, MOVE_S)
        hold(start, open_, SETTLE_S)
        out["backward_m"] = round(base(-BASE_V_MPS, open_, "backward") or 0.0, 3)
    except _Stopped:
        link.send(last["arm"], roll, last["grip"], 0.0)
        return finish("stopped", None, "stopped by the operator: the wheels stopped, the arm holds")
    return finish("done")
