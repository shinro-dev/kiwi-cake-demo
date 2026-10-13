// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CONTROLLER'S OWN OUTPUT, INSIDE DIAGNOSTICS.
//
// The supervisor has always written stdout and stderr into the run directory and nothing served them, so the one
// record of what the controller actually did needed a shell on the machine running the runtime. That is what sent
// the operator of the 2026-09-22 align failure to a terminal to find out why a run had stopped.
//
// IT IS BOUND TO A NAMED RUN AND NEVER TO "THE CURRENT RUN". The whole reason to read this is a run that has ended,
// and a log drawn under one run id while it holds another run's text is the defect this console already had
// elsewhere. The run id is chosen here, is printed above the log, and is what is fetched.
//
// NO MOTION LIVES IN THIS PANEL. Reading a log and commanding a robot are different things and the brief keeps them
// apart deliberately: every control here is a fetch, a filter or a copy. The recovery actions stay in `Actions`.
//
// WHAT IT DOES NOT DO. It does not poll: a finished run's output does not change, and re-reading it on a timer would
// be a request per second for nothing. It does not parse: the marks are prefixes, so a reader can jump to the state
// changes, and every line this module does not recognise is still shown in full and in order.

import type { ReactNode } from 'react';
import { useState } from 'react';
import { parseUtcMs } from '../model/utcTime';
import { countByKind, exportText, filterLines, logLines } from '../model/logLines';
import type { LogLineKind } from '../model/logLines';
import type { ConsoleStore, StoreSnapshot } from '../store/store';

const KIND_LABEL: Record<LogLineKind, string> = {
  state: 'state',
  move: 'motion',
  observation: 'observation',
  failure: 'failure',
  plan: 'plan',
  result: 'result',
  plain: 'other',
};

/** The tone each kind is drawn in. `failure` is the only one that shouts, because it is the only one that should. */
const KIND_COLOUR: Record<LogLineKind, string> = {
  state: 'var(--color-accent, #00d4ff)',
  move: 'var(--color-muted-foreground)',
  observation: 'var(--color-muted-foreground)',
  failure: '#f87171',
  plan: 'var(--color-muted-foreground)',
  result: '#34d399',
  plain: 'inherit',
};

/**
 * Which runs can be looked at: the finished ones the runtime lists, NEWEST FIRST (spec rule 6.6), by their recorded
 * finish, the runtime's own order reversed where a finish is not recorded. 8771 serves them oldest first.
 */
export function runChoices(snapshot: StoreSnapshot): string[] {
  const history = snapshot.history;
  const entries = history !== null && history.ok ? history.entries.map((entry, index) => ({ id: entry.run_id, ms: parseUtcMs(entry.finished_wall_utc), index })) : [];
  entries.sort((a, b) => (b.ms ?? Number.NEGATIVE_INFINITY) - (a.ms ?? Number.NEGATIVE_INFINITY) || b.index - a.index);
  const rows = entries.map((entry) => entry.id);
  const active = snapshot.view.active_run?.run_id ?? null;
  return active !== null && !rows.includes(active) ? [active, ...rows] : rows;
}

/** The run the picker opens on: the mission's last run when it is listed, else the newest finished run (rule 6.6). */
export function defaultRun(snapshot: StoreSnapshot, choices: readonly string[]): string {
  const runs = snapshot.mission?.targets.flatMap((target) => target.runs) ?? [];
  const last = runs[runs.length - 1];
  if (last !== undefined && choices.includes(last)) return last;
  const active = snapshot.view.active_run?.run_id ?? null;
  return choices.find((id) => id !== active) ?? choices[0] ?? '';
}

