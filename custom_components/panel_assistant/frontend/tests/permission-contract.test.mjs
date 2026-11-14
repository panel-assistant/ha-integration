import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {existsSync, mkdtempSync, readFileSync, writeFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {buildPermissionRead, parsePermissionRead,
  buildPermissionGrant, parsePermissionGrant, expectedServices,
  buildPermissionVerification, parsePermissionVerification, runtimePermissionRepair} from '../src/permission-contract.mjs';
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
    {permissionsVerified: true, notificationsRequired: true, denied: [], camera: 'granted', microphone: 'granted', secureSettings: 'granted', home: true, device: 'generic'});
  assert.deepEqual(parsePermissionVerification(verified(32), nonce, 32, other, PACKAGE_ID, OLD_CLASSES),
    {permissionsVerified: true, notificationsRequired: false, denied: [], camera: 'granted', microphone: 'granted', secureSettings: 'granted', home: true, device: 'generic'});
  for (const invalid of [verified().replace(other + ':', ''), verified().replace('\n1\n', '\n0\n'),
    verified().replace('WRITE_SETTINGS: allow', 'WRITE_SETTINGS: default'),
    verified().replace('SYSTEM_ALERT_WINDOW: allow', 'SYSTEM_ALERT_WINDOW: unknown'),
    verified().replace('REQUEST_INSTALL_PACKAGES: allow', 'REQUEST_INSTALL_PACKAGES: default'),
    verified().replace('\nREQUEST_INSTALL_PACKAGES: allow', ''),
    // A refused grant nobody had decided is not the owner's choice.
    verified().replace('granted=true, flags=[ USER_SET|USER_FIXED]', 'granted=false, flags=[ USER_SENSITIVE_WHEN_GRANTED]'),
    verified().replace('granted=true', 'granted=false').replace('flags=[', 'invalid=['),
    verified().replace('flags=[', 'invalid=['),
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
      {permissionsVerified: true, notificationsRequired: sdk >= 33, denied: [], camera: 'granted', microphone: 'granted',
        secureSettings: 'granted', home: true, device: 'generic'}, `sdk ${sdk}`);
    // Another app's grants never count as this one's.
    assert.throws(() => parsePermissionVerification(executeVerification(sdk, 'com.example.other'),
      nonce, sdk, other, PACKAGE_ID, OLD_CLASSES), /permissions_unverified/);
  }
});

function executeGrant(sdk, existing, {live = existing, rejectAppops = false} = {}) {
  const program = `settings() { if [ "$1" = get ]; then printf '%s\\n' '${live}'; else echo "settings $*" >&2; fi; }
pm() { echo "pm $*" >> "$HP_CALLS"; }
dumpsys() { echo '      android.permission.POST_NOTIFICATIONS: granted=false, flags=[ USER_SENSITIVE_WHEN_GRANTED]'; }
appops() { if [ "$1" = get ]; then echo "$3: default"; else echo "appops $*" >&2; ${rejectAppops ? 'return 1' : ':'}; fi; }
${buildPermissionGrant(nonce, sdk, existing, PACKAGE_ID, OLD_CLASSES)}`;
  const calls = join(mkdtempSync(join(tmpdir(), 'grant-')), 'calls');
  const result = spawnSync('/bin/sh', ['-c', program], {encoding: 'utf8', env: {...process.env, HP_CALLS: calls}});
  return {...result, calls: existsSync(calls) ? readFileSync(calls, 'utf8') : ''};
}

