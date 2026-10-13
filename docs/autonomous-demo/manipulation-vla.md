<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Manipulation / VLA plan

## Goal

Train a narrow policy that:
1. picks the fixed yellow cube once delivered into a measured manipulation region;
2. releases/places the held cube safely inside the large work-area pad.

No base control in the first VLA.

## Primary: SmolVLA
Run training/inference on desktop RTX 4060 Ti 16 GB.

Pin exact LeRobot version/commit before training.

Do not assume reference batch sizes fit. Start with one-batch memory probe,
supported mixed precision, then scale with gradient accumulation if needed.

## Fallback: ACT
Use same dataset schema.

ACT is diagnostic:
- ACT succeeds / SmolVLA fails -> inspect VLA conditioning/setup;
- both fail -> fix data/geometry/task.

## Observation
- body RGB;
- wrist RGB;
- arm state;
- gripper state;
- stable task instruction.

## Action
- arm actions;
- gripper action.

No wheels.

## Initial task strings
- `Pick up the yellow cube.`
- `Place the yellow cube in the work area.`

Try one conditioned checkpoint first. Split pick/place if reliability requires.

## Dataset v0

Revised 2026-09-15: split into two explicit tiers rather than one ~50-100
target, so the first proof isn't blocked on hardening-level volume.

- **v0 proof (first model): ~20 clean episodes.** Fixed cube start zone with
  only a few centimetres of position variation, fixed pad, consistent
  base/home pose, same room/lighting. Successful demonstrations only. Train
  and physically test on this before collecting more.
- **Reliability/hardening (later, only if the v0 model's physical failures
  call for it): another ~10-20 targeted demonstrations**, then reassess. The
  original ~50-100 spanning wider position/lighting/manipulation variation
  is a hardening target for later iterations, not a prerequisite for the
  first working model.

Placement tolerance is intentionally large: somewhere safely inside the pad.

## Iteration
dataset v0 -> model v0 -> physical grid evaluation -> targeted data -> v1.

Do not collect hundreds blindly.

## Privacy
Datasets/checkpoints remain local ignored. Publishing a dataset/model to
Hugging Face is a separate explicit release decision, not part of development.

## Inference safety
- reject stale observations;
- sequence actions;
- bounded timeout;
- neutral/stop behavior on inference loss;
- clear queued actions on task-phase change using the actual pinned LeRobot API.

## Acceptance
Use physical success map, not training loss.
