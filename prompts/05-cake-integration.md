<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Prompt 05: Cake AMD64 integration


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


Use the real local AMD64 Cake interfaces.

First document:
- build/version/target;
- descriptor/capsule model;
- admission;
- process/module lifecycle;
- IPC/data path;
- activation events.

Then integrate `mobile_approach` only where actually supported.

If functionality is missing, stop at a clean adapter and write a minimal
`docs/autonomous-demo/private-cake-requirement.md` describing behavior needed
without private implementation details.

Corrupted-artifact fixtures are local/ignored until final release preparation.
