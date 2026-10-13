// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The pad seen from above, in millimetres, in PAD_FRAME: origin at the pad centre, y toward the pad's top edge, pad
// 415 x 295 mm. This view is not a camera and is not a rendering of one. Every mark on it carries its provenance, and
// the four kinds are never allowed to look alike:
//
//   measured     a value read off an observation
//   inferred     a value derived from measured ones, including every release admission estimate
//   planned      where the controller intends to go
//   historical   something established earlier in this run and NOT re-verified since
//   unavailable  the runtime does not serve this, so nothing is drawn
//
// The release admission is an ESTIMATE with empirical bounds. It is not a sighting of where the cube landed, and this
// panel says so wherever it appears.
//
// The map is a COMPACT INSET by default and opens to the full drawing on request. It used to be a 760 x 560 drawing
// across two columns of the screen, which is more room than the four marks on it can fill; the box and the grid are
// now derived from the configured pad, so the compact and the open drawing are the same map at two scales and neither
// reserves a strip of scene for nothing.
//
// EVERY RULE DRAWN HERE IS READ OFF THE RECORD. The admissible centre inset is the threshold the controller decided
// with, taken from the predicate that carried it; this console holds no copy of it. Where a run recorded no such
// threshold, no inset is drawn, and where a run resolved no pad frame at all the absence is drawn as an absence.

import type { ReactNode } from 'react';
import { useState } from 'react';
import { number, UNAVAILABLE } from '../model/format';
import { admissibleCentreRules, admissionMarkFor, padFrameReading } from '../model/padScene';
import type { CentreRule, PadMark } from '../model/padScene';
import type { Provenanced, ViewState } from '../model/types';
import {
  centreAdmissibleHalfExtentsMm,
  PAD_HALF_X_MM,
  PAD_HALF_Y_MM,
  PAD_LONG_MM,
  PAD_SHORT_MM,
  padGridLinesMm,
  padToScene,
  sceneBoxOfWidth,
  sceneScale,
} from '../geometry/pad';
import type { SceneBox } from '../geometry/pad';
import type { Point2 } from '../geometry/homography';
import { Badge } from '../ui/Badge';
import { Panel } from '../ui/Panel';

/**
 * The two boxes, both derived from the pad's own proportions so that a millimetre is the same distance on both axes in
 * either of them. Only the width is chosen: the compact one is a card-width inset, the open one is a drawing.
 */
const COMPACT: SceneBox = sceneBoxOfWidth(420, 18);
const OPEN: SceneBox = sceneBoxOfWidth(760, 34);

const GRID_STEP_MM = 50;

const PROVENANCE_COLOUR: Record<Provenanced, string> = {
  measured: 'var(--provenance-measured)',
  inferred: 'var(--provenance-inferred)',
  planned: 'var(--provenance-planned)',
  historical: 'var(--provenance-historical)',
  unavailable: 'var(--provenance-unavailable)',
};

/**
 * One mark. In the compact map the label is the hover title only: the shapes carry the provenance, and the numbers are
 * read in the table under the open map rather than squeezed over a drawing the width of a card.
 */
function Mark(props: {
  point: Point2;
  box: SceneBox;
  provenance: Provenanced;
  label: string;
  shape: 'anchor' | 'placed' | 'estimate';
  open: boolean;
  radiusMm?: number;
}): ReactNode {
  const at = padToScene(props.point, props.box);
  const colour = PROVENANCE_COLOUR[props.provenance];
  const scale = sceneScale(props.box);
  return (
    <g>
      <title>{props.label}</title>
      {props.shape === 'estimate' && props.radiusMm !== undefined ? (
        <circle cx={at.x} cy={at.y} r={props.radiusMm * scale} fill={colour} fillOpacity="0.1" stroke={colour} strokeWidth="1.5" strokeDasharray="5 4" />
      ) : null}
      {props.shape === 'anchor' ? (
        <g stroke={colour} strokeWidth="2" fill="none">
          <path d={`M${String(at.x - 10)} ${String(at.y)} h20 M${String(at.x)} ${String(at.y - 10)} v20`} />
          <circle cx={at.x} cy={at.y} r="7" strokeDasharray="4 3" />
        </g>
      ) : null}
      {props.shape === 'placed' ? <rect x={at.x - 8} y={at.y - 8} width="16" height="16" fill={colour} fillOpacity="0.25" stroke={colour} strokeWidth="2" /> : null}
      {props.open ? (
        <text x={at.x + 13} y={at.y - 10} fill={colour} fontSize="12" fontFamily="var(--font-mono)">
          {props.label}
        </text>
      ) : null}
    </g>
  );
}

