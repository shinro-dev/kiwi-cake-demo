<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Operator console

One screen over the single-cube demo runtime. It exists to make five things understandable without anybody explaining
them: what the robot sees, what it is doing, why the controller chose the current action, what has actually been
verified, and which software is responsible.

```bash
export NVM_DIR=$HOME/.nvm; . $NVM_DIR/nvm.sh; nvm use 22
pnpm install                              # from the repository root
pnpm -F @kiwi/console-web dev             # the configured runtime, at the printed address
pnpm -F @kiwi/console-web build           # typecheck, then build into apps/console-web/build
pnpm -F @kiwi/console-web test --run      # the reducer, the geometry and the command flow
```

## What the address shows

**The bare address shows the CONFIGURED RUNTIME, and starts nothing.** Opening the page reads: it asks the runtime who
it is, reads one snapshot, follows the record stream and reads the finished-run list. None of those moves anything, and
no command is sent until somebody presses a button. The dev server proxies `/api` to a loopback port; the console API
serves the built page at `/` with the per-launch credential in it.

It used to open the built-in scripted run instead, and only `?source=live` reached a runtime. That was the one
arrangement here that can mislead somebody: the page the API itself served looked exactly like the console and was a
fixture, so a screen saying a camera had captured nothing, or that a phase had not been reached, was saying it about a
script rather than about the robot in the room. A page that cannot reach a runtime now says so in the runtime's own
absence, which is a true sentence about the machine.

The other two things the screen can be are named choices and are never arrived at by accident:

| address | what is on the screen |
| --- | --- |
| *(nothing)* or `?source=live` | the configured runtime, over the API on this machine |
| `?source=simulation` | the scripted run drawn inside this page: no runtime, no robot, no camera |
| `?source=simulation&scenario=one-block` | the one-block demo rehearsed inside this page: nothing plays until Start; then the search, the sweep (under the word search_sweep, which the rehearsal names: at the head the sweep runs only under it), the hold for the capability, the activation (the abort-swap aborts, the swapped Plan is observed), the resume, the pick, the base pulses, the placement, the return and the mission complete. `pnpm -F @kiwi/console-web dev`, then open the address the dev server prints with that query. |
| a run chosen in **finished runs** | a recorded run, replayed through the same reducer |

A value of `source` this console does not recognise resolves to the runtime: a typo must never quietly become a
fixture. The decision is in one place, `src/panels/sourceChoice.ts`, which `src/main.tsx` and the strip's own selection
buttons both read, so the screen and the address can never disagree about which of the three this is. A synthetic
runtime is still never relabelled as hardware: the mode badge carries the runtime's own `data_mode`, which no address
can change.

## What it will not do

- It will not print a number for a fact that was not recorded. Those read `unavailable`.
- It will not show a placement as confirmed unless all four end predicates measured true, whatever the reported
  outcome says. The reported outcome is shown beside it, unchanged.
- It will not skip a gap in the record. An announced resync and a sequence number that jumps both appear as a gap with
  its range.
- It will not resend a command. A lost response is resolved by id through `GET /api/commands/{id}`, or it stays
  unresolved and says so.
- It will not let a live view whose link dropped be read as replay. The data mode and the link are separate readouts.
- It will not move the decision frame while its result is on the screen.
- It will not infer that the cube is held from the jaw angle. It shows the retention evidence with its age, or nothing.
- It will not say that anything on the robot is enforcing this runtime's ownership unless the runtime says so. Under
  the baseline backend nothing is, and the screen says that in those words.

## What has to be on the screen without scrolling

At 1440x900, with a run going: the **current action**, the **phase** rows, the **primary controls** and **Stop**.
Measured over CDP at that size, those land at y=93 (Stop), y=380 (current action), y=443 to 664 (all six phase rows)
and y=432 to 714 (the first three controls), with no sideways scroll.

Three things pay for it, and each one was a fact stated twice rather than a fact removed:

- **The task card leads the wide column, and the camera is under it.** The camera card is about 490 px tall, so with
  it first the current action sat at y=1267 on a 900 px screen. The cost is real: about 100 px of the camera is above
  the fold now. Each camera keeps its own connectivity readout in the strip, so whether a camera is delivering is
  still answered above the fold even when the picture is not all on screen.
- **The strip is 229 px instead of 252**, while carrying the ownership readout it did not carry before. The Cake
  sentence is on one line beside its badge instead of on a row of its own, the age beside a readout that has never
  received a line is gone (its badge already said so in words, and the sentence is in its hover text), and the groups
  are stacked so a group is as wide as its widest line rather than as wide as all of them end to end.
