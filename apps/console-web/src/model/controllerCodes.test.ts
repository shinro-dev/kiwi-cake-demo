// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// EVERY CODE THE CONTROLLER CAN END A RUN WITH HAS AN ACCOUNT (operator ruling 2026-09-28; verify r6 M1).
//
// The controller ends a run in exactly one way: `raise self.failed(code, ...)` in run_single_cube_cycle.py, or
// `raise ctx.failed(code, ...)` in the stage policies (autonomy/manipulation/policy_*.py). This test reads those sources,
// finds every raise site, and resolves each site's code:
//
//   a literal ("grasp_missed"), taken as it is written;
//   the perception family (f"perception_remote_{...}"), glossed as a family;
//   a name, resolved from the source it comes from: a constant of the controller, target_binding.py's refusal and
//   attribution codes (TargetRefused.reason_code, Placement.reason_code), the controller's own listing refusals
//   (_mission_candidates, _mission_placed) and the base pulse's two refusal codes.
//
// A raise site whose code this test cannot resolve fails it, and so does a resolved code that has no account: the mission
// check's (missionCheck.ts, "what the robot did, and what the check could not do or refused") or the failure glossary's
// (failure.ts and the stage files). So a new code, or a new way of making one, fails here before it reaches a card.
//
// The codes that can end a run AT OR AFTER PLACED are named apart: every raise site of policy_return.py (it runs after
// the release) and every code the mission attribution can carry there. Each of those must have its account, and the
// mission-check ones must be said as a placement (after the release), never as a SEARCH refusal.

import { describe, expect, it } from 'vitest';
import controller from '../../../../bin/laptop/manipulation/run_single_cube_cycle.py?raw';
import targetBinding from '../../../../autonomy/manipulation/target_binding.py?raw';
import remotePerception from '../../../../autonomy/perception/remote_perception.py?raw';
import { describeFailureCode } from './failure';
import { MISSION_CHECK_CODES, RETIRED_MISSION_CHECK_CODES, missionCheck } from './missionCheck';
import type { MissionAttemptRecord } from './missionRecord';

