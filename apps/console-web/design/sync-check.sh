#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# Check a staged design import against the tokens it needs, and print the digests that pin it.
#
# It does NOT talk to the design integration: fetching is done by the agent through the claude-design tooling
# (read methods only), which restages apps/console-web/design/imported/. This is the gate that runs afterwards,
# by hand, before any staged import is built into the app.
#
# It fails when the design references a CSS custom property that the staged token sheet does not define. That is
# the exact condition under which the 256 KiB read cap on the design system's own stylesheet stops being
# harmless: see design/IMPORT.md.
#
# usage: bash apps/console-web/design/sync-check.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMPORTED="$HERE/imported"

[ -d "$IMPORTED" ] || { echo "FAIL no staged import at design/imported"; exit 1; }

echo "staged import:"
while IFS= read -r file; do
  printf '  %s  %s\n' "$(sha256sum "$file" | cut -c1-64)" "${file#"$IMPORTED"/}"
done < <(find "$IMPORTED" -type f | sort)

python3 - "$IMPORTED" <<'PY'
import pathlib
import re
import sys

imported = pathlib.Path(sys.argv[1])
designs = sorted(imported.glob("*.dc.html"))
if not designs:
    print("FAIL no .dc.html design document in the staged import")
    raise SystemExit(1)

tokens = set()
for design in designs:
    tokens |= set(re.findall(r"var\((--[a-zA-Z0-9-]+)", design.read_text(encoding="utf-8")))

defined = set()
for sheet in imported.rglob("*.css"):
    defined |= set(re.findall(r"^\s*(--[a-zA-Z0-9-]+)\s*:", sheet.read_text(encoding="utf-8"), re.M))

missing = sorted(token for token in tokens if token not in defined)
print(f"\ntokens referenced by the design: {len(tokens)}")
print(f"defined by the staged sheets:   {len(tokens) - len(missing)}")

# Utility classes are what the truncated part of the design system's stylesheet carries. The design styles
# inline, so this stays near zero; a revision that starts using them needs the full bundle and cannot get it
# through a capped read.
classes = set()
for design in designs:
    for group in re.findall(r'class="([^"]*)"', design.read_text(encoding="utf-8")):
        classes.update(group.split())
print(f"class attributes in the design: {len(classes)} {sorted(classes)}")

if missing:
    print("\nFAIL these tokens are referenced and not defined:")
    for token in missing:
        print(f"  {token}")
    print("\nThe design system's full stylesheet is larger than the integration's 256 KiB read cap, so it cannot")
    print("simply be refetched. Report this as a blocker rather than shipping a truncated stylesheet.")
    raise SystemExit(1)

print("\nsync-check: PASS")
PY
