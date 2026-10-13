// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The things this console must never do, each pinned by a test:
//   report a refusal as a lost response, send a mutating request without what the guard requires, show a list that
//   could not be read as an empty list, open a replay on nothing, or draw an overlay on a frame whose pixel size it
//   had to guess.

import { describe, expect, it } from 'vitest';
import type { Command } from '@kiwi/demo-contracts';
import { commandHeaders, parseBoot, readBoot } from './boot';
import { HttpSource } from './http';
import type { FetchLike } from './http';
import { ConsoleStore } from '../store/store';
import { SyntheticSource } from './synthetic';
import { overlayLayers } from '../model/overlay';
import { initialState } from '../model/reducer';

interface Call {
  method: string;
  url: string;
  body: string | null;
  headers: Record<string, string>;
}

function recorder(handler: (call: Call) => { status: number; body: unknown } | Error): { calls: Call[]; fetchImpl: FetchLike } {
  const calls: Call[] = [];
  const fetchImpl: FetchLike = async (url, init) => {
    const call: Call = {
      method: init?.method ?? 'GET',
      url,
      body: typeof init?.body === 'string' ? init.body : null,
      headers: (init?.headers ?? {}) as Record<string, string>,
    };
    calls.push(call);
    const answer = handler(call);
    if (answer instanceof Error) throw answer;
    return {
      ok: answer.status >= 200 && answer.status < 300,
      status: answer.status,
      json: async () => {
        if (answer.body === undefined) throw new Error('no body');
        return answer.body;
      },
    } as unknown as Response;
  };
  return { calls, fetchImpl };
}

const START: Command = { type: 'start_cycle', args: { plan_only: false } };

/** The shape apps/console-api injects: console_api/app.py handle_index. The value here is not a real credential. */
const BOOT_JSON = JSON.stringify({
  contract_version: '1',
  api_base: '/api',
  api_session: 'api-session-1',
  credential: 'not-a-real-credential-value',
  credential_header: 'X-Console-Credential',
  request_header: 'X-Console-Request',
});

function bootDocument(text: string | null): { getElementById(id: string): { textContent: string | null } | null } {
  return { getElementById: (id) => (id === 'kc-console-boot' && text !== null ? { textContent: text } : null) };
}

describe('the boot element', () => {
  it('is read from the page the API served, and the credential travels in headers only', () => {
    const boot = readBoot(bootDocument(BOOT_JSON));
    expect(boot?.credentials?.credential_header).toBe('X-Console-Credential');
    const headers = commandHeaders(boot?.credentials ?? null);
    expect(headers['content-type']).toBe('application/json');
    expect(headers['X-Console-Request']).toBe('1');
    expect(headers['X-Console-Credential']).toBe('not-a-real-credential-value');
  });

  it('invents nothing when the page was not served by the API', () => {
    expect(readBoot(bootDocument(null))).toBeNull();
    expect(parseBoot({ credential_header: 'X-Console-Credential' })?.credentials ?? null).toBeNull();
    // Without a credential the request still declares its content type, and nothing is put in its place.
    expect(Object.keys(commandHeaders(null))).toEqual(['content-type']);
  });

  it('does not read a malformed element as permission to command', () => {
    expect(readBoot(bootDocument('{not json'))).toBeNull();
  });

  it('keeps whose credential the page was served, and says nothing when the API did not say (verify-r2 must_fix 1)', () => {
    expect(parseBoot({ bound_runtime_session: 'rt-a', credential_owner: 'runtime_session' })?.served_credential).toEqual({ runtime_session: 'rt-a', owner: 'runtime_session' });
    expect(parseBoot({ bound_runtime_session: null, credential_owner: 'api_process' })?.served_credential).toEqual({ runtime_session: null, owner: 'api_process' });
    expect(parseBoot({ credential_owner: 'runtime_session' })?.served_credential).toBeNull();
    expect(parseBoot({ bound_runtime_session: 12 })?.served_credential).toBeNull();
  });
});

