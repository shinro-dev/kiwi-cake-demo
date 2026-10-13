<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Cake console integration: handoff, 2026-09-23

The starting point for the next session. Everything here is a recorded fact with where to check it. No private
configuration value, key or machine path is written down; where one is needed, this says how to obtain it.

START WITH SECTION 13, the final handover of 2026-09-26 (state, what is and is not freshly checked, the remaining
work and the exact resumption commands). Then SECTION 12, the night round that followed the handover (the operator console reconciled and made
observe-only, the robot perception benchmark with its placement figures, the browser and adapter work), then
section 10, the handover at the end of the 2026-09-23 session: the deployment was corrected to its original
placement (Cake resident, signed supervisor, supervised host and capability processes on LeKiwi; the desktop
relays), Cake is installed and running on the robot WITHOUT a host, and nothing has moved. Sections 1 to 7 describe
the earlier laptop development path and are kept as its record.

## 1. Commits

| commit | what it added |
|---|---|
| `e8bd0e2` | `packages/demo-runtime/demo_runtime/cake_admin.py`, the read-only adapter, and the ladder wiring |
| `c9f32cd` | the optional `cake` section in `RuntimeConfig` |
| `9225bc2` | the structured Cake status surface, versioned apart from contract v1 |
| `13bd504` | the sidebar binding: which resident answered, and how old that answer is |
| `522b128` | the frame-rate warm-up correction. COMMITTED, NOT YET DEPLOYED |
| `9c55b91` | `autonomy/motion/mobile_approach_service.py`, the process a capability slot spawns |
| `9e26d52` | `autonomy/motion/cake_capability_adapter.py`, availability bound to one slot, and how to read an answer |

Deployed on the console port: `13bd504`, bundle `index-CBFkzo_w.js`. `522b128` and later are not deployed.

## 2. The activation mechanism is ApplyPlan, not ReplaceSlot

MEASURED, NOT ASSUMED. `ReplaceSlot` naming the capability slot and the mobile capsule is answered
`transaction_phase ABORTED` on a freshly started resident with no prior transaction, and leaves the slot exactly
as it was. `ApplyPlan` carrying the prepared swapped candidate Plan completes and moves the slot. The candidate
changes the capability slot and the holder slot together, which is why one operation reaches it and a
single-slot replacement does not. There is no need to make `ReplaceSlot` work for this demonstration.

Nothing is uploaded. Both capsules are admitted when the resident starts, and `AdmitCapsule` is answered
unavailable, so the activation chooses between builds the resident already holds. The operator-facing action is
"Activate mobile approach"; diagnostics and history must name it as an ApplyPlan operation carrying the prepared
candidate, and must not imply an upload, a signature failure or a corrupted binary.

**Expected slot changes.** The capability slot `mobile_approach` moves from the inert capsule to the mobile one
and its generation advances by one. The holder slot `mobile_approach.holder` takes the inert capsule in
exchange. The LeRobot supervisor slot is outside every closure this deployment can compute and stays ACTIVE;
this was checked after activation, not assumed.

**Retry rules**, which the resident enforces and the adapter encodes:

- a NEW intended operation gets a FRESH operation UUID;
- a RETRY reuses the SAME UUID and MUST carry byte-identical payload, which means the SAME prepared Plan bytes;
- the same UUID with different bytes is answered `operation_identity_mismatch` and mutates nothing;
- after a reconnect the operation's state is retrieved, never resubmitted on a guess.

**Reading the answer.** `status ok` does NOT mean the update happened: the dispatcher answers it for a
transaction record without consulting the record's phase, so an ABORTED transaction arrives under `status ok`.
`transaction_phase` decides the outcome. A request-level failure is a third thing again and is never reported as
Cake rejecting the candidate. An activation is CONFIRMED by re-reading the slot, never by the answer.

## 3. What decides that the capability is available

One thing: the capability slot, named by its uuid, reported ACTIVE on the capsule that carries the capability.
Three readings look equivalent and are not, and each has a test in
`autonomy/tests/test_cake_capability_adapter.py`:

- the HOLDER slot carries the same active capsule by design, so the engine has an admitted mapping to activate
  onto. A reader that asked whether the capsule is present anywhere would answer yes before anything was
  activated, and erase the wait this demonstration exists to show;
- TRAFFIC IS NOT LIFECYCLE. An active capability legitimately publishes nothing while it waits for an
  observation, and a stale publisher can still be emitting after its slot went inert. No command, count or rate
  is read;
- the `command_id` prefix is a CORRELATION LABEL and preserves command uniqueness. Availability is decided from
  the slot first; the prefix then catches output from a generation that is no longer running. It is never the
  authority.

## 4. The thin path, closed

Verified against a live resident on the deployment `demo-plan build-mobile` prepares:

1. the inert capability slot provides no usable approach output, and the holder carrying the active capsule does
   not change that;
2. an unadmitted candidate is refused, `transaction_phase ABORTED` under `status ok`, with the slot untouched;
3. ApplyPlan with the prepared candidate completes: the slot moves to the active capsule, its generation
   advances, and the capability slot's own supervisor log goes from EMPTY to carrying its child's output, which
   is what says a producer now exists;
4. a labelled input whose geometry requires an approach produces a bounded nonzero vector through the capability
   code and the existing gateway. CORRECTION: this vector came from `ApproachService` running INSIDE the test
   process with scripted input, not from the process the capability slot spawned. The slot's child in that
   deployment is the stub script, because build-mobile gives every slot the stub. No Cake-spawned capability
   process had produced output when this was written;
5. applying the starting Plan again returns the slot to the inert capsule and no usable output follows, and
   output still labelled with the old generation is refused;
6. the supervisor slot and the holder slot are both still ACTIVE afterwards.

**The nonzero fixture** (software behaviour only: its output ended in an in-memory recorder, and no speed here is a
qualified hardware speed). `cube_far`, `x_m` 0.95, `y_m` 0.10, quality 0.92. It sits inside the trusted
localization envelope and OUTSIDE the handoff region, so the controller must drive. The capability publishes
`vx_mps` 0.10 and `vy_mps` 0.06. The gateway refuses it while autonomous is disabled
(`ZERO_AUTONOMOUS_DISABLED`, nothing forwarded) and reports `APPLIED` once enabled, clamping independently to
0.05 and 0.05. The gateway is the enforcer, not the capability.

An earlier fixture at `x_m` 0.40 produced a ZERO command. That was correct: 0.40 is inside the handoff region,
so the controller declined to drive. It was not a broken path.

**Where an APPLIED command goes in this harness.** `transport.FakeBaseMotionTransport.last_sent`, an in-memory
recorder that imports no lerobot and touches no device. `lerobot_transport.LeKiwiMotionTransport` is never
constructed. GATEWAY ACCEPTANCE IS NOT PHYSICAL EXECUTION, and every later piece of evidence must keep naming
this destination rather than implying the robot moved.

**This is development evidence.** The hosting process is `plan-resident` on the x86-64 fixture profile, which
the Cake repository declares a test tool. The update engine, dispatcher, transaction coordinator and admin
socket are the real shipped code; only the profile expression differs. The production target is the
LeKiwi aarch64 resident and profile.

## 5. Reproducing it

The prepared Plans and the staged store come from one command, run with the Cake build tree's root exported as
`CAKE_REPO_ROOT`:

    demo-plan build-mobile --out-dir DIR --gate-fixture-key

It prints the slot names and uuids, the inert and mobile capsule digests, the signing key it used and two Plan
artifacts under `DIR/plans`: `mobile-demo.cakepln`, the STARTING Plan whose capability slot is inert, and
`mobile-demo-swapped.cakepln`, the prepared ACTIVATION candidate. The staged store is `DIR/state-root` and must
be mode 0700 before a resident will use it.

The resident is then started with a configuration naming that store, that starting Plan, an admin socket short
enough for SUN_LEN, and the printed public key as `trusted_keys`. The exact key layout is section 3 of the
earlier console handoff and `kc_write_resident_conf` in `bin/lib/common.sh`; note that helper writes
`admin_socket_mode 0660` and always writes `plan_artifact`, so it is a layout reference rather than a drop-in.

**The rehearsal harness is now tracked** (superseding the note that it was not): it was promoted to
`autonomy/tests/test_cake_thin_path.py`, an opt-in test gated on `KC_CAKE_PROBE`, `KC_CAKE_SOCKET` and
`KC_CAKE_PLANS`. Its raw evidence and the private runtime configuration stay in ignored local directories.

## 6. What remains, in order

1. **The persisted mission sequence.** Reachable cube placed, second target selected, WAITING_FOR_CAPABILITY, a
   rejected activation leaving the mission waiting, a valid activation observed, the same target resumed through
   the capability and the guarded controller. Persist the mission identity, the selected target, the waiting
   reason and the activation operation. Child cycles may carry their own run ids, but their outcomes stay
   associated with the right mission and target. Resume from fresh validated observations and the existing
   guards, never from a saved motion command.
2. **The frontend integration.** The approved five-step layout is preserved. Add only the capability state, the
   prepared activation action, transaction progress and result, and the history of that transition. A rejected
   activation must leave the waiting mission understandable and recoverable. A successful request stays PENDING
   until the expected slot state is observed. A browser reload must recover the displayed state without starting
   a mission or resubmitting an activation.
3. **The browser rehearsal**, explicitly labelled non-motion, on an isolated stack so no fixture result can
   enter the live robot's history: complete the first target, wait on the second, reject the unavailable
   candidate, activate the prepared one, resume the same mission and target producing the bounded nonzero
   approach output, then reload and recover the history without resubmission.
4. **Production aarch64 verification.** Build the coherent LeKiwi artifacts and verify the same activation and
   execution connection, without motion, on the supported resident before the hardware path is called ready.
   Any first verification on the board stays non-motion and preserves the normal host, camera ownership and
   acquisition, and the Plan's startup behaviour is inspected before it is launched.
5. **The attended hardware qualification**, in its existing dependency order: the detector's 0.40 calibration
   validation, then the front-camera qualification. Their verdicts stay separate from task success.

**Pending deployment.** `522b128`, the frame-rate warm-up correction, is committed and NOT deployed. It shows
"Measuring frame rate..." until the rate window has been open its whole length and enough distinct frames have
landed, resets that warm-up when the source session changes, and leaves the steady-state calculation alone. It
must go out with the next frontend build, together with whatever the frontend integration above adds.

## 7. The physical execution adapter: settled decisions

The mission coordinator reuses the transition logic of `autonomy/manipulation/hybrid_pick_place.py` and takes
everything that touches a robot as an injected execution adapter. A separate task implements the hardware one.
These decisions are settled and bind it. They exist because a mapping that typechecks and passes its unit tests can
still swap the proven motion envelope for the state machine's test-oriented one.

**A rehearsal confirmation may never be reachable from a hardware-capable path.** The rehearsal adapter answers the
machine's judgment gates unconditionally, which is right for a non-motion rehearsal and is not a check. A
`performs_motion` flag is a label, not isolation: the rehearsal adapter is constructible only in a synthetic
runtime, and a hardware-capable path must not be able to obtain one. The operator's own physical staging and
supervision confirmations are a separate thing and are kept.

**Hardware execution uses the proven measured predicates.** The single-cube controller has no interactive gate at
all; it replaced each one with a measurement, among them grasp retention, release admission and placement. Those
are what carry the physical qualification and they are what a hardware adapter answers with.

**Cartesian requests pass through the proven builders and their complete guards.** The machine holds the requested
forward and vertical deltas one frame above every arm move. A hardware adapter passes THOSE to the proven pose
builders and lets them solve and gate. Taking the joints the machine already solved and handing them to the proven
mover keeps only the final result check and drops the straight-arm elbow guard, the interpolated-path sampling, the
tilt check and the floor limits. That substitution is forbidden.

**The base keeps its consecutive, spaced zero reads and every executor constraint.** The robot client returns its
cached state when no new message has arrived, so a single sample is not a base-zero reading. The executor's
posture, gripper-phase, arm-drift, pulse-duration and total-travel constraints are preserved, and a request the
pulse validator refuses is not split mechanically to make it fit.

**An unavailable mapping refuses the hardware operation.** Where a required physical mapping does not exist, that
operation is refused. It is never satisfied by an unconditional confirmation or by a weaker check.

