// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE PICTURE ELEMENT OF ONE CAMERA PANEL, WATCHED: the DOM half of `model/streamDecode.ts`.
//
// It owns three things the state machine cannot: the element's `load` and `error` events, a clock tick, and a sample of
// the decoded pixels. Everything they report goes through the machine, so what the chip says is decided there and
// pinned by its tests, and nothing here decides a word.
//
// THE PIXELS ARE READ, NOT ASSUMED. Every SAMPLE_EVERY_MS the decoded picture is drawn into a 32 by 24 canvas and
// digested. The stream is served by the console API on this page's own origin, so the canvas is not tainted and the
// read is allowed; if a browser refuses it anyway, the sample is skipped and nothing is judged from it. The same draw
// gives the picture's mean luminance, which is what lets a panel say a decoded picture is nearly black instead of
// leaving a dark panel to be read as no picture at all.
//
// NOTHING HERE STARTS OR RETRIES FASTER THAN THE BACKOFF. A failed or broken attempt drops its element (the element of a
// stale attempt is not kept on the screen) and the next one is mounted only when the machine says its wait is over. A
// decoded picture that has not changed is kept: the samples are reported, never acted on.
//
// THE LAST PICTURE IS COPIED, NOT KEPT ALIVE. On the same sampling tick the decoded picture is also drawn into a copy at
// most KEEP_WIDTH wide, and that copy (with the time it was taken) is what a panel may draw through a gap: on the classic
// screen marked STALE with its age (`keptPictureView` in `model/streamDecode.ts`), on the DEMO screen with the fixed time
// it was taken (`keptPictureAtView`). The element itself is dropped exactly as before.
//
// A DROPPED ELEMENT LETS GO OF ITS STREAM. Taking an <img> off the page does not end its request: MEASURED in the same
// headless Chrome, six multipart streams whose elements were removed stayed open, six of six, and a later request to the
// same server then waited for a free connection and timed out. Chrome allows six connections to one server, and this
// page already holds four for as long as it is open (the event stream, the telemetry stream and two cameras), so two
// dropped attempts were enough to leave every other request queued behind them, Stop's POST included. Removing the
// element's `src` once it has left the page ends the request (measured: none left open, and the next request answered
// in milliseconds). `releaseDetachedPicture` does that, and the ref callback calls it for every element it is handed
// back.

import { useCallback, useEffect, useRef, useState } from 'react';
import type { SyntheticEvent } from 'react';
import {
  decoded,
  elementMounted,
  errored,
  firstAttempt,
  nextAttempt,
  retryDue,
  SAMPLE_EVERY_MS,
  sampled,
  ticked,
} from '../model/streamDecode';
import type { KeptPicture, StreamDecode } from '../model/streamDecode';

const TICK_MS = 500;
const SAMPLE_WIDTH = 32;
const SAMPLE_HEIGHT = 24;
/** The widest copy kept of the last decoded picture, in pixels: enough to read the scene, small enough to redraw. */
const KEEP_WIDTH = 640;

/** A copy of the decoded picture as a jpeg data URL, or null when the browser refuses the read. */
function copyPicture(element: HTMLImageElement, canvas: HTMLCanvasElement): string | null {
  const width = element.naturalWidth;
  const height = element.naturalHeight;
  if (width <= 0 || height <= 0) return null;
  const scale = Math.min(1, KEEP_WIDTH / width);
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  const context = canvas.getContext('2d');
  if (context === null) return null;
  try {
    context.drawImage(element, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.8);
  } catch {
    return null;
  }
}

/** What the panel binds to its picture element for the current attempt, or null when no element should be shown. */
export interface StreamBinding {
  /** Changes with every attempt, so each attempt is a NEW element and a new request. */
  key: string;
  ref: (element: HTMLImageElement | null) => void;
  onLoad: (event: SyntheticEvent<HTMLImageElement>) => void;
  onError: (event: SyntheticEvent<HTMLImageElement>) => void;
}

/** The parts of a picture element `releaseDetachedPicture` reads and changes, so a test can hand it a stand-in. */
export interface DetachablePicture {
  readonly isConnected: boolean;
  hasAttribute(name: string): boolean;
  removeAttribute(name: string): void;
}

/**
 * END THE REQUEST OF A PICTURE ELEMENT THAT IS NO LONGER ON THE PAGE, and do nothing to one that still is. True when a
 * request was let go.
 *
 * `isConnected` is what tells a real removal from React's development-only rehearsal of one: under StrictMode React
 * hands every ref callback null and then the SAME element again, without taking the element off the page, and clearing
 * the `src` of a picture that is still showing would blank it with nothing to put it back. So this is called after the
 * commit that detached the element (from a microtask), and it acts only on an element that has actually gone.
 *
 * The attribute is removed rather than set to the empty string: both end the request (measured), and only the empty
 * string also fires an `error` event at the element.
 */
export function releaseDetachedPicture(element: DetachablePicture): boolean {
  if (element.isConnected || !element.hasAttribute('src')) return false;
  element.removeAttribute('src');
  return true;
}

