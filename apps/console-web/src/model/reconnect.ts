// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT TO OFFER WHEN THE RUNTIME UNDER THIS TAB IS NOT THE ONE IT STARTED AGAINST.
//
// The console detects the change in two places and turns commanding permanently off with `runtime_session_changed`.
// It then offered nothing: a bare reason code and a dead tab, with no way back short of the operator knowing to press
// F5 themselves.
//
// THE WAY BACK IS A DOCUMENT LOAD, AND THAT IS NOT A LIMITATION TO WORK AROUND. The API mints the credential for the
// RUNTIME SESSION and writes it into the index.html it serves; a page holds the one it was served at load. A tab whose
// runtime was replaced is holding a credential the server has stopped accepting, and no handshake gives it the new one,
// because the new one is only ever written into a freshly rendered page. So the action is `location.reload()`: it
// fetches a new document, which carries the current credential, and the store is constructed fresh against it, with
// readiness to establish again. ONE EXCEPTION, FOR STOP ALONE (redesign r4 verify-r4 must_fix 4): a Stop the server
// refuses `credential_superseded` reads that same freshly rendered page for its credential and is sent once more with
// it (`api/http.ts` stop). That credential is used for that one Stop and kept nowhere; nothing else this tab sends
// carries it, so every other control still offers the reload.
//
// WHAT A RELOAD IS GUARANTEED NOT TO DO, and why each matters:
//
//   IT SENDS NO COMMAND. Loading the page reads: a handshake, one snapshot, the record stream and the finished-run
//   list. `main.tsx` says so and the store's start() does exactly that. A command is only ever built by somebody
//   pressing a control.
//   IT REPLAYS NOTHING. There is no queue of commands to resend, because a command that was submitted was submitted
//   to the OLD runtime and belongs to it. A pending id that survives in browser storage is a record of what was sent,
//   and it is read back only to ASK the runtime what became of it, never to send it again.
//   IT CARRIES NO PHYSICAL ATTESTATION FORWARD. The attestations a live start requires are component state in
//   `Actions`, and a document load destroys them. The stated `holding` goes with them. That is the correct outcome
//   and not a side effect to be preserved: somebody stood at the robot and said the floor was clear of the OLD
//   session; nothing about that statement transfers to a runtime that has restarted since.
//
// A RELOAD IS ALSO NOT A CONFIRMATION. It gets the tab back to a state where it MAY command. Whether it then does is
// the operator's, through the same fresh Start confirmation as ever.

/** Why this tab is being offered a reload, or null when it is not. */
export type ReconnectReason = 'runtime_session_changed' | 'credential_superseded';

export interface ReconnectOffer {
  /** Null when nothing is wrong with this tab's authorization. */
  reason: ReconnectReason | null;
  /** What the operator is told, in one sentence. */
  sentence: string;
  /** What the control says. */
  label: string;
  /** What a reload will and will not do, stated on the control itself. */
  scope: string;
}

export interface SessionFacts {
  booted_runtime_session: string | null;
  current_runtime_session: string | null;
  credential_superseded: boolean;
  runtime_replaced: boolean;
  /** Who owns the credential the console API serves now, as its latest handshake said; absent or null when not stated. */
  credential_owner?: string | null;
  /** Who owned the credential this page was served with, as the boot handshake said; absent or null when not stated. */
  boot_credential_owner?: string | null;
  /** Whether the booted session ever answered this tab; absent is read as answered (the older shape). */
  booted_answered?: boolean;
  /**
   * Whose credential this page was served, as the console API's boot block said; absent or null when it did not say
   * (`api/boot.ts` ServedCredential).
   */
  served?: { runtime_session: string | null; owner: string | null } | null;
}

/** True when the console API minted this page's credential for itself and serves a runtime session's now. */
function apiMintedThenBound(session: SessionFacts): boolean {
  return session.boot_credential_owner === 'api_process' && session.credential_owner === 'runtime_session';
}

/**
 * The session this page's credential was served for and the one the console server serves now, when the boot block
 * named the first and they differ (redesign r4 verify-r2 must_fix 1); null otherwise.
 */
function servedForAnother(session: SessionFacts): { from: string; to: string } | null {
  const from = session.served?.runtime_session ?? null;
  const to = session.current_runtime_session;
  if (from === null || to === null || from === to) return null;
  return { from, to };
}

