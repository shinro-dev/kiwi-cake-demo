#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_contract_v2_pins.py: the contract v2 numbers are COPIES of the proven executor's constants
# (bin/laptop/manipulation/run_single_cube_cycle.py), because the capability's controller module stays standard-library
# only and must not import the executor. A copy that drifts is a contract that lies, so each one is pinned equal here:
# the three motion constants behind ControllerConfig.contract_v2() and the schema file, and the two relay timeouts
# behind the observation source's max_age_ns. No robot, no network.
# Run directly: python3 autonomy/tests/test_contract_v2_pins.py
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bin", "laptop", "manipulation"))

import run_single_cube_cycle as cycle  # noqa: E402

from autonomy.motion import mobile_approach, target_observation_source  # noqa: E402
from autonomy.motion.mobile_approach_service import V2_FIELDS  # noqa: E402

SCHEMA = os.path.join(ROOT, "autonomy", "schemas", "target_observation.v2.schema.json")


class TheThreeMotionConstants(unittest.TestCase):
    def test_the_module_copies_equal_the_executor(self):
        self.assertEqual(mobile_approach.V2_VX_MPS, cycle.BASE_VX_MPS)
        self.assertEqual(mobile_approach.V2_ADVANCE_TOLERANCE_M, cycle.BASE_ADVANCE_TOLERANCE_MM / 1000.0)
        self.assertEqual(mobile_approach.V2_MAX_ADVANCE_M, cycle.BASE_MAX_TOTAL_ADVANCE_MM / 1000.0)

    def test_the_v2_configuration_is_built_from_them(self):
        config = mobile_approach.ControllerConfig.contract_v2()
        self.assertEqual(config.vx_max_mps, cycle.BASE_VX_MPS)
        self.assertEqual(config.envelope.x_max_m, cycle.BASE_MAX_TOTAL_ADVANCE_MM / 1000.0)
        self.assertEqual(config.handoff.x_max_m, cycle.BASE_ADVANCE_TOLERANCE_MM / 1000.0)
        self.assertAlmostEqual(config.kx, cycle.BASE_VX_MPS / (cycle.BASE_ADVANCE_TOLERANCE_MM / 1000.0 / 2.0))
        self.assertEqual(config.command_ttl_ns, 250_000_000)

    def test_the_schema_file_bounds_x_by_the_executor_total_advance(self):
        with open(SCHEMA, encoding="utf-8") as handle:
            schema = json.load(handle)
        self.assertEqual(schema["properties"]["x_m"]["maximum"], cycle.BASE_MAX_TOTAL_ADVANCE_MM / 1000.0)
        self.assertEqual(schema["properties"]["x_m"]["minimum"], 0)
        self.assertEqual(schema["properties"]["contract"]["const"], mobile_approach.CONTRACT_V2)
        self.assertEqual(schema["properties"]["frame"]["const"], mobile_approach.FRAME_ANCHOR_REACH)
        self.assertEqual(tuple(schema["required"]), V2_FIELDS)

    def test_the_executor_pulse_bound_is_the_one_the_gain_was_derived_for(self):
        """The gain saturates vx at the pulse speed for every advance beyond the tolerance: that is only the right
        rule while the executor bounds each pulse's duration and total travel itself, so those bounds must exist."""
        self.assertEqual(cycle.BASE_MAX_PULSES, 4)
        self.assertEqual(cycle.BASE_PULSE_S, (0.2, 0.8))
        self.assertGreater(cycle.BASE_ADVANCE_TOLERANCE_MM, 0.0)
        self.assertLess(cycle.BASE_ADVANCE_TOLERANCE_MM, cycle.BASE_MAX_TOTAL_ADVANCE_MM)


class TheTwoRelayTimeouts(unittest.TestCase):
    """The observation source derives max_age_ns from the perception deadline plus the relay's bounded waits
    (2 x admin + 1 x link); its named copies of those two timeouts must equal the executor's."""

    def test_the_source_copies_equal_the_executor(self):
        self.assertEqual(target_observation_source.CAPABILITY_ADMIN_TIMEOUT_S, cycle.CAPABILITY_ADMIN_TIMEOUT_S)
        self.assertEqual(target_observation_source.CAPABILITY_LINK_TIMEOUT_S, cycle.CAPABILITY_LINK_TIMEOUT_S)


if __name__ == "__main__":
    unittest.main()
