import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';

class Element {
  attributes = new Map();
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name); }
  listeners = {}; children = []; value = ''; disabled = false; hidden = false; textContent = '';
  addEventListener(name, callback) { this.listeners[name] = callback; }
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; this.value = ''; }
}
globalThis.HTMLElement = class {
  isConnected = false;
  attachShadow() {
    const elements = new Map();
    this.shadowRoot = {
      querySelectorAll: () => [],
      querySelector: key => { if (!elements.has(key)) elements.set(key, new Element()); return elements.get(key); },
    };
  }
};
globalThis.document = { createElement: () => new Element() };
globalThis.customElements = { get: () => true };
const { HaPaneldUsbInstallPanel } = await import('../src/ha-install-panel.mjs');
const { ENGLISH_MESSAGES: { haInstall: HA_INSTALL_MESSAGES } } = await import('../src/frontend-localization.mjs');
const tick = () => new Promise(resolve => setImmediate(resolve));
const stable = { tag: 'v1.2.3', prerelease: false };
const rc = { tag: 'v1.2.4-rc1', prerelease: true };
const response = releases => new Response(JSON.stringify({ releases }), { headers: { 'Content-Type': 'application/json' } });
function fixture(fetchWithAuth) {
  const panel = new HaPaneldUsbInstallPanel();
  const hass = { user: { id: 'admin', is_admin: true }, connection: {}, auth: {}, fetchWithAuth };
  panel.hass = hass;
  panel.panel = { config: { installer_url: 'https://installer.example/' } };
  panel.isConnected = true;
  panel.connectedCallback();
  return { panel, hass, element: id => panel.shadowRoot.querySelector(`#${id}`) };
}
test('PA channel default stays implicit while explicit first RC and stable selections keep exact tags', async () => {
  const f = fixture(async () => response([rc, stable]));
  assert.equal(f.element('start').disabled, true);
  assert.equal(f.element('catalog-status').textContent, HA_INSTALL_MESSAGES.loading);
  await tick();
  assert.equal(f.element('release').value, '');
  assert.equal(f.element('start').disabled, false);
  assert.equal(f.element('release').children[0].disabled, false);
  assert.deepEqual(f.element('release').children.map(option => option.textContent), [HA_INSTALL_MESSAGES.choose, '1.2.4-rc1 (test version)', '1.2.3']);
  let opened;
  globalThis.window = { crypto: webcrypto, location: { origin: 'http://ha.example' }, addEventListener() {}, removeEventListener() {}, open(url) { opened = new URL(url); return { closed: false }; } };
  f.element('start').listeners.click();
  assert.equal(new URLSearchParams(opened.hash.slice(1)).get('rc'), '');
  f.element('cancel').listeners.click();
  await tick();
  f.element('release').value = rc.tag;
  f.element('release').listeners.change();
  f.element('start').listeners.click();
  assert.equal(new URLSearchParams(opened.hash.slice(1)).get('rc'), rc.tag);
  f.element('cancel').listeners.click();
  await tick();
  f.element('release').value = stable.tag;
  f.element('release').listeners.change();
  f.element('start').listeners.click();
  assert.equal(new URLSearchParams(opened.hash.slice(1)).get('rc'), stable.tag);
  assert.equal(f.element('release').disabled, true);
  f.panel.disconnectedCallback();
  await tick();
});
test('RC-only catalogue keeps the PA channel default and permits an explicit first RC only', async () => {
  const f = fixture(async () => response([rc]));
  await tick();
  assert.equal(f.element('release').value, '');
  assert.equal(f.element('start').disabled, false);
  f.element('release').value = 'v9.9.9-rc1';
  f.element('release').listeners.change();
  assert.equal(f.element('start').disabled, true);
  f.element('release').value = rc.tag;
  f.element('release').listeners.change();
  assert.equal(f.element('start').disabled, false);
});
test('error and empty states offer Retry and disable starting', async () => {
  const responses = [new Response('Forbidden', { status: 403 }), response([]), response([stable])];
  const f = fixture(async () => responses.shift());
  await tick();
  assert.equal(f.element('catalog-status').textContent, HA_INSTALL_MESSAGES.catalogError);
  assert.equal(f.element('retry').hidden, false);
  assert.equal(f.element('start').disabled, true);
  f.element('retry').listeners.click(); await tick();
  assert.equal(f.element('catalog-status').textContent, HA_INSTALL_MESSAGES.empty);
  assert.equal(f.element('retry').hidden, false);
  assert.equal(f.element('start').disabled, true);
  f.element('retry').listeners.click(); await tick();
  assert.equal(f.element('retry').hidden, true);
  assert.equal(f.element('start').disabled, false);
});
test('disconnect and identity changes abort pending requests and ignore late catalogues', async () => {
  const pending = [];
  const f = fixture((_, init) => new Promise(resolve => pending.push({ resolve, signal: init.signal })));
  f.panel.hass = { ...f.hass };
  assert.equal(pending.length, 1, 'ordinary state refresh does not refetch');
  f.panel.hass = { ...f.hass, user: { id: 'other', is_admin: true } };
  assert.equal(pending[0].signal.aborted, true);
  assert.equal(pending.length, 2);
  pending[0].resolve(response([stable])); await tick();
  assert.equal(f.element('start').disabled, true);
  f.panel.isConnected = false;
  f.panel.disconnectedCallback();
  assert.equal(pending[1].signal.aborted, true);
  pending[1].resolve(response([rc])); await tick();
  assert.equal(f.element('release').children.length, 0);
});
test('installer destination changes invalidate selection and non-admin users never fetch', async () => {
  let calls = 0;
  const f = fixture(async () => { calls++; return response([stable]); });
  await tick();
  f.panel.panel = { config: { installer_url: 'https://other.example/' } };
  assert.equal(f.element('start').disabled, true);
  await tick();
  assert.equal(calls, 2);
  f.panel.hass = { ...f.hass, user: { id: 'regular', is_admin: false } };
  assert.equal(calls, 2);
  assert.equal(f.element('start').disabled, true);
});
test('dev builds are labelled by name, PA channel defaults, and an explicit build keeps its exact tag', async () => {
  const feed = { tag: 'build-772', prerelease: true, name: '0.9.7-rc4 build 772' };
  const f = fixture(async () => response([stable, rc, feed]));
  await tick();
  assert.deepEqual(f.element('release').children.map(option => option.textContent),
    [HA_INSTALL_MESSAGES.choose, '1.2.3', '1.2.4-rc1 (test version)', '0.9.7-rc4 build 772 (dev build)']);
  assert.deepEqual(f.element('release').children.map(option => option.value), ['', stable.tag, rc.tag, feed.tag]);
  assert.equal(f.element('release').value, '');
  let opened;
  globalThis.window = { crypto: webcrypto, location: { origin: 'http://ha.example' }, addEventListener() {}, removeEventListener() {}, open(url) { opened = new URL(url); return { closed: false }; } };
  f.element('release').value = feed.tag;
  f.element('release').listeners.change();
  f.element('start').listeners.click();
  assert.equal(new URLSearchParams(opened.hash.slice(1)).get('rc'), feed.tag);
  f.panel.disconnectedCallback();
  await tick();
});
test('compatible feed-only catalogue retains PA channel default and exact build choice', async () => {
  const f = fixture(async () => response([{ tag: 'build-772', prerelease: true, name: '0.9.7-rc4 build 772' }]));
  await tick();
  assert.deepEqual(f.element('release').children.map(option => option.textContent),
    [HA_INSTALL_MESSAGES.choose, '0.9.7-rc4 build 772 (dev build)']);
  assert.equal(f.element('release').value, '');
  assert.equal(f.element('start').disabled, false);
});