function LegendItem(props: { provenance: Provenanced; children: ReactNode }): ReactNode {
  return (
    <span className="legend__item">
      <span className="legend__swatch" style={{ background: PROVENANCE_COLOUR[props.provenance] }} />
      {props.children}
    </span>
  );
}

/** The rectangle a cube centre has to stay inside for one recorded rule, drawn at the threshold the record carried. */
function CentreInset(props: { rule: CentreRule; box: SceneBox; open: boolean }): ReactNode {
  const extents = centreAdmissibleHalfExtentsMm(props.rule.margin_mm);
  if (extents === null) return null;
  const topLeft = padToScene([-extents.half_x_mm, extents.half_y_mm], props.box);
  const bottomRight = padToScene([extents.half_x_mm, -extents.half_y_mm], props.box);
  return (
    <g>
      <rect
        x={topLeft.x}
        y={topLeft.y}
        width={bottomRight.x - topLeft.x}
        height={bottomRight.y - topLeft.y}
        fill="none"
        stroke={PROVENANCE_COLOUR.planned}
        strokeWidth="1.5"
        strokeDasharray="7 5"
        opacity="0.9"
      />
      {props.open ? (
        <text x={topLeft.x + 6} y={topLeft.y - 6} fill={PROVENANCE_COLOUR.planned} fontSize="12" fontFamily="var(--font-mono)">
          centre admissible for {props.rule.predicates.join(', ')} at {number(props.rule.margin_mm, 1, 'mm')}
        </text>
      ) : null}
    </g>
  );
}