test('real shell grant program is scoped, preserves other services and gates notifications by SDK', () => {
  const result = executeGrant(33, other);
  parsePermissionGrant(result.stdout, nonce);
  assert.equal(result.status, 0);
  assert.ok(result.stderr.includes(`settings put secure enabled_accessibility_services ${other}:${ACCESSIBILITY_SERVICE}`));
  assert.match(result.stderr, /settings put secure accessibility_enabled 1/);
  assert.match(result.stderr, /appops set io.panelassistant.android REQUEST_INSTALL_PACKAGES allow/);
  assert.equal(result.stderr.trim().split('\n').length, 5);
  // Grant output is discarded, so grants are seen through a calls file.
  assert.match(result.calls, /pm grant io.panelassistant.android android.permission.POST_NOTIFICATIONS/);
  assert.match(result.calls, /pm grant io.panelassistant.android android.permission.WRITE_SECURE_SETTINGS/);
  assert.ok(!executeGrant(32, other).calls.includes('POST_NOTIFICATIONS'));
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

// A panel that keeps its grants between commands, so the grant program and the
// readback run against one state, the way Android answers them. Each runtime
// permission is one dump line (absent when `null`); each app operation is what
// `appops get` prints for it. With `header` the dump shows the package, as a
// real one does, and a grant adds the line Android 9 and older leave out until
// somebody decides the permission.
function ownerPanel({notifications, camera = 'granted=true, flags=[ USER_SET]', microphone = camera,
  ops = {}, sdk = 34, header = false} = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'owner-'));
  if (header) writeFileSync(join(dir, 'header'), `  Package [${PACKAGE_ID}] (1a2b3c):\n`);
  const runtime = {POST_NOTIFICATIONS: notifications, CAMERA: camera, RECORD_AUDIO: microphone,
    WRITE_SECURE_SETTINGS: 'granted=true'};
  for (const [name, line] of Object.entries(runtime)) {
    if (line != null) writeFileSync(join(dir, `perm.${name}`), `      android.permission.${name}: ${line}\n`);
  }
  for (const op of ['WRITE_SETTINGS', 'SYSTEM_ALERT_WINDOW', 'REQUEST_INSTALL_PACKAGES']) {
    writeFileSync(join(dir, `op.${op}`), `${ops[op] ?? `${op}: default`}\n`);
  }
  writeFileSync(join(dir, 'services'), `${other}\n`);
  const stubs = `dumpsys() { [ ! -e "$D/header" ] || cat "$D/header"; for f in "$D"/perm.*; do [ ! -e "$f" ] || cat "$f"; done; }
pm() { case "$1" in
  grant) echo "pm $*" >> "$D/calls"; n=\${3##*.}; [ ! -e "$D/perm.$n" ] && [ ! -e "$D/header" ] ||
    printf '      %s: granted=true, flags=[ USER_SENSITIVE_WHEN_GRANTED]\\n' "$3" > "$D/perm.$n" ;;
  list) printf 'feature:android.hardware.camera.any\\nfeature:android.hardware.microphone\\n' ;; esac; }
appops() { case "$1" in get) cat "$D/op.$3" ;; set) echo "appops $*" >> "$D/calls"; echo "$3: $4" > "$D/op.$3" ;; esac; }
settings() { if [ "$1" = get ]; then case "$3" in enabled_accessibility_services) cat "$D/services" ;; *) echo 1 ;; esac
  elif [ "$3" = enabled_accessibility_services ]; then printf '%s\\n' "$4" > "$D/services"; fi; }
am() { case "$*" in
  *"operation dashboard_home_policy"*) echo 'Broadcast completed: result=1, data="panel-assistant-home-builtin"' ;;
  *"broadcast"*) echo 'Broadcast completed: result=1, data="panel-assistant-setup-eligible"' ;; esac; }
cmd() { echo '${homeComponentFor(OLD_CLASSES)}'; }
getprop() { echo generic; }
input() { return 1; }; uiautomator() { return 1; }; sleep() { return 1; }`;
  const run = program => spawnSync('/bin/sh', ['-c', `${stubs}\n${program}`],
    {encoding: 'utf8', env: {...process.env, D: dir}, timeout: 5000});
  try {
    const grant = run(buildPermissionGrant(nonce, sdk, other, PACKAGE_ID, OLD_CLASSES));
    parsePermissionGrant(grant.stdout, nonce);
    const calls = existsSync(join(dir, 'calls')) ? readFileSync(join(dir, 'calls'), 'utf8') : '';
    const verify = run(buildPermissionVerification(nonce, sdk, PACKAGE_ID, OLD_CLASSES));
    return {calls, read: () => parsePermissionVerification(verify.stdout, nonce, sdk, other, PACKAGE_ID, OLD_CLASSES)};
  } finally { rmSync(dir, {recursive: true, force: true}); }
}
const undecided = 'granted=false, flags=[ USER_SENSITIVE_WHEN_GRANTED|USER_SENSITIVE_WHEN_DENIED]';

