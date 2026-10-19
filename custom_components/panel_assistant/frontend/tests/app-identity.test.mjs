import test from 'node:test';
import assert from 'node:assert/strict';
import { LAUNCH_COMPONENTS, LEGACY_PACKAGE_ID, PACKAGE_ID,
  accessibilityComponentFor, isLaunchComponent } from '../src/app-identity.mjs';
import { PreflightError, RESIDUE_PROBES, buildPreflight, classifyTarget,
  parsePreflight } from '../src/preflight.mjs';
import { DelegatedProofError, parseDelegatedProof } from '../src/delegated-proof.mjs';
import { InstallContractError, buildLaunch } from '../src/install-contract.mjs';
import { InstalledObservationError, buildInstalledObservation } from '../src/installed-observation.mjs';
import { buildVendorDisplaySettlement } from '../src/vendor-provisioning.mjs';
import { buildHomeClaim } from '../src/home-chooser.mjs';
import { createUsbTransactionPorts } from '../src/usb-transaction-ports.mjs';

const nonce = 'a'.repeat(32);
const notClean = error => error instanceof PreflightError && error.code === 'target_not_clean';
const runsTheApp = error => error instanceof PreflightError && error.code === 'target_installed';

const MOVED = 'io.panelassistant.android/io.panelassistant.android.MainActivity';
const OLD_APP_LAUNCH = 'io.github.maxlyth.hapaneld/.MainActivity';
// Preflight still probes the old app, first, so a panel holding it is refused.
const PROBED = [LEGACY_PACKAGE_ID, PACKAGE_ID];

test('the one app id names its own components, old classes and moved ones', () => {
  // Up to app 0.9.9 the classes stay in the old package; from 0.9.10 they live
  // in the app's own package.
  assert.deepEqual(Object.keys(LAUNCH_COMPONENTS), [PACKAGE_ID]);
  assert.deepEqual(LAUNCH_COMPONENTS[PACKAGE_ID],
    ['io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity', MOVED]);
  assert.equal(accessibilityComponentFor(LAUNCH_COMPONENTS[PACKAGE_ID][0]),
    'io.panelassistant.android/io.github.maxlyth.hapaneld.input.PanelAccessibilityService');
  assert.equal(accessibilityComponentFor(MOVED),
    'io.panelassistant.android/io.panelassistant.android.input.PanelAccessibilityService');
  for (const component of LAUNCH_COMPONENTS[PACKAGE_ID]) {
    assert.ok(isLaunchComponent(PACKAGE_ID, component));
    assert.ok(!isLaunchComponent(LEGACY_PACKAGE_ID, component));
  }
  // The old app's own launcher is not a build this installer drives.
  assert.ok(!isLaunchComponent(LEGACY_PACKAGE_ID, OLD_APP_LAUNCH));
  assert.ok(!isLaunchComponent(PACKAGE_ID, OLD_APP_LAUNCH));
  assert.throws(() => accessibilityComponentFor(OLD_APP_LAUNCH), RangeError);
  for (const unknown of ['io.example.other', 'io.panelassistant', '']) {
    assert.ok(!isLaunchComponent(unknown, MOVED));
  }
  assert.throws(() => accessibilityComponentFor('io.panelassistant.android/.MainActivity'), RangeError);
});

test("launch starts the descriptor's own package by its own component", () => {
  for (const component of LAUNCH_COMPONENTS[PACKAGE_ID]) {
    assert.ok(buildLaunch(nonce, PACKAGE_ID, component, 33).includes(
      `am start -W -n ${component} -p io.panelassistant.android`));
  }
  assert.throws(() => buildLaunch(nonce, 'io.example.other', MOVED, 33), InstallContractError);
  // The old app is never started, by its own launcher or the new one.
  assert.throws(() => buildLaunch(nonce, LEGACY_PACKAGE_ID, OLD_APP_LAUNCH, 33), InstallContractError);
  assert.throws(() => buildLaunch(nonce, LEGACY_PACKAGE_ID, MOVED, 33), InstallContractError);
});

test('no panel program is built for the old app', () => {
  assert.ok(buildInstalledObservation(nonce, PACKAGE_ID).includes(`pm path ${PACKAGE_ID})`));
  assert.throws(() => buildInstalledObservation(nonce, LEGACY_PACKAGE_ID),
    error => error instanceof InstalledObservationError && error.code === 'invalid_request');
  assert.ok(buildVendorDisplaySettlement(nonce, PACKAGE_ID).includes(PACKAGE_ID));
  assert.throws(() => buildVendorDisplaySettlement(nonce, LEGACY_PACKAGE_ID), /vendor_setup_unreadable/);
  for (const component of LAUNCH_COMPONENTS[PACKAGE_ID]) assert.match(buildHomeClaim(component), /set-home-activity/);
  assert.throws(() => buildHomeClaim(OLD_APP_LAUNCH), RangeError);
});

