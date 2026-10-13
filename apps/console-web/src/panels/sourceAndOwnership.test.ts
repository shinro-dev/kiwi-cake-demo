// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// TWO THINGS THE SCREEN MUST NOT GET WRONG, and they are the two the operator named.
//
// WHICH OF THE THREE THINGS THE PAGE IS SHOWING. The bare address used to serve a scripted run drawn inside the page,
// and only `?source=live` reached a runtime. That is the one arrangement here that can mislead somebody: the page the
// API itself served looked exactly like the console and was a fixture, so every absence on it was a statement about a
// script rather than about the robot in the room. The bare address is the configured runtime now, the simulation is a
// named choice, and a value this console does not recognise is the runtime and never quietly a fixture.
//
// WHAT OWNS THE ROBOT. Under the baseline backend nothing on the robot refuses anything: the whole of the ownership
// is this runtime refusing a second run on the computer it runs on. The tests below hold that sentence to its exact
// shape and hold the words "lease held" off the screen entirely under that backend, in any form, because a row that
// prints the phrase teaches the reader that a lease is the subject and that one would be held if things were working.
//
// It is a .ts file and builds its elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { SnapshotExecution } from '@kiwi/demo-contracts';
import { SyntheticSource } from '../api/synthetic';
import { initialState } from '../model/reducer';
import type { ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import { EngineeringView } from './EngineeringView';
import { ownershipWords, leaseIsTheSubject } from './ownership';
import { CHOICE_LABEL, searchFor, sourceChoiceFrom } from './sourceChoice';
import { StatusStrip } from './StatusStrip';

const NOW = 5_000_000;

function text(markup: string): string {
  return markup
    .replace(/<[^>]*>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ');
}

/** What the runtime actually answers on this machine today: the baseline backend, and no lease anywhere. */
const BASELINE: SnapshotExecution = {
  backend: 'baseline_normal_host',
  ownership_basis: 'runtime_single_run_slot',
  robot_side_enforcement: false,
  detail:
    "the only thing that owns the robot is this runtime's single run slot on this computer: one run at a time, refused here, by this process. Nothing on the robot refuses a command from anywhere else, and any other process on that computer can still open the servo bus",
};

const BOUNDARY_HELD: SnapshotExecution = {
  backend: 'boundary_lease_host',
  ownership_basis: 'host_command_lease',
  robot_side_enforcement: true,
  detail: 'the robot-side boundary granted this runtime a command lease and refuses commands that do not carry it',
};

function snapshotWith(patch: Record<string, unknown>): ReturnType<ConsoleStore['getSnapshot']> {
  const store = new ConsoleStore(new SyntheticSource());
  return { ...store.getSnapshot(), ...patch } as ReturnType<ConsoleStore['getSnapshot']>;
}

function viewWith(extra: Partial<ViewState> = {}): ViewState {
  return { ...initialState(), ...extra };
}

function strip(view: ViewState, patch: Record<string, unknown> = {}): string {
  return renderToStaticMarkup(
    createElement(StatusStrip, {
      snapshot: snapshotWith({ view, ...patch }),
      mode: view.data_mode,
      onStop: () => undefined,
      stopBusy: false,
      nowMs: NOW,
    }),
  );
}

describe('the bare address shows the configured runtime and starts nothing', () => {
  it('reads an address with no source at all as the runtime, never as the script in the page', () => {
    expect(sourceChoiceFrom('')).toBe('runtime');
    expect(sourceChoiceFrom('?')).toBe('runtime');
    expect(sourceChoiceFrom('?tab=engineering')).toBe('runtime');
  });

  it('keeps the simulation as a named choice that has to be asked for', () => {
    expect(sourceChoiceFrom('?source=simulation')).toBe('simulation');
  });

  it('keeps the address that named the runtime before this working', () => {
    expect(sourceChoiceFrom('?source=live')).toBe('runtime');
  });

  it('resolves a source it does not recognise to the runtime, so a typo never becomes a fixture', () => {
    expect(sourceChoiceFrom('?source=demo')).toBe('runtime');
    expect(sourceChoiceFrom('?source=synthetic')).toBe('runtime');
    expect(sourceChoiceFrom('?source=SIMULATION')).toBe('runtime');
    expect(sourceChoiceFrom('?source=')).toBe('runtime');
  });

  it('gives each choice an address that names it, so a screenshot of the address bar says which this is', () => {
    expect(searchFor('runtime')).toBe('?source=live');
    expect(searchFor('simulation')).toBe('?source=simulation');
  });

  it('labels both selections with what they are rather than with what they do to the page', () => {
    expect(CHOICE_LABEL.runtime).toContain('runtime');
    expect(CHOICE_LABEL.simulation).toContain('simulation');
  });

  it('names all three things the screen can be, including the recorded run, in the strip itself', () => {
    const words = text(strip(viewWith()));
    expect(words).toContain(CHOICE_LABEL.runtime);
    expect(words).toContain(CHOICE_LABEL.simulation);
    expect(words).toContain('a recorded run: in finished runs');
  });

  it('still refuses to let the script in the page pass for a runtime, whatever the address asked for', () => {
    // The source kind decides these words, not the address and not the mode the script's own snapshot carries.
    const words = text(strip(viewWith({ data_mode: 'live' })));
    expect(words).toContain("reading this page's own script");
  });
});

describe('what owns the robot, under the backend that takes no lease', () => {
  it('states the ownership as this runtime alone, and the robot side as a finding of no', () => {
    const words = ownershipWords(BASELINE);
    expect(words.backend).toContain('baseline');
    expect(words.basis).toBe('one run at a time, refused here');
    // The short form the strip carries and the full one the engineering view carries say the same thing. The short
    // one is not allowed to soften into a "not required", and the long one keeps the consequence.
    expect(words.robotShort).toBe('nothing on the robot refuses anything');
    expect(words.robotSide).toContain('nothing on the robot is refusing a command from anywhere else');
    expect(words.robotSide).toContain('any other process on that computer can still open the servo bus');
    expect(words.detail).toBe(BASELINE.detail);
  });

  it('never prints the words lease held for this backend, anywhere on the strip', () => {
    const markup = strip(viewWith({ execution: BASELINE }));
    expect(markup.toLowerCase()).not.toContain('lease held');
    expect(text(markup)).not.toContain('lease');
  });

  it('never prints the words lease held for this backend, anywhere in the engineering view', () => {
    const markup = renderToStaticMarkup(
      createElement(EngineeringView, {
        snapshot: snapshotWith({ view: viewWith({ execution: BASELINE }) }),
        store: new ConsoleStore(new SyntheticSource()),
        nowMs: NOW,
      }),
    );
    expect(markup.toLowerCase()).not.toContain('lease held');
  });

  // The ownership group was REMOVED from the strip: it was a standing summary of something the engineering view
  // states in full, and the operating screen is not where it belongs. The words are still held to the same rules,
  // here against the model that produces them and below against the view that prints them.
  it('never reaches for a word that makes the absence of a lease sound like a requirement met or waived', () => {
    const words = ownershipWords(BASELINE);
    expect(words.backend).toContain('baseline: the normal host');
    expect(words.basis).toContain('one run at a time, refused here');
    expect(words.robotShort).toContain('nothing on the robot refuses anything');
    expect(`${words.basis} ${words.robotShort} ${words.detail}`).not.toContain('not required');
    expect(`${words.basis} ${words.robotShort} ${words.detail}`).not.toContain('not applicable');
  });

  it('carries the runtime own sentence into the engineering view, unaltered', () => {
    const words = text(
      renderToStaticMarkup(
        createElement(EngineeringView, {
          snapshot: snapshotWith({ view: viewWith({ execution: BASELINE }) }),
          store: new ConsoleStore(new SyntheticSource()),
          nowMs: NOW,
        }),
      ),
    );
    expect(words).toContain(BASELINE.detail);
    expect(words).toContain('none is asked for and none is taken');
  });

  it('keeps a lease off the strip even when an owner record carries one, because the backend takes none', () => {
    // A stale owner record with a lease on it must not put a lease reading under a backend that never asked for one.
    const view = viewWith({
      execution: BASELINE,
      owner: { kind: 'autonomous', run_id: 'sc-1', lease: { generation: 4, host_session: 'host-a', expires_in_ms: 9000 } },
    });
    expect(leaseIsTheSubject(BASELINE)).toBe(false);
    expect(text(strip(view)).toLowerCase()).not.toContain('lease held');
  });
});

describe('what owns the robot, under the backend whose lease is the ownership', () => {
  it('reports the robot side as enforcing, and only while a lease is actually held', () => {
    expect(ownershipWords(BOUNDARY_HELD).robotSide).toContain('the robot-side boundary is refusing commands');
    expect(leaseIsTheSubject(BOUNDARY_HELD)).toBe(true);
    expect(ownershipWords({ ...BOUNDARY_HELD, robot_side_enforcement: null }).robotSide).toContain('not established');
  });

  it('lets a lease be the subject there, because it is the thing that owns the robot', () => {
    expect(leaseIsTheSubject(BOUNDARY_HELD)).toBe(true);
    expect(ownershipWords(BOUNDARY_HELD).backend).toContain('boundary');
  });
});

describe('a runtime that has stated nothing about either question', () => {
  it('says nothing has answered it, and never reads it off the absent lease beside it', () => {
    const words = ownershipWords(null);
    expect(words.unstated).toBe(true);
    expect(words.robotSide).toContain('not established');
    expect(words.detail).toContain('it is not read off the lease beside it');
  });

  it('draws it as a question nobody answered rather than as a fault', () => {
    expect(ownershipWords(null).tone).toBe('quiet');
    expect(ownershipWords(null).basis).toContain('this runtime has not said what owns the robot');
  });
});
