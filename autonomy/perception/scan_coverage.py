#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""scan_coverage: explicit bookkeeping of what a scan has and has not looked
at, so a later decision can say "the cube was not found, and the rear-right
sector has not been observed" before any base rotation is even considered
(autonomous-single-cube-plan.md section 7: no blind 360-degree base spin).

Initial representation, deliberately coarse:

  sector  one of twelve 30-degree azimuth bins around the arm, sector 0
          centered straight ahead, positive azimuth toward +y (left)
  band    the scan band name (FAR / MID / NEAR), a viewing elevation
  cell    (sector, band): usable observation ids, unusable ones, last time

Honesty rule: until the wrist camera is calibrated, the only geometry known
for a view is the FK azimuth and nominal depression of the TOOL axis in the
ARM base_link frame. A view therefore marks exactly the one sector its tool
azimuth falls in, and the summary states coverage_basis
NOMINAL_TOOL_AXIS_UNCALIBRATED with ground_footprint null. It says which way
the arm was pointed; it does not say which ground was imaged. The sector names
also assume the arm +x axis points robot-forward, which stays unverified until
arm_base_to_base_frame is calibrated. The metric footprint (image frustum on
the support plane minus the gripper occlusion mask) replaces this once
calibration exists.

An unusable view (no valid frame, or a dark frame) is recorded but never
counts as observed. Pure module: no hardware, no numpy.
"""
import math

SECTOR_WIDTH_DEG = 30.0
SECTOR_COUNT = 12
DEFAULT_BANDS = ("FAR", "MID", "NEAR")
FRAME = "ARM_BASE_LINK_FK"
COVERAGE_BASIS = "NOMINAL_TOOL_AXIS_UNCALIBRATED"

_NAME_BY_CENTER_DEG = {
    0: "front",
    30: "front-left",
    60: "front-left",
    90: "left",
    120: "rear-left",
    150: "rear-left",
    180: "rear",
    -150: "rear-right",
    -120: "rear-right",
    -90: "right",
    -60: "front-right",
    -30: "front-right",
}


def sector_index_for_azimuth(azimuth_deg: float) -> int:
    return int(math.floor((azimuth_deg + SECTOR_WIDTH_DEG / 2.0) / SECTOR_WIDTH_DEG)) % SECTOR_COUNT


def sector_center_deg(sector_index: int) -> int:
    center = int(sector_index * SECTOR_WIDTH_DEG)
    return center - 360 if center > 180 else center


def sector_name(sector_index: int) -> str:
    return _NAME_BY_CENTER_DEG[sector_center_deg(sector_index)]


class ScanCoverage:
    def __init__(self, bands=DEFAULT_BANDS):
        self.bands = tuple(bands)
        self._cells = {
            (sector, band): {"observation_ids": [], "unusable_observation_ids": [], "last_observed_utc": None}
            for sector in range(SECTOR_COUNT)
            for band in self.bands
        }
        # Filled only by from_observations(): stored records it could not place in any cell.
        self.skipped_records = []

    def mark(self, *, observation_id: str, tool_azimuth_deg: float, band: str, usable: bool, timestamp_utc: str) -> int:
        if band not in self.bands:
            raise ValueError(f"unknown scan band {band!r}; expected one of {self.bands}")
        sector = sector_index_for_azimuth(tool_azimuth_deg)
        cell = self._cells[(sector, band)]
        if usable:
            cell["observation_ids"].append(observation_id)
            cell["last_observed_utc"] = timestamp_utc
        else:
            cell["unusable_observation_ids"].append(observation_id)
        return sector

    def cell(self, sector_index: int, band: str) -> dict:
        cell = self._cells[(sector_index, band)]
        return {**cell, "observed": bool(cell["observation_ids"])}

    def _sector(self, sector_index: int) -> dict:
        return {
            "sector_index": sector_index,
            "name": sector_name(sector_index),
            "center_azimuth_deg": sector_center_deg(sector_index),
            "bands": {band: self.cell(sector_index, band)["observed"] for band in self.bands},
        }

    def observed_sectors(self) -> list:
        return [s for s in map(self._sector, range(SECTOR_COUNT)) if any(s["bands"].values())]

    def unobserved_sectors(self) -> list:
        return [s for s in map(self._sector, range(SECTOR_COUNT)) if not any(s["bands"].values())]

    def summary(self) -> dict:
        return {
            "coverage_basis": COVERAGE_BASIS,
            "frame": FRAME,
            "ground_footprint": None,
            "observed_sectors": self.observed_sectors(),
            "unexplored_sectors": self.unobserved_sectors(),
        }

    @classmethod
    def from_observations(cls, records, bands=DEFAULT_BANDS) -> "ScanCoverage":
        """Rebuild coverage from stored observation records. Records with no
        scan block (front camera, atlas stills) carry no scan coverage.

        Records are read back from disk without being re-validated, so one
        malformed record must never raise and block the rebuild for all the
        others. A record whose lifecycle or illumination block is missing or
        not an object is UNUSABLE (recorded, never observed): only a literal
        true counts, because a guess would turn an unknown view into coverage.
        A record that cannot be placed in a cell at all (not an object, no
        finite tool azimuth, unknown band) is skipped and listed in
        skipped_records with the reason."""
        coverage = cls(bands=bands)
        for record in records:
            if not isinstance(record, dict):
                coverage.skipped_records.append({"observation_id": None, "reason": f"record is not an object: {type(record).__name__}"})
                continue
            scan = record.get("scan")
            if not scan:
                continue
            problem = _placement_problem(scan, coverage.bands)
            if problem is not None:
                coverage.skipped_records.append({"observation_id": record.get("observation_id"), "reason": problem})
                continue
            coverage.mark(
                observation_id=record.get("observation_id"),
                tool_azimuth_deg=scan["tool_azimuth_deg"],
                band=scan["band"],
                usable=_flag(record.get("lifecycle"), "frame_valid") and _flag(record.get("illumination"), "usable"),
                timestamp_utc=record.get("timestamp_utc"),
            )
        return coverage


def _flag(block, key: str) -> bool:
    return isinstance(block, dict) and block.get(key) is True


def _placement_problem(scan, bands):
    """Why a stored scan block cannot be placed in a (sector, band) cell, or None."""
    if not isinstance(scan, dict):
        return f"scan is not an object: {type(scan).__name__}"
    azimuth = scan.get("tool_azimuth_deg")
    if isinstance(azimuth, bool) or not isinstance(azimuth, (int, float)) or not math.isfinite(azimuth):
        return f"scan.tool_azimuth_deg {azimuth!r} is not a finite number"
    if not isinstance(scan.get("band"), str) or scan["band"] not in bands:
        return f"unknown scan band {scan.get('band')!r}; expected one of {bands}"
    return None