Software coverage does not transfer physical qualification, and nothing above is evidence about hardware.

## 8. Deployment placement: as found, as originally proven, and as corrected

**The ruling (operator, 2026-09-23).** The LeKiwi robot runs the Cake resident, the signed supervisor capsule, the
robot host that Cake launches and supervises, and the capability processes of that deployment. The desktop runs
the frontend, connection and teleoperation, telemetry display, diagnostics and operator requests. The robot owns
authoritative execution and lifecycle state; a desktop proxy may relay requests and cache readings, and must never
substitute a local resident, a rehearsal coordinator or generated camera images for the robot. Everything in
sections 2 to 5 that ran on the laptop is development evidence and is not this deployment.

**As found, 2026-09-23 (the console had drifted from that).** Every console process ran on the laptop: the
runtime daemon with its single run slot, journal and stop path; the controller with its perception and gateway,
talking to the robot host directly over the LAN; and the API and frontend. The robot host was started by hand in a
foreground `ssh -t` from a laptop terminal, so its life was tied to that terminal. The camera service was started
over ssh and ran detached on the robot. The console's Cake status read an x86-64 resident on the laptop, with an
empty Plan and no children, which trusted the publicly derivable fixture key. Cake had no role in launching the host.

**As originally proven (public release v0.1.3 on the robot).** A systemd user unit ran `cake-resident`
(`Type=simple`, `Restart=on-abnormal`, `RestartSec=2`, `KillMode=mixed`, `TimeoutStopSec=60`). Its Plan was built
ON THE ROBOT by the aarch64 `demo-plan build`, signed with an Ed25519 key generated on the robot
(`--signing-key-file`, never the fixture key), with `--child-command` naming a wrapper that execs the host. The
supervisor slot forked and exec'd that wrapper, so the process tree was user manager, resident, host. A SIGTERM to
the host alone was answered by safe-stop and a restart at ordinal 1 with the resident and its session unchanged; a
SIGKILL to the resident was answered by systemd relaunching it with a fresh session and identical identities.

**How the supervisor treats its child (measured or read from the shipped behaviour).** The child is exec'd
directly with its argv, an environment of exactly `PATH=/usr/bin:/bin`, and a working directory inside the bundle,
so a wrapper must name its interpreter absolutely and must `exec`. It is restarted on ANY exit, at most four times
(five runs), then the slot reports DEGRADED and spawns nothing until a resident restart or a Plan re-apply. A stop
sends the configured signal and SIGKILL 500 ms later, to the child pid only. The child carries a parent-death
SIGKILL, so it dies with the resident, and safe-stop does not run in that case. Its stdout and stderr go to a log
capped at 64 KiB and truncated at every activation, so evidence is copied as soon as it is produced.

**What SIGKILL does not do.** The host's 500 ms base watchdog runs inside the host's own loop, and the base is
stopped only on an orderly disconnect. Every SIGKILL path (the 500 ms escalation, the parent-death signal, the
cgroup kill at the stop timeout) skips it, so a base moving at that moment keeps its last wheel velocity, and the
safe-attach host then refuses to attach to a wheel that is turning. This is a physical gap, recorded for the
attended qualification; nothing in this section moves the robot.

**A restart must not silently restore permission to move.** Cake restarts the host and systemd restarts the
resident without asking anyone, and a running controller's socket reconnects by itself. The corrected deployment
therefore holds motion after any restart: the host wrapper execs the host only when an operator's arm token is
present and consumes it, and the desktop binds motion permission to the resident's session and the supervised
host's pid, so a change in either revokes it until the operator confirms again.

**What the capsule signature covers, and what it does not.** The Ed25519 signature is over the capsule's content
digest: its manifest, its descriptor and the supervisor module it carries. It does NOT cover the Plan, which has
no signature section; the per-slot configuration, which holds the child command, its arguments, environment,
working directory, stop signal and safe-stop command and is bound to the Plan only by an unsigned digest; the
program at the child path, its interpreter, LeRobot and every Python dependency, or the calibration; the resident's
own configuration and trusted keys; or the resident binaries, which carry only the release signature on their
tarballs. Admission, and therefore the signature decision under `signature_policy require`, happens only when the
resident starts. `AdmitCapsule` is answered unavailable, so a live update can only select among capsules and
configurations the starting Plan already admitted: it is an activation of an admitted build, never an upload.

**Resident uptime defect (measured on the 68f0911 build).** An ApplyPlan swap on a freshly started resident
COMPLETED at 5 s and 20 s of uptime and was ABORTED at 40 s, 60 s, 40 s and 90 s, one fresh store per trial. The
transaction barrier passed a fixed 30 s as an absolute deadline that the supervisor module compares with time
since resident start, so after 30 s of uptime the old generation never quiesces and the swap aborts. The same
fixed value reaches the shutdown drain. On the robot the resident is always up far longer than 30 s before an
operator activates anything, so this blocks the update until Cake carries the fix. An earlier explanation, a store
reused across activate and revert cycles, was wrong and has been withdrawn wherever it was written.

**Device ownership, target.** The host owns the servo bus and no camera (`--robot.cameras={}`,
`--robot.disable_torque_on_disconnect=false`, exactly the proven safe-attach flags). The camera service owns the
wrist camera and its lock. Exactly one host may hold the bus: the two legacy user units on the robot stay inactive,
and the host wrapper checks bus and port ownership itself before it execs, because install-time checks do not
cover restarts or boots.

**Command and data paths, target.** The browser talks only to the desktop API on loopback. The API relays to the
desktop runtime daemon over its private socket. The daemon reads the robot's resident through an ssh
unix-socket forward of the robot's admin socket; that forward carries the whole admin protocol for this user's
processes, so the trust boundary is the user account and its ssh key, and the console client stays read-only by
construction except for the one activation module. Camera preview arrives over the existing ssh forward of the
camera service's publisher. Whether the task controller and its perception move to the robot is an open operator
decision: the proven perception needs a model runtime the robot does not have installed today.

## 9. The LeKiwi deployment, as deployed and verified (2026-09-23, no motion)