export function PadScenePanel(props: { view: ViewState; marks: PadMark[]; open?: boolean }): ReactNode {
  const { view, marks } = props;
  const [opened, setOpened] = useState(props.open ?? false);
  const box = opened ? OPEN : COMPACT;

  const topLeft = padToScene([-PAD_HALF_X_MM, PAD_HALF_Y_MM], box);
  const bottomRight = padToScene([PAD_HALF_X_MM, -PAD_HALF_Y_MM], box);
  const origin = padToScene([0, 0], box);
  const scale = sceneScale(box);
  const grid = padGridLinesMm(GRID_STEP_MM);

  const frame = padFrameReading(view, marks);
  const rules = admissibleCentreRules(view.predicates);

  const anchor = marks[0] ?? null;
  const placedMark = [...marks].reverse().find((mark) => mark.state === 'PLACED' || mark.state === 'RELEASED') ?? null;

  const admission = view.predicates.find((predicate) => predicate.predicate === 'release_admitted') ?? null;
  const baseReturn = view.predicates.find((predicate) => predicate.predicate === 'base_returned') ?? null;
  const travelling = view.machine !== null && ['BASE_ADVANCE', 'BASE_RETURN', 'CARRY'].includes(view.machine.state);

  // The admission estimate is drawn at the position THE PREDICATE CONSUMED, found through its own evidence. The newest
  // mark on the scene is not that position: it can come from a later observation, and drawn there the estimate would
  // move to evidence it was never computed from.
  const admissionMark = admissionMarkFor(admission, marks);
  const admissionDrawable = admission !== null && admissionMark !== null && admission.measured !== null;

  // Base displacement is only shown when a predicate MEASURED it. A pulse duration is not a displacement.
  const baseResidualMm = baseReturn?.measured ?? null;

  return (
    <Panel
      title="pad-relative scene"
      state={frame.state === 'resolved' ? 'active' : frame.state === 'absent' ? 'blocked' : 'waiting'}
      stateWords={frame.state === 'resolved' ? 'Placed from this run' : frame.state === 'absent' ? 'No pad frame resolved' : 'Nothing placed yet'}
      extra={
        <>
          <Badge tone="quiet" code title="the configured pad, not a measurement of one">
            {String(PAD_LONG_MM)} x {String(PAD_SHORT_MM)} mm
          </Badge>
          <button type="button" className="button" onClick={() => setOpened(!opened)} aria-expanded={opened}>
            {opened ? 'show the compact map' : 'open the map'}
          </button>
        </>
      }
      note={
        <>
          {frame.sentence}
          {frame.reason_code === null ? '' : ` The runtime's reason: ${frame.reason_code}.`} The release admission is an estimate computed from
          configured empirical bounds, not a sighting of the landing point. Where a mark is missing the runtime served nothing for it, and nothing is
          drawn in its place.
        </>
      }
    >
      <svg
        className="scene"
        viewBox={`0 0 ${box.width.toFixed(1)} ${box.height.toFixed(1)}`}
        // The compact map is an inset beside the cards it belongs with; opening it gives it the height a drawing
        // needs. The drawing is a viewBox either way, so both are the same map and every mark keeps its place.
        style={opened ? undefined : { maxHeight: 'min(13rem, 24vh)' }}
        role="img"
        aria-label="the pad seen from above, in pad-frame millimetres"
      >
        <rect
          x={topLeft.x}
          y={topLeft.y}
          width={bottomRight.x - topLeft.x}
          height={bottomRight.y - topLeft.y}
          fill="var(--muted)"
          stroke="var(--border)"
          strokeWidth="2"
          rx="4"
          // Nothing measured is on this map, so the outline is drawn as the configured shape it is and not as a survey.
          strokeDasharray={frame.state === 'resolved' ? undefined : '8 6'}
          opacity={frame.state === 'resolved' ? 1 : 0.7}
        />
        {grid.y.map((y) => {
          const line = padToScene([0, y], box);
          return <path key={`y${String(y)}`} d={`M${String(topLeft.x)} ${String(line.y)} H${String(bottomRight.x)}`} stroke="var(--border)" strokeWidth="1" opacity="0.6" />;
        })}
        {grid.x.map((x) => {
          const line = padToScene([x, 0], box);
          return <path key={`x${String(x)}`} d={`M${String(line.x)} ${String(topLeft.y)} V${String(bottomRight.y)}`} stroke="var(--border)" strokeWidth="1" opacity="0.6" />;
        })}
        {rules.map((rule) => (
          <CentreInset key={`inset-${String(rule.margin_mm)}`} rule={rule} box={box} open={opened} />
        ))}
        <g stroke="var(--muted-foreground)" strokeWidth="1.5">
          <path d={`M${String(origin.x)} ${String(origin.y)} h${String(GRID_STEP_MM * scale)}`} />
          <path d={`M${String(origin.x)} ${String(origin.y)} v${String(-GRID_STEP_MM * scale)}`} />
        </g>
        {opened ? (
          <>
            <text x={origin.x + GRID_STEP_MM * scale + 5} y={origin.y + 4} fill="var(--muted-foreground)" fontSize="12" fontFamily="var(--font-mono)">
              x {String(GRID_STEP_MM)} mm
            </text>
            <text x={origin.x + 4} y={origin.y - GRID_STEP_MM * scale - 5} fill="var(--muted-foreground)" fontSize="12" fontFamily="var(--font-mono)">
              y toward the top edge
            </text>
          </>
        ) : null}

        {anchor === null ? null : (
          <Mark
            point={anchor.point_mm}
            box={box}
            open={opened}
            provenance="measured"
            label={`search anchor (${anchor.state ?? 'state unknown'})`}
            shape="anchor"
          />
        )}
        {!admissionDrawable || admissionMark === null || admission === null || admission.measured === null ? null : (
          <Mark
            point={admissionMark.point_mm}
            box={box}
            open={opened}
            provenance="inferred"
            label={`release admission estimate, worst case ${number(admission.measured, 1, 'mm')}`}
            shape="estimate"
            radiusMm={admission.measured}
          />
        )}
        {placedMark === null ? null : (
          <Mark point={placedMark.point_mm} box={box} open={opened} provenance="measured" label="observed placement" shape="placed" />
        )}
        {anchor !== null && baseResidualMm !== null ? (
          <g>
            <path
              d={`M${String(padToScene(anchor.point_mm, box).x)} ${String(padToScene(anchor.point_mm, box).y + 26)} h${String(baseResidualMm * scale)}`}
              stroke={PROVENANCE_COLOUR.measured}
              strokeWidth="2"
            />
            <text
              x={padToScene(anchor.point_mm, box).x + 4}
              y={padToScene(anchor.point_mm, box).y + 42}
              fill={PROVENANCE_COLOUR.measured}
              fontSize="11"
              fontFamily="var(--font-mono)"
            >
              base return residual {number(baseResidualMm, 1, 'mm')}
            </text>
          </g>
        ) : null}
        {frame.state === 'resolved' ? null : (
          // The compact map is drawn at about seven tenths of the open one, so its type is set larger in the box's own
          // units to come out the same size on the screen.
          <text x={origin.x} y={origin.y} textAnchor="middle" fill="var(--muted-foreground)" fontSize={opened ? '13' : '17'} fontFamily="var(--font-mono)">
            {frame.state === 'absent' ? 'no pad frame was resolved in this run' : 'no pad-frame position resolved yet'}
          </text>
        )}
      </svg>

      <div className="legend">
        <LegendItem provenance="measured">measured</LegendItem>
        <LegendItem provenance="inferred">inferred, including every admission estimate</LegendItem>
        <LegendItem provenance="planned">
          planned: the admissible centre {rules.length === 0 ? '(no rule recorded yet)' : `at ${rules.map((rule) => number(rule.margin_mm, 1, 'mm')).join(' and ')}`}
        </LegendItem>
        <LegendItem provenance="historical">established earlier, not re-verified</LegendItem>
        <LegendItem provenance="unavailable">unavailable</LegendItem>
      </div>

      {opened ? (
        <table className="table" style={{ marginTop: '0.5rem' }}>
          <tbody>
            <tr>
              <td>search anchor</td>
              <td>{anchor === null ? UNAVAILABLE : `x ${anchor.point_mm[0].toFixed(1)} mm, y ${anchor.point_mm[1].toFixed(1)} mm`}</td>
              <td className="wrap">{anchor === null ? 'no frame in this run resolved a pad-frame position yet' : `measured, from observation ${anchor.observation_id}`}</td>
            </tr>
            <tr>
              <td>admissible centre</td>
              <td>{rules.length === 0 ? UNAVAILABLE : rules.map((rule) => number(rule.margin_mm, 1, 'mm')).join(', ')}</td>
              <td className="wrap">
                {rules.length === 0
                  ? 'no predicate in this run carried a centre-to-edge threshold in millimetres, so no inset is drawn; this console holds no copy of the rule'
                  : `read from the record: ${rules
                      .map(
                        (rule) =>
                          `${rule.predicates.join(', ')} decided at ${number(rule.margin_mm, 1, 'mm')}${rule.rule_ref === null ? '' : ` under ${rule.rule_ref}`}`,
                      )
                      .join('; ')}`}
              </td>
            </tr>
            <tr>
              <td>planned release point</td>
              <td>{UNAVAILABLE}</td>
              <td className="wrap">
                the record states the plan as a mode and a travel, not as a pad-frame point, so there is no planned position to draw; the rule it
                has to satisfy is the admissible centre above
              </td>
            </tr>
            <tr>
              <td>base displacement</td>
              <td>{baseResidualMm === null ? UNAVAILABLE : `${baseResidualMm.toFixed(1)} mm residual`}</td>
              <td className="wrap">
                {baseResidualMm === null
                  ? 'no predicate measured it; a commanded pulse duration is not a displacement and is not shown as one'
                  : 'measured, from the base_returned predicate'}
              </td>
            </tr>
            <tr>
              <td>release admission</td>
              <td>{admission === null ? UNAVAILABLE : `worst case ${number(admission.measured, 1, 'mm')} against ${number(admission.threshold, 1, 'mm')}`}</td>
              <td className="wrap">
                {admission === null
                  ? 'no release admission was evaluated in this run'
                  : admissionDrawable
                    ? `inferred: an estimate from configured empirical bounds, not a sighting; drawn at observation ${admissionMark?.observation_id ?? ''}`
                    : 'inferred: an estimate from configured empirical bounds, not a sighting. It is not drawn on the scene: no pad-frame position was resolved for the observation this predicate consumed, and drawing it at another one would move it to evidence it was not computed from.'}
              </td>
            </tr>
            <tr>
              <td>observed placement</td>
              <td>{placedMark === null ? UNAVAILABLE : `x ${placedMark.point_mm[0].toFixed(1)} mm, y ${placedMark.point_mm[1].toFixed(1)} mm`}</td>
              <td className="wrap">
                {placedMark === null ? 'nothing has been observed on the pad after a release in this run' : `measured, from observation ${placedMark.observation_id}`}
              </td>
            </tr>
            <tr>
              <td>retention</td>
              <td>
                {view.predicates.find((item) => item.predicate === 'object_secured') === undefined ? (
                  UNAVAILABLE
                ) : travelling ? (
                  <Badge tone="warn">historical</Badge>
                ) : (
                  <Badge tone="ok">measured</Badge>
                )}
              </td>
              <td className="wrap">
                {travelling
                  ? `last verified at seq ${String(view.predicates.find((item) => item.predicate === 'object_secured')?.seq ?? 0)}, ${String(
                      view.seq_cursor - (view.predicates.find((item) => item.predicate === 'object_secured')?.seq ?? view.seq_cursor),
                    )} record(s) ago, and not re-verified while the base travels`
                  : 'the object_secured predicate and its evidence are shown in the predicate table'}
              </td>
            </tr>
          </tbody>
        </table>
      ) : null}
    </Panel>
  );
}
