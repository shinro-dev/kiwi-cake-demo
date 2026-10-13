// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// TEST SUPPORT: ONE SCENARIO OF THE CATALOGUE, PLAYED THROUGH THE REAL STORE AND RENDERED AS THE DEMO SCREEN.
//
// Nothing in the application imports this file. It is the harness of `demoScenarios.test.ts`: it plays a scenario with the
// operator's presses (Start, Scan scene, Look, the capability check, the activations, Try again, the robot confirmation)
// under vitest's fake clock, renders the DEMO screen with the server renderer at every step, and reads back what the
// screen draws (docs/autonomous-demo/console-redesign-2026-09-27.md, section 8.1): the outcome card, the activation
// cards, the timeline's keys, the primary control, the readiness verdict, the signature card, every control and whether
// it is on, the stages, the Cake region and the camera chip. The screen is read from its markup only, never from the
// store, except for the record the claims compare it with.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { vi } from 'vitest';
import type { Command } from '@kiwi/demo-contracts';
import { SyntheticSource } from '../api/synthetic';
import { scenarioUrl } from '../api/syntheticScenarios';
import type { ScenarioSpec } from '../api/syntheticScenarios';
import { needsHydration, selectedRun } from '../model/selectedRun';
import { runIsActive } from '../model/steps';
import { scenarioFrom } from '../panels/sourceChoice';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { scanSceneCommand, submitRefusal } from '../panels/Actions';
import { DemoScreen, timelineTarget } from './DemoScreen';
import { pressRetry } from './retryPress';
import { pressStart } from './startPress';

export type Step = 'find' | 'pick' | 'carry' | 'place' | 'return';
export type Status = 'not_reached' | 'entered' | 'active' | 'verified' | 'failed' | 'not_observed' | 'stopped_by_controller' | 'stopped_on_request' | 'refused' | 'no_outcome';

/** The controls whose on or off the tests read, by their data-control id. */
export const CONTROLS = ['start', 'stop', 'retry', 'return_arm', 'scan_scene', 'observe', 'candidate'] as const;

export interface ScreenRead {
  /** The mission as the store holds it at the render, for the claims that compare the screen with the record. */
  mission: string | null;
  waiting: string | null;
  holdOffers: string[] | null;
  /** The run the runtime reports in progress, or null. */
  active: string | null;
  /** Every run id the mission record lists, for the claims that the screen prints them verbatim. */
  runIds: string[];
  /** The outcome card: its state, its text, the mission's and the run's verdict ids, its steps and what it reads. */
  outcome: {
    card: string;
    text: string;
    mission: string | null;
    run: string | null;
    /** The run the card's run line names. */
    runId: string | null;
    acknowledged: boolean;
    steps: string[];
    holding: string | null;
    cause: string | null;
    frame: string | null;
    ended: string | null;
    /** The stage the card's run is in or ended in (`data-run-stage` on the run line), or null when it names none. */
    runStage: string | null;
    /** The card's "Decided by" line, whole (`data-failure-limit`), or null when the card has none. */
    limit: string | null;
  };
  /** The activation cards, in the order drawn, each with its verdict id. */
  activations: Array<{ id: string; verdict: string }>;
  /** The operation ids of the mission's activation records, as read by the store. */
  operations: string[];
  /** The timeline's keys, in the order drawn, and how many of its lines are presses. */
  timeline: string[];
  timelinePresses: number;
  /** The controls drawn with data-primary="true", by their data-control (or data-candidate) id. */
  primary: string[];
  readiness: string;
  readinessSentence: string;
  signature: { stale: string | null; verdict: string | null };
  stopControls: number;
  /** Every data-t value on the screen. */
  times: string[];
  /** The whole markup, for the claims about attributes and styles. */
  markup: string;
  /** Every control by its data-control id (its first drawing): true when it is off. */
  off: Record<string, boolean>;
  /** The controls of CONTROLS that are drawn and on, in CONTROLS order. */
  offered: string[];
  stages: Record<Step, Status>;
  startOff: boolean;
  cake: string;
  timelineCake: string;
  chip: string;
  cover: string;
  /** The whole screen's text, for the claims about words that must never appear. */
  text: string;
}

function decode(text: string): string {
  return text
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&#39;/g, "'");
}

