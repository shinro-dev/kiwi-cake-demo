<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# mobile_approach architecture specification

This is a specification, not an implementation. Nothing here is built yet.
It refines `architecture.md`, `autonomy-motion.md`, and
`cake-runtime-contract.md` into one document that separates the Cake control
plane from the robotics data plane, per an explicit correction from review:
the Cake admin socket must not be assumed to carry high-frequency target
observations or velocity commands.

## 1. Plane separation

```text
                    CONTROL PLANE (Cake)
               Cake resident/runtime (desktop)
                       |
          admit / verify / activate / replace
                       |
                       v
           mobile_approach (child process)
                       |
                       | (capability-availability signal only,
                       |  low frequency, read-only introspection)
                       v
              task executive (mission state)


                    DATA PLANE (robotics)
                       |
 target observations -> mobile_approach -> desired base motion
        (perception)                          |
                                               v
                                    stable motion gateway
                                               |
                                               v
                                     LeRobot / ZMQ path
                                               |
                                               v
                                            LeKiwi
```

The two planes cross at exactly one point: Cake's read-only introspection
surface tells the task executive whether the `mobile_approach` slot is
`ACTIVE`. Everything else (observations in, bounded motion out) is data-plane
traffic that never touches Cake.

## 2. Module responsibility

`mobile_approach` does:
- consume fresh target observations;
- compute a bounded desired base motion;
- report its own internal state (see below);
- reject stale or low-quality input by producing no motion, not by producing
  an unbounded one.

`mobile_approach` does not:
- talk to LeRobot, the hardware host, or any actuator directly;
- enforce velocity, acceleration, lease, or e-stop bounds (the stable motion
  gateway owns all of that, unconditionally);
- persist mission state; the executive owns `mission_id` and `target_id`,
  `mobile_approach` only consumes the currently selected target;
- decide when it is allowed to run; Cake's admission/activation decides that.

### Conceptual contracts (not frozen)

The repo already has a committed `TargetObservation` schema
(`autonomy/schemas/target_observation.schema.json`) with fields `target_id`,
`timestamp_ns`, `frame` (fixed to `"base"`), `x_m`, `y_m`, `quality`. The
review draft named a `target_x`/`target_y`/`observation_quality`/
`manipulation_region` shape instead. **This spec defers to the committed
schema as the reconciled baseline** and treats the review draft's field names
as superseded, not as a second source of truth: `mobile_approach` consumes
the same `TargetObservation` the rest of the perception stack already
produces, unchanged. No `mission_id` field belongs on `TargetObservation`
itself; mission identity is carried separately (see Continuity semantics),
matching how `MissionEvent` already separates `mission_id` from per-target
data.

No `DesiredBaseMotion` schema exists yet. Conceptually, and only as a
starting point for the implementer to reconcile against
`autonomy/schemas/` conventions (snake_case, explicit units in field names,
`additionalProperties: false`, a bounded string pattern for any ID field):

```text
DesiredBaseMotion
  command_id       (string, same ID pattern as target_id)
  target_id        (string, the target this command was computed for)
  timestamp_ns      (integer)
  vx_mps, vy_mps, omega_rps   (number, gateway clamps regardless)
  expiry_ns         (integer, absolute; gateway is the enforcer, this is advisory)
```

`mobile_approach`'s own internal state (distinct from the mission executive's
states already enumerated in `autonomy/schemas/mission_event.schema.json`):

```text
IDLE              no active target, or capability just activated
APPROACHING       producing bounded commands toward a target
TARGET_LOST       target observation stale/missing, commanding zero
MANIPULATION_READY  handoff condition met, commands stopped
FAILED            internal fault, commands stopped, visible reason
```

These are not frozen either. Do not implement against them without checking
`autonomy/schemas/` for an equivalent convention first, and without deciding
whether this state belongs in a new schema file there.

## 3. Control plane (Cake concepts only)

Using the actual documented Cake model (`docs/architecture.md`,
`docs/release-internals.md`, `cake-runtime-contract.md`), not an invented
one:

Verified by a read-only inspection of the private Cake source (see
`cake-capability-assessment.md`); the earlier draft's guess that the Plan
model held one fixed slot was wrong and is corrected here.

