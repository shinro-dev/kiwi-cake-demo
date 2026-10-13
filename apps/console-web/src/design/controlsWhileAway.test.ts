// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// EVERY COMMAND CONTROL IS OFF, WITH A REASON, WHILE THE RUNTIME IS NOT ANSWERING OR A READ IT IS OFFERED FROM IS NOT CURRENT
// (verdict 2026-09-27 must_fix 3; the owner's MEDIUM item, A2; spec 4.1 and 4.3, the Stale and Disconnected columns).
//
// Scan scene and Observe stayed on while Start said "the runtime is not answering" (the snapshot read failed),
// and went off only 15 s later, and only on a live runtime; Run signature check stayed on against a runtime that did not
// answer; the mission card's Return arm stayed on beside a stale mission read and beside a motion hold that said it was
// off. Each command control of the demo screen and its Diagnostics drawer is drawn ON first, then the runtime (or the
// one read) stops answering, and the control is OFF with the reason in the words of what is not current:
//
//   Start, Scan scene, Observe            scene-scan, idle (the rehearsal's list keeps Observe off; its reason becomes
//                                         the runtime's), and the console API over a live idle snapshot, then 503
//   Candidate (activate a Plan)           swap-then-stopped-holding, held for Cake
//   Return arm, Run signature check       swap-then-stopped-holding, ended (the signature read served as offering a run)
//   Try again                             hold-then-retry-placed, held for a decision
//   Confirm this robot                    bring-up-not-confirmed
//   State this (the uncertain command)    the console API over the contract's blocked-by-an-uncertain-command snapshot
//   A prepared Plan's button (Cake tab)   undo-to-inert
//
// Stop is the one command control none of this turns off: it stays as the runtime's list said over the last snapshot.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import blockedSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/blocked-by-an-uncertain-command.json';
import idleSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/idle-first-start.json';
import { HttpSource } from '../api/http';
import type { EventSourceLike, FetchLike } from '../api/http';
import { SyntheticSource } from '../api/synthetic';
import type { ConsoleSource, EventsMessage } from '../api/types';
import type { SignatureCheckState } from '../model/signatureCheck';
import type { SyntheticScenario } from '../panels/sourceChoice';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { controlOffReason, submitRefusal } from '../panels/Actions';
import { CakeTab } from './CakeTab';
import { DemoScreen } from './DemoScreen';
import { textOf } from './demoScenarioPlay';

const READS = ['snapshot', 'mission', 'robotStatus', 'history', 'cakeStatus', 'capabilityCheck', 'signatureCheck', 'missionCandidates'];

/** Which of the runtime's answers stop, per switch. */
interface Switches {
  /** Every read throws and the event stream goes quiet: the runtime is not answering. */
  down: boolean;
  /** The snapshot read never answers (no error, no new snapshot). */
  hang: boolean;
  /** Only the mission read throws. */
  mission: boolean;
  /** Only the robot status read throws. */
  robot: boolean;
}

/**
 * A synthetic scenario whose answers stop per `switches`. `signatureLive` serves the scenario's signature check as a
 * runtime that could run one serves it (off_reason null); the in-page source never runs a verifier and says so.
 */
