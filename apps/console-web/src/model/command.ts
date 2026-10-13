// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHICH COMMAND THE SCREEN IS LOOKING AT, AND WHAT THE FIVE STEPS MEAN FOR IT.
//
// An Observe is not a cycle. A return of the arm is not a cycle either. All three are commands the runtime runs the
// same way, through the same supervisor, producing the same kinds of record and the same `run.finished`, and the
// journal records which one it was: `command_type` on the run that started, on the run in progress and on the history
// entry of the run that ended.
//
// WHAT WENT WRONG WITHOUT THIS FILE. A real non-motion Observe ran on hardware. It captured a frame, it recorded the
// pose it read, and it finished with outcome "unknown" and placement predicates that were all null. The console had
// no idea what command it was looking at, so it read the end of the controller subprocess as the end of THE CYCLE:
// it marked step 3 Run done, advanced to step 4 Result, and drew a large failure panel over a command that had done
// exactly what it was asked to do. For an Observe there is nothing to grasp and nothing to place, so those nulls are
// the CORRECT record of a command with no placement in it, and absent placement predicates are not a failure.
//
// THE FIX IS NOT A SPECIAL CASE FOR THE WORD "unknown". It is to say, in the model, that the same five steps mean
// different things for different commands, and to read which command it is from the record rather than inferring it
// from an outcome word. Everything in this file comes out of `command_type` and out of nothing else.
//
// WHAT IS NOT WEAKENED HERE. A CYCLE still requires its predicates. `requires_placement` below is true for a cycle and
// for every command this console does not recognize, so an exit status of 0 can never become a placement success and a
// cycle with absent predicates is still not a pass. The scoping only ever takes a claim away; it never adds one.

import type { CommandType } from '@kiwi/demo-contracts';
import { currentRunId, resultForCurrentRun } from './steps';
import type { ViewState } from './types';

/**
 * What kind of work the command on the screen is. `cycle` is the full pick and place; `unknown` is a command this
 * console cannot name, including a run whose type nothing in reach recorded, and it is treated as a cycle would be.
 */
export type CommandKind = 'cycle' | 'observe' | 'return_arm' | 'scan' | 'manual' | 'unknown';

const KIND: Record<CommandType, CommandKind> = {
  start_cycle: 'cycle',
  observe: 'observe',
  return_arm: 'return_arm',
  // Scan scene (docs/autonomous-demo/overnight-plan-2026-09-26.md, track P0b): pan-only views from the low scan pose and
  // back. A motion command with no grasp and no placement in it.
  scan_scene: 'scan',
  manual_pan: 'manual',
  manual_move: 'manual',
  manual_grip: 'manual',
  manual_base_pulse: 'manual',
  stop: 'manual',
};

/** Where the command type was read. The screen prints it, because a claim about the run rests on it. */
export type CommandSource = 'active_run' | 'run_started' | 'history' | null;

export interface CommandScope {
  command_type: CommandType | null;
  kind: CommandKind;
  source: CommandSource;
  /**
   * Whether finishing this command COMPLETES the Run step of the demonstration. Only a cycle does. An Observe is a
   * look at the scene, which is step 2, and a successful one leaves Run as the next thing to press rather than
   * marking it done and moving to the result of a cycle nobody ran.
   */
  completes_run: boolean;
  /**
   * Whether the grasp and placement predicates decide this command's verdict. True for a cycle and for an unnamed
   * command. False only where the command has no placement in it, and then their absence is the record being correct.
   */
  requires_placement: boolean;
  /** What this command is, in the words the screen uses for it. */
  label: string;
}

const SCOPE: Record<CommandKind, Omit<CommandScope, 'command_type' | 'source'>> = {
  cycle: { kind: 'cycle', completes_run: true, requires_placement: true, label: 'cycle' },
  observe: { kind: 'observe', completes_run: false, requires_placement: false, label: 'observation' },
  return_arm: { kind: 'return_arm', completes_run: false, requires_placement: false, label: 'return of the arm' },
  scan: { kind: 'scan', completes_run: false, requires_placement: false, label: 'scan of the scene' },
  manual: { kind: 'manual', completes_run: false, requires_placement: false, label: 'manual command' },
  // A command this console cannot name is held to the strictest reading there is. Nothing is relaxed on a guess.
  unknown: { kind: 'unknown', completes_run: true, requires_placement: true, label: 'command' },
};

/**
 * THE SCOPE USED WHEN NOTHING NAMED THE COMMAND. It is the strictest one there is, and it is what every function that
 * takes a scope falls back to when a caller gives none: the guide behaves exactly as it did before command types were
 * read at all, the placement predicates stay required, and no claim is relaxed because a type was missing. A scoping
 * that could only ever take a claim away cannot be weakened by being absent.
 */
export const UNSCOPED_COMMAND: CommandScope = { ...SCOPE.unknown, command_type: null, source: null };

/**
 * The command type of the run the screen is looking at, from the three places the record carries it.
 *
 * In order: the run in progress, then the `run.started` record this tab folded, then the history entry of that run.
 * The history is what a RELOADED tab has: it folded no records at all, and the finished-run list carries the type for
 * every run the journal still speaks about. `historyTypeFor` is passed in rather than read here, because the history
 * is a separate answer from a separate endpoint and this file stays a pure function of the fold.
 */
export function commandScope(view: ViewState, historyTypeFor?: (runId: string) => string | null): CommandScope {
  const runId = currentRunId(view);
  const active = view.active_run;
  if (active !== null && active.run_id === runId && active.command_type !== null) {
    return scopeFor(active.command_type, 'active_run');
  }
  const started = view.run_started;
  if (started !== null && started.command_type !== null) return scopeFor(started.command_type, 'run_started');
  if (runId !== null && historyTypeFor !== undefined) {
    // The history row's type is kept as a bare string by this console on purpose, so that a token from a newer
    // runtime reaches the screen as itself instead of being dropped by a copy of an enum that is behind. One this
    // console cannot name falls through to the strictest scope, which is the same thing as naming nothing at all.
    const recorded = historyTypeFor(runId);
    if (recorded !== null && recorded in KIND) return scopeFor(recorded as CommandType, 'history');
  }
  return { ...SCOPE.unknown, command_type: null, source: null };
}

function scopeFor(type: CommandType, source: CommandSource): CommandScope {
  return { ...SCOPE[KIND[type]], command_type: type, source };
}

/**
 * Whether the command on the screen ENDED WELL, for a command with no placement in it.
 *
 * A cycle's verdict is decided by its predicates and is not decided here; this answers only the narrower question the
 * step guide asks about an Observe or a return of the arm, which is whether the command did what it was asked to do.
 * It rests on the two facts the record actually carries for such a command: the controller exited 0, and the runtime
 * did not report it as failed, stopped or refused. The outcome word `unknown` is NOT read as a fault here, because for
 * a command with no placement in it the runtime has no placement to call the outcome after and writes exactly that.
 */
const BAD_OUTCOMES: ReadonlySet<string> = new Set(['failed', 'stopped', 'refused']);

export function nonPlacementCommandSucceeded(view: ViewState): boolean {
  const result = resultForCurrentRun(view);
  if (result === null) return false;
  if (result.reported !== null && BAD_OUTCOMES.has(result.reported)) return false;
  // A reason code on a finished command is the controller saying why it did not end as it should. An absent one is
  // the ordinary case; a present one is never read past.
  if (result.reason_code !== null) return false;
  return result.exit_status === 0;
}
