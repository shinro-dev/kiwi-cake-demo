// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The boot element the API injects into the page it serves.
//
// `apps/console-api` writes a `<script type="application/json" id="kc-console-boot">` into the index.html it hands
// out. It carries the per-launch credential and the NAMES of the two headers a mutating request must present it
// under. Without them every command is refused by the API's guard, and without the guard a page on any other origin
// could POST a motion command to this loopback port.
//
// The credential is held in memory and nowhere else: it is never written to storage, never put in a URL (a URL reaches
// access logs and the browser's history), never shown on the screen and never logged. It is read once, here.
//
// A page opened another way (the vite dev server, a static preview) has no boot element. That is not treated as
// permission to command: `credentials` is null, the console says so in the SERVER's own words when a command is
// refused, and nothing is invented to take its place.

export interface ConsoleCredential {
  /** The header name the API expects the credential under, from the boot element. Never hard-coded here. */
  credential_header: string;
  credential: string;
  /** The custom header whose presence forces a CORS preflight the API answers for nobody. */
  request_header: string;
  request_value: string;
}

/**
 * WHOSE CREDENTIAL THIS PAGE WAS SERVED (redesign r4 verify-r2 must_fix 1): the runtime session the console API had bound
 * the credential to when it rendered this page (`bound_runtime_session`, null when the API had minted its own because no
 * runtime had named a session), and who owned it then (`credential_owner`). The API rotates its credential when it
 * follows another runtime session, so a handshake that names another bound session than this one says, before any press,
 * that the server no longer accepts what this page holds.
 */
export interface ServedCredential {
  runtime_session: string | null;
  owner: string | null;
}

export interface ConsoleBoot {
  contract_version: string | null;
  api_base: string | null;
  api_session: string | null;
  credentials: ConsoleCredential | null;
  /** Null or absent when the boot element does not say which session the credential belongs to (an older API). */
  served_credential?: ServedCredential | null;
}

export const BOOT_ELEMENT_ID = 'kc-console-boot';

/** The value the API's `CONSOLE_HEADER_VALUE` is; it is a constant of the guard, not a secret. */
const REQUEST_HEADER_VALUE = '1';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asText(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

/**
 * Read the boot element. Every field is optional and a field of the wrong type is null: this is the one place the page
 * reads something it did not compute, and a malformed element must not take the screen down.
 */
export function parseBoot(value: unknown): ConsoleBoot | null {
  if (!isRecord(value)) return null;
  const credential = asText(value['credential']);
  const credentialHeader = asText(value['credential_header']);
  const requestHeader = asText(value['request_header']);
  // Stated only when the key is there and is a session or null; anything else is an API that did not say.
  const bound = value['bound_runtime_session'];
  const statesSession = 'bound_runtime_session' in value && (bound === null || typeof bound === 'string');
  return {
    contract_version: asText(value['contract_version']),
    api_base: asText(value['api_base']),
    api_session: asText(value['api_session']),
    credentials:
      credential === null || credentialHeader === null || requestHeader === null
        ? null
        : { credential_header: credentialHeader, credential, request_header: requestHeader, request_value: REQUEST_HEADER_VALUE },
    served_credential: statesSession ? { runtime_session: asText(bound), owner: asText(value['credential_owner']) } : null,
  };
}

export interface BootDocument {
  getElementById(id: string): { textContent: string | null } | null;
}

/** The boot element of a page the API served, or null when this page was not served by it. */
export function readBoot(document: BootDocument): ConsoleBoot | null {
  const element = document.getElementById(BOOT_ELEMENT_ID);
  if (element === null) return null;
  const text = element.textContent;
  if (text === null || text === '') return null;
  try {
    return parseBoot(JSON.parse(text) as unknown);
  } catch {
    return null;
  }
}

/**
 * THE BOOT ELEMENT OF A PAGE READ AS TEXT, or null (redesign r4 verify-r4 must_fix 4). A tab whose Stop the console server
 * refused `credential_superseded` reads the page the server serves now, as a reload would, and takes the credential from
 * its boot element for that one Stop. The server renders the element as a data element whose '<' are escaped
 * (`console_api/web.py` render_boot), so the first `</script>` after it closes it.
 */
export function bootFromHtml(html: string): ConsoleBoot | null {
  const at = html.indexOf(`id="${BOOT_ELEMENT_ID}"`);
  if (at < 0) return null;
  const open = html.indexOf('>', at);
  const close = open < 0 ? -1 : html.indexOf('</script>', open);
  if (close < 0) return null;
  try {
    return parseBoot(JSON.parse(html.slice(open + 1, close)) as unknown);
  } catch {
    return null;
  }
}

/**
 * The headers a mutating request carries. `content-type` is one of the four things the API's guard checks, so it is
 * never omitted, and the credential headers are added only when this page was served with them.
 */
export function commandHeaders(credentials: ConsoleCredential | null): Record<string, string> {
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (credentials === null) return headers;
  headers[credentials.request_header] = credentials.request_value;
  headers[credentials.credential_header] = credentials.credential;
  return headers;
}
