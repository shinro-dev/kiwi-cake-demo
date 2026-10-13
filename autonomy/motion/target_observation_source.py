#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""target_observation_source: the ACTUAL observation of the selected target that the mobile_approach capability's contract
v2 asks for (autonomy/schemas/target_observation.v2.schema.json, TargetObservationV2), or a refusal that names every reason
there is none.

THE OPERATOR'S RULING THIS FILE CARRIES: "Feed mobile_approach an actual observation of the selected unresolved target,
with the coordinate frame, units, capture identity and freshness required by its existing contract. [...] Do not replace
missing perception with fixed coordinates, a fixture trajectory or a capability-enabled flag that permits an unrelated
motion path." The synthetic observation the proven forward primitive feeds its controller (x 1.0 m, y 0, quality 0.9)
is a rehearsal input and lives in autonomy/motion/rehearsal_observation.py, which no LIVE module imports.

THE CONTRACT V2, as the schema states it (contract "kc.target_observation/2", eleven fields, nothing else):
  - target_id, the mission target; observation_id, the stored capture this observation came from;
  - capture_ns, the robot's monotonic stamp read BEFORE the capture was asked for (the capture's identity and age), and
    published_ns, the relay's now on the same clock at THIS publication: the capability's answer echoes published_ns as
    its timestamp_ns and anchors its expiry to it, and because the relay publishes with a fresh published_ns every period
    its successive commands are strictly newer for the stable motion gateway (v1's capture-stamped commands were not);
  - motion_epoch, the executor's count of ended motions when the capture was taken, and stationary (const true): the base
    read at rest at the capture (no wheel above one velocity count, base_rest.reads_at_rest). Scene invariance travels in
    these two, not in an age;
  - frame "anchor_reach": origin the held cube's anchor in PAD_FRAME from the latest stationary pad observation, x the
    unit reach (advance) axis in PAD_FRAME (the camera axis until a measured pulse gave the base's own displacement
    axis), y its left normal, metres. x_m is the remaining bounded advance along x for the anchor to reach the placement
    admission (run_single_cube_cycle.base_advance_needed_mm / 1000, the executor's own quantity, 0..0.120); y_m is 0.0
    by construction (the target lies on the axis; lateral deviation is measured and bounded by the executor's pulse
    gates, never steered);
  - max_age_ns, THIS observation's freshness bound, derived here: the desktop perception deadline plus the relay's
    bounded waits before a transport exists (two admin reads and the link wait; max_age_ns_for).
Everything else this module knows (anchor_pad_m, axis_pad, need_mm, landmark_inliers, reprojection_rms_px, frame_sha256,
run_id, perception_request_id) is EVIDENCE the relay records, never on the wire.

WHY EACH FORMER BLOCKER IS NOW DEFINED. The v1 source raised on five blockers; each is answered by a quantity the proven
bounded stop-observe-move executor (run_single_cube_cycle.advance_base) already computes, not by a new measurement:
  1. FRESHNESS. The complete per-frame perception takes seconds to tens of seconds. The v1 bound (200 ms, the capability's
     configuration) was never the executor's rule: the executor pulses only from a STATIONARY base, after a capture that
     no ended motion follows, and re-observes after every pulse. So the bound is the perception budget the run was given
     plus the relay's bounded waits, per observation, and what makes the scene the same is motion_epoch and stationary.
  2. FRAME. No wrist camera pose in a body frame is needed: the pad frame (homographies from the pad's print landmarks,
     PAD_FRAME metres, world-fixed) is taken from the stationary base, the anchor and the reach axis are PAD_FRAME
     quantities the executor derives from it, and "anchor_reach" is defined from those two alone.
  3. QUALITY. There is no graded quality. The frozen pipeline's validity predicate is binary (pad_frame_valid, with its
     inlier and reprojection gates in autonomy/perception/pad_template_localizer.py); an observation that fails it is
     never published (pad_frame_invalid), and the figures behind it travel as evidence.
  4. SEMANTICS. The approach target IS the held cube's placement admission point: the cube is held (D1: the target is
     the cube this run selected and grasped), the base advances along the reach axis until the anchor lies the required
     margin plus the buffer inside the pad (D6: forward only through the Cake-managed capability), and x_m is exactly
     that remaining advance. What "the target" of an approach is, is therefore the executor's own quantity.
  5. SECTION 7 of docs/autonomous-demo/cake-console-handoff-2026-09-23.md: "An unavailable mapping refuses the hardware
     operation. [...] It is never satisfied by an unconditional confirmation or by a weaker check." resolve() refuses on
     every missing requirement and names all of them; capability_observation_available() says a mapping exists, and
     whether THIS run has an observation is decided by resolve, per pulse.

