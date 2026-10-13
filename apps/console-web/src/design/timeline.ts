// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE HISTORY STRIP AND THE HISTORY DRAWER, built from contract records.
//
// The imported design carries a hand-written event list (`events: [{ t: '00:04', kind: 'sys', text: 'Baseline program
// started from browser', source: 'operator' }, ...]`) whose entries were composed for a walkthrough. This turns the
// real ordered record into the same shape, so the design's strip and drawer bind to something that happened.
//
// WHAT IT IS ALLOWED TO DO: choose a tone, choose a verb, and name the producer. Every noun in a line comes from the
// record: a state name is the controller's own, an action type is the controller's own, a predicate name is the
// predicate's own, and an outcome word is the runtime's own. Nothing here renames a controller state into friendlier
// words, because a state this console has never been told about must still be readable as itself.
//
// THE TIME IS A UTC TIME OF DAY, not an offset. The design shows `00:04`, an offset from a run start it invented. A real
// offset needs a run start, and a tab that joined mid-run has none (`model/mission.ts`), so the offset would be blank
// on exactly the records somebody is most likely to be reading. The envelope carries the wall time the runtime wrote
// the record at, so that is what is shown, and a record without one shows its sequence number, which is the one
// quantity every record has.

import type { ContractEvent } from '../contracts/envelope';
import { utcStamp } from '../model/utcTime';
import type { Tone } from './theme';

export interface TimelineLine {
  /** The UTC time the runtime wrote the record (`HH:MM:SSZ`), or its sequence number when it carries no time. */
  t: string;
  /** The record's time as an ISO string with milliseconds, for `data-t` and the title; null when it carries none. */
  iso: string | null;
  tone: Tone;
  /** What happened, in the record's own nouns. */
  text: string;
  /** Which producer wrote it. */
  source: string;
  /** The observation this record names, when it names one. It is what the Evidence button opens. */
  observation_id: string | null;
  seq: number;
}

// IN UTC, WITH A Z (spec 5.4): `HH:MM:SSZ` on the reference day, the date before it on any other, never a local time.
function timeWords(event: ContractEvent, referenceDay: string | null): { t: string; iso: string | null } {
  const stamp = utcStamp(event.t_wall_utc, referenceDay);
  return stamp === null ? { t: `#${String(event.seq)}`, iso: null } : { t: stamp.text, iso: stamp.iso };
}

/**
 * One record as one line.
 *
 * The switch is exhaustive over the 19 kinds of contract v1. A kind this console does not know cannot appear here,
 * because `contracts/envelope.ts` refuses to parse one, but the default arm still prints the kind rather than dropping
 * the record: a line nobody can read is better than a record nobody can see.
 */
export function lineOf(event: ContractEvent, referenceDay: string | null = null): TimelineLine {
  // The producer's NAME. The envelope carries a whole producer identity (version, artifact digest, contract version);
  // the line names who wrote the record and the rest of that identity belongs in the engineering reference.
  const base = { ...timeWords(event, referenceDay), source: event.producer.name, observation_id: event.observation_id, seq: event.seq };

  switch (event.kind) {
    case 'run.started':
      return { ...base, tone: 'sys', text: `Run started: ${event.payload.command_type ?? 'command type not stated'}` };
    case 'run.config':
      return { ...base, tone: 'sys', text: `Run configuration pinned, digest ${event.payload.config_digest.slice(0, 12)}` };
    case 'run.finished':
      return {
        ...base,
        tone: event.payload.outcome === 'placed_new' || event.payload.outcome === 'already_placed_noop' ? 'ok' : 'bad',
        text: `Run finished: ${event.payload.outcome}`,
      };
    case 'state.transition':
      return { ...base, tone: 'robot', text: `Controller state ${event.payload.to_state}` };
    case 'action.planned':
      return { ...base, tone: 'robot', text: `Planned ${event.payload.action_type}` };
    case 'action.started':
      return { ...base, tone: 'robot', text: `Executing ${event.payload.action_type}` };
    case 'action.completed':
      return {
        ...base,
        tone: event.payload.result === 'ok' ? 'ok' : event.payload.result === 'stopped' ? 'warn' : 'bad',
        text: `${event.payload.action_type} ${event.payload.result}`,
      };
    case 'action.refused':
      return {
        ...base,
        tone: 'bad',
        text: `Refused ${event.payload.action_type}: ${event.payload.detail}`,
      };
    case 'measured.state':
      return { ...base, tone: 'robot', text: 'Robot pose recorded' };
    case 'observation.captured':
      return {
        ...base,
        tone: 'robot',
        text: `${event.payload.source_role} camera captured a ${event.payload.media.replace(/_/g, ' ')}`,
      };
    case 'perception.result':
      return { ...base, tone: 'robot', text: `Perception ${event.payload.result} by ${event.payload.detector}` };
    case 'predicate.evaluated': {
      // A predicate that was NOT evaluated is not a predicate that measured false, and the two never share a word.
      const value = event.payload.value;
      const state = event.payload.state ?? (value === null ? 'not_evaluated' : 'evaluated');
      if (state !== 'evaluated' || value === null) {
        return { ...base, tone: 'warn', text: `${event.payload.predicate}: ${state.replace(/_/g, ' ')}` };
      }
      return { ...base, tone: value ? 'ok' : 'bad', text: `${event.payload.predicate} measured ${String(value)}` };
    }
    case 'stop.requested':
      return { ...base, tone: 'warn', text: `Stop requested by ${event.payload.origin}` };
    case 'stop.acknowledged':
      return { ...base, tone: 'warn', text: `Stop acknowledged by ${event.payload.acknowledged_by}` };
    case 'stop.outcome':
      return { ...base, tone: event.payload.outcome === 'holding' ? 'ok' : 'warn', text: `Stop outcome: ${event.payload.outcome}` };
    case 'timing.span':
      return { ...base, tone: 'sys', text: `Timing span ${event.payload.span}` };
    case 'evidence.gap':
      return { ...base, tone: 'warn', text: `Evidence gap: ${event.payload.gap}` };
    case 'telemetry.dropped':
      return { ...base, tone: 'warn', text: `Telemetry dropped at ${event.payload.stage}` };
    case 'diagnostic.text':
      return {
        ...base,
        tone: event.payload.level === 'error' ? 'bad' : event.payload.level === 'warning' ? 'warn' : 'sys',
        text: event.payload.text,
      };
    default:
      return { ...base, tone: 'sys', text: (event as ContractEvent).kind };
  }
}

/**
 * The lines this fold holds, oldest first.
 *
 * `recent_events` is a BOUNDED tail the reducer keeps for the engineering view, so this is the recent record and never
 * the whole run. The screen says so rather than presenting it as a complete log, and the finished-run list beside it
 * is what survives a reload.
 */
export function timeline(events: ContractEvent[], referenceDay: string | null = null): TimelineLine[] {
  return events.map((event) => lineOf(event, referenceDay));
}
