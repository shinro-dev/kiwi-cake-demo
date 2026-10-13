<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Console reliability: what was fixed, and what is left

Continues `console-reliability-handoff-2026-09-22.md`. That document's section 2 hypotheses and its section 6D
premise are both superseded here. Everything below is either a RECORDED FACT with where to check it, or is labelled
otherwise.

## 1. The ALIGN failure is closed

Diagnosed, corrected and verified offline. The full reading is in
`align-failure-diagnosis-2026-09-22.md`. In one line: the cube was in the decision frame at the run's highest
detector score and passed every shape criterion, and a constant apparent-size ceiling rejected it by 0.31 px.

The correction is in `autonomy/perception/cube_recognizer.py`, with `autonomy/tests/test_cube_size_limits.py`
pinning it against the sizes the robot produced. Re-running the real perception over all 59 retained console frames
with the corrected ceiling loses no cube that was detected before and recovers ten that were wrongly reported
absent, including the decision frame that failed the run. Every recovered box was confirmed by eye to be the cube,
and the pad badge stays rejected.

**It still needs an attended physical run.** The gap between the two populations is 0.07 of image width, measured on
one cube, one pad and one lighting setup. That is a separation, not a validated margin.

## 2. The Cake gate question is answered: YES

The handoff gated the whole Cake increment on whether a real resident exists to connect to, and recorded that the
runtime finds none. **A real Cake resident exists, built for both machines.**

- An x86-64 ELF executable built 2026-09-15 in the private Cake build tree, outside this repository.
- aarch64 ELF executables for both supported boards, staged 2026-09-12 inside this repository under the ignored
  `state/` tree, beside `admin-probe`, `demo-plan` and `demo-preflight`.
- `tests/mock-bin/` already holds tracked shell mocks of those same four tools, so the interface the tests exercise
  is already written down.

**The runtime was not wrong; the handoff generalised it.** `probe_cake` (`packages/demo-runtime/demo_runtime/read_models.py:388`)
globs `<root>/release/*/bin/cake-resident`, which is exactly the path `bin/fetch-release.sh` extracts a signed
release tarball into and exactly the path `bin/lib/common.sh` reads. Its evidence string says "no cake-resident
binary is present among the checkout's extracted binaries", which is TRUE of these checkouts, because
`fetch-release.sh` has not been run in them. That is a narrower statement than "no resident may exist", and the
handoff turned one into the other.

**So the blocker is not artifact availability. It is the integration surface**, which is genuinely absent and is
correctly reported as absent:

- `cake_view()` returns `slots: []`, `transactions: []`, `update_preview: None`, `continuity: None` as literals and
  `synthetic: true` (`read_models.py:448`).
- `mission_participation` is the constant false, "the controller has no Cake adapter" (`read_models.py:446`).
- `admin_interface_compatible` is null because this runtime has no admin client (`read_models.py:442`).
- Contract v1's eight command types contain nothing that could submit a Cake operation.
- Nothing under `bin/`, `tools/` or `docs/` references the staged binaries, so they are not wired to any documented
  path.

The next step is therefore not another availability probe. It is deciding whether the staged resident is installed
through the existing release path or reached where it is staged, and then writing the admin client this runtime
does not have.

## 3. Fixed in the console

- **A failed run is no longer drawn as a success.** The result card's rule was `result !== null ? C.ok : ...`, so
  every finished run got the colour of a good outcome, including the recorded failure. The decision is now
  `apps/console-web/src/design/resultRule.ts`, which reads the runtime's own outcome word, the exit status and the
  reason code. A run in progress and an idle-but-ready console are also no longer the same colour, and an idle
  runtime is not drawn as an established readiness.
- **The withdrawn front-camera cause is gone from the product.** The cover asserted that streaming both cameras
  lengthened the observation gaps and the heading said the qualification failed. Corrected in the cover text, the
  heading, the component's own docstring, the Pi start script's operator warning and the normative paragraph of
  `camera-service.md`. All of them now say the prerequisite is unproven and that nothing showed the camera does harm.