export function textOf(markup: string): string {
  return decode(markup.replace(/<style[\s\S]*?<\/style>/g, '').replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ').trim();
}

/** The text of an element, or null when there is none. */
function textOrNull(markup: string | null): string | null {
  return markup === null ? null : textOf(markup);
}

/** The element that carries `attr`, whole, with everything inside it. */
export function element(markup: string, attr: string): string | null {
  const at = markup.indexOf(attr);
  if (at < 0) return null;
  const start = markup.lastIndexOf('<', at);
  const tag = /^<([a-z0-9]+)/.exec(markup.slice(start))?.[1] ?? 'div';
  const pattern = new RegExp(`<${tag}[\\s>]|</${tag}>`, 'g');
  pattern.lastIndex = start;
  let depth = 0;
  for (let match = pattern.exec(markup); match !== null; match = pattern.exec(markup)) {
    depth += match[0].startsWith('</') ? -1 : 1;
    if (depth === 0) return markup.slice(start, match.index + match[0].length);
  }
  return markup.slice(start);
}

export function readScreen(markup: string, store: ConsoleStore): ScreenRead {
  const snap = store.getSnapshot();
  const card = element(markup, 'data-outcome-card=');
  const stages = {} as Record<Step, Status>;
  for (const match of markup.matchAll(/data-stage="([a-z]+)"[^>]*data-stage-status="([a-z_]+)"/g)) stages[match[1] as Step] = match[2] as Status;
  const chipMarkup = element(markup, 'data-camera-chip=""');
  const off: Record<string, boolean> = {};
  for (const match of markup.matchAll(/data-control="([a-z_-]+)"[^>]*aria-disabled="(true|false)"/g)) {
    const id = match[1] as string;
    if (!(id in off)) off[id] = match[2] === 'true';
  }
  const primary: string[] = [];
  for (const match of markup.matchAll(/<button[^>]*data-primary="true"[^>]*>/g)) {
    const tag = match[0];
    primary.push(/data-candidate="([^"]+)"/.exec(tag)?.[1] ?? /data-control="([^"]+)"/.exec(tag)?.[1] ?? 'unnamed');
  }
  const cardText = card === null ? '' : textOf(card);
  const signatureTag = /<[a-z]+[^>]*data-signature-card=""[^>]*>/.exec(markup)?.[0] ?? '';
  return {
    mission: snap.mission?.state ?? null,
    waiting: snap.mission?.waiting_reason ?? null,
    holdOffers: snap.mission?.hold?.next_actions ?? null,
    active: snap.view.active_run?.run_id ?? null,
    runIds: (snap.mission?.targets ?? []).flatMap((target) => target.runs),
    outcome: {
      card: /data-outcome-card="([a-z]+)"/.exec(markup)?.[1] ?? 'absent',
      text: cardText,
      mission: card === null ? null : (/data-verdict="([a-z_]+)"/.exec(card)?.[1] ?? null),
      run: card === null ? null : (/data-run-verdict="([a-z_]+)"/.exec(card)?.[1] ?? null),
      runId: /\b(synthetic-run-\d+)\b/.exec(textOf(element(card ?? '', 'data-run-line=') ?? ''))?.[1] ?? null,
      acknowledged: /data-outcome-card="collapsed"/.test(markup),
      steps: card === null ? [] : [...card.matchAll(/data-step="([a-z_]+)"/g)].map((match) => match[1] as string),
      holding: card === null ? null : (/data-holding="([a-z]+)"/.exec(card)?.[1] ?? null),
      cause: card === null ? null : (/data-cause="([a-z_0-9]+)"/.exec(card)?.[1] ?? null),
      frame: card === null ? null : (/data-failure-frame="([^"]*)"/.exec(card)?.[1] ?? null),
      ended: card === null ? null : (/data-ended="" data-t="([^"]+)"/.exec(card)?.[1] ?? null),
      limit: textOrNull(element(card ?? '', 'data-failure-limit=')),
      runStage: /data-run-line=""[^>]*data-run-stage="([a-z]+)"/.exec(card ?? '')?.[1] ?? null,
    },
    activations: [...markup.matchAll(/data-activation-card="([^"]+)" data-verdict="([^"]+)"/g)].map((match) => ({ id: match[1] as string, verdict: match[2] as string })),
    operations: (snap.mission?.activations ?? []).map((activation) => activation.operation_uuid),
    timeline: [...markup.matchAll(/data-timeline-entry="[a-z]+" data-key="([^"]+)"/g)].map((match) => decode(match[1] ?? '')),
    timelinePresses: (markup.match(/data-timeline-entry="press"/g) ?? []).length,
    primary,
    readiness: /data-readiness="([a-z_]+)"/.exec(markup)?.[1] ?? '',
    readinessSentence: textOf(element(markup, 'data-readiness-sentence=""') ?? ''),
    signature: { stale: /data-signature-stale="([a-z]+)"/.exec(signatureTag)?.[1] ?? null, verdict: /data-verdict="([a-z_]+)"/.exec(signatureTag)?.[1] ?? null },
    stopControls: (markup.match(/data-control="stop"/g) ?? []).length,
    times: [...markup.matchAll(/data-t="([^"]*)"/g)].map((match) => match[1] as string),
    markup,
    off,
    offered: CONTROLS.filter((id) => id in off && off[id] === false),
    stages,
    startOff: /data-control="start"[^>]*aria-disabled="true"/.test(markup),
    cake: textOf(element(markup, 'data-cake-column="demo"') ?? ''),
    timelineCake: [...markup.matchAll(/data-stage-cake=""[^>]*>([^<]*)/g)].map((match) => decode(match[1] ?? '')).join(' '),
    chip: [chipMarkup === null ? '' : textOf(chipMarkup), textOf(element(markup, 'data-camera-note=""') ?? '')].join(' ').trim(),
    cover: textOf(element(markup, 'data-camera-cover=""') ?? ''),
    text: textOf(markup),
  };
}

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

