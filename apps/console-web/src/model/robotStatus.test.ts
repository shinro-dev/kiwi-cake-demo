// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE ROBOT'S OWN RESIDENT: which robot, whether it answers, why motion is held, and what restarted.
//
// Every case here is one of the operator's questions answered from GET /api/robot, and each one pins the rule that
// keeps the answer honest: a retained reading is STALE with its age, a resident on this computer is never the robot's,
// a hold names its cause, and a component recovery is not a resident restart.

import { describe, expect, it } from 'vitest';
import {
  confirmDialogOff,
  holdCause,
  motionPermissionView,
  parseRobotStatus,
  recoveryView,
  robotLink,
  ROBOT_NAME_NOT_REPORTED,
} from './robotStatus';
import { FIXTURE_HOST_INSTANCE, FIXTURE_SESSION, FIXTURE_SLOT_INSTANCE, robotBody, robotStatus } from './robotStatusFixture';

const NOW = 1_000_000;

function read(status: ReturnType<typeof robotStatus> | null, extra: { error?: string | null; readAtMs?: number | null } = {}) {
  return { status, error: extra.error ?? null, readAtMs: extra.readAtMs === undefined ? NOW - 500 : extra.readAtMs };
}

describe('reading the robot status', () => {
  it('reads the schema shape and refuses a version it does not speak', () => {
    expect(parseRobotStatus(robotBody())?.resident.session_uuid).toBe(FIXTURE_SESSION);
    expect(parseRobotStatus(robotBody({ robot_status_version: '1' }))).toBeNull();
    expect(parseRobotStatus(robotBody({ robot_status_version: '3' }))).toBeNull();
    expect(parseRobotStatus({ ok: false })).toBeNull();
  });

  it('never reads supervision or an active capability from anything but an explicit true', () => {
    const status = parseRobotStatus(robotBody({ host: { supervised: 'yes' }, capability: { active: 1 } }));
    expect(status?.host.supervised).toBe(false);
    expect(status?.capability.active).toBe(false);
  });
});

describe('which LeKiwi, and whether it answers', () => {
  it('names the robot by its public display label, and says so when there is none', () => {
    const labelled = robotStatus({ identity: { ...(robotBody().identity as object), display_label: 'LeKiwi bench 1', labelled: true } });
    expect(robotLink(read(labelled), NOW).label).toBe('LeKiwi bench 1');
    const unnamed = robotLink(read(robotStatus()), NOW);
    expect(unnamed.label).toBe(ROBOT_NAME_NOT_REPORTED);
    expect(unnamed.labelKnown).toBe(false);
  });

  it('is answering only on a current reading', () => {
    const link = robotLink(read(robotStatus()), NOW);
    expect(link.state).toBe('answering');
    expect(link.stale).toBe(false);
  });

  it('marks a retained reading STALE with its age when the resident stops answering', () => {
    const status = robotStatus({
      reachable: false,
      stale: true,
      reason_code: 'robot_unreachable',
      resident: { ...(robotBody().resident as object), reachable: false, stale: true, observed: { wall_utc: '2026-09-23T10:00:00Z', age_ms: 95_000 } },
    });
    const link = robotLink(read(status), NOW);
    expect(link.state).toBe('not_answering');
    expect(link.stale).toBe(true);
    expect(link.residentAgeMs).toBe(95_000);
    expect(link.detail).toContain('robot_unreachable');
  });

  it('keeps the last answer and marks it STALE when the read of the runtime itself fails or goes quiet', () => {
    expect(robotLink(read(robotStatus(), { error: 'the runtime did not answer' }), NOW).stale).toBe(true);
    expect(robotLink(read(robotStatus(), { readAtMs: NOW - 60_000 }), NOW).stale).toBe(true);
  });

  it('never reports a resident on this computer as the robot', () => {
    const laptop = robotStatus({ configured: false, deployment: 'this_laptop' });
    const link = robotLink(read(laptop), NOW);
    expect(link.state).toBe('laptop_only');
    expect(link.headline).toBe('No robot configured');
    expect(link.detail).toContain('never reported as the robot');
  });
});

