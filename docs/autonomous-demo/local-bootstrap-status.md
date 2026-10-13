<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Local bootstrap status: autonomous cube-collection devpack

This records the state after installing and validating the autonomous
cube-collection development pack (v0.3). No demo functionality was
implemented in this session; this is setup and validation only. Nothing in
this file was published, pushed, or shared outside this local clone.

## Branch

- `dev/autonomous-cube-collection`, created from the current `main` tip.
- No upstream tracking branch is configured for it.
- Base commit is recorded locally in `.local-dev/base-commit` (ignored, not
  reproduced here) for later publication-audit use.

## Installed / merged

- `AGENTS.md` created at repo root from the pack's agent-rules fragment.
- `.gitignore` extended (existing rules kept) to also ignore local dev,
  dataset, checkpoint, and credential-shaped paths.
- `.githooks/pre-push` and `.githooks/pre-commit` installed;
  `core.hooksPath` set locally to `.githooks` (clone-local git config, not
  committed).
- `tools/public_repo_guard.py`, `tools/publication_audit.py`, and their unit
  tests installed.
- `autonomy/`, `prompts/`, and `docs/autonomous-demo/` scaffolding installed
  (specs, decision log, contracts; no demo code yet).
- A CI workflow that re-runs the publication guard on relevant PRs.
- Two pre-existing, untracked camera images (`img/`) and the new
  `.local-dev/` directory were protected via a clone-local git exclude list
  before any other step ran, so neither can be swept up by an ordinary
  `git add -A`.

## Publication safety

- `pre-push` blocks every push by default and was verified directly (empty
  stdin, no bypass variable set) to refuse with a clear message.
- The bypass variable was never set during this session.
- The repository guard scans for datasets/checkpoints/recordings/captures/
  outputs, key-shaped files, tokens, home paths, MAC addresses, and bare
  IPs, and its packaged self-test suite passed in full.
- No commit was created and nothing was pushed during this session.

## Environment readiness (summary, no local paths)

- GPU: an NVIDIA RTX 4060 Ti (16 GB) is present, healthy, and visible to
  both the driver and to PyTorch/CUDA.
- Python tooling: `uv` and `conda` are both available; no dependencies were
  installed, upgraded, or downgraded during this session.
- LeRobot: a working, GPU-enabled LeRobot install with a recent PyTorch/CUDA
  stack already exists on this machine. A second, differently-pinned LeRobot
  environment also exists but is currently unpopulated. Reconciling which
  environment to standardize on is an open decision, deliberately left
  unresolved by this bootstrap.

### LeRobot baseline recommendation (paper only, nothing changed)

This repository's own runbook (`docs/reproduce-end-to-end.md`) already pins
an exact target for this demo: LeRobot version 0.6.1 at a named upstream
commit, installed with the `lekiwi` and `viz` extras via a plain virtual
environment at `~/lerobot/.venv`, which is also the exact interpreter path
`bin/laptop/teleop.py` and the Pi-side host adapter already assume.

Neither environment currently on this machine matches that runbook exactly:

- the working, GPU-validated install (version 0.6.1, PyTorch/CUDA confirmed
  against this GPU) is editable-installed from a vendored copy that belongs
  to a different, sibling project, not to this repository, and its exact
  upstream commit is unconfirmed, so it may or may not match the pin this
  repo's scripts assume;
- the placeholder environment that already sits at the path the runbook and
  scripts expect is pinned to an older LeRobot version (0.4.4) and has
  nothing installed into it.

**Recommendation:** treat `~/lerobot/.venv`, rebuilt exactly per
`docs/reproduce-end-to-end.md` sections 4b/5a (commit
`b4e2d0b61017a0db646a12c98a2df3837e37a1b9`, `lekiwi`/`viz` extras), as the
development baseline for this demo, rather than repurposing the other
project's vendored install. It is version-identical to what already works
on this GPU, so the risk of that rebuild is expected to be low, and it
matches the path every existing script in this repository already assumes,
avoiding a second, ad hoc environment convention for new autonomy code. The
SmolVLA/ACT training path (`manipulation-vla.md`) will likely need
additional extras beyond `lekiwi`/`viz`; confirming exactly which ones is an
open item for whoever does that rebuild, not resolved here. **No
environment was created, populated, or modified to produce this
recommendation.**

