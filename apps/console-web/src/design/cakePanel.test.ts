// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CAKE PANEL, rendered from the synthetic fixtures (`design/cakeFixtures.ts`), which are the runtime's own shapes.
//
// These pin: one labelled button per prepared candidate with the outcome it expects, nothing preselected and the
// activate role the only primary button; a press sends that key and nothing else; the newest check drawn before and
// after with its state path, its ValidatePlan verdict and whether it met its role's expectation; the history newest
// first; module cards with a Revision and never a version number; the controller's card saying it is not a Cake
// module; participation read from the mission record; and the honesty lines on the page (no live admission, abort-swap
// is transactional, the tampered twin is an integrity refusal and not a signature one).

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { participationFromMission } from '../model/cakeTask';
import type { CapabilityCheckState } from '../model/capabilityCheck';
import { parseCapabilityCheckState } from '../model/capabilityCheck';
import {
  FIXTURE_CAPABILITY_SLOT,
  FIXTURE_HEALTH,
  FIXTURE_KEYS,
  FIXTURE_PLACED_MISSION,
  FIXTURE_SLOTS,
  fixtureCheckAnswer,
} from './cakeFixtures';
import { CAKE_HONESTY_LINES, CONTROLLER_NOTE, controllerCard, hostOutsideCake, moduleCards, processWords, revisionWords } from './cakeModules';
import type { RobotHost } from '../model/robotStatus';
import type { CakePanelProps } from './CakePanel';
import { CakePanel, candidateButtons } from './CakePanel';
import { C, INK } from './theme';

const NOW = 1_800_000_000_000;

function text(markup: string): string {
  return markup.replace(/<[^>]*>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/&gt;/g, '>').replace(/\s+/g, ' ');
}

function state(checks?: Parameters<typeof fixtureCheckAnswer>[0], extra: Record<string, unknown> = {}): CapabilityCheckState {
  const parsed = parseCapabilityCheckState({ ...fixtureCheckAnswer(checks), ...extra });
  if (parsed === null) throw new Error('fixture not parsed');
  return parsed;
}

function props(extra: Partial<CakePanelProps> = {}): CakePanelProps {
  return {
    check: state(),
    checkUnavailable: null,
    readAtMs: NOW,
    nowMs: NOW,
    applying: false,
    readOnlyReason: null,
    onApply: () => undefined,
    modules: [...moduleCards(FIXTURE_SLOTS, FIXTURE_HEALTH, FIXTURE_CAPABILITY_SLOT), controllerCard('sha256:' + '9f'.repeat(32))],
    participation: participationFromMission(FIXTURE_PLACED_MISSION, 'mobile_approach'),
    signature: null,
    ...extra,
  };
}

function render(extra: Partial<CakePanelProps> = {}): string {
  return renderToStaticMarkup(createElement(CakePanel, props(extra)));
}

function buttonsOf(markup: string): string[] {
  return [...markup.matchAll(/<button[^>]*data-cake-candidate="([^"]+)"[^>]*>/g)].map((match) => match[0]);
}

describe('the candidates', () => {
  it('draws one labelled button per prepared candidate, each with the outcome it expects', () => {
    const markup = render();
    expect(buttonsOf(markup)).toHaveLength(4);
    const words = text(markup);
    expect(words).toContain('Activate the capability (the swapped Plan)');
    expect(words).toContain('expected: the slot changes and, read back, carries the capability');
    expect(words).toContain('expected: Cake opens the transaction and aborts it; the slot does not change');
    expect(words).toContain('expected: refused by an integrity check before any transaction; the slot does not change');
    expect(words).toContain('expected: the transaction completes and the slot returns to the inert capsule');
  });

  it('preselects nothing, and only the activate role is the primary button', () => {
    const markup = render();
    expect(markup).not.toMatch(/selected=""|aria-pressed="true"|checked=""/);
    const buttons = buttonsOf(markup);
    const primary = buttons.filter((button) => button.includes('dsbutton--default'));
    expect(primary).toHaveLength(1);
    expect(primary[0]).toContain(`data-cake-candidate="${FIXTURE_KEYS.activate}"`);
    expect(buttons.filter((button) => button.includes('dsbutton--outline'))).toHaveLength(3);
  });

  it('puts the activate role first, the others after it in the runtime s order', () => {
    expect(candidateButtons(state()).map((item) => item.key)).toEqual([
      FIXTURE_KEYS.activate,
      FIXTURE_KEYS.refused,
      FIXTURE_KEYS.undo,
      FIXTURE_KEYS.tampered,
    ]);
  });

  it('a press sends that key and nothing else', () => {
    const onApply = vi.fn();
    const items = candidateButtons(state());
    const tampered = items.find((item) => item.key === FIXTURE_KEYS.tampered);
    if (tampered === undefined) throw new Error('no tampered candidate');
    tampered.press(onApply);
    expect(onApply).toHaveBeenCalledTimes(1);
    expect(onApply).toHaveBeenCalledWith(FIXTURE_KEYS.tampered);
  });

  it('turns every button off, with the reason on the page, when the runtime would refuse', () => {
    const markup = render({ check: state(undefined, { off_reason: 'robot_host_not_held' }) });
    for (const button of buttonsOf(markup)) expect(button).toContain('disabled=""');
    expect(text(markup)).toContain('a host process is under the supervisor slot');
  });

  it('says why there are no buttons when the runtime offers no check', () => {
    const markup = render({ check: null, checkUnavailable: 'this runtime is not LIVE' });
    expect(buttonsOf(markup)).toHaveLength(0);
    expect(text(markup)).toContain('this runtime is not LIVE');
  });
});