describe('a command', () => {
  it('carries the json content type and the guard headers the API requires', async () => {
    const { calls, fetchImpl } = recorder(() => ({ status: 200, body: { command_id: 'c1', status: 'accepted', reason_code: null, run_id: null } }));
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null, boot: readBoot(bootDocument(BOOT_JSON)) });
    await source.submit('c1', START);
    const post = calls[0] as Call;
    expect(post.url).toBe('/api/commands');
    // apps/console-api/console_api/security.py refuses in this order: Host, Origin, content type, console header,
    // credential. A command missing any of the last three is answered 403 and never reaches the runtime.
    expect(post.headers['content-type']).toBe('application/json');
    expect(post.headers['X-Console-Request']).toBe('1');
    expect(post.headers['X-Console-Credential']).toBe('not-a-real-credential-value');
  });

  it('reports a guard refusal as a REJECTION with the server reason, not as a lost response', async () => {
    const { calls, fetchImpl } = recorder((call) => {
      if (call.method === 'POST') return { status: 403, body: { ok: false, reason_code: 'credential_missing' } };
      return { status: 404, body: { ok: false, reason_code: 'unknown_command' } };
    });
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    const outcome = await source.submit('c2', START);

    expect(outcome.kind).toBe('result');
    if (outcome.kind === 'result') {
      expect(outcome.result.status).toBe('rejected');
      expect(outcome.result.reason_code).toBe('credential_missing');
    }
    // A refusal is decided before the runtime is touched: there is nothing to resolve and nothing to resend.
    expect(calls.length).toBe(1);
  });

  it('keeps the refusal status when the refusal carries no reason, rather than inventing one', async () => {
    const { fetchImpl } = recorder(() => ({ status: 415, body: null }));
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    const outcome = await source.submit('c3', START);
    expect(outcome.kind).toBe('result');
    if (outcome.kind === 'result') expect(outcome.result.reason_code).toBe('refused_with_status_415');
  });

  it('still resolves rather than resending when the failure could have happened AFTER the runtime took it', async () => {
    const { calls, fetchImpl } = recorder((call) => {
      if (call.method === 'POST') return { status: 503, body: { ok: false, reason_code: 'runtime_unavailable' } };
      return { status: 200, body: { command_id: 'c4', status: 'running', reason_code: null, run_id: 'run-4' } };
    });
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    const outcome = await source.submit('c4', START);

    expect(outcome.kind).toBe('result');
    if (outcome.kind === 'result') expect(outcome.result.status).toBe('running');
    expect(calls.filter((call) => call.method === 'POST').length).toBe(1);
  });
});

describe('the stop request', () => {
  it('is sent as json with the guard headers, so the API does not refuse it unread', async () => {
    const { calls, fetchImpl } = recorder(() => ({ status: 200, body: { ok: true, stop: { requested_mono_ns: 5, run_id: 'run-1' } } }));
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null, boot: readBoot(bootDocument(BOOT_JSON)) });
    const ack = await source.stop();

    const post = calls[0] as Call;
    expect(post.method).toBe('POST');
    expect(post.headers['content-type']).toBe('application/json');
    expect(post.headers['X-Console-Request']).toBe('1');
    expect(typeof post.body).toBe('string');
    expect(ack.outcome).toBe('taken');
  });

  it('says a refused stop was refused, with the server reason', async () => {
    const { fetchImpl } = recorder(() => ({ status: 403, body: { ok: false, reason_code: 'content_type_not_json' } }));
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    const ack = await source.stop();
    expect(ack.ok).toBe(false);
    expect(ack.outcome).toBe('refused');
    expect(ack.reason).toBe('content_type_not_json');
  });

  it('says a stop whose fate is unknown is unknown, which is not the same as refused', async () => {
    const { fetchImpl } = recorder(() => new Error('the connection dropped'));
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    const ack = await source.stop();
    expect(ack.outcome).toBe('unknown');
  });

  it('is kept on the store, so a refusal reaches the screen instead of being dropped', async () => {
    const { fetchImpl } = recorder((call) => {
      if (call.url === '/api/stop') return { status: 403, body: { ok: false, reason_code: 'credential_missing' } };
      return { status: 200, body: {} };
    });
    const store = new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory: null }));
    expect(store.getSnapshot().lastStop).toBeNull();
    await store.requestStop();
    expect(store.getSnapshot().lastStop?.outcome).toBe('refused');
    expect(store.getSnapshot().lastStop?.reason).toBe('credential_missing');
    store.dispose();
  });
});

