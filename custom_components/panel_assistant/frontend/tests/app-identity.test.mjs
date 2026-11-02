import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { LAUNCH_COMPONENTS, LEGACY_PACKAGE_ID, PACKAGE_ID,
  accessibilityComponentFor, homeComponentFor, isLaunchComponent } from '../src/app-identity.mjs';
import { PreflightError, RESIDUE_PROBES, buildPreflight, classifyTarget,
  parsePreflight } from '../src/preflight.mjs';
import { DelegatedProofError, parseDelegatedProof } from '../src/delegated-proof.mjs';
import { InstallContractError, buildLaunch } from '../src/install-contract.mjs';
import { InstalledObservationError, buildInstalledObservation, parseInstalledObservation } from '../src/installed-observation.mjs';
import { buildVendorDisplaySettlement, parseVendorDisplaySettlement } from '../src/vendor-provisioning.mjs';
import { buildHomeClaim } from '../src/home-chooser.mjs';
import { createUsbTransactionPorts } from '../src/usb-transaction-ports.mjs';
import { createInstallController } from '../src/install-controller.mjs';

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

test("launch refuses foreign packages and the old app", () => {
  assert.throws(() => buildLaunch(nonce, 'io.example.other', MOVED, 33), InstallContractError);
  // The old app is never started, by its own launcher or the new one.
  assert.throws(() => buildLaunch(nonce, LEGACY_PACKAGE_ID, OLD_APP_LAUNCH, 33), InstallContractError);
  assert.throws(() => buildLaunch(nonce, LEGACY_PACKAGE_ID, MOVED, 33), InstallContractError);
});

test('no panel program is built for the old app', () => {
  assert.throws(() => buildInstalledObservation(nonce, LEGACY_PACKAGE_ID),
    error => error instanceof InstalledObservationError && error.code === 'invalid_request');
  assert.throws(() => buildVendorDisplaySettlement(nonce, LEGACY_PACKAGE_ID), /vendor_setup_unreadable/);
  assert.throws(() => buildHomeClaim(OLD_APP_LAUNCH), RangeError);
});

// The same programs run for this app against a panel that answers only for it.
const runPanel = program => spawnSync('/bin/sh', ['-c', program], { encoding: 'utf8', timeout: 5000 });

test('the installed observation reads this app', () => {
  // The host has no /data/app to redirect from, so the size read answers in place.
  const result = runPanel(`pm() { [ "$1 $2" = 'path ${PACKAGE_ID}' ] && echo 'package:/data/app/x/base.apk'; }
stat() { echo 81a4; }; sha256sum() { echo "${'a'.repeat(64)}  $1"; }
${buildInstalledObservation(nonce, PACKAGE_ID).replace('wc -c < "$hapaneld_apk"', 'echo 1234')}`);
  assert.equal(parseInstalledObservation(result.stdout, nonce, { apkSize: 1234, apkSha256: 'a'.repeat(64) }), true);
});

