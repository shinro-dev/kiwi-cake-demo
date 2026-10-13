// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SCENE MAP: the pad and the ground around it seen from above, in PAD_FRAME millimetres, drawn from the fold in
// `model/sceneMap.ts` and from nothing else. One millimetre is one unit of the drawing on both axes, y up.
//
// It draws the configured pad, the placement envelope at the margin the record decided with, the ground each scan view
// saw, the pan axis with the home bearing, the pan band's wedge only when the scan's fit was accepted, every block
// coloured by its class with its class sentence beside it, the measured pulses of the held block, and the placed
// block. The robot's base is not drawn, because no record carries where it stands, and the map says so in words.
// A position inferred from another view is drawn dashed and says so; the return pulses are listed, not drawn, because
// nothing on the pad anchors them.
//
// The component takes the folded map as a prop and holds no state: whoever mounts it folds the events it has.

import type { ReactNode } from 'react';
import { BASE_NOT_DRAWN, baseCapabilityWords, CUBE_LAYERS, placementLine, sceneExtentMm } from '../model/sceneMap';
import type { LiveSlot } from '../model/liveSlot';
import { utcStamp } from '../model/utcTime';
import type { NewestLook, Point, SceneAnswer, SceneBlock, SceneMap as SceneMapModel } from '../model/sceneMap';
import { Badge } from '../ui/Badge';
import { Panel } from '../ui/Panel';

const PAD_HALF_X_MM = 207.5;
const PAD_HALF_Y_MM = 147.5;
/** The cube's side, from the controller's PLACED rule: its half-diagonal is 17.96 mm. */
const CUBE_SIDE_MM = 25.4;

const CLASS_COLOUR: Record<string, string> = {
  ALREADY_PLACED: 'var(--provenance-measured)',
  ARM_ONLY: 'var(--success)',
  BASE_ADVANCE: 'var(--primary)',
  NOT_PLACEABLE: 'var(--destructive)',
  TOO_NEAR: 'var(--warning)',
  OUTSIDE_PAN_BAND: 'var(--warning)',
  SEEN_NOT_MEASURABLE: 'var(--muted-foreground)',
  NO_PAD_FRAME: 'var(--muted-foreground)',
};

function colourOf(blockClass: string | null): string {
  return blockClass === null ? 'var(--muted-foreground)' : (CLASS_COLOUR[blockClass] ?? 'var(--muted-foreground)');
}

/** A point of PAD_FRAME in the drawing: x as it is, y negated so that the pad's top edge is at the top. */
function at(point: Point): { x: number; y: number } {
  return { x: point[0], y: -point[1] };
}

function bearingPoint(apex: Point, bearingDeg: number, radius: number): Point {
  const radians = (bearingDeg * Math.PI) / 180;
  return [apex[0] + radius * Math.sin(radians), apex[1] + radius * Math.cos(radians)];
}

function mmText(value: number | null): string {
  return value === null ? 'not recorded' : `${String(Math.round(value * 100) / 100)} mm`;
}

/** A number of degrees to a tenth, with no trailing zero. */
function tenth(value: number): string {
  return String(Math.round(value * 10) / 10);
}

function pointText(point: Point): string {
  return `x ${point[0].toFixed(1)} mm, y ${point[1].toFixed(1)} mm`;
}

/**
 * WHOSE SIGHTING A BLOCK IS, for a block an earlier run saw (verify-r2 must_fix 3): "seen by run <id> at HH:MM:SSZ", from
 * that run's own record. Null for a block of the run the map is on now.
 */
export function earlierWords(block: SceneBlock, day: string | null): string | null {
  if (!block.earlier) return null;
  const when = utcStamp(block.seen_wall_utc, day);
  return `seen by run ${block.run_id ?? '(not named)'}${when === null ? '' : ` at ${when.text}`}`;
}

