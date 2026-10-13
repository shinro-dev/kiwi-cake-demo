// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A CAMERA PANEL SAYS LIVE ONLY OVER A PICTURE IT DECODED.
//
// The robot stack showed the defect: the wrist chip read LIVE and "stream advancing" from the runtime's frame metadata
// while the picture area was black. These pin the panel model over each state its own element can be in, and pin that
// the footer is labelled as what it is: delivery metadata, not the picture.
//
// And a dropped attempt lets go of its stream. Chrome keeps a multipart request open after its <img> is taken off the
// page, and dropped attempts once held every connection the browser allows to the server, so Stop could not be sent.
// The browser half of that is tests/test-browser-camera.sh; the rule the ref callback applies is pinned here.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import type { ConsoleSource, PreviewSample } from '../api/types';
import { SyntheticSource } from '../api/synthetic';
import { applySnapshot, initialState } from '../model/reducer';
import { decoded, errored, firstAttempt, sampled } from '../model/streamDecode';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { cameraPanel } from './cameras';
import { Screen } from './Screen';
import { releaseDetachedPicture } from './useStreamDecode';
import type { DetachablePicture } from './useStreamDecode';

const source = {
  ...new SyntheticSource(),
  previewUrl: (role: string) => `/api/preview.mjpg?role=${role}`,
  frameUrl: (id: string) => `/api/observations/${id}/frame`,
} as unknown as ConsoleSource;

function streaming(): StoreSnapshot {
  const store = new ConsoleStore(new SyntheticSource());
  const base = store.getSnapshot();
  const sample = { role: 'wrist', state: 'connected', available: true, measured_fps: 8, seq: 12 } as unknown as PreviewSample;
  return {
    ...base,
    view: { ...applySnapshot(initialState(), runningSnapshot as unknown as Snapshot), data_mode: 'live' },
    telemetry: {
      ...base.telemetry,
      previews: { wrist: sample },
      preview_received_at_ms_by_role: { wrist: 1000 },
      preview_frame_at_ms_by_role: { wrist: 1000 },
      preview_frame_times_by_role: { wrist: [800, 900, 1000] },
    },
  };
}

