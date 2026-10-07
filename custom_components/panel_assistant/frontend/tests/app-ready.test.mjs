import test from 'node:test';
import assert from 'node:assert/strict';
import { buildAppReady, parseAppReady } from '../src/install-contract.mjs';
import { createUsbTransactionPorts } from '../src/usb-transaction-ports.mjs';
import { SUCCESSOR_PACKAGE_ID } from '../src/app-identity.mjs';

const nonce = 'f'.repeat(32);
const frame = status => `HAPANELD_READY_BEGIN:${nonce}\nHAPANELD_READY_END:${nonce}:${status}\n`;

test('the wait is a bounded, read-only loop on the app port', () => {
  const program = buildAppReady(nonce);
  assert.match(program, /while \[ \$i -lt 30 \]/, 'at most 30 checks');
  assert.match(program, /sleep 2/, 'two seconds apart, about a minute in all');
  assert.match(program, /grep -q ':8888 '/);
  assert.ok(!/am |pm |kill|rm |settings /.test(program), 'waiting never starts, stops or changes anything');
  assert.throws(() => buildAppReady('bad'));
});

test('listening and not-yet-listening are both answers, anything else is malformed', () => {
  assert.equal(parseAppReady(frame(0), nonce), true);
  assert.equal(parseAppReady(frame(1), nonce), false);
  assert.throws(() => parseAppReady(frame(2), nonce));
  assert.throws(() => parseAppReady(`HAPANELD_READY_BEGIN:${nonce}\nstray\nHAPANELD_READY_END:${nonce}:0\n`, nonce));
});

test('the strict health read runs only after waiting for the app', async () => {
  const target = {model: 'Test panel', serial: 'serial', primaryAbi: 'arm64-v8a', androidSdk: 33,
    rootMode: 'rootless', usbVendorId: 1, usbProductId: 2, usbSerial: 'usb'};
  const descriptor = {apkSize: 1234, apkSha256: 'a'.repeat(64), packageId: SUCCESSOR_PACKAGE_ID,
    versionName: '1.2.3'};
  const release = {kind: 'authenticated-apk-bytes', descriptor};
  const seen = [];
  const adb = {async createSocket(command) {
    const n = command.match(/BEGIN:([a-f0-9]{32})/)?.[1];
    const step = n ? command.match(/HAPANELD_([A-Z]+)_BEGIN/)[1] : 'HEALTH';
    seen.push(step);
    const section = (name, value) => [`HAPANELD_${step}_${name}_BEGIN:${n}`, value, `HAPANELD_${step}_${name}_END:${n}:0`];
    let lines;
    if (step === 'POSTURE') {
      lines = Object.entries({MODEL: target.model, SERIAL: target.serial, ABI: target.primaryAbi, SDK: '33',
        UID: '2000', SECURE: '1', DEBUGGABLE: '0', SU: 'absent'}).flatMap(([name, value]) => section(name, value));
    } else if (step === 'INSTALLED') {
      const path = '/data/app/test/base.apk';
      lines = ['present', path, ...section('MODE', '81a4'), ...section('SIZE', '1234'),
        ...section('SHA', `${descriptor.apkSha256}  ${path}`)];
    } else if (step === 'READY') {
      return shell(`HAPANELD_READY_BEGIN:${n}\nHAPANELD_READY_END:${n}:1\n`);
    } else throw new Error('stop after the first health read');
    return shell([`HAPANELD_${step}_BEGIN:${n}`, ...lines, `HAPANELD_${step}_END:${n}`, ''].join('\n'));
  }};
  const shell = body => ({readable: new ReadableStream({start(controller) {
    controller.enqueue(new TextEncoder().encode(body)); controller.close();
  }}), close: async () => {}});
  const ports = createUsbTransactionPorts({adb, usbDevice: {vendorId: 1, productId: 2, serialNumber: 'usb'},
    authenticate: async () => release, quarantine() {}});
  await ports.authenticate();
  await assert.rejects(ports.inspect({phase: 'launching', target}, release));
  assert.deepEqual(seen, ['POSTURE', 'INSTALLED', 'READY', 'HEALTH'], 'wait for the app, then read its health');
});
