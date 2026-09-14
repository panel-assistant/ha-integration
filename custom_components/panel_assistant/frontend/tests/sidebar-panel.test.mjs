import test from 'node:test';
import assert from 'node:assert/strict';

// A minimal shadow DOM: fixed markup is parsed for tags, ids and data-message, and every
// innerHTML assignment is recorded so a test can prove panel text never reaches markup.
const markup = [];
class Element {
  constructor(tag) { this.tagName = tag; this.attributes = new Map(); this.listeners = {}; this.children = []; this.dataset = {}; this.hidden = false; this.textContent = ''; this.value = ''; }
  set innerHTML(html) { markup.push(html); }
  addEventListener(name, fn) { (this.listeners[name] ??= []).push(fn); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  removeAttribute(name) { this.attributes.delete(name); }
  append(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  fire(name, event = {}) { for (const fn of this.listeners[name] ?? []) fn(event); }
}
globalThis.HTMLElement = class {
  isConnected = false; events = [];
  dispatchEvent(event) { this.events.push(event); return true; }
  attachShadow() {
    const elements = [];
    this.shadowRoot = {
      set innerHTML(html) {
        markup.push(html);
        for (const [, tag, attrs] of html.replace(/<style>[^]*?<\/style>/, '').matchAll(/<([a-z][a-z0-9]*)([^>]*)>/g)) {
          const element = new Element(tag);
          for (const [, name, value] of attrs.matchAll(/([a-z-]+)="([^"]*)"/g)) {
            if (name === 'data-message') element.dataset.message = value; else element.setAttribute(name, value);
          }
          elements.push(element);
        }
      },
      querySelectorAll: selector => selector === '[data-message]' ? elements.filter(e => e.dataset.message) : [],
      querySelector: selector => elements.find(e => `#${e.getAttribute('id')}` === selector) ?? assert.fail(`missing ${selector}`),
    };
  }
};
globalThis.document = { createElement: tag => new Element(tag) };
globalThis.customElements = { get() {}, define() {} };
const store = new Map();
globalThis.localStorage = { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) };
const pushed = [];
globalThis.history = { pushState: (...args) => pushed.push(args) };
globalThis.window = new EventTarget();
let intervals = [];
globalThis.setInterval = (fn, ms) => { const handle = { fn, ms, cleared: false }; intervals.push(handle); return handle; };
globalThis.clearInterval = handle => { if (handle) handle.cleared = true; };

const { SIDEBAR_MESSAGES, SELECTION_KEY, parsePanels, embedToken, versionText, PanelAssistantSidebar } = await import('../src/sidebar-panel.mjs');

const tick = () => new Promise(resolve => setImmediate(resolve));
const TOKEN = 'a'.repeat(43);
const TOKEN2 = 'b'.repeat(43);
const url = token => `/api/panel_assistant/embed/${token}/`;
const row = (entry_id, state = 'reachable', title = entry_id) => ({ entry_id, title, state });

function fakeHass({ panels = [row('one'), row('two')], admin = true, language = 'en', dark = false } = {}) {
  const connection = {
    subscriptions: [], listeners: new Map(),
    addEventListener(name, fn) { this.listeners.set(name, fn); },
    removeEventListener(name, fn) { if (this.listeners.get(name) === fn) this.listeners.delete(name); },
    subscribeMessage(callback, message, options) {
      const sub = { callback, message, options, unsubscribed: 0 };
      sub.promise = new Promise((resolve, reject) => { sub.resolve = resolve; sub.reject = reject; });
      sub.resolve(() => { sub.unsubscribed++; return Promise.reject(Error('gone')); });
      this.subscriptions.push(sub);
      return sub.promise;
    },
  };
  const hass = { user: { id: 'u', is_admin: admin }, language, themes: { darkMode: dark }, connection, calls: 0, panels };
  hass.callWS = async message => { assert.deepEqual(message, { type: 'panel_assistant/embed_panels' }); hass.calls++; return { panels: hass.panels }; };
  return hass;
}

async function mount(hass, narrow = false) {
  intervals = [];
  const panel = new PanelAssistantSidebar();
  panel.hass = hass; panel.narrow = narrow;
  panel.isConnected = true; panel.connectedCallback(); await tick();
  const $ = selector => panel.shadowRoot.querySelector(selector);
  return { panel, $, subs: hass.connection.subscriptions };
}

