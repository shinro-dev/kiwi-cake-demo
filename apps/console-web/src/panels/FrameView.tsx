// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A frame and its overlays. The SVG is in the IMAGE PIXELS of that frame, so nothing has to be rescaled by hand, and
// every layer is drawn from THAT frame's own perception. A layer whose evidence is missing is not drawn and is listed
// as unavailable with the reason: an overlay that is absent must never be mistaken for a measurement that came back
// empty.
//
// Anything derived through the pad frame is drawn differently from a current detection: current detections are solid,
// derived and retained positions are dashed and hollow.
//
// EVERY OVERLAY IS BOUND TO ONE OBSERVATION. The picture carries the identity it is a picture of, the layers carry the
// identity they were measured in, and nothing is drawn unless the two are the same. A pinned decision moves and its
// perception is fetched after it, so without the check there is a moment in every run where the newest measurements
// would be drawn over the previous picture, looking exactly like measurements of it.

import type { ReactNode } from 'react';
import { useState } from 'react';
import { proxiedMediaUrl } from '../model/camera';
import type { PerceptionDto } from '../model/observation';
import { overlayForFrame, overlayWithheld } from '../model/overlay';
import type { OverlayModel } from '../model/overlay';
import { closed, project, projectPolyline } from '../geometry/homography';
import type { Mat3, Point2, Projected } from '../geometry/homography';
import { metresToMillimetres, PAD_CORNERS_MM, PAD_HALF_X_MM, PAD_HALF_Y_MM, padGridLinesMm } from '../geometry/pad';

function runsToPath(runs: Projected[][]): string {
  return runs
    .map((run) => run.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(' '))
    .join(' ');
}

/** The pad-frame grid: lines every 50 mm, out to the pad's own edges, projected through this frame's homography. */
function padGrid(h: Mat3): string {
  const ticks = padGridLinesMm(50);
  const lines: Point2[][] = [];
  for (const x of ticks.x) lines.push([[x, -PAD_HALF_Y_MM] as Point2, [x, PAD_HALF_Y_MM] as Point2]);
  for (const y of ticks.y) lines.push([[-PAD_HALF_X_MM, y] as Point2, [PAD_HALF_X_MM, y] as Point2]);
  return lines.map((line) => runsToPath(projectPolyline(h, line, 12))).join(' ');
}

export interface FrameViewProps {
  imageUrl: string | null;
  /**
   * WHICH observation this picture is. It is the identity the overlay is checked against, so an overlay measured in
   * one frame can never be drawn over another, and it is the React key of the image element, so a new picture replaces
   * the old element instead of loading into it under the old caption.
   */
  frameId: string | null;
  /**
   * The frame's own pixel size, which is the coordinate system every overlay is expressed in. Null when it was not
   * recorded: no drawing surface is then set up at all, because a surface built on a guessed size would place a
   * measured box somewhere it was not measured.
   */
  widthPx: number | null;
  heightPx: number | null;
  perception: PerceptionDto | null;
  overlay: OverlayModel;
  /** The watermark: nothing but a live frame goes unmarked. */
  watermark: 'replay' | 'synthetic' | null;
  /** The word printed on the watermark, when a caller names the mode in its own words (Evidence: Rehearsal, Recording). */
  watermarkWord?: string;
  caption: ReactNode;
  /** A frame with no image: what to say instead of showing one. */
  emptyNote: ReactNode;
  /**
   * Told once when the browser asked for this frame's picture and did not get it.
   *
   * A picture that fails to load leaves an element with nothing in it, and an empty box under a caption that names an
   * observation reads as a frame that is blank rather than as a request that failed. This card says which it is by
   * itself; the callback is for the PANEL around it, whose heading has to stop calling the card by the state it was in
   * before the request failed. It is called with the observation id so a late failure for a picture that has already
   * been replaced cannot be attributed to the picture now on screen.
   */
  onImageError?: (frameId: string | null) => void;
  /** A second frame beside the first one: evidence rather than the view out of the window, and given less of the page. */
  inset?: boolean;
}

