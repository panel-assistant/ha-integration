import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {existsSync, mkdtempSync, readFileSync, writeFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {buildPermissionRead, parsePermissionRead,
  buildPermissionGrant, parsePermissionGrant, expectedServices,
  buildPermissionVerification, parsePermissionVerification} from '../src/permission-contract.mjs';
import {LAUNCH_COMPONENTS, LEGACY_PACKAGE_ID, PACKAGE_ID, accessibilityComponentFor,
  homeComponentFor} from '../src/app-identity.mjs';

// Builds up to app 0.9.9, whose classes stay in the old package, and from
// 0.9.10, whose classes live in the app's own.
const [OLD_CLASSES, NEW_CLASSES] = LAUNCH_COMPONENTS[PACKAGE_ID];
const ACCESSIBILITY_SERVICE = accessibilityComponentFor(OLD_CLASSES);

const nonce = 'a'.repeat(32);
const other = 'com.example.reader/.ReaderService';
const frame = (kind, lines, code = 0) => [`HAPANELD_PERMISSIONS_${kind}_BEGIN:${nonce}`,
  ...lines, `HAPANELD_PERMISSIONS_${kind}_END:${nonce}:${code}`, ''].join('\n');
const verified = (sdk = 33, services = expectedServices(other, PACKAGE_ID, OLD_CLASSES)) => frame('VERIFY', [services, '1',
  'WRITE_SETTINGS: allow; time=+1s', 'SYSTEM_ALERT_WINDOW: allow',
  'REQUEST_INSTALL_PACKAGES: allow', sdk >= 33
    ? '      android.permission.POST_NOTIFICATIONS: granted=true, flags=[ USER_SET|USER_FIXED]' : 'not_required',
  'granted', 'granted', 'granted', `home:${homeComponentFor(OLD_CLASSES)}`, 'device:generic']);

test('permission observation is nonce-bound and rejects malformed accessibility lists', () => {
  for (const existing of ['', 'null', other, ACCESSIBILITY_SERVICE, `${other}:${ACCESSIBILITY_SERVICE}`]) {
    assert.equal(parsePermissionRead(frame('READ', [existing]), nonce), existing);
  }
  for (const invalid of [`${other}:${other}`, "com.example/.X'; touch /tmp/x", 'a/b\n', ':', 'x'.repeat(4097)]) {
    assert.throws(() => parsePermissionRead(frame('READ', [invalid]), nonce), /permissions_unverified/);
    assert.throws(() => buildPermissionGrant(nonce, 33, invalid, PACKAGE_ID, OLD_CLASSES), /permissions_unverified/);
  }
  for (const invalid of [frame('READ', [other], 1), frame('READ', [other]).replaceAll(nonce, 'b'.repeat(32)),
    frame('READ', [other, other]), frame('READ', [other]) + 'extra', frame('READ', [other]).replace(other, '\x00')]) {
    assert.throws(() => parsePermissionRead(invalid, nonce));
  }
  assert.throws(() => buildPermissionRead('bad'));
});

test('readback requires every grant and exact preservation; no generic completion claim', () => {
  assert.deepEqual(parsePermissionVerification(verified(), nonce, 33, other, PACKAGE_ID, OLD_CLASSES),
    {permissionsVerified: true, notificationsRequired: true, camera: 'granted', microphone: 'granted', secureSettings: 'granted', home: true, device: 'generic'});
  assert.deepEqual(parsePermissionVerification(verified(32), nonce, 32, other, PACKAGE_ID, OLD_CLASSES),
    {permissionsVerified: true, notificationsRequired: false, camera: 'granted', microphone: 'granted', secureSettings: 'granted', home: true, device: 'generic'});
  for (const invalid of [verified().replace(other + ':', ''), verified().replace('\n1\n', '\n0\n'),
    verified().replace('WRITE_SETTINGS: allow', 'WRITE_SETTINGS: default'),
    verified().replace('SYSTEM_ALERT_WINDOW: allow', 'SYSTEM_ALERT_WINDOW: deny'),
    verified().replace('REQUEST_INSTALL_PACKAGES: allow', 'REQUEST_INSTALL_PACKAGES: default'),
    verified().replace('\nREQUEST_INSTALL_PACKAGES: allow', ''),
    verified().replace('granted=true', 'granted=false'), verified().replace('flags=[', 'invalid=['),
    verified().replace('\n      android.', '\nextra\n      android.'), verified().replace(/:0\n$/, ':1\n'),
    verified().replace('\ngranted\n', '\nmaybe\n'), verified().replace('\nhome:', '\nhome '),
    verified().replace('\ndevice:generic', ''), verified().replace('\ngranted\nhome:', '\nmaybe\nhome:'),
    verified().replace('\ngranted\nhome:', '\nhome:'),
    verified().replace('\ngranted\ngranted\ngranted\n', '\ngranted\nmaybe\ngranted\n'),
    verified().replace('\ndevice:generic', '\ndevice:generic\nextra')]) {
    assert.throws(() => parsePermissionVerification(invalid, nonce, 33, other, PACKAGE_ID, OLD_CLASSES));
  }
  assert.throws(() => parsePermissionVerification(verified(), nonce, 101, other, PACKAGE_ID, OLD_CLASSES));
});

// A panel whose package manager knows only `installed`: Android below 13 has
// no notification permission, so its dump carries no such line.
function executeVerification(sdk, installed = PACKAGE_ID) {
  const program = `settings() { case "$3" in enabled_accessibility_services) echo '${other}:${ACCESSIBILITY_SERVICE}' ;; *) echo 1 ;; esac; }
appops() { if [ "$2" = '${installed}' ]; then echo "$3: allow"; else echo 'No operations.'; fi; }
dumpsys() { [ "$2" = '${installed}' ] || { echo "Unable to find package: $2"; return 0; }
  ${sdk >= 33 ? "echo '      android.permission.POST_NOTIFICATIONS: granted=true, flags=[ USER_SET]'" : ':'}
  for p in CAMERA RECORD_AUDIO WRITE_SECURE_SETTINGS; do echo "      android.permission.$p: granted=true"; done; }
pm() { printf 'feature:android.hardware.camera.any\\nfeature:android.hardware.microphone\\n'; }
cmd() { echo '${homeComponentFor(OLD_CLASSES)}'; }
getprop() { echo generic; }
${buildPermissionVerification(nonce, sdk, PACKAGE_ID, OLD_CLASSES)}`;
  return spawnSync('/bin/sh', ['-c', program], {encoding: 'utf8'}).stdout;
}

test("the executed verification reads this app's own grants, notifications only from Android 13", () => {
  for (const sdk of [32, 33]) {
    assert.deepEqual(parsePermissionVerification(executeVerification(sdk), nonce, sdk, other, PACKAGE_ID, OLD_CLASSES),
      {permissionsVerified: true, notificationsRequired: sdk >= 33, camera: 'granted', microphone: 'granted',
        secureSettings: 'granted', home: true, device: 'generic'}, `sdk ${sdk}`);
    // Another app's grants never count as this one's.
    assert.throws(() => parsePermissionVerification(executeVerification(sdk, 'com.example.other'),
      nonce, sdk, other, PACKAGE_ID, OLD_CLASSES), /permissions_unverified/);
  }
});

function executeGrant(sdk, existing, {live = existing, rejectAppops = false} = {}) {
  const program = `settings() { if [ "$1" = get ]; then printf '%s\\n' '${live}'; else echo "settings $*" >&2; fi; }
pm() { echo "pm $*" >&2; echo "pm $*" >> "$HP_CALLS"; }
appops() { echo "appops $*" >&2; ${rejectAppops ? 'return 1' : ':'}; }
${buildPermissionGrant(nonce, sdk, existing, PACKAGE_ID, OLD_CLASSES)}`;
  const calls = join(mkdtempSync(join(tmpdir(), 'grant-')), 'calls');
  const result = spawnSync('/bin/sh', ['-c', program], {encoding: 'utf8', env: {...process.env, HP_CALLS: calls}});
  return {...result, calls: existsSync(calls) ? readFileSync(calls, 'utf8') : ''};
}

test('real shell grant program is scoped, preserves other services and gates notifications by SDK', () => {
  const result = executeGrant(33, other);
  parsePermissionGrant(result.stdout, nonce);
  assert.equal(result.status, 0);
  assert.match(result.stderr, /pm grant io.panelassistant.android android.permission.POST_NOTIFICATIONS/);
  assert.ok(result.stderr.includes(`settings put secure enabled_accessibility_services ${other}:${ACCESSIBILITY_SERVICE}`));
  assert.match(result.stderr, /settings put secure accessibility_enabled 1/);
  assert.match(result.stderr, /appops set io.panelassistant.android REQUEST_INSTALL_PACKAGES allow/);
  assert.equal(result.stderr.trim().split('\n').length, 6);
  // The secure-settings grant's output is discarded like the microphone grant's,
  // so it is seen through a calls file rather than stderr.
  assert.match(result.calls, /pm grant io.panelassistant.android android.permission.WRITE_SECURE_SETTINGS/);
  assert.ok(!executeGrant(32, other).stderr.includes('pm grant'));
  const again = executeGrant(33, `${other}:${ACCESSIBILITY_SERVICE}`);
  parsePermissionGrant(again.stdout, nonce);
  assert.ok(!again.stderr.includes('enabled_accessibility_services'));
});

test('USB Companion grants retain the previous launcher through install and repair', () => {
  const previous = 'com.android.launcher3/com.android.launcher3.Launcher';
  for (const restore of [false, true]) {
    const dir = mkdtempSync(join(tmpdir(), 'companion-home-'));
    try {
      writeFileSync(join(dir, 'home'), restore ? homeComponentFor(NEW_CLASSES) : previous);
      const program = `settings() { if [ "$1" = get ]; then echo null; fi; }
pm() { :; }
appops() { :; }
am() { case "$*" in
  *"operation dashboard_home_policy"*) if [ "$(cat "$HP_DIR/home")" = '${previous}' ]; then
    echo 'Broadcast completed: result=2, data="panel-assistant-home-retained"'; else
    echo 'Broadcast completed: result=3, data="panel-assistant-home-restore:${previous}"'; fi ;;
  *"operation restore_previous_home_shell_lock_eligibility"*)
    echo 'Broadcast completed: result=1, data="panel-assistant-home-restore-shell-locked"' ;;
  *"operation acknowledge_retained_home"*) touch "$HP_DIR/ack" ;;
  *"operation setup_eligibility"*) echo 'Broadcast completed: result=1, data="panel-assistant-setup-eligible"' ;;
esac; }
cmd() { case "$1 $2" in
  'package resolve-activity') cat "$HP_DIR/home" ;;
  'package set-home-activity') echo "$3" >> "$HP_DIR/calls"; printf '%s' "$3" > "$HP_DIR/home" ;;
esac; }
${buildPermissionGrant(nonce, 33, 'null', PACKAGE_ID, NEW_CLASSES).replace('9</system', '9<"$HP_DIR"')}`;
      for (let attempt = 0; attempt < 2; attempt++) {
        const result = spawnSync('/bin/sh', ['-c', program], {encoding: 'utf8', env: {...process.env, HP_DIR: dir}});
        assert.equal(result.status, 0, result.stderr);
        assert.equal(readFileSync(join(dir, 'home'), 'utf8'), previous);
        assert.equal(parsePermissionGrant(result.stdout, nonce), 'retained');
      }
      assert.equal(existsSync(join(dir, 'ack')), restore);
      assert.equal(existsSync(join(dir, 'calls')) ? readFileSync(join(dir, 'calls'), 'utf8') : '', restore ? `${previous}\n` : '');
    } finally { rmSync(dir, {recursive: true, force: true}); }
  }
});

test('USB HOME ownership is rechecked after setup eligibility', () => {
  const dir = mkdtempSync(join(tmpdir(), 'home-recheck-'));
  try {
    const program = `settings() { if [ "$1" = get ]; then echo null; fi; }
pm() { :; }
appops() { :; }
dumpsys() { echo 'PreservePreviousHomeReceiver'; }
am() { case "$*" in
  *"operation dashboard_home_policy"*) if [ -e "$HP_DIR/changed" ]; then
    echo 'Broadcast completed: result=2, data="panel-assistant-home-retained"'; else
    echo 'Broadcast completed: result=1, data="panel-assistant-home-builtin"'; fi ;;
  *"operation setup_eligibility"*) touch "$HP_DIR/changed"
    echo 'Broadcast completed: result=1, data="panel-assistant-setup-eligible"' ;;
esac; }
cmd() { case "$1 $2" in
  'package resolve-activity') echo 'com.android.launcher3/.Launcher' ;;
  'package set-home-activity') touch "$HP_DIR/claimed" ;;
esac; }
${buildPermissionGrant(nonce, 33, 'null', PACKAGE_ID, NEW_CLASSES)}`;
    const result = spawnSync('/bin/sh', ['-c', program], {encoding: 'utf8', env: {...process.env, HP_DIR: dir}});
    assert.equal(result.status, 0, result.stderr);
    assert.equal(parsePermissionGrant(result.stdout, nonce), 'deferred');
    assert.equal(existsSync(join(dir, 'claimed')), false);
  } finally { rmSync(dir, {recursive: true, force: true}); }
});

// Android lists each active admin by its short name, as a Meta Portal printed it.
function executeAdminGrant(adminActive, packageId = PACKAGE_ID, launchComponent = NEW_CLASSES) {
  const dir = mkdtempSync(join(tmpdir(), 'admin-'));
  const program = `settings() { if [ "$1" = get ]; then echo null; fi; }
pm() { :; }
appops() { :; }
dumpsys() { echo '  Enabled Device Admins (User 0, provisioningState: 0):'
  [ ! -e "$HP_DIR/admin" ] || echo '    io.panelassistant.android/.platform.PanelDeviceAdminReceiver:'; }
dpm() { echo "dpm $*" >> "$HP_DIR/calls"
  [ "$1 $2" != 'set-active-admin io.panelassistant.android/io.panelassistant.android.platform.PanelDeviceAdminReceiver' ] ||
  touch "$HP_DIR/admin"; }
${adminActive ? 'touch "$HP_DIR/admin"' : ':'}
${buildPermissionGrant(nonce, 33, 'null', packageId, launchComponent)}`;
  const result = spawnSync('/bin/sh', ['-c', program], {encoding: 'utf8', env: {...process.env, HP_DIR: dir}});
  const calls = join(dir, 'calls');
  return {...result, calls: existsSync(calls) ? readFileSync(calls, 'utf8') : '', active: existsSync(join(dir, 'admin'))};
}

test('USB installation leaves fresh admin inactive and preserves existing admin', () => {
  for (const launchComponent of [OLD_CLASSES, NEW_CLASSES]) {
    const fresh = executeAdminGrant(false, PACKAGE_ID, launchComponent);
    assert.equal(fresh.active, false);
    assert.equal(fresh.calls, '');
    const existing = executeAdminGrant(true, PACKAGE_ID, launchComponent);
    assert.equal(existing.active, true);
    assert.equal(existing.calls, '');
  }
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

test('a build with old classes is granted through its own fully qualified component', () => {
  // `<id>/.Class` resolves against the application id while the classes stay in
  // the old namespace, so the shorthand names nothing.
  const service = 'io.panelassistant.android/io.github.maxlyth.hapaneld.input.PanelAccessibilityService';
  assert.equal(expectedServices('', PACKAGE_ID, OLD_CLASSES), service);
  assert.ok(!expectedServices('', PACKAGE_ID, OLD_CLASSES).includes('/.input.'));
  assert.equal(expectedServices(other, PACKAGE_ID, OLD_CLASSES), `${other}:${service}`);
});

test('a service already enabled in either spelling is kept, and the old app is never granted', () => {
  const short = 'io.panelassistant.android/.input.PanelAccessibilityService';
  const full = 'io.panelassistant.android/io.panelassistant.android.input.PanelAccessibilityService';
  // Either spelling of a new-classes component is the same enabled service, and
  // the setting reads back whichever was written.
  for (const enabled of [short, full, `${other}:${short}`]) {
    assert.equal(expectedServices(enabled, PACKAGE_ID, NEW_CLASSES), enabled);
  }
  assert.equal(expectedServices(other, PACKAGE_ID, NEW_CLASSES), `${other}:${full}`);
  // The old app's own service is another app's: kept, never counted as this one.
  const oldApp = 'io.github.maxlyth.hapaneld/.input.PanelAccessibilityService';
  assert.equal(expectedServices(oldApp, PACKAGE_ID, NEW_CLASSES), `${oldApp}:${full}`);
  for (const unknown of ['io.example.other', '', null, 'IO.PANELASSISTANT.ANDROID', LEGACY_PACKAGE_ID]) {
    assert.throws(() => expectedServices(other, unknown, OLD_CLASSES), /permissions_unverified/);
    assert.throws(() => buildPermissionGrant(nonce, 33, other, unknown, OLD_CLASSES), /permissions_unverified/);
  }
  const oldLauncher = 'io.github.maxlyth.hapaneld/.MainActivity';
  assert.throws(() => buildPermissionGrant(nonce, 33, other, LEGACY_PACKAGE_ID, oldLauncher), /permissions_unverified/);
  assert.throws(() => buildPermissionVerification(nonce, 33, LEGACY_PACKAGE_ID, oldLauncher), /permissions_unverified/);
});