export function LogConsole(props: { snapshot: StoreSnapshot; store: ConsoleStore }): ReactNode {
  const { snapshot, store } = props;
  const choices = runChoices(snapshot);
  const output = snapshot.runOutput;
  const [selected, setSelected] = useState<string>(defaultRun(snapshot, choices));
  const [stream, setStream] = useState<'stdout' | 'stderr'>('stdout');
  const [query, setQuery] = useState('');
  const [kinds, setKinds] = useState<ReadonlySet<LogLineKind>>(new Set());
  const [copied, setCopied] = useState<string | null>(null);

  const runId = selected !== '' ? selected : defaultRun(snapshot, choices);
  const all = output === null ? [] : logLines(output.text);
  const filter = { query, kinds };
  const shown = filterLines(all, filter);
  const counts = countByKind(all);
  // The log on the screen belongs to the run the runtime served it for, which is not always the one in the picker:
  // somebody can change the picker without pressing Read. Saying which one this text is stops that being invisible.
  const showing = output?.run_id ?? null;
  const stale = showing !== null && showing !== runId;

  async function read(): Promise<void> {
    if (runId === '') return;
    await store.openRunOutput(runId, stream);
  }

  async function copy(): Promise<void> {
    if (output === null) return;
    const text = exportText({ runId: output.run_id, stream: output.stream, lines: shown, totalLines: all.length, truncated: output.truncated, filter });
    try {
      await navigator.clipboard.writeText(text);
      setCopied(`${String(shown.length)} lines copied`);
    } catch {
      // A browser that refuses the clipboard is not a failure of the log: the text is still on the screen.
      setCopied('the browser refused the clipboard: select the text instead');
    }
  }

  function download(): void {
    if (output === null) return;
    const text = exportText({ runId: output.run_id, stream: output.stream, lines: shown, totalLines: all.length, truncated: output.truncated, filter });
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `${output.run_id}.${output.stream}.txt`;
    link.click();
    URL.revokeObjectURL(url);
  }

  function toggleKind(kind: LogLineKind): void {
    const next = new Set(kinds);
    if (next.has(kind)) next.delete(kind);
    else next.add(kind);
    setKinds(next);
  }

  return (
    <section className="rail__card" aria-labelledby="log-console-title">
      <h3 className="rail__title" id="log-console-title">
        Controller output
      </h3>
      <p className="rail__note">
        What the controller itself printed, for one run, as the runtime serves it. Reading it commands nothing.
      </p>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginTop: 8 }}>
        <label htmlFor="log-run" style={{ fontSize: 12 }}>
          Run
        </label>
        <select id="log-run" value={runId} onChange={(event) => setSelected(event.target.value)} style={{ fontFamily: 'var(--font-mono), monospace', fontSize: 12, maxWidth: 260 }}>
          {choices.length === 0 ? (
            <option value="">
              {/* A RUN LIST THAT WAS NOT READ is not an empty one (verify-r1 must_fix 3). */}
              {snapshot.history === null
                ? 'the run list has not been read yet'
                : !snapshot.history.ok
                  ? `the run list has not been read: ${snapshot.history.reason_code}`
                  : 'no run has been recorded yet'}
            </option>
          ) : null}
          {choices.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>

        <label htmlFor="log-stream" style={{ fontSize: 12 }}>
          Stream
        </label>
        <select id="log-stream" value={stream} onChange={(event) => setStream(event.target.value === 'stderr' ? 'stderr' : 'stdout')} style={{ fontSize: 12 }}>
          <option value="stdout">stdout</option>
          <option value="stderr">stderr</option>
        </select>

        <button type="button" className="button" onClick={() => void read()} disabled={runId === '' || snapshot.runOutputLoading}>
          {snapshot.runOutputLoading ? 'Reading...' : 'Read'}
        </button>
      </div>

      {snapshot.runOutputError !== null ? (
        <p className="rail__absent" role="status">
          This output could not be read: {snapshot.runOutputError}
        </p>
      ) : null}

      {output === null ? (
        <p className="rail__absent">Nothing has been read yet. Choose a run and press Read.</p>
      ) : (
        <>
          <p className="rail__note" style={{ marginTop: 10 }}>
            <strong style={{ fontFamily: 'var(--font-mono), monospace' }}>{output.run_id}</strong>, {output.stream},{' '}
            {all.length} lines, {output.total_bytes} bytes.{' '}
            {output.truncated ? 'The runtime served the END of this stream: earlier output is not included.' : 'This is the whole stream.'}
            {stale ? ` The picker now names ${runId}: press Read to see that run instead.` : ''}
          </p>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginTop: 8 }}>
            <label htmlFor="log-search" style={{ fontSize: 12 }}>
              Search
            </label>
            <input
              id="log-search"
              type="search"
              value={query}
              placeholder="plain text, not a pattern"
              onChange={(event) => setQuery(event.target.value)}
              style={{ fontFamily: 'var(--font-mono), monospace', fontSize: 12, flex: 1, minWidth: 140 }}
            />
            <button type="button" className="button" onClick={() => void copy()}>
              Copy
            </button>
            <button type="button" className="button" onClick={download}>
              Save
            </button>
          </div>

          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }} role="group" aria-label="show only these kinds of line">
            {(Object.keys(KIND_LABEL) as LogLineKind[])
              .filter((kind) => counts[kind] > 0)
              .map((kind) => (
                <button
                  key={kind}
                  type="button"
                  className="button"
                  aria-pressed={kinds.has(kind)}
                  onClick={() => toggleKind(kind)}
                  style={{ fontSize: 11, opacity: kinds.size === 0 || kinds.has(kind) ? 1 : 0.5 }}
                >
                  {KIND_LABEL[kind]} {counts[kind]}
                </button>
              ))}
          </div>

          <p className="rail__note" aria-live="polite">
            {shown.length === all.length ? `All ${String(all.length)} lines.` : `${String(shown.length)} of ${String(all.length)} lines.`}
            {copied === null ? '' : ` ${copied}`}
          </p>

          <div
            style={{
              maxHeight: 360,
              overflow: 'auto',
              border: '1px solid var(--color-border)',
              borderRadius: 8,
              background: 'var(--color-background)',
              fontFamily: 'var(--font-mono), monospace',
              fontSize: 11,
              lineHeight: 1.5,
            }}
            tabIndex={0}
            role="log"
            aria-label={`${output.stream} of run ${output.run_id}`}
          >
            {shown.length === 0 ? (
              <p className="rail__absent" style={{ padding: 10 }}>
                No line of this run matches. The log is unchanged; only what is shown was narrowed.
              </p>
            ) : (
              shown.map((line) => (
                <div key={line.number} style={{ display: 'flex', gap: 10, padding: '0 10px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  <span style={{ color: 'var(--color-muted-foreground)', userSelect: 'none', minWidth: 42, textAlign: 'right', flex: 'none' }}>{line.number}</span>
                  <span style={{ color: KIND_COLOUR[line.kind] }}>{line.text}</span>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </section>
  );
}
