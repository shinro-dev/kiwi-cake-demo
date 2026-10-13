// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// Which source the console talks to is chosen HERE and nowhere else, so there is one place to look. WHAT the address
// means is decided by `panels/sourceChoice.ts`, which the strip's own selection buttons read as well, so the screen
// and this file can never disagree about which of the three things is on it.
//
// The bare address is the CONFIGURED RUNTIME, and opening it starts nothing: the console asks the runtime who it is,
// reads one snapshot, follows the record stream and reads the finished-run list, and a command is only ever sent by
// somebody pressing a button. `?source=simulation` is the scripted run drawn inside this page, which is a named
// choice and never arrived at by accident.

import '@fontsource/geist-sans/400.css';
import '@fontsource/geist-sans/500.css';
import '@fontsource/geist-sans/600.css';
// The imported design sets the wordmark at weight 700, which no earlier screen used.
import '@fontsource/geist-sans/700.css';
import '@fontsource-variable/jetbrains-mono';
import './styles/tokens.css';
import './styles/base.css';
import './styles/console.css';
// The ported design last, so its tokens and its button win where the two stylesheets meet. The earlier sheets are
// still loaded because the panels in the Diagnostics drawer are the existing ones and are styled by them.
import './design/design.css';

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { DemoScreen } from './design/DemoScreen';
import { Screen } from './design/Screen';
import { screenChoiceFrom } from './design/screenChoice';
import { readBoot } from './api/boot';
import { HttpSource } from './api/http';
import { SyntheticSource } from './api/synthetic';
import type { ConsoleSource } from './api/types';
import { scenarioFrom, sourceChoiceFrom } from './panels/sourceChoice';
import { ConsoleStore } from './store/store';
import { StoreContext } from './store/useConsole';

function chooseSource(): ConsoleSource {
  // `?source=simulation&scenario=one-block` is the one-block demo rehearsed inside the page; it plays nothing until
  // Start is pressed. The plain simulation keeps playing its scripted run on its own.
  if (sourceChoiceFrom(window.location.search) === 'simulation') return new SyntheticSource({ scenario: scenarioFrom(window.location.search) });
  // The API injects the per-launch credential into the page it serves. It is read once, here, kept in memory by the
  // source and presented on mutating requests only. A page opened another way carries none, and what the screen then
  // shows is the server's own refusal, not a guess at one.
  return new HttpSource({ boot: readBoot(document) });
}

const container = document.getElementById('root');
if (container === null) throw new Error('the page has no root element');

const store = new ConsoleStore(chooseSource());
// The simplified demo screen by default; the classic screen behind `?screen=classic` (`design/screenChoice.ts`).
const ChosenScreen = screenChoiceFrom(window.location.search) === 'classic' ? Screen : DemoScreen;

createRoot(container).render(
  <StrictMode>
    <StoreContext value={store}>
      <ChosenScreen />
    </StoreContext>
  </StrictMode>,
);
