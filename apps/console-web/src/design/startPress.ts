// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT ONE PRESS OF START DOES, kept out of the component so it can be pinned by a test with a recorded transport.
//
// It is the canonical mission start and nothing else: POST /api/mission/start, through the store, once. It never
// submits `start_cycle`, which is what the header's Start used to send. Before anything is sent the refusals are made
// AGAIN against a freshly read store snapshot, because the screen is up to one poll old and the question at the click
// is what is true now: the same command guards every control makes (`submitRefusal`), then the mission's own
// (`missionStartOff`). A refusal here sends nothing and says why.

import { missionStartOff } from '../model/missionView';
import { runIsActive } from '../model/steps';
import { submitRefusal } from '../panels/Actions';
import type { ConsoleStore } from '../store/store';
import { asSentence } from './actionCard';
import { missionStartReceipt } from './receipts';
import type { Receipt } from './receipts';

export async function pressStart(store: ConsoleStore, confirmed: readonly string[] = []): Promise<Receipt> {
  const fresh = store.getSnapshot();
  const missionNow = missionStartOff({
    mission: fresh.mission,
    missionError: fresh.missionError,
    missionErrorDetail: fresh.missionErrorDetail,
    runActive: runIsActive(fresh.view),
  });
  const refusal =
    submitRefusal(fresh, 'start_cycle') ??
    (missionNow === null ? null : `this changed between the screen and the click, so nothing was sent: ${missionNow}`);
  if (refusal !== null) {
    // A press refused at the click is still a press on this display, and the press list keeps it (spec rule 6.5).
    const receipt: Receipt = { tone: 'refused', text: asSentence(refusal) };
    store.refusedPress('start', receipt.text);
    return receipt;
  }
  return missionStartReceipt(await store.startMission(confirmed));
}
