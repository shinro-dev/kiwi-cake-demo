<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Prompt 08: publication review (ONLY after human says E2E + independent review complete)


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


Do not run this prompt unless the human explicitly states that:
- E2E demo works;
- independent review is complete;
- publication review is authorized.

Do not push yet.

1. Run full publication audit from `.local-dev/base-commit`.
2. Review every commit and diff.
3. Verify no sensitive blob was introduced then deleted.
4. Verify final release Cake binary/checksum/signature/public verification
   assets are the deliberately approved ones, with no private signing material.
5. Review third-party licenses.
6. Review claims/evidence.
7. Produce final publication report and exact proposed push command.

Wait for a separate explicit command before setting
`KIWI_CAKE_ALLOW_PUBLIC_PUSH=YES` or pushing.
