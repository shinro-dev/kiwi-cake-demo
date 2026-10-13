// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The expected values in this file were computed independently, with numpy, from the same four correspondences; they
// are not a recording of what this implementation happens to print.

import { describe, expect, it } from 'vitest';
import { asMat3, closed, homographyFromQuad, invert, project, projectPolyline } from './homography';
import type { Mat3, Point2 } from './homography';
import { PAD_CORNERS_MM, padToImage, padToScene } from './pad';

const IMAGE_CORNERS: Point2[] = [
  [120, 150],
  [520, 150],
  [610, 430],
  [30, 430],
];

describe('homographyFromQuad', () => {
  it('recovers the matrix for the pad corners, to the golden vector', () => {
    const h = homographyFromQuad(PAD_CORNERS_MM, IMAGE_CORNERS);
    expect(h).not.toBeNull();
    const expected = [1.14089009098, 0.398478035282, 320.0, 0.0, -0.588031822899, 264.285714286, 0.0, 0.00124524386026, 1.0];
    for (let i = 0; i < 9; i += 1) expect((h as Mat3)[i]).toBeCloseTo(expected[i] as number, 9);
  });

  it('maps each source corner exactly onto its destination corner', () => {
    const h = homographyFromQuad(PAD_CORNERS_MM, IMAGE_CORNERS) as Mat3;
    for (let i = 0; i < 4; i += 1) {
      const out = project(h, PAD_CORNERS_MM[i] as Point2);
      expect(out).not.toBeNull();
      expect((out as { x: number }).x).toBeCloseTo((IMAGE_CORNERS[i] as Point2)[0], 8);
      expect((out as { y: number }).y).toBeCloseTo((IMAGE_CORNERS[i] as Point2)[1], 8);
    }
  });

  it('projects interior points to the golden vectors', () => {
    const h = homographyFromQuad(PAD_CORNERS_MM, IMAGE_CORNERS) as Mat3;
    const centre = project(h, [0, 0]) as { x: number; y: number; w: number };
    expect(centre.x).toBeCloseTo(320.0, 9);
    expect(centre.y).toBeCloseTo(264.2857142857, 9);
    expect(centre.w).toBeCloseTo(1.0, 9);

    const offset = project(h, [100, 50]) as { x: number; y: number; w: number };
    expect(offset.x).toBeCloseTo(427.4019294371, 8);
    expect(offset.y).toBeCloseTo(221.1169000326, 8);
    expect(offset.w).toBeCloseTo(1.062262193, 9);

    const edge = project(h, [-207.5, 0]) as { x: number; y: number };
    expect(edge.x).toBeCloseTo(83.2653061224, 8);
    expect(edge.y).toBeCloseTo(264.2857142857, 8);
  });

  it('refuses a degenerate correspondence instead of returning a matrix', () => {
    const degenerate: Point2[] = [
      [0, 0],
      [0, 0],
      [0, 0],
      [0, 0],
    ];
    expect(homographyFromQuad(degenerate, IMAGE_CORNERS)).toBeNull();
    expect(homographyFromQuad(PAD_CORNERS_MM, [[1, 1]])).toBeNull();
  });
});

describe('points with a non-positive w', () => {
  // w = 1 + 0.01 * y, so y = -100 puts w at exactly zero and y below that puts it behind the plane.
  const h: Mat3 = asMat3([1, 0, 0, 0, 1, 0, 0, 0.01, 1]);

  it('projects a point in front', () => {
    const out = project(h, [10, 10]) as { x: number; w: number };
    expect(out.w).toBeCloseTo(1.1, 12);
    expect(out.x).toBeCloseTo(9.0909090909, 8);
  });

  it('drops a point whose w is exactly zero', () => {
    expect(project(h, [0, -100])).toBeNull();
  });

  it('drops a point whose w is negative', () => {
    expect(project(h, [0, -200])).toBeNull();
  });

  it('breaks a polyline into runs instead of drawing across the dropped samples', () => {
    // A segment from y = +200 down to y = -300 crosses w = 0 once, so one run survives and it stops at the crossing.
    const runs = projectPolyline(h, [[0, 200] as Point2, [0, -300] as Point2], 51);
    expect(runs.length).toBe(1);
    const run = runs[0] as { y: number; w: number }[];
    expect(run.every((point) => point.w > 0)).toBe(true);
    expect(run.length).toBeLessThan(51);
  });

  it('keeps the runs on either side of a dropped middle', () => {
    // Two segments in a row, the middle of which lies behind the plane: two runs come back, not one.
    const polyline: Point2[] = [
      [0, 200],
      [0, -300],
      [0, 200],
    ];
    const runs = projectPolyline(h, polyline, 51);
    expect(runs.length).toBe(2);
    for (const run of runs) expect(run.every((point) => point.w > 0)).toBe(true);
  });
});

describe('invert', () => {
  it('round-trips a point through the matrix and its inverse', () => {
    const h = homographyFromQuad(PAD_CORNERS_MM, IMAGE_CORNERS) as Mat3;
    const back = invert(h) as Mat3;
    const forward = project(h, [100, 50]) as { x: number; y: number };
    const round = project(back, [forward.x, forward.y]) as { x: number; y: number };
    expect(round.x).toBeCloseTo(100, 6);
    expect(round.y).toBeCloseTo(50, 6);
  });

  it('refuses a singular matrix', () => {
    expect(invert(asMat3([1, 2, 3, 2, 4, 6, 1, 1, 1]))).toBeNull();
  });
});

describe('padToImage', () => {
  it('uses a served matrix without deriving one', () => {
    const served = [2, 0, 1, 0, 2, 1, 0, 0, 1];
    const result = padToImage(served, IMAGE_CORNERS);
    expect(result?.derived).toBe(false);
    expect(result?.matrix[0]).toBe(2);
  });

  it('derives one from the four pad corners when no matrix was served', () => {
    const result = padToImage(null, IMAGE_CORNERS);
    expect(result?.derived).toBe(true);
    expect(result?.matrix[2]).toBeCloseTo(320.0, 9);
  });

  it('gives nothing when neither was served', () => {
    expect(padToImage(null, null)).toBeNull();
    expect(padToImage([1, 2, 3], null)).toBeNull();
  });
});

describe('the pad scene', () => {
  it('puts the pad origin at the centre and the pad top edge above it', () => {
    const box = { width: 800, height: 600, padding: 20 };
    const origin = padToScene([0, 0], box);
    expect(origin.x).toBeCloseTo(400, 9);
    expect(origin.y).toBeCloseTo(300, 9);
    const top = padToScene([0, 147.5], box);
    expect(top.y).toBeLessThan(origin.y);
    const right = padToScene([207.5, 0], box);
    expect(right.x).toBeGreaterThan(origin.x);
  });

  it('closes a polygon by repeating the first point', () => {
    const ring = closed(PAD_CORNERS_MM);
    expect(ring.length).toBe(5);
    expect(ring[4]).toEqual(PAD_CORNERS_MM[0]);
    expect(closed([])).toEqual([]);
  });
});