- A **Plan** is a multi-slot graph: named **slots** (module instances),
  typed bindings, declared resource grants and lifecycle edges. Modules are
  in-process, sealed, signed **capsules** loaded by the **resident**. The
  process-supervisor module is a capsule that spawns and supervises one
  external child process from its immutable configuration (command, working
  directory, environment, restart bound, stop signal, optional post-exit
  safe-stop command). `mobile_approach` is hosted exactly that way: its own
  supervising slot, whose child is the Python capability process. Because
  that module is single-instance per capsule identity, the slot uses a
  second build identity of the same module.
- **The slot set is fixed at start.** Adding or removing a slot at runtime
  is refused, typed, before any effect. So the `mobile_approach` slot is
  **pre-declared** in the start Plan in an inert generation (an inert build
  of the supervising module that accepts the same configuration and spawns
  nothing), which is what "capability absent" is.
- **Admission** is deterministic and happens before activation: signature
  policy against the trusted keys, content identity re-hashed on read,
  descriptor, ELF, ABI-range and profile checks. Today it runs at resident
  start for the capsules and configuration contents the start Plan names;
  a live replacement can only move a slot onto content already admitted and
  held. Runtime admission of a new capsule is the one missing surface
  (`private-cake-requirement.md`, Path B). Under Path A the real
  `mobile_approach` build is pre-admitted at start through a holder slot
  that runs it with an inert configuration.
- **Activation of the capability** is the existing replacement operation
  naming the `mobile_approach` slot and the real build's content identity.
  The runtime previews the affected closure (the slot alone; nothing is
  bound to it), quiesces only that slot (its inert child, if any, is
  stopped), creates the incoming generation, which spawns the real child,
  publishes, and reclaims the retiring generation. Generation and capsule
  identity advance in the slot listing; the LeRobot supervisor slot and its
  child are untouched, which the runtime already measures for another slot.
- **Replacement** of a running `mobile_approach` with a newer build is the
  same operation again; its child restarts. Module memory is not carried
  across generations by design, which is why the capability is stateless.
- **Failure**: a `mobile_approach` child crash is a supervisor child-exit
  event and a restart under the next ordinal, up to the configured bound,
  after which the slot's health reports degraded. It is not a Cake failure.
- **Known-good behavior**: a candidate naming an unadmitted capsule, an
  unheld configuration, a configuration that does not fit the incoming
  module, or a change the engine does not execute is refused on the way
  into the validated state: no member quiesced, no module called, Plan
  digest and epoch unchanged. A candidate that fails inside a module
  callback is torn down without publication and the prior Plan stays
  authoritative. Rollback is a new transaction onto the prior durable Plan.

### Corrupted-update beat, restated against real mechanics

Under Path A (no engine change):

1. `mobile_approach` is at its inert generation (absent) or a known-good
   real generation.
2. A candidate naming a capsule outside the admitted set (a tampered build
   has a different content identity; an unknown build is simply not
   admitted) is presented through the replacement operation.
3. The runtime refuses it before any effect: typed reason, event recorded,
   no quiesce, no module call, generation unchanged.
4. A candidate naming the pre-admitted real build is presented and
   activated normally.
5. Signature and tamper refusals themselves are shown at start, exactly as
   the public demo already shows them (`claims.md` rows 2 and 3): a
   tampered store object refuses the start naming the content identity; an
   unsigned or untrusted build is refused naming the policy.

Under Path B (runtime admission implemented):

1. Same starting state.
2. A tampered or untrusted candidate is presented for **admission**; the
   runtime's own signature/content-identity check refuses it live, typed,
   with the running graph untouched.
3. The valid candidate is admitted live, then activated by the same
   replacement operation.

In both paths the refusal is the runtime's, not the capability's. What is
never claimed: that the child program's bytes are covered by the capsule
signature. They are not, exactly as the public documentation already states
for the LeRobot host; section 9 records the optional hardening.

## 4. Data plane

### Constraint

Both `mobile_approach` and the task executive run on the same desktop
(`architecture.md`'s preferred topology has perception, VLA, the executive,
and Cake-managed `mobile_approach` all on the Ubuntu AMD64 desktop). This is
a same-host IPC problem, not a network problem. The network hop (desktop to
Pi) is already solved: it is the existing LeRobot ZMQ client/host link,
unchanged.

