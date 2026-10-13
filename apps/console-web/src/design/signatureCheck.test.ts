// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SIGNATURE POLICY CHECK BLOCK: the release's own verifier run on the robot over every deployed capsule, NO MOTION,
// no slot touched, drawn in the Cake panel directly under the capability slot check.
//
// These pin, on the model and on the rendered screen:
//
//   the runtime's answer is read field by field, and a version this console does not speak is not read at all;
//   the block sits directly under the slot check card, carries its title, its button and the honesty sentence (this is
//   the release verifier under the resident's policy; the resident admits capsules when it starts; the abort-swap
//   candidate is a transactional activation failure, not a signature refusal), and is drawn only while no mission is
//   active;
//   the verdict table shows, per row, the capsule's short digest, the key, what was expected, what was observed, the
//   reason code and whether it was admitted (yes or no), and a SKIPPED row is drawn as skipped, never as a pass;
//   the Run button is off, with the reason on the page, for every reason the runtime would refuse;
//   the HTTP request carries EXACTLY an empty body with the guard's headers, a refusal is a refusal and an unreadable
//   answer is unknown and is never resent;
//   the store refuses a second press while one is in flight.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import { readBoot } from '../api/boot';
import { HttpSource } from '../api/http';
import type { FetchLike } from '../api/http';
import { SyntheticSource } from '../api/synthetic';
import type { ConsoleSource, SignatureCheckOutcome } from '../api/types';
import type { SignatureCheckState, SignatureRow } from '../model/signatureCheck';
import {
  parseSignatureCheckState,
  shortDigest,
  signatureAdmittedWords,
  signatureRunOff,
  signatureUnavailableWords,
  signatureVerdictWords,
} from '../model/signatureCheck';
import { parseCapabilityCheckState } from '../model/capabilityCheck';
import type { MissionRecord } from '../model/missionRecord';
import { applySnapshot, initialState } from '../model/reducer';
import type { ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { Screen } from './Screen';
import { signatureReceiptWords } from './SignatureCheck';

const SUPERVISOR = '18d9332f438b8444e5f21f1a4715ef811851f43e117d4962a8b4b4e61c344721';
const MOBILE = 'a384e7607b7db45dfe2d201211364cb6dee8d2bd375c89aaa46f6066c0ebb79b';
const DEPLOY_KEY = '821c43a0c15918f4fd3e2931927041a41796e8159aaac46e8498c11534a30be4';
const THROWAWAY_KEY = '91'.repeat(32);

function text(markup: string): string {
  return markup.replace(/<[^>]*>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/\s+/g, ' ');
}

function rowOf(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    capsule: SUPERVISOR, kind: 'signed_capsule', key: 'deployment', expected: 'admitted', expected_reason: null,
    observed: 'admitted', reason_code: null, admitted: true, as_expected: true, ...extra,
  };
}

/** One recorded run, shaped exactly as demo_runtime.signature_check records one (minus the record envelope). */
function recorded(extra: Record<string, unknown> = {}, rows?: unknown[]): Record<string, unknown> {
  return {
    check_uuid: 'ab'.repeat(16), runtime_session: 'rt-a', t_wall_utc: '2026-09-26T10:00:01.000Z', policy: 'require',
    trusted_keys: [DEPLOY_KEY], throwaway_public_key: THROWAWAY_KEY, capsules: 2, unsigned_twins: 0,
    rows: rows ?? [
      rowOf(),
      rowOf({ key: 'throwaway', expected: 'refused', expected_reason: 'signature_key_id_not_trusted', observed: 'refused',
              reason_code: 'signature_key_id_not_trusted', admitted: false }),
      rowOf({ capsule: MOBILE }),
      rowOf({ capsule: MOBILE, key: 'throwaway', expected: 'refused', expected_reason: 'signature_key_id_not_trusted', observed: 'refused',
              reason_code: 'signature_key_id_not_trusted', admitted: false }),
      rowOf({ capsule: null, kind: 'unsigned_twin', expected: 'refused', expected_reason: 'signature_missing', observed: 'skipped',
              reason_code: 'unsigned_twin_not_staged', admitted: null, as_expected: null }),
    ],
    rows_as_expected: 4, rows_not_as_expected: 0, rows_skipped: 1, rows_error: 0, as_expected: true, complete: false, duration_ms: 812.5,
    ...extra,
  };
}