test("a runtime permission the owner or a policy denied is kept, reported, and the install still verifies", () => {
  for (const flags of ['USER_SET', 'USER_FIXED', 'USER_SET|USER_FIXED', 'POLICY_FIXED',
    'USER_SENSITIVE_WHEN_GRANTED|USER_SET|USER_SENSITIVE_WHEN_DENIED']) {
    const denial = `granted=false, flags=[ ${flags}]`;
    const panel = ownerPanel({notifications: denial, camera: denial, microphone: denial});
    assert.ok(!/pm grant \S+ android\.permission\.(POST_NOTIFICATIONS|CAMERA|RECORD_AUDIO)/.test(panel.calls), flags);
    const result = panel.read();
    assert.equal(result.permissionsVerified, true, flags);
    assert.equal(result.camera, 'denied', flags);
    assert.equal(result.microphone, 'denied', flags);
    assert.deepEqual(result.denied, ['camera', 'microphone', 'notifications'], flags);
  }
});

test("Android's own USER_SENSITIVE defaults are not a decision: those permissions are granted", () => {
  const panel = ownerPanel({notifications: undecided, camera: undecided, microphone: 'granted=false, flags=[ ]'});
  for (const permission of ['POST_NOTIFICATIONS', 'CAMERA', 'RECORD_AUDIO']) {
    assert.ok(panel.calls.includes(`pm grant ${PACKAGE_ID} android.permission.${permission}\n`), permission);
  }
  const result = panel.read();
  assert.deepEqual([result.camera, result.microphone, result.denied], ['granted', 'granted', []]);
});

test('a runtime permission missing from a dump that does not show the package is never granted, and is not proof of a denial', () => {
  const panel = ownerPanel({notifications: undecided, camera: null, microphone: 'granted=maybe'});
  assert.ok(!/android\.permission\.(CAMERA|RECORD_AUDIO)/.test(panel.calls));
  const result = panel.read();
  assert.deepEqual([result.camera, result.microphone, result.denied], ['unknown', 'unknown', []]);
  // Notifications are required from Android 13: an unreadable line still fails.
  assert.throws(() => ownerPanel({notifications: null}).read(), /permissions_unverified/);
});

test('Android 9 and older list no line for a permission nobody decided: a fresh install grants it', () => {
  for (const sdk of [27, 28]) {
    const panel = ownerPanel({notifications: null, camera: null, microphone: null, sdk, header: true});
    for (const permission of ['CAMERA', 'RECORD_AUDIO']) {
      assert.ok(panel.calls.includes(`pm grant ${PACKAGE_ID} android.permission.${permission}\n`), `${sdk} ${permission}`);
    }
    const result = panel.read();
    assert.deepEqual([result.camera, result.microphone, result.denied], ['granted', 'granted', []], String(sdk));
  }
});

// Runs one permission's repair against a stand-in dump, recording `pm`.
function repairOn(dump, status = 0) {
  const dir = mkdtempSync(join(tmpdir(), 'repair-'));
  try {
    const lines = dump.map(line => `echo '${line}'`).join('; ');
    spawnSync('/bin/sh', ['-c', `dumpsys() { ${lines}; return ${status}; }
pm() { echo "pm $*" >> "$D/calls"; }
${runtimePermissionRepair(PACKAGE_ID, 'android.permission.RECORD_AUDIO')}`], {encoding: 'utf8', env: {...process.env, D: dir}, timeout: 5000});
    return existsSync(join(dir, 'calls')) ? readFileSync(join(dir, 'calls'), 'utf8').trim().split('\n') : [];
  } finally { rmSync(dir, {recursive: true, force: true}); }
}

