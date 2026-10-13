// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT THE RUNTIME SAID ABOUT A START OR A STOP THIS TAB SENT, in words, and nothing it did not say.
//
// A button that answers with silence is a button an operator will believe worked, so every press of Start and Stop gets
// one line back. That line is a RECEIPT for the request. It is never the outcome:
//
//   A STOP THAT WAS TAKEN IS NOT MOTION THAT CEASED. The runtime answers the request at once; whether the robot stopped
//   is what the run's stop.requested, stop.acknowledged and stop.outcome records say, and this line points there
//   instead of claiming it. It says WHAT the runtime took the stop against: the controller run it signalled, the
//   mission whose stop it wrote down, both, or neither.
//   A STOP THAT ENDED ONLY A MISSION WAS TAKEN. A mission held on a capability holds the run slot with no controller run
//   under it, so the runtime can answer with no run stopped and a mission stopping. That press did something, and it
//   is never reported as refused.
//   A STOP NEVER REACHES THE HOST OR THE RESIDENT. Nothing here could say it did, because the runtime's stop path does
//   not touch either; the sentence never names them as stopped.
//   A MISSION START IS A MISSION WRITTEN DOWN. Whether it is running is read back from the mission record, which the
//   screen shows beside this, so this says the runtime wrote it down under an id and no more.

import type { ActivationOutcome, CapabilityCheckOutcome, MissionStartOutcome, RobotConfirmOutcome, SignatureCheckOutcome, StopAck } from '../api/types';
import type { CommandResult } from '@kiwi/demo-contracts';

export type ReceiptTone = 'taken' | 'refused' | 'unknown';

export interface Receipt {
  tone: ReceiptTone;
  text: string;
}

const MOTION_IS_IN_THE_RECORDS = "Whether motion ceased is not in this answer; the run's stop records say that.";

/**
 * THE REFUSAL CODES OF THE CONSOLE SERVER ITSELF (`apps/console-api`): its guard on every mutating request
 * (`security.py` check_command), its body check, and its session check before a command is forwarded
 * (`app.py` _verify_runtime_session). The runtime never sends any of them. A press refused with one of these was refused
 * by the console server, and nothing of the command reached the runtime, so it is never said as the runtime's refusal
 * (redesign r4 verify-r1 must_fix 2).
 */
export const CONSOLE_SERVER_REFUSALS: ReadonlySet<string> = new Set([
  'host_not_allowed',
  'origin_not_allowed',
  'content_type_not_json',
  'console_header_missing',
  'credential_missing',
  'credential_invalid',
  'credential_superseded',
  'body_not_json',
  'runtime_session_changed',
]);

/** The console server's refusal in its own voice, or null when the code is not one of its own. */
export function consoleServerRefusal(code: string | null | undefined): string | null {
  if (code === null || code === undefined || !CONSOLE_SERVER_REFUSALS.has(code)) return null;
  // The session check asks the runtime who it is before forwarding; the command itself was still not sent on.
  return code === 'runtime_session_changed'
    ? `The console server refused it: ${code}. The command was not sent on to the runtime.`
    : `The console server refused it: ${code}. Nothing reached the runtime.`;
}

/** What this display did after the console server refused its credential on a Stop, said before the answer (must_fix 4). */
const TAKEOVER_LEAD =
  "The console server refused this tab's credential (credential_superseded). This display read the page the console server serves now, as a reload does, and sent Stop once more with the credential in it";

