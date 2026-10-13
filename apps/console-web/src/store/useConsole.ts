// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import { createContext, useContext, useSyncExternalStore } from 'react';
import { nowMs, subscribeNow } from './clock';
import type { ConsoleStore, StoreSnapshot } from './store';

export const StoreContext = createContext<ConsoleStore | null>(null);

export function useStore(): ConsoleStore {
  const store = useContext(StoreContext);
  if (store === null) throw new Error('the console store is not mounted');
  return store;
}

export function useConsole(): StoreSnapshot {
  const store = useStore();
  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}

/**
 * The shared reading of this computer's clock, advancing once a second.
 *
 * Every age on the screen comes from this and from no other reading, so ages advance while the screen is idle and two
 * panels rendered together always agree. A panel that is handed a `nowMs` for a test uses that instead, the way
 * `SourcesPanels` already does.
 */
export function useNowMs(): number {
  return useSyncExternalStore(subscribeNow, nowMs, nowMs);
}
