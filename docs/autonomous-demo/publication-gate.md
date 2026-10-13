<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Publication gate

This branch must not be pushed merely because development is finished.

## Required human gates

- [ ] Physical E2E demo works.
- [ ] Reliability run set completed.
- [ ] Independent code/architecture review completed.
- [ ] Cake claims and evidence reviewed.
- [ ] Robot safety review completed.
- [ ] Privacy/security review completed.
- [ ] Dependency/license review completed.
- [ ] Public documentation reviewed for overclaims.
- [ ] Final Cake artifact/version selected.
- [ ] Final public signatures/checksums produced through private signing process.
- [ ] `tools/publication_audit.py` passes.
- [ ] Human explicitly authorizes the push/PR.

## Publication audit

From repo root:

```bash
python3 tools/publication_audit.py --base-file .local-dev/base-commit
```

The audit checks both the current diff and blobs introduced anywhere in branch
history.

## Push

Only after explicit approval:

```bash
KIWI_CAKE_ALLOW_PUBLIC_PUSH=YES git push -u origin dev/autonomous-cube-collection
```

The local pre-push hook re-runs publication audit.

An agent must never set the approval variable on its own.
