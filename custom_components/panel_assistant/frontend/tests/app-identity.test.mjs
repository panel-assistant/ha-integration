import test from 'node:test';
import assert from 'node:assert/strict';
import { ACCEPTED_PACKAGE_IDS, LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID,
  accessibilityComponentFor, counterpartOf, launchComponentFor } from '../src/app-identity.mjs';
import { PreflightError, RESIDUE_PROBES, buildPreflight, classifyTarget,
  parsePreflight } from '../src/preflight.mjs';
import { DelegatedProofError, parseDelegatedProof } from '../src/delegated-proof.mjs';
import { InstallContractError, buildLaunch } from '../src/install-contract.mjs';

const nonce = 'a'.repeat(32);
const notClean = error => error instanceof PreflightError && error.code === 'target_not_clean';

test('each identity names its own components, never the other spelling', () => {
  // The classes stay in the legacy namespace whichever id the build carries,
  // so only the legacy id may use the `<id>/.Class` shorthand.
  assert.equal(launchComponentFor(LEGACY_PACKAGE_ID), 'io.github.maxlyth.hapaneld/.MainActivity');
  assert.equal(launchComponentFor(SUCCESSOR_PACKAGE_ID),
    'io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity');
  assert.equal(accessibilityComponentFor(SUCCESSOR_PACKAGE_ID),
    'io.panelassistant.android/io.github.maxlyth.hapaneld.input.PanelAccessibilityService');
  for (const packageId of ACCEPTED_PACKAGE_IDS) {
    const [id, className] = launchComponentFor(packageId).split('/');
    assert.equal(id, packageId);
    assert.equal(className.startsWith('.'), packageId === LEGACY_PACKAGE_ID);
    assert.equal(counterpartOf(counterpartOf(packageId)), packageId);
  }
  for (const unknown of ['io.example.other', 'io.panelassistant', '']) {
    assert.throws(() => launchComponentFor(unknown), RangeError);
    assert.throws(() => counterpartOf(unknown), RangeError);
  }
});

test("launch starts the descriptor's own package by its own component", () => {
  assert.ok(buildLaunch(nonce, SUCCESSOR_PACKAGE_ID).includes(
    'am start -W -n io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity'
    + ' -p io.panelassistant.android'));
  assert.ok(buildLaunch(nonce, LEGACY_PACKAGE_ID).includes(
    'am start -W -n io.github.maxlyth.hapaneld/.MainActivity -p io.github.maxlyth.hapaneld'));
  assert.throws(() => buildLaunch(nonce, 'io.example.other'), InstallContractError);
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

test('an old package with no new package is a migration candidate, nothing else is', () => {
  const none = new Set();
  // A clean target holds neither package: either identity may be installed and
  // no handover is expected.
  assert.equal(classifyTarget(SUCCESSOR_PACKAGE_ID, [], none), false);
  assert.equal(classifyTarget(LEGACY_PACKAGE_ID, [], none), false);
  // Old only: the successor installs beside it and the panel hands over. Its
  // own data is what the handover migrates, so that residue is expected.
  assert.equal(classifyTarget(SUCCESSOR_PACKAGE_ID, [LEGACY_PACKAGE_ID], none), true);
  assert.equal(
    classifyTarget(SUCCESSOR_PACKAGE_ID, [LEGACY_PACKAGE_ID], new Set([LEGACY_PACKAGE_ID])), true);
  // Both present, or the target already installed: the refusal is unchanged.
  for (const installed of [[LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID], [SUCCESSOR_PACKAGE_ID]]) {
    assert.throws(() => classifyTarget(SUCCESSOR_PACKAGE_ID, installed, none), notClean);
  }
  // New only, installing the legacy build: never a migration, always refused.
  assert.throws(() => classifyTarget(LEGACY_PACKAGE_ID, [SUCCESSOR_PACKAGE_ID], none), notClean);
  assert.throws(() => classifyTarget(LEGACY_PACKAGE_ID, [LEGACY_PACKAGE_ID], none), notClean);
  // Residue of the package being installed is still unclean, and so is legacy
  // residue with no legacy package to migrate from.
  assert.throws(
    () => classifyTarget(SUCCESSOR_PACKAGE_ID, [], new Set([SUCCESSOR_PACKAGE_ID])), notClean);
  assert.throws(
    () => classifyTarget(SUCCESSOR_PACKAGE_ID, [], new Set([LEGACY_PACKAGE_ID])), notClean);
  assert.throws(() => classifyTarget(
    SUCCESSOR_PACKAGE_ID, [LEGACY_PACKAGE_ID], new Set([SUCCESSOR_PACKAGE_ID])), notClean);
  assert.throws(
    () => classifyTarget(LEGACY_PACKAGE_ID, [], new Set([LEGACY_PACKAGE_ID])), notClean);
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
    assert.equal(clean.migrationCandidate, false);
    assert.equal(clean.rootMode, 'rootless');
  }

  const migrating = parsePreflight(
    preflightBody({ installed: [LEGACY_PACKAGE_ID], residue: [LEGACY_PACKAGE_ID] }),
    nonce, { ...DESCRIPTOR, packageId: SUCCESSOR_PACKAGE_ID });
  assert.equal(migrating.migrationCandidate, true);

  // The same body, installing the legacy build, is the refusal it always was.
  assert.throws(() => parsePreflight(preflightBody({ installed: [LEGACY_PACKAGE_ID] }),
    nonce, { ...DESCRIPTOR, packageId: LEGACY_PACKAGE_ID }), notClean);
  // The successor's own sections are read as the successor's, not the legacy's.
  assert.throws(() => parsePreflight(preflightBody({ installed: [SUCCESSOR_PACKAGE_ID] }),
    nonce, { ...DESCRIPTOR, packageId: SUCCESSOR_PACKAGE_ID }), notClean);
  assert.throws(() => parsePreflight(preflightBody({ residue: [SUCCESSOR_PACKAGE_ID] }),
    nonce, { ...DESCRIPTOR, packageId: SUCCESSOR_PACKAGE_ID }), notClean);
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
  assert.equal(parseDelegatedProof(delegatedBody(), nonce, false), true);
  // The old app's own data is what a handover migrates.
  assert.equal(parseDelegatedProof(delegatedBody([LEGACY_PACKAGE_ID]), nonce, true), true);
  // Without a handover it is residue like any other, and the successor's data
  // is never excused, because that is the package being installed.
  assert.throws(() => parseDelegatedProof(delegatedBody([LEGACY_PACKAGE_ID]), nonce, false),
    error => error instanceof DelegatedProofError && error.code === 'target_not_clean');
  assert.throws(() => parseDelegatedProof(delegatedBody([SUCCESSOR_PACKAGE_ID]), nonce, true),
    error => error.code === 'target_not_clean');
});
