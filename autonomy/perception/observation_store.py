#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""observation_store: persistence for world observations (raw frame +
validated record), kept local by a guard that fails closed.

Layout, per scan / atlas session:

  <root>/<session_id>/frames/<observation_id>.png    lossless raw frame
  <root>/<session_id>/<observation_id>.json          the observation record

Rules:
  - raw physical captures never enter the repository: the store refuses any
    directory that is inside the repository and not ignored by git
    (AGENTS.md). The check fails closed: if git cannot answer, the directory
    is refused. It is applied to the root, to the session directory and, at
    every save, to the two directories that are written, always by real path,
    so a symlink cannot stand in for an ignored or outside location.
    session_id and observation_id must each be one plain path component, so
    neither can climb out of the directory that was checked. The guard is
    aimed at operator mistakes: it does not defend against a directory being
    swapped between the check and the write;
  - a record is validated before anything is written, so an invalid record
    leaves no partial evidence behind;
  - a save is all or nothing for failures the process survives (a full disk,
    a permission error, an image encoder error): frame and record are written
    under partial names and hard linked into place, record last, and a failed
    save removes what it wrote, so there is no frame without its joints
    record, no truncated record, and the same id can be saved again. Every
    such failure is an ObservationStoreError, the one store failure a scan
    turns into an abort reason. NOT covered: power loss or a killed process
    (nothing is fsynced, and a leftover partial file is possible; load_all
    reports it), and a filesystem without hard links, where save refuses;
  - earlier evidence is never overwritten: a second save of the same
    observation id is refused, also when the first one lands mid-save;
  - a failed capture is still evidence: it is stored as a record with no
    frame and raw_frame_path null;
  - one unreadable or foreign .json does not cost the session: load_all skips
    it and lists it in last_load_skipped.

