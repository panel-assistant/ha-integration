import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { startReleaseHandoff } from '../src/ha-release-handoff.mjs';
import { handOverThenOpen, requestSetupHandover } from '../src/release-handoff.mjs';

// A USB-installed panel is told Home Assistant set it up before the installer
// sends the browser to the panel's own setup, so its wizard skips the MQTT step.
// The installer holds no Home Assistant credential; the Home Assistant window
// that opened it relays the panel's address to Home Assistant's own endpoint.

const metadata = () => ({
  id: 'a'.repeat(32), tag: 'v1.2.3', checksum: btoa('checksum'),
  checksum_signature: btoa('s'.repeat(256)), descriptor: btoa('{}'),
  descriptor_signature: btoa('d'.repeat(256)), apk_size: 3, apk_sha256: 'b'.repeat(64),
});
const json = (value, status = 200) => new Response(JSON.stringify(value),
  { status, headers: { 'Content-Type': 'application/json' } });
const tick = () => new Promise((resolve) => setImmediate(resolve));

// The Home Assistant window, serving a verified bundle to the installer window.
async function servingOpener(handoverResponse = json({ outcome: 'handed_over' }), { verify = true } = {}) {
  const listeners = new Set();
  const calls = [];
  const posts = [];
  const responses = [json(metadata()), new Response('apk'), handoverResponse];
  const child = { closed: false, postMessage: (...args) => posts.push(args) };
  let opened;
  const windowObject = {
    crypto: webcrypto, location: { origin: 'http://ha.example:8123' },
    addEventListener: (_, fn) => listeners.add(fn), removeEventListener: (_, fn) => listeners.delete(fn),
    open: (url) => { opened = new URL(url); return child; },
  };
  const hass = { fetchWithAuth: async (...args) => {
    calls.push(args);
    const next = responses.shift();
    if (next instanceof Error) throw next;
    return next;
  } };
  const handle = startReleaseHandoff(hass, 'https://installer.example/', { windowObject });
  const nonce = new URLSearchParams(opened.hash.slice(1)).get('nonce');
  const send = (data, overrides = {}) => {
    for (const listener of listeners) {
      listener({ source: child, origin: 'https://installer.example', data: { nonce, ...data }, ...overrides });
    }
  };
  send({ type: 'ha-paneld/usb-ready' });
  await tick(); await tick();
  if (verify) { send({ type: 'ha-paneld/usb-verified' }); await handle.completion; }
  return { calls, posts, send, nonce, child, handle };
}

test('the Home Assistant window hands a verified install\'s panel to Home Assistant and answers', async () => {
  const f = await servingOpener();
  f.send({ type: 'ha-paneld/usb-handover', address: '192.168.1.20' });
  await tick(); await tick();
  assert.equal(f.calls.length, 3);
  const [url, init] = f.calls[2];
  assert.equal(url, '/api/panel_assistant/usb/handover');
  assert.equal(init.method, 'POST');
  assert.equal(init.redirect, 'error');
  assert.deepEqual(JSON.parse(init.body), { address: '192.168.1.20' });
  const replies = f.posts.slice(1);
  assert.deepEqual(replies.map(([message]) => message), [
    { type: 'ha-paneld/usb-handover-accepted', nonce: f.nonce },
    { type: 'ha-paneld/usb-handover-result', nonce: f.nonce, outcome: 'handed_over' },
  ]);
  assert.ok(replies.every(([, origin]) => origin === 'https://installer.example'));
  f.handle.cancel();
});

test('a refused or failed relay still answers, with a code the support log can show', async () => {
  for (const [response, outcome] of [
    [json({ error: 'browser_handover_invalid_request' }, 400), 'browser_handover_invalid_request'],
    [new Error('offline'), 'request_failed'],
    [new Response('nope', { status: 502 }), 'http_502'],
  ]) {
    const f = await servingOpener(response);
    f.send({ type: 'ha-paneld/usb-handover', address: '192.168.1.20' });
    await tick(); await tick();
    assert.deepEqual(f.posts.at(-1)[0], { type: 'ha-paneld/usb-handover-result', nonce: f.nonce, outcome });
    f.handle.cancel();
  }
});

test('nothing is relayed for another window, another nonce, junk, or before the bundle is verified', async () => {
  const unverified = await servingOpener(undefined, { verify: false });
  unverified.send({ type: 'ha-paneld/usb-handover', address: '192.168.1.20' });
  await tick();
  assert.equal(unverified.calls.length, 2, 'no handover before the installer verified the bundle');
  unverified.handle.completion.catch(() => {});
  unverified.handle.cancel();

  const f = await servingOpener();
  f.send({ type: 'ha-paneld/usb-handover', address: '192.168.1.20' }, { source: {} });
  f.send({ type: 'ha-paneld/usb-handover', address: '192.168.1.20' }, { origin: 'https://evil.example' });
  f.send({ type: 'ha-paneld/usb-handover', address: '192.168.1.20', nonce: 'f'.repeat(32) });
  f.send({ type: 'ha-paneld/usb-handover', address: 'evil.example' });
  f.send({ type: 'ha-paneld/usb-handover', address: '192.168.1.20', url: 'http://x' });
  await tick();
  assert.equal(f.calls.length, 2);
  assert.equal(f.posts.length, 1);
  f.handle.cancel();
  f.send({ type: 'ha-paneld/usb-handover', address: '192.168.1.20' });
  await tick();
  assert.equal(f.calls.length, 2, 'a cancelled transfer relays nothing');
});