test('a missing line is undecided only in a dump that succeeded and shows this package', () => {
  const header = `Package [${PACKAGE_ID}] (1a2b3c):`;
  const grant = [`pm grant ${PACKAGE_ID} android.permission.RECORD_AUDIO`];
  assert.deepEqual(repairOn([header]), grant, 'a valid dump with no line');
  assert.deepEqual(repairOn([header], 1), [], 'a failed dump');
  assert.deepEqual(repairOn([`Unable to find package: ${PACKAGE_ID}`]), [], 'no package header');
  assert.deepEqual(repairOn([`Package [${PACKAGE_ID}.other] (1a2b3c):`]), [], 'another package');
  assert.deepEqual(repairOn([header, '      android.permission.RECORD_AUDIO: granted=false, flags=[ USER_SET|USER_FIXED]']), [],
    'a decided line is kept');
  assert.deepEqual(repairOn([header, `      android.permission.RECORD_AUDIO: ${undecided}`]), grant, 'an undecided line');
});

test('an app operation the owner set is kept and reported; one nobody set is allowed', () => {
  const panel = ownerPanel({notifications: undecided, ops: {WRITE_SETTINGS: 'WRITE_SETTINGS: deny',
    SYSTEM_ALERT_WINDOW: 'SYSTEM_ALERT_WINDOW: ignore; rejectTime=+3d', REQUEST_INSTALL_PACKAGES: 'REQUEST_INSTALL_PACKAGES: default'}});
  assert.ok(!panel.calls.includes('WRITE_SETTINGS allow'));
  assert.ok(!panel.calls.includes('SYSTEM_ALERT_WINDOW allow'));
  assert.ok(panel.calls.includes(`appops set ${PACKAGE_ID} REQUEST_INSTALL_PACKAGES allow`));
  const result = panel.read();
  assert.equal(result.permissionsVerified, true);
  assert.deepEqual(result.denied, ['modify system settings', 'display over other apps']);
  for (const mode of ['foreground', 'errored']) {
    const kept = ownerPanel({notifications: undecided, ops: {REQUEST_INSTALL_PACKAGES: `REQUEST_INSTALL_PACKAGES: ${mode}`}});
    assert.ok(!kept.calls.includes('REQUEST_INSTALL_PACKAGES allow'), mode);
    assert.deepEqual(kept.read().denied, ['install packages'], mode);
  }
});

test("Android 14's two-line answer for an unset operation is unset, whatever default it names", () => {
  for (const mode of ['default', 'deny', 'ignore', 'foreground', 'errored']) {
    const panel = ownerPanel({notifications: undecided, ops: {WRITE_SETTINGS: `No operations.\nDefault mode: ${mode}`}});
    assert.ok(panel.calls.includes(`appops set ${PACKAGE_ID} WRITE_SETTINGS allow`), mode);
    assert.deepEqual(panel.read().denied, [], mode);
  }
  // An unset operation whose default already allows needs no write, and reads back as allowed.
  const allowed = ownerPanel({notifications: undecided, ops: {SYSTEM_ALERT_WINDOW: 'No operations.\nDefault mode: allow'}});
  assert.ok(!allowed.calls.includes('SYSTEM_ALERT_WINDOW allow'));
  assert.deepEqual(allowed.read().denied, []);
});

test('an unreadable operation is never set, and the readback refuses it', () => {
  const panel = ownerPanel({notifications: undecided, ops: {WRITE_SETTINGS: ''}});
  assert.ok(!panel.calls.includes('WRITE_SETTINGS allow'));
  assert.throws(() => panel.read(), /permissions_unverified/);
});
