// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The engineering view: the same data, unfolded. Raw records, the durations by span and clock, the telemetry tap and
// the state the controller consumed. It is a second view of one set of facts, not a second set: everything here came
// through the same reducer as the presentation view.
//
// It is read in GROUPS, and the groups are the subjects somebody debugging actually asks about: where the numbers
// come from, what a decision was taken on, who holds the robot, how long things took, what Cake is doing, and what
// the finished runs were. Before this they were seven cards in the order they were written, and finding the card that
// held an answer meant knowing in advance which one it was.
//
// THE DECISION FRAME IS NOT DRAWN HERE. This view sits in the Diagnostics drawer's Connections tab, and the picture a
// decision was taken on is Evidence's: the operator's ruling is that a rehearsal's generated frames appear in Evidence
// only, under a GENERATED label, and this view had been drawing the same frame a second time with nothing but the
// panel's own watermark on it. Evidence draws it for every mode, with the label a rehearsal needs, so nothing is lost by
// drawing it once. The predicates it was decided with are still here.

import type { ReactNode } from 'react';
import { Fragment, useEffect, useState } from 'react';
import '../styles/engineering.css';
import {
  advanceReplayCursor,
  clockDomainsOf,
  commandBlockReason,
  compareTapWithConsumed,
  comparisonWords,
  digest12,
  groupRuns,
  jointNames,
  JOINT_REPRESENTATION_NOTE,
  JOINT_REPRESENTATION_UNSTATED,
  provenanceWords,
  recordedProvenance,
  runGroupWords,
  replayAxis,
  REPLAY_TICK_MS,
  stateReentries,
  timingTable,
} from '../model/engineering';
import { integer, nanosAsMillis, number, seconds, textOr, tristate, UNAVAILABLE } from '../model/format';
import { tapView } from '../model/sources';
import type { ViewState } from '../model/types';
import type { StoreSnapshot } from '../store/store';
import type { ConsoleStore } from '../store/store';
import { useNowMs } from '../store/useConsole';
import { Badge } from '../ui/Badge';
import { IconClock } from '../ui/Icons';
import { Field, Fields, Panel } from '../ui/Panel';
import { CameraClocksPanel } from './LiveViewPanel';
import { leaseIsTheSubject, ownershipWords } from './ownership';
import { PredicatesPanel, runRecordsNotRead } from './PredicatesPanel';
import { SourcesDetailPanels } from './SourcesPanel';
import { CakePanel, ComponentsPanel, EvidenceSummary, FuturePanel } from './SystemPanels';

/** A named band of cards about one subject, with one sentence saying what the subject is. */
function Group(props: { title: string; subtitle: string; extra?: ReactNode; single?: boolean; children: ReactNode }): ReactNode {
  return (
    <section className="group">
      <header className="group__head">
        <h2 className="group__title">{props.title}</h2>
        <p className="group__subtitle">{props.subtitle}</p>
        {props.extra ?? null}
      </header>
      <div className={`group__body ${props.single === true ? 'group__body--single' : ''}`}>{props.children}</div>
    </section>
  );
}

/* ---- timing ---------------------------------------------------------------------------------------------------- */

/**
 * The measured durations, by span AND by clock domain.
 *
 * The badge used to read "0.0 ms measured" before a single span had been recorded, because it reduced an empty list
 * with a seed of zero, and it sat over a table whose own body said that nothing had been recorded. There is no badge
 * at all until a sample exists now, and there is no figure that crosses a clock boundary: a capture measured on the
 * robot's computer and a capture measured on this one are two rows that are never added.
 */
