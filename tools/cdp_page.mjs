// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// Read a console page the way a person would: in a real browser, with the real build, against a real API.
//
// This exists because the console's own suite cannot see a rendering defect. Nothing in apps/console-web renders a
// component, so a change to JSX is invisible to every test in it. One such change was written into a card that nothing
// imports, and the whole suite stayed green while the screen was unchanged. Loading the built page is what caught it.
//
// It drives a DEDICATED headless Chrome over CDP, with its own profile directory and its own debugging port, and never
// the operator's browser. Chrome's own --screenshot cannot be used here: the page holds an open event stream, so the
// virtual time budget never elapses and the browser hangs. Node 22's global WebSocket is enough to speak CDP directly.
//
// usage: node tools/cdp_page.mjs --url URL [--wait TEXT] [--screenshot FILE] [--width N] [--height N] [--port N]
// It prints one JSON line of findings, then the page's text. Exit status 0 when the page loaded and, if --wait was
// given, the text appeared; 1 otherwise, so a shell suite can judge it.

import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CHROME = process.env.KC_CHROME || '/opt/google/chrome/chrome';

function arg(name, fallback = null) {
  const at = process.argv.indexOf(`--${name}`);
  return at === -1 || at === process.argv.length - 1 ? fallback : process.argv[at + 1];
}

const url = arg('url');
const waitText = arg('wait');
const screenshot = arg('screenshot');
const width = Number(arg('width', '1440'));
const height = Number(arg('height', '900'));
const port = Number(arg('port', '9333'));
const settleMs = Number(arg('settle', '40000'));
// A file of JavaScript to run in the page ONCE the wait text has appeared, before the body is read back. It is how a
// test drives a control the page only offers after it has loaded: open a drawer, choose a run, press a button. The
// script is a path on disk, not an argument, so nothing quoted on a command line has to survive two shells.
const scriptPath = arg('script');
const afterMs = Number(arg('after', '2500'));

if (url === null) {
  process.stderr.write('cdp_page: --url is required\n');
  process.exit(2);
}

const profile = mkdtempSync(join(tmpdir(), 'kc-cdp-'));
const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', `--window-size=${width},${height}`, 'about:blank',
], { stdio: ['ignore', 'ignore', 'pipe'] });
chrome.stderr.on('data', () => {});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function done(code, payload, text) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
  if (text !== undefined) process.stdout.write(`${text}\n`);
  try { chrome.kill('SIGTERM'); } catch { /* already gone */ }
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* best effort */ }
  process.exit(code);
}

async function firstPage() {
  for (let attempt = 0; attempt < 80; attempt++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = list.find((target) => target.type === 'page');
      if (page !== undefined) return page;
    } catch { /* chrome not listening yet */ }
    await sleep(250);
  }
  done(1, { ok: false, reason: 'chrome_never_exposed_a_page' });
}

let nextId = 0;
function send(socket, method, params = {}) {
  const id = ++nextId;
  socket.send(JSON.stringify({ id, method, params }));
  return id;
}

function reply(socket, wantId) {
  return new Promise((resolve, reject) => {
    const onMessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.id !== wantId) return;
      socket.removeEventListener('message', onMessage);
      resolve(data);
    };
    socket.addEventListener('message', onMessage);
    setTimeout(() => { socket.removeEventListener('message', onMessage); reject(new Error(`cdp timeout on ${wantId}`)); }, 30000);
  });
}

async function evaluate(socket, expression) {
  const answer = await reply(socket, send(socket, 'Runtime.evaluate', { expression, returnByValue: true }));
  return answer.result?.result?.value;
}

const page = await firstPage();
const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve) => socket.addEventListener('open', resolve, { once: true }));

await reply(socket, send(socket, 'Page.enable'));
await reply(socket, send(socket, 'Runtime.enable'));
await reply(socket, send(socket, 'Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }));
await reply(socket, send(socket, 'Page.navigate', { url }));

// An open event stream means "load" is not a finish line, so the DOM is polled for the text the caller is waiting on.
let found = waitText === null;
const deadline = Date.now() + settleMs;
while (Date.now() < deadline) {
  await sleep(500);
  const text = await evaluate(socket, 'document.body ? document.body.innerText : ""');
  if (waitText === null) { found = typeof text === 'string' && text.length > 0; if (found) break; continue; }
  if (typeof text === 'string' && text.includes(waitText)) { found = true; break; }
}

if (scriptPath !== null) {
  const source = readFileSync(scriptPath, 'utf8');
  await evaluate(socket, source);
  await sleep(afterMs);  // the page answers a fetch and re-renders; the body is read after that, not during it
}

if (screenshot !== null) {
  const shot = await reply(socket, send(socket, 'Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }));
  writeFileSync(screenshot, Buffer.from(shot.result.data, 'base64'));
}

const body = await evaluate(socket, 'document.body ? document.body.innerText : ""');
const widths = await evaluate(socket, 'JSON.stringify([document.documentElement.scrollWidth, document.documentElement.clientWidth])');
const [scrollWidth, clientWidth] = JSON.parse(widths ?? '[0,0]');
done(found ? 0 : 1, { ok: found, url, wait: waitText, scrollWidth, clientWidth, overflows: scrollWidth > clientWidth }, body);
