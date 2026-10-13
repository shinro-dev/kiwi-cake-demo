<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Two-cube flagship mission: design only

Not implemented. Not actuated. This document answers the operator's design
questions after `physical-pass-2026-09-18.md`'s `MOBILE_CAPABILITY` boundary
pass, resolved around **one mission identity spanning two target cycles**.

## The claim this mission makes (and does not make)

> Cake can safely change the capabilities available to a running physical
> system while its mission remains alive.

It does **not** claim "fully autonomous general-purpose manipulation."
Deterministic manipulation primitives and the existing operator
physical-confirmation gates are acceptable and expected for this first
production - the capability transition is the entire focal automation, not
perception or planning.

## Target story (required structure)

```
MISSION START
  mission_id = X

  TARGET A
    target_id = cube_a
    requires_mobile_approach = false

    deterministic grasp/place cycle
    does NOT enter MOBILE_READY
    target complete

  TARGET B
    target_id = cube_b
    requires_mobile_approach = true

    deterministic grasp
    clearance
    mobility requirement
    MOBILE_READY
    WAITING_FOR_CAPABILITY
    invalid/unadmitted candidate rejected
    remains safely waiting
    valid candidate admitted/activated
    MOBILE_APPROACH
    placement
    target complete

MISSION DONE
  mission_id = SAME X
```

Both targets are two cycles of the same 15-state machine, under one outer
mission context - not two independent missions and not a redesign of the
states themselves (`PRECHECK` ... `RETREAT`/`DONE`/`ABORTED` keep their
existing meaning). What changes between the two cycles is only which
`TargetSpec` the outer loop hands in, and - for Cube A only - one small,
additive, backward-compatible state-machine parameter described in section 5.

## 1. Target identity: `TargetSpec`

Deterministic metadata, no perception inference, for the first
implementation:

```
TargetSpec(
    id="cube_a",
    requires_mobile_approach=False,
    grasp_pose_deg=CUBE_A_GRASP_POSE_DEG,
)
TargetSpec(
    id="cube_b",
    requires_mobile_approach=True,
    grasp_pose_deg=CUBE_B_GRASP_POSE_DEG,
)
```

`run_hybrid_pick_place(deps, grasp_pose_deg=...)` **already accepts** a
grasp-pose override today - unused by any caller yet. Each target's
`grasp_pose_deg` is passed through the existing parameter; no new pose
concept is needed. `requires_mobile_approach` is new (section 5) and is the
one piece of metadata the state machine itself needs to see, because it
determines whether `MOBILE_READY` is entered at all - everything else
(`id`, and any future perception/placement metadata) is purely an
outer-loop/logging concern, same as mission ID.

## 2. How mission identity persists across Cube A and Cube B

The mission ID is generated **once**, by the outer loop, before either
target cycle starts (`main()` generates it once - today
`interactive_hybrid_pick_place.py` does this per-run; for two targets it is
generated once and reused across both `run_hybrid_pick_place()` calls, not
regenerated per target).

Concretely: wrap `deps.log` in the same `make_mission_aware_log()`-style
closure (already built this session) for **both** target cycles, parameterized
by `mission_id` and each cycle's own `target_id`:

```
def make_target_aware_log(base_log, mission_id, target_id):
    def log(event, **fields):
        base_log(event, mission_id=mission_id, target_id=target_id, **fields)
    return log
```