function TimingPanel(props: { view: ViewState }): ReactNode {
  const table = timingTable(props.view);
  if (table === null) {
    return (
      <Panel
        title="timing by span"
        state="waiting"
        stateWords="No span measured yet"
        note="A figure computed over no samples would be an absence of measurement printed as a zero. Nothing is shown until a span has actually been measured."
      >
        <p className="muted">No span has been measured in this run. That is not a duration of zero: nothing has timed anything yet.</p>
      </Panel>
    );
  }
  const domains = clockDomainsOf(table.rows);
  return (
    <Panel
      title="timing by span"
      extra={
        <Badge tone="quiet" icon={<IconClock />}>
          {String(table.rows.length)} row(s) of span and clock, {String(domains.length)} clock domain(s)
        </Badge>
      }
      note={
        <>
          {table.basis === 'runtime' ? (
            <>
              Aggregated by the runtime over every record of run <span className="mono">{textOr(table.run_id)}</span>, which is a longer window
              than this tab saw.
            </>
          ) : (
            <>Folded from the records that reached this tab. The runtime served no aggregate, so this covers only what came past here.</>
          )}{' '}
          A duration is only ever compared with another from the same clock domain, so one span name measured in two
          domains is two rows and their figures are never added. The maximum is the largest sample observed inside the
          window beside it, which is an observation and not a bound on the span.
        </>
      }
      flush
    >
      <table className="table">
        <thead>
          <tr>
            <th>span</th>
            <th>clock domain</th>
            <th>count</th>
            <th>window (seq)</th>
            <th>last</th>
            <th>max observed</th>
            <th>total</th>
          </tr>
        </thead>
        <tbody>
          {table.rows.map((timing) => (
            <tr key={`${timing.span}/${timing.clock_domain}`}>
              <td>{timing.span}</td>
              <td className="muted">{timing.clock_domain}</td>
              <td>{String(timing.count)}</td>
              <td>
                {String(timing.first_seq)} to {String(timing.last_seq)}
              </td>
              <td>{nanosAsMillis(timing.last_ns)}</td>
              <td>{nanosAsMillis(timing.max_ns)}</td>
              <td>{nanosAsMillis(timing.total_ns)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

/* ---- the tap and the sample a decision consumed ---------------------------------------------------------------- */

function velocityWords(velocity: { x_mps: number | null; y_mps: number | null; theta_dps: number | null } | null): string {
  if (velocity === null) return UNAVAILABLE;
  // All three components, each with its unit. The lateral one used to be dropped, which on a base that can strafe is
  // a motion the screen said was not happening.
  return `x ${number(velocity.x_mps, 3, 'm/s')}, y ${number(velocity.y_mps, 3, 'm/s')}, theta ${number(velocity.theta_dps, 2, 'deg/s')}`;
}

/**
 * The latest sample this tab holds, beside the sample the controller actually acted on, and what may be said about
 * the difference between them.
 *
 * What may be said is a COUNT OF SAMPLES inside one source session. The two stamps are monotonic readings taken on
 * the robot's computer and they are not wall clock times; subtracting them, or subtracting either from anything on
 * this machine, produces a duration nobody measured. Across two sessions even the sequence numbers count different
 * things, and then the honest answer is that there is no comparison to make.
 */
function TapAndConsumedPanel(props: { snapshot: StoreSnapshot; nowMs?: number }): ReactNode {
  const now = props.nowMs ?? Date.now();
  const telemetry = props.snapshot.telemetry;
  const tap = tapView(telemetry.tap, telemetry.tap_received_at_ms, now);
  const consumed = props.snapshot.view.measured_state;
  const comparison = compareTapWithConsumed(tap.source_stamp, consumed?.consumed_source_stamp ?? null);
  return (
    <Panel
      title="the tap and the sample a decision consumed"
      state={comparison.kind === 'absent' ? 'waiting' : undefined}
      stateWords={comparison.kind === 'absent' ? 'Nothing to compare yet' : undefined}
      extra={<Badge tone={comparison.kind === 'not_comparable' ? 'warn' : 'quiet'}>{comparison.kind.replace(/_/g, ' ')}</Badge>}
      note={JOINT_REPRESENTATION_NOTE}
    >
      <p className="muted">{comparisonWords(comparison)}</p>
      <Fields wide>
        <Field label="tap source session">{textOr(tap.source_stamp?.session ?? null)}</Field>
        <Field label="tap sample sequence">{tap.source_stamp === null ? UNAVAILABLE : String(tap.source_stamp.seq)}</Field>
        <Field label="tap clock domain">{textOr(tap.sample_clock_domain)}</Field>
        <Field label="consumed source session">{textOr(consumed?.consumed_source_stamp.session ?? null)}</Field>
        <Field label="consumed sample sequence">{consumed === null ? UNAVAILABLE : String(consumed.consumed_source_stamp.seq)}</Field>
        <Field label="consumed clock domain">{textOr(consumed?.consumed_source_stamp.clock_domain ?? null)}</Field>
        <Field label="base velocity, tap">{velocityWords(tap.base_velocity)}</Field>
        <Field label="base velocity, consumed">{velocityWords(consumed?.base_velocity ?? null)}</Field>
      </Fields>

      <div className="table-scroll"><table className="table">
        <thead>
          <tr>
            <th>joint</th>
            <th>tap</th>
            <th>consumed by the controller</th>
            <th>representation</th>
          </tr>
        </thead>
        <tbody>
          {jointNames(tap, consumed).map((joint) => (
            <tr key={joint}>
              <td>{joint}</td>
              <td>{number(tap.joints?.[joint] ?? null, 2)}</td>
              <td>{number(consumed?.joints[joint] ?? null, 2)}</td>
              <td className="muted">{JOINT_REPRESENTATION_UNSTATED}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </Panel>
  );
}

/* ---- who holds the robot, and what this tab sent it -------------------------------------------------------------- */

/**
 * The lease, the capabilities and the commands this tab sent.
 *
 * The command list is THIS BROWSER's record of what it submitted, and it says so: the runtime's journal is the
 * authority on what was accepted, and a tab that was opened after a command was sent has no line for it at all.
 */
function OwnershipPanel(props: { snapshot: StoreSnapshot }): ReactNode {
  const view = props.snapshot.view;
  const lease = view.lease;
  const commands = props.snapshot.commands;
  const ownership = ownershipWords(view.execution);
  // Under the baseline backend a lease is not the subject and its words are not printed in any form. The lease block
  // below is not merely empty there: it is replaced by the statement of what the ownership actually is, because a row
  // reading "lease held: no" teaches the reader that a lease is what is being reported on, and the reading that
  // follows is that one would be held if things were working. None was ever asked for.
  const leaseIsSubject = leaseIsTheSubject(view.execution);
  return (
    <Panel
      title="ownership and commands"
      extra={<Badge tone={view.owner.kind === 'unresolved' ? 'warn' : 'quiet'}>{view.owner.kind}</Badge>}
      note="The command list below is this browser's own record of what it submitted from this tab; the runtime's journal is what says which commands were accepted. What owns the robot is the runtime's own statement and is never inferred from the presence or the absence of a lease."
    >
      <Fields wide>
        <Field label="owner">{view.owner.kind}</Field>
        <Field label="owning run">{textOr(view.owner.run_id)}</Field>
        <Field label="execution backend">{ownership.backend}</Field>
        <Field label="what owns the robot">{ownership.basis}</Field>
        <Field label="enforced on the robot itself">{ownership.robotSide}</Field>
      </Fields>

      {/* The runtime's own sentence, verbatim. The three rows above are built from the two enumerated fields, so a
          change of wording at the runtime cannot change what this screen claims; this is the wording itself. */}
      <p className="muted" style={{ marginTop: '0.5rem' }}>
        {ownership.detail}
      </p>

      {/* Ten fields of the word unavailable would say the runtime tried to report a lease and could not. What is true
          when this is null is that it has nothing to say about one at all, which is also what a runtime older than
          the field says by not sending it. */}
      {!leaseIsSubject ? (
        <p className="muted" style={{ marginTop: '0.5rem' }}>
          {ownership.unstated
            ? 'This runtime states nothing about a host command lease, and nothing about which backend it runs commands through either. Neither absence is read as the other.'
            : 'Nothing about a host command lease is reported on this backend, because none is asked for and none is taken. The ownership is stated above in full, and it is not the absence of a lease.'}
        </p>
      ) : lease === null ? (
        <p className="muted" style={{ marginTop: '0.5rem' }}>
          This runtime states nothing about a host command lease. That is not the same as a lease that is not held.
        </p>
      ) : (
        <Fields wide>
          <Field label="lease held">{tristate(lease.held)}</Field>
          <Field label="lease required here">{tristate(lease.required)}</Field>
          <Field label="lease attached">{tristate(lease.attached)}</Field>
          <Field label="lease generation">{integer(lease.generation)}</Field>
          <Field label="host session">{textOr(lease.host_session)}</Field>
          <Field label="time left on the lease">{lease.expires_in_ms === null ? UNAVAILABLE : seconds(lease.expires_in_ms / 1000, 1)}</Field>
          <Field label="renew failures">{integer(lease.renew_failures)}</Field>
          <Field label="why no lease is held">{lease.held ? 'one is held' : textOr(lease.reason)}</Field>
        </Fields>
      )}

      <div className="table-scroll"><table className="table">
        <thead>
          <tr>
            <th>capability</th>
            <th>state</th>
            <th>the runtime&apos;s reason</th>
          </tr>
        </thead>
        <tbody>
          {view.capabilities.length === 0 ? (
            <tr>
              <td colSpan={3} className="wrap">
                No snapshot has named a capability yet.
              </td>
            </tr>
          ) : (
            view.capabilities.map((capability) => (
              <tr key={capability.id}>
                <td>{capability.id}</td>
                <td>{capability.state}</td>
                <td className="wrap">{textOr(capability.reason)}</td>
              </tr>
            ))
          )}
        </tbody>
      </table></div>

      <div className="table-scroll"><table className="table">
        <thead>
          <tr>
            <th>command from this tab</th>
            <th>answer</th>
          </tr>
        </thead>
        <tbody>
          {commands.length === 0 ? (
            <tr>
              <td colSpan={2} className="wrap">
                This tab has sent no command. That is not the same as none having been sent: a command sent from
                another tab, or before this one was opened, is in the runtime&apos;s journal and not here.
              </td>
            </tr>
          ) : (
            commands.map((command) => (
              <tr key={command.command_id}>
                <td>{command.type}</td>
                <td className="wrap">
                  {command.unresolved !== null
                    ? `the answer was lost: ${command.unresolved}`
                    : command.result === null
                      ? 'sent, no answer yet'
                      : `${command.result.status}${command.result.reason_code === null ? '' : `: ${command.result.reason_code}`}`}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table></div>
    </Panel>
  );
}

/* ---- what happened during a run --------------------------------------------------------------------------------- */

/**
 * The counts a run ended with, each labelled with the basis it was counted on.
 *
 * THREE DIFFERENT QUANTITIES that a single number would have merged. The controller's own retry total is what the
 * controller counted, by kind, and summed into its record. The state re-entries are what the transitions in this fold
 * show, which is an observation of the record stream and is NOT the retry count; the two are shown apart and the
 * screen never presents one as the other. The operator interventions are counted by the controller as well, and the
 * reason that figure can be at most one is the runtime's own rule, quoted from the capability list rather than
 * written here.
 */
/**
 * A total the CONTROLLER reports when a run ends.
 *
 * Before it reports one there is no number, and the word for that is not unavailable: unavailable says the runtime
 * was in a position to report it and could not. A run that is still going has not reported yet, and a runtime in
 * which no run has finished has nothing to have reported.
 */
function runTotal(value: number | null, view: ViewState): string {
  if (value !== null) return integer(value);
  // Short words, because the column beside this one is where the whole sentence belongs.
  if (view.active_run !== null) return 'not yet';
  if (view.outcome.run_id === null) return 'no run yet';
  return UNAVAILABLE;
}

function RunCountsPanel(props: { view: ViewState }): ReactNode {
  const view = props.view;
  const reentries = stateReentries(view);
  const blockReason = commandBlockReason(view);
  const runEvidence = view.run_evidence;
  return (
    <Panel
      title="what the run counted"
      state={view.outcome.automatic_retries === null ? 'waiting' : undefined}
      stateWords={view.outcome.automatic_retries === null ? 'Not reported yet' : undefined}
      note="The retry total is the controller's own, summed from its per-kind counters. The re-entry figures below it are this console's observation of the transitions, and they are a different quantity: a state entered again is not necessarily a retry, and a retry the controller counted need not have changed the state."
    >
      {/* A table and not a field list, because every figure here needs its basis stated beside it, and the basis is a
          sentence. Two of these are the controller's own counts and the rest are the runtime's; none of them is this
          console's arithmetic. */}
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>what was counted</th>
              <th>figure</th>
              <th>counted by, and on what basis</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>automatic retries</td>
              <td>{runTotal(view.outcome.automatic_retries, view)}</td>
              <td className="wrap">the controller, from its own per-kind counters, summed into the run record when the run ends</td>
            </tr>
            <tr>
              <td>task control interventions</td>
              <td>{runTotal(view.outcome.task_control_interventions, view)}</td>
              <td className="wrap">
                the controller, in the same record, when the run ends.
                {blockReason === null ? '' : ` While a run is in flight the runtime accepts almost nothing from an operator: ${blockReason}`}
              </td>
            </tr>
            {/* The run's own evidence counters, which the runtime keeps apart from the session's. A runtime that
                serves none says so in one row rather than in four rows of the word unavailable. */}
            {runEvidence === null ? (
              <tr>
                <td colSpan={3} className="wrap">
                  This runtime serves no per-run evidence counters. The session counters are in the record integrity
                  card, and they count the whole session and not this run.
                </td>
              </tr>
            ) : (
              <>
                <tr>
                  <td>records kept for this run</td>
                  <td>{integer(runEvidence.records)}</td>
                  <td className="wrap">the runtime, counting what it appended for this run</td>
                </tr>
                <tr>
                  <td>evidence gaps in this run</td>
                  <td>{integer(runEvidence.gaps)}</td>
                  <td className="wrap">the runtime, from the gap records of this run</td>
                </tr>
                <tr>
                  <td>telemetry dropped in this run</td>
                  <td>{integer(runEvidence.dropped_telemetry)}</td>
                  <td className="wrap">the runtime, from the drop records of this run</td>
                </tr>
                <tr>
                  <td>this run&apos;s evidence complete</td>
                  <td>{runEvidence.complete === null ? 'not stated yet' : tristate(runEvidence.complete)}</td>
                  <td className="wrap">
                    the run&apos;s own record when it ends. An absence of gaps while it is still going is not a
                    completed mission.
                  </td>
                </tr>
              </>
            )}
            {reentries.length === 0 ? (
              <tr>
                <td>state re-entries observed</td>
                <td>none</td>
                <td className="wrap">this console, from the transitions it folded. No state has been entered twice.</td>
              </tr>
            ) : (
              reentries.map((row) => (
                <tr key={row.state}>
                  <td>{row.state} entered again</td>
                  <td>
                    {/* THE FIRST ENTRY IS NOT A RE-ENTRY: two entries into ALIGN are one "entered again" (status 2026-09-27 LOW 3). */}
                    {String(row.entries - 1)} time(s), {String(row.entries)} entries in all
                  </td>
                  <td className="wrap">this console, from the transitions it folded. This is an observation, not a retry count.</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function RecordPanel(props: { view: ViewState }): ReactNode {
  const tail = props.view.recent_events.slice(-80).reverse();
  return (
    <Panel title="records" extra={<Badge tone="quiet">{String(props.view.recent_events.length)} held</Badge>} note="Newest first. This is the contract stream as it arrived.">
      <div className="log">
        {tail.length === 0 ? (
          <span className="muted">Nothing has arrived yet.</span>
        ) : (
          tail.map((event) => (
            <div className="log__line" key={`${String(event.seq)}-${event.kind}`}>
              <span className="log__seq">{String(event.seq).padStart(5, '0')}</span>
              <span>{event.kind}</span>
              <span className="muted">{JSON.stringify(event.payload).slice(0, 180)}</span>
            </div>
          ))
        )}
      </div>
    </Panel>
  );
}

function DiagnosticsPanel(props: { view: ViewState }): ReactNode {
  return (
    <Panel
      title="controller diagnostics"
      extra={<Badge tone="quiet">{String(props.view.diagnostics.length)} lines</Badge>}
      note="These lines are for a human. Nothing on this screen is parsed out of them, and a line the emitter altered to make it safe is marked redacted."
    >
      <div className="log">
        {props.view.diagnostics.length === 0 ? (
          <span className="muted">The controller has printed nothing in this run.</span>
        ) : (
          props.view.diagnostics
            .slice()
            .reverse()
            .map((line) => (
              <div className="log__line" key={line.seq}>
                <span className="log__seq">{String(line.seq).padStart(5, '0')}</span>
                <span className={`log__level--${line.level}`}>{line.level}</span>
                <span>{line.text}</span>
                {line.redacted ? <span className="muted">[redacted]</span> : null}
              </div>
            ))
        )}
      </div>
    </Panel>
  );
}

/* ---- the finished runs, and the transport that replays one -------------------------------------------------------- */

/**
 * Play, pause and scrub over an open recording.
 *
 * NOTHING HERE RECONSTRUCTS ANYTHING ITSELF. The cursor is moved through the store, which re-folds the recording from
 * its beginning up to the cursor through the SAME reducer the live view uses, so every panel on the screen is the
 * recording's own state at that point and not a frame swapped under a live view.
 *
 * The clock is the RECORDING's, not this computer's: each turn advances the cursor by as much of the recording's own
 * elapsed time as one tick covers, so a pause between two records in the recording is a pause on the screen. Records
 * that carry another clock domain have no place on that axis and are stepped past one at a time instead of being
 * given a time nobody measured.
 */
function ReplayTransport(props: { snapshot: StoreSnapshot; store: ConsoleStore }): ReactNode {
  const replay = props.snapshot.replay;
  const [playing, setPlaying] = useState(false);
  const store = props.store;

  useEffect(() => {
    if (!playing) return undefined;
    const timer = window.setInterval(() => {
      const current = store.getSnapshot().replay;
      if (current === null || current.cursor >= current.events.length) {
        setPlaying(false);
        return;
      }
      store.setReplayCursor(advanceReplayCursor(current.events, current.cursor, REPLAY_TICK_MS));
    }, REPLAY_TICK_MS);
    return () => window.clearInterval(timer);
  }, [playing, store]);

  if (replay === null) return null;
  const axis = replayAxis(replay.events, replay.cursor);
  const provenance = recordedProvenance(replay.events);
  const atEnd = replay.cursor >= replay.events.length;
  return (
    <div className="replay">
      <div className="replay__controls">
        <button
          type="button"
          className="button"
          onClick={() => {
            if (playing) {
              setPlaying(false);
              return;
            }
            // Pressing play at the end starts the recording again rather than doing nothing visible.
            if (atEnd) store.setReplayCursor(0);
            setPlaying(true);
          }}
        >
          {playing ? 'pause' : atEnd ? 'play from the start' : 'play'}
        </button>
        <input
          className="slider replay__slider"
          type="range"
          aria-label="replay cursor"
          min={0}
          max={replay.events.length}
          value={replay.cursor}
          onChange={(event) => {
            setPlaying(false);
            store.setReplayCursor(Number(event.target.value));
          }}
        />
        <Badge tone="replay" strong>
          {provenanceWords(provenance)}
        </Badge>
      </div>
      <div className="replay__caption">
        Folded {String(replay.cursor)} of {String(replay.events.length)} records of <span className="mono">{replay.run_id}</span>.{' '}
        {axis.elapsed_ns === null ? (
          <>The recording states no time axis: it holds fewer than two records in one clock domain.</>
        ) : (
          <>
            Recorded time {seconds((axis.elapsed_ns ?? 0) / 1e9, 1)} of {seconds((axis.span_ns ?? 0) / 1e9, 1)}, measured in{' '}
            {textOr(axis.clock_domain)}
            {axis.off_axis === 0 ? '' : `, over the ${String(axis.on_axis)} records in that domain; ${String(axis.off_axis)} record(s) carry another clock and are not placed on this axis`}.
          </>
        )}{' '}
        {provenance.implementation === null ? (
          <>The recording names no implementation digest.</>
        ) : (
          <>
            Implementation <span className="mono">{digest12(provenance.implementation)}</span>.
          </>
        )}
      </div>
    </div>
  );
}

export function HistoryPanel(props: { snapshot: StoreSnapshot; store: ConsoleStore }): ReactNode {
  const { history, replay } = props.snapshot;
  const entries = history !== null && history.ok ? history.entries : [];
  const groups = groupRuns(entries);
  // Three different facts, never collapsed into one: not read yet, read and refused, read and empty.
  const emptyNote =
    history === null
      ? 'The finished-run list has not been read yet.'
      : !history.ok
        ? `The runtime did not serve the finished-run list: ${history.reason_code}. This is not the same as there being none.`
        : 'No run has finished in this runtime session.';
  return (
    <Panel
      title="finished runs"
      extra={
        replay === null ? (
          <Badge tone="quiet">live fold</Badge>
        ) : (
          <>
            <Badge tone="replay" strong>
              replay
            </Badge>
            <button type="button" className="button" onClick={() => props.store.closeReplay()}>
              back to live
            </button>
          </>
        )
      }
      note="A replay folds the recorded events through the same reducer the live view uses, from the beginning up to the cursor. Nothing is recomputed differently. Runs are grouped by what they ran against and which implementation ran them, and no figure is ever computed across one of those boundaries."
    >
      <ReplayTransport snapshot={props.snapshot} store={props.store} />
      <table className="table">
        <thead>
          <tr>
            <th>run</th>
            <th>started</th>
            <th>command</th>
            <th>outcome</th>
            <th>evidence</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {entries.length === 0 ? (
            <tr>
              <td colSpan={6} className="wrap">
                {emptyNote}
              </td>
            </tr>
          ) : (
            groups.map((group) => (
              <Fragment key={group.key}>
                <tr className="run-group">
                  <td colSpan={6}>
                    {runGroupWords(group)}{' '}
                    <span className="run-group__count">
                      {String(group.entries.length)} run(s)
                      {group.data_mode === null ? '. A run that did not record its mode is kept apart and never filled in from this runtime.' : ''}
                    </span>
                  </td>
                </tr>
                {group.entries.map((entry) => (
                  <tr key={entry.run_id}>
                    <td>{entry.run_id}</td>
                    <td className="muted">{textOr(entry.started_wall_utc)}</td>
                    <td className="muted">{textOr(entry.command_type)}</td>
                    <td>{textOr(entry.outcome)}</td>
                    <td>
                      {entry.evidence_complete === null ? UNAVAILABLE : entry.evidence_complete ? 'complete' : 'incomplete'}
                      {entry.evidence_gaps === null || entry.evidence_gaps === 0 ? '' : `, ${String(entry.evidence_gaps)} gap(s)`}
                    </td>
                    <td>
                      <button type="button" className="button" onClick={() => void props.store.openReplay(entry.run_id)}>
                        replay
                      </button>
                    </td>
                  </tr>
                ))}
              </Fragment>
            ))
          )}
        </tbody>
      </table>
    </Panel>
  );
}

export function EngineeringView(props: {
  snapshot: StoreSnapshot;
  store: ConsoleStore;
  nowMs?: number;
  /**
   * The view of the run on the screen, for the panels about ONE run (its decisions, counts, spans and records): the live
   * fold while it watched that run, else the run's own read-back fold (`viewForSelectedRun`), so a page opened after the
   * run never reads the empty live fold as the run's (verifier r2 defect 4). Absent: the live fold.
   */
  runView?: ViewState;
}): ReactNode {
  const snapshot = props.snapshot;
  const runView = props.runView ?? snapshot.view;
  // The one shared reading of this computer's clock, so an age on this tab advances between the polls instead of
  // standing still and then jumping. A test pins it by passing its own.
  const shared = useNowMs();
  const nowMs = props.nowMs ?? shared;
  const replaying = snapshot.replay !== null;
  return (
    <div>
      <Group
        title="telemetry"
        subtitle="Where the numbers come from: each source as the runtime states it, and the sample a decision was taken on."
        extra={
          replaying ? (
            <Badge tone="warn" strong>
              live lines, not part of the open recording
            </Badge>
          ) : null
        }
      >
        <SourcesDetailPanels snapshot={snapshot} nowMs={nowMs} />
        {/* The three camera ages and the reasoning under them, moved off the presentation card at the operator's
            request. They sit with the source panels because they are about the same two sources. */}
        <CameraClocksPanel snapshot={snapshot} nowMs={nowMs} />
        <TapAndConsumedPanel snapshot={snapshot} nowMs={nowMs} />
      </Group>

      <Group title="decisions" subtitle="What the controller looked at is in Evidence; what it concluded from it, and which states it moved through, are here.">
        <PredicatesPanel view={runView} notRead={runRecordsNotRead(snapshot)} />
      </Group>

      <Group title="ownership and commands" subtitle="Who holds the robot, what this runtime will accept, and what this tab has sent it.">
        <OwnershipPanel snapshot={snapshot} />
      </Group>

      <Group title="timing" subtitle="How long the measured spans took, kept apart by the clock each one was measured in." single>
        <TimingPanel view={runView} />
      </Group>

      <Group title="cake" subtitle="What the resident is doing in this mission, and which capabilities are not built yet.">
        <CakePanel view={snapshot.view} mission={snapshot.mission} robot={snapshot.robot} robotError={snapshot.robotError} />
        <FuturePanel view={snapshot.view} />
      </Group>

      <Group title="runs and diagnostics" subtitle="What the finished runs were, what this one counted, and the records themselves.">
        {/* The finished-run table and the replay transport take the whole band: six columns and a slider somebody
            drags do not belong in a third of the width. */}
        <div className="group__wide">
          <HistoryPanel snapshot={snapshot} store={props.store} />
        </div>
        <RunCountsPanel view={runView} />
        {/* Session-wide: the cursor, the runtime session and the session's evidence are this tab's, never one run's. */}
        <EvidenceSummary view={snapshot.view} />
        <ComponentsPanel view={snapshot.view} />
        <RecordPanel view={runView} />
        <DiagnosticsPanel view={runView} />
      </Group>
    </div>
  );
}
