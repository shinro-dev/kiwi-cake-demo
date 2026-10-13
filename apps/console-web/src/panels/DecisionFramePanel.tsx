// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CURRENT DECISION FRAME: the exact stored frame, and the exact perception, that the last predicate or action
// consumed. It is kept apart from the live view and it does not move while its result is on the screen, because a
// result shown next to a later picture is a result nobody can check.
//
// The stored frame is immutable and it is fetched BY ITS OBSERVATION ID through the console API on this page's own
// origin. The runtime transcodes it on demand for size; it never replaces it. Nothing here holds the camera open: a
// stored frame costs the device nothing, which is what lets this card sit beside the live view.
//
// The picture, the pixel size and the overlay all have to belong to the SAME observation before anything is drawn.
// The pin moves with the record and the perception behind it is fetched afterwards, so for a moment the newest
// perception is the previous frame's; drawn over this one it would look exactly like a measurement of it.

import type { ReactNode } from 'react';
import { digestPrefix, number, textOr, UNAVAILABLE } from '../model/format';
import { overlayForShown } from '../model/overlay';
import { modeMark } from '../model/modeMark';
import { stopCause } from '../model/stopCause';
import type { ConsoleSource } from '../api/types';
import type { StoreSnapshot } from '../store/store';
import type { ObservationMeta } from '../model/types';
import { Badge } from '../ui/Badge';
import { IconFrame } from '../ui/Icons';
import { Field, Fields, Panel } from '../ui/Panel';
import { FrameView, LayerList } from './FrameView';
import type { ScreenMode } from './StatusStrip';

/**
 * WHAT A PINNED OBSERVATION IS WHEN IT IS NOT A PICTURE (verdict 2026-09-27 item 10): the words for a capture whose record
 * says it is a reading of scalar values or a frame the runtime did not retain, or null for a retained picture and for a
 * capture whose record has not arrived. Such an observation is said, never drawn: no <img>, no overlay.
 */
export function noPictureWords(meta: ObservationMeta | null): string | null {
  if (meta === null) return null;
  if (meta.media === 'scalar_set') return `a ${meta.source_role} reading of scalar values with no picture`;
  if (!meta.retained) return `a ${meta.source_role} ${meta.media === 'frame_pair' ? 'frame pair' : 'frame'} the runtime did not retain, so no picture of it is stored`;
  return null;
}

