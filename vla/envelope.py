# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""vla.envelope: the limits derived from the recorded demonstrations (tools/vla/derive_envelope.py), loaded once."""
import json
from pathlib import Path

ENVELOPE = json.loads((Path(__file__).resolve().parent / "envelope.json").read_text())
JOINTS = tuple(ENVELOPE["joints"])
ACTION_KEYS = tuple(f"arm_{j}.pos" for j in JOINTS)