describe('the chip rests on the decoded element', () => {
  it('says CONNECTING, covers the picture and never says advancing before a decode', () => {
    const panel = cameraPanel(streaming(), 'wrist', source, 1100, firstAttempt(1000));
    expect(panel.word).toBe('CONNECTING');
    expect(panel.showingVideo).toBe(false);
    expect(panel.streamUrl).toBe('/api/preview.mjpg?role=wrist&attempt=1');
    expect(panel.cover?.note).toContain('has not decoded one');
    expect(panel.note).not.toContain('advancing');
  });

  it('says LIVE once this attempt decoded a frame, with no cover', () => {
    const panel = cameraPanel(streaming(), 'wrist', source, 1100, decoded(firstAttempt(1000), 640, 1050));
    expect(panel.word).toBe('LIVE');
    expect(panel.note).toBe('decoded in this tab');
    expect(panel.cover).toBeNull();
  });

  it('says a decoded picture is nearly black rather than leaving a dark panel unexplained', () => {
    const dark = sampled(decoded(firstAttempt(1000), 640, 1050), 'd1', 3, 100, 1100);
    const panel = cameraPanel(streaming(), 'wrist', source, 1100, dark);
    expect(panel.word).toBe('LIVE');
    expect(panel.note).toContain('nearly black');
  });

  it('says NO PICTURE after a failed attempt, with the attempt and when the next one is', () => {
    const failed = errored(firstAttempt(1000), 1100);
    const panel = cameraPanel(streaming(), 'wrist', source, 1100, failed);
    expect(panel.word).toBe('NO PICTURE');
    expect(panel.showingVideo).toBe(false);
    expect(panel.cover?.title).toBe('No picture decoded in this tab');
    expect(panel.cover?.note).toContain('attempt 1');
    expect(panel.cover?.note).toContain('The next attempt is in 1 s');
  });

  /** One decoded attempt whose picture was sampled `count` times, 2 s apart, always the same, while frames arrived. */
  function unchangedPicture(count: number, luminance: number) {
    let state = decoded(firstAttempt(1000), 640, 1050);
    for (let round = 0; round < count; round += 1) state = sampled(state, 'same', luminance, 100, 2000 + round * 2000);
    return state;
  }

  // CHANGED DELIBERATELY. This used to pin NOT ADVANCING, a cover and a retry for a picture that stopped changing while
  // frames kept arriving. On a static view that dropped a good picture and flapped LIVE, NOT ADVANCING, LIVE every few
  // seconds, and the cover said the stream "has ended" on evidence that does not show it.
  it('keeps LIVE and the picture over an unchanged picture, and says only that it has not changed', () => {
    const panel = cameraPanel(streaming(), 'wrist', source, 1100, unchangedPicture(4, 80));
    expect(panel.word).toBe('LIVE');
    expect(panel.showingVideo).toBe(true);
    expect(panel.cover).toBeNull();
    expect(panel.streamUrl).toBe('/api/preview.mjpg?role=wrist&attempt=1');
    expect(panel.note).toBe('decoded in this tab; the picture has not changed for 6 s while the runtime reports new frames');
    expect(panel.note).not.toContain('ended');
  });

  it('keeps a nearly black unchanged picture as a decoded picture and says both facts', () => {
    const panel = cameraPanel(streaming(), 'wrist', source, 1100, unchangedPicture(12, 3));
    expect(panel.word).toBe('LIVE');
    expect(panel.cover).toBeNull();
    expect(panel.note).toBe(
      'decoded in this tab; the picture is nearly black; the picture has not changed for 22 s while the runtime reports new frames',
    );
  });

  it('says the stream broke, as a sentence, when the element errored after a decode', () => {
    const broke = errored(decoded(firstAttempt(1000), 640, 1050), 1100);
    const panel = cameraPanel(streaming(), 'wrist', source, 1100, broke);
    expect(panel.showingVideo).toBe(false);
    expect(panel.word).not.toBe('LIVE');
    const note = panel.cover?.note ?? '';
    expect(note).toBe('The stream broke after it had shown a picture (attempt 1). The next attempt is in 1 s.');
  });
});

describe('the footer is delivery metadata', () => {
  it('is labelled as such on the rendered screen, under both cameras', () => {
    const store = new ConsoleStore(new SyntheticSource());
    (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...streaming(), sourceKind: 'live' };
    const markup = renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(Screen)));
    expect(markup.split('Delivery metadata').length - 1).toBe(2);
    // The metadata alone says frames are arriving, and the chip still does not say LIVE: nothing was decoded here.
    expect(markup).not.toContain('stream advancing');
  });
});

describe('a dropped attempt lets go of its stream', () => {
  /** A picture element's stand-in: whether it is on the page, and its attributes. */
  function picture(connected: boolean, src: string | null): DetachablePicture & { attributes: Map<string, string> } {
    const attributes = new Map<string, string>();
    if (src !== null) attributes.set('src', src);
    return {
      isConnected: connected,
      attributes,
      hasAttribute: (name) => attributes.has(name),
      removeAttribute: (name) => {
        attributes.delete(name);
      },
    };
  }

  it('ends the request of an element that has left the page', () => {
    const gone = picture(false, '/api/preview.mjpg?role=wrist&attempt=3');
    expect(releaseDetachedPicture(gone)).toBe(true);
    expect(gone.attributes.has('src')).toBe(false);
  });

  it('never blanks an element that is still on the page, which is what React development mode hands back', () => {
    // StrictMode calls the ref with null and then with the same element, without taking it off the page.
    const showing = picture(true, '/api/preview.mjpg?role=wrist&attempt=1');
    expect(releaseDetachedPicture(showing)).toBe(false);
    expect(showing.attributes.get('src')).toBe('/api/preview.mjpg?role=wrist&attempt=1');
  });

  it('does nothing to an element with no request left to end', () => {
    expect(releaseDetachedPicture(picture(false, null))).toBe(false);
  });
});