test('panel list parser keeps the display contract and rejects anything else', () => {
  assert.deepEqual(parsePanels({ panels: [{ ...row('A_1-b'), extra: 'drop' }] }), [row('A_1-b')]);
  const bad = { notArray: { panels: { length: 0, map: () => [] } }, tooMany: { panels: Array.from({ length: 201 }, (_, i) => row(`p${i}`)) },
    state: { panels: [row('x', 'online')] }, duplicate: { panels: [row('x'), row('x')] }, title: { panels: [{ ...row('x'), title: 7 }] },
    entryId: { panels: [row('../x')] }, longTitle: { panels: [row('x', 'reachable', 'x'.repeat(257))] } };
  for (const [name, value] of Object.entries(bad)) assert.throws(() => parsePanels(value), undefined, name);
});

test('only the proxy root URL yields a token', () => {
  assert.equal(embedToken(url(TOKEN)), TOKEN);
  for (const value of [`https://evil.example${url(TOKEN)}`, url('a'.repeat(42)), `${url(TOKEN)}x`, `/api/other/embed/${TOKEN}/`, null]) {
    assert.equal(embedToken(value), null, String(value));
  }
});

test('copy is keyed, markup carries no text and the frame is a titled, unsandboxed iframe', async () => {
  markup.length = 0;
  const { $ } = await mount(fakeHass());
  assert.ok(Object.isFrozen(SIDEBAR_MESSAGES));
  assert.doesNotMatch(markup[0].replace(/<style>[^]*?<\/style>/, ''), />\s*[^<\s]/, 'fixed markup contains no copy');
  assert.equal($('#add').textContent, SIDEBAR_MESSAGES.addPanel);
  assert.equal($('#settings').textContent, SIDEBAR_MESSAGES.integrationSettings);
  assert.equal($('#frame').getAttribute('title'), SIDEBAR_MESSAGES.frameTitle);
  assert.equal($('#frame').getAttribute('sandbox'), null);
});

test('a non-administrator sees the admin message and no WebSocket call is made', async () => {
  const hass = fakeHass({ admin: false });
  const { $, subs } = await mount(hass);
  intervals[0].fn(); await tick();
  assert.equal($('#status').textContent, SIDEBAR_MESSAGES.admin);
  assert.equal(subs.length, 0);
  assert.equal(hass.calls, 0);
});

test('panel titles render as text and the first panel opens a session', async () => {
  markup.length = 0;
  const evil = '<img src=x onerror=alert(1)>';
  const hass = fakeHass({ panels: [row('one', 'reachable', evil), row('two', 'unreachable')], dark: true, language: 'de' });
  const { $, subs } = await mount(hass);
  const options = $('#panels').children;
  assert.equal(options[0].textContent, `${evil} (${SIDEBAR_MESSAGES.reachable})`);
  assert.equal(options[1].textContent, `two (${SIDEBAR_MESSAGES.unreachable})`);
  assert.ok(!markup.some(html => html.includes(evil)), 'a title never reaches innerHTML');
  assert.deepEqual(subs.map(s => [s.message, s.options]), [[
    { type: 'panel_assistant/embed_session', entry_id: 'one', language: 'de', theme: 'dark' }, { resubscribe: false }]]);
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  assert.equal($('#frame').getAttribute('src'), url(TOKEN));
  assert.equal($('#status').hidden, true);
});

test('an opened event with a foreign URL never loads the frame', async () => {
  const { $, subs } = await mount(fakeHass());
  subs[0].callback({ kind: 'opened', url: 'https://evil.example/' });
  assert.equal($('#frame').getAttribute('src'), null);
  assert.equal($('#status').textContent, SIDEBAR_MESSAGES.failed);
});

test('a reconnect resumes the token and reloads the frame only for a different URL', async () => {
  const hass = fakeHass();
  const { $, subs } = await mount(hass);
  const frame = $('#frame'); let sets = 0;
  const setAttribute = frame.setAttribute.bind(frame);
  frame.setAttribute = (name, value) => { if (name === 'src') sets++; setAttribute(name, value); };
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  hass.connection.listeners.get('ready')(); await tick();
  assert.equal(subs[1].message.resume, TOKEN);
  assert.equal(subs[0].unsubscribed, 0, 'a dead subscription is not unsubscribed on the new socket');
  subs[1].callback({ kind: 'opened', url: url(TOKEN) });
  subs[0].callback({ kind: 'opened', url: url(TOKEN2) });
  assert.equal(sets, 1);
  subs[1].callback({ kind: 'opened', url: url(TOKEN2) });
  assert.equal(frame.getAttribute('src'), url(TOKEN2));
});

