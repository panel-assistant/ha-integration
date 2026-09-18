import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {ACCESSIBILITY_SERVICE, buildPermissionRead, parsePermissionRead,
  buildPermissionGrant, parsePermissionGrant, expectedServices,
  buildPermissionVerification, parsePermissionVerification} from '../src/permission-contract.mjs';
import {LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID} from '../src/app-identity.mjs';

const nonce = 'a'.repeat(32);
const other = 'com.example.reader/.ReaderService';
const frame = (kind, lines, code = 0) => [`HAPANELD_PERMISSIONS_${kind}_BEGIN:${nonce}`,
  ...lines, `HAPANELD_PERMISSIONS_${kind}_END:${nonce}:${code}`, ''].join('\n');
const verified = (sdk = 33, services = expectedServices(other, LEGACY_PACKAGE_ID)) => frame('VERIFY', [services, '1',
  'WRITE_SETTINGS: allow; time=+1s', 'SYSTEM_ALERT_WINDOW: allow', sdk >= 33
    ? '      android.permission.POST_NOTIFICATIONS: granted=true, flags=[ USER_SET|USER_FIXED]' : 'not_required']);

test('permission observation is nonce-bound and rejects malformed accessibility lists', () => {
  for (const existing of ['', 'null', other, ACCESSIBILITY_SERVICE, `${other}:${ACCESSIBILITY_SERVICE}`]) {
    assert.equal(parsePermissionRead(frame('READ', [existing]), nonce), existing);
  }
  for (const invalid of [`${other}:${other}`, "com.example/.X'; touch /tmp/x", 'a/b\n', ':', 'x'.repeat(4097)]) {
    assert.throws(() => parsePermissionRead(frame('READ', [invalid]), nonce), /permissions_unverified/);
    assert.throws(() => buildPermissionGrant(nonce, 33, invalid, LEGACY_PACKAGE_ID), /permissions_unverified/);
  }
  for (const invalid of [frame('READ', [other], 1), frame('READ', [other]).replaceAll(nonce, 'b'.repeat(32)),
    frame('READ', [other, other]), frame('READ', [other]) + 'extra', frame('READ', [other]).replace(other, '\x00')]) {
    assert.throws(() => parsePermissionRead(invalid, nonce));
  }
  assert.ok(buildPermissionRead(nonce).includes('settings get secure'));
  assert.throws(() => buildPermissionRead('bad'));
});

test('readback requires every grant and exact preservation; no generic completion claim', () => {
  assert.deepEqual(parsePermissionVerification(verified(), nonce, 33, other, LEGACY_PACKAGE_ID),
    {permissionsVerified: true, notificationsRequired: true});
  assert.deepEqual(parsePermissionVerification(verified(32), nonce, 32, other, LEGACY_PACKAGE_ID),
    {permissionsVerified: true, notificationsRequired: false});
  for (const invalid of [verified().replace(other + ':', ''), verified().replace('\n1\n', '\n0\n'),
    verified().replace('WRITE_SETTINGS: allow', 'WRITE_SETTINGS: default'),
    verified().replace('SYSTEM_ALERT_WINDOW: allow', 'SYSTEM_ALERT_WINDOW: deny'),
    verified().replace('granted=true', 'granted=false'), verified().replace('flags=[', 'invalid=['),
    verified().replace('\n      android.', '\nextra\n      android.'), verified().replace(/:0\n$/, ':1\n')]) {
    assert.throws(() => parsePermissionVerification(invalid, nonce, 33, other, LEGACY_PACKAGE_ID));
  }
  assert.throws(() => parsePermissionVerification(verified(), nonce, 101, other, LEGACY_PACKAGE_ID));
  assert.match(buildPermissionVerification(nonce, 33, LEGACY_PACKAGE_ID), /dumpsys package io.github.maxlyth.hapaneld/);
  assert.ok(!buildPermissionVerification(nonce, 32, LEGACY_PACKAGE_ID).includes('POST_NOTIFICATIONS'));
});

function executeGrant(sdk, existing, {live = existing, rejectAppops = false} = {}) {
  const program = `settings() { if [ "$1" = get ]; then printf '%s\\n' '${live}'; else echo "settings $*" >&2; fi; }
pm() { echo "pm $*" >&2; }
appops() { echo "appops $*" >&2; ${rejectAppops ? 'return 1' : ':'}; }
${buildPermissionGrant(nonce, sdk, existing, LEGACY_PACKAGE_ID)}`;
  return spawnSync('/bin/sh', ['-c', program], {encoding: 'utf8'});
}

