// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// Every predicate with the number behind it: what was measured, what it was compared against, which host observation
// it was computed from, how old that record is, and what KIND of evidence it rests on. A predicate with no value is
// shown as not evaluated, which is not the same as false and is never drawn as one.
//
// FOUR STATES AND THE OLDER SHAPE, BOTH. A record can now say which of the four states it is in (not_evaluated,
// not_applicable, unavailable, evaluated) with the reason the payload carried and the rule that decided it. A record
// written before that field existed says none of it, and for those the older reading holds: a value is an evaluation,
// a null value was not evaluated. Both paths are rendered here and `cardStateOfPredicate` is what maps them, so the
// words are the same as on every other card. Only a record that says `unavailable` prints the word unavailable.
//
// A VERDICT AND THE FRESHNESS OF ITS EVIDENCE ARE TWO FACTS. The verdict was taken at a sequence number on evidence
// that existed then, and it does not change afterwards: a grip verification that measured true stays a recorded
// success even once it has stopped saying anything about what is in the gripper NOW. The age beside it belongs to the
// evidence, is measured inside one clock of one session of the source by `decisionAgeNs`, and comes back with the
// reason it could not be taken rather than with a number that looks current.
//
// TWO THINGS THIS SCREEN NEVER DOES. It never writes a retention score as a percentage or a confidence: the score is a
// comparison between two frames against a configured minimum, and a percent sign would turn it into a probability
// nobody computed. And it never merges the release ADMISSION with the observed PLACEMENT: the first is a bound checked
// against predicted pad-relative evidence before the gripper opened, the second is a measurement taken afterwards from
// the frozen perception at the low pose. They are different evidence and they stay on different rows.

import type { ReactNode } from 'react';
import { Fragment } from 'react';
import { nanosAsMillis, UNAVAILABLE } from '../model/format';
import { phaseOfState } from '../model/phase';
import { decisionAgeNs, predicateState, predicateStateWasRecorded } from '../model/predicate';
import type { PredicateView, ViewState } from '../model/types';
import type { StoreSnapshot } from '../store/store';
import { Badge, Tristate } from '../ui/Badge';
import { CardStatus, cardStateOfPredicate } from '../ui/CardState';
import { Panel } from '../ui/Panel';

const EVIDENCE_TONE: Record<string, 'ok' | 'info' | 'warn' | 'quiet'> = {
  frame: 'info',
  frame_pair: 'info',
  scalar: 'quiet',
  pad_measurement: 'ok',
  anchor: 'warn',
};

/**
 * What KIND of evidence a verdict rests on, for the predicates where confusing two kinds would be reading the screen
 * wrong. It is an explicit list of the controller's own predicate names and nothing is inferred for a name outside it:
 * a predicate this console has never been told about gets no note rather than a guessed one.
 */
const EVIDENCE_KIND: Record<string, string> = {
  already_placed:
    'measured before anything moved: how far inside the pad outline the cube already was. A run that stops here moves nothing.',
  object_secured:
    'a retention score between two frames taken at different heights, against a configured minimum. It is not a probability and not a percentage, and it says the cube was held when the score was taken, not that it is held now.',
  placement_plan: 'a bound checked before the cube was picked up: the margin a planned placement would leave, against the margin one needs.',
  arm_only_sufficient: 'a bound checked with the cube held: whether the arm alone can reach the planned placement.',
  release_margin: 'a bound checked before the gripper opened, from pad-relative evidence: the worst-case centre margin a release here would leave.',
  release_height: 'a bound checked before the gripper opened: how high above the pad the cube would be released from.',
  release_admitted: 'the admission decision itself, taken from release_margin and release_height before the gripper opened. It is not a measurement of where the cube ended up.',
  placed: 'measured after the release, from the frozen perception at the low pose. It is a different fact from the release admission above it.',
  clearance_target_met: 'measured after the release: how far the cube centre ended up from the nearest pad edge.',
  base_returned: 'measured after the return: how far the base ended up from where it started.',
  base_measured: 'measured from two pad frames around a timed pulse, which is how the base speed is established.',
};

/**
 * How old the evidence a verdict consumed is, measured inside ONE clock of ONE session of the source, against the
 * newest host sample this fold holds. The newest sample is the one the runtime last recorded a measured state from;
 * `decisionAgeNs` refuses the subtraction outright when the session or the clock domain is not the same one, and the
 * refusal is what the screen then prints.
 */
function EvidenceAge(props: { predicate: PredicateView; view: ViewState }): ReactNode {
  const newest = props.view.measured_state?.consumed_source_stamp ?? null;
  const answer = decisionAgeNs(props.predicate, newest);
  if (answer.age_ns === null) {
    return <span className="muted">evidence age: {answer.refusal}</span>;
  }
  return (
    <span className="muted" title="measured between the sample this verdict consumed and the newest sample of the same session of that source">
      evidence was {nanosAsMillis(answer.age_ns)} older than the newest sample of that source
    </span>
  );
}

/** The verdict, or the words for the one of the four states this record is in when it carries no verdict. */
function Verdict(props: { predicate: PredicateView }): ReactNode {
  const card = cardStateOfPredicate(props.predicate);
  if (card === null) return <Tristate value={props.predicate.value} />;
  return <CardStatus state={card.state} words={card.words} title={props.predicate.reason_code ?? undefined} />;
}

/** A measured value with its unit. A score carries no unit and is written as the number it is, never as a percentage. */
function valueWithUnit(value: number | null, unit: string | null): string {
  if (value === null) return UNAVAILABLE;
  return unit === null ? String(value) : `${String(value)} ${unit}`;
}