/** A digest and the mean luminance of a small draw of the decoded picture, or null when it cannot be read. */
function samplePicture(element: HTMLImageElement, canvas: HTMLCanvasElement): { digest: string; luminance: number } | null {
  if (!element.complete || element.naturalWidth === 0) return null;
  try {
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (context === null) return null;
    context.drawImage(element, 0, 0, SAMPLE_WIDTH, SAMPLE_HEIGHT);
    const pixels = context.getImageData(0, 0, SAMPLE_WIDTH, SAMPLE_HEIGHT).data;
    // FNV-1a over the colour channels. It only has to tell one picture from the next; it is not a security digest.
    let hash = 0x811c9dc5;
    let sum = 0;
    for (let index = 0; index < pixels.length; index += 4) {
      const red = pixels[index] ?? 0;
      const green = pixels[index + 1] ?? 0;
      const blue = pixels[index + 2] ?? 0;
      sum += 0.2126 * red + 0.7152 * green + 0.0722 * blue;
      for (const channel of [red, green, blue]) {
        hash ^= channel;
        hash = Math.imul(hash, 0x01000193) >>> 0;
      }
    }
    return { digest: hash.toString(16), luminance: sum / (pixels.length / 4) };
  } catch {
    // A canvas the browser will not let this page read. Nothing is concluded from a sample that was not taken.
    return null;
  }
}

/**
 * Watch the picture element of one panel.
 *
 * `baseUrl` is the stream the runtime offers for this role, or null when it offers none; a change of it starts over at
 * attempt 1. `frameAgeMs` is how long ago the runtime last handed this tab a NEW frame of this role, from the telemetry,
 * and is read only so an unchanged picture is reported as one while the runtime says new frames are arriving.
 */
export function useStreamDecode(
  baseUrl: string | null,
  frameAgeMs: number | null,
): { decode: StreamDecode | null; binding: StreamBinding | null; kept: KeptPicture | null } {
  const [decode, setDecode] = useState<StreamDecode | null>(null);
  const [kept, setKept] = useState<KeptPicture | null>(null);
  const keepCanvas = useRef<HTMLCanvasElement | null>(null);
  // THE MACHINE'S CURRENT STATE, held where the element's events and the tick both read it. Every change goes through
  // `advance`, outside any state updater: the pixel sample and the timestamps are effects, and a React updater may be
  // run twice, which would take a sample twice and keep only the second answer.
  const state = useRef<StreamDecode | null>(null);
  const element = useRef<HTMLImageElement | null>(null);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const lastSampleAt = useRef(0);
  const frameAge = useRef(frameAgeMs);
  frameAge.current = frameAgeMs;

  const advance = useCallback((next: StreamDecode | null) => {
    if (next === state.current) return;
    state.current = next;
    setDecode(next);
  }, []);

  // A NEW STREAM STARTS AT ATTEMPT 1, and no stream has no attempt. The failure count starts over with it.
  useEffect(() => {
    lastSampleAt.current = 0;
    advance(baseUrl === null ? null : firstAttempt(Date.now()));
  }, [baseUrl, advance]);

  const active = decode !== null;
  useEffect(() => {
    if (!active) return undefined;
    const timer = setInterval(() => {
      const current = state.current;
      if (current === null) return;
      const nowMs = Date.now();
      let next = ticked(current, nowMs);
      const shown = element.current;
      // THE ELEMENT'S OWN WORD, in case its load event came before this component listened: a width on THIS attempt's
      // element is a decode of this attempt, because every attempt is a new element.
      if (next.phase === 'opening' && shown !== null && shown.complete && shown.naturalWidth > 0) {
        next = decoded(next, shown.naturalWidth, nowMs);
      }
      if (next.phase === 'decoded' && shown !== null && nowMs - lastSampleAt.current >= SAMPLE_EVERY_MS) {
        lastSampleAt.current = nowMs;
        if (canvas.current === null) {
          canvas.current = document.createElement('canvas');
          canvas.current.width = SAMPLE_WIDTH;
          canvas.current.height = SAMPLE_HEIGHT;
        }
        const sample = samplePicture(shown, canvas.current);
        next = sampled(next, sample?.digest ?? null, sample?.luminance ?? null, frameAge.current, nowMs);
        if (keepCanvas.current === null) keepCanvas.current = document.createElement('canvas');
        const copy = copyPicture(shown, keepCanvas.current);
        if (copy !== null) setKept({ url: copy, at_ms: nowMs });
      }
      if (retryDue(next, nowMs)) {
        lastSampleAt.current = 0;
        next = nextAttempt(next, nowMs);
      }
      advance(next);
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [active, advance]);

  // EVERY ELEMENT THIS IS HANDED BACK IS LET GO OF, once it has left the page. React calls this with null when the
  // attempt's element is taken off, and with the new element when a new attempt replaces it; either way the element it
  // held before is checked after the commit has finished and, if it is gone, its stream is ended.
  const ref = useCallback((node: HTMLImageElement | null) => {
    const previous = element.current;
    element.current = node;
    if (previous !== null && previous !== node) queueMicrotask(() => releaseDetachedPicture(previous));
  }, []);
  // ONLY THE CURRENT ATTEMPT'S ELEMENT SPEAKS FOR IT. An element that has been dropped can still deliver an event it
  // queued before it went, and read against the attempt that is current now it would mark a new attempt decoded or
  // failed on the word of an old one.
  const onLoad = useCallback(
    (event: SyntheticEvent<HTMLImageElement>) => {
      const current = state.current;
      if (current === null || event.currentTarget !== element.current) return;
      advance(decoded(current, event.currentTarget.naturalWidth, Date.now()));
    },
    [advance],
  );
  const onError = useCallback(
    (event: SyntheticEvent<HTMLImageElement>) => {
      const current = state.current;
      if (current === null || event.currentTarget !== element.current) return;
      advance(errored(current, Date.now()));
    },
    [advance],
  );

  // THE ELEMENT EXISTS ONLY WHILE AN ATTEMPT IS OPEN OR SHOWING. A failed or broken attempt's picture is dropped, and
  // the cover says why; the next attempt mounts a new element when its wait is over.
  const shown = elementMounted(decode);
  return {
    decode,
    binding: shown && baseUrl !== null ? { key: `${baseUrl}#${String(decode.attempt)}`, ref, onLoad, onError } : null,
    kept,
  };
}