- **The camera's three clock rows are in the engineering tab**, in a card called `the camera's clocks`, with the whole
  paragraph about why a capture age is not a computable number. The presentation card keeps the one line an operator
  acts on, and the still keeps its own identity and age.

Nothing was made smaller than 14 px, clipped, or hidden behind an overflow to do it.

## The still, and how old it is

With no preview stream the camera card shows the newest frame the CONTROLLER stored, fetched by its observation id,
which costs the camera nothing and adds no second owner of the device. The caption leads with **still, not video**,
then the observation id, the camera role, and the camera session and sample number where the record carried them
(where it did not, it says so rather than inventing a second identity for the picture).

**Its age is counted in records, not in seconds.** The frame was stamped on the robot's computer and written by the
runtime, and this browser owns a third clock; none of the three is related to another by anything measured. The one
quantity both ends agree on is the sequence number of the ordered log, so the card says `17 record(s) ago`. The
recorded wall time is printed beside it exactly as the runtime wrote it and is never turned into a duration.

## Who owns the robot

`snapshot.execution` says which path a command takes to the robot and what owns the robot while one runs. They are two
questions and the console keeps them apart, because the second is the one that is easy to overstate.

Under **`baseline_normal_host`** the runtime runs the proven controller against the motor host in its normal mode and
takes no lease of any kind. The whole of the ownership is this runtime's own single run slot on the computer it runs
on: one run at a time, refused there, by that process. Nothing on the robot refuses a command from anywhere else, and
any other process on that computer can still open the servo bus. The strip says `one run at a time, refused here;
nothing on the robot refuses anything`, and the engineering view carries the full finding and the runtime's own
sentence verbatim.

**The words "lease held" are never printed under that backend, in any form**: not as a no, not as a not-applicable,
not in a tooltip. A screen that prints the phrase teaches the reader that a lease is the thing being reported on, and
what follows from a greyed-out "lease held: no" is that one would be held if things were working. None was ever asked
for. Under `boundary_lease_host` the lease *is* the ownership, so it is reported in full. A runtime that states
nothing draws a question nobody answered, and it is never read off the absent lease beside it.

## Where things are

| path | what is in it |
| --- | --- |
| `src/contracts` | the event envelope, composed from the generated contract types (see the note below) |
| `src/model` | the pure reducer, the view state, per-layer overlay validity, the sanitized observation DTO, the reason templates |
| `src/geometry` | the plane homography and the pad-frame scene math |
| `src/api` | the HTTP source, the synthetic source and the scripted run, command ids |
| `src/store` | one external store, read through `useSyncExternalStore` |
| `src/panels` | one panel per question the screen answers |
| `src/ui` | the small primitives: panel, badge, field, dialog, inline SVG icons |
| `src/styles` | the Shinro Studio instrument-panel tokens |

Live and replay fold the SAME reducer: live applies events as they arrive, replay applies `events[0..cursor]` from the
beginning. There is no second code path that could disagree with the first.

## Three panels that exist so nobody has to open a terminal

**`why it ended this way`** (`src/panels/FailurePanel.tsx`, over `src/model/failure.ts`). A run that ends failed,
stopped, refused or unknown is explained from the record alone: the phase, the machine-readable `reason_code`, the
controller's own sentence verbatim, the measured values against their thresholds, and the observations the controller
named. **A reason code this console has no gloss for renders as the code itself, with the whole payload under it.** That
is the case the panel is built for: the console will always be a version or two behind the controller, and a screen that
goes blank on an unknown code sends the operator back to a terminal. A run that ended badly with no `reason_code` at all
is stated as a gap in the record, not filled in.

**`a command whose execution is NOT known`** (`src/panels/UncertainPanel.tsx`, over `src/model/uncertain.ts`). It says
outright that whether the command ran is not known, shows the machine evidence the daemon read for itself (the process
table, the run's files, the observation store, the journal, and which of them could not be read), and offers the
operator's answer as an **attestation** in its own words: *I am stating this; the console did not verify it.* The
statement goes to `POST /api/commands/{id}/resolve`; the RUNTIME reconciles and decides whether the block lifts, and the
controls come back only when a later snapshot says so. While the block stands, **every motion control is off with the
reason `owner_unresolved`**, whatever subset the runtime named, and **there is no control anywhere that sends the
ambiguous command again**. The panel reads `liveUnresolved`, not the folded view, so opening a replay does not make a
live block disappear from the screen.

**`host telemetry tap` and `camera preview source`** (`src/panels/SourcesPanel.tsx`, over `src/model/sources.ts`).
Source identity, freshness and cadence as the runtime states them. *Not attached*, *disconnected* and *stalled* are
three different facts and are drawn differently, each with the runtime's own reason code; *connecting*, *failed*, and
*silent to this tab* (this browser's own measurement, labelled as such) are three more. A stalled source keeps its last
stamp and the age of it and shows no values. The sample clock and the receive clock are both named and are never
subtracted from each other, and the preview's capture age is `unavailable` with the runtime's reason for why it cannot
be computed at all; the one span inside a single clock is labelled as the service latency it is.

## The synthetic source

It is reached only by asking for it, at `?source=simulation`. It is kept, rather than deleted with the old default,
because it is what the reducer, the gap handling and the command flow are exercised against in the tests and in a
demonstration with no runtime to hand; deleting it would leave those with nothing to run against.

`src/api/syntheticScript.ts` builds a full cycle from the contract fixtures in `packages/demo-contracts/fixtures`: the
envelopes and payloads start as known-valid fixtures, and the state names are the controller's real ones from
`bin/laptop/manipulation/run_single_cube_cycle.py`. It plays on a timer, loops, and answers every question the HTTP
source answers, so the whole screen is demonstrable with no runtime, no robot and no network.

Nothing in it was measured. Every number is invented, the contract objects are marked `synthetic`, and the badge and
the watermark across every camera panel come from that field. The source serves no image bytes at all: a synthetic
source that returned a picture would be inviting the screen to be read as a photograph of something that happened.

The scripted run genuinely skips a range of sequence numbers and then announces it, so the gap on the screen is a gap
that really exists rather than a decoration.

## Two things this application had to state itself

Both are written down where they are used, and neither is a second copy of something that already exists.

**The event envelope.** `@kiwi/demo-contracts` generates `Event` from a schema that is a closed base object (the
envelope) plus a `oneOf` over `kind`. The generator emitted only the branches, so the generated `Event` has `kind`,
`payload` and the per-branch id hints and none of `seq`, `producer`, `t_mono_ns` and the rest. `src/contracts/envelope.ts`
composes the envelope from the generated field types; only the LIST of envelope fields is stated there, because nothing
generated states it. If the generator is ever made to print the intersection its README describes, that file becomes a
re-export.

**The sanitized observation DTO.** The `observation` op serves an allowlisted projection of the controller's stored
record, defined by `RECORD_ALLOW` and `PERCEPTION_ALLOW` in `packages/demo-runtime/demo_runtime/read_models.py`. It is
not a contract record and has no generated type. `src/model/observation.ts` reads it field by field: anything absent or
of the wrong type becomes null, and the overlay layer that needed it is reported unavailable with a reason.

## Overlays

Overlays are valid LAYER BY LAYER, drawn as SVG over the frame in that frame's image pixels, from that frame's own
perception.

The cube box, mask and contact point and the jaw window rest on the detector alone and stay valid when the pad frame is
missing. The pad outline, the landmarks and every pad-frame projection are drawn only when that frame's pad frame is
valid. A pad-frame point is projected through that frame's pad-to-image homography: the served matrix when the runtime
serves one, otherwise one derived from the four pad corners and the recorded pad size, which the panel marks as
derived. Edges are sampled, a point whose homogeneous w is not positive is dropped, and only the contiguous runs that
survive are drawn.

Current detections are solid. Anything derived through the pad frame, or retained from earlier, is dashed and hollow,
so the two can never be read as the same kind of thing.

## What this console states to the API, and why

`apps/console-api` refuses a command whose body does not state the contract version at both levels, and `GET /api/hello`
takes the version as a query parameter. So:

- `hello` is sent as `/api/hello?contract_version=1`;
- a command body is `{command_id, command, contract_version: "1", client: {contract_version: "1"}}`. The API accepts
  the command either bare or wrapped, and keys the result on the id either way, but a body without the version comes
  back `rejected` with `contract_incompatible`. Both facts are locked down by tests in `src/api/commands.test.ts`.

The API can also say a tab may not command for a reason that is NOT the contract version: a replay-only runtime, no
runtime, no command path. That is a different state from an incompatible contract and the screen shows it as one: the
record keeps arriving and keeps being drawn, and only the controls go away, with the server's reason. The events stream
carries a third line kind, `{"dropped": ...}`, which is counted as a drop notice rather than as a record that failed to
parse.

## What the runtime serves that this console cannot draw today

These are observations about the runtime as it stands, not defects in this application. Each one shows on the screen as
an unavailable layer with its reason.

- `PERCEPTION_ALLOW` passes `cube.box_px`, while `autonomy/perception/cube_recognizer.py` emits `bbox_xyxy`. This
  console reads either, so it works whichever the runtime settles on.
- `cube.mask_polygon_px` is not in the allowlist, so the cube mask layer cannot be drawn from a live observation.
- Neither `homography_pad_to_image` nor pad landmark points are in the allowlist. The pad-to-image matrix is therefore
  derived from `pad.corners_px` when those are served, and the landmark layer stays unavailable.
- The controller's grasp reference window is not served, so the jaw window layer stays unavailable on a live frame.
- A preview's **capture age cannot be computed at all**: the camera service stamps a capture in its own clock and this
  laptop has no known relation to it. The runtime says so with `capture_age_reason`, this console prints `unavailable`
  and shows the service latency, which is the only span in this path that lies inside one clock.
- `unresolved_command` travels **beside** the contract snapshot, not inside it, because `snapshot.schema.json` has
  `additionalProperties: false`. `applySnapshot` reads it off the object defensively. It belongs in the schema; adding
  it is the contract owner's change, not this application's.
- The tap's `lease` is read and held on the view but is not drawn yet: the snapshot's own `owner.lease` is what the
  strip shows, and two lease objects on one screen would have to be explained before they could be shown.