test('a closed session clears the frame, refreshes the list and reopens once reachable', async () => {
  const hass = fakeHass();
  const { $, subs } = await mount(hass);
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  hass.panels = [row('one', 'not_loaded'), row('two')];
  subs[0].callback({ kind: 'closed', reason: 'entry_unloaded' }); await tick();
  assert.equal($('#frame').getAttribute('src'), null);
  assert.equal(subs.length, 1);
  assert.equal(hass.calls, 2);
  assert.equal($('#status').textContent, SIDEBAR_MESSAGES.closed);
  hass.panels = [row('one'), row('two')];
  intervals[0].fn(); await tick();
  assert.equal(subs.length, 2);
  assert.equal(subs[1].message.resume, undefined);
});

test('a rejected subscription shows a message and only the list refresh retries', async () => {
  const hass = fakeHass();
  hass.connection.subscribeMessage = function (callback, message) {
    const sub = { callback, message }; this.subscriptions.push(sub);
    return Promise.reject({ code: 'not_loaded', message: 'no' });
  };
  const { $, subs } = await mount(hass);
  assert.equal($('#status').textContent, SIDEBAR_MESSAGES.notLoadedBody);
  await tick(); await tick();
  assert.equal(subs.length, 1);
  intervals[0].fn(); await tick();
  assert.equal(subs.length, 2);
});

test('selection is remembered, survives broken storage and restarts the session', async () => {
  store.set(SELECTION_KEY, 'two');
  const hass = fakeHass();
  const { $, subs } = await mount(hass);
  assert.equal(subs[0].message.entry_id, 'two');
  $('#panels').fire('change', { target: { value: 'one' } }); await tick();
  assert.equal(store.get(SELECTION_KEY), 'one');
  assert.equal(subs[0].unsubscribed, 1);
  assert.equal(subs[1].message.entry_id, 'one');
  store.set(SELECTION_KEY, 'missing');
  assert.equal((await mount(fakeHass())).subs[0].message.entry_id, 'one');
  const saved = globalThis.localStorage;
  globalThis.localStorage = { getItem() { throw Error('denied'); }, setItem() { throw Error('denied'); } };
  try {
    const mounted = await mount(fakeHass());
    assert.equal(mounted.subs[0].message.entry_id, 'one');
    mounted.$('#panels').fire('change', { target: { value: 'two' } }); await tick();
    assert.equal(mounted.subs[1].message.entry_id, 'two');
  } finally { globalThis.localStorage = saved; store.clear(); }
});

test('language or theme changes reopen without resume; other hass updates do not', async () => {
  const hass = fakeHass();
  const { panel, subs } = await mount(hass);
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  panel.hass = { ...hass, states: {} }; await tick();
  assert.equal(subs.length, 1);
  assert.equal(hass.calls, 1);
  panel.hass = { ...hass, language: 'fr' }; await tick();
  assert.equal(subs[0].unsubscribed, 1);
  assert.deepEqual([subs[1].message.language, subs[1].message.resume], ['fr', undefined]);
  panel.hass = { ...hass, language: 'fr', themes: { darkMode: true } }; await tick();
  assert.equal(subs[2].message.theme, 'dark');
});

test('unreachable and not-loaded panels open no session; a refresh to reachable does', async () => {
  const hass = fakeHass({ panels: [row('one', 'unreachable')] });
  const { $, subs } = await mount(hass);
  assert.equal($('#status').textContent, SIDEBAR_MESSAGES.unreachableBody);
  hass.panels = [row('one', 'not_loaded')]; intervals[0].fn(); await tick();
  assert.equal($('#status').textContent, SIDEBAR_MESSAGES.notLoadedBody);
  assert.equal(subs.length, 0);
  assert.equal(intervals[0].ms, 30000);
  hass.panels = [row('one')]; intervals[0].fn(); await tick();
  assert.equal(subs.length, 1);
});

