# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""THE FIXTURE-DIRECTORY HOOK of the stub runtime (overnight plan 2026-09-26, track U2, the local replay stage).

A directory of recorded runs, `<run>.events.json` with an optional `<run>.output.json` beside it, is served through the
stub runtime as that runtime's finished-run list, so the console can open each recorded run as a replay without a
robot. The records stay where they are: the hook reads a directory it is given and commits nothing. These tests build
their records at run time in a temporary directory.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import ApiCase, StubRuntime, contract_errors, event_object, load_fixture_directory, serve_fixture_directory  # noqa: E402

# A run whose id sorts BEFORE the recorded ones but which started LAST: the list must follow the start time, not the id.
LATE_RUN = "a-run-recorded-last"


def _run(run_id: str, outcome: str, *, started_wall_utc: str, finished_wall_utc: str, command_type: str = "start_cycle", reason_code=None) -> dict:
    started = event_object(1, kind="run.started", run_id=run_id,
                           payload={"command_id": "c0ffee0123456789", "command_type": command_type, "data_mode": "live", "plan_only": False, "holding": False})
    started["t_wall_utc"] = started_wall_utc
    finished = event_object(2, kind="run.finished", run_id=run_id, payload={
        "outcome": outcome, "exit_status": 0 if outcome == "placed_new" else 5,
        "predicates": {"object_secured": None, "placed": None, "clearance_target_met": None, "base_returned": None},
        "automatic_retries": 0, "task_control_interventions": 0, "evidence_complete": False, "evidence_gaps": 1,
        "reason_code": reason_code, "phase": None, "failure": None})
    finished["t_wall_utc"] = finished_wall_utc
    return {"ok": True, "run_id": run_id, "events": [started, finished], "origins": []}


class TestFixtureDirectory(ApiCase):
    def make_stub(self) -> StubRuntime:
        stub = StubRuntime(data_mode="live")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / "console-20260926T201540Z-mb3c1048.events.json").write_text(json.dumps(_run("console-20260926T201540Z-mb3c1048", "placed_new",
                                                                                   started_wall_utc="2026-09-26T20:15:40.000000Z",
                                                                                   finished_wall_utc="2026-09-26T20:21:40.000000Z")))
        (root / "console-20260926T201540Z-mb3c1048.output.json").write_text(json.dumps(
            {"ok": True, "run_id": "console-20260926T201540Z-mb3c1048", "stream": "stdout", "text": "[STATE] SEARCH {}\n"}))
        (root / "console-20260926T191919Z-m8c5f9e9.events.json").write_text(json.dumps(_run("console-20260926T191919Z-m8c5f9e9", "failed", reason_code="grasp_missed",
                                                                                   started_wall_utc="2026-09-26T19:19:19.000000Z",
                                                                                   finished_wall_utc="2026-09-26T19:23:30.000000Z")))
        (root / f"{LATE_RUN}.events.json").write_text(json.dumps(_run(LATE_RUN, "failed", reason_code="align_cube_not_detected",
                                                                     started_wall_utc="2026-09-26T21:31:00.000000Z",
                                                                     finished_wall_utc="2026-09-26T21:33:00.000000Z")))
        (root / "mission-0123456789ab.json").write_text("{}")  # not a run: ignored
        self.loaded = load_fixture_directory(stub, root)
        return stub

    async def test_every_recorded_run_is_listed_newest_first_with_its_outcome(self):
        # Newest first by the recorded start time: the run started last comes first although its id sorts first.
        self.assertEqual(self.loaded, [LATE_RUN, "console-20260926T201540Z-mb3c1048", "console-20260926T191919Z-m8c5f9e9"])
        listing = await self.json_of(await self.client.get("/api/history"))
        runs = listing["runs"]
        self.assertEqual([run["run_id"] for run in runs], self.loaded)
        self.assertEqual([run["outcome"] for run in runs], ["failed", "placed_new", "failed"])
        placed = runs[1]
        self.assertEqual(placed["command_type"], "start_cycle")
        self.assertEqual(placed["data_mode"], "live")
        self.assertEqual(placed["started_wall_utc"], "2026-09-26T20:15:40.000000Z")
        self.assertEqual(placed["finished_wall_utc"], "2026-09-26T20:21:40.000000Z")
        self.assertFalse(placed["adopted"])

    async def test_a_listed_run_replays_its_recorded_events_and_output(self):
        events = await self.json_of(await self.client.get("/api/history/console-20260926T191919Z-m8c5f9e9/events"))
        self.assertEqual([event["kind"] for event in events["events"]], ["run.started", "run.finished"])
        for event in events["events"]:
            self.assertEqual(contract_errors("event", event), [])
        output = await self.json_of(await self.client.get("/api/history/console-20260926T201540Z-mb3c1048/output?stream=stdout"))
        self.assertEqual(output["text"], "[STATE] SEARCH {}\n")
        missing = await self.client.get("/api/history/console-20260926T191919Z-m8c5f9e9/output?stream=stdout")
        self.assertEqual(missing.status, 404)

    async def test_a_directory_that_does_not_exist_loads_nothing(self):
        stub = StubRuntime()
        self.assertEqual(load_fixture_directory(stub, Path(self.directory.name) / "absent"), [])
        self.assertEqual(stub.history_runs, [])


class TestReplayStagePorts(unittest.TestCase):
    def test_the_consoles_own_ports_are_refused_before_anything_binds(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch("aiohttp.web.run_app") as run_app:
            for port in (8770, 8771):
                with self.assertRaises(ValueError) as refusal:
                    serve_fixture_directory(directory, port=port)
                self.assertIn(str(port), str(refusal.exception))
            run_app.assert_not_called()

    def test_another_port_is_served_on_loopback(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch("aiohttp.web.run_app") as run_app:
            serve_fixture_directory(directory, port=8792)
            run_app.assert_called_once()
            self.assertEqual(run_app.call_args.kwargs["host"], "127.0.0.1")
            self.assertEqual(run_app.call_args.kwargs["port"], 8792)
