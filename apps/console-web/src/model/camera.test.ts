// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The camera card said DEGRADED at every simulation this console has ever been opened in, because nothing may be
// streamed outside live mode and the panel called anything it could not stream degraded. These cases pin the
// difference between a camera that is broken, a camera that was never configured, and a screen that is not showing a
// stream for a reason of its own.

import { describe, expect, it } from 'vitest';
import { cameraReading, captureAgeSentence, proxiedMediaUrl } from './camera';
import { previewView } from './sources';
import type { PreviewSample } from '../api/types';

const NOW = 5_000_000;

function preview(extra: Partial<PreviewSample> = {}): PreviewSample {
  return { role: 'wrist', seq: 4, capture_clock: null, receive_age_ms: 22, measured_fps: 9.1, available: true, reason: null, ...extra };
}

function reading(input: {
  sample: PreviewSample | null;
  mode: 'live' | 'synthetic' | 'replay';
  sourceKind: 'live' | 'synthetic';
  receivedAtMs?: number | null;
  silentForMs?: number | null;
}) {
  const received = input.receivedAtMs === undefined ? NOW : input.receivedAtMs;
  return cameraReading({
    camera: previewView(input.sample, received, NOW),
    mode: input.mode,
    sourceKind: input.sourceKind,
    silentForMs: input.silentForMs ?? 0,
    silentAfterMs: 4000,
  });
}

describe('a camera that was never configured', () => {
  it('is an expected fixture state in a simulation and is not called degraded', () => {
    const result = reading({
      sample: preview({ state: 'disconnected', available: false, reason: 'preview_not_configured' }),
      mode: 'synthetic',
      sourceKind: 'live',
    });
    expect(result.state).toBe('idle');
    expect(result.words).toBe('No camera configured');
    expect(result.expected).toBe(true);
    expect(result.stream).toBe(false);
    expect(result.sentence).toContain('there is nothing to connect to');
    expect(result.sentence).toContain('preview_not_configured');
  });

  it('says the same thing on a live runtime launched without a camera, because that is the same fact', () => {
    const result = reading({
      sample: preview({ state: 'disconnected', available: false, reason: 'preview_source_not_attached' }),
      mode: 'live',
      sourceKind: 'live',
    });
    expect(result.state).toBe('idle');
    expect(result.words).toBe('No camera configured');
    expect(result.stream).toBe(false);
  });

  it('says the script inside this page carries no camera at all, rather than reporting on a runtime', () => {
    const result = reading({ sample: null, receivedAtMs: null, mode: 'synthetic', sourceKind: 'synthetic' });
    expect(result.state).toBe('idle');
    expect(result.sentence).toContain('carries no camera bytes');
    expect(result.expected).toBe(true);
  });
});

describe('a camera that something is wrong with', () => {
  it('reports a broken transport as failed, with the runtime reason', () => {
    const result = reading({ sample: preview({ state: 'failed', available: false, reason: 'device_read_error' }), mode: 'live', sourceKind: 'live' });
    expect(result.state).toBe('failed');
    expect(result.expected).toBe(false);
    expect(result.sentence).toContain('device_read_error');
  });

  it('reports a source the runtime is not holding open as blocked rather than as absent', () => {
    const result = reading({ sample: preview({ state: 'disconnected', available: false, reason: 'open_failed' }), mode: 'live', sourceKind: 'live' });
    expect(result.state).toBe('blocked');
    expect(result.words).toBe('Not connected');
  });

  it('reports frames that arrived and stopped as stale at the runtime', () => {
    const result = reading({ sample: preview({ state: 'stalled', available: false, reason: 'no_frame_within_the_fresh_window' }), mode: 'live', sourceKind: 'live' });
    expect(result.state).toBe('stale');
    expect(result.words).toBe('Stalled at the runtime');
  });

  it('reports a line that stopped reaching this tab as silent to this tab, measured in the browser clock', () => {
    const result = reading({ sample: preview({ state: 'connected' }), mode: 'live', sourceKind: 'live', silentForMs: 9000 });
    expect(result.state).toBe('stale');
    expect(result.words).toBe('Silent to this tab');
    expect(result.sentence).toContain("this computer's clock");
    expect(result.stream).toBe(false);
  });
});