function source(scenario: SyntheticScenario, switches: Switches, signatureLive = false): ConsoleSource {
  const inner = new SyntheticSource({ scenario });
  return new Proxy(inner, {
    get(target, property, receiver) {
      if (property === 'snapshot') {
        return async () => {
          if (switches.down) throw new Error('the runtime did not answer');
          if (switches.hang) return new Promise<never>(() => undefined);
          return target.snapshot();
        };
      }
      if (property === 'signatureCheck' && signatureLive) {
        return async () => {
          if (switches.down) throw new Error('the runtime did not answer');
          const answer = await target.signatureCheck();
          return answer.ok ? { ...answer, state: { ...answer.state, off_reason: null } as SignatureCheckState } : answer;
        };
      }
      if (typeof property === 'string' && READS.includes(property)) {
        const read = Reflect.get(target, property, receiver) as () => Promise<unknown>;
        return async () => {
          if (switches.down || (property === 'mission' && switches.mission) || (property === 'robotStatus' && switches.robot)) {
            throw new Error('the runtime did not answer');
          }
          return read.call(target);
        };
      }
      if (property === 'subscribeEvents') {
        return (cursor: number, onMessage: (message: EventsMessage) => void) =>
          target.subscribeEvents(cursor, (message) => {
            if (!switches.down) onMessage(message);
          });
      }
      const value = Reflect.get(target, property, receiver) as unknown;
      return typeof value === 'function' ? (value as (...args: unknown[]) => unknown).bind(target) : value;
    },
  }) as ConsoleSource;
}

function up(): Switches {
  return { down: false, hang: false, mission: false, robot: false };
}

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

/** The opening tag of the first element carrying `data-control="<id>"` (and `data-candidate` when given). */
function tagOf(markup: string, control: string): string | null {
  return new RegExp(`<[a-z]+[^>]*data-control="${control}"[^>]*>`).exec(markup)?.[0] ?? null;
}

/** Whether the control is drawn and on. */
function on(markup: string, control: string): boolean {
  const tag = tagOf(markup, control);
  return tag !== null && tag.includes('aria-disabled="false"');
}

/** The reason a control is off, as the screen gives it: its title, or its "Off:" line. */
function reasonOf(markup: string, control: string): string {
  const tag = tagOf(markup, control) ?? '';
  const title = /title="([^"]*)"/.exec(tag)?.[1] ?? '';
  const line = new RegExp(`data-control-reason="${control}"[^>]*>([^<]*)<`).exec(markup)?.[1] ?? '';
  return `${title} ${line}`.replace(/&#x27;/g, "'").trim();
}

// NO TIME ON IT (verify-r1 must_fix 4): the time of this page's first failed read is not the runtime's, and a reload moved it.
const NOT_ANSWERING = /the runtime is not answering(?! since)/i;

async function until(store: ConsoleStore, test: (snap: StoreSnapshot) => boolean, what: string, limitMs = 300_000): Promise<void> {
  for (let elapsed = 0; elapsed <= limitMs; elapsed += 250) {
    if (test(store.getSnapshot())) return;
    await vi.advanceTimersByTimeAsync(250);
  }
  throw new Error(`never reached: ${what}`);
}

/** Every command control drawn in `markup` that is on, Stop left out. */
function commandControlsOn(markup: string): string[] {
  const ids = ['start', 'retry', 'return_arm', 'scan_scene', 'observe', 'candidate', 'signature-check', 'robot-confirm', 'resolve-uncertain'];
  return ids.filter((id) => on(markup, id));
}

