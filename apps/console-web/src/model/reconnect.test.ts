// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import { describe, expect, it } from 'vitest';
import { reconnectOffer, stopReload } from './reconnect';
import type { SessionFacts } from './reconnect';

const HEALTHY: SessionFacts = {
  booted_runtime_session: 'rt-20260922T191759Z-9b44a4b6',
  current_runtime_session: 'rt-20260922T191759Z-9b44a4b6',
  credential_superseded: false,
  runtime_replaced: false,
};

describe('when a tab is offered a way back', () => {
  it('offers nothing while the runtime is the one this tab started against', () => {
    expect(reconnectOffer(HEALTHY).reason).toBeNull();
    expect(reconnectOffer(HEALTHY).label).toBe('');
  });

  it('offers a reload when the runtime was replaced, and names both sessions', () => {
    const offer = reconnectOffer({ ...HEALTHY, current_runtime_session: 'rt-20260923T090000Z-cafe1234', runtime_replaced: true });
    expect(offer.reason).toBe('runtime_session_changed');
    expect(offer.sentence).toContain('rt-20260922T191759Z-9b44a4b6');
    expect(offer.sentence).toContain('rt-20260923T090000Z-cafe1234');
    expect(offer.label).toBe('Reload to reconnect');
  });

  it('reports a refused credential in the server’s own terms, and ahead of the session change', () => {
    const offer = reconnectOffer({ ...HEALTHY, credential_superseded: true, runtime_replaced: true });
    expect(offer.reason).toBe('credential_superseded');
  });

  it('claims no readiness against a session that never answered this tab (verify-r1 must_fix 2)', () => {
    const offer = reconnectOffer({ ...HEALTHY, current_runtime_session: 'rt-20260923T090000Z-cafe1234', runtime_replaced: true, booted_answered: false });
    expect(offer.sentence).not.toContain('established readiness');
    expect(offer.sentence).toContain('has not answered this tab');
    expect(offer.sentence).toContain('rt-20260923T090000Z-cafe1234');
  });

  it('says the server minted the credential itself when the boot handshake said api_process', () => {
    const offer = reconnectOffer({
      booted_runtime_session: null, current_runtime_session: 'rt-20260923T090000Z-cafe1234', credential_superseded: true, runtime_replaced: true,
      credential_owner: 'runtime_session', boot_credential_owner: 'api_process', booted_answered: false,
    });
    expect(offer.reason).toBe('credential_superseded');
    expect(offer.sentence).toContain('minted that one itself');
    expect(offer.sentence).toContain('rt-20260923T090000Z-cafe1234');
  });

  it('still offers a reload when the sessions are not both known', () => {
    const offer = reconnectOffer({ ...HEALTHY, booted_runtime_session: null, current_runtime_session: null, runtime_replaced: true });
    expect(offer.reason).toBe('runtime_session_changed');
    expect(offer.sentence).toContain('not the one this tab established readiness against');
  });
});

describe('what the control promises about a reload', () => {
  const offer = reconnectOffer({ ...HEALTHY, runtime_replaced: true });

  it('states that it sends no command', () => {
    expect(offer.scope).toContain('sends no command');
  });

  it('states that it replays nothing', () => {
    expect(offer.scope).toContain('replays nothing');
  });

  it('states that nothing stated at the robot is carried forward', () => {
    expect(offer.scope).toContain('does not carry forward');
    expect(offer.scope).toContain('asks for those checks again');
  });

  it('says why a document load is the mechanism, rather than implying a new one', () => {
    expect(offer.scope).toContain('fetches a new document');
    expect(offer.scope).toContain('carries the current credential');
  });
});

describe('what Stop is on a tab whose credential the console server no longer accepts', () => {
  it('is the plain Stop while the runtime is the one this tab started against', () => {
    expect(stopReload({ ...HEALTHY, credential_owner: 'runtime_session' })).toBeNull();
  });

  it('names the reload when the runtime was replaced under a server that owns its credential per runtime session', () => {
    const words = stopReload({ ...HEALTHY, current_runtime_session: 'rt-20260923T090000Z-cafe1234', runtime_replaced: true, credential_owner: 'runtime_session' });
    expect(words).toContain('rt-20260922T191759Z-9b44a4b6');
    expect(words).toContain('rt-20260923T090000Z-cafe1234');
    expect(words).toContain('console server');
    expect(words).toContain('Reload to stop');
    expect(words).toContain('sends no command');
  });

  it('names the refusal when the server refused the credential by name', () => {
    expect(stopReload({ ...HEALTHY, credential_superseded: true, runtime_replaced: true })).toContain('credential_superseded');
  });

  it('names the served session and the one served now, without claiming a refusal, when the handshake showed the rotation (verify-r2 must_fix 1)', () => {
    const words = stopReload({
      ...HEALTHY, booted_runtime_session: null, current_runtime_session: 'rt-20260923T090000Z-cafe1234', credential_superseded: true, runtime_replaced: true,
      credential_owner: 'runtime_session', served: { runtime_session: 'rt-20260922T191759Z-9b44a4b6', owner: 'runtime_session' },
    }) ?? '';
    expect(words).toContain('served the credential of runtime session rt-20260922T191759Z-9b44a4b6');
    expect(words).toContain('rt-20260923T090000Z-cafe1234');
    expect(words).toContain('Reload to stop');
    expect(words).not.toMatch(/refused this tab/);
  });

  it('keeps the plain Stop when the server keeps one credential for its whole life (api_process), which a new runtime does not change', () => {
    expect(stopReload({ ...HEALTHY, current_runtime_session: 'rt-20260923T090000Z-cafe1234', runtime_replaced: true, credential_owner: 'api_process' })).toBeNull();
  });
});
