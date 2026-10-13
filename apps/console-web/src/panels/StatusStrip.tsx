// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The persistent strip. It is always on the screen and it survives a screenshot: everything needed to read the picture
// correctly is in it. Two separations are load-bearing here and they are never collapsed:
//
//   DATA MODE  (live / replay / synthetic)  is where the data comes from.
//   LINK       (connected / stale / gone)   is whether it is arriving.
//
// A live view whose link dropped is a live view with a dead link. It is not replay, and the strip never lets the two
// be read as one.
//
// THE MODE IS THE RUNTIME'S WORD AND THE SELECTION IS THIS PAGE'S. They are two readouts because they are two facts:
// `data_mode` is what the runtime says it is doing, and the selection is which source this page chose to read. The
// address bar can change the second and can never change the first, so a synthetic runtime cannot be relabelled as
// hardware by opening it at a different URL.
//
// CONNECTIVITY IS NOT ONE BADGE. The console, the robot telemetry and each camera are asked separately, in
// `connectivity.ts`, because they fail separately and a single green light over all of them has been wrong on this
// screen in both directions at once.
//
// WHAT OWNS THE ROBOT IS BESIDE WHO IS COMMANDING IT, and the two are not the same question. The owner is which kind
// of thing is driving right now; the ownership basis is what would stop anybody else. Under the baseline backend the
// answer to the second is that this runtime refuses a second run on this computer and nothing on the robot refuses
// anything, and the strip says that in those words. The phrase "lease held" is never printed under that backend, in
// any form: see `ownership.ts`.
//
// EVERY ROW THIS STRIP TAKES IS A ROW THE SCREEN BELOW DOES NOT GET. The strip is sticky and sits above the fold, so
// its height is subtracted from the one thing the operator must see without scrolling: the current action, the phase,
// the controls and the stop. It was measured at 1440x900 with a run going and cost 252 px, which put the phase rows
// off the bottom of the screen. Two facts that were already stated elsewhere at full width were taken out of it: the
// Cake sentence, which the Cake card carries, and the age beside a readout that has never received a line, which its
// own badge already says in words. Nothing was made smaller and nothing was clipped to do it.

import type { ReactNode } from 'react';
import type { DataMode } from '@kiwi/demo-contracts';
import { nanosAsMillis, textOr } from '../model/format';
import type { StoreSnapshot } from '../store/store';
import { useNowMs } from '../store/useConsole';
import { reconnectOffer } from '../model/reconnect';
import { Age } from '../ui/Age';
import { Badge } from '../ui/Badge';
import { IconAlert, IconCamera, IconLink, IconStop } from '../ui/Icons';
import { AGE_MEANING } from '../model/measurement';
import { connectivityReadouts } from './connectivity';
import type { Readout } from './connectivity';
import { CHOICE_LABEL, CHOICE_TITLE, searchFor } from './sourceChoice';
import type { SourceChoice } from './sourceChoice';

/**
 * How long this console waits for another tap sample before it stops preferring the tap's own age. A tap that stopped
 * arriving keeps its last age forever, and the snapshot's `link` is the fresher fact once that happens.
 */
const SILENT_TAP_MS = 4000;

/** The runtime's data mode for the screen, or null while no snapshot has said it (spec 5.2 "Mode not recorded"). */
export type ScreenMode = DataMode | null;

/** The plain word for a data mode. `synthetic` is a simulation and this screen calls it one. */
const MODE_WORD: Record<DataMode, string> = {
  live: 'live runtime',
  replay: 'recorded run',
  synthetic: 'simulation',
};

/**
 * One labelled group of the strip.
 *
 * `stacked` puts each child on a line of its own instead of flowing them along one very wide line. It is a WIDTH
 * decision, not a decoration: the groups wrap into rows inside the strip, so a group wider than the row takes a row
 * to itself, and a row of the strip is a row the screen below does not get. A group of two short lines is about as
 * wide as one of them and shares its row with a neighbour; the same content on one line is twice as wide and does
 * not. Nothing is truncated either way.
 */
function Group(props: { label: string; stacked?: boolean; children: ReactNode }): ReactNode {
  return (
    <div className="strip__group">
      <div className="strip__label">{props.label}</div>
      <div className={`strip__value ${props.stacked === true ? 'strip__value--stacked' : ''}`}>{props.children}</div>
    </div>
  );
}



