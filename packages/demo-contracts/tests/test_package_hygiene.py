# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""This package obeys the repository's committed-text rules by construction, checked here so that a fixture or a schema
edit cannot break them unnoticed: the license header, no em-dash, no symbol or emoji characters, no home path, no
private source layout, no private-range address, no hardware address but the documentation one, no banned directory
name. Every forbidden string is ASSEMBLED at run time: this file must pass the same rules it checks.

Also the package manifest: the name, the engine range, the package manager and an exactly pinned generator.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parents[1]
# Tool droppings, not committed text. .pytest_cache earns its place here now that tests/test-contracts.sh runs this
# suite from the roster: running pytest by hand before tests/run-all.sh would otherwise leave a directory behind that
# turns the whole roster red, which reads as a repository defect and is nothing of the kind.
SKIPPED_DIRS = {"node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
HEADER = "Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE."
HEADER_SUFFIXES = {".py", ".sh", ".md", ".ts", ".tsx", ".css", ".html", ".mjs", ".yaml"}
BANNED_DIRS = {"state", "runs", "captures", "outputs", "recordings", "dist", "release", "datasets", "checkpoints", "wandb", "local-data"}

SLASH = "/"
LEAKS = {
    "a home path": re.compile(SLASH + "(?:home|Users)" + SLASH + "[A-Za-z0-9._-]"),
    "a private source layout": re.compile("(?:crates|vendor)" + SLASH + "[a-z]|fixtures" + SLASH + "gate"),
    "a private-range address": re.compile(r"192\." + r"168\.[0-9]+\.[0-9]+"),
}
HARDWARE_ADDRESS = re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")
DOCUMENTATION_HARDWARE_ADDRESS = ":".join(["00", "00", "5e", "00", "53", "00"])
DOTTED_QUAD = re.compile(r"(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])")
DOCUMENTATION_PREFIXES = ((192, 0, 2), (198, 51, 100), (203, 0, 113))


def owned_files() -> list[Path]:
    files = [path for path in PACKAGE.rglob("*") if path.is_file() and not SKIPPED_DIRS & set(path.relative_to(PACKAGE).parts)]
    return sorted(files + [REPO / "pnpm-workspace.yaml", REPO / ".nvmrc"])


class CommittedText(unittest.TestCase):
    def test_every_file_passes_the_committed_text_rules(self):
        files = owned_files()
        self.assertGreater(len(files), 100)
        for path in files:
            relative = str(path.relative_to(REPO))
            with self.subTest(file=relative):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(chr(0x2014), text, "em-dash")
                symbols = [hex(ord(ch)) for ch in text if 0x2600 <= ord(ch) <= 0x27BF or 0x1F300 <= ord(ch) <= 0x1FAFF]
                self.assertEqual(symbols, [], "symbol or emoji characters")
                for label, pattern in LEAKS.items():
                    self.assertIsNone(pattern.search(text), label)
                self.assertEqual([m for m in HARDWARE_ADDRESS.findall(text) if m.lower() != DOCUMENTATION_HARDWARE_ADDRESS], [], "a hardware address")
                for quad in DOTTED_QUAD.findall(text):
                    octets = tuple(int(part) for part in quad.split("."))
                    self.assertTrue(octets[:3] in DOCUMENTATION_PREFIXES or max(octets) > 255, f"{quad} is not a documentation address")
                self.assertEqual(BANNED_DIRS & set(Path(relative).parts), set(), "a banned directory name")
                if path.suffix in HEADER_SUFFIXES:
                    self.assertIn(HEADER, "\n".join(text.splitlines()[:3]), "the license header belongs in the first 3 lines")
                if path.suffix == ".json":
                    json.loads(text)
                    self.assertNotIn("Copyright", text.splitlines()[0], "JSON files carry no header")

    def test_addresses_appear_only_where_a_fixture_proves_they_are_refused(self):
        for path in owned_files():
            if DOTTED_QUAD.search(path.read_text(encoding="utf-8")):
                with self.subTest(file=str(path.relative_to(REPO))):
                    self.assertIn("invalid", path.relative_to(PACKAGE).parts, "only an invalid fixture may carry a (documentation) address")

    def test_no_image_log_or_binary_file_is_in_the_package(self):
        allowed = {".json", ".py", ".md", ".mjs", ".ts"}
        for path in owned_files():
            if PACKAGE in path.parents:
                with self.subTest(file=str(path.relative_to(REPO))):
                    self.assertIn(path.suffix, allowed)


class Manifest(unittest.TestCase):
    def test_package_json(self):
        manifest = json.loads((PACKAGE / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "@kiwi/demo-contracts")
        self.assertIs(manifest["private"], True)
        self.assertEqual(manifest["engines"], {"node": ">=22 <23"})
        self.assertEqual(manifest["packageManager"], "pnpm@10.4.1")
        self.assertEqual(set(manifest["scripts"]), {"generate", "check"})
        self.assertNotIn("dependencies", manifest, "types and schemas only: no runtime dependency")
        self.assertEqual(list(manifest["devDependencies"]), ["json-schema-to-typescript"])
        self.assertRegex(manifest["devDependencies"]["json-schema-to-typescript"], r"^15\.[0-9]+\.[0-9]+$", "pinned exactly, no range")
        for script in manifest["scripts"].values():
            self.assertIn("scripts/generate.mjs", script)
        self.assertTrue((PACKAGE / "scripts" / "generate.mjs").is_file())

    def test_workspace_files(self):
        workspace = (REPO / "pnpm-workspace.yaml").read_text(encoding="utf-8")
        for member in ("apps/console-web", "packages/demo-contracts"):
            self.assertIn(f"  - {member}\n", workspace)
        self.assertEqual((REPO / ".nvmrc").read_text(encoding="utf-8").strip(), "22")
        ignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
        for rule in ("node_modules/", "apps/console-web/build/", "*.tsbuildinfo", "apps/console-web/coverage/"):
            self.assertIn(rule, ignore)

    def test_the_python_package_imports_nothing_outside_the_standard_library(self):
        import ast
        import sys

        for path in (PACKAGE / "python" / "demo_contracts").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
                for name in names:
                    with self.subTest(file=path.name, module=name):
                        self.assertIn(name.split(".")[0], sys.stdlib_module_names)


if __name__ == "__main__":
    unittest.main()
