// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// ONE ACTIVATION'S EVIDENCE, DRAWN THE SAME WAY WHEREVER IT IS DRAWN: the record's facts on one line (state, phase,
// result, the resident's handle for the transaction, the epoch its answer printed, a stop that landed while it was in
// flight) and the capability slot as the runtime read it just before the submit and just after the answer.
//
// It is drawn in two places, and it used to be drawn in one. The Cake panel shows the NEWEST activation, which is the one
// an operator acts on. Evidence shows EVERY activation the mission recorded, because the operator's ruling is that an
// activation's transaction and slot read-back are RETAINED: after a second activation, the first one's read-back was on
// no screen at all, and Evidence, which History calls the full records, listed its identities without the slot or the
// transaction. One component for both places is what keeps them from saying different things about one record.
//
// It DECIDES NOTHING. The verdict is `activationLine`'s and the words are `activationFactsLine`'s and `slotReadingRow`'s
// (`design/cake.ts`). A read that failed is said to have failed, never drawn as an empty slot, and an absent value is
// "not reported", never a zero.

import type { CSSProperties, ReactNode } from 'react';
import type { MissionActivationRecord } from '../model/missionRecord';
import { activationFactsLine, slotReadingRow } from './cake';
import { INK } from './theme';

const CELL: CSSProperties = { padding: '2px 6px 2px 0', verticalAlign: 'top', overflowWrap: 'anywhere' };

/**
 * The facts line and the before-and-after table of one activation.
 *
 * `full` adds the rows a reader checking the record needs and an operator acting on it does not: WHICH resident
 * answered each read (its session) and under WHICH Plan. Evidence passes it; the Cake panel does not.
 */
export function ActivationEvidence(props: { activation: MissionActivationRecord; full?: boolean }): ReactNode {
  const activation = props.activation;
  const before = slotReadingRow(activation.slot_before);
  const after = slotReadingRow(activation.slot_after);
  const rows: [string, string, string][] = [
    ['read', before.read, after.read],
    ['state', before.state, after.state],
    ['capsule', before.capsule, after.capsule],
    ['generation', before.generation, after.generation],
    ['instance', before.instance, after.instance],
    ['epoch', before.epoch, after.epoch],
  ];
  if (props.full === true) {
    rows.push(['session', before.session, after.session], ['Plan', before.plan, after.plan]);
  }
  return (
    <div data-activation-evidence={activation.operation_uuid} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{ fontFamily: 'var(--font-mono), monospace', fontSize: 11, color: 'var(--color-muted-foreground)', lineHeight: 1.5, overflowWrap: 'anywhere' }}>
        {activationFactsLine(activation)}
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed', fontSize: 11, marginTop: 2 }}>
        <thead>
          <tr style={{ color: 'var(--color-muted-foreground)', textAlign: 'left' }}>
            <th style={{ ...CELL, width: 70, fontWeight: 500 }}>slot</th>
            <th style={{ ...CELL, fontWeight: 500 }}>before</th>
            <th style={{ ...CELL, fontWeight: 500 }}>after</th>
          </tr>
        </thead>
        <tbody style={{ fontFamily: 'var(--font-mono), monospace', color: INK.soft }}>
          {rows.map(([label, was, now]) => (
            <tr key={label} style={{ borderTop: '1px solid var(--color-border)' }}>
              <td style={{ ...CELL, color: 'var(--color-muted-foreground)', fontFamily: 'var(--font-sans), sans-serif' }}>{label}</td>
              <td style={CELL}>{was}</td>
              {/* A value that moved is brighter, and only when both reads answered: a failed read is not a change. */}
              <td style={{ ...CELL, color: was !== now && before.answered && after.answered ? INK.bright : undefined }}>{now}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
