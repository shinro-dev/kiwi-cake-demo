// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// ONE ROBOT STATUS ANSWER, SHAPED EXACTLY AS `robot_status.schema.json` v2 DESCRIBES IT, for the tests.
//
// It follows schema v2 (identity by build identity, the host INSTANCE in the motion gate, slot instance handles as
// decimal strings). It is
// the answer of a robot whose resident answers, whose host is supervised and whose capability is not yet
// active, with motion held because no session was ever confirmed. Tests change one field at a time from here, so each
// one states the single fact it is about. Nothing in the application imports this file.

import { parseRobotStatus } from './robotStatus';
import type { RobotStatus } from './robotStatus';

export const FIXTURE_SESSION = '0123456789abcdef0123456789abcdef';
export const FIXTURE_HOST_PID = 4242;
/** Larger than 2**53, as a real Cake instance handle is: it must survive as a string, never become a number. */
export const FIXTURE_SLOT_INSTANCE = '6003298303284871169';
export const FIXTURE_HOST_INSTANCE = { slot_instance: FIXTURE_SLOT_INSTANCE, spawn_sequence: 2, pid: FIXTURE_HOST_PID };
export const FIXTURE_HOST_SLOT = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
export const FIXTURE_CAPABILITY_SLOT = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';

export function robotBody(patch: Record<string, unknown> = {}): Record<string, unknown> {
  const observed = { wall_utc: '2026-09-23T10:00:00Z', age_ms: 1200 };
  return {
    robot_status_version: '2',
    configured: true,
    deployment: 'robot_resident',
    reachable: true,
    reason_code: null,
    stale: false,
    clock_domain: 'laptop_monotonic',
    identity: {
      is_robot: true, reason_code: null, expected_build_identity: 'cafebabecafebabe', answering_build_identity: 'cafebabecafebabe',
      answering_target_profile_digest: 'feedfacefeedface', display_label: null, labelled: false,
      build_identity: 'cafebabecafebabe', session_uuid: FIXTURE_SESSION, stale: false, observed,
    },
    connection: {
      deployment: 'robot_resident',
      forward: {
        state: 'connected', reason: null, since_wall_utc: '2026-09-23T09:00:00Z', since_age_ms: 3_600_000,
        starts: 1, deaths: 0, restarts: 0, next_attempt_in_ms: null,
      },
      observed,
    },
    resident: {
      reachable: true, stale: false, reason_code: null, session_uuid: FIXTURE_SESSION, build_identity: 'cafebabecafebabe',
      plan_digest: 'deadbeefdeadbeef', target_profile_digest: 'feedfacefeedface', epoch: 7, session_changed: false, observed,
    },
    slots: {
      stale: false, reason_code: null, observed,
      items: [
        { name: 'supervisor.lekiwi', uuid: FIXTURE_HOST_SLOT, capsule: '1111111111111111', generation: 1, instance: FIXTURE_SLOT_INSTANCE, state: 'ACTIVE' },
        { name: 'mobile_approach', uuid: FIXTURE_CAPABILITY_SLOT, capsule: '2222222222222222', generation: 1, instance: '911746368697286402', state: 'STAGED' },
      ],
    },
    health: {
      stale: false, reason_code: null, observed,
      items: [
        { slot: FIXTURE_HOST_SLOT, state_name: 'HEALTHY', child_pid: FIXTURE_HOST_PID, restarts: 1, spawns: 2, last_status: 0 },
        { slot: FIXTURE_CAPABILITY_SLOT, state_name: 'HEALTHY', child_pid: 5151, restarts: 0, spawns: 1, last_status: 0 },
      ],
    },
    host: {
      supervised: true, reason_code: null, slot_uuid: FIXTURE_HOST_SLOT, slot_state: 'ACTIVE', slot_instance: FIXTURE_SLOT_INSTANCE,
      child_pid: FIXTURE_HOST_PID, spawn_sequence: 2, health_state: 'HEALTHY',
      readiness_state: 'normal_host_running', readiness_age_ms: 900, observed,
    },
    capability: {
      configured: true, name: 'mobile_approach', active: false, reason_code: 'slot_not_active', capsule: '2222222222222222',
      generation: 1, child_pid: 5151, stale: false, observed,
    },
    updates: {
      stale: false, reason_code: null, dropped: 0, excluded_admin_records: 12, other_records: 0, observed,
      items: [{ sequence: 40, event: '0x05FFFFFC', name: 'EVT_SUPERVISOR_CHILD_STARTED', kind: 'supervisor', arg0: 1, arg1: FIXTURE_HOST_PID, epoch: 7 }],
    },
    motion_permission: {
      applies: true, permitted: false, reason_code: 'robot_session_not_confirmed',
      detail: 'no robot resident session has been confirmed by the operator',
      confirmable: true, changed_component: null,
      confirmed_session_uuid: null, confirmed_host_instance: null, confirmed_wall_utc: null, current_session_uuid: FIXTURE_SESSION,
      current_host_instance: { ...FIXTURE_HOST_INSTANCE }, current_host_reason_code: null, observed,
    },
    ...patch,
  };
}

/** The fixture, parsed, with one section replaced. */
export function robotStatus(patch: Record<string, unknown> = {}): RobotStatus {
  const status = parseRobotStatus(robotBody(patch));
  if (status === null) throw new Error('the robot status fixture no longer parses');
  return status;
}
