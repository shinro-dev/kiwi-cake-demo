#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_base_rest_replay.py: the base-at-rest rule of autonomy/manipulation/base_rest.py replayed over the
# RECORDED base reads themselves (operator ruling 2026-09-27, docs/autonomous-demo/decision-log.md).
#
# LOCAL ONLY: skipped unless KC_EVIDENCE_ROOT names a directory of evidence. Everything under it is walked (symbolic links
# are followed) and read in memory; nothing is written, and no path of the evidence is printed. It reads:
#   - event streams, as the console API serves them (*.events.json, {"events": [...]}) or as the runtime writes them
#     (controller-events.jsonl): the measured.state base velocity, every planned base pulse, every base_zero evaluation;
#   - controller output, as the console API serves it (*.output.json, {"text": ...}) or as text (*.log, *.txt): every printed
#     base read and every BASE_MEASURED pad measurement of a pulse;
#   - observation records (*.json with robot_state.base_velocity, bare or under "data") and robot-runtime-data archives
#     (*.tgz, observation members only).
# It proves: every read recorded at rest decomposes into whole velocity counts and is at rest under the rule (the former
# per-axis band is shown rejecting some of the SAME reads, so the replay is not vacuous); every recorded base pulse, by its
# commanded speed and by its pad-measured rate, is not at rest. No base velocity read DURING a pulse is recorded anywhere
# (the primitive's own stop reads are named by id only), which is why the motion side is the commanded and measured rate.
import json
import os
import re
import sys
import tarfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "manipulation"))

import base_rest  # noqa: E402

EVIDENCE_ROOT_ENV = "KC_EVIDENCE_ROOT"
PRINTED_READ = re.compile(r"\{'x\.vel': ([-0-9.e]+), 'y\.vel': ([-0-9.e]+), 'theta\.vel': ([-0-9.e]+)\}")
PAD_MEASURED = re.compile(r"BASE_MEASURED (\{[^\n]*\})")
FORMER_BAND = {"x": 0.0025, "y": 0.0030, "theta": 0.70}  # scan_world.BASE_ZERO_TOLERANCE until 2026-09-27
MIN_READS = 500  # the evidence of 2026-09-19 to 2026-09-27 holds 995; fewer means the root is not that evidence
MIN_PULSES = 20


def _former_band_rejects(x, y, theta) -> bool:
    return abs(x) > FORMER_BAND["x"] or abs(y) > FORMER_BAND["y"] or abs(theta) > FORMER_BAND["theta"]


