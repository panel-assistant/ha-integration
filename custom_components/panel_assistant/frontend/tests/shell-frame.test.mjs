import test from 'node:test';
import assert from 'node:assert/strict';
import { decodeLines, readSections } from '../src/shell-session.mjs';
import { inspectSessionTarget } from '../src/session-target.mjs';
import { createUsbTransactionPorts } from '../src/usb-transaction-ports.mjs';
import { RESIDUE_PROBES, parsePreflight } from '../src/preflight.mjs';
import { ACCEPTED_PACKAGE_IDS } from '../src/app-identity.mjs';

const fail = () => { throw new Error('bad'); };
const nonce = 'a'.repeat(32);
const other = 'b'.repeat(32);

test('one decoder keeps each acceptance mode exactly', () => {
  const R = 'reject';
  const cases = [
    ['CRLF', 'a\r\nb\n', ['a', 'b'], ['a', 'b'], ['a', 'b']],
    ['missing trailing newline', 'a\nb', R, R, R],
    ['control char', 'a\x01\n', R, R, ['a\x01']],
    ['lone CR', 'a\rb\n', R, R, R],
    ['U+2028', 'a\u2028b\n', R, R, ['a', 'b']],
    ['lone surrogate', 'a\uD800\n', R, R, R],
    ['non-ASCII letter', 'é\n', ['é'], R, ['é']],
    ['tab', 'a\tb\n', R, ['a\tb'], ['a\tb']],
    ['oversize', `${'a'.repeat(64)}\n`, R, R, R],
    ['empty', '', R, R, R],
    ['bytes', new TextEncoder().encode('a\n'), ['a'], ['a'], ['a']],
    ['invalid UTF-8', new Uint8Array([0xff, 0x0a]), R, R, R],
    ['not text', 7, R, R, R],
  ];
  for (const [name, body, ...expected] of cases) {
    ['unicode', 'ascii', 'splitlines'].forEach((mode, i) => {
      if (expected[i] === R) assert.throws(() => decodeLines(body, 64, fail, mode), /bad/, `${name} ${mode}`);
      else assert.deepEqual(decodeLines(body, 64, fail, mode), expected[i], `${name} ${mode}`);
    });
  }
});

test('section walker refuses wrong nonces, stray frames and missing ends', () => {
  const frame = (n, inner) => [`HAPANELD_X_BEGIN:${n}`, `HAPANELD_X_A_BEGIN:${n}`, ...inner,
    `HAPANELD_X_A_END:${n}:3`, `HAPANELD_X_END:${n}`];
  assert.deepEqual(readSections(frame(nonce, ['v', '']), nonce, 'X', ['A'], fail),
    { A: { values: ['v', ''], status: 3 } });
  assert.throws(() => readSections(frame(other, ['v']), nonce, 'X', ['A'], fail), /bad/);
  assert.throws(() => readSections(frame(nonce, ['HAPANELD_Y']), nonce, 'X', ['A'], fail), /bad/);
  assert.throws(() => readSections(frame(nonce, ['v']), nonce, 'X', ['A', 'B'], fail), /bad/);
  const noEnd = frame(nonce, ['v']).filter(line => !line.includes('_A_END'));
  assert.throws(() => readSections(noEnd, nonce, 'X', ['A'], fail), /bad/);
});

const IDENTITY = { MODEL: 'Test panel', SERIAL: 'serial', ABI: 'arm64-v8a', SDK: '33' };
const LADDER = [
  [{ UID: '2000', SECURE: '1', DEBUGGABLE: '0', SU: 'absent' }, 'rootless'],
  [{ UID: '0', SECURE: '1', DEBUGGABLE: '0', SU: 'absent' }, 'root_adbd'],
  [{ UID: '2000', SECURE: '0', DEBUGGABLE: '1', SU: 'present' }, 'root_su'],
  [{ UID: '2000', SECURE: '0', DEBUGGABLE: '0', SU: 'absent' }, 'root_state_ambiguous'],
  [{ UID: '1000', SECURE: '1', DEBUGGABLE: '0', SU: 'absent' }, 'root_state_ambiguous'],
  [{ UID: '2000', SECURE: '1', DEBUGGABLE: '0', SU: 'abnormal' }, 'target_response_invalid'],
];
function postureBody(n, fields) {
  return [`HAPANELD_POSTURE_BEGIN:${n}`, ...Object.entries(fields).flatMap(([key, value]) =>
    [`HAPANELD_POSTURE_${key}_BEGIN:${n}`, value, `HAPANELD_POSTURE_${key}_END:${n}:0`]),
  `HAPANELD_POSTURE_END:${n}`, ''].join('\n');
}
function fakeAdb(fields) {
  return { async createSocket(command) {
    const n = command.match(/BEGIN:([a-f0-9]{32})/)[1];
    const body = postureBody(n, fields);
    return { readable: new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(body)); c.close(); } }),
      close: async () => {} };
  } };
}
const usbDevice = { vendorId: 1, productId: 2, serialNumber: 'usb' };