describe('the finished-run list', () => {
  it('reads the envelope the API forwards', async () => {
    const { fetchImpl } = recorder(() => ({
      status: 200,
      body: {
        ok: true,
        runs: [{ run_id: 'sc-1', started_wall_utc: '2026-09-20T00:00:00Z', outcome: 'placed_new', evidence_complete: true, artifact_digest: 'sha256:ab' }],
      },
    }));
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    const answer = await source.history();
    expect(answer.ok).toBe(true);
    if (answer.ok) {
      expect(answer.entries.length).toBe(1);
      // The runtime's journal spells it started_wall_utc; reading `started` alone silently lost it.
      expect(answer.entries[0]?.started_wall_utc).toBe('2026-09-20T00:00:00Z');
    }
  });

  it('still reads a source that spells the start time `started`, rather than losing it again', async () => {
    const { fetchImpl } = recorder(() => ({
      status: 200,
      body: { ok: true, runs: [{ run_id: 'sc-1', started: '2026-09-20T00:00:00Z', outcome: 'placed_new' }] },
    }));
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    const answer = await source.history();
    expect(answer.ok).toBe(true);
    if (answer.ok) expect(answer.entries[0]?.started_wall_utc).toBe('2026-09-20T00:00:00Z');
  });

  it('keeps the six fields of a finished run this console used to drop on the floor', async () => {
    const { fetchImpl } = recorder(() => ({
      status: 200,
      body: {
        ok: true,
        runs: [
          {
            run_id: 'sc-1',
            command_id: 'cmd-1',
            command_type: 'start_cycle',
            started_wall_utc: '2026-09-20T00:00:00Z',
            finished_wall_utc: '2026-09-20T00:02:00Z',
            outcome: 'placed_new',
            exit_status: 0,
            evidence_complete: false,
            evidence_gaps: 2,
            artifact_digest: 'sha256:ab',
            adopted: false,
          },
        ],
      },
    }));
    const answer = await new HttpSource({ fetchImpl, eventSourceFactory: null }).history();
    expect(answer.ok).toBe(true);
    if (!answer.ok) return;
    const entry = answer.entries[0];
    expect(entry?.command_id).toBe('cmd-1');
    expect(entry?.command_type).toBe('start_cycle');
    expect(entry?.finished_wall_utc).toBe('2026-09-20T00:02:00Z');
    expect(entry?.exit_status).toBe(0);
    expect(entry?.evidence_gaps).toBe(2);
    expect(entry?.adopted).toBe(false);
    // Absent from this row, and never filled in from the mode the runtime happens to be in now.
    expect(entry?.data_mode).toBeNull();
    expect(entry?.bundle_digest).toBeNull();
  });

  it('reports a list that could not be read as NOT READ, never as an empty list', async () => {
    const { fetchImpl } = recorder(() => ({ status: 503, body: { ok: false, reason_code: 'runtime_unavailable' } }));
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    const answer = await source.history();
    expect(answer.ok).toBe(false);
    if (!answer.ok) expect(answer.reason_code).toBe('runtime_unavailable');
  });
});