export function FrameView(props: FrameViewProps): ReactNode {
  const { perception } = props;
  const served = proxiedMediaUrl(props.imageUrl);

  /**
   * The size of the picture the browser actually received, kept with the observation it was received for. The runtime
   * transcodes a stored frame for width and never crops or rotates it, so the served picture has the recorded shape
   * and an overlay in recorded pixels lands where it was measured. This checks that rather than assuming it: a picture
   * of another shape means the path between the store and this tab did something to it, and a box measured in the
   * recorded pixels cannot be placed on it.
   */
  const [receivedSize, setReceivedSize] = useState<{ for: string | null; width: number; height: number } | null>(null);
  const received = receivedSize !== null && receivedSize.for === props.frameId ? receivedSize : null;

  /**
   * The observation whose picture this browser asked for and did not get, kept with the observation it failed for so a
   * failure never carries over onto the next frame. While it stands, the marked scene is drawn with the reason on it
   * instead of an element that loaded nothing: a blank box under a caption naming a stored frame is exactly what made
   * a served frame look like a missing one.
   */
  const [failedFor, setFailedFor] = useState<string | null>(null);
  const imageFailed = props.frameId !== null && failedFor === props.frameId;

  const size = props.widthPx === null || props.heightPx === null ? null : { width: props.widthPx, height: props.heightPx };
  const shapeChanged =
    size !== null && received !== null && received.height > 0 && Math.abs(received.width / received.height - size.width / size.height) > 0.01
      ? `the picture served for this observation is ${String(received.width)} x ${String(received.height)} px, which is not the shape the record states (${String(size.width)} x ${String(size.height)} px)`
      : null;
  const overlay =
    shapeChanged === null
      ? overlayForFrame(props.overlay, props.frameId)
      : overlayWithheld(`${shapeChanged}, so nothing measured in the recorded pixels is drawn over it`, props.overlay.bound_to);
  const cube = perception?.cube ?? null;
  const box = overlay.layers.cube_box.valid ? cube?.box_px ?? null : null;
  const contact = overlay.layers.cube_contact.valid ? cube?.contact_px ?? null : null;
  const mask = overlay.layers.cube_mask.valid ? cube?.mask_polygon_px ?? null : null;
  const jaw = overlay.layers.jaw_window.valid ? perception?.jaw_window_px ?? null : null;
  const landmarks = overlay.layers.pad_landmarks.valid ? perception?.landmarks_px ?? null : null;
  const h = overlay.homography;

  const padOutline = h !== null && overlay.layers.pad_outline.valid ? runsToPath(projectPolyline(h.matrix, closed(PAD_CORNERS_MM), 24)) : null;
  const grid = h !== null && overlay.layers.pad_projection.valid ? padGrid(h.matrix) : null;
  const centreMm = perception?.cube_by_template?.cube_center_pad_m ?? null;
  const derivedCentre =
    h !== null && overlay.layers.pad_projection.valid && centreMm !== null ? project(h.matrix, metresToMillimetres(centreMm)) : null;

  return (
    <div className={`frame ${props.inset === true ? 'frame--inset' : ''}`} style={{ containerType: 'inline-size' }}>
      {served.url === null || imageFailed ? (
        <SyntheticBackdrop
          width={size?.width ?? 640}
          height={size?.height ?? 480}
          note={
            imageFailed ? (
              <>
                <strong>The frame could not be served.</strong>
                <span>
                  The controller stored this frame and the request this page made for its image did not answer with one. The capture is not missing; its
                  picture did not arrive. Nothing is drawn over a picture that is not here.
                </span>
              </>
            ) : served.refusal === null ? (
              props.emptyNote
            ) : (
              <>
                <strong>This picture is not loaded.</strong>
                <span>{served.refusal}</span>
              </>
            )
          }
        />
      ) : (
        <img
          // The element is keyed by the observation, so a new decision replaces the picture rather than loading a new
          // one into the element the old caption and the old overlay were drawn over.
          key={props.frameId ?? served.url}
          className="frame__image"
          src={served.url}
          alt="the stored frame this decision was taken from"
          // A stored frame is evidence that is read when it is looked at. The live preview is a stream and is never
          // lazy: deferring a stream defers the stream itself.
          loading="lazy"
          decoding="async"
          onLoad={(event) => {
            const image = event.currentTarget;
            setReceivedSize({ for: props.frameId, width: image.naturalWidth, height: image.naturalHeight });
          }}
          onError={() => {
            setFailedFor(props.frameId);
            props.onImageError?.(props.frameId);
          }}
        />
      )}

      {size === null ? null : (
      <svg className="frame__svg" viewBox={`0 0 ${String(size.width)} ${String(size.height)}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
        {grid === null ? null : <path d={grid} stroke="var(--accent)" strokeWidth="1" fill="none" opacity="0.45" strokeDasharray="3 4" />}
        {padOutline === null ? null : <path d={padOutline} stroke="var(--accent)" strokeWidth="2" fill="none" />}
        {landmarks === null
          ? null
          : landmarks.map((point, index) => (
              <circle key={`landmark-${String(index)}`} cx={point[0]} cy={point[1]} r="4" fill="none" stroke="var(--accent)" strokeWidth="1.5" />
            ))}
        {mask === null ? null : (
          <polygon points={mask.map((point) => `${String(point[0])},${String(point[1])}`).join(' ')} fill="var(--primary)" opacity="0.16" />
        )}
        {jaw === null ? null : (
          <rect
            x={jaw[0]}
            y={jaw[1]}
            width={jaw[2] - jaw[0]}
            height={jaw[3] - jaw[1]}
            fill="none"
            stroke="var(--agent-accent)"
            strokeWidth="1.5"
            strokeDasharray="6 4"
          />
        )}
        {box === null ? null : <rect x={box[0]} y={box[1]} width={box[2] - box[0]} height={box[3] - box[1]} fill="none" stroke="var(--primary)" strokeWidth="2" />}
        {contact === null ? null : (
          <g stroke="var(--primary)" strokeWidth="2">
            <path d={`M${String(contact[0] - 9)} ${String(contact[1])} h18`} />
            <path d={`M${String(contact[0])} ${String(contact[1] - 9)} v18`} />
          </g>
        )}
        {derivedCentre === null ? null : (
          // Derived through the pad frame, not seen directly: hollow and dashed, so it never reads as a detection.
          <g>
            <circle cx={derivedCentre.x} cy={derivedCentre.y} r="9" fill="none" stroke="var(--warning)" strokeWidth="1.5" strokeDasharray="4 3" />
            <circle cx={derivedCentre.x} cy={derivedCentre.y} r="1.5" fill="var(--warning)" />
          </g>
        )}
      </svg>
      )}

      {props.watermark === null ? null : (
        <div className={`frame__watermark ${props.watermark === 'replay' ? 'frame__watermark--replay' : ''}`}>{props.watermarkWord ?? props.watermark}</div>
      )}
      <div className="frame__caption">
        {props.caption}
        {shapeChanged === null ? null : <span>overlays withheld: {shapeChanged}</span>}
      </div>
    </div>
  );
}

/** What is drawn when there is no image: a marked scene, never something that could pass for a photograph. */
function SyntheticBackdrop(props: { width: number; height: number; note: ReactNode }): ReactNode {
  return (
    <>
      <svg className="frame__svg" viewBox={`0 0 ${String(props.width)} ${String(props.height)}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
        <defs>
          <pattern id="backdrop-grid" width="32" height="32" patternUnits="userSpaceOnUse">
            <path d="M32 0H0V32" fill="none" stroke="var(--border)" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width={props.width} height={props.height} fill="var(--surface-sunken)" />
        <rect width={props.width} height={props.height} fill="url(#backdrop-grid)" opacity="0.5" />
      </svg>
      <div className="frame__empty" style={{ justifyContent: 'flex-start', paddingTop: '0.75rem' }}>
        {props.note}
      </div>
    </>
  );
}

/** The per-layer list that always sits beside a frame. */
export function LayerList(props: { overlay: OverlayModel }): ReactNode {
  return (
    <div className="layers">
      {Object.entries(props.overlay.layers).map(([name, layer]) => (
        <div key={name} className={`layer ${layer.valid ? 'layer--on' : 'layer--off'}`}>
          <span className="layer__name">{name}</span>
          <span className="layer__reason">{layer.valid ? 'drawn from this frame' : layer.reason}</span>
        </div>
      ))}
    </div>
  );
}