test('real shell grant program is scoped, preserves other services and gates notifications by SDK', () => {
  const result = executeGrant(33, other);
  parsePermissionGrant(result.stdout, nonce);
  assert.equal(result.status, 0);
  assert.match(result.stderr, /pm grant io.github.maxlyth.hapaneld android.permission.POST_NOTIFICATIONS/);
  assert.ok(result.stderr.includes(`settings put secure enabled_accessibility_services ${other}:${ACCESSIBILITY_SERVICE}`));
  assert.match(result.stderr, /settings put secure accessibility_enabled 1/);
  assert.equal(result.stderr.trim().split('\n').length, 5);
  assert.ok(!executeGrant(32, other).stderr.includes('pm grant'));
  const again = executeGrant(33, `${other}:${ACCESSIBILITY_SERVICE}`);
  parsePermissionGrant(again.stdout, nonce);
  assert.ok(!again.stderr.includes('enabled_accessibility_services'));
  assert.equal(expectedServices(`io.github.maxlyth.hapaneld/io.github.maxlyth.hapaneld.input.PanelAccessibilityService`, LEGACY_PACKAGE_ID),
    'io.github.maxlyth.hapaneld/io.github.maxlyth.hapaneld.input.PanelAccessibilityService');
});

test('live accessibility change or grant refusal stops and cannot report verified success', () => {
  const changed = executeGrant(33, other, {live: 'com.example.second/.Service'});
  assert.throws(() => parsePermissionGrant(changed.stdout, nonce));
  assert.equal(changed.stderr, '');
  const refused = executeGrant(33, other, {rejectAppops: true});
  assert.throws(() => parsePermissionGrant(refused.stdout, nonce));
  assert.ok(!refused.stderr.includes('settings put'));
  assert.throws(() => parsePermissionGrant(frame('GRANT', ['unexpected output']), nonce));
});

test('the successor is granted through its own fully qualified component', () => {
  // `<id>/.Class` resolves against the application id while the classes stay in
  // the legacy namespace, so the successor's shorthand names nothing.
  const successor = 'io.panelassistant.android/io.github.maxlyth.hapaneld.input.PanelAccessibilityService';
  assert.equal(expectedServices('', SUCCESSOR_PACKAGE_ID), successor);
  assert.ok(!expectedServices('', SUCCESSOR_PACKAGE_ID).includes('/.input.'));
  assert.equal(expectedServices(other, SUCCESSOR_PACKAGE_ID), `${other}:${successor}`);

  const grant = buildPermissionGrant(nonce, 33, other, SUCCESSOR_PACKAGE_ID);
  assert.ok(grant.includes(`pm grant ${SUCCESSOR_PACKAGE_ID} android.permission.POST_NOTIFICATIONS`));
  assert.ok(grant.includes(`appops set ${SUCCESSOR_PACKAGE_ID} WRITE_SETTINGS allow`));
  assert.ok(grant.includes(`settings put secure enabled_accessibility_services '${other}:${successor}'`));
  assert.ok(!grant.includes(`appops set ${LEGACY_PACKAGE_ID} `));

  const verify = buildPermissionVerification(nonce, 33, SUCCESSOR_PACKAGE_ID);
  assert.ok(verify.includes(`appops get ${SUCCESSOR_PACKAGE_ID} SYSTEM_ALERT_WINDOW`));
  assert.ok(verify.includes(`dumpsys package ${SUCCESSOR_PACKAGE_ID} |`));
});

test('each identity reads back only its own already-enabled service', () => {
  const successor = expectedServices('', SUCCESSOR_PACKAGE_ID);
  const legacyFull = 'io.github.maxlyth.hapaneld/io.github.maxlyth.hapaneld.input.PanelAccessibilityService';
  // Either spelling of the legacy component is the same enabled service, and
  // the setting reads back whichever was written.
  for (const enabled of [ACCESSIBILITY_SERVICE, legacyFull, `${other}:${legacyFull}`]) {
    assert.equal(expectedServices(enabled, LEGACY_PACKAGE_ID), enabled);
  }
  // One identity's component never counts as the other's.
  assert.equal(expectedServices(successor, LEGACY_PACKAGE_ID), `${successor}:${ACCESSIBILITY_SERVICE}`);
  assert.equal(expectedServices(ACCESSIBILITY_SERVICE, SUCCESSOR_PACKAGE_ID),
    `${ACCESSIBILITY_SERVICE}:${successor}`);
  assert.equal(expectedServices(successor, SUCCESSOR_PACKAGE_ID), successor);
  // Verification is held to the identity it was asked about.
  assert.throws(() => parsePermissionVerification(
    verified(33, expectedServices(other, LEGACY_PACKAGE_ID)), nonce, 33, other, SUCCESSOR_PACKAGE_ID),
  /permissions_unverified/);
  for (const unknown of ['io.example.other', '', null, 'IO.PANELASSISTANT.ANDROID']) {
    assert.throws(() => expectedServices(other, unknown), /permissions_unverified/);
    assert.throws(() => buildPermissionGrant(nonce, 33, other, unknown), /permissions_unverified/);
  }
});
