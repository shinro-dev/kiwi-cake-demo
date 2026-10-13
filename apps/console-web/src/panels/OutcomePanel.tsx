// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The physical outcome and the completeness of the evidence are two facts, side by side and never merged: a cube can
// be placed on thin evidence, and a refusal can be fully evidenced. The console shows the runtime's outcome as the
// runtime reported it, and separately whether the four end predicates all measured true, which is the only thing that
// makes a placement CONFIRMED on this screen.
//
// NOTHING ON THIS CARD IS REPORTED UNTIL A RUN FINISHES, and that is why every empty row here used to print the word
// unavailable. Seven rows of it, while a run was going well, said the runtime had tried to report seven things and
// could not. What was true was that a run reports them when it ends. The three cases are different facts and get three
// different words: no run has been started, a run is going and has not reported this yet, and a run finished without
// this value on its record. Only the third one is unavailable.
//
// THE NO-OP IS NOT AN ACCEPTANCE PASS. When the cube is already inside the pad outline at the start, the controller
// records `already_placed` from that measurement, states DONE_ALREADY_PLACED, moves nothing, and sets the `placed`
// end predicate to true by that decision rather than from a placement it measured (run_single_cube_cycle.py:950, :952
// and :1611). The run then reports already_placed_noop, which is a success and not a failure. This card says which of
// the two happened in plain words, because the outcome word alone does not: a screen that draws both of them the same
// way is telling an operator that a cube was placed when nothing moved.

import type { ReactNode } from 'react';
import { integer } from '../model/format';
import type { PredicateView, ViewState } from '../model/types';
import { Badge, Tristate } from '../ui/Badge';
import { CardStatus } from '../ui/CardState';
import { Field, Fields, Panel } from '../ui/Panel';

const PREDICATE_LABELS: [keyof NonNullable<ViewState['outcome']['predicates']>, string][] = [
  ['object_secured', 'object secured'],
  ['placed', 'placed'],
  ['clearance_target_met', 'clearance target met'],
  ['base_returned', 'base returned'],
];

/** Where a run is in its life, which is what decides the words for everything this card has no value for yet. */
type RunStage = 'none' | 'running' | 'finished';

/**
 * The outcome this card is about, and the one it is NOT about.
 *
 * A snapshot carries the runtime's last finished run, and a console that attaches while a new run is going folds it
 * (reducer.ts:256). That outcome, its exit status, its retry counts and its four end predicates all belong to the
 * EARLIER run. Drawn in these rows they would read as the run an operator is watching, which is the same mistake as
 * printing a stale reading as a current one. So the rows say the run in flight has reported nothing yet, and the
 * earlier run is named in a line of its own.
 */
function outcomeOfThisRun(view: ViewState): { shown: ViewState['outcome'] | null; earlier: ViewState['outcome'] | null; stage: RunStage } {
  const outcome = view.outcome;
  const activeRunId = view.active_run?.run_id ?? null;
  if (activeRunId !== null && outcome.reported !== null && outcome.run_id !== null && outcome.run_id !== activeRunId) {
    return { shown: null, earlier: outcome, stage: 'running' };
  }
  if (outcome.reported !== null) return { shown: outcome, earlier: null, stage: 'finished' };
  return { shown: outcome, earlier: null, stage: activeRunId === null ? 'none' : 'running' };
}

/**
 * The words for a row with nothing in it. A run that finished and did not carry the value is the ONE case that reads
 * unavailable: the runtime was in a position to report it and did not.
 */
function Absent(props: { stage: RunStage; what: string }): ReactNode {
  if (props.stage === 'finished') {
    return <CardStatus state="unknown" title={`the run reported its outcome and ${props.what} was not on the record`} />;
  }
  if (props.stage === 'running') {
    return <CardStatus state="waiting" title={`a run reports ${props.what} when it finishes, and this one is still going`} />;
  }
  return <CardStatus state="idle" words="No run yet" title={`${props.what} is reported by a run, and none has been started`} />;
}

function measuredAgainst(predicate: PredicateView | null): string | null {
  if (predicate === null || predicate.measured === null || predicate.threshold === null) return null;
  const unit = predicate.unit === null ? '' : ` ${predicate.unit}`;
  return `${String(predicate.measured)}${unit} against the ${String(predicate.threshold)}${unit} a placement needs`;
}