test('the preflight still reads the old app and its data, as well as this one', () => {
  const program = buildPreflight(nonce);
  assert.deepEqual([...new Set(RESIDUE_PROBES.map(({ packageId }) => packageId))], PROBED);
  for (const packageId of PROBED) {
    assert.ok(program.includes(`pm path ${packageId};`), packageId);
    assert.ok(program.includes(`pm list packages -u ${packageId};`), packageId);
    for (const base of ['/data/user/0', '/data/data', '/data/user_de/0']) {
      assert.ok(program.includes(`[ -e ${base}/${packageId} ]`), `${base}/${packageId}`);
    }
  }
});

test('USB classification refuses every existing panel, the old app included', () => {
  const none = new Set();
  assert.equal(classifyTarget(PACKAGE_ID, [], none), false);
  // Anything of the old app, or this app's id kept without the app running, is unclean.
  for (const installed of [[LEGACY_PACKAGE_ID], [PACKAGE_ID], PROBED]) {
    assert.throws(() => classifyTarget(PACKAGE_ID, installed, none), notClean);
    for (const packageId of PROBED) {
      assert.throws(() => classifyTarget(PACKAGE_ID, installed, new Set([packageId])), notClean);
    }
  }
  for (const packageId of PROBED) {
    assert.throws(() => classifyTarget(PACKAGE_ID, [], new Set([packageId])), notClean);
  }
  // A panel running this app alone, its own data included, is told apart.
  for (const residue of [none, new Set([PACKAGE_ID])]) {
    assert.throws(() => classifyTarget(PACKAGE_ID, [PACKAGE_ID], residue, [PACKAGE_ID]), runsTheApp);
  }
  assert.throws(() => classifyTarget(PACKAGE_ID, PROBED, none, PROBED), notClean);
  assert.throws(() => classifyTarget(PACKAGE_ID, [PACKAGE_ID], new Set(PROBED), [PACKAGE_ID]), notClean);
  // The old app is never an install target.
  assert.throws(() => classifyTarget(LEGACY_PACKAGE_ID, [], none),
    error => error instanceof PreflightError && error.code === 'invalid_request');
});

// Whole-body cases: `classifyTarget` above is the rule, these prove the wiring
// that feeds it — which section index carries which application id.
const DESCRIPTOR = Object.freeze({ minSdk: 26, supportedAbis: ['arm64-v8a', 'armeabi-v7a'] });

function section(name, values, status = 0) {
  return [`HAPANELD_PREFLIGHT_${name}_BEGIN:${nonce}`, ...values,
    `HAPANELD_PREFLIGHT_${name}_END:${nonce}:${status}`];
}

function preflightBody({ installed = [], retained = [], residue = [] } = {}) {
  const lines = [`HAPANELD_PREFLIGHT_BEGIN:${nonce}`,
    ...section('MODEL', ['Test Panel']), ...section('SERIAL', ['SERIAL-1']),
    ...section('ABI', ['arm64-v8a']), ...section('SDK', ['34']),
    ...section('UID', ['2000']), ...section('SECURE', ['1']),
    ...section('DEBUGGABLE', ['0']), ...section('SU', ['absent']),
    ...section('LIVE', ['package:/system/framework/framework-res.apk'])];
  PROBED.forEach((packageId, index) => {
    const present = installed.includes(packageId);
    // Retained alone: uninstalled with its data kept, so `pm path` finds nothing.
    const kept = present || retained.includes(packageId);
    lines.push(...section(`PACKAGE${index}`,
      present ? [`package:/data/app/${packageId}-1/base.apk`] : [], present ? 0 : 1));
    lines.push(...section(`RETAINED${index}`, kept ? [`package:${packageId}`] : []));
  });
  for (let index = 0; index < 3; index += 1) lines.push(...section(`BASE${index}`, ['readable']));
  RESIDUE_PROBES.forEach(({ packageId }, index) => lines.push(
    ...section(`RESIDUE${index}`, [residue.includes(packageId) ? 'present' : 'absent'])));
  lines.push(`HAPANELD_PREFLIGHT_END:${nonce}`, '');
  return lines.join('\n');
}

test('a whole preflight body reads each probed id from its own sections', () => {
  const target = { ...DESCRIPTOR, packageId: PACKAGE_ID };
  const clean = parsePreflight(preflightBody(), nonce, target);
  assert.equal(clean.rootMode, 'rootless');
  assert.equal(clean.installationAdmission, 'clean_preflight');

  // Running the app (pm path finds it) differs from keeping its id after an uninstall.
  assert.throws(() => parsePreflight(preflightBody({ installed: [PACKAGE_ID] }), nonce, target), runsTheApp);
  assert.throws(() => parsePreflight(preflightBody({ installed: [PACKAGE_ID], residue: [PACKAGE_ID] }),
    nonce, target), runsTheApp);
  assert.throws(() => parsePreflight(preflightBody({ installed: PROBED }), nonce, target), notClean);
  assert.throws(() => parsePreflight(preflightBody({ installed: [PACKAGE_ID], retained: [LEGACY_PACKAGE_ID] }),
    nonce, target), notClean);
  assert.throws(() => parsePreflight(preflightBody({ retained: [PACKAGE_ID] }), nonce, target), notClean);
  assert.throws(() => parsePreflight(preflightBody({ residue: [PACKAGE_ID] }), nonce, target), notClean);
  // An old-app descriptor is never admitted, even on a clean panel.
  assert.throws(() => parsePreflight(preflightBody(), nonce, { ...DESCRIPTOR, packageId: LEGACY_PACKAGE_ID }),
    error => error instanceof PreflightError && error.code === 'invalid_request');
});

