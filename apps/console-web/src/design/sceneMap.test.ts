// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SCENE MAP, RENDERED. It draws what the fold holds and nothing else: the pad, the placement envelope at the
// recorded margin, the scan's view footprints, the pan axis and the wedge only from an accepted fit, the blocks with
// their class sentences, the measured pulses, the placed block, and the sentence that the base is not drawn.
// It is a .ts file and builds its elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ContractEvent } from '../contracts/envelope';
import { buildFarBlockRun, buildSceneScanRun, SCENARIO_CATALOGUE } from '../api/syntheticScenarios';
import type { RunFactory } from '../api/syntheticScenarios';
import { emptySceneMap, foldSceneMap } from '../model/sceneMap';
import type { SceneMap as SceneMapModel } from '../model/sceneMap';
import { SceneMap } from './SceneMap';

function eventsOf(build: RunFactory, index = 1, startSeq = 0): ContractEvent[] {
  return build(index, startSeq, 1_000_000_000).steps.map((step) => step.event);
}

function markup(map: SceneMapModel): string {
  return renderToStaticMarkup(createElement(SceneMap, { map }));
}

function text(html: string): string {
  return html.replace(/<[^>]*>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/\s+/g, ' ');
}

function layer(html: string, name: string): boolean {
  return html.includes(`data-layer="${name}"`);
}

describe('the scene map, rendered', () => {
  it('draws the configured pad and says the base is not drawn, with nothing measured yet', () => {
    const html = markup(emptySceneMap());
    expect(layer(html, 'pad')).toBe(true);
    expect(layer(html, 'blocks')).toBe(false);
    expect(layer(html, 'wedge')).toBe(false);
    expect(text(html)).toContain("The robot's base is not drawn: no record carries where it stands.");
    expect(text(html)).toContain('Nothing on this map yet');
  });

  // ONE PLACE PER FACT (the second part of R5, named open by fixer C and by the integration of round 4): the sentence was in
  // the region's note and again as the drawing's <title>, so the Scene said it twice, and a screen reader read it twice.
  it('says the base is not drawn once, empty and with a scan drawn', () => {
    const sentence = "The robot's base is not drawn: no record carries where it stands.";
    for (const map of [emptySceneMap(), foldSceneMap(eventsOf(buildSceneScanRun))]) {
      expect(text(markup(map)).split(sentence).length - 1).toBe(1);
    }
  });

  it('draws a scan: the five footprints, the axis, the wedge of the accepted fit, and each block with its class sentence', () => {
    const html = markup(foldSceneMap(eventsOf(buildSceneScanRun)));
    expect(html.match(/data-view="/g)?.length).toBe(5);
    expect(layer(html, 'axis')).toBe(true);
    expect(layer(html, 'wedge')).toBe(true);
    const words = text(html);
    expect(words).toContain('BASE_ADVANCE');
    expect(words).toContain('placing it needs the base advance');
    expect(words).toContain('OUTSIDE_PAN_BAND');
    expect(words).toContain('the pan angle inferred');
    expect(html).not.toContain('data-inferred="true"');
    expect(words).toContain('fit accepted');
  });

  it('names the pan band around the home bearing the fit measured, not as absolute bearings', () => {
    const scan = eventsOf(buildSceneScanRun).map((event) => {
      if (event.kind !== 'perception.result' || event.payload.detector !== 'scene-pad') return event;
      return { ...event, payload: { ...event.payload, measurements: event.payload.measurements.map((item) => (item.name === 'home_bearing_deg' ? { ...item, value: 12.5 } : item)) } } as ContractEvent;
    });
    const words = text(markup(foldSceneMap(scan)));
    expect(words).toContain('the pan band, +/-25 deg around the home bearing (12.5 deg), drawn from an accepted fit');
    expect(words).not.toContain('from the home bearing');
  });

  it('marks a block whose position was carried by the pan alone as inferred', () => {
    const scan = eventsOf(buildSceneScanRun).map((event) => {
      if (event.kind !== 'perception.result' || event.payload.detector !== 'scene-block') return event;
      return { ...event, payload: { ...event.payload, measurements: event.payload.measurements.map((item) => (item.name === 'inferred' ? { ...item, value: true } : item)) } } as ContractEvent;
    });
    const html = markup(foldSceneMap(scan));
    expect(html).toContain('data-inferred="true"');
    expect(text(html)).toContain('its position is inferred, carried from another view by the pan alone');
  });

  it('draws no wedge and says why when the fit was not accepted', () => {
    const scan = eventsOf(buildSceneScanRun).map((event) => {
      if (event.kind !== 'perception.result' || event.payload.detector !== 'scene-pad') return event;
      return { ...event, payload: { ...event.payload, measurements: event.payload.measurements.map((item) => (item.name === 'fit_accepted' ? { ...item, value: false } : item)) } } as ContractEvent;
    });
    const html = markup(foldSceneMap(scan));
    expect(layer(html, 'wedge')).toBe(false);
    expect(text(html)).toContain('fit not accepted');
  });

  it('draws a placed cycle: the pulses with their measured advance, the placed block and the envelope from the record', () => {
    const html = markup(foldSceneMap(eventsOf(buildFarBlockRun)));
    expect(html.match(/data-pulse="/g)?.length).toBe(3);
    expect(layer(html, 'placed')).toBe(true);
    expect(layer(html, 'placement-envelope')).toBe(true);
    const words = text(html);
    expect(words).toContain('26.6 mm');
    expect(words).toContain('47.5 mm');
    expect(words).toContain('37.96 mm');
    expect(words).toContain('return pulses');
  });

  it('draws a refused block with the limits the refusal named', () => {
    const run = SCENARIO_CATALOGUE.find((spec) => spec.name === 'not-placeable-side')?.flow[0]?.build;
    if (run === undefined) throw new Error('no run');
    const words = text(markup(foldSceneMap(eventsOf(run))));
    expect(words).toContain('NOT_PLACEABLE');
    // Track P's explanation of the binding limit, the one the failure banner states too.
    expect(words).toContain('the right edge along the reach axis is 9.4 mm short of 58.96 mm after 120 mm of advance');
  });
});
