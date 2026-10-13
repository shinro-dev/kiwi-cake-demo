# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.bundle: the pinned run bundle and the run manifest.

WHY. A controller that is started from the working checkout runs whatever the checkout holds at the moment each module is
first imported, and the checkout changes while a run is in progress (an editor saves, a branch is switched). A run must be
able to say WHICH code it ran. So before a spawn the runtime copies the controller entry and everything it can import from
this repository into a content-addressed, read-only directory, and the child runs FROM that directory (cwd = bundle root).

WHAT GOES IN.
  - the controller entry and its static import closure, walked with ast across the repository's source directories (bin/,
    autonomy/, tools/). Function-level imports count: ast.walk visits every Import node, wherever it sits;
  - packages/demo-runtime/demo_runtime and packages/demo-contracts (python + schema), whole;
  - the helper files the controller ships to the robot host (the PI_FILES tuple of run_stage1_scan.py, read with ast, never
    by importing that module: it pulls in OpenCV).
  Repository-relative paths are kept, so a module that finds its siblings relative to its own file finds them in the bundle.

THE DIGEST. sha256 over the sorted list of "<relative path>\\0<sha256 of the file>\\n". Same files, same digest, same
directory: a bundle is built once and reused. A bundle is built in a temporary sibling and renamed into place, so a
half-written bundle never carries a digest as its name.

NAMED RESIDUALS. The closure is STATIC: a module imported by a computed name (importlib, __import__) is not seen. A module
name that exists in two source directories is bundled from both (a superset is safe; a guess is not). Third-party packages
are not copied; the manifest records the versions of the ones that shape a run. Model weights are never copied: the
manifest names the model, it does not contain it.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

CONTROLLER_ENTRY = "bin/laptop/manipulation/run_single_cube_cycle.py"
PI_FILES_SOURCE = "bin/laptop/perception/run_stage1_scan.py"
SOURCE_DIRS = ("bin", "autonomy", "tools")
WHOLE_TREES = (("packages/demo-runtime/demo_runtime", (".py",)), ("packages/demo-contracts/python", (".py",)), ("packages/demo-contracts/schema", (".json",)))
SKIPPED_DIR_NAMES = frozenset({"tests", "__pycache__", "node_modules"})
KEY_PACKAGES = ("lerobot", "torch", "transformers", "opencv-python-headless", "numpy", "pyzmq")
BUNDLE_INDEX = "BUNDLE.json"
_GIT_LOCATION_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_PREFIX", "GIT_NAMESPACE")


class BundleError(RuntimeError):
    """The bundle could not be built. The message names repository-relative files only."""


@dataclass(frozen=True)
class Bundle:
    digest: str
    root: Path
    files: tuple  # ((relative path, sha256), ...) sorted
    reused: bool


def _module_index(source_root: Path) -> tuple:
    """(modules by flat name, packages by directory name) over the source directories."""
    modules: dict = {}
    packages: dict = {}
    for top in SOURCE_DIRS:
        for directory, names, files in os.walk(source_root / top):
            names[:] = sorted(name for name in names if name not in SKIPPED_DIR_NAMES and not name.startswith("."))
            here = Path(directory)
            if "__init__.py" in files:
                packages.setdefault(here.name, []).append(here)
            for name in sorted(files):
                if name.endswith(".py") and name != "__init__.py":
                    modules.setdefault(name[:-3], []).append(here / name)
    return modules, packages


def _imported_names(path: Path, source_root: Path) -> set:
    try:
        tree = ast.parse(path.read_bytes(), filename=path.name)
    except (OSError, SyntaxError) as error:
        raise BundleError(f"{path.relative_to(source_root)} cannot be parsed for its imports ({type(error).__name__})") from None
    names = set()
    for node in ast.walk(tree):  # every Import node at any depth: a function-level import is still a dependency
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
            elif node.level > 0:  # relative: the sibling module or package next to this file
                names.add(("." * node.level) + (node.module or "").split(".")[0])
                names.update("." * node.level + alias.name for alias in node.names if not node.module)
    return names


