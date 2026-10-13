// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CAPABILITY CARD'S CHECK MODE: one prepared Plan applied to the robot's capability slot and read back, NO MOTION.
//
// These pin, on the model and on the rendered screen:
//
//   the runtime's answer is read field by field, and a version this console does not speak is not read at all;
//   the Apply button is off, with the reason on the page, for every reason the runtime would refuse and for a reading
//   too old to send its session; and it sends the CURRENT reading's session, which the card prints beside it;
//   the card is drawn only when no mission is active, and a waiting mission keeps its own request in that slot;
//   its words never use the mission's word for the outcome, never speak of the robot going anywhere and never say
//   motion is enabled;
//   a check whose host was not held throughout, or whose resident session changed, gets a RED row;
//   HELD and CHANGED are the runtime's words: a host whose host_running is false is not drawn as held on that alone, a
//   slot is drawn changed or unchanged only by the runtime's slot_changed, and a completed Plan that left a slot already
//   carrying the capability as it was is never headed as observed;
//   the HTTP request carries exactly the key, the digest and the session, with the guard's headers, and a refusal is a
//   refusal while an unreadable answer is unknown and is never resent;
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
import type { CapabilityCheckOutcome, ConsoleSource } from '../api/types';
import type { CapabilityCheckRecord, CapabilityCheckState } from '../model/capabilityCheck';
import {
  checkApplyOff,
  checkChoice,
  checkHeadline,
  checkHostWords,
  checkThroughoutWords,
  checkUnavailableWords,
  checkWarnings,
  parseCapabilityCheckState,
  slotChangeWords,
} from '../model/capabilityCheck';
import type { MissionRecord, SlotReading } from '../model/missionRecord';
import { applySnapshot, initialState } from '../model/reducer';
import type { ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { Screen } from './Screen';

const SESSION = '01a0d8320c9c7b09b03c1f945f489104';
const INERT = 'c3'.repeat(32);
const MOBILE = 'b2'.repeat(32);
const VALID = { key: 'mobile-demo-swapped', payload_digest: 'sha256:' + 'ab'.repeat(32) };
const INVALID = { key: 'mobile-demo-active-successor-abort-swap', payload_digest: 'sha256:' + 'cd'.repeat(32) };
const NOW = 1_800_000_000_000;

function text(markup: string): string {
  return markup.replace(/<[^>]*>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/\s+/g, ' ');
}

function reading(capsule: string, generation: string, instance: string): SlotReading {
  return {
    read: 'ok', slot_uuid: '6d6f62696c655f617070726f61636801', name: 'mobile_approach', state: 'ACTIVE', capsule, generation, instance,
    session_uuid: SESSION, plan_digest: 'c9'.repeat(32), epoch: '3', capability_active: capsule === MOBILE,
  };
}

/** A runtime answer, shaped exactly as demo_runtime.daemon._op_capability_check_state builds one. */
function runtimeAnswer(extra: Record<string, unknown> = {}, checks: unknown[] = []): Record<string, unknown> {
  return {
    ok: true, capability_check_version: '1', available: true, in_flight: false, pending_operation_uuid: null, off_reason: null,
    candidates: [INVALID, VALID],
    current: { session_uuid: SESSION, build_identity: 'd3'.repeat(32), host_running: false, host_held: true, host_reason_code: 'supervisor_child_not_running',
               slot: reading(INERT, '1', '7'), observed_wall_utc: '2026-09-25T10:00:00.000Z', age_ms: 800 },
    checks, checks_dropped: 0, damaged_lines: 0, ...extra,
  };
}

function recorded(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    operation_uuid: 'ab'.repeat(16), target_id: null, candidate_key: VALID.key, payload_digest: VALID.payload_digest,
    activation_state: 'OBSERVED', outcome: 'ACCEPTED', transaction_phase: 'COMPLETED', transaction_uuid: 'abababab-txn',
    detail: 'the transaction completed', t_wall_utc: '2026-09-25T10:00:01.000Z', answer_wall_utc: '2026-09-25T10:00:02.000Z',
    slot_before: reading(INERT, '1', '7'), slot_after: reading(MOBILE, '2', '8'),
    transaction: { operation_uuid: 'ab'.repeat(16), transaction_uuid: 'abababab-txn', transaction_handle: '21', transaction_phase: 'COMPLETED',
                   status: 'ok', reason_code: null, answer_epoch: '4' },
    stopped_during_activation: null, requested_session_uuid: SESSION,
    host_before: { session_uuid: SESSION, build_identity: 'd3'.repeat(32), host_running: false, held: true, reason_code: 'supervisor_child_not_running', observed_wall_utc: null },
    host_after: { session_uuid: SESSION, build_identity: 'd3'.repeat(32), host_running: false, held: true, reason_code: 'supervisor_child_not_running', observed_wall_utc: null },
    host_held_throughout: true, resident_session_changed: false, slot_changed: true, answered: true, orphaned: false, duration_ms: 812.5, phases_mono_ns: null,
    ...extra,
  };
}