/** Where a recording came from and how far into it the screen is. Null when no recording is open. */
export interface ReplayPosition {
  run_id: string;
  folded: number;
  total: number;
  /** The wall time the record at the cursor was written at, or null when that record carries none. */
  wall_utc: string | null;
  /** Who produced the record at the cursor. */
  producer: string | null;
  /**
   * The mode the RECORDING was made in, from the finished-run row. Null when the row does not carry one, which is
   * what a run recorded before the field existed says; it is never filled in from the runtime's current mode.
   */
  recorded_mode: string | null;
}

export function replayPosition(snapshot: StoreSnapshot): ReplayPosition | null {
  const replay = snapshot.replay;
  if (replay === null) return null;
  const record = replay.cursor > 0 ? replay.events[replay.cursor - 1] : undefined;
  const history = snapshot.history;
  const row = history !== null && history.ok ? history.entries.find((entry) => entry.run_id === replay.run_id) ?? null : null;
  return {
    run_id: replay.run_id,
    folded: replay.cursor,
    total: replay.events.length,
    wall_utc: record?.t_wall_utc ?? null,
    producer: record?.producer.name ?? null,
    recorded_mode: row?.data_mode ?? null,
  };
}

/**
 * One connectivity readout, with its own words, its own glyph and, WHEN IT HAS ONE, its own age.
 *
 * A readout that has never received a line has no age, and what stood here in its place was a second sentence saying
 * so beside a badge that had just said it: "front: nothing captured yet" followed by "no preview slot for this
 * camera". On a live runtime with two cameras that duplicate is what pushed this group on to a second row, and a
 * second row of the strip is a row the current action does not get. The badge's words are the state, which is the
 * rule that matters here, and the sentence is not lost: it is in the readout's own hover text with the runtime's
 * evidence, and in the sources card at full width.
 */
function ReadoutBadge(props: { readout: Readout; nowMs?: number }): ReactNode {
  const readout = props.readout;
  const detail = readout.atMs === null ? `${readout.detail}. ${readout.absent}` : readout.detail;
  return (
    <span className="row" style={{ gap: 'var(--space-1)' }}>
      <Badge tone={readout.tone} icon={readout.kind === 'camera' ? <IconCamera /> : <IconLink />} title={detail}>
        {readout.label}: {readout.words}
      </Badge>
      {readout.atMs === null ? null : (
        /* THE AGE IS LABELLED WITH WHAT IT MEASURES. A bare duration beside the words "lines are arriving" reads as a
           contradiction the moment it grows, because the two are different measurements of different things. The
           label says which of the three this one is; `model/measurement.ts` holds the set. */
        <span className="muted" style={{ fontSize: 'var(--text-xs)' }} title={readout.age_label === undefined ? undefined : AGE_MEANING[readout.age_label]}>
          {readout.age_label === undefined ? null : `${readout.age_label} `}
          <Age atMs={readout.atMs} nowMs={props.nowMs} absent={readout.absent} />
        </span>
      )}
    </span>
  );
}

/** The strip is sticky and every row of it is a row the screen below does not get. Loose text is bounded here. */
const BOUNDED_TEXT = { fontSize: 'var(--text-xs)', maxWidth: '22rem', overflow: 'hidden', textOverflow: 'ellipsis' } as const;

/** A selection button, small enough to sit in the strip without setting the height of the row it is in. */
const SELECT_BUTTON = { fontSize: 'var(--text-xs)', padding: '0.1rem 0.4rem' } as const;

/**
 * The same bound as the loose text above, set for a clause that is stated again in full on a card below it.
 *
 * It is WIDER than the 22rem it was, not narrower: the Cake sentence took two lines inside the old bound, and a
 * second line made that group the tallest thing in its row. Nothing here is being truncated to fit.
 */

/** A clause on a line of its own inside a stacked group, at the size the other loose text in this strip is set at. */
const STACKED_TEXT = { fontSize: 'var(--text-xs)', minWidth: 0 } as const;

/**
 * Get this tab a current authorization, by loading a new document.
 *
 * `location.reload()` and nothing else: the credential is written into the page the API serves, so a new page is the
 * only thing that carries the current one. It sends no command, and every piece of component state, including the
 * physical attestations and the stated holding, is destroyed with the document rather than carried forward.
 */
function reloadToReconnect(): void {
  if (typeof window === 'undefined') return;
  window.location.reload();
}

