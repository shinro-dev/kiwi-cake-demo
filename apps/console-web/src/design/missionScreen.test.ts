// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE MISSION, ON THE RENDERED SCREEN.
//
// The models beside this file are pinned on their own, and that is not enough on its own: this project has already
// shipped a panel that was written, reviewed and committed while nothing imported it, with a green suite over it
// (`panels/OutcomePanel.tsx`). So these render the whole screen to static markup and read the words out, which is the
// cheapest test that cannot be fooled that way.
//
// Four things are asserted here that no model test can assert: that a held mission SAYS it is held on the screen, that
// the prepared activation is offered then and NOT at any other time, that the rail and the figures beside it name the
// mission's own target, and that a submitted activation does not read as a finished one anywhere on the page.
//
// It is a .ts file and builds its elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import runFinished from '@kiwi/demo-contracts/fixtures/event/run.finished.json';
import type { ContractEvent } from '../contracts/envelope';
import { SyntheticSource } from '../api/synthetic';
import { applyEvent, applySnapshot, initialState } from '../model/reducer';
import type { MissionRecord } from '../model/missionRecord';
import type { ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { robotStatus } from '../model/robotStatusFixture';
import { commandScope } from '../model/command';
import { Drawer } from './Drawers';
import { Screen } from './Screen';
import { runSummary } from './runSummary';

function text(markup: string): string {
  return markup
    .replace(/<[^>]*>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ');
}

function held(extra: Partial<MissionRecord> = {}): MissionRecord {
  return {
    mission_record_version: '1',
    mission_id: 'mission-0001',
    state: 'WAITING_FOR_CAPABILITY',
    data_mode: 'live',
    performs_motion: true,
    execution_label: 'physical',
    started_wall_utc: '2026-09-23T10:00:00.000Z',
    selected_target: 'cube-b',
    waiting_reason: 'capability_unavailable',
    detail: null,
    targets: [
      { target_id: 'cube-a', label: 'Cube A', reachable: true, requires_capability: null, state: 'PLACED', runs: ['run-a1'] },
      {
        target_id: 'cube-b',
        label: 'Cube B',
        reachable: false,
        requires_capability: true,
        state: 'WAITING_FOR_CAPABILITY',
        runs: [],
      },
    ],
    activations: [],
    ...extra,
  };
}

/** The running fixture, and the same with the record that FINISHED that run folded in. */
function running(): ViewState {
  return applySnapshot(initialState(), runningSnapshot as unknown as Snapshot);
}

function finished(): ViewState {
  return applyEvent(running(), runFinished as unknown as ContractEvent);
}

/** The screen as it stands with one published store snapshot. Nothing starts and nothing is fetched. */
function screenWords(patch: Partial<StoreSnapshot>, view: ViewState = running()): string {
  const store = new ConsoleStore(new SyntheticSource());
  // The store's published snapshot is what every component reads, exactly as `screen.test.ts` does it and for the
  // same reason: adding an injection point to production code for a test's benefit would be the wrong trade.
  const state = store.getSnapshot();
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...state, ...patch, view };
  return text(renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(Screen))));
}

