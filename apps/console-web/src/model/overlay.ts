// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// Overlay validity, LAYER BY LAYER, from one frame's own perception.
//
// The cube layers and the jaw window live in image pixels and rest on the detector alone. The pad outline, the pad
// landmarks and anything projected from PAD_FRAME rest on that frame's pad frame. When the pad frame is missing the
// pad layers go away and the cube layers stay: a missing pad frame is not a reason to stop drawing what was seen.

import type { PerceptionDto } from './observation';
import type { Mat3 } from '../geometry/homography';
import { padToImage } from '../geometry/pad';

export const LAYER_NAMES = ['cube_box', 'cube_mask', 'cube_contact', 'jaw_window', 'pad_outline', 'pad_landmarks', 'pad_projection'] as const;

export type LayerName = (typeof LAYER_NAMES)[number];

export interface LayerState {
  valid: boolean;
  /** Why not, when not. Never null for an invalid layer. */
  reason: string | null;
}

export type Layers = Record<LayerName, LayerState>;

export interface OverlayModel {
  layers: Layers;
  /**
   * Tristate on purpose. `true` and `false` are claims about the frame; `null` means no perception was served for it,
   * so nothing is known either way and the screen must not print "not valid for this frame".
   */
  pad_frame_valid: boolean | null;
  /** The pad-to-image matrix for THIS frame, and whether it was derived from the corners rather than served. */
  homography: { matrix: Mat3; derived: boolean } | null;
  /**
   * WHICH OBSERVATION these layers belong to. Every coordinate in them was measured in that frame's pixels, so an
   * overlay is only ever drawn over the picture of that same observation; see `overlayForFrame`.
   */
  bound_to: string | null;
}

const ok: LayerState = { valid: true, reason: null };

function no(reason: string): LayerState {
  return { valid: false, reason };
}

function allOff(reason: string): Layers {
  return {
    cube_box: no(reason),
    cube_mask: no(reason),
    cube_contact: no(reason),
    jaw_window: no(reason),
    pad_outline: no(reason),
    pad_landmarks: no(reason),
    pad_projection: no(reason),
  };
}

/**
 * The layers for one frame.
 *
 * `framePx` is the frame's own pixel size. Every overlay coordinate the detector serves is in THOSE pixels, and the
 * drawing surface is set up from them, so a frame whose size is not known cannot be drawn on at all: a box placed with
 * a guessed size would sit over the wrong part of the picture and still look like a measurement. `undefined` means the
 * caller is not drawing over a picture and has nothing to check.
 */
export function overlayLayers(
  perception: PerceptionDto | null,
  framePx?: { width: number; height: number } | null,
  boundTo: string | null = null,
): OverlayModel {
  if (framePx === null) {
    return {
      layers: allOff('the pixel size of this frame was not recorded, so nothing can be placed in its coordinates'),
      pad_frame_valid: perception === null ? null : perception.pad_by_template?.pad_frame_valid ?? null,
      homography: null,
      bound_to: boundTo,
    };
  }
  if (perception === null) {
    return {
      layers: allOff('no perception was served for this frame'),
      pad_frame_valid: null,
      homography: null,
      bound_to: boundTo,
    };
  }

  const cube = perception.cube;
  const padFrameValid = perception.pad_by_template?.pad_frame_valid === true;
  const padReason = padFrameValid
    ? null
    : perception.pad_by_template?.reason ?? 'this frame has no valid pad frame';
  const homography = padFrameValid ? padToImage(perception.homography_pad_to_image, perception.pad?.corners_px ?? null) : null;

  return {
    layers: {
      cube_box: cube === null ? no('this frame carries no cube result') : cube.box_px === null ? no('the cube result carries no box') : ok,
      cube_mask:
        cube === null
          ? no('this frame carries no cube result')
          : cube.mask_polygon_px === null
            ? no('the served observation does not carry the cube mask')
            : ok,
      cube_contact:
        cube === null ? no('this frame carries no cube result') : cube.contact_px === null ? no('the cube result carries no contact point') : ok,
      jaw_window: perception.jaw_window_px === null ? no('the served observation does not carry the grasp reference window') : ok,
      pad_outline: !padFrameValid
        ? no(padReason as string)
        : perception.pad?.corners_px == null
          ? no('the pad frame is valid but this frame carries no pad corners')
          : ok,
      pad_landmarks: !padFrameValid
        ? no(padReason as string)
        : perception.landmarks_px === null
          ? no('the served observation does not carry landmark points')
          : ok,
      pad_projection: !padFrameValid
        ? no(padReason as string)
        : homography === null
          ? no('the pad frame is valid but neither a pad-to-image matrix nor four pad corners were served')
          : ok,
    },
    pad_frame_valid: padFrameValid,
    homography,
    bound_to: boundTo,
  };
}

