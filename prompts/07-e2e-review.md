<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Prompt 07: pre-hardware/E2E adversarial review


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


Do not add features.

Audit:
- stale data cannot cause motion;
- replaceable capability cannot bypass stable safety;
- bad candidate cannot change active state;
- mission/target persist across activation;
- no private data/log leakage;
- no raw data is tracked;
- claims map to observable evidence;
- Cake guarantees are deterministic code, not conventions.

Run guard/tests. Produce a publish-safe readiness review and enumerate the
specific real-robot tests requiring human authorization.
