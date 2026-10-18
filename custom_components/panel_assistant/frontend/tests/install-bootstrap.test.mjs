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

for (const locale of ['en', 'de']) test(`a failed handoff from Home Assistant offers the way back it names (${locale})`, () => {
  const main = new URL('../src/install-main.mjs', import.meta.url).href;
  const script = `
    import assert from 'node:assert/strict';
    const elements = new Map();
    const node = id => {
      if (!elements.has(id)) elements.set(id, { hidden: false, dataset: {}, style: {}, textContent: '',
        listeners: new Map(), addEventListener(type, fn) { this.listeners.set(type, fn); },
        querySelector() { return node(id + ":child"); },
        setAttribute() {}, replaceChildren() {} });
      return elements.get(id);
    };
    globalThis.document = { documentElement: {}, getElementById: node,
      querySelectorAll: selector => selector === '[data-screen-message]' ? [Object.assign(node('retry'), { dataset: { screenMessage: 'tryAgain' } })] : [],
      createElement: () => ({ setAttribute() {} }) };
    globalThis.location = { search: '?lang=${locale}' };
    // Opened from Home Assistant, whose tab has since closed.
    globalThis.window = { isSecureContext: true, opener: null,
      location: { hash: '#ha_origin=http%3A%2F%2Fha.local%3A8123&nonce=${'0'.repeat(32)}&rc=' },
      addEventListener() {} };
    Object.defineProperty(globalThis, 'navigator', { value: { locks: {}, usb: { addEventListener() {} } }, configurable: true });
    globalThis.indexedDB = {};
    await import(${JSON.stringify(main)});
    assert.equal(node('step-error').hidden, false);
    assert.equal(node('error-text').textContent, ${JSON.stringify(FRONTEND_TRANSLATIONS[locale].installer.handoffFailure)});
    assert.equal(node('retry').textContent, ${JSON.stringify(FRONTEND_TRANSLATIONS[locale].installer.backToHa)});
  `;
  execFileSync(process.execPath, ['--input-type=module', '-e', script], { stdio: 'pipe' });
});

