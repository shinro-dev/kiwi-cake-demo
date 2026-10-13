// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHICH OF THE THREE THINGS THIS PAGE IS SHOWING, decided from the address and from nothing else.
//
// THE BARE ADDRESS IS THE CONFIGURED RUNTIME. It used to be the scripted run drawn inside this page, and only
// `?source=live` reached a runtime. That is the one arrangement on this screen that can actually mislead somebody:
// the page the API itself served looked exactly like the console and was a fixture, so every absence on it, every
// phase that had not been reached and every camera that reported nothing was a statement about a script rather than
// about the robot in the room. Opening the configured runtime starts nothing: the console asks it who it is, reads a
// snapshot, follows the record stream and reads the finished-run list, and a command is only ever sent by somebody
// pressing a button.
//
// THE OTHER TWO ARE NAMED CHOICES AND ARE NEVER ARRIVED AT BY ACCIDENT. The simulation is the scripted run inside
// this page, chosen with `?source=simulation`; the replay is a recorded run, chosen by its run id in the finished-run
// list, which is why it is not a value of this parameter at all. Each one says what it is in the strip, in the banner
// under it and in the badge that carries the runtime's own data mode.
//
// A VALUE THIS MODULE DOES NOT KNOW IS THE RUNTIME. A mistyped source must not quietly become a fixture, which is the
// failure this whole module exists to prevent, so anything that is not exactly the simulation resolves to the runtime.

/** Which source this page reads. `replay` is not here: a recording is chosen by run id, not by the address. */
export type SourceChoice = 'runtime' | 'simulation';

/** The value of `?source=` that names the scripted run inside this page. */
export const SIMULATION_PARAM = 'simulation';

/**
 * The value of `?source=` that names the runtime.
 *
 * It is kept although the bare address now means the same thing, because links and notes written before this exist
 * and must not change what they open.
 */
export const RUNTIME_PARAM = 'live';

/** What the address asks for. Anything that is not the simulation is the runtime, including nothing at all. */
export function sourceChoiceFrom(search: string): SourceChoice {
  return new URLSearchParams(search).get('source') === SIMULATION_PARAM ? 'simulation' : 'runtime';
}

/**
 * The scenarios the simulation can play, each by its name in `?scenario=`. `one-block` is the one-block demo rehearsed
 * inside the page; the others are the scenario catalogue of `api/syntheticScenarios.ts`: one per failure mode and per
 * success of the live session of 2026-09-26, and one per behaviour the overnight tracks add. Nothing plays in any of
 * them until the operator presses what starts it. The names live here, beside the address that selects them, so that
 * reading the address never loads the scripts.
 */
export const SCENARIO_NAMES = [
  'one-block',
  'near-block-grasp-miss',
  'far-block-success',
  'far-left-yawed-success',
  'not-placeable-side',
  'not-placeable-budget',
  'align-no-proposal',
  'align-clipped-top',
  'observe-landmarks-only',
  'preview-stall',
  'preview-not-configured',
  'abort-swap-repeated',
  'bring-up-not-confirmed',
  'host-not-supervised',
  'scene-scan',
  'hold-then-retry-placed',
  'hold-live-default',
  'tampered-refused-before-transaction',
  'undo-to-inert',
  'swap-then-stopped-holding',
] as const;

export type SyntheticScenario = (typeof SCENARIO_NAMES)[number];

/** The value of `?scenario=` that names the one-block rehearsal. It means nothing without `?source=simulation`. */
export const ONE_BLOCK_SCENARIO: SyntheticScenario = 'one-block';

/** Whether a text is the name of a scenario. A near miss is not one. */
export function isScenarioName(value: string | null): value is SyntheticScenario {
  return value !== null && (SCENARIO_NAMES as readonly string[]).includes(value);
}

/** Which scenario the address names, or null for the plain scripted run. An unknown name is nothing, never a guess. */
export function scenarioFrom(search: string): SyntheticScenario | null {
  const params = new URLSearchParams(search);
  if (params.get('source') !== SIMULATION_PARAM) return null;
  const name = params.get('scenario');
  return isScenarioName(name) ? name : null;
}

/** The address that opens a scenario. It always names the simulation, because the scenario means nothing without it. */
export function scenarioUrl(name: SyntheticScenario): string {
  return `?source=${SIMULATION_PARAM}&scenario=${name}`;
}

/**
 * The words the button for each choice carries. They name the thing, never the mechanism.
 *
 * They are short because these two buttons sit in the sticky strip, whose height comes straight out of the screen
 * below it. The sentence that says what each one actually does is `CHOICE_TITLE`, on the control itself.
 */
export const CHOICE_LABEL: Record<SourceChoice, string> = {
  runtime: 'the runtime',
  simulation: 'the simulation',
};

/** What each choice does, in a sentence, on the control that makes it. */
export const CHOICE_TITLE: Record<SourceChoice, string> = {
  runtime:
    'Read the runtime this console is configured for, over the API on this machine. It reads only: nothing is started and no command is sent until a button below is pressed. The page reloads, because the selection is made before the store exists.',
  simulation:
    'Run the scripted simulation drawn inside this page. There is no runtime, no robot and no camera behind it, and nothing outside the browser is involved. The page reloads.',
};

/** The address that selects a choice. The runtime keeps a named value rather than a bare address, so a screenshot of the address bar says which of the three this is. */
export function searchFor(choice: SourceChoice): string {
  return `?source=${choice === 'simulation' ? SIMULATION_PARAM : RUNTIME_PARAM}`;
}
