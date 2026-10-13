<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Console reliability handoff, 2026-09-22

Written to be the single starting point after this session is cleared. Everything below is either a RECORDED FACT with
where to check it, or a HYPOTHESIS labelled as one. The two are never merged.

## 1. What is running, checked 2026-09-22T19:58:03Z

| | |
|---|---|
| Branch | `feat/operator-console`, worktree `../kiwi-cake-demo-console` |
| Source revision | `c0be68e`, working tree clean |
| Frontend served on 8770 | `index-CeuqiRmO.js`, sha256 `193e9c595e25bceb...`; `index.html` sha256 `2ae6557cc3f6039b...`. The bundle the page requests was confirmed to be this one. |
| Runtime session | `rt-20260922T191759Z-9b44a4b6`, live, hardware commands ENABLED |
| Command status | idle: no active run, owner `free`, no unresolved command |
| Motor host | NORMAL host attached, pid 4402, `--robot.cameras={}` |
| Camera service | pid 30657, WRIST only, preview on, FRONT off |
| Preview endpoints | wrist 200, front 503 |
| Capture provider | `camera_service` (the managed path) |

The build is gitignored, so the bundle name and digest above are the only durable identity of what was served.

## 2. The failure to carry forward

**Run `console-20260922T194137Z-gellh5f4`.** Terminal status from the runtime and the controller, not from the screen:
outcome `failed`, exit status 5, reason code `align_cube_not_detected`, started 19:41:37.521Z, finished 19:43:00.787Z.
The controller process exited on its own; nothing was left running, and the runtime reports the slot free.

**States reached:** `SEARCH`, `PREGRASP`, `ALIGN`. It never reached grasp, so all four end predicates are null, which is
correct: they were never evaluated.

**RECORDED FACTS, from the controller's own stdout and the event log:**

- In the pregrasp view the cube WAS detected, confidence 0.93, not clipped at any border, no occlusion hint.
- In that same view: `CUBE in PAD_FRAME: not available from this frame (needs a pad fit AND a usable cube edge)`.
- `[STATE] ALIGN {"attempt": 0, "lateral_mm": 5.2, "radial_mm": 5.5, "aligned": true, "tool": "+8.1 mm out / +68.6 mm
  up vs the proven grasp, pitch 64.7"}`. The alignment was accepted while the tool was **68.6 mm above** the proven
  grasp pose.
- A non-contact descent ran to `pregrasp-descend-fwd11-dz43` and captured `obs-20260922T194245Z-wrist-0001`.
- In THAT frame: `cube detected: NO confidence 0.00 contact unavailable`.
- `CYCLE FAILED: ALIGN: the cube is not detected in the pregrasp view (coarse geometry too far off, or occluded). The
  arm HOLDS; base measured +0.0 mm from where this run started.`
- 4 wrist mission captures, all retained. One evidence gap, `source_stamp_missing`, which is the known NORMAL-backend
  provenance limitation and NOT a measurement of anything about this failure.
- Two predicates evaluated false: `already_placed` and `cube_detected_in_the_pregrasp_view`.

**HYPOTHESES, none of them established by the evidence above:**

- The cube left the camera's view during the descent because the descent began 68.6 mm high, so the geometry the
  approach was planned from did not survive the move. The recorded numbers are consistent with this; nothing in the
  run tests it.
- The pad fit was unavailable in the pregrasp view, so the cube's pad-frame position was never resolved and the
  approach rested on coarse geometry alone. Again consistent, not demonstrated.
- Lighting, cube placement relative to the demonstrated pickup region, or the tool pitch of 64.7 degrees may each
  contribute. Nothing here separates them.

**What would distinguish them cheaply, without moving the robot:** the four retained frames and their perception
sidecars are preserved (below). Re-running the existing perception offline over the descent frame and the pregrasp
frame, and comparing the cube and pad fits, would say whether the cube was out of frame, present but unfitted, or
present and fitted but rejected. That is an offline reading of preserved evidence and needs no physical run.

## 3. Evidence locations

