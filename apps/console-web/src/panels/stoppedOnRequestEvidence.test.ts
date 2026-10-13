// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A RUN AN OPERATOR STOPPED IS EXPLAINED BY ITS STOP RECORDS, NEVER AS "A GAP IN THE RECORD" (redesign r4 round 7 item 2;
// verify-r6 honesty lens M2).
//
// After a Stop mid-run the record holds stop.requested (origin operator_console), stop.acknowledged, stop.outcome and a
// run.finished `stopped` with no reason code and evidence_complete true. Diagnostics > Evidence drew a red alert badge
// and said the run "ended badly and the record carries no machine-readable reason. That is a gap in the record", beside
// "The record carried no sentence for this failure". The records are built by hand with `model/stageFixtures`; every id
// and time is invented.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { explainFailure } from '../model/failure';
import { foldEvents, initialState } from '../model/reducer';
import { enter, finished, record, started } from '../model/stageFixtures';
import type { ContractEvent } from '../contracts/envelope';
import { FailurePanel } from './FailurePanel';

const RUN = 'synthetic-run-stopped-0001';

function stoppedRun(withStopRecords: boolean): ContractEvent[] {
  // Built in record order: the fixtures number records as they are made.
  const head = [started(RUN), enter(RUN, 'SEARCH')];
  const stops: ContractEvent[] = withStopRecords
    ? [
        record(RUN, 'stop.requested', { origin: 'operator_console', command_id: 'synthetic-stop-0001' }, { t_wall_utc: '2026-09-28T09:00:12.000Z' }),
        record(RUN, 'stop.acknowledged', { acknowledged_by: 'synthetic-controller', command_id: 'synthetic-stop-0001', latency_ns: 40_000_000 }, { t_wall_utc: '2026-09-28T09:00:12.040Z' }),
        record(
          RUN,
          'stop.outcome',
          { outcome: 'ceased_unverified', motion_ceased: null, evidence: 'the controller exited on the signal; motion was not measured' },
          { t_wall_utc: '2026-09-28T09:00:13.000Z', reason_code: 'stop_acknowledged' },
        ),
      ]
    : [];
  return [...head, ...stops, finished(RUN, 'stopped', null)];
}

function panelText(events: ContractEvent[]): string {
  const view = foldEvents(initialState(), events);
  return renderToStaticMarkup(createElement(FailurePanel, { view })).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
}

describe('Evidence for a run stopped on request', () => {
  it('explains the ending from the stop records, in a neutral tone, and never calls it a gap or a failure', () => {
    const events = stoppedRun(true);
    const view = foldEvents(initialState(), events);
    const explanation = explainFailure(view);
    expect(explanation?.unexplained).toBe(false);
    const markup = renderToStaticMarkup(createElement(FailurePanel, { view }));
    const text = markup.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    expect(text).not.toContain('gap in the record');
    expect(text).not.toContain('ended badly');
    expect(text).not.toContain('failure');
    expect(text).toContain('operator_console');
    expect(text).toContain('09:00:12Z');
    expect(text).toContain('synthetic-controller');
    expect(text).toContain('ceased_unverified');
    expect(text).toContain('stop_acknowledged');
    expect(markup).not.toContain('badge--bad');
  });

  it('keeps the gap note for a stopped ending that no record explains', () => {
    const text = panelText(stoppedRun(false));
    expect(text).toContain('gap in the record');
  });
});
