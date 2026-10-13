// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT THE IMPORTED DESIGN PUTS ONE CLICK AWAY: ONE RIGHT-HAND DRAWER, DIAGNOSTICS, NOW WITH FOUR FOCUSED VIEWS.
//
// The design has one drawer with one title and one body at a time. It carried five bodies: Run history, Recorded
// evidence, Diagnostics, an activation receipt (which became the mission drawer) and Field bindings. The operator
// rulings of 2026-09-23 replace that set:
//
//   FIELD BINDINGS IS GONE from the operator interface. It described the screen to the people building it.
//   DIAGNOSTICS IS GONE, split by the question each part answers. What connects to what, and how current each link
//   is, is CONNECTIONS. What a run, the controller or the robot's resident wrote is LOGS. What a run measured and the
//   frame it decided on is EVIDENCE. The mission, its targets with their own runs, its activations and every finished
//   run with its duration is HISTORY. Observe and Return arm moved to the main workspace, the same `Actions` component
//   with the same guards.
//
// ONE SCOPE FOR ALL FOUR, stated under the tab row: the robot, the ONE mission the runtime serves (there is no route
// that lists missions, so the selected mission is the current one) and, in Logs, the module. Identities and detailed
// counters are behind an expansion, so each view opens on what an operator acts on.
//
// THE DRAWER IS CALLED DIAGNOSTICS, and so is the header button that opens it (operator requirement: "the focused
// Diagnostics drawer"). It is the FOCUSED one: the four views above, and none of the old Diagnostics body's panels
// piled into one scroll. Field bindings has no entry point anywhere, by ruling.
//
// Everything here is read back from the runtime on every poll and nothing is kept in the browser, which is what lets a
// reload recover it without starting or submitting anything.

import type { CSSProperties, ReactNode } from 'react';
import { useState } from 'react';
import { modeMark } from '../model/modeMark';
import { textOr } from '../model/format';
import { durationWords, parseUtcMs, utcDayOf, utcStamp } from '../model/utcTime';
import type { UtcStamp } from '../model/utcTime';
import { missionEndTime } from '../model/missionTimeline';
import { currencyContext, missionCurrency, missionNewestTime, recordCurrency, runCurrency, sessionWords } from '../model/currency';
import type { CurrencyContext } from '../model/currency';
import { earlierSignatureLines } from '../model/cakeStory';
import { missionEnded, missionVerdict, runVerdictOf, runWordFor } from '../model/verdict';
import { stopCause } from '../model/stopCause';
import { selectedRun, viewForSelectedRun } from '../model/selectedRun';
import { missionSteps, nextStep } from '../model/nextSteps';
import { DecisionFramePanel, noPictureWords } from '../panels/DecisionFramePanel';
import { EngineeringView } from '../panels/EngineeringView';
import { FailurePanel } from '../panels/FailurePanel';
import { LogConsole } from '../panels/LogConsole';
import { PredicatesPanel, runRecordsNotRead } from '../panels/PredicatesPanel';
import { UncertainPanel } from '../panels/UncertainPanel';
import { ResultPanel, TelemetryPanel } from '../panels/Workspace';
import { commandScope } from '../model/command';
import type { CommandScope } from '../model/command';
import { explainFailure } from '../model/failure';
import { missionExecution, missionPlaced, targetCapabilityWords, targetStateWords } from '../model/missionView';
import type { MissionRecord } from '../model/missionRecord';
import { hostInstanceWords, motionPermissionView, recoveryView, robotLink } from '../model/robotStatus';
import type { RobotLink, RobotStatus } from '../model/robotStatus';
import { previewView } from '../model/sources';
import type { ScreenMode } from '../panels/StatusStrip';
import type { ConsoleStore, StoreSnapshot } from '../store/store';
import type { OutcomeView } from '../model/types';
import { SNAPSHOT_POLL_MS } from '../store/store';
import { SNAPSHOT_CURRENT_WITHIN_MS } from '../model/arrivalSince';
import { useNowMs } from '../store/useConsole';
import { ActivationEvidence } from './ActivationEvidence';
import { CakeTab } from './CakeTab';
import { cakeModel } from './cake';
import { RUNTIME_DETAIL_LABEL, activationCard } from '../model/activationCard';
import { TONE_COLOUR } from './TopBar';
import type { CakeModel } from './cake';
import { lastRunLine, RUN_MODE_MARK, runDuration, runMode, runSummary } from './runSummary';
import { timeline } from './timeline';
import { C, INK, SURFACE, TONE_DOT, TONE_INK } from './theme';

export type DrawerId = 'history' | 'logs' | 'evidence' | 'connections' | 'cake';

export const DRAWER_ORDER: DrawerId[] = ['history', 'logs', 'evidence', 'connections', 'cake'];

/** The drawer's own name, on its title and on the header button that opens it. */
export const DRAWER_NAME = 'Diagnostics';

export const DRAWER_TITLE: Record<DrawerId, string> = {
  history: 'History',
  logs: 'Logs',
  evidence: 'Evidence',
  connections: 'Connections',
  cake: 'Cake',
};

// Sentence case, as the demo screen writes its headings (spec section 1, browser D9): no label here is transformed.
const LABEL: CSSProperties = {
  fontSize: 12,
  fontWeight: 600,
  color: 'var(--color-muted-foreground)',
};

const MONO: CSSProperties = { fontFamily: 'var(--font-mono), monospace', fontSize: 12, color: 'var(--color-muted-foreground)' };

const PLAIN: CSSProperties = {
  font: '500 12px var(--font-sans), sans-serif',
  whiteSpace: 'nowrap',
  color: INK.quiet,
  background: 'transparent',
  border: '1px solid var(--color-border)',
  borderRadius: 6,
  padding: '3px 9px',
  cursor: 'pointer',
};

function Mark(props: { text: string; tone: string }): ReactNode {
  return (
    <span
      style={{
        fontSize: 10, fontWeight: 700, letterSpacing: 0.5, padding: '1px 6px', borderRadius: 5, whiteSpace: 'nowrap',
        color: props.tone, border: `1px solid ${props.tone}`,
      }}
    >
      {props.text}
    </span>
  );
}

function modeTone(mark: string): string {
  return mark === 'Live' ? C.ok : mark === 'Rehearsal' ? C.warn : mark === 'Recording' ? C.accent : C.mute;
}

/** One labelled value. A value nobody reported is said to be absent, never blanked and never a zero. */
function Field(props: { label: string; value: string | number | boolean | null; mono?: boolean; title?: string }): ReactNode {
  const absent = props.value === null;
  const shown = absent ? 'not reported' : typeof props.value === 'boolean' ? (props.value ? 'yes' : 'no') : String(props.value);
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '3px 0' }}>
      <span style={{ fontSize: 12, color: 'var(--color-muted-foreground)', minWidth: 170, flex: 'none' }}>{props.label}</span>
      <span
        title={props.title}
        style={{
          fontSize: 12,
          fontFamily: props.mono === true && !absent ? 'var(--font-mono), monospace' : undefined,
          fontStyle: absent ? 'italic' : undefined,
          color: absent ? 'var(--color-muted-foreground)' : INK.body,
          wordBreak: 'break-all',
        }}
      >
        {shown}
      </span>
    </div>
  );
}

/** Identities and detailed counters live behind this, so a view opens on what an operator acts on. */
function More(props: { label: string; children: ReactNode }): ReactNode {
  return (
    <details style={{ marginTop: 6 }}>
      <summary style={{ fontSize: 12, color: INK.quiet, cursor: 'pointer' }}>{props.label}</summary>
      <div style={{ padding: '6px 0 2px 12px' }}>{props.children}</div>
    </details>
  );
}

/**
 * THE RETAINED-READING MARK, IN THE DEMO SCREEN'S WORDS AND COLOUR: "Not read since HH:MM:SSZ", a fixed UTC time and
 * never an age (spec 5.4, 6.8). It flips on once, when the reading stops being current, and then does not move.
 */
