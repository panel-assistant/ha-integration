import test from 'node:test';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, writeFileSync, readFileSync, chmodSync, statSync, lstatSync, symlinkSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert/strict';
import { createUsbTransactionPorts } from '../src/usb-transaction-ports.mjs';
import { LEGACY_PACKAGE_ID, PACKAGE_ID } from '../src/app-identity.mjs';
import { RESIDUE_PROBES } from '../src/preflight.mjs';
import { buildStagedPreparation, parseStagedPreparation, stagingPath } from '../src/staging-contract.mjs';
import { ENGLISH_MESSAGES } from '../src/frontend-localization.mjs';

// Preflight frames the old app's sections first, then this app's.
const PROBED = [LEGACY_PACKAGE_ID, PACKAGE_ID];
const nonce = 'c'.repeat(32);
const job = 'd'.repeat(32);

test('preparation sets 0644 on only this job’s regular file and preserves its bytes', t => {
  const root = mkdtempSync(join(tmpdir(), 'staged-preparation-'));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  const own = join(root, 'own.apk'), neighbour = join(root, 'neighbour.apk');
  writeFileSync(own, 'uploaded bytes');
  chmodSync(own, 0o666);
  writeFileSync(neighbour, 'unrelated bytes', {mode: 0o600});
  const run = () => {
    // Only the device path is mapped; the real file checks and chmod execute.
    const result = spawnSync('/bin/sh', ['-c', buildStagedPreparation(nonce, job)
      .replaceAll(stagingPath(job), own)], {encoding: 'utf8', timeout: 2000});
    assert.equal(result.status, 0);
    assert.equal(parseStagedPreparation(result.stdout, nonce), true);
  };
  run();
  assert.equal(statSync(own).mode & 0o777, 0o644);
  assert.equal(readFileSync(own, 'utf8'), 'uploaded bytes');
  assert.equal(statSync(neighbour).mode & 0o777, 0o600);
  assert.equal(readFileSync(neighbour, 'utf8'), 'unrelated bytes');
  rmSync(own);
  symlinkSync(neighbour, own);
  run();
  assert.ok(lstatSync(own).isSymbolicLink());
  assert.equal(statSync(neighbour).mode & 0o777, 0o600, 'a symlink target is never chmodded');
  assert.equal(readFileSync(neighbour, 'utf8'), 'unrelated bytes');
  rmSync(own);
  mkdirSync(own, {mode: 0o700});
  run();
  assert.equal(statSync(own).mode & 0o777, 0o700, 'a directory is never chmodded');
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
    descriptor: {apkSize: 1234, apkSha256: 'a'.repeat(64), packageId: PACKAGE_ID, minSdk: 21,
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
          ...PROBED.flatMap((_, i) => [...section(`PACKAGE${i}`, []),
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

test('a lost upload\'s staged copy is matched to its release prefix and cleaned up by that digest', async t => {
  const root = mkdtempSync(join(tmpdir(), 'prefix-cleanup-'));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  mkdirSync(join(root, 'data/local/tmp'), {recursive: true});
  const localPath = join(root, stagingPath(job));
  const apk = new Blob([new TextEncoder().encode('abcd')]);
  const target = {model: 'Test panel', serial: 'serial', primaryAbi: 'arm64-v8a', androidSdk: 34,
    rootMode: 'rootless', usbVendorId: 1, usbProductId: 2, usbSerial: 'usb'};
  const release = {kind: 'authenticated-apk-bytes', apk,
    descriptor: {apkSize: 4, apkSha256: 'a'.repeat(64), packageId: PACKAGE_ID, minSdk: 21,
      supportedAbis: ['arm64-v8a', 'armeabi-v7a']}};
  const identity = {MODEL: target.model, SERIAL: target.serial, ABI: target.primaryAbi, SDK: '34',
    UID: '2000', SECURE: '1', DEBUGGABLE: '0', SU: 'absent'};
  // The panel reports what it holds; 'xy' is a copy that is not this release's prefix.
  for (const [copied, held] of [['ab', 'ab'], ['abcd', 'abcd'], ['ab', 'xy']]) {
    const digest = createHash('sha256').update(held).digest('hex');
    const cleanups = [];
    const adb = {async createSocket(command) {
      const n = command.match(/BEGIN:([a-f0-9]{32})/)[1];
      const step = command.match(/HAPANELD_([A-Z]+)_BEGIN/)[1];
      const section = (name, values) => [`HAPANELD_${step}_${name}_BEGIN:${n}`, ...values,
        `HAPANELD_${step}_${name}_END:${n}:${values.length ? 0 : 1}`];
      let lines;
      if (step === 'POSTURE') {
        lines = Object.entries(identity).flatMap(([name, value]) => section(name, [value]));
      } else if (step === 'PREFLIGHT') {
        lines = [...Object.entries(identity).flatMap(([name, value]) => section(name, [value])),
          ...section('LIVE', ['package:/system/framework/framework-res.apk']),
          ...PROBED.flatMap((_, i) => [...section(`PACKAGE${i}`, []),
            `HAPANELD_PREFLIGHT_RETAINED${i}_BEGIN:${n}`, `HAPANELD_PREFLIGHT_RETAINED${i}_END:${n}:0`]),
          ...[0, 1, 2].flatMap(i => section(`BASE${i}`, ['readable'])),
          ...RESIDUE_PROBES.flatMap((_, i) => section(`RESIDUE${i}`, ['absent']))];
      } else if (step === 'PATH') {
        return shell(`HAPANELD_PATH_BEGIN:${n}\npresent\nHAPANELD_PATH_END:${n}:0\n`);
      } else if (step === 'PREPARE') {
        return shell(`HAPANELD_PREPARE_BEGIN:${n}\nHAPANELD_PREPARE_END:${n}:0\n`);
      } else if (step === 'STAGED') {
        lines = [...section('MODE', ['81a4']), ...section('SIZE', [String(copied.length)]),
          ...section('SHA', [`${digest}  ${stagingPath(job)}`])];
      } else if (step === 'CLEANUP') {
        cleanups.push(command);
        const run = bytes => {
          writeFileSync(localPath, bytes, {mode: 0o644});
          return spawnSync('/bin/sh', ['-c', `stat() { if [ "$2" = %u ]; then echo 2000; else command stat "$@"; fi; }
${command.slice('shell:'.length).replaceAll('/data/', `${root}/data/`).replaceAll('[ ! -L /data ]', `[ ! -L ${root}/data ]`)}`],
            {encoding: 'utf8', timeout: 2000});
        };
        for (const changed of ['x'.repeat(held.length), held + 'x']) {
          const refused = run(changed);
          assert.equal(refused.status, 0);
          assert.ok(existsSync(localPath), 'cleanup must keep a file changed since its observation');
          assert.equal(readFileSync(localPath, 'utf8'), changed);
        }
        const removed = run(held);
        assert.equal(removed.status, 0);
        assert.equal(existsSync(localPath), false, 'cleanup removes only its observed bytes');
        return shell(removed.stdout);
      } else throw new Error(`unexpected ${step}`);
      return shell([`HAPANELD_${step}_BEGIN:${n}`, ...lines, `HAPANELD_${step}_END:${n}`, ''].join('\n'));
    }};
    const ports = createUsbTransactionPorts({adb, usbDevice: {vendorId: 1, productId: 2, serialNumber: 'usb'},
      authenticate: async () => release, quarantine() {}});
    await ports.authenticate();
    if (copied !== held) {
      await assert.rejects(ports.inspect({id: job, phase: 'staging', target}, release), /staged_prefix_mismatch/);
      continue;
    }
    const inspected = await ports.inspect({id: job, phase: 'staging', target}, release);
    assert.equal(inspected.staged, copied.length === 4, `${copied}: a whole copy is staged, a partial one is not`);
    await ports.cleanup({id: job, phase: 'cleanup_pending', target}, release);
    assert.equal(cleanups.length, 1);
  }
});

const shell = body => ({readable: new ReadableStream({start(controller) {
  controller.enqueue(new TextEncoder().encode(body)); controller.close();
}}), close: async () => {}});

// Source-text reason: USB-powered panels must never be told to cut installation power.
test('no message ever tells a person to unplug a panel that may be powered by that cable', () => {
  const shown = [...Object.values(ENGLISH_MESSAGES.errors), ...Object.values(ENGLISH_MESSAGES.installer)].join('\n');
  assert.ok(!/unplug/i.test(shown), 'unplugging a USB-powered panel cuts its power mid-install');
});
