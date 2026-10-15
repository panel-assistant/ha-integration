import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { AdbAuthenticationProcessor, AdbCommand } from '@yume-chan/adb';
import { startReleaseHandoff } from '../src/ha-release-handoff.mjs';
import { parsePanelAssistantKey, requestPanelAssistantKey } from '../src/release-handoff.mjs';
import { ensureNetworkAdb, panelAssistantKeyAuthenticator } from '../src/panel-assistant-adb.mjs';

// A USB install leaves Panel Assistant able to reach the panel over network ADB:
// the owner's one Allow approves Panel Assistant's own key, and adbd is left
// listening on the port Panel Assistant connects to.

// Android's adbkey.pub shape: 524 bytes starting with the 64-word modulus length.
const raw = new Uint8Array(524);
new DataView(raw.buffer).setUint32(0, 64, true);
raw.fill(7, 4);
const KEY = `${btoa(String.fromCharCode(...raw))} ha-paneld@home-assistant`;

const json = (value, status = 200) => new Response(JSON.stringify(value),
  { status, headers: { 'Content-Type': 'application/json' } });
const tick = () => new Promise(resolve => setImmediate(resolve));
const metadata = () => ({
  id: 'a'.repeat(32), tag: 'v1.2.3', checksum: btoa('checksum'),
  checksum_signature: btoa('s'.repeat(256)), descriptor: btoa('{}'),
  descriptor_signature: btoa('d'.repeat(256)), apk_size: 3, apk_sha256: 'b'.repeat(64),
});

