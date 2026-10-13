// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHY THE RUN DID NOT END WELL, without a terminal.
//
// Everything on this panel came off the record: the phase the machine was in, the machine-readable reason code, the
// controller's own sentence, the values that decided it against their thresholds, and the observations the controller
// named. Nothing here is recomputed and nothing is paraphrased.
//
// A reason code this console has no gloss for renders as the CODE, in the headline, with its whole payload under it.
// That is the case the panel is really built for: the console will always be behind the controller by a version or
// two, and a screen that goes blank on an unknown code sends the operator back to a terminal, which is the one thing
// this panel exists to prevent.
//
// A FAILURE OPENS WITH FOUR THINGS: the phase it stopped in, the reason, the predicate that decided it, and what can
// be done next. The last one is the runtime's answer and not this console's: the console has a table of which command
// is the right next step after which reason code, and the SERVER's capability list decides whether that command is
// available and says why when it is not. A code the table does not cover is offered nothing at all.

import type { ReactNode } from 'react';
import { UNSCOPED_COMMAND } from '../model/command';
import type { CommandScope } from '../model/command';
import { explainFailure, stopRecordLines } from '../model/failure';
import type { MeasuredAgainst } from '../model/failure';
import type { NextStep } from '../model/nextSteps';
import type { StopCause } from '../model/stopCause';
import { textOr, UNAVAILABLE } from '../model/format';
import { PHASE_LABEL, phaseOfState } from '../model/phase';
import type { ViewState } from '../model/types';
import { runWordFor } from '../model/verdict';
import { Badge } from '../ui/Badge';
import { IconAlert } from '../ui/Icons';
import { Panel } from '../ui/Panel';
import type { ConsoleStore, StoreSnapshot } from '../store/store';

function valueText(value: MeasuredAgainst['value']): string {
  if (value === null) return UNAVAILABLE;
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
}

function MeasurementRows(props: { rows: MeasuredAgainst[] }): ReactNode {
  return (
    <>
      {props.rows.map((row) => (
        <tr key={`${row.source}-${row.name}`}>
          <td>{row.name}</td>
          <td>
            {valueText(row.value)}
            {row.unit === null || row.value === null ? '' : ` ${row.unit}`}
          </td>
          <td>
            {/* A threshold that was not recorded is not a threshold of zero, and this value is then a measurement and
                not a comparison. The cell says which of the two it is holding rather than the word unavailable, which
                would read as a runtime that tried to report a threshold and could not. */}
            {row.threshold === null ? (
              <span className="muted" title="the record carried this value and no threshold, so it is a measurement and not a comparison">
                no threshold recorded
              </span>
            ) : (
              `${String(row.threshold)}${row.unit === null ? '' : ` ${row.unit}`}`
            )}
          </td>
          <td className="muted">{row.source === 'failure' ? 'the failure record' : 'a predicate that measured false'}</td>
        </tr>
      ))}
    </>
  );
}

