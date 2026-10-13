# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""console_api: the operator console's HTTP surface, and nothing else.

WHAT IT IS NOT. It owns no run, no motor, no task execution and no Cake semantics. It starts no controller, it holds no
lease, it never decides whether a command is allowed. Every command goes to the runtime daemon over its private Unix
socket (packages/demo-runtime/demo_runtime/rpc.py, RuntimeClient) and comes back as the runtime's own contract answer.
Capabilities are the RUNTIME's truth: this process never 'unlocks' anything. Evidence rules live in the runtime; the
browser renders what it is handed.

RESTARTABLE AT ANY TIME. The API holds nothing a run depends on: kill it in the middle of a cycle and the controller keeps
going, because the controller is the runtime's child and the runtime is not in the motion path either. A new API process
mints a new per-launch credential, so an open browser tab must be reloaded; nothing else changes.

BINDING AND SECRECY. The listener is 127.0.0.1 only. There is no LAN mode in v1: reach it from another machine with an ssh
forward. Every POST must carry the Host and Origin of the bound address, application/json, a custom header no cross-origin
form can set, and the per-launch credential. No CORS header is ever sent, so no other origin can read an answer. The
credential is random, minted at start, kept in memory and in one 0600 file outside the repository, and delivered to the
page only inside the served index.html.
"""
from __future__ import annotations

import sys
from pathlib import Path

API_VERSION = "0.1.0"
DEFAULT_PORT = 8770
LOOPBACK = "127.0.0.1"


def repository_root() -> Path:
    """The checkout this package was loaded from: <root>/apps/console-api/console_api/__init__.py."""
    return Path(__file__).resolve().parents[3]


def ensure_runtime_importable() -> None:
    """demo_runtime and demo_contracts live in packages/ of the same checkout. The API puts them on sys.path itself so
    that `python3 -m console_api` needs one path entry, exactly as the runtime does for its own contracts package."""
    root = repository_root()
    for candidate in (root / "packages" / "demo-runtime", root / "packages" / "demo-contracts" / "python"):
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.append(str(candidate))


ensure_runtime_importable()
