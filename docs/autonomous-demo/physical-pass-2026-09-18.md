<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# CAKE MOBILE_CAPABILITY BOUNDARY: PHYSICAL PASS

Date: 2026-09-18

Source of truth this document is grounded in: `.local-dev/confirmed_grasp_handoff.md`
(private/gitignored session log - this document is the durable, trackable
extract of the parts that matter for reproduction and for the record).
Nothing below is embellished beyond what that log and the code actually
show.

## 1. What was proven, precisely

**PHYSICALLY PROVEN** (real robot, real cube, real pad, this session):

- Mission ID **`72e9c281fe41`** ran a single continuous `run_hybrid_pick_place()`
  process from `PRECHECK` through `DONE` with no restart.
- `MOBILE_READY` correctly found `mobile_approach` **unavailable**
  (`CAPABILITY_REQUIRED: mobile_approach`, `CAPABILITY_AVAILABLE: False`)
  and transitioned to `WAITING_FOR_CAPABILITY`, not directly to
  `MOBILE_APPROACH`.
- At `WAITING_FOR_CAPABILITY`, physical preconditions already held and were
  preserved, not advanced: `object_secured=True`,
  `end_effector_clearance_confirmed=True`, `arm_frozen=True`,
  `base_stationary=True`. No arm or base command was sent while waiting -
  the wait is a pure `threading.Event.wait()` inside
  `CapabilityRegistry.wait_for_change()`, not a polling loop with periodic
  actuation.
- Operator submitted an **invalid/unadmitted replacement candidate**
  (`SUBMIT INVALID`, capsule identity `mobile-approach-invalid-candidate`,
  not a member of the registry's admitted set). **Cake rejected an
  invalid/unadmitted replacement candidate** (`CAKE_CANDIDATE_REJECTED`,
  reason: `"candidate names an unadmitted capsule"`). The capability
  remained unavailable (`CAPABILITY_AVAILABLE: False`, re-logged after the
  rejection - proof the wait loop woke, re-checked via the real registry,
  and observed "still unavailable", not a static hang). **The robot did
  not move** during this window; operator visually confirmed no change.
- Operator submitted the valid candidate (`SUBMIT VALID`, capsule identity
  `mobile-approach-real-build`, a member of the admitted set).
  `CAKE_CANDIDATE_ADMITTED` → `CAPABILITY_ACTIVATED` →
  `CAPABILITY_AVAILABLE: True`.
- The **same mission ID (`72e9c281fe41`) was logged again**, immediately
  before `STATE_ENTER: MOBILE_APPROACH` - the same process, the same
  `run_hybrid_pick_place()` call, the same `_Ctx` instance resumed. No
  second `GRASP_POSITION`, no restart of any kind.
- `MOBILE_APPROACH` executed the **unmodified** `MobileApproachController`
  → `StableMotionGateway` → `LeKiwiTransport` refreshed-lease sustained
  motion (`vx_mps=0.05`, `duration_s=1.5`), `stopped_confirmed=True`.
- `BASE_STOP_VERIFY` confirmed `x.vel=y.vel=theta.vel=0.0`.
- `PLACE_APPROACH` (`solve_cartesian_forward_and_z`, `EXTEND` forward
  60mm/down 27mm) → `PLACE_READY` (operator YES) → `RELEASE` (gripper
  66.8→80.193, operator-confirmed `SUCCESS` - cube left the jaws and landed
  on the pad) → `RETREAT` (+20mm pure Cartesian) → `DONE`.
- Final result flags: `object_secured=True`,
  `end_effector_clearance_confirmed=True`, `placement_ready=True`,
  `abort_reason=None`.
- Final physical state: cube stable on the pad, gripper open, arm
  retreated ~20mm clear, **base 0/0/0**.

**OPERATOR-CONFIRMED** (physical judgment calls a sensor did not make):
cube grasp security, clearance sufficiency at each increment, "robot
physically unchanged" after the rejected candidate, release-readiness,
release success. None of these were inferred from a camera by software -
every one was an explicit line answered by the human, exactly as
`hybrid_pick_place.py`'s docstring requires for this version.

**OFFLINE TESTED, not (yet) separately re-proven live**: the individual
unit/integration properties in `autonomy/tests/test_capability_registry.py`
(4 tests) and `autonomy/tests/test_hybrid_pick_place.py`'s
`TestCakeCapabilityBoundary` class (4 tests) - e.g. "rejected candidate
never causes base motion" is proven both as an offline assertion (fake rig,
zero `move_base_calls`) and, separately, as a live observation this
session; they are two different kinds of evidence, both true, not the same
evidence counted twice.