test('picker default never invents prerelease consent when running PA policy changes', async () => {
  const requests = [];
  const posts = [];
  const listeners = new Set();
  let testing = true;
  let opened;
  const child = { closed: false, postMessage: (message) => posts.push(message) };
  globalThis.window = {
    crypto: webcrypto, location: { origin: 'http://ha.example' },
    addEventListener: (_, listener) => listeners.add(listener),
    removeEventListener: (_, listener) => listeners.delete(listener),
    open(url) { opened = new URL(url); return child; },
  };
  const f = fixture(async (url, options) => {
    if (url.endsWith('/releases')) return response([rc, stable]);
    if (url.endsWith('/apk')) return new Response('apk');
    const selection = JSON.parse(options.body);
    requests.push(selection);
    // PA allows an explicit test-panel choice, but its implicit default follows
    // the version actually running when this admission request arrives.
    const tag = selection.release_candidate ?? (testing ? rc.tag : stable.tag);
    return new Response(JSON.stringify({
      id: 'a'.repeat(32), tag, checksum: btoa('checksum'),
      checksum_signature: btoa('s'.repeat(256)), descriptor: btoa('{}'),
      descriptor_signature: btoa('d'.repeat(256)), apk_size: 3,
      apk_sha256: 'b'.repeat(64),
    }), { headers: { 'Content-Type': 'application/json' } });
  });
  await tick();
  f.element('start').listeners.click();
  const nonce = new URLSearchParams(opened.hash.slice(1)).get('nonce');
  const send = (type, extra = {}) => {
    for (const listener of listeners) listener({
      source: child, origin: 'https://installer.example',
      data: { type: `ha-paneld/usb-${type}`, nonce, ...extra },
    });
  };
  try {
    send('ready');
    await tick(); await tick();
    assert.equal(posts[0].type, 'ha-paneld/usb-bundle');
    assert.equal(posts[0].bundle.tag, rc.tag);
    send('verified');
    await tick();
    testing = false;
    send('admission', { requestId: 'c'.repeat(32), tag: rc.tag, apkSha256: 'b'.repeat(64) });
    await tick(); await tick();
    assert.deepEqual(requests, [{}, {}]);
    assert.equal(posts.at(-1).type, 'ha-paneld/usb-admission-result');
    assert.equal(posts.at(-1).admitted, false);
  } finally {
    f.panel.disconnectedCallback();
    await tick();
  }
});

