// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SIDEBAR'S RESIDENT LINE, and the three things it must never say.
//
// The surface behind it exists because the contract's cake object is sealed and its ladder carries its facts inside
// EVIDENCE PROSE written for an operator to read. A screen that scraped that prose would break the first time a
// sentence was reworded, so nothing here reads one; every value comes from the structured status.
//
// What is pinned here is the honesty, not the wording of a headline:
//
//   - a resident that stopped answering keeps what it last reported, marked stale and aged, and never turns into a
//     resident holding nothing;
//   - having no reading at all is said as that, and never drawn as a reading of zero;
//   - a slot count is never presented as execution status, and a restart is surfaced as a new session rather than
//     inferred from a reachability flicker.

import { describe, expect, it } from 'vitest';
import type { CakeStatus } from '../api/types';
import { agedWords, residentLine } from './cake';

function status(over: Partial<CakeStatus> = {}): CakeStatus {
  return {
    cake_status_version: '1',
    configured: true,
    reachable: true,
    reason_code: null,
    stale: false,
    session_changed: null,
    clock_domain: 'laptop_monotonic',
    observation: {
      wall_utc: '2026-09-23T10:27:24.757Z',
      mono_ns: 1_000_000_000,
      age_ms: 120,
      resident: {
        session_uuid: '01a0cdba8c9c7b09b03c1f945f489104',
        build_identity: 'd392ef91e3fbffd825c693665f6b49341efb02666a2a7d23731ed4764450ee33',
        config_identity: '8ba283b86a3a96739585078756373b069cd94483c322674c2cd6ed2ac4f655c7',
        target_profile_digest: '841c3e3f73e01e48ece200ce11385aa2dc4945df51de6bef6d1f8e8bd97d15b3',
        plan_digest: 'c93aa621cf3d10522a9040ff0ac154f1e9729da6d7247976f1cb3d41aab6c6bc',
      },
      counts: { epoch: 0, slots: 0, bindings: 0, resources: 0, replay: 0, snapshot_present: 1 },
      transaction_state: 'none',
      flight: { records: 3, oldest: 0, next: 2 },
    },
    activity: { value: false, evidence: 'the resident reports 0 slots' },
    mission_participation: { value: false, evidence: 'the controller has no Cake adapter' },
    ...over,
  };
}

describe('the resident line', () => {
  it('says a resident is answering, and how recently', () => {
    const line = residentLine(status(), null);
    expect(line.headline).toBe('Resident answering');
    expect(line.stale).toBe(false);
    expect(line.age).not.toBeNull();
    expect(line.restarted).toBe(false);
  });

  it('keeps the last answer when contact is lost, and marks it stale with its age', () => {
    const line = residentLine(status({ reachable: false, stale: true, reason_code: 'admin_socket_absent' }), null);
    expect(line.stale).toBe(true);
    expect(line.headline).toContain('last seen');
    expect(line.detail).toContain('admin_socket_absent');
    // The reading is still there to be shown. A lost resident is not a resident holding nothing.
    expect(line.age).not.toBeNull();
  });

  it('never reads a lost resident as a current one', () => {
    const current = residentLine(status(), null);
    const lost = residentLine(status({ reachable: false, stale: true }), null);
    expect(lost.headline).not.toBe(current.headline);
    expect(lost.tone).not.toBe(current.tone);
  });

  it('distinguishes never configured, never reached, and answering', () => {
    const unconfigured = residentLine(status({ configured: false, reachable: false, observation: null, reason_code: 'not_configured' }), null);
    expect(unconfigured.headline).toBe('No resident configured');
    expect(unconfigured.stale).toBe(false);
    const unreached = residentLine(status({ reachable: false, observation: null, reason_code: 'admin_probe_timeout' }), null);
    expect(unreached.headline).toBe('Resident not reached');
    expect(unreached.stale).toBe(false);
    expect(unreached.detail).toContain('admin_probe_timeout');
  });

  it('reports a restart as a different session rather than as a reconnection', () => {
    expect(residentLine(status({ session_changed: true }), null).restarted).toBe(true);
    expect(residentLine(status({ session_changed: false }), null).restarted).toBe(false);
    // Null is the runtime saying it has not had two answers to compare. It is not a no.
    expect(residentLine(status({ session_changed: null }), null).restarted).toBe(false);
  });

  it('says it has no reading when the runtime has not answered, and carries the reason', () => {
    const line = residentLine(null, 'this runtime does not report a Cake resident');
    expect(line.headline).toBe('No resident reading');
    expect(line.detail).toBe('this runtime does not report a Cake resident');
    expect(line.age).toBeNull();
  });
});

describe('an age in words', () => {
  it('reads as an age at every scale, and never as a bare zero', () => {
    expect(agedWords(0)).toBe('just now');
    expect(agedWords(999)).toBe('just now');
    expect(agedWords(4_000)).toBe('4s ago');
    expect(agedWords(90_000)).toBe('1m ago');
    expect(agedWords(3 * 3_600_000)).toBe('3h ago');
  });
});