/** A runtime answer, shaped exactly as demo_runtime.daemon._op_capability_signature_state builds one. */
function runtimeAnswer(extra: Record<string, unknown> = {}, checks: unknown[] = []): Record<string, unknown> {
  return { ok: true, signature_check_version: '1', available: true, in_flight: false, off_reason: null, policy: 'require',
           checks, checks_dropped: 0, damaged_lines: 0, ...extra };
}

function state(extra: Record<string, unknown> = {}, checks: unknown[] = []): SignatureCheckState {
  const parsed = parseSignatureCheckState(runtimeAnswer(extra, checks));
  if (parsed === null) throw new Error('the fixture is not a signature check state');
  return parsed;
}

function idle(): ViewState {
  const snapshot = JSON.parse(JSON.stringify(runningSnapshot)) as Record<string, unknown>;
  snapshot['active_run'] = null;
  return applySnapshot(initialState(), snapshot as unknown as Snapshot);
}

function screen(patch: Partial<StoreSnapshot>): string {
  const store = new ConsoleStore(new SyntheticSource());
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...store.getSnapshot(), sourceKind: 'live', view: idle(), ...patch };
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(Screen)));
}

/** The block's own markup, from its opening element to the end of the Cake panel. */
function block(markup: string): string {
  const start = markup.indexOf('data-signature-check=""');
  expect(start).toBeGreaterThan(-1);
  return markup.slice(start, markup.indexOf('</aside>', start));
}

function button(markup: string): string {
  return /<button[^>]*data-control="signature-check-run"[^>]*>/.exec(markup)?.[0] ?? '';
}

function mission(stateName: string): MissionRecord {
  return {
    mission_record_version: '1', mission_id: 'mission-0000000000aa', state: stateName, data_mode: 'live', performs_motion: true,
    execution_label: 'proven-controller', started_wall_utc: '2026-09-25T10:00:00.000Z', selected_target: 'cube',
    waiting_reason: stateName === 'WAITING_FOR_CAPABILITY' ? 'capability_unavailable' : null, detail: null,
    targets: [{ target_id: 'cube', label: 'the block', reactive: false, reachable: false, requires_capability: true, state: 'PENDING', runs: [] }],
    activations: [],
  } as unknown as MissionRecord;
}

// GENERAL COPY ABOUT HOW THE RESIDENT WORKS, asserting nothing about this runtime's current state (verifier r2 R-3):
// the policy is the record's (`policy <state.policy>` beside the Run button), never written into the sentence.
const HONESTY = 'This is the release verifier under the resident\'s signature policy. The resident admits capsules when it starts and has no live admission operation, so the abort-swap candidate is a transactional activation failure, not a signature refusal.';

describe('reading the runtime answer', () => {
  it('reads every field it draws, and the runs as records with their rows', () => {
    const read = state({}, [recorded()]);
    expect(read.signature_check_version).toBe('1');
    expect(read.in_flight).toBe(false);
    expect(read.off_reason).toBeNull();
    expect(read.policy).toBe('require');
    expect(read.checks).toHaveLength(1);
    const run = read.checks[0];
    expect(run?.check_uuid).toBe('ab'.repeat(16));
    expect(run?.rows).toHaveLength(5);
    expect(run?.rows[0]).toEqual({ capsule: SUPERVISOR, kind: 'signed_capsule', key: 'deployment', expected: 'admitted', expected_reason: null,
                                   observed: 'admitted', reason_code: null, admitted: true, as_expected: true });
    expect(run?.rows[4]?.observed).toBe('skipped');
    expect(run?.rows[4]?.admitted).toBeNull();
    expect((run?.as_expected) === true && run.complete === false).toBe(true);
    expect(run?.rows_skipped).toBe(1);
    expect(run?.trusted_keys).toEqual([DEPLOY_KEY]);
    expect(run?.duration_ms).toBe(812.5);
  });

  it('does not read a version it does not speak, or a refusal, as a state, and drops a row without a capsule kind', () => {
    expect(parseSignatureCheckState(runtimeAnswer({ signature_check_version: '2' }))).toBeNull();
    expect(parseSignatureCheckState({ ok: false, reason_code: 'signature_check_unavailable' })).toBeNull();
    const read = state({}, [recorded({}, [rowOf(), { observed: 'admitted' }])]);
    expect(read.checks[0]?.rows).toHaveLength(1);
    expect(state({}, [{ rows: [] }]).checks).toHaveLength(0);
  });
});

