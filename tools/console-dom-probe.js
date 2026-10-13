// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A DOM PROBE OF THE OPERATOR CONSOLE, for `node tools/cdp_page.mjs --url URL --wait TEXT --script tools/console-dom-probe.js`.
//
// A screenshot shows what a control looks like; it cannot show whether the browser will refuse a click on it, and a
// camera panel that is black in a headless capture cannot say whether the picture element decoded nothing or decoded a
// black frame. This reads both from the page itself:
//
//   CONTROLS: for Start, Stop, Observe, Return arm, the activation button and the Diagnostics button, whether each is
//   present, its native `disabled` attribute, its `aria-disabled`, its title, and the reason text the page SHOWS for it
//   (the element its `aria-describedby` names, and only when that element is rendered). Every candidate select is
//   reported the same way, with its options.
//
//   CAMERAS: for each camera panel, its chip and note, its cover, and its picture element: which kind (stream or stored
//   frame), its address, `complete`, `naturalWidth` and `naturalHeight`, and a PIXEL CHECK of the decoded picture drawn
//   into a 64 by 48 canvas (mean red, green and blue, mean luminance, and the count of distinct colours). The stream is
//   served by the console API on the page's own origin, so the canvas is not tainted; if a browser refuses the read
//   anyway, the check says so rather than reporting zeros.
//
//   Beside them: the last Start or Stop receipt, the activation read-back in the Cake panel, and the drawer title when
//   the drawer is open.
//
// HOW THE RESULT COMES BACK. cdp_page.mjs evaluates this file and discards the value; what it prints is the page's
// `document.body.innerText` after `--after` milliseconds. So the probe writes ONE LINE into a rendered but visually
// clipped element appended to <body>, which innerText includes:
//
//   CONSOLE_DOM_PROBE {"probe":"console-dom-probe","version":1,...}
//
// and also sets `document.title` to `CONSOLE_DOM_PROBE written`. A shell reads the line with
// `grep '^CONSOLE_DOM_PROBE ' out.txt | cut -d' ' -f2-`. The expression's own value is the same JSON, for a caller that
// does read it. It reads the page and commands nothing: no control is clicked and no request is sent.
//
// THE LINE IS KEPT CURRENT. cdp_page.mjs runs this as soon as its `--wait` text appears and reads the body `--after`
// milliseconds later, and a camera takes a moment to decode its first frame. So the probe measures at once and again
// every second, rewriting the one line, and the line that is printed describes the page when it was read (to within a
// second; `at` and `samples` say when and how often). Running the probe again replaces the earlier one's timer.

(() => {
  const CONTROLS = ['start', 'stop', 'observe', 'return_arm', 'activate', 'diagnostics'];

  const rendered = (element) => element !== null && element.getClientRects().length > 0;

  const describedText = (element) => {
    const ids = (element.getAttribute('aria-describedby') || '').split(/\s+/).filter((id) => id !== '');
    const texts = ids
      .map((id) => document.getElementById(id))
      .filter((node) => rendered(node))
      .map((node) => node.innerText.trim())
      .filter((words) => words !== '');
    return texts.length === 0 ? null : texts.join(' ');
  };

  const control = (element) => {
    if (element === null) return { present: false };
    return {
      present: true,
      tag: element.tagName.toLowerCase(),
      label: (element.innerText || '').trim(),
      disabled: element.hasAttribute('disabled'),
      aria_disabled: element.getAttribute('aria-disabled'),
      title: element.getAttribute('title'),
      visible: rendered(element),
      reason: describedText(element),
    };
  };

  const pixels = (image) => {
    if (!image.complete || image.naturalWidth === 0) return { drawn: false, why: 'no decoded picture on the element' };
    try {
      const canvas = document.createElement('canvas');
      canvas.width = 64;
      canvas.height = 48;
      const context = canvas.getContext('2d', { willReadFrequently: true });
      context.drawImage(image, 0, 0, 64, 48);
      const data = context.getImageData(0, 0, 64, 48).data;
      const colours = new Set();
      let red = 0;
      let green = 0;
      let blue = 0;
      const count = data.length / 4;
      for (let index = 0; index < data.length; index += 4) {
        red += data[index];
        green += data[index + 1];
        blue += data[index + 2];
        colours.add((data[index] << 16) | (data[index + 1] << 8) | data[index + 2]);
      }
      const mean = (sum) => Math.round((sum / count) * 10) / 10;
      return {
        drawn: true,
        mean_rgb: [mean(red), mean(green), mean(blue)],
        mean_luminance: Math.round(((0.2126 * red + 0.7152 * green + 0.0722 * blue) / count) * 10) / 10,
        distinct_colours: colours.size,
      };
    } catch (error) {
      return { drawn: false, why: `the canvas read was refused: ${error && error.name ? error.name : 'error'}` };
    }
  };

  const camera = (panel) => {
    const image = panel.querySelector('img');
    const text = (selector) => {
      const node = panel.querySelector(selector);
      return node === null ? null : node.innerText.trim();
    };
    let picture = { present: false };
    if (image !== null) {
      const address = new URL(image.currentSrc || image.src, location.href);
      picture = {
        present: true,
        kind: image.hasAttribute('data-camera-stream') ? 'stream' : image.hasAttribute('data-camera-still') ? 'stored_frame' : 'other',
        src: address.pathname + address.search,
        complete: image.complete,
        natural_width: image.naturalWidth,
        natural_height: image.naturalHeight,
        pixels: pixels(image),
      };
    }
    return {
      role: panel.getAttribute('data-camera-panel'),
      chip: text('[data-camera-chip]'),
      note: text('[data-camera-note]'),
      cover: text('[data-camera-cover]'),
      metadata: text('[data-camera-metadata]'),
      image: picture,
    };
  };

  const textOf = (selector) => {
    const node = document.querySelector(selector);
    return rendered(node) ? node.innerText.trim() : null;
  };

  let samples = 0;
  const measure = () => {
    samples += 1;
    const result = {
      probe: 'console-dom-probe',
      version: 1,
      at: new Date().toISOString(),
      samples,
      path: location.pathname,
      controls: Object.fromEntries(CONTROLS.map((name) => [name, control(document.querySelector(`[data-control="${name}"]`))])),
      candidates: Array.from(document.querySelectorAll('[data-control="candidate"]')).map((select) => ({
        ...control(select),
        value: select.value,
        options: Array.from(select.options).map((option) => option.value),
      })),
      cameras: Array.from(document.querySelectorAll('[data-camera-panel]')).map(camera),
      receipt: textOf('[data-receipt]'),
      activation_readback: textOf('[data-activation-readback]'),
      drawer_title: textOf('[data-drawer-title]'),
    };

    const line = `CONSOLE_DOM_PROBE ${JSON.stringify(result)}`;
    let out = document.getElementById('console-dom-probe');
    if (out === null) {
      out = document.createElement('pre');
      out.id = 'console-dom-probe';
      // RENDERED, so innerText includes it, and clipped to one pixel, so it covers nothing an operator reads.
      out.setAttribute('style', 'position:fixed;left:0;bottom:0;width:1px;height:1px;overflow:hidden;margin:0;white-space:pre;z-index:-1');
      document.body.appendChild(out);
    }
    out.textContent = `\n${line}\n`;
    document.title = 'CONSOLE_DOM_PROBE written';
    return JSON.stringify(result);
  };

  if (window.__consoleDomProbeTimer !== undefined) clearInterval(window.__consoleDomProbeTimer);
  window.__consoleDomProbeTimer = setInterval(measure, 1000);
  return measure();
})();
