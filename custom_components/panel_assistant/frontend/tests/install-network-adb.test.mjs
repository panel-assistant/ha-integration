import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';

// The real installer bootstrap, with USB and the transaction stubbed: what a USB
// install leaves for Panel Assistant's own ADB access, through the page that runs it.
function journey({ key, runsTheApp = false, networkAdb = 'turned_on' }) {
  const main = new URL('../src/install-main.mjs', import.meta.url).href;
  const actualHandover = new URL('../src/release-handoff.mjs?actual', import.meta.url).href;
  const script = `
    import assert from 'node:assert/strict';
    import { registerHooks } from 'node:module';
    const order = [];
    globalThis.order = order;
    const elements = new Map(), handlers = new Map();
    const node = id => {
      if (!elements.has(id)) elements.set(id, { hidden: false, dataset: {}, style: {}, textContent: '', attributes: new Map(), children: [],
        listeners: new Map(), addEventListener(type, fn) { this.listeners.set(type, fn); },
        querySelector() { return node(id + ':child'); }, setAttribute(name, value) { this.attributes.set(name, value); }, replaceChildren(...children) { this.children = children; } });
      return elements.get(id);
    };
    globalThis.document = { documentElement: {}, getElementById: node, querySelectorAll: () => [],
      createElement: () => ({ attributes: new Map(), setAttribute(name, value) { this.attributes.set(name, value); }, append() {}, replaceChildren() {} }),
      createTextNode: text => ({ text }) };
    globalThis.location = { search: '?lang=en' };
    globalThis.window = { isSecureContext: true, opener: { closed: false }, location: { hash: '#handoff', reload() {}, assign() { order.push('navigate'); } },
      addEventListener(name, fn) { handlers.set(name, fn); } };
    Object.defineProperty(globalThis, 'navigator', { value: { locks: {}, usb: {} }, configurable: true });
    globalThis.indexedDB = {};
    const stubs = {
      '@yume-chan/adb': 'export class Adb { constructor() { this.disconnected = new Promise(resolve => { globalThis.unplug = resolve; }); } close() {} }; export const AdbDaemonTransport = { authenticate: async options => { globalThis.authOptions = options; globalThis.order.push("authenticate"); return {}; } };',
      '@yume-chan/adb-credential-web': 'export default class Store {}',
      '@yume-chan/adb-daemon-webusb': 'export class AdbDaemonWebUsbDeviceManager { requestDevice() { return Promise.resolve({ raw: { close() {} }, serial: "panel", connect: async () => ({}) }); } }',
      'usb-bounds.mjs': 'export const boundedUsb = value => value;',
      'session-target.mjs': 'export const inspectSessionTarget = async () => ({ serial: "RAW_SERIAL" });',
      'job-store.mjs': 'export const openJobStore = async () => ({ close() {} });',
      'usb-transaction-ports.mjs': 'export function createUsbTransactionPorts(options) { return {}; }',
      'install-controller.mjs': 'export function createInstallController() { return { preview: async () => { if (${runsTheApp}) throw Object.assign(new Error("target_installed"), { code: "target_installed" }); return { receipt: null }; }, install: async () => { globalThis.order.push("install"); return { permissions: {} }; }, completeSetup: async () => { globalThis.order.push("completeSetup"); return {}; } }; }',
      'panel-address.mjs': 'export const readSetupUrl = async () => "http://192.168.1.20:8080/setup";',
      'panel-assistant-adb.mjs': 'export const panelAssistantKeyAuthenticator = key => "authenticator for " + key; export const ensureNetworkAdb = async (adb, { beforeRestart }) => { globalThis.order.push("networkAdb"); beforeRestart(); globalThis.unplug(); await new Promise(resolve => setImmediate(resolve)); return ${JSON.stringify(networkAdb)}; };',
      'release-handoff.mjs': 'import { handOverThenOpen as actualOpen } from ' + JSON.stringify(${JSON.stringify(actualHandover)}) + '; export const handoffOptions = () => ({}); export const receiveReleaseHandoff = () => ({ cancel() {}, completion: Promise.resolve({ release: { descriptor: { versionName: "1.2.3", versionCode: 42 } }, authenticate: async () => ({}) }) }); export const requestPanelAssistantKey = async () => ${JSON.stringify(key)}; export const requestSetupHandover = async () => { globalThis.order.push("handover"); return "handed_over"; }; export const handOverThenOpen = actualOpen;',
    };
    registerHooks({
      resolve(specifier, context, next) { if (Object.hasOwn(stubs, specifier)) return { url: 'stub:' + specifier, shortCircuit: true }; return next(specifier, context); },
      load(url, context, next) {
        if (url.startsWith('stub:')) return { format: 'module', source: stubs[url.slice(5)], shortCircuit: true };
        const name = url.split('/').at(-1);
        if (Object.hasOwn(stubs, name)) return { format: 'module', source: stubs[name], shortCircuit: true };
        return next(url, context);
      },
    });
    const tick = () => new Promise(resolve => setImmediate(resolve));
    await import(${JSON.stringify(main)});
    await tick();
    await node('connect').listeners.get('click')();
    if (!${runsTheApp}) await node('install').listeners.get('click')();
    await tick();
    process.stdout.write(JSON.stringify({ order, authenticators: globalThis.authOptions.authenticators ?? null,
      done: node('step-done').hidden === false, error: node('step-error').hidden === false,
      support: node('support-log').textContent, heading: node('step-done:child').textContent,
      text: node('done-text').textContent + node('error-text').textContent }));
  `;
  return JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e', script], { encoding: 'utf8' }));
}

test('a USB install offers Panel Assistant’s key at the panel’s prompt and turns network ADB on last', () => {
  const result = journey({ key: 'PA_KEY' });
  assert.deepEqual(result.authenticators, ['authenticator for PA_KEY']);
  // Network ADB comes after everything else the session does, because adbd
  // restarts to turn it on and the USB session ends with it.
  assert.deepEqual(result.order.filter(step => step !== 'navigate'),
    ['authenticate', 'install', 'handover', 'completeSetup', 'networkAdb']);
  // That ending is the install finishing, not the cable being pulled: nothing
  // says the session stopped.
  assert.equal(result.done, true);
  assert.equal(result.error, false);
  assert.doesNotMatch(result.support, /Stopped/);
  assert.match(result.support, /turned_on/);
});

test('an older Home Assistant that sends no key leaves the browser’s own key in use', () => {
  const result = journey({ key: null });
  assert.equal(result.authenticators, null);
  assert.equal(result.done, true);
  assert.equal(result.order.at(-1), 'networkAdb');
});

test('a panel that already runs the app gets nothing installed, only Panel Assistant’s route back', () => {
  const result = journey({ key: 'PA_KEY', runsTheApp: true });
  // The owner's Allow trusts Panel Assistant's key; then network ADB, and nothing else.
  assert.deepEqual(result.authenticators, ['authenticator for PA_KEY']);
  assert.deepEqual(result.order, ['authenticate', 'networkAdb']);
  assert.equal(result.done, true);
  assert.equal(result.error, false);
  assert.match(result.heading, /reach this panel again/);
  assert.match(result.text, /nothing was installed/);
  assert.doesNotMatch(result.support, /Stopped/);
});

test('when network ADB cannot be turned on, the owner is told the panel already has the app', () => {
  const result = journey({ key: 'PA_KEY', runsTheApp: true, networkAdb: 'failed' });
  assert.equal(result.done, false);
  assert.equal(result.error, true);
  assert.match(result.text, /already has the app/);
});