describe('why motion is held', () => {
  it('names each cause from the runtime reason code', () => {
    expect(holdCause('robot_session_not_confirmed')).toBe('never_confirmed');
    expect(holdCause('robot_session_changed')).toBe('resident_restarted');
    expect(holdCause('robot_host_changed')).toBe('host_restarted');
    expect(holdCause('robot_host_unreadable')).toBe('host_unreadable');
    expect(holdCause('robot_resident_not_the_robot')).toBe('not_the_robot');
    expect(holdCause('robot_build_identity_unreadable')).toBe('not_the_robot');
    expect(holdCause('robot_identity_changed')).toBe('identity_changed');
    expect(holdCause('robot_session_unreadable')).toBe('unreachable');
    expect(holdCause('robot_unreachable')).toBe('unreachable');
    expect(holdCause('not_the_robot')).toBe('not_the_robot');
    expect(holdCause('something_newer')).toBe('other');
  });

  it('offers the confirmation with the CURRENT session and host instance, the handle kept as its exact string', () => {
    const view = motionPermissionView(read(robotStatus()), NOW);
    expect(view.held).toBe(true);
    expect(view.cause).toBe('never_confirmed');
    expect(view.currentSession).toBe(FIXTURE_SESSION);
    expect(view.hostInstance).toEqual(FIXTURE_HOST_INSTANCE);
    expect(view.hostInstance?.slot_instance).toBe(FIXTURE_SLOT_INSTANCE);
    expect(view.confirmOff).toBeNull();
    // The runtime's own sentence is carried unaltered beside the cause.
    expect(view.detail).toContain('no robot resident session has been confirmed');
  });

  it('offers nothing for pid 0, a held or DEGRADED host, an unidentified host or a partial instance', () => {
    const motion = robotBody().motion_permission as Record<string, unknown>;
    const cases: [Record<string, unknown>, string][] = [
      [{ confirmable: false, current_host_instance: null, current_host_reason_code: 'robot_host_not_running' }, 'no host is running'],
      [{ confirmable: false, current_host_instance: null, current_host_reason_code: 'robot_host_unreadable' }, 'could not be identified'],
      [{ current_host_instance: { ...FIXTURE_HOST_INSTANCE, pid: 0 } }, 'no running, identified host'],
      [{ current_host_instance: { slot_instance: FIXTURE_SLOT_INSTANCE, pid: 4242 } }, 'no running, identified host'],
      [{ current_host_instance: { ...FIXTURE_HOST_INSTANCE, slot_instance: 6003298303284871000 } }, 'no running, identified host'],
      [{ confirmable: false }, 'no running, identified host'],
    ];
    for (const [patch, words] of cases) {
      const view = motionPermissionView(read(robotStatus({ motion_permission: { ...motion, ...patch } })), NOW);
      expect(view.confirmOff).toContain(words);
      expect(view.hostInstance).toBeNull();
    }
  });

  it('turns the dialog confirm off for every banner reason and when the reading moved under the open dialog', () => {
    const view = motionPermissionView(read(robotStatus()), NOW);
    const shown = { session: FIXTURE_SESSION, instance: { ...FIXTURE_HOST_INSTANCE } };
    expect(confirmDialogOff(null, shown, view)).toBeNull();
    expect(confirmDialogOff('a confirmation from this tab is in flight', shown, view)).toContain('in flight');
    for (const moved of [
      { ...shown, session: 'ff'.repeat(16) },
      { ...shown, instance: { ...FIXTURE_HOST_INSTANCE, spawn_sequence: 3 } },
      { ...shown, instance: { ...FIXTURE_HOST_INSTANCE, slot_instance: '911746368697286402' } },
      { ...shown, instance: { ...FIXTURE_HOST_INSTANCE, pid: 4243 } },
    ]) {
      expect(confirmDialogOff(null, moved, view)).toContain('different session or host instance');
    }
    const stale = motionPermissionView(read(robotStatus(), { error: 'lost' }), NOW);
    expect(confirmDialogOff(stale.confirmOff, shown, stale)).toContain('not answering');
  });

  it('says which component changed when a confirmation was revoked', () => {
    const motion = robotBody().motion_permission as Record<string, unknown>;
    const revoked = robotStatus({ motion_permission: { ...motion, reason_code: 'robot_host_changed', changed_component: 'spawn_sequence' } });
    const view = motionPermissionView(read(revoked), NOW);
    expect(view.cause).toBe('host_restarted');
    expect(view.changed).toContain('spawn sequence changed, whatever the pid');
  });

  it('never describes a resident that does not report the pinned build as the robot, and never says the build proves a machine', () => {
    const impostor = robotStatus({
      reachable: false,
      identity: { ...(robotBody().identity as object), is_robot: false, reason_code: 'robot_resident_not_the_robot' },
      motion_permission: { ...(robotBody().motion_permission as object), reason_code: 'robot_resident_not_the_robot' },
    });
    const link = robotLink(read(impostor), NOW);
    expect(link.state).toBe('not_the_robot');
    expect(link.headline).toBe('Not the pinned build');
    expect(link.detail).toContain('reports a build other than the one pinned for the robot');
    expect(link.stale).toBe(true);
    const view = motionPermissionView(read(impostor), NOW);
    expect(view.cause).toBe('not_the_robot');
    expect(view.confirmOff).toContain('does not report the build pinned for the robot');
    // THE CORRECTION: a build identity identifies software. Nothing here may say it proved which robot answered.
    expect(`${link.headline} ${link.detail} ${view.confirmOff ?? ''}`).not.toMatch(/prove/i);
  });

  it('refuses to offer a confirmation from a retained reading', () => {
    const view = motionPermissionView(read(robotStatus(), { error: 'the runtime did not answer' }), NOW);
    expect(view.held).toBe(true);
    expect(view.confirmOff).toContain('not answering');
  });

  it('never reads a retained permitted as a current one', () => {
    const permitted = robotStatus({ motion_permission: { ...(robotBody().motion_permission as object), permitted: true, reason_code: null } });
    expect(motionPermissionView(read(permitted), NOW).permitted).toBe(true);
    expect(motionPermissionView(read(permitted, { error: 'lost' }), NOW).permitted).toBe(false);
  });

  it('does not apply at all without a robot resident', () => {
    const none = robotStatus({ configured: false, deployment: 'not_configured', motion_permission: { applies: false, permitted: null } });
    const view = motionPermissionView(read(none), NOW);
    expect(view.applies).toBe(false);
    expect(view.held).toBe(false);
  });
});

