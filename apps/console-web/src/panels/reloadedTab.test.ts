// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE HARDWARE CASE, RENDERED.
//
// A non-motion Observe ran on the robot. The controller captured and retained a wrist frame at 1280x720, and the
// console API answered the URL this page builds with 200 image/jpeg. A tab opened after that run folded no
// observation.captured record off the stream, so its observation map was empty, and the camera stage told the
// operator the controller had stored no frame. Everything below is that tab: a snapshot that names the observation,
// NOTHING folded at all, and an `img` that has to appear pointed at the stored frame.
//
// It is a .ts file and builds its elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import type { ConsoleSource } from '../api/types';
import { initialState } from '../model/reducer';
import type { ObservationMeta, ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import { Actions } from './Actions';
import { CameraStage } from './Workspace';

const NOW = 5_000_000;

function text(markup: string): string {
  return markup
    .replace(/<[^>]*>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ');
}

/** A source that serves pictures the way the console API does: relative paths on this page, by observation id. */
const servingSource = {
  kind: 'live' as const,
  label: 'the runtime on this machine',
  frameUrl: (observationId: string, what: string, width: number) => `/api/observations/${observationId}/frame?what=${what}&w=${String(width)}`,
  previewUrl: (role: string) => `/api/preview.mjpg?role=${role}`,
} as unknown as ConsoleSource;

/** Exactly the capture the robot took: a retained wrist frame, 1280x720, with a content digest. */
function wristFrame(extra: Partial<ObservationMeta> = {}): ObservationMeta {
  return {
    observation_id: 'obs-0007',
    source_role: 'wrist',
    media: 'frame',
    width_px: 1280,
    height_px: 720,
    content_digest: 'sha256:4d1f2a9c8b3e',
    source_stamp: null,
    retained: true,
    seq: 17,
    t_wall_utc: '2026-09-21T10:14:02Z',
    ...extra,
  };
}

function storeSnapshot(view: ViewState) {
  const store = new ConsoleStore(new SyntheticSource());
  return { ...store.getSnapshot(), view } as ReturnType<ConsoleStore['getSnapshot']>;
}

function stage(view: ViewState): string {
  return renderToStaticMarkup(
    createElement(CameraStage, { snapshot: storeSnapshot(view), mode: 'live', source: servingSource, nowMs: NOW, showDecisionFrame: false }),
  );
}

describe('a tab that connected or reloaded after the capture', () => {
  // NOTHING FOLDED. This is the whole point of the case: `observations` is empty, exactly as it is in a tab that was
  // not open while the record went past, and the snapshot is the only thing that names a frame.
  const view: ViewState = { ...initialState(), seq_cursor: 21, snapshot_observation: wristFrame() };

  it('draws the frame instead of reporting that nothing was captured', () => {
    const markup = stage(view);
    expect(markup).toContain('/api/observations/obs-0007/frame?what=raw&amp;w=960');
    expect(text(markup)).not.toContain('No capture yet');
    expect(text(markup)).not.toContain('has stored no frame');
  });

  it('points exactly one element at a picture, and it is the stored frame and not the camera', () => {
    const images = stage(view).match(/<img[^>]*>/g) ?? [];
    expect(images).toHaveLength(1);
    expect(images[0]).not.toContain('preview.mjpg');
  });

  it('captions it with the identity and an elapsed age from the time the record carries, not a count of records', () => {
    // "4 record(s) ago" is a true statement about the log and not an answer to how old the picture is. The record
    // carries the wall time the runtime wrote it at, so the PRIMARY age is an elapsed time from that stamp, named as
    // the recording time because the shutter instant is another clock. The count stays as a secondary detail.
    const words = text(stage(view));
    expect(words).toContain('observation obs-0007');
    expect(words).toContain('wrist camera');
    expect(words).toContain('frame freshness: recorded');
    expect(words).toContain('recording time 2026-09-21T10:14:02Z');
    expect(words).toContain('4 record(s) back in the log');
    expect(words).not.toContain('4 record(s) ago');
  });

  it('says the frame was named by the runtime, because a fold and a snapshot are not the same claim', () => {
    expect(text(stage(view))).toContain('named by the runtime snapshot');
  });

  it('keeps preferring what it folded once it has folded something', () => {
    const folding: ViewState = { ...view, observations: { 'obs-0100': wristFrame({ observation_id: 'obs-0100', seq: 20 }) } };
    const markup = stage(folding);
    expect(markup).toContain('/api/observations/obs-0100/frame');
    expect(markup).not.toContain('/api/observations/obs-0007/frame');
  });
});

describe('the three absences, which used to be one sentence', () => {
  it('says nothing was captured only when nothing was, and says which button produces one', () => {
    const words = text(stage(initialState()));
    expect(words).toContain('No capture yet');
    expect(words).toContain('Press Observe');
  });

  it('calls a capture that retained no frame a failure, and not an absence of captures', () => {
    const view: ViewState = { ...initialState(), observations: { 'obs-0007': wristFrame({ retained: false }) } };
    const words = text(stage(view));
    expect(words).toContain('Capture failed');
    expect(words).toContain('no frame was retained');
    expect(words).not.toContain('No capture yet');
  });

  it('states the missing video separately from the still, and never as a missing picture', () => {
    const words = text(stage({ ...initialState(), snapshot_observation: wristFrame() }));
    expect(words).toContain('No continuous preview');
    expect(words).toContain('no live video source attached');
    expect(words).toContain('A still IS present below');
  });
});

describe('the holding declaration beside Observe', () => {
  const view: ViewState = {
    ...initialState(),
    data_mode: 'live',
    capabilities: [{ id: 'observe', state: 'enabled', reason_code: null, reason: null, dependency: null, planned: false }],
  };

  it('says before the click that the button is off and what single answer turns it on', () => {
    const words = text(
      renderToStaticMarkup(createElement(Actions, { snapshot: storeSnapshot(view), store: new ConsoleStore(new SyntheticSource()) })),
    );
    expect(words).toContain('Off until you state whether the gripper is holding the cube.');
    expect(words).toContain('the controller runs a different check depending on the answer');
  });

  it('asks it once, with two choices and not a row of four beside every control', () => {
    const markup = renderToStaticMarkup(
      createElement(Actions, { snapshot: storeSnapshot(view), store: new ConsoleStore(new SyntheticSource()) }),
    );
    expect(markup.match(/Off until you state whether the gripper is holding the cube/g) ?? []).toHaveLength(1);
    // Stated, never asked: no question mark on the control (spec rule 5.7.1).
    expect(markup).not.toContain('?');
    expect(markup.match(/holding the cube\s*<\/button>/g) ?? []).toHaveLength(1);
  });
});