## AMD64 Cake integration findings

- The public release only ships AArch64 binaries; there is no public x86-64
  Cake release.
- A private, locally built Ubuntu AMD64 Cake toolchain is available to this
  developer through their own private workflow, independent of the public
  release. Its exact location and internal source were not read or copied
  into this repository.
- Cake's documented lifecycle model (resident process, signed capsule,
  declared Plan, read-only admin-socket introspection) is architecture
  neutral and applies the same way on AMD64 as on the published AArch64
  target.
- Cake has no actuation/torque concept in the current model. Bounded
  base-motion commands for a future `mobile_approach` capability would need
  to keep flowing through the existing LeRobot-owned client/host link, with
  any motion-bounds enforcement living on that side, not inside Cake.
- Corrected after a read-only inspection of the private Cake source
  (`cake-capability-assessment.md`): the Plan model is a multi-slot graph
  (the public demo's one-slot Plan is the demo's shape, not a runtime
  limit), live replacement of one slot while another slot's child process
  runs on is implemented and tested, invalid candidates are refused before
  any effect, and the admin socket already reports per-slot generation,
  content identities, lifecycle and health. The one missing surface is
  runtime admission of a new capsule; the baseline design avoids needing
  it by pre-declaring the capability slot. No private change was made.
- No Cake binary was executed during this bootstrap (no `--help`, no probe
  invocation, no daemon/resident start), and none was executed during the
  later inspection either; every interface question was answered from
  source and from the private repository's own test evidence, read only.
- The local x86-64 binaries found on this machine predate the private
  repository's current development tip and are to be rebuilt from it before
  use; the current tip also fixes a shutdown fault affecting capsules with
  their own thread.

## Tests run

- The packaged repository guard's self-test suite (unit tests).
- The repository guard itself, against the actual set of newly
  installed/merged files (staged deliberately for the check, then
  unstaged; nothing was committed).
- Non-hardware repository tests where applicable; hardware-dependent
  segment demos were not run, per the no-hardware constraint for this
  session.

## Blockers / open items

- Reconciling the two differing LeRobot environments before any training or
  recording work begins.
- Resolved: the private Cake interface questions are answered behaviorally
  in `cake-capability-assessment.md`; the minimal private items (build
  identities, an inert variant, one replacement test, and the optional
  runtime-admission extension) are scoped in `private-cake-requirement.md`.
- Resolved: the wording pass over the pack's bundled documentation is done
  and `tests/run-all.sh` passes, so this repository's documentation-hygiene
  gate no longer blocks committing the branch.
- Open: rebuild the desktop Cake binaries from the private repository's
  current development tip (the local ones predate it), and add the
  supervising-slot replacement test on the private side before the first
  end-to-end run.

## Recommended next engineering prompt

"On the `dev/autonomous-cube-collection` branch, read
`docs/autonomous-demo/cake-capability-assessment.md`,
`docs/autonomous-demo/mobile-approach-spec.md` and
`docs/autonomous-demo/private-cake-requirement.md`. Then, on the private
Cake side and in its own method: (1) rebuild the four desktop x86-64
binaries and the supervising module from the current development tip,
(2) add the second build identity and the inert variant of the supervising
module, (3) add the test that a replacement targeting a supervising slot
restarts only its own child while the other supervising slot's child is
uninterrupted, and (4) stage a three-slot desktop Plan (LeRobot supervisor,
`mobile_approach` at the inert generation, holder slot with the real
build) so the replacement can be driven over the admin socket with a stub
child. No robot, no torque, no hardware. Report the evidence lines, then
return here to start the Python `mobile_approach` process against the
spec's data-plane contract with the stub child replaced by it."