function NotReadSince(props: { at: UtcStamp | null; title?: string }): ReactNode {
  return (
    <span data-stale-marker="" data-t={props.at?.iso} title={props.title ?? props.at?.title} style={{ fontSize: 12, color: C.warn, whiteSpace: 'nowrap' }}>
      {props.at === null ? 'Not read since: the time is not recorded' : `Not read since ${props.at.text}`}
    </span>
  );
}

/** A time this page read something, stamped as the screen prints a recorded time. */
function readStamp(ms: number | null, day: string | null): UtcStamp | null {
  return ms === null ? null : utcStamp(new Date(ms).toISOString(), day);
}

function Section(props: { title: string; children: ReactNode; aside?: ReactNode }): ReactNode {
  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 5 }}>
        <span style={LABEL}>{props.title}</span>
        {props.aside}
      </div>
      {props.children}
    </section>
  );
}

function scopeOf(snapshot: StoreSnapshot): CommandScope {
  return commandScope(snapshot.view, (runId) => {
    const history = snapshot.history;
    if (history === null || !history.ok) return null;
    return history.entries.find((item) => item.run_id === runId)?.command_type ?? null;
  });
}

/* ---- History ------------------------------------------------------------------------------------------------ */

// The same four words as the top bar's mode mark (rule 5.2): a physical mission is a Live one.
const EXECUTION_MARK: Record<ReturnType<typeof missionExecution>, string> = {
  physical: 'Live',
  rehearsal: 'Rehearsal',
  unstated: 'Mode not recorded',
};

/**
 * THE MISSION, ITS TARGETS WITH THEIR OWN RUNS, ITS ACTIVATIONS, AND EVERY FINISHED RUN WITH ITS DURATION.
 *
 * A run is shown under the target the mission recorded it against and under no other, and a run in no target's list is
 * shown apart, as not in the mission. Each run carries its own data mode, so a rehearsal's run is marked REHEARSAL and
 * is never counted or worded as a placement. The mission record has no finish time: its end is the last run's finish, as
 * the mission card derives and labels it (rule 6.7).
 */
/** The previous run, in its spec 5.1 word and a UTC time, the token and the ISO time only in titles (browser r2 N2). */
function PreviousRunLine(props: { previous: OutcomeView; snapshot: StoreSnapshot; day: string | null }): ReactNode {
  const { previous } = props;
  const word = runWordFor({
    events: previous.run_id === null ? undefined : props.snapshot.missionRunEvents[previous.run_id],
    view: { run_started: null, stop: { requested: null } },
    run_id: previous.run_id,
    outcome: previous.reported,
    reason_code: previous.reason_code,
    placement: true,
  });
  const at = utcStamp(previous.finished_wall_utc, props.day);
  return (
    <div style={{ fontSize: 12, color: 'var(--color-muted-foreground)' }} title={previous.reported === null ? undefined : `run.finished outcome ${previous.reported}`}>
      Previous run: {word === null ? 'its word is not read yet' : word.word}
      {at === null ? '' : ', finished '}
      {at === null ? null : (
        <span data-t={at.iso} title={at.title}>
          {at.text}
        </span>
      )}
      . It is not this run&apos;s result.
    </div>
  );
}