function renderedJourney(scenario, locale = 'de', realCatalogue = false) {
  const target = realCatalogue ? FRONTEND_TRANSLATIONS[locale] : Object.fromEntries(Object.entries(FRONTEND_TRANSLATIONS.en).map(([group, values]) => [group, Object.fromEntries(Object.entries(values).map(([key, text]) => [key, text ? '★ ' + text : text]))]));
  const main = new URL('../src/install-main.mjs', import.meta.url).href;
  const english = new URL('../src/translations/en.json', import.meta.url).href;
  const actualHandover = new URL('../src/release-handoff.mjs?actual', import.meta.url).href;
  const script = `
    import assert from 'node:assert/strict';
    import { registerHooks } from 'node:module';
    const scenario = ${JSON.stringify(scenario)};
    const FRONTEND_WARNING = ${JSON.stringify(FRONTEND_TRANSLATIONS.en.installer.doneAnotherHomeAssistant)};
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
    globalThis.document = { documentElement: {}, getElementById: node, querySelectorAll: selector => selector === '[data-screen-message]' ? [Object.assign(node('retry'), { dataset: { screenMessage: 'tryAgain' } })] : [], createElement: () => ({ attributes: new Map(), setAttribute(name, value) { this.attributes.set(name, value); } }) };
    globalThis.location = { search: '?lang=en' };
    let navigations = 0;
    const scheduled = [];
    const originalSetTimeout = globalThis.setTimeout;
    globalThis.setTimeout = (fn, ms, ...args) => { scheduled.push(ms); return originalSetTimeout(fn, ms, ...args); };
    globalThis.window = { isSecureContext: true, opener: { closed: false }, location: { hash: '#handoff', reload() {}, assign() { navigations++; } },
      addEventListener(name, fn) { handlers.set(name, fn); } };
    Object.defineProperty(globalThis, 'navigator', { value: { locks: {}, usb: {} }, configurable: true });
    globalThis.indexedDB = {};
    globalThis.calls = calls;
    globalThis.portOptions = null;
    globalThis.controllerOptions = null;
    globalThis.installResolve = null;
    globalThis.installReject = null;
    globalThis.settlementHold = scenario === 'settlement_interrupted';
    const stubs = {
      '@yume-chan/adb': 'export const AdbCommand = {}; export class AdbTcpIpService {}; export class Adb { disconnected = new Promise(() => {}); close() {} }; export const AdbDaemonTransport = { authenticate: async () => { globalThis.calls.authenticate++; return {}; } };',
      '@yume-chan/adb-credential-web': 'export default class Store {}',
      '@yume-chan/adb-daemon-webusb': 'export class AdbDaemonWebUsbDeviceManager { constructor() {} requestDevice() { globalThis.calls.chooser++; return Promise.resolve({ raw: { close() {} }, serial: "panel", connect: async () => ({}) }); } }',
      'usb-bounds.mjs': 'export const boundedUsb = value => value;',
      'session-target.mjs': 'export const inspectSessionTarget = async () => ({ serial: "RAW_SERIAL" });',
      'job-store.mjs': 'export const openJobStore = async () => ({ close() {} });',
      'usb-transaction-ports.mjs': 'export function createUsbTransactionPorts(options) { globalThis.portOptions = options; return {}; }',
      'install-controller.mjs': 'export function createInstallController(options) { globalThis.controllerOptions = options; return { preview: async () => { globalThis.calls.preview++; return '+JSON.stringify({receipt:null,adopt:scenario==='adopt',discarded:scenario==='adopt'?{reason:'RAW_REASON'}:null})+'; }, install: async () => { globalThis.calls.install++; return new Promise((resolve, reject) => { globalThis.installResolve = resolve; globalThis.installReject = reject; }); }, completeSetup: async () => { globalThis.settlementStarted = true; if (globalThis.settlementHold) return new Promise(resolve => { globalThis.settlementResolve = resolve; }); return {}; } }; }',
      'release-handoff.mjs': 'export const handoffOptions = () => ({}); export const requestPanelAssistantKey = async () => null; export const receiveReleaseHandoff = () => ({ cancel() {}, completion: Promise.resolve({ release: { descriptor: { versionName: "1.2.3", versionCode: 42 } }, authenticate: async () => ({}) }) }); export const requestSetupHandover = async () => {}; export const handOverThenOpen = async (url, options) => { globalThis.calls.handover++; options.support("Setup handover", "RAW_OUTCOME"); await options.beforeOpen(); options.open(url); };',
      'panel-address.mjs': 'export const readSetupUrl = async () => null;',
    };
    if (scenario === 'foreign' || scenario === 'settlement_interrupted') {
      stubs['panel-assistant-adb.mjs'] = 'export const panelAssistantKeyAuthenticator = () => {}; export const ensureNetworkAdb = async () => "already_on";';
      stubs['panel-address.mjs'] = 'export const readSetupUrl = async () => "http://192.168.1.20:8080/setup";';
      stubs['release-handoff.mjs'] = 'import { handOverThenOpen as actualOpen } from '+JSON.stringify(${JSON.stringify(actualHandover)})+'; export const handoffOptions = () => ({}); export const receiveReleaseHandoff = () => ({ cancel() {}, completion: Promise.resolve({ release: { descriptor: { versionName: "1.2.3", versionCode: 42 } }, authenticate: async () => ({}) }) }); export const requestPanelAssistantKey = async () => null; export const requestSetupHandover = async options => { options.onRegistration("added_to_another_home_assistant"); return "handed_over"; }; export const handOverThenOpen = async (url, options) => { globalThis.calls.handover++; return actualOpen(url, options); };';
    }
    registerHooks({
      resolve(specifier, context, next) { if (Object.hasOwn(stubs, specifier)) return { url: 'stub:' + specifier, shortCircuit: true }; return next(specifier, context); },
      load(url, context, next) {
        if (url.startsWith('stub:')) return { format: 'module', source: stubs[url.slice(5)], shortCircuit: true };
        const name = url.split('/').at(-1);
        if (Object.hasOwn(stubs, name)) return { format: 'module', source: stubs[name], shortCircuit: true };
        if (${!realCatalogue} && url.endsWith('/translations/index.mjs')) return { format: 'module', shortCircuit: true, source: 'import en from '+JSON.stringify(${JSON.stringify(english)})+' with { type: "json" }; const de = Object.fromEntries(Object.entries(en).map(([group, keys]) => [group, Object.fromEntries(Object.entries(keys).map(([key, text]) => [key, text ? "★ " + text : text]))])); export const FRONTEND_TRANSLATIONS = { en, [${JSON.stringify(locale)}]: de };' };
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
    controllerOptions.onSetAside({ job: 'RAW_SET_ASIDE' });
    controllerOptions.onStagedCopy({ removed: 'RAW_STAGED_COPY' });
    assert.match(node('support-log').textContent, /RAW_SET_ASIDE[^]*RAW_STAGED_COPY/, 'the set-aside job and its file are logged');
    assert.deepEqual(calls, progressBefore);
    if (scenario === 'error') {
      const error = new Error('RAW_DIAGNOSTIC'); error.code = 'shell_timeout'; installReject(error); await pending;
      assert.equal(node('step-error').hidden, false);
      assert.equal(node('error-text').textContent, target.errors.installErrorConnection);
      assert.equal(node('retry').textContent, target.installer.tryAgain, 'the only error button matches the message: press Try again');
      const errorBefore = { ...calls };
      globalThis.location.search = '?lang=en'; handlers.get('languagechange')();
      assert.equal(node('error-text').textContent, 'The connection to the panel dropped. Keep it plugged in and press Try again.');
      assert.equal(node('retry').textContent, 'Try again');
      assert.match(node('support-log').textContent, /shell_timeout/);
      assert.deepEqual(calls, errorBefore);
    } else {
    installResolve({ permissions: { raw: 'RAW_PERMISSION' } });
    if (scenario === 'settlement_interrupted') {
      await tick(); await tick();
      assert.equal(globalThis.settlementStarted, true);
      assert.equal(node('step-done').hidden, true);
      assert.equal(node('step-progress').hidden, false);
      portOptions.quarantine();
      globalThis.settlementResolve({});
      await pending;
      assert.equal(node('step-done').hidden, true, 'a late settlement cannot complete the interrupted session');
      assert.equal(node('step-error').hidden, false, 'retry remains available');
      assert.equal(scheduled.includes(1200), false);
      assert.equal(navigations, 0);
      process.exit(0);
    }
    await pending;
    assert.equal(node('step-done').hidden, false);
    assert.equal(node('journey').children.findIndex(item => item.attributes.has('aria-current')), 3);
    assert.equal(node('done-text').textContent, scenario === 'foreign' ? target.installer.doneAnotherHomeAssistant : target.installer.doneManual);
    assert.ok(node('support-log').textContent.includes(target.support.setupHandover));
    assert.match(node('support-log').textContent, scenario === 'foreign' ? /handed_over/ : /RAW_OUTCOME/);
    const doneBefore = { ...calls };
    globalThis.location.search = '?lang=en'; handlers.get('popstate')();
    assert.equal(node('done-text').textContent, scenario === 'foreign' ? FRONTEND_WARNING : 'Finish setting up on the panel’s screen.');
    if (scenario === 'foreign') {
      assert.match(node('done-text').textContent, /signed in to another Home Assistant/);
      assert.equal(node('open-setup').hidden, false);
      assert.equal(node('open-setup').href, 'http://192.168.1.20:8080/setup');
      assert.equal(scheduled.includes(1200), false, 'the warning is not replaced by scheduled navigation');
      assert.equal(navigations, 0);
    }
    assert.deepEqual(calls, doneBefore);
    assert.deepEqual(calls, { chooser: 1, authenticate: 1, preview: 1, install: 1, handover: 1 });
    // Closing USB after success is not a failure: the done screen stays.
    portOptions.quarantine();
    assert.equal(node('step-done').hidden, false);
    assert.equal(node('step-error').hidden, true);
    }
  `;
  execFileSync(process.execPath, ['--input-type=module', '-e', script], { stdio: 'pipe' });
}

