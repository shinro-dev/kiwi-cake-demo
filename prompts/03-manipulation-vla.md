<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Prompt 03: SmolVLA/ACT pipeline


You are working on the local-only branch `dev/autonomous-cube-collection` in
`kiwi-cake-demo`.

Read root `AGENTS.md` (including autonomous-demo rules), `SECURITY.md`,
`SAFETY.md`, `LIMITATIONS.md`, and all relevant
`docs/autonomous-demo/*.md` before changing code.

Do not push, publish, open a PR, or set `KIWI_CAKE_ALLOW_PUBLIC_PUSH=YES`.

Do not run real robot motion/torque unless the human explicitly authorizes the
specific test.

Do not commit raw captures/datasets/checkpoints/dev signatures/secrets/PII,
the private Cake source repository, machine IDs or local paths.

Use the actual local Ubuntu AMD64 Cake interface. Do not fabricate a Cake API.


Inspect the exact installed/pinned LeRobot API first.

Build:
- two-camera dataset validation;
- SmolVLA 4060 Ti 16 GB memory-probe/fine-tune config;
- one-batch safe smoke path;
- inference adapter with freshness/action sequencing;
- ACT fallback using same dataset schema;
- physical success-map recorder interface.

Do not commit data/checkpoints/credentials. No wheel actions in VLA.