Gates on these: `tsc --noEmit` clean, vitest 599 in 36 files (was 590), `tests/test-docs.sh` at its 2-failure
baseline, shellcheck clean on the edited script, autonomy 633 passed.

## 4. Not fixed, mapped precisely

These were established by reading the code, and each survived an adversarial pass that refuted several neighbouring
claims. They are in the brief's own priority order.

### Freshness semantics

- `apps/console-web/src/store/store.ts:367` stamps a preview RECEIPT on any preview telemetry LINE. The runtime
  heartbeats a frame-less status line every second while a source is STALLED
  (`packages/demo-runtime/demo_runtime/sources.py:543`), so a stalled camera reads as "just now". This is the
  central defect of the brief's item 2.
- Everything a correct fix needs already reaches the browser: `seq`, `source_seq` and
  `capture_clock.session` per role (`apps/console-web/src/model/sources.ts:262-297`), from
  `PreviewFrame` (`preview_client.py:105-131`) and `PreviewTracker` (`preview_client.py:151-207`), whose
  `measured_fps` is already computed from received FRAMES and is None below two samples.
- `gaps` and `malformed` are published as objects where the contract requires `integer|null`
  (`sources.py:379`), so the one frame-progression fault signal the runtime computes is discarded at both ends.
- `measured_fps` is rendered ungated by any freshness (`apps/console-web/src/design/cameras.ts:153`), so it outlives
  the feed it describes.
- `apps/console-web/src/panels/CameraRoles.tsx:36` prints the RUNTIME's receive age under a browser-receipt label.
- `apps/console-web/src/panels/SourcesPanel.tsx:220` reads `Date.now()` during render instead of the shared tick.
- `skipped_for_mission` and `clock_relation` are never published (`sources.py:368`), so the yielding field and the
  licensed capture age can never render.

### Terminal state and run binding

- Mission time does not freeze in the ordinary case: `run.finished`'s wall time is dropped at
  `apps/console-web/src/model/reducer.ts:657`, so a tab that watched a whole run takes the growing-age branch.
  The freeze branch at `model/mission.ts:88-98` is reachable, but not by the common path.
- Target and the phase rail read the live fold while Result and Progress read the selected run's retained
  predicates (`apps/console-web/src/design/Screen.tsx:349-369`), with no shared run id.
- A finished run's outcome survives a runtime restart and is drawn as the current run's result under the new idle
  session (`reducer.ts:305`, `reducer.ts:364-365`), while `previousRun()` returns null so the one mechanism that
  would label it as previous is also off.
- The header link reads as a warning during a healthy run, because readiness is the label
  (`Screen.tsx:432-436`).

**One premise in the brief does not hold.** There is no fixture fallback to remove from LIVE mode. The source is
chosen once, in `apps/console-web/src/main.tsx:37`, and `SyntheticSource` is constructed only on an explicit
`?source=simulation`. Nothing else imports it. Explicitly-labelled simulation and replay are the only paths to it,
which is what the brief asks for.

### Recovery and the log console

- `apps/console-web/src/panels/FailurePanel.tsx:76-95` names the next action beside a failed result but offers no
  control; the operator must find the `Actions` panel higher in the same drawer.
- `nextActionsFor` reports eligibility from `capability.state` alone
  (`apps/console-web/src/model/failure.ts:354`), so the offer can contradict the real button, which applies four
  more refusal layers.
- The runtime reports ONE shared eligibility for all seven motion capabilities
  (`read_models.py:325-336`), so `return_arm` has no eligibility of its own. A UI implying a per-command verdict
  would claim a distinction the snapshot cannot make. This constrains what deliverable (A) can honestly show.
- The Return arm control states no movement scope and no holding prerequisite (`panels/Actions.tsx:238-245`).
- Base recovery exists in the runtime as `manual_base_pulse` but has no frontend control at all, so the brief's base
  clause has nothing to attach to without new UI.
