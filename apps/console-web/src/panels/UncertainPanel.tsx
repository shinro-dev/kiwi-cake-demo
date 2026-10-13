// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE COMMAND WHOSE EXECUTION IS NOT KNOWN.
//
// This panel exists because the honest answer to "did that command run?" is sometimes "nobody knows", and a console
// that cannot say so will be believed when it says something else. Four rules shape it:
//
//  1. It states plainly that whether the command ran is NOT known, before anything else on it.
//  2. It shows the MACHINE EVIDENCE the daemon read for itself: the process table, the run's own files, the
//     observation store, the journal. That is what the decision rests on.
//  3. The operator's answer is offered as an ATTESTATION, in its own words: I am stating this; the console did not
//     verify it. It is recorded beside the evidence and never instead of it, and the RUNTIME decides whether it clears
//     the block. This screen never clears anything.
//  4. There is no control on this panel that sends the ambiguous command again. Not a retry, not a "try once more",
//     not anything that takes its id and submits it. While this panel is up, every motion control on the console is
//     off with the reason owner_unresolved.

import type { ReactNode } from 'react';
import { useState } from 'react';
import { textOr } from '../model/format';
import { reasonCodeLabel } from '../model/reason';
import {
  ATTESTATION_WORDING,
  classificationSentence,
  evidenceLines,
  findingSentence,
  isDescribable,
} from '../model/uncertain';
import type { StoreSnapshot } from '../store/store';
import type { ConsoleStore } from '../store/store';
import { useNowMs } from '../store/useConsole';
import { runtimeAnswerBlock } from './commandGuards';
import { Badge } from '../ui/Badge';
import { IconAlert, IconBlocked } from '../ui/Icons';
import { Panel } from '../ui/Panel';