def import_closure(entry: Path, source_root: Path) -> list:
    """Every repository file the entry can import, itself included, as sorted repository-relative POSIX paths."""
    source_root, entry = Path(source_root).resolve(), Path(entry).resolve()
    modules, packages = _module_index(source_root)
    seen, pending = set(), [entry]
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        for name in _imported_names(path, source_root):
            if name.startswith("."):
                base = path.parent
                for _ in range(len(name) - len(name.lstrip(".")) - 1):
                    base = base.parent
                stem = name.lstrip(".")
                candidates = [base / f"{stem}.py", base / stem / "__init__.py"] if stem else []
                pending.extend(candidate for candidate in candidates if candidate.is_file())
                continue
            pending.extend(modules.get(name, ()))
            for package_dir in packages.get(name, ()):
                pending.extend(sorted(package_dir.rglob("*.py")))
    return sorted(path.relative_to(source_root).as_posix() for path in seen)


def pi_files(source_root: Path) -> tuple:
    """The PI_FILES tuple, read from the source text. () when the file or the assignment is absent."""
    path = Path(source_root) / PI_FILES_SOURCE
    try:
        tree = ast.parse(path.read_bytes(), filename=path.name)
    except (OSError, SyntaxError):
        return ()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "PI_FILES" for target in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return ()
            return tuple(item for item in value if isinstance(item, str))
    return ()


def _whole_tree(source_root: Path, relative: str, suffixes: tuple) -> list:
    found = []
    for directory, names, files in os.walk(source_root / relative):
        names[:] = sorted(name for name in names if name not in SKIPPED_DIR_NAMES and not name.startswith("."))
        found.extend((Path(directory) / name).relative_to(source_root).as_posix() for name in sorted(files) if name.endswith(suffixes))
    return found


def bundle_file_list(source_root: Path, entry_relative: str = CONTROLLER_ENTRY, extra: Iterable = ()) -> list:
    source_root = Path(source_root).resolve()
    entry = source_root / entry_relative
    if not entry.is_file():
        raise BundleError(f"the controller entry {entry_relative} does not exist in the source checkout")
    files = set(import_closure(entry, source_root))
    if PI_FILES_SOURCE in files:
        files.update(pi_files(source_root))
    for relative, suffixes in WHOLE_TREES:
        files.update(_whole_tree(source_root, relative, suffixes))
    files.update(extra)
    for relative in files:
        target = (source_root / relative).resolve()
        if source_root not in target.parents or not target.is_file():
            raise BundleError(f"{relative} is named by the bundle plan and is not a file inside the source checkout")
    return sorted(files)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def digest_of(entries: Iterable) -> str:
    digest = hashlib.sha256()
    for relative, file_digest in sorted(entries):
        digest.update(relative.encode("utf-8") + b"\0" + file_digest.encode("ascii") + b"\n")
    return digest.hexdigest()


def make_tree_writable(root) -> None:
    """Undo the read-only bits, so that a bundle (or a directory that holds one) can be removed."""
    for directory, names, files in os.walk(root):
        for name in names + files:
            path = os.path.join(directory, name)
            if not os.path.islink(path):
                os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
    if os.path.isdir(root):
        os.chmod(root, os.stat(root).st_mode | stat.S_IWUSR)


def verify_bundle(root: Path, entries: Iterable) -> bool:
    try:
        return all(_sha256_file(Path(root) / relative) == file_digest for relative, file_digest in entries)
    except OSError:
        return False


def build_bundle(source_root, data_dir, entry_relative: str = CONTROLLER_ENTRY, extra: Iterable = ()) -> Bundle:
    """Build, or reuse, DATA/bundles/<digest>/. The copy is hashed AFTER it was written and compared with the plan, so a
    file that changed between the two reads fails the build instead of producing a bundle whose name lies."""
    source_root, bundles = Path(source_root).resolve(), Path(data_dir) / "bundles"
    relatives = bundle_file_list(source_root, entry_relative, extra)
    entries = tuple((relative, _sha256_file(source_root / relative)) for relative in relatives)
    digest = digest_of(entries)
    final = bundles / digest
    if final.is_dir():
        if verify_bundle(final, entries):
            return Bundle(digest, final, entries, True)
        raise BundleError("a bundle directory with this digest exists and its content does not match: refusing to run from it")
    bundles.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=bundles))
    try:
        for relative, _ in entries:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_root / relative, target)
        if not verify_bundle(staging, entries):
            raise BundleError("a source file changed while the bundle was being copied; nothing was kept")
        (staging / BUNDLE_INDEX).write_text(json.dumps({"schema": "demo-runtime-bundle/1", "digest": digest, "entry": entry_relative,
                                                          "files": [{"path": relative, "sha256": file_digest} for relative, file_digest in entries]}, indent=1) + "\n")
        for directory, names, files in os.walk(staging):
            for name in files:
                os.chmod(os.path.join(directory, name), 0o444)
            for name in names:
                os.chmod(os.path.join(directory, name), 0o555)
        os.chmod(staging, 0o555)
        try:
            os.rename(staging, final)
        except OSError:
            if final.is_dir() and verify_bundle(final, entries):  # another builder won the race with the same content
                return Bundle(digest, final, entries, True)
            raise BundleError("the finished bundle could not be moved into place") from None
        return Bundle(digest, final, entries, False)
    finally:
        if staging.exists():
            make_tree_writable(staging)
            shutil.rmtree(staging, ignore_errors=True)


