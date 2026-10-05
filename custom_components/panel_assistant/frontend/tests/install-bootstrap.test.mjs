import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { FRONTEND_TRANSLATIONS } from '../src/translations/index.mjs';

test('the actual installer bootstrap refuses saved offline files before opening USB', () => {
  const main = new URL('../src/install-main.mjs', import.meta.url).href;
  const script = `
    import assert from 'node:assert/strict';
    const elements = new Map();
    const node = id => {
      if (!elements.has(id)) elements.set(id, { hidden: false, dataset: {}, style: {},
        listeners: new Map(), addEventListener(type, fn) { this.listeners.set(type, fn); },
        querySelector() { return node(id + ":child"); },
        setAttribute() {}, replaceChildren() {} });
      return elements.get(id);
    };
    globalThis.document = { documentElement: {}, getElementById: node, querySelectorAll: () => [],
      createElement: () => ({ setAttribute() {} }) };
    globalThis.window = { isSecureContext: true, opener: null, location: { hash: '' },
      addEventListener() {} };
    Object.defineProperty(globalThis, 'navigator', { value: { locks: {}, usb: {
      addEventListener() {}, requestDevice() { assert.fail('offline bootstrap opened USB'); }
    } }, configurable: true });
    globalThis.indexedDB = {};
    await import(${JSON.stringify(main)});
    assert.equal(node('step-error').hidden, false);
    assert.equal(node('step-connect').hidden, true);
    await node('connect').listeners.get('click')();
    assert.ok(node('error-text').textContent.length > 0);
  `;
  assert.doesNotThrow(() => execFileSync(process.execPath, ['--input-type=module', '-e', script], { stdio: 'pipe' }));
});

