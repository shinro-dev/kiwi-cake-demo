// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SCENE MAP, FOLDED FROM THE RECORD (docs/autonomous-demo/overnight-plan-2026-09-26.md, track U2).
//
// A top-down map in PAD_FRAME millimetres (origin at the pad centre, x along the long edge, y toward the top edge):
// the pad, the pan axis and the home bearing, the pan band's wedge ONLY when the scan's fit was accepted, the ground
// each scan view saw, the blocks coloured by their class with the class sentence, the measured base pulses, the
// placed block, and the placement envelope at the margin the record decided with. It is folded from events alone and
// is SEPARATE from the reducer: the reducer lets every `perception.result` replace a frame's own perception
// (`reducer.ts`), and the scene results name several frames at once, so this fold keeps them apart from it.
//
// THE ROBOT'S BASE IS NOT DRAWN. No record carries where the base stands on the floor, and a base drawn at a guessed
// place would be the one mark on this map nobody measured. The map says so instead.
//
// WHAT IT READS, AND ONLY THAT:
//   perception.result `scene-view`, `scene-pad`, `scene-block`   the scan's views, its pan-axis fit and its blocks
//                                                                  (track S; the measurement names are the ones
//                                                                  `api/syntheticScenarios.ts` spells)
//   perception.result `cube-in-pad-frame` at SEARCH or in a Look   a block seen, with its pad-frame position
//   perception.result `cube-recognizer`, `cube-in-pad-frame`,      what the newest run answered about the block, any
//     `scene-view` of the newest run                                 result (the line beside an earlier run's block)
//   [STATE] TARGET_SELECTED, PLACEMENT_PLAN, BASE_ADVANCE,          the selection and its mode, the anchor, each
//     BASE_MEASURED, PLACED                                          pulse's axis and measured advance, the placement
//   run.finished reason_code and failure measurements              the class of a block refused at SEARCH, and that
//                                                                  the newest run has ended
//   predicate.evaluated thresholds of the placement predicates     the placement envelope

import type { ContractEvent } from '../contracts/envelope';
import { placementLimit } from './placementLimit';
import { liveSlotWhen } from './liveSlot';
import type { LiveSlot } from './liveSlot';

/**
 * THE CAPABILITY WORDS BESIDE A BLOCK THAT NEEDS THE BASE ADVANCE, from GET /api/robot's `capability`
 * (docs/autonomous-demo/console-redesign-2026-09-27.md, rule 5.7.5; audit C8): the live slot, never a fixed text.
 */
export function baseCapabilityWords(slot: LiveSlot | null | undefined): string {
  if (slot === null || slot === undefined) return 'needs the base capability; the robot has not been read';
  const name = slot.name ?? 'the base capability';
  // WITH THE TIME OF THE RECORD IT RESTS ON (browser r2 N1), which is the newest record by time (`model/liveSlot.ts`).
  const when = liveSlotWhen(slot);
  if (slot.active === true) return `the base advance uses ${name}, active${slot.generation === null ? '' : ` at generation ${slot.generation}`} (${when})`;
  if (slot.active === false) return `needs ${name}, which is not active (${when})`;
  return `needs ${name}, and whether it is active was not read back (${when})`;
}

export type Point = [number, number];

/** The classes a seen block takes (plan, track S: `classify_block`), and the sentence each one reads as. */
export const BLOCK_CLASSES = {
  ALREADY_PLACED: 'already on the pad: nothing to carry',
  ARM_ONLY: 'the arm alone reaches a placement: no base motion',
  // The capability the base advance uses is said beside it from the live slot (`baseCapabilityWords`), never here.
  BASE_ADVANCE: 'placing it needs the base advance',
  NOT_PLACEABLE: 'no bounded placement from here',
  TOO_NEAR: 'nearer than the pickup band',
  OUTSIDE_PAN_BAND: 'beyond the pan band from home',
  SEEN_NOT_MEASURABLE: 'seen, but no pad-frame position could be measured for it',
  NO_PAD_FRAME: 'seen in a view with no pad frame',
} as const;

export type BlockClass = keyof typeof BLOCK_CLASSES;

type Detail = { name: string; value: string | number | boolean | null; unit: string | null };

