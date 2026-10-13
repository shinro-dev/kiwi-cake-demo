// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// Plane-to-plane projection for the overlays. A pad-frame point is projected into the pixels of ONE frame through THAT
// frame's pad-to-image homography, and a point whose homogeneous w is not positive is behind the camera plane: it is
// dropped, never clamped and never drawn. An edge is therefore sampled and drawn as the runs of points that survived,
// so an edge that leaves the plane in front of the camera breaks instead of sweeping across the image.

/** Row-major 3x3. */
export type Mat3 = readonly [number, number, number, number, number, number, number, number, number];

export type Point2 = readonly [number, number];

export interface Projected {
  x: number;
  y: number;
  w: number;
}

const W_EPSILON = 1e-9;

export function isMat3(value: readonly number[] | null): value is number[] & { length: 9 } {
  return value !== null && value.length === 9 && value.every((item) => Number.isFinite(item));
}

export function asMat3(value: readonly number[]): Mat3 {
  return [value[0] as number, value[1] as number, value[2] as number, value[3] as number, value[4] as number, value[5] as number, value[6] as number, value[7] as number, value[8] as number];
}

/**
 * Project one point. Returns null when w is not strictly positive: such a point has no image and is dropped.
 */
export function project(h: Mat3, point: Point2): Projected | null {
  const [x, y] = point;
  const w = h[6] * x + h[7] * y + h[8];
  if (!(w > W_EPSILON)) return null;
  const u = h[0] * x + h[1] * y + h[2];
  const v = h[3] * x + h[4] * y + h[5];
  return { x: u / w, y: v / w, w };
}

/** Gaussian elimination with partial pivoting. Returns null for a singular system. */
export function solveLinear(a: number[][], b: number[]): number[] | null {
  const n = b.length;
  const m = a.map((row, index) => [...row, b[index] as number]);
  for (let column = 0; column < n; column += 1) {
    let pivot = column;
    for (let row = column + 1; row < n; row += 1) {
      if (Math.abs((m[row] as number[])[column] as number) > Math.abs((m[pivot] as number[])[column] as number)) pivot = row;
    }
    const pivotRow = m[pivot] as number[];
    if (Math.abs(pivotRow[column] as number) < 1e-12) return null;
    m[pivot] = m[column] as number[];
    m[column] = pivotRow;
    const top = m[column] as number[];
    for (let row = 0; row < n; row += 1) {
      if (row === column) continue;
      const current = m[row] as number[];
      const factor = (current[column] as number) / (top[column] as number);
      if (factor === 0) continue;
      for (let k = column; k <= n; k += 1) current[k] = (current[k] as number) - factor * (top[k] as number);
    }
  }
  const out: number[] = [];
  for (let row = 0; row < n; row += 1) {
    const current = m[row] as number[];
    out.push((current[n] as number) / (current[row] as number));
  }
  return out.every((value) => Number.isFinite(value)) ? out : null;
}

/**
 * The homography that maps four source points to four destination points, normalized so that h33 is 1. Null when the
 * correspondence is degenerate. This is how a pad-to-image homography is obtained when the runtime serves the pad
 * corners but not the matrix: the pad's size is a recorded project value, so the four pad corners are known in
 * millimetres and the four image corners come from that frame's perception.
 */
export function homographyFromQuad(source: readonly Point2[], destination: readonly Point2[]): Mat3 | null {
  if (source.length !== 4 || destination.length !== 4) return null;
  const a: number[][] = [];
  const b: number[] = [];
  for (let i = 0; i < 4; i += 1) {
    const [x, y] = source[i] as Point2;
    const [u, v] = destination[i] as Point2;
    a.push([x, y, 1, 0, 0, 0, -u * x, -u * y]);
    b.push(u);
    a.push([0, 0, 0, x, y, 1, -v * x, -v * y]);
    b.push(v);
  }
  const solution = solveLinear(a, b);
  if (solution === null) return null;
  return [
    solution[0] as number,
    solution[1] as number,
    solution[2] as number,
    solution[3] as number,
    solution[4] as number,
    solution[5] as number,
    solution[6] as number,
    solution[7] as number,
    1,
  ];
}

/** The inverse, normalized so that h33 is 1. Null when the matrix is singular or the inverse has h33 of zero. */
export function invert(h: Mat3): Mat3 | null {
  const [a, b, c, d, e, f, g, i, j] = h;
  const cofactor0 = e * j - f * i;
  const cofactor1 = f * g - d * j;
  const cofactor2 = d * i - e * g;
  const determinant = a * cofactor0 + b * cofactor1 + c * cofactor2;
  if (!Number.isFinite(determinant) || Math.abs(determinant) < 1e-15) return null;
  const inverse = [
    cofactor0 / determinant,
    (c * i - b * j) / determinant,
    (b * f - c * e) / determinant,
    cofactor1 / determinant,
    (a * j - c * g) / determinant,
    (c * d - a * f) / determinant,
    cofactor2 / determinant,
    (b * g - a * i) / determinant,
    (a * e - b * d) / determinant,
  ];
  const last = inverse[8] as number;
  if (!Number.isFinite(last) || Math.abs(last) < 1e-15) return null;
  return asMat3(inverse.map((value) => value / last));
}

/** `count` points along the segment, endpoints included. */
export function sampleSegment(from: Point2, to: Point2, count: number): Point2[] {
  const steps = Math.max(2, Math.floor(count));
  const out: Point2[] = [];
  for (let i = 0; i < steps; i += 1) {
    const t = i / (steps - 1);
    out.push([(from[0] as number) + ((to[0] as number) - (from[0] as number)) * t, (from[1] as number) + ((to[1] as number) - (from[1] as number)) * t]);
  }
  return out;
}

/**
 * Project a polyline of plane points, sampling each segment, and return the CONTIGUOUS runs of image points. A run
 * ends wherever a sample was dropped, so nothing is drawn across a point that has no image.
 */
export function projectPolyline(h: Mat3, polyline: readonly Point2[], samplesPerSegment = 16): Projected[][] {
  const runs: Projected[][] = [];
  let run: Projected[] = [];
  const pushRun = (): void => {
    if (run.length >= 2) runs.push(run);
    run = [];
  };
  for (let i = 0; i + 1 < polyline.length; i += 1) {
    const samples = sampleSegment(polyline[i] as Point2, polyline[i + 1] as Point2, samplesPerSegment);
    // The shared endpoint is projected once per segment; skipping the first sample after the first segment keeps the
    // run continuous without a duplicated point.
    for (let s = i === 0 ? 0 : 1; s < samples.length; s += 1) {
      const projected = project(h, samples[s] as Point2);
      if (projected === null) pushRun();
      else run.push(projected);
    }
  }
  pushRun();
  return runs;
}

/** A closed polygon: the polyline with the first point repeated at the end. */
export function closed(points: readonly Point2[]): Point2[] {
  if (points.length === 0) return [];
  return [...points, points[0] as Point2];
}