const NONE: ReconnectOffer = { reason: null, sentence: '', label: '', scope: '' };

const SCOPE =
  'Reloading this page fetches a new document, which is the only thing that carries the current credential. ' +
  'It sends no command, replays nothing, and does not carry forward anything anybody stated at the robot: a live ' +
  'start asks for those checks again.';

/**
 * Whether to offer a reload, and what to say about it.
 *
 * `credential_superseded` is reported only when the API said so in those words, and it is listed first because it is
 * the narrower and more certain of the two: the server has refused this tab's credential by name.
 */
export function reconnectOffer(session: SessionFacts): ReconnectOffer {
  if (session.credential_superseded) {
    const to = session.current_runtime_session;
    const served = servedForAnother(session);
    return {
      reason: 'credential_superseded',
      sentence: apiMintedThenBound(session)
        ? `The server has stopped accepting this tab’s credential. It minted that one itself before any runtime named a session, and it serves the credential of runtime session ${to ?? '(not named)'} now; a credential is written into the page at load, so no handshake gives this tab the new one; a reload does, for every control.`
        : served !== null
          ? `The server has stopped accepting this tab’s credential. This page was served the credential of runtime session ${served.from}, and the server serves the credential of runtime session ${served.to} now; a credential is written into the page at load, so no handshake gives this tab the new one; a reload does, for every control.`
          : 'The server has stopped accepting this tab’s credential. It is issued for one runtime session and written into the page at load, so no handshake gives this tab the new one; a reload does, for every control.',
      label: 'Reload to reconnect',
      scope: SCOPE,
    };
  }
  if (session.runtime_replaced) {
    const from = session.booted_runtime_session;
    const to = session.current_runtime_session;
    // READINESS IS CLAIMED ONLY AGAINST A SESSION THAT ANSWERED THIS TAB (verify-r1 must_fix 2, honesty S4): a session
    // the API named while the runtime was down is the one the credential belongs to, and nothing was checked against it.
    return {
      reason: 'runtime_session_changed',
      sentence:
        from !== null && to !== null
          ? session.booted_answered === false
            ? `This tab’s credential was issued for runtime session ${from}, which has not answered this tab, and ${to} is answering now. Nothing on this page was checked against the runtime that is running.`
            : `This tab established readiness against runtime session ${from}, and ${to} is answering now. Nothing was checked against the runtime that is running.`
          : 'The runtime answering now is not the one this tab established readiness against. Nothing was checked against the runtime that is running.',
      label: 'Reload to reconnect',
      scope: SCOPE,
    };
  }
  return NONE;
}

/** What a reload does, said on the control that offers it in place of Stop. */
const RELOAD_TO_STOP_TAIL =
  'Reload to stop: reloading fetches the page with the current credential and sends no command; press Stop again on the reloaded page.';

/**
 * WHAT STOP IS ON A TAB WHOSE CREDENTIAL THE CONSOLE SERVER NO LONGER ACCEPTS (redesign r4 verify-r1 must_fix 1), or null
 * while the plain Stop is what the server serves this tab.
 *
 * The console API keeps one credential per runtime session and rotates it when the runtime under it is replaced
 * (`console_api/app.py` bind, `security.py` rotate); POST /api/stop runs the same credential check as every command, so
 * a tab still holding the old value is refused `credential_superseded` before anything reaches the runtime. A Stop drawn
 * as a working stop there is a control the server refuses while the new runtime may be moving the robot. These are the
 * words of "Reload to stop", whose press reloads the page. Since round 7 (item 3) the screens draw it only when a press's
 * one-time takeover could not send its second Stop (`staleStop`); until then a stale tab's Stop takes over on its press.
 *
 * It applies when the server refused this tab's credential by name, and when the runtime was replaced under a console
 * API that owns its credential per RUNTIME SESSION (its handshake said `credential_owner: runtime_session`). An API that
 * says `api_process` for a runtime it serves keeps one credential for its whole life (it has no session store), so a
 * replaced runtime changes nothing about Stop there, and Stop stays the plain POST the server accepts.
 */
export function stopReload(session: SessionFacts): string | null {
  const stale = staleCredential(session);
  return stale === null ? null : `${stale.fact}, so ${stale.refusal} ${RELOAD_TO_STOP_TAIL}`;
}

