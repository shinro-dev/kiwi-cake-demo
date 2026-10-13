#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""rehearsal_observation: the SYNTHETIC target observation, for a rehearsal and for nothing else.

WHAT IT IS. The proven forward primitive (run_hybrid_pick_place._make_move_base_sustained) feeds its in-process
MobileApproachController one fixed observation: x 1.0 m, y 0, quality 0.9. The capability relay (capability_pulse.py)
used to publish the same one to the Cake-launched capability. The operator ruled: "The synthetic observation remains
valid only for rehearsal. It must be structurally excluded from LIVE execution." The RELAY'S copy lives here now. The
in-process primitive keeps its OWN copy, in the loop of run_hybrid_pick_place._make_move_base_sustained. A LIVE run never
imports that module: run_single_cube_cycle._run hands make_move_base live_forward_refused as the forward half. A run
started without KC_RUNTIME_MODE (by hand, as every recorded physical run was) still imports it and feeds it that copy.

UNDER CONTRACT V2 (target_observation_source, kc.target_observation/2). The relay's copy is built in the v2 shape of
ObservedTarget: frame "anchor_reach", no quality (v2 carries no graded quality), stationary true (a rehearsal moves
nothing) and a freshness bound of its own. Its x stays the primitive's 1.0 m, which is OUTSIDE the v2 envelope
0..0.120 m (the executor's bounded total advance, target_observation_source.MAX_ADVANCE_MM): a v2 capability drops it
as x_out_of_bounds and publishes no command for it. It is not a remaining advance the executor measured and it is not
made to look like one; that is one more reason it is a rehearsal input and nothing else.

WHAT IT PRODUCED, AND WHAT THAT PROVES. Through mobile_approach (kx 0.6 toward the handoff centre at 0.35 m) the 1.0 m
asks for 0.39 m/s, which the controller clamps to 0.15 and slews up from zero by 0.10 per step; the gateway then clamps
to the pulse's |vx|. Two different paths have carried it, and they must not be confused:
  - the IN-PROCESS primitive ran it on hardware, commanded at 0.05 m/s (physical pass 2026-09-18), and the pad measured
    about twice that rate (run_single_cube_cycle.BASE_RATE_PRIOR_MM_S, "50.8 mm in 0.5 s");
  - the RELAY has published it only inside tests. Its accepted commands reached RecordingTransport, a
    FakeBaseMotionTransport subclass in autonomy/tests/test_capability_pulse.py, and no device.
The nonzero fixture of the handoff's section 4 is a THIRD thing, and neither of the two: x_m 0.95 through ApproachService
inside the test process, whose 0.10 and 0.06 m/s the gateway clamped to 0.05 and 0.05 in
FakeBaseMotionTransport.last_sent. The relay and its synthetic observation were not part of it. All of this is software
behaviour. None of it is a validated hardware speed of the relay or of the capability.

STRUCTURAL EXCLUSION, in two layers:
  - synthetic_source(mode) refuses unless `mode` is exactly "synthetic", the runtime's data-mode word
    (demo_runtime.supervisor.SYNTHETIC, exported to every child as KC_RUNTIME_MODE). It is the same gate
    demo_runtime.mission_rehearsal.recording_execution puts in front of the rehearsal's unconditional confirmations: the
    callable is created after the check, so a refused caller holds nothing it could hand to a relay;
  - no LIVE module imports this file (the controller, the relay, the mission execution and the mission service):
    autonomy/tests/test_rehearsal_observation.py reads their imports and their keyword literals to keep it so.
The LIVE source is autonomy/motion/target_observation_source.py, whose resolve() returns only a remaining advance the
executor computed from a stored stationary capture, and refuses by name otherwise.
"""
from __future__ import annotations

if __package__:
    from .target_observation_source import CONTRACT_FRAME, ObservedTarget
else:
    from target_observation_source import CONTRACT_FRAME, ObservedTarget

#: The one runtime mode a synthetic observation may exist in (demo_runtime.supervisor.SYNTHETIC; a test keeps them equal).
REHEARSAL_MODE = "synthetic"
#: Why a synthetic source was not built. A reason code: it names no mode of a deployment and no value.
REASON_NOT_SYNTHETIC = "synthetic_observation_requires_synthetic_mode"
#: The proven forward primitive's synthetic observation, in metres. Under v2 it is carried in the "anchor_reach" frame
#: word, where 1.0 m lies outside the envelope (see UNDER CONTRACT V2); the primitive's quality 0.9 has no v2 field.
SYNTHETIC_X_M, SYNTHETIC_Y_M = 1.0, 0.0
#: The freshness bound the synthetic observation carries: the 200 ms the v1 capability configured, the bound the relay
#: used to apply to this observation. Each observe() call re-stamps it on the relay's clock, so it is never older than
#: one relay period when checked. No perception deadline exists for it (nothing is perceived), so none is derived.
SYNTHETIC_MAX_AGE_NS = 200_000_000
#: The observation id every synthetic observation carries, so that no record could take one for a stored capture.
SYNTHETIC_OBSERVATION_ID = "rehearsal-synthetic"


class SyntheticObservationRefused(RuntimeError):
    """A synthetic observation was asked for by something that is not a synthetic runtime."""


def synthetic_source(mode: str, *, target_id: str, clock, motion_epoch):
    """observe() -> ObservedTarget, the synthetic observation of `target_id`, for a SYNTHETIC runtime; otherwise
    SyntheticObservationRefused and nothing at all.

    Every call is a new observation stamped on `clock` (the relay's own clock, so that each publication is bound to a
    fresh stamp exactly as the relay used to stamp it) and carrying `motion_epoch()` (the relay's own epoch reader, so
    that it always passes the relay's epoch check: a rehearsal moves nothing). It is stationary by the same token, and
    its bound is SYNTHETIC_MAX_AGE_NS. No evidence field is filled: there is no stored frame, run or perception request
    behind it."""
    if mode != REHEARSAL_MODE:
        raise SyntheticObservationRefused(REASON_NOT_SYNTHETIC)
    if not callable(motion_epoch) or not hasattr(clock, "now_ns"):
        raise ValueError("a synthetic source needs the relay's clock and its motion epoch reader")

    def observe() -> ObservedTarget:
        return ObservedTarget(target_id=target_id, x_m=SYNTHETIC_X_M, y_m=SYNTHETIC_Y_M, frame=CONTRACT_FRAME,
                              capture_monotonic_ns=clock.now_ns(), motion_epoch=motion_epoch(), max_age_ns=SYNTHETIC_MAX_AGE_NS,
                              stationary=True, observation_id=SYNTHETIC_OBSERVATION_ID)

    return observe