WHAT resolve IS. A pure validation and packaging of numbers the executor computed with its proven functions: the approach
state advance_base keeps (the anchor, the axis, the remaining advance, the pad validity figures of the view they came
from), the latest stamped capture (its id, stamp, epoch and whether the base read at rest), the mission selection, the
caller's clock and the perception budget. Nothing is measured here, no coordinate is substituted, and a missing input is
a named refusal, never a default.

Standard library only. No robot, no socket, no file opened.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

#: The contract word every v2 observation carries; a v1 consumer drops a document carrying it (fail closed).
CONTRACT = "kc.target_observation/2"
#: The v2 frame word, the only frame the capability accepts under v2 (mobile_approach_service.parse_observation_v2).
CONTRACT_FRAME = "anchor_reach"
#: y_m by construction: the target lies on the reach axis (a named constant, so that no LIVE module spells a coordinate).
Y_M_ON_AXIS = 0.0
#: run_single_cube_cycle.BASE_MAX_TOTAL_ADVANCE_MM, spelled here so that this module imports no numpy (a test keeps the two
#: equal): the executor's bounded total advance, and the v2 x_m range.
MAX_ADVANCE_MM = 120.0
#: run_single_cube_cycle's bounds on the relay's waits before a transport exists (one admin read, the link wait), spelled
#: here for max_age_ns_for and pinned equal by autonomy/tests/test_contract_v2_pins.py.
CAPABILITY_ADMIN_TIMEOUT_S = 5.0
CAPABILITY_LINK_TIMEOUT_S = 5.0
#: The eleven wire fields, in the schema's order.
WIRE_FIELDS = ("contract", "target_id", "capture_ns", "published_ns", "motion_epoch", "frame", "x_m", "y_m", "max_age_ns",
               "stationary", "observation_id")

# ---- why there is no observation: reason codes (lower case, the contract's reason_code shape), in their fixed order ------
REASON_NOT_SELECTED = "target_not_selected"                      # no selection of this mission target exists in this run
REASON_NO_OBSERVATION = "observation_missing"                    # no stamped capture exists
REASON_CAPTURE_TIME_UNKNOWN = "capture_time_unknown"             # the capture carries no monotonic stamp
REASON_NOT_APPROACH_VIEW = "capture_not_the_approach_view"       # the approach state's view is not the latest capture
REASON_NOT_STATIONARY = "base_not_stationary_at_capture"         # the base did not read at rest at the capture
REASON_PAD_FRAME_INVALID = "pad_frame_invalid"                   # the approach view failed the pipeline's validity predicate
REASON_APPROACH_NOT_MEASURED = "approach_not_measured"           # no advance state: outside advance_base nothing is approached
REASON_ADVANCE_OUT_OF_BOUNDS = "advance_out_of_bounds"           # the remaining advance is not within 0..MAX_ADVANCE_MM
REASON_PERCEPTION_BUDGET_UNKNOWN = "perception_budget_unknown"   # no perception deadline: a LIVE run always has one
REASON_STALE = "capture_older_than_budget"                       # older than max_age_ns on the caller's clock, or in the future
REASONS = (REASON_NOT_SELECTED, REASON_NO_OBSERVATION, REASON_CAPTURE_TIME_UNKNOWN, REASON_NOT_APPROACH_VIEW, REASON_NOT_STATIONARY,
           REASON_PAD_FRAME_INVALID, REASON_APPROACH_NOT_MEASURED, REASON_ADVANCE_OUT_OF_BOUNDS, REASON_PERCEPTION_BUDGET_UNKNOWN, REASON_STALE)

# ---- why an observation handed to a pulse was refused (check_for_pulse) ---------------------------------------------
PULSE_NOT_AN_OBSERVATION = "observation_not_an_observed_target"
PULSE_TARGET_MISMATCH = "observation_target_mismatch"
PULSE_FRAME_NOT_ANCHOR_REACH = "observation_frame_not_anchor_reach"
PULSE_NOT_STATIONARY = "observation_base_not_stationary"
PULSE_EPOCH_CHANGED = "observation_epoch_changed"
PULSE_STALE = "observation_older_than_its_bound"