describe('when the Run button is off, and why', () => {
  it('is on for an available check with nothing in the way', () => {
    expect(signatureRunOff({ state: state(), running: false, readOnlyReason: null })).toBeNull();
  });

  it('is off for every reason, each said in words with the runtime code beside it', () => {
    expect(signatureRunOff({ state: state(), running: false, readOnlyReason: 'this tab may not command' })).toBe('this tab may not command');
    expect(signatureRunOff({ state: state(), running: true, readOnlyReason: null })).toContain('nothing is sent twice');
    expect(signatureRunOff({ state: state({ in_flight: true }), running: false, readOnlyReason: null })).toContain('(signature_check_in_flight)');
    expect(signatureRunOff({ state: state({ off_reason: 'mission_running' }), running: false, readOnlyReason: null })).toContain('(mission_running)');
    expect(signatureRunOff({ state: state({ off_reason: 'capability_check_in_flight' }), running: false, readOnlyReason: null })).toContain('slot check');
    expect(signatureRunOff({ state: state({ off_reason: 'something_new' }), running: false, readOnlyReason: null })).toContain('(something_new)');
  });
});

describe('the words of a row', () => {
  it('shortens a digest and says admitted yes or no, never yes for a skipped or error row', () => {
    expect(shortDigest(SUPERVISOR)).toBe('18d9332f438b');
    expect(shortDigest(null)).toBe('none staged');
    expect(signatureAdmittedWords(true)).toBe('yes');
    expect(signatureAdmittedWords(false)).toBe('no');
    expect(signatureAdmittedWords(null)).toBe('not run');
    const skipped = state({}, [recorded()]).checks[0]?.rows[4] as SignatureRow;
    expect(signatureVerdictWords(skipped)).toContain('skipped');
    expect(signatureVerdictWords(skipped)).toContain('none staged');
    expect(signatureVerdictWords(skipped)).not.toContain('as expected');
    const admitted = state({}, [recorded()]).checks[0]?.rows[0] as SignatureRow;
    expect(signatureVerdictWords(admitted)).toBe('as expected');
    const wrong = { ...admitted, key: 'throwaway', expected: 'refused', observed: 'admitted', as_expected: false } as SignatureRow;
    expect(signatureVerdictWords(wrong)).toContain('NOT as expected');
    const error = { ...admitted, observed: 'error', reason_code: 'verifier_timeout', admitted: null, as_expected: false } as SignatureRow;
    expect(signatureVerdictWords(error)).toContain('error');
  });

  it('says why a runtime offers no check, by the detail it sends', () => {
    expect(signatureUnavailableWords('signature_check_unavailable', 'robot_link_not_local')).toContain('on the robot');
    expect(signatureUnavailableWords('signature_check_unavailable', 'robot_link_not_local')).toContain('(robot_link_not_local)');
    expect(signatureUnavailableWords('signature_check_unavailable', 'signature_check_not_configured')).toContain('names no release');
    expect(signatureUnavailableWords('signature_check_unavailable', null)).toBe('the runtime says it cannot run one');
    expect(signatureUnavailableWords('runtime_unavailable', null)).toBe('runtime_unavailable');
  });
});

