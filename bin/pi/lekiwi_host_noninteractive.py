#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# lekiwi_host_noninteractive.py: the stock LeKiwi host, started without its
# calibration prompt, for use as the child the Cake supervisor spawns.
#
# The stock host (python -m lerobot.robots.lekiwi.lekiwi_host) calls
# LeKiwi.connect(), whose default calibrate=True runs LeKiwi.calibrate()
# whenever the live servo registers disagree with the loaded calibration
# file, and calibrate() asks a question on standard input with input().
# Under the supervisor there is no terminal: input() raises EOFError, the
# host exits, the supervisor restarts it, and after its restart bound the
# supervisor gives up (LIMITATIONS.md, "The stock host prompts for
# calibration on connect"). Calibrating beforehand does not prevent it,
# because the disagreement recurs.
#
# This file replaces exactly one method, LeKiwi.calibrate. When a
# calibration file was loaded for --robot.id, it is written to the servos
# without asking, which is what pressing ENTER at the prompt does. When none
# was loaded, the host exits with a message and writes nothing to any
# motor; it never runs a blind recalibration. Everything else is the stock
# host's main(), unchanged, with the stock host's own arguments.
#
# Written against lerobot at commit b4e2d0b61017a0db646a12c98a2df3837e37a1b9
# (version 0.6.1). Check that LeKiwi.calibrate still has the same shape
# before using another version.
import sys

from lerobot.robots.lekiwi.lekiwi import LeKiwi


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
