// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE READY CHECKLIST: what has to be true before the demo starts, in the order it has to become true, and the FIRST
// one that is not, in one sentence.
//
// On 2026-09-26 the arm was not acquired after a power cycle and a stand-alone host was started by hand, and nothing on
// the screen named the missing prerequisite. This names it. Three rules:
//
//  1. EVERY ITEM IS READ FROM SOMETHING THE RUNTIME ANSWERED, and says which. Nothing here is inferred from a button
//     being on or off, except the last item, which is the Start control's own reason and is labelled as that.
//  2. WHAT THIS PAGE CANNOT READ IS SAID TO BE NOT READABLE, never met. Whether the arm was acquired after the last power
//     cycle is not reported on any path this console has; it is listed as not readable here, with the terminal step
//     that establishes it, and it never turns the chip green or red by itself.
//  3. A REHEARSAL HAS NO ROBOT, and says so. The robot items are "not applicable" in a rehearsal instead of unmet, so the
//     chip can say a rehearsal is ready without saying a robot is. A rehearsal that SERVES a robot status (the
//     simulation's bring-up scenarios) is judged on it: its host and motion items apply, so a motion hold or a host
//     outside Cake is never drawn as ready.

import type { MotionPermissionView, RobotLink, RobotStatus } from './robotStatus';
import { ROBOT_ANSWER_STALE_AFTER_MS } from './robot';
import type { ViewState } from './types';
import { utcStamp } from './utcTime';

export type ReadyState = 'met' | 'unmet' | 'not_readable' | 'not_applicable';

export interface ReadyItem {
  id: 'runtime' | 'contract' | 'commands' | 'robot' | 'host' | 'arm' | 'motion' | 'uncertain' | 'wrist' | 'start';
  label: string;
  state: ReadyState;
  /** One sentence: what was read, and what it says. */
  sentence: string;
  /** What to do about it, where there is one step that establishes it. */
  hint: string | null;
}

export interface ReadyChecklist {
  items: ReadyItem[];
  /** The first item that is unmet, or null when every readable item is met. */
  first_unmet: ReadyItem | null;
  ready: boolean;
  /** The chip's sentence: the first unmet item's, or what "ready" rests on. */
  sentence: string;
}

export interface ReadyInput {
  view: ViewState;
  sourceKind: 'live' | 'synthetic';
  sourceError: string | null;
  replaying: boolean;
  nowMs: number;
  /** The screen's reference UTC day (spec 5.4): a fixed time on it prints as `HH:MM:SSZ`, on any other with its date. */
  referenceDay: string | null;
  robot: RobotStatus | null;
  link: RobotLink;
  permission: MotionPermissionView;
  /** The runtime's live unresolved command (kept apart from a replay's fold). */
  unresolved: ViewState['unresolved'];
  /** The wrist panel: whether its element is showing video, and its note (only its reason code is said). Null when no panel is built. */
  wrist: { showing: boolean; note: string } | null;
  /** The Start control's own off-reason, as the screen computed it; null when Start is on. */
  startOff: string | null;
  /**
   * The handshake as the store settled it (verify-r1 must_fix 3). Anything but `answered` is a runtime whose contract was
   * never read. Absent: read as answered, for a caller that holds no handshake of its own.
   */
  handshake?: { state: 'not_asked' | 'answered' | 'not_answered' | 'incompatible'; reason: string | null };
  /** Whether a snapshot from the runtime has reached this page. Absent: read as yes. */
  snapshotRead?: boolean;
}

/** Said beside the arm item. It names the step at the robot, not a command this console could run. */
export const ARM_NOT_READABLE =
  'Whether the arm was acquired after the last power cycle is not reported to this page; nothing on this path reads it.';
export const ARM_HINT = 'At the robot, acquire the arm with the operator tooling after every power cycle, before Start.';
export const HOST_HINT = 'At the desk: bash bin/pi/lekiwi-deploy.sh check (read-only), then bash bin/pi/lekiwi-deploy.sh arm.';

/**
 * THE RUNTIME'S REASON CODE in a camera panel's note, or null when it carries none. The note can also say how long ago
 * a frame arrived; that age ticks while nothing happens (spec 6.8), so only the code is ever repeated.
 */
export function noteReason(note: string): string | null {
  return /\b[a-z]+(?:_[a-z0-9]+)+\b/.exec(note)?.[0] ?? null;
}

function sentence(text: string): string {
  const trimmed = text.trim();
  if (trimmed === '') return trimmed;
  const first = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(first) ? first : `${first}.`;
}