/** Change which source this page reads. It reloads the page, because the selection is made before the store exists. */
function selectSource(choice: SourceChoice): void {
  if (typeof window === 'undefined') return;
  window.location.search = searchFor(choice);
}

export function StatusStrip(props: {
  snapshot: StoreSnapshot;
  mode: ScreenMode;
  onStop: () => void;
  stopBusy: boolean;
  /** The shared reading of this computer's clock. A test passes one; the screen lets every age take the shared tick. */
  nowMs?: number;
}): ReactNode {
  const { view, telemetry, lastStop } = props.snapshot;
  const replaying = props.snapshot.replay !== null;
  const sourceKind = props.snapshot.sourceKind;
  // A tap sample's age is frozen the moment the tap stops arriving. Once it has been silent on THIS computer's clock
  // for longer than the threshold, the snapshot's link is the fresher fact and the tap's numbers are no longer shown
  // as if they were current. The two clocks are never subtracted from each other.
  //
  // The reading comes from the shared tick and not from the wall clock at render time, so a tap that goes quiet is
  // noticed within a second rather than at whatever moment something else happens to re-render this strip.
  const shared = useNowMs();
  const reference = props.nowMs ?? shared;
  const tapSilent = telemetry.tap_received_at_ms === null || reference - telemetry.tap_received_at_ms > SILENT_TAP_MS;
  const tap = tapSilent ? null : telemetry.tap;
  const sourceSeq = tap?.source_stamp?.seq ?? view.link.source_stamp?.seq ?? null;

  const readouts = connectivityReadouts(props.snapshot, reference);
  // What the RUNTIME says owns the robot. It is never derived from the lease beside it: under the baseline backend no
  // lease is ever asked for, so an absent lease is not an absence of ownership and must not read as one.
  const position = replayPosition(props.snapshot);

  const stop = view.stop;

  return (
    <div className="strip">
      <div className="strip__groups">
      {/* The runtime's word and this page's selection, side by side, because they are two facts and the second one
          cannot change the first.

          ALL THREE THINGS THE SCREEN CAN BE ARE NAMED HERE. The two that are a source are buttons; the recorded run
          is chosen by its run id in the finished-run card, because a recording is a particular run and not a mode
          the address can ask for, so it is said in words rather than given a button that could not be pressed. */}
      <Group label="what this screen is" stacked>
        <span className="row" style={{ gap: 'var(--space-1)' }}>
        <Badge
          tone={props.mode ?? 'quiet'}
          strong
          title={props.mode === null ? 'no snapshot has stated the runtime\'s data_mode yet' : `the runtime's own data_mode is ${props.mode}, and no address bar can change it`}
        >
          {props.mode === null ? 'mode not recorded' : MODE_WORD[props.mode]}
        </Badge>
        <Badge tone="quiet" title={props.snapshot.sourceLabel}>
          reading {sourceKind === 'live' ? 'the runtime' : "this page's own script"}
        </Badge>
        <button
          type="button"
          className="button"
          style={SELECT_BUTTON}
          disabled={sourceKind === 'live'}
          title={CHOICE_TITLE.runtime}
          onClick={() => selectSource('runtime')}
        >
          {CHOICE_LABEL.runtime}
        </button>
        <button
          type="button"
          className="button"
          style={SELECT_BUTTON}
          disabled={sourceKind === 'synthetic'}
          title={CHOICE_TITLE.simulation}
          onClick={() => selectSource('simulation')}
        >
          {CHOICE_LABEL.simulation}
        </button>
        </span>
        <span className="muted" style={STACKED_TEXT} title="a recorded run is opened by its run id, in the finished runs card below">
          a recorded run: in finished runs
        </span>
      </Group>

      {position === null ? null : (
        <Group label="replay position">
          <Badge tone="replay" icon={<IconAlert />} title={`the recording is ${position.run_id}`}>
            record {String(position.folded)} of {String(position.total)}
          </Badge>
          <span className="muted" style={BOUNDED_TEXT}>
            {position.wall_utc === null ? 'the record at the cursor carries no wall time' : position.wall_utc} , recorded by{' '}
            {textOr(position.producer)} in {position.recorded_mode === null ? 'a mode the recording does not state' : position.recorded_mode} mode
          </span>
        </Group>
      )}


      {/* WHO IS COMMANDING, AND WHAT WOULD STOP ANYBODY ELSE. Two questions, and the second is the one this screen
          must not overstate: under the baseline backend the whole of the ownership is this runtime refusing a second
          run on this computer, and nothing on the robot refuses anything at all. The runtime's own sentence is the
          hover text; the clause on the screen is the short form of the same finding, never a softer one.

          The time left on a lease is shown only where a lease is the ownership. Under the baseline backend there is
          no lease to have a time left, and the words for one are never printed: see `ownership.ts`. */}
      {/* WHAT USED TO BE HERE: who owns the robot, the executing artifact, the task outcome and a Cake row. Each of
          them was a standing summary of something stated again below, and together they were a quarter of the screen
          above the picture. The run's outcome is in the rail, scoped to the run it belongs to; Cake is stated once
          beside the modules; ownership and the executing artifact are read in Diagnostics, which is where the full
          finding and the runtime's own sentence always were. */}

      <Group label="connectivity">
        {readouts.map((readout) => (
          <ReadoutBadge key={readout.id} readout={readout} nowMs={props.nowMs} />
        ))}
        <span className="muted" style={{ fontSize: 'var(--text-xs)' }}>
          source seq {sourceSeq === null ? 'not reported' : String(sourceSeq)}
        </span>
      </Group>

      </div>

      <div className="strip__stop">
        <div className="stop-detail">
          <strong>Cooperative software stop, not an emergency stop.</strong> It asks the controller to stop and cannot cut power. The servo power
          switch on the robot is the physical cut-off.
          {replaying ? ' This button acts on the LIVE runtime, not on the recording on screen.' : ''}
        </div>
        <div className="stop-states">
          <Badge tone={stop.requested === null ? 'quiet' : 'warn'}>{stop.requested === null ? 'not requested' : `requested by ${stop.requested.origin}`}</Badge>
          <Badge tone={stop.acknowledged === null ? 'quiet' : 'info'}>
            {stop.acknowledged === null ? 'not acknowledged' : `acknowledged in ${nanosAsMillis(stop.acknowledged.latency_ns)}`}
          </Badge>
          <Badge tone={stop.outcome === null ? 'quiet' : stop.outcome.motion_ceased === true ? 'ok' : 'warn'}>
            {stop.outcome === null ? 'no observed outcome' : `outcome ${stop.outcome.outcome}`}
          </Badge>
        </div>
        {/* What became of the REQUEST, kept apart from what became of the stop. A request the server refused, or one
            whose fate is unknown, is said so here: a button that answers with silence reads as a button that worked. */}
        {lastStop === null || lastStop.outcome === 'taken' ? null : (
          <div className={`stop-request stop-request--${lastStop.outcome}`}>
            {lastStop.outcome === 'refused'
              ? `The last stop request was REFUSED and never reached the controller: ${textOr(lastStop.reason)}.`
              : `The last stop request did not get an answer, so whether it reached the controller is NOT known: ${textOr(lastStop.reason)}. Press again, or use the servo power switch.`}
          </div>
        )}
        <button
          type="button"
          className="stop-button"
          onClick={props.onStop}
          disabled={props.stopBusy || !view.contract_compatible}
          title={
            replaying
              ? 'Ask the LIVE controller to stop. This is not an emergency stop, and it does not act on the recording on screen.'
              : 'Ask the controller to stop. This is not an emergency stop.'
          }
        >
          <IconStop size={16} /> STOP
        </button>
      </div>
    </div>
  );
}