describe('the newest check, before and after', () => {
  it('shows the undo back to inert with its full state path, its ValidatePlan verdict and its expectation met', () => {
    const words = text(render());
    expect(words).toContain('CREATED > VALIDATED > PREPARED > QUIESCING > ACTIVATING > COMMITTING > COMMITTED > DURABLE > RETIRING > COMPLETED');
    expect(words).toContain('ValidatePlan: the resident reads these exact bytes as a valid Plan');
    expect(words).toContain('as expected for the undo role');
  });

  it('shows the tampered twin as an integrity refusal before any transaction, never a signature one', () => {
    const markup = render({ check: state(['tampered']) });
    const words = text(markup);
    expect(words).toContain('Refused before any transaction, as expected: an integrity check failed');
    expect(words).toContain('state path: none, no transaction was opened');
    expect(words).toContain('ValidatePlan: the resident reads these exact bytes as invalid (reason 7)');
    expect(words).toContain('as expected for the tampered-demo role');
    const newest = markup.slice(markup.indexOf('data-cake-newest'), markup.indexOf('data-cake-section="participation"'));
    expect(newest.length).toBeGreaterThan(0);
    expect(text(newest).toLowerCase()).not.toMatch(/signature (refusal|check failed|invalid)/);
  });

  it('shows abort-swap aborted with the path the resident took', () => {
    const words = text(render({ check: state(['aborted']) }));
    expect(words).toContain('Refused as expected');
    expect(words).toContain('CREATED > VALIDATED > PREPARED > QUIESCING > ABORTED');
  });

  // SPEC 5.1 AND 5.5, ONE WORD AND ONE COLOUR EVERYWHERE (verifier r2 defect 3): a Cake refusal is green only with an
  // expectation it met, mute without one, and never red or amber for being a refusal.
  function newestHeadline(markup: string): string {
    const at = markup.indexOf('data-cake-newest');
    return markup.slice(at, markup.indexOf('</span>', markup.indexOf('font-weight:600', at)) + 7);
  }

  it("draws an abort-swap refusal that met its role's expectation green, in the card's words", () => {
    const headline = newestHeadline(render({ check: state(['aborted']) }));
    expect(text(headline)).toContain('Refused as expected');
    expect(headline).toContain(`color:${C.ok}`);
    expect(headline).not.toContain(C.bad);
  });

  it('draws an abort-swap refusal with no expectation served in the neutral bright ink, as Cake refused the successor, never red (verdict 2026-09-27 item 12)', () => {
    const headline = newestHeadline(render({ check: state(['aborted'], { candidates: [] }) }));
    expect(text(headline)).toContain('Cake refused the successor');
    expect(headline).toContain(`color:${INK.bright}`);
    expect(headline).not.toContain(C.bad);
    expect(headline).not.toContain(C.warn);
  });

  it('draws a refusal before any transaction with no expectation served in the neutral bright ink, and its integrity detail kept', () => {
    const headline = newestHeadline(render({ check: state(['tampered'], { candidates: [] }) }));
    expect(text(headline)).toContain('Cake refused the successor before any transaction');
    expect(text(headline)).toContain('an integrity check failed');
    expect(headline).toContain(`color:${INK.bright}`);
    expect(headline).not.toContain(C.bad);
  });

  it('lists an earlier abort-swap refusal in the same words', () => {
    const markup = render();
    const history = text(markup.slice(markup.indexOf('data-cake-history')));
    expect(history).toContain(`${FIXTURE_KEYS.refused}: Refused as expected`);
    expect(history).not.toContain('Aborted by the resident');
  });

  it('lists the earlier checks newest first, each with whether it met its expectation', () => {
    const markup = render();
    const history = text(markup.slice(markup.indexOf('data-cake-history')));
    const observed = history.indexOf(FIXTURE_KEYS.activate);
    const aborted = history.indexOf(FIXTURE_KEYS.refused);
    const tampered = history.indexOf(FIXTURE_KEYS.tampered);
    expect(observed).toBeGreaterThan(-1);
    expect(observed).toBeLessThan(aborted);
    expect(aborted).toBeLessThan(tampered);
    expect(history).toContain('as expected');
  });
});

