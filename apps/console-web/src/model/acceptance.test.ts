// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A placement that missed its clearance target is the case the 2026-09-22 physical run made real, and the case the
// screen used to describe only in the negative. These hold the sentence it gets instead: what the run DID first, then
// the one check that did not measure true and what that check measured, then what is still outstanding.

import { describe, expect, it } from 'vitest';
import type { OutcomeView, PredicateView } from './types';
import { acceptanceNote, resultPredicates } from './acceptance';

function outcome(patch: Partial<OutcomeView> = {}): OutcomeView {
  return {
    reported: 'placed_new',
    confirmed: null,
    predicates: { object_secured: true, placed: true, clearance_target_met: false, base_returned: true },
    exit_status: 0,
    evidence_complete: false,
    evidence_gaps: 1,
    automatic_retries: 0,
    task_control_interventions: 0,
    run_id: 'run-c',
    finished_seq: 1162,
    reason_code: null,
    ...patch,
  } as OutcomeView;
}

function predicate(name: string, patch: Partial<PredicateView> = {}): PredicateView {
  return { predicate: name, value: false, threshold: 37.96, measured: 35.57, unit: 'mm', measurements: [], seq: 900, ...patch } as PredicateView;
}

describe('the sentence a placement that missed its target gets', () => {
  it('says nothing at all for a run that met every check', () => {
    expect(acceptanceNote(outcome({ confirmed: 'placed_new' }), [])).toBeNull();
  });

  it('says nothing for an outcome that is not a placement', () => {
    expect(acceptanceNote(outcome({ reported: 'failed', confirmed: null }), [])).toBeNull();
    expect(acceptanceNote(outcome({ reported: 'unknown', confirmed: null }), [])).toBeNull();
  });

  it('leads with what the run physically did, not with what it failed', () => {
    const note = acceptanceNote(outcome(), [predicate('clearance_target_met')]);
    expect(note).not.toBeNull();
    expect(note?.startsWith('Placed and returned.')).toBe(true);
  });

  it('names the two numbers for what they ARE, a centre margin and a required centre margin', () => {
    const note = acceptanceNote(outcome(), [predicate('clearance_target_met')]);
    expect(note).toContain('Cube-centre edge margin: 35.57 mm; required: 37.96 mm.');
    // The shortfall the operator reads, taken from the record and not from a constant.
    expect(note).toContain('Clearance target missed by 2.39 mm.');
  });

  it('never calls the centre margin a footprint clearance', () => {
    // The footprint clearance is a DIFFERENT quantity (run_single_cube_cycle.py:1135), reported on the PLACED state
    // record and not on this predicate. Printing it here would mean subtracting a remembered on-pad bar.
    const note = acceptanceNote(outcome(), [predicate('clearance_target_met')]);
    expect(note).not.toContain('footprint');
    expect(note).not.toContain('17.6');
    expect(note).not.toContain('17.96');
    expect(note).not.toContain('20 mm');
  });

  it('says the record is missing rather than printing a number it does not have', () => {
    // A tab that joined mid-run folded no predicate.evaluated for the clearance check.
    const note = acceptanceNote(outcome(), []);
    expect(note).toContain('not in this fold');
    expect(note).not.toContain('missed by');
  });

  it('never claims a shortfall when the measurement beat the target', () => {
    // A false predicate whose numbers do not show a shortfall: give the two numbers, invent no arithmetic.
    const note = acceptanceNote(outcome(), [predicate('clearance_target_met', { measured: 40.0, threshold: 37.96 })]);
    expect(note).toContain('40 mm');
    expect(note).not.toContain('missed by');
  });

  it('names every check that missed when more than the clearance did', () => {
    const note = acceptanceNote(
      outcome({ predicates: { object_secured: true, placed: true, clearance_target_met: false, base_returned: null } }),
      [predicate('clearance_target_met')],
    );
    expect(note).toContain('clearance target met');
    expect(note).toContain('base returned');
  });

  it('closes on what is still outstanding, so the run is not read as accepted', () => {
    const note = acceptanceNote(outcome(), [predicate('clearance_target_met')]);
    expect(note).toContain('not confirmed');
  });
});

describe('which predicate records describe the result on the screen', () => {
  const retained = [predicate('clearance_target_met')];

  it('prefers what this tab folded live, by the shorter path to the same records', () => {
    const live = [predicate('clearance_target_met', { measured: 1 })];
    expect(resultPredicates(live, 'run-c', { run_id: 'run-c', predicates: retained, state: 'retained' })).toBe(live);
  });

  it('reads the run back when this tab folded nothing, so a reloaded tab shows the same figures', () => {
    const note = acceptanceNote(outcome(), resultPredicates([], 'run-c', { run_id: 'run-c', predicates: retained, state: 'retained' }));
    expect(note).toContain('Cube-centre edge margin: 35.57 mm; required: 37.96 mm.');
    expect(note).not.toContain('not in this fold');
  });

  it('NEVER draws one run’s measurement beside another run’s outcome', () => {
    const chosen = resultPredicates([], 'run-c', { run_id: 'a-different-run', predicates: retained, state: 'retained' });
    expect(chosen).toEqual([]);
    expect(acceptanceNote(outcome(), chosen)).toContain('not in this fold');
  });

  it('keeps a run that genuinely retained nothing unavailable', () => {
    const chosen = resultPredicates([], 'run-c', { run_id: 'run-c', predicates: [], state: 'unavailable' });
    expect(chosen).toEqual([]);
    expect(acceptanceNote(outcome(), chosen)).toContain('not in this fold');
  });

  it('falls back to the live fold when nothing has been read back yet', () => {
    expect(resultPredicates([], 'run-c', null)).toEqual([]);
  });
});