**NOT YET AUTOMATED**: perception (no camera-driven grasp/clearance/
placement judgment), the two-cube mission, any retry/auto-shrink on a
refused Cartesian target, any capability besides `mobile_approach`, and a
real Cake admin-socket adapter (this session's `CapabilityRegistry` is
software-only - see `autonomy/motion/capability_registry.py`'s own
docstring).

## 2. Precise wording (do not upgrade)

> Cake rejected an invalid/unadmitted replacement candidate.

This is the only claim this milestone supports. It is **not**:
"corrupted binary rejection", "malicious update rejection", or
"cryptographic integrity protection" - those would require a real signed
capsule/admission path (`docs/autonomous-demo/cake-capability-assessment.md`
section 4, "Path B"), which is not implemented. This session's
`CapabilityRegistry` checks membership in a pre-declared identity set
("Path A" semantics) - deterministic, but not a signature or integrity
check.

## 3. Relevant test-suite result at this milestone

- `autonomy/tests/` (tracked): **202/202** passing
  (`python3 -m unittest discover -s autonomy/tests -p "test_*.py"`).
- `.local-dev/test_interactive_hybrid_pick_place.py` (gitignored scratch,
  tests the interactive wiring script, not part of the tracked suite): 12/12.
- Combined offline total this milestone: **214/214**.
- New test files contributing to this milestone: `test_cartesian_lift.py`
  (17, includes `solve_cartesian_forward_and_z`), `test_hybrid_pick_place.py`
  (18, includes the 4 `TestCakeCapabilityBoundary` tests),
  `test_capability_registry.py` (4).

## 4. Exact commands/workflow used

Historical machine-specific network identifiers and usernames have been
redacted from this public-repository record; the original values remain
only in local/private session notes (`.local-dev/confirmed_grasp_handoff.md`,
gitignored).

Fresh-session safety check (every session, before any actuation):
```
ping -c 2 -W 2 <pi-ip>
ssh <user>@<pi-ip> 'pgrep -af "python3.*lekiwi_host_eval.py"; ss -ltnp | grep -E "5555|5556"; uptime; vcgencmd get_throttled'
```
(If the host had rebooted: bus-only Present→Goal sync with torque OFF -
see the reproduction checklist below - was NOT needed this run, the host
had been running continuously.)

### Reproducing this milestone today: the tracked runner

`bin/laptop/manipulation/run_hybrid_pick_place.py` is the tracked entry
point and reproduces the full capability boundary
(`WAITING_FOR_CAPABILITY` → invalid-candidate rejected → valid-candidate
admitted → `MOBILE_APPROACH`) from one normal terminal, backed by the real
`autonomy/motion/capability_registry.py` `CapabilityRegistry` - no
gitignored chat/FIFO harness required:
```
KC_REMOTE_IP=<pi-ip> KC_ROBOT_ID=<robot-id> \
  python3 bin/laptop/manipulation/run_hybrid_pick_place.py
```
Every existing physical-judgment gate (cube placement, clearance,
placement, release) still answers via plain `input()` in that same
terminal, unchanged. When the state machine reaches
`WAITING_FOR_CAPABILITY: mobile_approach`, the same terminal prompts
`CAKE> ` - type `INVALID` to submit the known unadmitted capsule identity
(rejected, capability stays unavailable, remains waiting) or `VALID` to
submit the known admitted one (activates, the same mission proceeds
straight into `MOBILE_APPROACH`). One `mission_id` (printed as
`MISSION_ID: <id>`) is generated once at process start and is unchanged
across the whole run, including across capability activation.

### This session's actual launch (historical: not required for reproduction)