export function readyChecklist(input: ReadyInput): ReadyChecklist {
  const { view } = input;
  const rehearsal = input.sourceKind === 'synthetic' || view.data_mode === 'synthetic';
  const items: ReadyItem[] = [];

  const snapshotAgeMs = view.snapshot_at_ms === null ? null : Math.max(0, input.nowMs - view.snapshot_at_ms);
  items.push(
    input.sourceError !== null
      ? { id: 'runtime', label: 'Runtime answering', state: 'unmet', sentence: sentence(`The runtime did not answer: ${input.sourceError}`), hint: null }
      : snapshotAgeMs === null
        ? { id: 'runtime', label: 'Runtime answering', state: 'unmet', sentence: 'No snapshot from the runtime has reached this page yet.', hint: null }
        : snapshotAgeMs > ROBOT_ANSWER_STALE_AFTER_MS && view.snapshot_at_ms !== null
          ? {
              id: 'runtime',
              label: 'Runtime answering',
              state: 'unmet',
              // THE FIXED TIME, never the age: the item flips once and then reads the same until a snapshot arrives.
              sentence: `The runtime's last snapshot reached this page at ${
                utcStamp(new Date(view.snapshot_at_ms).toISOString(), input.referenceDay)?.text ?? new Date(view.snapshot_at_ms).toISOString()
              }.`,
              hint: null,
            }
          : { id: 'runtime', label: 'Runtime answering', state: 'met', sentence: 'The runtime answered within the last few seconds.', hint: null },
  );

  // THE IN-PAGE SIMULATION HAS NO RUNTIME AND NO SERVER (verify-r1 honesty S1): what answered there is the simulation.
  const simulation = input.sourceKind === 'synthetic';
  const handshake = input.handshake ?? { state: 'answered' as const, reason: null };
  const unanswered = view.contract_compatible && handshake.state !== 'answered';
  items.push(
    !view.contract_compatible
      ? { id: 'contract', label: 'Contract', state: 'unmet', sentence: 'The runtime speaks a contract version this console does not read.', hint: null }
      : unanswered
        ? {
            id: 'contract',
            label: 'Contract',
            state: 'not_readable',
            sentence: `Not known: the runtime has not answered the handshake${handshake.reason === null ? '' : ` (${handshake.reason})`}.`,
            hint: null,
          }
        : simulation
          ? { id: 'contract', label: 'Contract', state: 'met', sentence: 'The simulation in this page answered the handshake in the contract this console reads.', hint: null }
          : { id: 'contract', label: 'Contract', state: 'met', sentence: 'The runtime speaks the contract this console reads.', hint: null },
  );

  items.push(
    input.replaying
      ? { id: 'commands', label: 'Commands allowed', state: 'unmet', sentence: 'A recorded run is on the screen, so nothing here commands the robot.', hint: null }
      : view.commands_enabled && simulation
        ? { id: 'commands', label: 'Commands allowed', state: 'met', sentence: "The simulation in this page takes this page's commands itself.", hint: null }
        : view.commands_enabled
        ? { id: 'commands', label: 'Commands allowed', state: 'met', sentence: 'The server allows this page to command.', hint: null }
        : {
            id: 'commands',
            label: 'Commands allowed',
            state: 'unmet',
            sentence: sentence(`The server does not allow this page to command${view.read_only_reason === null ? '' : `: ${view.read_only_reason}`}`),
            hint: null,
          },
  );

  // THE ROBOT ITEMS APPLY WHENEVER A ROBOT STATUS WAS SERVED, rehearsal or not. A rehearsal that serves one (the
  // simulation's bring-up scenarios) is judged on it: a motion hold or a host outside Cake is never drawn as ready.
  const robotServed = input.robot !== null;
  const robotApplies = !rehearsal || robotServed;
  if (!robotApplies) {
    items.push({ id: 'robot', label: 'Robot answering', state: 'not_applicable', sentence: 'A rehearsal: no robot is involved.', hint: null });
  } else if (rehearsal) {
    items.push({ id: 'robot', label: 'Robot answering', state: 'met', sentence: "The simulation serves a robot status (a rehearsal's), and it is judged here as the runtime's would be.", hint: null });
  } else if (input.link.state === 'answering' && !input.link.stale) {
    items.push({ id: 'robot', label: 'Robot answering', state: 'met', sentence: sentence(`${input.link.label} answers`), hint: null });
  } else {
    items.push({ id: 'robot', label: 'Robot answering', state: 'unmet', sentence: sentence(`${input.link.headline}: ${input.link.detail}`), hint: null });
  }

  const host = input.robot?.host ?? null;
  // A ROBOT STATUS THAT WAS NOT READ SAYS NOTHING ABOUT A RESIDENT (status 2026-09-27, LOW 5): the read failed or has not
  // answered yet, so the host and motion items say that, with the read's own code, never "the runtime reports no robot
  // resident", which is the runtime's answer when it has one and none is configured.
  const robotUnread = input.robot === null && input.link.state === 'not_read' ? `The robot status has not been read: ${input.link.detail}.` : null;
  if (!robotApplies) {
    items.push({ id: 'host', label: 'Host under Cake', state: 'not_applicable', sentence: 'A rehearsal: no robot host is involved.', hint: null });
  } else if (robotUnread !== null) {
    items.push({ id: 'host', label: 'Host under Cake', state: 'unmet', sentence: `${robotUnread} Whether the host runs under Cake is not known here.`, hint: null });
  } else if (host === null || input.robot?.configured !== true) {
    items.push({ id: 'host', label: 'Host under Cake', state: 'unmet', sentence: 'The runtime reports no robot resident, so no supervised host can be read.', hint: HOST_HINT });
  } else if (host.supervised && host.child_pid !== null && host.child_pid > 0) {
    items.push({
      id: 'host',
      label: 'Host under Cake',
      state: 'met',
      sentence: `The robot host runs under Cake's supervisor (pid ${String(host.child_pid)}).`,
      hint: null,
    });
  } else {
    items.push({
      id: 'host',
      label: 'Host under Cake',
      state: 'unmet',
      sentence: host.supervised
        ? 'The supervisor slot exists but no host process runs under it.'
        : sentence(`The robot host is not running under Cake's supervisor${host.reason_code === null ? '' : ` (${host.reason_code})`}`),
      hint: HOST_HINT,
    });
  }

  items.push(
    robotApplies && !rehearsal
      ? { id: 'arm', label: 'Arm acquired', state: 'not_readable', sentence: ARM_NOT_READABLE, hint: ARM_HINT }
      : { id: 'arm', label: 'Arm acquired', state: 'not_applicable', sentence: 'A rehearsal: no arm is involved.', hint: null },
  );

  if (robotApplies && robotUnread !== null) {
    items.push({ id: 'motion', label: 'Motion permitted', state: 'not_readable', sentence: `${robotUnread} Whether motion is held is not known here.`, hint: null });
  } else if (!robotApplies || !input.permission.applies) {
    items.push({
      id: 'motion',
      label: 'Motion permitted',
      state: 'not_applicable',
      sentence: robotApplies ? 'No robot resident gates motion here; the runtime makes its other checks.' : 'A rehearsal commands no motion.',
      hint: null,
    });
  } else if (input.permission.permitted) {
    items.push({ id: 'motion', label: 'Motion permitted', state: 'met', sentence: 'The robot session and host are confirmed for motion.', hint: null });
  } else {
    items.push({
      id: 'motion',
      label: 'Motion permitted',
      state: 'unmet',
      sentence: sentence(input.permission.why ?? input.permission.detail ?? 'Motion is held on this account'),
      hint: input.permission.confirmOff === null ? 'Confirm the robot session in the motion hold banner.' : null,
    });
  }

  items.push(
    input.snapshotRead === false
      ? {
          id: 'uncertain',
          label: 'No uncertain command',
          state: 'not_readable',
          sentence: 'Not known: no snapshot from the runtime has reached this page, so whether a command is of unknown execution was not read.',
          hint: null,
        }
      : input.unresolved === null
      ? { id: 'uncertain', label: 'No uncertain command', state: 'met', sentence: simulation ? 'The simulation reports no command of unknown execution.' : 'No command is of unknown execution.', hint: null }
      : {
          id: 'uncertain',
          label: 'No uncertain command',
          state: 'unmet',
          sentence: 'One command’s execution is not known, and it blocks conflicting work until it is resolved.',
          hint: 'Resolve it in the uncertain-command panel.',
        },
  );

  if (input.wrist === null || (rehearsal && !input.wrist.showing)) {
    items.push({
      id: 'wrist',
      label: 'Wrist view',
      state: 'not_applicable',
      sentence: rehearsal ? 'A rehearsal: the wrist camera is not required.' : 'No wrist camera panel is built on this page.',
      hint: null,
    });
  } else if (input.wrist.showing) {
    items.push({ id: 'wrist', label: 'Wrist view', state: 'met', sentence: 'The wrist camera is showing video on this page.', hint: null });
  } else {
    const reason = noteReason(input.wrist.note);
    items.push({
      id: 'wrist',
      label: 'Wrist view',
      state: 'unmet',
      sentence: sentence(`The wrist camera is not showing video${reason === null ? '' : `: ${reason}`}`),
      hint: null,
    });
  }

  items.push(
    input.startOff === null
      ? { id: 'start', label: 'Start', state: 'met', sentence: 'Start is on.', hint: null }
      : { id: 'start', label: 'Start', state: 'unmet', sentence: sentence(`Start is off: ${input.startOff}`), hint: null },
  );

  const firstUnmet = items.find((item) => item.state === 'unmet') ?? null;
  const armUnread = items.some((item) => item.id === 'arm' && item.state === 'not_readable');
  return {
    items,
    first_unmet: firstUnmet,
    ready: firstUnmet === null,
    sentence:
      firstUnmet !== null
        ? firstUnmet.sentence
        : rehearsal
          ? robotServed
            ? "Ready for a rehearsal: the robot status is the simulation's, and no robot is involved."
            : 'Ready for a rehearsal: no robot is involved.'
          : armUnread
            ? 'Every readable prerequisite is met; the arm must be acquired at the robot, which this page cannot read.'
            : 'Every prerequisite is met.',
  };
}