test('empty and invalid lists show their messages', async () => {
  const { $ } = await mount(fakeHass({ panels: [] }));
  assert.equal($('#status').textContent, SIDEBAR_MESSAGES.empty);
  assert.equal($('#add').hidden, false);
  const { $: $$ } = await mount(fakeHass({ panels: [row('x', 'online')] }));
  assert.equal($$('#status').textContent, SIDEBAR_MESSAGES.failed);
});

test('disconnect unsubscribes, stops the timer and listener, and ignores late results', async () => {
  const hass = fakeHass();
  const { panel, subs } = await mount(hass);
  const timer = intervals[0];
  panel.isConnected = false; panel.disconnectedCallback(); await tick();
  assert.equal(subs[0].unsubscribed, 1);
  assert.equal(timer.cleared, true);
  assert.equal(hass.connection.listeners.size, 0);
  let release;
  const late = fakeHass();
  late.callWS = () => new Promise(resolve => { release = resolve; });
  const second = await mount(late);
  second.panel.isConnected = false; second.panel.disconnectedCallback();
  release({ panels: [row('one')] }); await tick();
  assert.equal(second.subs.length, 0);
});

test('a connection change moves the ready listener and reloads the list', async () => {
  const hass = fakeHass();
  const { panel, subs } = await mount(hass);
  const next = fakeHass();
  panel.hass = next; await tick();
  assert.equal(hass.connection.listeners.size, 0);
  assert.equal(next.connection.listeners.size, 1);
  assert.equal(subs[0].unsubscribed, 1);
  assert.equal(next.calls, 1);
});

test('menu toggles only when narrow and links navigate inside Home Assistant', async () => {
  const { panel, $ } = await mount(fakeHass(), true);
  assert.equal($('#menu').hidden, false);
  panel.narrow = false;
  assert.equal($('#menu').hidden, true);
  $('#menu').fire('click');
  assert.deepEqual([panel.events[0].type, panel.events[0].bubbles, panel.events[0].composed], ['hass-toggle-menu', true, true]);
  let changed;
  window.addEventListener('location-changed', event => { changed = event.detail; }, { once: true });
  let prevented = 0;
  $('#add').fire('click', { button: 0, preventDefault: () => prevented++ });
  assert.deepEqual(pushed.at(-1), [null, '', '/config/integrations/dashboard/add?domain=panel_assistant']);
  assert.deepEqual(changed, { replace: false });
  assert.equal(prevented, 1);
  assert.equal($('#settings').getAttribute('href'), '/config/integrations/integration/panel_assistant');
  $('#settings').fire('click', { button: 0, ctrlKey: true, preventDefault: () => prevented++ });
  assert.equal(prevented, 1);
});

test('a refresh lost in transit keeps the session so a reconnect resumes it', async () => {
  const hass = fakeHass();
  const { $, subs } = await mount(hass);
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  hass.callWS = async () => { throw { code: 3, message: 'Connection lost' }; };
  intervals[0].fn(); await tick();
  assert.equal($('#frame').getAttribute('src'), url(TOKEN));
  hass.connection.listeners.get('ready')(); await tick();
  assert.equal(subs[1].message.resume, TOKEN);
  hass.callWS = async () => ({ panels: 'invalid' });
  intervals[0].fn(); await tick();
  assert.equal($('#status').textContent, SIDEBAR_MESSAGES.failed);
});

test('the top menu shows the integration version and build from the panel config, as text', () => {
  assert.equal(versionText({ version: '0.2.1', build: 62 }), '0.2.1 build 62');
  assert.equal(versionText({ version: '0.3.0b1', build: 0 }), '0.3.0b1 build 0');
  for (const config of [undefined, {}, { version: '0.2.1' }, { build: 62 }, { version: '0.2.1', build: '62' },
    { version: '0.2.1', build: -1 }, { version: '0.2.1', build: 1.5 }, { version: '<b>1</b>', build: 62 }, { version: '', build: 62 }]) {
    assert.equal(versionText(config), '', JSON.stringify(config));
  }
  const element = new PanelAssistantSidebar();
  const shown = element.shadowRoot.querySelector('#version');
  assert.equal(shown.textContent, '');
  element.panel = { config: { version: '0.2.1', build: 62 } };
  assert.equal(shown.textContent, '0.2.1 build 62');
  assert.equal(element.panel.config.build, 62);
  element.panel = { config: { version: '0.2.1', build: 'x' } };
  assert.equal(shown.textContent, '');
});
