#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""calibration_inventory: the explicit record of which camera/geometry
calibration exists, so no consumer can turn image pixels into metric
coordinates by accident.

Every required item is in exactly one of three states, and the states are
never collapsed:

  VERIFIED              a value exists AND has been checked against
                        independent physical ground truth
  AVAILABLE_UNVERIFIED  a value exists (e.g. a solver produced it) but
                        nothing independent has confirmed it yet
  MISSING               no value exists

An item that is absent from a loaded file is MISSING, never an error and
never usable. A consumer states what it needs with require(); if anything is
not VERIFIED the call raises CalibrationUnavailable naming each item and its
actual status. AVAILABLE_UNVERIFIED values are only handed out when the
caller explicitly opts in, and that opt-in can never make a MISSING item
usable.

Pure module: no hardware, no lerobot, no numpy. Numeric calibration results
are plain JSON values; raw calibration images stay in ignored local
directories (docs/autonomous-demo/measurement-experiment-spec.md).
"""
import json
import os
from dataclasses import dataclass
from typing import Any, Optional

VERIFIED = "VERIFIED"
AVAILABLE_UNVERIFIED = "AVAILABLE_UNVERIFIED"
MISSING = "MISSING"
STATUSES = (VERIFIED, AVAILABLE_UNVERIFIED, MISSING)

# Frames: BASE_FRAME is the robot frame of autonomous-single-cube-plan.md
# section 5. cartesian_lift's forward kinematics is expressed in the ARM
# base_link, which is a different frame until arm_base_to_base_frame exists.
REQUIRED_ITEMS = (
    "wrist_intrinsics",
    "wrist_distortion",
    "wrist_hand_eye",  # camera pose in the gripper_frame_link tool frame
    "front_intrinsics",
    "front_distortion",
    "front_to_base_extrinsics",  # front camera pose in BASE_FRAME
    "arm_base_to_base_frame",  # arm base_link pose in BASE_FRAME
    "support_plane",  # plane the cube and pad rest on, in BASE_FRAME
)

EMPTY_VERSION = "none"


class InvalidCalibrationInventory(ValueError):
    pass


class CalibrationUnavailable(RuntimeError):
    """Raised by CalibrationInventory.require(). `unavailable` maps each item
    that could not be supplied to its actual status."""

    def __init__(self, unavailable: dict):
        self.unavailable = dict(unavailable)
        detail = ", ".join(f"{name}={status}" for name, status in sorted(self.unavailable.items()))
        super().__init__(f"required calibration is not available: {detail}")


@dataclass(frozen=True)
class CalibrationItem:
    name: str
    status: str
    value: Any = None
    provenance: Optional[str] = None
    # Which image the value applies to, e.g. "direct-v4l 1280x720 unrotated".
    applies_to: Optional[str] = None

    def __post_init__(self):
        if self.status not in STATUSES:
            raise InvalidCalibrationInventory(f"{self.name}: unknown calibration status {self.status!r}")
        if self.status == MISSING:
            if self.value is not None:
                raise InvalidCalibrationInventory(f"{self.name}: a MISSING item may not carry a value")
            return
        if self.value is None:
            raise InvalidCalibrationInventory(f"{self.name}: status {self.status} requires a value")
        if not self.provenance:
            raise InvalidCalibrationInventory(f"{self.name}: status {self.status} requires provenance")

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "value": self.value,
            "provenance": self.provenance,
            "applies_to": self.applies_to,
        }


class CalibrationInventory:
    def __init__(self, items, version: str):
        self.version = str(version)
        self._items = {}
        for item in items:
            if item.name not in REQUIRED_ITEMS:
                raise InvalidCalibrationInventory(f"unknown calibration item {item.name!r}")
            if item.name in self._items:
                raise InvalidCalibrationInventory(f"duplicate calibration item {item.name!r}")
            self._items[item.name] = item
        for name in REQUIRED_ITEMS:
            self._items.setdefault(name, CalibrationItem(name=name, status=MISSING))

    @classmethod
    def empty(cls) -> "CalibrationInventory":
        return cls([], version=EMPTY_VERSION)

    def status(self, name: str) -> str:
        return self._items[name].status

    def statuses(self) -> dict:
        return {name: self._items[name].status for name in REQUIRED_ITEMS}

    def require(self, *names: str, allow_unverified: bool = False) -> dict:
        """Values for `names`, or CalibrationUnavailable. KeyError for a name
        that is not a REQUIRED_ITEMS member (a caller typo must not read as
        'missing calibration')."""
        accepted = (VERIFIED, AVAILABLE_UNVERIFIED) if allow_unverified else (VERIFIED,)
        unavailable = {}
        values = {}
        for name in names:
            item = self._items[name]
            if item.status in accepted:
                values[name] = item.value
            else:
                unavailable[name] = item.status
        if unavailable:
            raise CalibrationUnavailable(unavailable)
        return values

    def to_dict(self) -> dict:
        return {"version": self.version, "items": {name: self._items[name].to_dict() for name in REQUIRED_ITEMS}}

    def to_status_summary(self) -> dict:
        """Statuses only (no values): what an observation record embeds."""
        return {"version": self.version, "items": self.statuses()}

    @classmethod
    def from_dict(cls, data: dict) -> "CalibrationInventory":
        items = [
            CalibrationItem(
                name=name,
                status=fields["status"],
                value=fields.get("value"),
                provenance=fields.get("provenance"),
                applies_to=fields.get("applies_to"),
            )
            for name, fields in data.get("items", {}).items()
        ]
        return cls(items, version=data.get("version", EMPTY_VERSION))

    @classmethod
    def load(cls, path: str) -> "CalibrationInventory":
        """A nonexistent file is the empty inventory: no calibration has been
        produced yet, which is a state, not an error."""
        if not os.path.exists(path):
            return cls.empty()
        with open(path) as f:
            return cls.from_dict(json.load(f))
