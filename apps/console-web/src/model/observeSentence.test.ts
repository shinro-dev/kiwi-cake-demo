// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import { describe, expect, it } from 'vitest';
import { initialState, foldEvents } from './reducer';
import { captured, record } from './stageFixtures';
import { observeSentence } from './observeSentence';

function layers(runId: string, id: string, landmarks: { valid: boolean; inliers: number | null; rms: number | null }, outline: boolean) {
  return [
    captured(runId, id),
    record(runId, 'perception.result', {
      detector: 'pad-outline-fit',
      observation_ids: [id],
      result: outline ? 'detected' : 'not_detected',
      confidence: null,
      measurements: [{ name: 'boundary_rms', value: 9.1, unit: 'px' }],
    }, { observation_id: id }),
    record(runId, 'perception.result', {
      detector: 'pad-print-landmarks',
      observation_ids: [id],
      result: landmarks.valid ? 'detected' : 'not_detected',
      confidence: null,
      measurements: [
        { name: 'template_available', value: true, unit: null },
        { name: 'pad_frame_valid', value: landmarks.valid, unit: null },
        { name: 'landmark_inliers', value: landmarks.inliers, unit: null },
        { name: 'reprojection_rms', value: landmarks.rms, unit: 'px' },
      ],
    }, { observation_id: id }),
    record(runId, 'perception.result', { detector: 'cube-in-pad-frame', observation_ids: [id], result: 'detected', confidence: null, measurements: [] }, { observation_id: id }),
  ];
}

describe('the Observe sentence', () => {
  it('states the print-landmark pad frame, and the outline fit as a diagnostic that decided nothing', () => {
    const view = foldEvents(initialState(), layers('obs-run', 'o1', { valid: true, inliers: 97, rms: 1.03 }, false));
    const words = observeSentence(view);
    expect(words?.pad).toBe('Pad located by its print (97 landmarks, reprojection 1.03 px).');
    expect(words?.outline).toBe('The outline fit did not converge on this frame; it is a diagnostic and decides nothing.');
    expect(words?.observation_id).toBe('o1');
  });

  it('says the pad print was not resolved, with the count, when the frame has no pad frame', () => {
    const view = foldEvents(initialState(), layers('obs-run', 'o2', { valid: false, inliers: 11, rms: null }, true));
    const words = observeSentence(view);
    expect(words?.pad).toBe('The pad print was not resolved in this frame (11 landmarks matched); no position on the pad is measured from it.');
    expect(words?.outline).toBeNull();
  });

  it('reads the newest frame only, and is null before any pad layer was recorded', () => {
    expect(observeSentence(initialState())).toBeNull();
    const view = foldEvents(initialState(), [
      ...layers('r', 'old', { valid: false, inliers: 3, rms: null }, false),
      ...layers('r', 'new', { valid: true, inliers: 96, rms: 0.97 }, true),
    ]);
    expect(observeSentence(view)?.observation_id).toBe('new');
    expect(observeSentence(view)?.pad).toContain('96 landmarks');
  });
});
