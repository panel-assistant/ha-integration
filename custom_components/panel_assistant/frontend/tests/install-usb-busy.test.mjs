import test from 'node:test';
import { execFileSync } from 'node:child_process';
import { FRONTEND_TRANSLATIONS } from '../src/translations/index.mjs';

for (const scenario of ['busy', 'generic', 'transaction_busy']) {
  test(`USB connection failure: ${scenario}`, () => {
    const script = `
      import assert from 'node:assert/strict';
      import { registerHooks } from 'node:module';
      import { AdbDaemonWebUsbDevice } from '@yume-chan/adb-daemon-webusb';
      globalThis.actualDevice = AdbDaemonWebUsbDevice;
      const scenario = ${JSON.stringify(scenario)};
      const elements = new Map(), handlers = new Map();
      const node = id => {
        if (!elements.has(id)) elements.set(id, { hidden: false, dataset: {}, style: {}, textContent: '', attributes: new Map(), children: [],
          listeners: new Map(), addEventListener(type, fn) { this.listeners.set(type, fn); },
          querySelector() { return node(id + ':child'); }, setAttribute(name, value) { this.attributes.set(name, value); }, replaceChildren(...children) { this.children = children; } });
        return elements.get(id);
      };
      globalThis.document = { documentElement: {}, getElementById: node, querySelectorAll: () => [], createElement: () => ({ attributes: new Map(), setAttribute(name, value) { this.attributes.set(name, value); } }) };
      globalThis.location = { search: '?lang=en' };
      let closes = 0, reloads = 0, connects = 0, commands = 0;
      globalThis.window = { isSecureContext: true, opener: { closed: false }, location: { hash: '#handoff', reload() { reloads++; } }, addEventListener(name, fn) { handlers.set(name, fn); } };
      Object.defineProperty(globalThis, 'navigator', { value: { locks: {}, usb: {} }, configurable: true });
      globalThis.indexedDB = {};
      const cause = new DOMException('Unable to claim interface', 'NetworkError');
      const failure = scenario === 'busy' ? new AdbDaemonWebUsbDevice.DeviceBusyError(cause)
        : scenario === 'transaction_busy' ? Object.assign(new Error('saved install owns transaction'), { code: 'transaction_busy' }) : new Error('The device is already in used by another program');
      globalThis.chosen = { raw: { close() { closes++; throw new Error('session_closed'); }, transferIn() { commands++; }, transferOut() { commands++; } }, async connect() { connects++; throw failure; } };
      const stubs = {
        '@yume-chan/adb-daemon-webusb': 'export const AdbDaemonWebUsbDevice = globalThis.actualDevice; export class AdbDaemonWebUsbDeviceManager { requestDevice() { return Promise.resolve(globalThis.chosen); } }',
        'release-handoff.mjs': 'export const requestPanelAssistantKey = async () => null; export const handoffOptions = () => ({}); export const receiveReleaseHandoff = () => ({ cancel() {}, completion: Promise.resolve({ release: { descriptor: { versionName: "1.2.3", versionCode: 42 } }, authenticate: async () => ({}) }) }); export const requestSetupHandover = async () => {}; export const handOverThenOpen = async () => {};',
      };
      registerHooks({
        resolve(specifier, context, next) { if (Object.hasOwn(stubs, specifier)) return { url: 'stub:' + specifier, shortCircuit: true }; return next(specifier, context); },
        load(url, context, next) { const name = url.startsWith('stub:') ? url.slice(5) : url.split('/').at(-1); if (Object.hasOwn(stubs, name)) return { format: 'module', source: stubs[name], shortCircuit: true }; return next(url, context); },
      });
      await import(${JSON.stringify(new URL('../src/install-main.mjs', import.meta.url).href)});
      await new Promise(resolve => setImmediate(resolve));
      await node('connect').listeners.get('click')();
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(node('step-error').hidden, false);
      const expected = ${JSON.stringify(FRONTEND_TRANSLATIONS.en.errors)};
      assert.equal(node('error-text').textContent, expected[scenario === 'busy' ? 'installErrorUsbBusy' : scenario === 'transaction_busy' ? 'installErrorBusy' : 'installErrorGeneric']);
      assert.ok(closes > 0, 'failed connection closes its raw USB resource');
      assert.ok(node('support-log').textContent.includes(failure.code ?? failure.message), 'support retains actual original cause');
      if (scenario === 'busy') assert.equal(failure.cause, cause);
      if (scenario === 'busy') {
        for (const [locale, catalogue] of Object.entries(${JSON.stringify(FRONTEND_TRANSLATIONS)})) {
          globalThis.location.search = '?lang=' + locale;
          handlers.get('popstate')();
          assert.equal(node('error-text').textContent, catalogue.errors.installErrorUsbBusy, locale + ' renders its localized guidance');
        }
      }
      const guidance = node('error-text').textContent;
      handlers.get('pagehide')();
      assert.equal(node('error-text').textContent, guidance, 'later cleanup cannot replace the first failure');
      await node('connect').listeners.get('click')();
      assert.equal(connects, 1, 'quarantined session cannot issue another connection');
      assert.equal(commands, 0, 'failed connection sends no commands');
      for (let i = 0; i < 2; i++) node('retry').listeners.get('click')();
      assert.equal(reloads, 2, 'retry reloads to obtain a fresh session');
    `;
    execFileSync(process.execPath, ['--input-type=module', '-e', script], { cwd: new URL('..', import.meta.url), stdio: 'pipe' });
  });
}
