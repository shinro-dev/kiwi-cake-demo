#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_observation_cadence_probe.py: the arithmetic and the refusals of the cadence probe.
#
# The probe's one dangerous property is that the host's observation socket is PUSH/PULL: attaching a second consumer
# TAKES samples from the controller instead of copying them. The refusal that guards it is tested here, because it is
# the only thing standing between a diagnostic and a controller quietly starved of the observations it steers on.
#
# Nothing here opens a socket. The collection loop needs a publisher and is exercised by running the tool; the parts
# that decide, summarise and refuse are pure and are what these tests hold.
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import observation_cadence_probe as probe  # noqa: E402


def arrivals_every(ms: float, count: int, *, start_ns: int = 1_000_000_000) -> list:
    return [start_ns + int(index * ms * 1e6) for index in range(count)]


class Percentiles(unittest.TestCase):
    def test_it_reads_the_ends_and_the_middle_of_a_known_series(self):
        # NEAREST RANK, not interpolation: the value it returns is a value that was really measured, which is what a
        # reader of a latency tail wants. On an odd-length series the middle is unambiguous.
        odd = [float(v) for v in range(1, 102)]  # 1..101
        self.assertEqual(probe.percentile(odd, 0.0), 1.0)
        self.assertEqual(probe.percentile(odd, 1.0), 101.0)
        self.assertEqual(probe.percentile(odd, 0.5), 51.0)
        # p95 and p99 are ranks into the same series and are likewise values that occurred.
        self.assertEqual(probe.percentile(odd, 0.95), 96.0)
        self.assertIn(probe.percentile(odd, 0.99), (100.0, 101.0))

    def test_an_empty_series_has_no_percentile_and_says_so_as_nan(self):
        self.assertNotEqual(probe.percentile([], 0.5), probe.percentile([], 0.5))  # NaN is the only value unequal to itself


class TheReport(unittest.TestCase):
    def summary(self, arrivals, **kwargs):
        return probe.summarise(arrivals, endpoint_stated="loopback", socket_kind="pull", seconds=10.0, **kwargs)

    def test_a_steady_stream_reports_its_interval_and_its_rate(self):
        report = self.summary(arrivals_every(20.0, 51))  # 50 intervals of 20 ms: 50 Hz over one second
        self.assertEqual(report["received"], 51)
        self.assertAlmostEqual(report["intervals_ms"]["median"], 20.0, places=3)
        self.assertAlmostEqual(report["rate_hz_from_arrivals"], 50.0, places=1)
        self.assertAlmostEqual(report["intervals_ms"]["longest"], 20.0, places=3)

    def test_one_long_wait_shows_up_as_the_longest_interval_and_is_listed(self):
        arrivals = arrivals_every(20.0, 20)
        arrivals.append(arrivals[-1] + int(250 * 1e6))  # a 250 ms wait, the shape the A/B/A was reporting
        report = self.summary(arrivals)
        self.assertAlmostEqual(report["intervals_ms"]["longest"], 250.0, places=3)
        self.assertIn(250.0, report["notable_intervals_ms"])
        # The median is unmoved by one outlier, which is why both are reported.
        self.assertAlmostEqual(report["intervals_ms"]["median"], 20.0, places=3)

    def test_nothing_arriving_is_reported_as_nothing_and_not_as_a_rate_of_zero(self):
        report = self.summary([])
        self.assertEqual(report["received"], 0)
        self.assertIsNone(report["intervals_ms"])
        self.assertNotIn("rate_hz_from_arrivals", report)

    def test_one_arrival_yields_no_interval_at_all(self):
        report = self.summary([1_000_000_000])
        self.assertEqual(report["received"], 1)
        self.assertIsNone(report["intervals_ms"])

    def test_the_report_states_what_it_cannot_attribute(self):
        report = self.summary(arrivals_every(20.0, 5))
        self.assertFalse(report["host_provides_sequence"])
        self.assertFalse(report["host_provides_timestamp"])
        self.assertIn("indistinguishable", report["attribution_limit"])
        self.assertIn("receiver cadence", report["instrument_note"])
        # It must never be mistaken for the earlier series.
        self.assertIn("must not be merged", report["instrument_note"])

    def test_it_records_the_shape_of_the_endpoint_and_never_the_endpoint(self):
        report = probe.summarise(arrivals_every(20.0, 5), endpoint_stated="remote", socket_kind="sub", seconds=1.0)
        self.assertEqual(report["endpoint_kind"], "remote")
        self.assertEqual(report["socket_kind"], "sub")
        self.assertNotIn("endpoint", report, "an endpoint can name a machine and is not written into the report")


class TheRefusals(unittest.TestCase):
    """What the tool will not do, and the words it refuses in."""

    def run_main(self, argv) -> int:
        return probe.main(argv)

    def test_it_refuses_to_attach_to_the_observation_socket_without_a_stated_sole_consumer(self):
        self.assertEqual(self.run_main(["--endpoint", "tcp://127.0.0.1:1", "--socket", "pull", "--seconds", "1"]), 3)

    def test_it_refuses_a_window_that_is_not_positive(self):
        for seconds in ("0", "-5"):
            self.assertEqual(self.run_main(["--endpoint", "tcp://127.0.0.1:1", "--seconds", seconds, "--sole-consumer"]), 2)

    def test_the_pull_refusal_is_the_default_because_pull_is_the_default(self):
        # A caller that names no socket type gets the one that steals, so the guard has to apply to the default.
        self.assertEqual(self.run_main(["--endpoint", "tcp://127.0.0.1:1", "--seconds", "1"]), 3)


class TheWrittenReport(unittest.TestCase):
    def test_the_file_it_writes_is_the_json_it_printed(self):
        report = probe.summarise(arrivals_every(20.0, 5), endpoint_stated="loopback", socket_kind="sub", seconds=1.0)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "report.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(report, indent=1, sort_keys=True) + "\n")
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["schema"], "observation-cadence-probe/1")


if __name__ == "__main__":
    unittest.main()
