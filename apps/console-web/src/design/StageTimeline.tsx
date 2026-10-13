// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE STAGES, compact (docs/autonomous-demo/console-redesign-2026-09-27.md, sections 2.1, 3.5 and 7.2).
//
// Find, Pick, Carry, Place, Return for the target the mission is on, newest run: each row its label, its status word
// ("Stopped here by the controller" where the run ended) and the UTC time of the run's first state transition into it.
// The numbers, frames and rules a stage decided on are Evidence's, in Diagnostics; no Cake text is drawn on a stage.
// Every value is `model/stageTimeline.ts`'s.

import type { ReactNode } from 'react';
import type { PhaseStatus } from '../model/phase';
import type { StageRow, StageTimeline as StageTimelineModel } from '../model/stageTimeline';
import { utcStamp } from '../model/utcTime';
import { C, INK } from './theme';

function tone(status: PhaseStatus): string {
  switch (status) {
    case 'verified':
      return C.ok;
    case 'failed':
    case 'stopped_by_controller':
    case 'refused':
      return C.bad;
    case 'stopped_on_request':
    case 'no_outcome':
      return C.warn;
    // Entered and left with no verifying predicate: what the record says, and nothing to warn about.
    case 'entered':
      return C.mute;
    case 'active':
      return C.accent;
    case 'not_observed':
      return C.wait;
    default:
      return INK.faint;
  }
}

function Stage(props: { row: StageRow; referenceDay: string | null }): ReactNode {
  const row = props.row;
  const colour = tone(row.status);
  const at = utcStamp(row.entered_at, props.referenceDay);
  return (
    <li
      data-stage={row.step}
      data-stage-status={row.status}
      style={{ listStyle: 'none', display: 'grid', gridTemplateColumns: '10px 64px minmax(0, 1fr) auto', alignItems: 'baseline', gap: 8, padding: '5px 0', borderBottom: '1px solid var(--color-border)' }}
    >
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: colour, alignSelf: 'center' }} />
      <span style={{ fontSize: 13, fontWeight: 600, color: INK.bright }}>{row.label}</span>
      <span data-stage-verdict="" style={{ fontSize: 13, color: colour, minWidth: 0, overflowWrap: 'anywhere' }}>
        {row.words}
      </span>
      {at === null ? <span /> : (
        // LABELLED AS THE ENTRY TIME (verifier r2 defect 9): beside "Stopped here by the controller" a bare time read as
        // the stop's, which is the card's own end time.
        <span data-t={at.iso} title={`the time the run first entered this stage: ${at.title}`} style={{ fontSize: 12, color: INK.quiet, fontVariantNumeric: 'tabular-nums' }}>
          entered {at.text}
        </span>
      )}
    </li>
  );
}

export function StageTimeline(props: { timeline: StageTimelineModel; referenceDay: string | null }): ReactNode {
  return (
    <section aria-label="Stages" data-region="stages" data-stage-timeline="" title={props.timeline.basis} style={{ minWidth: 0 }}>
      <div style={{ fontSize: 13, color: INK.quiet, marginBottom: 6 }}>Stages</div>
      <ol style={{ margin: 0, padding: 0 }}>
        {props.timeline.rows.map((row) => (
          <Stage key={row.step} row={row} referenceDay={props.referenceDay} />
        ))}
      </ol>
    </section>
  );
}