describe('the command controls of the demo screen while the runtime does not answer', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T05:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('Start, Scan scene and Observe go off with the reason Start gives the moment the snapshot read fails, and Stop does not', async () => {
    const switches = up();
    const store = new ConsoleStore(source('scene-scan', switches), { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(3000);
    const before = render(store);
    for (const control of ['start', 'scan_scene']) expect(on(before, control), control).toBe(true);
    // Observe is drawn; the rehearsal's own list keeps it off, in its own words.
    expect(tagOf(before, 'observe')).not.toBeNull();
    expect(reasonOf(before, 'observe')).not.toMatch(NOT_ANSWERING);
    const stopBefore = reasonOf(before, 'stop');

    switches.down = true;
    await vi.advanceTimersByTimeAsync(7000);
    const away = render(store);
    for (const control of ['start', 'scan_scene', 'observe']) {
      expect(on(away, control), control).toBe(false);
      expect(reasonOf(away, control), control).toMatch(NOT_ANSWERING);
    }
    expect(commandControlsOn(away)).toEqual([]);
    // Stop is as the runtime's list said over the last snapshot, never off because a read failed.
    expect(reasonOf(away, 'stop')).toBe(stopBefore);
    expect(reasonOf(away, 'stop')).not.toMatch(NOT_ANSWERING);

    // A runtime that does not report live gets no robot staleness to turn them off later: they are still off at +60 s.
    await vi.advanceTimersByTimeAsync(53_000);
    const later = render(store);
    for (const control of ['start', 'scan_scene', 'observe']) expect(reasonOf(later, control), control).toMatch(NOT_ANSWERING);
    store.dispose();
  });

  it('a snapshot read that hangs turns them off once the last snapshot is older than 15 s, dated by its fixed time', async () => {
    const switches = up();
    const store = new ConsoleStore(source('scene-scan', switches), { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(3000);
    switches.hang = true;
    await vi.advanceTimersByTimeAsync(20_000);
    const first = render(store);
    for (const control of ['start', 'scan_scene', 'observe']) {
      expect(on(first, control), control).toBe(false);
      expect(reasonOf(first, control), control).toMatch(/the runtime has not answered since its last snapshot reached this page at \d{2}:\d{2}:\d{2}Z/i);
    }
    // Nothing ticks: 20 s later the screen is the same.
    await vi.advanceTimersByTimeAsync(20_000);
    expect(render(store)).toBe(first);
    store.dispose();
  });

  it('Candidate: the Plans offered while the mission waits for Cake go off', async () => {
    const switches = up();
    const store = new ConsoleStore(source('swap-then-stopped-holding', switches), { storage: null });
    await store.start();
    await store.startMission();
    await until(store, (snap) => snap.mission?.state === 'WAITING_FOR_CAPABILITY' && (snap.missionCandidates?.length ?? 0) > 0, 'the capability hold');
    await vi.advanceTimersByTimeAsync(5500);
    expect(on(render(store), 'candidate')).toBe(true);

    switches.down = true;
    await vi.advanceTimersByTimeAsync(7000);
    const away = render(store);
    expect(on(away, 'candidate')).toBe(false);
    expect(reasonOf(away, 'candidate')).toMatch(NOT_ANSWERING);
    expect(commandControlsOn(away)).toEqual([]);
    store.dispose();
  });

  /** swap-then-stopped-holding played to its end: the mission failed holding the block, Return arm listed. */
  async function ended(switches: Switches): Promise<ConsoleStore> {
    const store = new ConsoleStore(source('swap-then-stopped-holding', switches, true), { storage: null });
    await store.start();
    await store.startMission();
    await until(store, (snap) => snap.mission?.state === 'WAITING_FOR_CAPABILITY' && (snap.missionCandidates?.length ?? 0) > 0, 'the capability hold');
    for (const key of ['mobile-demo-active-successor-abort-swap', 'mobile-demo-swapped']) {
      await store.activateCandidate(key);
      await vi.advanceTimersByTimeAsync(6000);
    }
    await until(store, (snap) => snap.mission?.state === 'FAILED' && snap.view.active_run === null, 'the mission end');
    await vi.advanceTimersByTimeAsync(5500);
    return store;
  }

  it('Return arm and Run signature check at the end of a mission go off', async () => {
    const switches = up();
    const store = await ended(switches);
    const before = render(store);
    expect(on(before, 'return_arm')).toBe(true);
    expect(on(before, 'signature-check')).toBe(true);

    switches.down = true;
    await vi.advanceTimersByTimeAsync(7000);
    const away = render(store);
    for (const control of ['return_arm', 'signature-check']) {
      expect(on(away, control), control).toBe(false);
      expect(reasonOf(away, control), control).toMatch(NOT_ANSWERING);
    }
    expect(commandControlsOn(away)).toEqual([]);
    store.dispose();
  });

  it('Return arm and Run signature check go off while only the mission read fails, in the words of that read', async () => {
    const switches = up();
    const store = await ended(switches);
    switches.mission = true;
    await vi.advanceTimersByTimeAsync(6000);
    const stale = render(store);
    // The snapshot still answers: Start, which is not offered from the mission's step list, stays as its guards say.
    expect(store.getSnapshot().sourceError).toBeNull();
    for (const control of ['return_arm', 'signature-check']) {
      expect(on(stale, control), control).toBe(false);
      expect(reasonOf(stale, control), control).toMatch(/the last mission read failed \(the runtime did not answer\), and the mission record on this page is the one read at \d{2}:\d{2}:\d{2}Z/);
    }
    store.dispose();
  });

  it('Return arm goes off beside the motion hold a robot read that stopped answering leaves, as the banner says', async () => {
    const switches = up();
    const store = await ended(switches);
    switches.robot = true;
    await vi.advanceTimersByTimeAsync(20_000);
    const held = render(store);
    expect(store.getSnapshot().sourceError).toBeNull();
    expect(on(held, 'return_arm')).toBe(false);
    expect(reasonOf(held, 'return_arm')).toMatch(/^Off: robot motion is held: /);
    // The banner beside it says the same controls are off.
    expect(textOf(held)).toContain('Start, Scan scene, Return arm and Try again stay off until it is lifted.');
    expect(on(held, 'signature-check')).toBe(false);
    expect(reasonOf(held, 'signature-check')).toMatch(/the robot status on this page is not current/);
    store.dispose();
  });

  it('Try again at a hold for a decision goes off', async () => {
    const switches = up();
    const store = new ConsoleStore(source('hold-then-retry-placed', switches), { storage: null });
    await store.start();
    await store.startMission();
    await until(store, (snap) => snap.mission?.waiting_reason === 'operator_decision' && snap.view.active_run === null, 'the decision hold');
    await vi.advanceTimersByTimeAsync(5500);
    expect(on(render(store), 'retry')).toBe(true);

    switches.down = true;
    await vi.advanceTimersByTimeAsync(7000);
    const away = render(store);
    expect(on(away, 'retry')).toBe(false);
    expect(reasonOf(away, 'retry')).toMatch(NOT_ANSWERING);
    expect(commandControlsOn(away)).toEqual([]);
    store.dispose();
  });

  it('Confirm this robot is not offered, and the hold says why', async () => {
    const switches = up();
    const store = new ConsoleStore(source('bring-up-not-confirmed', switches), { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(3000);
    expect(tagOf(render(store), 'robot-confirm')).not.toBeNull();

    switches.down = true;
    await vi.advanceTimersByTimeAsync(7000);
    const away = render(store);
    expect(tagOf(away, 'robot-confirm')).toBeNull();
    expect(textOf(away)).toMatch(/Nothing to confirm: the runtime is not answering(?! since)/);
    store.dispose();
  });

  it("A prepared Plan's button in the Cake tab of Diagnostics goes off", async () => {
    const switches = up();
    const store = new ConsoleStore(source('undo-to-inert', switches), { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(3000);
    const tab = (): string => renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(CakeTab, { snapshot: store.getSnapshot(), store })));
    const plans = (markup: string): string[] => [...markup.matchAll(/<button[^>]*data-cake-candidate="[^"]+"[^>]*>/g)].map((match) => match[0]);
    const before = plans(tab());
    expect(before.length).toBeGreaterThan(0);
    for (const button of before) expect(button).toContain('aria-disabled="false"');

    switches.down = true;
    await vi.advanceTimersByTimeAsync(7000);
    const away = plans(tab());
    expect(away.length).toBe(before.length);
    for (const button of away) {
      expect(button).toContain('aria-disabled="true"');
      expect(/title="([^"]*)"/.exec(button)?.[1] ?? '').toMatch(NOT_ANSWERING);
    }
    store.dispose();
  });
});

describe('over the console API while the runtime does not answer', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T05:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  function json(status: number, body: unknown): Response {
    return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
  }

  it('the statement about an uncertain command is not offered, and says why', async () => {
    const down = { value: false };
    const posts: string[] = [];
    const fetchImpl: FetchLike = async (input, init) => {
      if ((init?.method ?? 'GET').toUpperCase() !== 'GET') {
        posts.push(input);
        return json(599, {});
      }
      if (input.startsWith('/api/hello')) {
        return json(200, { ok: true, reason_code: null, contract_version: '1', read_only: false, commands_enabled: true, runtime_session: blockedSnapshot.runtime_session, credential_owner: 'runtime_session' });
      }
      if (down.value) return json(503, { ok: false, reason_code: 'runtime_unavailable' });
      if (input === '/api/snapshot') return json(200, blockedSnapshot);
      if (input.startsWith('/api/history')) return json(200, { ok: true, runs: [] });
      return json(404, { ok: false, reason_code: 'not_served_in_this_test' });
    };
    const eventSourceFactory = (): EventSourceLike => ({ onmessage: null, onerror: null, close: () => undefined });
    const store = new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory, boot: null, sessionId: 'uncertain-away' }), { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(3000);
    const before = render(store);
    expect(tagOf(before, 'resolve-uncertain')).not.toBeNull();
    expect(reasonOf(before, 'resolve-uncertain')).toBe('');

    down.value = true;
    await vi.advanceTimersByTimeAsync(7000);
    const away = render(store);
    expect(tagOf(away, 'resolve-uncertain')).toContain('aria-disabled="true"');
    expect(reasonOf(away, 'resolve-uncertain')).toMatch(NOT_ANSWERING);
    expect(posts).toEqual([]);
    store.dispose();
  });

  it('the verdict repro: a live runtime up, then 503: Scan scene and Observe are off at +7 s with the reason Start gives', async () => {
    const down = { value: false };
    const live = {
      ...idleSnapshot,
      data_mode: 'live',
      capabilities: [
        ...idleSnapshot.capabilities.filter((item) => item.id !== 'observe'),
        { id: 'observe', state: 'enabled', reason_code: null, reason: null, dependency: null, planned: false },
        { id: 'scan_scene', state: 'enabled', reason_code: null, reason: null, dependency: null, planned: false },
      ],
    };
    const fetchImpl: FetchLike = async (input) => {
      if (input.startsWith('/api/hello')) return json(200, { ok: true, reason_code: null, contract_version: '1', read_only: false, commands_enabled: true, runtime_session: idleSnapshot.runtime_session, credential_owner: 'runtime_session' });
      if (down.value) return json(503, { ok: false, reason_code: 'runtime_unavailable' });
      if (input === '/api/snapshot') return json(200, live);
      if (input.startsWith('/api/history')) return json(200, { ok: true, runs: [] });
      return json(404, { ok: false, reason_code: 'not_served_in_this_test' });
    };
    const store = new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory: null, boot: null, sessionId: 'idle-live' }), { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(3000);
    const before = render(store);
    expect(on(before, 'scan_scene')).toBe(true);
    // Observe is served; what keeps its button off here is only the holding question, which the operator answers.
    expect(controlOffReason(store.getSnapshot(), 'observe', Date.now())).toBeNull();

    down.value = true;
    await vi.advanceTimersByTimeAsync(7000);
    const away = render(store);
    for (const control of ['start', 'scan_scene', 'observe']) {
      expect(tagOf(away, control), control).toContain('aria-disabled="true"');
      expect(reasonOf(away, control), control).toMatch(NOT_ANSWERING);
    }
    // And again at the click.
    expect(submitRefusal(store.getSnapshot(), 'observe')).toMatch(NOT_ANSWERING);
    expect(submitRefusal(store.getSnapshot(), 'scan_scene')).toMatch(NOT_ANSWERING);
    store.dispose();
  });
});