for (const locale of ['de', 'cs', 'pt-BR']) {
  test(`${locale}: already-installed and set-aside guidance translates in the real bootstrap without another consent or install`, () => renderedJourney('adopt', locale));
  test(`${locale}: live copy progress, support framing and completion switch language without replaying any action`, () => renderedJourney('clean', locale));
  test(`${locale}: a live connection refusal keeps its recovery instruction and raw diagnostic when language changes`, () => renderedJourney('error', locale));
}

test('every admitted catalogue redraws consent, progress, completion and refusal without replaying actions', () => {
  for (const locale of Object.keys(FRONTEND_TRANSLATIONS).filter(locale => locale !== 'en')) {
    for (const scenario of ['adopt', 'clean', 'error']) renderedJourney(scenario, locale, true);
  }
});

test('a foreign-server completion translates with a setup link and switches back to English', () => renderedJourney('foreign', 'de', true));

test('losing USB while display settlement is pending cannot mark finished or open setup', () => renderedJourney('settlement_interrupted'));

// The cable timer bounds a silent cable, not the person: it starts once the
// panel has allowed this computer, never while the chooser or Allow prompt waits.
// The Allow instruction is shown only to a panel still asking after a moment:
// one that trusts this computer, or never asks, answers at once.
function cableTimer(leg) {
  const main = new URL('../src/install-main.mjs', import.meta.url).href;
  const script = `
    import assert from 'node:assert/strict';
    import { registerHooks } from 'node:module';
    const leg = ${JSON.stringify(leg)};
    const elements = new Map(), handlers = new Map();
    const node = id => {
      if (!elements.has(id)) elements.set(id, { hidden: false, dataset: {}, style: {}, textContent: '', attributes: new Map(), children: [],
        listeners: new Map(), addEventListener(type, fn) { this.listeners.set(type, fn); },
        querySelector() { return node(id + ':child'); }, setAttribute(name, value) { this.attributes.set(name, value); }, replaceChildren(...children) { this.children = children; } });
      return elements.get(id);
    };
    globalThis.document = { documentElement: {}, getElementById: node, querySelectorAll: () => [], createElement: () => ({ attributes: new Map(), setAttribute(name, value) { this.attributes.set(name, value); } }) };
    globalThis.location = { search: '?lang=en' };
    const cable = [];
    const originalSetTimeout = globalThis.setTimeout, originalClearTimeout = globalThis.clearTimeout;
    const prompt = [];
    globalThis.setTimeout = (fn, ms, ...args) => {
      const held = ms === 45000 ? cable : ms === 1500 ? prompt : null;
      if (!held) return originalSetTimeout(fn, ms, ...args);
      const timer = { fn, cleared: false }; held.push(timer); return timer;
    };
    globalThis.clearTimeout = timer => { if (cable.includes(timer) || prompt.includes(timer)) timer.cleared = true; else originalClearTimeout(timer); };
    let allowHidden = true, allowShown = false;
    Object.defineProperty(node('step-allow'), 'hidden', { get: () => allowHidden, set: value => { allowHidden = value; if (!value) allowShown = true; } });
    globalThis.window = { isSecureContext: true, opener: { closed: false }, location: { hash: '#handoff', reload() {}, assign() {} },
      addEventListener(name, fn) { handlers.set(name, fn); } };
    Object.defineProperty(globalThis, 'navigator', { value: { locks: {}, usb: {} }, configurable: true });
    globalThis.indexedDB = {};
    globalThis.calls = { authenticate: 0, preview: 0 };
    let allow = () => {};
    globalThis.authenticated = leg !== 'silent' ? new Promise(resolve => { allow = resolve; }) : Promise.resolve({});
    const stubs = {
      '@yume-chan/adb': 'export class Adb { disconnected = new Promise(() => {}); close() {} }; export const AdbDaemonTransport = { authenticate: () => { globalThis.calls.authenticate++; return globalThis.authenticated; } };',
      '@yume-chan/adb-credential-web': 'export default class Store {}',
      '@yume-chan/adb-daemon-webusb': 'export class AdbDaemonWebUsbDeviceManager { requestDevice() { return Promise.resolve({ raw: { close() {} }, serial: "panel", connect: async () => ({}) }); } }',
      'usb-bounds.mjs': 'export const boundedUsb = value => value;',
      'session-target.mjs': leg === 'allow' ? 'export const inspectSessionTarget = async () => ({ serial: "panel" });' : 'export const inspectSessionTarget = () => new Promise(() => {});',
      'job-store.mjs': 'export const openJobStore = async () => ({ close() {} });',
      'usb-transaction-ports.mjs': 'export function createUsbTransactionPorts() { return {}; }',
      'install-controller.mjs': 'export function createInstallController() { return { preview: async () => { globalThis.calls.preview++; return { receipt: null, adopt: false, discarded: null }; } }; }',
      'release-handoff.mjs': 'export const requestPanelAssistantKey = async () => null; export const handoffOptions = () => ({}); export const receiveReleaseHandoff = () => ({ cancel() {}, completion: Promise.resolve({ release: { descriptor: { versionName: "1.2.3", versionCode: 42 } }, authenticate: async () => ({}) }) }); export const requestSetupHandover = async () => {}; export const handOverThenOpen = async () => {};',
      'panel-address.mjs': 'export const readSetupUrl = async () => null;',
      'panel-assistant-adb.mjs': 'export const panelAssistantKeyAuthenticator = () => {}; export const ensureNetworkAdb = async () => "already_on";',
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
    const expire = () => { for (const timer of cable) if (!timer.cleared) timer.fn(); };
    const promptDue = () => { for (const timer of prompt) if (!timer.cleared) timer.fn(); };
    const timeout = 'The panel didn’t answer. Check the cable is firmly in, or swap it: a cable can power the panel and still carry no data.';
    await import(${JSON.stringify(main)});
    await tick();
    const clicked = node('connect').listeners.get('click')();
    for (let i = 0; i < 5; i++) await tick();
    assert.equal(calls.authenticate, 1);
    if (leg === 'stopped') {
      // The window closes while the panel is still asking; the error stays on screen.
      handlers.get('pagehide')();
      promptDue();
      assert.equal(node('step-error').hidden, false);
      assert.equal(allowShown, false, 'a stopped session is never told to tap Allow');
      process.exit(0);
    }
    if (leg === 'allow') {
      assert.equal(allowShown, false, 'nothing tells the person to tap Allow before the panel has had a moment');
      assert.equal(node('step-checking').hidden, false);
      promptDue();
      assert.equal(node('step-allow').hidden, false, 'a panel still asking gets the Allow instruction');
    } else {
      promptDue();
      assert.equal(allowShown, false, 'a panel that answered at once is never told to tap Allow');
      assert.equal(node('step-checking').hidden, false);
    }
    expire();
    if (leg === 'allow') {
      assert.equal(node('step-error').hidden, true, 'waiting for Allow is not a cable timeout');
      assert.notEqual(node('error-text').textContent, timeout);
      assert.equal(node('step-allow').hidden, false);
      allow({});
      await clicked;
      assert.equal(calls.preview, 1);
      assert.equal(node('step-confirm').hidden, false);
    } else {
      assert.equal(node('error-text').textContent, timeout, 'a panel that allowed and then went silent still times out');
      assert.equal(node('step-error').hidden, false);
    }
    process.exit(0);
  `;
  execFileSync(process.execPath, ['--input-type=module', '-e', script], { stdio: 'pipe' });
}

test('a panel that waits for Allow is asked for it, and the cable timer waits too', () => cableTimer('allow'));
test('a panel that answers at once is never asked for Allow, and still times out if it goes silent', () => cableTimer('silent'));
test('a session stopped while the panel is still asking never shows the Allow instruction', () => cableTimer('stopped'));
