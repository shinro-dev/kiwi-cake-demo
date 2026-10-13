<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Autonomous collection demo: agent rules

These rules apply to all work under `docs/autonomous-demo/`, `autonomy/`, the
related tooling, and any Cake integration for this segment.

## Local-only development boundary

The working branch is local-only until all of the following are true:

1. the physical E2E demo works;
2. the branch has been independently reviewed;
3. public claims have been checked against observed evidence;
4. privacy/security publication audit passes;
5. the human operator explicitly authorizes publication.

Agents must never run `git push`, create a remote branch or open a PR before
that explicit authorization. Never set `KIWI_CAKE_ALLOW_PUBLIC_PUSH=YES`
without that authorization.

## Public-repository privacy/security

Treat every committed byte as potentially public later.

Never commit or paste:

- private keys, seed material, credentials, passwords or access tokens;
- private signing configuration or dev signatures;
- raw robot camera captures/videos;
- training datasets, LeRobot episodes or checkpoints;
- private Cake source (the private Cake source repository);
- full environment dumps;
- real usernames/home paths/hostnames;
- IP/MAC addresses or Wi-Fi information;
- USB/device serials;
- PII observed by robot cameras;
- crash dumps or raw runtime state.

Use ignored local directories for raw work. Tests use synthetic/reserved data.

Existing intentionally public release verification material is not a precedent
for committing new development signing material.

## Private Cake boundary

The developer has access to the private Cake source repository and an
Ubuntu AMD64 Cake build. A missing Cake capability may be implemented there
ad hoc, but:

- first document the minimal required behavior;
- do not copy private source into this repository;
- do not expose internal/private details in public docs;
- build/sign using the private release process;
- only final explicitly approved binary/checksum/signature/public verification
  material may later cross into the public repo.

## Robot safety

Do not start hardware, enable torque or command motors unless the human
operator explicitly authorizes the specific test.

Stable code outside the replaceable module owns:

- command expiry/lease;
- hard velocity/acceleration bounds;
- stop-on-disconnect;
- operator stop;
- actuator safety constraints.

Cake admission/lifecycle and VLA outputs may never weaken these invariants.

## Technical defaults

- Desktop Ubuntu AMD64 is the preferred Cake/perception/VLA/task/UI runtime.
- RTX 4060 Ti 16 GB is available for VLA training/inference.
- Raspberry Pi remains stable LeRobot hardware host initially.
- SmolVLA is the manipulation baseline; ACT is fallback.
- VLA controls arm/gripper only for the first implementation.
- `mobile_approach` is deterministic visual servoing and the preferred
  Cake-replaceable capability.
- Classical cube detection first, permissively licensed learned detector only
  if experiments require it.
- Pad localization starts as known-planar-landmark matching/homography.
- No SLAM, generic VLM navigation or learned depth on the critical path.

## Scope and evidence

The demo claim is runtime capability evolution, not general autonomous
robotics. Do not make stronger claims than the evidence.

A bad candidate must be rejected without changing known-good active state.
A valid capability activation must preserve mission/target continuity.

Never implement a safety/security claim only in a prompt or convention.