The live 2026-09-18 mission itself ran through a chat-driven operator
session with no live terminal, so it used a different, gitignored harness
instead of the tracked runner above (which did not yet exist at the time):
```
KC_REMOTE_IP=<pi-ip> KC_ROBOT_ID=<robot-id> python3 .local-dev/interactive_hybrid_pick_place.py /tmp/hybrid_pick_place_bridge --remote-ip <pi-ip> --robot-id <robot-id>
```

Operator-gate answers (unchanged FIFO):
```
echo "<ANSWER>" > /tmp/hybrid_pick_place_bridge/answer.fifo
```

Cake candidate submission (separate, independent FIFO):
```
echo "SUBMIT INVALID" > /tmp/hybrid_pick_place_bridge/cake_events.fifo
echo "SUBMIT VALID" > /tmp/hybrid_pick_place_bridge/cake_events.fifo
```

Progress was watched via:
```
tail -n <N> /tmp/hybrid_pick_place_bridge/events.log
cat /tmp/hybrid_pick_place_bridge/prompt.txt
cat /tmp/hybrid_pick_place_bridge/done.txt   # after DONE
```

`.local-dev/interactive_hybrid_pick_place.py` remains gitignored scratch,
useful for a chat-driven operator session without a live terminal, but is
**not** a required runtime dependency for reproducing this milestone.

## 5. Relevant files/logs

- `.local-dev/confirmed_grasp_handoff.md` - the full session-by-session
  narrative this document is extracted from (private, gitignored).
- `/tmp/hybrid_pick_place_bridge/events.log` - the full structured event
  log of this specific run (ephemeral, `/tmp`, not preserved across
  reboots - the handoff file's inline excerpts are the durable copy of the
  parts that matter).
- `.local-dev/captures/*.png` - wrist-camera frames captured at each
  physical checkpoint across this session's runs (gitignored).
- Code: `autonomy/manipulation/hybrid_pick_place.py`,
  `autonomy/motion/capability_registry.py`,
  `autonomy/manipulation/cartesian_lift.py`,
  `bin/laptop/manipulation/run_hybrid_pick_place.py` (the tracked
  reproduction entry point, section 4), and, historically only,
  `.local-dev/interactive_hybrid_pick_place.py` (the chat-driven harness
  that actually ran this session's live mission, not required for
  reproduction today).

---

# Reproduction checklist

For a fresh session to reproduce this milestone without rediscovering the
underlying robotics work. Follow in order; do not skip a step because a
prior session's state "probably" still holds.

1. **Pi/network health**: `ping <pi-ip>`. If unreachable, this needs
   physical operator inspection - no automated recovery exists.
2. **Host/port check**: SSH in, confirm `lekiwi_host_eval.py` is running
   and ports 5555/5556 are listening. Check `uptime` - a small uptime means
   a reboot happened since the last session.
3. **Power/battery**: `vcgencmd get_throttled` - must be `0x0`. Any nonzero
   value means undervoltage has occurred; investigate before proceeding.
4. **Power-cycle safety (mandatory if the host was not already running, or
   uptime indicates a reboot - no exceptions)**: torque is OFF between host
   sessions. Connect `LeKiwi(config)`, call `robot.bus.connect()` - **never**
   the full `.connect()`, which torques immediately - read
   `Present_Position` and `Goal_Position` for all arm motors, and if they
   differ (mismatches up to 305° have been observed after a reboot in this
   project), write `Goal_Position = Present_Position` (safe: torque is off,
   no motion is possible) **before** starting the host / enabling torque.
   Skipping this risks a violent snap toward a stale goal on torque-enable.
5. **Wrist camera health**: capture one read-only frame
   (`.local-dev/capture_wrist_frame.py`) and visually confirm it looks
   sane. The front camera stays disconnected (standing invariant).
6. **Deterministic grasp setup**: known-good pose (pan -10.549, lift
   43.121, elbow 54.286, wrist_flex -33.714; wrist_roll is **never**
   forced - always a fresh runtime read). Operator places the cube between
   the jaws; the state machine's `GRASP_CLOSE` gate
   (`PLACE CUBE BETWEEN JAWS, then confirm CUBE READY/ABORT`) exists
   specifically so this never closes blind.