for (const [signal, language] of [['de-DE', 'de'], ['cs-CZ', 'cs'], ['pt_br-u-nu-latn', 'pt-BR'], ['pt', 'en'], ['pt-PT', 'en']]) test(`${signal}: language switching preserves explicit selection and active transfer without another fetch or popup`, async () => {
  const { frontendMessages } = await import('../src/frontend-localization.mjs');
  let requests = 0, opens = 0;
  const f = fixture(async () => { requests++; return response([rc, stable]); });
  await tick();
  try {
  f.element('release').value = rc.tag;
  f.element('release').listeners.change();
  let opened;
  globalThis.window = { crypto: webcrypto, location: { origin: 'http://ha.example' }, addEventListener() {}, removeEventListener() {},
    open(url) { opens++; opened = new URL(url); return { closed: false }; } };
  f.panel.hass = { ...f.hass, language: signal };
  assert.equal(f.element('release').value, rc.tag);
  assert.equal(requests, 1);
  assert.equal(f.element('release').children[0].textContent, frontendMessages('haInstall', language).choose);
  f.element('start').listeners.click();
  assert.equal(opened.searchParams.get('lang'), language);
  assert.equal(new URLSearchParams(opened.hash.slice(1)).get('rc'), rc.tag);
  const childOptions = f.element('release').children;
  f.panel.hass = { ...f.hass, language: 'fr' };
  assert.equal(f.element('release').children, childOptions);
  assert.equal(f.element('release').value, rc.tag);
  assert.equal(f.element('release').disabled, true);
  assert.equal(f.element('cancel').disabled, false);
  assert.equal(f.element('status').textContent, frontendMessages('haInstall', 'fr').waiting);
  assert.deepEqual([requests, opens], [1, 1]);
  } finally { f.panel.disconnectedCallback(); await tick(); }
});