// The installer window, opened by Home Assistant with a handoff fragment.
function installerWindow() {
  const listeners = new Set();
  const sent = [];
  const opener = { closed: false, postMessage: (...args) => sent.push(args) };
  const windowObject = {
    opener,
    addEventListener: (_, fn) => listeners.add(fn), removeEventListener: (_, fn) => listeners.delete(fn),
  };
  const options = { origin: 'http://ha.example:8123', nonce: 'c'.repeat(32) };
  const reply = (data, overrides = {}) => {
    for (const listener of [...listeners]) {
      listener({ source: opener, origin: options.origin, data: { nonce: options.nonce, ...data }, ...overrides });
    }
  };
  return { windowObject, options, sent, reply, listeners, opener };
}

test('the installer asks its Home Assistant window and returns the panel\'s outcome', async () => {
  const w = installerWindow();
  const pending = requestSetupHandover({ windowObject: w.windowObject, options: w.options, address: '192.168.1.20' });
  assert.deepEqual(w.sent, [[
    { type: 'ha-paneld/usb-handover', nonce: w.options.nonce, address: '192.168.1.20' }, w.options.origin,
  ]]);
  w.reply({ type: 'ha-paneld/usb-handover-result', outcome: 'forged' }, { source: {} });
  w.reply({ type: 'ha-paneld/usb-handover-result', outcome: 'forged' }, { origin: 'https://evil.example' });
  w.reply({ type: 'ha-paneld/usb-handover-result', outcome: 'forged', nonce: 'd'.repeat(32) });
  w.reply({ type: 'ha-paneld/usb-handover-accepted' });
  w.reply({ type: 'ha-paneld/usb-handover-result', outcome: 'handed_over' });
  assert.equal(await pending, 'handed_over');
  assert.equal(w.listeners.size, 0);
});

test('an older Home Assistant that never answers costs only a short wait', async () => {
  const w = installerWindow();
  const outcome = await requestSetupHandover({ windowObject: w.windowObject, options: w.options,
    address: '192.168.1.20', ackMs: 5, resultMs: 10000 });
  assert.equal(outcome, 'no_reply');
});

test('an acknowledged handover waits for the panel, but not forever', async () => {
  const w = installerWindow();
  const pending = requestSetupHandover({ windowObject: w.windowObject, options: w.options,
    address: '192.168.1.20', ackMs: 5, resultMs: 30 });
  w.reply({ type: 'ha-paneld/usb-handover-accepted' });
  await new Promise((resolve) => setTimeout(resolve, 15));
  assert.equal(w.listeners.size, 1, 'the acknowledgement extends the wait past the first deadline');
  assert.equal(await pending, 'no_result');
});

test('without a Home Assistant window there is nobody to ask', async () => {
  const w = installerWindow();
  assert.equal(await requestSetupHandover({ windowObject: { ...w.windowObject, opener: null },
    options: w.options, address: '192.168.1.20' }), 'no_home_assistant_window');
  assert.equal(await requestSetupHandover({ windowObject: w.windowObject, options: null,
    address: '192.168.1.20' }), 'no_home_assistant_window');
  assert.equal(w.sent.length, 0);
});

test('setup opens only after the handover has answered, and the outcome is logged', async () => {
  const order = [];
  let answer;
  const run = handOverThenOpen('http://192.168.1.20:8888/setup', {
    handover: (address) => { order.push(`handover:${address}`); return new Promise((resolve) => { answer = resolve; }); },
    support: (label, value) => order.push(`${label}:${value}`),
    open: (url) => order.push(`open:${url}`),
  });
  await tick();
  assert.deepEqual(order, ['handover:192.168.1.20'], 'setup is not opened while the handover is pending');
  answer('handed_over');
  await run;
  assert.deepEqual(order, ['handover:192.168.1.20', 'Setup handover:handed_over',
    'open:http://192.168.1.20:8888/setup']);
});

test('a failed handover never blocks setup, and no address means no handover', async () => {
  const order = [];
  await handOverThenOpen('http://192.168.1.20:8888/setup', {
    handover: () => { throw new Error('boom'); },
    support: (label, value) => order.push(`${label}:${value}`),
    open: (url) => order.push(`open:${url}`),
  });
  assert.deepEqual(order, ['Setup handover:failed', 'open:http://192.168.1.20:8888/setup']);
  const none = [];
  await handOverThenOpen(null, {
    handover: () => { none.push('handover'); },
    support: () => none.push('support'),
    open: (url) => none.push(`open:${url}`),
  });
  assert.deepEqual(none, ['open:null']);
});
