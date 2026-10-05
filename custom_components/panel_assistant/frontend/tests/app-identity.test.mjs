import test from 'node:test';
import assert from 'node:assert/strict';
import { ACCEPTED_PACKAGE_IDS, LAUNCH_COMPONENTS, LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID,
  accessibilityComponentFor, counterpartOf, isLaunchComponent } from '../src/app-identity.mjs';
import { PreflightError, RESIDUE_PROBES, buildPreflight, classifyTarget,
  parsePreflight } from '../src/preflight.mjs';
import { DelegatedProofError, parseDelegatedProof } from '../src/delegated-proof.mjs';
import { InstallContractError, buildLaunch } from '../src/install-contract.mjs';

const nonce = 'a'.repeat(32);
const notClean = error => error instanceof PreflightError && error.code === 'target_not_clean';

const MOVED = 'io.panelassistant.android/io.panelassistant.android.MainActivity';

test('each identity names its own components, never the other spelling', () => {
  // Up to app 0.9.9 the classes stay in the old package whichever id the build
  // carries; from 0.9.10 the new app's classes live in its own package.
  assert.deepEqual(LAUNCH_COMPONENTS[LEGACY_PACKAGE_ID], ['io.github.maxlyth.hapaneld/.MainActivity']);
  assert.deepEqual(LAUNCH_COMPONENTS[SUCCESSOR_PACKAGE_ID],
    ['io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity', MOVED]);
  assert.equal(accessibilityComponentFor(LAUNCH_COMPONENTS[SUCCESSOR_PACKAGE_ID][0]),
    'io.panelassistant.android/io.github.maxlyth.hapaneld.input.PanelAccessibilityService');
  assert.equal(accessibilityComponentFor(MOVED),
    'io.panelassistant.android/io.panelassistant.android.input.PanelAccessibilityService');
  for (const packageId of ACCEPTED_PACKAGE_IDS) {
    for (const component of LAUNCH_COMPONENTS[packageId]) {
      assert.equal(component.split('/')[0], packageId);
      assert.ok(isLaunchComponent(packageId, component));
      assert.ok(!isLaunchComponent(counterpartOf(packageId), component));
    }
    assert.equal(counterpartOf(counterpartOf(packageId)), packageId);
  }
  for (const unknown of ['io.example.other', 'io.panelassistant', '']) {
    assert.ok(!isLaunchComponent(unknown, MOVED));
    assert.throws(() => counterpartOf(unknown), RangeError);
  }
  assert.throws(() => accessibilityComponentFor('io.panelassistant.android/.MainActivity'), RangeError);
});

test("launch starts the descriptor's own package by its own component", () => {
  for (const component of LAUNCH_COMPONENTS[SUCCESSOR_PACKAGE_ID]) {
    assert.ok(buildLaunch(nonce, SUCCESSOR_PACKAGE_ID, component, 33).includes(
      `am start -W -n ${component} -p io.panelassistant.android`));
  }
  assert.ok(buildLaunch(nonce, LEGACY_PACKAGE_ID, LAUNCH_COMPONENTS[LEGACY_PACKAGE_ID][0], 33).includes(
    'am start -W -n io.github.maxlyth.hapaneld/.MainActivity -p io.github.maxlyth.hapaneld'));
  assert.throws(() => buildLaunch(nonce, 'io.example.other', MOVED, 33), InstallContractError);
  // A launcher is held to its own id: the old app never names a moved class.
  assert.throws(() => buildLaunch(nonce, LEGACY_PACKAGE_ID, MOVED, 33), InstallContractError);
});

test('the preflight reads each accepted package and its data on its own', () => {
  const program = buildPreflight(nonce);
  for (const packageId of ACCEPTED_PACKAGE_IDS) {
    assert.ok(program.includes(`pm path ${packageId};`), packageId);
    assert.ok(program.includes(`pm list packages -u ${packageId};`), packageId);
    for (const base of ['/data/user/0', '/data/data', '/data/user_de/0']) {
      assert.ok(program.includes(`[ -e ${base}/${packageId} ]`), `${base}/${packageId}`);
    }
  }
});