# ---- the run manifest -----------------------------------------------------------------------------------------------------

def git_identity(source_root) -> dict:
    """{"revision", "dirty"} of the source checkout, or nulls when git cannot answer. Never raises, never returns a path."""
    env = {key: value for key, value in os.environ.items() if key not in _GIT_LOCATION_ENV}

    def run(*arguments) -> Optional[str]:
        try:
            result = subprocess.run(["git", "-C", os.fspath(source_root), *arguments], capture_output=True, text=True, env=env, timeout=20, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout if result.returncode == 0 else None

    revision, status = run("rev-parse", "HEAD"), run("status", "--porcelain")
    revision = revision.strip() if revision else None
    return {"revision": revision if revision and all(ch in "0123456789abcdef" for ch in revision) else None, "dirty": None if status is None else bool(status.strip())}


def package_versions(names: Iterable = KEY_PACKAGES) -> dict:
    versions = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def run_manifest(*, bundle: Bundle, source_root, contract_version: str, config=None, run_id: str, command_id: str, command_type: str, data_mode: str) -> dict:
    """What a run states about itself. Digests, versions and a model REFERENCE; never an address, an id, a path or argv."""
    config_digest_value, inputs, model = None, None, None
    if config is not None:
        from .config import config_digest, public_model_field
        from .preflight import PreflightInputError, input_digests

        config_digest_value = config_digest(config)
        try:
            inputs = input_digests(config)
        except PreflightInputError:
            inputs = None  # the preflight refuses a live run without its inputs; a manifest must not invent a digest of nothing
        model = {"identity": public_model_field(config.model.identity), "revision": public_model_field(config.model.revision, revision=True),
                 "identity_withheld": bool(config.model.identity) and public_model_field(config.model.identity) is None}
    return {
        "schema": "demo-runtime-run-manifest/1", "run_id": run_id, "command_id": command_id, "command_type": command_type, "data_mode": data_mode,
        "bundle_digest": bundle.digest, "bundle_file_count": len(bundle.files), "source": git_identity(source_root), "contract_version": contract_version,
        "config_digest": config_digest_value, "input_digests": inputs,
        "interpreter": {"implementation": sys.implementation.name, "version": ".".join(str(part) for part in sys.version_info[:3])},
        "packages": package_versions(), "model": model,
    }


# ---- the run export -------------------------------------------------------------------------------------------------------
#
# WHAT IT IS. One run, packaged so that it can be handed to somebody else: the manifest the run wrote about itself, the
# ordered records of that run, the frames those records point at, and bounded summaries. Everything in it is something the
# runtime ALREADY keeps; this packages, it does not measure, and it starts no second store.
#
# WHAT KEEPS IT SAFE AND BOUNDED. It is read-only. It carries no telemetry stream and no preview video, only counts. It
# carries at most max_images frames and at most size_limit_bytes of content, and a frame left out for either reason, or one
# the store could no longer resolve, is NAMED on the index as a stated gap rather than silently missing: an export that is
# short says so. Every byte still leaves the process through the runtime's sanitizer, which is what removes this machine's
# private values; nothing here is a second sanitizer.
#
# NAMED RESIDUAL. content_digest on an exported frame is the identity the RECORD gives that frame. This packaging does not
# recompute it and does not verify the bytes against it: it carries the stored frame as the store returns it.

EXPORT_SCHEMA = "demo-runtime-run-export/1"
# An export travels as ONE answer and is held whole in memory at both ends, so these bounds are what keep that honest
# rather than a guess at what is polite. An export that needs more than this needs a transport that streams, which this
# one deliberately is not; until there is one, a run that does not fit says on its index exactly what it left out.
EXPORT_SIZE_LIMIT_BYTES = 8 * 1024 * 1024
EXPORT_MAX_IMAGES = 32
EXPORT_GAP_REASONS = {
    "frame_not_available": "the observation store could not resolve this frame, so the export names it instead of carrying it",
    "export_size_limit": "the export reached the size it is allowed to carry before this frame, so the export names it instead of carrying it",
    "export_image_limit": "the export reached the number of frames it carries, so the export names it instead of carrying it",
}


def _compact(value) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=True))