Preserved under the existing convention, outside the checkout, with a digest list:

```
<main>/.local-dev/evidence/align-failure-20260922T194137Z/
  manifest.json            the run manifest: source revision, bundle and config digests, interpreter, packages
  completion.json          the runtime's own terminal record for the command
  events.json              all 124 contract records of the run, as the API serves them
  controller-events.jsonl  the controller's own event stream
  stdout.log, stderr.log   the controller process output, including the lines quoted above
  observations/console-20260922T194137Z-gellh5f4/
      obs-*.json           the four decision observations
      frames/              the images
      perception/          the detector outputs
      service-captures/    the camera service's own frame metadata
      cycle-*.json         the cycle record
  SHA256SUMS               27 files
```

The successful milestone's evidence is separate and is listed in section 5.

## 4. Changes in this branch

**Completed and verified** (all on `c0be68e`, gates green: tsc clean, vitest 590 in 36 files, autonomy 622,
demo-runtime 493, contracts 83, console-api 149, `run-all.sh` 16 passed plus the `test-docs` 2-failure baseline):

- The imported design ported to React with a thin adapter (`apps/console-web/src/design/`), deployed on 8770.
- The managed capture wiring: `ServiceCamera` beside `PiCamera`, a provider-aware `make_camera`, `--capture-provider`
  and `--mission-port` on the controller, and the supervisor passing them ONLY when the config names the service, so
  the legacy argv is byte-identical. Nine integration tests.
- Five presentation steps, the Cake sidebar reduced to a state with detail in Diagnostics, the established-result
  binding, the saved-observation label, and the front panel reading the timing verdict.

**Unfinished, not started, and NOT to be inferred as working:**

- After `runtime_session_changed`, the console offers no Reload or Reconnect action. The prohibition on replaying
  commands and the fresh Start confirmation must both survive whatever is added.
- A completed run's target, progress and duration are not all bound to that retained run; the result is, the others
  follow the live session. A new idle session is not visually distinguished from the previous completed run.
- Cake: nothing real is connected. See section 6.

**The components and data paths that actually need the work:** `apps/console-web/src/design/Screen.tsx` (the header
link and the three figures), `apps/console-web/src/model/mission.ts` (`missionTime`, `missionProgress`,
`missionTarget` all read the live view rather than a named run), `apps/console-web/src/store/store.ts`
(`followResultDetail` is the only read-back path and it fetches predicates only), and
`apps/console-web/src/design/Drawers.tsx` for anything that belongs in Diagnostics.

## 5. The successful milestone, preserved separately

Tag **`wrist-stream-cycle-pass-2026-09-22`** on revision `5636da8`, which the run manifest records with `dirty: false`.

Run `console-20260922T180557Z-5xeetlmj`: outcome `placed_new`, exit 0, 0 retries, 0 interventions, 0 refused actions,
and all four end checks true: object_secured 0.818 (>= 0.5), placed 55.36 mm (>= 17.96), clearance_target_met
55.36 mm (>= 37.96), base_returned 0.391 mm (<= 10.0).

**The corrected capture accounting.** Scoped to that run's camera service session `cs-20260922T173941Z-fbfe6699`, the
retained evidence holds **25 metadata records with 25 distinct sha256 digests and 25 distinct stored paths**, every one
role wrist at 1280x720, against **25 retained wrist `observation.captured` records** in the run's own log. **25 of 25
satisfy both the timestamp rule and the settle rule.** Earlier drafts said "24 captures" and "40/40 checks"; both were
wrong denominators (a counter difference taken after the first capture had landed, and the newest 40 metadata files
spanning four sessions). A digest-level join between the two sides is NOT available: the observation `content_digest`
and the service `sha256` are computed over different artifacts.

**The brightness exception stands as scoped.** Mean brightness through the service is 3.53 standard deviations below
the legacy path's mean, which is outside the documented spread, so that comparison is not a clean pass. The practical
magnitude is 0.24 grey levels on 118.9, 0.20 percent, systematic. Accepted by the operator for ONE attended wrist-only
run on the tested configuration; it changes no bound and does not close the step for repeat use.

