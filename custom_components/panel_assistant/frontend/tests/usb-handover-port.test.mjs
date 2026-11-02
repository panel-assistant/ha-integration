import test from 'node:test';
import { execFileSync } from 'node:child_process';

// Run the real bootstrap in a fresh browser-like global. Fake USB transport and
// release authority; retain its port reader, address reader and both window relays.
for (const port of [8888, 8889]) test(`the installer carries USB listener ${port} through the Home Assistant handover`, () => {
  const main = new URL('../src/install-main.mjs', import.meta.url).href;
  const actual = new URL('../src/release-handoff.mjs?actual', import.meta.url).href;
  const relay = new URL('../src/ha-release-handoff.mjs', import.meta.url).href;
  execFileSync(process.execPath, ['--input-type=module', '-e', `
    import assert from 'node:assert/strict';
    import { registerHooks } from 'node:module';
    import { webcrypto } from 'node:crypto';
    import { startReleaseHandoff } from ${JSON.stringify(relay)};
    const port = ${port};
    const elements = new Map();
    const node = id => {
      if (!elements.has(id)) elements.set(id, { hidden: false, dataset: {}, style: {}, textContent: '', children: [],
        listeners: new Map(), addEventListener(type, fn) { this.listeners.set(type, fn); },
        querySelector() { return node(id + ':child'); }, setAttribute() {}, replaceChildren(...items) { this.children = items; } });
      return elements.get(id);
    };
    globalThis.document = { documentElement: {}, getElementById: node, querySelectorAll: () => [],
      createElement: () => ({ setAttribute() {} }) };
    globalThis.location = { search: '' };
    globalThis.indexedDB = {};
    const parentListeners = new Set(), childListeners = new Set(), calls = [];
    let installerUrl;
    const child = { closed: false, postMessage(data) { for (const fn of [...childListeners]) fn({ source: opener, origin: 'http://ha.example:8123', data }); } };
    const opener = { closed: false, postMessage(data) { for (const fn of [...parentListeners]) fn({ source: child, origin: 'https://installer.example', data }); } };
    const metadata = { id: 'a'.repeat(32), tag: 'v1.2.3', checksum: btoa('checksum'), checksum_signature: btoa('s'.repeat(256)),
      descriptor: btoa('{}'), descriptor_signature: btoa('d'.repeat(256)), apk_size: 3, apk_sha256: 'b'.repeat(64) };
    const json = value => new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } });
    const handle = startReleaseHandoff({ fetchWithAuth: async (path, init) => {
      if (path.endsWith('/handover')) { calls.push(JSON.parse(init.body)); return json({ outcome: 'handed_over' }); }
      return path.endsWith('/apk') ? new Response('apk') : json(metadata);
    } }, 'https://installer.example/', { windowObject: { crypto: webcrypto, location: { origin: 'http://ha.example:8123' },
      addEventListener(_, fn) { parentListeners.add(fn); }, removeEventListener(_, fn) { parentListeners.delete(fn); },
      open(url) { installerUrl = new URL(url); return child; } } });
    const nonce = new URLSearchParams(installerUrl.hash.slice(1)).get('nonce');
    opener.postMessage({ type: 'ha-paneld/usb-ready', nonce });
    const tick = () => new Promise(resolve => setImmediate(resolve));
    await tick(); await tick();
    opener.postMessage({ type: 'ha-paneld/usb-verified', nonce }); await handle.completion;
    for (const address of ['192.168.1.70:0', '192.168.1.70:65536', '192.168.1.70:-1', '192.168.1.70:01', 'evil.example:8889']) {
      opener.postMessage({ type: 'ha-paneld/usb-handover', nonce, address });
    }
    for (const override of [{ source: {} }, { origin: 'https://foreign.example' }, { data: { type: 'ha-paneld/usb-handover', nonce: 'f'.repeat(32), address: '192.168.1.70:8889' } }]) {
      for (const fn of parentListeners) fn({ source: child, origin: 'https://installer.example', data: { type: 'ha-paneld/usb-handover', nonce, address: '192.168.1.70:8889' }, ...override });
    }
    await tick();
    assert.deepEqual(calls, [], 'invalid ports and untrusted window messages are never relayed');
    globalThis.window = { isSecureContext: true, opener, location: { hash: installerUrl.hash, assign() {}, reload() {} },
      addEventListener(type, fn) { if (type === 'message') childListeners.add(fn); },
      removeEventListener(type, fn) { if (type === 'message') childListeners.delete(fn); } };
    Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { locks: {}, usb: {} } });
    const target = { model: 'Test panel', serial: 'serial', primaryAbi: 'arm64-v8a', androidSdk: 33,
      rootMode: 'rootless', usbVendorId: 1, usbProductId: 2, usbSerial: 'usb' };
    globalThis.target = target;
    const descriptor = { apkSize: 1234, apkSha256: 'a'.repeat(64), packageId: 'io.panelassistant.android', versionName: '1.2.3' };
    globalThis.release = { kind: 'authenticated-apk-bytes', descriptor };
    const health = 'ha-paneld 1.2.3 panel=hall_panel build=42 cfg=0123abcd\\n';
    const opened = [];
    const socket = body => ({ readable: new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(body)); c.close(); } }), close: async () => {}, writable: new WritableStream() });
    globalThis.adb = { disconnected: new Promise(() => {}), close() {}, async createSocket(command) {
      const n = command.match(/BEGIN:([a-f0-9]{32})/)?.[1];
      if (!n) { opened.push(command); assert.equal(command, 'tcp:' + port + '\\0'); return socket('HTTP/1.1 200 OK\\r\\nContent-Length: ' + health.length + '\\r\\n\\r\\n' + health); }
      if (command.includes('HAPANELD_ADDR_BEGIN')) return socket('HAPANELD_ADDR_BEGIN:' + n + '\\n2: wlan0 inet 192.168.1.70/24 brd 192.168.1.255 scope global wlan0\\nHAPANELD_ADDR_END:' + n + '\\n');
      const step = command.match(/HAPANELD_([A-Z]+)_BEGIN/)[1];
      const section = (name, value) => ['HAPANELD_' + step + '_' + name + '_BEGIN:' + n, value, 'HAPANELD_' + step + '_' + name + '_END:' + n + ':0'];
      let lines;
      if (step === 'POSTURE') lines = Object.entries({ MODEL: target.model, SERIAL: target.serial, ABI: target.primaryAbi, SDK: '33', UID: '2000', SECURE: '1', DEBUGGABLE: '0', SU: 'absent' }).flatMap(([name, value]) => section(name, value));
      else if (step === 'INSTALLED') lines = ['present', '/data/app/test/base.apk', ...section('MODE', '81a4'), ...section('SIZE', '1234'), ...section('SHA', descriptor.apkSha256 + '  /data/app/test/base.apk')];
      else if (step === 'READY') { assert.ok(command.includes('/proc/net/tcp')); return socket('HAPANELD_READY_BEGIN:' + n + '\\n' + port + '\\nHAPANELD_READY_END:' + n + ':0\\n'); }
      else throw new Error('Unexpected shell step ' + step);
      return socket(['HAPANELD_' + step + '_BEGIN:' + n, ...lines, 'HAPANELD_' + step + '_END:' + n, ''].join('\\n'));
    } };
    const stubs = {
      '@yume-chan/adb': 'export const AdbCommand = {}; export class AdbTcpIpService {}; export class Adb { constructor() { return globalThis.adb; } }; export const AdbDaemonTransport = { authenticate: async () => ({}) };',
      '@yume-chan/adb-credential-web': 'export default class Store {}',
      '@yume-chan/adb-daemon-webusb': 'export class AdbDaemonWebUsbDevice { static DeviceBusyError = class extends Error {}; } export class AdbDaemonWebUsbDeviceManager { requestDevice() { return Promise.resolve({ raw: { close() {}, vendorId: 1, productId: 2, serialNumber: "usb" }, vendorId: 1, productId: 2, serialNumber: "usb", serial: "usb", connect: async () => ({}) }); } }',
      'usb-bounds.mjs': 'export const boundedUsb = value => value;',
      'session-target.mjs': 'export const inspectSessionTarget = async () => globalThis.target;',
      'job-store.mjs': 'export const openJobStore = async () => ({ close() {} });',
      'install-controller.mjs': 'export function createInstallController({ ports }) { return { preview: async () => ({ receipt: null }), install: async () => { const release = await ports.authenticate(); const state = await ports.inspect({ phase: "launching", target: globalThis.target }, release); if (!state.healthy) throw new Error("not healthy"); return { receipt: { id: "a".repeat(32) }, adopted: true, permissions: {} }; }, completeSetup: async () => ({}) }; }',
      'webview.mjs': 'export const provisionUsbWebView = async () => ({ outcome: "already_current" });',
      'panel-assistant-adb.mjs': 'export const panelAssistantKeyAuthenticator = () => {}; export const ensureNetworkAdb = async () => "already_on";',
      'release-handoff.mjs': 'export { handoffOptions, handOverThenOpen, requestSetupHandover } from ' + ${JSON.stringify(JSON.stringify(actual))} + '; export const requestPanelAssistantKey = async () => null; export const receiveReleaseHandoff = () => ({ cancel() {}, completion: Promise.resolve({ release: globalThis.release, authenticate: async () => globalThis.release }) });',
    };
    registerHooks({ resolve(specifier, context, next) { if (Object.hasOwn(stubs, specifier)) return { url: 'stub:' + specifier, shortCircuit: true }; return next(specifier, context); },
      load(url, context, next) { if (url.startsWith('stub:')) return { format: 'module', source: stubs[url.slice(5)], shortCircuit: true }; const name = url.split('/').at(-1); if (Object.hasOwn(stubs, name)) return { format: 'module', source: stubs[name], shortCircuit: true }; return next(url, context); } });
    try {
      await import(${JSON.stringify(main)}); await tick();
      await node('connect').listeners.get('click')();
      await node('install').listeners.get('click')();
      assert.equal(node('step-error').hidden, true, node('support-log').textContent);
      assert.deepEqual(opened, ['tcp:' + port + '\\0'], 'USB health uses the actual listener');
      assert.deepEqual(calls, [{ address: port === 8888 ? '192.168.1.70' : '192.168.1.70:' + port }], 'authenticated relay preserves the nondefault endpoint');
      assert.equal(node('open-setup').href, 'http://192.168.1.70:' + port + '/setup');
      assert.equal(node('step-done').hidden, false);
    } finally { handle.cancel(); }
  `], { stdio: 'pipe', timeout: 15000 });
});