def referenced_observations(records: Iterable) -> list:
    """Every observation id one run's records point at, in the order they were first referenced: the envelope's own
    observation_id, the ids a predicate names in its evidence, the ids an action names in evidence_refs, and the ids on
    any record's provenance. Nothing is invented: an id no record names is not in the export."""
    seen: list = []
    for event in records:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        provenance = event.get("provenance") if isinstance(event.get("provenance"), dict) else {}
        candidates = [event.get("observation_id")]
        for item in payload.get("evidence") or ():
            if isinstance(item, dict):
                candidates.extend(item.get("observation_ids") or ())
        candidates.extend(payload.get("evidence_refs") or ())
        candidates.extend(provenance.get("observation_ids") or ())
        for one in candidates:
            if isinstance(one, str) and one not in seen:
                seen.append(one)
    return seen


def export_summaries(records: Iterable) -> dict:
    """The bounded summaries, read from the run's OWN records and never from the state of the runtime that exports it: a
    run exported years later must summarise the same way. evidence_complete and evidence_gaps are null for a run that
    never recorded a run.finished, because a run that did not report cannot be summarised as complete or as whole."""
    complete, gaps, dropped = None, None, 0
    for event in records:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event.get("kind") == "run.finished":
            complete, gaps = payload.get("evidence_complete"), payload.get("evidence_gaps")
        elif event.get("kind") == "telemetry.dropped":
            dropped += int(payload.get("count") or 0)
    return {"evidence_complete": complete, "evidence_gaps": gaps, "dropped_telemetry": dropped, "cake_continuity": None}


def run_export(*, run_id: str, manifest: dict, records: list, image_of, contract_version: str,
               size_limit_bytes: int = EXPORT_SIZE_LIMIT_BYTES, max_images: int = EXPORT_MAX_IMAGES) -> dict:
    """{"index": ..., "records": [...], "images": [...]} for one run. records are that run's contract events in log order.

    image_of(observation_id) returns {"content_type", "bytes_b64"} for the STORED frame, or None when the store has none;
    the caller decides what the store is, and this function never reads a file itself.

    size_bytes is measured over what the export actually carries: the manifest and the records as they serialise, plus the
    base64 of each included frame. It excludes the index's own framing, which is the one thing that cannot be measured
    before it is built."""
    ordered = [event for event in records if isinstance(event, dict)]
    seqs = [event["seq"] for event in ordered if isinstance(event.get("seq"), int)]
    captured = {event.get("observation_id"): event.get("payload") or {} for event in ordered if event.get("kind") == "observation.captured"}
    size = _compact(manifest) + sum(_compact(event) for event in ordered)
    images, carried, gaps = [], [], []
    for position, observation_id in enumerate(referenced_observations(ordered)):
        payload = captured.get(observation_id) or {}
        entry = {"observation_id": observation_id, "content_digest": payload.get("content_digest"), "width_px": payload.get("width_px"),
                 "height_px": payload.get("height_px"), "included": False, "reason_code": None}
        found = image_of(observation_id) if position < max_images else None
        if position >= max_images:
            entry["reason_code"] = "export_image_limit"
        elif found is None:
            entry["reason_code"] = "frame_not_available"
        elif size + len(found["bytes_b64"]) > size_limit_bytes:
            entry["reason_code"] = "export_size_limit"
        else:
            size += len(found["bytes_b64"])
            carried.append({"observation_id": observation_id, "content_type": found["content_type"], "bytes_b64": found["bytes_b64"]})
            entry["included"] = True
        if not entry["included"]:
            gaps.append({"gap": "frame_missing", "detail": EXPORT_GAP_REASONS[entry["reason_code"]], "observation_ids": [observation_id]})
        images.append(entry)
    index = {
        "schema": EXPORT_SCHEMA, "contract_version": contract_version, "run_id": run_id, "manifest": manifest,
        "records": {"count": len(ordered), "first_seq": min(seqs) if seqs else None, "last_seq": max(seqs) if seqs else None,
                    "source_sessions": sorted({event["source_session"] for event in ordered if isinstance(event.get("source_session"), str)})},
        "images": images, "summaries": export_summaries(ordered), "gaps": gaps, "size_bytes": size, "size_limit_bytes": size_limit_bytes,
    }
    return {"index": index, "records": ordered, "images": carried}