/**
 * WHAT A PANEL ABOUT A RUN'S RECORDS SAYS WHILE NONE OF THIS RUNTIME'S RUNS HAS REACHED THIS DISPLAY (redesign r4
 * verify-r2 must_fix 4): no snapshot has answered this page and the fold holds no run, frame or verdict. The empty
 * record's sentence ("No decision frame is pinned", "0 predicates") would be a claim about records never read. Null
 * once anything was read, in a replay, and whenever the fold holds a run.
 */
export function runRecordsNotRead(snapshot: Pick<StoreSnapshot, 'replay' | 'runtimeAnswered' | 'view' | 'hydrated' | 'sourceError' | 'handshake'>): string | null {
  if (snapshot.replay !== null || snapshot.runtimeAnswered) return null;
  const view = snapshot.view;
  if (view.run_started !== null || view.outcome.run_id !== null || view.decision_frame !== null || view.predicates.length > 0 || snapshot.hydrated !== null) return null;
  const why = snapshot.sourceError ?? snapshot.handshake.reason ?? 'no answer has arrived';
  return `Not read: no run of this runtime has reached this display (${why}).`;
}

export function PredicatesPanel(props: { view: ViewState; notRead?: string | null }): ReactNode {
  const view = props.view;
  const notRead = props.notRead ?? null;
  // Whether the cube is being carried right now, which is what makes an old retention verdict worth a sentence of
  // warning. It is read from the records and not from a list of state names: the grip was verified, the mission is in
  // the phase where the cube is carried or the base is driven, and no release has been recorded since.
  const secured = view.predicates.find((item) => item.predicate === 'object_secured') ?? null;
  const released = view.machine !== null && view.machine.history.some((item) => item.state === 'RELEASED' || item.state === 'PLACED');
  const phase = view.machine === null ? 'unknown' : phaseOfState(view.machine.state);
  const carrying = secured?.value === true && !released && (phase === 'place' || phase === 'return');

  return (
    <Panel
      title="verification"
      // The fold keeps the NEWEST verdict of each predicate, and the badge says so: it is not the count of predicate
      // records, which Evidence states beside it (verifier r2 defect 7).
      extra={<Badge tone="quiet">{notRead === null ? `${String(view.predicates.length)} predicates, newest verdict of each` : 'not read'}</Badge>}
      note={
        carrying ? (
          <>
            The cube is being carried. Retention was verified before it set off and has not been verified since: the age column is how old that
            verification is, and it is not proof that the cube is still held now.
          </>
        ) : (
          <>Holding is never read off the jaw angle. What is shown is the retention evidence and its age, or nothing.</>
        )
      }
      flush
    >
      {notRead !== null ? (
        <p className="muted" data-predicates-not-read="" style={{ padding: '0.75rem' }}>
          {notRead}
        </p>
      ) : view.predicates.length === 0 ? (
        <p className="muted" style={{ padding: '0.75rem' }}>
          No predicate has been recorded in this run.
        </p>
      ) : (
        <table className="table table--stacked">
          <thead>
            <tr>
              <th>predicate</th>
              <th>verdict</th>
              <th>measured</th>
              <th>threshold</th>
            </tr>
          </thead>
          <tbody>
            {view.predicates.map((predicate) => {
              const kind = EVIDENCE_KIND[predicate.predicate];
              const failed = predicate.value === false;
              return (
                <Fragment key={predicate.predicate}>
                  <tr>
                    <td>{predicate.predicate}</td>
                    <td>
                      <Verdict predicate={predicate} />
                    </td>
                    {/* A failed verdict is the one that has to carry its number: measured against threshold, in the
                        unit the record named, and the word unavailable where the record carried neither. */}
                    <td>{valueWithUnit(predicate.measured, predicate.unit)}</td>
                    <td>
                      {predicate.threshold === null ? (
                        UNAVAILABLE
                      ) : (
                        <>
                          {valueWithUnit(predicate.threshold, predicate.unit)}
                          {failed && predicate.measured !== null ? <span className="muted"> not met</span> : null}
                        </>
                      )}
                    </td>
                  </tr>
                  {/* The second row carries where the value came from and how old it is: never folded into the first. */}
                  <tr className="table__detail">
                    <td colSpan={4}>
                      <div className="row">
                        <span className="muted">verdict recorded at seq {String(predicate.seq)}</span>
                        <span className="muted">
                          source {predicate.consumed_source_stamp === null ? UNAVAILABLE : `host seq ${String(predicate.consumed_source_stamp.seq)}`}
                        </span>
                        <EvidenceAge predicate={predicate} view={view} />
                        {predicate.evidence.length === 0 ? (
                          <Badge tone="bad">no evidence recorded</Badge>
                        ) : (
                          predicate.evidence.map((piece, index) => (
                            <Badge key={`${predicate.predicate}-${String(index)}`} tone={EVIDENCE_TONE[piece.type] ?? 'quiet'}>
                              {piece.type}
                              {piece.scalar_name === null ? '' : ` ${piece.scalar_name}`}
                            </Badge>
                          ))
                        )}
                        {/* The record's own reason for a state that carries no verdict, which the contract requires
                            for not_applicable and unavailable. It is the payload's reason and not the envelope's. */}
                        {predicate.reason_code === null ? null : <Badge tone="quiet" code>{predicate.reason_code}</Badge>}
                        {predicate.rule_ref === null ? null : (
                          <Badge tone="quiet" code title="the rule that decided this verdict, as the record named it">
                            {predicate.rule_ref}
                          </Badge>
                        )}
                        {predicateStateWasRecorded(predicate) ? null : (
                          <span className="muted" title="this record was written before the four-state field existed">
                            read as {predicateState(predicate).replace('_', ' ')}
                          </span>
                        )}
                      </div>
                      {kind === undefined ? null : <div className="muted wrap">{kind}</div>}
                    </td>
                  </tr>
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
