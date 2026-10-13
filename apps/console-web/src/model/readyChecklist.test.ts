// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import { describe, expect, it } from 'vitest';
import { ARM_HINT, ARM_NOT_READABLE, HOST_HINT, readyChecklist } from './readyChecklist';
import type { ReadyInput } from './readyChecklist';
import { initialState } from './reducer';
import { motionPermissionView, robotLink } from './robotStatus';
import { robotBody, robotStatus } from './robotStatusFixture';
import type { RobotStatus } from './robotStatus';
import type { ViewState } from './types';

const NOW = 1_000_000;

function liveView(patch: Partial<ViewState> = {}): ViewState {
  return { ...initialState(), data_mode: 'live', commands_enabled: true, contract_compatible: true, snapshot_at_ms: NOW - 1000, ...patch };
}

function input(patch: Partial<ReadyInput> & { status?: RobotStatus | null } = {}): ReadyInput {
  const status = patch.status === undefined ? robotStatus() : patch.status;
  const read = { status, error: null, readAtMs: NOW - 500 };
  return {
    view: liveView(),
    sourceKind: 'live',
    sourceError: null,
    replaying: false,
    nowMs: NOW,
    referenceDay: '1970-01-01',
    robot: status,
    link: robotLink(read, NOW),
    permission: motionPermissionView(read, NOW),
    unresolved: null,
    wrist: { showing: true, note: 'LIVE' },
    startOff: null,
    ...patch,
  };
}

function permitted(): RobotStatus {
  const body = robotBody();
  const motion = body.motion_permission as Record<string, unknown>;
  return robotStatus({ motion_permission: { ...motion, permitted: true, reason_code: null, detail: null } });
}