describe('a replay', () => {
  const EVENT = {
    schema_version: '1',
    seq: 1,
    kind: 'state.transition',
    payload: { machine: 'single_cube', from_state: null, to_state: 'SEARCH' },
    t_mono_ns: 1,
    producer: { name: 'controller', version: '1', artifact_digest: null },
    run_id: 'sc-1',
    action_id: null,
    observation_id: null,
    source_session: 's',
    clock_domain: 'laptop_monotonic',
    t_wall_utc: null,
    reason_code: null,
    provenance: null,
  };

  it('reads the envelope the API forwards', async () => {
    const { fetchImpl } = recorder(() => ({ status: 200, body: { ok: true, run_id: 'sc-1', events: [EVENT], origins: [] } }));
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    const answer = await source.historyEvents('sc-1');
    expect(answer.ok).toBe(true);
    if (answer.ok) expect(answer.events.length).toBe(1);
  });

  it('is NOT opened on an answer that carried no records', async () => {
    const { fetchImpl } = recorder((call) => {
      if (call.url.includes('/events')) return { status: 404, body: { ok: false, reason_code: 'unknown_run' } };
      return { status: 200, body: {} };
    });
    const store = new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory: null }));
    await store.openReplay('sc-missing');
    // An empty fold under a REPLAY badge would read as a recorded run in which nothing happened.
    expect(store.getSnapshot().replay).toBeNull();
    expect(store.getSnapshot().replayError).toContain('unknown_run');
    store.dispose();
  });

  it('opens on records that did arrive', async () => {
    const { fetchImpl } = recorder(() => ({ status: 200, body: { ok: true, run_id: 'sc-1', events: [EVENT] } }));
    const store = new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory: null }));
    await store.openReplay('sc-1');
    expect(store.getSnapshot().replay?.events.length).toBe(1);
    expect(store.getSnapshot().replayError).toBeNull();
    expect(store.getSnapshot().view.machine?.state).toBe('SEARCH');
    store.dispose();
  });
});

describe('telemetry', () => {
  it('records WHEN it arrived, so a stalled feed cannot go on reading as current', async () => {
    const { fetchImpl } = recorder(() => ({ status: 200, body: {} }));
    const store = new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory: null }));
    const before = store.getSnapshot().telemetry;
    expect(before.tap_received_at_ms).toBeNull();
    expect(before.preview_received_at_ms).toBeNull();
    store.dispose();
  });

  it('stamps each sample on arrival and keeps that apart from the age inside it', async () => {
    const source = new SyntheticSource();
    const store = new ConsoleStore(source);
    const at = Date.now();
    await store.start();
    await new Promise((resolve) => setTimeout(resolve, 400));
    const telemetry = store.getSnapshot().telemetry;
    expect(telemetry.preview_received_at_ms).not.toBeNull();
    expect(telemetry.preview_received_at_ms ?? 0).toBeGreaterThanOrEqual(at);
    // The sample's own age is a runtime measurement and is NOT the arrival stamp.
    expect(telemetry.preview?.receive_age_ms).not.toBe(telemetry.preview_received_at_ms);
    store.dispose();
  });
});

describe('the finished-run list on screen', () => {
  it('is re-read when a run finishes, so it cannot go on saying there are none', async () => {
    let runs: unknown[] = [];
    const { calls, fetchImpl } = recorder((call) => {
      if (call.url === '/api/hello?contract_version=1') return { status: 200, body: { ok: true, contract_version: '1', runtime_session: 'r', commands_enabled: true } };
      if (call.url === '/api/history') return { status: 200, body: { ok: true, runs } };
      if (call.url === '/api/snapshot') return new Error('no snapshot in this test');
      return { status: 200, body: {} };
    });

    let deliver: ((data: string) => void) | null = null;
    const source = new HttpSource({
      fetchImpl,
      eventSourceFactory: (url: string) => {
        const stream = { onmessage: null as null | ((event: { data: string }) => void), onerror: null, close: () => undefined };
        // Only the events stream, not the telemetry one: they share this factory.
        if (url.startsWith('/api/events')) deliver = (data) => stream.onmessage?.({ data });
        return stream;
      },
    });
    const store = new ConsoleStore(source);
    await store.start();
    expect(calls.filter((call) => call.url === '/api/history').length).toBe(1);

    runs = [{ run_id: 'sc-1', outcome: 'placed_new', evidence_complete: true, artifact_digest: null }];
    const finished = {
      schema_version: '1', seq: 9, kind: 'run.finished',
      payload: { outcome: 'placed_new', exit_status: 0, evidence_complete: true, evidence_gaps: 0, automatic_retries: 0, task_control_interventions: 0, predicates: { object_secured: true, placed: true, clearance_target_met: true, base_returned: true } },
      t_mono_ns: 9, producer: { name: 'c', version: '1', artifact_digest: null }, run_id: 'sc-1', action_id: null,
      observation_id: null, source_session: 's', clock_domain: 'laptop_monotonic', t_wall_utc: null, reason_code: null, provenance: null,
    };
    (deliver as unknown as (data: string) => void)(JSON.stringify(finished));
    await new Promise((resolve) => setTimeout(resolve, 30));

    expect(calls.filter((call) => call.url === '/api/history').length).toBe(2);
    const history = store.getSnapshot().history;
    expect(history?.ok === true ? history.entries[0]?.run_id : null).toBe('sc-1');
    store.dispose();
  });
});