export interface SceneBlock {
  point_mm: Point;
  /** The class the record gave it, or null where nothing classified it (a Look, a SEARCH that ended first). */
  class: BlockClass | string | null;
  sentence: string;
  /** Where it came from: the scan's fused scene, the SEARCH frame of a cycle, or a Look. */
  source: 'scan' | 'search' | 'look';
  observation_id: string | null;
  views_seen: number | null;
  spread_mm: number | null;
  /** A position carried from another view by pan only: never a placement anchor (plan, track S). */
  inferred: boolean;
  /** The block the run selected as its target. */
  selected: boolean;
  details: Detail[];
  /** The run whose record drew it, and that record's wall time; null where the record named none. */
  run_id: string | null;
  seen_wall_utc: string | null;
  /**
   * Drawn from an EARLIER run than the one the fold is on now (redesign r4 verify-r2 must_fix 3). A mission's next run
   * begins with no sighting of its own; the block the earlier run saw stays on the map, as that run's, until the new
   * run reports a detection of its own. A scan's blocks are the scan run's and are kept by the scan's own rule.
   */
  earlier: boolean;
}

export interface SceneView {
  observation_id: string | null;
  view: string | null;
  pan_deg: number | null;
  footprint_mm: Point[] | null;
  pad_frame_valid: boolean | null;
  accepted: boolean | null;
}

export interface SceneFit {
  axis_mm: Point;
  /** The home pan's bearing in PAD_FRAME, degrees from +y toward +x. Null when the record did not give it. */
  home_bearing_deg: number | null;
  band_deg: number | null;
  rotation_deg: number | null;
  residual_mm: number | null;
  accepted: boolean;
}

export interface SceneWedge {
  apex_mm: Point;
  /** Bearings from +y toward +x, degrees. */
  from_deg: number;
  to_deg: number;
  radius_mm: number;
}

export interface ScenePulse {
  index: number;
  from_mm: Point;
  /** Where the held block's anchor moved: the measured advance ALONG the pulse's axis. The lateral is stated, not drawn. */
  to_mm: Point;
  axis: Point;
  advance_mm: number;
  lateral_mm: number | null;
}

export interface SceneReturnPulse {
  index: number;
  advance_mm: number;
  lateral_mm: number | null;
}

export interface ScenePlaced {
  point_mm: Point;
  centre_to_edge_mm: number | null;
}

/** One answer of a cube layer (`cube-recognizer`, `cube-in-pad-frame`), as the run's record gives it. */
export interface SceneAnswer {
  detector: string;
  result: string;
  observation_id: string | null;
  t_wall_utc: string | null;
}

/**
 * WHAT THE NEWEST RUN'S OWN RECORDS SAY ABOUT THE BLOCK SO FAR (redesign r4 verify-r3 must_fix 2): the cube-layer answers of
 * the newest frame it reported, how many scan views it reported, and whether its `run.finished` has been folded. The
 * Scene's line beside an earlier run's block says what these say, and "has not reported what it sees yet" only while
 * they hold nothing and the run has not ended.
 */
export interface NewestLook {
  /** The cube-layer answers of the newest frame the newest run reported, in record order; empty before any. */
  frame: SceneAnswer[];
  /** How many scan views (`scene-view`) the newest run reported. */
  views: number;
  /** Whether the newest run's `run.finished` has been folded. */
  ended: boolean;
}

export const CUBE_LAYERS: readonly string[] = ['cube-recognizer', 'cube-in-pad-frame'];

export interface SceneMap {
  views: SceneView[];
  fit: SceneFit | null;
  wedge: SceneWedge | null;
  blocks: SceneBlock[];
  pulses: ScenePulse[];
  /** The return pulses: measured, but with no anchor on the pad to draw them from, so they are listed and not drawn. */
  returns: SceneReturnPulse[];
  placed: ScenePlaced | null;
  /** The centre-to-edge margins the placement predicates decided with, millimetres, widest first. */
  placement_margins_mm: number[];
  base_drawn: false;
  base_note: string;
  /** The newest run whose `run.started` the fold read, or null before any. */
  newest_run: string | null;
  /** What the newest run's own records say about the block so far. */
  newest_look: NewestLook;
  /** Where the fold has got to in the run it is reading. Internal to the fold; nothing draws it. */
  cursor: { run_kind: string | null; state: string | null; anchor_mm: Point | null; axis: Point | null };
}

export const BASE_NOT_DRAWN = "The robot's base is not drawn: no record carries where it stands.";