describe('a camera that is publishing', () => {
  it('streams only in live mode', () => {
    const live = reading({ sample: preview({ state: 'connected' }), mode: 'live', sourceKind: 'live' });
    expect(live.state).toBe('active');
    expect(live.stream).toBe(true);
    expect(live.role).toBe('wrist');
  });

  it('holds the stream back beside simulated records and says why, instead of calling the camera degraded', () => {
    const result = reading({ sample: preview({ state: 'connected' }), mode: 'synthetic', sourceKind: 'live' });
    expect(result.stream).toBe(false);
    expect(result.words).toBe('Stream held back');
    expect(result.state).toBe('idle');
    expect(result.sentence).toContain('would read as a picture of them');
  });

  it('shows a replay as a recording, with no stream and nothing broken', () => {
    const result = reading({ sample: preview({ state: 'connected' }), mode: 'replay', sourceKind: 'live' });
    expect(result.state).toBe('idle');
    expect(result.words).toBe('Recorded run');
    expect(result.expected).toBe(true);
  });

  it('carries the count of previews the camera stood aside for, which is not a fault', () => {
    const result = reading({ sample: preview({ state: 'connected', skipped_for_mission: 7 }), mode: 'live', sourceKind: 'live' });
    expect(result.yielded_for_mission).toBe(7);
    expect(result.state).toBe('active');
  });

  it('waits rather than reporting a fault when no preview line has arrived at all', () => {
    const result = reading({ sample: null, receivedAtMs: null, mode: 'live', sourceKind: 'live' });
    expect(result.state).toBe('waiting');
    expect(result.words).toBe('No preview line yet');
  });
});

describe('how long ago the picture was taken', () => {
  it('is not known while nothing has measured the two clocks against each other', () => {
    const camera = previewView(
      preview({
        state: 'connected',
        capture_age_ms: 44,
        capture_age_reason: 'no_clock_relation_between_source_and_laptop',
        capture_clock: { session: 'camera-a', seq: 9, sample_mono_ns: 7, clock_domain: 'pi_monotonic' },
      }),
      NOW,
      NOW,
    );
    expect(camera.capture_age_ms).toBeNull();
    const sentence = captureAgeSentence(camera);
    expect(sentence).toContain('not known');
    expect(sentence).toContain('no console may subtract two monotonic clocks on two machines');
    expect(sentence).toContain('no_clock_relation_between_source_and_laptop');
  });

  it('states the measured relation and its uncertainty when one licensed the age', () => {
    const camera = previewView(
      preview({
        state: 'connected',
        capture_age_ms: 44,
        capture_clock: { session: 'camera-a', seq: 9, sample_mono_ns: 7, clock_domain: 'pi_monotonic' },
        clock_relation: {
          domains: ['pi_monotonic', 'laptop_monotonic'],
          offset_ns: 12_000_000,
          uncertainty_ns: 2_500_000,
          method: 'ptp_probe',
          measured_at: null,
        },
      }),
      NOW,
      NOW,
    );
    expect(camera.capture_age_ms).toBe(44);
    expect(captureAgeSentence(camera)).toContain('2.5 ms');
  });
});

describe('the addresses a picture may be loaded from', () => {
  it('loads the console API path the runtime serves a stored frame at', () => {
    expect(proxiedMediaUrl('/api/observations/obs-1/frame?what=raw&w=800')).toEqual({ url: '/api/observations/obs-1/frame?what=raw&w=800', refusal: null });
    expect(proxiedMediaUrl('/api/preview.mjpg?role=wrist').url).toBe('/api/preview.mjpg?role=wrist');
    expect(proxiedMediaUrl(null)).toEqual({ url: null, refusal: null });
  });

  it('refuses an address that names a host, so no picture is fetched around the API on this page', () => {
    for (const url of ['http://another-host.invalid/api/observations/obs-1/frame', '//cdn.invalid/frame.jpg', 'file:///frames/obs-1.png']) {
      const result = proxiedMediaUrl(url);
      expect(result.url, url).toBeNull();
      expect(result.refusal, url).toContain('outside the console API');
    }
  });

  it('refuses an address that carries a credential, because a URL is kept in history and in every log on the way', () => {
    const result = proxiedMediaUrl('/api/observations/obs-1/frame?what=raw&token=abc123');
    expect(result.url).toBeNull();
    expect(result.refusal).toContain('credential-shaped parameter (token)');
  });
});