_REASON = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
_TARGET_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_NS_PER_S = 1_000_000_000


class ObservationUnavailable(Exception):
    """No actual observation of the selected target exists. `reasons` names EVERY failing requirement, in a fixed order;
    the message is the codes and never a value (a record holds paths)."""

    def __init__(self, reasons):
        reasons = tuple(reasons)
        if not reasons or not all(isinstance(reason, str) and _REASON.fullmatch(reason) for reason in reasons):
            raise ValueError("an unavailable observation names at least one reason code")
        super().__init__(",".join(reasons))
        self.reasons = reasons

    @property
    def reason_code(self) -> str:
        return self.reasons[0]


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _point(value) -> bool:
    return isinstance(value, (tuple, list)) and len(value) == 2 and all(_finite(v) for v in value)


@dataclass(frozen=True)
class ObservedTarget:
    """ONE observation of the selected target in contract v2, with what identifies it and the evidence behind it.

    The wire fields, as wire_v2 puts them on the wire: target_id, x_m, y_m, frame, capture_monotonic_ns (the wire's
    capture_ns), motion_epoch, max_age_ns, stationary, observation_id; published_ns is the relay's, at publication. Then:
      capture_monotonic_ns  the robot's time.monotonic_ns() taken BEFORE the capture was asked for (the conservative
                            age: the frame cannot be older than this reading says), on the clock the relay reads;
      motion_epoch          the controller's count of commanded motions that had ENDED when the capture was taken. An
                            observation is about the scene only while no further motion has ended;
      max_age_ns            this observation's own freshness bound (max_age_ns_for), which check_for_pulse applies;
      stationary            the base read at rest at the capture (no wheel above one velocity count);
      observation_id        the stored observation it came from;
    and the evidence, never on the wire: anchor_pad_m and axis_pad (PAD_FRAME, the origin and x axis of "anchor_reach"),
    need_mm (x_m in millimetres, as the executor computed it), landmark_inliers and reprojection_rms_px (the validity
    figures of the approach view), frame_sha256 (the stored frame's digest), run_id (the run that captured it) and
    perception_request_id (the desktop perception request that answered for it, when perception ran offboard).
    A malformed value is a programming error and raises ValueError: it is never read as "no observation"."""

    target_id: str
    x_m: float
    capture_monotonic_ns: int
    motion_epoch: int
    observation_id: str
    max_age_ns: int
    stationary: bool
    y_m: float = Y_M_ON_AXIS
    frame: str = CONTRACT_FRAME
    anchor_pad_m: Optional[tuple] = None
    axis_pad: Optional[tuple] = None
    need_mm: Optional[float] = None
    landmark_inliers: Optional[int] = None
    reprojection_rms_px: Optional[float] = None
    frame_sha256: Optional[str] = None
    run_id: Optional[str] = None
    perception_request_id: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.target_id, str) or _TARGET_ID.fullmatch(self.target_id) is None:
            raise ValueError("target_id must be a target id")
        if not _finite(self.x_m) or not _finite(self.y_m):
            raise ValueError("x_m and y_m must be finite numbers")
        if not isinstance(self.frame, str):
            raise ValueError("frame must be a string")
        if not _count(self.capture_monotonic_ns) or not _count(self.motion_epoch):
            raise ValueError("capture_monotonic_ns and motion_epoch must be whole numbers, 0 or more")
        if not _count(self.max_age_ns) or self.max_age_ns == 0:
            raise ValueError("max_age_ns must be a whole number of nanoseconds, more than 0")
        if not isinstance(self.stationary, bool):
            raise ValueError("stationary must be a bool")
        if not isinstance(self.observation_id, str) or _ID.fullmatch(self.observation_id) is None:
            raise ValueError("observation_id must be an id")
        for name in ("anchor_pad_m", "axis_pad"):
            value = getattr(self, name)
            if value is not None and not _point(value):
                raise ValueError(f"{name} must be two finite numbers or None")
        if self.need_mm is not None and not _finite(self.need_mm):
            raise ValueError("need_mm must be a finite number or None")
        if self.landmark_inliers is not None and not _count(self.landmark_inliers):
            raise ValueError("landmark_inliers must be a count or None")
        if self.reprojection_rms_px is not None and (not _finite(self.reprojection_rms_px) or self.reprojection_rms_px < 0):
            raise ValueError("reprojection_rms_px must be a finite number, 0 or more, or None")
        if self.frame_sha256 is not None and (not isinstance(self.frame_sha256, str) or _HEX64.fullmatch(self.frame_sha256) is None):
            raise ValueError("frame_sha256 must be 64 lowercase hex characters or None")
        for name in ("run_id", "perception_request_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or _ID.fullmatch(value) is None):
                raise ValueError(f"{name} must be an id or None")

    def wire_v2(self, published_ns: int) -> dict:
        """The TargetObservationV2 document the capability receives: EXACTLY the schema's eleven fields. `published_ns` is
        the relay's monotonic now at this publication, on the capture's clock; the capability's answer is bound to this
        publication by that value (capability_pulse), and the capture's age is judged against max_age_ns by both sides."""
        if not _count(published_ns):
            raise ValueError("published_ns must be a whole number of nanoseconds, 0 or more")
        return {"contract": CONTRACT, "target_id": self.target_id, "capture_ns": self.capture_monotonic_ns, "published_ns": published_ns,
                "motion_epoch": self.motion_epoch, "frame": self.frame, "x_m": float(self.x_m), "y_m": float(self.y_m),
                "max_age_ns": self.max_age_ns, "stationary": self.stationary, "observation_id": self.observation_id}

    def identity(self) -> dict:
        """Ids and numbers only, for evidence: which capture, which run, which request, which motion epoch."""
        return {"observation_id": self.observation_id, "frame_sha256": self.frame_sha256, "run_id": self.run_id,
                "perception_request_id": self.perception_request_id, "capture_monotonic_ns": self.capture_monotonic_ns,
                "motion_epoch": self.motion_epoch}

    def evidence(self) -> dict:
        """The figures behind the wire, for evidence: the frame's origin and axis in PAD_FRAME, the remaining advance in
        millimetres, the validity figures of the view, and the bound and the stationarity the wire carries."""
        return {"anchor_pad_m": None if self.anchor_pad_m is None else [float(v) for v in self.anchor_pad_m],
                "axis_pad": None if self.axis_pad is None else [float(v) for v in self.axis_pad],
                "need_mm": self.need_mm, "landmark_inliers": self.landmark_inliers, "reprojection_rms_px": self.reprojection_rms_px,
                "max_age_ns": self.max_age_ns, "stationary": self.stationary}


def max_age_ns_for(perception_deadline_s: float) -> int:
    """This observation's freshness bound: the perception deadline (the answer can be that late and still be the answer to
    this capture) plus the relay's bounded waits before a transport exists, two admin reads and the link wait
    (capability_pulse, THE STOP), so that a capture that qualified when the pulse started still qualifies when the pulse
    can first command anything. Scene invariance is carried by motion_epoch and stationary, not by this bound."""
    if not _finite(perception_deadline_s) or perception_deadline_s <= 0:
        raise ValueError("the perception deadline must be a positive, finite number of seconds")
    return int(round((float(perception_deadline_s) + 2.0 * CAPABILITY_ADMIN_TIMEOUT_S + CAPABILITY_LINK_TIMEOUT_S) * _NS_PER_S))


def check_for_pulse(observed, *, target_id: str, motion_epoch: int, now_ns: int) -> Optional[str]:
    """None when `observed` may feed a pulse of `target_id` NOW, else the first requirement it fails:
      - it is an ObservedTarget (anything else is not an observation);
      - it names this pulse's target;
      - its frame is the contract's "anchor_reach";
      - the base was stationary at its capture;
      - no commanded motion ended since its capture (its motion_epoch is the controller's current one);
      - its capture is at most its own max_age_ns old on the caller's monotonic clock, and not in the future.
    The relay applies it before its admin reads and again before every publication (capability_pulse.py)."""
    if not isinstance(observed, ObservedTarget):
        return PULSE_NOT_AN_OBSERVATION
    if observed.target_id != target_id:
        return PULSE_TARGET_MISMATCH
    if observed.frame != CONTRACT_FRAME:
        return PULSE_FRAME_NOT_ANCHOR_REACH
    if observed.stationary is not True:
        return PULSE_NOT_STATIONARY
    if observed.motion_epoch != motion_epoch:
        return PULSE_EPOCH_CHANGED
    age_ns = now_ns - observed.capture_monotonic_ns
    if age_ns < 0 or age_ns > observed.max_age_ns:
        return PULSE_STALE
    return None


def resolve(approach, capture, selection, now_ns: int, *, perception_deadline_s, target_id) -> ObservedTarget:
    """The v2 observation of the selected target that the executor's state supports, measured against the contract at
    `now_ns`, or ObservationUnavailable naming EVERY failing requirement in the order of REASONS.

    `approach` is the executor's advance state (run_single_cube_cycle.Cycle._approach: observation_id, anchor_pad_m,
    axis_pad, need_mm, pad_frame_valid, landmark_inliers, reprojection_rms_px), None outside advance_base; `capture` is
    the latest STAMPED capture (Cycle._capture: observation_id, capture_monotonic_ns, motion_epoch, stationary, and the
    optional identity frame_sha256, run_id, perception_request_id); `selection` is the mission's selection of this
    target (checked for presence: the held cube IS the selected one, by ALIGN's one-cube-in-view rule and the grasp);
    `perception_deadline_s` is the desktop perception budget the run was given; `target_id` is the mission target.
    Nothing is measured and nothing is defaulted."""
    reasons = []
    if selection is None or not isinstance(target_id, str) or not target_id:
        reasons.append(REASON_NOT_SELECTED)
    stamp = None
    if not isinstance(capture, dict):
        reasons.append(REASON_NO_OBSERVATION)
    else:
        stamp = capture.get("capture_monotonic_ns")
        if not _count(stamp):
            reasons.append(REASON_CAPTURE_TIME_UNKNOWN)
            stamp = None
        if isinstance(approach, dict) and approach.get("observation_id") != capture.get("observation_id"):
            reasons.append(REASON_NOT_APPROACH_VIEW)
        if capture.get("stationary") is not True:
            reasons.append(REASON_NOT_STATIONARY)
    need = None
    if isinstance(approach, dict):
        if approach.get("pad_frame_valid") is not True:
            reasons.append(REASON_PAD_FRAME_INVALID)
        need = approach.get("need_mm")
        if not _finite(need) or need < 0 or need > MAX_ADVANCE_MM:
            reasons.append(REASON_ADVANCE_OUT_OF_BOUNDS)
    else:
        reasons.append(REASON_APPROACH_NOT_MEASURED)
    max_age_ns = None
    if not _finite(perception_deadline_s) or perception_deadline_s <= 0:
        reasons.append(REASON_PERCEPTION_BUDGET_UNKNOWN)
    else:
        max_age_ns = max_age_ns_for(perception_deadline_s)
        if stamp is not None and not 0 <= now_ns - stamp <= max_age_ns:
            reasons.append(REASON_STALE)
    if reasons:
        raise ObservationUnavailable(reasons)
    return ObservedTarget(target_id=target_id, x_m=float(need) / 1000.0, capture_monotonic_ns=stamp, motion_epoch=capture.get("motion_epoch"),
                          observation_id=capture.get("observation_id"), max_age_ns=max_age_ns, stationary=True,
                          anchor_pad_m=tuple(float(v) for v in approach["anchor_pad_m"]), axis_pad=tuple(float(v) for v in approach["axis_pad"]),
                          need_mm=float(need), landmark_inliers=approach.get("landmark_inliers"), reprojection_rms_px=approach.get("reprojection_rms_px"),
                          frame_sha256=capture.get("frame_sha256"), run_id=capture.get("run_id"), perception_request_id=capture.get("perception_request_id"))


def capability_observation_available() -> tuple:
    """(available, reason): whether ANY actual observation of a target could be handed to the capability.

    (True, None): a mapping from the executor's approach state to TargetObservationV2 exists (resolve). Whether THIS run
    has an observation at a given moment is decided by resolve, per pulse, and refused by name when it does not. The
    mission runtime asks this BEFORE it passes the capability gate or spawns a run for a target that measured
    BASE_ADVANCE (demo_runtime.mission_execution), and the controller asks it at SEARCH before anything is picked up
    (run_single_cube_cycle.py, MISSION MODE); both refuse on False. It is a function rather than a constant so that a
    test can stand in a source that is unavailable and prove the refusal path, and so that there is one answer."""
    return (True, None)