describe('the modules and the honesty lines', () => {
  it('names every module by its Revision, never by a version number', () => {
    const markup = render();
    const words = text(markup);
    expect(words).toContain('Revision: capsule b2b2b2b2b2b2, generation 2');
    expect(words).toContain('pid 43289');
    expect(words).toContain('no child process now (pid 0)');
    expect(words).toContain('spawns 5');
    expect(words).toMatch(/config digest not reported/);
    expect(words).not.toMatch(/\bv?\d+\.\d+\.\d+\b/);
  });

  it('says on the controller s card that it is not a Cake module', () => {
    const card = controllerCard(null);
    expect(card.note).toBe(CONTROLLER_NOTE);
    expect(revisionWords(card)).toBe('Revision: bundle not reported');
    expect(processWords(card)).toBe('pid not reported');
    expect(text(render())).toContain('content-addressed, not signed, not a Cake module');
  });

  it('carries every honesty line', () => {
    const words = text(render());
    for (const line of CAKE_HONESTY_LINES) expect(words).toContain(line);
    // General copy about how Cake works, never a claim about this runtime's state no record backs (verifier r2 R-3, 10).
    expect(words).not.toContain('this resident has no live admission');
    expect(words).not.toContain('AdmitCapsule is answered unavailable');
    expect(words).not.toContain('the capability the mission waits on');
    expect(words).toContain('an activation moves this slot and its holder slot together');
  });

  it('states participation from the mission record, naming the module, its generation and its pid', () => {
    const words = text(render());
    expect(words).toContain('Taking part in this task: yes');
    expect(words).toContain('mobile_approach (generation 2, pid 43289)');
    const none = text(render({ participation: participationFromMission(null, null) }));
    expect(none).toContain('Taking part in this task: no');
  });
});

describe('a motor host running outside Cake', () => {
  const host = (extra: Partial<RobotHost>): RobotHost => ({
    supervised: false,
    reason_code: 'supervisor_child_not_running',
    slot_uuid: FIXTURE_CAPABILITY_SLOT,
    slot_state: 'ACTIVE',
    slot_instance: '1',
    child_pid: 0,
    spawn_sequence: 5,
    health_state: 'DEGRADED',
    readiness_state: 'running_normal',
    readiness_age_ms: 100,
    observed: null,
    ...extra,
  });

  it('is named when the readiness probe finds a host and the supervisor has no child of its own', () => {
    const finding = hostOutsideCake(host({}));
    expect(finding?.code).toBe('host_outside_cake');
    expect(finding?.sentence).toContain('Cake did not start');
    expect(hostOutsideCake(host({ reason_code: 'host_pid_mismatch', child_pid: 4242, health_state: 'HEALTHY' }))?.sentence).toContain('pid 4242');
    const markup = render({ hostFinding: finding });
    expect(markup).toContain('data-cake-finding="host_outside_cake"');
  });

  it('is not named for a held host, a supervised one, or readings that were not read', () => {
    expect(hostOutsideCake(host({ readiness_state: 'not_running' }))).toBeNull();
    expect(hostOutsideCake(host({ supervised: true, reason_code: null, child_pid: 4242 }))).toBeNull();
    expect(hostOutsideCake(host({ readiness_state: null }))).toBeNull();
    expect(hostOutsideCake(host({ reason_code: 'host_readiness_not_current' }))).toBeNull();
    expect(hostOutsideCake(null)).toBeNull();
    expect(render()).not.toContain('data-cake-finding');
  });
});
