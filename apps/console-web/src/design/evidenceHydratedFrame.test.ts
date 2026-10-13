// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// DIAGNOSTICS > EVIDENCE ON A PAGE OPENED AFTER THE RUN NEVER SAYS "THIS PERCEPTION WAS SERVED FOR NO OBSERVATION"
// (redesign r4 verify-r4 must_fix 3).
//
// Evidence draws the decision frame from the selected run's read-back fold, but the store fetched a frame's record and
// perception only for the frame pinned in the live or replay fold, which a page opened after the run does not hold. So the
// panel bound its overlay to a fetch that never happened, and every one of the seven layers said "this perception was
// served for no observation and the picture on screen is <id>", in the runtime's voice about a read the page never made
// (the judge's JB over the saved real records: 7 occurrences, 0 requests to /observations/). The run here is built by hand
// with `model/stageFixtures`: a retained wrist frame pinned by a predicate, the frame the failure names. It is served as
// the runtime serves a finished run, and the store is opened after it.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import idleSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/idle-first-start.json';
import type { ContractEvent } from '../contracts/envelope';
import { HttpSource } from '../api/http';
import type { EventSourceLike, FetchLike } from '../api/http';
import { LAYER_NAMES } from '../model/overlay';
import { captured, enter, finished, started, verdict } from '../model/stageFixtures';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { Drawer } from './Drawers';
import { settle, textOf } from './demoScenarioPlay';

const SESSION = 'synthetic-session-0000';
const RUN = 'synthetic-run-evidence-0001';
const OBS = `${RUN}-obs-align`;
const HELLO = {
  ok: true, reason_code: null, contract_version: '1', read_only: false, commands_enabled: true, runtime_session: SESSION,
  credential_owner: 'runtime_session', bound_runtime_session: SESSION,
  runtime: { reachable: true, session: SESSION, contract_version: '1', data_mode: 'live' },
};

function runEvents(): ContractEvent[] {
  return [
    started(RUN),
    enter(RUN, 'SEARCH'),
    enter(RUN, 'ALIGN', 'SEARCH'),
    captured(RUN, OBS),
    verdict(RUN, 'aligned', { value: false, measured: 9.1, threshold: 6, unit: 'mm', frame: OBS }),
    finished(RUN, 'failed', { code: 'align_cube_not_detected', phase: 'ALIGN', detail: 'ALIGN: the cube was not detected', measurements: [], frames: [OBS] }),
  ];
}

/**
 * The perception the runtime holds for the frame: a cube box and contact, no pad frame. The console API serves it inside
 * the runtime's envelope, `{ok, observation_id, what, data}` (daemon `_op_observation`, console_api `handle_observation`);
 * round 7 item 1: the pin served it bare, a shape no console API serves, and so passed while the page read every field
 * of the real answer as null.
 */