describe('the rendered block', () => {
  const base: Partial<StoreSnapshot> = { signatureCheck: state({}, [recorded()]), signatureCheckReadAtMs: 1 };

  it('sits directly under the capability slot check card with its title, its button and the honesty sentence', () => {
    const check = parseCapabilityCheckState({
      ok: true, capability_check_version: '1', available: true, in_flight: false, pending_operation_uuid: null, off_reason: null,
      candidates: [{ key: 'mobile-demo-swapped', payload_digest: 'sha256:' + 'ab'.repeat(32) }],
      current: { session_uuid: '01a0d8320c9c7b09b03c1f945f489104', build_identity: 'd3'.repeat(32), host_running: false, host_held: true,
                 host_reason_code: 'supervisor_child_not_running', slot: null, observed_wall_utc: '2026-09-25T10:00:00.000Z', age_ms: 800 },
      checks: [], checks_dropped: 0, damaged_lines: 0,
    });
    const markup = screen({ ...base, capabilityCheck: check, capabilityCheckReadAtMs: 1 });
    const cakePanel = markup.indexOf('data-capability-check=""');
    const signature = markup.indexOf('data-signature-check=""');
    expect(cakePanel).toBeGreaterThan(-1);
    expect(signature).toBeGreaterThan(cakePanel);
    // Directly under: nothing else with a data-capability-request or a mission card between the two.
    expect(markup.slice(cakePanel, signature)).not.toContain('data-capability-request');
    const words = text(block(markup));
    expect(words).toContain('Signature policy check (no motion)');
    expect(words).toContain('Run the signature check');
    expect(button(markup)).toContain('data-control="signature-check-run"');
    expect(button(markup)).toContain('aria-disabled="false"');
    expect(button(markup)).not.toContain(' disabled');
    expect(words).toContain(HONESTY);
    expect(words).not.toMatch(/motion is enabled|the robot moves|admitted live|live admission (ran|succeeded|was run|is available)/i);
    expect(words).toContain('has no live admission operation');
    expect(words).not.toContain('policy (require)');
    expect(words).not.toContain('AdmitCapsule is unavailable');
  });

  it('draws the verdict table with the short digest, the key, expected, observed, the reason code and admitted yes or no', () => {
    const words = text(block(screen(base)));
    expect(words).toContain(shortDigest(SUPERVISOR));
    expect(words).toContain(shortDigest(MOBILE));
    expect(words).toContain('deployment');
    expect(words).toContain('throwaway');
    expect(words).toContain('signature_key_id_not_trusted');
    expect(words).toContain('signature_missing');
    expect(words).toMatch(/admitted[^|]*yes/);
    expect(words).toMatch(/no\b/);
    expect(words).toContain('skipped');
    expect(words).toContain('none staged');
    expect(words).toContain('unsigned_twin_not_staged');
    // The run's own summary: as expected, but not complete because a row was skipped.
    expect(words).toContain('4 rows as expected');
    expect(words).toContain('1 skipped');
    expect(words).toMatch(/not complete/i);
    expect(words).not.toContain('all rows passed');
  });

  it('says there is no run on the record, and is off with the reason when the runtime would refuse', () => {
    const empty = text(block(screen({ signatureCheck: state(), signatureCheckReadAtMs: 1 })));
    expect(empty).toContain('No signature check is on this runtime\'s record');
    const off = screen({ signatureCheck: state({ off_reason: 'mission_running' }), signatureCheckReadAtMs: 1 });
    expect(button(off)).toContain('disabled');
    expect(text(block(off))).toContain('Off: a mission is being carried');
    const inFlight = screen({ ...base, signatureChecking: true });
    expect(button(inFlight)).toContain('disabled');
    expect(text(block(inFlight))).toContain('The request is in flight');
  });

  it('is drawn only while no mission is active, and says why a LIVE runtime offers none', () => {
    for (const active of ['SEARCHING', 'WAITING_FOR_CAPABILITY', 'CARRYING']) {
      expect(screen({ ...base, mission: mission(active) })).not.toContain('data-signature-check=""');
    }
    for (const ended of ['COMPLETE', 'STOPPED', 'FAILED']) {
      expect(screen({ ...base, mission: mission(ended) })).toContain('data-signature-check=""');
    }
    const live = screen({ view: { ...idle(), data_mode: 'live' }, signatureCheckError: 'signature_check_unavailable', signatureCheckErrorDetail: 'robot_link_not_local' });
    expect(text(live)).toContain('Signature policy check (no motion): not offered here, because');
    expect(text(live)).toContain('(robot_link_not_local)');
    const rehearsal = screen({ sourceKind: 'synthetic', view: { ...idle(), data_mode: 'synthetic' }, signatureCheckError: 'x' });
    expect(rehearsal).not.toContain('data-signature-check-off');
  });

  it('keeps the receipt for the press apart from the record, in words', () => {
    expect(signatureReceiptWords({ kind: 'refused', reason_code: 'mission_running', detail: null })).toContain('refused the last signature check');
    expect(signatureReceiptWords({ kind: 'unknown', detail: 'no answer' })).toContain('could not be read');
    const answered: SignatureCheckOutcome = { kind: 'answered', check_uuid: 'x', as_expected: true, complete: false, rows_as_expected: 4,
                                              rows_not_as_expected: 0, rows_skipped: 1, rows_error: 0 };
    expect(signatureReceiptWords(answered)).toContain('recorded a run');
    expect(signatureReceiptWords(answered)).toContain('the table below is the record');
    const words = text(block(screen({ ...base, lastSignatureCheck: answered })));
    expect(words).toContain('recorded a run');
  });
});

