// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE RECEIPT FOR A TRY AGAIN THE RUNTIME REFUSED BECAUSE IT ADMITS NO RETRY AT ALL (supervisor.mission_retry_refusal).
// A LIVE runtime ships with mission.retry off and answers mission_retry_not_enabled; with the switch on and no attended
// label it answers mission_retry_not_attended. The receipt keeps the code and says what it means.

import { describe, expect, it } from 'vitest';
import { missionRetryReceipt } from './receipts';

describe('the receipt for a Try again the runtime does not admit', () => {
  it('says the switch is off for mission_retry_not_enabled', () => {
    const receipt = missionRetryReceipt({ kind: 'refused', reason_code: 'mission_retry_not_enabled', detail: null });
    expect(receipt.tone).toBe('refused');
    expect(receipt.text).toContain('mission_retry_not_enabled');
    expect(receipt.text).toContain('this runtime admits no Retry: its configuration does not set mission.retry');
    expect(receipt.text).toContain('Nothing was started.');
  });

  it('says the attended label is missing for mission_retry_not_attended', () => {
    const receipt = missionRetryReceipt({ kind: 'refused', reason_code: 'mission_retry_not_attended', detail: null });
    expect(receipt.text).toContain('mission_retry_not_attended');
    expect(receipt.text).toContain('this runtime admits no Retry: its configuration names no attended label');
  });

  it('keeps any other refusal as the runtime wrote it', () => {
    const receipt = missionRetryReceipt({ kind: 'refused', reason_code: 'mission_retry_not_offered', detail: null });
    expect(receipt.text).toBe('The runtime did not retry: mission_retry_not_offered. Nothing was started.');
  });
});
