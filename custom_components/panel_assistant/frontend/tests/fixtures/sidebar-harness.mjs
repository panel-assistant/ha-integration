// The sidebar test harness: a minimal shadow DOM and fake Home Assistant shared by the sidebar test files.
import assert from 'node:assert/strict';

// A minimal shadow DOM: fixed markup is parsed for tags, ids and data-message, and every
// innerHTML assignment is recorded so a test can prove panel text never reaches markup.
export const markup = [];
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
  isConnected = false; events = []; attributes = new Map();
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
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
export const store = new Map();
globalThis.localStorage = { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) };
export const pushed = [];
export const replaced = [];
globalThis.history = { pushState: (...args) => pushed.push(args), replaceState: (...args) => replaced.push(args) };
globalThis.window = new EventTarget();
globalThis.location = { origin: 'http://ha.example', pathname: '/panel-assistant' };
export let intervals = [];
globalThis.setInterval = (fn, ms) => { const handle = { fn, ms, cleared: false }; intervals.push(handle); return handle; };
globalThis.clearInterval = handle => { if (handle) handle.cleared = true; };
// The sidebar's open deadlines never fire on their own here; a test that needs them mocks the clock.
globalThis.setTimeout = () => 0;

const { ENGLISH_MESSAGES: { sidebar: SIDEBAR_MESSAGES } } = await import('../../src/frontend-localization.mjs');
export { SIDEBAR_MESSAGES };
export const { SELECTION_KEY, RELAY_TYPE, parsePanels, embedToken, versionText, openingText, PanelAssistantSidebar } = await import('../../src/sidebar-panel.mjs');

export const tick = () => new Promise(resolve => setImmediate(resolve));
export const TOKEN = 'a'.repeat(43);
export const TOKEN2 = 'b'.repeat(43);
export const url = token => `/api/panel_assistant/embed/${token}/`;
export const row = (entry_id, state = 'reachable', title = entry_id, device_id = `${entry_id}-device`) => ({ entry_id, title, state, device_id });

export function fakeHass({ panels = [row('one'), row('two')], admin = true, language = 'en', dark = false } = {}) {
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

export async function mount(hass, narrow = false, path = '') {
  intervals = [];
  const panel = new PanelAssistantSidebar();
  panel.hass = hass; panel.narrow = narrow; panel.route = { path };
  panel.isConnected = true; panel.connectedCallback(); await tick();
  const $ = selector => panel.shadowRoot.querySelector(selector);
  return { panel, $, subs: hass.connection.subscriptions };
}

export function frameWindow() {
  return { posted: [], postMessage(message, origin) { this.posted.push([message, origin]); } };
}
// The frame's pages are sandboxed, so what they post names the opaque origin "null".
export function fromWindow(source, data, origin = 'null') {
  const event = new Event('message');
  Object.assign(event, { source, data, origin });
  window.dispatchEvent(event);
}
