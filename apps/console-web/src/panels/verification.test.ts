// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The four cards that say what the mission did and whether it was verified, actually rendered, with the words an
// operator would read out of them.
//
// Every case here is one of the things the screen got wrong before: a card that said seven things were unavailable
// while a run was going well, a no-op drawn like a placement, a retention score that could be read as a percentage, a
// release admission standing in for a measurement of where the cube ended up, and a failure that said what happened
// without saying what could be done next. They are rendered to static markup, so what is asserted is what would be on
// the screen and not what a pure function returned.
//
// It is a .ts file and builds its elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Capability, PayloadPredicateEvaluated, Snapshot, SourceStamp } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import type { ContractEvent, EventEnvelope } from '../contracts/envelope';
import { applyEvent, applySnapshot, initialState } from '../model/reducer';
import type { PredicateView, ViewState } from '../model/types';
import { FailurePanel } from './FailurePanel';
import { OutcomePanel } from './OutcomePanel';
import { PredicatesPanel } from './PredicatesPanel';

let seq = 100;

function text(markup: string): string {
  return markup
    .replace(/<[^>]*>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ');
}

function envelope(extra: Partial<EventEnvelope> = {}): EventEnvelope {
  seq += 1;
  return {
    schema_version: '1',
    seq,
    run_id: 'run-v',
    action_id: null,
    observation_id: null,
    producer: { name: 'test', version: '0', artifact_digest: null, contract_version: '1' },
    source_session: 'session-v',
    t_mono_ns: seq * 1_000_000,
    clock_domain: 'laptop_monotonic',
    t_wall_utc: null,
    reason_code: null,
    provenance: null,
    ...extra,
  };
}

function transition(to: string): ContractEvent {
  return { ...envelope(), kind: 'state.transition', payload: { machine: 'single_cube', from_state: null, to_state: to } } as ContractEvent;
}

function predicate(payload: Partial<PayloadPredicateEvaluated> & { predicate: string }): ContractEvent {
  return {
    ...envelope(),
    kind: 'predicate.evaluated',
    payload: { value: null, threshold: null, measured: null, unit: null, evidence: [], consumed_source_stamp: null, ...payload },
  } as ContractEvent;
}

function finished(payload: Record<string, unknown>): ContractEvent {
  return {
    ...envelope(),
    kind: 'run.finished',
    payload: {
      outcome: 'failed',
      exit_status: 5,
      predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null },
      automatic_retries: 0,
      task_control_interventions: 0,
      evidence_complete: true,
      evidence_gaps: 0,
      ...payload,
    },
  } as ContractEvent;
}

/** A state with a run in flight, which is the situation every "not yet" case is about. */
function running(...events: ContractEvent[]): ViewState {
  let state = applySnapshot(initialState(), runningSnapshot as unknown as Snapshot);
  for (const event of events) state = applyEvent(state, event);
  return state;
}

/** The same fold with no run in flight, which is what a finished run leaves behind. */
function ended(...events: ContractEvent[]): ViewState {
  return { ...running(...events), active_run: null };
}

function capability(extra: Partial<Capability>): Capability {
  return { id: 'observe', state: 'enabled', reason_code: null, reason: null, dependency: null, planned: false, ...extra };
}

function renderPanel(panel: typeof OutcomePanel, view: ViewState): string {
  return text(renderToStaticMarkup(createElement(panel, { view })));
}