/**
 * The sentence that says what the screen is, or null when it is a live runtime and there is nothing to say.
 *
 * `sourceKind` is not the same fact as `mode`, and the difference is the difference between two true sentences and one
 * false one. `mode` is the RUNTIME's data_mode: 'synthetic' means the runtime daemon was started with --synthetic, and
 * it then runs a real controller process, journals real events, serves stored frames and answers real commands, with no
 * robot and no camera. `sourceKind` is which source this page is reading: 'synthetic' is the script inside this page,
 * where nothing outside the browser is involved at all. One sentence covered both and said "No robot, no camera and no
 * runtime were involved", which is false in the mode the demo actually runs in.
 *
 * THE SOURCE KIND IS ASKED BEFORE THE MODE. The script inside this page decides the words it appears under, whatever
 * `data_mode` the script's own snapshot happens to carry: branching on the mode first let a script that called itself
 * live pass with no banner at all, which is the one arrangement on this screen that reads as hardware when it is not.
 */
export function modeBanner(mode: ScreenMode, sourceKind: 'live' | 'synthetic'): string | null {
  if (mode === 'replay') return 'This is a recorded run being replayed. Nothing is moving now.';
  if (sourceKind === 'synthetic') {
    return 'Everything below is a scripted synthetic run drawn inside this page. No runtime, no robot and no camera were involved.';
  }
  if (mode !== 'synthetic') return null;
  return 'The runtime is in SIMULATION: a real runtime and a real controller process produced everything below, and no robot and no camera were involved.';
}