**Pinned Cake.** Private branch `task/ck-lekiwi-deploy`: `a794687` (absolute quiesce deadline), `9775cbe`
(build-mobile per-slot children), `34dca51` (task contract, UNRATIFIED). Resident source digest `1545e4b0...`; the
deployed resident reports build identity `1204d3f725b3339300c5ab9f3c2cf5e06cfb27e7d1dccb38112613b562baeb18`, which the
desktop pins as `cake.robot.expected_build_identity`. CORRECTION (2026-09-25): a build identity identifies the
SOFTWARE, not a machine or a deployment; a resident built from the same source elsewhere reports the same value
(12.4). Which deployment is observed is established by the authenticated connection to the intended robot (the ssh
forward to that robot's admin socket) and that endpoint's current resident session. The pin only refuses a
different build; the target profile digest refuses nothing here, since every build for this profile shares it.

**The fix, precisely.** The drain barrier handed the 30 s budget to `prepare_quiesce`, whose argument is an absolute
reading of Cake's clock counted from resident start, so past 30 s of uptime every deadline had already expired. The
field is now a budget converted at each call boundary to now plus budget. The budget is unchanged, so a module that
genuinely fails to quiesce still times out and is resolved by policy; admission, candidate validation and every
refusal path are untouched. Regression: tests that assert the handed deadline lies a full budget after the clock,
and a selector driving the abort path past the budget. The private canonical gate was not green in the worktree
layout used; every red lane was traced to pre-existing stale items, that layout, or a known flaky test.

**Deployed.** A verified release under the deployment root on the robot, built there by the aarch64 `demo-plan
build-mobile` and signed with a deployment key generated on the robot (key id `bcef31cc...`; the seed never left
it). The user unit is installed, not enabled. Admission under `signature_policy require`: all three capsules
(supervisor `395c819e`, inert `9600566d`, mobile `2f34a84c`) verify admitted with the deployment key and are refused
`signature_key_id_not_trusted` with a throwaway key, and the real resident refused a throwaway-signed capsule at
start with `plan-capsule-not-admitted ... policy require refused this capsule` and exit 1, in its own scratch store.
build-mobile writes no unsigned capsule, so that case was not exercised.

**Observed relationship.** Started without an arm token, the resident (systemd user unit) logged `signature policy
require, trusted key ids bcef31cc...`, activated three slots, and its supervisor slot spawned the host launcher five
times, each refused before opening the servo bus ("HOST HELD ... no operator arm token"); get-health reports the slot
DEGRADED with five spawns and no child, and nothing listens on the host ports. The host has therefore NOT yet run
under Cake: its launch is the operator step below. The holder runs the stub.

**Update on the target.** At 42 s of uptime, and again at about 15 minutes: the invalid candidate was answered
`status ok`, `ABORTED`, with session, Plan, epoch, every slot and every health row identical; the valid candidate
COMPLETED, the capability slot moved to the mobile capsule at generation 2 with a new instance, the holder took the
inert capsule with no child, the supervisor slot kept its capsule, generation and instance, and the session did not
change. The capability's child is the capability service, exec'd by its wrapper, whose parent is the resident and
whose pid equals the slot's reported child pid; the holder never ran it. A non-motion probe fed it one labelled
observation: it answered with a command labelled with its own pid, which the probe bound to the process Cake
reports, and a directly constructed gateway recorded ZERO_AUTONOMOUS_DISABLED while disabled and APPLIED (0.05,
0.05) once enabled, into `transport.FakeBaseMotionTransport`; no lerobot module was loaded.

**Recovery.** SIGKILL of the capability child: Cake restarted it (new pid, spawn 2), resident and session
unchanged. Rollback by applying the starting Plan: capability inert again. `systemctl --user restart`: new resident
and new session, and the supervisor slot held again: a restart did not restore permission to start the host.

**Desktop.** A connected stack on its own port and data directory, LIVE without hardware commands, reads the robot
through an ssh forward of its admin socket; the page shows the labelled robot, the resident, the held host, the
capability generation and pid, the last transaction and motion HELD (never confirmed), with Observe and Return arm
off and their scope described. The wrist stream reports frames arriving; the headless capture shows the image area
black, and whether that is the camera view or the capture is not established. The operator's existing console on
its own port still runs its earlier configuration and must be cut over deliberately.

**Remaining before any motion.** (1) Operator present at the robot: `bin/pi/lekiwi-deploy.sh arm`, which lets the
host attach once through its own safe-attach checks; a historical torque-on log says nothing about the arm now, and
no automatic acquisition exists. (2) Confirm the robot in the console (session and host instance). (3) The mission
and controller execution path on the robot: a real execution adapter that keeps the measured predicates and motion
envelope of section 7; the LIVE refusal stays until then. (4) The OWLv2 feasibility measurement on the robot. (5)
SIGKILL of the host or resident skips the base watchdog; the arm token does not close that gap. (6) The detector
0.40 validation and the front-camera qualification, each separate.

## 10. Handover at the end of the 2026-09-23 session

Implementation is stopped. Nothing was pushed. Paths below are relative to the console worktree root unless they
start with `~` (the robot's home) or name the main checkout.

**Revisions.**
- Public, branch `feat/operator-console`: this commit and, in this session, `f0c1bab` `43cb91c` `857f45c` `e4a2f7e`
  `d023f39` `5956dd6` (laptop development path), then `026eb56` `22322ac` `75d6a74` `afd0f10` `3eb4382` `bd0dfc5`
  `eb7048d` (deployment correction). Tree clean at handover.
- Private Cake, a separate worktree of the private Cake repository beside this one, branch `task/ck-lekiwi-deploy` from `68f0911`: `a794687` (absolute
  quiesce deadline), `9775cbe` (build-mobile per-slot children), `34dca51` (task contract). THE CONTRACT IS
  UNRATIFIED: status open, "UNRATIFIED DRAFT", `loop/RATIFICATIONS` untouched. One file there is modified by the gate,
  `evidence/gate1/flight/event-registry.txt` (376 to 377, already stale at `68f0911`, also modified in the main Cake
  checkout); it was left for the Cake owner. The main Cake checkout is untouched on `task/ck-mob-path-a`. The previous
  binaries are backed up with checksums in `.local-dev/console/cake-binaries-68f0911/`.

**Deployed artifacts (robot).** Release `~/kc-lekiwi/releases/20260923T193210Z-602111`, reached through the link
`~/kc-lekiwi/current`; it is the only release (first install). sha256: `cake-resident` `2bb5c2a5...a568d826d3`,
`admin-probe` `8d0bca96...02497c5ed1`, `demo-plan` `59c8a25c...29e9daf5a9ade`, `demo-preflight`
`e740f670...cfaf3ca4b`; each equals the build in the shared Cake target directory. Resident source digest `1545e4b0...`,
build identity `1204d3f725b3339300c5ab9f3c2cf5e06cfb27e7d1dccb38112613b562baeb18`. Capsules: supervisor `395c819e`,
inert `9600566d`, mobile `2f34a84c`. Deployment key id `bcef31cc...`; its seed is in `~/kc-lekiwi/keys` on the robot
only. Unit `~/.config/systemd/user/kiwi-cake-lekiwi.service`, state static (not enabled); the release's own
`resident.conf` names `signature_policy require`, the deployment key, the admin socket `/run/user/1000/kc-lekiwi.sock`
(0600) and the STARTING Plan.

**Configuration locations (ignored, never committed).** Robot: `~/kc-lekiwi/{current,releases,keys,run,logs}` and the
unit file. Desktop, console worktree: `.local-dev/console/lekiwi-env.sh` (the exports `lekiwi-deploy.sh` needs; values
not recorded here), `.local-dev/console/lekiwi-console/{up.sh,runtime-config.json,web/}` (the connected stack),
`.local-dev/console/lekiwi-candidates/` (the two prepared Plans, digest-verified), `.local-dev/console/lekiwi-evidence/`
(install, prove-signature, status, activation, recovery, probe and browser evidence). Main checkout:
`.local-dev/console/runtime-config.json` (the 8770 console) and `.local-dev/console/deploy-20260923/`.

**Commands**, from the console worktree root after `set -a; . .local-dev/console/lekiwi-env.sh; set +a`:
- robot evidence: `bash bin/pi/lekiwi-deploy.sh status`;
- stop the robot resident (and remove any token): `bash bin/pi/lekiwi-deploy.sh stop`;
- start the host, OPERATOR PRESENT AT THE ROBOT ONLY: `bash bin/pi/lekiwi-deploy.sh arm`;
- start the resident UNARMED, as this session did: `ssh "$KC_PI_SSH" systemctl --user start kiwi-cake-lekiwi.service`;
- return the capability to the inert build (rollback, epoch advances):
  `ssh "$KC_PI_SSH" '~/kc-lekiwi/current/bin/admin-probe request /run/user/1000/kc-lekiwi.sock apply-plan --payload ~/kc-lekiwi/current/bundle/plans/mobile-demo.cakepln --uuid $(openssl rand -hex 16)'`;
- there is no previous robot release to roll back to; removing the deployment is `lekiwi-deploy.sh stop`, then
  deleting `~/kc-lekiwi` and the unit file and `systemctl --user daemon-reload` (not done);
- camera service: `KC_WRIST_CAMERA_ID=... bash bin/pi/start-camera-service.sh stop` (the id is the 8770 config's
  `cameras.wrist_id`);
- connected desktop stack (currently stopped): `bash .local-dev/console/lekiwi-console/up.sh`, and `... up.sh stop`;
- the 8770 console rollback: `bash <main checkout>/.local-dev/console/deploy-20260923/rollback.sh`;
- private binaries back to the Path A build: copy from `.local-dev/console/cake-binaries-68f0911/` (checksums beside).

**Processes and listeners at handover.**

| where | process | owner | admission and state |
|---|---|---|---|
| robot | `cake-resident` pid 36142, unit `kiwi-cake-lekiwi.service`, up since 20:34:31 BST | this session | session `01a0cfc332ff...`, epoch 3, Plan `ee7faf62`; admin socket `/run/user/1000/kc-lekiwi.sock` (0600) |
| robot | supervisor slot `supervisor.lekiwi` | Cake | DEGRADED: five launcher attempts refused, no child, NO HOST PROCESS |
| robot | `mobile_approach_service.py` pid 43160, child of 36142 | Cake capability slot, generation 2 | ACTIVE; publishes only on its ipc sockets in `~/kc-lekiwi/run`; nothing consumes them |
| robot | holder slot | Cake | inert build, no child |
| robot | `camera_service.py` pid 42381 | this session | wrist camera only; loopback 5570 (mission) and 5571 (preview) |
| robot | nothing on 5555, 5556, 5557, 5558 | | no host is running; legacy units `kiwi-cake-demo.service` and `cake-resident.service` inactive and disabled |
| robot | `~/kc-perception-feasibility` (1.1 GB, contains `pi-run.sh`) | this session | inert; runs only if a person invokes it |
| laptop | `demo_runtime.daemon` pid 2171918 and `console_api` pid 2171933, port 8770 | the operator's earlier deploy | started 13:02 with `--enable-hardware-commands`, baseline backend; see the 8770 note below |
| laptop | x86 `cake-resident` pid 2176324, `/run/user/1000/cake-admin.sock` | another session | empty Plan, trusts the fixture key; NOT the robot |
| laptop | vite on 5199 and 8786 | sessions of 2026-09-21 | untouched |

Stopped by this handover: the connected stack on 8797 and its three ssh forwards (including the forward of the
robot's admin socket), the feasibility agent and the design-review agent. **The arm token does not exist. It was
never created, so it was never consumed.**

**Refused launcher attempts are not a supervised host.** Cake is proven to launch its supervisor slot's child: it
spawned the host launcher five times, each attempt refused by the launcher itself for lack of an arm token, before the
servo bus was opened, and the slot reports five spawns and DEGRADED. Cake has NOT yet supervised an actual running
robot host in this deployment; that happens only after `arm`. The capability slot, by contrast, is shown running a
real child: its reported child pid is the service's pid and its parent is the resident.

**Shell or browser.** Every mutation was made through the SHELL (ssh to the robot, `admin-probe`, `systemctl`):
install, prove-signature, the unarmed start, the rejected and valid ApplyPlan at 42 s of uptime, the SIGKILL of the
capability child, the rollback ApplyPlan, the resident restart, and a second valid ApplyPlan at about 15 minutes
that left the capability active. The browser only OBSERVED (screenshots `lekiwi-evidence/browser/01` to `03`): in LIVE
mode an activation is bound to a waiting mission, which LIVE refuses, and no `confirm-session` was ever posted.

**The accidental 8770 bundle change.** A build in this session wrote `apps/console-web/build/`, which the 8770 API
serves; since 16:05 it serves bundle `index-ChCLTYRW.js` (from `d023f39`) instead of the deployed `13bd504` bundle.
The 8770 runtime and API are still the processes started at 13:02, whose Python code is `13bd504` (the next commit
landed at 13:43 and was frontend-only). That API has no mission and no robot routes, so the page's mission and robot
reads find nothing; commands remain the earlier surface, enabled by `--enable-hardware-commands`, and motion is
refused by the readiness check while no host runs (inferred from the preflight's process check, not exercised). The
`13bd504` bundle itself is no longer on disk. The known compatible rollback is `deploy-20260923/rollback.sh`, which
restores the pre-cutover configuration and bundle `index-BI0ULoUA.js` and restarts both processes without touching
the robot. The forward-looking alternative is a deliberate cutover of 8770 to the connected configuration.

**The OWLv2 feasibility benchmark (on the robot).** Its pre-frame guard stops when any process holds `/dev/video*`,
`/dev/media*` or a serial device, or is a robot host, camera service or resident. `wireplumber` (pid 1338, the desktop
session, running since boot) held `/dev/media3` and `/dev/media4`, so the one attempt stopped after loading the model
and before the first frame (65.9 to 69.2 C, not throttled). Two attempts to exempt only those handles were DENIED by
the automatic permission review as a security weakening, and were not worked around. The proposed narrow exception,
for the operator to decide: `wireplumber` or `pipewire` may hold `/dev/media*` only; any video or serial handle or any
robot process still stops the run. CURRENT CONFLICTS: the camera service now holds the wrist camera and the resident
and capability are running, so the guard would stop the run anyway; stop them first. Collected (laptop, the
controller's own environment, same model revision and preprocessing, weights hash-identical on both machines):
detector 0.18 s on the GPU and 2.4 s on the CPU; a whole perception frame 12.2 s median (GPU) and 14.7 s (CPU), of
which the pad outline fit is about 91 percent; peak memory about 2.6 GB; GPU and CPU agree to 4.7e-06 in score with
the same decision on 8 of 8 frames; one recorded cycle makes 25 perception calls. Remaining: every robot figure
(load, inference, memory, thermals, the pad fit on one Pi core), the robot-versus-laptop equivalence, and whether a
cycle's quiet perception phase fits the 180 s lease. No verdict exists.

**Not verified.** The wrist panel on the robot stack reported frames arriving while the headless capture showed a
black image area; whether that is the camera's view or the capture is unknown. The disabled state of Start was not
confirmed from the screenshot. That a rehearsal runtime shows generated frames only in Evidence was not re-verified.

**Private test failures.** The canonical `tests/run-all.sh` was red in the worktree layout used (a shared target
directory): each red lane traced to a stale item predating the branch (the event registry, a doc map row), to that
layout (selectors expecting binaries under the worktree's own `target/`), to the then-uncommitted tree, or to the
known flaky `lekiwi-supervisor/tests/capsule.rs` fd-count test (it counts the whole process's descriptors; failed at
lines 1655 and 1777 under parallel threads, passes with `--test-threads=1`, unchanged since `ff97f15`). The new
past-budget selector failed 5 of 5 before the fix and passed 4 of 5 after; its failing symptom ("GetStatus was not
answered after the aborted transaction") appeared only with three residents running at once (1 of 3 at 1 s, 1 of 3 at
33 s of uptime) and never run one at a time (4 of 4), matching the recorded rule that concurrent residents interfere;
it is therefore not wired into `run-all`, an operator decision. A green run needs a standalone checkout with its own
target directory.

**Remaining work.** (1) The mission and controller execution path ON THE ROBOT (settled direction): a robot-side
runtime hosting the mission coordinator and the controller, and a real execution adapter that keeps section 7's
measured predicates and motion envelope; the LIVE refusal stays until then. (2) The perception compute decision, from
the benchmark above. (3) The 8770 cutover or rollback. (4) Physical qualification, operator present: `arm` (the
host's own safe-attach decides; a historical torque-on log says nothing about the arm now), then confirming the robot
in the console; the SIGKILL gap (the base watchdog lives in the host, and the token does not close it); the detector
0.40 validation and the front-camera qualification, each separate.

**Background work.** Every workflow of this session has finished: `wf_d07b1c96-a60`, `wf_4d28310c-0b7`,
`wf_617a6e2b-e6e`, `wf_f30e39b3-a98`, `wf_c5b364fd-3f1`. The agents are stopped. No background shell task remains.
Nothing left running can deploy, arm or benchmark by itself. Resume from this section; the operator's session memory
entry `lekiwi-deployment-correction` supplements it.

## 11. Out of scope here

Front-camera qualification and the 0.40 detector calibration's attended validation are separate and unchanged.
No robot motion belongs to any of the work above. The monitoring-module experiment and the observer replacement
that preceded this are development evidence about Cake's lifecycle and are not part of this sequence.

## 12. Night round, 2026-09-23 (after the handover)

The operator's brief, in its order: (1) reconcile the running console deployments and retire obsolete command paths
before the supervised host is armed; (2) finish the bounded perception benchmark on the robot; (3) close the browser
gaps; (4) prepare the robot-side execution adapter and the arm checkpoint. Times are UTC unless marked local. Raw
evidence is in ignored directories and is named where it is.

### 12.1 The operator console, reconciled

**As found (verified from the process table and the served pages).** The console port ran the runtime and API
started at 11:02 on commit `13bd504`, with `--enable-hardware-commands`, the `baseline_normal_host` backend and no
`cake.robot` section: its Cake status read the laptop's x86 resident, and `/api/robot` and `/api/mission` answered
404. Both processes imported from the LIVE console worktree, so a module first imported after 11:02 would have come
from whatever the worktree held then. The API served the worktree's `apps/console-web/build`, which the accidental
16:05 local build had replaced with bundle `index-ChCLTYRW.js` (`d023f39`). The second port's connected stack was
stopped; its launcher would have started a second LIVE stack on the same admin-socket forward and preview port.
CORRECTION to section 10: the `13bd504` bundle `index-CBFkzo_w.js` was not gone; `apps/console-web/build-dev` still
held it.

**Why it had to go before arming.** That runtime admitted every motion command subject only to its readiness
check, and its readiness probe looks for a process named like the safe-attach host, which the Cake-supervised host
is. Once armed, it would have started a controller on this computer driving the host directly over the LAN, with no
binding to the resident session or the host instance (inferred from the code, not exercised).

**Cutover, 20:25 (local 22:25), after an idle check (no active run, no unresolved command).** A new local deploy
tool in the main checkout, `.local-dev/console/deploy-20260923-night/deploy.sh` (`stage`, `cutover`, `rollback`,
`status`), runs the console only from PINNED RELEASES: a `git archive` of one commit, a web build written to the
release's own directory from a tree verified equal to that commit, and a pinned copy of the admin-probe client. Every
start is WITHOUT `--enable-hardware-commands`, and its readiness check fails the start if the runtime reports them
enabled. The console port now runs release `ead60a6` (bundle `index-CiA3KHYW.js`) on the same data directory, so its
history is kept, and reads the robot's resident through the ssh forward with the build identity pinned. `rollback`
returns to a pinned `13bd504` release with its own bundle and the pre-cutover configuration, also observe-only.
Nothing builds into `apps/console-web/build` any more.

**Retired.** The earlier cutover and rollback scripts (they restart the runtime with hardware commands on the
baseline backend) and the second port's launcher now refuse with exit 64, their original text kept beside them. On
the robot, the two legacy user units were moved aside and the manager reloaded (21:05): both now load as
`not-found`, so nothing can start the v0.1.3 host outside the arm token by name; `lekiwi-deploy.sh` still reads them
as idle. Undo: move the two files back from `~/.config/systemd/user/retired-20260923/` and reload.

**Command authority now.** No desktop runtime admits a controller command. As found, the supervised host, once
armed, would have bound its command port on every interface with the host boundary off, so any process that could
reach the robot on the LAN could push a velocity command; the historical proven runs had the same exposure. The
operator ruled it closed before arming, by loopback binding and without the host boundary: see 12.2. Anyone holding
the robot account's ssh key can still write the arm token or start a host outside Cake; that trust boundary is the
account and its key, as section 8 says.

### 12.2 The host's command socket on loopback (operator ruling, deployed, not yet exercised by a host)

**What changed (`8e78b48`).** lerobot's host binds its command socket on `tcp://*` and has no setting for the address,
so the smallest change is in our safe-attach wrapper: a new delimited block, off unless `KC_HOST_COMMAND_LOOPBACK=1`,
wraps the host constructor. After the stock constructor it replaces the command socket with the same kind (PULL,
CONFLATE 1) on the same port bound to `127.0.0.1`, reads the bound endpoint back, and ends the process before the
host loop on anything else; it refuses a command socket another block already replaced, so it is never combined with
the host boundary. The observation socket keeps its stock binding, and the camera service's ports are untouched. The
Cake-supervised `run-host.sh` exports the variable; nothing else sets it. The demonstrated wrapper body's digest is
unchanged with the block cut out, and the block has its own pin.

**Checked twice when the host starts.** `lekiwi-deploy.sh arm` refuses before writing a token unless the deployed
`run-host.sh` exports the variable and the deployed wrapper carries the block (the CONFIGURED binding); after the
host starts it reads the ACTUAL listener from the kernel with `ss`, and stops the unit on any 5555 listener that is
not loopback or on a table it cannot read (the arm keeps holding: the host never drops torque on disconnect). A first
exposed reading is read again one second later, because the stock constructor binds every interface for the
milliseconds before the wrapper replaces that socket, and no command is read from it. `status` prints both.

**Desktop forwards (`0fbd44c`).** A forward naming the raw command port (5555, or the configured one) is refused as a
state and never spawns ssh; no existing forward carried it.

**Proven, and not yet proven.** A functional test runs the block with real pyzmq against a stub of the stock
constructor and reads the listeners from the kernel's table. On the robot, lerobot's own host class with the robot's
pyzmq, on spare ports and without opening any device, listed the command port on `127.0.0.1` only, received a command
pushed over loopback, and refused a connection from the LAN while the observation port accepted one (evidence:
`.local-dev/console/lekiwi-evidence/loopback-proof/`). Deployed at 21:25 as release `20260923T212456Z-964007` from a
`git archive` of `0fbd44c`: the rendered `run-host.sh` exports the variable, the resident runs unarmed (session
`01a0d028...`, Plan `a6a4ca73`, epoch 3 after the recorded ApplyPlan), the host is held, and nothing listens on 5555.
The first real host start under it is the operator's `arm`, which reads the listener back.

**Review and correction (`a882b80`).** An independent review reproduced the binding with lerobot's own host class and
found three things, all fixed: wording that implied commands could only arrive through the robot-side service's
checks; a read-back that could end without a verdict after an unreadable or exposed first reading; and a foreign
`127.0.0.1:5555` listener being taken for the host. `arm` now keeps reading an unreadable table, clears an exposed
reading only with a clean loopback one, and requires the listener to belong to the supervised host (the safe-attach
wrapper process whose parent is the unit's MainPID). The robot runs the release built from `0fbd44c`; the two differ
from `a882b80` only in comments, and the read-back logic runs from the laptop's `lekiwi-deploy.sh` at `arm`, so `arm`
must be run from `a882b80` or later.

**What this is not.** Loopback decides who can REACH the command socket: processes on the robot, and anything an ssh
forward made on the robot's side hands to it. The host checks nothing itself: any process on the robot that reaches
the socket can push to it, and so can any holder of the robot account's ssh key through a forward of their own. The
session, ownership and admission checks apply only to commands routed through the robot-side service, which does not
run on the robot yet. The observation port still answers on the LAN; its PUSH socket shares observations among every
connected reader, so a desktop reader would take a share of what the robot-side controller should receive.

### 12.3 The perception benchmark on the robot

**Authorization.** The operator approved a narrow exception for this benchmark only: WirePlumber or PipeWire may hold
`/dev/media*` nodes; any camera or serial handle, any other media holder and any robot workload still stop the run.
It went through the normal approval path and was not refused.

**The harness (local, ignored: `.local-dev/console/owlv2-feasibility/`, v1 kept beside it).** It runs the
controller's unchanged per-frame path on the eight retained wrist frames: image read, the illumination statistics,
`observe_frame` (OWLv2, cube recognition, the pad outline fit at the three focal-sweep factors, the template
localizer), the sidecar, the overlay, the summary and the read-back, exactly as the single-cube cycle calls them.
Before anything is imported it checks the perception sources and `perceive()` against their hashes at `ead60a6`,
the thresholds against the controller's, and the model against revision `cfd3195` and every file hash. It opens no
camera, serial or media device, touches no lease and no host. Its guard runs before the imports, before the model
load, before every frame and after the last; it matches robot workloads on the whole command line and identifies the
exempted media daemons by executable, and writes every exempted handle into the result.

**The window.** 20:41 checks: no arm token, no host, nothing on the host ports, console hardware commands off, no
active run, no controller on either machine. 20:42 the camera service and the resident stopped through their normal
scripts; afterwards only the session's WirePlumber held `/dev/media3` and `/dev/media4`. 20:42 to 20:59 the full run,
two passes; 21:00 to 21:05 a one-pass replay; 21:05 the recorded unarmed state restored (below).

**Results (steady state, first frame excluded; laptop runs at four threads to match the robot's cores).**

| | robot, Pi 5, torch 2.11 | laptop CPU, torch 2.7.1 | laptop CPU, torch 2.11 | laptop GPU (v1) |
|---|---|---|---|---|
| whole per-frame pipeline, median | 62.8 s (p90 71.2, max 73.7) | 14.6 s | 14.6 s | 12.0 s |
| OWLv2 detect | 25.2 s | 2.9 s | 2.8 s | 0.19 s |
| pad outline fit, three per frame | 36.0 s (11.9 s each) | 11.1 s | 11.4 s | |
| template localizer | 1.10 s | 0.31 s | 0.31 s | |
| model load | 1.9 s | 1.6 s | 1.3 s | 2.8 s |
| peak memory | 1.8 GB (2.4 GB per-frame high water) | 1.9 GB | 1.7 GB | |
| one recorded cycle, 25 perception calls | 26 min | 6.1 min | 6.1 min | about 5 min |
| a four-frame quiet phase | 251 s (worst 295 s) | 58 s | 58 s | |

The robot throttled within about two minutes of load: 91.1 C peak, 85 C median, the arm clock capped at 1.5 GHz
most of the run and at 1.0 GHz at worst (`get_throttled` 0xe000e: soft temperature limit, frequency capping and
throttling, now and since boot). At least 6 GB of memory stayed available throughout.

**Equivalence.** Every decision agrees on all eight frames between the robot and both laptop runs: the detector's
label set, the cube's detected flag and label, each pad fit's result, the template's PAD_FRAME, and every
inside-the-pad flag. Largest continuous differences: detector score 4.7e-06, box 0.0012 px, camera height 0.013 mm,
cube centre in the pad frame 0.16 mm, all inside tolerances fixed before the robot ran. Both passes on the robot were
identical. **The torch versions:** 2.7.1 against 2.11 on the same laptop changed scores by at most 2.4e-06 and nothing
downstream. The replay fed the laptop's own detections through the robot's downstream stages and reproduced the same
0.16 mm, so that spread is floating point in the pad fit on the robot's architecture, not the detector and not torch.

**Limits of these figures.** They are the BEST case: the host, the camera service, the resident and the capability
were stopped, and in operation they share the same four cores. Processes of another account on the robot (a second
desktop session among them) cannot be inspected without root; the benchmark itself opened no device.

**Verdict.** On the robot as it is today, the unchanged perception does not fit the recorded cycle: about four times
the laptop's per-frame time, a four-frame quiet phase of 251 s against the 180 s host lease (which was not changed),
and sustained thermal throttling. Memory is not the constraint.

**Placement recommendation (a decision for the operator, not taken here).** Keep Cake, the mission and controller,
motion authority and camera ownership on LeKiwi, and grant ONE explicit offboard exception: the `observe_frame`
computation (OWLv2 and the pad localization) runs on the desktop's GPU as a stateless service the robot-side
controller calls with a frame from the robot's own camera service. Each answer names the model revision and the
frame's digest; a missing, stale or mismatched answer makes the robot-side controller hold, never move. On-robot
alternatives each need their own decision and measurement: active cooling removes the throttling but not the
fourfold gap; running the three focal-sweep fits in parallel preserves the computation but is a perception code
change that needs its own equivalence proof. By arithmetic on the numbers above (not measured), both together would
still leave a four-frame quiet phase near 130 to 160 s before the co-resident load, with no margin to the lease.

### 12.4 Start, Stop and activation in the console (`f3729f3`, `5ca8bc0`, `f584ef5`)

**Runtime.** One cancel event per mission, checked before every step the machine takes and before any activation
attempt. One Stop path for `/api/stop`, `/api/mission/stop` and a stop command: the supervisor's existing guarded
stop first (SIGINT to the controller pid only; never the host or the resident; no Cake operation), then the mission.
Terminal mission states stay terminal: a late activation answer is recorded and never resumes the mission.
Overlapping activations are refused. Every activation record carries the transaction and the capability slot read
back before and after it; only that read-back makes an activation OBSERVED. An ABORTED answer now states only what
is known ("the answer does not say why"): the earlier text claimed a cause section 8 shows can be false.

**Browser.** Start addresses `POST /api/mission/start` and is disabled in LIVE with the runtime's reason shown. Stop
reports what the runtime said it stopped. The capability request sits in the Cake panel with the slot read-back.
A camera reads LIVE only when its picture element has decoded a frame, and an unchanged picture is a note, never an
"ended" stream. A rehearsal draws no stored frame in the camera panels. The drawer is Diagnostics, with the History,
Logs, Evidence and Connections tabs; Field bindings has no entry point.

**Browser evidence** (`.local-dev/console/lekiwi-evidence/browser-night/`, read from the DOM by
`tools/console-dom-probe.js`, not from screenshots):
- 8770, LIVE, release `f584ef5`, connected to the robot: Start `disabled` and `aria-disabled="true"` with its reason
  shown (`mission_unavailable`); Stop enabled; Observe and Return arm disabled with their reasons; the wrist chip
  LIVE, "decoded in this tab", the decoded picture drawn into a canvas with mean luminance 146.9 and 1846 distinct
  colours, and the screenshot shows the gripper, a cube and the mat. The earlier black capture was timing.
- 8799, an isolated rehearsal against a fresh x86 fixture resident, driven by clicks on the real controls: Start,
  then Activate on the unadmitted candidate (ABORTED, slot generation 1 to 1), then on the prepared one (OBSERVED,
  COMPLETED, capsule `87dee37b`, generation 1 to 2, epoch 2 to 3); the mission ended COMPLETE (synthetic, no motion).
  The persisted record and the resident's own `list-slots` agree. The camera panels drew no image. This run pinned
  no decision frame, so a GENERATED frame in Evidence was not shown here; the frontend's verifier showed it in its
  own run.
- FINDING: the x86 fixture resident reports build identity `1204d3f7...`, the value 8770 pins. That is expected:
  a build identity identifies the software, and both residents were built from the same source. Which deployment
  8770 observes is established by its authenticated ssh connection to the intended robot's admin socket and that
  endpoint's current resident session, not by the pin.

### 12.5 The physical execution route (operator ruling; `064c0f6` to `ad28364`)

The proven single-cube controller is the mission's physical execution; the hybrid machine stays the rehearsal's.
The seams are the ones the ruling names: target binding (`064c0f6`) so a second execution stays on the unresolved
target and cannot finish on the placed cube; enumeration of every verified cube without changing perception
(`1589558`); the capability relay (`e58db22`), through which a forward base pulse of a capability target comes only
from the Cake-launched capability's pid-labelled, timestamp-matched output through the bounded gateway; the hooks in
the controller (`df3eb4e`, inert without a target file; the behaviour-preserved test is green); the supervisor's
mission run, `run_proven_target`, the refusing hybrid adapter and the LIVE builder SHIPPED OFF (`48f7079`); a
pinned release and an unenabled user unit for a runtime on the robot (`ad28364`, not deployed).

**GRASP_POSITION, recorded and refused.** The hybrid machine asks `move_arm({shoulder_pan -10.549, shoulder_lift
43.121, elbow_flex 54.286, wrist_flex -33.714}, 2.0, 0.0)`: an absolute joint pose, pan included, with no
Cartesian delta. Section 7 lets only requested deltas reach the proven builders, so `refuse_hybrid_arm_request`
refuses it before any read or send. The incompatible precondition: the hybrid path expects a person to place the
cube between the jaws and answer CUBE READY, while the proven cycle reaches a grasp only through perception, a
non-contact pregrasp, ALIGNED and bounded approach steps.

**Adopted defaults, for the operator to confirm or change.** D1 the capability gate is passed before the capability
target's execution, never with a cube held (two-cube-mission-design.md held at HOLD_OBSERVE with the cube). D2 the
relay publishes the synthetic observation x 1.0 m. CORRECTION (2026-09-25): no speed here is a qualified hardware
speed. That synthetic observation has only ever reached test fakes. The section 4 fixture (`x_m` 0.95) produced
0.10 and 0.06, which the gateway clamped to 0.05 and 0.05 into `FakeBaseMotionTransport.last_sent`: software
behaviour. 0.05 m/s has run on hardware only through the proven in-process pulse primitive
(physical-pass-2026-09-18.md), which is not the capability path. The operator ruled the synthetic observation out
of LIVE execution; see section 12.8 for what the physical path needs instead. D3 claim and match radii 30 and 40 mm.
D5 an arm-only target that measures a base advance is refused. D6 the base returns by the proven reverse.
Undecided: D4 staging, D7 perception placement (12.3), D8 whether another account on the robot makes the host
boundary necessary.

**Known residuals.** A stop during an arm move is recorded as a perception abort rather than a step abort
(evidence only). The pid label is a correlation check, not authentication. In the rehearsal the near cube still
passes through the hybrid machine's base step once. Stop completes only after the perception frame in flight, up to
about 74 s on the robot.

### 12.6 State at this handover (2026-09-25)

The robot rebooted while unattended and came up with nothing running; the recorded unarmed state was restored at
10:53: resident session `01a0d832...`, Plan `a6a4ca73`, epoch 3, host held, capability generation 2, camera service
wrist only; no arm token. 8770 runs release `f584ef5`, observe-only; rollback `deploy.sh rollback` (to `ead60a6`).

### 12.7 The next operator action: arm, with the operator at the robot

Prepared: the command socket goes to loopback on the first host start and `arm` checks it; the obsolete desktop
command paths are retired; the robot's legacy units are gone; no desktop runtime admits a motion command.

Run from the console worktree at `a882b80` or later (the read-back logic travels with the script), with the robot on
its stand and a hand near power:

    set -a; . .local-dev/console/lekiwi-env.sh; set +a
    bash bin/pi/lekiwi-deploy.sh arm

It stops the unit, writes the one-shot token and restarts it. The wrapper claims the token and checks the servo bus
and ports; the safe attach verifies the arm by reads and holds it (it refuses on a wheel turning or a joint without
torque). `arm` then reads the command listener from the kernel, requires it on `127.0.0.1` and owned by the
supervised host, and stops the unit otherwise; the arm keeps holding. To end: `bash bin/pi/lekiwi-deploy.sh stop`.

**What it establishes, if it passes:** the host attached under Cake with the command socket on loopback only, the
supervisor slot running a real host, and a host instance the console can show and the operator can confirm.

**What stays disabled:** LIVE missions (`mission.execution` unset, the robot-side runtime not deployed), every
desktop hardware command (8770 observe-only), and any controller anywhere.

**What it does not establish:** safe stop on SIGKILL (the base watchdog lives in the host; the token does not close
that gap); command authority on the robot (the host checks nothing itself); and the separate verdicts that follow
only when the operator is present: detector 0.40, camera qualification, manipulation and Cake continuity.

### 12.8 Round three (2026-09-25): the perception exception, the physical capability path, the robot checks

**Perception placement (operator decision).** One explicit exception: the complete frame-perception operation (the
unchanged `observe_frame`, OWLv2 and the pad fitting, the existing model, preprocessing and thresholds) may run on
the desktop GPU. Everything else stays on LeKiwi. Built in `d611029`: a service on desktop loopback reached by the
robot through a reverse ssh forward the desktop owns; every request carries the robot's capture identity, resident
session and run, mission and target ids, and the code and model pins; a reply is accepted only before the robot's
own deadline, with the same request id, the identities echoed byte-equal and the digests recomputed on the robot.
Anything else is a refusal, so a missing, stale, mismatched or late answer never authorizes motion or resumes a
stopped mission. The frozen perception sources and `perceive()` are byte-identical and pinned. The lease is
unchanged. NOT YET PROVEN ON THE ROBOT: `.local-dev/console/offboard-perception/proof.sh` (robot-originated
captures, the real desktop operation, results read back on the robot with the full round trip, plus the timeout,
late and stop cases, with the host held) refused at its first step at 18:4x CEST because the robot stopped
answering over the network. It is ready to run unchanged.

**The physical capability path (`0585758`).** The synthetic observation is rehearsal-only, structurally absent from
LIVE. The capability must be fed an actual observation of the selected target through its existing contract, and
none can be produced today: its 200 ms freshness bound against perception that takes seconds, no recorded hand-eye
or arm-to-base calibration for its frame, no mapping from perception scores to its quality field, and the proven
advance being a cube-held move toward the pad. That seam therefore REFUSES in LIVE (section 7). The capability gate
now follows the measured placement mode at SEARCH, before any movement that needs the capability.

**The 30 mm and 40 mm radii.** R_claim decided whether a seen cube is the one an earlier target placed; R_match
decided whether a SEARCH candidate is the target bound on the first execution. No measured bound in the repository has
either meaning, so the defaults were removed and every LIVE mission target is refused until both are configured from
a validated value. The second execution cannot finish on the cube already on the pad (tests; named residual: the
attribution can swap if the second cube lands within the claim radius of where the first was recorded while the
first was knocked away).

**Robot-connected browser check (`e4e25e9`, deployed on 8770).** A non-motion capability check drives the existing
activation controls against the LeKiwi resident with no mission, only while no mission or run is active and the
supervisor slot runs no host; "held throughout" compares the slot instance, generation, spawns and restarts at both
reads. NOT YET RUN against the robot for the same reason.

**The reboot (item 5), and a second loss.** Cause UNKNOWN: journald keeps only the current boot and
`/var/log/journal` is empty; the clock was restored at boot by timesyncd to Sep 23 23:52:38 BST, the last time it
was known running; the robot booted Sep 25 10:30 BST; `throttled=0x0` and 5.08 V on that boot. No arm token existed,
the resident session changed and the console showed the robot session unconfirmed: nothing restored motion
authority. The robot was reachable until about 13:05 CEST on Sep 25 and not from 18:45 CEST; whether it rebooted
again or left the network is unknown until it answers (its boot id before was `21557a61...`).

**Before the attended arm step, in order.** (1) Bring the robot back on power and network. (2) Run the perception proof
above. (3) Restore the unarmed state if it rebooted (`window-20260923/restore.sh`), then drive the capability check on
8770 (invalid candidate: slot unchanged; valid Plan: observed change, no resident restart, host held throughout). (4)
Check the host's physical preconditions fresh (`lekiwi-deploy.sh check` and `status`). Only then the arm step of 12.7.
The arm step still establishes only the host attaching under Cake with the command socket on loopback; the physical
approach stays unavailable (above), and detector 0.40, camera qualification and safe-stop/recovery (the SIGKILL gap)
remain separate proofs.

## 13. Final handover (2026-09-26)

Nothing below was freshly checked on the robot for this handover: every robot state is LAST OBSERVED, with its time.
The operator REPORTS that the robot's power was restored on 2026-09-26. That report is recorded here as a report; it
is not evidence of connectivity, of a running service, or of readiness. No robot service was changed and no
experiment was started for this handover.

### 13.1 Revisions

- **Public, branch `feat/operator-console`** (the console worktree, not pushed; tree clean). This round, oldest
  first: `8e78b48` `0fbd44c` (host command socket on loopback; raw command-port forwards refused), `5e4726b`
  `8f1600b` (docs), `a882b80` (arm read-back fixes), `8d1b093` (docs), `064c0f6` `1589558` `e58db22` `df3eb4e`
  (target binding, cube enumeration, capability relay, controller seams), `f3729f3` `5ca8bc0` (Stop and activation
  runtime; neutral ABORTED detail), `f584ef5` (console Start/Stop/cameras/Evidence), `48f7079` `ad28364` (proven
  controller as the mission's execution, shipped off; runtime release tool), `f659e78` (docs), `d611029` (desktop
  perception exception), `0585758` (no synthetic input in LIVE; gate on measured reachability; radii unavailable),
  `e4e25e9` (non-motion capability check; identity wording), `738e1bd` (docs, section 12.8), and this commit.
- **Main checkout, branch `dev/two-cube-flagship`**: not changed this round (only its ignored `.local-dev`).
- **Private Cake**: branch `task/ck-lekiwi-deploy` at `34dca51`, unchanged this round (section 10).

### 13.2 Deployed releases

- **Robot (last observed 2026-09-25 13:05 CEST):** release `20260923T212456Z-964007`, built from `0fbd44c` (the loopback
  binding; it differs from `a882b80` only in comments), is `~/kc-lekiwi/current`; the earlier release
  `20260923T193210Z-602111` is kept. Unit `kiwi-cake-lekiwi.service` installed, NOT enabled, so after a power loss it
  does not start by itself (INFERRED from its state; not checked since). The legacy units were moved to
  `~/.config/systemd/user/retired-20260923/`.
- **Desktop 8770:** release `e4e25e9`, observe-only (no `--enable-hardware-commands`), from the pinned-release tool in
  the main checkout, `.local-dev/console/deploy-20260923-night/deploy.sh`; recorded rollback target `f584ef5`; staged
  releases `13bd504` `ead60a6` `f584ef5` `e4e25e9`. Last observed running at its cutover on 2026-09-25 about 18:40
  CEST; its ssh forwards to the robot die when the robot is unreachable and are not retried by design.

### 13.3 Service states, last observed

| where | what | last observed |
|---|---|---|
| robot | resident, unarmed, session `01a0d832...`, epoch 3, Plan `a6a4ca73`; host held (supervisor slot DEGRADED); capability generation 2; camera service wrist only | 2026-09-25 13:05 CEST; unreachable from at least 18:45 to 21:51 CEST |
| robot | arm token | none (2026-09-25 13:05 CEST) |
| desktop | 8770 runtime and API on release `e4e25e9` | 2026-09-25 ~18:40 CEST |
| desktop | rehearsal stack on 8799, offboard perception service | stopped by this session |
| desktop | background jobs and workflows of this session | none left running |

Expected after the reported power restoration, NOT verified: the robot booted with a new boot id (the previous one
was `21557a61...`), nothing of the deployment is running, no arm token exists, and any motion permission is void
(the resident session will be new; 8770 binds permission to the session and host instance).

### 13.4 Evidence locations (ignored, never committed)

Console worktree `.local-dev/console/`: `lekiwi-evidence/{browser-night,loopback-proof,loopback-release,reboot-check}/`,
`owlv2-feasibility/` (harness, `results/pi/`, `results/analysis-night.json`, `results/cmp-*.json`,
`window-20260923/`), `offboard-perception/` (`proof.sh`, `dry-run.sh`, the dry run's output), and
`browser-rehearsal-night/stack.sh`. Main checkout `.local-dev/console/deploy-20260923-night/` (releases, cutover logs,
backups).

### 13.5 Remaining work

1. **The two pending robot proofs, both non-motion and both ready:** (a) the desktop perception proof: robot
   captures, the real desktop operation, results read back on the robot with the full round trip, plus the timeout,
   late and stop cases, with the host held; (b) the robot-connected capability check in the 8770 browser: the invalid
   candidate leaves the slot unchanged, the valid prepared Plan gives the observed slot change with no resident
   restart, and the host is held throughout. The capability must first be returned to the inert build for (b).
2. **The capability's input contract cannot be met today:** its 200 ms freshness against perception that takes 12 s
   (desktop GPU) to 63 s (robot) per frame; its coordinate frame, for which no hand-eye or arm-to-base calibration is
   recorded; and its quality field, to which no perception score is mapped. The LIVE seam therefore refuses; closing
   it needs a qualified observation source, not a relaxed check.
3. **Target association is unresolved for LIVE:** the claim and match radii have no measured bound and are
   unconfigured, so every LIVE mission target is refused; named residuals are the attribution swap (12.8) and a
   respawn binding that rests on there being exactly one cube of a mode. Decisions D1 to D8 (12.5) remain open.
4. **LIVE motion stays disabled:** `mission.execution` unset, 8770 observe-only, the robot-side runtime not deployed,
   no controller anywhere.
5. **Separate physical qualifications, each its own proof:** host attachment under Cake (`arm`, 12.7, which also
   reads the loopback listener back); physical approach qualification; detector 0.40; camera qualification; and
   safe-stop and recovery, where the SIGKILL gap stays open (neither the token nor loopback closes it).
6. **The unattended outages:** the robot went dark twice; the cause is unknown (12.8). Qualify power and network
   before any attended session.

### 13.6 Resumption commands

From the console worktree root, after `set -a; . .local-dev/console/lekiwi-env.sh; set +a`:

- connectivity and boot, read-only: `ssh -o BatchMode=yes "$KC_PI_SSH" 'cat /proc/sys/kernel/random/boot_id; uptime -s'`
- deployment state, read-only: `bash bin/pi/lekiwi-deploy.sh status`
- restore the recorded unarmed state (resident unarmed, capability generation 2, camera service; never a token):
  `bash .local-dev/console/owlv2-feasibility/window-20260923/restore.sh`
- reopen 8770's forwards once the robot answers: `bash <main checkout>/.local-dev/console/deploy-20260923-night/deploy.sh cutover e4e25e9`
  (status: `... deploy.sh status`; rollback: `... deploy.sh rollback`)
- proof (a): `bash .local-dev/console/offboard-perception/proof.sh`
- before proof (b), the capability back to inert (non-motion, recorded command):
  `ssh "$KC_PI_SSH" '~/kc-lekiwi/current/bin/admin-probe request /run/user/1000/kc-lekiwi.sock apply-plan --payload ~/kc-lekiwi/current/bundle/plans/mobile-demo.cakepln --uuid $(openssl rand -hex 16)'`,
  then the capability check in the 8770 Cake panel, read with `node tools/cdp_page.mjs --url http://127.0.0.1:8770/ --wait LeKiwi --script tools/console-dom-probe.js --after 9000`
- physical preconditions, fresh: `bash bin/pi/lekiwi-deploy.sh check` and `status`
- only then, operator at the robot, the arm step of 12.7 (from `a882b80` or later)

## 14. Round four (2026-09-26): the robot back, the two proofs, the observation contract closed

### 14.1 The robot after the power restoration (verified 10:11 UTC)

A new boot id (`ce46656b...`; the previous one was `21557a61...`), booted at 09:59:47 UTC (`uptime -s`; the journal's
first entry shows the pre-synchronisation clock, 2026-09-25 17:24 BST, the last time it was known running), throttled
`0x0`. Nothing of the deployment was running: no resident, no host, no camera service; the unit static and inactive;
NO arm token; two stale ipc socket files in `~/kc-lekiwi/run`. The desktop's 8770 runtime (release `e4e25e9`) had
reopened its admin-socket forward by itself at 10:00:15 UTC. Nothing restored motion authority: the resident session is
new, no token exists, and 8770 reports `robot_session_not_confirmed`.

The recorded unarmed state was restored at 10:13 UTC with `window-20260923/restore.sh`: resident session
`01a0dd34...`, Plan `a6a4ca73`, epoch 3 (ApplyPlan COMPLETED), capability generation 2, supervisor slot DEGRADED after
five refused spawns (host held), camera service on the wrist camera only (pid 4025, 1280x720 MJPG, 31 fps measured).

### 14.2 Proof (a): robot capture, desktop perception, associated result (PASSED 10:21 UTC)

`.local-dev/console/offboard-perception/proof.sh` at HEAD `1af92d4` (runtime release `1af92d40fe4b-20260926T101824Z`
staged here and copied to the robot; the desktop service from that release on the GPU python, owning its reverse
forward to the robot's loopback port 5590). Its first attempt refused at the first case: the robot-side probe ran under
the robot's base miniconda python, which has no scipy (the cube enumerator imports the pad localizer). The local proof
script now runs the robot side under the benchmark venv (`~/kc-perception-feasibility/venv`, the interpreter the
robot/laptop equivalence was measured with: numpy 2.2.6, cv2 4.12, scipy 1.18, pyzmq 27, torch 2.11) with a preflight
import check. Second attempt, six of six cases as expected, host held at every read (eight bindings, `host_running`
false, session `01a0dd34...` unchanged), the listener on `127.0.0.1` only:

| case | expected | got |
|---|---|---|
| normal, 5 robot-originated wrist captures | accepted | 5 of 5 accepted; listing pass 1 matches the record for all |
| replay (an old answer offered to a new request) | request_id_mismatch, then capture_mismatch | exactly those |
| missing (a port nobody listens on) | deadline | deadline after 2.02 s |
| timeout (2 s short deadline) | deadline | deadline after 2.02 s; the service was free again 11.0 s later |
| late (the answer arrives after the robot's deadline) | late, nothing written | late by 10.5 s, no result written |
| stop (cancel while a request is outstanding) | cancelled, then the next request accepted | cancelled in 101 ms; next accepted |

Timing on the robot's clock: full round trip (capture to accepted result) p50 13.6 s, p90 15.2 s, max 15.2 s; send to
accept p50 13.1 s; desktop perceive p50 12.6 s, max 14.3 s (desktop clock). Each accepted record places the cube in
PAD_FRAME by the pad's print landmarks (73 to 77 inliers, rms 0.85 to 0.97 px; the cube at about x -0.033 m, y -0.165
m, outside the pad outline). Evidence: `proof-20260926T101822Z/{probe.txt,service.log,robot-evidence/}`; the robot
keeps its copy under `~/kc-offboard-perception/evidence/20260926T101822Z/`.

### 14.3 Proof (b): browser-driven activation against the LeKiwi resident (PASSED 10:24 UTC)

Preconditions. The 8770 runtime had no prepared candidates (`no_activation_candidate_prepared`): release `e4e25e9`'s
configuration named none. The two candidate Plans on the desktop were the 2026-09-23 19:32 release's, whose digests no
longer matched the robot's current release; `lekiwi-deploy.sh fetch-candidates` refetched them sha256-verified
(swapped `63049075...`, abort-swap `1f4642c5...`; the old copies are kept beside them). The staged release's
`runtime-config.json` gained `cake.activation_candidates` (backup kept; the deploy tool's stage step now writes it for
every future release), and 8770 was cut over to the same release `e4e25e9` again (runtime session
`rt-20260926T102222Z`, observe-only). The capability was returned to the inert build first with the recorded
ApplyPlan of `mobile-demo.cakepln` (epoch 4, capability slot inert at generation 3, host still held).

Driven in the operator's own Chrome tab, through the card's real controls (the `prepared Plan` select and `Apply this
prepared Plan`), the runtime bound each press to the resident session shown on the card:

| press | resident answered | slot before, after | host held throughout | session changed | from press to last read |
|---|---|---|---|---|---|
| `mobile-demo-active-successor-abort-swap` | ABORTED (REJECTED_BY_CAKE) | inert gen 3 inst ...483, unchanged | yes (DEGRADED, child 0, spawns 5, restarts 4 at both reads) | no | 313 ms |
| `mobile-demo-swapped` | COMPLETED (OBSERVED) | inert gen 3 to mobile capsule gen 4, new instance ...828, epoch 4 to 5 | yes (same counters) | no | 176 ms |

The robot's own read-back afterwards: session `01a0dd34...` unchanged, resident MainPID 3793 unchanged (started at the
10:13 restore: no resident restart), capability slot on the mobile capsule at generation 4, capability child pid 8138,
supervisor DEGRADED with child 0, no arm token, no listener on 5555 or 5556, camera service untouched. Evidence:
`.local-dev/console/lekiwi-evidence/capability-check-20260926/` (the runtime's records before and after, the check
journal, `/api/robot`, five browser screenshots, the panel text, the robot read-back).

### 14.4 The capability-observation interface, closed by contract v2 (`4fd28ff` to `800650f`)

The version 1 contract could not be met by the pipeline that exists, for four reasons read in the code: a 200 ms
freshness bound against perception that takes seconds; a "base" frame that meant a body-camera floor homography never
related to the wrist camera or to anything the placement measures; a graded quality nobody measured; and, found in
this round, the command expiry: the capability stamps every command with the observation's own stamp, so with a
seconds-old capture every command would expire on arrival at the stable motion gateway, and with any capture every
command after the first of a pulse would be rejected as replayed (same timestamp). The version 1 wiring had never
produced a sustained pulse; it had only ever reached test fakes.

Version 2 (`kc.target_observation/2`, `autonomy/schemas/target_observation.v2.schema.json`, eleven fields, nothing
else; `docs/autonomous-demo/mobile-approach-spec.md` section 10) is built around the proven bounded stop-observe-move
behaviour and changes none of its gates:

- **Frame and quantity.** `anchor_reach`: origin the held cube's anchor in PAD_FRAME from the latest stationary pad
  observation, x the measured reach axis. `x_m` is the executor's own remaining bounded advance
  (`base_advance_needed_mm` / 1000, 0 to 0.120 m), `y_m` is 0 by construction. No transform was invented: PAD_FRAME
  is the metric reference the placement already measures against, and the executor already derives the anchor and
  the axis from it.
- **Freshness.** `capture_ns` (read before the capture) and `published_ns` (the relay's now at each publication, same
  clock) travel separately; the command echoes `published_ns` and anchors its expiry to it, so successive commands
  are strictly newer and never expired on arrival. Scene invariance rests on `motion_epoch` and `stationary`; the age
  bound `max_age_ns` is derived per observation from the run's perception deadline plus the relay's bounded waits.
- **Quality.** None graded: the frozen pipeline's validity predicate is binary and an observation that fails it is
  never published; the fit's inlier count and residual travel as evidence.
- **Semantics.** The approach target is the held cube's placement admission point (decisions D1 and D6): the target
  that requires the capability is the one whose placement measured BASE_ADVANCE.
- **The capability's configuration** (`ControllerConfig.contract_v2()`) copies the executor's constants, pinned equal by
  a test: the forward speed saturates at 0.05 m/s for every remaining advance beyond the 8 mm tolerance, the envelope
  is the 120 mm bounded advance, the handoff is the tolerance, no deadband. The executor still bounds every pulse's
  duration, confirms the stop, reads the base zero, checks the arm's drift and measures the displacement in PAD_FRAME
  before the next observation. The Cake-launched capability remains the only producer of the forward velocity.
- **The executor** (`1d774c8`) keeps the approach state advance_base pulses on, takes one stamped capture after the
  HOLD_OBSERVE rise for a run that carries the capability (a run_pose capture is never stamped, and the grasp's capture
  is of a past epoch), records whether the base read zero at every capture, and counts the commands the gateway
  APPLIED beside those the relay accepted. Placement and completion are decided by the proven MEASURED predicates
  (object secured, placed by the exit view's margin, base returned, attribution); an applied command is one more
  necessary condition for a capability target, never a sufficient one: APPLIED says the gateway dispatched a
  velocity, not that a cube was placed (result document version 2).
- **Association** (`4fd28ff`): both radii are half a cube edge, 12.7 mm: a CHOSEN association rule, not a measured
  localization bound, with the basis stated in the code and checked against the retained examples; an identity the
  rule cannot settle is refused (5.2 mm and 0.3 mm re-readings, 1.6 mm hem effect, base return within 2.7 mm; two
  touching cubes' centres 25.4 mm apart; the two-cube scene's placed and BASE_ADVANCE cubes about 55 mm apart). The
  configuration word `cube_half_edge` names them; absent still refuses.
- **The probe** (`8377543`): `approach_probe.py --contract v2 --record PATH` derives a v2 observation from a real stored
  perception record and drives the capability Cake launched, without motion.

Gates on the final tree: autonomy 1108 tests OK (5 skipped), contracts 987 OK, `tests/run-all.sh` 17 of 18 with
`test-docs` at its pre-existing two-failure baseline. The adversarial verifiers of each work package passed after a fix
round; three further defects were fixed by hand (identifiers matched whole in the v2 reader; two version 1 wordings
in the runtime).

### 14.5 The v2 path connected on the robot, without motion (12:11 to 12:14 UTC)

- **Robot release** `20260926T121114Z-463057`, installed from a git snapshot of `800650f` with `lekiwi-deploy.sh install`
  after `stop` (the unit stopped, no token; the previous release `20260923T212456Z-964007` kept and untouched; the
  deployment key kept; the four aarch64 Cake binaries byte-identical to the previous release's, so the build identity
  the console pins, `1204d3f7...`, is unchanged). The wrapper rendered on the robot launches the capability with
  `--contract v2`. Nothing was started or enabled by the install.
- **Restored unarmed** on the new release: resident session `01a0dda1...`; the release's prepared swapped Plan applied
  (COMPLETED, epoch 3); capability slot on the mobile capsule at generation 2, launched pid 25612, its log
  `mobile_approach_service: ready (identity mobile_approach.pid25612, contract v2)`; supervisor slot DEGRADED after
  five refused spawns (host held); no arm token; no listener on 5555 or 5556; the camera service (pid 4025) untouched
  by the install and the restart. The desktop's candidate Plans were refetched sha256-verified from the new release
  (swapped `4d729e40...`, abort-swap `6c19197d...`; the previous release's copies kept beside them).
- **Desktop 8770** cut over to release `800650f` (runtime session `rt-20260926T121303Z`, observe-only, same web bundle;
  rollback target `e4e25e9`); it reads the new session, the held host and the two new candidates.
- **The v2 probe, PASSED** (`bin/pi/approach_probe.py --contract v2`, run through `lekiwi-deploy.sh approach-probe`
  under the benchmark venv, the executor taken from a runtime release of `800650f` copied to the robot;
  `.local-dev/console/v2-probe/probe-20260926T121315Z/probe-record.json`). Its input was a REAL stored record of proof
  (a): a robot wrist capture perceived on the desktop (`probe-001`, 73 landmark inliers, rms 0.95 px). The executor's
  own functions gave the anchor (-0.033, -0.165) m in PAD_FRAME, the reach axis (0.026, 0.9997) and a remaining advance
  of 77.0 mm, so `x_m` 0.077, with `max_age_ns` 75 s (a 60 s perception deadline plus the relay's bounded waits).
  The Cake-launched capability answered `mobile_approach.pid25612.cmd_1`: `timestamp_ns` equal to the publication
  stamp, `expiry_ns` 250 ms after it, `vx_mps` 0.05, `vy_mps` 0, `omega_rps` 0; bound to the launched pid; submitted
  0.6 ms after publication with 249 ms of TTL left; the disabled gateway answered ZERO, the enabled one APPLIED
  (0.05, 0, 0) to `transport.FakeBaseMotionTransport`; no lerobot module loaded; the slot, generation and pid the
  same before and after. Gateway acceptance is not physical execution: no motion transport to the robot was built.
- **The negative, as designed:** the v1 fixture against the v2 capability got no command within the timeout (the
  capability drops a v1 document), verdict FAIL, Cake unchanged (`lekiwi-evidence/v2-release-20260926/04-*`).
- **`lekiwi-deploy.sh check`** refuses now, by its own rule: the unit is active and the camera service owns a video
  node; it is the read-only check for a robot with no host, to be run after `stop` and the camera service's stop, at
  the start of the attended session.

### 14.6 State at this handover, what remains, and the attended session

| where | what | observed |
|---|---|---|
| robot | release `20260926T121114Z-463057` current; resident unarmed, session `01a0dda1...`, epoch 3, capability v2 at generation 2 (pid 25612), host held, no token, camera service wrist only | 12:14 UTC |
| desktop | 8770 on release `800650f`, observe-only, forward connected, candidates of the new release | 12:13 UTC |
| public branch | `feat/operator-console` at the docs commit after `800650f`, not pushed, tree clean | this commit |
| private Cake | `task/ck-lekiwi-deploy` at `34dca51`, unchanged | |

**LIVE motion stays disabled:** `mission.execution` unset, 8770 without hardware commands, the robot-side mission
runtime not deployed, no controller anywhere. The software path is now connected end to end without motion: a real
capture, the desktop perception, the executor's quantities, the v2 observation, the Cake-launched capability's answer,
the bounded gateway.

**No further physical measurement is needed before an attended session.** Every quantity the v2 path uses is one the
proven placement already measured (the pad frame, the anchor, the reach axis, the remaining advance) or one the cubes'
geometry fixes (the radii). What remains is physical qualification, which only the attended session gives:

1. `lekiwi-deploy.sh stop`, the camera service's stop, then `lekiwi-deploy.sh check` (read-only), then the arm step of
   section 12.7 (`lekiwi-deploy.sh arm`, from `a882b80` or later; the robot on its stand, a hand near power). It
   establishes the host attached under Cake with the command socket on loopback and nothing more.
2. The physical approach qualification through the capability path: the relay's per-pulse travel at 0.05 m/s commanded
   has been measured only through the in-process primitive (50.8 mm in 0.5 s); the first pulses through the relay are
   measured by the executor's own re-observation and bounded as before (0.2 to 0.8 s, four pulses, 120 mm). It needs
   the robot-side mission runtime deployed and `mission.execution` set, with `mission.radii` `cube_half_edge` and the
   perception deadline (60 s covers the measured p90 of 15.2 s with room), each a configuration change to be made
   with the operator present, none of them here.
3. The separate qualifications stay separate: detector 0.40, camera qualification, safe stop and recovery (the SIGKILL
   gap remains open; neither the token nor loopback closes it).
4. The two-cube staging (D4) needs no measurement: cube A inside the pad outline by less than 17.96 mm (so its
   placement measures ARM_ONLY), cube B outside the pad as the recorded starts were (37 to 45 mm, so its placement
   measures BASE_ADVANCE), the two more than 25.4 mm apart; SEARCH measures both modes and refuses anything else.

**Named residuals.** The attribution swap of section 12.8 stands (12.7 mm now bounds it). The pid label is a
correlation, not authentication. A stop during an arm move is recorded as a perception abort. The two docs-gate em-dash
failures predate this branch. `python3 -m unittest discover -s autonomy/tests` cannot collect in this checkout (no
package files); the binding form is the explicit module list, as run here.

### 14.7 Resumption commands

From the console worktree root, after `set -a; . .local-dev/console/lekiwi-env.sh; set +a`:

- connectivity and boot: `ssh -o BatchMode=yes "$KC_PI_SSH" 'cat /proc/sys/kernel/random/boot_id; uptime -s'`
- deployment state: `bash bin/pi/lekiwi-deploy.sh status`
- the recorded unarmed state after a reboot, on the CURRENT release (never a token): start the unit, then the ApplyPlan
  of `~/kc-lekiwi/current/bundle/plans/mobile-demo-swapped.cakepln` as `lekiwi-evidence/v2-release-20260926/01-*`
  records it; the camera service with the release's `start-camera-service.sh` when it is not running
- 8770 after the robot returns: `bash <main checkout>/.local-dev/console/deploy-20260923-night/deploy.sh cutover 800650f`
- the v2 probe again: `bash .local-dev/console/v2-probe/run.sh 800650f`
- proof (a) again: `bash .local-dev/console/offboard-perception/proof.sh`
- proof (b) again: the inert Plan first (section 13.6), then the 8770 Cake panel's check

## 15. Round five (2026-09-26, 12:30 to 12:45 UTC): the robot-side runtime deployed, the connection rechecked, ready for one attended procedure

### 15.1 The robot-side mission runtime, deployed and verified without motion

- **Release** `a57ff86bf39c-20260926T123817Z` of commit `a57ff86` (a git archive verified against the commit's tree by
  `bin/runtime-release.sh stage`, copied read-only to `~/kc-runtime/releases/`; the first release of this round,
  `7f9d94fd3585-20260926T123106Z`, is kept). The unit `kiwi-cake-runtime.service` was rendered ON THE ROBOT by the
  release's own tool: interpreter the benchmark venv (`~/kc-perception-feasibility/venv`: numpy 2.2.6, cv2 4.12.0,
  scipy 1.18.0, pyzmq 27.1.0, lerobot 0.5.2, torch 2.11; the base miniconda lacks scipy), `--enable-hardware-commands`
  so a LIVE mission can be admitted at all, no `[Install]` section (never enabled), `KillMode=process`.
- **LIVE configuration** `~/kc-runtime/runtime-config.json` (mode 600, validated by the release's own loader): data root
  `~/kc-runtime-data` with this desktop's saved low scan pose and the robot's benchmark pad template (the one proof (a)
  ran with); host `127.0.0.1` 5555/5556; `capture_provider` camera_service (the supervisor's local mode: frames read
  from the store on the robot); `cake.robot.link` local on the resident's own socket, the pinned build identity, the
  capability slot and capsule, `activation_candidates` from `~/kc-lekiwi/current`; `mission.execution`
  proven_controller, the two ipc endpoints, `mission.radii` `cube_half_edge`; `perception.remote` port 5590, deadline
  60 s, the model revision and file digests of the benchmark pin; tap null, preview null.
- **Checks under the venv on the robot:** the daemon modules import; the controller bundle builds from the release
  (digest `c2b4de60...`, 67 python files, every v2 module in its closure); the controller entry and its closure import
  from the bundle; lerobot, cv2, scipy and pyzmq import; `capability_observation_available` answers available.
- **One fix found and committed** (`a57ff86`): the preflight failed an empty `pi_ssh_target` unconditionally, so the
  robot-side runtime could never start a run. A runtime whose robot link is local asks nothing over ssh; an empty target
  now passes there with that reason, a set target is still checked for safety, a desktop runtime still needs one.
- **Running now:** the robot runtime (LIVE) with the host held; the desktop console for it on port 8771 through an ssh
  unix-socket forward of the runtime's socket (`.local-dev/console/runtime-deploy-20260926/console-8771.sh`), the
  pinned `800650f` web bundle. Its Start is off at the motion boundary itself: "the safe-attach motor host is not
  running on the robot computer", robot motion `robot_session_not_confirmed`. 8770 (desktop runtime, observe-only)
  stays as the camera view; the robot runtime carries no preview.

### 15.2 The activation connection, rechecked on the v2 release through the robot runtime

Driven from the 8771 console's own card (the runtime reading the resident locally), from the inert starting state
(epoch 4, capability slot inert at generation 3):

| press | resident answered | slot before, after | host held throughout | session changed |
|---|---|---|---|---|
| `mobile-demo-swapped` (`4d729e40...`) | COMPLETED, OBSERVED | inert gen 3 to mobile gen 4, new instance | yes | no |
| `mobile-demo-active-successor-abort-swap` (`6c19197d...`) | ABORTED, REJECTED_BY_CAKE | mobile gen 4, unchanged | yes | no |

The process the valid Plan launched, pid 37222, runs `--contract v2` (its log: ready, contract v2). The v2 probe then
bound to THAT pid (`probe-20260926T124037Z`): from the same real record, `x_m` 0.077, the answer
`mobile_approach.pid37222.cmd_1` at 0.05 m/s straight ahead with the publication stamp, the gateway APPLIED
(0.05, 0, 0) to the in-memory recorder, verdict PASS. The mission path's relay binds by the same rule
(`cake_capability_adapter.bind_command_to_launched_process` against the slot's launched pid), so the Cake-launched
process the console activates is the producer the mission path consumes. 8770's candidates are the same two files.
Note on the browser: the extension's element clicks did not reach the card on this tab; the presses were made through
the card's own button and select handlers in the page (the runtime's records and receipts are the evidence).

### 15.3 The starting state, set and read back (12:41 UTC)

Evidence preserved first (`lekiwi-evidence/v2-release-20260926/07-*`, `08-*`, the robot runtime's
`capability-checks.jsonl` as `09-*`), then the recorded ApplyPlan of `mobile-demo.cakepln`: epoch 6, the capability
slot on the INERT capsule at generation 5 (no process), the holder ACTIVE on the mobile capsule at generation 5 (the
active capsule pre-admitted, pid 38086), the supervisor slot DEGRADED with child 0 (host held), no arm token. A
resident restart reloads this same starting Plan. Availability still depends on the capability slot itself: the
mission waits until that slot carries the mobile capsule (the shared activation verdict reads the slot back).

### 15.4 Two wordings corrected

- **Applied is dispatch acceptance.** Placement and completion are decided by the proven measured predicates:
  `object_secured`, `placed` (the exit view's margin against PLACED_MIN_EDGE_MARGIN_MM), `base_returned`, and the
  attribution (exactly one unclaimed placed cube, earlier targets undisturbed) in `run_proven_target` item 4 and
  `target_binding.result_claims_placement`. An APPLIED command is one more necessary condition for a capability
  target, never a sufficient one; the docstrings and this document now say so.
- **12.7 mm is the chosen association rule**, not a measured localization bound: `target_binding` (THE CHOSEN RULE), its
  basis text, the spec's section 10 and 14.4 say so; the retained examples are checked against it by the tests, an
  identity the rule cannot settle is refused (the not-unique refusals), and the attended run tests the rule beside the
  approach.

### 15.5 The assembled path to the motion boundary, and authority renewal

Verified on the deployed pieces, without motion: a real robot capture, the desktop perception, the executor's own
quantities, the v2 observation, the Cake-launched capability's answer, the bounded gateway (15.2 and 14.5); the robot
runtime admits the LIVE mission surface and refuses Start at the host (15.1); the runtime's checks and the mission
path read the same resident and the same launched pid. Authority is never renewed by a new command: the relay
re-qualifies the observation every period against its capture stamp, its motion epoch, its stationarity and its own
bound (`test_another_target_another_frame_a_moving_base_a_changed_epoch_and_a_stale_capture_are_refused`), a stop
seen before, during or after the Cake reads commands nothing (`test_a_stop_before_the_call...`,
`test_a_stop_during_the_cake_reads...`, `test_a_stop_while_the_link_waits...`, `test_a_stop_flag_that_cannot_be_read_is_a_stop`),
a change of the slot, generation, capsule or pid while a pulse ran refuses it
(`test_a_change_of_generation_pid_or_capsule_is_refused_after_the_stop`), and the capture stamp travels beside the
publication stamp on the wire (`capture_ns`, `published_ns`; `test_a_stale_capture_is_judged_only_when_a_stamp_and_a_budget_exist`).
The rehearsal's synthetic observation lies outside the v2 envelope and is never imported by a LIVE module.

### 15.6 The attended procedure (the one remaining physical proof)

**Deployed revisions.** Robot: Cake release `20260926T121114Z-463057` (v2 capability), runtime release
`a57ff86bf39c-20260926T123817Z`, camera service from `ead60a6`. Desktop: 8770 release `800650f` (cameras, Cake view),
8771 console (robot runtime), perception service from the `a57ff86` runtime release
(`.local-dev/console/offboard-perception/service.sh`). Public branch `feat/operator-console` at this commit.

**Initial state (as left):** capability slot inert (generation 5), holder pre-admitted, host held, no token, robot
runtime running with Start off at the host, camera service on the wrist only, front camera disabled, detector 0.40
qualification pending (the deployed detector ceiling is unchanged), the host SIGKILL watchdog gap open (a killed host
leaves no base watchdog; the token and the loopback do not close it: your hand near power is the stop of last resort).

**Scene.** Cube A inside the pad outline by less than 18 mm (its placement measures ARM_ONLY); cube B outside the pad
by 37 to 45 mm as the recorded starts were (BASE_ADVANCE), the two more than 25.4 mm apart; 15 cm of free floor in
front of and behind the robot; the robot at its start with the arm at the saved low scan pose.

**Your actions, in order** (from the console worktree root, after `set -a; . .local-dev/console/lekiwi-env.sh; set +a`):
1. Desktop: `bash .local-dev/console/offboard-perception/service.sh start <a57ff86 runtime release dir>` and `status`
   (the robot's 5590 listens); `bash .local-dev/console/runtime-deploy-20260926/console-8771.sh status`.
2. Robot, you present: `bash bin/pi/lekiwi-deploy.sh stop`; stop the camera service (`start-camera-service.sh stop` of
   the `ead60a6` release); `bash bin/pi/lekiwi-deploy.sh check` (read-only, must PASS); `bash bin/pi/lekiwi-deploy.sh
   arm` (the one-shot token, the host attaches, the loopback listener is read back; the resident restarts on the
   STARTING Plan: capability inert, holder pre-admitted); start the camera service again (wrist only);
   `bash .local-dev/console/runtime-deploy-20260926/console-8771.sh robot-stop` then `robot-start` (the runtime reads
   the new resident session).
3. Browser, 8771: Robot motion, confirm the connected robot (the new session and the running host instance); 8770 shows
   the wrist camera. Press Start.
4. Expected transitions. Target `cube_near`: SEARCH (TARGET_SELECTED, mode ARM_ONLY, bound within 12.7 mm), PREGRASP,
   ALIGN (one cube in view), GRASP_CLOSE (retention measured), CARRY, PLACE, PLACED (exit view margin at least 17.96
   mm), target complete. Target `cube_far`: a first execution WITHOUT the capability: SEARCH measures BASE_ADVANCE and
   refuses `mission_capability_required` before anything is picked up; the runtime records the need, asks the
   observation source (available), and the same unresolved target WAITS FOR THE CAPABILITY.
5. In the Cake panel apply `mobile-demo-active-successor-abort-swap`: REJECTED_BY_CAKE, the slot unchanged, the target
   still waiting. Apply `mobile-demo-swapped`: OBSERVED, the slot on the mobile capsule at the next generation, a new
   `--contract v2` process; the mission resumes the SAME target with one respawn carrying the capability.
6. Continuation through the proven controller: SEARCH re-binds the same cube (within 12.7 mm; a second candidate
   inside the radius is a refusal), PREGRASP, ALIGN, GRASP_CLOSE, the rise to the low scan pose, HOLD_OBSERVE, one
   stamped stationary capture, then BASE_ADVANCE pulses (at most four, 0.2 to 0.8 s, 120 mm total): each pulse is the
   Cake-launched capability's own answer to the v2 observation, relayed to the bounded gateway and the host on
   loopback, then stop confirmed, base zero read, arm drift checked, displacement MEASURED in the pad frame; then
   PLACE_APPROACH, PLACE, PLACED by the measured margin, BASE_RETURN by the proven reverse, mission COMPLETE.
7. After: `bash bin/pi/lekiwi-deploy.sh stop` (removes any token), `service.sh stop`, copy the run's evidence.

**Stop conditions.** The Stop button (the mission's cancel, then SIGINT to the controller's guarded stop; the base is
confirmed zero and the report says where it is); any executor refusal (posture, gripper, drift, base zero, pulse
bounds, implausible displacement, pad frame invalid, capability not active or its pid changed, observation stale or
of a past epoch, no command applied) ends the run with the arm holding; a perception answer that is late, missing or
mismatched holds; the resident session changing revokes motion permission; power, by hand, for everything else.

### 15.7 Genuine remaining blockers

None in software for this procedure that this round could find. Named residuals, not blockers: the relay's real
per-pulse travel is measured for the first time in the run itself (bounded and measured per pulse); the 8771 console
carries no camera preview (8770 does); the desktop perception service is started by hand for the run (step 1); the
robot's session confirmation needs the running host (step 3), which only `arm` gives.

## 16. Round six (2026-09-26, 13:10 to 15:10 UTC): the attended session, the ruling, the work order

The attended procedure of 15.6 was run: perception service up, stop, camera stop, check, arm (the safe attach refused
once because the arm had not been acquired since the morning's power cycle; acquired with the operator's tooling; a
stand-alone host started by hand was stopped; arm passed: supervised host on loopback), camera start, runtime restart,
robot confirmed in the browser, Return arm completed. Four Starts refused at binding with no motion: the two-block
staging D4 needs an arm-only block inside the pad by less than 17.96 mm and yet reachable, a window that at the
staged pad distance measured empty (40.3 mm of loaded travel against 42 mm required), and a base block that is not cut
by the frame border. The operator ruled (decision-log.md, 2026-09-26): one block, the robot decides. D4 and D5 are
superseded; the two-target default and the declared mode as target identity are withdrawn. The plan of record is
committed as `autonomous-single-cube-plan.md`; the deliverables, gates and qualification order are in
`one-block-demo-2026-09-26.md`. Correction to 15.6 step 4: with the two-target default, cube_near's first SEARCH binds
both targets, so the mission waits for the capability straight after cube_near is placed; there is no cube_far run
without the capability. The robot was left stopped (both units inactive, no token, arm holding) for a battery change.

## 17. Round seven (2026-09-26, 18:40 to 20:30 UTC): the one-block demo, live

Release `914c1314229a-20260926T184151Z` (commit 914c131: the auto target, the search sweep, the signature check, the
browser end to end) installed on the robot, the 8771 console on the same commit, `cake.signature` added to the private
configuration. With the operator at the robot: acquire, check, arm (supervised host on loopback), camera service,
runtime, confirm in the browser. One block on the wood, Start: the search measured BASE_ADVANCE, the mission held for
the capability, the abort-swap candidate was rejected with the slot unchanged, the swapped candidate observed, and the
mission resumed. That run missed the grasp (the block 44 mm nearer than the proven grasp radius; the gap is recorded
in `one-block-demo-2026-09-26.md`). The block moved to the grasp radius, Start again: pick, three relayed base pulses
measured in the pad frame, place at 47.5 mm from the edge, base return to 2.9 mm, mission complete. The signature
check ran from the console afterwards. Details and evidence paths in `one-block-demo-2026-09-26.md`, section 3, T5.
