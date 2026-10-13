// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import { describe, expect, it } from 'vitest';
import { keptPictureAtView, keptPictureView } from './streamDecode';

describe('the last picture kept through a gap', () => {
  const kept = { url: 'data:image/jpeg;base64,AAAA', at_ms: 10_000 };

  it('is not drawn while the stream is showing video', () => {
    expect(keptPictureView(true, kept, 12_000, 'LIVE')).toBeNull();
  });

  it('is drawn through a gap with its age on this page and the runtime reason', () => {
    const view = keptPictureView(false, kept, 22_000, 'paused for a decision capture');
    expect(view?.url).toBe(kept.url);
    expect(view?.words).toBe('STALE · last picture decoded here 12 s ago. paused for a decision capture');
  });

  it('is nothing at all when no picture was ever decoded here', () => {
    expect(keptPictureView(false, null, 22_000, 'stalled')).toBeNull();
  });
});

describe('the last picture kept through a gap, on the DEMO screen (rule 6.8: a fixed time, never an age)', () => {
  const kept = { url: 'data:image/jpeg;base64,AAAA', at_ms: Date.parse('2026-09-27T14:21:46.250Z') };

  it('is drawn through a gap with the fixed time this page decoded it, on this display clock', () => {
    const view = keptPictureAtView(false, kept, '2026-09-27');
    expect(view?.url).toBe(kept.url);
    expect(view?.words).toBe("The last picture this page decoded, at 14:21:46Z (this display's clock); not live.");
    expect(view?.iso).toBe('2026-09-27T14:21:46.250Z');
  });

  it('carries the date when the picture was decoded on another UTC day', () => {
    expect(keptPictureAtView(false, kept, '2026-09-28')?.words).toBe("The last picture this page decoded, at 2026-09-27 14:21:46Z (this display's clock); not live.");
  });

  it('is nothing while the stream shows video or when no picture was decoded here', () => {
    expect(keptPictureAtView(true, kept, '2026-09-27')).toBeNull();
    expect(keptPictureAtView(false, null, '2026-09-27')).toBeNull();
  });
});