How the capability process comes to exist matters for the data plane only
in two small ways. It is spawned by its supervising capsule with the
command, working directory and environment named in immutable
configuration, so socket paths and the like are configuration, not code.
Its standard output is captured into a bounded log inside the slot's
granted data directory, so the process should keep stdout quiet and treat
that log as diagnostics, never as a data channel.

### Options considered

| Option | Complexity | Latency | Lifecycle coupling | Observability | Failure behavior | Fit for 7-10 days |
| --- | --- | --- | --- | --- | --- | --- |
| Unix-domain-socket ZeroMQ (PUB/SUB for observations, PUB/SUB or REQ nothing for commands) | Low: `pyzmq` is already a transitive dependency via LeRobot, same library the team already runs in this exact stack | Low, well under the demo's ~250 ms command TTL | Loose: either side can restart without crashing the other; socket just goes idle | Easy to tap/log for the evidence UI | Natural fit: no reply within TTL already means "stale," gateway already zeroes on timeout | Good; no new dependency, small amount of new code |
| Shared memory / lock-free ring buffer | Higher: needs custom framing, crash-safety, and version negotiation written from scratch | Lowest | Tight: a crash mid-write can corrupt shared state without careful design | Needs bespoke tooling to inspect | Requires hand-built staleness/ownership detection | Not justified: this loop's data rate and payload size do not need it |
| Cake-specific transport (e.g. piggyback on the admin socket) | Explicitly rejected by review: Cake's introspection surface is not designed or sized for a continuous control loop | N/A | Would tightly couple data-plane liveness to Cake's own resident process | Would conflate lifecycle events with control data in evidence | Failure of Cake's own resident would incorrectly appear to stop motion data too | Rejected |
| HTTP/gRPC loopback | Medium: new dependency, more boilerplate than the payloads justify | Adequate but higher than ZMQ for this payload size | Loose | Good (standard tooling) | Requires hand-rolled TTL semantics on top | Not justified; ZMQ already covers this with less new surface |
| Direct in-process function call (no separate process) | Lowest engineering cost | Lowest | None, because there is no separate process | Trivial | N/A | **Explicitly rejected**: see adversarial review, section 7. This would make Cake decorative, not load-bearing |

### Recommendation

Unix-domain-socket ZeroMQ, one socket for `TargetObservation` publication
(perception/executive to `mobile_approach`) and one for `DesiredBaseMotion`
publication (`mobile_approach` to the stable motion gateway), both using the
existing LeRobot-pinned `pyzmq` dependency rather than a new library. This
keeps the data plane genuinely separate from Cake (Cake never sees a
message), reuses a dependency already in the stack, and matches the
TTL-based staleness handling the stable gateway already specifies
(`command_ttl_ms: 250` in `autonomy/config/demo.example.yaml`).

## 5. Continuity semantics

Per `cake-runtime-contract.md`, these must remain unchanged across a
`mobile_approach` activation/replacement event:

- `mission_id` and `target_id` (owned by the task executive, never by
  `mobile_approach`);
- the LeRobot client/host connection (owned by the stable hardware path,
  untouched by Cake activity on the desktop);
- camera streams (owned by perception, independent process);
- perception and VLA processes (independent of `mobile_approach`'s
  lifecycle);
- the stable motion gateway (never restarted by a `mobile_approach` event).

Allowed to pause briefly: only the flow of new `DesiredBaseMotion` commands
from `mobile_approach` itself, during the admission-to-activation window.
Record this as a **transition duration** metric (resident's own activation
timestamp/ordinal to the first new command observed by the gateway), plus a
**reconnect/restart counter** for the capability slot, both already named as
evidence fields in `cake-runtime-contract.md`. The E2E run recorder in
`measurement-experiment-spec.md` already has a slot for "continuity
counters"; this transition duration belongs there.

## 6. Capability discovery

The task executive must observe `mobile_approach = UNAVAILABLE` and later
`mobile_approach = AVAILABLE` without a mission restart, and the mission
executive's `WAITING_FOR_CAPABILITY -> MOBILE_APPROACH` transition
(`autonomy-motion.md`) must be driven by that observation, not by a demo
script manually restarting the task.

Mechanism, using what the admin socket already answers (no Cake change):

