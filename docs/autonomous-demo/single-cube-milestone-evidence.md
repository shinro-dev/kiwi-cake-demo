<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Single-cube local cycle: milestone evidence index (2026-09-20)

This index preserves what the 2026-09-20 session demonstrated, what supports it, and
what is missing. It is the baseline for the operator console work. The local tag
`single-cube-cycle-pass-2026-09-20` marks the commit it describes.

## Conclusion on autonomy

The passing runs were autonomous runs of a bounded task. The conclusion rests on a
source inspection and on the run records, not on a new physical run.

- The entry point `bin/laptop/manipulation/run_single_cube_cycle.py` and its static
  import closure (29 repository files) contain no prompt, no `input()`, no action or
  target file, and no polling of a file, socket or variable for a decision.
- Perception is invoked programmatically: a zero-shot detector plus a pad localizer
  that matches the pad's printed landmarks. Every cube and pad coordinate used in a run
  comes from a frame captured during that run. The fixed image constants describe the
  camera and jaw geometry and a reference grasp, not the scene.
- Every file of that closure is tracked, clean, and identical between the
  implementation commit and the documentation commits above it.
- Each passing run has exactly one launcher log inside its time window. No single
  action (`exit`, `grip`, `move`) was launched inside any window.
- State timelines advance continuously. The stretches of 50 to 86 s without a state
  line are the place approach, descent and lowering, and the retreat, exit and settle.
  The logs show them as plan and result lines.

This does not establish reliability on arbitrary scenes. It is a small demonstration of
variation on one pad, one camera mount and one staging area.

## What is missing

- The run records carry no source revision, run identifier, process identifier or
  caller. The revision of each run is consistent with commit times and run times; it is
  not machine-recorded.
- "No operator action during the run" is the operator's and the agent's statement. A
  command issued outside the launcher would have left no trace.
- The launcher's exit status is echoed to the terminal only, so the logs hold no exit
  code. The outcome below is read from the terminal line of each log.
- No software stop or abort has been exercised on hardware in any recorded log.

The operator console closes the recording gap for new runs: revision, artifact
identity, command identity and structured events.

## Every cycle launch of 2026-09-20

Eleven `cycle` launches, eleven state records, 32 launcher logs of all actions. Times
are UTC. "log" and "record" are the first 16 hex characters of the sha256 of the
launcher log and of the state record.

| start | commit | outcome | intervention | log | record |
|---|---|---|---|---|---|
| 09:26:56 | before d9e06d9 | FAILED: empty close, predicate refused | none during the run | 6741a1f09639d00f | 6d2fe0a3f3efb2b5 |
| 09:31:43 | before d9e06d9 | FAILED at the placement threshold, cube held | agent finished by single actions: not one run | 188d86b4122e73e3 | e1cf27d9656c16da |
| 09:55:43 | d9e06d9 | STOPPED by the base-zero guard before a lift | agent single actions restored a safe state | 7227f8cb54fc8812 | e81bdb609b779ad6 |
| 10:31:36 | 925eec7 | PASSED, first acceptance, footprint clearance 3.4 mm | none | 1439d0f1488d156c | d9ab11374c82f1d9 |
| 11:36:04 | 1645623 | FAILED in the pickup: three empty closes | two built-in advances; agent `exit` afterwards | a3b21d63a34e212b | 9b6dd38352f0134a |
| 11:50:18 | 5dd28be | PASSED, clearance 34.4 mm, base advance 109.4 mm | none | c70f99700b0ee860 | 3202a3fe7fc1c784 |
| 11:59:12 | 5dd28be | NO-OP: cube already placed, nothing moved | launched without verifying the staging | b621c5a982f43b51 | f364d1998f0071ae |
| 12:02:26 | 5dd28be | PASSED, clearance 29.0 mm, base advance 97.1 mm | none | ebf6f7530dc680e3 | aea397bf9b28e3b1 |
| 12:12:05 | 5dd28be | STOPPED by the base-zero guard after the first arm move | agent `exit` afterwards; nothing touched | 29362d9130abadd9 | 2467ef4184c284d6 |
| 12:15:17 | 5dd28be | PASSED, clearance 33.1 mm, base advance 99.9 mm | none | 6115a49dd57a88b5 | 46e6476ae4c19a4a |
| 13:39:24 | 1b9d692 | PASSED, clearance 27.4 mm, base advance 94.2 mm | none | 0ce4703d725045f2 | 6f74bde1d9a898ee |

Commit `1b9d692` differs from `5dd28be` in documentation only; the code is identical.
So the placement implementation has four passes from four starts, one chance stop by
the unchanged base-zero guard, one failure on an earlier commit, and one no-op. The
first acceptance at `925eec7` used the earlier release admission.

A pass means the controller's four success predicates were all true: object secured,
placed, clearance target met, base returned. `CYCLE DONE` alone is not a pass: the
11:59 no-op also ended with it.

The committed run instructions were written after the third pass and state three. The
13:39 run happened later the same day and is recorded only here and in the local run
record.

## Measurement limits

- Footprint clearances of the four passes: 34.4, 29.0, 33.1 and 27.4 mm against a
  20 mm target. A second reading of one placed cube from another base pose differed by
  5.2 mm, so the estimate varies by about 5 mm with the viewpoint. That is observed
  variation, not a calibrated confidence interval.
- The admission's worst-case bound held by 3.1, 2.3, 8.3 and 2.2 mm. The 6 mm anchor
  allowance and the 1 / 1.6 discount of forward-kinematics travel rest on a few runs;
  they are configured empirical bounds, not guarantees.
- Every pass used the bounded base advance and return. The arm-only placement branch,
  the operator abort of the base path and the refusal branches of the base path have
  offline coverage only.
- A cube lost while the base moves would only be noticed by the final placement check;
  retention is not verified during base translation.
- Nothing senses the clearance between the base and the pad.

## Where the evidence is

The evidence stays local and is ignored by git: raw camera frames, perception records,
state records and logs are never committed. A sha256 manifest of the session directory
(1272 files, 210 frames) and of the 32 launcher logs was written before any console
run, together with an archive of the small files. The manifests are verifiable on the
development machine only.

| local manifest | sha256 |
|---|---|
| session files | e9c6139fc0ab9636b148b66d46c8f6a5a4ff8ee0af83b6bbd5016b4d8faaf39a |
| launcher logs | 72cf7fb9c7dea57ce51672980a0e8405ab5099c231a0b5f7793e2bf3ce271876 |
| small-file archive | fc750899f3cb5e68054527e50ed3de9eb8c24755225f5cf923c182ea8acafd3a |

Console runs write to their own per-run session directory, so this evidence is never
added to again.
