// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// ONLY A RETAINED PICTURE IS A DECISION FRAME (verdict 2026-09-27 item 10). On the real 8771 record the refused CLEARANCE
// lift consumed base-rx-...-000149, an `observation.captured` with media scalar_set that the runtime did not retain, and
// Evidence pinned it and drew an <img> for it under "the stored frame this decision was taken from". A base-velocity
// reading has no picture. The pin skips a capture known not to be a retained picture, and a pinned id whose capture
// turns out to be one (its record arrived after the decision) is said as a reading with no picture, never drawn.
//
// A .ts file building elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import { buildStoppedHoldingRun } from '../api/syntheticScenarios';
import type { ContractEvent } from '../contracts/envelope';
import { applyEvent, foldEvents, initialState } from '../model/reducer';
import type { ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { element } from './demoScenarioPlay';
import { Drawer } from './Drawers';

const BASE = 'base-rx-f9da6e-000149';

/** A store showing `view` on a source that serves a frame address for every id, as the live API does. */
function storeShowing(view: ViewState): ConsoleStore {
  const base = new SyntheticSource();
  const source = new Proxy(base, {
    get(target, key) {
      if (key === 'frameUrl') return (id: string) => `/api/observations/${id}/frame?what=raw&max_width=800`;
      const value: unknown = Reflect.get(target, key, target);
      return typeof value === 'function' ? (value as (...args: unknown[]) => unknown).bind(target) : value;
    },
  });
  const store = new ConsoleStore(source, { storage: null });
  const held = store.getSnapshot();
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, view };
  return store;
}

function evidence(view: ViewState): string {
  const store = storeShowing(view);
  return renderToStaticMarkup(
    createElement(StoreContext, { value: store }, createElement(Drawer, { id: 'evidence', snapshot: store.getSnapshot(), store, mode: 'live', onSelect: () => undefined, onClose: () => undefined })),
  );
}

function text(markup: string): string {
  return markup.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/\s+/g, ' ');
}

function scalarCapture(template: ContractEvent, seq: number, id: string, media: 'scalar_set' | 'frame', retained: boolean): ContractEvent {
  return {
    ...template,
    seq,
    kind: 'observation.captured',
    observation_id: id,
    action_id: null,
    payload: { source_role: media === 'frame' ? 'wrist' : 'base', media, width_px: media === 'frame' ? 640 : null, height_px: media === 'frame' ? 480 : null, content_digest: null, source_stamp: null, retained },
  } as unknown as ContractEvent;
}

/**
 * Run m6ed2dd9's shape with the real record's base reading: a capture of `id` just before the CLEARANCE lift's base_zero
 * predicate, which consumes it. Every later record's seq moves up by one, so the stream stays contiguous.
 */
function withConsumedCapture(id: string, media: 'scalar_set' | 'frame', retained: boolean): ContractEvent[] {
  const events = buildStoppedHoldingRun(2, 100, 1_000_000_000).steps.map((step) => step.event);
  const at = events.findIndex((event) => event.kind === 'predicate.evaluated' && (event.payload as { predicate: string }).predicate === 'base_zero');
  expect(at).toBeGreaterThan(0);
  const predicate = events[at] as ContractEvent;
  const out: ContractEvent[] = events.slice(0, at);
  out.push(scalarCapture(predicate, predicate.seq, id, media, retained));
  for (const event of events.slice(at)) {
    if (event === predicate) {
      const payload = event.payload as unknown as Record<string, unknown>;
      out.push({ ...event, seq: event.seq + 1, payload: { ...payload, evidence: [{ type: 'scalar', observation_ids: [id], scalar_name: 'base_velocity' }] } } as unknown as ContractEvent);
    } else {
      out.push({ ...event, seq: event.seq + 1 } as ContractEvent);
    }
  }
  return out;
}

describe('the decision frame is a retained picture, never a reading with no picture (verdict 2026-09-27 item 10)', () => {
  const plain = foldEvents(initialState(), buildStoppedHoldingRun(2, 100, 1_000_000_000).steps.map((step) => step.event));
  const straddle = plain.decision_frame?.primary_observation_id ?? '';

  it('does not pin a scalar_set reading a decision consumed; the newest picture stays pinned', () => {
    expect(straddle).not.toBe('');
    const view = foldEvents(initialState(), withConsumedCapture(BASE, 'scalar_set', false));
    expect(view.observations[BASE]?.media).toBe('scalar_set');
    expect(view.decision_frame?.primary_observation_id).toBe(straddle);
    expect(view.decision_frame?.observation_ids).not.toContain(BASE);
  });

  it('does not pin a frame the runtime did not retain', () => {
    const view = foldEvents(initialState(), withConsumedCapture('wrist-not-kept', 'frame', false));
    expect(view.decision_frame?.primary_observation_id).toBe(straddle);
  });

  it('pins a retained frame a decision consumed, as before', () => {
    const view = foldEvents(initialState(), withConsumedCapture('wrist-kept', 'frame', true));
    expect(view.decision_frame?.primary_observation_id).toBe('wrist-kept');
  });

  it('draws no <img> for the scalar reading in Evidence, and no sentence about a stored frame of it', () => {
    const markup = evidence({ ...foldEvents(initialState(), withConsumedCapture(BASE, 'scalar_set', false)), data_mode: 'live' });
    const frame = element(markup, 'data-evidence-frame=') ?? '';
    expect(frame.length).toBeGreaterThan(0);
    expect(frame).not.toContain(`/api/observations/${BASE}/`);
    expect(text(frame)).not.toContain(`observation ${BASE}`);
  });

  it('says a pinned id whose capture arrived later as a scalar reading is a reading with no picture, and draws nothing for it', () => {
    // The decision consumed the id before its capture record arrived (a tab that missed it), then the record says what it is.
    const template = buildStoppedHoldingRun(2, 100, 1_000_000_000).steps[0]?.event as ContractEvent;
    const decided = applyEvent(initialState(), {
      ...template,
      seq: 10,
      kind: 'predicate.evaluated',
      payload: { predicate: 'base_zero', value: false, threshold: 3, measured: 0, unit: 'reads', evidence: [{ type: 'scalar', observation_ids: [BASE], scalar_name: 'base_velocity' }], consumed_source_stamp: null },
    } as unknown as ContractEvent);
    expect(decided.decision_frame?.primary_observation_id).toBe(BASE);
    const bound = applyEvent(decided, scalarCapture(template, 11, BASE, 'scalar_set', false));
    expect(bound.decision_frame?.capture?.media).toBe('scalar_set');
    const markup = evidence({ ...bound, data_mode: 'live' });
    const frame = element(markup, 'data-evidence-frame=') ?? '';
    expect(frame).not.toContain('<img');
    expect(frame).not.toContain('<svg');
    expect(text(frame)).toContain(`This decision read ${BASE}, a base reading of scalar values with no picture`);
    expect(text(frame)).not.toContain('Recorded frame, not live.');
    expect(text(frame)).not.toContain('stored frame');
    expect(text(frame)).not.toContain('The controller stored this frame');
  });
});