describe('a component recovery is not a resident restart', () => {
  it('counts the supervisor restarting the host inside one resident session as a component recovery', () => {
    const body = robotBody();
    const health = body.health as { items: { restarts: number }[] };
    const recovered = robotStatus({ health: { ...health, items: [{ ...health.items[0], restarts: 2 }, health.items[1]] } });
    const view = recoveryView(recovered);
    expect(view.hostRestarts).toBe(2);
    expect(view.residentRestarted).toBe(false);
    expect(view.componentSentence).toContain('restarted its child 2 time(s)');
    expect(view.componentSentence).toContain('not a resident restart');
  });

  it('says a resident restart when the session changed', () => {
    const restarted = robotStatus({ resident: { ...(robotBody().resident as object), session_changed: true } });
    const view = recoveryView(restarted);
    expect(view.residentRestarted).toBe(true);
    expect(view.residentSentence).toContain('Resident restart');
    expect(view.residentSentence).toContain('motion stays held');
  });

  it('does not keep a resident restart as a hold once the new session was confirmed', () => {
    const restarted = robotStatus({
      resident: { ...(robotBody().resident as object), session_changed: true },
      motion_permission: { ...(robotBody().motion_permission as object), permitted: true, reason_code: null, confirmed_session_uuid: FIXTURE_SESSION },
    });
    const view = recoveryView(restarted);
    expect(view.residentSentence).toContain('has since been confirmed');
    expect(view.residentSentence).not.toContain('motion stays held');
  });

  it('marks the recovery sentences STALE with the retained reading\'s age when the robot is not answering', () => {
    const status = robotStatus();
    const link = robotLink(read(status, { error: 'the runtime did not answer' }), NOW);
    const view = recoveryView(status, link);
    expect(view.stale).toBe(true);
    expect(view.ageMs).toBe(1200);
    expect(recoveryView(status, robotLink(read(status), NOW)).stale).toBe(false);
  });
});
