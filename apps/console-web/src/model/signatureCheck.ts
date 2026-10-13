// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SIGNATURE POLICY CHECK, as the runtime serves it (GET /api/capability/signature), read at the boundary.
//
// WHAT IT IS. The release's own verifier (`demo-plan verify --policy require`), run on the robot by the runtime over
// every capsule the deployed manifest names: under the resident's trusted keys (expected admitted), under a throwaway key
// the runtime generates and discards (expected refused, signature_key_id_not_trusted) and for any unsigned twin the
// manifest names (expected refused, signature_missing). NO motion, no slot touched, nothing asked of the resident
// (`demo_runtime/signature_check.py`).
//
// WHAT IT IS NOT, AND THE SCREEN SAYS SO. The resident admits capsules only when it starts; there is no live admission
// operation in it, and the abort-swap candidate of the slot check is a transactional activation failure, not a
// signature refusal. Nothing here upgrades a verifier verdict into a claim about the live resident.
//
// WHAT THIS FILE DOES. It reads the runtime's answer field by field, the way `capabilityCheck.ts` reads the slot check,
// and it decides the little a screen needs: whether the Run button is off and why, and the words of a row. A row's
// `admitted` and `as_expected` are the runtime's, drawn as they come; a SKIPPED row is never drawn as a pass.

/** The only version of this surface this console speaks. It moves on its own, apart from every other version. */
export const SIGNATURE_CHECK_VERSION = '1';

/** One line of the verdict table, as the runtime recorded it. */
export interface SignatureRow {
  /** The capsule's content digest (64 hex), or null for the skipped twin row when none is staged. */
  capsule: string | null;
  /** signed_capsule or unsigned_twin. */
  kind: string;
  /** deployment (the resident's trusted keys) or throwaway. */
  key: string;
  expected: string;
  expected_reason: string | null;
  /** admitted, refused, error or skipped. */
  observed: string;
  reason_code: string | null;
  /** The verdict as a boolean; null for an error or a skipped row. */
  admitted: boolean | null;
  /** The runtime's comparison of verdict and reason with what was expected; null for a skipped row. */
  as_expected: boolean | null;
}

/** One recorded run of the verifier. */
export interface SignatureCheckRecord {
  check_uuid: string;
  runtime_session: string | null;
  t_wall_utc: string | null;
  policy: string | null;
  trusted_keys: string[];
  throwaway_public_key: string | null;
  capsules: number | null;
  unsigned_twins: number | null;
  rows: SignatureRow[];
  rows_as_expected: number;
  rows_not_as_expected: number;
  rows_skipped: number;
  rows_error: number;
  /** True only when every row that RAN read as expected. */
  as_expected: boolean | null;
  /** True only when no row was skipped or an error. */
  complete: boolean | null;
  duration_ms: number | null;
}

