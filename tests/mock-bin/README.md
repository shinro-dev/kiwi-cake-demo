<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Mock binaries for testing the runner

These four shell scripts stand in for `cake-resident`, `admin-probe`,
`demo-plan` and `demo-preflight` so that the demo runner's own logic (key
handling, tamper, start, telemetry parsing, child kill, resident kill and
relaunch, clean stop) can be exercised on a machine where the real aarch64
binaries cannot run. They imitate the output lines the runner parses and
nothing else. They are test doubles, not Cake, and prove nothing about Cake.

`admin-probe` reads two knobs from the environment: `KC_MOCK_PROBE_HANG=1`
makes any `request` hang forever (`exec sleep 3600`) after its socket and
state checks, to exercise the wall-clock deadline in `kc_query`.