export function OutcomePanel(props: { view: ViewState }): ReactNode {
  const view = props.view;
  const { shown, earlier, stage } = outcomeOfThisRun(view);
  const predicates = shown?.predicates ?? null;
  const reported = shown?.reported ?? null;
  const confirmed = shown?.confirmed ?? null;
  const noop = reported === 'already_placed_noop';
  // The measurement the no-op rests on, which is the only measurement in it: how far inside the pad the cube already
  // was when the run started. It is shown with the no-op so the claim can be checked rather than taken.
  const alreadyPlaced = view.predicates.find((item) => item.predicate === 'already_placed') ?? null;
  const alreadyPlacedMargin = measuredAgainst(alreadyPlaced);

  return (
    <Panel
      title="outcome"
      state={stage === 'finished' ? undefined : stage === 'running' ? 'active' : 'idle'}
      stateWords={stage === 'running' ? 'Run in progress' : stage === 'none' ? 'No run yet' : undefined}
      extra={
        <>
          <Badge tone={reported === null ? 'quiet' : reported === 'failed' ? 'bad' : 'info'} strong>
            {reported ?? 'no outcome yet'}
          </Badge>
          {/* A verdict needs something to be a verdict ON. Before a run has reported an outcome the four end predicates
              have not been measured at all, and "not confirmed" would be a claim about measurements that do not exist. */}
          {reported === null ? (
            <Badge tone="quiet" title="no run has reported an outcome, so there is nothing to confirm or refuse">
              nothing to confirm yet
            </Badge>
          ) : noop ? (
            <Badge tone="warn" title="the cube was already inside the pad outline, so this run measured no placement of its own">
              no placement was measured
            </Badge>
          ) : confirmed === null ? (
            <Badge tone="warn" title="the four end predicates did not all measure true">
              not confirmed
            </Badge>
          ) : (
            <Badge tone="ok" title="all four end predicates measured true">
              confirmed by four predicates
            </Badge>
          )}
        </>
      }
      note={
        reported === 'placed_new' && confirmed === null ? (
          <>
            The runtime reported <code>placed_new</code>, but not all four end predicates measured true. This screen shows the reported outcome and
            refuses to call the placement confirmed.
          </>
        ) : (
          <>Automatic retries are the controller retrying itself. Task-control interventions are a person intervening. They are counted apart.</>
        )
      }
    >
      {!noop ? null : (
        <div className="reason">
          <div className="reason__headline">Nothing was moved: the cube was already inside the pad outline when the run started.</div>
          <div className="reason__detail wrap">
            The controller set the <code>placed</code> end predicate because of that, not from a placement it measured in this run. A run that moved
            nothing is not an acceptance pass, and this screen does not count it as one.
          </div>
          {alreadyPlaced === null ? (
            <div className="reason__basis">the already_placed record is not in this fold, so the margin it decided on is not shown</div>
          ) : (
            <div className="reason__basis">
              built from: predicate.evaluated already_placed at seq {String(alreadyPlaced.seq)}
              {alreadyPlacedMargin === null ? '' : `, ${alreadyPlacedMargin}`}
            </div>
          )}
        </div>
      )}

      {earlier === null ? null : (
        <p className="muted wrap">
          The runtime&apos;s last finished run, {earlier.run_id ?? 'whose id the record does not carry'}, ended {earlier.reported}. That is a
          different run from the one in progress, and its numbers are not the ones below.
        </p>
      )}

      <Fields wide>
        <Field label="reported outcome">{reported ?? <Absent stage={stage} what="the outcome" />}</Field>
        <Field label="exit status">
          {shown === null || shown.exit_status === null ? <Absent stage={stage} what="the exit status" /> : integer(shown.exit_status)}
        </Field>
        <Field label="evidence">
          {shown === null || shown.evidence_complete === null ? (
            <Absent stage={stage} what="the completeness of the evidence" />
          ) : shown.evidence_complete ? (
            <Badge tone="ok">complete</Badge>
          ) : (
            <Badge tone="warn">{String(shown.evidence_gaps ?? 0)} gap(s)</Badge>
          )}
        </Field>
        <Field label="automatic retries">
          {shown === null || shown.automatic_retries === null ? (
            <Absent stage={stage} what="its retry count" />
          ) : (
            integer(shown.automatic_retries)
          )}
        </Field>
        <Field label="task-control interventions">
          {shown === null || shown.task_control_interventions === null ? (
            <Absent stage={stage} what="its intervention count" />
          ) : (
            integer(shown.task_control_interventions)
          )}
        </Field>
        {/* These four are counted by this console as records arrive, so a zero here is a count that was taken and came
            back empty, which is a different thing from a value a run has not reported. */}
        <Field label="reconnects">{integer(view.counters.reconnects)}</Field>
        <Field label="runtime restarts">{integer(view.counters.runtime_restarts)}</Field>
        <Field label="dropped telemetry">{integer(view.evidence.dropped_telemetry)}</Field>
        <Field label="unreadable records">{integer(view.counters.malformed_lines)}</Field>
      </Fields>

      <table className="table" style={{ marginTop: '0.75rem' }}>
        <thead>
          <tr>
            <th>end predicate</th>
            <th>value</th>
          </tr>
        </thead>
        <tbody>
          {PREDICATE_LABELS.map(([key, label]) => (
            <tr key={key}>
              <td>{label}</td>
              <td>
                {predicates === null ? (
                  <Absent stage={stage} what="its end predicates" />
                ) : (
                  <>
                    <Tristate value={predicates[key]} />
                    {noop && key === 'placed' ? (
                      <span className="muted"> set by the controller because the cube was already there, not measured in this run</span>
                    ) : null}
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {view.evidence_gap_events.length === 0 ? null : (
        <table className="table" style={{ marginTop: '0.75rem' }}>
          <thead>
            <tr>
              <th>evidence gap</th>
              <th>detail</th>
            </tr>
          </thead>
          <tbody>
            {view.evidence_gap_events.map((gap) => (
              <tr key={gap.seq}>
                <td>{gap.gap}</td>
                <td className="wrap">{gap.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
