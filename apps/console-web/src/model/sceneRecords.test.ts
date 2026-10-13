// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE RECORDS THE SCENE DRAWS, NAMED (redesign verdict item 8). Each run contributes its own records only, the scan is a
// named run from the runtime's list, and the Scene says which runs it drew. The live fold's carried records of an
// earlier run never reach the map.

import { describe, expect, it } from 'vitest';
import type { ContractEvent } from '../contracts/envelope';
import type { HistoryEntry } from '../api/types';
import { buildFromSceneRun, buildSceneScanRun } from '../api/syntheticScenarios';
import { foldEvents, initialState } from './reducer';
import { foldSceneMap } from './sceneMap';
import { newestScanRun, sceneRecords, sceneRecordsLine } from './sceneRecords';
import type { ViewState } from './types';

const T0 = Date.parse('2026-09-27T14:16:00Z');
const DAY = '2026-09-27';

function stamped(events: readonly ContractEvent[], fromMs: number): ContractEvent[] {
  return events.map((event, index) => ({ ...event, t_wall_utc: new Date(fromMs + index * 100).toISOString() }) as ContractEvent);
}

/** A scan (run 1), then a cycle (run 2), numbered as one runtime log numbers them. */
function scanThenCycle(): { scan: ContractEvent[]; cycle: ContractEvent[] } {
  const scan = stamped(buildSceneScanRun(1, 0, 1_000_000_000).steps.map((step) => step.event), T0);
  const last = scan[scan.length - 1] as ContractEvent;
  const cycle = stamped(buildFromSceneRun(2, last.seq, last.t_mono_ns).steps.map((step) => step.event), T0 + 60_000);
  return { scan, cycle };
}

function entry(runId: string, commandType: string, started: string): HistoryEntry {
  return { run_id: runId, command_id: null, command_type: commandType, started_wall_utc: started, finished_wall_utc: null, outcome: null, exit_status: null, evidence_complete: null, evidence_gaps: null, artifact_digest: null, adopted: false } as HistoryEntry;
}

describe('the records the Scene draws', () => {
  const { scan, cycle } = scanThenCycle();
  const scanId = scan[0]?.run_id as string;
  const cycleId = cycle[0]?.run_id as string;
  const scanStart = scan[0]?.t_wall_utc as string;
  const cycleStart = cycle[0]?.t_wall_utc as string;
  const watched = foldEvents(initialState(), [...scan, ...cycle]);
  const cycleOnly = foldEvents(initialState(), cycle);
  const scanFold = foldEvents(initialState(), scan);
  const history = [entry(scanId, 'scan_scene', scanStart)];

  it('draws the same map from the watching fold and from the run read back on its own', () => {
    const held = (runId: string): ViewState | null => (runId === scanId ? scanFold : null);
    const playing = sceneRecords({ drawnRuns: [{ run_id: cycleId, view: watched }], history, live: watched, held });
    const reloaded = sceneRecords({ drawnRuns: [{ run_id: cycleId, view: cycleOnly }], history, live: cycleOnly, held });
    expect(playing.map((run) => run.run_id)).toEqual([scanId, cycleId]);
    expect(reloaded).toEqual(playing);
    expect(foldSceneMap(reloaded.flatMap((run) => run.events))).toEqual(foldSceneMap(playing.flatMap((run) => run.events)));
    expect(sceneRecordsLine(playing, DAY)).toBe(`Drawn from the records of the Scan scene run ${scanId}, started 14:16:00Z, and run ${cycleId} (Start), started 14:17:00Z.`);
  });

  it("never draws an earlier run's records carried by the live fold when no scan is named", () => {
    const runs = sceneRecords({ drawnRuns: [{ run_id: cycleId, view: watched }], history: [], live: cycleOnly, held: () => null });
    expect(runs.map((run) => run.run_id)).toEqual([cycleId]);
    expect(runs[0]?.events.every((event) => event.run_id === cycleId)).toBe(true);
    expect(foldSceneMap(runs[0]?.events ?? []).views).toEqual([]);
  });

  it('counts a scan this tab watched before the run list names it', () => {
    expect(newestScanRun([], watched)?.run_id).toBe(scanId);
    expect(newestScanRun([], cycleOnly)).toBeNull();
  });

  it('draws a scan newer than every drawn run alone, as the newest record of the scene', () => {
    const laterScan = foldEvents(initialState(), stamped(scan, T0 + 120_000));
    const later = [entry(scanId, 'scan_scene', new Date(T0 + 120_000).toISOString())];
    const runs = sceneRecords({ drawnRuns: [{ run_id: cycleId, view: cycleOnly }], history: later, live: cycleOnly, held: () => laterScan });
    expect(runs.map((run) => run.run_id)).toEqual([scanId]);
    expect(sceneRecordsLine(runs, DAY)).toBe(`Drawn from the records of the Scan scene run ${scanId}, started 14:18:00Z.`);
  });

  it('leaves out a scan that an earlier cycle, not drawn, came after', () => {
    const between = [...history, entry('an-earlier-mission-run', 'start_cycle', new Date(T0 + 30_000).toISOString())];
    const runs = sceneRecords({ drawnRuns: [{ run_id: cycleId, view: cycleOnly }], history: between, live: cycleOnly, held: () => scanFold });
    expect(runs.map((run) => run.run_id)).toEqual([cycleId]);
    expect(sceneRecordsLine(runs, DAY)).toBe(`Drawn from the records of run ${cycleId} (Start), started ${cycleStart.slice(11, 19)}Z.`);
  });

  it('says nothing when it draws no run', () => {
    expect(sceneRecordsLine([], DAY)).toBeNull();
  });
});