describe('a mission that is held, on the screen', () => {
  const waiting = {
    mission: held(),
    missionCandidates: [{ key: 'candidate-0001', payload_digest: 'sha256:abcd' }],
  };

  it('says it is held, says why, and names the target and the capability the robot reports', () => {
    const words = screenWords({ ...waiting, robot: robotStatus(), robotReadAtMs: Date.now() });
    expect(words).toContain('Held, waiting for a capability');
    expect(words).toContain('Cube B');
    expect(words).toContain('mobile_approach');
    expect(words).toContain('capability_unavailable');
    expect(words).toContain('Nothing has failed');
  });

  it('offers the prepared activation, by candidate key', () => {
    const words = screenWords(waiting);
    expect(words).toContain('Activate this candidate');
    expect(words).toContain('candidate-0001');
  });

  it('offers it at no other time', () => {
    for (const state of ['RUNNING', 'COMPLETE', 'STOPPED', 'FAILED']) {
      const words = screenWords({ ...waiting, mission: held({ state }) });
      expect(words).not.toContain('Activate this candidate');
      expect(words).not.toContain('Held, waiting for a capability');
    }
    // And not at all for a runtime carrying no mission, which is every runtime before this surface existed.
    expect(screenWords({ mission: null })).not.toContain('Activate this candidate');
  });

  it('names the mission own target and counts the mission own targets, not one cube', () => {
    const words = screenWords(waiting);
    // The rail names the held target; the hold itself is stated once, in the action card or the request card.
    expect(words).toContain('Cube B, held');
    expect(words.split('Held, waiting for a capability').length - 1).toBe(1);
    expect(words).toContain('1 of 2 cubes placed');
    // The single-cube wording is what a runtime with no mission gets, and it is not on this screen.
    expect(words).not.toContain('of 1 cube placed');
  });

  it('attributes a finished run to the target the mission recorded it against, and to no other', () => {
    // The running fixture's own run, recorded by this mission against Cube A. The result card says so, so the outcome
    // cannot be read against Cube B, which is the target the rail and the figures are about.
    const attributed = held({
      targets: [
        {
          target_id: 'cube-a',
          label: 'Cube A',
          reachable: true,
          requires_capability: null,
          state: 'PLACED',
          runs: ['synthetic-run-0001'],
        },
        {
          target_id: 'cube-b',
          label: 'Cube B',
          reachable: false,
          requires_capability: true,
          state: 'WAITING_FOR_CAPABILITY',
          runs: [],
        },
      ],
    });
    // THE OUTCOME IS HISTORY'S. The main screen no longer restates a finished run's result; it points at History.
    const main = screenWords({ mission: attributed, missionCandidates: [] }, finished());
    expect(main).not.toContain('carried for');
    expect(main).not.toContain('Placed and returned');
    // History's summary attributes it, to the target the mission recorded it against.
    const store = new ConsoleStore(new SyntheticSource());
    const snap = { ...store.getSnapshot(), view: finished(), mission: attributed };
    expect(runSummary(snap, commandScope(snap.view, () => null), attributed)?.target?.label).toBe('Cube A');
    // A run the mission attributes to nobody is attributed to nobody there either.
    expect(runSummary({ ...snap, mission: held() }, commandScope(snap.view, () => null), held())?.target).toBe(null);
  });

  it('draws no step of the run this tab folded against the target that has not run', () => {
    const words = screenWords(waiting);
    // The fold is the running fixture, whose run belongs to no target of this mission. Cube B has been nowhere.
    expect(words).toContain('Find');
    expect(words).toContain('Return');
    expect(words).not.toContain('Verified');
  });
});

describe('an activation on the screen', () => {
  const submitted = held({
    activations: [
      {
        operation_uuid: 'op-0001',
        target_id: 'cube-b',
        candidate_key: 'candidate-0001',
        payload_digest: 'sha256:abcd',
        activation_state: 'SUBMITTED',
        outcome: 'ok',
        transaction_phase: 'COMPLETED',
        transaction_uuid: 'tx-0001',
        detail: null,
        t_wall_utc: '2026-09-23T10:05:00.000Z',
      },
    ],
  });

  it('does not read as activated while the transaction merely reports itself complete', () => {
    const words = screenWords({ mission: submitted, missionCandidates: [] });
    expect(words).toContain('Submitted, not yet activated');
    expect(words).toContain('A phase is not an activation');
    // The one word that would make a pending activation read as a finished one.
    expect(words).not.toContain('Activated The slot was read back');
  });

  it('says a request that did not land judged nothing, and never that Cake rejected it', () => {
    const failed = held({
      activations: [
        {
          operation_uuid: 'op-0002',
          target_id: 'cube-b',
          candidate_key: 'candidate-0001',
          payload_digest: null,
          activation_state: 'REQUEST_FAILED',
          outcome: null,
          transaction_phase: null,
          transaction_uuid: null,
          detail: null,
          t_wall_utc: null,
        },
      ],
    });
    const words = screenWords({ mission: failed, missionCandidates: [] }).toLowerCase();
    expect(words).toContain('did not reach the resident');
    expect(words).toContain('nothing was judged');
    expect(words).not.toContain('rejected');
  });

  it('says an aborted transaction activated nothing, and blames no upload or signature', () => {
    const rejected = held({
      activations: [
        {
          operation_uuid: 'op-0003',
          target_id: 'cube-b',
          candidate_key: 'candidate-0001',
          payload_digest: null,
          activation_state: 'REJECTED_BY_CAKE',
          outcome: 'aborted',
          transaction_phase: 'ABORTED',
          transaction_uuid: 'tx-0003',
          detail: null,
          t_wall_utc: null,
        },
      ],
    });
    const words = screenWords({ mission: rejected, missionCandidates: [] }).toLowerCase();
    expect(words).toContain('the transaction aborted');
    // CHANGED DELIBERATELY: the cause "not a capsule this resident admitted" is no longer stated, because aborts of an
    // admitted candidate were measured (docs/autonomous-demo/cake-console-handoff-2026-09-23.md, section 8). The
    // screen says the answer carries no cause.
    expect(words).toContain('its answer says that the transaction aborted and not why');
    expect(words).not.toContain('not a capsule this resident admitted');
    for (const forbidden of ['upload', 'signature', 'corrupt']) {
      expect(words).not.toContain(forbidden);
    }
  });
});