7. **Gripper latch**: 66.8 (close), 80.193 (open) - both deterministic
   constants in `hybrid_pick_place.py`, not re-derived per session.
8. **Cartesian clearance**: `solve_cartesian_z_lift`, fresh-solved +20mm
   increments, operator-gated (`CONFIRM CLEARANCE`: YES/PARTIAL/NO/ABORT)
   after each. `max_clearance_steps` defaults to 3 - if more are genuinely
   needed, the run has to be restarted with a higher value passed to
   `run_hybrid_pick_place()`, since the cap cannot be raised mid-run (see
   the known limitation in `.local-dev/confirmed_grasp_handoff.md`).
9. **MOBILE_READY**: the state machine checks mechanical preconditions
   (`object_secured`, `end_effector_clearance_confirmed`, `arm_frozen`,
   `base_stationary`) **before** even checking capability availability -
   an available capability never bypasses these.
10. **Cake candidate submission**: using the tracked runner
    (`bin/laptop/manipulation/run_hybrid_pick_place.py`), this is a `CAKE> `
    prompt in the SAME terminal as every other operator gate - distinct
    from those gates only in which state triggers it; `confirm()` is still
    never called from `WAITING_FOR_CAPABILITY`, `wait_for_capability_change()`
    is. (Historically, the chat-driven session that ran the original
    2026-09-18 mission used a second, independent FIFO - `cake_events.fifo`,
    never the answer FIFO, read by a background daemon thread - because it
    had no live terminal; that FIFO path still works for that harness but
    is not required for reproduction.)
11. **`CAKE> INVALID`**: expect `CAKE_CANDIDATE_REJECTED` (reason: candidate
    names an unadmitted capsule), then a re-logged
    `CAPABILITY_AVAILABLE: False` (the wait loop waking and re-checking).
    No motion. Visually confirm the robot is unchanged before continuing.
12. **`CAKE> VALID`**: expect `CAKE_CANDIDATE_ADMITTED` →
    `CAPABILITY_ACTIVATED` → `CAPABILITY_AVAILABLE: True` → the **same**
    `MISSION_ID` logged again → `STATE_ENTER: MOBILE_APPROACH`. If a
    different mission ID appears, or `GRASP_POSITION` runs a second time,
    something is wrong - stop.
13. **Refreshed sustained `mobile_approach` semantics (do not regress
    this)**: **TTL (150ms) is a watchdog, not the intended
    movement-duration primitive.** Isolated 150ms pulses were measured
    ineffective under a loaded/elevated arm (~1mm across 10 pulses).
    Refreshed sustained leases (lease resubmitted every ~75-100ms, well
    inside the TTL, explicit zero at the end, **repeated-read** zero
    confirmation - not a single sample, transient blips have been observed
    and must settle to exact zero) are what actually moves the base. Any
    future change to `mobile_approach.py`/`stable_motion_gateway.py` must
    preserve this.
14. **Explicit base-zero verification**: after every base move, confirm
    `x.vel=y.vel=theta.vel=0.0` from **repeated** reads, not the first one.
15. **Placement forward+down IK**: `solve_cartesian_forward_and_z` -
    pure vertical descent was tried first and found insufficient (the
    cube stayed short of the pad interior); combining forward reach with
    descent, within the arm's existing fixed plane (shoulder_pan held), is
    what actually worked. The singularity-margin refusal
    (elbow_flex within ~10° of 0°/180°) is a **feature**: a step that
    would need it refuses itself rather than actuating; the operator then
    supplies a smaller step from the same fresh pose, never a restart.
16. **Release**: freeze arm at current measured position, gripper
    66.8→80.193 over ~0.5s, hold ~0.75s, then a small `RETREAT`
    (`solve_cartesian_z_lift`, +20mm). Operator confirms cube-on-pad
    before any further step.
17. **Final success criteria**: `final_state == DONE`, `abort_reason is
    None`, `object_secured`, `end_effector_clearance_confirmed`, and
    `placement_ready` all `True`, base `0/0/0`, cube visually confirmed
    stable on the pad.