/** The receipt for one press of Stop, from the runtime's answer to it. */
export function stopReceipt(ack: StopAck): Receipt {
  const takeover = ack.takeover ?? null;
  if (takeover !== null && !takeover.sent) {
    return {
      tone: 'refused',
      text: `Stop refused by the console server: credential_superseded. Nothing reached the runtime. This display could not get a new credential from the page the console server serves now (${takeover.not_sent_reason ?? 'no reason given'}), so no second Stop was sent. Reload the page and press Stop again, or use the servo power switch.`,
    };
  }
  if (takeover !== null) {
    const lead = `${TAKEOVER_LEAD}${takeover.runtime_session === null ? '' : ` (the page names runtime session ${takeover.runtime_session})`}.`;
    if (ack.outcome === 'refused' && consoleServerRefusal(ack.reason) !== null) {
      return {
        tone: 'refused',
        text: `${lead} The console server refused it again (${ack.reason ?? ''}), so nothing reached the runtime and nothing was stopped by this press. It is not sent a third time: reload the page and press Stop again, or use the servo power switch.`,
      };
    }
    const second = stopReceipt({ ...ack, takeover: null });
    return { tone: second.tone, text: `${lead} ${second.text}` };
  }
  const mission = ack.mission;
  if (ack.outcome === 'unknown') {
    return {
      tone: 'unknown',
      text: `The Stop request got no answer (${ack.reason ?? 'no reason given'}), so whether it reached the runtime is not known. It is not sent again on its own; press Stop again, or use the servo power switch.`,
    };
  }
  if (ack.outcome === 'refused' && consoleServerRefusal(ack.reason) !== null) {
    return {
      tone: 'refused',
      text: `Stop refused by the console server: ${ack.reason ?? ''}. Nothing reached the runtime, and nothing was stopped by this press.`,
    };
  }
  if (ack.outcome === 'refused') {
    const missionWords =
      mission !== null && mission.reason_code === 'no_running_mission' ? ' No mission was running either.' : '';
    return {
      tone: 'refused',
      text: `Stop refused: ${ack.reason ?? 'no reason given'}. Nothing was stopped by this press.${missionWords}`,
    };
  }
  const parts: string[] = [];
  if (ack.run_id !== null) parts.push(`signalled the controller run ${ack.run_id} to stop`);
  else if (mission === null || !mission.stopping) parts.push('took the stop');
  if (mission !== null && mission.stopping) {
    const id = mission.mission_id ?? 'the mission';
    parts.push(
      mission.state === 'STOPPED'
        ? `ended mission ${id} (it now reads STOPPED)`
        : `asked mission ${id} to stop (it read ${mission.state ?? 'no state'} when the runtime answered)`,
    );
  }
  // Said only when the runtime's own receipt says it: the command result names no_active_run.
  const noRun = ack.run_id === null && ack.result?.reason_code === 'no_active_run' ? ' No controller run was active.' : '';
  return { tone: 'taken', text: `Stop taken: the runtime ${parts.join(' and ')}.${noRun} ${MOTION_IS_IN_THE_RECORDS}` };
}

/** The receipt for one press of Start, from the runtime's answer to the mission start. */
export function missionStartReceipt(outcome: MissionStartOutcome): Receipt {
  if (outcome.kind === 'started') {
    return {
      tone: 'taken',
      text: `The runtime wrote down mission ${outcome.mission_id ?? '(no id given)'}. What it is doing is read back from the mission record, not from this answer.`,
    };
  }
  if (outcome.kind === 'refused') {
    const server = consoleServerRefusal(outcome.reason_code);
    if (server !== null) return { tone: 'refused', text: `${server} Nothing was started.` };
    return {
      tone: 'refused',
      text: `The runtime did not start a mission: ${outcome.reason_code}${outcome.detail === null ? '' : ` (${outcome.detail})`}. Nothing was started.`,
    };
  }
  return {
    tone: 'unknown',
    text: `The answer to Start was lost (${outcome.detail}). It was not sent again; the mission record, read back, says whether a mission started.`,
  };
}

/** What the runtime's refusal of any Retry at all means (supervisor.mission_retry_refusal), by its code. */
const RETRY_REFUSAL_WORDS: Record<string, string> = {
  mission_retry_not_enabled: 'this runtime admits no Retry: its configuration does not set mission.retry, so a hold offers only Stop',
  mission_retry_not_attended: 'this runtime admits no Retry: its configuration names no attended label, so a hold offers only Stop',
};

/** The receipt for one press of Try again, from the runtime's answer to the mission retry. */
export function missionRetryReceipt(outcome: MissionStartOutcome): Receipt {
  if (outcome.kind === 'started') {
    return {
      tone: 'taken',
      text: 'The runtime took the retry: it parks the arm and starts a fresh attempt, each its own run. What it is doing is read back from the mission record, not from this answer.',
    };
  }
  if (outcome.kind === 'refused') {
    const server = consoleServerRefusal(outcome.reason_code);
    if (server !== null) return { tone: 'refused', text: `${server} Nothing was started.` };
    return {
      tone: 'refused',
      text: `The runtime did not retry: ${outcome.reason_code}${outcome.detail === null ? '' : ` (${outcome.detail})`}${RETRY_REFUSAL_WORDS[outcome.reason_code] === undefined ? '' : `; ${RETRY_REFUSAL_WORDS[outcome.reason_code] ?? ''}`}. Nothing was started.`,
    };
  }
  return {
    tone: 'unknown',
    text: `The answer to Try again was lost (${outcome.detail}). It was not sent again; the mission record, read back, says whether an attempt started.`,
  };
}

/**
 * THE PRESS RECEIPTS OF THE OTHER CONTROLS, for the press list (spec rule 6.5). Each says what came back for that one
 * press, and never the verdict: the verdict is the record the runtime keeps, which the screen draws beside it.
 */
