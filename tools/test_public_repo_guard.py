# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
from pathlib import Path
import importlib.util

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("guard",ROOT/"tools/public_repo_guard.py")
guard=importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)

def test_safe_text():
    assert not guard.scan_bytes("docs/example.md", b"host=example.invalid\nip=192.0.2.10\n")

def test_private_key_blocked():
    # Built from split literals so this fixture's own source text does not
    # itself match the private-key regex when the guard scans this file.
    marker = "-----BEGIN " + "PRIVATE KEY" + "-----"
    e=guard.scan_bytes("docs/x.txt", (marker + "\nabc\n").encode())
    assert e

def test_hf_token_blocked():
    e=guard.scan_bytes("docs/x.txt",b"token=hf_" + b"A"*30)
    assert e

def test_raw_dataset_path_blocked():
    e=guard.scan_bytes("datasets/a.json",b"{}")
    assert e
