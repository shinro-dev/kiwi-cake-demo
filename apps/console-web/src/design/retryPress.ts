// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT ONE PRESS OF TRY AGAIN DOES, kept out of the component so it can be pinned by a test, as Start's is.
//
// It is POST /api/mission/retry through the store, once, naming the mission whose hold the operator saw. Before
// anything is sent the refusals are made AGAIN against a freshly read store snapshot (`retryRefusal`, the same guards
// Start and every control make), because the screen is up to one poll old and the question at the click is what is
// true now. A refusal here sends nothing and says why.

import { retryRefusal } from '../panels/Actions';
import type { ConsoleStore } from '../store/store';
import { asSentence } from './actionCard';
import { missionRetryReceipt } from './receipts';
import type { Receipt } from './receipts';

export async function pressRetry(store: ConsoleStore): Promise<Receipt> {
  const fresh = store.getSnapshot();
  const refusal = retryRefusal(fresh, Date.now());
  const missionId = fresh.mission?.mission_id ?? null;
  if (refusal !== null || missionId === null) {
    const why = refusal ?? 'no mission is on the record';
    const text = asSentence(`this is not true at the click, so nothing was sent: ${why}`);
    // A press refused at the click is still a press on this display, and the press list keeps it (spec rule 6.5).
    store.refusedPress('retry', text);
    return { tone: 'refused', text };
  }
  return missionRetryReceipt(await store.retryMission(missionId));
}
