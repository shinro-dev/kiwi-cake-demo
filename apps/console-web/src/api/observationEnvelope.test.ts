// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CONSOLE API'S OBSERVATION ANSWER IS AN ENVELOPE, AND THE PAGE READS WHAT IS INSIDE IT (redesign r4 round 7 item 1).
//
// The runtime answers an observation read with `{"ok": true, "observation_id": ..., "what": ..., "data": <projected
// record>}` (demo_runtime daemon `_op_observation`) and the console API forwards it as it is (`handle_observation`,
// `_forwarded`); a refusal keeps the runtime's reason_code and gets the status that matches it. The page handed the
// whole envelope to the parsers, so every field was null and Evidence said "this frame carries no cube result" about a
// frame whose served perception had a detected cube. The bodies below are shaped exactly as console_api serves them;
// every id and value is invented.

import { describe, expect, it } from 'vitest';
import { HttpSource } from './http';
import type { EventSourceLike, FetchLike } from './http';

const OBS = 'obs-20260928T090000Z-wrist-0042';

const PERCEPTION_ANSWER = {
  ok: true,
  observation_id: OBS,
  what: 'perception',
  data: {
    pad_by_template: { pad_frame_valid: true, reason: null },
    cube: { detected: true, confidence: 0.87, clipped: false, box_px: [300, 200, 360, 260], contact_px: [330, 258] },
    cube_by_template: { cube_center_pad_m: [0.052, -0.018], inside_pad_outline: true },
  },
};

const RECORD_ANSWER = {
  ok: true,
  observation_id: OBS,
  what: 'record',
  data: {
    observation_id: OBS,
    timestamp_utc: '2026-09-28T09:00:00.000Z',
    camera: { role: 'wrist', image_orientation: 'upright', width_px: 640, height_px: 480 },
    robot_state: { source: 'synthetic', measured_joints_deg: { shoulder_pan: 1.5 }, joint_stability_max_delta_deg: 0.2 },
    has_frame: true,
  },
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

function sourceServing(answers: Record<string, () => Response | Promise<Response>>): HttpSource {
  const fetchImpl: FetchLike = async (input) => {
    const answer = answers[input];
    return answer === undefined ? json(404, { ok: false, reason_code: 'not_found' }) : answer();
  };
  const eventSourceFactory = (): EventSourceLike => ({ onmessage: null, onerror: null, close: () => undefined });
  return new HttpSource({ fetchImpl, eventSourceFactory, boot: null, sessionId: 'observation-envelope-test' });
}

describe("the console API's observation answer", () => {
  it('unwraps the perception from an ok envelope, as console_api serves it', async () => {
    const source = sourceServing({ [`/api/observations/${OBS}/perception`]: () => json(200, PERCEPTION_ANSWER) });
    const read = await source.observationPerception(OBS);
    expect(read.reason).toBeNull();
    expect(read.data?.cube?.detected).toBe(true);
    expect(read.data?.cube?.box_px).toEqual([300, 200, 360, 260]);
    expect(read.data?.pad_by_template?.pad_frame_valid).toBe(true);
    expect(read.data?.cube_by_template?.cube_center_pad_m).toEqual([0.052, -0.018]);
  });

  it('unwraps the record from an ok envelope, as console_api serves it', async () => {
    const source = sourceServing({ [`/api/observations/${OBS}/record`]: () => json(200, RECORD_ANSWER) });
    const read = await source.observationRecord(OBS);
    expect(read.reason).toBeNull();
    expect(read.data?.observation_id).toBe(OBS);
    expect(read.data?.camera?.width_px).toBe(640);
    expect(read.data?.robot_state?.source).toBe('synthetic');
  });

  it("passes a refusal's reason_code through, and reads no data from it", async () => {
    const source = sourceServing({ [`/api/observations/${OBS}/perception`]: () => json(404, { ok: false, reason_code: 'not_found' }) });
    const read = await source.observationPerception(OBS);
    expect(read.data).toBeNull();
    expect(read.reason).toBe('the console server answered not_found');
  });

  it('never reads an envelope served for another observation, or one that carries no data, as this one', async () => {
    const other = sourceServing({ [`/api/observations/${OBS}/perception`]: () => json(200, { ...PERCEPTION_ANSWER, observation_id: 'obs-other' }) });
    const readOther = await other.observationPerception(OBS);
    expect(readOther.data).toBeNull();
    expect(readOther.reason).toContain('obs-other');
    const empty = sourceServing({ [`/api/observations/${OBS}/perception`]: () => json(200, { ok: true, observation_id: OBS, what: 'perception' }) });
    const readEmpty = await empty.observationPerception(OBS);
    expect(readEmpty.data).toBeNull();
    expect(readEmpty.reason).toBe('the answer carried no data');
  });

  it('says no answer came when the request did not complete', async () => {
    const source = sourceServing({ [`/api/observations/${OBS}/perception`]: () => Promise.reject(new Error('network down')) });
    const read = await source.observationPerception(OBS);
    expect(read.data).toBeNull();
    expect(read.reason).toBe('no answer came');
  });
});
