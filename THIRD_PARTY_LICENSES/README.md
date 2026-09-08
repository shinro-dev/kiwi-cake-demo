<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Third-party license texts

One directory per third-party component statically linked into the released
binaries, each holding that component's own license file or files exactly as
published with it. The list of components, versions and license identifiers
is in `NOTICE` at the repository root.

`rust-std/` holds the MIT and Apache-2.0 license texts of the Rust standard
library, which is linked into every binary.

Components that are used only at build time (procedural macro and
build-script crates) are not linked into any binary and are not listed here.
