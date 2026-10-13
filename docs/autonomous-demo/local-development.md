<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Local development workflow

## Branch

`dev/autonomous-cube-collection`

No upstream tracking branch during development.

## Baseline

`.local-dev/base-commit` stores the exact starting commit locally and is
ignored.

Do not silently rebase/merge moving `main` into the demo branch during active
physical development. Integrate upstream deliberately after reviewing impact.

## Raw work

Use ignored:
- `state/`
- `datasets/`
- `recordings/`
- `captures/`
- `checkpoints/`
- `outputs/`

Keep all physical-run data local.

## Private Cake repo

Private source remains separate. If a feature is required, write a narrow
public-side requirement and implement it privately. Only final public artifacts
cross the boundary after publication review.

## Commits

Local commits are encouraged for rollback/review, but remember: **anything in
local branch history can later be published accidentally**. Do not commit
sensitive material even temporarily.

Run before every commit:

```bash
python3 tools/public_repo_guard.py --staged
```

## Pushes

Blocked by hook until explicit publication approval.