- **Slot listing** reports, per slot, its capsule content identity, its
  configuration identity, its generation and its lifecycle state. The
  executive is configured with the content identity of the inert build and
  of the real build. `mobile_approach` at the inert identity is
  `UNAVAILABLE`; at the real identity, lifecycle `ACTIVE`, is a candidate
  for `AVAILABLE`.
- **Health** reports the supervising slot's own health export: healthy
  while its child runs, degraded at the restart bound, faulted on a spawn
  failure. `AVAILABLE` requires healthy.
- **Status** reports the transaction phase, so the window between the
  replacement request and its completion reads as "activating".
- **Flight records** carry, with sequence numbers, the transaction rows and
  the child start/exit/restart-bound/health events; the executive reads
  them incrementally from its last sequence for the evidence UI and for
  transition-duration measurement. Refusals of a bad candidate appear here
  and in the replacement operation's own typed answer.

All of this is polled over the local admin socket (order of 1 Hz; there is
no push surface, and none is needed). It is a low-frequency, small-payload
control-plane read: it announces availability, it never carries target
observations or motion commands. The first implementation may shell out to
the existing probe tool and parse its key/value lines; a small native client
for the framed protocol is a later refinement, not a requirement.

On `UNAVAILABLE -> AVAILABLE`, and only then, the executive transitions
`WAITING_FOR_CAPABILITY -> MOBILE_APPROACH`, preserving `mission_id` and
`target_id` exactly as `autonomy-motion.md` already specifies. On
`AVAILABLE -> UNAVAILABLE` (health not healthy, or lifecycle not active) it
transitions back to `WAITING_FOR_CAPABILITY`; motion is already zero through
the gateway's lease expiry by then.

## 7. Failure behavior

Every failure that could affect motion resolves to bounded or zero motion
through the stable gateway; the gateway is the single enforcement point, so
no row below depends on `mobile_approach` behaving correctly to stay safe.