describe('the Ready checklist', () => {
  it('names the first unmet prerequisite in one sentence, in the order they must become true', () => {
    const list = readyChecklist(input({ view: liveView({ snapshot_at_ms: null }) }));
    expect(list.ready).toBe(false);
    expect(list.first_unmet?.id).toBe('runtime');
    expect(list.sentence).toBe('No snapshot from the runtime has reached this page yet.');
    expect(list.items.map((item) => item.id)).toEqual(['runtime', 'contract', 'commands', 'robot', 'host', 'arm', 'motion', 'uncertain', 'wrist', 'start']);
  });

  it('states the arm as not readable here, with its terminal hint, and never as met', () => {
    const list = readyChecklist(input({ status: permitted() }));
    const arm = list.items.find((item) => item.id === 'arm');
    expect(arm?.state).toBe('not_readable');
    expect(arm?.sentence).toBe(ARM_NOT_READABLE);
    expect(arm?.hint).toBe(ARM_HINT);
    // Every readable item is met, and the chip says the arm is still the operator's to establish.
    expect(list.ready).toBe(true);
    expect(list.sentence).toContain('the arm must be acquired at the robot');
  });

  it('names a host that is not under the supervisor, with the deploy step', () => {
    const body = robotBody();
    const host = body.host as Record<string, unknown>;
    const status = robotStatus({ host: { ...host, supervised: false, child_pid: null, reason_code: 'robot_host_not_supervised' } });
    const list = readyChecklist(input({ status }));
    expect(list.first_unmet?.id).toBe('host');
    expect(list.sentence).toBe("The robot host is not running under Cake's supervisor (robot_host_not_supervised).");
    expect(list.first_unmet?.hint).toBe(HOST_HINT);
  });

  it('holds motion with the permission view sentence before anything after it', () => {
    const list = readyChecklist(input({ startOff: 'motion is held' }));
    expect(list.first_unmet?.id).toBe('motion');
  });

  it('puts an uncertain command before the camera and Start', () => {
    const unresolved = { command_id: 'c1', command_type: 'start_cycle', block_reason_code: 'command_uncertain' } as unknown as ViewState['unresolved'];
    const list = readyChecklist(input({ status: permitted(), unresolved, wrist: { showing: false, note: 'stalled' } }));
    expect(list.first_unmet?.id).toBe('uncertain');
  });

  it("says the wrist view is not showing, with the runtime's reason code and never the note's age", () => {
    const list = readyChecklist(
      input({ status: permitted(), wrist: { showing: false, note: 'preview_stalled; the runtime last received a frame 12.0 s ago' } }),
    );
    expect(list.first_unmet?.id).toBe('wrist');
    expect(list.sentence).toBe('The wrist camera is not showing video: preview_stalled.');
  });

  it('ends the wrist sentence with no dangling colon when the note carries no reason code', () => {
    for (const note of ['', 'the preview stalled']) {
      const list = readyChecklist(input({ status: permitted(), wrist: { showing: false, note } }));
      expect(list.sentence).toBe('The wrist camera is not showing video.');
    }
  });

  it('prints the fixed UTC time the last snapshot reached this page, never its age (spec 5.4, 6.8)', () => {
    const at = Date.parse('2026-09-27T14:20:05.250Z');
    const view = liveView({ snapshot_at_ms: at });
    const soon = readyChecklist(input({ view, nowMs: at + 16_000, referenceDay: '2026-09-27' }));
    const later = readyChecklist(input({ view, nowMs: at + 60_000, referenceDay: '2026-09-27' }));
    expect(soon.first_unmet?.id).toBe('runtime');
    expect(soon.sentence).toBe("The runtime's last snapshot reached this page at 14:20:05Z.");
    expect(later.items).toEqual(soon.items);
    const otherDay = readyChecklist(input({ view, nowMs: at + 16_000, referenceDay: '2026-09-26' }));
    expect(otherDay.sentence).toBe("The runtime's last snapshot reached this page at 2026-09-27 14:20:05Z.");
  });

  it('judges a rehearsal that serves a robot status on it: a motion hold is never ready', () => {
    // The default fixture holds motion (robot_session_not_confirmed), as the bring-up scenario serves it.
    const held = readyChecklist(input({ sourceKind: 'synthetic', view: liveView({ data_mode: 'synthetic' }), wrist: { showing: false, note: 'no stream' } }));
    expect(held.ready).toBe(false);
    expect(held.first_unmet?.id).toBe('motion');
    expect(held.sentence).not.toContain('Ready');
    expect(held.items.find((item) => item.id === 'arm')?.state).toBe('not_applicable');
    const clear = readyChecklist(
      input({ sourceKind: 'synthetic', view: liveView({ data_mode: 'synthetic' }), status: permitted(), wrist: { showing: false, note: 'no stream' } }),
    );
    expect(clear.ready).toBe(true);
    expect(clear.sentence).toContain("the robot status is the simulation's");
  });

  it('is ready for a rehearsal without calling any robot item met', () => {
    const list = readyChecklist(
      input({ sourceKind: 'synthetic', view: liveView({ data_mode: 'synthetic' }), status: null, wrist: { showing: false, note: 'no stream' } }),
    );
    expect(list.ready).toBe(true);
    expect(list.sentence).toBe('Ready for a rehearsal: no robot is involved.');
    for (const id of ['robot', 'host', 'arm', 'motion', 'wrist'] as const) {
      expect(list.items.find((item) => item.id === id)?.state).toBe('not_applicable');
    }
  });

  it('is never ready while a recording is on the screen', () => {
    const list = readyChecklist(input({ replaying: true }));
    expect(list.first_unmet?.id).toBe('commands');
  });

  // OWNER'S STATUS 2026-09-27, LOW 5 (verifier r3 defect 5): with the robot read failing at load, the host and motion
  // items said "The runtime reports no robot resident" and "No robot resident gates motion here". The runtime reported
  // neither: the read failed, and this runtime has a resident and a motion gate. They say the status was not read.
  it('says the robot status was not read, never that the runtime reports no resident, when the robot read failed', () => {
    const read = { status: null, error: 'robot_unreachable', readAtMs: null };
    const list = readyChecklist(input({ status: null, link: robotLink(read, NOW), permission: motionPermissionView(read, NOW) }));
    const host = list.items.find((item) => item.id === 'host');
    const motion = list.items.find((item) => item.id === 'motion');
    expect(host?.state).toBe('unmet');
    expect(host?.sentence).toBe('The robot status has not been read: robot_unreachable. Whether the host runs under Cake is not known here.');
    expect(motion?.state).toBe('not_readable');
    expect(motion?.sentence).toBe('The robot status has not been read: robot_unreachable. Whether motion is held is not known here.');
    const words = list.items.map((item) => item.sentence).join(' ');
    expect(words).not.toContain('reports no robot resident');
    expect(words).not.toContain('No robot resident gates motion');
    // The chip still names the first unmet item, the robot's own read, and is not ready.
    expect(list.ready).toBe(false);
    expect(list.first_unmet?.id).toBe('robot');
  });

  it('says the robot status was not read yet before its first answer, and keeps the configured-without-robot wording', () => {
    const unread = { status: null, error: null, readAtMs: null };
    const list = readyChecklist(input({ status: null, link: robotLink(unread, NOW), permission: motionPermissionView(unread, NOW) }));
    expect(list.items.find((item) => item.id === 'host')?.sentence).toBe(
      'The robot status has not been read: this runtime has not answered about a robot yet. Whether the host runs under Cake is not known here.',
    );
    // A runtime that answered and reports no configured robot is said as the runtime's report, as before.
    const body = robotBody();
    const none = readyChecklist(input({ status: robotStatus({ ...body, configured: false }) }));
    expect(none.items.find((item) => item.id === 'host')?.sentence).toBe('The runtime reports no robot resident, so no supervised host can be read.');
  });
});
