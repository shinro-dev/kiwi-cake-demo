// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// STABLE POSITIONS (operator layout ruling 2026-09-28; docs/autonomous-demo/console-redesign-2026-09-27.md, section 2.1).
// The mission card grew with a run's account when the run ended, and the stages, the scene and the wrist camera under it
// dropped 580 px at 1440 x 900 (verify-r4 browser, must_fix 1). The mission card and Cake now keep one fixed footprint;
// each keeps its head in view and draws the rest, whole, in a bounded region that scrolls within the card.
//
// This suite pins the parts a markup render can see: the fixed heights in the stylesheet at both widths, the card filling
// its region, and every part of the account inside the bounded region with nothing dropped. The positions themselves are
// measured in a real browser, idle, running and ended, by tests/test-browser-layout.sh.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import type { HistoryEntry } from '../api/types';
import type { ContractEvent } from '../contracts/envelope';
import type { MissionAttemptRecord, MissionRecord } from '../model/missionRecord';
import { outcomeCard } from '../model/outcomeCard';
import type { OutcomeInput } from '../model/outcomeCard';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { moreBelow } from './BoundedAccount';
import { DemoScreen } from './DemoScreen';
import designCss from './design.css?raw';
import { OutcomeCard } from './OutcomeCard';
import { TopBar } from './TopBar';
import type { TopBarProps } from './TopBar';
import { readiness } from '../model/readiness';
import { modeMark } from '../model/modeMark';

const RUN = 'console-20260928T101500Z-mst4b1e1';

function ev(seq: number, kind: string, t: string, payload: Record<string, unknown>, reason: string | null = null): ContractEvent {
  return {
    schema_version: '1', seq, run_id: RUN, action_id: null, observation_id: null,
    producer: { name: 'demo-runtime', version: '0.1.0', artifact_digest: null, contract_version: '1' },
    source_session: 'rt', t_mono_ns: seq, clock_domain: 'laptop_monotonic', t_wall_utc: t, reason_code: reason, provenance: null, kind, payload,
  } as unknown as ContractEvent;
}

const CODE = 'mission_earlier_target_disturbed';
const MEAS = [
  { name: 'unclaimed_placed_count', value: 1, unit: null },
  { name: 'earlier_targets_undisturbed', value: false, unit: null },
  { name: 'predicted_worst_case_centre_margin_mm', value: 40.5, unit: 'mm' },
];
const EVENTS: ContractEvent[] = [
  ev(41, 'run.started', '2026-09-28T10:15:00.000Z', { command_type: 'start_cycle' }),
  ev(42, 'state.transition', '2026-09-28T10:15:20.000Z', { machine: 'cycle', from_state: 'RELEASE_ADMISSION', to_state: 'RELEASED' }),
  ev(43, 'run.finished', '2026-09-28T10:15:28.000Z', {
    outcome: 'failed', exit_status: 5, reason_code: CODE, phase: 'BASE_RETURNED',
    predicates: { object_secured: true, placed: false, clearance_target_met: false, base_returned: true },
    failure: { detail: `PLACED not demonstrated for this mission target (${CODE})`, measurements: MEAS, observation_ids: [] },
  }, CODE),
];
const ATTEMPT: MissionAttemptRecord = {
  attempt: 1, run_id: RUN, park_run_id: null, outcome_class: 'fatal', reason_code: CODE, phase: 'BASE_RETURNED', measurements: MEAS, observation_ids: [],
  stop_state: { holding: false, base_ahead: false, moved: true }, next_actions: ['return_arm', 'start'], budget_left: 0, t_wall_utc: '2026-09-28T10:15:29.000Z',
};
const MISSION: MissionRecord = {
  mission_record_version: '1', mission_id: 'mission-st4b1e000001', state: 'FAILED', data_mode: 'live', performs_motion: true, execution_label: null,
  started_wall_utc: '2026-09-28T10:14:59.000Z', selected_target: 'cube', waiting_reason: null, detail: null, stop_requested: false, stop_requested_wall_utc: null,
  targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'FAILED', runs: [RUN], attempts: [ATTEMPT], retries: [], park_runs: [] }],
  activations: [], hold: null, next_actions: ['return_arm', 'start'],
};
const HISTORY = [{ run_id: RUN, started_wall_utc: '2026-09-28T10:15:00.000Z', finished_wall_utc: '2026-09-28T10:15:28.000Z', outcome: 'failed' }] as unknown as HistoryEntry[];

function ended(): string {
  const input: OutcomeInput = { mission: MISSION, missionError: null, history: HISTORY, runEvents: { [RUN]: EVENTS }, activeRunId: null, acknowledgements: [], capability: null, drawnRunEvents: {} };
  const returnArm = createElement('button', { 'data-control': 'return_arm', 'aria-disabled': 'false' }, 'Return arm');
  return renderToStaticMarkup(createElement(OutcomeCard, { model: outcomeCard(input), returnArm, retrying: false, onRetry: () => undefined, onAcknowledge: () => undefined, onHistory: () => undefined }));
}

