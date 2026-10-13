// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// Run in the page by tools/cdp_page.mjs --script for tests/test-browser-layout.sh. Once a second it records the page
// y of each region (mission, Cake, stages, scene, wrist, timeline), the mission card's verdict and Stop's place, and
// writes them as one PROBE line into a hidden element, which the body read of cdp_page.mjs returns. It reads only.
(() => {
  const out = document.createElement('pre');
  out.style.cssText = 'position:absolute;left:0;top:0;width:1px;height:1px;overflow:hidden;margin:0';
  document.body.appendChild(out);
  const rows = [];
  const tick = () => {
    const at = {};
    for (const element of document.querySelectorAll('[data-region]')) {
      const name = element.getAttribute('data-region');
      if (!(name in at)) at[name] = Math.round(element.getBoundingClientRect().top + window.scrollY);
    }
    const verdict = document.querySelector('[data-region="mission"] [data-verdict]')?.getAttribute('data-verdict') ?? null;
    const past = document.querySelector('[data-region="mission"] [data-last-mission]') !== null;
    const stop = document.querySelector('[data-control="stop"]');
    const box = stop?.getBoundingClientRect();
    const hit = box === undefined ? null : document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    const bounded = document.querySelector('[data-bounded="mission"]');
    rows.push({
      verdict,
      past,
      at,
      innerHeight: window.innerHeight,
      readiness: document.querySelector('[data-readiness]')?.getAttribute('data-readiness') ?? null,
      stopTop: box === undefined ? null : Math.round(box.top),
      stopHit: stop !== null && hit !== null && (hit === stop || stop.contains(hit)),
      account: bounded === null ? null : {
        client: bounded.clientHeight,
        scroll: bounded.scrollHeight,
        // WHAT THE ACCOUNT HOLDS, scrolled or not: textContent reads the whole region, not only the part in view.
        did: bounded.querySelector('[data-mission-check]') !== null,
        why: bounded.querySelector('[data-cause]') !== null,
        where: bounded.querySelector('[data-where]') !== null,
        steps: bounded.querySelector('[data-steps]') !== null,
        more: document.querySelector('[data-bounded-more="mission"]') !== null,
      },
    });
    out.textContent = `PROBE ${JSON.stringify(rows)}`;
  };
  tick();
  setInterval(tick, 1000);
})();
