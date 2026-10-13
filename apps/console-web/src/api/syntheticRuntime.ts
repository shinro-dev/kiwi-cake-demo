// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT THE RUNTIME DECIDES AFTER A MISSION RUN ENDS, ported for the simulation so a rehearsal holds exactly as the runtime
// holds. Three functions of the runtime at integrate/overnight-2026-09-26 (05fcea0), each ported line for line:
//
//   `demo_runtime.mission_execution.stop_state_from_events`: where a run left the robot, from the run's own events;
//   `demo_runtime.mission_execution.classify_outcome`: the class the hold decides on, from the run's end and that state;
//   `demo_runtime.mission.next_actions`: what a press can do after an attempt of that class.
//
// The code lists are the runtime's own (mission_execution.py, RETRYABLE_CODES and the lists after it). A code missing from
// them is FATAL there, and here. The runtime at that head can park the arm before a retry (Supervisor.start_mission_park),
// so its coordinator offers one retry per target (mission_coordinator.RETRY_BUDGET_PER_TARGET) whenever the recorded state
// allows it; the simulation does the same.

import type { ContractEvent } from '../contracts/envelope';

/** Where a run left the robot: each part true, false, or null for unknown. */
export interface RuntimeStopState {
  holding: boolean | null;
  base_ahead: boolean | null;
  moved: boolean | null;
}

/** The classes of mission.OUTCOME_CLASSES. */
export type OutcomeClass = 'placed' | 'retryable' | 'operator_needed' | 'base_only' | 'fatal' | 'stopped';

/** The classes after which the mission holds for the operator rather than ending (mission.HOLDING_CLASSES). */
export const HOLDING_CLASSES: ReadonlySet<string> = new Set(['retryable', 'operator_needed']);

/** mission_coordinator.RETRY_BUDGET_PER_TARGET. */
export const RETRY_BUDGET_PER_TARGET = 1;

const RETRYABLE_CODES: ReadonlySet<string> = new Set([
  'pregrasp_no_non_contact_pose',
  'straddling_not_reached',
  'grasp_missed',
  'search_no_pad_frame',
  'search_cube_not_in_pad_frame',
  'grasp_retry_cube_lost',
  'grasp_retry_not_aligned',
  'grasp_retry_no_untried_radius',
  'retention_not_demonstrated_set_down',
]);
/**
 * mission_execution.NO_RETRY_CODES: the operator is needed and no Retry follows (the park would start where no recorded
 * exit started, ALIGN refused the block as too near, or the target binding refused the SEARCH). Read before the lists
 * above: an align_ code here is not retryable by its prefix.
 */
const NO_RETRY_CODES: ReadonlySet<string> = new Set([
  'grasp_retry_lift_refused',
  'grasp_set_down_lift_refused',
  'grasp_retry_approach_refused',
  'align_inside_grasp_radius',
  'mission_binding_no_candidate',
  'mission_binding_candidate_not_placeable',
]);
const RETRYABLE_PREFIXES = ['align_', 'search_no_cube'];
const ENVELOPE_REFUSAL_CODES: ReadonlySet<string> = new Set([
  'search_placement_infeasible',
  'search_cube_beyond_return_bound',
  'search_cube_beyond_pregrasp_entry_bound',
  'search_cube_inside_pregrasp_reach',
]);
const BLOCK_MOVE_CODES: ReadonlySet<string> = new Set(['approach_cube_beyond_grasp_range', 'search_scene_no_block', 'search_scene_not_one_unplaced_block']);
const BASE_ONLY_CODES: ReadonlySet<string> = new Set(['base_not_returned']);
const PLACEMENT_PREDICATES = ['object_secured', 'placed', 'base_returned'] as const;
/** supervisor.STOP_EXIT_STATUS and the controller's CycleFailed exit (mission_execution.CYCLE_FAILED_EXIT_STATUS). */
const STOP_EXIT_STATUS = 4;
const CYCLE_FAILED_EXIT_STATUS = 5;

