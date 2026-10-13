<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Autonomous cube collection demo: delivery plan

**Version:** 0.3
**Date:** 2026-09-15

## Objective

Deliver a continuous physical demo in which LeKiwi autonomously collects fixed
yellow cubes into the fixed Seeed Studio / Hugging Face / LeRobot work-area
pad, while Cake adds a capability without restarting the protected robot
session or autonomous mission.

Primary claim:

> Cake can safely change the capabilities of an autonomous physical system
> while that system remains operational.

## First filmable sequence

Use two cubes:

- Cube A inside the measured manipulation envelope.
- Cube B outside the envelope and requiring base repositioning.

Mission:

> Collect the yellow cubes and place them in the work area.

Sequence:

1. Mission starts once.
2. Robot finds pad + cubes.
3. Robot picks Cube A and places it on the pad.
4. Robot selects Cube B.
5. Cube B is outside manipulation readiness.
6. Mission enters `WAITING_FOR_CAPABILITY`.
7. Corrupted `mobile_approach` candidate is presented to Cake.
8. Cake rejects it; active mission/session stay alive.
9. Valid `mobile_approach` is admitted/activated.
10. Same mission and target resume.
11. Robot approaches Cube B.
12. VLA picks it.
13. Robot reacquires/returns to pad.
14. VLA or bounded placement behavior drops it inside work area.
15. Mission completes/resumes search.

Five-cube continuous collection is a follow-on reliability goal.

## Development/publication workflow

### Development
- local branch only;
- no remote branch;
- no PR;
- raw experiment data stays local/ignored;
- private Cake changes remain in the private Cake source repository.

### E2E acceptance
- physical run works;
- repeated runs establish reliability;
- failures classified.

### Independent review
- code/architecture review;
- Cake claims/evidence review;
- safety review;
- privacy/security review;
- licensing review.

### Publication
Only after explicit human approval:
- run full branch-history publication audit;
- build/sign the final intended Cake artifact privately if required;
- copy only approved public artifacts;
- push branch;
- open PR.

## Compute topology

### Desktop Ubuntu AMD64: RTX 4060 Ti 16 GB

Preferred:

- Cake resident/runtime for replaceable autonomy capability;
- perception;
- calibrated localization;
- task executive;
- SmolVLA training + inference;
- UI;
- local recorder/replay.

### Raspberry Pi / LeKiwi

Stable:

- LeRobot hardware host;
- camera/actuator I/O as provided by LeRobot;
- stable command gateway/watchdog/bounds;
- network transport.

The exact split must be reconciled with the actual AMD64 Cake interface already
available locally. Do not fake a runtime contract.

## Parallel workstreams

| Track | Responsibility | Criticality |
|---|---|---|
| Manipulation | SmolVLA pick/place, ACT fallback | Critical |
| Perception | cube + pad detection/localization | High |
| Mobility | visual approach + pad return | High |
| Executive | deterministic mission state | Medium |
| Cake | admission/activation/continuity | High |
| Security | local guard + publication audit | Hard gate |

## Roadmap

### Day 0
Local branch/guardrails, camera calibration, physical measurements, run recorder.

### Day 1
Manipulation dataset v0 + classical perception baseline + pad landmark baseline.

### Day 2
VLA physical evaluation + manipulation-success map + mobile approach.

### Day 3
Single distant cube: approach -> pick -> return -> place.

### Day 4
Two-cube mission and `WAITING_FOR_CAPABILITY`, without Cake choreography.

### Day 5
Cake bad-candidate rejection + valid capability activation + mission resume.

### Days 6–7
Reliability only. No feature creep.

### Days 8–10
Hardening if required, then independent review.

## Estimate

- first E2E: 4–5 aggressive days;
- repeatable/filmable: 7–10 days;
- portable robustness: ~2 weeks.

The manual train/test/data loop is the main schedule risk.

## Scope-control rule

Nothing enters the critical path unless required to demonstrate live Cake
capability evolution.
