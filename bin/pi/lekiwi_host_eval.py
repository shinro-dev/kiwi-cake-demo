#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# lekiwi_host_eval.py: the wrist-only, noninteractive host for autonomous
# manipulation-v0 policy evaluation. Combines the two patches already in
# production use, exactly as-is, with neither modified:
#
#   - lekiwi_host_noninteractive.py's calibrate-from-file-or-refuse override
#     (never a blind recalibration under a supervisor/no-TTY session).
#   - lekiwi_host_kinesthetic.py's wrist-only camera init override (keeps
#     only config.cameras["wrist"], pointed at the USB-serial-stable by-id
#     path, before LeKiwi.__init__ ever constructs a front OpenCVCamera).
#
# Deliberately NOT included: lekiwi_host_kinesthetic.py's post-connect
# torque-disable on the compliant arm motors. Autonomous evaluation needs
# every arm joint torqued so the policy's commanded positions actually reach
# the servos -- this host leaves LeKiwi.connect() exactly as stock, which
# torques every motor (verified in lekiwi.py's configure()).
#
# Written against lerobot at commit b4e2d0b61017a0db646a12c98a2df3837e37a1b9
# (version 0.6.1), same pin as lekiwi_host_noninteractive.py and
# lekiwi_host_kinesthetic.py.
import os
import sys

from lerobot.robots.lekiwi.lekiwi import LeKiwi

# Default matches the by-id path used for every physical pass recorded in
# docs/autonomous-demo/ -- override via env var on a different board/camera
# without touching this file. Behavior is unchanged unless the var is set.
WRIST_CAMERA_STABLE_PATH = os.environ.get(
    "LEKIWI_WRIST_CAMERA_PATH",
    "/dev/v4l/by-id/usb-Web_Camera_Web_Camera_202512181-video-index0",
)

_stock_init = LeKiwi.__init__


def _wrist_only_init(self, config) -> None:
    wrist_camera_config = config.cameras["wrist"]
    wrist_camera_config.index_or_path = WRIST_CAMERA_STABLE_PATH
    config.cameras = {"wrist": wrist_camera_config}
    _stock_init(self, config)


LeKiwi.__init__ = _wrist_only_init


def _calibrate_from_file_or_refuse(self) -> None:
    if not self.calibration:
        sys.exit(
            f"lekiwi_host: no calibration file was loaded for --robot.id {self.id!r}; "
            "refusing to calibrate with no terminal attached. Calibrate once by hand "
            "first (docs/reproduce-end-to-end.md)."
        )
    self.bus.write_calibration(self.calibration)


LeKiwi.calibrate = _calibrate_from_file_or_refuse

from lerobot.robots.lekiwi.lekiwi_host import main  # noqa: E402

if __name__ == "__main__":
    main()