// The Home Assistant window serving a bundle to the installer window.
async function servingOpener(keyResponse, { verify = true } = {}) {
  const listeners = new Set();
  const calls = [];
  const posts = [];
  const responses = [json(metadata()), new Response('apk'), keyResponse];
  const child = { closed: false, postMessage: (...args) => posts.push(args) };
  let opened;
  const windowObject = {
    crypto: webcrypto, location: { origin: 'http://ha.example:8123' },
    addEventListener: (_, fn) => listeners.add(fn), removeEventListener: (_, fn) => listeners.delete(fn),
    open: url => { opened = new URL(url); return child; },
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
  return { calls, posts, send, nonce, handle };
}

test('the Home Assistant window hands the installer Panel Assistant’s public key', async () => {
  const f = await servingOpener(json({ public_key: KEY }));
  f.send({ type: 'ha-paneld/usb-adb-key' });
  await tick(); await tick();
  assert.equal(f.calls[2][0], '/api/panel_assistant/usb/adb_key');
  assert.equal(f.calls[2][1].method, 'GET');
  assert.deepEqual(f.posts.at(-1), [{ type: 'ha-paneld/usb-adb-key-result', nonce: f.nonce, key: KEY },
    'https://installer.example']);
  f.handle.cancel();
});

test('without a key from Home Assistant the installer is told so, and nothing is sent before verification', async () => {
  for (const response of [json({ error: 'browser_adb_key_unavailable' }, 503), new Error('offline')]) {
    const f = await servingOpener(response);
    f.send({ type: 'ha-paneld/usb-adb-key' });
    await tick(); await tick();
    assert.deepEqual(f.posts.at(-1)[0], { type: 'ha-paneld/usb-adb-key-result', nonce: f.nonce, key: null });
    f.handle.cancel();
  }
  const unverified = await servingOpener(json({ public_key: KEY }), { verify: false });
  unverified.send({ type: 'ha-paneld/usb-adb-key' });
  unverified.send({ type: 'ha-paneld/usb-adb-key' }, { origin: 'https://evil.example' });
  await tick();
  assert.equal(unverified.calls.length, 2);
  unverified.handle.completion.catch(() => {});
  unverified.handle.cancel();
});

// The installer window, opened by Home Assistant with a handoff fragment.
function installerWindow() {
  const listeners = new Set();
  const sent = [];
  const opener = { closed: false, postMessage: (...args) => sent.push(args) };
  const windowObject = { opener,
    addEventListener: (_, fn) => listeners.add(fn), removeEventListener: (_, fn) => listeners.delete(fn) };
  const options = { origin: 'http://ha.example:8123', nonce: 'c'.repeat(32) };
  const reply = (data, overrides = {}) => {
    for (const listener of [...listeners]) {
      listener({ source: opener, origin: options.origin, data: { nonce: options.nonce, ...data }, ...overrides });
    }
  };
  return { windowObject, options, sent, reply, listeners };
}

test('the installer takes only a well-formed key from its own Home Assistant window', async () => {
  const w = installerWindow();
  const pending = requestPanelAssistantKey({ windowObject: w.windowObject, options: w.options });
  assert.deepEqual(w.sent, [[{ type: 'ha-paneld/usb-adb-key', nonce: w.options.nonce }, w.options.origin]]);
  w.reply({ type: 'ha-paneld/usb-adb-key-result', key: 'FORGED' }, { origin: 'https://evil.example' });
  w.reply({ type: 'ha-paneld/usb-adb-key-result', key: KEY });
  assert.equal(await pending, KEY);
  assert.equal(w.listeners.size, 0);

  for (const key of [null, 'FORGED', KEY.replace(' ha-paneld', ''), KEY.replace('QAAAA', 'QQAAA'), `${KEY}\n`]) {
    const bad = installerWindow();
    const result = requestPanelAssistantKey({ windowObject: bad.windowObject, options: bad.options });
    bad.reply({ type: 'ha-paneld/usb-adb-key-result', key });
    assert.equal(await result, null, String(key));
  }
  assert.equal(parsePanelAssistantKey(KEY), KEY);
});

test('an older Home Assistant that never answers leaves the browser’s own key in use', async () => {
  const w = installerWindow();
  assert.equal(await requestPanelAssistantKey({ windowObject: w.windowObject, options: w.options, timeoutMs: 5 }), null);
  assert.equal(await requestPanelAssistantKey({ windowObject: {}, options: w.options }), null);
});

test('the panel’s first token is answered with Panel Assistant’s key and nothing is signed', async () => {
  const store = { iterateKeys() { throw new Error('the browser key was used'); }, generateKey() { throw new Error('a browser key was made'); } };
  const processor = new AdbAuthenticationProcessor([panelAssistantKeyAuthenticator(KEY)], store);
  const reply = await processor.process({ command: AdbCommand.Auth, arg0: 1, arg1: 0, payload: new Uint8Array(20) });
  assert.equal(reply.command, AdbCommand.Auth);
  assert.equal(reply.arg0, 3, 'an RSA public key, for the owner to approve');
  assert.equal(new TextDecoder().decode(reply.payload), `${KEY}\0`);
  // A second token means the owner refused: nothing else is offered.
  await assert.rejects(processor.process({ command: AdbCommand.Auth, arg0: 1, arg1: 0, payload: new Uint8Array(20) }));
});

// adbd's properties, and the one service that restarts it on the network.
function panelAdb(props, reply = 'restarting in TCP mode port: 5555\n') {
  const services = [];
  return { services, adb: {
    getProp: async name => props[name] ?? '',
    createSocketAndWait: async service => { services.push(service); return reply; },
  } };
}

test('network ADB is turned on last when it is off, and left alone when it is on', async () => {
  for (const props of [{ 'service.adb.tcp.port': '5555' }, { 'persist.adb.tcp.port': '5555' },
    { 'service.adb.listen_addrs': 'tcp:5555,vsock:5555' }]) {
    const p = panelAdb(props);
    assert.equal(await ensureNetworkAdb(p.adb, { beforeRestart: () => assert.fail('restarted adbd') }), 'already_on');
    assert.deepEqual(p.services, []);
  }
  for (const props of [{}, { 'service.adb.tcp.port': '0' }, { 'service.adb.tcp.port': '5556' },
    { 'service.adb.tcp.port': '5556', 'persist.adb.tcp.port': '5555' }]) {
    const p = panelAdb(props);
    const steps = [];
    p.adb.createSocketAndWait = async service => { steps.push(service); return 'restarting in TCP mode port: 5555\n'; };
    assert.equal(await ensureNetworkAdb(p.adb, { beforeRestart: () => steps.push('beforeRestart') }), 'turned_on');
    assert.deepEqual(steps, ['beforeRestart', 'tcpip:5555'], JSON.stringify(props));
  }
});

test('a panel that refuses network ADB leaves the install finished', async () => {
  const refused = panelAdb({}, 'error: not allowed\n');
  assert.equal(await ensureNetworkAdb(refused.adb), 'failed');
  const unreadable = { getProp: async () => { throw new Error('gone'); } };
  assert.equal(await ensureNetworkAdb(unreadable), 'failed');
});
