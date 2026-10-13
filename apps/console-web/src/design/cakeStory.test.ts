// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// CAKE ON THE ROBOT (spec sections 3.3, 5.5, 5.6 and 6.1; audit C1, C2, C11, T1). The region draws every activation of
// the mission as its own card, oldest first, never replaced; the pending choice under them while the mission waits for
// Cake; one dated signature-check card, muted as a whole when it is from an earlier runtime session; and the live slot as
// a footer that names the record which set it. It never draws a capability slot check's record: those are not mission
// records (they are in Diagnostics, Cake). The records are those of 2026-09-27 on 8771.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { MissionActivationRecord, MissionRecord, SlotReading } from '../model/missionRecord';
import type { SignatureCheckRecord, SignatureCheckState } from '../model/signatureCheck';
import { cakeStory, earlierSignatureLines } from '../model/cakeStory';
import type { CakeStoryInput } from '../model/cakeStory';
import { robotStatus } from '../model/robotStatusFixture';
import { parseCapabilityCheckState } from '../model/capabilityCheck';
import { followTop } from './BoundedAccount';
import { CakeStory } from './CakeStory';
import { FIXTURE_KEYS, fixtureCheckAnswer } from './cakeFixtures';
import { element } from './demoScenarioPlay';
import { C, INK } from './theme';

const OLD = '9600566de6c9a4cee25afaa14ff0d3e403515c181f1f345da906c73085c9389e';
const NEW = '2f34a84c19719a6ef4a040fbbd8114d831d093ef81d6a9fb78240dc8696da8a7';

function slot(generation: string, capsule: string, instance: string): SlotReading {
  return { read: 'ok', slot_uuid: 's', name: 'mobile_approach', state: 'ACTIVE', capsule, generation, instance, session_uuid: '01a0e337', plan_digest: null, epoch: '2', capability_active: null };
}

const REFUSAL: MissionActivationRecord = {
  operation_uuid: '40b94b8a4bc742b89f4fe56b64e56376', target_id: 'cube', candidate_key: 'mobile-demo-active-successor-abort-swap', payload_digest: null,
  activation_state: 'REJECTED_BY_CAKE', outcome: 'REJECTED_BY_CAKE', transaction_phase: 'ABORTED', transaction_uuid: 'aa29',
  detail: 'the resident answered ABORTED; the transaction did not complete, and the answer does not say why', t_wall_utc: '2026-09-27T14:20:21.636Z',
  slot_before: slot('1', OLD, '600'), slot_after: slot('1', OLD, '600'),
};
const SWAP: MissionActivationRecord = { ...REFUSAL, operation_uuid: '5d293b0225f64a23b387bac05beeb2d8', candidate_key: 'mobile-demo-swapped', activation_state: 'OBSERVED', detail: 'the transaction completed', t_wall_utc: '2026-09-27T14:21:45.496Z', slot_after: slot('2', NEW, '601') };

function mission(activations: MissionActivationRecord[], patch: Partial<MissionRecord> = {}): MissionRecord {
  return {
    mission_record_version: '1', mission_id: 'mission-f65c3cf8e99f', state: 'FAILED', data_mode: 'live', performs_motion: true, execution_label: null,
    started_wall_utc: '2026-09-27T14:16:38.847Z', selected_target: 'cube', waiting_reason: null, detail: null, stop_requested: false, stop_requested_wall_utc: null,
    targets: [], activations, hold: null, next_actions: ['open_gripper_by_hand', 'return_arm', 'start'], ...patch,
  };
}

function signatureCheck(runtimeSession: string, t: string): SignatureCheckRecord {
  return {
    check_uuid: '89a9442f', runtime_session: runtimeSession, t_wall_utc: t, policy: 'require', trusted_keys: [], throwaway_public_key: null, capsules: 3, unsigned_twins: 0,
    rows: [
      { capsule: NEW, kind: 'signed_capsule', key: 'deployment', expected: 'admitted', expected_reason: null, observed: 'admitted', reason_code: null, admitted: true, as_expected: true },
      { capsule: NEW, kind: 'signed_capsule', key: 'throwaway', expected: 'refused', expected_reason: 'signature_key_id_not_trusted', observed: 'refused', reason_code: 'signature_key_id_not_trusted', admitted: false, as_expected: true },
      { capsule: null, kind: 'unsigned_twin', key: 'deployment', expected: 'refused', expected_reason: 'signature_missing', observed: 'skipped', reason_code: 'unsigned_twin_not_staged', admitted: null, as_expected: null },
    ],
    rows_as_expected: 6, rows_not_as_expected: 0, rows_skipped: 1, rows_error: 0, as_expected: true, complete: false, duration_ms: 49,
  };
}