const NO_LOOK: NewestLook = { frame: [], views: 0, ended: false };

export function emptySceneMap(): SceneMap {
  return {
    views: [],
    fit: null,
    wedge: null,
    blocks: [],
    pulses: [],
    returns: [],
    placed: null,
    placement_margins_mm: [],
    base_drawn: false,
    base_note: BASE_NOT_DRAWN,
    newest_run: null,
    newest_look: NO_LOOK,
    cursor: { run_kind: null, state: null, anchor_mm: null, axis: null },
  };
}

/* ---- reading values ----------------------------------------------------------------------------------------------- */

const MERGE_RADIUS_MM = 30;
const PAD_HALF_X_MM = 207.5;
const PAD_HALF_Y_MM = 147.5;

/** The predicates whose threshold is the placement's required centre margin, the envelope drawn on the pad. */
export const PLACEMENT_ENVELOPE_PREDICATES: readonly string[] = ['placement_plan', 'arm_only_sufficient', 'release_margin', 'clearance_target_met'];

/** The reason codes that refuse a block at SEARCH, and the class each one gives it. */
const REFUSAL_CLASS: Record<string, BlockClass> = {
  mission_binding_candidate_not_placeable: 'NOT_PLACEABLE',
  search_placement_infeasible: 'NOT_PLACEABLE',
  search_cube_inside_pregrasp_reach: 'TOO_NEAR',
  search_cube_beyond_return_bound: 'OUTSIDE_PAN_BAND',
  search_cube_beyond_pregrasp_entry_bound: 'OUTSIDE_PAN_BAND',
  search_cube_not_in_pad_frame: 'NO_PAD_FRAME',
  search_no_pad_frame: 'NO_PAD_FRAME',
  mission_selected_cube_already_placed: 'ALREADY_PLACED',
};