describe('the verification card', () => {
  it('reads an unevaluated check as not checked yet, and never as unavailable', () => {
    const words = renderPanel(PredicatesPanel, running(predicate({ predicate: 'aligned', value: null, state: 'not_evaluated' })));
    expect(words).toContain('Not checked yet');
    expect(words).not.toContain('not determined');
  });

  it('reads the four-state record in the words of each state, with the reason the payload carried', () => {
    const notApplicable = renderPanel(PredicatesPanel, running(predicate({ predicate: 'base_returned', value: null, state: 'not_applicable' })));
    expect(notApplicable).toContain('Does not apply here');
    const unavailable = renderPanel(
      PredicatesPanel,
      running(predicate({ predicate: 'placed', value: null, state: 'unavailable', reason_code: 'no_frame_was_kept' })),
    );
    expect(unavailable).toContain('Unavailable');
    expect(unavailable).toContain('no_frame_was_kept');
  });

  it('reads a record written before the four-state field the way it has always been read, and says so', () => {
    const words = renderPanel(PredicatesPanel, running(predicate({ predicate: 'aligned', value: null })));
    expect(words).toContain('Not checked yet');
    expect(words).toContain('read as not evaluated');
  });

  it('shows the measurement and the threshold of a check that measured false', () => {
    const words = renderPanel(
      PredicatesPanel,
      running(predicate({ predicate: 'release_margin', value: false, measured: 21.4, threshold: 37.96, unit: 'mm' })),
    );
    expect(words).toContain('21.4 mm');
    expect(words).toContain('37.96 mm');
    expect(words).toContain('not met');
  });

  it('writes a retention score as the score it is and never as a percentage', () => {
    const words = renderPanel(PredicatesPanel, running(predicate({ predicate: 'object_secured', value: true, measured: 0.62, threshold: 0.55 })));
    expect(words).toContain('0.62');
    expect(words).toContain('0.55');
    expect(words).not.toContain('%');
    expect(words).toContain('not a probability and not a percentage');
  });

  it('keeps the release admission and the observed placement on separate rows, as separate kinds of evidence', () => {
    const words = renderPanel(
      PredicatesPanel,
      running(
        predicate({ predicate: 'release_admitted', value: true }),
        predicate({ predicate: 'placed', value: true, measured: 31.2, threshold: 3, unit: 'mm' }),
      ),
    );
    expect(words).toContain('release_admitted');
    expect(words).toContain('before the gripper opened');
    // Sentence case (verifier r2 R-5): the words are unchanged, no longer shouted.
    expect(words).toContain('measured after the release');
  });

  it('keeps a recorded verdict apart from the freshness of the evidence it rests on', () => {
    const stamp: SourceStamp = { session: 'host-session-a', seq: 400, sample_mono_ns: 5_000_000_000, clock_domain: 'pi_monotonic' };
    const view = running(
      predicate({ predicate: 'object_secured', value: true, measured: 0.81, threshold: 0.55, consumed_source_stamp: stamp }),
      {
        ...envelope(),
        kind: 'measured.state',
        payload: {
          joints: { shoulder_pan: 0.1 },
          base_velocity: { x_mps: null, y_mps: null, theta_dps: null },
          consumed_source_stamp: { ...stamp, seq: 460, sample_mono_ns: 5_400_000_000 },
        },
      } as ContractEvent,
    );
    const words = renderPanel(PredicatesPanel, view);
    expect(words).toContain('verdict recorded at seq');
    expect(words).toContain('evidence was 400.0 ms older than the newest sample of that source');
    // The verdict itself is still a recorded true: age does not turn it into a refusal.
    expect(words).toContain('true');
  });

  it('says why an age could not be taken rather than printing a number that looks current', () => {
    const words = renderPanel(PredicatesPanel, running(predicate({ predicate: 'aligned', value: true, measured: 1.2, threshold: 6, unit: 'mm' })));
    expect(words).toContain('evidence age: the verdict names no source sample');
  });
});