function state(check: SignatureCheckRecord | null): SignatureCheckState {
  return { signature_check_version: '1', in_flight: false, off_reason: null, policy: 'require', checks: check === null ? [] : [check], checks_dropped: 0 };
}

const ROBOT = robotStatus({ capability: { configured: true, name: 'mobile_approach', active: true, reason_code: null, capsule: NEW, generation: 2, child_pid: 13377, stale: false, observed: null } });

function input(patch: Partial<CakeStoryInput> = {}): CakeStoryInput {
  return {
    mission: mission([REFUSAL, SWAP]),
    candidates: [
      { key: 'mobile-demo-active-successor-abort-swap', payload_digest: 'sha256:6c19', role: null, label: null, expect: null },
      { key: 'mobile-demo-swapped', payload_digest: 'sha256:4d72', role: null, label: null, expect: null },
    ],
    signature: state(signatureCheck('rt-20260926T184836Z-e34dcd31', '2026-09-26T20:26:13.435Z')),
    runtimeSession: 'rt-20260927T141418Z-89540562',
    robot: ROBOT,
    capabilityCheck: null,
    ...patch,
  };
}

function render(patch: Partial<CakeStoryInput> = {}, pendingOff: string | null = null): string {
  return renderToStaticMarkup(
    createElement(CakeStory, {
      model: cakeStory(input(patch)),
      activating: false,
      activationOff: pendingOff,
      signatureRunOff: null,
      signatureRunning: false,
      onActivate: () => undefined,
      onRunSignature: () => undefined,
      notReadSince: null,
    }),
  );
}

function text(markup: string): string {
  return markup.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/&gt;/g, '>').replace(/\s+/g, ' ');
}

