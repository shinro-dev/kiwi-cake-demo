// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE FIVE NUMBERED STEPS, which are a VIEW of the run and not a wizard through it.
//
// Pressing a step changes what is being inspected on the screen below and nothing else. No step commands the robot,
// no step completes anything, and no step resets a run: the step the console is ON is derived from the record every
// time this renders (`model/steps.ts`), so a click can only ever move what is being LOOKED at. That is why each
// button says "show" in its title and why the selected step is kept apart from the derived one.

import type { ReactNode } from 'react';
import type { StepId, StepView } from '../model/steps';

const STATE_WORD: Record<StepView['state'], string> = {
  done: 'done',
  current: 'now',
  blocked: 'blocked',
  waiting: 'not yet',
  later: 'later',
};

export function Steps(props: {
  steps: StepView[];
  /** The step the RECORD says the console is on. */
  derived: StepId;
  /** The step the operator is looking at, which is the derived one until they choose another. */
  selected: StepId;
  onSelect: (step: StepId) => void;
}): ReactNode {
  return (
    <nav className="steps" aria-label="the five steps of the demonstration">
      {props.steps.map((step) => {
        const selected = step.id === props.selected;
        const derived = step.id === props.derived;
        return (
          <button
            type="button"
            key={step.id}
            className={`steps__step steps__step--${step.state} ${selected ? 'steps__step--selected' : ''}`}
            aria-current={derived ? 'step' : undefined}
            aria-pressed={selected}
            title={`show step ${String(step.id)}: ${step.name}`}
            onClick={() => props.onSelect(step.id)}
          >
            <span className="steps__number">{step.id}</span>
            <span className="steps__text">
              <span className="steps__name">
                {step.name}
                {step.id === 5 ? <span className="steps__later"> - later</span> : null}
              </span>
              <span className="steps__instruction">{step.blocker ?? step.instruction}</span>
            </span>
            <span className={`steps__state steps__state--${step.state}`}>{derived ? STATE_WORD[step.state] : STATE_WORD[step.state]}</span>
          </button>
        );
      })}
    </nav>
  );
}
