// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHOSE FRAME IS THIS. The runtime publishes one telemetry topic per camera, so the role is read from the TOPIC and a
// reader asking for the front camera can never be handed the wrist picture. The older single-stream line is still
// accepted, and there the record's own role decides. Getting this wrong does not fail: it shows the wrong room.

import { describe, expect, it } from 'vitest';
import type { PreviewSample, TelemetryMessage } from './types';
import { previewRoleOf, previewSampleOf, PREVIEW_ROLES } from './types';

const sample = (role: string, seq: number): PreviewSample => ({ role, seq, available: true } as unknown as PreviewSample);

describe('which camera a telemetry line is about', () => {
  it('reads the role from the topic', () => {
    for (const role of PREVIEW_ROLES) {
      const line = { [`preview:${role}`]: sample(role, 1) } as unknown as TelemetryMessage;
      expect(previewRoleOf(line, previewSampleOf(line))).toBe(role);
    }
  });

  it('trusts the topic over a record that disagrees with it', () => {
    // A mislabelled record must not move a frame to another camera: the topic is what the runtime routed it on.
    const line = { 'preview:front': sample('wrist', 3) } as unknown as TelemetryMessage;
    expect(previewRoleOf(line, previewSampleOf(line))).toBe('front');
  });

  it('falls back to the record’s own role on a single-stream line', () => {
    const line = { preview: sample('front', 4) } as unknown as TelemetryMessage;
    expect(previewRoleOf(line, previewSampleOf(line))).toBe('front');
  });

  it('defaults a single-stream line with no usable role to the wrist camera', () => {
    const line = { preview: sample('nonsense', 5) } as unknown as TelemetryMessage;
    expect(previewRoleOf(line, previewSampleOf(line))).toBe('wrist');
  });

  it('is not a preview line at all when it carries the tap', () => {
    const line = { tap: {} } as unknown as TelemetryMessage;
    expect(previewSampleOf(line)).toBeNull();
    expect(previewRoleOf(line, null)).toBeNull();
  });

  it('returns each camera’s own sample, never the other’s', () => {
    const wrist = { 'preview:wrist': sample('wrist', 1) } as unknown as TelemetryMessage;
    const front = { 'preview:front': sample('front', 9) } as unknown as TelemetryMessage;
    expect(previewSampleOf(wrist)?.seq).toBe(1);
    expect(previewSampleOf(front)?.seq).toBe(9);
  });
});