const PERCEPTION = {
  cube: { detected: true, box_px: [500, 300, 700, 480], contact_px: [600, 476] },
  pad_by_template: { pad_frame_valid: false, reason: 'pad_not_detected' },
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

async function openedAfterTheRun(servesPerception: boolean): Promise<{ store: ConsoleStore; reads: string[] }> {
  const events = runEvents();
  const last = events[events.length - 1] as ContractEvent;
  const reads: string[] = [];
  const fetchImpl: FetchLike = async (input, init) => {
    const method = (init?.method ?? 'GET').toUpperCase();
    reads.push(`${method} ${input}`);
    if (method !== 'GET') return json(599, { ok: false, reason_code: 'this_test_sends_no_command' });
    if (input.startsWith('/api/hello')) return json(200, HELLO);
    if (input === '/api/snapshot') {
      return json(200, {
        ...idleSnapshot,
        runtime_session: SESSION,
        data_mode: 'live',
        active_run: null,
        last_outcome: {
          run_id: RUN,
          finished_seq: last.seq,
          result: { outcome: 'failed', exit_status: 5, predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null }, automatic_retries: 0, task_control_interventions: 0, evidence_complete: true, evidence_gaps: 0 },
        },
      });
    }
    if (input === '/api/history') {
      return json(200, { ok: true, runs: [{ run_id: RUN, command_id: `cmd-${RUN}`, command_type: 'start_cycle', started_wall_utc: '2026-09-28T09:00:00.000Z', finished_wall_utc: '2026-09-28T09:00:40.000Z', outcome: 'failed', exit_status: 5, data_mode: 'live' }] });
    }
    if (input === `/api/history/${RUN}/events`) return json(200, { ok: true, events });
    if (servesPerception && input === `/api/observations/${OBS}/perception`) return json(200, { ok: true, observation_id: OBS, what: 'perception', data: PERCEPTION });
    if (servesPerception && input === `/api/observations/${OBS}/record`) {
      return json(200, { ok: true, observation_id: OBS, what: 'record', data: { observation_id: OBS, camera: { width_px: 1280, height_px: 720 }, has_frame: true } });
    }
    // The runtime's refusal for a frame it holds no perception for, forwarded by the console API with its status.
    if (input.startsWith(`/api/observations/${OBS}/`)) return json(404, { ok: false, reason_code: 'not_found' });
    return json(404, { ok: false, reason_code: 'not_served_in_this_test' });
  };
  const eventSourceFactory = (): EventSourceLike => ({ onmessage: null, onerror: null, close: () => undefined });
  const store = new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory, boot: null, sessionId: 'evidence-hydrated-frame-test' }), { storage: null });
  await store.start();
  await settle(store);
  await vi.advanceTimersByTimeAsync(1_000);
  await settle(store);
  return { store, reads };
}

function evidence(store: ConsoleStore): string {
  const snapshot = store.getSnapshot();
  return renderToStaticMarkup(
    createElement(StoreContext, { value: store }, createElement(Drawer, { id: 'evidence', snapshot, store, mode: 'live', onSelect: () => undefined, onClose: () => undefined })),
  );
}

/** Each layer row's name and reason, in the order the list draws them. */
function layerRows(markup: string): Array<{ name: string; words: string }> {
  return [...markup.matchAll(/<span class="layer__name">([^<]*)<\/span><span class="layer__reason">([^<]*)<\/span>/g)].map((match) => ({
    name: match[1] as string,
    words: textOf(match[2] as string),
  }));
}

describe('Evidence on a page opened after the run', () => {
  afterEach(() => vi.useRealTimers());

  it('reads the perception of the frame it draws, and draws the layers from it', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T09:05:00.000Z') });
    const { store, reads } = await openedAfterTheRun(true);
    expect(store.getSnapshot().view.decision_frame).toBeNull();
    const markup = evidence(store);
    const text = textOf(markup);
    expect(text).toContain(OBS);
    expect(text).not.toContain('served for no observation');
    expect(reads).toContain(`GET /api/observations/${OBS}/perception`);
    const rows = layerRows(markup);
    expect(rows.map((row) => row.name)).toEqual([...LAYER_NAMES]);
    expect(rows.find((row) => row.name === 'cube_box')?.words).toBe('drawn from this frame');
    expect(rows.find((row) => row.name === 'pad_outline')?.words).toBe('pad_not_detected');
    store.dispose();
  }, 60_000);

  it('says of every layer that the perception could not be read and passes the refusal\'s reason through, when the runtime serves none, and never that it was served for no observation', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T09:05:00.000Z') });
    const { store, reads } = await openedAfterTheRun(false);
    const markup = evidence(store);
    const text = textOf(markup);
    expect(text).toContain(OBS);
    expect(text).not.toContain('served for no observation');
    expect(reads).toContain(`GET /api/observations/${OBS}/perception`);
    const rows = layerRows(markup);
    expect(rows.map((row) => row.name)).toEqual([...LAYER_NAMES]);
    for (const row of rows) expect(row.words, row.name).toBe(`the perception of ${OBS} could not be read by this page; the console server answered not_found`);
    store.dispose();
  }, 60_000);
});