- The controller's stdout and stderr are written to disk (`supervisor.py:1133-1134`) and served by NO route. A log
  console needs either a new endpoint or `store.openReplay(runId)`; `view.diagnostics` is cleared on every
  `run.started` and capped at 200 lines (`reducer.ts:634-639`).
- The Drawer shell is not an accessible dialog (`design/Drawers.tsx:402-412`), unlike `ui/Dialog`.

### Session recovery

- No Reload or Reconnect affordance exists; the operator sees a bare reason code
  (`apps/console-web/src/panels/StatusStrip.tsx:364-371`).
- `commands_enabled` can never be restored in process (`model/reducer.ts:64-65`), so a soft reconnect leaves the tab
  dead. Only a new `ConsoleStore` produces a fresh `initialState()`.
- The credential is read once at module load (`main.tsx:40`), and the server serves a rotated credential only in a
  freshly rendered page, so **only a document load can authorize a reconnect**. This decides the shape of the fix.
- `ConsoleStore.start()` rebases `booted_runtime_session` onto the new session (`store.ts:315-326`), so the obvious
  dispose-and-start reconnect would destroy the evidence that readiness was established against a different runtime.
- The operator's stated `holding` is component state that is never cleared on a session change
  (`panels/Actions.tsx:203`). That is a physical attestation carried forward, which the brief forbids. The API does
  refuse the command with 409 `runtime_session_changed` (`console_api/app.py:570-572`), so the consequence is a
  misleading screen rather than a wrong command.

### Front camera

- The instrument that produced the 246.46 / 243.92 / 291.79 ms figures is not under version control, so the proposed
  loopback re-run has no consumer to run. The smallest next step is writing that consumer, not re-running it.
- `_check_front_camera` returns PASS for the absent front camera
  (`packages/demo-runtime/demo_runtime/preflight.py:357`), so no preflight signal distinguishes "off pending
  qualification" from "configured and healthy". The distinction exists only in the frontend.

## 5. Second round, 2026-09-23

### What is running, checked read-only at 07:15Z

The battery was changed and the arm reacquired, so every pid in the original handoff is stale. The LAPTOP side is
not: the runtime daemon and the console API have been up throughout and the runtime session is unchanged at
`rt-20260922T191759Z-9b44a4b6`, so `runtime_session_changed` has NOT fired. `data_mode` live, `commands_enabled`
true, owner free, no active run, last outcome still the failed align run. **Both preview endpoints now answer 503**
(the wrist answered 200 before), and `link.state` is `unknown` with no observation age: the robot-side camera
service and motor host are down, which is what a power cycle leaves behind. Seven capabilities read enabled,
`cake_update` disabled `not_built`. Nothing was started, stopped or acquired to establish any of this.

Stray from earlier sessions and left alone: three synthetic daemons and two console APIs on ports 8791 and 8792,
from development sessions that did not clean up. They hold their own temporary data directories and touch nothing.

### Done in this round

- **ALIGN is qualified accurately** and the detector identity that awaits the attended test is pinned. See section 7
  of the diagnosis document, which now carries the old and new bound, the normalization rule, the affected image
  path, the effective configuration, the magnitude of the change against the magnitude of the miss, and the
  statement that the 59 frames are the CALIBRATION corpus and not held-out evidence.
- **An ALIGN failure states the recognizer's own reason.** Both named causes in the old sentence were false.
- **The producer-to-browser path for the frame-progression counters**, with `gaps` and `malformed` as the
  `integer|null` the schema declares, unknown preserved as unknown, and a test that validates a produced slot
  against the real schema rather than comparing key names.
- **Preview freshness from frame identity**, scoped by role and camera-service session, handling duplicates,
  out-of-order lines and restarts, never consulting pixels. The screen names three facts apart: frames delivered to
  this tab, the age of the last frame, and the age of the last line.