export function activationPressReceipt(outcome: ActivationOutcome): Receipt {
  const server = outcome.kind === 'refused' ? consoleServerRefusal(outcome.reason_code) : null;
  if (server !== null) return { tone: 'refused', text: `${server} No transaction was opened.` };
  if (outcome.kind === 'refused') {
    return { tone: 'refused', text: `The runtime did not take the activation: ${outcome.reason_code}. Nothing was judged and no transaction was opened.` };
  }
  if (outcome.kind === 'unknown') {
    return { tone: 'unknown', text: `The answer to the activation could not be read (${outcome.detail}). It was not sent again; the mission record says what became of it.` };
  }
  return {
    tone: 'taken',
    text: `The runtime took the activation and answered ${outcome.activation_state}${outcome.transaction_phase === null ? '' : `, transaction phase ${outcome.transaction_phase}`}. What it came to is the mission's activation record.`,
  };
}

export function checkPressReceipt(outcome: CapabilityCheckOutcome, offWords: (code: string) => string): Receipt {
  const server = outcome.kind === 'refused' ? consoleServerRefusal(outcome.reason_code) : null;
  if (server !== null) return { tone: 'refused', text: `${server} Nothing was submitted.` };
  if (outcome.kind === 'refused') return { tone: 'refused', text: `The runtime refused the slot check and submitted nothing: ${offWords(outcome.reason_code)}.` };
  if (outcome.kind === 'unknown') return { tone: 'unknown', text: `The answer to the slot check could not be read (${outcome.detail}). It was not sent again; the check's record says what happened.` };
  return {
    tone: 'taken',
    text: `The runtime took the slot check and recorded ${outcome.activation_state}${outcome.transaction_phase === null ? '' : `, transaction phase ${outcome.transaction_phase}`}. The check's record, in Diagnostics, says what the slot and the host did.`,
  };
}

export function signaturePressReceipt(outcome: SignatureCheckOutcome, offWords: (code: string) => string): Receipt {
  const server = outcome.kind === 'refused' ? consoleServerRefusal(outcome.reason_code) : null;
  if (server !== null) return { tone: 'refused', text: `${server} Nothing was run.` };
  if (outcome.kind === 'refused') {
    return { tone: 'refused', text: `The runtime refused the signature check and ran nothing: ${offWords(outcome.reason_code)}${outcome.detail === null ? '' : `, ${outcome.detail}`}.` };
  }
  if (outcome.kind === 'unknown') return { tone: 'unknown', text: `The answer to the signature check could not be read (${outcome.detail}). It was not sent again.` };
  return {
    tone: 'taken',
    text: `The runtime ran the verifier: ${String(outcome.rows_as_expected)} rows as expected, ${String(outcome.rows_not_as_expected)} not, ${String(outcome.rows_error)} in error, ${String(outcome.rows_skipped)} skipped.`,
  };
}

export function confirmPressReceipt(outcome: RobotConfirmOutcome): Receipt {
  const server = outcome.kind === 'refused' ? consoleServerRefusal(outcome.reason_code) : null;
  if (server !== null) return { tone: 'refused', text: `${server} Motion stays held.` };
  if (outcome.kind === 'refused') return { tone: 'refused', text: `The runtime refused the confirmation: ${outcome.reason_code}. Motion stays held.` };
  if (outcome.kind === 'unknown') return { tone: 'unknown', text: `The answer to the confirmation could not be read (${outcome.detail}). It was not sent again; the robot status says whether motion is permitted.` };
  return outcome.ok
    ? { tone: 'taken', text: `The runtime took the confirmation of session ${outcome.confirmed_session_uuid ?? '(none named)'}. The robot status says whether motion is permitted.` }
    : { tone: 'refused', text: `The runtime did not take the confirmation: ${outcome.reason_code ?? 'no reason given'}. Motion stays held.` };
}

/** A command submitted from a control (Return arm, Scan scene, Observe): the result the runtime gave for its id. */
export function commandPressReceipt(result: CommandResult | null, unresolved: string | null): Receipt {
  if (result === null) return { tone: 'unknown', text: `The answer was lost (${unresolved ?? 'no reason given'}). It was not sent again; the command's id is kept to ask the runtime.` };
  if (result.status === 'rejected') {
    const server = consoleServerRefusal(result.reason_code);
    if (server !== null) return { tone: 'refused', text: `${server} Nothing was started.` };
    return { tone: 'refused', text: `The runtime refused it: ${result.reason_code ?? 'no reason given'}. Nothing was started.` };
  }
  return { tone: 'taken', text: `The runtime took it${result.run_id === null ? '' : ` as run ${result.run_id}`}. What it did is that run's record.` };
}