/** The phase the stopped state belongs to, in the words the task card uses, or nothing for a state with no phase. */
/** Each id with what this stream says about its capture: its role, its seq and whether the runtime retained it. */
function ObservationList(props: { ids: readonly string[]; view: ViewState }): ReactNode {
  return (
    <ul className="note-list">
      {props.ids.map((id) => {
        const capture = props.view.observations[id];
        return (
          <li key={id}>
            <code>{id}</code>
            {capture === undefined ? (
              <span className="muted"> - no capture record for it arrived on this stream</span>
            ) : (
              <span className="muted">
                {' '}
                - {capture.source_role} capture at seq {String(capture.seq)}
                {capture.retained ? ', retained' : ', not retained'}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function phaseWords(phase: string | null): string | null {
  if (phase === null) return null;
  const assignment = phaseOfState(phase);
  if (assignment === 'unknown') return 'this console has no phase for that state name';
  if (assignment === 'terminal') return 'the end of the run, and not one of the phases';
  return PHASE_LABEL[assignment];
}

export function FailurePanel(props: {
  view: ViewState;
  scope?: CommandScope;
  snapshot?: StoreSnapshot;
  store?: ConsoleStore;
  /** The stop's own cause, joined by action id (`model/stopCause.ts`), the same the mission card states. */
  cause?: StopCause | null;
  /** The runtime's step list, in the mission card's words, or null when no mission lists one. */
  steps?: readonly NextStep[] | null;
}): ReactNode {
  // The scope is what keeps this panel off the screen for a command that has no placement in it and ended well: a
  // successful Observe is not a failure and is never drawn as one. `model/failure.ts` holds that decision. A caller
  // that names no command gets the strictest scope there is, so an absent scope can only ever draw MORE than a named
  // one would and never less.
  const explanation = explainFailure(props.view, props.scope ?? UNSCOPED_COMMAND);
  if (explanation === null) return null;

  const code = explanation.reason_code;
  // A RUN STOPPED ON REQUEST (round 7 item 2): its stop records explain the ending, in a neutral tone. It is not drawn as
  // a failure, and a missing reason code beside them is not "a gap in the record".
  const onRequest = explanation.stop_request !== null;
  const stopLines = explanation.stop_request === null ? [] : stopRecordLines(explanation.stop_request);
  // The sentence with the raise site's own values in it where the record carried them all, and the wordy one where it
  // did not. Neither is this console's own account of what happened: both come out of the glossary for the code the
  // controller decided at the point of decision.
  const sentence = explanation.sentence;
  // THE RUN'S WORD (spec 5.1; browser r2 N2), never the outcome token on the screen: the token is in the title.
  const word = runWordFor({
    events: explanation.run_id === null || props.snapshot === undefined ? undefined : props.snapshot.missionRunEvents[explanation.run_id],
    view: props.view,
    run_id: explanation.run_id,
    outcome: explanation.outcome,
    reason_code: code,
    placement: (props.scope ?? UNSCOPED_COMMAND).requires_placement,
  });
  const ended = `The run ended${word === null ? ', its word not read yet' : `: ${word.word}`}`;
  const headline =
    code === null
      ? onRequest
        ? `${ended}. Its stop records below say how it ended.`
        : `${ended}. The record does not carry a reason code.`
      : sentence === null
        ? // Unknown code: the code IS the headline. No guess, no blank.
          `${ended} (${code})`
        : `${ended}. ${sentence.charAt(0).toUpperCase()}${sentence.slice(1)}`;
  const token = `run.finished outcome ${explanation.outcome}`;
  const phase = phaseWords(explanation.phase);
  const activeRunId = props.view.active_run?.run_id ?? null;
  const earlierRun = activeRunId !== null && explanation.run_id !== null && explanation.run_id !== activeRunId ? explanation.run_id : null;


  return (
    <Panel
      title="why it ended this way"
      className="panel--failure"
      extra={
        <>
          <Badge tone={onRequest ? 'neutral' : 'bad'} strong title={token}>
            {onRequest ? null : <IconAlert />} {word === null ? 'Ended' : word.word}
          </Badge>
          <Badge tone="quiet" title="the state the machine was in when it stopped">
            phase {textOr(explanation.phase)}
            {phase === null ? '' : ` (${phase})`}
          </Badge>
          <Badge tone={explanation.recognised ? 'info' : onRequest && code === null ? 'quiet' : 'warn'} title={explanation.recognised ? undefined : 'this console has no sentence for this code, so the code itself is shown'}>
            {code ?? 'no reason code on the record'}
          </Badge>
        </>
      }
      note={
        onRequest && code === null ? (
          <>
            This run was stopped on request. The lines above are its stop records as the runtime wrote them; the record
            carries no reason code beside them.
          </>
        ) : explanation.unexplained ? (
          <>
            This run ended badly and the record carries no machine-readable reason. That is a gap in the record, not a
            run without a cause: the controller diagnostics in the engineering view are the next place to look.
          </>
        ) : explanation.recognised ? (
          <>
            The sentence above is the controller&apos;s own, decided where the decision was made. The code beside it is what
            a machine routes on; both came from the same record.
          </>
        ) : (
          <>
            This console has no sentence for <code>{code}</code>, so the code is shown as it arrived, with{' '}
            {explanation.measurements.every((item) => item.source !== 'failure')
              ? "every predicate of this run that measured false; the stop's record carried no values"
              : "the values the stop's record carried and every predicate of this run that measured false"}
            . Nothing has been guessed at and nothing has been left out.
          </>
        )
      }
    >
      {/* A snapshot carries the runtime's last finished run, so this card can be about a run that ended before the one
          now under way. When it is, it says so first, because everything under it belongs to that earlier run. */}
      {earlierRun === null ? null : (
        <p className="muted wrap">
          This is the run that ended before the one now in progress. It is run {earlierRun}, and the run in flight has not ended.
        </p>
      )}

      <div className="reason">
        <div className="reason__headline" title={token}>
          {headline}
        </div>
        {onRequest ? (
          <ul className="note-list" data-stop-records="">
            {stopLines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        ) : null}
        {explanation.detail === null ? (
          onRequest ? null : <div className="reason__detail muted">
            The record carried no sentence for this failure, only the code{code === null ? ' (and not even that)' : ''}.
          </div>
        ) : (
          <div className="reason__detail wrap">{explanation.detail}</div>
        )}
        {code === null || explanation.recognised ? null : (
          <div className="reason__basis">
            reason code shown verbatim: <code>{code}</code>
          </div>
        )}
      </div>

      {/* THE STOP'S OWN CAUSE, joined by action id, in the mission card's words (spec 6.3.4, audit C4): the same function
          the card reads, so this tab cannot say a value is missing while the card states it. */}
      {props.cause === null || props.cause === undefined ? null : (
        <div data-evidence-cause={props.cause.reason_code} style={{ marginTop: '0.75rem' }}>
          <div className="field__label">why the run stopped, from its own records</div>
          <ul className="note-list">
            {props.cause.lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <div className="muted">
            {String(props.cause.predicates_recorded)} predicates recorded, {String(props.cause.predicates_tied)} tied to the stop.
          </div>
        </div>
      )}
      {explanation.measurements.length === 0 ? (
        props.cause === null || props.cause === undefined ? (
          <p className="muted" style={{ marginTop: '0.75rem' }}>
            No measured value was recorded with this {onRequest ? 'stop' : 'failure'}. The screen says so rather than showing a table of zeroes.
          </p>
        ) : null
      ) : (
        <table className="table" style={{ marginTop: '0.75rem' }}>
          <thead>
            <tr>
              <th>measured</th>
              <th>value</th>
              <th>against</th>
              <th>from</th>
            </tr>
          </thead>
          <tbody>
            <MeasurementRows rows={explanation.measurements} />
          </tbody>
        </table>
      )}

      {/* THE STOP'S OWN NAMES UNDER THE CONTROLLER'S HEADING, and nothing else (verdict 2026-09-27 item 9): a frame a
          false predicate read is listed apart and said not to be named by the stop. */}
      <div data-evidence-named="" style={{ marginTop: '0.75rem' }}>
        <div className="field__label">evidence the controller named</div>
        {explanation.observation_ids.length === 0 ? (
          <div className="muted">The stop named no observation: its record&apos;s list of observations is empty.</div>
        ) : (
          <ObservationList ids={explanation.observation_ids} view={props.view} />
        )}
      </div>
      {explanation.predicate_observation_ids.length === 0 ? null : (
        <div data-evidence-read="" style={{ marginTop: '0.75rem' }}>
          <div className="field__label">observations the false predicates read, not named by the stop</div>
          <ObservationList ids={explanation.predicate_observation_ids} view={props.view} />
        </div>
      )}

      {explanation.failing_predicates.length === 0 ? null : (
        <table className="table" style={{ marginTop: '0.75rem' }}>
          <thead>
            <tr>
              <th>predicate that measured false</th>
              <th>measured</th>
              <th>threshold</th>
              <th>at seq</th>
            </tr>
          </thead>
          <tbody>
            {explanation.failing_predicates.map((predicate) => (
              <tr key={predicate.predicate}>
                <td>{predicate.predicate}</td>
                <td>
                  {predicate.measured === null ? UNAVAILABLE : String(predicate.measured)}
                  {predicate.unit === null || predicate.measured === null ? '' : ` ${predicate.unit}`}
                </td>
                <td>
                  {predicate.threshold === null ? UNAVAILABLE : String(predicate.threshold)}
                  {predicate.unit === null || predicate.threshold === null ? '' : ` ${predicate.unit}`}
                </td>
                <td className="muted">{String(predicate.seq)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* THE RUNTIME'S OWN STEPS, as the mission card lists them (spec 6.2.4, audit C5). This tab keeps no step table of
          its own: a reason-code table here said "no next step" beside a card that listed three. */}
      {props.steps === null || props.steps === undefined ? null : (
        <div style={{ marginTop: '0.75rem' }}>
          <div className="field__label">the runtime&apos;s steps, as the mission card lists them</div>
          {props.steps.length === 0 ? (
            <div className="muted wrap">The mission record lists no step.</div>
          ) : (
            <ol className="note-list">
              {props.steps.map((step) => (
                <li key={step.token}>{step.words}</li>
              ))}
            </ol>
          )}
        </div>
      )}

      {/* The machine-readable side of the sentence above, one click down: the raw code and every value the raise site
          carried, under the names it used, for a bug report and for a console that is a version behind. */}
      <details style={{ marginTop: '0.75rem' }}>
        <summary className="field__label" style={{ cursor: 'pointer' }}>
          the raw code and the values as recorded
        </summary>
        <div className="reason__basis" style={{ marginTop: '0.5rem' }}>
          reason code: {textOr(code)}
        </div>
        {explanation.measurements.length === 0 ? (
          <div className="muted">The record carried no values under the code.</div>
        ) : (
          <ul className="note-list">
            {explanation.measurements.map((row) => (
              <li key={`${row.source}-${row.name}`}>
                <code>
                  {row.name} = {valueText(row.value)}
                  {row.unit === null || row.value === null ? '' : ` ${row.unit}`}
                </code>
                <span className="muted"> from {row.source === 'failure' ? 'the failure record' : 'a predicate that measured false'}</span>
              </li>
            ))}
          </ul>
        )}
      </details>
    </Panel>
  );
}