/** A second line under the strip, only when there is something the operator must not miss. */
export function StripAlerts(props: { snapshot: StoreSnapshot; mode: ScreenMode; sourceKind: 'live' | 'synthetic' }): ReactNode {
  const { view, sourceError, replayError } = props.snapshot;
  const alerts: ReactNode[] = [];

  if (replayError !== null) {
    alerts.push(
      <div className="banner" key="replay">
        <IconAlert /> {replayError}. Nothing was opened: an empty fold under a REPLAY badge would read as a run in which nothing happened.
      </div>,
    );
  }

  if (!view.contract_compatible) {
    alerts.push(
      <div className="banner banner--bad" key="contract">
        <IconAlert /> This runtime speaks a contract version this console does not: <code>{view.read_only_reason ?? 'unknown'}</code>. The tab is
        read-only. No command can be sent and nothing below is being updated.
      </div>,
    );
  }
  // A HANDSHAKE THE RUNTIME HAS NOT ANSWERED is said by the failed exchange below; the record is not arriving then, so the
  // read-only line's "keeps arriving" would be false (verify-r1 must_fix 3).
  if (view.contract_compatible && !view.commands_enabled && props.snapshot.handshake.state !== 'not_answered') {
    // WHEN THE TAB CAN GET ITSELF BACK, IT IS TOLD HOW. The credential is minted for one runtime session and reaches a
    // page only in the index.html the API serves, so no handshake gives a tab whose runtime was replaced the new one: the
    // way back for its controls is a document load (a Stop the server refuses reads that page once for itself,
    // `api/http.ts` stop). `model/reconnect.ts` decides whether to offer it and states what a reload will and will not
    // do. Everything else keeps the bare reason it had.
    const offer = reconnectOffer(props.snapshot.session);
    alerts.push(
      <div className="banner" key="readonly">
        <IconAlert /> The server does not allow this tab to command: <code>{view.read_only_reason ?? 'read_only'}</code>.{' '}
        {/* "KEEPS ARRIVING" ONLY WHILE THE RUNTIME ANSWERS THIS DISPLAY (round 7 item 4): with the last exchange failed the
            record on screen is the last one read, and saying it keeps arriving would be false. */}
        {sourceError === null
          ? 'The record below keeps arriving and is being shown; only the controls are off.'
          : 'The record below is the last this display read; only the controls are off.'}
        {offer.reason === null ? null : (
          <>
            {' '}
            {offer.sentence}{' '}
            <button type="button" className="button" title={offer.scope} onClick={reloadToReconnect} style={{ marginLeft: 6 }}>
              {offer.label}
            </button>
            <div className="muted wrap" style={{ marginTop: 4 }}>
              {offer.scope}
            </div>
          </>
        )}
      </div>,
    );
  }
  if (sourceError !== null) {
    alerts.push(
      <div className="banner" key="source">
        <IconAlert /> The last exchange with the runtime failed: {sourceError}.{' '}
        {props.snapshot.runtimeAnswered ? 'What is shown below is the last state that did arrive.' : 'Nothing from the runtime has reached this display yet.'}
      </div>,
    );
  }
  if (view.stream_gaps.length > 0) {
    const last = view.stream_gaps[view.stream_gaps.length - 1];
    alerts.push(
      <div className="banner" key="gap">
        <IconAlert /> {String(view.stream_gaps.length)} gap(s) in the record. The most recent covers seq {String(last?.from_seq)} to{' '}
        {String(last?.to_seq)} and was {last?.declared === true ? 'announced by the runtime' : 'detected from the sequence numbers'}. Those records
        are not on this screen.
      </div>,
    );
  }
  const banner = modeBanner(props.mode, props.sourceKind);
  if (banner !== null) {
    alerts.push(
      <div className="banner" key="mode">
        <IconCamera /> {banner}
      </div>,
    );
  }
  if (alerts.length === 0) return null;
  return <>{alerts}</>;
}
