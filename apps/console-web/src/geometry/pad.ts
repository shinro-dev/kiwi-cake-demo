// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// PAD_FRAME: the origin is the pad centre, x runs along the long edge, y runs toward the pad's top edge, in
// millimetres. The pad is 415 x 295 mm, the recorded project value the perception code also uses
// (`autonomy/perception/pad_localizer.py`, PAD_SIZE_M). The scene view draws this frame directly, so a millimetre on
// the screen is a millimetre on the pad; nothing on that view is in image pixels.

import type { Mat3, Point2 } from './homography';
import { homographyFromQuad } from './homography';

export const PAD_LONG_MM = 415;
export const PAD_SHORT_MM = 295;
export const PAD_HALF_X_MM = PAD_LONG_MM / 2;
export const PAD_HALF_Y_MM = PAD_SHORT_MM / 2;

/** The pad outline, top left first, clockwise on the pad as the scene view draws it. */
export const PAD_CORNERS_MM: readonly Point2[] = [
  [-PAD_HALF_X_MM, PAD_HALF_Y_MM],
  [PAD_HALF_X_MM, PAD_HALF_Y_MM],
  [PAD_HALF_X_MM, -PAD_HALF_Y_MM],
  [-PAD_HALF_X_MM, -PAD_HALF_Y_MM],
];

export function metresToMillimetres(point: Point2): Point2 {
  return [(point[0] as number) * 1000, (point[1] as number) * 1000];
}

/** Signed distance from the pad edge, positive inside. Negative means that far outside. */
export function insideMarginMm(point: Point2): number {
  const x = PAD_HALF_X_MM - Math.abs(point[0] as number);
  const y = PAD_HALF_Y_MM - Math.abs(point[1] as number);
  return Math.min(x, y);
}

export interface SceneBox {
  width: number;
  height: number;
  padding: number;
}

/**
 * The pad's own proportions, long edge over short edge. Every box the scene is drawn in is derived from this rather
 * than chosen by hand: a box whose drawable area has another shape leaves a band of empty scene along one axis, and a
 * reader takes that band for pad that nothing was found on.
 */
export const PAD_ASPECT = PAD_LONG_MM / PAD_SHORT_MM;

/**
 * A scene box of the given width whose DRAWABLE area carries the pad's proportions exactly, so the pad fills it in
 * both directions. The height follows from the width and the padding and is never stated separately: a height chosen
 * by hand is a second source of truth for the same shape, and the two drift.
 */
export function sceneBoxOfWidth(width: number, padding: number): SceneBox {
  const usable = width - padding * 2;
  return { width, height: usable / PAD_ASPECT + padding * 2, padding };
}

/**
 * Grid lines every `stepMm` from the origin, out to the last multiple that is still ON the pad.
 *
 * The extents come from the configured pad size. Written out by hand they went 2.5 mm past the short edge, which drew
 * two lines over floor that is not pad and put every reading near the edge a little further inside than it is.
 */
export function padGridLinesMm(stepMm: number): { x: number[]; y: number[] } {
  const ticks = (halfMm: number): number[] => {
    const out: number[] = [0];
    for (let value = stepMm; value <= halfMm; value += stepMm) {
      out.unshift(-value);
      out.push(value);
    }
    return out;
  };
  return { x: ticks(PAD_HALF_X_MM), y: ticks(PAD_HALF_Y_MM) };
}

/**
 * The rectangle a cube CENTRE has to stay inside for `marginMm` to hold against every pad edge, or null when no such
 * rectangle exists because the margin is wider than the pad's short half.
 *
 * `insideMarginMm` is the same rule read the other way round, and the two are kept in one file so a change to one is a
 * change to both.
 */
export function centreAdmissibleHalfExtentsMm(marginMm: number): { half_x_mm: number; half_y_mm: number } | null {
  if (!Number.isFinite(marginMm) || marginMm < 0) return null;
  const halfX = PAD_HALF_X_MM - marginMm;
  const halfY = PAD_HALF_Y_MM - marginMm;
  if (halfX <= 0 || halfY <= 0) return null;
  return { half_x_mm: halfX, half_y_mm: halfY };
}

/** Millimetres to the scene viewport, y flipped so that the pad's top edge is at the top of the screen. */
export function padToScene(point: Point2, box: SceneBox): { x: number; y: number } {
  const usableWidth = box.width - box.padding * 2;
  const usableHeight = box.height - box.padding * 2;
  const scale = Math.min(usableWidth / PAD_LONG_MM, usableHeight / PAD_SHORT_MM);
  return {
    x: box.width / 2 + (point[0] as number) * scale,
    y: box.height / 2 - (point[1] as number) * scale,
  };
}

export function sceneScale(box: SceneBox): number {
  return Math.min((box.width - box.padding * 2) / PAD_LONG_MM, (box.height - box.padding * 2) / PAD_SHORT_MM);
}

/**
 * The pad-to-image homography for one frame: the matrix the runtime served, when it served one, otherwise the matrix
 * derived from that frame's four pad corners in image pixels. `derived` says which, so the screen can mark a
 * projection that rests on a reconstruction.
 */
export function padToImage(
  served: readonly number[] | null,
  cornersPx: readonly Point2[] | null,
): { matrix: Mat3; derived: boolean } | null {
  if (served !== null && served.length === 9 && served.every((value) => Number.isFinite(value))) {
    return {
      matrix: [
        served[0] as number,
        served[1] as number,
        served[2] as number,
        served[3] as number,
        served[4] as number,
        served[5] as number,
        served[6] as number,
        served[7] as number,
        served[8] as number,
      ],
      derived: false,
    };
  }
  if (cornersPx !== null && cornersPx.length === 4) {
    const matrix = homographyFromQuad(PAD_CORNERS_MM, cornersPx);
    if (matrix !== null) return { matrix, derived: true };
  }
  return null;
}