function renderedJourney(scenario, locale = 'de', realCatalogue = false) {
  const target = realCatalogue ? FRONTEND_TRANSLATIONS[locale] : Object.fromEntries(Object.entries(FRONTEND_TRANSLATIONS.en).map(([group, values]) => [group, Object.fromEntries(Object.entries(values).map(([key, text]) => [key, text ? '★ ' + text : text]))]));
  const main = new URL('../src/install-main.mjs', import.meta.url).href;
  const english = new URL('../src/translations/en.json', import.meta.url).href;
  const script = `
    import assert from 'node:assert/strict';
    import { registerHooks } from 'node:module';
    const scenario = ${JSON.stringify(scenario)};
    const target = ${JSON.stringify(target)};
    const targetLanguage = ${JSON.stringify(locale)};
    const calls = { chooser: 0, authenticate: 0, preview: 0, install: 0, handover: 0 };
    const elements = new Map(), handlers = new Map();
    const node = id => {
      if (!elements.has(id)) elements.set(id, { hidden: false, dataset: {}, style: {}, textContent: '', attributes: new Map(), children: [],
        listeners: new Map(), addEventListener(type, fn) { this.listeners.set(type, fn); },
        querySelector() { return node(id + ':child'); }, setAttribute(name, value) { this.attributes.set(name, value); }, replaceChildren(...children) { this.children = children; } });
      return elements.get(id);
    };
    globalThis.document = { documentElement: {}, getElementById: node, querySelectorAll: () => [], createElement: () => ({ attributes: new Map(), setAttribute(name, value) { this.attributes.set(name, value); } }) };
    globalThis.location = { search: '?lang=en' };
    globalThis.window = { isSecureContext: true, opener: { closed: false }, location: { hash: '#handoff', reload() {} },
      addEventListener(name, fn) { handlers.set(name, fn); } };
    Object.defineProperty(globalThis, 'navigator', { value: { locks: {}, usb: {} }, configurable: true });
    globalThis.indexedDB = {};
    globalThis.calls = calls;
    globalThis.portOptions = null;
    globalThis.controllerOptions = null;
    globalThis.installResolve = null;
    globalThis.installReject = null;
    const stubs = {
      '@yume-chan/adb': 'export class Adb { disconnected = new Promise(() => {}); close() {} }; export const AdbDaemonTransport = { authenticate: async () => { globalThis.calls.authenticate++; return {}; } };',
      '@yume-chan/adb-credential-web': 'export default class Store {}',
      '@yume-chan/adb-daemon-webusb': 'export class AdbDaemonWebUsbDeviceManager { constructor() {} requestDevice() { globalThis.calls.chooser++; return Promise.resolve({ raw: { close() {} }, serial: "panel", connect: async () => ({}) }); } }',
      'usb-bounds.mjs': 'export const boundedUsb = value => value;',
      'session-target.mjs': 'export const inspectSessionTarget = async () => ({ serial: "RAW_SERIAL" });',
      'job-store.mjs': 'export const openJobStore = async () => ({ close() {} });',
      'usb-transaction-ports.mjs': 'export function createUsbTransactionPorts(options) { globalThis.portOptions = options; return {}; }',
      'install-controller.mjs': 'export function createInstallController(options) { globalThis.controllerOptions = options; return { preview: async () => { globalThis.calls.preview++; return '+JSON.stringify({receipt:null,adopt:scenario==='adopt',discarded:scenario==='adopt'?{reason:'RAW_REASON'}:null})+'; }, install: async () => { globalThis.calls.install++; return new Promise((resolve, reject) => { globalThis.installResolve = resolve; globalThis.installReject = reject; }); } }; }',
      'release-handoff.mjs': 'export const handoffOptions = () => ({}); export const receiveReleaseHandoff = () => ({ cancel() {}, completion: Promise.resolve({ release: { descriptor: { versionName: "1.2.3", versionCode: 42 } }, authenticate: async () => ({}) }) }); export const requestSetupHandover = async () => {}; export const handOverThenOpen = async (url, options) => { globalThis.calls.handover++; options.support("Setup handover", "RAW_OUTCOME"); options.open(url); };',
      'panel-address.mjs': 'export const readSetupUrl = async () => null;',
    };
    registerHooks({
      resolve(specifier, context, next) { if (Object.hasOwn(stubs, specifier)) return { url: 'stub:' + specifier, shortCircuit: true }; return next(specifier, context); },
      load(url, context, next) {
        if (url.startsWith('stub:')) return { format: 'module', source: stubs[url.slice(5)], shortCircuit: true };
        const name = url.split('/').at(-1);
        if (Object.hasOwn(stubs, name)) return { format: 'module', source: stubs[name], shortCircuit: true };
        if (${!realCatalogue} && url.endsWith('/translations/index.mjs')) return { format: 'module', shortCircuit: true, source: 'import en from '+JSON.stringify(${JSON.stringify(english)})+' with { type: "json" }; const de = Object.fromEntries(Object.entries(en).map(([group, keys]) => [group, Object.fromEntries(Object.entries(keys).map(([key, text]) => [key, text ? "★ " + text : text]))])); export const FRONTEND_TRANSLATIONS = { en, de };' };
        return next(url, context);
      },
    });
    const tick = () => new Promise(resolve => setImmediate(resolve));
    await import(${JSON.stringify(main)});
    await tick();
    await node('connect').listeners.get('click')();
    assert.deepEqual(calls, { chooser: 1, authenticate: 1, preview: 1, install: 0, handover: 0 });
    assert.equal(node('journey').children.findIndex(item => item.attributes.has('aria-current')), 2);
    if (scenario === 'adopt') {
      assert.equal(node('step-confirm').hidden, false);
      assert.equal(node('step-confirm:child').textContent, 'Already installed');
      assert.equal(node('install').textContent, 'Continue');
      assert.match(node('confirm-body').textContent, /different release.*already on your panel/);
    }
    const before = { ...calls };
    globalThis.location.search = '?lang=' + targetLanguage; handlers.get('popstate')();
    assert.deepEqual(calls, before);
    if (scenario === 'adopt') {
      assert.equal(node('step-confirm:child').textContent, target.installer.alreadyInstalledHeading);
      assert.equal(node('install').textContent, target.installer.continueSetup);
      assert.equal(node('confirm-body').textContent, target.installer.restartedDifferentVersion + ' ' + target.installer.alreadyInstalledBody);
    }
    const pending = node('install').listeners.get('click')();
    await tick();
    const progressBefore = { ...calls };
    controllerOptions.onReceipt({ phase: 'installed', revision: 7, raw: 'RAW_RECEIPT', messageKey: 'RAW_KEY'  });
    portOptions.onUploadProgress(42, 42);
    globalThis.location.search = '?lang=en'; handlers.get('languagechange')();
    assert.equal(node('activity').textContent, 'Finishing the copy…');
    assert.deepEqual(calls, progressBefore);
    globalThis.location.search = '?lang=' + targetLanguage; handlers.get('languagechange')();
    assert.equal(node('activity').textContent, target.installer.stepFinishingCopy);
    assert.equal(node('journey').children.findIndex(item => item.attributes.has('aria-current')), 2);
    assert.ok(node('support-log').textContent.includes(target.support.copied));
    assert.ok(node('support-log').textContent.includes(target.support.copiedDetail.replace('{bytes}', '42')));
    assert.match(node('support-log').textContent, /RAW_RECEIPT/);
    assert.match(node('support-log').textContent, /RAW_KEY/);
    assert.deepEqual(calls, progressBefore);
    if (scenario === 'error') {
      const error = new Error('RAW_DIAGNOSTIC'); error.code = 'shell_timeout'; installReject(error); await pending;
      assert.equal(node('step-error').hidden, false);
      assert.equal(node('error-text').textContent, target.errors.installErrorConnection);
      const errorBefore = { ...calls };
      globalThis.location.search = '?lang=en'; handlers.get('languagechange')();
      assert.equal(node('error-text').textContent, 'The connection to the panel dropped. Keep it plugged in and press Try again.');
      assert.match(node('support-log').textContent, /shell_timeout/);
      assert.deepEqual(calls, errorBefore);
    } else {
    installResolve({ permissions: { raw: 'RAW_PERMISSION' } }); await pending;
    assert.equal(node('step-done').hidden, false);
    assert.equal(node('journey').children.findIndex(item => item.attributes.has('aria-current')), 3);
    assert.equal(node('done-text').textContent, target.installer.doneManual);
    assert.ok(node('support-log').textContent.includes(target.support.setupHandover));
    assert.match(node('support-log').textContent, /RAW_OUTCOME/);
    const doneBefore = { ...calls };
    globalThis.location.search = '?lang=en'; handlers.get('popstate')();
    assert.equal(node('done-text').textContent, 'Finish setting up on the panel’s screen.');
    assert.deepEqual(calls, doneBefore);
    assert.deepEqual(calls, { chooser: 1, authenticate: 1, preview: 1, install: 1, handover: 1 });
    }
  `;
  execFileSync(process.execPath, ['--input-type=module', '-e', script], { stdio: 'pipe' });
}

test('already-installed and set-aside guidance translates in the real bootstrap without another consent or install', () => renderedJourney('adopt'));
test('live copy progress, support framing and completion switch language without replaying any action', () => renderedJourney('clean'));

test('a live connection refusal keeps its recovery instruction and raw diagnostic when language changes', () => renderedJourney('error'));

test('all five admitted catalogues redraw live progress and refusal guidance without replaying actions', () => {
  for (const locale of ['de', 'es', 'fr', 'it', 'zh-Hans']) renderedJourney('error', locale, true);
});