| Failure | Detected by | Effect on motion | Mission executive transition |
| --- | --- | --- | --- |
| Corrupted candidate presented | Cake resident admission check | None; nothing new activates | No transition; slot stays at its prior state |
| Failed admission (valid signature, invalid content) | Cake resident | None | No transition |
| Activation failure (admitted but fails to start) | Cake resident child-exit handling | None; slot reports unavailable | Stays in/returns to `WAITING_FOR_CAPABILITY` |
| Capability crash during operation | Cake resident child-exit event | Data-plane socket goes idle; gateway's TTL expiry zeroes motion | `MOBILE_APPROACH -> WAITING_FOR_CAPABILITY` |
| Capability disappears mid-approach | Same as above, from the executive's perspective | Zero via TTL expiry, same path as crash | `MOBILE_APPROACH -> WAITING_FOR_CAPABILITY` |
| Stale perception input | `mobile_approach`'s own freshness check on `TargetObservation.timestamp_ns` | `mobile_approach` stops publishing new commands; gateway TTL zeroes | Stays in `MOBILE_APPROACH`, capability-internal state `TARGET_LOST` |
| Desktop/process failure affecting the gateway itself | Out of scope for `mobile_approach`; this is the existing LeRobot host/hardware failure path already documented in `docs/architecture.md` and `SAFETY.md` | Governed by existing LeRobot reconnect/torque behavior, unchanged by this work | Unchanged by this work |
| Target lost during the `WAITING_FOR_CAPABILITY -> MOBILE_APPROACH` transition | Task executive, comparing `target_id` before/after | Motion stays zero until a fresh target is reselected | `MOBILE_APPROACH -> SELECT_TARGET` (or `SEARCH_CUBE`, per `autonomy-motion.md`'s existing failure table) |

## 8. Out of scope (per review)

Not part of this design and not to be added without a separate explicit
decision: SLAM, ROS, a generic plugin framework, a new RPC framework (ZMQ
already covers the identified need), high-frequency traffic through Cake's
admin/control interface, VLA-driven wheel control, Shinro Studio
integration, and any real hardware code. This document specifies, it does
not implement.

## 9. Adversarial self-review

- **Is Cake genuinely responsible for something important here?** Yes, if
  and only if `mobile_approach` is admitted, activated, and replaced as a
  real, separately signed OS process through the resident's existing
  admission machinery. If it is instead loaded via `importlib`/dynamic
  Python import inside an already-running process, Cake is decorative: the
  corrupted-update beat would be proving Python's own import error handling,
  not Cake's admission check. This spec's control-plane section (4) and data
  plane's explicit rejection of "direct in-process function call" in
  section 4's option table both exist specifically to avoid that trap.
- **Could this demo accidentally just look like dynamic process loading?**
  Only if implementation skips the actual signature/content-identity check
  and treats any candidate binary as admitted. The corrupted-update beat
  (section 3) exists specifically to exercise the resident's real refusal
  path, mirroring `claims.md` row 2's documented byte-flip refusal, not a
  bespoke check written inside `mobile_approach` itself.
- **Does corrupt-update rejection prove Cake admission, not
  application-level validation?** Only if the corrupted candidate is
  presented to the resident before any `mobile_approach` process exists to
  validate anything. If the test instead starts a `mobile_approach` process
  and only then feeds it bad input, that tests the capability's own input
  handling, not Cake. The spec is explicit about this distinction in section
  3, item 4.
- **Is any safety guarantee incorrectly delegated to the replaceable
  process?** No: sections 2 and 7 both state that `mobile_approach` never
  owns velocity/acceleration/TTL/e-stop enforcement; the stable motion
  gateway does, unconditionally, and every failure row in section 7 resolves
  through the gateway, not through `mobile_approach` behaving correctly.
- **Does the design preserve the mission and robot session?** Yes, per
  section 5: everything that must stay alive is explicitly owned by
  processes independent of `mobile_approach`'s lifecycle.
- **Is the data plane unnecessarily coupled to Cake?** No: section 4
  concludes with a transport (ZMQ over Unix sockets) that never touches
  Cake's admin surface, and section 1's diagram crosses the control/data
  boundary at exactly one low-frequency, read-only point.
- **Are we requiring a private Cake feature that can be avoided?** Possibly
  not avoidable, but kept minimal; see `private-cake-requirement.md` for the
  behavior-only ask. The alternative (skip Cake admission entirely for
  `mobile_approach`, e.g. treat it as a plain systemd-supervised process)
  would remove the entire premise of this demo (evaluating Cake's admission/
  replacement model), so it is not a viable simplification for this task,
  only a fallback if the private feature turns out to be infeasible in the
  7-10 day window.
- **Is this realistic in 7-10 days?** Yes. The riskiest unknown, whether
  the runtime supports a second slot, is answered by the inspection in
  `cake-capability-assessment.md`: no engine change is needed for the
  baseline (Path A), and the optional extension (Path B) fills an operation
  the protocol already reserves.
- **What is still outside the signature.** The capsule signature covers the
  supervising module, not the child program's bytes, the same boundary the
  public documentation already states for the LeRobot host. A swap of the
  Python file under an unchanged generation would be exactly the
  "importlib.reload behind a Cake UI" failure and is forbidden: the demo's
  capability update must always be a new Cake generation (new capsule
  identity or new configuration identity). Optional hardening, labelled as a
  module-level check and never as a Cake admission property: pin the child
  program's content digest in the immutable configuration and have the
  supervising module refuse to spawn on a mismatch.

## 10. Observation contract, version 2 (2026-09-26)

Section 2 kept the committed `TargetObservation` (`target_id`, `timestamp_ns`, `frame` fixed to `"base"`, `x_m`,
`y_m`, `quality`) as the capability's input. Measured against the pipeline that actually exists, that contract could
not be met, for four reasons read in the code, not in a wish:

1. Freshness. The controller zeroes an observation older than 200 ms; the complete per-frame perception takes about
   12 s on the desktop GPU (the approved exception, proven on the robot on 2026-09-26: p50 13.6 s round trip) and
   about 63 s on the robot.
2. Frame. `"base"` meant body_floor_mapping_v0, a BODY-camera floor homography never related to the wrist camera or
   to anything the placement measures. The one metric reference of the system is PAD_FRAME, the pad's print
   landmarks seen by the wrist camera at the low scan pose.
3. Quality. No perception score maps to `quality`; the recognizer's confidence is an ordering aid, not a probability.
4. Command expiry. The capability steps its controller with the observation's own stamp as "now", so every command's
   `expiry_ns` is the capture stamp plus 250 ms and its `timestamp_ns` is the capture stamp: the stable motion
   gateway rejects a command that is older than its expiry on arrival, and every command after the first of a pulse
   as replayed (same timestamp). The version 1 wiring has therefore never produced a sustained pulse.

Version 2 is a separate schema, `autonomy/schemas/target_observation.v2.schema.json`, `contract`
`"kc.target_observation/2"`, all fields required, no other field admitted. A version 1 consumer drops it
(`frame_not_base`) and a version 2 consumer drops a version 1 document (`contract_mismatch`): a mismatch fails closed,
the relay accepts no command, the executor refuses the pulse.

| field | meaning |
|---|---|
| `target_id` | the mission target the executor carries |
| `capture_ns` | the robot's monotonic stamp read before the capture was asked for: the capture's identity and its age |
| `published_ns` | the relay's monotonic now at this publication, on the same clock; the answer echoes it as `timestamp_ns` and anchors its expiry to it, so successive answers are strictly newer and never expired on arrival |
| `motion_epoch` | the executor's count of commanded motions that had ended when the capture was taken; an observation is about the scene only while no further motion has ended |
| `frame` | `"anchor_reach"`: origin at the held cube's anchor in PAD_FRAME from the latest stationary pad observation, x along the measured reach axis (the camera axis until a measured pulse gave the base's own displacement axis), y its left normal, metres |
| `x_m` | the remaining bounded advance along x for the anchor to reach the placement admission, `base_advance_needed_mm` of the proven controller divided by 1000, between 0 and 0.120 |
| `y_m` | always 0: the target lies on the axis by construction; lateral deviation is measured and bounded by the executor's pulse gates, never steered |
| `max_age_ns` | this observation's own freshness bound, derived by the source: the run's perception deadline plus the relay's bounded Cake reads and link wait; the relay refuses an older capture on its own clock; scene invariance rests on `motion_epoch` and `stationary`, not on this bound |
| `stationary` | always true: the base read at rest at the capture, no wheel above one velocity count (`autonomy/manipulation/base_rest.py`; exact zero on all three axes until 2026-09-27) |
| `observation_id` | the stored capture this observation came from |