function mm(metres: number): number {
  return Math.round(metres * 10000) / 10;
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function valueOf(list: readonly Detail[], name: string): Detail['value'] | undefined {
  return list.find((item) => item.name === name)?.value;
}

function numberOf(list: readonly Detail[], name: string): number | null {
  return num(valueOf(list, name));
}

function pointOf(value: unknown): Point | null {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const x = num(value[0]);
  const y = num(value[1]);
  return x === null || y === null ? null : [mm(x), mm(y)];
}

function axisOf(value: unknown): Point | null {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const x = num(value[0]);
  const y = num(value[1]);
  return x === null || y === null ? null : [x, y];
}

function distance(a: Point, b: Point): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

/** The fields of a `[STATE] <name> {...}` line, or null for any other text. */
function stateLine(text: string): { name: string; fields: Record<string, unknown> } | null {
  const match = /^\[STATE\] ([A-Z_]+) (\{.*\})$/.exec(text);
  if (match === null) return null;
  try {
    const fields = JSON.parse(match[2] as string) as unknown;
    return typeof fields === 'object' && fields !== null ? { name: match[1] as string, fields: fields as Record<string, unknown> } : null;
  } catch {
    return null;
  }
}

function formatMm(value: number): string {
  return `${String(Math.round(value * 100) / 100)} mm`;
}

/**
 * The binding constraint a not-placeable refusal names (its `constraint` value), spelled as the placement track's
 * envelope solver names it (plan section 5, track P Must 2). The scenarios emit these same constants.
 */
export const BINDING_CONSTRAINT = {
  side_edge: 'side_edge',
  along_budget: 'along_budget',
  arm_reach: 'arm_reach',
  pickup_inside_grasp_radius: 'pickup_inside_grasp_radius',
  lateral_needs_capability: 'lateral_needs_capability',
} as const;

/**
 * The sentence a block reads as: its class sentence, then the numbers the record gave for it, never a number the
 * record did not carry. A block nothing classified says where it came from instead.
 */
export function blockSentence(blockClass: string | null, source: SceneBlock['source'], details: readonly Detail[], inferred: boolean): string {
  if (blockClass === null) {
    return source === 'look' ? 'seen by Look, which classifies nothing' : 'seen at SEARCH; this run did not classify it';
  }
  const known = (BLOCK_CLASSES as Record<string, string>)[blockClass];
  let sentence = known ?? `class ${blockClass}, which this console has no sentence for`;
  const pan = numberOf(details, 'pan_from_home_deg');
  const band = numberOf(details, 'pan_band_deg');
  const advance = numberOf(details, 'base_advance_mm');
  const hint = valueOf(details, 'hint');
  if (blockClass === 'BASE_ADVANCE' && advance !== null) sentence += ` (${formatMm(advance)} planned)`;
  let hintSaid = false;
  if (blockClass === 'NOT_PLACEABLE') {
    const parts: string[] = [];
    // THE LIMIT, read as the failure banner reads it (`placementLimit`), with the hint when the record carries one.
    const limit = placementLimit(details);
    if (limit !== null) {
      parts.push(limit);
      hintSaid = typeof hint === 'string' && hint.length > 0;
    }
    if (pan !== null) parts.push(`about ${String(pan)} deg from the home pan${band !== null ? ` against the ${String(band)} deg band` : ''}${valueOf(details, 'pan_inferred') === true ? ' (inferred)' : ''}`);
    if (parts.length > 0) sentence += `: ${parts.join('; ')}`;
  }
  if (blockClass === 'TOO_NEAR') {
    const move = numberOf(details, 'distance_to_move_mm');
    if (move !== null) sentence += `: move it ${formatMm(move)} outward`;
  }
  if (blockClass === 'OUTSIDE_PAN_BAND' && pan !== null) sentence += ` (about ${String(pan)} deg from home${valueOf(details, 'pan_inferred') === true ? ', the pan angle inferred' : ''})`;
  if (!hintSaid && typeof hint === 'string' && hint.length > 0) sentence += `; ${hint}`;
  if (inferred) sentence += '; its position is inferred, carried from another view by the pan alone, and is never a placement anchor';
  return sentence;
}

/* ---- the fold ----------------------------------------------------------------------------------------------------- */

function wedgeOf(fit: SceneFit | null, views: readonly SceneView[], blocks: readonly SceneBlock[]): SceneWedge | null {
  if (fit === null || !fit.accepted || fit.band_deg === null || fit.home_bearing_deg === null) return null;
  const reach = [...views.flatMap((view) => view.footprint_mm ?? []), ...blocks.map((block) => block.point_mm)].map((point) => distance(point, fit.axis_mm));
  return {
    apex_mm: fit.axis_mm,
    from_deg: fit.home_bearing_deg - fit.band_deg,
    to_deg: fit.home_bearing_deg + fit.band_deg,
    radius_mm: Math.max(400, ...reach),
  };
}

function withWedge(map: SceneMap): SceneMap {
  return { ...map, wedge: wedgeOf(map.fit, map.views, map.blocks) };
}

/** A block the NEW run reports replaces the ones kept from an earlier run: they were kept only until it did. */
function withoutEarlier(map: SceneMap): SceneMap {
  return map.blocks.some((block) => block.earlier) ? { ...map, blocks: map.blocks.filter((block) => !block.earlier) } : map;
}

function sceneResult(map: SceneMap, event: ContractEvent, detector: string, observationIds: readonly string[], measurements: readonly Detail[]): SceneMap {
  if (detector === 'scene-view') {
    const index = numberOf(measurements, 'view_index');
    // A new scan begins with its first view: the previous scan's views, fit and blocks are replaced, never merged.
    const base = index === 0 ? { ...map, views: [], fit: null, blocks: map.blocks.filter((block) => block.source !== 'scan') } : map;
    const corners: Point[] = [];
    for (let corner = 0; corner < 8; corner += 1) {
      const x = numberOf(measurements, `footprint_${String(corner)}_x`);
      const y = numberOf(measurements, `footprint_${String(corner)}_y`);
      if (x === null || y === null) break;
      corners.push([mm(x), mm(y)]);
    }
    const view: SceneView = {
      observation_id: observationIds[0] ?? null,
      view: typeof valueOf(measurements, 'view') === 'string' ? (valueOf(measurements, 'view') as string) : null,
      pan_deg: numberOf(measurements, 'pan_deg'),
      footprint_mm: corners.length >= 3 ? corners : null,
      pad_frame_valid: typeof valueOf(measurements, 'pad_frame_valid') === 'boolean' ? (valueOf(measurements, 'pad_frame_valid') as boolean) : null,
      accepted: typeof valueOf(measurements, 'accepted') === 'boolean' ? (valueOf(measurements, 'accepted') as boolean) : null,
    };
    return withWedge({ ...base, views: [...base.views, view] });
  }
  if (detector === 'scene-pad') {
    const x = numberOf(measurements, 'axis_x');
    const y = numberOf(measurements, 'axis_y');
    if (x === null || y === null) return map;
    const fit: SceneFit = {
      axis_mm: [mm(x), mm(y)],
      home_bearing_deg: numberOf(measurements, 'home_bearing_deg'),
      band_deg: numberOf(measurements, 'pan_band_deg'),
      rotation_deg: numberOf(measurements, 'fit_rotation_deg'),
      residual_mm: numberOf(measurements, 'fit_residual_mm'),
      accepted: valueOf(measurements, 'fit_accepted') === true,
    };
    return withWedge({ ...map, fit });
  }
  if (detector === 'scene-block') {
    const x = numberOf(measurements, 'pad_x');
    const y = numberOf(measurements, 'pad_y');
    if (x === null || y === null) return map;
    const blockClass = typeof valueOf(measurements, 'class') === 'string' ? (valueOf(measurements, 'class') as string) : null;
    const inferred = valueOf(measurements, 'inferred') === true;
    const best = valueOf(measurements, 'best_observation');
    const block: SceneBlock = {
      point_mm: [mm(x), mm(y)],
      class: blockClass,
      sentence: blockSentence(blockClass, 'scan', measurements, inferred),
      source: 'scan',
      observation_id: typeof best === 'string' ? best : (observationIds[0] ?? null),
      views_seen: numberOf(measurements, 'views_seen'),
      spread_mm: numberOf(measurements, 'spread_mm'),
      inferred,
      selected: false,
      details: [...measurements],
      run_id: event.run_id ?? null,
      seen_wall_utc: event.t_wall_utc ?? null,
      earlier: false,
    };
    const current = withoutEarlier(map);
    return withWedge({ ...current, blocks: [...current.blocks, block] });
  }
  return map;
}

function seenBlock(map: SceneMap, event: ContractEvent, observationId: string | null, measurements: readonly Detail[]): SceneMap {
  const kind = map.cursor.run_kind;
  const searching = map.cursor.state === 'SEARCH' || map.cursor.state === 'SEARCH_SWEEP';
  const source: SceneBlock['source'] | null = kind === 'observe' ? 'look' : kind === 'cycle' && searching ? 'search' : null;
  if (source === null) return map;
  const x = numberOf(measurements, 'cube_center_pad_x');
  const y = numberOf(measurements, 'cube_center_pad_y');
  if (x === null || y === null) return map;
  const point: Point = [mm(x), mm(y)];
  // THIS RUN'S OWN SIGHTING: what an earlier run saw was kept only until now (verify-r2 must_fix 3).
  const current = withoutEarlier(map);
  // A block already on the map from the scan, or already seen in this run, is the same block: it is not drawn twice.
  if (current.blocks.some((block) => distance(block.point_mm, point) <= MERGE_RADIUS_MM)) return current;
  const block: SceneBlock = {
    point_mm: point,
    class: null,
    sentence: blockSentence(null, source, [], false),
    source,
    observation_id: observationId,
    views_seen: 1,
    spread_mm: null,
    inferred: false,
    selected: false,
    details: [],
    run_id: event.run_id ?? null,
    seen_wall_utc: event.t_wall_utc ?? null,
    earlier: false,
  };
  return withWedge({ ...current, blocks: [...current.blocks, block] });
}

function nearestIndex(blocks: readonly SceneBlock[], point: Point, sources: readonly SceneBlock['source'][]): number {
  let best = -1;
  let bestDistance = MERGE_RADIUS_MM;
  blocks.forEach((block, index) => {
    if (!sources.includes(block.source)) return;
    const d = distance(block.point_mm, point);
    if (d <= bestDistance) {
      best = index;
      bestDistance = d;
    }
  });
  return best;
}

function replaceBlock(map: SceneMap, index: number, patch: Partial<SceneBlock>): SceneMap {
  const blocks = map.blocks.map((block, position) => {
    if (position !== index) return block;
    const next = { ...block, ...patch };
    return { ...next, sentence: blockSentence(next.class, next.source, next.details, next.inferred) };
  });
  return { ...map, blocks };
}

function onStateLine(map: SceneMap, name: string, fields: Record<string, unknown>): SceneMap {
  if (name === 'TARGET_SELECTED') {
    const seen = pointOf(fields['seen_pad_m']);
    if (seen === null) return map;
    const index = nearestIndex(map.blocks, seen, ['scan', 'search']);
    if (index < 0) return map;
    const block = map.blocks[index] as SceneBlock;
    const mode = typeof fields['mode'] === 'string' ? fields['mode'] : null;
    return replaceBlock(map, index, { selected: true, class: block.class ?? mode });
  }
  if (name === 'PLACEMENT_PLAN') {
    const anchor = pointOf(fields['anchor_pad_m']);
    return anchor === null ? map : { ...map, cursor: { ...map.cursor, anchor_mm: anchor } };
  }
  if (name === 'BASE_ADVANCE') {
    const axis = axisOf(fields['axis_pad']);
    return axis === null ? map : { ...map, cursor: { ...map.cursor, axis } };
  }
  if (name === 'BASE_MEASURED') {
    const vx = num(fields['vx_mps']);
    const advance = num(fields['advance_mm']);
    const lateral = num(fields['lateral_mm']);
    if (vx === null || advance === null) return map;
    if (vx < 0) return { ...map, returns: [...map.returns, { index: map.returns.length, advance_mm: advance, lateral_mm: lateral }] };
    const from = map.cursor.anchor_mm;
    const axis = map.cursor.axis;
    if (from === null || axis === null) return map;
    const length = Math.hypot(axis[0], axis[1]) || 1;
    const to: Point = [Math.round((from[0] + (advance * axis[0]) / length) * 100) / 100, Math.round((from[1] + (advance * axis[1]) / length) * 100) / 100];
    const pulse: ScenePulse = { index: map.pulses.length, from_mm: from, to_mm: to, axis, advance_mm: advance, lateral_mm: lateral };
    return { ...map, pulses: [...map.pulses, pulse], cursor: { ...map.cursor, anchor_mm: to } };
  }
  if (name === 'PLACED') {
    const point = pointOf(fields['cube_pad_m']);
    if (point === null) return map;
    return { ...map, placed: { point_mm: point, centre_to_edge_mm: num(fields['centre_to_edge_mm']) } };
  }
  return map;
}

function onRunFinished(map: SceneMap, reason: string | null | undefined, measurements: readonly Detail[]): SceneMap {
  if (reason === null || reason === undefined) return map;
  const blockClass = REFUSAL_CLASS[reason];
  if (blockClass === undefined) return map;
  // The block this run refused is the one it selected, or else the one its SEARCH saw; never a block an earlier run saw.
  let index = map.blocks.findIndex((block) => block.selected && block.source === 'search' && !block.earlier);
  if (index < 0) index = map.blocks.map((block) => (block.earlier ? null : block.source)).lastIndexOf('search');
  if (index < 0) return map;
  return replaceBlock(map, index, { class: blockClass, details: [...measurements] });
}

/** Whether an event is the newest run's own: its run, or a record that names none. */
function newestRunOwn(map: SceneMap, event: ContractEvent): boolean {
  return map.newest_run === null || event.run_id === null || event.run_id === undefined || event.run_id === map.newest_run;
}

/** The newest run's answer of a cube layer, kept with the other answers of the same frame (a new frame starts afresh). */
function withAnswer(map: SceneMap, event: ContractEvent, detector: string, result: string, observationId: string | null): SceneMap {
  if (!newestRunOwn(map, event)) return map;
  const answer: SceneAnswer = { detector, result, observation_id: observationId, t_wall_utc: event.t_wall_utc ?? null };
  const frame = map.newest_look.frame;
  const sameFrame = frame.length > 0 && frame[frame.length - 1]?.observation_id === observationId;
  return { ...map, newest_look: { ...map.newest_look, frame: sameFrame ? [...frame, answer] : [answer] } };
}

/** One event into the map. Pure: an event the map does not read returns the same map object. */
export function applySceneEvent(map: SceneMap, event: ContractEvent): SceneMap {
  switch (event.kind) {
    case 'run.started':
      // A new run keeps the scan (the cycle that follows a scan picks from it) and forgets the last run's own marks. The
      // blocks an earlier run saw stay, as that run's, until this run reports a sighting of its own (verify-r2 must_fix
      // 3): a new run that has reported nothing has not seen that nothing is there.
      return {
        ...map,
        blocks: map.blocks.map((block) => (block.source === 'scan' ? { ...block, selected: false } : { ...block, selected: false, earlier: true })),
        newest_run: event.run_id ?? null,
        newest_look: NO_LOOK,
        pulses: [],
        returns: [],
        placed: null,
        placement_margins_mm: [],
        cursor: { run_kind: null, state: null, anchor_mm: null, axis: null },
      };
    case 'run.config': {
      const kind = event.payload.summary.find((item) => item.name === 'action_kind')?.value;
      return { ...map, cursor: { ...map.cursor, run_kind: typeof kind === 'string' ? kind : null } };
    }
    case 'state.transition':
      return { ...map, cursor: { ...map.cursor, state: event.payload.to_state } };
    case 'diagnostic.text': {
      const line = stateLine(event.payload.text);
      return line === null ? map : onStateLine(map, line.name, line.fields);
    }
    case 'perception.result': {
      const { detector, observation_ids: ids, measurements, result } = event.payload;
      if (detector.startsWith('scene-')) {
        const looked = detector === 'scene-view' && newestRunOwn(map, event) ? { ...map, newest_look: { ...map.newest_look, views: map.newest_look.views + 1 } } : map;
        return sceneResult(looked, event, detector, ids, measurements);
      }
      if (!CUBE_LAYERS.includes(detector)) return map;
      // WHAT THE NEWEST RUN ANSWERED, whatever it answered (verify-r3 must_fix 2): a not_detected or an error is an answer.
      const answered = withAnswer(map, event, detector, result, event.observation_id ?? ids[0] ?? null);
      if (detector === 'cube-in-pad-frame' && result === 'detected') return seenBlock(answered, event, event.observation_id ?? ids[0] ?? null, measurements);
      return answered;
    }
    case 'predicate.evaluated': {
      const { predicate, threshold, unit } = event.payload;
      if (!PLACEMENT_ENVELOPE_PREDICATES.includes(predicate) || threshold === null || unit !== 'mm') return map;
      const margin = Math.round(threshold * 100) / 100;
      if (margin <= 0 || margin >= PAD_HALF_Y_MM || map.placement_margins_mm.includes(margin)) return map;
      return { ...map, placement_margins_mm: [...map.placement_margins_mm, margin].sort((a, b) => b - a) };
    }
    case 'run.finished': {
      const ended = newestRunOwn(map, event) ? { ...map, newest_look: { ...map.newest_look, ended: true } } : map;
      return onRunFinished(ended, event.payload.reason_code, event.payload.failure?.measurements ?? []);
    }
    default:
      return map;
  }
}

export function foldSceneMap(events: readonly ContractEvent[]): SceneMap {
  return events.reduce(applySceneEvent, emptySceneMap());
}

/**
 * THE PLACEMENT IN WORDS, from the run's PLACED line: the one sentence the Scene and the outcome card both print, so the
 * two cannot differ (spec 4.4).
 */
export function placementLine(placed: ScenePlaced): string {
  const [x, y] = placed.point_mm;
  const edge = placed.centre_to_edge_mm === null ? 'not recorded' : formatMm(placed.centre_to_edge_mm);
  return `Placed at x ${x.toFixed(1)} mm, y ${y.toFixed(1)} mm, its centre ${edge} from the nearest edge.`;
}

/** The rectangle the map is drawn in, millimetres: the pad and every drawn point, with a margin around them. */
export function sceneExtentMm(map: SceneMap): { min_x: number; max_x: number; min_y: number; max_y: number } {
  const points: Point[] = [
    [-PAD_HALF_X_MM, -PAD_HALF_Y_MM],
    [PAD_HALF_X_MM, PAD_HALF_Y_MM],
    ...map.views.flatMap((view) => view.footprint_mm ?? []),
    ...map.blocks.map((block) => block.point_mm),
    ...map.pulses.flatMap((pulse) => [pulse.from_mm, pulse.to_mm]),
    ...(map.fit === null ? [] : [map.fit.axis_mm]),
    ...(map.placed === null ? [] : [map.placed.point_mm]),
  ];
  const margin = 40;
  return {
    min_x: Math.min(...points.map((point) => point[0])) - margin,
    max_x: Math.max(...points.map((point) => point[0])) + margin,
    min_y: Math.min(...points.map((point) => point[1])) - margin,
    max_y: Math.max(...points.map((point) => point[1])) + margin,
  };
}