/** What the DemoScreen's effects do in a browser: hydrate the selected run, fold the target's other runs. */
export async function settle(store: ConsoleStore): Promise<void> {
  for (let pass = 0; pass < 3; pass += 1) {
    const snap = store.getSnapshot();
    const selected = selectedRun(snap.view, snap.session.runtime_replaced);
    if (needsHydration(selected, snap.view, snap.hydrated) && selected.run_id !== null) await store.hydrateRun(selected.run_id);
    const again = store.getSnapshot();
    const target = timelineTarget(again.mission, selected.run_id);
    if (target !== null) {
      const ids = target.runs.filter((id) => id !== selected.run_id);
      if (ids.length > 0) await store.foldRuns(ids);
    }
    await vi.advanceTimersByTimeAsync(0);
  }
}

/** The candidates each scenario's operator presses while the mission holds for the capability, in order. */
const ACTIVATIONS: Record<string, string[]> = {
  'one-block': ['mobile-demo-active-successor-abort-swap', 'mobile-demo-swapped'],
  'abort-swap-repeated': ['mobile-demo-active-successor-abort-swap', 'mobile-demo-active-successor-abort-swap', 'mobile-demo-active-successor-abort-swap', 'mobile-demo-swapped'],
  'tampered-refused-before-transaction': ['mobile-demo-swapped-tampered', 'mobile-demo-swapped'],
  // As on 8771 on 2026-09-27: the abort-swap first, then the swap.
  'swap-then-stopped-holding': ['mobile-demo-active-successor-abort-swap', 'mobile-demo-swapped'],
};

/** The Plans the capability check applies, in order. */
const CHECKS: Record<string, string[]> = { 'undo-to-inert': ['mobile-demo-swapped', 'mobile-demo-starting'] };

export interface Played {
  /** The screen before the operator pressed anything. */
  start: ScreenRead;
  /** The screen once the mission first holds (for the capability or for a decision), settled; null when it never holds. */
  hold: ScreenRead | null;
  /** For a capability hold: the screen once the activations were pressed. */
  afterActivations: ScreenRead | null;
  /** The screen where the scenario rests: the mission ended, a hold that offers only Stop, or nothing more to play. */
  end: ScreenRead;
  /** The same screen rendered again 20 s later, with nothing pressed in between (spec INV-9). */
  again: ScreenRead;
  /** Every render taken while the scenario played. */
  all: ScreenRead[];
  /**
   * With `reloads`: the renders of a tab opened afresh while a run was in progress (a reload, or a second display), and
   * the renders of the playing tab every 50 ms for 400 ms after each press. Empty otherwise.
   */
  extra: ScreenRead[];
  receipts: string[];
  /** With `presses`: every control drawn on at the start and at the end, pressed as the screen presses it. */
  presses: Press[];
}