There is no graded quality in version 2. The frozen pipeline's own validity predicate is binary (a pad frame from the
print landmarks with its inlier and residual gates); an observation that fails it is never published, and the
controller receives the value 1.0 with that meaning only. The identity the relay records beside every pulse (the
frame's digest, the run, the desktop perception request, the anchor, the axis, the remaining advance, the pad fit's
inlier count and residual) is evidence and never on the wire.

What the target of the approach IS, in this version: the placement admission point of the held cube. The proven
bounded stop-observe-move behaviour drives with the cube held toward the pad; a target that requires the capability is
the one whose placement measured BASE_ADVANCE at SEARCH. The capability's controller runs with a configuration bound
to that executor's constants: forward speed saturating at 0.05 m/s (the pulse speed the placement passes were run at)
for every remaining advance beyond the executor's tolerance of 8 mm, an envelope of 0 to 120 mm (the bounded total
advance), the handoff region 0 to 8 mm, no deadband. The executor still bounds every pulse's duration, confirms the
stop, reads the base zero, checks the arm's drift and MEASURES the displacement in PAD_FRAME before the next
observation; none of those gates moved. The capability remains the only producer of the forward velocity the gateway
receives.

Association, in the same round: the claim and match radii of target binding are 12.7 mm, half a cube edge. That is
the CHOSEN association rule, not a measured localization bound; it is checked against the retained examples, an
identity it cannot settle is refused, and the attended run tests it beside the approach. The basis stated in the code: two distinct cubes' centres are never closer than one edge (25.4 mm, touching), so no
recorded position can be claimed by two cubes; every recorded re-reading of an unmoved cube falls inside it (5.2 mm
across the advanced and returned viewpoints, 0.3 mm from one viewpoint, 1.6 mm hem effect, base return residual within
2.7 mm); in the two-cube scene a placed cube (at least 17.96 mm inside the pad) and a cube that measures BASE_ADVANCE
(recorded starts 37 to 45 mm outside) are more than four radii apart.