/** Every module of autonomy/manipulation, as text; the ones with a raise site are found below, not listed by hand. */
const MANIPULATION = import.meta.glob('../../../../autonomy/manipulation/*.py', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

const RAISE = /(?:ctx|self)\.failed\(\s*([^,]+),/g;

function between(text: string, start: string, end: string): string {
  const from = text.indexOf(start);
  if (from < 0) throw new Error(`not found: ${start}`);
  const to = text.indexOf(end, from + start.length);
  return text.slice(from, to < 0 ? undefined : to);
}

/** The body of a method or function of `text`, from its def to the next def at the same or an outer indentation. */
function body(text: string, name: string): string {
  const match = new RegExp(`^( *)def ${name}\\(`, 'm').exec(text);
  if (match === null) throw new Error(`no def ${name}`);
  const indent = match[1]?.length ?? 0;
  const rest = text.slice(match.index + match[0].length);
  const next = new RegExp(`^ {0,${String(indent)}}(?:def |class |@)`, 'm').exec(rest);
  return text.slice(match.index, match.index + match[0].length + (next === null ? rest.length : next.index));
}

/** The name of the def a raise site at `index` sits in. */
function enclosing(text: string, index: number): string {
  const defs = [...text.slice(0, index).matchAll(/^ *def ([a-z_0-9]+)\(/gm)];
  return defs[defs.length - 1]?.[1] ?? '';
}

function literals(text: string, pattern: RegExp): string[] {
  return [...text.matchAll(pattern)].map((match) => match[1]).filter((code): code is string => code !== undefined);
}

/** target_binding.py's "refusal and attribution reason codes" block: what TargetRefused and Placement carry. */
const BINDING_CODES = literals(between(targetBinding, '# ---- refusal and attribution reason codes', '\nclass '), /^[A-Z_]+ = "(mission_[a-z_]+)"$/gm);
/** The controller's own refusals to list the view the check looks at, and to read a listed block's position. */
const LISTING_CODES = [...literals(body(controller, '_mission_candidates'), /return \[\], "([a-z_]+)"/g), ...literals(body(controller, '_mission_placed'), /"(mission_[a-z_]+)"/g)];
/** What policy_return.py's attribution raise can carry: target_binding's placement codes and the listing refusals. */
const ATTRIBUTION_CODES = [...BINDING_CODES.filter((code) => MISSION_CHECK_CODES[code]?.stage === 'placement'), ...LISTING_CODES];

function constant(text: string, name: string): string {
  const match = new RegExp(`^${name} = "([a-z_0-9]+)"`, 'm').exec(text);
  if (match?.[1] === undefined) throw new Error(`no constant ${name}`);
  return match[1];
}

const PERCEPTION_FAMILY = 'perception_remote_';

interface Site {
  file: string;
  fn: string;
  expression: string;
  codes: string[];
}

/** Resolve one raise site's first argument to the codes it can carry, or null when this test cannot. */
function resolve(file: string, text: string, fn: string, expression: string): string[] | null {
  const literal = /^"([a-z0-9_]+)"$/.exec(expression);
  if (literal?.[1] !== undefined) return [literal[1]];
  if (expression.startsWith(`f"${PERCEPTION_FAMILY}{`)) return [`${PERCEPTION_FAMILY}*`];
  if (expression === 'LIVE_FORWARD_EXCLUDED' || expression === 'ctl.LIVE_FORWARD_EXCLUDED') return [constant(controller, 'LIVE_FORWARD_EXCLUDED')];
  if (expression === 'ctl.target_binding.REFUSED_MODE_MISMATCH') return [constant(targetBinding, 'REFUSED_MODE_MISMATCH')];
  if (expression === 'refusal.reason_code' && fn === '_mission_search') return BINDING_CODES.filter((code) => MISSION_CHECK_CODES[code]?.stage === 'binding');
  if (expression === 'attribution.reason_code' && file.endsWith('policy_return.py')) return ATTRIBUTION_CODES;
  if (expression === 'code' && fn === '_mission_search') return literals(body(controller, '_mission_candidates'), /return \[\], "([a-z_]+)"/g);
  if (expression === 'code' && fn === 'base_pulse') return literals(body(text, 'base_pulse'), /code(?:, [a-z_]+)+ = "([a-z_]+)"/g);
  return null;
}

function sites(file: string, text: string): Site[] {
  const out: Site[] = [];
  for (const match of text.matchAll(RAISE)) {
    const expression = (match[1] ?? '').trim();
    const fn = enclosing(text, match.index);
    if (fn === 'failed') continue; // the definition's own docstring names the call
    const codes = resolve(file, text, fn, expression);
    if (codes === null) throw new Error(`${file}: the raise site in ${fn} with code ${expression} is not resolved by this test`);
    out.push({ file, fn, expression, codes });
  }
  return out;
}

const SOURCES: [string, string][] = [
  ['bin/laptop/manipulation/run_single_cube_cycle.py', controller],
  ...Object.entries(MANIPULATION).map(([path, text]): [string, string] => [path.replace(/^(\.\.\/)+/, ''), text]),
];
const ALL_SITES = SOURCES.flatMap(([file, text]) => sites(file, text));
const ALL_CODES = [...new Set(ALL_SITES.flatMap((site) => site.codes))].sort();

function accounted(code: string): boolean {
  if (MISSION_CHECK_CODES[code] !== undefined) return true;
  // A family is accounted when its gloss covers a member; the test below holds it to every member remote_perception.py has.
  const probe = code.endsWith('*') ? `${code.slice(0, -1)}deadline` : code;
  return describeFailureCode(probe) !== null;
}

function attemptAt(code: string, phase: string, measurements: MissionAttemptRecord['measurements'] = []): MissionAttemptRecord {
  return {
    attempt: 1, run_id: 'r', park_run_id: null, outcome_class: 'fatal', reason_code: code, phase, measurements, observation_ids: [],
    stop_state: { holding: false, base_ahead: false, moved: true }, next_actions: ['start'], budget_left: 1, t_wall_utc: '2026-09-28T17:37:34.515Z',
  };
}

describe('every code the controller can end a run with', () => {
  it('finds the raise sites of the controller and of every stage policy', () => {
    const files = new Set(ALL_SITES.map((site) => site.file.replace(/^.*\//, '')));
    for (const file of ['run_single_cube_cycle.py', 'policy_search.py', 'policy_align.py', 'policy_grasp.py', 'policy_placement.py', 'policy_return.py']) {
      expect(files.has(file), file).toBe(true);
    }
    expect(ALL_SITES.length).toBeGreaterThan(80);
  });

  it('resolves the mission path\'s named codes from their own sources', () => {
    // The pad policy of 2026-09-29: seven binding refusals and the release agreement's one placement refusal.
    expect(BINDING_CODES).toHaveLength(8);
    expect(BINDING_CODES.filter((code) => MISSION_CHECK_CODES[code]?.stage === 'placement')).toEqual(['mission_placed_no_cube_at_release_point']);
    expect([...LISTING_CODES].sort()).toEqual([
      'mission_candidate_unreadable',
      'mission_candidates_failed',
      'mission_candidates_frame_unreadable',
      'mission_candidates_incomplete',
      'mission_candidates_refused',
      'mission_placed_no_exit_view',
    ]);
  });

  it('names no retired code: each has an account only for the old records History shows', () => {
    for (const code of RETIRED_MISSION_CHECK_CODES) expect(ALL_CODES, code).not.toContain(code);
    for (const [, text] of SOURCES) for (const code of RETIRED_MISSION_CHECK_CODES) expect(text.includes(`"${code}"`), code).toBe(false);
  });

  it('has an account for every one of them', () => {
    const missing = ALL_CODES.filter((code) => !accounted(code));
    expect(missing).toEqual([]);
  });

  it('glosses the whole perception family, whose codes are remote_perception.py\'s', () => {
    const codes = literals(between(remotePerception, '# Reason codes.', '\n\n\n'), /"([a-z_]+)"/g);
    expect(codes.length).toBeGreaterThan(10);
    for (const code of codes) expect(describeFailureCode(`${PERCEPTION_FAMILY}${code}`), code).not.toBeNull();
    expect(describeFailureCode(PERCEPTION_FAMILY)).toBeNull();
  });
});

describe('the codes that can end a run at or after PLACED', () => {
  const afterPlaced = [...new Set(ALL_SITES.filter((site) => site.file.endsWith('policy_return.py')).flatMap((site) => site.codes))].sort();

  it('are every raise site of policy_return.py, the attribution among them', () => {
    for (const code of ATTRIBUTION_CODES) expect(afterPlaced).toContain(code);
    expect(afterPlaced).toContain('placed_not_demonstrated');
    expect(afterPlaced).toContain('base_not_returned');
  });

  it('each has an account', () => {
    expect(afterPlaced.filter((code) => !accounted(code))).toEqual([]);
  });

  it('each mission-check one is said as a placement after the release, ending with what the record says of PLACED', () => {
    for (const code of ATTRIBUTION_CODES) {
      const check = missionCheck(attemptAt(code, 'BASE_RETURNED'), []);
      expect(check?.stage, code).toBe('placement');
      expect(check?.refused, code).toMatch(/^The mission check (did not accept|could not check) the placement: .+ The record says placed: false\.$/);
    }
  });
});
