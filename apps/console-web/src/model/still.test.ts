// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CASE THAT FAILED ON HARDWARE, and the two sentences it was told apart from afterwards.
//
// A non-motion Observe ran on the robot. The controller captured a wrist frame at 1280x720, retained it, and the
// console API answered the exact URL this page builds with 200 image/jpeg and 52412 bytes. A tab opened after that run
// had folded no observation.captured record, held no reference to any frame, and said the controller had stored none.
// The first case here is that tab: a snapshot carrying an observation, nothing folded at all, and a frame to draw.

import { describe, expect, it } from 'vitest';
import { cameraReading } from './camera';
import { initialState } from './reducer';
import { previewView } from './sources';
import { previewReading, stillReading } from './still';
import type { EvidenceGapView, ObservationMeta } from './types';

function capture(extra: Partial<ObservationMeta> = {}): ObservationMeta {
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

function reading(patch: Partial<Parameters<typeof stillReading>[0]> = {}) {
  return stillReading({ observations: {}, snapshot_observation: null, evidence_gaps: [], image_failed_for: null, ...patch });
}

describe('a tab that connected after the capture', () => {
  const snapshotOnly = reading({ snapshot_observation: capture() });

  it('draws the frame the snapshot named rather than saying nothing was captured', () => {
    expect(snapshotOnly.state).toBe('shown');
    expect(snapshotOnly.still?.observation_id).toBe('obs-0007');
    expect(snapshotOnly.sentence).not.toContain('Nothing has been captured');
  });

  it('carries the pixel size, so the picture has a drawing surface and a caption of its own', () => {
    expect(snapshotOnly.still?.width_px).toBe(1280);
    expect(snapshotOnly.still?.height_px).toBe(720);
    expect(snapshotOnly.still?.source_role).toBe('wrist');
  });

  it('says the frame was named by the runtime and not folded here, because the two are not the same claim', () => {
    expect(snapshotOnly.from).toBe('snapshot');
    expect(snapshotOnly.sentence).toContain('as the runtime named it on the snapshot');
  });

  it('hands the card the same shape a folded record has, so nothing downstream has two descriptions of one capture', () => {
    expect(snapshotOnly.still).toEqual(capture());
    // And a fresh fold holds nothing of its own, which is exactly the state the hardware tab was in.
    expect(Object.keys(initialState().observations)).toHaveLength(0);
  });
});

describe('the fold and the snapshot together', () => {
  it('prefers what this tab folded, because a snapshot is up to one poll old', () => {
    const state = reading({
      observations: { 'obs-0100': capture({ observation_id: 'obs-0100', seq: 100 }) },
      snapshot_observation: capture({ observation_id: 'obs-0007', seq: 17 }),
    });
    expect(state.still?.observation_id).toBe('obs-0100');
    expect(state.from).toBe('folded');
  });

  it('keeps showing a frame through a reload, where the fold is empty and only the snapshot is left', () => {
    const before = reading({ observations: { 'obs-0100': capture({ observation_id: 'obs-0100', seq: 100 }) } });
    const afterReload = reading({ snapshot_observation: capture({ observation_id: 'obs-0100', seq: 100 }) });
    expect(before.still?.observation_id).toBe('obs-0100');
    expect(afterReload.still?.observation_id).toBe('obs-0100');
    expect(afterReload.state).toBe('shown');
  });
});

describe('the three states the operator asked to be told apart', () => {
  it('says nothing was captured only when nothing was captured, and says how to get one', () => {
    const none = reading();
    expect(none.state).toBe('none_yet');
    expect(none.words).toBe('No capture yet');
    expect(none.sentence).toContain('Press Observe');
  });

  it('calls a capture that retained no frame a failure, and never no capture yet', () => {
    const state = reading({ observations: { 'obs-0007': capture({ retained: false }) } });
    expect(state.state).toBe('failed');
    expect(state.sentence).toContain('no frame was retained');
    expect(state.sentence).not.toContain('Nothing has been captured');
  });

  it("uses the runtime's own frame_missing gap when it has one, and quotes the record it is at", () => {
    const gaps: EvidenceGapView[] = [{ gap: 'frame_missing', detail: 'the capture returned no image', observation_ids: [], seq: 12 }];
    const state = reading({ evidence_gaps: gaps });
    expect(state.state).toBe('failed');
    expect(state.sentence).toContain('frame_missing gap at record 12');
    expect(state.sentence).toContain('the capture returned no image');
  });

  it('separates a frame whose image could not be served from a capture that produced nothing', () => {
    const state = reading({ snapshot_observation: capture(), image_failed_for: 'obs-0007' });
    expect(state.state).toBe('failed');
    expect(state.words).toBe('Frame not served');
    expect(state.sentence).toContain('could not be served');
    expect(state.sentence).toContain('The capture is not missing');
  });

  it('does not blame the picture on screen for a failure that was recorded against another one', () => {
    const state = reading({ snapshot_observation: capture({ observation_id: 'obs-0200' }), image_failed_for: 'obs-0007' });
    expect(state.state).toBe('shown');
  });
});

describe('the video state, which is not the still state', () => {
  // No preview line has ever arrived, which is the state of this baseline: nothing publishes a preview to attach to.
  const camera = cameraReading({
    camera: previewView(null, null, 0),
    mode: 'live',
    sourceKind: 'live',
    silentForMs: null,
    silentAfterMs: 4000,
  });

  it('says there is no continuous preview, and says the still beside it is present anyway', () => {
    const preview = previewReading(camera, true);
    expect(camera.stream).toBe(false);
    expect(preview.words).toBe('No continuous preview');
    expect(preview.sentence).toContain('no live video source attached');
    expect(preview.sentence).toContain('A still IS present below');
  });

  it('never prints the placeholder this console uses for a value nobody reported', () => {
    expect(previewReading(camera, false).words.toLowerCase()).not.toContain('unavailable');
  });
});
