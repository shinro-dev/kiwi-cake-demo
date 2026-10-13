#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""mobile_approach: task-bounded visual-servo controller, pure compute, offline.

Reconciled against the repo's existing (committed) conventions rather than
inventing parallel ones:
- input shape matches autonomy/schemas/target_observation.schema.json exactly
  (target_id, timestamp_ns, frame="base", x_m, y_m, quality);
- output shape matches the DesiredBaseMotion contract now frozen at
  autonomy/schemas/desired_base_motion.schema.json (command_id, target_id,
  timestamp_ns, vx_mps, vy_mps, omega_rps, expiry_ns), superseding the
  "conceptual, not frozen" draft in docs/autonomous-demo/mobile-approach-spec.md;
- control law and step-and-observe loop match docs/autonomous-demo/autonomy-motion.md
  ("Start with bounded P control: vx = clamp(Kx*ex), vy = clamp(Ky*ey)");
- internal state names extend the "not frozen" list in mobile-approach-spec.md
  (IDLE, APPROACHING, TARGET_LOST, MANIPULATION_READY, FAILED) with
  OUTSIDE_LOCALIZATION_ENVELOPE, which that draft did not anticipate but this
  task's trusted-envelope requirement needs.

What this module does NOT do (see mobile-approach-spec.md section 2 and
docs/autonomous-demo/stable-motion-gateway-contract.md):
- talk to LeRobot, the hardware host, Cake, or any actuator, directly or
  indirectly -- there is no import of any of those here;
- enforce velocity/acceleration/TTL/e-stop bounds as a SAFETY guarantee --
  the numbers below are advisory clamps for sane commands, not a substitute
  for the stable motion gateway, which enforces the real bounds
  unconditionally and is the only thing this module's output is ever
  connected to;
- persist or own mission_id/target_id selection -- it only reacts to
  whatever TargetObservation it is handed.