Reuses the raw-png-plus-json pattern of manipulation/pickup_diagnostics.py
without its detector coupling and without its overwrite-by-label behavior.
"""
import contextlib
import json
import os
import re
import subprocess

from world_observation import validate_observation

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_ROOT = os.path.join(REPO_ROOT, ".local-dev", "observations")
FRAMES_SUBDIR = "frames"
_PARTIAL_PREFIX = ".partial-"  # never a valid observation id: ids start with an alphanumeric


class ObservationStoreError(RuntimeError):
    pass


def _git_is_ignored(path: str, repo_root: str) -> bool:
    result = subprocess.run(["git", "-C", repo_root, "check-ignore", "-q", path], capture_output=True)
    if result.returncode not in (0, 1):
        raise OSError(f"git check-ignore failed with status {result.returncode}")
    return result.returncode == 0


def ensure_local_only(path: str, repo_root: str = REPO_ROOT, is_ignored=_git_is_ignored) -> None:
    """Raise unless `path` is outside the repository or ignored by git.
    Both sides are compared by their real paths: a symlink that merely looks
    like it is outside the checkout, or a symlinked spelling of the checkout
    itself, must not talk the guard out of asking git."""
    path, repo_root = os.path.realpath(path), os.path.realpath(repo_root)
    if os.path.commonpath([path, repo_root]) != repo_root:
        return
    try:
        ignored = is_ignored(path, repo_root)
    except Exception as error:  # noqa: BLE001 -- fail closed
        raise ObservationStoreError(f"cannot confirm {path!r} is ignored by git ({error}); refusing to store raw captures there")
    if not ignored:
        raise ObservationStoreError(f"{path!r} is inside the repository and not ignored by git; raw captures must stay local")


def _write_png(path: str, frame_bgr) -> None:
    import cv2

    if not cv2.imwrite(path, frame_bgr):
        raise ObservationStoreError(f"could not write frame {path!r}")


# A session id and an observation id each become exactly one path component.
# The first character is alphanumeric, so "", ".", ".." and hidden names are
# out, and no separator is in the class, so neither "../" nor an absolute path
# (which os.path.join would let replace the root) can get through.
_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _require_safe_component(field: str, value) -> None:
    if not isinstance(value, str) or not _SAFE_COMPONENT.fullmatch(value):
        raise ObservationStoreError(f"{field} {value!r} refused: it must be one plain path component matching {_SAFE_COMPONENT.pattern}")


class ObservationStore:
    def __init__(self, root_dir: str = DEFAULT_ROOT, session_id: str = "session", repo_root: str = REPO_ROOT, write_image=_write_png):
        _require_safe_component("session_id", session_id)
        ensure_local_only(root_dir, repo_root=repo_root)
        self.session_dir = os.path.join(os.path.abspath(root_dir), session_id)
        ensure_local_only(self.session_dir, repo_root=repo_root)
        self._repo_root = repo_root
        self._write_image = write_image
        self.last_load_skipped = []

    def frame_relative_path(self, observation_id: str) -> str:
        return f"{FRAMES_SUBDIR}/{observation_id}.png"

    def save(self, observation: dict, frame_bgr) -> dict:
        observation_id = observation.get("observation_id")
        _require_safe_component("observation_id", observation_id)
        reasons = validate_observation(observation)
        expected_frame_path = self.frame_relative_path(observation_id) if frame_bgr is not None else None
        if not reasons and observation["raw_frame_path"] != expected_frame_path:
            reasons.append(f"raw_frame_path {observation['raw_frame_path']!r} does not match the stored frame {expected_frame_path!r}")
        if reasons:
            raise ObservationStoreError(f"observation {observation_id!r} refused: {reasons}")

        record_path = os.path.join(self.session_dir, f"{observation_id}.json")
        frame_path = None if frame_bgr is None else os.path.join(self.session_dir, expected_frame_path)
        for existing in filter(None, (record_path, frame_path)):
            if os.path.lexists(existing):
                raise ObservationStoreError(f"{existing!r} already exists; earlier evidence is never overwritten")

        # Checked again here, for the directories that are about to be written:
        # either may have become a symlink since the store was constructed.
        frames_dir = os.path.join(self.session_dir, FRAMES_SUBDIR)
        for directory in (self.session_dir, frames_dir):
            ensure_local_only(directory, repo_root=self._repo_root)

        # Everything is written under a partial name first and only then hard
        # linked to its final name. os.link refuses an existing target, so the
        # no-overwrite rule holds even against a writer that appeared after
        # the check above, which os.replace would not give. The frame is placed
        # first and the record LAST: a record on disk means its frame is there.
        # The frame's partial name keeps the .png suffix because the image
        # writer picks its encoder from it; the record's does not end in .json
        # so nothing that globs for records can pick up a partial one.
        partial = f"{_PARTIAL_PREFIX}{os.getpid()}-{observation_id}"
        staged = [] if frame_path is None else [(os.path.join(frames_dir, f"{partial}.png"), frame_path)]
        staged.append((os.path.join(self.session_dir, f"{partial}.json.tmp"), record_path))
        placed, complete = [], False
        try:
            record_text = json.dumps(observation, indent=2) + "\n"
            os.makedirs(frames_dir, exist_ok=True)
            if frame_path is not None:
                self._write_image(staged[0][0], frame_bgr)
            with open(staged[-1][0], "w") as f:
                f.write(record_text)
            for partial_path, final_path in staged:
                try:
                    os.link(partial_path, final_path)
                except FileExistsError:
                    raise ObservationStoreError(f"{final_path!r} already exists; earlier evidence is never overwritten") from None
                placed.append(final_path)
            complete = True
        except ObservationStoreError:
            raise
        except Exception as error:  # noqa: BLE001 -- OSError, cv2.error, an injected writer: all are a store refusal to the caller
            raise ObservationStoreError(f"could not store observation {observation_id!r}: {type(error).__name__}: {error}") from error
        finally:
            # On failure this also takes back what THIS call placed (never a
            # file that was already there), so no frame is left without its
            # record and the same id can be saved again.
            for leftover in [partial_path for partial_path, _ in staged] + ([] if complete else placed):
                with contextlib.suppress(OSError):
                    os.unlink(leftover)
        return {"record": record_path, "frame": frame_path}

    def load_all(self) -> list:
        """Records in observation id order. A .json that is not a complete
        record for its own file name (truncated, foreign, unreadable) and any
        leftover of an interrupted save is skipped and listed in
        last_load_skipped as {"file", "reason"}: one bad file must not cost
        the whole session, and must not vanish silently either. Records are
        NOT re-validated against the current schema here."""
        self.last_load_skipped = []
        if not os.path.isdir(self.session_dir):
            return []
        records = []
        for name in sorted(os.listdir(self.session_dir)):
            if name.startswith(_PARTIAL_PREFIX):
                self.last_load_skipped.append({"file": name, "reason": "leftover of an interrupted save"})
                continue
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.session_dir, name)) as f:
                    record = json.load(f)
            except (OSError, ValueError) as error:  # ValueError covers JSONDecodeError and UnicodeDecodeError
                self.last_load_skipped.append({"file": name, "reason": f"unreadable: {type(error).__name__}: {error}"})
                continue
            if not isinstance(record, dict) or record.get("observation_id") != name[: -len(".json")]:
                self.last_load_skipped.append({"file": name, "reason": "not an observation record for this file name"})
                continue
            records.append(record)
        return records