// Through the USB ports the installer uses: a refused clean check closes the
// session, except a panel that already runs the app, which the installer still
// needs the session for, to give Panel Assistant its route back.
async function cleanCheck(installed) {
  const target = { model: 'Test Panel', serial: 'SERIAL-1', primaryAbi: 'arm64-v8a', androidSdk: 34,
    rootMode: 'rootless', usbVendorId: 1, usbProductId: 2, usbSerial: 'usb' };
  const release = { kind: 'authenticated-apk-bytes', descriptor: { ...DESCRIPTOR, packageId: PACKAGE_ID } };
  const shell = body => ({ readable: new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode(body)); controller.close();
  } }), close: async () => {} });
  const adb = { async createSocket(command) {
    const n = command.match(/BEGIN:([a-f0-9]{32})/)[1];
    if (command.includes('HAPANELD_PREFLIGHT_BEGIN')) return shell(preflightBody({ installed }).replaceAll(nonce, n));
    if (!command.includes('HAPANELD_POSTURE_BEGIN')) throw new Error('nothing but posture and preflight is read');
    const identity = { MODEL: 'Test Panel', SERIAL: 'SERIAL-1', ABI: 'arm64-v8a', SDK: '34', UID: '2000',
      SECURE: '1', DEBUGGABLE: '0', SU: 'absent' };
    return shell([`HAPANELD_POSTURE_BEGIN:${n}`, ...Object.entries(identity).flatMap(([name, value]) => [
      `HAPANELD_POSTURE_${name}_BEGIN:${n}`, value, `HAPANELD_POSTURE_${name}_END:${n}:0`]),
    `HAPANELD_POSTURE_END:${n}`, ''].join('\n'));
  } };
  let stopped = 0;
  const ports = createUsbTransactionPorts({ adb, usbDevice: { vendorId: 1, productId: 2, serialNumber: 'usb' },
    authenticate: async () => release, quarantine() { stopped += 1; } });
  await ports.authenticate();
  const error = await ports.inspect({ id: 'd'.repeat(32), phase: 'prepared', target }, release)
    .then(() => null, caught => caught);
  return { code: error?.code, stopped };
}

test('the USB ports keep the session for a panel that runs the app, and close it for any other refusal', async () => {
  assert.deepEqual(await cleanCheck([PACKAGE_ID]), { code: 'target_installed', stopped: 0 });
  assert.deepEqual(await cleanCheck([LEGACY_PACKAGE_ID]), { code: 'target_not_clean', stopped: 1 });
  assert.deepEqual(await cleanCheck(PROBED), { code: 'target_not_clean', stopped: 1 });
});

test('a panel holding only the old app is refused, never given the new app beside it', () => {
  const target = { ...DESCRIPTOR, packageId: PACKAGE_ID };
  for (const body of [preflightBody({ installed: [LEGACY_PACKAGE_ID] }),
    preflightBody({ retained: [LEGACY_PACKAGE_ID] }),
    preflightBody({ residue: [LEGACY_PACKAGE_ID] })]) {
    assert.throws(() => parsePreflight(body, nonce, target), notClean);
  }
});

function delegatedBody(residue = []) {
  const lines = [`HAPANELD_DELEGATE_BEGIN:${nonce}`, `HAPANELD_SU_BEGIN:${nonce}`,
    `HAPANELD_SU_UID_BEGIN:${nonce}`, '0', `HAPANELD_SU_UID_END:${nonce}:0`];
  for (let index = 0; index < 3; index += 1) {
    lines.push(`HAPANELD_SU_BASE${index}_BEGIN:${nonce}`, 'readable',
      `HAPANELD_SU_BASE${index}_END:${nonce}:0`);
  }
  RESIDUE_PROBES.forEach(({ packageId }, index) => lines.push(
    `HAPANELD_SU_RESIDUE${index}_BEGIN:${nonce}`,
    residue.includes(packageId) ? 'present' : 'absent',
    `HAPANELD_SU_RESIDUE${index}_END:${nonce}:0`));
  lines.push(`HAPANELD_SU_END:${nonce}`, `HAPANELD_DELEGATE_END:${nonce}:0`, '');
  return lines.join('\n');
}

test('root judges residue by the same rule the preflight already applied', () => {
  assert.equal(parseDelegatedProof(delegatedBody(), nonce), true);
  for (const packageId of PROBED) {
    assert.throws(() => parseDelegatedProof(delegatedBody([packageId]), nonce),
      error => error instanceof DelegatedProofError && error.code === 'target_not_clean');
  }
});