function state(extra: Record<string, unknown> = {}, checks: unknown[] = []): CapabilityCheckState {
  const parsed = parseCapabilityCheckState(runtimeAnswer(extra, checks));
  if (parsed === null) throw new Error('the fixture is not a check state');
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

/** The card's own markup, from its opening element to the end of the Cake panel. */
function card(markup: string): string {
  const start = markup.indexOf('data-capability-check=""');
  expect(start).toBeGreaterThan(-1);
  return markup.slice(start, markup.indexOf('</aside>', start));
}

function button(markup: string): string {
  return /<button[^>]*data-control="capability-check-apply"[^>]*>/.exec(markup)?.[0] ?? '';
}

function mission(stateName: string): MissionRecord {
  return {
    mission_record_version: '1', mission_id: 'mission-0000000000aa', state: stateName, data_mode: 'live', performs_motion: true,
    execution_label: 'proven-controller', started_wall_utc: '2026-09-25T10:00:00.000Z', selected_target: 'cube_far',
    waiting_reason: stateName === 'WAITING_FOR_CAPABILITY' ? 'capability_unavailable' : null, detail: null,
    targets: [{ target_id: 'cube_far', label: 'the far cube', reachable: false, requires_capability: true, state: 'PENDING', runs: [] }],
    activations: [],
  } as MissionRecord;
}

describe('reading the runtime answer', () => {
  it('reads every field it draws, and the checks as records the evidence component can draw', () => {
    const read = state({}, [recorded()]);
    expect(read.current.session_uuid).toBe(SESSION);
    expect(read.current.host_running).toBe(false);
    expect(read.current.host_held).toBe(true);
    expect(read.current.slot?.generation).toBe('1');
    expect(read.candidates.map((item) => item.key)).toEqual([INVALID.key, VALID.key]);
    const check = read.checks[0] as CapabilityCheckRecord;
    expect(check.activation_state).toBe('OBSERVED');
    expect(check.slot_after?.capsule).toBe(MOBILE);
    expect(check.transaction?.transaction_handle).toBe('21');
    expect(check.host_held_throughout).toBe(true);
    expect(check.host_before?.held).toBe(true);
    expect(check.slot_changed).toBe(true);
  });

  it('does not read a version it does not speak, or a refusal, as a check', () => {
    expect(parseCapabilityCheckState(runtimeAnswer({ capability_check_version: '2' }))).toBeNull();
    expect(parseCapabilityCheckState({ ok: false, reason_code: 'capability_check_unavailable' })).toBeNull();
  });
});

describe('when the Apply button is off, and why', () => {
  const base = { readAtMs: NOW, nowMs: NOW, applying: false, chosen: VALID.key, readOnlyReason: null };

  it('is on for a current reading with a held host and a picked Plan', () => {
    expect(checkApplyOff({ ...base, state: state() })).toBeNull();
  });

  it('is off for every reason, each said in words with the runtime code beside it', () => {
    expect(checkApplyOff({ ...base, state: state(), readOnlyReason: 'this tab may not command' })).toBe('this tab may not command');
    expect(checkApplyOff({ ...base, state: state(), applying: true })).toContain('nothing is sent twice');
    expect(checkApplyOff({ ...base, state: state({ in_flight: true }) })).toContain('(capability_check_in_flight)');
    expect(checkApplyOff({ ...base, state: state({ pending_operation_uuid: 'op' }) })).toContain('still running');
    expect(checkApplyOff({ ...base, state: state({ pending_operation_uuid: 'op' }) })).toContain('did not arrive in time');
    expect(checkApplyOff({ ...base, state: state({ off_reason: 'robot_host_not_held' }) })).toContain('keeps the host held');
    expect(checkApplyOff({ ...base, state: state({ off_reason: 'mission_running' }) })).toContain('(mission_running)');
    expect(checkApplyOff({ ...base, state: state({ off_reason: 'something_new' }) })).toContain('(something_new)');
    expect(checkApplyOff({ ...base, state: state({ candidates: [] }) })).toContain('no prepared Plan');
    expect(checkApplyOff({ ...base, state: state({ candidates: [{ key: VALID.key }] }) })).toContain('no digest');
    const noSession = runtimeAnswer();
    (noSession['current'] as Record<string, unknown>)['session_uuid'] = null;
    expect(checkApplyOff({ ...base, state: parseCapabilityCheckState(noSession) as CapabilityCheckState })).toContain('names no resident session');
  });

  it('is off for a reading too old to send its session, counting the age it already had when it was served', () => {
    expect(checkApplyOff({ ...base, state: state(), nowMs: NOW + 16_000 })).toContain('too old to send its session');
    expect(checkApplyOff({ ...base, state: state(), nowMs: NOW + 14_000 })).toBeNull();
    const served = runtimeAnswer();
    (served['current'] as Record<string, unknown>)['age_ms'] = 15_500;
    expect(checkApplyOff({ ...base, state: parseCapabilityCheckState(served) as CapabilityCheckState })).toContain('too old');
    expect(checkApplyOff({ ...base, state: state(), readAtMs: null })).toContain('of unknown age');
  });

  it('picks the operator s choice while it is offered, and otherwise nothing: no candidate is preselected', () => {
    expect(checkChoice(state(), VALID.key)).toBe(VALID.key);
    expect(checkChoice(state(), 'gone')).toBeNull();
    expect(checkChoice(state(), null)).toBeNull();
    expect(checkChoice(state({ candidates: [] }), null)).toBeNull();
  });
});

describe('what a recorded check shows', () => {
  it('says whether the slot changed as the runtime decided it, and never works it out again from the readings', () => {
    const read = state({}, [recorded(), recorded({ operation_uuid: 'cd'.repeat(16), slot_after: reading(INERT, '1', '7'), slot_changed: false }),
                            recorded({ operation_uuid: 'ef'.repeat(16), slot_after: { read: 'failed' }, slot_changed: null }),
                            recorded({ operation_uuid: '12'.repeat(16), slot_changed: null }),
                            recorded({ operation_uuid: '34'.repeat(16), slot_changed: undefined })]);
    expect(slotChangeWords(read.checks[0] as CapabilityCheckRecord)).toContain('changed (generation 1 to 2');
    expect(slotChangeWords(read.checks[1] as CapabilityCheckRecord)).toContain('unchanged (generation 1');
    expect(slotChangeWords(read.checks[2] as CapabilityCheckRecord)).toContain('not known');
    // Readings that differ, and a runtime that did not say they changed: not known, never "changed" from here.
    expect(slotChangeWords(read.checks[3] as CapabilityCheckRecord)).toContain('not known');
    expect(slotChangeWords(read.checks[4] as CapabilityCheckRecord)).toContain('not known');
  });

  it('never heads a completed Plan that left a slot already carrying the capability as observed, and says why', () => {
    const same = state({}, [recorded({ activation_state: 'COMPLETED_NOT_OBSERVED', slot_before: reading(MOBILE, '2', '8'), slot_after: reading(MOBILE, '2', '8'),
                                       slot_changed: false }),
                            recorded({ operation_uuid: 'cd'.repeat(16), activation_state: 'COMPLETED_NOT_OBSERVED', resident_session_changed: true }),
                            recorded({ operation_uuid: 'ef'.repeat(16), activation_state: 'COMPLETED_NOT_OBSERVED', slot_changed: null })]);
    const unchanged = same.checks[0] as CapabilityCheckRecord;
    expect(checkHeadline(unchanged, same.roles).words).toBe('Completed; the slot already carried the capability capsule and did not change, so no change was observed');
    expect(checkHeadline(unchanged, same.roles).words).not.toContain('does not carry');
    expect(slotChangeWords(unchanged)).toContain('unchanged (generation 2');
    expect(checkHeadline(same.checks[1] as CapabilityCheckRecord, same.roles).words).toContain('another resident session');
    expect(checkHeadline(same.checks[2] as CapabilityCheckRecord, same.roles).words).toContain('whether the slot changed is not known');
    const markup = screen({ capabilityCheck: same, capabilityCheckReadAtMs: Date.now() });
    expect(text(card(markup))).not.toContain('Observed:');
  });

  it('draws a host as held only when the runtime says held, whatever host_running says', () => {
    expect(checkHostWords({ host_running: false, held: true, reason_code: 'supervisor_child_not_running' })).toBe('held (no host process under the supervisor slot)');
    const unhealthy = checkHostWords({ host_running: false, held: false, reason_code: 'supervisor_host_not_healthy' });
    expect(unhealthy).toContain('NOT held');
    expect(unhealthy).toContain('not HEALTHY');
    expect(checkHostWords({ host_running: false, held: null, reason_code: null })).toBe('not read');
    expect(checkHostWords({ host_running: true, held: false, reason_code: null })).toBe('RUNNING under the supervisor slot');
    expect(checkHostWords(null)).toBe('not read');
    const answer = runtimeAnswer({ off_reason: 'robot_host_not_held' });
    Object.assign(answer['current'] as Record<string, unknown>, { host_running: false, host_held: false, host_reason_code: 'supervisor_host_not_healthy' });
    const markup = screen({ capabilityCheck: parseCapabilityCheckState(answer) as CapabilityCheckState, capabilityCheckReadAtMs: Date.now() });
    const words = text(card(markup));
    expect(words).toContain('host NOT held (a host process is under the supervisor slot, reported but not HEALTHY)');
    expect(words).not.toContain('host held (no host process');
  });

  it('never reads a Plan that returns the slot to its inert capsule as a slot that did not change', () => {
    const back = state({}, [recorded({ activation_state: 'COMPLETED_NOT_OBSERVED', slot_before: reading(MOBILE, '2', '8'), slot_after: reading(INERT, '3', '9') })]);
    const check = back.checks[0] as CapabilityCheckRecord;
    expect(checkHeadline(check, back.roles).words).toBe('Completed; the slot read back does not carry the capability capsule');
    expect(slotChangeWords(check)).toContain('changed (generation 2 to 3');
  });

  it('draws a red row for a host that was not held throughout and for a session that changed, and none otherwise', () => {
    const read = state({}, [recorded(), recorded({ operation_uuid: 'cd'.repeat(16), host_held_throughout: false,
                                                   host_after: { host_running: false, held: false, reason_code: 'supervisor_host_not_healthy' },
                                                   resident_session_changed: true })]);
    expect(checkWarnings(read.checks[0] as CapabilityCheckRecord)).toEqual([]);
    expect(checkWarnings(read.checks[1] as CapabilityCheckRecord).map((item) => item.key)).toEqual(['host_not_held', 'session_changed']);
    const markup = screen({ capabilityCheck: read, capabilityCheckReadAtMs: Date.now() });
    const rows = card(markup);
    expect(rows).toContain('data-check-warning="host_not_held"');
    expect(rows).toContain('data-check-warning="session_changed"');
    expect(text(rows)).toContain('The host was NOT held throughout: a host process was under the supervisor slot when it was read after');
    expect(text(rows)).toContain('The resident session CHANGED across the operation');
  });
});

describe('what the counters and an open fate show', () => {
  it('draws a red row naming the spawns when both reads were held but the supervisor spawned between them', () => {
    const detail = 'both reads were held, but the supervisor slot changed between them: spawns 5 to 10; restarts 5 to 10. Something ran or was re-applied under it during the check';
    const read = state({}, [recorded({ host_held_throughout: false, host_throughout_detail: detail })]);
    const check = read.checks[0] as CapabilityCheckRecord;
    expect(check.host_before?.held).toBe(true);
    expect(check.host_after?.held).toBe(true);
    expect(check.host_throughout_detail).toBe(detail);
    const warnings = checkWarnings(check);
    expect(warnings.map((item) => item.key)).toEqual(['host_not_held']);
    expect(warnings[0]?.text).toContain('spawns 5 to 10');
    const rows = card(screen({ capabilityCheck: read, capabilityCheckReadAtMs: Date.now() }));
    expect(rows).toContain('data-check-warning="host_not_held"');
    expect(text(rows)).toContain('The host was NOT held throughout: both reads were held, but the supervisor slot changed between them: spawns 5 to 10');
  });

  it('says a host held at both reads is not known to be held throughout when the runtime cannot tell, with no red row', () => {
    const read = state({}, [recorded({ host_held_throughout: null, host_throughout_detail: 'a read carries no supervisor counters' })]);
    const check = read.checks[0] as CapabilityCheckRecord;
    expect(checkWarnings(check)).toEqual([]);
    expect(checkThroughoutWords(check)).toContain('not known');
    expect(checkThroughoutWords(check)).toContain('a read carries no supervisor counters');
    expect(checkThroughoutWords(state({}, [recorded()]).checks[0] as CapabilityCheckRecord)).toContain('held throughout');
  });

  it('never says an ApplyPlan whose answer did not arrive in time did not land', () => {
    const timedOut = recorded({ activation_state: 'REQUEST_FAILED', outcome: 'REQUEST_FAILED', transaction_phase: null, unresolved: true,
                                detail: 'no answer arrived in time (admin_probe_timeout); the operation may still be running',
                                slot_after: reading(INERT, '1', '7'), slot_changed: false });
    const check = state({}, [timedOut]).checks[0] as CapabilityCheckRecord;
    expect(check.unresolved).toBe(true);
    const headline = checkHeadline(check, []);
    expect(headline.words).toBe('No answer in time; the operation may still be running');
    expect(headline.words).not.toContain('did not land');
    const refused = state({}, [recorded({ activation_state: 'REQUEST_FAILED', outcome: 'REQUEST_FAILED', unresolved: false })]).checks[0] as CapabilityCheckRecord;
    expect(checkHeadline(refused, []).words).toBe('The request did not land, so nothing was judged');
    const open = state({ pending_operation_uuid: 'ab'.repeat(16), off_reason: 'capability_check_in_flight' }, [timedOut]);
    const markup = text(card(screen({ capabilityCheck: open, capabilityCheckReadAtMs: Date.now() })));
    expect(markup).toContain('No answer in time; the operation may still be running');
    expect(markup).not.toContain('did not land');
  });
});

describe('the card on the screen', () => {
  it('sits in the Cake panel, sends the current session it prints, and says no motion and nothing else', () => {
    const markup = screen({ capabilityCheck: state({}, [recorded()]), capabilityCheckReadAtMs: Date.now() });
    expect(markup.indexOf('data-capability-check=""')).toBeGreaterThan(markup.indexOf('<aside'));
    const words = text(card(markup));
    expect(words).toContain('Capability slot check (no motion)');
    expect(words).toContain('Apply this prepared Plan');
    expect(words).toContain(`resident session ${SESSION}`);
    expect(words).toContain('host held');
    expect(words).toContain('Observed: the slot changed and the read back carries the capability capsule');
    expect(words).toContain('slot: changed (generation 1 to 2');
    // NOTHING IS PRESELECTED: the button stays off until the operator picks a Plan, and says so.
    expect(button(markup)).toContain('aria-disabled="true"');
    expect(words).toContain('choose a prepared Plan');
    expect(words).toContain('Off: choose one of the prepared Plans first');
    // WHAT THE CARD NEVER SAYS.
    expect(words).not.toMatch(/activat/i);
    expect(words).not.toMatch(/\bmov(e|ed|es|ing)\b/i);
    expect(words).not.toMatch(/motion enabled/i);
  });

  it('turns the button off, with the reason on the page, for a stale reading and for a host that runs', () => {
    const stale = screen({ capabilityCheck: state(), capabilityCheckReadAtMs: Date.now() - 60_000 });
    expect(button(stale)).toContain('disabled=""');
    expect(text(card(stale))).toContain('too old to send its session');
    const running = screen({ capabilityCheck: state({ off_reason: 'robot_host_not_held' }), capabilityCheckReadAtMs: Date.now() });
    expect(button(running)).toContain('aria-disabled="true"');
    expect(text(card(running))).toContain('Off: a host process is under the supervisor slot');
  });

  it('is drawn only while no mission is active, and a waiting mission keeps its own request', () => {
    const base = { capabilityCheck: state(), capabilityCheckReadAtMs: Date.now() };
    expect(screen({ ...base, mission: mission('RUNNING') })).not.toContain('data-capability-check=""');
    const waiting = screen({ ...base, mission: mission('WAITING_FOR_CAPABILITY'), missionCandidates: [VALID] });
    expect(waiting).not.toContain('data-capability-check=""');
    expect(waiting).toContain('data-capability-request');
    for (const ended of ['COMPLETE', 'STOPPED', 'FAILED']) {
      expect(screen({ ...base, mission: mission(ended) })).toContain('data-capability-check=""');
    }
  });

  it('says why a LIVE runtime offers no check, and says nothing in a rehearsal', () => {
    const live = screen({ view: { ...idle(), data_mode: 'live' }, capabilityCheckError: 'capability_check_unavailable',
                          capabilityCheckErrorDetail: 'no_activation_candidate_prepared' });
    expect(text(live)).toContain('Capability slot check (no motion): not offered here, because this runtime was configured with no prepared Plan to apply');
    expect(checkUnavailableWords('capability_check_unavailable', null)).toBe('the runtime says it cannot run one');
    const view = idle();
    const rehearsal = screen({ sourceKind: 'synthetic', view: { ...view, data_mode: 'synthetic' }, capabilityCheckError: 'x' });
    expect(rehearsal).not.toContain('data-capability-check-off');
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

  it('posts exactly the key, the digest and the session, and reads the answer as a receipt', async () => {
    const { http, calls } = source({ status: 200, body: { ok: true, activation_state: 'REJECTED_BY_CAKE', operation_uuid: 'op', transaction_phase: 'ABORTED',
                                                          detail: null, resident_session_changed: false, host_after: { host_running: false } } });
    const outcome = await http.applyCapabilityCheck(VALID.key, VALID.payload_digest, SESSION);
    expect(calls).toHaveLength(1);
    expect(`${calls[0]?.method} ${calls[0]?.url}`).toBe('POST /api/capability/check');
    expect(JSON.parse(calls[0]?.body ?? '{}')).toEqual({ candidate_key: VALID.key, payload_digest: VALID.payload_digest, session_uuid: SESSION });
    // The same headers every mutating request carries: the json content type and the two boot headers.
    expect(calls[0]?.headers['content-type']).toBe('application/json');
    expect(calls[0]?.headers['X-Console-Credential']).toBe('not-a-real-credential-value');
    expect(calls[0]?.headers['X-Console-Request']).toBe('1');
    expect(outcome).toEqual({ kind: 'answered', operation_uuid: 'op', activation_state: 'REJECTED_BY_CAKE', transaction_phase: 'ABORTED', detail: null,
                              resident_session_changed: false, host_after_running: false });
  });

  it('reads a refusal as a refusal and an unreadable failure as unknown, and sends nothing again', async () => {
    const refused = source({ status: 409, body: { ok: false, reason_code: 'robot_host_not_held' } });
    expect(await refused.http.applyCapabilityCheck(VALID.key, VALID.payload_digest, SESSION)).toEqual({ kind: 'refused', reason_code: 'robot_host_not_held', detail: null });
    const failed = source({ status: 502, body: { ok: false, reason_code: 'internal_error' } });
    expect((await failed.http.applyCapabilityCheck(VALID.key, VALID.payload_digest, SESSION)).kind).toBe('unknown');
    expect(failed.calls).toHaveLength(1);
  });

  it('reads the state, and keeps a refusal s detail', async () => {
    const good = source({ status: 200, body: runtimeAnswer() });
    const read = await good.http.capabilityCheck();
    expect(read.ok && read.state.current.session_uuid).toBe(SESSION);
    expect(`${good.calls[0]?.method} ${good.calls[0]?.url}`).toBe('GET /api/capability/check');
    const off = source({ status: 409, body: { ok: false, reason_code: 'capability_check_unavailable', detail: 'no_activation_candidate_prepared' } });
    expect(await off.http.capabilityCheck()).toEqual({ ok: false, reason_code: 'capability_check_unavailable', detail: 'no_activation_candidate_prepared' });
  });
});

describe('the store', () => {
  it('refuses a second press while one is in flight and sends the values it was given', async () => {
    const sent: string[][] = [];
    let release: (outcome: CapabilityCheckOutcome) => void = () => undefined;
    const pending = new Promise<CapabilityCheckOutcome>((resolve) => {
      release = resolve;
    });
    const base = new SyntheticSource();
    const driven = Object.assign(Object.create(Object.getPrototypeOf(base) as object) as ConsoleSource, base, {
      applyCapabilityCheck: (key: string, digest: string, session: string) => {
        sent.push([key, digest, session]);
        return pending;
      },
    });
    const store = new ConsoleStore(driven);
    const first = store.applyCapabilityCheck(VALID.key, VALID.payload_digest, SESSION);
    const second = await store.applyCapabilityCheck(INVALID.key, INVALID.payload_digest, SESSION);
    expect(second.kind).toBe('refused');
    release({ kind: 'refused', reason_code: 'robot_host_not_held', detail: null });
    await first;
    expect(sent).toEqual([[VALID.key, VALID.payload_digest, SESSION]]);
    expect(store.getSnapshot().lastCheck).toEqual({ kind: 'refused', reason_code: 'robot_host_not_held', detail: null });
    expect(store.getSnapshot().checking).toBe(false);
  });
});