This proves the same mission identity spans both target cycles the same way
it already proved the same identity spans
`WAITING_FOR_CAPABILITY -> MOBILE_APPROACH` within one cycle - same
mechanism, wider scope. `hybrid_pick_place.py` itself never needs to know
about mission IDs or target IDs at all; this is purely an outer-loop/logging
wrapper around the `deps.log` each cycle already receives, the same
separation the Cake boundary already established (the state machine only
calls `capability_available()`/`wait_for_capability_change()` through
injected deps; it doesn't know what a "mission" spans).

## 3. Minimum new orchestration states

One thin outer loop, reusing `run_hybrid_pick_place()` twice:

- `MISSION_START` - generates `mission_id` once, logs it, logs which target
  runs first.
- `TARGET_A_CYCLE` - `run_hybrid_pick_place(deps_a, grasp_pose_deg=CUBE_A_GRASP_POSE_DEG, requires_mobile_approach=False)`.
  Its `PRECHECK -> ... -> DONE` never visits `MOBILE_READY` (section 5).
- `TARGET_B_CYCLE` - `run_hybrid_pick_place(deps_b, grasp_pose_deg=CUBE_B_GRASP_POSE_DEG, requires_mobile_approach=True)`,
  same process, same `mission_id`, same `CapabilityRegistry` instance
  (section 4). Its `PRECHECK -> ... -> MOBILE_READY -> WAITING_FOR_CAPABILITY -> ... -> DONE`
  is exactly today's machine - this is where the capability boundary fires,
  by construction of the demo staging (Cube B's target is positioned to
  require `mobile_approach`; Cube A's is not - section 6).
- `MISSION_DONE` - the outer loop's own terminal state once both target
  cycles report `DONE`. If either cycle reports `ABORTED`, the mission
  itself ends without attempting the other target - an aborted target is
  not silently skipped in favor of the next one.

`deps_a` and `deps_b` are the same hardware-wiring closures
(`_make_move_arm`, `_make_move_base_sustained`, etc.) built once against the
one connected robot - only their `log` and `capability_available`/
`wait_for_capability_change` differ per target (sections 2, 4).

## 4. `CapabilityRegistry`: same instance, unchanged internals

**Unchanged code, single shared instance.** The outer loop constructs
exactly **one** `CapabilityRegistry`, before `TARGET_A_CYCLE` starts, with
`mobile_approach` starting unavailable (no admitted candidate submitted
yet). That same instance's `has_capability`/`wait_for_change` are passed as
`deps.capability_available`/`deps.wait_for_capability_change` to **both**
target cycles.

- Cube A does not require the capability (`requires_mobile_approach=False`
  means its cycle structurally never calls `capability_available()` at all -
  section 5), so it is unaffected by the capability's state either way.
- Cube B's cycle is what actually drives the registry: reaches
  `MOBILE_READY`, finds `mobile_approach` unavailable, enters
  `WAITING_FOR_CAPABILITY`, observes an invalid/unadmitted candidate
  rejected (remains safely waiting, no motion), then a valid candidate
  admitted/activated, then proceeds to `MOBILE_APPROACH`.
- There is no deactivation path today, and this design doesn't need one
  (per `mobile-approach-spec.md`'s own failure table, deactivation only
  happens on a real capability crash/health failure, out of scope here) - so
  passing the same instance to both cycles is safe by construction, not
  just convenient: nothing Cube A's cycle could do would ever change the
  registry's state, since it never calls into it.

`capability_registry.py` needs **zero code changes** for this design.

## 5. The one necessary state-machine touch (correction from the prior draft)

The prior draft of this document claimed `hybrid_pick_place.py`'s 15-state
machine could be "reused twice, unmodified," leaving whether Cube A's cycle
touches `MOBILE_READY` as an open staging choice. That claim does not survive
contact with the required structure above: `_state_clearance_verify`'s `YES`
branch unconditionally returns `STATE_MOBILE_READY` today - there is no
existing branch point that lets an outer loop skip it, and if Cube A's cycle
entered `MOBILE_READY` while `mobile_approach` is genuinely unavailable (it
is - Cube B hasn't triggered admission yet), it would enter
`WAITING_FOR_CAPABILITY` and block, which contradicts "Cube A: ... target
complete" with no capability wait.

The honest resolution is one small, additive, backward-compatible parameter
on `run_hybrid_pick_place()`:

```
def run_hybrid_pick_place(
    deps: PickPlaceDeps,
    grasp_pose_deg: dict = None,
    requires_mobile_approach: bool = True,   # NEW - default preserves today's exact behavior
    ...
):
```

and a single change to `_state_clearance_verify`'s `YES` branch: return
`STATE_MOBILE_READY` if `requires_mobile_approach` else `STATE_PLACE_APPROACH`
directly (that parameter threads through the state loop the same way
`grasp_pose_deg` already does - no `_Ctx`/`PickPlaceDeps` shape change).

Why this is not a "physical primitive change" in the sense the operator's
preference (section 7) rules out: no IK solver, no arm/base motion primitive,
no gateway, no wrist-roll handling, no gripper value changes. It is a routing
decision inside the orchestration layer only, and it is strictly
backward-compatible - every existing caller (today there is exactly one:
`bin/laptop/manipulation/run_hybrid_pick_place.py`, which never passes this
parameter) keeps its exact current behavior (`MOBILE_READY` always entered,
capability always available by default) with zero code or behavior change.
This is the same backward-compatibility pattern the capability boundary
itself already used (`capability_available` defaulting to "always True").

Flagged explicitly rather than left open, per the operator's request to
resolve the architecture, not just describe two possible interpretations.

## 6. How Cube B intentionally triggers the capability requirement

Not through new code - through **physical setup**, exactly like this
session's `MOBILE_CAPABILITY` pass did: Cube B's known-good grasp pose is
chosen (by the operator, at demo-setup time) far enough from the pad that
`CLEARANCE_LIFT`/`PLACE_APPROACH` alone cannot reach it without a base
reposition - the same "arm reach vs. pad distance" gap this whole
diagnostic already characterized (Trials 8-11 in
`.local-dev/confirmed_grasp_handoff.md`). `MOBILE_READY`'s existing
precondition/capability check does the rest, unchanged. Cube A, by
contrast, is staged close enough that its own `PLACE_APPROACH` reaches the
pad - and, per section 5, its cycle is explicitly configured
(`requires_mobile_approach=False`) to never enter `MOBILE_READY` in the
first place, removing the ambiguity the prior draft left open.

## 7. Whether any physical primitive requires modification

**No.** `cartesian_lift.py`, `canonical_handoff.py`, `action_adapter.py`
(including `freeze_wrist_roll`), `mobile_approach.py`,
`stable_motion_gateway.py`, and `capability_registry.py` are all reused
exactly as proven, with zero code changes. The only new code for a first
two-cube production is: the thin outer loop (section 3), the
`make_target_aware_log` wrapper (section 2), two named pose constants, two
`TargetSpec` values (section 1), and the one additive
`requires_mobile_approach` parameter plus its single-branch use in
`hybrid_pick_place.py` (section 5). This matches the operator's strong
preference stated for this design: no physical primitive changes - the one
code touch is orchestration-layer routing, not a primitive.

## Risks / open questions for whoever implements this next

- The pad's physical position/orientation must stay consistent with
  wherever Cube A's cycle left the arm/wrist-camera framing when Cube B's
  cycle starts `CLEARANCE_LIFT` fresh - not verified by this design, only
  flagged.
- `max_clearance_steps`/`max_place_steps` are still fixed per
  `run_hybrid_pick_place()` call (the known limitation from the single-cube
  pass) - Cube B's cycle inherits the same limitation independently of
  Cube A's.
- If `TARGET_A_CYCLE` aborts, section 3 specifies the mission ends rather
  than silently attempting Cube B - confirm this is the desired operator
  experience (vs. e.g. skipping to Cube B) before implementation.
- `requires_mobile_approach=False`'s single-branch change (section 5) needs
  its own unit test (`_state_clearance_verify` routes to `PLACE_APPROACH`
  directly, `MOBILE_READY`/`WAITING_FOR_CAPABILITY` never entered, capability
  deps never called) before this design is implemented - not written by this
  document, which proposes but does not implement.