/** One press of a control the screen drew on, and whether the rehearsal's runtime (or the click's own re-check) refused it. */
export interface Press {
  moment: 'start' | 'end';
  control: string;
  /** Null when the press was taken; else the refusal, in the words or the code it came back with. */
  refused: string | null;
}

/** The controls a probe presses, in this order: the three standalone commands, then Start, which begins a mission. */
const PROBED = ['scan_scene', 'observe', 'return_arm', 'start'] as const;

const PROBE_COMMANDS: Record<string, Command> = {
  scan_scene: scanSceneCommand(),
  observe: { type: 'observe', args: { holding: false } },
  return_arm: { type: 'return_arm', args: { plan_only: false } },
};

/**
 * PRESS EVERY CONTROL DRAWN ON, one after the other, each only while it is still drawn on. A standalone command that is
 * taken is played to its end before the next press; Start is pressed last and ends the probe.
 */
async function pressDrawn(store: ConsoleStore, moment: Press['moment'], presses: Press[]): Promise<void> {
  for (const control of PROBED) {
    await settle(store);
    const screen = readScreen(render(store), store);
    if (!screen.offered.includes(control)) continue;
    if (control === 'start') {
      const receipt = await pressStart(store);
      presses.push({ moment, control, refused: receipt.tone === 'refused' ? receipt.text : null });
      return;
    }
    const clickRefusal = submitRefusal(store.getSnapshot(), control);
    if (clickRefusal !== null) {
      presses.push({ moment, control, refused: clickRefusal });
      continue;
    }
    const track = await store.submit(PROBE_COMMANDS[control] as Command);
    const refused = track.result?.status === 'rejected' ? `rejected: ${String(track.result.reason_code)}` : null;
    presses.push({ moment, control, refused });
    if (refused === null) {
      await untilIdle(store, async () => readScreen(render(store), store));
      await vi.advanceTimersByTimeAsync(5500);
    }
  }
}

/** Advance the clock until the run in progress has ended in the store's fold and the runtime reports none in progress. */
async function untilIdle(store: ConsoleStore, take: () => Promise<ScreenRead>): Promise<void> {
  for (let slice = 0; slice < 400; slice += 1) {
    await vi.advanceTimersByTimeAsync(250);
    if (slice % 8 === 0) await take();
    const view = store.getSnapshot().view;
    if (view.outcome.run_id !== null && !runIsActive(view) && view.active_run === null) return;
  }
}

/**
 * Play `spec` as its operator would. `variant` 'scan-first' presses Scan scene alone and nothing else. `stopAtHold`
 * presses Stop at the first hold for a decision instead of Try again.
 */
