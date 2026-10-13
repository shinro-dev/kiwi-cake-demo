# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Validate JSON files against a contract schema: python3 -m demo_contracts NAME FILE [FILE...].

Exit 0 when every file is valid, 1 when any is not, 2 for a usage or schema error. Reads files only; opens no socket
and no device.
"""
from __future__ import annotations

import json
import sys

from . import SchemaError, validate


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python3 -m demo_contracts NAME FILE [FILE...]   (NAME: event, command, command_result, snapshot, capability, component, cake, cake_ladder, ...)", file=sys.stderr)
        return 2
    name, failures = argv[0], 0
    for path in argv[1:]:
        try:
            with open(path, encoding="utf-8") as handle:
                errors = validate(name, json.load(handle))
        except SchemaError as error:
            print(f"schema error: {error}", file=sys.stderr)
            return 2
        except (OSError, json.JSONDecodeError) as error:
            errors = [f"cannot read: {error}"]
        print(f"{'ok  ' if not errors else 'FAIL'} {path}")
        for line in errors:
            print(f"     {line}")
        failures += bool(errors)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