export function UncertainPanel(props: { snapshot: StoreSnapshot; store: ConsoleStore }): ReactNode {
  // The LIVE block, not the replay fold's. A recording carries no live block, and the robot in the room does not stop
  // being in an unknown state because somebody opened a recording on the screen in front of it.
  const unresolved = props.snapshot.liveUnresolved;
  const [finding, setFinding] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [attested, setAttested] = useState(false);
  const nowMs = useNowMs();

  if (unresolved === null) return null;

  const describable = isDescribable(unresolved);
  const lines = evidenceLines(unresolved.evidence);
  const resolution = props.snapshot.lastResolution;
  const replaying = props.snapshot.replay !== null;
  // A STATEMENT IS A REQUEST TO THE RUNTIME: not offered while it is not answering (verdict 2026-09-27 must_fix 3).
  const runtimeOff = replaying ? null : runtimeAnswerBlock(props.snapshot, nowMs);
  const statementOff = finding === null || !attested || props.snapshot.resolving || runtimeOff !== null;

  return (
    <Panel
      title="a command whose execution is NOT known"
      className="panel--uncertain"
      state="blocked"
      stateWords="Every motion control is off"
      extra={
        <>
          <Badge tone="bad" strong>
            <IconAlert /> not known
          </Badge>
          <Badge tone="warn">
            <IconBlocked /> every motion control is off: {unresolved.block_reason_code}
          </Badge>
          {unresolved.classification === null ? null : <Badge tone="quiet">{String(unresolved.classification)}</Badge>}
        </>
      }
      note={
        <>
          Nothing on this panel resends anything. The runtime re-read the evidence itself; your answer is recorded as an
          attestation beside it, and the runtime is what decides whether commands are allowed again.
        </>
      }
    >
      <div className="banner banner--bad">
        <IconAlert /> Whether this command ran is <strong>NOT known</strong>. It was accepted and the runtime then lost
        track of what became of it. It has not been sent again and it never will be from this screen.
      </div>

      {describable ? null : (
        <div className="banner">
          <IconAlert /> The runtime reports a block and this console could not read the detail of it. The controls stay
          off. Treating an unreadable answer as no block would be the one mistake that matters here.
        </div>
      )}

      <table className="table" style={{ marginTop: '0.75rem' }}>
        <tbody>
          <tr>
            <td>command</td>
            <td>
              {textOr(unresolved.command_type)} <span className="muted">{textOr(unresolved.command_id)}</span>
            </td>
          </tr>
          <tr>
            <td>run</td>
            <td className="muted">{textOr(unresolved.run_id)}</td>
          </tr>
          <tr>
            <td>why the runtime does not know</td>
            <td className="wrap">{textOr(reasonCodeLabel(unresolved.reason_code))}</td>
          </tr>
          <tr>
            <td>what the evidence classifies as</td>
            <td className="wrap">
              {unresolved.classification === null ? (
                'the runtime did not classify it'
              ) : (
                <>
                  <code>{String(unresolved.classification)}</code>
                  {classificationSentence(String(unresolved.classification)) === null
                    ? null
                    : `: ${String(classificationSentence(String(unresolved.classification)))}`}
                </>
              )}
            </td>
          </tr>
          <tr>
            <td>commands the runtime is refusing</td>
            <td className="wrap">
              {unresolved.blocked_commands.length === 0 ? 'the runtime named none' : unresolved.blocked_commands.join(', ')}
            </td>
          </tr>
        </tbody>
      </table>

      <div style={{ marginTop: '0.75rem' }}>
        <div className="field__label">what the runtime checked, on the machine the controller runs on</div>
        {lines.length === 0 ? (
          <p className="muted">The answer carried no evidence object. Nothing is claimed about what was checked.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>check</th>
                <th>readable</th>
                <th>what it found</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => (
                <tr key={line.check}>
                  <td>{line.check}</td>
                  <td>
                    {line.readable === null ? (
                      <Badge tone="quiet">not stated</Badge>
                    ) : line.readable ? (
                      <Badge tone="ok">yes</Badge>
                    ) : (
                      <Badge tone="bad">no</Badge>
                    )}
                  </td>
                  <td className="wrap">{line.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {unresolved.attestations.length === 0 ? null : (
        <table className="table" style={{ marginTop: '0.75rem' }}>
          <thead>
            <tr>
              <th>statements already on the record</th>
              <th>classified as</th>
              <th>outcome</th>
            </tr>
          </thead>
          <tbody>
            {unresolved.attestations.map((attestation, index) => (
              <tr key={`${attestation.finding}-${String(index)}`}>
                <td className="wrap">
                  {findingSentence(attestation.finding)}
                  {attestation.note === null ? null : <span className="muted"> ({attestation.note})</span>}
                </td>
                <td className="muted">{textOr(attestation.classification)}</td>
                <td>
                  {attestation.refused !== null ? (
                    <Badge tone="bad">refused: {attestation.refused}</Badge>
                  ) : attestation.clears_block ? (
                    <Badge tone="ok">the runtime acted on it</Badge>
                  ) : (
                    <Badge tone="warn">recorded, the block stayed</Badge>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div style={{ marginTop: '0.75rem' }}>
        <div className="field__label">the question you are being asked</div>
        <p className="wrap">{textOr(unresolved.question)}</p>
      </div>

      {replaying ? (
        <div className="banner">
          <IconAlert /> This screen is folding a RECORDED run. A statement about the live runtime is not offered from a
          recording.
        </div>
      ) : (
        <div style={{ marginTop: '0.75rem' }}>
          <div className="field__label">your statement, after you have looked at the robot</div>
          {unresolved.findings.length === 0 ? (
            <p className="muted">
              The runtime named no finding it will accept, so none is offered. There is nothing here this console could
              send that the runtime would take.
            </p>
          ) : (
            unresolved.findings.map((item) => (
              <label className="check" key={item}>
                <input
                  type="radio"
                  name="uncertain-finding"
                  checked={finding === item}
                  onChange={() => {
                    setFinding(item);
                    setAttested(false);
                  }}
                />
                <span>
                  {findingSentence(item)} <span className="muted">({item})</span>
                </span>
              </label>
            ))
          )}

          <label className="check" style={{ marginTop: '0.5rem' }}>
            <span className="muted">a note for the record, optional</span>
          </label>
          <input
            className="number-input"
            style={{ width: '100%' }}
            type="text"
            maxLength={200}
            value={note}
            placeholder="what you saw at the robot"
            onChange={(event) => setNote(event.target.value)}
          />

          <label className="check" style={{ marginTop: '0.5rem' }}>
            <input type="checkbox" checked={attested} disabled={finding === null} onChange={(event) => setAttested(event.target.checked)} />
            <span>{ATTESTATION_WORDING}</span>
          </label>

          <div className="row" style={{ marginTop: '0.5rem' }}>
            <button
              type="button"
              className="button button--primary"
              data-control="resolve-uncertain"
              disabled={statementOff}
              aria-disabled={statementOff ? 'true' : 'false'}
              title={runtimeOff ?? undefined}
              onClick={() => {
                if (finding === null || runtimeOff !== null) return;
                void props.store.resolveUncertain(unresolved.command_id, finding, note === '' ? null : note);
              }}
            >
              {props.snapshot.resolving ? 'the runtime is reconciling' : 'state this and let the runtime reconcile'}
            </button>
            <span className="muted">This sends a statement. It does not send the command.</span>
          </div>
          {runtimeOff === null ? null : (
            <p className="action__off" data-control-reason="resolve-uncertain">
              Off: {runtimeOff}
            </p>
          )}
        </div>
      )}

      {resolution === null ? null : resolution.kind === 'unknown' ? (
        <div className="banner banner--bad" style={{ marginTop: '0.75rem' }}>
          <IconAlert /> The last statement did not get an answer this console could read: {resolution.detail}. Nothing is
          assumed to have changed, and the block is whatever the next snapshot says it is.
        </div>
      ) : (
        <div className={`banner ${resolution.cleared ? '' : 'banner--bad'}`} style={{ marginTop: '0.75rem' }}>
          <IconAlert />{' '}
          {resolution.cleared
            ? `The runtime reconciled this as ${String(resolution.classification ?? 'unstated')} and lifted the block (${textOr(resolution.cleared_by)}).`
            : `The runtime did NOT lift the block: ${textOr(resolution.reason_code)}. ${
                resolution.contradiction === null
                  ? 'Your statement is on the record either way.'
                  : `It found something that contradicts the statement: ${JSON.stringify(resolution.contradiction)}.`
              }`}
        </div>
      )}
    </Panel>
  );
}
