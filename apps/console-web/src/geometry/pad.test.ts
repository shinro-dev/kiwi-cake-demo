// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The pad frame and the box the map is drawn in. Every mark on the map goes through these functions, so a change here
// moves every reading on the screen; the numbers below are computed from the configured pad size (415 x 295 mm) by
// hand, not recorded from a run of this code.

import { describe, expect, it } from 'vitest';
import type { Point2 } from './homography';
import {
  centreAdmissibleHalfExtentsMm,
  insideMarginMm,
  metresToMillimetres,
  PAD_ASPECT,
  PAD_HALF_X_MM,
  PAD_HALF_Y_MM,
  padGridLinesMm,
  padToImage,
  padToScene,
  sceneBoxOfWidth,
  sceneScale,
} from './pad';

const COMPACT = sceneBoxOfWidth(360, 16);
const EXPANDED = sceneBoxOfWidth(760, 44);

describe('the box the pad is drawn in', () => {
  it('gives its drawable area the proportions of the pad itself at every width', () => {
    for (const box of [COMPACT, EXPANDED, sceneBoxOfWidth(1000, 0)]) {
      const usableWidth = box.width - box.padding * 2;
      const usableHeight = box.height - box.padding * 2;
      expect(usableWidth / usableHeight).toBeCloseTo(PAD_ASPECT, 9);
    }
  });

  it('draws the pad the same way in the compact box and in the expanded one, to the scale factor', () => {
    const compactScale = sceneScale(COMPACT);
    const expandedScale = sceneScale(EXPANDED);
    const point: Point2 = [100, -60];
    const inCompact = padToScene(point, COMPACT);
    const inExpanded = padToScene(point, EXPANDED);
    expect((inCompact.x - COMPACT.width / 2) / compactScale).toBeCloseTo((inExpanded.x - EXPANDED.width / 2) / expandedScale, 9);
    expect((inCompact.y - COMPACT.height / 2) / compactScale).toBeCloseTo((inExpanded.y - EXPANDED.height / 2) / expandedScale, 9);
  });

  it('puts the pad centre at the centre of the box and the top edge above it', () => {
    const centre = padToScene([0, 0], COMPACT);
    expect(centre.x).toBeCloseTo(COMPACT.width / 2, 9);
    expect(centre.y).toBeCloseTo(COMPACT.height / 2, 9);
    expect(padToScene([0, PAD_HALF_Y_MM], COMPACT).y).toBeLessThan(centre.y);
  });

  it('leaves the padding free on all four sides and no more than that', () => {
    const topLeft = padToScene([-PAD_HALF_X_MM, PAD_HALF_Y_MM], COMPACT);
    const bottomRight = padToScene([PAD_HALF_X_MM, -PAD_HALF_Y_MM], COMPACT);
    expect(topLeft.x).toBeCloseTo(COMPACT.padding, 9);
    expect(topLeft.y).toBeCloseTo(COMPACT.padding, 9);
    expect(bottomRight.x).toBeCloseTo(COMPACT.width - COMPACT.padding, 9);
    expect(bottomRight.y).toBeCloseTo(COMPACT.height - COMPACT.padding, 9);
  });
});

describe('the grid lines on the pad', () => {
  it('stops at the last multiple that is still on the pad, on both axes', () => {
    const lines = padGridLinesMm(50);
    expect(lines.x).toEqual([-200, -150, -100, -50, 0, 50, 100, 150, 200]);
    // The short half is 147.5 mm, so a line at 150 would be drawn 2.5 mm off the pad.
    expect(lines.y).toEqual([-100, -50, 0, 50, 100]);
  });

  it('never places a line past a pad edge, at any step', () => {
    for (const step of [10, 25, 50, 147.5]) {
      const lines = padGridLinesMm(step);
      for (const x of lines.x) expect(Math.abs(x)).toBeLessThanOrEqual(PAD_HALF_X_MM);
      for (const y of lines.y) expect(Math.abs(y)).toBeLessThanOrEqual(PAD_HALF_Y_MM);
      expect(lines.x).toContain(0);
      expect(lines.y).toContain(0);
    }
  });
});

describe('the margin a cube centre has to keep from the pad edge', () => {
  it('reads the same rule forwards and backwards', () => {
    const extents = centreAdmissibleHalfExtentsMm(37.96);
    expect(extents).not.toBeNull();
    expect((extents as { half_x_mm: number }).half_x_mm).toBeCloseTo(169.54, 9);
    expect((extents as { half_y_mm: number }).half_y_mm).toBeCloseTo(109.54, 9);
    // A centre exactly on that rectangle is exactly at the margin the rule asks for.
    expect(insideMarginMm([169.54, 0])).toBeCloseTo(37.96, 9);
    expect(insideMarginMm([0, 109.54])).toBeCloseTo(37.96, 9);
  });

  it('refuses a margin that no centre on this pad could satisfy, instead of returning an inverted rectangle', () => {
    expect(centreAdmissibleHalfExtentsMm(PAD_HALF_Y_MM)).toBeNull();
    expect(centreAdmissibleHalfExtentsMm(400)).toBeNull();
    expect(centreAdmissibleHalfExtentsMm(-1)).toBeNull();
    expect(centreAdmissibleHalfExtentsMm(Number.NaN)).toBeNull();
  });

  it('reports a centre outside the pad as a negative margin rather than as zero', () => {
    expect(insideMarginMm([0, 0])).toBeCloseTo(PAD_HALF_Y_MM, 9);
    expect(insideMarginMm([PAD_HALF_X_MM + 12, 0])).toBeCloseTo(-12, 9);
    expect(insideMarginMm([-PAD_HALF_X_MM, PAD_HALF_Y_MM])).toBeCloseTo(0, 9);
  });

  it('reads a pad-frame centre in metres as millimetres', () => {
    const millimetres = metresToMillimetres([0.1054, -0.0321]);
    expect(millimetres[0]).toBeCloseTo(105.4, 9);
    expect(millimetres[1]).toBeCloseTo(-32.1, 9);
  });
});

describe('the pad-to-image matrix for one frame', () => {
  const CORNERS: Point2[] = [
    [120, 150],
    [520, 150],
    [610, 430],
    [30, 430],
  ];

  it('uses the matrix the runtime served and says it was not derived', () => {
    const served = [1, 0, 320, 0, -1, 264, 0, 0, 1];
    const result = padToImage(served, CORNERS);
    expect(result?.derived).toBe(false);
    expect(result?.matrix[2]).toBe(320);
  });

  it('derives one from four pad corners when the runtime served none, and says so', () => {
    const result = padToImage(null, CORNERS);
    expect(result?.derived).toBe(true);
  });

  it('returns nothing at all when neither a matrix nor four corners were served', () => {
    expect(padToImage(null, null)).toBeNull();
    expect(padToImage([1, 2, 3], CORNERS.slice(0, 3))).toBeNull();
    expect(padToImage([1, 0, 0, 0, 1, 0, 0, 0, Number.NaN], null)).toBeNull();
  });
});