describe('the Cake region', () => {
  it('draws two activations as two cards, in the order the runtime recorded them', () => {
    const markup = render();
    const cards = [...markup.matchAll(/data-activation-card="([^"]+)" data-verdict="([^"]+)"/g)].map((match) => [match[1], match[2]]);
    expect(cards).toEqual([
      ['40b94b8a4bc742b89f4fe56b64e56376', 'refused_by_cake'],
      ['5d293b0225f64a23b387bac05beeb2d8', 'activated'],
    ]);
    expect(text(markup)).toContain('Cake refused the successor, slot unchanged 1 -> 1');
    expect(text(markup)).toContain('Activated, slot 1 -> 2');
    expect(text(markup)).toContain('This runtime serves no expected outcome for this Plan.');
  });

  it("labels each card's detail as the runtime's, never as the resident's answer (verdict 2026-09-27 item 4)", () => {
    const markup = render();
    const refusal = text(element(markup, `data-activation-card="${REFUSAL.operation_uuid}"`) ?? '');
    expect(refusal).toContain("The runtime's detail: the resident answered ABORTED; the transaction did not complete, and the answer does not say why");
    expect(text(markup)).not.toContain("The resident's answer");
  });

  // VERDICT 2026-09-27 ITEM 12: on 8771 (no candidate roles served) the refusal was the quietest card on the page, grey
  // 14 px over a 5 percent wash, directly above a green "Activated". A refusal with the slot unchanged is now a
  // prominent, neutral card in the design's bright ink, whether or not an expectation is served; green only when the
  // served expectation says so, red only for the bad verdicts.
  function headlineStyle(markup: string, id: string): { colour: string; size: number; weight: number; words: string } {
    const card = element(markup, `data-activation-card="${id}"`) ?? '';
    const headline = /<div style="([^"]*)">([^<]*)<\/div>/.exec(card);
    const style = headline?.[1] ?? '';
    return {
      colour: /(?:^|;)color:([^;]+)/.exec(style)?.[1] ?? '',
      size: Number(/font-size:(\d+)px/.exec(style)?.[1] ?? '0'),
      weight: Number(/font-weight:(\d+)/.exec(style)?.[1] ?? '0'),
      words: headline?.[2] ?? '',
    };
  }

  it('draws a refusal with no expectation served as a prominent neutral card, never the quietest one (verdict 2026-09-27 item 12)', () => {
    const markup = render();
    const refusal = headlineStyle(markup, REFUSAL.operation_uuid);
    const activated = headlineStyle(markup, SWAP.operation_uuid);
    expect(refusal.words).toBe('Cake refused the successor, slot unchanged 1 -&gt; 1');
    expect(refusal.colour).toBe(INK.bright);
    expect([C.mute, C.ok, C.bad, C.warn]).not.toContain(refusal.colour);
    expect(refusal.size).toBeGreaterThanOrEqual(activated.size);
    expect(refusal.weight).toBeGreaterThanOrEqual(activated.weight);
    // Its rule is solid and bright, and it stays on the page after the activation that followed it.
    const card = element(markup, `data-activation-card="${REFUSAL.operation_uuid}"`) ?? '';
    expect(card).toContain(`border-left:4px solid ${INK.bright}`);
    expect(card).toContain('data-verdict="refused_by_cake"');
  });

  it('reads "Refused as expected" in green only with the served expectation, and an unexpected refusal never green', () => {
    const served = render({
      candidates: [
        { key: 'mobile-demo-active-successor-abort-swap', payload_digest: 'sha256:6c19', role: 'refused-demo', label: 'Abort-swap', expect: 'REJECTED_BY_CAKE' },
        { key: 'mobile-demo-swapped', payload_digest: 'sha256:4d72', role: 'activate', label: 'Activate', expect: 'OBSERVED' },
      ],
    });
    const expected = headlineStyle(served, REFUSAL.operation_uuid);
    const activated = headlineStyle(served, SWAP.operation_uuid);
    expect(expected.words).toBe('Refused as expected, slot unchanged 1 -&gt; 1');
    expect(expected.colour).toBe(C.ok);
    // The prominence does not depend on the expectation: the expected refusal is as loud as the one with none served.
    expect(expected.size).toBeGreaterThan(activated.size);
    expect(expected.weight).toBeGreaterThan(activated.weight);
    expect(element(served, `data-activation-card="${REFUSAL.operation_uuid}"`) ?? '').toContain(`border-left:4px solid ${C.ok}`);
    const unexpected = render({
      candidates: [{ key: 'mobile-demo-active-successor-abort-swap', payload_digest: 'sha256:6c19', role: 'activate', label: null, expect: 'OBSERVED' }],
    });
    const refused = headlineStyle(unexpected, REFUSAL.operation_uuid);
    expect(refused.words).toBe('Refused, not as expected, slot unchanged 1 -&gt; 1');
    expect(refused.colour).toBe(C.bad);
    expect(refused.colour).not.toBe(C.ok);
    expect(refused.size).toBeGreaterThan(headlineStyle(unexpected, SWAP.operation_uuid).size);
  });

  it('draws a refusal before any transaction with no expectation served in the same prominent neutral way', () => {
    const before: MissionActivationRecord = { ...REFUSAL, operation_uuid: 'b1', activation_state: 'REFUSED_BEFORE_TRANSACTION', outcome: 'REFUSED_BEFORE_TRANSACTION', transaction_phase: null };
    const markup = render({ mission: mission([before, SWAP]) });
    const headline = headlineStyle(markup, 'b1');
    expect(headline.words).toBe('Cake refused the successor before any transaction, slot unchanged 1 -&gt; 1');
    expect(headline.colour).toBe(INK.bright);
  });

  it('holds no capability-check record, and no Last check, before and after, even with a check of this session read', () => {
    // A real slot check of the current resident session that left the slot at the live generation (1 -> 2): the region
    // draws no check record, and only the footer names the check as what set the slot.
    const check = parseCapabilityCheckState(fixtureCheckAnswer(['observed']));
    expect(check?.checks.length).toBe(1);
    expect(check?.checks[0]?.slot_after?.session_uuid).toBe(check?.current.session_uuid);
    const markup = render({ mission: mission([]), capabilityCheck: check });
    expect(markup).not.toContain('data-capability-check');
    expect(text(markup)).not.toContain('Last check');
    expect(text(markup)).not.toContain('before and after');
    expect(text(markup)).toContain(`Set by the slot check of ${FIXTURE_KEYS.activate}`);
  });

  it('never credits the live slot to a slot check that left it unchanged', () => {
    // A refused check (1 -> 1) whose slot after reads the live generation is not what set the slot.
    const held = robotStatus({ capability: { configured: true, name: 'mobile_approach', active: false, reason_code: null, capsule: OLD, generation: 1, child_pid: 13377, stale: false, observed: null } });
    const words = text(render({ mission: mission([]), robot: held, capabilityCheck: parseCapabilityCheckState(fixtureCheckAnswer(['aborted'])) }));
    expect(words).toContain('Live slot: mobile_approach, not active, generation 1');
    expect(words).not.toContain('Set by the slot check of');
    expect(words).toContain('No record of this mission or of this resident session says what set it.');
  });

  // VERIFIER R2 DEFECT 5: a read that failed is never drawn as the default claim of an empty record.
  it('says the mission was not read, never that it holds no activation, when the mission read failed', () => {
    const words = text(render({ mission: null, missionError: 'mission_read_failed' }));
    expect(words).not.toContain('No Cake activation in this mission');
    expect(words).toContain('The mission has not been read: mission_read_failed.');
    expect(words).not.toContain('No record of this mission or of this resident session says what set it.');
    expect(words).toContain('The mission has not been read, so this page cannot say what set it.');
    const notYet = text(render({ mission: null, missionError: null }));
    expect(notYet).toContain('The mission has not been read yet.');
  });

  it('says the signature check was not read, never that none ran in this session, when its read failed', () => {
    const markup = render({ signature: null, signatureCheckError: 'signature_read_failed' });
    const words = text(markup);
    expect(words).not.toContain('No signature check in this session');
    expect(words).toContain('Signature check: not read: signature_read_failed');
    expect(markup).toMatch(/data-signature-card="" data-signature-stale="false" data-verdict="sig_unread"/);
    expect(text(render({ signature: null, signatureCheckError: null }))).toContain('Signature check: not read yet');
  });

  it('marks a signature check from an earlier runtime session as such, and leaves its record to History, dated (spec 5.6 as amended, section 12)', () => {
    const markup = render();
    expect(markup).toContain('data-signature-stale="true"');
    expect(markup).toMatch(/data-signature-card="" data-signature-stale="true" data-verdict="sig_earlier"/);
    const words = text(markup);
    // THE MAIN CARD: none in this session, and where the earlier one is, with its full date and session. No counts.
    expect(words).toContain('No check in this runtime session. The last one, 2026-09-26 20:26:13Z (runtime session rt-20260926T184836Z-e34dcd31), is in History.');
    expect(words).not.toContain('rows as expected');
    expect(words).not.toContain('It covered capsule');
    // HISTORY: the earlier session's record, whole.
    const history = earlierSignatureLines(input());
    expect(history).toEqual([
      'Earlier session: 2026-09-26 20:26:13Z (runtime session rt-20260926T184836Z-e34dcd31). It says nothing about this session.',
      expect.stringContaining('rows as expected'),
      'It covered capsule 2f34a84c1971, which the live slot carries now: admitted with the deployment key, refused with the throwaway key.',
    ]);
    expect(earlierSignatureLines(input({ runtimeSession: 'rt-20260926T184836Z-e34dcd31' }))).toBeNull();
  });

  it('draws the verdict of a check of this session', () => {
    const markup = render({ signature: state(signatureCheck('rt-20260927T141418Z-89540562', '2026-09-27T14:40:00.000Z')) });
    expect(markup).toContain('data-signature-stale="false"');
    expect(markup).toContain('data-verdict="sig_partial"');
    expect(text(markup)).toContain('As expected, 1 skipped');
  });

  it('says no signature check was served in this session when none was', () => {
    expect(render({ signature: state(null) })).toContain('data-verdict="sig_none"');
  });

  it("names the activation that set the live slot in the footer", () => {
    const words = text(render());
    expect(words).toContain('Live slot: mobile_approach, active, generation 2, capsule 2f34a84c1971');
    expect(words).toContain('Set by the activation of mobile-demo-swapped at 14:21:45Z');
  });

  it('never credits the live slot to a refused activation, whose slot after reads the live generation', () => {
    // D2: held after the refusal (1 -> 1), and again after the swap while the robot still reads generation 1.
    const held = robotStatus({ capability: { configured: true, name: 'mobile_approach', active: false, reason_code: null, capsule: OLD, generation: 1, child_pid: 13377, stale: false, observed: null } });
    for (const activations of [[REFUSAL], [REFUSAL, SWAP]]) {
      const words = text(render({ mission: mission(activations), robot: held }));
      expect(words).toContain('Live slot: mobile_approach, not active, generation 1');
      expect(words).not.toContain('Set by the activation of');
      expect(words).toContain('No record of this mission or of this resident session says what set it.');
    }
  });

  it('credits the live slot only to an activation whose two slot reads show it changed the slot to that generation', () => {
    const unread: MissionActivationRecord = { ...SWAP, slot_before: { ...slot('1', OLD, '600'), read: 'failed' } };
    const words = text(render({ mission: mission([REFUSAL, unread]) }));
    expect(words).toContain('Live slot: mobile_approach, active, generation 2');
    expect(words).not.toContain('Set by the activation of');
  });

  it('offers the pending candidates, one button each, none primary without roles, while the mission waits for Cake', () => {
    const waiting = mission([REFUSAL], { state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable', next_actions: ['stop'] });
    const markup = render({ mission: waiting });
    expect(markup.match(/data-control="candidate"/g)?.length).toBe(2);
    expect(markup).not.toMatch(/data-control="candidate"[^>]*data-primary="true"|data-primary="true"[^>]*data-control="candidate"/);
    expect(render()).not.toContain('data-control="candidate"');
  });

  it('makes the activate role the primary candidate when roles are served', () => {
    const waiting = mission([], { state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable', next_actions: ['stop'] });
    const markup = render({
      mission: waiting,
      candidates: [
        { key: 'mobile-demo-active-successor-abort-swap', payload_digest: 'sha256:c3', role: 'refused-demo', label: 'Abort-swap', expect: 'REJECTED_BY_CAKE' },
        { key: 'mobile-demo-swapped', payload_digest: 'sha256:d4', role: 'activate', label: 'Activate the capability', expect: 'OBSERVED' },
      ],
    });
    expect(markup).toMatch(/data-candidate="mobile-demo-swapped"[^>]*data-primary="true"|data-primary="true"[^>]*data-candidate="mobile-demo-swapped"/);
    expect(markup.match(/data-primary="true"/g)?.length).toBe(1);
  });

  // VERIFY-R5 M2: while the mission waits for Cake, the choice was drawn under the activation cards inside the bounded
  // region, and one refused activation at 390 px (two at 1440) pushed it, and the candidate buttons the top bar points to,
  // below the fold. A hold that needs the operator is never hidden: the choice is drawn above the region, outside it.
  it('draws the pending choice and its candidates above the bounded region, outside it, while the mission waits for Cake', () => {
    const waiting = mission([REFUSAL, SWAP, { ...REFUSAL, operation_uuid: 'c3' }], { state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable', next_actions: ['stop'] });
    const markup = render({ mission: waiting }, null);
    const region = markup.indexOf('data-bounded="cake"');
    expect(region).toBeGreaterThan(-1);
    const choice = markup.indexOf('data-pending-choice=""');
    expect(choice).toBeGreaterThan(-1);
    expect(choice).toBeLessThan(region);
    expect(markup.lastIndexOf('data-control="candidate"')).toBeLessThan(region);
    expect(text(markup)).toContain('The mission waits for Cake. The prepared Plans:');
    // Every card is still drawn, whole, inside the region.
    expect(markup.slice(region).match(/data-activation-card=/g)?.length).toBe(3);
  });

  it('names the newest activation for the region to follow, so the result of the latest press is in view (verify-r5 M2)', () => {
    const markup = render();
    expect(/data-bounded="cake"[^>]*data-follow="([^"]+)"|data-follow="([^"]+)"[^>]*data-bounded="cake"/.exec(markup)?.slice(1).find((v) => v !== undefined)).toBe(SWAP.operation_uuid);
    expect(render({ mission: mission([]) })).not.toMatch(/data-bounded="cake"[^>]*data-follow=|data-follow=[^>]*data-bounded="cake"/);
  });

  it('scrolls the region, never the page, so the followed card\'s top is at the region\'s top, or as far as the region scrolls', () => {
    // region top 100 px in the view, scrolled 0; the newest card at 340 px: scroll 240 within the region.
    expect(followTop({ top: 100, scrollTop: 0, clientHeight: 200, scrollHeight: 600 }, 340)).toBe(240);
    // already scrolled 50: the card's view top already reflects it.
    expect(followTop({ top: 100, scrollTop: 50, clientHeight: 200, scrollHeight: 600 }, 290)).toBe(240);
    // never past the end of the region.
    expect(followTop({ top: 100, scrollTop: 0, clientHeight: 200, scrollHeight: 300 }, 340)).toBe(100);
    // never negative.
    expect(followTop({ top: 100, scrollTop: 0, clientHeight: 200, scrollHeight: 300 }, 60)).toBe(0);
  });
});