/** The cube layers' answers of one frame, in the record's own tokens: "cube-recognizer: not_detected, cube-in-pad-frame: error". */
function answerTokens(frame: readonly SceneAnswer[]): string {
  return CUBE_LAYERS.flatMap((detector) => {
    const answer = [...frame].reverse().find((item) => item.detector === detector);
    return answer === undefined ? [] : [`${detector}: ${answer.result}`];
  }).join(', ');
}

/**
 * WHAT THE NEWEST RUN'S OWN RECORDS SAY ABOUT THE BLOCK, as one clause (redesign r4 verify-r3 must_fix 2), for the newest
 * frame it reported: the recognizer's not_detected is a look that saw no block; a cube-in-pad-frame answer other than
 * detected is a block that could not be located in the pad frame; a detected one the map does not draw was not at a
 * SEARCH or a Look; a recognizer's detected with no pad-frame answer yet has not said where the block is. The layers'
 * tokens follow in brackets, as the record gives them. `ended` drops every "yet".
 */
function answerClause(runId: string, look: NewestLook, day: string | null): string {
  const frame = look.frame;
  if (frame.length === 0) {
    const views = `${String(look.views)} scan view${look.views === 1 ? '' : 's'}`;
    return look.ended ? `Run ${runId} reported ${views} and no block` : `Run ${runId} has reported ${views} and no block yet`;
  }
  const newest = frame[frame.length - 1] as SceneAnswer;
  const stamp = utcStamp(newest.t_wall_utc, day);
  const at = stamp === null ? '' : ` at ${stamp.text}`;
  const recognizer = [...frame].reverse().find((item) => item.detector === 'cube-recognizer')?.result ?? null;
  const inPadFrame = [...frame].reverse().find((item) => item.detector === 'cube-in-pad-frame')?.result ?? null;
  const tokens = `(${answerTokens(frame)})`;
  if (recognizer === 'not_detected') return `Run ${runId} looked${at} and did not see a block ${tokens}`;
  if (inPadFrame !== null && inPadFrame !== 'detected') return `Run ${runId} could not locate a block in the pad frame${at} ${tokens}`;
  if (inPadFrame === 'detected') return `Run ${runId} located a block in the pad frame${at} outside a SEARCH or a Look, which this map does not draw ${tokens}`;
  if (recognizer === 'detected') return look.ended ? `Run ${runId} saw a block${at} and did not report where it is ${tokens}` : `Run ${runId} saw a block${at} and has not reported where it is yet ${tokens}`;
  return `Run ${runId} looked${at} and reported no block ${tokens}`;
}

/**
 * WHAT THE MAP SAYS WHILE THE NEWEST RUN HAS NO SIGHTING OF ITS OWN ON IT and an earlier run's blocks are drawn: what that
 * run's own records say (verify-r3 must_fix 2). "Has not reported what it sees yet" only while it has given no cube-layer
 * answer and no scan view and has not ended; "ended without reporting a sighting" when it ended with none; otherwise the
 * clause of its newest frame (`answerClause`), and "the run has ended" once its run.finished is folded. Null otherwise.
 */
export function newestRunWaitingWords(map: SceneMapModel, day: string | null = null): string | null {
  const earlier = map.blocks.filter((block) => block.earlier);
  if (map.newest_run === null || earlier.length === 0 || map.blocks.some((block) => !block.earlier && block.source !== 'scan')) return null;
  const runs = [...new Set(earlier.map((block) => block.run_id ?? '(not named)'))];
  const drawn = `the block${earlier.length === 1 ? '' : 's'} drawn ${earlier.length === 1 ? 'is' : 'are'} what run ${runs.join(' and run ')} saw`;
  const look = map.newest_look;
  const answered = look.frame.length > 0 || look.views > 0;
  if (!answered) return look.ended ? `Run ${map.newest_run} ended without reporting a sighting; ${drawn}.` : `Run ${map.newest_run} has not reported what it sees yet; ${drawn}.`;
  const clause = answerClause(map.newest_run, look, day);
  return look.ended ? `${clause}, and the run has ended; ${drawn}.` : `${clause}; ${drawn}.`;
}