class Evidence:
    def __init__(self, root: str):
        self.reads, self.commanded, self.measured_rates, self.evaluations, self.unreadable = {}, [], [], 0, []
        for directory, _names, files in os.walk(root, followlinks=True):
            for name in sorted(files):
                path = os.path.join(directory, name)
                try:
                    self._file(path, name)
                except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
                    if name.endswith((".events.json", ".output.json", ".jsonl", ".tgz")) or (name.startswith("obs-") and name.endswith(".json")):  # a kind this replay reads
                        self.unreadable.append(f"{name}: {type(error).__name__}")

    def _read(self, key, x, y, theta) -> None:
        self.reads.setdefault(key, (float(x), float(y), float(theta)))  # one sample, however many copies of it exist

    def _events(self, events, source) -> None:
        for event in events:
            payload = event.get("payload") or {}
            if event.get("kind") == "measured.state" and payload.get("base_velocity"):
                base = payload["base_velocity"]
                self._read(("event", event.get("run_id"), event.get("seq")), base["x_mps"], base["y_mps"], base["theta_dps"])
            elif event.get("kind") == "action.planned" and payload.get("action_type") == "base_pulse":
                parameters = {entry["name"]: entry["value"] for entry in payload.get("parameters", [])}
                if parameters.get("vx_mps") is not None:
                    self.commanded.append(((event.get("run_id"), event.get("action_id")), float(parameters["vx_mps"])))
            elif event.get("kind") == "predicate.evaluated" and payload.get("predicate") == "base_zero":
                self.evaluations += 1

    def _text(self, text, source) -> None:
        for number, line in enumerate(text.splitlines(), 1):
            for match in PRINTED_READ.finditer(line):
                self._read(("printed", source, number, match.start()), *match.groups())
            for match in PAD_MEASURED.finditer(line):
                measured = json.loads(match.group(1))
                if measured.get("rate_mm_s") is not None:
                    self.measured_rates.append(((source, number), float(measured["rate_mm_s"])))

    def _record(self, record, source) -> None:
        record = record.get("data", record) if isinstance(record, dict) else None
        base = ((record or {}).get("robot_state") or {}).get("base_velocity")
        if base:
            self._read(("observation", record.get("observation_id") or source), base["x.vel"], base["y.vel"], base["theta.vel"])

    def _file(self, path, name) -> None:
        if name.endswith(".events.json"):
            document = json.load(open(path, encoding="utf-8"))
            self._events(document["events"], name)
        elif name == "controller-events.jsonl":
            self._events([json.loads(line) for line in open(path, encoding="utf-8") if line.strip()], path)
        elif name.endswith(".output.json"):
            document = json.load(open(path, encoding="utf-8"))
            self._text(document.get("text") or "", document.get("run_id") or name)
        elif name.endswith((".log", ".txt")):  # a run's stdout.log, and the controller logs of 2026-09-19 and 2026-09-20
            self._text(open(path, encoding="utf-8", errors="replace").read(), path)
        elif name.endswith(".tgz"):
            with tarfile.open(path, "r:gz") as archive:
                for member in archive.getmembers():
                    base = os.path.basename(member.name)
                    if member.isfile() and "/observations/" in "/" + member.name and base.startswith("obs-") and base.endswith(".json"):
                        handle = archive.extractfile(member)
                        if handle is not None:
                            self._record(json.loads(handle.read().decode("utf-8")), base)
        elif name.endswith(".json") and ("obs-" in name or "observation" in name):
            self._record(json.load(open(path, encoding="utf-8")), name)


@unittest.skipUnless(os.environ.get(EVIDENCE_ROOT_ENV), f"{EVIDENCE_ROOT_ENV} is not set: the recorded evidence is local only")
class TheRecordedReadsReplayed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence = Evidence(os.environ[EVIDENCE_ROOT_ENV])

    def test_there_is_enough_evidence_to_mean_something(self):
        self.assertEqual([], self.evidence.unreadable, "an evidence file of a kind this replay reads could not be read")
        self.assertGreaterEqual(len(self.evidence.reads), MIN_READS)
        self.assertGreaterEqual(len(self.evidence.commanded), MIN_PULSES)
        self.assertGreaterEqual(len(self.evidence.measured_rates), MIN_PULSES)

    def test_every_recorded_read_is_whole_counts_on_the_wheels(self):
        for key, body in self.evidence.reads.items():
            counts = base_rest.wheel_counts(*body)
            self.assertLess(max(abs(c - round(c)) for c in counts), 1e-6, f"a read that is not whole counts: {body}")

    def test_no_recorded_read_at_rest_is_refused_and_the_former_band_refused_some_of_them(self):
        refused = [body for body in self.evidence.reads.values() if not base_rest.at_rest(*body)]
        self.assertEqual([], refused)
        formerly = [body for body in self.evidence.reads.values() if _former_band_rejects(*body)]
        self.assertTrue(formerly, "no read of this evidence was refused by the former band: the replay would prove nothing about the trip")
        self.assertEqual(1, max(max(abs(round(c)) for c in base_rest.wheel_counts(*body)) for body in self.evidence.reads.values()))

    def test_every_recorded_pulse_is_not_at_rest(self):
        for key, vx in self.evidence.commanded:
            with self.subTest(pulse=key):
                self.assertFalse(base_rest.at_rest(vx, 0.0, 0.0))
        for key, rate in self.evidence.measured_rates:
            with self.subTest(measured=key):
                self.assertFalse(base_rest.at_rest(rate / 1000.0, 0.0, 0.0))
                self.assertFalse(base_rest.at_rest(-rate / 1000.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