test('USB classification refuses existing panels including legacy to successor migration', () => {
  const none = new Set();
  for (const target of ACCEPTED_PACKAGE_IDS) {
    assert.equal(classifyTarget(target, [], none), false);
    for (const installed of [[LEGACY_PACKAGE_ID], [SUCCESSOR_PACKAGE_ID], ACCEPTED_PACKAGE_IDS]) {
      assert.throws(() => classifyTarget(target, installed, none), notClean);
      for (const packageId of ACCEPTED_PACKAGE_IDS) {
        assert.throws(() => classifyTarget(target, installed, new Set([packageId])), notClean);
      }
    }
    for (const packageId of ACCEPTED_PACKAGE_IDS) {
      assert.throws(() => classifyTarget(target, [], new Set([packageId])), notClean);
    }
  }
});

// Whole-body cases: `classifyTarget` above is the rule, these prove the wiring
// that feeds it — which section index carries which application id.
const DESCRIPTOR = Object.freeze({ minSdk: 26, supportedAbis: ['arm64-v8a', 'armeabi-v7a'] });

function section(name, values, status = 0) {
  return [`HAPANELD_PREFLIGHT_${name}_BEGIN:${nonce}`, ...values,
    `HAPANELD_PREFLIGHT_${name}_END:${nonce}:${status}`];
}

function preflightBody({ installed = [], residue = [] } = {}) {
  const lines = [`HAPANELD_PREFLIGHT_BEGIN:${nonce}`,
    ...section('MODEL', ['Test Panel']), ...section('SERIAL', ['SERIAL-1']),
    ...section('ABI', ['arm64-v8a']), ...section('SDK', ['34']),
    ...section('UID', ['2000']), ...section('SECURE', ['1']),
    ...section('DEBUGGABLE', ['0']), ...section('SU', ['absent']),
    ...section('LIVE', ['package:/system/framework/framework-res.apk'])];
  ACCEPTED_PACKAGE_IDS.forEach((packageId, index) => {
    const present = installed.includes(packageId);
    lines.push(...section(`PACKAGE${index}`,
      present ? [`package:/data/app/${packageId}-1/base.apk`] : [], present ? 0 : 1));
    lines.push(...section(`RETAINED${index}`, present ? [`package:${packageId}`] : []));
  });
  for (let index = 0; index < 3; index += 1) lines.push(...section(`BASE${index}`, ['readable']));
  RESIDUE_PROBES.forEach(({ packageId }, index) => lines.push(
    ...section(`RESIDUE${index}`, [residue.includes(packageId) ? 'present' : 'absent'])));
  lines.push(`HAPANELD_PREFLIGHT_END:${nonce}`, '');
  return lines.join('\n');
}

test('a whole preflight body reads each identity from its own sections', () => {
  for (const packageId of ACCEPTED_PACKAGE_IDS) {
    const clean = parsePreflight(preflightBody(), nonce, { ...DESCRIPTOR, packageId });
    assert.equal(clean.rootMode, 'rootless');
    assert.equal(clean.installationAdmission, 'clean_preflight');
  }

  for (const target of ACCEPTED_PACKAGE_IDS) {
    for (const installed of ACCEPTED_PACKAGE_IDS) {
      assert.throws(() => parsePreflight(preflightBody({ installed: [installed] }),
        nonce, { ...DESCRIPTOR, packageId: target }), notClean);
    }
    for (const residue of ACCEPTED_PACKAGE_IDS) {
      assert.throws(() => parsePreflight(preflightBody({ residue: [residue] }),
        nonce, { ...DESCRIPTOR, packageId: target }), notClean);
    }
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
  for (const packageId of ACCEPTED_PACKAGE_IDS) {
    assert.throws(() => parseDelegatedProof(delegatedBody([packageId]), nonce),
      error => error instanceof DelegatedProofError && error.code === 'target_not_clean');
  }
});