function BlockMark(props: { block: SceneBlock }): ReactNode {
  const { block } = props;
  const centre = at(block.point_mm);
  const colour = colourOf(typeof block.class === 'string' ? block.class : null);
  const half = CUBE_SIDE_MM / 2;
  return (
    <g data-block={block.class ?? 'unclassified'} data-inferred={block.inferred ? 'true' : 'false'} data-earlier-run={block.earlier ? (block.run_id ?? '') : undefined}>
      <title>{`${block.class ?? 'not classified'}: ${block.sentence}`}</title>
      {block.selected ? <circle cx={centre.x} cy={centre.y} r={CUBE_SIDE_MM} fill="none" stroke={colour} strokeWidth="2" /> : null}
      <rect
        x={centre.x - half}
        y={centre.y - half}
        width={CUBE_SIDE_MM}
        height={CUBE_SIDE_MM}
        fill={colour}
        fillOpacity={block.inferred || block.earlier ? 0.12 : 0.35}
        stroke={colour}
        strokeWidth="2"
        strokeDasharray={block.inferred ? '5 4' : undefined}
      />
      <text x={centre.x + half + 6} y={centre.y + 4} fill={colour} fontSize="13" fontFamily="var(--font-mono)">
        {block.class ?? 'seen'}
      </text>
    </g>
  );
}