export function DecisionFramePanel(props: { snapshot: StoreSnapshot; mode: ScreenMode; source: ConsoleSource }): ReactNode {
  const { view, frame } = props.snapshot;
  const pinned = view.decision_frame;
  const meta = pinned?.capture ?? null;
  const shownId = pinned?.primary_observation_id ?? null;

  // The fetched record and perception belong to the observation the fetch was FOR. They are read only when that is the
  // observation being shown; otherwise they are held back rather than mixed with this frame's own capture record.
  const fetchedIsForShown = frame.observation_id !== null && frame.observation_id === shownId;
  const record = fetchedIsForShown ? frame.record : null;

  // The frame's OWN pixel size, from the capture record or the fetched observation. There is no default: every overlay
  // coordinate is in these pixels, so a guessed size would draw a box over the wrong part of a real photograph. When
  // the two sources of the size disagree, neither is used: one of them describes another picture.
  const fromMeta = meta?.width_px == null || meta.height_px == null ? null : { width: meta.width_px, height: meta.height_px };
  const fromRecord = record?.camera?.width_px == null || record.camera.height_px == null ? null : { width: record.camera.width_px, height: record.camera.height_px };
  const sizeDisagrees = fromMeta !== null && fromRecord !== null && (fromMeta.width !== fromRecord.width || fromMeta.height !== fromRecord.height);
  const framePx = sizeDisagrees ? null : fromMeta ?? fromRecord;

  const shownPerception = fetchedIsForShown ? frame.perception : null;
  // Bound here rather than only inside the frame, so the layer list beside the picture gives the same answer the
  // picture does: an overlay that is withheld has to SAY it is withheld, in the list a reader checks it in. What was
  // fetched for ANOTHER frame, or nothing at all, is said as this page's read, never as what the runtime served
  // (verify-r4 must_fix 3).
  const overlay = overlayForShown(frame, framePx, shownId);
  const imageUrl = shownId === null ? null : props.source.frameUrl(shownId, 'raw', 800);
  const perception = pinned?.perception ?? null;
  const provenance = perception?.provenance ?? null;

  if (pinned === null) {
    return (
      <Panel title="current decision frame" state="waiting" stateWords="Nothing pinned yet">
        <p className="muted">
          No predicate or action has consumed a frame in this run yet. This panel stays empty rather than showing the newest capture: the newest
          capture is not what anything was decided on.
        </p>
      </Panel>
    );
  }

  const noPicture = noPictureWords(meta);
  if (noPicture !== null) {
    return (
      <Panel title="current decision reading" state="active" stateWords="A reading, no picture">
        <p className="muted wrap" data-frame-media={meta?.media ?? ''}>
          {`This decision read ${shownId ?? UNAVAILABLE}, ${noPicture}: the ${pinned.consumed_by.by} ${pinned.consumed_by.name} consumed it at seq ${String(pinned.consumed_by.seq)}. `}
          Nothing is drawn for it, and no overlay is placed.
        </p>
      </Panel>
    );
  }

  // WHAT THE RECORD SAYS ABOUT THIS FRAME, and no more. It is the newest frame a predicate or an action consumed; it is
  // tied to the run's stop only when the stop's own records (`failure.observation_ids`) name it. When they name no frame,
  // or another one, the panel says so rather than lending the stop the frame that happens to be pinned (browser D5).
  const cause = stopCause(view.recent_events, null);
  const tie: 'tied' | 'other' | 'none' | null =
    cause === null || shownId === null ? null : cause.frame_ids.includes(shownId) ? 'tied' : cause.frame_ids.length === 0 ? 'none' : 'other';
  const tieWords =
    tie === 'tied'
      ? "The stop's records name this frame, so it is tied to the stop."
      : tie === 'other'
        ? `The stop's records name another frame, ${cause?.frame_ids.join(', ') ?? ''}, so this frame is not tied to the stop.`
        : tie === 'none'
          ? "The stop's records name no frame, so this frame is not tied to the stop."
          : null;
  const stateWords = tie === 'tied' ? 'Tied to the stop' : tie === null ? 'Newest frame consumed' : 'Not named by the stop';
  // The mode in the words of the top bar (spec 5.2): Rehearsal or Recording, never the source's own identifier.
  const watermark = props.mode === 'live' ? null : props.mode;
  const watermarkWord = watermark === null ? undefined : modeMark({ dataMode: props.mode, sourceKind: 'live', replaying: props.mode === 'replay' }).word;

  return (
    <Panel
      title="current decision frame"
      state={frame.loading ? 'waiting' : 'active'}
      stateWords={frame.loading ? 'Reading the record' : stateWords}
      extra={
        <>
          <Badge tone="info">
            <IconFrame /> pinned at seq {String(pinned.pinned_at_seq)}
          </Badge>
          <Badge tone="quiet" title={`this frame was consumed by the ${pinned.consumed_by.by} ${pinned.consumed_by.name}`}>
            {pinned.consumed_by.by} {pinned.consumed_by.name}
          </Badge>
        </>
      }
      note={
        <>
          {`This is the newest frame a predicate or an action consumed: the ${pinned.consumed_by.by} ${pinned.consumed_by.name}, at seq ${String(pinned.consumed_by.seq)}. `}
          It and the perception below are held until a predicate or an action consumes different evidence.{' '}
          {tieWords === null ? null : <span data-frame-tie={tie ?? ''}>{tieWords} </span>}
          {frame.loading ? 'The record is still being fetched.' : ''}
          {sizeDisagrees
            ? ' The capture record and the fetched observation state different pixel sizes for this frame, so no overlay is placed on it: one of the two describes another picture.'
            : ''}
        </>
      }
    >
      <FrameView
        imageUrl={imageUrl}
        frameId={shownId}
        widthPx={framePx?.width ?? null}
        heightPx={framePx?.height ?? null}
        perception={shownPerception}
        overlay={overlay}
        inset
        watermark={watermark}
        watermarkWord={watermarkWord}
        emptyNote={
          <>
            <strong>The stored frame is not served by this source.</strong>
            <span>
              {props.mode === 'synthetic'
                ? "A rehearsal carries no image bytes. The overlays below are drawn from the rehearsal's perception for this observation, in its image pixels."
                : 'The runtime did not return an image for this observation.'}
            </span>
          </>
        }
        caption={
          <>
            <span>observation {shownId ?? UNAVAILABLE}</span>
            <span>{framePx === null ? `pixel size ${UNAVAILABLE}` : `${String(framePx.width)} x ${String(framePx.height)} px`}</span>
            <span>digest {digestPrefix(meta?.content_digest ?? null)}</span>
            <span>{meta?.retained === true ? 'retained' : 'not retained'}</span>
          </>
        }
      />

      <div style={{ marginTop: '0.75rem' }}>
        <Fields wide>
          <Field label="detector">{textOr(perception?.payload.detector ?? null)}</Field>
          <Field label="result">
            {perception === null ? (
              UNAVAILABLE
            ) : (
              <Badge tone={perception.payload.result === 'detected' ? 'ok' : perception.payload.result === 'error' ? 'bad' : 'warn'}>
                {perception.payload.result}
              </Badge>
            )}
          </Field>
          <Field label="confidence">{number(perception?.payload.confidence ?? null, 3)}</Field>
          <Field label="measured pose">
            {shownPerception?.cube_by_template?.cube_center_pad_m == null
              ? UNAVAILABLE
              : `pad frame x ${(shownPerception.cube_by_template.cube_center_pad_m[0] * 1000).toFixed(1)} mm, y ${(
                  shownPerception.cube_by_template.cube_center_pad_m[1] * 1000
                ).toFixed(1)} mm`}
          </Field>
          <Field label="producer">{textOr(perception === null ? null : `${perception.payload.detector}`)}</Field>
          <Field label="model identity">{textOr(provenance?.model_identity ?? null)}</Field>
          <Field label="config digest">{digestPrefix(provenance?.config_digest ?? null)}</Field>
          <Field label="calibration digest">{digestPrefix(provenance?.calibration_digest ?? null)}</Field>
          <Field label="derivation">
            {provenance === null ? (
              UNAVAILABLE
            ) : provenance.reconstructed ? (
              <Badge tone="warn">reconstructed: constants were not recorded with the run</Badge>
            ) : (
              <Badge tone="ok">recorded with the run</Badge>
            )}
          </Field>
          <Field label="pad frame">
            {overlay.pad_frame_valid === null ? (
              // Nothing was served for this frame. "Not valid" would be a claim about the frame; this is a claim about
              // the record, and the two are not the same.
              <Badge tone="quiet">{frame.loading ? 'not read yet' : UNAVAILABLE}</Badge>
            ) : overlay.pad_frame_valid ? (
              <Badge tone="ok">valid{overlay.homography?.derived === true ? ', matrix derived from the corners' : ''}</Badge>
            ) : (
              <Badge tone="warn">not valid for this frame</Badge>
            )}
          </Field>
          <Field label="measurements">
            {perception === null || perception.payload.measurements.length === 0
              ? UNAVAILABLE
              : perception.payload.measurements.map((item) => `${item.name} ${String(item.value)}${item.unit === null ? '' : ' ' + item.unit}`).join(', ')}
          </Field>
        </Fields>
        <LayerList overlay={overlay} />
      </div>
    </Panel>
  );
}