function HistoryBody(props: { snapshot: StoreSnapshot; store: ConsoleStore }): ReactNode {
  const { snapshot, store } = props;
  const mission = snapshot.mission;
  const history = snapshot.history;
  const entries = history !== null && history.ok ? history.entries : [];
  const inMission = new Set(mission === null ? [] : mission.targets.flatMap((target) => target.runs));
  // NEWEST FIRST (spec rule 6.6): by the recorded finish, the runtime's order reversed where none is recorded.
  const others = entries
    .map((entry, index) => ({ entry, index, ms: parseUtcMs(entry.finished_wall_utc) }))
    .filter((item) => !inMission.has(item.entry.run_id))
    .sort((a, b) => (b.ms ?? Number.NEGATIVE_INFINITY) - (a.ms ?? Number.NEGATIVE_INFINITY) || b.index - a.index)
    .map((item) => item.entry);
  const summary = runSummary(snapshot, scopeOf(snapshot), mission);
  const earlierMissions = snapshot.missionsRead.filter((item) => mission === null || item.mission_id !== mission.mission_id).reverse();
  // EVERY ITEM HERE CARRIES ITS FULL DATE AND ITS SESSION (spec section 12): History is where an earlier runtime session's
  // or an earlier day's records are, so none of its times leaves the date out.
  const context = currencyContext(snapshot.view.runtime_session, Date.now());
  const earlierSignature = earlierSignatureLines({ signature: snapshot.signatureCheck, runtimeSession: snapshot.view.runtime_session, robot: snapshot.robot });
  const lines = timeline(snapshot.view.recent_events, utcDayOf(mission?.started_wall_utc ?? null) ?? new Date().toISOString().slice(0, 10)).reverse();

  const runRow = (runId: string): ReactNode => {
    const entry = entries.find((item) => item.run_id === runId) ?? null;
    const mode = runMode(entry?.data_mode);
    const duration = entry === null ? null : runDuration(entry);
    const finished = utcStamp(entry?.finished_wall_utc ?? null, null);
    const runNow = entry === null ? null : runCurrency(runId, { active: false, history: entries, times: [] }, context);
    // THE RUN'S WORD, the one the mission card and the timeline give it (`runVerdictOf`, spec 5.1), from the run's own
    // events. Until they are read there is no word, and the row says so rather than guessing one from the list (V13).
    const verdict = runVerdictOf(snapshot.missionRunEvents[runId] ?? []);
    const word =
      verdict !== null ? (
        <span style={{ fontSize: 13, fontWeight: 600, color: TONE_COLOUR[verdict.tone] }}>{verdict.word}</span>
      ) : (
        // NO WORD, AND THE LIST'S TOKEN ONLY IN A TITLE (spec 5.1; browser r2 N2): the token is not the run's word.
        <span
          style={{ fontSize: 12, color: INK.quiet }}
          title={entry === null ? undefined : entry.outcome === null ? 'the finished-run list gives no outcome' : `the finished-run list gives the outcome ${entry.outcome}`}
        >
          {inMission.has(runId)
            ? "word not read yet: the run's own records have not been read"
            : "word not read: this page reads the records of the mission's runs only"}
        </span>
      );
    return (
      <div key={runId} data-run-row={runId} data-run-verdict={verdict?.id ?? 'none'} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 0', borderTop: '1px solid var(--color-border)', flexWrap: 'wrap' }}>
        <span style={MONO}>{runId}</span>
        {entry === null ? (
          <>
            {verdict === null ? null : word}
            <span style={{ fontSize: 12, color: INK.quiet }}>not in the finished-run list{history === null ? ' (not read yet)' : ''}</span>
          </>
        ) : (
          <>
            <Mark text={RUN_MODE_MARK[mode]} tone={modeTone(RUN_MODE_MARK[mode])} />
            {word}
            <span style={{ fontSize: 12, color: 'var(--color-muted-foreground)' }}>
              {finished === null ? 'finish not recorded, ' : (
                <>
                  finished{' '}
                  <span data-t={finished.iso} title={finished.title}>
                    {finished.text}
                  </span>
                  {', '}
                </>
              )}
              {runNow === null ? '' : `${sessionWords(runNow, context)}, `}
              {duration === null ? 'duration not recorded' : `took ${duration}`}
              {entry.evidence_complete === false ? `, evidence incomplete (${String(entry.evidence_gaps ?? '?')} gap(s))` : ''}
            </span>
            <div style={{ flex: 1 }} />
            <button type="button" onClick={() => void store.openReplay(runId)} style={PLAIN}>
              Open recording
            </button>
          </>
        )}
      </div>
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <Section title="The mission">
        {mission === null ? (
          <p style={{ margin: 0, fontSize: 13, color: INK.quiet, lineHeight: 1.5 }}>
            {snapshot.missionError === null
              ? 'The mission has not been read yet.'
              : snapshot.missionError === 'no_mission_recorded'
                ? `This runtime reports no mission: ${snapshot.missionError}. That is not the same as a mission with nothing in it.`
                : `The mission has not been read: ${snapshot.missionError}.`}
          </p>
        ) : (
          <MissionSummary mission={mission} snapshot={snapshot} context={context} served />
        )}
      </Section>

      {/* THE MISSIONS THIS PAGE READ BEFORE THE ONE SERVED NOW (verify-r1 M1): after Start the runtime serves the new
          mission, and the earlier one stays here, dated and placed, instead of leaving the page altogether. */}
      {earlierMissions.length === 0 ? null : (
        <Section title="Earlier missions this page read, newest first">
          {earlierMissions.map((item) => (
            <div key={item.mission_id} data-history-earlier-mission={item.mission_id} style={{ borderTop: '1px solid var(--color-border)', padding: '10px 0' }}>
              <MissionSummary mission={item} snapshot={snapshot} context={context} served={false} />
            </div>
          ))}
        </Section>
      )}

      {mission === null ? null : (
        <Section title="Targets, each with its own runs, newest first">
          {mission.targets.length === 0 ? (
            <p style={{ margin: 0, fontSize: 13, color: INK.quiet }}>This mission record carries no target.</p>
          ) : (
            mission.targets.map((target) => (
              <div key={target.target_id} style={{ borderTop: '1px solid var(--color-border)', padding: '10px 0' }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>{target.label}</span>
                  <span style={{ fontSize: 12, color: INK.soft }}>{targetStateWords(target.state, mission)}</span>
                  {target.target_id === mission.selected_target ? (
                    <span style={{ fontSize: 12, fontWeight: 600, color: C.accent }}>Selected</span>
                  ) : null}
                </div>
                <div style={{ fontSize: 12, color: 'var(--color-muted-foreground)', marginTop: 3, lineHeight: 1.45 }}>
                  {/* NULL IS NOT A NO, and the capability flag is a boolean the runtime wrote (finding F1), or the word
                      `auto`, whose answer is the robot's measurement (`model/missionView.ts`, targetCapabilityWords). */}
                  reachable: {target.reachable === null ? 'not determined' : target.reachable ? 'yes' : 'no'}
                  {`, ${targetCapabilityWords(target, snapshot.robot?.capability.name ?? null, mission)}`}
                </div>
                <div style={{ marginTop: 6 }}>
                  {target.runs.length === 0 ? (
                    <div style={{ fontSize: 12, color: 'var(--color-muted-foreground)' }}>no run has been recorded against this target</div>
                  ) : (
                    // NEWEST FIRST (spec rule 6.6): the mission appends each run to its target's list, so the list reversed.
                    [...target.runs].reverse().map(runRow)
                  )}
                </div>
              </div>
            ))
          )}
        </Section>
      )}

      {summary === null ? null : (
        <Section title="The last run's outcome">
          {/* THE FINISHED RUN'S ONE LINE, moved here from the action card by the design review's ruling: repeated
              completion statements and outcomes are History's. It carries the run's mode mark, what it is established to
              have done and its figures, and a rehearsal's line says so and never says placed. */}
          <div
            data-last-run=""
            title={summary.token === null ? undefined : `run.finished outcome ${summary.token}`}
            style={{ fontSize: 14, fontWeight: 600, color: INK.bright, lineHeight: 1.45, textWrap: 'pretty' }}
          >
            {lastRunLine(summary)}
          </div>
          <div style={{ fontSize: 12, color: INK.quiet, lineHeight: 1.45 }}>
            run {textOr(summary.runId)}
            {summary.reason === null ? '' : `, why: ${summary.reason}`}
            {summary.target === null ? '' : `, carried for ${summary.target.label}`}
          </div>
          {summary.recorded === null ? null : (
            <div style={{ fontSize: 12, color: 'var(--color-muted-foreground)' }} title={`run.finished outcome ${summary.recorded}`}>
              The run&apos;s own record gives it the word {summary.word === null ? 'that is not read yet' : summary.word.word}; the line above is what the run&apos;s
              retained predicates measured.
            </div>
          )}
          {summary.previous === null ? null : (
            <PreviousRunLine previous={summary.previous} snapshot={snapshot} day={utcDayOf(mission?.started_wall_utc ?? null)} />
          )}
        </Section>
      )}

      {mission === null ? null : (
        <Section title="Activations, in the order the runtime recorded them">
          <p style={{ margin: '0 0 6px', fontSize: 12, color: INK.quiet, lineHeight: 1.45 }}>
            A submitted activation is pending until the slot is read back. A request that did not land judged nothing. An aborted
            transaction activated nothing. Only a slot read back means the capability is there. The full records are in Evidence.
          </p>
          {mission.activations.length === 0 ? (
            <p style={{ margin: 0, fontSize: 13, color: INK.quiet }}>This mission has recorded no activation.</p>
          ) : (
            mission.activations.map((activation) => {
              // THE DEMO SCREEN'S CARD WORDS AND COLOUR (spec 5.1): a refusal with no expectation served is muted, never red.
              // FULL DATES IN HISTORY (spec section 12): no reference day.
              const card = activationCard(activation, snapshot.missionCandidates, null);
              const tone = TONE_COLOUR[card.verdict.tone];
              return (
                <div key={activation.operation_uuid} data-activation-row={activation.operation_uuid} data-verdict={card.verdict.id} style={{ borderTop: '1px solid var(--color-border)', padding: '8px 0' }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
                    <span style={{ width: 6, height: 6, borderRadius: '50%', flex: 'none', background: tone }} />
                    <span style={{ fontSize: 13, fontWeight: 600, color: tone }}>{card.headline}</span>
                  </div>
                  <div style={{ ...MONO, marginTop: 3 }} data-t={card.stamp?.iso} title={card.stamp?.title}>{card.line}</div>
                  {card.answer === null ? null : <div style={{ fontSize: 12, color: INK.quiet, marginTop: 3, lineHeight: 1.45 }}>{card.answer}</div>}
                  {card.note === null ? null : <div style={{ fontSize: 12, color: INK.quiet, marginTop: 3, lineHeight: 1.45 }}>{card.note}</div>}
                </div>
              );
            })
          )}
        </Section>
      )}

      <Section title={mission === null ? 'Finished runs, held by the runtime, newest first' : 'Finished runs not in this mission, newest first'}>
        {history === null ? (
          <p style={{ margin: 0, fontSize: 13, color: INK.quiet }}>The finished-run list has not been read yet.</p>
        ) : !history.ok ? (
          <p style={{ margin: 0, fontSize: 13, color: INK.quiet }}>
            The runtime did not serve the finished-run list: {textOr(history.reason_code)}. This is not the same as there being none.
          </p>
        ) : others.length === 0 ? (
          <p style={{ margin: 0, fontSize: 13, color: INK.quiet }}>
            {entries.length === 0 ? 'No run has finished in this runtime session.' : 'Every finished run is listed under its target above.'}
          </p>
        ) : (
          others.map((entry) => runRow(entry.run_id))
        )}
      </Section>

      {earlierSignature === null ? null : (
        <Section title="Signature check, from an earlier runtime session">
          {earlierSignature.map((line) => (
            <p key={line} data-history-signature="" style={{ margin: 0, fontSize: 13, color: INK.quiet, lineHeight: 1.5 }}>
              {line}
            </p>
          ))}
        </Section>
      )}

      <Section title="The record this tab holds">
        <p style={{ margin: '0 0 6px', fontSize: 12, color: INK.quiet, lineHeight: 1.45 }}>
          A bounded tail of the ordered stream, newest first. It is what this tab folded and not a complete log of the run.
        </p>
        {lines.length === 0 ? (
          <p style={{ margin: 0, fontSize: 13, color: INK.quiet }}>No record has reached this tab.</p>
        ) : (
          lines.map((line) => (
            <div key={line.seq} style={{ display: 'flex', gap: 12, padding: '7px 0', borderTop: '1px solid var(--color-border)' }}>
              <span style={{ ...MONO, width: 76, flex: 'none' }} data-t={line.iso ?? undefined} title={line.iso ?? undefined}>
                {line.t}
              </span>
              <span style={{ width: 6, height: 6, borderRadius: '50%', marginTop: 6, flex: 'none', background: TONE_DOT[line.tone] }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, lineHeight: 1.45, color: TONE_INK[line.tone], overflowWrap: 'anywhere' }}>{line.text}</div>
                <div style={{ fontSize: 12, color: 'var(--color-muted-foreground)', marginTop: 2 }}>
                  {line.source}
                  {line.observation_id === null ? '' : ` · observation ${line.observation_id}`}
                </div>
              </div>
            </div>
          ))
        )}
      </Section>
    </div>
  );
}

function MissionSummary(props: { mission: MissionRecord; snapshot: StoreSnapshot; context: CurrencyContext; served: boolean }): ReactNode {
  const { mission, snapshot } = props;
  const entries = snapshot.history !== null && snapshot.history.ok ? snapshot.history.entries : [];
  // WHERE THE MISSION SITS, derived from its times: the record does not name its runtime session (spec section 12).
  // Its placement is read from its newest record time; whether the main screen shows it is `missionCurrency`'s (a held or
  // watched mission stays there whatever its age).
  const placed = recordCurrency(missionNewestTime(mission, entries), props.context);
  const onMain = missionCurrency(mission, entries, props.context, snapshot.missionsSeenOpen).current;
  const count = missionPlaced(mission);
  const mark = EXECUTION_MARK[missionExecution(mission)];
  // The last read's failure is said beside the mission the runtime serves; an earlier mission is not read any more.
  const retained = props.served && snapshot.missionError !== null && snapshot.missionReadAtMs !== null;
  const verdict = missionVerdict({ state: mission.state, waiting_reason: mission.waiting_reason, stop_requested: mission.stop_requested ?? false });
  // FULL DATES (spec section 12): History prints every time with its day.
  const day = null;
  const historyRead = snapshot.history !== null && snapshot.history.ok;
  const end = missionEndTime(mission, snapshot.history !== null && snapshot.history.ok ? snapshot.history.entries : []);
  const started = utcStamp(mission.started_wall_utc, day);
  const endStamp = end === null ? null : utcStamp(end.iso, day);
  const duration = end === null ? null : durationWords(mission.started_wall_utc, end.iso);
  return (
    <div style={{ fontSize: 13, color: INK.body, lineHeight: 1.5 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        {/* THE MISSION CARD'S WORD AND COLOUR (spec 5.1): "Mission failed", never "<id> is failed". */}
        <span data-mission-verdict={verdict.id} style={{ fontSize: 14, fontWeight: 600, color: TONE_COLOUR[verdict.tone] }}>
          Mission {verdict.word.charAt(0).toLowerCase()}{verdict.word.slice(1)}
        </span>
        <span style={MONO}>{mission.mission_id}</span>
        <Mark text={mark} tone={modeTone(mark)} />
        {retained ? <NotReadSince at={readStamp(snapshot.missionReadAtMs, day)} title={`the last read failed: ${snapshot.missionError ?? ''}`} /> : null}
      </div>
      <div data-history-session={placed.reason} data-on-main-screen={onMain ? 'true' : 'false'} style={{ color: INK.quiet, marginTop: 3 }}>
        Recorded in {sessionWords(placed, props.context)}
        {onMain ? '. It is on the main screen.' : '. It is not on the main screen.'}
      </div>
      <div style={{ color: INK.quiet, marginTop: 3 }}>{count.words}.</div>
      {mission.detail === null ? null : <div style={{ color: INK.quiet, marginTop: 3 }}>{mission.detail}</div>}
      {/* THE MISSION CARD'S TIMES (spec 5.4, 6.7): the start as recorded and the end derived from the last run's finish,
          labelled in its title; "not recorded" only when nothing records it, "not read" when the list was not read. */}
      <div style={{ fontSize: 12, color: 'var(--color-muted-foreground)', marginTop: 4, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
        {started === null ? (
          <span>start time not recorded</span>
        ) : (
          <span data-started="" data-t={started.iso} title={started.title}>
            Started {started.text}
          </span>
        )}
        {!missionEnded(mission.state) ? null : end === null || endStamp === null ? (
          <span>
            {historyRead
              ? "end time not recorded: neither the mission record nor the finished-run list carries the last run's finish"
              : 'end time not read: the finished-run list has not been read'}
          </span>
        ) : (
          <span data-ended="" data-t={endStamp.iso} title={end.title}>
            Ended {endStamp.text}
          </span>
        )}
        {duration === null ? null : <span>{duration}</span>}
      </div>
      <More label="Record details">
        <Field label="Record version" value={mission.mission_record_version} />
        <Field label="Data mode" value={mission.data_mode} />
        <Field label="Performs motion" value={mission.performs_motion} />
        <Field label="Execution" value={mission.execution_label} />
        <Field label="Waiting reason" value={mission.waiting_reason} />
        <Field label="Selected target" value={mission.selected_target} mono />
      </More>
    </div>
  );
}

/* ---- Logs --------------------------------------------------------------------------------------------------- */

type LogModule = 'controller' | 'cake' | 'capability';

/**
 * WHAT WAS WRITTEN, BY WHICH MODULE. The controller's own output for a named run is real (`LogConsole`), and the
 * robot resident's update and supervisor records are real (`/api/robot` updates, poll traffic excluded). A module with
 * no source is shown and disabled with the words "not served by the runtime", never as an empty log and never as a
 * promise ("to be provided") nobody has made.
 */
function LogsBody(props: { snapshot: StoreSnapshot; store: ConsoleStore; module: LogModule; onModule: (module: LogModule) => void }): ReactNode {
  const robot = props.snapshot.robot;
  const chip = (id: LogModule, label: string, disabled: string | null): ReactNode => (
    <button
      key={id}
      type="button"
      disabled={disabled !== null}
      title={disabled ?? undefined}
      onClick={() => props.onModule(id)}
      style={{
        ...PLAIN,
        color: props.module === id ? INK.bright : disabled !== null ? INK.faint : INK.quiet,
        borderColor: props.module === id ? C.accent : 'var(--color-border)',
        cursor: disabled !== null ? 'not-allowed' : 'pointer',
      }}
    >
      {label}
      {disabled === null ? '' : ' (not served by the runtime)'}
    </button>
  );
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {chip('controller', 'Controller', null)}
        {chip('cake', 'Robot Cake resident', null)}
        {chip('capability', robot?.capability.name ?? 'Capability process', 'the capability process output is not served by the runtime')}
      </div>
      {props.module === 'controller' ? <LogConsole snapshot={props.snapshot} store={props.store} /> : null}
      {props.module === 'cake' ? <CakeUpdates status={robot} error={props.snapshot.robotError} day={utcDayOf(props.snapshot.mission?.started_wall_utc ?? null)} /> : null}
    </div>
  );
}

function CakeUpdates(props: { status: RobotStatus | null; error: string | null; day: string | null }): ReactNode {
  const status = props.status;
  if (status === null || !status.configured) {
    return (
      <p style={{ margin: 0, fontSize: 13, color: INK.quiet }}>
        {status === null ? `No robot reading: ${props.error ?? 'not read yet'}.` : 'No robot is configured on this runtime, so there is no robot resident to read.'}
      </p>
    );
  }
  const updates = status.updates;
  return (
    <Section
      title="Update and supervisor records from the robot's flight ring"
      aside={updates.stale ? <NotReadSince at={utcStamp(updates.observed?.wall_utc ?? null, props.day)} /> : null}
    >
      <p style={{ margin: '0 0 6px', fontSize: 12, color: INK.quiet, lineHeight: 1.45 }}>
        Transaction and supervisor lifecycle records only, oldest first. This runtime&apos;s own polling writes admin records too; those
        are counted ({String(updates.excluded_admin_records ?? 'not reported')}) and never listed, so polling never reads as module
        activity.{updates.dropped !== null && updates.dropped > 0 ? ` ${String(updates.dropped)} older record(s) were dropped from the list.` : ''}
        {` Other records outside the listed codes: ${updates.other_records === null ? 'not reported' : String(updates.other_records)}, counted and not listed.`}
      </p>
      {updates.reason_code === null ? null : <p style={{ margin: 0, fontSize: 12, color: C.warn }}>The listing reported: {updates.reason_code}</p>}
      {updates.items.length === 0 ? (
        <p style={{ margin: 0, fontSize: 13, color: INK.quiet }}>No update or supervisor record is in the flight ring.</p>
      ) : (
        updates.items.map((row, index) => (
          <div key={`${String(row.sequence)}-${String(index)}`} style={{ display: 'flex', gap: 10, padding: '6px 0', borderTop: '1px solid var(--color-border)', flexWrap: 'wrap' }}>
            <span style={{ ...MONO, width: 60 }}>{row.sequence === null ? '?' : String(row.sequence)}</span>
            <span style={{ fontSize: 12, color: row.kind === 'supervisor' ? C.wait : C.accent }}>{row.kind}</span>
            <span style={{ fontSize: 13, color: INK.soft }}>{row.name}</span>
            <span style={MONO}>
              {row.event} arg0 {String(row.arg0 ?? '-')} arg1 {String(row.arg1 ?? '-')} epoch {String(row.epoch ?? '-')}
            </span>
          </div>
        ))
      )}
    </Section>
  );
}

/* ---- Evidence ----------------------------------------------------------------------------------------------- */

/**
 * WHAT A RUN MEASURED, THE FRAME IT DECIDED ON, AND EVERY ACTIVATION RECORD WHOLE.
 *
 * WHOLE means with the slot read-back and the transaction, not only the identities: the operator's ruling is that an
 * activation's transaction and slot read-back are retained, and the Cake panel shows only the newest one.
 *
 * A frame is always labelled as recorded, never live, and as generated when the runtime is a rehearsal's: a simulation
 * runtime serves frames the fake controller drew, and nothing but this label tells them apart from a camera's.
 */
function EvidenceBody(props: { snapshot: StoreSnapshot; store: ConsoleStore; mode: ScreenMode }): ReactNode {
  const { store } = props;
  // THE RUN'S OWN RECORDS: the view the demo screen reads for the run it names (`viewForSelectedRun`), so a page opened
  // after the run reads the run's hydrated fold and never the live fold, which holds none of its records (audit C4).
  const live = props.snapshot.view;
  const selected = selectedRun(live, props.snapshot.session.runtime_replaced);
  const folded = viewForSelectedRun(live, selected, props.snapshot.hydrated);
  // A READ-BACK FOLD STARTS FROM THE INITIAL STATE, whose data mode is not the runtime's: every part of this tab that
  // falls back to the view's mode (the result section when the history entry is not read) reads the live snapshot's.
  const view = folded === live ? live : { ...folded, data_mode: live.data_mode };
  const snapshot = folded === live ? props.snapshot : { ...props.snapshot, view };
  const scope = scopeOf(snapshot);
  const failure = explainFailure(view, scope);
  const mission = snapshot.mission;
  // THE RUN'S DATA MODE, from the run's own history entry, else from the live snapshot (the screen's mode): never from a
  // read-back fold, which the store builds from the initial state and so never carries the runtime's mode (verifier r2
  // defect 1). A frame is called generated only when that record says the run was a rehearsal's.
  const runId = view.outcome.run_id ?? selected.run_id;
  const history = props.snapshot.history;
  const entryMode = history !== null && history.ok ? (history.entries.find((entry) => entry.run_id === runId)?.data_mode ?? null) : null;
  const generated = (entryMode ?? (props.mode === 'replay' ? null : props.mode)) === 'synthetic';
  const summary = runSummary(snapshot, scope, mission);
  // NOTHING OF THIS RUNTIME'S RUNS WAS READ (verify-r2 must_fix 4): the empty record's sentences are not said.
  const notRead = runRecordsNotRead(props.snapshot);
  return (
    <div className="diagnostics__body">
      <section className="rail__card" data-evidence-frame="">
        <h3 className="rail__title">Decision frame</h3>
        {view.decision_frame === null || noPictureWords(view.decision_frame.capture) !== null ? null : (
          <p className="rail__note" style={{ color: generated ? C.warn : undefined, fontWeight: generated ? 600 : undefined }}>
            {generated ? 'Generated by the rehearsal, not a camera image. Recorded frame, not live.' : 'Recorded frame, not live.'}
          </p>
        )}
        {view.decision_frame === null ? (
          <p className="rail__absent" data-evidence-not-read={notRead === null ? undefined : ''}>
            {notRead ?? 'No decision frame is pinned. The controller pins one when a predicate or an action consumes an observation.'}
          </p>
        ) : (
          <DecisionFramePanel snapshot={snapshot} mode={props.mode} source={store.currentSource} />
        )}
      </section>
      {/* A REHEARSAL'S RESULT IS WHAT THE REHEARSAL RECORDED. The result panel words a placement ("Placed and
          returned"), which is right for a live runtime's run and is never said of a fixture's. */}
      {summary !== null && summary.mode !== 'live' ? (
        <section className="rail__card">
          <h3 className="rail__title">
            Result <Mark text={RUN_MODE_MARK[summary.mode]} tone={modeTone(RUN_MODE_MARK[summary.mode])} />
          </h3>
          <p className="rail__line">{summary.headline}</p>
          <p className="rail__note">run {textOr(summary.runId)}</p>
          {summary.note === null ? null : <p className="rail__note">{summary.note}</p>}
        </section>
      ) : (
        <ResultPanel snapshot={snapshot} scope={scope} />
      )}
      {failure === null ? null : (
        <FailurePanel
          view={view}
          scope={scope}
          snapshot={snapshot}
          store={store}
          cause={stopCause(view.recent_events, utcDayOf(mission?.started_wall_utc ?? null))}
          steps={mission === null ? null : missionSteps(mission).map(nextStep)}
        />
      )}
      <PredicatesPanel view={view} notRead={notRead} />
      <section className="rail__card">
        <h3 className="rail__title">Activation records</h3>
        {mission === null || mission.activations.length === 0 ? (
          <p className="rail__absent">{mission === null ? 'No mission has been read.' : 'This mission has recorded no activation.'}</p>
        ) : (
          mission.activations.map((activation, index) => {
            // EVERY RECORD WHOLE, in the order the runtime recorded them: its verdict, its identities, what the
            // resident's answer said about the transaction, and the slot as read before and after it. An earlier
            // activation keeps all of it after a later one replaces it in the Cake panel.
            const card = activationCard(activation, snapshot.missionCandidates, utcDayOf(mission.started_wall_utc));
            const transaction = activation.transaction ?? null;
            return (
              <div key={activation.operation_uuid} data-activation-record={activation.operation_uuid} data-verdict={card.verdict.id} style={{ borderTop: '1px solid var(--color-border)', padding: '8px 0' }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
                  <span style={{ ...MONO, fontSize: 11 }}>{`${String(index + 1)} of ${String(mission.activations.length)}`}</span>
                  <span style={{ fontSize: 13, fontWeight: 600, color: TONE_COLOUR[card.verdict.tone] }}>{card.headline}</span>
                </div>
                <Field label="Operation" value={activation.operation_uuid} mono />
                <Field label="Target" value={activation.target_id} mono />
                <Field label="Candidate" value={activation.candidate_key} mono />
                <Field label="Payload digest" value={activation.payload_digest} mono />
                <Field label="Activation state" value={activation.activation_state} />
                <Field label="Outcome" value={activation.outcome} />
                <Field label="Transaction" value={activation.transaction_uuid} mono />
                <Field label="Transaction phase" value={activation.transaction_phase} />
                <Field label="Transaction handle" value={transaction?.transaction_handle ?? null} mono />
                <Field label="Answer status" value={transaction?.status ?? null} />
                <Field label="Answer refusal code" value={transaction?.reason_code ?? null} />
                <Field label="Answer epoch" value={transaction?.answer_epoch ?? null} mono />
                <Field label="Stop asked while in flight" value={activation.stopped_during_activation ?? null} />
                <Field label="Recorded at" value={activation.t_wall_utc} mono />
                {activation.detail === null ? null : <Field label={RUNTIME_DETAIL_LABEL} value={activation.detail} />}
                <div style={{ marginTop: 6 }}>
                  <ActivationEvidence activation={activation} full />
                </div>
              </div>
            );
          })
        )}
      </section>
    </div>
  );
}

/* ---- Connections -------------------------------------------------------------------------------------------- */

function Hop(props: { title: string; tone: string; state: string; stateAt?: UtcStamp | null; stale?: boolean; since?: UtcStamp | null; children?: ReactNode }): ReactNode {
  return (
    <section style={{ borderTop: '1px solid var(--color-border)', padding: '10px 0' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 9, flexWrap: 'wrap' }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', flex: 'none', background: props.tone }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: INK.bright }}>{props.title}</span>
        <span data-t={props.stateAt?.iso} title={props.stateAt?.title} style={{ fontSize: 12, color: props.tone }}>
          {props.state}
        </span>
        {props.stale === true ? <NotReadSince at={props.since ?? null} /> : null}
      </div>
      <div style={{ paddingLeft: 16, marginTop: 4 }}>{props.children}</div>
    </section>
  );
}

/**
 * WHAT CONNECTS TO WHAT, AND HOW CURRENT EACH LINK IS: this tab to the desktop runtime, the runtime to the robot, the
 * robot's resident, its supervised host, its capability, the motion gate, the cameras and the telemetry tap. Every
 * retained value is marked "Not read since" with the fixed UTC time of its last reading, never an age (spec 5.4, 6.8). The runtime's `/api/cake` reading is kept apart at the end, because it
 * does not say which machine answered and is never shown as the robot's.
 */
function ConnectionsBody(props: { snapshot: StoreSnapshot; store: ConsoleStore }): ReactNode {
  const nowMs = useNowMs();
  const { snapshot, store } = props;
  const view = snapshot.view;
  const read = { status: snapshot.robot, error: snapshot.robotError, readAtMs: snapshot.robotReadAtMs };
  const link: RobotLink = robotLink(read, nowMs);
  const status = snapshot.robot;
  const permission = motionPermissionView(read, nowMs);
  const recovery = recoveryView(status, link);
  const cake: CakeModel = cakeModel(view, snapshot.cakeStatus, snapshot.cakeStatusError);
  const snapshotAge = view.snapshot_at_ms === null ? null : Math.max(0, nowMs - view.snapshot_at_ms);
  const configured = status !== null && status.configured;
  // THE DEMO SCREEN'S DAY AND READ TIMES (spec 5.4): the mission's UTC start date, or today's with no mission; the robot
  // "not read since" the last reading this page received, as the top bar says it.
  const day = utcDayOf(snapshot.mission?.started_wall_utc ?? null) ?? new Date(nowMs).toISOString().slice(0, 10);
  const snapshotAt = readStamp(view.snapshot_at_ms, day);
  const snapshotCurrent = snapshotAge !== null && snapshotAge <= SNAPSHOT_CURRENT_WITHIN_MS;
  const snapshotSince = readStamp(snapshot.snapshotSinceMs ?? null, day);
  const robotSince = readStamp(snapshot.robotReadAtMs, day);
  const cakeTakenAt = utcStamp(cake.status?.observation?.wall_utc ?? null, day);
  const observedSince = (observed: { wall_utc: string | null } | null | undefined): UtcStamp | null => (link.stale ? robotSince : utcStamp(observed?.wall_utc ?? null, day) ?? robotSince);
  // THE RUN ON THE SCREEN, for the engineering reference's run panels: its read-back fold on a page opened after it, as
  // Evidence reads it, with the live snapshot's mode (a read-back fold starts from the initial state).
  const folded = viewForSelectedRun(view, selectedRun(view, snapshot.session.runtime_replaced), snapshot.hydrated);
  const runView = folded === view ? view : { ...folded, data_mode: view.data_mode };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <UncertainPanel snapshot={snapshot} store={store} />

      <Hop
        title="This tab to the desktop runtime"
        tone={snapshot.sourceError !== null || !view.contract_compatible ? C.bad : snapshotAge !== null && snapshotAge > SNAPSHOT_CURRENT_WITHIN_MS ? C.warn : C.ok}
        state={
          snapshot.sourceError !== null
            ? `the last exchange failed: ${snapshot.sourceError}`
            : snapshotAt === null
              ? 'no snapshot yet'
              : snapshotCurrent
                ? // THE TIME IT BEGAN, NEVER THE NEWEST POLL'S (browser r2 N4, verifier R-2): the poll answers every few
                  // seconds, and its time moved with each answer at a quiet end state.
                  `answering${snapshotSince === null ? '' : ` since ${snapshotSince.text}`}, read every ${String(SNAPSHOT_POLL_MS / 1000)} s`
                : `the last snapshot reached this page at ${snapshotAt.text}`
        }
        stateAt={snapshot.sourceError === null ? (snapshotCurrent ? snapshotSince : snapshotAt) : null}
      >
        {/* NOT READ WHILE THE RUNTIME HAS NOT ANSWERED THE HANDSHAKE (verify-r1 must_fix 3): the fold's default is no answer. */}
        <Field
          label="Contract"
          value={
            !view.contract_compatible
              ? `incompatible: ${textOr(view.read_only_reason)}`
              : snapshot.handshake.state === 'not_answered'
                ? `not read: the runtime has not answered the handshake (${textOr(snapshot.handshake.reason)})`
                : 'compatible'
          }
        />
        <Field label="Commands from this tab" value={view.commands_enabled ? 'allowed' : `off: ${textOr(view.read_only_reason)}`} />
        {/* The runtime's own data mode in the spec 5.2 word, read from its value alone; the raw value only in the title. */}
        <Field
          label="Runtime data mode"
          value={modeMark({ dataMode: view.data_mode, sourceKind: 'live', replaying: false }).word}
          title={view.data_mode ?? undefined}
        />
        <More label="Identities and counters">
          <Field label="Runtime session" value={view.runtime_session} mono />
          <Field label="Record cursor" value={view.seq_cursor} />
          {Object.entries(view.counters).map(([name, value]) => (
            <Field key={name} label={name.replace(/_/g, ' ')} value={value} />
          ))}
        </More>
      </Hop>

      <Hop
        title={`The runtime to the robot${link.labelKnown ? `: ${link.label}` : ''}`}
        tone={configured ? (link.state === 'answering' ? C.ok : C.bad) : C.mute}
        state={configured ? `${link.headline}; connection ${status?.connection.forward?.state ?? 'not reported'}` : link.headline}
        stale={link.stale}
        since={robotSince}
      >
        <p style={{ margin: 0, fontSize: 12, color: INK.quiet, lineHeight: 1.45 }}>{link.detail}.</p>
        {!link.labelKnown && configured ? <Field label="Robot name" value="not reported by the runtime" /> : null}
        {status === null ? null : (
          <More label="Connection details">
            <Field label="Deployment" value={status.deployment} />
            {/* is_robot is the runtime's kept name: it says the PINNED BUILD answered. The build identity identifies software;
                which deployment is observed is this runtime's connection to the robot and the session its resident reports. */}
            <Field label="Reports the pinned build" value={status.identity.is_robot === null ? 'nothing answered now' : status.identity.is_robot} />
            <Field label="Identity reason" value={status.identity.reason_code} />
            <Field label="Display label given" value={status.identity.labelled} />
            <Field label="Expected build identity" value={status.identity.expected_build_identity} mono />
            <Field label="Answering build identity" value={status.identity.answering_build_identity} mono />
            <Field label="Answering target profile (information only; it refuses nothing)" value={status.identity.answering_target_profile_digest} mono />
            <Field label="Forward state" value={status.connection.forward?.state ?? null} />
            <Field label="Forward reason" value={status.connection.forward?.reason ?? null} />
            <Field label="Since" value={status.connection.forward?.since_wall_utc ?? null} mono />
            <Field label="Starts" value={status.connection.forward?.starts ?? null} />
            <Field label="Deaths" value={status.connection.forward?.deaths ?? null} />
            <Field label="Restarts" value={status.connection.forward?.restarts ?? null} />
            <Field label="Next attempt in (ms)" value={status.connection.forward?.next_attempt_in_ms ?? null} />
            <Field label="Execution backend" value={view.execution?.backend ?? null} />
            <Field label="Lease held" value={view.lease?.held ?? null} />
          </More>
        )}
      </Hop>

      {!configured || status === null ? null : (
        <>
          <Hop
            title="Cake resident on the robot"
            tone={status.resident.reachable && !link.stale ? C.ok : C.warn}
            state={status.resident.reachable && !link.stale ? 'answering' : `not answering: ${textOr(status.resident.reason_code)}`}
            stale={link.stale || status.resident.stale}
            since={observedSince(status.resident.observed)}
          >
            <p style={{ margin: 0, fontSize: 12, color: INK.quiet, lineHeight: 1.45 }}>{recovery.residentSentence}</p>
            <More label="Identity it reported">
              <Field label="Session" value={status.resident.session_uuid} mono />
              <Field label="Build" value={status.resident.build_identity} mono />
              <Field label="Plan digest" value={status.resident.plan_digest} mono />
              <Field label="Target profile" value={status.resident.target_profile_digest} mono />
              <Field label="Epoch" value={status.resident.epoch} />
              <Field label="Observed at" value={status.resident.observed?.wall_utc ?? null} mono />
              <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--color-muted-foreground)' }}>
                A plan digest is a value the resident prints about itself. It does not establish that any Plan was admitted.
              </p>
            </More>
          </Hop>

          <Hop
            title="Supervised robot host"
            tone={status.host.supervised && !link.stale ? C.ok : C.bad}
            state={status.host.supervised && !link.stale ? 'supervised, running' : `not supervised: ${textOr(status.host.reason_code)}`}
            stale={link.stale}
            since={robotSince}
          >
            <p style={{ margin: 0, fontSize: 12, color: INK.quiet, lineHeight: 1.45 }}>{recovery.componentSentence}</p>
            <More label="Host details">
              <Field label="Supervisor slot" value={status.host.slot_uuid} mono />
              <Field label="Slot state" value={status.host.slot_state} />
              <Field label="Slot instance handle" value={status.host.slot_instance} mono />
              <Field label="Host spawn sequence" value={status.host.spawn_sequence} />
              <Field label="Supervisor health" value={status.host.health_state} />
              <Field label="Host pid on the robot" value={status.host.child_pid} />
              <Field label="Readiness" value={status.host.readiness_state} />
              <Field label="Readiness age (ms)" value={status.host.readiness_age_ms} />
            </More>
          </Hop>

          <Hop
            title={`Capability ${status.capability.name ?? '(not named)'}`}
            tone={status.capability.active && !link.stale && !status.capability.stale ? C.ok : C.wait}
            state={!status.capability.configured ? 'none configured' : status.capability.active && !link.stale && !status.capability.stale ? 'active' : `not active: ${textOr(status.capability.reason_code)}`}
            stale={link.stale || status.capability.stale}
            since={observedSince(status.capability.observed)}
          >
            <More label="Slots and health, as the resident listed them">
              {status.slots.items.length === 0 ? (
                <p style={{ margin: 0, fontSize: 12, color: INK.quiet }}>No slot listed{status.slots.reason_code === null ? '' : `: ${status.slots.reason_code}`}.</p>
              ) : (
                status.slots.items.map((slot) => (
                  <div key={`${slot.name}-${String(slot.uuid)}`} style={{ padding: '4px 0', borderTop: '1px solid var(--color-border)' }}>
                    <Field label={slot.name} value={`${slot.state}, generation ${String(slot.generation ?? '?')}, instance ${String(slot.instance ?? '?')}`} />
                    <Field label="uuid" value={slot.uuid} mono />
                    <Field label="capsule" value={slot.capsule} mono />
                  </div>
                ))
              )}
              {status.health.items.map((row, index) => (
                <Field
                  key={`${String(row.slot)}-${String(index)}`}
                  label={`health ${row.slot === null ? '?' : row.slot.slice(0, 12)}`}
                  value={`${row.state_name}, pid ${String(row.child_pid ?? '?')}, restarts ${String(row.restarts ?? '?')}, spawns ${String(row.spawns ?? '?')}`}
                />
              ))}
            </More>
          </Hop>

          <Hop
            title="Motion permission"
            tone={permission.applies ? (permission.permitted ? C.ok : C.wait) : C.mute}
            state={!permission.applies ? 'does not apply' : permission.permitted ? 'permitted' : 'held'}
          >
            {permission.why === null ? null : <p style={{ margin: 0, fontSize: 12, color: INK.soft }}>{permission.why}</p>}
            {permission.detail === null ? null : <p style={{ margin: '3px 0 0', fontSize: 12, color: INK.quiet }}>The runtime: {permission.detail}.</p>}
            <More label="Sessions">
              <Field label="Current session" value={permission.currentSession} mono />
              <Field label="Current host instance" value={permission.hostInstance === null ? `not offered: ${textOr(permission.confirmOff)}` : hostInstanceWords(permission.hostInstance)} mono />
              <Field label="Confirmed session" value={permission.confirmedSession} mono />
              <Field label="Confirmed host instance" value={permission.confirmedHostInstance === null ? null : hostInstanceWords(permission.confirmedHostInstance)} mono />
              <Field label="What changed" value={permission.changed} />
              <Field label="Confirmed at" value={permission.confirmedWall} mono />
              <Field label="Reason code" value={permission.code} />
            </More>
          </Hop>
        </>
      )}

      {/* THE SNAPSHOT'S OWN STATEMENTS (spec 7.1, the audit's API-only item): what the runtime's snapshot says about its
          components, its link and the cake_update capability, each as the snapshot words it and under this heading, so no
          reader takes them for the robot's reading above. None of them is on the demo screen. */}
      <section data-snapshot-statements="" style={{ borderTop: '1px solid var(--color-border)', padding: '10px 0' }}>
        <div style={{ ...LABEL, marginBottom: 4 }}>What the runtime&apos;s snapshot says about itself</div>
        <Field
          label="link"
          value={
            !snapshot.runtimeAnswered
              ? 'not read: no snapshot from the runtime has reached this page'
              : `link: ${view.link.state}${view.link.source_stamp?.session === undefined || view.link.source_stamp?.session === null ? '' : `, host session ${view.link.source_stamp.session}`}`
          }
        />
        {view.components.map((component) => (
          <Field key={component.id} label={component.id} value={`${component.health}${component.evidence === null || component.evidence === undefined ? '' : `: ${component.evidence}`}`} />
        ))}
        {view.capabilities
          .filter((capability) => capability.id === 'cake_update')
          .map((capability) => (
            <Field key={capability.id} label={capability.id} value={`${capability.state}${capability.reason_code === null ? '' : ` (${capability.reason_code})`}${capability.reason === null ? '' : `: ${capability.reason}`}`} />
          ))}
      </section>

      <Hop title="Cameras" tone={C.mute} state="as the runtime reports each role">
        {(['wrist', 'front'] as const).map((role) => {
          const sample = snapshot.telemetry.previews[role] ?? null;
          const camera = previewView(sample, snapshot.telemetry.preview_received_at_ms_by_role[role] ?? null, nowMs);
          return (
            <div key={role} style={{ padding: '3px 0' }}>
              <Field
                label={role}
                value={
                  // THIS PAGE'S OWN OBSERVATION, in words: no line of this camera has reached it. It is not a state the runtime reported.
                  camera.availability === 'never_arrived'
                    ? 'not read: no line from this camera has reached this page'
                    : `${camera.availability}${camera.reason === null ? '' : `: ${camera.reason}`}${camera.measured_fps === null ? '' : `, ${camera.measured_fps.toFixed(1)} fps measured`}`
                }
              />
            </div>
          );
        })}
      </Hop>

      <div style={{ paddingTop: 8 }}>
        <TelemetryPanel snapshot={snapshot} nowMs={nowMs} day={day} />
      </div>

      <section style={{ borderTop: '1px solid var(--color-border)', padding: '10px 0' }}>
        <div style={{ ...LABEL, marginBottom: 4 }}>This runtime&apos;s Cake status surface</div>
        <p style={{ margin: 0, fontSize: 12, color: INK.quiet, lineHeight: 1.45 }}>
          GET /api/cake reports the resident this runtime is configured with. It does not say which machine that resident runs on, so
          nothing here is shown as the robot&apos;s: {cake.resident.headline.toLowerCase()}
          {cakeTakenAt === null ? '' : `, taken at ${cakeTakenAt.text}`}.
        </p>
        <More label="What it reported">
          <CakeStatusFields model={cake} />
        </More>
      </section>

      <div style={{ background: SURFACE.raised, borderRadius: 8 }}>
        <More label="Engineering reference (states, phases, sources, counters)">
          <EngineeringView snapshot={snapshot} store={store} runView={runView} />
        </More>
      </div>
    </div>
  );
}

function CakeStatusFields(props: { model: CakeModel }): ReactNode {
  const status = props.model.status;
  const observation = status?.observation ?? null;
  if (status === null) return <p style={{ margin: 0, fontSize: 12, color: INK.quiet }}>{props.model.resident.detail}</p>;
  return (
    <>
      <Field label="Configured" value={status.configured} />
      <Field label="Answering now" value={status.reachable} />
      <Field label="Refusal" value={status.reason_code} />
      <Field label="Session changed" value={status.session_changed === null ? 'not determined' : status.session_changed} />
      {observation === null ? null : (
        <>
          <Field label="Taken at" value={observation.wall_utc} mono />
          <Field label="Session" value={observation.resident.session_uuid} mono />
          <Field label="Build" value={observation.resident.build_identity} mono />
          <Field label="Module slots (count)" value={observation.counts.slots} />
          <Field label="Epoch" value={observation.counts.epoch} />
          <Field label="Transaction state" value={observation.transaction_state} />
        </>
      )}
    </>
  );
}

/* ---- the shell ---------------------------------------------------------------------------------------------- */

/**
 * The drawer shell, its four tabs and its scope line.
 *
 * The overlay closes it, and so do the Close button and the Escape key: a panel that covers the screen over a robot that
 * may be moving has to be dismissible without hunting for a target. It covers the screen below the top bar only, so the
 * bar's Stop is never under it.
 */
export function Drawer(props: {
  id: DrawerId;
  snapshot: StoreSnapshot;
  store: ConsoleStore;
  mode: ScreenMode;
  /**
   * Where the overlay starts, in px from the top of the view: the height of the sticky top bar that holds the only Stop
   * (`TopBar`'s `onHeight`). The overlay covered that bar, so with Evidence or History open while the robot moved, a press
   * where Stop is drawn closed nothing and stopped nothing (verify-r7 must_fix M1). A screen with no sticky bar passes 0.
   */
  top?: number;
  onSelect: (id: DrawerId) => void;
  onClose: () => void;
}): ReactNode {
  const nowMs = useNowMs();
  const [module, setModule] = useState<LogModule>('controller');
  const link = robotLink({ status: props.snapshot.robot, error: props.snapshot.robotError, readAtMs: props.snapshot.robotReadAtMs }, nowMs);
  const mission = props.snapshot.mission;
  const scope = [
    `robot: ${link.state === 'not_configured' || link.state === 'laptop_only' ? 'none configured' : link.label}`,
    // A MISSION READ THAT FAILED is not "no mission" (verify-r2 must_fix 4): only the runtime's own answer says there is none.
    `mission: ${mission !== null ? mission.mission_id : props.snapshot.missionError === 'no_mission_recorded' ? 'no mission' : props.snapshot.missionError === null ? 'not read yet' : `not read (${props.snapshot.missionError})`}`,
    props.id === 'logs' ? `module: ${module === 'controller' ? 'controller' : module === 'cake' ? 'robot Cake resident' : 'capability'}` : null,
  ].filter((item) => item !== null);
  return (
    // BELOW THE TOP BAR, and under it: the overlay starts at the bar's height and sits one layer beneath it (the bar is
    // z-index 20), so the bar's Stop stays uncovered and pressable while the drawer is open, at every width.
    <div
      data-drawer-overlay=""
      style={{ position: 'fixed', top: props.top ?? 0, left: 0, right: 0, bottom: 0, zIndex: 19, background: 'rgba(5,5,8,.62)', display: 'flex', justifyContent: 'flex-end' }}
    >
      <div role="button" tabIndex={-1} aria-label="close the panel" onClick={props.onClose} style={{ flex: 1, cursor: 'default' }} />
      <div
        className="dc-drawer"
        style={{
          width: '600px',
          maxWidth: '94vw',
          height: '100%',
          background: 'var(--color-card)',
          borderLeft: '1px solid var(--color-border)',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, padding: '14px 20px 0', flex: 'none' }}>
          <span data-drawer-title="" style={{ fontSize: 15, fontWeight: 600, color: INK.bright }}>
            {DRAWER_NAME}
          </span>
          <div style={{ flex: 1 }} />
          <button type="button" onClick={props.onClose} style={{ ...PLAIN, padding: '4px 10px' }}>
            Close
          </button>
        </div>
        <div role="tablist" aria-label={DRAWER_NAME} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 20px 0', flex: 'none', flexWrap: 'wrap' }}>
          {DRAWER_ORDER.map((id) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={props.id === id}
              onClick={() => props.onSelect(id)}
              style={{
                font: '600 13px var(--font-sans), sans-serif',
                color: props.id === id ? INK.bright : INK.quiet,
                background: 'transparent',
                border: 'none',
                borderBottom: `2px solid ${props.id === id ? C.accent : 'transparent'}`,
                padding: '6px 10px 9px',
                cursor: 'pointer',
              }}
            >
              {DRAWER_TITLE[id]}
            </button>
          ))}
        </div>
        <div
          data-drawer-scope=""
          style={{
            padding: '8px 20px 10px',
            borderBottom: '1px solid var(--color-border)',
            fontSize: 12,
            color: 'var(--color-muted-foreground)',
            flex: 'none',
            overflowWrap: 'anywhere',
          }}
        >
          {scope.join(' · ')}
        </div>
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden', padding: '16px 20px 24px' }}>
          {props.id === 'history' ? <HistoryBody snapshot={props.snapshot} store={props.store} /> : null}
          {props.id === 'logs' ? <LogsBody snapshot={props.snapshot} store={props.store} module={module} onModule={setModule} /> : null}
          {props.id === 'evidence' ? <EvidenceBody snapshot={props.snapshot} store={props.store} mode={props.mode} /> : null}
          {props.id === 'connections' ? <ConnectionsBody snapshot={props.snapshot} store={props.store} /> : null}
          {props.id === 'cake' ? <CakeTab snapshot={props.snapshot} store={props.store} /> : null}
        </div>
      </div>
    </div>
  );
}