/**
 * The layers as they may be drawn over the picture of `frameId`, which is the identity of the image actually on the
 * screen.
 *
 * A pinned decision moves, and the perception behind it is fetched afterwards; between the two there is a moment when
 * the newest perception belongs to one observation and the picture on the screen to another. A box measured in one
 * frame drawn over a different frame is a measurement placed on a picture it was not taken from, and it would look
 * exactly like a measurement. So the identities have to agree before anything is drawn, and when they do not the
 * layers go off with that as the reason, which is the reason the layer list then prints.
 */
export function overlayForFrame(overlay: OverlayModel, frameId: string | null): OverlayModel {
  if (overlay.bound_to === frameId) return overlay;
  // NOTHING WAS READ FOR ANY OBSERVATION (verify-r4 must_fix 3): no perception was "served" for anything, so the words
  // are about this page's read of the shown frame, never the runtime's serving.
  if (overlay.bound_to === null) return overlayWithheld(notReadWords(frameId), null);
  const shown = frameId === null ? 'a picture with no stated observation' : frameId;
  return overlayWithheld(
    `this perception was served for ${overlay.bound_to} and the picture on screen is ${shown}: nothing measured in one frame is drawn over another`,
    overlay.bound_to,
  );
}

/** Every layer's words when this page has read no perception for the shown frame. */
function notReadWords(frameId: string | null): string {
  return frameId === null ? 'no perception has been read by this page for the picture on screen' : `the perception of ${frameId} has not been read by this page`;
}

/** What this page fetched for one observation: the store's `frame`. */
export interface FetchedPerception {
  observation_id: string | null;
  perception: PerceptionDto | null;
  loading: boolean;
  /** Why the read brought no perception back, from its answer (round 7 item 1). */
  perception_reason?: string | null;
}

/**
 * THE LAYERS FOR THE FRAME ON SCREEN, FROM WHAT THIS PAGE FETCHED (redesign r4 verify-r4 must_fix 3). Only a perception
 * the page read for the shown frame is drawn. A read for it that is in flight, or that brought nothing back, says so in
 * this page's voice; a perception read for another frame keeps the two identities (`overlayForFrame`); and with no
 * perception in hand for any frame, or a read for another frame that brought none back, the layers say the shown frame's
 * perception has not been read. None of these says a perception was served when none was read.
 */
export function overlayForShown(fetched: FetchedPerception, framePx: { width: number; height: number } | null | undefined, shownId: string | null): OverlayModel {
  if (fetched.observation_id !== null && fetched.observation_id === shownId) {
    if (fetched.perception !== null || framePx === null) return overlayLayers(fetched.perception, framePx, shownId);
    return overlayWithheld(
      fetched.loading
        ? `the perception of ${shownId} is being read by this page`
        : `the perception of ${shownId} could not be read by this page${fetched.perception_reason == null ? '' : `; ${fetched.perception_reason}`}`,
      shownId,
    );
  }
  if (fetched.observation_id !== null && fetched.perception !== null) {
    return overlayForFrame(overlayLayers(fetched.perception, framePx, fetched.observation_id), shownId);
  }
  return overlayWithheld(notReadWords(shownId), null);
}

/** Every layer off, for one stated reason, keeping the identity the perception was served for. */
export function overlayWithheld(reason: string, boundTo: string | null): OverlayModel {
  return { layers: allOff(reason), pad_frame_valid: null, homography: null, bound_to: boundTo };
}