/**
 * WHY THIS TAB'S CREDENTIAL IS NOT THE ONE THE CONSOLE SERVER ACCEPTS NOW, as a fact (no refusal claimed), and the
 * refusal a Stop sent with it meets; null while it is the accepted one. `stopReload` and `staleStop` word it.
 */
function staleCredential(session: SessionFacts): { fact: string; refusal: string } | null {
  if (session.credential_superseded && apiMintedThenBound(session)) {
    return {
      fact: `The console server minted this tab's credential itself before any runtime named a session, and it serves the credential of runtime session ${session.current_runtime_session ?? '(not named)'} now`,
      refusal: 'a Stop sent with it is refused before it reaches the runtime.',
    };
  }
  const served = session.credential_superseded ? servedForAnother(session) : null;
  if (served !== null) {
    // The handshake's word, not a refusal: nothing has to have been sent for this to be known (verify-r2 must_fix 1).
    return {
      fact: `This page was served the credential of runtime session ${served.from}, and the console server serves the credential of runtime session ${served.to} now`,
      refusal: 'a Stop sent with it would be refused before it reaches the runtime.',
    };
  }
  if (session.credential_superseded) {
    return { fact: "The console server refused this tab's credential (credential_superseded)", refusal: 'a Stop sent with it is refused before it reaches the runtime.' };
  }
  if (session.runtime_replaced && session.credential_owner === 'runtime_session') {
    const from = session.booted_runtime_session;
    const to = session.current_runtime_session;
    const whose =
      from !== null && to !== null
        ? `This tab's credential belongs to runtime session ${from}, and ${to} is answering now`
        : "This tab's credential belongs to a runtime session that is not the one answering now";
    return { fact: `${whose}; the console server accepts only the credential of the session it serves`, refusal: 'a Stop sent with it would be refused before it reaches the runtime.' };
  }
  return null;
}

/** What a stale tab's Stop press does (redesign r4 round 7 item 3), said on the control. */
const TAKEOVER_TAIL =
  "A press sends Stop; when the console server refuses this tab's credential as superseded, this display reads the page the console server serves now, as a reload does, and sends Stop once more with the credential in it. This is not an emergency stop.";

/** The part of a Stop answer `staleStop` reads: whether a takeover was tried and what came of it. */
export interface StopPressFacts {
  outcome: string;
  reason: string | null;
  takeover?: { sent: boolean; not_sent_reason: string | null } | null;
}

/**
 * STOP ON A TAB THAT KNOWS ITS CREDENTIAL IS NOT THE ONE THE CONSOLE SERVER ACCEPTS NOW (redesign r4 round 7 item 3), or
 * null while it is. The press goes through the one-time takeover of `api/http.ts` stop(): refused `credential_superseded`,
 * the tab reads the page the server serves now and sends Stop once more with its credential, so the first press stops
 * the run. "Reload to stop" (a press reloads the page and sends no command) is drawn only after a press whose takeover
 * could not send its second Stop, or whose second Stop was refused for its credential too: the reload is then the way
 * left.
 */
export function staleStop(session: SessionFacts, lastStop: StopPressFacts | null): { reload: boolean; title: string } | null {
  const stale = staleCredential(session);
  if (stale === null) return null;
  if (lastStop !== null) {
    const takeover = lastStop.takeover ?? null;
    const refusedForCredential = lastStop.outcome === 'refused' && (lastStop.reason ?? '').startsWith('credential_');
    const why =
      takeover !== null && !takeover.sent
        ? `the last press could not send Stop with the credential of the page the console server serves now: ${takeover.not_sent_reason ?? 'no reason was given'}`
        : takeover !== null && refusedForCredential
          ? `the last press sent Stop with the credential of the page the console server serves now, and that was refused too (${lastStop.reason ?? ''})`
          : takeover === null && refusedForCredential
            ? // A page not served with a boot element (a dev server), or a refusal other than credential_superseded: no takeover.
              `the last press was refused for this tab's credential (${lastStop.reason ?? ''}) and no second Stop was sent`
            : null;
    if (why !== null) return { reload: true, title: `${stale.fact}; ${why}. ${RELOAD_TO_STOP_TAIL}` };
  }
  return { reload: false, title: `${stale.fact}. ${TAKEOVER_TAIL}` };
}
