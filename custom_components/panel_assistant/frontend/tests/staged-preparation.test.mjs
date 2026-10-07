import test from 'node:test';
import assert from 'node:assert/strict';
import { createUsbTransactionPorts } from '../src/usb-transaction-ports.mjs';
import { ACCEPTED_PACKAGE_IDS, SUCCESSOR_PACKAGE_ID } from '../src/app-identity.mjs';
import { RESIDUE_PROBES } from '../src/preflight.mjs';
import { buildStagedPreparation, parseStagedPreparation, stagingPath } from '../src/staging-contract.mjs';
import { ENGLISH_MESSAGES } from '../src/frontend-localization.mjs';

const nonce = 'c'.repeat(32);
const job = 'd'.repeat(32);

test('preparation sets exactly 0644 on this job\'s own regular file and nothing else', () => {
  const program = buildStagedPreparation(nonce, job);
  const path = stagingPath(job);
  assert.ok(program.includes(`if [ -f ${path} ] && [ ! -L ${path} ]; then chmod 0644 ${path}; fi`));
  assert.equal((program.match(/chmod/g) ?? []).length, 1, 'one chmod, on one fixed path');
  assert.ok(!/rm |mv |cp |>/.test(program), 'preparation never moves, copies or writes content');
  assert.throws(() => buildStagedPreparation(nonce, '../../etc'), /invalid_request/);
});

test('preparation succeeds only on a clean, correctly framed zero exit', () => {
  const ok = `HAPANELD_PREPARE_BEGIN:${nonce}\nHAPANELD_PREPARE_END:${nonce}:0\n`;
  assert.equal(parseStagedPreparation(ok, nonce), true);
  for (const body of [
    `HAPANELD_PREPARE_BEGIN:${nonce}\nHAPANELD_PREPARE_END:${nonce}:1\n`,
    `HAPANELD_PREPARE_BEGIN:${nonce}\nchmod: Operation not permitted\nHAPANELD_PREPARE_END:${nonce}:0\n`,
    `HAPANELD_PREPARE_BEGIN:${'e'.repeat(32)}\nHAPANELD_PREPARE_END:${nonce}:0\n`,
  ]) {
    assert.throws(() => parseStagedPreparation(body, nonce), /staged_preparation_failed/);
  }
});

test('both staged-file checks prepare the file before reading its mode', async () => {
  const target = {model: 'Test panel', serial: 'serial', primaryAbi: 'arm64-v8a', androidSdk: 34,
    rootMode: 'rootless', usbVendorId: 1, usbProductId: 2, usbSerial: 'usb'};
  const release = {kind: 'authenticated-apk-bytes',
    descriptor: {apkSize: 1234, apkSha256: 'a'.repeat(64), packageId: SUCCESSOR_PACKAGE_ID, minSdk: 21,
      supportedAbis: ['arm64-v8a', 'armeabi-v7a']}};
  const identity = {MODEL: target.model, SERIAL: target.serial, ABI: target.primaryAbi, SDK: '34',
    UID: '2000', SECURE: '1', DEBUGGABLE: '0', SU: 'absent'};
  // 'staged' reaches the plain check, 'staging' after a lost upload reaches the prefix check.
  for (const phase of ['staged', 'staging']) {
    const seen = [];
    const adb = {async createSocket(command) {
      const n = command.match(/BEGIN:([a-f0-9]{32})/)[1];
      const step = command.match(/HAPANELD_([A-Z]+)_BEGIN/)[1];
      seen.push(step);
      const section = (name, values) => [`HAPANELD_${step}_${name}_BEGIN:${n}`, ...values,
        `HAPANELD_${step}_${name}_END:${n}:${values.length ? 0 : 1}`];
      let lines;
      if (step === 'POSTURE') {
        lines = Object.entries(identity).flatMap(([name, value]) => section(name, [value]));
      } else if (step === 'PREFLIGHT') {
        lines = [...Object.entries(identity).flatMap(([name, value]) => section(name, [value])),
          ...section('LIVE', ['package:/system/framework/framework-res.apk']),
          ...ACCEPTED_PACKAGE_IDS.flatMap((_, i) => [...section(`PACKAGE${i}`, []),
            `HAPANELD_PREFLIGHT_RETAINED${i}_BEGIN:${n}`, `HAPANELD_PREFLIGHT_RETAINED${i}_END:${n}:0`]),
          ...[0, 1, 2].flatMap(i => section(`BASE${i}`, ['readable'])),
          ...RESIDUE_PROBES.flatMap((_, i) => section(`RESIDUE${i}`, ['absent']))];
      } else if (step === 'PATH') {
        return shell(`HAPANELD_PATH_BEGIN:${n}\npresent\nHAPANELD_PATH_END:${n}:0\n`);
      } else if (step === 'PREPARE') {
        return shell(`HAPANELD_PREPARE_BEGIN:${n}\nHAPANELD_PREPARE_END:${n}:1\n`);
      } else throw new Error('the mode was read');
      return shell([`HAPANELD_${step}_BEGIN:${n}`, ...lines, `HAPANELD_${step}_END:${n}`, ''].join('\n'));
    }};
    const ports = createUsbTransactionPorts({adb, usbDevice: {vendorId: 1, productId: 2, serialNumber: 'usb'},
      authenticate: async () => release, quarantine() {}});
    await ports.authenticate();
    await assert.rejects(ports.inspect({id: job, phase, target}, release), /staged_preparation_failed/);
    assert.equal(seen.at(-1), 'PREPARE', `${phase}: a failed preparation stops before any observation`);
    assert.ok(!seen.includes('STAGED'), `${phase} prepares before it observes`);
  }
});

const shell = body => ({readable: new ReadableStream({start(controller) {
  controller.enqueue(new TextEncoder().encode(body)); controller.close();
}}), close: async () => {}});

test('no message ever tells a person to unplug a panel that may be powered by that cable', () => {
  const shown = [...Object.values(ENGLISH_MESSAGES.errors), ...Object.values(ENGLISH_MESSAGES.installer)].join('\n');
  assert.ok(!/unplug/i.test(shown), 'unplugging a USB-powered panel cuts its power mid-install');
});
