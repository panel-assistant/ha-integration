import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { buildAppReady, parseAppReady } from '../src/install-contract.mjs';
import { createUsbTransactionPorts } from '../src/usb-transaction-ports.mjs';
import { PACKAGE_ID } from '../src/app-identity.mjs';

const nonce = 'f'.repeat(32);
const frame = status => `HAPANELD_READY_BEGIN:${nonce}\nHAPANELD_READY_END:${nonce}:${status}\n`;

test('the app wait accepts either socket tool, retries only its port, and stops after one minute', t => {
  const root = mkdtempSync(join(tmpdir(), 'app-ready-'));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  let scenario = 0;
  for (const [tool, port, readyAt, expected, attempts] of [
    ['ss', 8888, 1, true, 1],
    ['netstat', 8888, 3, true, 3],
    ['ss', 18888, 1, false, 30],
    ['netstat', 8889, 1, false, 30],
    ['ss', 8888, 31, false, 30],
  ]) {
    // Execute the same shell program used by the USB ports. Only the panel's
    // socket tools and clock are replaced; the loop, matching and framing run.
    const state = join(root, String(scenario++));
    const result = spawnSync('/bin/sh', ['-c', `
exec 3>&2
attempt() { n=$(cat "${state}-$1" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "${state}-$1"; }
ss() { echo ss >&3; attempt ss; ${tool === 'ss' ? `if [ "$n" -ge ${readyAt} ]; then echo 'LISTEN 0 128 0.0.0.0:${port} 0.0.0.0:*'; fi` : ':'}; }
netstat() { echo netstat >&3; attempt netstat; ${tool === 'netstat' ? `if [ "$n" -ge ${readyAt} ]; then echo 'tcp 0 0 0.0.0.0:${port} 0.0.0.0:* LISTEN'; fi` : ':'}; }
sleep() { echo "sleep $*" >&2; }
am() { echo mutation >&2; }; pm() { echo mutation >&2; }; kill() { echo mutation >&2; }
rm() { echo mutation >&2; }; settings() { echo mutation >&2; }
${buildAppReady(nonce)}`], {encoding: 'utf8', timeout: 2000});
    const label = `${tool}:${port}, ready at ${readyAt}`;
    assert.equal(result.status, 0, label);
    assert.equal(parseAppReady(result.stdout, nonce), expected, label);
    const calls = result.stderr.trim().split('\n').filter(Boolean);
    assert.equal(calls.filter(call => call === tool).length, attempts, label);
    assert.deepEqual(calls.filter(call => call.startsWith('sleep ')),
      Array(expected ? attempts - 1 : attempts).fill('sleep 2'), label);
    assert.ok(!calls.includes('mutation'), 'waiting never changes the panel');
  }
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
  const descriptor = {apkSize: 1234, apkSha256: 'a'.repeat(64), packageId: PACKAGE_ID,
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
