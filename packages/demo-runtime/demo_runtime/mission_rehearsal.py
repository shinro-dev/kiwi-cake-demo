# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.mission_rehearsal: the recording, non-motion execution adapter, and the one gate that hands one out.

WHY THIS IS ITS OWN FILE. A rehearsal adapter answers the state machine's judgment gates UNCONDITIONALLY. That is
right for a rehearsal that commands nothing and it is not a check: `confirm` returns "y" without looking at anything,
which on a hardware-capable path would replace an operator's own staging and supervision confirmation with a constant.
A reader auditing whether such a thing can be reached from a path that could move a robot reads this file, and this
file says: only a SYNTHETIC runtime can obtain one.

THE `performs_motion` FLAG IS A LABEL, NOT ISOLATION. It travels with the adapter so a recorded outcome cannot later be
read as a physical one, and the mission coordinator records it for exactly that reason. It does not stop anybody from
constructing the adapter, which is why the isolation is the mode check below rather than the flag.

WHERE THE GATE IS. It is the first statement of `recording_execution`, which is the ONLY place in this repository's
runtime that constructs an ExecutionAdapter whose `confirm` is a constant. Nothing at module scope builds one, so
there is no already-built object for a caller to pick up, and the callables themselves are closures created after the
check has passed. A live or replay runtime asking for one gets RehearsalNotAvailable and no adapter at all.

WHAT IT RECORDS. Every call the machine makes, in order, into a list the caller is handed alongside the adapter. That
list is a REHEARSAL TRANSCRIPT and nothing else: it is not evidence about hardware, it never reaches a robot, and the
adapter it comes from performs no motion by construction, because none of its callables touches a device, a socket or
a transport.
"""
from __future__ import annotations

from .mission_coordinator import ExecutionAdapter

#: The one runtime mode a rehearsal adapter may exist in. Spelled here, compared here, and nowhere else; it is equal to
#: demo_runtime.daemon.SYNTHETIC and a test keeps the two equal rather than importing the daemon into this module.
REHEARSAL_MODE = "synthetic"

#: What the adapter calls itself wherever it is recorded. It says "rehearsal" in the word a reader sees first.
LABEL = "recording-rehearsal"

#: The reason an adapter was not built. It is a reason CODE: it names no path, no mode of a deployment and no value.
REASON_NOT_SYNTHETIC = "rehearsal_requires_synthetic_mode"

#: What the recorded reads answer with. Fixed values, stated here so nothing in the transcript can be mistaken for
#: a measurement: a rehearsal reads no encoder and no base.
#:
#: THESE ARE THE SHAPES THE TRANSITION LOGIC READS, and they are not the motion path's. A pose is JOINT ANGLES IN
#: DEGREES keyed by cartesian_lift.JOINT_ORDER, plus the gripper the machine's own overrides write. A base reading
#: is keyed x_vel / y_vel / theta_vel, which is what _base_is_stationary indexes, and NOT the vx_mps / vy_mps /
#: omega_rps of a base motion COMMAND. Either one wrong is a KeyError inside the machine, not a wrong answer.
REHEARSAL_POSE = {"shoulder_pan": 0.0, "shoulder_lift": -80.0, "elbow_flex": 35.0,
                  "wrist_flex": 67.0, "wrist_roll": 0.0, "gripper": 0.0}
REHEARSAL_BASE = {"x_vel": 0.0, "y_vel": 0.0, "theta_vel": 0.0}


class RehearsalNotAvailable(RuntimeError):
    """A rehearsal adapter was asked for by something that is not a synthetic runtime."""


def recording_execution(*, mode: str) -> tuple:
    """(adapter, transcript) for a SYNTHETIC runtime, or RehearsalNotAvailable.

    The check is first and there is no second way in: every callable below is created after it, so a caller that is
    refused holds nothing that could be handed to a mission.
    """
    if mode != REHEARSAL_MODE:
        raise RehearsalNotAvailable(REASON_NOT_SYNTHETIC)

    transcript: list = []

    def note(call: str, **fields) -> None:
        transcript.append({"call": call, **fields})

    # The pose this rehearsal pretends to hold. move_arm applies the machine's overrides to it so the next read
    # reflects them, which is what an actuator that worked would look like from inside the transition logic.
    pose = dict(REHEARSAL_POSE)

    def read_pose() -> dict:
        note("read_pose")
        return dict(pose)

    def read_base() -> dict:
        note("read_base")
        return dict(REHEARSAL_BASE)

    def move_arm(target, duration_s, speed) -> dict:
        # RETURNS THE MEASURED POSE, because that is what the machine does with this answer: it becomes ctx.last_pose
        # and is read by the next Cartesian solve. A flag like {"ok": True} is a KeyError one state later.
        #
        # RECORDED, NOT COMMANDED. The requested joint VALUES are not written to the transcript: they are a motion
        # command, and the one rule this whole mission surface keeps is that nothing it persists could be replayed
        # at a robot. Only the joint NAMES are noted, so a reader can see which states moved which joints.
        note("move_arm", duration_s=duration_s, joints=sorted(target) if isinstance(target, dict) else None)
        if isinstance(target, dict):
            pose.update({name: value for name, value in target.items() if name in pose})
        return dict(pose)

    def move_base_sustained(vx, duration_s) -> bool:
        note("move_base_sustained", duration_s=duration_s)
        return True

    def confirm(prompt) -> str:
        # UNCONDITIONAL, AND ONLY REACHABLE HERE. Each of the machine's six judgment gates has its OWN vocabulary and
        # aborts on anything else, so a single "y" is not a stand-in for a person: it is an abort at the first gate.
        # This answers each one with the token that gate accepts, which is what makes a rehearsal reach the end.
        #
        # THIS IS NOT A CHECK. It is the absence of one, and it is why a rehearsal adapter may never be reachable
        # from a hardware-capable path. On hardware these six are answered by the proven controller's MEASURED
        # predicates, never by this.
        text = (prompt or "").strip().upper()
        if "CUBE READY" in text:
            answer = "CUBE READY"
        elif text.startswith("PLACE_APPROACH"):
            answer = "READY"          # no further descent requested
        elif "SUCCESS/FAIL" in text:
            answer = "SUCCESS"
        else:
            answer = "YES"
        # The prompt is not recorded: it is written for a person to read and nobody read it. The ANSWER is, because
        # a reader of the transcript is owed what this adapter asserted on the operator's behalf.
        note("confirm", answered=answer)
        return answer

    adapter = ExecutionAdapter(read_pose=read_pose, read_base=read_base, move_arm=move_arm,
                               move_base_sustained=move_base_sustained, confirm=confirm,
                               performs_motion=False, label=LABEL)
    return adapter, transcript
