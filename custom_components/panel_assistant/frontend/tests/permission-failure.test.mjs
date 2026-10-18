import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { FRONTEND_TRANSLATIONS } from '../src/translations/index.mjs';

// The real installer page and the real USB ports, against a panel that answers
// the permission step's shell commands. Only the chooser, the ADB transport
// and the job's earlier steps are stubbed: the install press goes straight to
// granting permissions, as it does for a job that is already healthy.
function failedPermissions(panelOptions) {
  const main = new URL('../src/install-main.mjs', import.meta.url).href;
  const panel = new URL('./fixtures/permission-panel.mjs', import.meta.url).href;
  const script = `
    import { registerHooks } from 'node:module';
    import { permissionPanel } from ${JSON.stringify(panel)};
    const options = ${panelOptions};
    globalThis.panel = permissionPanel(options);
    const elements = new Map();
    const node = id => {
      if (!elements.has(id)) elements.set(id, { hidden: false, dataset: {}, style: {}, textContent: '', attributes: new Map(), children: [],
        listeners: new Map(), addEventListener(type, fn) { this.listeners.set(type, fn); },
        querySelector() { return node(id + ':child'); }, setAttribute(name, value) { this.attributes.set(name, value); }, replaceChildren(...children) { this.children = children; } });
      return elements.get(id);
    };
    globalThis.document = { documentElement: {}, getElementById: node, querySelectorAll: () => [],
      createElement: () => ({ attributes: new Map(), setAttribute(name, value) { this.attributes.set(name, value); } }) };
    globalThis.location = { search: '?lang=en' };
    globalThis.window = { isSecureContext: true, opener: { closed: false }, location: { hash: '#handoff', reload() {}, assign() {} },
      addEventListener() {} };
    Object.defineProperty(globalThis, 'navigator', { value: { locks: {}, usb: {} }, configurable: true });
    globalThis.indexedDB = {};
    const stubs = {
      '@yume-chan/adb': 'export const AdbCommand = {}; export class AdbTcpIpService {}; export class Adb { disconnected = new Promise(() => {}); createSocket(command) { return globalThis.panel.adb.createSocket(command); } close() {} }; export const AdbDaemonTransport = { authenticate: async () => ({}) };',
      '@yume-chan/adb-credential-web': 'export default class Store {}',
      '@yume-chan/adb-daemon-webusb': 'export class AdbDaemonWebUsbDeviceManager { requestDevice() { return Promise.resolve({ raw: { ...globalThis.panel.usbDevice, close() {} }, serial: "panel", connect: async () => ({}) }); } }',
      'usb-bounds.mjs': 'export const boundedUsb = value => value;',
      'session-target.mjs': 'export const inspectSessionTarget = async () => globalThis.panel.target;',
      'job-store.mjs': 'export const openJobStore = async () => ({ close() {} });',
      'install-controller.mjs': 'export function createInstallController({ ports }) { return { preview: async () => ({ receipt: null }), install: async () => { await ports.authenticate(); return { permissions: await ports.commissionPermissions(globalThis.panel.receipt, globalThis.panel.release) }; } }; }',
      'release-handoff.mjs': 'export const handoffOptions = () => ({}); export const handOverThenOpen = async () => {}; export const requestSetupHandover = async () => {}; export const requestPanelAssistantKey = async () => null; export const receiveReleaseHandoff = () => ({ cancel() {}, completion: Promise.resolve({ release: globalThis.panel.release, authenticate: async () => globalThis.panel.release }) });',
    };
    registerHooks({
      resolve(specifier, context, next) { if (Object.hasOwn(stubs, specifier)) return { url: 'stub:' + specifier, shortCircuit: true }; return next(specifier, context); },
      load(url, context, next) {
        if (url.startsWith('stub:')) return { format: 'module', source: stubs[url.slice(5)], shortCircuit: true };
        const name = url.split('/').at(-1);
        if (Object.hasOwn(stubs, name) && !url.includes('/tests/')) return { format: 'module', source: stubs[name], shortCircuit: true };
        return next(url, context);
      },
    });
    const tick = () => new Promise(resolve => setImmediate(resolve));
    await import(${JSON.stringify(main)});
    await tick();
    await node('connect').listeners.get('click')();
    await node('install').listeners.get('click')();
    await tick();
    process.stdout.write(JSON.stringify({ error: node('step-error').hidden === false,
      text: node('error-text').textContent, support: node('support-log').textContent,
      granted: globalThis.panel.commands.some(command => command.includes('PERMISSIONS_GRANT_BEGIN')) }));
  `;
  return JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e', script], { encoding: 'utf8' }));
}

const english = FRONTEND_TRANSLATIONS.en;

test('a permission readback with one wrong line says the permissions were not confirmed and names that line', () => {
  // Line 6 of the readback: notifications refused on Android 13.
  const result = failedPermissions(`{ verifyLines: lines => lines.with(5,
    'android.permission.POST_NOTIFICATIONS: granted=false, flags=[ USER_SET ]') }`);
  assert.equal(result.granted, true);
  assert.equal(result.error, true);
  assert.equal(result.text, english.errors.installErrorPermissions);
  assert.match(result.support, new RegExp(`${english.support.failedCheck} \\(\\+[0-9.]+s\\)\\n` +
    'verify line 6 \\(notifications\\): "android\\.permission\\.POST_NOTIFICATIONS: granted=false'));
});

test('a grant program that stops on a failed command is named by its status', () => {
  const result = failedPermissions('{ failGrant: true }');
  assert.equal(result.text, english.errors.installErrorPermissions);
  assert.match(result.support, /grant: a command failed \(status "1"\)/);
});

test('the other apps’ accessibility services are counted, never shown', () => {
  const result = failedPermissions(`{ verifyLines: lines => lines.with(0, 'com.other/.Reader:com.secret/.Service') }`);
  assert.equal(result.text, english.errors.installErrorPermissions);
  assert.match(result.support, /verify line 1 \(accessibility services\): 2 entries/);
  assert.doesNotMatch(result.support, /com\.secret/);
});
