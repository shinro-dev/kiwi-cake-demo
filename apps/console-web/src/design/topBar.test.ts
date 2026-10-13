// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE TOP BAR (spec sections 3.1, 4.2, 4.3 and 5.2; audit C7, C12, C13): one mode mark in one of four words, the robot
// and its connection, the motion permission, ONE readiness verdict computed from Start's own guards, Start (primary only
// when the verdict is Ready), ONE Stop with its fixed danger style that is never primary, and Diagnostics. The verdict
// and Start's `aria-disabled` cannot disagree, because both are read from the same off-reason.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { readiness } from '../model/readiness';
import type { MissionRecord } from '../model/missionRecord';
import { modeMark } from '../model/modeMark';
import { TopBar } from './TopBar';
import type { TopBarProps } from './TopBar';

function failed(): MissionRecord {
  return {
    mission_record_version: '1', mission_id: 'mission-f65c3cf8e99f', state: 'FAILED', data_mode: 'live', performs_motion: true, execution_label: null,
    started_wall_utc: '2026-09-27T14:16:38.847Z', selected_target: 'cube', waiting_reason: null, detail: null, stop_requested: false, stop_requested_wall_utc: null,
    targets: [], activations: [], hold: null, next_actions: ['open_gripper_by_hand', 'return_arm', 'start'],
  };
}

function props(startOff: string | null, mission: MissionRecord | null = null, patch: Partial<TopBarProps> = {}): TopBarProps {
  return {
    mode: modeMark({ dataMode: 'live', sourceKind: 'live', replaying: false }),
    robot: { label: 'LeKiwi bench', connection: 'connected', tone: 'ok', notReadSince: null },
    motion: { words: 'Motion permitted', tone: 'ok' },
    readiness: readiness({ mission, runActive: false, startOff, checklist: [] }),
    start: { label: 'Start', off: startOff, busy: false },
    stop: { off: 'there is no run to stop', busy: false },
    onStart: () => undefined,
    onStop: () => undefined,
    onDiagnostics: () => undefined,
    ...patch,
  };
}

function render(p: TopBarProps): string {
  return renderToStaticMarkup(createElement(TopBar, p));
}

function attr(markup: string, control: string, name: string): string | null {
  const tag = new RegExp(`<button[^>]*data-control="${control}"[^>]*>`).exec(markup)?.[0] ?? '';
  return new RegExp(`${name}="([^"]*)"`).exec(tag)?.[1] ?? null;
}

describe('the top bar', () => {
  it('says Not ready with Start off, and the two agree', () => {
    const markup = render(props('robot motion is held: robot_session_not_confirmed'));
    expect(/data-readiness="([a-z_]+)"/.exec(markup)?.[1]).toBe('not_ready');
    expect(attr(markup, 'start', 'aria-disabled')).toBe('true');
    expect(attr(markup, 'start', 'data-primary')).toBeNull();
  });

  it('says Ready with Start on and primary', () => {
    const markup = render(props(null));
    expect(/data-readiness="([a-z_]+)"/.exec(markup)?.[1]).toBe('ready');
    expect(attr(markup, 'start', 'aria-disabled')).toBe('false');
    expect(attr(markup, 'start', 'data-primary')).toBe('true');
  });

  it('says Steps first beside a block held: Start on, never primary', () => {
    const markup = render(props(null, failed()));
    expect(/data-readiness="([a-z_]+)"/.exec(markup)?.[1]).toBe('steps_first');
    expect(markup).toContain('Open the gripper by hand and take the block out.');
    expect(attr(markup, 'start', 'aria-disabled')).toBe('false');
    expect(attr(markup, 'start', 'data-primary')).toBeNull();
  });

  it('draws exactly one mode mark and one Stop, and the Stop is never primary', () => {
    const markup = render(props(null, null, { stop: { off: null, busy: false } }));
    expect(markup.match(/data-mode-mark="/g)?.length).toBe(1);
    expect(/data-mode-mark="([a-z_]+)"/.exec(markup)?.[1]).toBe('live');
    expect(markup.match(/data-control="stop"/g)?.length).toBe(1);
    expect(attr(markup, 'stop', 'data-primary')).toBeNull();
    expect(attr(markup, 'stop', 'aria-disabled')).toBe('false');
    expect(markup).not.toContain('text-transform');
  });

  it('names the mode in one of four words, and never with the old marks', () => {
    expect(modeMark({ dataMode: 'live', sourceKind: 'live', replaying: false })).toEqual({ id: 'live', word: 'Live' });
    expect(modeMark({ dataMode: 'synthetic', sourceKind: 'live', replaying: false })).toEqual({ id: 'rehearsal', word: 'Rehearsal' });
    expect(modeMark({ dataMode: null, sourceKind: 'synthetic', replaying: false })).toEqual({ id: 'rehearsal', word: 'Rehearsal' });
    expect(modeMark({ dataMode: 'live', sourceKind: 'live', replaying: true })).toEqual({ id: 'recording', word: 'Recording' });
    expect(modeMark({ dataMode: null, sourceKind: 'live', replaying: false })).toEqual({ id: 'not_recorded', word: 'Mode not recorded' });
    const text = render(props(null, null, { mode: modeMark({ dataMode: 'synthetic', sourceKind: 'synthetic', replaying: false }) }));
    for (const old of ['Live demonstration', 'LIVE RUNTIME', 'PHYSICAL', 'SYNTHETIC']) expect(text).not.toContain(old);
  });

  it('marks a robot not read since a fixed time, never with an age', () => {
    const markup = render(props(null, null, { robot: { label: 'LeKiwi bench', connection: 'connected', tone: 'ok', notReadSince: '14:30:02Z' } }));
    expect(markup).toContain('Not read since 14:30:02Z');
    expect(markup).not.toMatch(/\bago\b/);
  });
});
