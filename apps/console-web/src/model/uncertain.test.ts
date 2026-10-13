// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The rules that keep an uncertain command from becoming a second motion. Each one is one-directional: when in doubt
// this console blocks more and sends less, because the cost of a wrong refusal is one click and the cost of a wrong
// send is a robot moving while nobody can say what it is holding.

import { describe, expect, it } from 'vitest';
// The panel's own source, as text. `?raw` is vite's, and it is used here rather than node's fs because this package
// declares only `vite/client` types: the structural checks below must not drag a node type dependency into a web app.
import actionsSource from '../panels/Actions.tsx?raw';
import { applySnapshot, initialState } from './reducer';
import type { Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import {
  BLOCK_REASON_CODE,
  blockedByUncertainty,
  canResend,
  classificationSentence,
  evidenceLines,
  isDescribable,
  MOTION_CONTROLS,
  parseUnresolved,
} from './uncertain';

const DETAIL = {
  command_id: 'abcd1234ef0123456789',
  run_id: 'sc-20260101T000000Z-abcd1234',
  command_type: 'start_cycle',
  reason_code: 'runtime_restarted_before_child_recorded',
  classification: 'ran_and_was_interrupted',
  block_reason_code: 'owner_unresolved',
  question: 'confirm that none is running and say how it ended, after looking at the robot',
  findings: ['no_controller_running', 'controller_finished', 'controller_stopped_by_operator'],
  evidence: {
    process: { alive: false, readable: true, found_by: null },
    run_files: { readable: true, terminal_record: false, event_records: 41, output_present: true, outcome: null },
    observations: { checked: true, readable: true, files_after_accept: 3 },
    journal: { status: 'uncertain', reason_code: 'runtime_restarted_before_child_recorded', child_identity: true },
    unreadable: [],
  },
  attestations: [],
  blocked_commands: ['start_cycle', 'observe'],
};

function snapshotWith(unresolved: unknown): Snapshot {
  return { ...(runningSnapshot as unknown as Snapshot), unresolved_command: unresolved } as unknown as Snapshot;
}

describe('an uncertain command blocks every motion control', () => {
  it('blocks all of them with owner_unresolved, not only the ones the server happened to name', () => {
    const unresolved = parseUnresolved(DETAIL);
    expect(unresolved?.blocked_commands).toEqual(['start_cycle', 'observe']);
    for (const control of MOTION_CONTROLS) {
      expect(blockedByUncertainty(unresolved, control), control).toBe(BLOCK_REASON_CODE);
    }
    // The server named two. This console blocks seven, including the five it did not name.
    expect(MOTION_CONTROLS.length).toBeGreaterThan(unresolved?.blocked_commands.length ?? 0);
  });

  it('blocks them even when the server sent no list of blocked commands at all', () => {
    const unresolved = parseUnresolved({ ...DETAIL, blocked_commands: [] });
    for (const control of MOTION_CONTROLS) {
      expect(blockedByUncertainty(unresolved, control), control).toBe(BLOCK_REASON_CODE);
    }
  });

  it('blocks them even when the block detail is unreadable, because a parse failure is not permission', () => {
    const unresolved = parseUnresolved({ something: 'the runtime says there is a block and this console cannot read it' });
    expect(unresolved).not.toBeNull();
    expect(isDescribable(unresolved)).toBe(false);
    for (const control of MOTION_CONTROLS) {
      expect(blockedByUncertainty(unresolved, control), control).toBe(BLOCK_REASON_CODE);
    }
  });

  it('never blocks the stop request: asking a controller to stop is what is needed most when nobody knows', () => {
    expect(MOTION_CONTROLS).not.toContain('stop');
    expect(blockedByUncertainty(parseUnresolved(DETAIL), 'stop')).toBeNull();
  });

  it('blocks nothing at all when the runtime reports no unresolved command', () => {
    expect(parseUnresolved(undefined)).toBeNull();
    expect(parseUnresolved(null)).toBeNull();
    for (const control of MOTION_CONTROLS) {
      expect(blockedByUncertainty(null, control), control).toBeNull();
    }
  });
});

describe('the ambiguous command can never be sent again', () => {
  it('is the answer for every command there has ever been', () => {
    expect(canResend()).toBe(false);
  });
});

describe('the block travels on the snapshot', () => {
  it('is read off the additive key and lands on the view', () => {
    const state = applySnapshot(initialState(), snapshotWith(DETAIL));
    expect(state.unresolved?.command_id).toBe('abcd1234ef0123456789');
    expect(state.unresolved?.block_reason_code).toBe('owner_unresolved');
    expect(state.unresolved?.findings).toHaveLength(3);
  });

  it('clears only when a snapshot arrives without the key: the runtime decides, not this tab', () => {
    const blocked = applySnapshot(initialState(), snapshotWith(DETAIL));
    expect(blocked.unresolved).not.toBeNull();
    const after = applySnapshot(blocked, runningSnapshot as unknown as Snapshot);
    expect(after.unresolved).toBeNull();
  });

  it('defaults the block reason to owner_unresolved when the runtime did not name one', () => {
    const unresolved = parseUnresolved({ command_id: 'abcd1234ef0123456789' });
    expect(unresolved?.block_reason_code).toBe(BLOCK_REASON_CODE);
  });
});

describe('the actions panel really routes through the block', () => {
  // A pure function that blocks everything is worth nothing if the panel forgets to call it for one control. These
  // read the panel's SOURCE, which is the only way to see that from a test in this environment: the suite runs in node
  // with no DOM, so the component itself cannot be rendered here.
  const source = actionsSource;

  it('names only motion controls this console knows, and exactly the four this screen offers', () => {
    // The panel builds every control from ONE list of specifications, each of which states the capability id the
    // server drives it by. The list is read out of the source here: a control added without an id would not parse as
    // a specification at all, and one added with an id this console does not treat as motion fails this comparison.
    // The manual jog controls were deliberately removed from the screen, so the panel no longer declares all seven.
    // Their ids stay in MOTION_CONTROLS, because that list is what the BLOCK covers and a capability this screen does
    // not offer is still a motion command. What is checked here is that every id the panel does declare is one of
    // them, and that the four it declares are the four this screen is supposed to offer (Scan scene joined with P0b's
    // scan_scene command).
    const declared = [...source.matchAll(/^ {6}id: '([a-z_]+)',$/gm)].map((match) => match[1]);
    expect([...declared].sort()).toEqual(['observe', 'return_arm', 'scan_scene', 'start_cycle']);
    for (const id of declared) expect(MOTION_CONTROLS).toContain(id);
  });

  it('hands the unresolved command to every one of those rows', () => {
    // There is exactly one place a control is drawn, and it asks the blocking question for whichever control it is
    // drawing. That is a stronger statement than one-check-per-row: there is no second path a new control could be
    // added on, so no control can be written that forgets to ask.
    const rendered = [...source.matchAll(/<ActionControl\b/g)];
    expect(rendered).toHaveLength(1);
    expect(source).toContain('offReason(unresolved, spec.id, capability, authority)');
    expect(source).toContain('unresolved={unresolved}');
    // start_cycle carries one refusal of its own (a second start while the first is uncertain) and is still routed
    // through the same call before that refusal is even considered.
    expect(source).toContain("spec.id === 'start_cycle'");
    // And the capability list is read AGAIN at the moment of the click, against the same function. The comparison is
    // made against whitespace-collapsed source: this pins the CALL, and wrapping it across lines must not read as
    // having removed it.
    const flat = source.replace(/\s+/g, ' ');
    expect(flat).toContain('const reason = offReason(fresh.view.unresolved, capabilityId,');
    // That click-time re-check lives at module scope and is EXPORTED, which is what lets the Start control in the
    // imported design's header make the same refusals from the same function. A second copy of this reasoning
    // somewhere else is how one of the two ends up a check short, so the export is the thing being pinned.
    expect(source).toContain('export function submitRefusal(');
  });

  it('has no control anywhere that submits a command by an existing id', () => {
    // `submit` always takes a freshly minted id inside the store. Nothing here may pass one in, which is what a resend
    // would look like.
    expect(source).not.toMatch(/store\.submit\([^)]*command_id/);
    expect(source).not.toMatch(/store\.submit\([^)]*track\./);
  });
});

describe('the machine evidence, read for the screen', () => {
  it('turns every check the runtime reported into one line, and adds none of its own', () => {
    const lines = evidenceLines(DETAIL.evidence);
    expect(lines.map((line) => line.check)).toEqual([
      'process table',
      "the run's own files",
      'the observation store',
      "the runtime's own journal",
    ]);
    expect(lines[0]?.summary).toContain('no process with this command identity is alive');
    expect(lines[1]?.summary).toContain('41 record(s) written');
  });

  it('says outright that a check could not be read, which is the case that decides everything', () => {
    const lines = evidenceLines({ ...DETAIL.evidence, process: { alive: false, readable: false }, unreadable: ['process_table'] });
    expect(lines[0]?.readable).toBe(false);
    expect(lines[0]?.summary).toContain('a live controller cannot be ruled out');
    expect(lines.some((line) => line.check === 'what could not be read')).toBe(true);
  });

  it('leaves out a check the runtime did not report, rather than showing it as empty', () => {
    expect(evidenceLines({ process: { alive: false, readable: true } }).map((line) => line.check)).toEqual(['process table']);
    expect(evidenceLines(null)).toEqual([]);
  });

  it('gives each classification a sentence and shows an unknown one as itself', () => {
    expect(classificationSentence('still_unknown')).toContain('the block stays');
    expect(classificationSentence('a_class_from_a_newer_runtime')).toBe('a_class_from_a_newer_runtime');
    expect(classificationSentence(null)).toBeNull();
  });
});