describe('the outcome before any run', () => {
  it('counts nothing, because nothing has counted', () => {
    const outcome = initialState().outcome;
    // A zero here would read as "the controller retried itself no times", which nobody has established.
    expect(outcome.automatic_retries).toBeNull();
    expect(outcome.task_control_interventions).toBeNull();
    expect(outcome.reported).toBeNull();
    expect(outcome.confirmed).toBeNull();
  });
});

describe('an overlay', () => {
  const PERCEPTION = {
    pad_note: null,
    pad: null,
    pad_by_template: { pad_identity: null, landmark_inliers: null, pad_frame_valid: true, reason: null },
    cube: { detected: true, confidence: 0.9, clipped: false, box_px: [10, 10, 40, 40] as [number, number, number, number], contact_px: [25, 40] as [number, number], mask_polygon_px: null },
    cube_by_template: null,
    homography_pad_to_image: null,
    jaw_window_px: null,
    landmarks_px: null,
  };

  it('is drawn for a frame whose pixel size is known', () => {
    const model = overlayLayers(PERCEPTION, { width: 1280, height: 720 });
    expect(model.layers.cube_box.valid).toBe(true);
    expect(model.pad_frame_valid).toBe(true);
  });

  it('draws NOTHING when the frame pixel size was not recorded', () => {
    const model = overlayLayers(PERCEPTION, null);
    // Every coordinate the detector served is in that frame's pixels. A guessed size would place a measured box over
    // the wrong part of a real photograph and still look like a measurement.
    for (const layer of Object.values(model.layers)) {
      expect(layer.valid).toBe(false);
      expect(layer.reason).not.toBeNull();
    }
    expect(model.homography).toBeNull();
  });

  it('says NOTHING IS KNOWN about the pad frame when no perception was served, rather than "not valid"', () => {
    expect(overlayLayers(null, { width: 640, height: 480 }).pad_frame_valid).toBeNull();
    expect(overlayLayers({ ...PERCEPTION, pad_by_template: { pad_identity: null, landmark_inliers: null, pad_frame_valid: false, reason: 'too few landmarks' } }, { width: 640, height: 480 }).pad_frame_valid).toBe(false);
  });
});

describe('the URL of a picture', () => {
  // A decision frame is fetched by an <img> tag, which cannot carry a header, so the identity in the URL is all there
  // is. It names an OBSERVATION and nothing else: no credential (the guard takes that in a header, and only on a
  // mutating request), no path on the machine the runtime runs on, and no device.
  const boot = readBoot(bootDocument(BOOT_JSON));
  const source = new HttpSource({ boot, eventSourceFactory: null });
  const CREDENTIAL = 'not-a-real-credential-value';

  it('carries the credential nowhere, for a stored frame or for the live preview', () => {
    expect(source.canPresentCredential).toBe(true);
    expect(source.frameUrl('obs-1', 'raw', 640)).not.toContain(CREDENTIAL);
    expect(source.previewUrl('wrist')).not.toContain(CREDENTIAL);
    // It travels in a header instead, and the guard requires it only where something can be changed.
    expect(commandHeaders(boot?.credentials ?? null)['X-Console-Credential']).toBe(CREDENTIAL);
  });

  it('names an observation through the API prefix, and never a path on the machine that holds it', () => {
    const url = source.frameUrl('obs-1', 'overlay', 640);
    expect(url.startsWith('/api/observations/')).toBe(true);
    expect(url).toContain('what=overlay');
    expect(url).not.toMatch(/\/(home|var|tmp|Users)\//);
  });

  it('encodes an identity that would otherwise reach outside the route it belongs to', () => {
    const url = source.frameUrl('../../etc/passwd', 'raw', 640);
    expect(url).not.toContain('../');
    expect(url).toContain('%2F');
  });
});