- **Mission time freezes at the end the runtime recorded**, including for a failed run, with a late join still
  saying unavailable.
- **A run-scoped `GET /api/history/{run_id}/output`**, bounded, redacted, path composed by the runtime from the run
  id, and **a searchable, copyable log console in Diagnostics** that reads it.

### Verified in a browser

Against a labelled synthetic fixture on its own port, with the live console on 8770 untouched and confirmed
untouched afterwards. `tests/test-browser-result.sh` passes against the new build (a watching tab, a late-opening
tab and a reload agree). The Diagnostics drawer opens and draws the log console: `27 lines, 631 bytes. This is the
whole stream.`, filter chips counting `state 26` and `other 1`, numbered lines. The camera strip renders
`no frames delivered / no frame yet / last line 4954 ms ago`, which is the stalled-source case with healthy polling.
The front cover reads `Front off  qualification unresolved` with the corrected sentence.

### Still open, in the brief's own order

- **Run binding.** Target, progress, result and diagnostics are still not all bound to one selected run.
- **Recovery.** Return arm is not offered beside a failed result; the holding selection, Observe eligibility and its
  disabled explanation have not been reconciled.
- **Reconnect.** Not started. The shape is decided by a fact already established: the credential reaches a page only
  in the index.html the API serves, so the action is a reload of the existing bootstrap and not a new mechanism.
- **The camera collector (item 4) is BLOCKED and not merely unstarted.** The bounded robot-local comparison needs
  the robot's camera service running, and it is down. The earlier instrument is also not under version control, so
  the first step is writing a collector, not re-running one.
- **Cake read-only integration (item 5).** Not started. The artifacts are present (section 2) and
  `tests/mock-bin/` already describes the four tools' interface, so the next step is a launcher that takes an
  explicit artifact path, records the binary identity, and performs an admin handshake and inventory read.

## 6. Third round, 2026-09-23, deployed

### Robot services, checked directly and not inferred

The previous round said the motor host was gone. **That was wrong**, and it was wrong in exactly the way the brief
warns against: it was inferred from preview failures. Checked directly, the host command and observation ports on the
robot are OPEN and ssh reaches it. The runtime's own `robot-host` component says `health: unknown` with the evidence
"the runtime never contacts the robot host and no telemetry source is attached", which is by design: the
configuration sets `tap: null`, so the runtime observes the host not at all. **`link: unknown` is therefore not
evidence about the host.**

