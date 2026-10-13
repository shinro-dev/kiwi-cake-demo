// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CONTROLLER'S OUTPUT, AS LINES SOMEBODY CAN LOOK THROUGH.
//
// The runtime serves the controller's stdout and stderr as one bounded string per run. This turns it into numbered
// lines, marks the ones that carry a recognisable structure, and answers a search. It holds no state and fetches
// nothing: what it is given is the text of ONE named run, and every line it returns came out of that text.
//
// WHY THE MARKS ARE NOT A PARSE. The controller prints `[STATE] NAME {json}`, `[ARM_MOVE] ...`, `CYCLE FAILED: ...`
// and a great deal of free prose. Recognising a prefix lets a reader jump to the state changes; it does NOT turn the
// line into a record, and nothing downstream reads a value out of one. The structured events are a separate, real
// record and are shown beside this, not derived from it. A line this module does not recognise is still shown in
// full, in order, unchanged: a log viewer that hides what it cannot classify is worse than no log viewer.
//
// THE SEARCH IS PLAIN TEXT, case-insensitive, and never a regular expression: an operator looking at a failed run
// should not be able to hang the tab with a pattern, and the one thing they want is to type `ALIGN` and see it.

/** What a line appears to be, from its prefix alone. `plain` is everything else and is never hidden. */
export type LogLineKind = 'state' | 'move' | 'observation' | 'failure' | 'plan' | 'result' | 'plain';

export interface LogLine {
  /** 1-based, counted over the text as it was served. A truncated tail still numbers from 1 and says so elsewhere. */
  number: number;
  text: string;
  kind: LogLineKind;
}

const PREFIXES: ReadonlyArray<[LogLineKind, RegExp]> = [
  ['state', /^\[STATE\]/],
  ['move', /^\[(ARM_MOVE|SCAN_SEGMENT|BASE_[A-Z_]+)\]/],
  ['observation', /^(\[SCAN_OBSERVATION\]|observation\s)/],
  ['failure', /^(CYCLE FAILED|STOPPED|REFUSED|Traceback|\s*File ")/],
  ['plan', /^PLAN\s/],
  ['result', /^(RESULT|LIVE pose|BASE \(measured)/],
];

function kindOf(text: string): LogLineKind {
  for (const [kind, pattern] of PREFIXES) if (pattern.test(text)) return kind;
  return 'plain';
}

/**
 * The served text as numbered lines.
 *
 * A trailing newline does not make a final empty line: every log this reads ends with one, and an empty row at the
 * bottom of every run reads as output that was cut off.
 */
export function logLines(text: string): LogLine[] {
  if (text.length === 0) return [];
  const raw = text.split('\n');
  if (raw.length > 0 && raw[raw.length - 1] === '') raw.pop();
  return raw.map((line, index) => ({ number: index + 1, text: line, kind: kindOf(line) }));
}

export interface LogFilter {
  /** Plain text, case-insensitive. Empty matches everything. */
  query: string;
  /** When set, only these kinds are shown. Empty means every kind. */
  kinds: ReadonlySet<LogLineKind>;
}

export const NO_FILTER: LogFilter = { query: '', kinds: new Set() };

export function matches(line: LogLine, filter: LogFilter): boolean {
  if (filter.kinds.size > 0 && !filter.kinds.has(line.kind)) return false;
  if (filter.query.length === 0) return true;
  return line.text.toLowerCase().includes(filter.query.toLowerCase());
}

export function filterLines(lines: readonly LogLine[], filter: LogFilter): LogLine[] {
  return lines.filter((line) => matches(line, filter));
}

/** How many of each kind the whole log holds, so the filter buttons can say what they would show. */
export function countByKind(lines: readonly LogLine[]): Record<LogLineKind, number> {
  const counts: Record<LogLineKind, number> = { state: 0, move: 0, observation: 0, failure: 0, plan: 0, result: 0, plain: 0 };
  for (const line of lines) counts[line.kind] += 1;
  return counts;
}

/**
 * What the copy and export buttons hand over.
 *
 * The line NUMBERS are kept, and the header names the run, the stream and whether this is the whole of it. A log
 * pasted into a report without those four facts cannot be checked against the run it came from, and a truncated one
 * pasted without saying so is a misquote.
 */
export function exportText(input: {
  runId: string;
  stream: string;
  lines: readonly LogLine[];
  totalLines: number;
  truncated: boolean;
  filter: LogFilter;
}): string {
  const shown =
    input.lines.length === input.totalLines
      ? `all ${String(input.totalLines)} lines`
      : `${String(input.lines.length)} of ${String(input.totalLines)} lines`;
  const query = input.filter.query.length > 0 ? `, matching ${JSON.stringify(input.filter.query)}` : '';
  const kinds = input.filter.kinds.size > 0 ? `, kinds ${[...input.filter.kinds].sort().join('+')}` : '';
  const head = [
    `# ${input.stream} of run ${input.runId}`,
    `# ${shown}${query}${kinds}`,
    input.truncated ? '# the runtime served the END of this stream: earlier output is not included' : '# the whole stream as the runtime served it',
  ];
  return [...head, ...input.lines.map((line) => `${String(line.number).padStart(5, ' ')}  ${line.text}`)].join('\n') + '\n';
}
