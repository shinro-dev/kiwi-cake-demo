// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The sample below is the real shape of what the controller prints, taken from the retained stdout of run
// console-20260922T194137Z-gellh5f4, which is the run this whole log console exists because of.

import { describe, expect, it } from 'vitest';
import { countByKind, exportText, filterLines, logLines, NO_FILTER } from './logLines';
import type { LogLineKind } from './logLines';

const STDOUT = [
  'LIVE pose {\'shoulder_pan\': -9.582}; tool -16.3 mm out / +282.1 mm up vs the proven grasp, pitch 26.0',
  '[STATE] SEARCH {"pose": {"shoulder_pan": -9.58}}',
  'observation obs-20260922T194140Z-wrist-0001 image: frames/obs-20260922T194140Z-wrist-0001.png',
  'WRIST:',
  '  cube detected: YES  confidence 0.85',
  '[STATE] PREGRASP {"coarse": {"pan_deg": -18.07}}',
  'PLAN PREGRASP (non-contact): start {...} -> target {...}',
  '[ARM_MOVE] planner 1.00 s x slow-factor 2.0 = 2.00 s commanded; wall-clock 2.02 s',
  '[SCAN_OBSERVATION] {"observation_id": "obs-20260922T194203Z-wrist-0001"}',
  'RESULT PREGRASP (non-contact): abort None',
  '[STATE] ALIGN {"attempt": 0, "aligned": true}',
  'CYCLE FAILED: ALIGN: the cube is not detected in the pregrasp view.',
].join('\n') + '\n';

describe('the controller output as lines', () => {
  it('numbers every line from one and keeps them in order', () => {
    const lines = logLines(STDOUT);
    expect(lines).toHaveLength(12);
    expect(lines[0]?.number).toBe(1);
    expect(lines[11]?.number).toBe(12);
    expect(lines[11]?.text).toContain('CYCLE FAILED');
  });

  it('does not turn the trailing newline into an empty final line', () => {
    expect(logLines('one\n')).toHaveLength(1);
    expect(logLines('one\ntwo\n').map((line) => line.text)).toEqual(['one', 'two']);
    expect(logLines('')).toHaveLength(0);
  });

  it('marks the lines that carry a recognisable prefix', () => {
    const byText = (needle: string): LogLineKind => logLines(STDOUT).find((line) => line.text.includes(needle))!.kind;
    expect(byText('[STATE] SEARCH')).toBe('state');
    expect(byText('[ARM_MOVE]')).toBe('move');
    expect(byText('[SCAN_OBSERVATION]')).toBe('observation');
    expect(byText('observation obs-')).toBe('observation');
    expect(byText('CYCLE FAILED')).toBe('failure');
    expect(byText('PLAN PREGRASP')).toBe('plan');
    expect(byText('RESULT PREGRASP')).toBe('result');
  });

  it('shows what it does not recognise in full, unchanged, rather than hiding it', () => {
    const lines = logLines(STDOUT);
    const plain = lines.filter((line) => line.kind === 'plain');
    expect(plain.map((line) => line.text)).toContain('WRIST:');
    expect(plain.map((line) => line.text)).toContain('  cube detected: YES  confidence 0.85');
    // Leading whitespace is part of the line: the controller indents its perception block and that is how it reads.
    expect(lines.find((line) => line.text.includes('cube detected'))!.text.startsWith('  ')).toBe(true);
  });

  it('counts each kind so a filter can say what it would show', () => {
    const counts = countByKind(logLines(STDOUT));
    expect(counts.state).toBe(3);
    expect(counts.failure).toBe(1);
    expect(counts.state + counts.move + counts.observation + counts.failure + counts.plan + counts.result + counts.plain).toBe(12);
  });
});

describe('searching the output', () => {
  const lines = logLines(STDOUT);

  it('matches plain text without regard to case', () => {
    expect(filterLines(lines, { ...NO_FILTER, query: 'align' })).toHaveLength(2);
    expect(filterLines(lines, { ...NO_FILTER, query: 'ALIGN' })).toHaveLength(2);
  });

  it('treats a regular expression as the text somebody typed, not as a pattern', () => {
    // An operator looking at a failed run must not be able to hang the tab, and `.*` is text here.
    expect(filterLines(lines, { ...NO_FILTER, query: '.*' })).toHaveLength(0);
    expect(filterLines(lines, { ...NO_FILTER, query: '(' })).toHaveLength(2);
  });

  it('shows everything when nothing is typed', () => {
    expect(filterLines(lines, NO_FILTER)).toHaveLength(lines.length);
  });

  it('narrows by kind, and by kind and text together', () => {
    expect(filterLines(lines, { query: '', kinds: new Set<LogLineKind>(['state']) })).toHaveLength(3);
    expect(filterLines(lines, { query: 'pregrasp', kinds: new Set<LogLineKind>(['state']) })).toHaveLength(1);
  });
});

describe('what the copy and export buttons hand over', () => {
  const lines = logLines(STDOUT);
  const base = { runId: 'console-20260922T194137Z-gellh5f4', stream: 'stdout', totalLines: lines.length, truncated: false, filter: NO_FILTER };

  it('names the run and the stream and keeps the line numbers', () => {
    const text = exportText({ ...base, lines });
    expect(text).toContain('# stdout of run console-20260922T194137Z-gellh5f4');
    expect(text).toContain('all 12 lines');
    expect(text).toContain('   12  CYCLE FAILED');
  });

  it('says when it is only part of the log, and why', () => {
    const shown = filterLines(lines, { ...NO_FILTER, query: 'ALIGN' });
    const text = exportText({ ...base, lines: shown, filter: { ...NO_FILTER, query: 'ALIGN' } });
    expect(text).toContain('2 of 12 lines');
    expect(text).toContain('matching "ALIGN"');
    // The numbers are the ones in the whole log, so a filtered paste can still be located in the run.
    expect(text).toContain('   11  ');
  });

  it('says when the runtime served only the end of the stream', () => {
    expect(exportText({ ...base, lines, truncated: true })).toContain('served the END of this stream');
    expect(exportText({ ...base, lines, truncated: false })).toContain('the whole stream as the runtime served it');
  });
});
