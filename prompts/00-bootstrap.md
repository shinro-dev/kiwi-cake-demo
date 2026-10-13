<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Prompt 00: inspect and bootstrap implementation


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


## Task
1. Confirm local branch/base/guards.
2. Inspect actual Ubuntu AMD64 Cake binary/docs/interfaces available locally.
3. Inspect existing LeRobot client/host integration.
4. Reconcile architecture docs with actual interfaces.
5. Create only package/module skeleton + typed schemas + synthetic tests.
6. Write `docs/autonomous-demo/implementation-checklist.md` with confirmed
   interfaces, P0 gaps and first implementation tasks.

If Cake lacks a required capability, write a minimal private-Cake requirement;
do not implement private source here.