describe('the outcome card', () => {
  it('says not yet, not unavailable, for everything a run reports when it finishes', () => {
    const words = renderPanel(OutcomePanel, running(transition('SEARCH')));
    expect(words).toContain('Not yet');
    expect(words).not.toContain('unavailable');
    expect(words).not.toContain('Unavailable');
  });

  it('never shows an earlier run’s numbers as the numbers of the run in progress', () => {
    // The snapshot carries the runtime's last finished run, which a console that attaches mid-run folds.
    const words = renderPanel(OutcomePanel, running(transition('SEARCH')));
    expect(words).toContain('last finished run');
    expect(words).toContain('a different run from the one in progress');
    expect(words).toContain('no outcome yet');
  });

  it('says no run has been started before one is', () => {
    const words = renderPanel(OutcomePanel, initialState());
    expect(words).toContain('No run yet');
    expect(words).toContain('nothing to confirm yet');
  });

  it('says unavailable only for a value a finished run did not carry', () => {
    const words = renderPanel(OutcomePanel, ended(finished({ outcome: 'failed', exit_status: null, automatic_retries: null })));
    expect(words).toContain('Unavailable');
    expect(words).not.toContain('Not yet');
  });

  it('says outright that the already-placed ending moved nothing and measured no placement', () => {
    const words = renderPanel(
      OutcomePanel,
      ended(
        predicate({ predicate: 'already_placed', value: true, measured: 41.2, threshold: 3, unit: 'mm' }),
        finished({
          outcome: 'already_placed_noop',
          exit_status: 0,
          predicates: { object_secured: null, placed: true, clearance_target_met: null, base_returned: null },
        }),
      ),
    );
    expect(words).toContain('Nothing was moved');
    expect(words).toContain('no placement was measured');
    expect(words).toContain('not measured in this run');
    expect(words).toContain('is not an acceptance pass');
    expect(words).toContain('already_placed at seq');
    expect(words).not.toContain('confirmed by four predicates');
  });

  it('keeps calling a placement confirmed only when the four end predicates measured true', () => {
    const confirmed = renderPanel(
      OutcomePanel,
      ended(
        finished({
          outcome: 'placed_new',
          exit_status: 0,
          predicates: { object_secured: true, placed: true, clearance_target_met: true, base_returned: true },
        }),
      ),
    );
    expect(confirmed).toContain('confirmed by four predicates');
    const unconfirmed = renderPanel(
      OutcomePanel,
      ended(
        finished({
          outcome: 'placed_new',
          exit_status: 0,
          predicates: { object_secured: true, placed: true, clearance_target_met: false, base_returned: true },
        }),
      ),
    );
    expect(unconfirmed).toContain('not confirmed');
  });
});

describe('the failure card', () => {
  function failed(capabilities: Capability[], predicates: PredicateView[] = []): ViewState {
    const state = ended(
      finished({
        outcome: 'failed',
        reason_code: 'retention_not_demonstrated',
        phase: 'OBJECT_SECURED',
        failure: {
          detail: 'retention NOT demonstrated: the image between the jaws changed with height',
          measurements: [
            { name: 'retention_score', value: 0.31, unit: null },
            { name: 'retention_min_score', value: 0.55, unit: null },
          ],
          observation_ids: [],
        },
      }),
    );
    return { ...state, capabilities, predicates: [...state.predicates, ...predicates] };
  }

  it('opens with the phase and the reason with its numbers, and keeps no next-step table of its own (spec 7.2)', () => {
    const words = renderPanel(
      FailurePanel,
      failed([capability({ id: 'observe' }), capability({ id: 'return_arm', state: 'blocked', reason: 'a run is active: only stop is accepted' })]),
    );
    expect(words).toContain('OBJECT_SECURED');
    expect(words).toContain('Verify grip');
    expect(words).toContain('0.31');
    expect(words).toContain('0.55');
    // The steps are the runtime's list on the mission card; this card no longer derives its own from the reason code.
    expect(words).not.toContain('what can be done next');
    expect(words).not.toContain('the runtime allows it now');
  });

  it('says outright when the failure it explains is not the run now in progress', () => {
    const state = failed([capability({ id: 'observe' })]);
    const words = renderPanel(FailurePanel, { ...state, active_run: { run_id: 'synthetic-run-0009', command_id: null, command_type: null, status: 'running', state: null, started_seq: 4 } });
    expect(words).toContain('the run that ended before the one now in progress');
    expect(words).toContain('run-v');
  });

  it('names a value with no threshold as a measurement rather than as an unavailable comparison', () => {
    const words = renderPanel(FailurePanel, failed([capability({ id: 'observe' })]));
    expect(words).toContain('no threshold recorded');
  });

  it('keeps the raw code and the recorded values one click away from the sentence', () => {
    const words = renderPanel(FailurePanel, failed([capability({ id: 'observe' })]));
    expect(words).toContain('the raw code and the values as recorded');
    expect(words).toContain('reason code: retention_not_demonstrated');
    expect(words).toContain('retention_score = 0.31');
  });
});