describe('the request over HTTP', () => {
  const BOOT_JSON = JSON.stringify({ contract_version: '1', api_base: '/api', api_session: 'api-session-1', credential: 'not-a-real-credential-value',
                                     credential_header: 'X-Console-Credential', request_header: 'X-Console-Request' });
  const bootDocument = { getElementById: (id: string) => (id === 'kc-console-boot' ? { textContent: BOOT_JSON } : null) };

  function source(answer: { status: number; body: unknown }) {
    const calls: { url: string; method: string; headers: Record<string, string>; body: string | null }[] = [];
    const impl: FetchLike = async (url: string, init?: RequestInit) => {
      calls.push({ url, method: (init?.method ?? 'GET').toUpperCase(), headers: (init?.headers ?? {}) as Record<string, string>,
                   body: typeof init?.body === 'string' ? init.body : null });
      return { ok: answer.status >= 200 && answer.status < 300, status: answer.status, json: async () => answer.body } as unknown as Response;
    };
    return { http: new HttpSource({ fetchImpl: impl, eventSourceFactory: null, boot: readBoot(bootDocument) }), calls };
  }

  it('posts exactly an empty body with the guard s headers, and reads the answer as a receipt', async () => {
    const { http, calls } = source({ status: 200, body: recorded({ ok: true }) });
    const outcome = await http.runSignatureCheck();
    expect(calls).toHaveLength(1);
    expect(`${calls[0]?.method} ${calls[0]?.url}`).toBe('POST /api/capability/signature');
    expect(calls[0]?.body).toBe('{}');
    expect(calls[0]?.headers['content-type']).toBe('application/json');
    expect(calls[0]?.headers['X-Console-Credential']).toBe('not-a-real-credential-value');
    expect(calls[0]?.headers['X-Console-Request']).toBe('1');
    expect(outcome).toEqual({ kind: 'answered', check_uuid: 'ab'.repeat(16), as_expected: true, complete: false, rows_as_expected: 4,
                              rows_not_as_expected: 0, rows_skipped: 1, rows_error: 0 });
  });

  it('reads a refusal as a refusal and an unreadable failure as unknown, and sends nothing again', async () => {
    const refused = source({ status: 409, body: { ok: false, reason_code: 'resident_policy_not_require', detail: 'warn' } });
    expect(await refused.http.runSignatureCheck()).toEqual({ kind: 'refused', reason_code: 'resident_policy_not_require', detail: 'warn' });
    const failed = source({ status: 502, body: { ok: false, reason_code: 'internal_error' } });
    expect((await failed.http.runSignatureCheck()).kind).toBe('unknown');
    expect(failed.calls).toHaveLength(1);
  });

  it('reads the state, and keeps a refusal s detail', async () => {
    const good = source({ status: 200, body: runtimeAnswer({}, [recorded()]) });
    const read = await good.http.signatureCheck();
    expect(read.ok && read.state.checks[0]?.check_uuid).toBe('ab'.repeat(16));
    expect(`${good.calls[0]?.method} ${good.calls[0]?.url}`).toBe('GET /api/capability/signature');
    const off = source({ status: 409, body: { ok: false, reason_code: 'signature_check_unavailable', detail: 'robot_link_not_local' } });
    expect(await off.http.signatureCheck()).toEqual({ ok: false, reason_code: 'signature_check_unavailable', detail: 'robot_link_not_local' });
    const absent = source({ status: 404, body: { ok: false, reason_code: 'not_found' } });
    expect((await absent.http.signatureCheck() as { reason_code: string }).reason_code).toContain('does not offer a signature');
  });
});

describe('the store', () => {
  it('refuses a second press while one is in flight and never sends twice', async () => {
    let sent = 0;
    let release: (outcome: SignatureCheckOutcome) => void = () => undefined;
    const pending = new Promise<SignatureCheckOutcome>((resolve) => {
      release = resolve;
    });
    const base = new SyntheticSource();
    const driven = Object.assign(Object.create(Object.getPrototypeOf(base) as object) as ConsoleSource, base, {
      runSignatureCheck: () => {
        sent += 1;
        return pending;
      },
    });
    const store = new ConsoleStore(driven);
    const first = store.runSignatureCheck();
    const second = await store.runSignatureCheck();
    expect(second.kind).toBe('refused');
    expect(store.getSnapshot().signatureChecking).toBe(true);
    release({ kind: 'refused', reason_code: 'mission_running', detail: null });
    await first;
    expect(sent).toBe(1);
    expect(store.getSnapshot().lastSignatureCheck).toEqual({ kind: 'refused', reason_code: 'mission_running', detail: null });
    expect(store.getSnapshot().signatureChecking).toBe(false);
  });
});