test('vendor display settlement proceeds only while this app holds HOME', t => {
  const dir = mkdtempSync(join(tmpdir(), 'identity-settle-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const settle = home => runPanel(`
pm() { echo package:cloud.shelly.stargate; }
cmd() { echo '${home}/.MainActivity'; }
timeout() { shift; "$@"; }
am() { rm -f "${dir}/paused"; echo 'Status: ok'; }
input() { touch "${dir}/paused"; }
pidof() { echo 42; }
wm() { echo 'Physical density: 216'; }
settings() { echo 1; }
dumpsys() { echo '  ACTIVITY cloud.shelly.stargate/.activities.MainActivity 1 pid=42'
  if [ -e "${dir}/paused" ]; then echo '    mResumed=false mStopped=true mFinished=false'
  else echo '    mResumed=true mStopped=false mFinished=false'; fi
  echo '    mCurrentConfig={1.0 ?mcc 216dpi }'; }
${buildVendorDisplaySettlement(nonce, PACKAGE_ID)}`).stdout;
  assert.equal(parseVendorDisplaySettlement(settle(PACKAGE_ID), nonce), 'settled');
  assert.throws(() => parseVendorDisplaySettlement(settle(LEGACY_PACKAGE_ID), nonce), /vendor_setup_unreadable/);
});

test("the HOME claim sets this app's dashboard as HOME", t => {
  const dir = mkdtempSync(join(tmpdir(), 'identity-home-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  for (const component of LAUNCH_COMPONENTS[PACKAGE_ID]) {
    rmSync(join(dir, 'home'), { force: true });
    const result = runPanel(`
cmd() { if [ "$2" = set-home-activity ]; then echo "$3" > "${dir}/home"; else cat "${dir}/home" 2>/dev/null || echo android/com.android.internal.app.ResolverActivity; fi; }
input() { :; }; uiautomator() { return 1; }; sleep() { :; }
${buildHomeClaim(component)}
echo "$hp_chooser"`);
    assert.equal(result.stdout, 'set\n', component);
    assert.equal(readFileSync(join(dir, 'home'), 'utf8'), `${homeComponentFor(component)}\n`);
  }
});

test('the executed preflight refuses either app, retained package, and every data location', t => {
  const root = mkdtempSync(join(tmpdir(), 'identity-preflight-'));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  const bases = ['/data/user/0', '/data/data', '/data/user_de/0'];
  for (const base of bases) mkdirSync(join(root, base), {recursive: true});
  const run = ({installed = '', retained = ''} = {}) => {
    const result = spawnSync('/bin/sh', ['-c', `
getprop() { case "$1" in ro.product.model) echo 'Test panel';; ro.serialno) echo serial;; ro.product.cpu.abi) echo arm64-v8a;; ro.build.version.sdk) echo 34;; ro.secure) echo 1;; ro.debuggable) echo 0;; esac; }
id() { echo 2000; }
command() { return 1; }
pm() {
  if [ "$1 $2" = 'path android' ]; then echo package:/system/framework/framework-res.apk;
  elif [ "$1" = path ]; then [ "$2" = '${installed}' ] && echo "package:/data/app/$2/base.apk";
  elif [ "$1 $2 $3" = 'list packages -u' ]; then [ "$4" != '${retained}' ] || echo "package:$4";
  else return 1; fi
}
${buildPreflight(nonce).replaceAll('/data/', `${root}/data/`)}`], {encoding: 'utf8', timeout: 2000});
    assert.equal(result.status, 0);
    // Capture the caller's result so a refusal mutant fails an assertion, rather
    // than escaping as a parser or shell exception.
    try { return parsePreflight(result.stdout, nonce, {...DESCRIPTOR, packageId: PACKAGE_ID}); }
    catch (error) { return {code: error.code}; }
  };
  assert.equal(run().installationAdmission, 'clean_preflight');
  for (const packageId of PROBED) {
    assert.deepEqual(run({installed: packageId}),
      {code: packageId === PACKAGE_ID ? 'target_installed' : 'target_not_clean'});
    assert.deepEqual(run({retained: packageId}), {code: 'target_not_clean'});
    for (const base of bases) {
      const residue = join(root, base, packageId);
      mkdirSync(residue);
      assert.deepEqual(run(), {code: 'target_not_clean'}, `${base}/${packageId}`);
      rmSync(residue, {recursive: true});
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
async function cleanCheck(installed, { throughPreview = false } = {}) {
  const target = { model: 'Test Panel', serial: 'SERIAL-1', primaryAbi: 'arm64-v8a', androidSdk: 34,
    rootMode: 'rootless', usbVendorId: 1, usbProductId: 2, usbSerial: 'usb' };
  const release = { kind: 'authenticated-apk-bytes',
    descriptor: { ...DESCRIPTOR, packageId: PACKAGE_ID, apkSize: 1234, apkSha256: 'a'.repeat(64) } };
  const shell = body => ({ readable: new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode(body)); controller.close();
  } }), close: async () => {} });
  const adb = { async createSocket(command) {
    const n = command.match(/BEGIN:([a-f0-9]{32})/)[1];
    if (command.includes('HAPANELD_PREFLIGHT_BEGIN')) return shell(preflightBody({ installed }).replaceAll(nonce, n));
    if (command.includes('HAPANELD_INSTALLED_BEGIN')) {
      // Another build of the app: present, but not this release's bytes.
      const apk = '/data/app/~~x==/io.panelassistant.android-y==/base.apk';
      return shell([`HAPANELD_INSTALLED_BEGIN:${n}`, 'present', apk,
        `HAPANELD_INSTALLED_MODE_BEGIN:${n}`, '81a4', `HAPANELD_INSTALLED_MODE_END:${n}:0`,
        `HAPANELD_INSTALLED_SIZE_BEGIN:${n}`, '999', `HAPANELD_INSTALLED_SIZE_END:${n}:0`,
        `HAPANELD_INSTALLED_SHA_BEGIN:${n}`, `${'b'.repeat(64)}  ${apk}`, `HAPANELD_INSTALLED_SHA_END:${n}:0`,
        `HAPANELD_INSTALLED_END:${n}`, ''].join('\n'));
    }
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
  const attempt = throughPreview
    ? createInstallController({ store: { load: async () => null }, ports }).preview(target)
    : ports.inspect({ id: 'd'.repeat(32), phase: 'prepared', target }, release);
  const error = await attempt.then(() => null, caught => caught);
  return { code: error?.code, stopped };
}

test('the USB ports keep the session for a panel that runs the app, and close it for any other refusal', async () => {
  assert.deepEqual(await cleanCheck([PACKAGE_ID]), { code: 'target_installed', stopped: 0 });
  assert.deepEqual(await cleanCheck([LEGACY_PACKAGE_ID]), { code: 'target_not_clean', stopped: 1 });
  assert.deepEqual(await cleanCheck(PROBED), { code: 'target_not_clean', stopped: 1 });
  // The installer's own preview: another build of the app is not adopted, and
  // reaches the installer as this answer with the session still open.
  assert.deepEqual(await cleanCheck([PACKAGE_ID], { throughPreview: true }), { code: 'target_installed', stopped: 0 });
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