What was actually down was the camera service, and one more thing: **the preview ssh forward**. Forwards are opened
once at daemon start and are deliberately never restarted (`forwards.py`, "an ssh that keeps dying is a fact an
operator has to see, not a loop to hide it in"), so the robot power cycle killed the preview child and the runtime
left it dead. That is why the preview read 503 with a healthy host.

The wrist-only camera service was restored through its own guarded launcher, after `lock-status` showed both camera
locks free and the deployed bytes matching this checkout. It holds the wrist camera, front is off, and the new camera
service session is `cs-20260923T080554Z-38c37522`.

### Deployed

Frontend, API and runtime were deployed together, because the new page calls a route only the new runtime answers.
The data directory and the private configuration were passed unchanged and the journal reconciled clean.

| | |
|---|---|
| deployed revision | `e538d00` |
| working-tree revision | `e538d00`, clean |
| served bundle | `index-BcxyYZaX.js`, `index.html` sha256 `8a409df8564767e4b078e5b5` |
| previous bundle, kept for rollback | `index-CeuqiRmO.js`, `index.html` sha256 `2ae6557cc3f6039ba30f931d` |
| runtime session after deploy | `rt-20260923T081158Z-484b416e` |

Verified on the deployed console against the retained failed run
`console-20260922T194137Z-gellh5f4`: the result card reads `failed` and names that run and its reason code; target
and progress both read "not recorded for this run" rather than borrowing the session's state; the controller's own
output is served and readable, 64 lines, and the five home-path occurrences in the file on disk arrive as
`[redacted]`; the Return arm control renders beside the failure with its movement scope; Observe carries its holding
question. The wrist camera reads `LIVE, 4.0 fps delivered, last frame 0 ms ago, last line 0 ms ago`, which is the
three-way separation working against a real camera. **A page load left the runtime's command journal at 48 records,
unchanged: reload sends no command.**

Mission time reads `unavailable` on that run, which is correct and is worth stating plainly: a tab that opens after
a run has ended folded no `run.started`, so it has no start to measure from. The frozen duration is exercised by a
tab that watches a run, and is covered by tests.

### A defect the deployed console showed

Every empty-gripper failure offered "start a cycle" as a next step. A cycle refuses unless it starts from the saved
low scan pose (`start_pose_not_low_scan`), and a run that stopped part way did not leave the arm there, so the offer
would have refused. Return arm is now offered between looking and starting.

### The bounded diagnostic

`tools/observation_cadence_probe.py` is committed, with its refusals tested. It measures receiver cadence only and
says so in its own report, and it is explicitly NOT the instrument that produced the 2026-09-22 A/B/A series.

**It has not been run.** The measurement requires submissions prevented, which means taking the console's command
path down while the operator is using it. The procedure is: stop the console API, copy the probe to the robot
computer, run it there over loopback against the host's observation port with `--sole-consumer`, remove it, restart
the API. That is one operator decision away, not a missing capability.

### The real Cake connection

The real x86-64 artifacts were launched by absolute path and their own interface was read from them, not from
`tests/mock-bin`:

- `cake-resident --config PATH`, whose required keys it names itself: `store_root`, `admin_socket_path`,
  `admin_socket_group`, `admin_socket_mode`, `admin_max_connections`, `signature_policy` (allow, warn or require),
  `trusted_keys`, `flight_ring_capacity`, `target_profile`, `plan_artifact` (omitted entirely for the empty Plan).
- `admin-probe request SOCKET OPERATION`, with `get-status` and `read-flight` as the read-only operations.
- The repository already carries the matching configuration writer, `kc_write_resident_conf` in `bin/lib/common.sh`.

**The resident really started**: it loaded one trusted key, created its journal at format version 1 and reported a
durable fresh start. It then refused, correctly and on its own admission rule:

```
plan plan-target-profile-mismatch: the Plan names a target profile digest that is not the active profile's
```

**That is the precise prerequisite for the inventory read.** The admin socket is created only after admission
passes, so `get-status` cannot be reached until the Plan's target profile digest and the resident's active profile
agree. The next step is to build the Plan for the `lekiwi-mvp` profile, or to configure the resident with the
profile the demo Plan was built for. Nothing about this is blocked by the module SDK question, which remains
separate and unanswered.

Two path constraints worth keeping: the admin socket path must fit `SUN_LEN`, so a scratch directory under a long
path fails to connect, and `plan_artifact` must be absent rather than empty when there is no Plan.

## 7. Fourth round, 2026-09-23

**Retained-run hydration is done and deployed.** A finished run now answers from its own retained records, read back
through the existing history path and folded from `initialState()` in isolation. A stale answer cannot replace a
newer selection (each request takes a token; only the newest publishes), a run already read is not re-fetched, and a
fold of a different run is never taken as an answer about this one. Command eligibility still reads the live view.

Verified on the deployed console against `console-20260922T194137Z-gellh5f4`: mission time reads **01:23**, which is
the subtraction of that run's own two runtime clock readings (19:41:37.521Z to 19:43:00.787Z), where it previously
read `unavailable`. Two independently opened late tabs agree exactly, and the command journal was unchanged across
both loads.

**The bounded camera diagnostic was executed.** Its own record is
`observation-gap-diagnostic-2026-09-23.md`. In one line: the host publishes evenly, the excursions are made between
the robot and the laptop, and the front camera was off in both conditions so it cannot be their cause.

**Cake admission is resolved analytically and the coherent pair is NOT on this laptop.** Both binaries carry the
canonical target profile `linux-aarch64-gnu/1`, and the profile name grammar they publish is
`<os>-<architecture>-<libc>/<cake_abi_major>`. The Plan that `demo-plan build` produces therefore targets aarch64,
which is the LeKiwi deployment, and running the x86-64 resident against it on the laptop is what produced
`plan-target-profile-mismatch`. That refusal was correct and the resident was right to make it.

The remaining step for the read-only inventory is to run the coherent pair on the robot computer: the staged
aarch64 artifacts in `state/staging/pi5-aarch64/` with a Plan for that profile, then
`admin-probe request SOCKET get-status` and `read-flight`. Nothing was changed to admit the available Plan: the
resident's profile was not altered, no digest was patched, and admission was not weakened. Two practical constraints
stand: the admin socket path must fit `SUN_LEN`, and `plan_artifact` must be absent rather than empty when there is
no Plan.

## 8. Fifth round, 2026-09-23: a real Cake connection

**The supported planless startup works, and it is what delivered the connection.** With `plan_artifact` omitted, a
store root at owner-only permissions and a socket path short enough for `SUN_LEN`, the native x86-64 resident starts,
loads its trusted key, creates its journal at format version 1, reports a durable fresh start, prints `ready` and
exposes its admin socket. No Plan is admitted, and none is claimed.

The real responses, from `admin-probe request SOCKET get-status` and `read-flight`:

```
status ok            epoch 0           slots 0        bindings 0     resources 0
build_identity d392ef91e3fbffd8...     session_uuid 01a0cd7e60ac7a1f...
config_identity 295f749e3eb386cd...    target_profile_digest 841c3e3f73e01e48...
plan_digest c93aa621cf3d1052...        transaction_state none
read-flight: 3 flight records, sequences 0 to 2
```

`packages/demo-runtime/demo_runtime/cake_admin.py` is the adapter. It runs the shipped `admin-probe` as a child and
parses what it prints; it does not open the admin socket and does not speak the wire protocol, because that protocol
is Cake's and `admin-probe` is its supported client. Only `get-status` and `read-flight` are permitted, by name, so no
code path in it can mutate anything. The binary is named by absolute path and never searched for, so `tests/mock-bin`
cannot be picked up in place of the real tool by accident.

The ladder now reports what the resident said rather than what this machine could infer about itself, and
`modules_active` reads the resident's own slot count, where **zero is an answer** and not a failed read. Connected,
disconnected and restarted were each exercised against the real process: reachable true, then false with
`admin_socket_absent`, then true again with a new `session_uuid`.

**Contract v1 was not widened.** `cake.schema.json` sets `additionalProperties: false`, so the first attempt, which
added a `resident` key, was removed and the identity and counts now travel in the ladder rungs' own evidence text.
A read-only milestone is not a reason to change a contract.

**The three facts stay apart**: a resident answering, a module being active, and this robot's task being carried by
Cake. The third stays false and is not derived from the other two.

**What is NOT done.** The deployed runtime does not yet read a resident, because naming one needs a `cake` section in
the runtime configuration and `config.py` rejects unknown keys by design. That is a small, contained change and it
was not rushed into the operator's live configuration at the end of a long session. Until it is made, the sidebar
shows the same ladder it showed before, which is honest: this runtime is not connected to a resident.

The aarch64 bring-up on the Pi was therefore not needed for this milestone and was not performed.

**Module SDK and build availability remain a separate, unanswered question.** Nothing here depends on it.

## 9. Conventions, unchanged

Everything in section 7 of the handoff still holds. Nothing was pushed. Nothing was committed on `main`.
One convention was broken once in this round and is repeated here because it cost a shell: **never `pkill -f`**, it
matches the calling shell. Kill by pid.