export interface SignatureCheckState {
  signature_check_version: string;
  in_flight: boolean;
  /** The first reason the runtime would refuse a run now. Null: none it can see. */
  off_reason: string | null;
  policy: string | null;
  /** Newest last, as the runtime bounds them. */
  checks: SignatureCheckRecord[];
  checks_dropped: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asText(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function asBool(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function asCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

/** One row, or null when it names no kind, key, expectation or observation (there is then nothing to draw). */
export function parseSignatureRow(value: unknown): SignatureRow | null {
  if (!isRecord(value)) return null;
  const kind = asText(value['kind']);
  const key = asText(value['key']);
  const expected = asText(value['expected']);
  const observed = asText(value['observed']);
  if (kind === null || key === null || expected === null || observed === null) return null;
  return {
    capsule: asText(value['capsule']),
    kind,
    key,
    expected,
    expected_reason: asText(value['expected_reason']),
    observed,
    reason_code: asText(value['reason_code']),
    admitted: asBool(value['admitted']),
    as_expected: asBool(value['as_expected']),
  };
}

/** One recorded run, or null when it carries no check id (there is then nothing to report about it). */
export function parseSignatureCheck(value: unknown): SignatureCheckRecord | null {
  if (!isRecord(value)) return null;
  const uuid = asText(value['check_uuid']);
  if (uuid === null) return null;
  const rows: SignatureRow[] = [];
  for (const item of Array.isArray(value['rows']) ? value['rows'] : []) {
    const row = parseSignatureRow(item);
    if (row !== null) rows.push(row);
  }
  const keys = Array.isArray(value['trusted_keys']) ? value['trusted_keys'].filter((item): item is string => typeof item === 'string') : [];
  return {
    check_uuid: uuid,
    runtime_session: asText(value['runtime_session']),
    t_wall_utc: asText(value['t_wall_utc']),
    policy: asText(value['policy']),
    trusted_keys: keys,
    throwaway_public_key: asText(value['throwaway_public_key']),
    capsules: asCount(value['capsules']),
    unsigned_twins: asCount(value['unsigned_twins']),
    rows,
    rows_as_expected: asCount(value['rows_as_expected']) ?? 0,
    rows_not_as_expected: asCount(value['rows_not_as_expected']) ?? 0,
    rows_skipped: asCount(value['rows_skipped']) ?? 0,
    rows_error: asCount(value['rows_error']) ?? 0,
    as_expected: asBool(value['as_expected']),
    complete: asBool(value['complete']),
    duration_ms: asCount(value['duration_ms']),
  };
}

/**
 * The runtime's answer, or null when it is not one this console speaks. A version this console does not know is not
 * read at all: a surface that changed meaning must not be drawn with this one's words.
 */
export function parseSignatureCheckState(value: unknown): SignatureCheckState | null {
  if (!isRecord(value) || value['ok'] !== true) return null;
  if (value['signature_check_version'] !== SIGNATURE_CHECK_VERSION) return null;
  const checks: SignatureCheckRecord[] = [];
  for (const item of Array.isArray(value['checks']) ? value['checks'] : []) {
    const check = parseSignatureCheck(item);
    if (check !== null) checks.push(check);
  }
  return {
    signature_check_version: SIGNATURE_CHECK_VERSION,
    in_flight: value['in_flight'] === true,
    off_reason: asText(value['off_reason']),
    policy: asText(value['policy']),
    checks,
    checks_dropped: asCount(value['checks_dropped']) ?? 0,
  };
}

/** Why the runtime would refuse a run, in words, by its code. A code with no words here is printed as itself. */
export const SIGNATURE_OFF_WORDS: Record<string, string> = {
  signature_check_in_flight: 'a signature check is already in flight; nothing is sent twice',
  mission_running: 'a mission is being carried, and the check never runs under one',
  run_active: 'a controller run is active, and the check never runs under one',
  owner_unresolved: 'a command is unresolved, and nothing but stop is accepted until it is settled',
  capability_check_in_flight: 'a capability slot check is in flight, and its ApplyPlan is moving the store this verifier would read',
};

export function signatureOffWords(code: string): string {
  return `${SIGNATURE_OFF_WORDS[code] ?? 'the runtime would refuse it'} (${code})`;
}

/**
 * WHY THE RUN BUTTON IS OFF, or null when it may be pressed. In this order: the tab may not command at all; a press is
 * already in flight from this tab; the runtime says a run is in flight or would refuse one.
 */
export function signatureRunOff(input: { state: SignatureCheckState; running: boolean; readOnlyReason: string | null }): string | null {
  const { state, running, readOnlyReason } = input;
  if (readOnlyReason !== null) return readOnlyReason;
  if (running) return 'a signature check from this tab is in flight; nothing is sent twice';
  if (state.in_flight) return signatureOffWords('signature_check_in_flight');
  if (state.off_reason !== null) return signatureOffWords(state.off_reason);
  return null;
}

/** Why a runtime offers no check, by the `detail` it sends beside `signature_check_unavailable`. */
export const SIGNATURE_UNAVAILABLE_WORDS: Record<string, string> = {
  signature_check_requires_live_mode: 'this runtime is not LIVE, and the verifier is only ever run on the robot',
  robot_not_configured: 'this runtime names no robot resident',
  robot_link_not_local: 'this runtime does not run on the robot, where the verifier and the deployed store are',
  signature_check_not_configured: 'this runtime names no release (verifier, manifest and resident.conf) to verify',
};

/** One sentence for a runtime that offers no check: the runtime's detail in words, or its code as itself. */
export function signatureUnavailableWords(reasonCode: string, detail: string | null): string {
  if (detail !== null) return `${SIGNATURE_UNAVAILABLE_WORDS[detail] ?? 'the runtime says it cannot run one'} (${detail})`;
  return reasonCode === 'signature_check_unavailable' ? 'the runtime says it cannot run one' : reasonCode;
}

/** The first twelve hex characters of a digest, which is how the table names a capsule; the whole digest is its title. */
export function shortDigest(digest: string | null): string {
  return digest === null ? 'none staged' : digest.slice(0, 12);
}

/** Admitted, as a word: yes, no, or not run (an error or a skipped row). Never yes for a row that did not run. */
export function signatureAdmittedWords(admitted: boolean | null): string {
  return admitted === true ? 'yes' : admitted === false ? 'no' : 'not run';
}

/** What a row came to against what was expected. A skipped row is said to be skipped and why, never as expected. */
export function signatureVerdictWords(row: SignatureRow): string {
  if (row.observed === 'skipped') {
    return row.reason_code === 'unsigned_twin_not_staged'
      ? 'skipped: none staged, so nothing was verified'
      : `skipped (${row.reason_code ?? 'no reason recorded'})`;
  }
  if (row.observed === 'error') return `error: the verifier gave no verdict (${row.reason_code ?? 'no reason recorded'})`;
  if (row.as_expected === true) return 'as expected';
  return `NOT as expected: ${row.expected}${row.expected_reason === null ? '' : ` ${row.expected_reason}`} was expected`;
}

/** The run's own summary line, from the runtime's counts. */
export function signatureSummaryWords(check: SignatureCheckRecord): string {
  const parts = [`${String(check.rows_as_expected)} rows as expected`];
  if (check.rows_not_as_expected > 0) parts.push(`${String(check.rows_not_as_expected)} NOT as expected`);
  if (check.rows_error > 0) parts.push(`${String(check.rows_error)} in error`);
  if (check.rows_skipped > 0) parts.push(`${String(check.rows_skipped)} skipped`);
  const completeness = check.complete === true ? 'complete' : 'not complete: a row was skipped or in error, and a skipped row is not a pass';
  return `${parts.join(', ')}; ${completeness}`;
}