**Measurement limits that remain.** The NORMAL host exports no servo read errors, retries or internal loop rate.
Observation delivery gaps during motion were not measured, because the host's observation socket is ZeroMQ PUSH and a
second consumer would take samples from the controller. The run's single evidence gap, `source_stamp_missing`, is a
provenance limitation and is not a substitute for a delivery-gap measurement.

Evidence: `<main>/.local-dev/qualification/step3-20260922T173206Z/`.

## 6. Outstanding work, in priority order

**A. Reliability of the cycle itself.** The align failure above is the first recorded failure of the browser-driven
cycle since it started passing. Before any new feature, establish from the preserved frames whether the cube left the
view, and whether the 68.6 mm descent start is inside or outside what the proven runs did. Nothing should be tuned
until that reading exists.

**B. The three frontend corrections** listed as unfinished in section 4.

**C. The camera investigation, EXPLICITLY UNRESOLVED.** The front camera is off. The reason it is off is that its
prerequisite is unproven, NOT that it was shown to cause harm. A controlled A/B/A (wrist, both, wrist again, equal
windows, one session) gave longest observation gaps of 246.46, 243.92 and 291.79 ms: the both-streams window was the
SHORTEST and the worst gap was wrist-only. The second stream does not explain the gaps, and the criterion is exceeded
by the qualified wrist-only configuration too, so it cannot carry a front-camera verdict either way. The earlier
monotonic progression was a confound of measurement order and is withdrawn, as is the "bus contention" cause.
THE MEASUREMENT IS THE SUSPECT: it is taken on the laptop over the LAN, and the stock host neither stamps nor
sequences observations. The smallest next step is to run the same passive consumer ON the robot computer over
loopback, which removes the LAN and the laptop's scheduling and changes nothing about the host, the service or the
cameras. Choose no remedy before that: a lower capture profile or a port move would be tuning against a number whose
source is not established. Recorded but NOT a finding: the wrist camera is on `usb3` (`xhci-hcd.1`) alone and the
front camera is on `usb1` (`xhci-hcd.0`), which also carries the servo serial adapter.

**D. Cake, and what blocks it.** There is no Cake integration in the execution path. `cake_view()` returns empty
slots, empty transactions and null previews as literals on every snapshot; `mission_participation` is the constant
false with "the controller has no Cake adapter"; `components()` never emits a `cake_module`; and contract v1 has eight
command types, none of which could submit a Cake operation. One capability is published, `cake_update`, disabled with
reason code `not_built`. **Before any Cake work, establish artifact availability**: the runtime probes for a
`cake-resident` process and finds none, and `decision-log.md` records that the runtime source and a buildable module
SDK are not supplied publicly, so a real resident may not exist to connect to. That question is the gate on the whole
Cake increment and should be answered first. The demonstration target remains: place the reachable cube, reach the
missing mobile-approach condition for the second cube, reject an invalid update, activate the valid capability, and
resume the same mission and target. An observer-only update is an INTERMEDIATE milestone and must be labelled as one.

## 7. Conventions that must not be relearned

- Never push. Never commit on `main`.
- `tests/test-docs.sh` forbids an em-dash in any committed file and scans every tracked file. Its baseline is
  **2 failures**, both pre-existing em-dash documents. Its private-token scan does not fail on its real match because
  the verdict comes from the last `xargs` batch's exit status; that is pre-existing and untouched.
- Node 22 for everything web: `export NVM_DIR=$HOME/.nvm; . $NVM_DIR/nvm.sh; nvm use 22`.
- Build the replacement into `apps/console-web/build-dev` while developing; `vite build` empties its output directory
  and `apps/console-web/build` is what 8770 serves. `KC_WEB_BUILD` points the browser regression at either.
- Never `pkill -f` a process name: it matches the calling shell. Kill by pid.
- Changing camera ownership means stopping the console first, so no command can be submitted during the handover.