describe('the hold after a failed attempt, in the history drawer and on the classic rail', () => {
  const decision = (): MissionRecord =>
    held({
      waiting_reason: 'operator_decision',
      selected_target: 'cube-b',
      targets: [{ target_id: 'cube-b', label: 'Cube B', reachable: true, requires_capability: 'auto', state: 'WAITING_FOR_CAPABILITY', runs: [] }],
      hold: {
        target_id: 'cube-b', attempt: 1, run_id: null, park_run_id: null, outcome_class: 'retryable', reason_code: 'align_cube_not_detected', phase: 'ALIGN',
        observation_ids: [], stop_state: { holding: false, base_ahead: false, moved: true }, next_actions: ['retry', 'stop'], budget_left: 1, retry_requested: false,
      },
    });

  it('says the mission and its target are held for the decision, and never that they wait for a capability', () => {
    const store = new ConsoleStore(new SyntheticSource());
    const snapshot = { ...store.getSnapshot(), mission: decision() };
    const markup = renderToStaticMarkup(
      createElement(StoreContext, { value: store }, createElement(Drawer, { id: 'history', snapshot, store, mode: 'synthetic', onSelect: () => {}, onClose: () => {} })),
    );
    const words = text(markup);
    // The mission's word is the mission card's (spec 5.1): Held for you, and never the capability wait's.
    expect(markup).toContain('data-mission-verdict="held_for_you"');
    expect(words).toContain('Mission held for you');
    expect(words).not.toContain('Mission waiting for Cake');
    expect(words).toContain("Cube B held for the operator's decision after a failed attempt");
    expect(markup).not.toContain('waiting for a capability');
  });

  it('does not name Try again on the classic screen, which has no such control, and says where the choice is', () => {
    const words = screenWords({ mission: decision(), missionError: null }, finished());
    expect(words).toContain('Held for your decision');
    expect(words).not.toContain('Try again');
    expect(words).toContain('the default screen');
  });

  it('keeps the capability wait out of the classic rail tooltip too', () => {
    const store = new ConsoleStore(new SyntheticSource());
    const state = store.getSnapshot();
    (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...state, mission: decision(), view: running() };
    const markup = renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(Screen)));
    expect(markup).toContain('Cube B');
    expect(markup).not.toContain('waiting for a capability');
  });
});

describe('a target whose mode the robot decides (requires_capability "auto"), in the mission drawer', () => {
  function drawerWords(mission: MissionRecord): string {
    const store = new ConsoleStore(new SyntheticSource());
    const snapshot = { ...store.getSnapshot(), mission };
    return text(
      renderToStaticMarkup(
        createElement(StoreContext, { value: store }, createElement(Drawer, { id: 'history', snapshot, store, mode: 'synthetic', onSelect: () => {}, onClose: () => {} })),
      ),
    );
  }
  const auto = (measured: string | null) =>
    held({
      state: 'RUNNING',
      waiting_reason: null,
      selected_target: 'cube',
      targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', measured_mode: measured, state: 'ACTIVE', runs: [] }],
    });

  it('says the decision is the measurement before the robot has measured, and states the mode after', () => {
    expect(drawerWords(auto(null))).toContain('whether it needs a capability is decided by measurement, and the robot has not measured it yet');
    expect(drawerWords(auto('BASE_ADVANCE'))).toContain('decided by measurement: it measured BASE_ADVANCE, so it needs the capability');
    expect(drawerWords(auto('ARM_ONLY'))).toContain('decided by measurement: it measured ARM_ONLY, so the arm alone places it');
  });

  it('says an arm-alone target the mission holds for the capability waits for it for its base fallback, never that it needs none', () => {
    const waiting = held({
      selected_target: 'cube',
      targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', measured_mode: 'ARM_ONLY', state: 'WAITING_FOR_CAPABILITY', runs: [] }],
    });
    const words = drawerWords(waiting);
    expect(words).toContain('it measured ARM_ONLY, so the arm alone places it, and the mission waits for the capability');
    // verify r6 policy MF2: the fallback is stated as conditional on the loaded arm coming back to the carry pose.
    expect(words).toContain('that its base fallback needs: should the release be refused, the base moves only if the loaded arm comes back to the carry pose');
    expect(words).toContain('otherwise the arm holds the cube where it stands for the operator');
    expect(words).not.toContain('no capability is needed');
  });
});
