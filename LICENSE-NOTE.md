<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# License note for maintainers

`LICENSE` is the Business Source License 1.1. Its three parameters were set
by Shinro SAS on 2026-09-08:

| Parameter | Value | Constraint from the license's own Covenants |
| --- | --- | --- |
| Additional Use Grant | personal, educational and non-commercial use of the Licensed Work permitted; commercial use requires a separate license (full text in `LICENSE`) | must only add rights, never restrict the rights the base license already grants |
| Change Date | 2026-09-08 | a date; the license also converts automatically four years after first public distribution of a version, whichever comes first |
| Change License | Apache License, Version 2.0 | GPL Version 2.0 or any later version, or a license compatible with it |

Why Apache License, Version 2.0 satisfies the Change License covenant: the
covenant (`LICENSE`, "Covenants of Licensor," item 1) requires a license
"compatible with GPL Version 2.0 or a later version," where compatible means
software under the Change License can be included in a program with software
under GPLv2 or a later version. The Apache Software Foundation's own
compatibility statement
(https://www.apache.org/licenses/GPL-compatibility.html) confirms Apache
License 2.0 code can be included in a GPLv3 program, though not a GPLv2-only
one. Because GPLv3 is "a later version" of GPL Version 2.0, that one-way
compatibility satisfies the covenant as written. Verified 2026-09-09 by
fetching the Apache Software Foundation's page directly rather than assumed.

The license text itself is reproduced verbatim from the MariaDB publication
of BSL 1.1 and must not be edited beyond the Parameters block. Per the
license's own Terms, this License applies separately to each version of the
Licensed Work released, and the Change Date may differ for a later release;
these values bind the version distributed from this repository at the time
they were set and are not retroactively changed by a future release setting
different ones.

`tools/build-release.sh` refuses to assemble a release while any
`[PLACEHOLDER` marker remains in `LICENSE`, unless it is run with
`--allow-license-placeholders`. No placeholder remains as of this note.

Binaries in this project are provided for evaluation only, without source and
without warranty of any kind, in the license's own terms. `NOTICE` carries
the third-party attributions the binaries require.
