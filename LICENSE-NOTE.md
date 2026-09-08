<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# License note for maintainers

`LICENSE` is the Business Source License 1.1 with three parameters that are
still placeholders and MUST be reviewed and set with counsel before anything
is published:

| Parameter | Current value | Constraint from the license's own Covenants |
| --- | --- | --- |
| Additional Use Grant | placeholder | either an additional grant that imposes no further restriction, or the exact text "None" |
| Change Date | placeholder | a date; the license also converts automatically four years after first public distribution of a version |
| Change License | placeholder | GPL Version 2.0 or any later version, or a license compatible with it |

The license text itself is reproduced verbatim from the MariaDB publication
of BSL 1.1 and must not be edited beyond the Parameters block.

`tools/build-release.sh` refuses to assemble a release while any
`[PLACEHOLDER` marker remains in `LICENSE`, unless it is run with
`--allow-license-placeholders`, so a release cannot carry them by accident.

Binaries in this project are provided for evaluation only, without source and
without warranty of any kind, in the license's own terms. `NOTICE` carries
the third-party attributions the binaries require.