CONTRACT V2 (autonomy/schemas/target_observation.v2.schema.json, TargetObservationV2). The v1 shape above carries a
"base" frame that was never related to anything the manipulation pipeline measures, a 200 ms age bound that the
capability process never applied to its own clock, and a graded quality that no perception score maps to. v2
replaces the three with quantities the proven bounded stop-observe-move executor already computes: the frame is
"anchor_reach" (origin the held cube's anchor in the pad frame, x the reach axis), x_m is the executor's own
remaining bounded advance (0..0.120 m), y_m is 0.0 by construction, the age bound travels with each observation
(max_age_ns, on the robot's monotonic clock: capture_ns is read before the capture, published_ns at publication),
and quality has no graded value because the pipeline's validity predicate is binary. ControllerConfig.contract_v2()
is the controller configuration for that contract; its numbers are copies of the executor's constants
(BASE_VX_MPS, BASE_ADVANCE_TOLERANCE_MM, BASE_MAX_TOTAL_ADVANCE_MM in bin/laptop/manipulation/
run_single_cube_cycle.py), pinned equal by autonomy/tests/test_contract_v2_pins.py, because this module stays
standard-library only and must not import the executor. Under v2 the controller does not read the wire: the
service (mobile_approach_service.py) parses TargetObservationV2, applies the freshness check against the
observation's own bound, and hands the controller the projection TargetObservationV2.controller_input(), whose
timestamp is the publication stamp and whose quality is 1.0, so the command's timestamp_ns and expiry_ns are
anchored to the publication, which is what the relay binds on and what keeps successive commands strictly newer
for the gateway.
"""

import math
from dataclasses import dataclass, field
from enum import Enum

#: The v2 contract word. A v1 consumer never sees a document carrying it as a v1 observation (fail closed).
CONTRACT_V2 = "kc.target_observation/2"
#: The v2 frame: origin the held cube's anchor in the pad frame, x the unit reach axis, y its left normal, metres.
FRAME_ANCHOR_REACH = "anchor_reach"
#: The proven executor's pulse speed (BASE_VX_MPS): the one speed every v2 command is saturated at.
V2_VX_MPS = 0.05
#: The proven executor's advance tolerance (BASE_ADVANCE_TOLERANCE_MM / 1000): inside it the executor never pulses.
V2_ADVANCE_TOLERANCE_M = 0.008
#: The proven executor's total advance bound (BASE_MAX_TOTAL_ADVANCE_MM / 1000): the v2 x_m range.
V2_MAX_ADVANCE_M = 0.120
#: v2 has no graded quality: the pipeline's validity predicate is binary, and an observation that fails it is never
#: published. The controller is handed this value and the v2 configuration asks for no more than 0.0.
V2_QUALITY_NONE = 1.0


class ControllerState(str, Enum):
    IDLE = "IDLE"
    APPROACHING = "APPROACHING"
    TARGET_LOST = "TARGET_LOST"
    OUTSIDE_LOCALIZATION_ENVELOPE = "OUTSIDE_LOCALIZATION_ENVELOPE"
    MANIPULATION_READY = "MANIPULATION_READY"
    FAILED = "FAILED"


@dataclass(frozen=True)
class TargetObservation:
    """Matches autonomy/schemas/target_observation.schema.json exactly."""
    target_id: str
    timestamp_ns: int
    x_m: float
    y_m: float
    quality: float
    frame: str = "base"


@dataclass(frozen=True)
class TargetObservationV2:
    """Matches autonomy/schemas/target_observation.v2.schema.json exactly (the eleven fields, nothing else).

    Reading it is the service's job (strictly, every field); this record only holds what was read. Two stamps on
    ONE clock, the robot's monotonic one: capture_ns was read before the capture was asked for and is the
    observation's identity and age; published_ns is the relay's now at this publication, which the answering
    command must echo as its timestamp_ns and anchor its expiry to.
    """
    target_id: str
    capture_ns: int
    published_ns: int
    motion_epoch: int
    x_m: float
    max_age_ns: int
    observation_id: str
    contract: str = CONTRACT_V2
    frame: str = FRAME_ANCHOR_REACH
    y_m: float = 0.0
    stationary: bool = True

    def controller_input(self) -> "TargetObservation":
        """The projection the controller steps on: the publication stamp as the timestamp (so the command it
        answers with is anchored to the publication, not to the capture), the v2 frame word, and quality 1.0
        because no graded quality exists. The freshness of the capture has been judged by the service against
        this observation's own max_age_ns before this is built; the controller's 200 ms check then sees age 0."""
        return TargetObservation(
            target_id=self.target_id,
            timestamp_ns=self.published_ns,
            x_m=self.x_m,
            y_m=self.y_m,
            quality=V2_QUALITY_NONE,
            frame=self.frame,
        )


@dataclass(frozen=True)
class DesiredBaseMotion:
    """Matches autonomy/schemas/desired_base_motion.schema.json exactly."""
    command_id: str
    target_id: str | None
    timestamp_ns: int
    vx_mps: float
    vy_mps: float
    omega_rps: float
    expiry_ns: int


@dataclass(frozen=True)
class LocalizationEnvelope:
    """v1 defaults: the trusted body_floor_mapping_v0 region frozen for this session:
    x approx 0.30-1.10m, |y| <= 0.40m (see .local-dev/localization/evaluation-summary.json
    for the accuracy evidence this is based on -- NOT a calibrated camera model).
    Under contract v2 the same box holds the executor's bounded advance instead:
    x 0.0..0.120 m along the reach axis, y exactly 0.0 (ControllerConfig.contract_v2())."""
    x_min_m: float = 0.30
    x_max_m: float = 1.10
    y_abs_max_m: float = 0.40

    def contains(self, x_m, y_m):
        return self.x_min_m <= x_m <= self.x_max_m and abs(y_m) <= self.y_abs_max_m


@dataclass(frozen=True)
class HandoffRegion:
    """v1 defaults: placeholder manipulation-ready region, deliberately narrower than the
    full trusted envelope and deliberately not claiming real arm-reach
    validation -- that is the separate, not-yet-run manipulation success
    grid in docs/autonomous-demo/measurement-experiment-spec.md. This box
    only says 'the controller may stop driving and report readiness here';
    it is not a substitute for the eventual ManipulationReadiness assessment
    in docs/autonomous-demo/perception-localization.md.
    Under contract v2 it is the executor's own advance tolerance, x 0.0..0.008 m
    and y exactly 0.0: the executor never pulses there, so MANIPULATION_READY
    means zero (ControllerConfig.contract_v2())."""
    x_min_m: float = 0.30
    x_max_m: float = 0.40
    y_abs_max_m: float = 0.12

    def contains(self, x_m, y_m):
        return self.x_min_m <= x_m <= self.x_max_m and abs(y_m) <= self.y_abs_max_m

    @property
    def center_x_m(self):
        return (self.x_min_m + self.x_max_m) / 2.0


@dataclass(frozen=True)
class ControllerConfig:
    # Bounded proportional gains -- placeholder starting values, not tuned;
    # see docs/autonomous-demo/autonomy-motion.md's Kx*ex / Ky*ey note.
    kx: float = 0.6
    ky: float = 0.6
    vx_max_mps: float = 0.15
    vy_max_mps: float = 0.15
    omega_max_rps: float = 0.0  # v0: omega is not used at all (autonomy-motion.md: "Add yaw only if experiments require it")
    deadband_m: float = 0.02
    max_slew_mps_per_step: float = 0.10  # maximum per-command change; reset to 0 baseline on target_id change
    max_observation_age_ns: int = 200_000_000  # 200ms; camera runs ~30fps so this is a few frames of slack
    quality_min: float = 0.5
    command_ttl_ns: int = 250_000_000  # matches command_ttl_ms: 250 in autonomy/config/demo.example.yaml
    envelope: LocalizationEnvelope = field(default_factory=LocalizationEnvelope)
    handoff: HandoffRegion = field(default_factory=HandoffRegion)

    def __post_init__(self):
        if self.vx_max_mps <= 0 or self.vy_max_mps <= 0:
            raise ValueError("vx_max_mps and vy_max_mps must be positive")
        if self.kx <= 0 or self.ky <= 0:
            raise ValueError("kx and ky must be positive")
        if self.deadband_m < 0:
            raise ValueError("deadband_m must be non-negative")
        if self.command_ttl_ns <= 0:
            raise ValueError("command_ttl_ns must be positive")

    @classmethod
    def contract_v2(cls) -> "ControllerConfig":
        """The configuration for contract v2, every number a copy of the proven executor's constant (pinned equal
        by autonomy/tests/test_contract_v2_pins.py; this module must not import the executor).

        - vx_max_mps = BASE_VX_MPS: the one pulse speed the executor ran on hardware; vy_max_mps the same bound,
          never reached because y is 0.0 by construction; omega unused as in v1.
        - kx = vx_max / (tolerance / 2) = 0.05 / 0.004 = 12.5, so vx saturates at the pulse speed for EVERY
          remaining advance beyond the executor's tolerance: the executor bounds the duration and measures the
          travel, the controller only says "advance, at the proven speed".
        - deadband_m = 0.0: the executor's tolerance IS the handoff, there is no second dead zone.
        - envelope x 0.0..0.120 m (BASE_MAX_TOTAL_ADVANCE_MM), |y| <= 0.0; handoff x 0.0..0.008 m
          (BASE_ADVANCE_TOLERANCE_MM), |y| <= 0.0: MANIPULATION_READY means zero, the executor never pulses there.
        - max_slew_mps_per_step 0.10, unchanged and >= vx_max, so the first command is already full speed.
        - quality_min 0.0: no graded quality exists (the pipeline's validity predicate is binary and an
          observation that fails it is never published).
        - command_ttl_ns 250 ms, unchanged; max_observation_age_ns 200 ms, unchanged and never reached under v2:
          the service judges the capture's age against the observation's own max_age_ns and steps the controller
          with the publication stamp as both the observation time and now (see TargetObservationV2.controller_input).
        """
        return cls(
            kx=V2_VX_MPS / (V2_ADVANCE_TOLERANCE_M / 2.0),
            ky=V2_VX_MPS / (V2_ADVANCE_TOLERANCE_M / 2.0),
            vx_max_mps=V2_VX_MPS,
            vy_max_mps=V2_VX_MPS,
            omega_max_rps=0.0,
            deadband_m=0.0,
            max_slew_mps_per_step=0.10,
            max_observation_age_ns=200_000_000,
            quality_min=0.0,
            command_ttl_ns=250_000_000,
            envelope=LocalizationEnvelope(x_min_m=0.0, x_max_m=V2_MAX_ADVANCE_M, y_abs_max_m=0.0),
            handoff=HandoffRegion(x_min_m=0.0, x_max_m=V2_ADVANCE_TOLERANCE_M, y_abs_max_m=0.0),
        )


def _clamp(value, limit):
    return max(-limit, min(limit, value))


def _finite(*values):
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


class MobileApproachController:
    """One step() call = one observe -> compute -> (gateway applies for a
    bounded interval) -> stop -> reacquire cycle. No internal timers, no
    background threads, no continuous trajectory: the caller drives the loop
    by calling step() again with the next observation, matching
    docs/autonomous-demo/autonomy-motion.md's step-and-observe sequence."""

    def __init__(self, config=None):
        self.config = config or ControllerConfig()
        self.state = ControllerState.IDLE
        self._last_target_id = None
        self._last_vx_mps = 0.0
        self._last_vy_mps = 0.0
        self._seq = 0

    def _zero_command(self, timestamp_ns, target_id=None):
        self._seq += 1
        return DesiredBaseMotion(
            command_id=f"cmd_{self._seq}",
            target_id=target_id,
            timestamp_ns=timestamp_ns,
            vx_mps=0.0,
            vy_mps=0.0,
            omega_rps=0.0,
            expiry_ns=timestamp_ns + self.config.command_ttl_ns,
        )

    def _slew_limit(self, target_id, vx, vy):
        if target_id != self._last_target_id:
            # A different target must never inherit the previous target's
            # velocity trajectory -- no accidental continuation.
            self._last_vx_mps = 0.0
            self._last_vy_mps = 0.0
        max_step = self.config.max_slew_mps_per_step
        vx = self._last_vx_mps + _clamp(vx - self._last_vx_mps, max_step)
        vy = self._last_vy_mps + _clamp(vy - self._last_vy_mps, max_step)
        self._last_vx_mps, self._last_vy_mps = vx, vy
        return vx, vy

    def step(self, observation, now_ns):
        """observation: a TargetObservation, or None if no target is currently selected.
        now_ns: the caller's current time (integer nanoseconds) -- not read from
        the system clock here, so replay/synthetic tests are deterministic.

        Returns (DesiredBaseMotion, ControllerState, reason: str).
        """
        if observation is None:
            self.state = ControllerState.IDLE
            self._last_target_id = None
            self._last_vx_mps = self._last_vy_mps = 0.0
            return self._zero_command(now_ns), self.state, "no active target"

        if not _finite(observation.timestamp_ns, observation.x_m, observation.y_m, observation.quality):
            self.state = ControllerState.TARGET_LOST
            return self._zero_command(now_ns, observation.target_id), self.state, "invalid numeric values in observation"

        age_ns = now_ns - observation.timestamp_ns
        if age_ns < 0 or age_ns > self.config.max_observation_age_ns:
            self.state = ControllerState.TARGET_LOST
            return self._zero_command(now_ns, observation.target_id), self.state, f"stale observation (age={age_ns}ns)"

        if observation.quality < self.config.quality_min:
            self.state = ControllerState.TARGET_LOST
            return self._zero_command(now_ns, observation.target_id), self.state, f"quality {observation.quality} below minimum {self.config.quality_min}"

        if not self.config.envelope.contains(observation.x_m, observation.y_m):
            self.state = ControllerState.OUTSIDE_LOCALIZATION_ENVELOPE
            self._last_vx_mps = self._last_vy_mps = 0.0
            return self._zero_command(now_ns, observation.target_id), self.state, "target outside trusted localization envelope"

        if self.config.handoff.contains(observation.x_m, observation.y_m):
            self.state = ControllerState.MANIPULATION_READY
            self._last_target_id = observation.target_id
            self._last_vx_mps = self._last_vy_mps = 0.0
            return self._zero_command(now_ns, observation.target_id), self.state, "target within manipulation handoff region"

        ex = observation.x_m - self.config.handoff.center_x_m
        ey = observation.y_m - 0.0  # handoff region is centered on y=0 (holonomic base, symmetric reach assumed for v0)

        vx = 0.0 if abs(ex) < self.config.deadband_m else _clamp(self.config.kx * ex, self.config.vx_max_mps)
        vy = 0.0 if abs(ey) < self.config.deadband_m else _clamp(self.config.ky * ey, self.config.vy_max_mps)
        vx, vy = self._slew_limit(observation.target_id, vx, vy)

        self.state = ControllerState.APPROACHING
        self._last_target_id = observation.target_id
        self._seq += 1
        return (
            DesiredBaseMotion(
                command_id=f"cmd_{self._seq}",
                target_id=observation.target_id,
                timestamp_ns=now_ns,
                vx_mps=vx,
                vy_mps=vy,
                omega_rps=0.0,
                expiry_ns=now_ns + self.config.command_ttl_ns,
            ),
            self.state,
            f"approaching: ex={ex:.3f}m ey={ey:.3f}m",
        )