export function SceneMap(props: { map: SceneMapModel; title?: string; slot?: LiveSlot | null; drawnFrom?: string | null; day?: string | null }): ReactNode {
  const { map } = props;
  const day = props.day ?? null;
  const waiting = newestRunWaitingWords(map, day);
  const extent = sceneExtentMm(map);
  const width = extent.max_x - extent.min_x;
  const height = extent.max_y - extent.min_y;
  const empty = map.blocks.length === 0 && map.views.length === 0 && map.placed === null;
  const wedge = map.wedge;
  const fit = map.fit;

  return (
    <Panel
      title={props.title ?? 'scene map'}
      state={empty ? 'waiting' : 'active'}
      stateWords={empty ? 'Nothing seen yet' : `${String(map.blocks.length)} block(s) on the map`}
      extra={
        <Badge tone="quiet" code title="PAD_FRAME, millimetres, seen from above">
          pad 415 x 295 mm
        </Badge>
      }
      note={
        <>
          {BASE_NOT_DRAWN} The pad outline is the configured pad, not a measurement. Blocks drawn dashed are inferred: their position was carried
          from another view by the pan alone and is never a placement anchor.
        </>
      }
    >
      <svg
        className="scene"
        viewBox={`${extent.min_x.toFixed(1)} ${(-extent.max_y).toFixed(1)} ${width.toFixed(1)} ${height.toFixed(1)}`}
        role="img"
        aria-label="the pad and the ground around it seen from above, in pad-frame millimetres"
        style={{ maxHeight: 'min(28rem, 60vh)', width: '100%' }}
      >
        {/* No <title> here: the note above says the base is not drawn, once (one place per fact); aria-label names the drawing. */}
        <g data-layer="pad">
          <rect x={-PAD_HALF_X_MM} y={-PAD_HALF_Y_MM} width={PAD_HALF_X_MM * 2} height={PAD_HALF_Y_MM * 2} fill="var(--muted)" stroke="var(--border)" strokeWidth="2" rx="4" />
        </g>

        {map.placement_margins_mm.length === 0 ? null : (
          <g data-layer="placement-envelope">
            {map.placement_margins_mm.map((margin) => (
              <rect
                key={`envelope-${String(margin)}`}
                x={-PAD_HALF_X_MM + margin}
                y={-PAD_HALF_Y_MM + margin}
                width={(PAD_HALF_X_MM - margin) * 2}
                height={(PAD_HALF_Y_MM - margin) * 2}
                fill="none"
                stroke="var(--provenance-planned)"
                strokeWidth="1.5"
                strokeDasharray="7 5"
              >
                <title>{`placement envelope: a centre at least ${mmText(margin)} from every edge, the margin the record decided with`}</title>
              </rect>
            ))}
          </g>
        )}

        {map.views.length === 0 ? null : (
          <g data-layer="views">
            {map.views.map((view, index) =>
              view.footprint_mm === null ? null : (
                <polygon
                  key={`view-${String(index)}`}
                  data-view={view.view ?? String(index)}
                  points={view.footprint_mm.map((point) => `${String(at(point).x)},${String(at(point).y)}`).join(' ')}
                  fill="var(--provenance-measured)"
                  fillOpacity="0.06"
                  stroke="var(--provenance-measured)"
                  strokeOpacity="0.5"
                  strokeWidth="1"
                >
                  <title>{`view ${view.view ?? String(index)} at pan ${view.pan_deg === null ? 'not recorded' : `${String(view.pan_deg)} deg`}`}</title>
                </polygon>
              ),
            )}
          </g>
        )}

        {wedge === null ? null : (
          <g data-layer="wedge">
            {(() => {
              const apex = at(wedge.apex_mm);
              const from = at(bearingPoint(wedge.apex_mm, wedge.from_deg, wedge.radius_mm));
              const to = at(bearingPoint(wedge.apex_mm, wedge.to_deg, wedge.radius_mm));
              return (
                <path
                  d={`M${String(apex.x)} ${String(apex.y)} L${String(from.x)} ${String(from.y)} A${String(wedge.radius_mm)} ${String(wedge.radius_mm)} 0 0 1 ${String(to.x)} ${String(to.y)} Z`}
                  fill="var(--provenance-planned)"
                  fillOpacity="0.05"
                  stroke="var(--provenance-planned)"
                  strokeDasharray="4 4"
                >
                  <title>{`the pan band, +/-${tenth((wedge.to_deg - wedge.from_deg) / 2)} deg around the home bearing (${tenth((wedge.to_deg + wedge.from_deg) / 2)} deg), drawn from an accepted fit`}</title>
                </path>
              );
            })()}
          </g>
        )}

        {fit === null ? null : (
          <g data-layer="axis" stroke="var(--foreground)" strokeWidth="2">
            <title>{`the pan axis at ${pointText(fit.axis_mm)}`}</title>
            <path d={`M${String(at(fit.axis_mm).x - 10)} ${String(at(fit.axis_mm).y)} h20 M${String(at(fit.axis_mm).x)} ${String(at(fit.axis_mm).y - 10)} v20`} />
            {fit.home_bearing_deg === null ? null : (
              <path
                d={`M${String(at(fit.axis_mm).x)} ${String(at(fit.axis_mm).y)} L${String(at(bearingPoint(fit.axis_mm, fit.home_bearing_deg, 160)).x)} ${String(at(bearingPoint(fit.axis_mm, fit.home_bearing_deg, 160)).y)}`}
                strokeDasharray="6 4"
              />
            )}
            <text x={at(fit.axis_mm).x + 14} y={at(fit.axis_mm).y + 4} fill="var(--foreground)" stroke="none" fontSize="13" fontFamily="var(--font-mono)">
              pan axis
            </text>
          </g>
        )}

        {map.pulses.length === 0 ? null : (
          <g data-layer="pulses" stroke="var(--provenance-measured)" strokeWidth="2.5">
            {map.pulses.map((pulse) => (
              <g key={`pulse-${String(pulse.index)}`} data-pulse={String(pulse.index)}>
                <title>{`pulse ${String(pulse.index + 1)}: ${mmText(pulse.advance_mm)} measured along its axis, ${mmText(pulse.lateral_mm)} lateral (stated, not drawn)`}</title>
                <line x1={at(pulse.from_mm).x} y1={at(pulse.from_mm).y} x2={at(pulse.to_mm).x} y2={at(pulse.to_mm).y} />
                <circle cx={at(pulse.to_mm).x} cy={at(pulse.to_mm).y} r="3.5" fill="var(--provenance-measured)" />
              </g>
            ))}
          </g>
        )}

        {map.blocks.length === 0 ? null : (
          <g data-layer="blocks">
            {map.blocks.map((block, index) => (
              <BlockMark key={`block-${String(index)}`} block={block} />
            ))}
          </g>
        )}

        {map.placed === null ? null : (
          <g data-layer="placed">
            <title>{`placed, centre ${mmText(map.placed.centre_to_edge_mm)} from the nearest edge`}</title>
            <rect
              x={at(map.placed.point_mm).x - CUBE_SIDE_MM / 2}
              y={at(map.placed.point_mm).y - CUBE_SIDE_MM / 2}
              width={CUBE_SIDE_MM}
              height={CUBE_SIDE_MM}
              fill="var(--success)"
              fillOpacity="0.6"
              stroke="var(--success)"
              strokeWidth="2"
            />
          </g>
        )}

        {empty ? (
          <text x={0} y={0} textAnchor="middle" fill="var(--muted-foreground)" fontSize="16" fontFamily="var(--font-mono)">
            Nothing on this map yet
          </text>
        ) : null}
      </svg>

      <ul className="scene-map__legend" style={{ listStyle: 'none', margin: '0.5rem 0 0', padding: 0, display: 'grid', gap: '0.35rem' }}>
        {props.drawnFrom === undefined || props.drawnFrom === null ? null : <li data-scene-records="">{props.drawnFrom}</li>}
        {waiting === null ? null : <li data-scene-waiting="">{waiting}</li>}
        {map.blocks.map((block, index) => (
          <li key={`legend-${String(index)}`} style={{ display: 'flex', gap: '0.5rem', alignItems: 'baseline', flexWrap: 'wrap' }}>
            <span aria-hidden="true" style={{ display: 'inline-block', width: '0.75rem', height: '0.75rem', background: colourOf(typeof block.class === 'string' ? block.class : null), borderRadius: '2px' }} />
            <Badge tone="quiet" code>
              {block.class ?? 'not classified'}
            </Badge>
            <span>{block.class === 'BASE_ADVANCE' ? `${block.sentence}: ${baseCapabilityWords(props.slot)}` : block.sentence}</span>
            <span style={{ color: 'var(--muted-foreground)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-xs)' }}>
              {pointText(block.point_mm)}
              {block.selected ? ', selected' : ''}
              {block.earlier ? `, ${earlierWords(block, day) ?? ''}` : ''}
              {block.views_seen === null ? '' : `, seen in ${String(block.views_seen)} view(s)`}
            </span>
          </li>
        ))}
        {fit === null ? null : (
          <li>
            The pan-axis fit: rotation {fit.rotation_deg === null ? 'not recorded' : `${String(fit.rotation_deg)} deg`}, residual {mmText(fit.residual_mm)},{' '}
            {fit.accepted ? 'fit accepted, so the pan band is drawn' : 'fit not accepted, so no pan band is drawn'}.
          </li>
        )}
        {map.pulses.length === 0 ? null : (
          <li>
            Measured base pulses of the held block: {map.pulses.map((pulse) => mmText(pulse.advance_mm)).join(', ')} along their axes.
          </li>
        )}
        {map.returns.length === 0 ? null : (
          <li>
            The return pulses measured {map.returns.map((pulse) => mmText(pulse.advance_mm)).join(', ')}; they are not drawn, because nothing on the pad
            anchors them.
          </li>
        )}
        {map.placed === null ? null : (
          <li data-scene-placement="">{placementLine(map.placed)}</li>
        )}
        {map.placement_margins_mm.length === 0 ? null : (
          <li>The placement envelope is drawn at {map.placement_margins_mm.map((margin) => mmText(margin)).join(' and ')}, the margin the record decided with.</li>
        )}
      </ul>
    </Panel>
  );
}
