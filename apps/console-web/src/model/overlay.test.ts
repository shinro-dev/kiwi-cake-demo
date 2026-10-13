// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// An overlay is a set of measurements taken in ONE frame's pixels. These cases pin the two ways it can end up drawn
// somewhere it was not measured: over a frame of another observation, which happens in every run for as long as the
// pin has moved and the perception behind it has not been fetched yet, and over a frame whose pixel size nobody
// recorded. In both the layers go off and carry the reason, because an overlay that is simply absent is read as a
// measurement that came back empty.

import { describe, expect, it } from 'vitest';
import { LAYER_NAMES, overlayForFrame, overlayForShown, overlayLayers, overlayWithheld } from './overlay';
import { parsePerception } from './observation';

const SIZE = { width: 640, height: 480 };

function perception(extra: Record<string, unknown> = {}) {
  return parsePerception({
    pad: { detected: true, corners_px: [[10, 10], [600, 12], [610, 400], [20, 402]] },
    pad_by_template: { pad_frame_valid: true, pad_identity: 'pad-a', landmark_inliers: 18 },
    cube: { detected: true, box_px: [100, 100, 160, 160], contact_px: [130, 158] },
    cube_by_template: { cube_center_pad_m: [0.05, -0.02], inside_pad_outline: true },
    ...extra,
  });
}

describe('which observation an overlay belongs to', () => {
  it('carries the identity it was built for', () => {
    expect(overlayLayers(perception(), SIZE, 'obs-0007').bound_to).toBe('obs-0007');
    expect(overlayLayers(null, SIZE, 'obs-0007').bound_to).toBe('obs-0007');
    expect(overlayLayers(null, null, 'obs-0007').bound_to).toBe('obs-0007');
  });

  it('draws nothing over a picture of another observation, and says which two they are', () => {
    const overlay = overlayLayers(perception(), SIZE, 'obs-older');
    expect(overlay.layers.cube_box.valid).toBe(true);

    const withheld = overlayForFrame(overlay, 'obs-newer');
    for (const name of LAYER_NAMES) {
      expect(withheld.layers[name].valid, name).toBe(false);
      expect(withheld.layers[name].reason, name).toContain('obs-older');
      expect(withheld.layers[name].reason, name).toContain('obs-newer');
    }
    expect(withheld.homography).toBeNull();
    expect(withheld.pad_frame_valid).toBeNull();
  });

  it('draws the layers when the picture and the measurements are the same observation', () => {
    const overlay = overlayForFrame(overlayLayers(perception(), SIZE, 'obs-0007'), 'obs-0007');
    expect(overlay.layers.cube_box.valid).toBe(true);
    expect(overlay.layers.pad_outline.valid).toBe(true);
    expect(overlay.homography).not.toBeNull();
  });

  it('refuses to draw a perception over a picture that states no observation at all', () => {
    const withheld = overlayForFrame(overlayLayers(perception(), SIZE, 'obs-0007'), null);
    expect(withheld.layers.cube_box.valid).toBe(false);
    expect(withheld.layers.cube_box.reason).toContain('no stated observation');
  });

  it('leaves a frame with no perception exactly as it was, with its own reason', () => {
    const overlay = overlayForFrame(overlayLayers(null, null, 'obs-0007'), 'obs-0007');
    expect(overlay.layers.cube_box.valid).toBe(false);
    expect(overlay.layers.cube_box.reason).toContain('pixel size of this frame was not recorded');
  });

  it('withholds every layer for one stated reason when the caller has one of its own', () => {
    const withheld = overlayWithheld('the served picture is not the shape the record states', 'obs-0007');
    expect(withheld.bound_to).toBe('obs-0007');
    for (const name of LAYER_NAMES) expect(withheld.layers[name].valid, name).toBe(false);
    expect(withheld.layers.jaw_window.reason).toBe('the served picture is not the shape the record states');
  });
});

describe('which layers a frame carries', () => {
  it('keeps the cube layers when the pad frame is missing, and takes only the pad ones away', () => {
    const overlay = overlayLayers(perception({ pad_by_template: { pad_frame_valid: false, reason: 'pad_not_detected' } }), SIZE, 'obs-1');
    expect(overlay.layers.cube_box.valid).toBe(true);
    expect(overlay.layers.cube_contact.valid).toBe(true);
    expect(overlay.layers.pad_outline.valid).toBe(false);
    expect(overlay.layers.pad_outline.reason).toBe('pad_not_detected');
    expect(overlay.layers.pad_projection.valid).toBe(false);
    expect(overlay.pad_frame_valid).toBe(false);
  });

  it('says nothing is known about the pad frame when no perception was served, rather than that it is invalid', () => {
    expect(overlayLayers(null, SIZE, 'obs-1').pad_frame_valid).toBeNull();
  });
});

describe('an overlay for a picture whose perception this page has not read (verify-r4 must_fix 3)', () => {
  it('says the perception of the shown frame has not been read, never that one was served for no observation', () => {
    const withheld = overlayForFrame(overlayLayers(null, SIZE, null), 'obs-0007');
    for (const name of LAYER_NAMES) {
      expect(withheld.layers[name].valid, name).toBe(false);
      expect(withheld.layers[name].reason, name).toBe('the perception of obs-0007 has not been read by this page');
    }
  });

  it('draws from the perception read for the shown frame, and names the frame in every other case', () => {
    const read = overlayForShown({ observation_id: 'obs-0007', perception: perception(), loading: false }, SIZE, 'obs-0007');
    expect(read.layers.cube_box.valid).toBe(true);
    expect(read.bound_to).toBe('obs-0007');

    const reading = overlayForShown({ observation_id: 'obs-0007', perception: null, loading: true }, SIZE, 'obs-0007');
    expect(reading.layers.cube_box.reason).toBe('the perception of obs-0007 is being read by this page');

    const failed = overlayForShown({ observation_id: 'obs-0007', perception: null, loading: false }, SIZE, 'obs-0007');
    for (const name of LAYER_NAMES) expect(failed.layers[name].reason, name).toBe('the perception of obs-0007 could not be read by this page');
    expect(failed.bound_to).toBe('obs-0007');
    // Nothing can be placed without the frame's pixel size, whatever was read.
    expect(overlayForShown({ observation_id: 'obs-0007', perception: null, loading: false }, null, 'obs-0007').layers.cube_box.reason).toContain('pixel size of this frame was not recorded');

    // A perception read for ANOTHER frame: the two identities, as before.
    const other = overlayForShown({ observation_id: 'obs-older', perception: perception(), loading: false }, SIZE, 'obs-0007');
    expect(other.layers.cube_box.reason).toContain('obs-older');
    expect(other.layers.cube_box.reason).toContain('obs-0007');

    // No perception in hand for any frame, or a read for another frame that brought none back: not read for this one.
    for (const fetched of [
      { observation_id: null, perception: null, loading: false },
      { observation_id: 'obs-older', perception: null, loading: false },
      { observation_id: 'obs-older', perception: null, loading: true },
    ]) {
      const overlay = overlayForShown(fetched, SIZE, 'obs-0007');
      expect(overlay.layers.cube_box.reason).toBe('the perception of obs-0007 has not been read by this page');
      expect(overlay.layers.cube_box.reason).not.toMatch(/served for/);
    }
  });
});