/** The declarations of the rule whose selector list is exactly `selector`, inside `block` (the sheet or a media block). */
function rule(block: string, selector: string): string | null {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // THE SELECTOR LIST IS THIS ONE SELECTOR: preceded only by the end of a rule, the start of a block, or a comment.
  return new RegExp(`(^|[{}]|\\*/)\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(block)?.[2] ?? null;
}

function narrowBlock(): string {
  const at = designCss.indexOf('@media (max-width: 1099px)');
  expect(at).toBeGreaterThan(-1);
  return designCss.slice(at, designCss.indexOf('@media (max-width: 719px)'));
}

describe('the mission card and Cake keep one footprint, idle, running or ended (spec 2.1)', () => {
  it('the stylesheet gives both regions a fixed height at desktop width and below 1100 px', () => {
    const desktop = designCss.slice(0, designCss.indexOf('@media (max-width: 1099px)'));
    expect(rule(desktop, '.dc .demo-grid__mission')).toMatch(/(^|;|\s)height:\s*300px/);
    expect(rule(desktop, '.dc .demo-grid__cake')).toMatch(/(^|;|\s)height:\s*300px/);
    expect(rule(narrowBlock(), '.dc .demo-grid__mission')).toMatch(/(^|;|\s)height:\s*380px/);
    expect(rule(narrowBlock(), '.dc .demo-grid__cake')).toMatch(/(^|;|\s)height:\s*400px/);
  });

  it('no rule lets either region grow past that height (no min-height, max-height or height: auto on them)', () => {
    const rules = [...designCss.matchAll(/([^{}]*\.demo-grid__(?:mission|cake)[^{}]*)\{([^}]*)\}/g)];
    for (const found of rules) {
      expect(found[2] ?? '').not.toMatch(/(min-height|max-height)\s*:/);
      expect(found[2] ?? '').not.toMatch(/(^|;|\s)height:\s*auto/);
    }
  });

  it('the readiness sentence keeps room for its lines, so a shorter sentence does not lift the screen under the bar', () => {
    expect(rule(designCss, '.dc [data-readiness-sentence]')).toMatch(/min-height:\s*calc\(2 \* 1\.4em\)/);
    expect(rule(narrowBlock(), '.dc [data-readiness-sentence]')).toMatch(/min-height:\s*calc\(3 \* 1\.4em\)/);
    // Below 720 px the hold's sentence takes four lines at 390 px (verify-r5 M1: an 18 px jump when a hold began).
    expect(rule(designCss.slice(designCss.indexOf('@media (max-width: 719px)')), '.dc [data-readiness-sentence]')).toMatch(/min-height:\s*calc\(4 \* 1\.4em\)/);
  });

  it('Ready keeps the sentence\'s room too, on its own row, so a run started from Ready does not grow the bar (verify-r5 M1)', () => {
    // Ready, the normal state before Start, has no readiness sentence; the bar was 58 px then and 113 px running at 1440,
    // and every region under it moved 55 px at Start. The sentence's place is drawn in every state, empty on Ready.
    const bar = (startOff: string | null, runActive: boolean): string => {
      const p: TopBarProps = {
        mode: modeMark({ dataMode: 'live', sourceKind: 'live', replaying: false }),
        robot: { label: 'LeKiwi bench', connection: 'connected', tone: 'ok', notReadSince: null },
        motion: { words: 'Motion permitted', tone: 'ok' },
        readiness: readiness({ mission: null, runActive, startOff, checklist: [] }),
        start: { label: 'Start', off: startOff, busy: false },
        stop: { off: runActive ? null : 'there is no run to stop', busy: false },
        onStart: () => undefined,
        onStop: () => undefined,
        onDiagnostics: () => undefined,
      };
      return renderToStaticMarkup(createElement(TopBar, p));
    };
    const ready = bar(null, false);
    expect(/data-readiness="([a-z_]+)"/.exec(ready)?.[1]).toBe('ready');
    const sentence = /<span[^>]*data-readiness-sentence=""[^>]*>/.exec(ready)?.[0];
    expect(sentence).toBeDefined();
    expect(sentence).toContain('aria-hidden="true"');
    for (const markup of [ready, bar('robot motion is held: robot_session_not_confirmed', false), bar('a run is active', true)]) {
      expect(/<span[^>]*data-readiness-sentence=""[^>]*>/.exec(markup)?.[0]).toMatch(/flex-basis:100%/);
    }
  });

  it('below 1100 px Start, Stop and Diagnostics take a row of their own, so a wider Start label does not rewrap the bar', () => {
    // At 1039 px from Ready the controls fitted on the first row, and with the running Start label (18 px wider) they
    // wrapped to a row of their own: the bar grew 40 px at Start and Stop moved 49 -> 130.
    expect(rule(narrowBlock(), '.dc [data-topbar-controls]')).toMatch(/flex-basis:\s*100%/);
    const markup = renderToStaticMarkup(
      createElement(TopBar, {
        mode: modeMark({ dataMode: 'live', sourceKind: 'live', replaying: false }),
        robot: { label: 'LeKiwi bench', connection: 'connected', tone: 'ok', notReadSince: null },
        motion: null,
        readiness: readiness({ mission: null, runActive: false, startOff: null, checklist: [] }),
        start: { label: 'Start', off: null, busy: false },
        stop: { off: 'there is no run to stop', busy: false },
        onStart: () => undefined,
        onStop: () => undefined,
        onDiagnostics: () => undefined,
      }),
    );
    const controls = markup.slice(markup.indexOf('data-topbar-controls'));
    expect(markup.indexOf('data-topbar-controls')).toBeGreaterThan(-1);
    for (const control of ['start', 'stop', 'diagnostics']) expect(controls).toContain(`data-control="${control}"`);
  });

  it('the ended card fills its region, with the head, History and Acknowledge outside the bounded account', () => {
    const markup = ended();
    expect(markup).toMatch(/data-region="mission"[^>]*style="[^"]*height:100%/);
    const region = markup.indexOf('data-bounded="mission"');
    expect(region).toBeGreaterThan(-1);
    // History is one click away with a current mission too (spec 12.3; verify-r4 S1), in the head row.
    for (const head of ['data-control="open-history"', 'data-control="acknowledge"', 'data-verdict="failed"', 'data-started=""', 'data-ended=""']) {
      const at = markup.indexOf(head);
      expect(at, head).toBeGreaterThan(-1);
      expect(at, head).toBeLessThan(region);
    }
    expect(markup).toContain('role="region"');
    expect(markup).toContain('tabindex="0"');
  });

  it("nothing of the account is dropped: the run line, the steps, the check, why it stopped and where the robot was left are all in the bounded region", () => {
    const markup = ended();
    const account = markup.slice(markup.indexOf('data-bounded="mission"'));
    for (const part of ['data-run-line=""', 'data-steps=""', 'data-control="return_arm"', `data-mission-check="${CODE}"`, `data-cause="${CODE}"`, 'data-where=""']) {
      expect(account, part).toContain(part);
    }
    // THE STEPS COME BEFORE THE LONG ACCOUNT, so the control the runtime names is in view at the fixed footprint.
    expect(account.indexOf('data-steps=""')).toBeLessThan(account.indexOf('data-mission-check='));
    expect(account.indexOf('data-mission-check=')).toBeLessThan(account.indexOf('data-cause='));
    expect(account.indexOf('data-cause=')).toBeLessThan(account.indexOf('data-where=""'));
  });

  it('Cake fills its region and draws its records in its own bounded region', () => {
    const markup = renderToStaticMarkup(createElement(StoreContext, { value: new ConsoleStore(new SyntheticSource()) }, createElement(DemoScreen)));
    const cake = markup.slice(markup.indexOf('data-region="cake"'), markup.indexOf('class="demo-grid__strip"'));
    expect(cake).toMatch(/^data-region="cake"[^>]*style="[^"]*height:100%/);
    expect(cake).toContain('data-bounded="cake"');
    expect(cake.indexOf('data-bounded="cake"')).toBeLessThan(cake.indexOf('data-signature-card'));
  });

  // VERIFY-R5 (browser) MUST_FIX 1: below 1100 px the strip stacks, and the scene's height changed with its caption
  // ("Drawn from the records of run ...") and its legend, 464, 525 and 571 px idle, running and ended at 390 px, so the
  // wrist camera under it moved 61 and 46 px. Below 1100 px the scene keeps one height and draws its map and legend in a
  // bounded region that scrolls within it.
  it('below 1100 px the scene keeps one height, and draws its map and legend in its own bounded region', () => {
    expect(rule(narrowBlock(), '.dc .demo-strip__scene')).toMatch(/(^|;|\s)height:\s*600px/);
    const desktop = designCss.slice(0, designCss.indexOf('@media (max-width: 1099px)'));
    expect(rule(desktop, '.dc .demo-strip__scene') ?? '').not.toMatch(/(^|;|\s)height:\s*\d/);
    const markup = renderToStaticMarkup(createElement(StoreContext, { value: new ConsoleStore(new SyntheticSource()) }, createElement(DemoScreen)));
    const scene = markup.slice(markup.indexOf('data-region="scene"'));
    expect(markup).toMatch(/<section[^>]*class="demo-strip__scene"[^>]*data-region="scene"|<section[^>]*data-region="scene"[^>]*class="demo-strip__scene"/);
    expect(scene).toContain('data-bounded="scene"');
    expect(scene.indexOf('data-bounded="scene"')).toBeLessThan(scene.indexOf('<svg'));
  });

  it('says there is more below only while the region is scrolled short of its end', () => {
    expect(moreBelow({ scrollTop: 0, clientHeight: 126, scrollHeight: 651 })).toBe(true);
    expect(moreBelow({ scrollTop: 525, clientHeight: 126, scrollHeight: 651 })).toBe(false);
    expect(moreBelow({ scrollTop: 0, clientHeight: 172, scrollHeight: 172 })).toBe(false);
  });
});