test('session target reads identity and the root ladder from posture', async () => {
  const descriptor = { supportedAbis: ['arm64-v8a', 'armeabi-v7a'], minSdk: 24 };
  for (const [root, expected] of LADDER) {
    const run = inspectSessionTarget(fakeAdb({ ...IDENTITY, ...root }), descriptor, usbDevice);
    if (['rootless', 'root_adbd', 'root_su'].includes(expected)) {
      const target = await run;
      assert.equal(target.rootMode, expected);
      assert.deepEqual([target.model, target.serial, target.primaryAbi, target.androidSdk],
        ['Test panel', 'serial', 'arm64-v8a', 33]);
    }
    else await assert.rejects(run, error => error.code === expected);
  }
  await assert.rejects(inspectSessionTarget(fakeAdb({ ...IDENTITY, SDK: '0', ...LADDER[0][0] }),
    descriptor, usbDevice), error => error.code === 'target_response_invalid');
});

test('posture refuses a changed target through the transaction ports', async () => {
  const target = { model: 'Test panel', serial: 'serial', primaryAbi: 'arm64-v8a', androidSdk: 33,
    rootMode: 'rootless', usbVendorId: 1, usbProductId: 2, usbSerial: 'usb' };
  const artifact = { apkSize: 1, apkSha256: 'a'.repeat(64), packageId: ACCEPTED_PACKAGE_IDS[0],
    launchComponent: `${ACCEPTED_PACKAGE_IDS[0]}/.Main` };
  const release = { kind: 'authenticated-apk-bytes', descriptor: artifact };
  for (const [change, root] of [[{ SERIAL: 'other' }, LADDER[0][0]], [{}, LADDER[1][0]]]) {
    const ports = createUsbTransactionPorts({ adb: fakeAdb({ ...IDENTITY, ...change, ...root }), usbDevice,
      authenticate: async () => release, quarantine: () => {} });
    await ports.authenticate();
    await assert.rejects(ports.commissionPermissions({ phase: 'healthy', target, artifact }, release),
      error => error.code === 'target_changed');
  }
});

function section(name, values, status = 0) {
  return [`HAPANELD_PREFLIGHT_${name}_BEGIN:${nonce}`, ...values,
    `HAPANELD_PREFLIGHT_${name}_END:${nonce}:${status}`];
}
function preflightBody(root, unreadable = false) {
  const lines = [`HAPANELD_PREFLIGHT_BEGIN:${nonce}`,
    ...Object.entries({ ...IDENTITY, ...root }).flatMap(([name, value]) => section(name, [value])),
    ...section('LIVE', ['package:/system/framework/framework-res.apk'])];
  ACCEPTED_PACKAGE_IDS.forEach((_, i) => lines.push(...section(`PACKAGE${i}`, [], 1),
    ...section(`RETAINED${i}`, [])));
  for (let i = 0; i < 3; i++) lines.push(...section(`BASE${i}`, [unreadable && i === 1 ? 'unreadable' : 'readable']));
  RESIDUE_PROBES.forEach((_, i) => lines.push(...section(`RESIDUE${i}`, ['absent'])));
  lines.push(`HAPANELD_PREFLIGHT_END:${nonce}`, '');
  return lines.join('\n');
}

test('preflight root ladder and admission', () => {
  const descriptor = { minSdk: 24, supportedAbis: ['arm64-v8a', 'armeabi-v7a'], packageId: ACCEPTED_PACKAGE_IDS[0] };
  for (const [root, expected] of LADDER) {
    if (['rootless', 'root_adbd', 'root_su'].includes(expected)) {
      const result = parsePreflight(preflightBody(root), nonce, descriptor);
      assert.equal(result.rootMode, expected);
      assert.equal(result.installationAdmission,
        expected === 'root_su' ? 'delegated_root_proof_required' : 'clean_preflight');
    } else assert.throws(() => parsePreflight(preflightBody(root), nonce, descriptor), error => error.code === expected);
  }
  assert.throws(() => parsePreflight(preflightBody(LADDER[1][0], true), nonce, descriptor),
    error => error.code === 'root_state_ambiguous');
  assert.equal(parsePreflight(preflightBody(LADDER[0][0], true), nonce, descriptor).rootMode, 'rootless');
});
