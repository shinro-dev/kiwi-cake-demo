<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Contributing

## Help make LeKiwi failures easier to explain

The most useful feedback is a concrete run: what you were investigating,
which evidence helped, and what was still missing. Start with
[Diagnosing a run](docs/diagnosing-a-run.md) for the available files and
their interpretation.

## Issues are welcome

Open an issue for:

- a bug in the scripts or in the documentation;
- a reproduction report: the demo ran, or did not, on your board, with the
  facts `bin/doctor.sh` prints;
- a correction to a runbook, where a step did not match what you saw;
- a workflow report: whether the lifecycle events and identity comparisons
  answered a question that your existing LeRobot or systemd logs did not.

Use the [issue tracker](https://github.com/shinro-dev/kiwi-cake-demo/issues)
and include:

- **Goal and result:** the question you were investigating, what you
  expected, the first failed step and what happened instead.
- **Environment:** release version, source revision, `bin/doctor.sh`
  output, and relevant local changes. For segment 2, include LeRobot and
  Python versions on both machines and whether the clamp fix was applied.
- **Evidence:** the commands used, selected transcript excerpts, the first
  host error and relevant lifecycle events. Keep step names and event
  sequence numbers intact.
- **Robot context:** stub or real host, whether teleoperation ran or was
  skipped, and any physical observations. An operator confirmation is
  different from a captured measurement.
- **Remaining work:** what you still had to inspect manually, or which
  documentation step was unclear.

Do not attach an entire run directory or `state/`: they can contain
private signing keys and local configuration. Select text excerpts and
remove private paths, hostnames, addresses and device identifiers, using
consistent placeholders. There is no automatic support-bundle generator.

## Pull requests are not accepted

The Cake runtime in this repository is binary-only, under the Business
Source License 1.1. Its scripts and documents are maintained from a
private tree and exported here. Under the current maintenance policy,
pull requests against this repository are closed with a request to open
an issue instead. Describe the proposed change and its reason in the
issue; accepted changes are exported with a release-notes entry.

## Security reports

Report a suspected vulnerability privately as [SECURITY.md](SECURITY.md)
describes, rather than putting reproduction details in a public issue.