export async function playScenario(
  spec: ScenarioSpec,
  options: { variant?: 'normal' | 'scan-first'; stopAtHold?: boolean; reloads?: boolean; presses?: boolean } = {},
): Promise<Played> {
  // THE START, PRESSED: a tab of its own on a source of its own, so the play below starts from the untouched scenario.
  const presses: Press[] = [];
  if (options.presses === true) {
    const probeStore = new ConsoleStore(new SyntheticSource({ scenario: scenarioFrom(scenarioUrl(spec.name)) }));
    await probeStore.start();
    await pressDrawn(probeStore, 'start', presses);
    probeStore.dispose();
  }
  const source = new SyntheticSource({ scenario: scenarioFrom(scenarioUrl(spec.name)) });
  const store = new ConsoleStore(source);
  await store.start();
  const all: ScreenRead[] = [];
  const extra: ScreenRead[] = [];
  const receipts: string[] = [];
  // A TAB OPENED WHILE A RUN IS IN PROGRESS. It subscribes from the snapshot's cursor, as a reload does, so it never folds
  // the `run.started` of the run the runtime reports active; its outcome is whatever the snapshot's `last_outcome` says.
  const reopen = async (): Promise<void> => {
    if (options.reloads !== true || store.getSnapshot().view.active_run === null) return;
    const tab = new ConsoleStore(source);
    await tab.start();
    await settle(tab);
    extra.push(readScreen(render(tab), tab));
    tab.dispose();
  };
  const take = async (): Promise<ScreenRead> => {
    await settle(store);
    const screen = readScreen(render(store), store);
    all.push(screen);
    await reopen();
    return screen;
  };
  // THE MOMENTS JUST AFTER A PRESS, where a snapshot naming the new run can be folded before the new run's `run.started`.
  const closely = async (): Promise<void> => {
    if (options.reloads !== true) return;
    for (let step = 0; step < 8; step += 1) {
      await settle(store);
      extra.push(readScreen(render(store), store));
      await reopen();
      await vi.advanceTimersByTimeAsync(50);
    }
  };
  const start = await take();

  if (options.variant === 'scan-first') {
    receipts.push(JSON.stringify(await store.submit({ type: 'scan_scene', args: { plan_only: false } } as unknown as Command)));
    await closely();
  } else if (spec.robot === 'not_confirmed') {
    receipts.push((await pressStart(store)).text);
    await take();
    const permission = store.getSnapshot().robot?.motion_permission;
    if (permission?.current_session_uuid && permission.current_host_instance) {
      receipts.push(JSON.stringify(await store.confirmRobotSession(permission.current_session_uuid, permission.current_host_instance)));
    }
    await take();
    receipts.push((await pressStart(store)).text);
    await closely();
  } else if (spec.trigger === 'look') {
    receipts.push(JSON.stringify(await store.submit({ type: 'observe', args: { holding: false } } as Command)));
    await closely();
  } else if (spec.trigger === 'check') {
    for (const key of CHECKS[spec.name] ?? []) {
      const state = store.getSnapshot().capabilityCheck;
      const candidate = state?.candidates.find((item) => item.key === key);
      receipts.push(JSON.stringify(await store.applyCapabilityCheck(key, candidate?.payload_digest ?? '', state?.current.session_uuid ?? '')));
      await vi.advanceTimersByTimeAsync(5500);
      await take();
    }
  } else if (spec.trigger === 'scan') {
    // Scan scene on its own, played to its end, then Start.
    receipts.push(JSON.stringify(await store.submit({ type: 'scan_scene', args: { plan_only: false } } as unknown as Command)));
    await closely();
    await untilIdle(store, take);
    await vi.advanceTimersByTimeAsync(5500);
    await take();
    receipts.push((await pressStart(store)).text);
    await closely();
  } else {
    receipts.push((await pressStart(store)).text);
    await closely();
  }

  let hold: ScreenRead | null = null;
  let afterActivations: ScreenRead | null = null;
  let decided = false;
  for (let slice = 0; slice < 2400; slice += 1) {
    await vi.advanceTimersByTimeAsync(250);
    const snap = store.getSnapshot();
    if (slice % 8 === 0) await take();
    const reason = snap.mission?.waiting_reason ?? null;
    const waiting = snap.mission?.state === 'WAITING_FOR_CAPABILITY';
    if (hold === null && waiting && reason !== null) {
      await vi.advanceTimersByTimeAsync(5500);
      hold = await take();
      if (reason === 'capability_unavailable') {
        for (const key of ACTIVATIONS[spec.name] ?? []) {
          receipts.push(JSON.stringify(await store.activateCandidate(key)));
          await vi.advanceTimersByTimeAsync(300);
        }
        await closely();
        afterActivations = await take();
      }
    }
    if (hold !== null && waiting && reason === 'operator_decision' && !decided) {
      decided = true;
      if (options.stopAtHold === true) {
        receipts.push(JSON.stringify(await store.requestStop()));
      } else {
        receipts.push((await pressRetry(store)).text);
      }
      await closely();
      await vi.advanceTimersByTimeAsync(300);
    }
    const now = store.getSnapshot();
    const state = now.mission?.state ?? null;
    const ended = state === null || state === 'COMPLETE' || state === 'FAILED' || state === 'STOPPED';
    // A hold that offers no retry is where the scenario rests: nothing moves until the operator stops it.
    const offers = now.mission?.hold?.next_actions ?? [];
    const resting = decided && now.mission?.waiting_reason === 'operator_decision' && now.mission?.state === 'WAITING_FOR_CAPABILITY' && !offers.includes('retry') && !offers.includes('move_block_then_retry');
    if ((ended || resting) && now.view.active_run === null && slice > 40) break;
  }
  await vi.advanceTimersByTimeAsync(6000);
  const end = await take();
  await vi.advanceTimersByTimeAsync(20000);
  await settle(store);
  const again = readScreen(render(store), store);
  if (options.presses === true) await pressDrawn(store, 'end', presses);
  store.dispose();
  return { start, hold, afterActivations, end, again, all, extra, receipts, presses };
}