/** mission_execution.stop_state_from_events over the run's own contract events, in order. */
export function stopStateFromEvents(events: readonly ContractEvent[]): RuntimeStopState {
  let moved = false;
  let holding = false;
  let baseAdvanced = false;
  let baseReturned = false;
  let openClose = false;
  let setDown = false;
  for (const event of events) {
    if (event.kind === 'action.started') {
      moved = true;
    } else if (event.kind === 'state.transition') {
      const to = event.payload.to_state;
      if (to === 'GRASP_CLOSE') {
        openClose = true;
        setDown = false;
      } else if (to === 'RELEASED') {
        holding = false;
        openClose = false;
        setDown = false;
      } else if (to === 'GRASP_SET_DOWN') {
        setDown = true;
      } else if (to === 'BASE_ADVANCE') {
        baseAdvanced = true;
        baseReturned = false;
      }
    } else if (event.kind === 'predicate.evaluated') {
      const name = event.payload.predicate;
      const value = event.payload.value;
      if (name === 'closed_on_something' && typeof value === 'boolean') {
        holding = holding || value;
        openClose = false;
      } else if (name === 'object_secured' && value === true) {
        holding = true;
        openClose = false;
      } else if (name === 'gripper_opened' && value === true && setDown) {
        holding = false;
        openClose = false;
        setDown = false;
      } else if (name === 'base_returned' && value === true) {
        baseReturned = true;
      }
    }
  }
  return { holding: openClose && !holding ? null : holding, base_ahead: baseAdvanced && !baseReturned, moved };
}

function retryable(code: string | null): boolean {
  return code !== null && !NO_RETRY_CODES.has(code) && (RETRYABLE_CODES.has(code) || RETRYABLE_PREFIXES.some((prefix) => code.startsWith(prefix)));
}

/** mission_execution.retry_allowed: whether a Retry may follow an ending with this code at all. */
export function retryAllowed(code: string | null): boolean {
  return code === null || !NO_RETRY_CODES.has(code);
}

/** The run's end as classify_outcome reads it: the completion's outcome and exit status, and its run.finished payload. */
export interface RunEnd {
  outcome: string | null;
  exit_status: number | null;
  reason_code: string | null;
  predicates: { readonly object_secured?: boolean | null; readonly placed?: boolean | null; readonly base_returned?: boolean | null } | null;
}

/** mission_execution.classify_outcome: the first of its rules that holds. */
export function classifyOutcome(end: RunEnd, stop: RuntimeStopState | null): OutcomeClass {
  if (end.outcome === 'stopped' || end.exit_status === STOP_EXIT_STATUS) return 'stopped';
  const predicates = end.predicates ?? {};
  if (end.exit_status === 0 && end.outcome === 'placed_new' && PLACEMENT_PREDICATES.every((name) => predicates[name] === true)) return 'placed';
  const state = stop ?? { holding: null, base_ahead: null, moved: null };
  if (state.holding === true) return 'operator_needed';
  if (state.holding === null || state.base_ahead === null || state.moved === null) return 'fatal';
  if (end.reason_code !== null && BASE_ONLY_CODES.has(end.reason_code)) return 'base_only';
  if (state.base_ahead === true) return 'operator_needed';
  if (retryable(end.reason_code) && end.exit_status === CYCLE_FAILED_EXIT_STATUS) return 'retryable';
  if (end.reason_code !== null && (BLOCK_MOVE_CODES.has(end.reason_code) || NO_RETRY_CODES.has(end.reason_code)) && end.exit_status === CYCLE_FAILED_EXIT_STATUS) return 'operator_needed';
  if (end.reason_code !== null && ENVELOPE_REFUSAL_CODES.has(end.reason_code) && state.moved === false) return 'operator_needed';
  return 'fatal';
}

/**
 * mission.next_actions: while the mission holds, a retry only when the budget has one left and the recorded state says
 * nothing is held and the base is home (null is never read as either), then Stop; after the end, what the operator does
 * by hand or by command, the worst reading taken for an unknown part, then a new mission.
 */
export function nextActions(outcomeClass: string, stop: RuntimeStopState | null, retryLeft: boolean): string[] {
  const state = stop ?? { holding: null, base_ahead: null, moved: null };
  if (HOLDING_CLASSES.has(outcomeClass)) {
    const actions: string[] = [];
    if (retryLeft && state.holding === false && state.base_ahead === false) actions.push(outcomeClass === 'retryable' ? 'retry' : 'move_block_then_retry');
    return [...actions, 'stop'];
  }
  const actions: string[] = [];
  if (state.holding !== false) actions.push('open_gripper_by_hand');
  if (state.base_ahead === true) actions.push('return_base');
  if (state.moved !== false || state.holding !== false) actions.push('return_arm');
  return [...actions, 'start'];
}
