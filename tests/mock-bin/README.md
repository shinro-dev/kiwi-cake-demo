<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Mock binaries for testing the runner

These four shell scripts stand in for `cake-resident`, `admin-probe`,
`demo-plan` and `demo-preflight` so that the demo runner's own logic (key
handling, tamper, start, telemetry parsing, child kill, resident kill and
relaunch, clean stop) can be exercised on a machine where the real aarch64
binaries cannot run. They imitate the output lines the runner parses and
nothing else. They are test doubles, not Cake, and prove nothing about Cake.
They need GNU coreutils 8.31 or later (`env --default-signal`).

`admin-probe` reads two knobs from the environment: `KC_MOCK_PROBE_HANG=1`
makes any `request` hang forever (`exec sleep 3600`) after its socket and
state checks, to exercise the wall-clock deadline in `kc_query`;
`KC_MOCK_PROBE_BLANK_FIELDS=1` answers `get-status` and `list-slots`
without the `build_identity`, `session_uuid` and `plan_digest` lines, to
exercise the identity-shape helpers on an incomplete reply.

`cake-resident`, on SIGTERM, prints a `shutdown drain` line in the shape of
the board record quoted in `docs/stopping-and-cleanup.md` and then
`cake-resident: stopped`, so that `tests/unit-stop-signals.sh` can dry-run
its journal checks against the mock (`KC_UNIT_TEST_ALLOW_MOCK=1`); the
numbers in that line are copied, not measured.
