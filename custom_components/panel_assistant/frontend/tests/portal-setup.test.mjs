import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync, writeFileSync, readFileSync, existsSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createUsbTransactionPorts} from '../src/usb-transaction-ports.mjs';
import {LEGACY_LAUNCH_COMPONENT} from '../src/app-identity.mjs';
import {CHOOSER_DUMP} from '../src/home-chooser.mjs';
import {META_CONTROL_CENTER, META_RESTORE_RECORD, META_SCREENSAVER_COMPONENTS,
  META_SCREENSAVER_DEFAULT_COMPONENT} from '../src/vendor-provisioning.mjs';

// The USB installer's grant, readback and Meta setup programs run unchanged in
// a real shell whose settings, appops, pm, cmd, getprop and dumpsys keep one
// panel's state in files. Only the restore record's path moves into that state.
const STAND_IN = String.raw`
settings() {
  if [ "$1" = get ]; then
    if [ -f "$STATE/$3" ]; then cat "$STATE/$3"; else echo null; fi
  else
    echo "settings $*" >> "$STATE/calls"
    printf '%s\n' "$4" > "$STATE/$3"
  fi
}
appops() {
  if [ "$1" = set ]; then echo "appops $*" >> "$STATE/calls"; echo "$4" > "$STATE/appop.$2.$3"
  elif [ -f "$STATE/appop.$2.$3" ]; then echo "$3: $(cat "$STATE/appop.$2.$3")"
  else echo "$3: default"; fi
}
pm() {
  if [ "$1" = path ]; then [ ! -f "$STATE/package.$2" ] || echo "package:/system/app/$2.apk"
  elif [ "$1" = list ]; then echo reqGlEsVersion=0x30002; [ ! -f "$STATE/microphone" ] || echo feature:android.hardware.microphone
  elif [ "$1" = grant ]; then echo "pm $*" >> "$STATE/calls"; echo true > "$STATE/permission.$3"; fi
}
dumpsys() {
  for p in POST_NOTIFICATIONS RECORD_AUDIO; do
    g=false; [ ! -f "$STATE/permission.android.permission.$p" ] || g=true
    echo "      android.permission.$p: granted=$g, flags=[ USER_SET]"
  done
}
cmd() {
  if [ "$2" = resolve-activity ]; then
    echo "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true"
    if [ -f "$STATE/home" ]; then cat "$STATE/home"; else echo android/com.android.internal.app.ResolverActivity; fi
  elif [ "$2" = set-home-activity ]; then
    echo "cmd $*" >> "$STATE/calls"
    [ -f "$STATE/refuse.home" ] || echo "$3" > "$STATE/home"
  fi
}
getprop() { if [ -f "$STATE/prop.$1" ]; then cat "$STATE/prop.$1"; else echo generic; fi; }
sleep() { :; }
input() {
  echo "input $*" >> "$STATE/calls"
  if [ "$*" = "keyevent 3" ] && [ -f "$STATE/chooser.xml" ]; then touch "$STATE/chooser.shown"
  elif [ "$1" = tap ] && [ -f "$STATE/chooser.shown" ] && [ "$2 $3" = "$(cat "$STATE/chooser.tap")" ]; then
    cp "$STATE/chooser.home" "$STATE/home"; fi
}
uiautomator() { if [ -f "$STATE/chooser.shown" ]; then cp "$STATE/chooser.xml" "$2"; fi; }
`;
// The chooser a Portal showed on 2026-10-11 after set-home-activity, Always at
// [772,601][862,646]; the build under test is labelled ha-paneld.
const CHOOSER = readFileSync(new URL('../../../../tests/fixtures/portal_home_chooser.xml', import.meta.url), 'utf8')
  .replaceAll('Panel Assistant', 'ha-paneld');

function panel({portal = false, microphone = true} = {}) {
  const state = mkdtempSync(join(tmpdir(), 'portal-setup-'));
  if (microphone) writeFileSync(join(state, 'microphone'), '');
  if (portal) {
    writeFileSync(join(state, 'prop.ro.product.device'), 'omni\n');
    writeFileSync(join(state, 'screensaver_components'), `${META_SCREENSAVER_COMPONENTS}\n`);
    writeFileSync(join(state, 'screensaver_default_component'), `${META_SCREENSAVER_DEFAULT_COMPONENT}\n`);
    writeFileSync(join(state, `package.${META_CONTROL_CENTER}`), '');
  }
  const target = {model: 'Portal', serial: 'serial', primaryAbi: 'arm64-v8a', androidSdk: 33,
    rootMode: 'rootless', usbVendorId: 1, usbProductId: 2, usbSerial: 'usb'};
  const artifact = {apkSize: 1234, apkSha256: 'a'.repeat(64),
    packageId: LEGACY_LAUNCH_COMPONENT.split('/')[0], launchComponent: LEGACY_LAUNCH_COMPONENT};
  const release = {kind: 'authenticated-apk-bytes', descriptor: artifact};
  const commands = [];
  const adb = {async createSocket(command) {
    commands.push(command);
    const n = command.match(/BEGIN:([a-f0-9]{32})/)[1];
    let body;
    if (command.includes('HAPANELD_POSTURE_BEGIN:')) {
      const fields = {MODEL: target.model, SERIAL: target.serial, ABI: target.primaryAbi, SDK: '33',
        UID: '2000', SECURE: '1', DEBUGGABLE: '0', SU: 'absent'};
      body = [`HAPANELD_POSTURE_BEGIN:${n}`, ...Object.entries(fields).flatMap(([key, value]) =>
        [`HAPANELD_POSTURE_${key}_BEGIN:${n}`, value, `HAPANELD_POSTURE_${key}_END:${n}:0`]),
      `HAPANELD_POSTURE_END:${n}`, ''].join('\n');
    } else if (command.includes('HAPANELD_INSTALLED_BEGIN:')) {
      const path = '/data/app/test/base.apk';
      body = [`HAPANELD_INSTALLED_BEGIN:${n}`, 'present', path,
        `HAPANELD_INSTALLED_MODE_BEGIN:${n}`, '81a4', `HAPANELD_INSTALLED_MODE_END:${n}:0`,
        `HAPANELD_INSTALLED_SIZE_BEGIN:${n}`, '1234', `HAPANELD_INSTALLED_SIZE_END:${n}:0`,
        `HAPANELD_INSTALLED_SHA_BEGIN:${n}`, `${artifact.apkSha256}  ${path}`,
        `HAPANELD_INSTALLED_SHA_END:${n}:0`, `HAPANELD_INSTALLED_END:${n}`, ''].join('\n');
    } else {
      body = spawnSync('/bin/sh', ['-c', STAND_IN +
        command.replace(/^shell:/, '').replaceAll(META_RESTORE_RECORD, join(state, 'restore-record'))
          .replaceAll(CHOOSER_DUMP, join(state, 'chooser-dump.xml'))],
      {encoding: 'utf8', env: {PATH: process.env.PATH, STATE: state}}).stdout;
    }
    return {readable: new ReadableStream({start(controller) {
      controller.enqueue(new TextEncoder().encode(body)); controller.close();
    }}), close: async () => {}};
  }};
  const ports = createUsbTransactionPorts({adb,
    usbDevice: {vendorId: 1, productId: 2, serialNumber: 'usb'}, authenticate: async () => release,
    quarantine: () => {}});
  const read = name => existsSync(join(state, name)) ? readFileSync(join(state, name), 'utf8').replace(/\n$/, '') : null;
  const calls = () => (read('calls') ?? '').split('\n').filter(Boolean);
  const commission = async () => {
    await ports.authenticate();
    return ports.commissionPermissions({phase: 'healthy', target, artifact}, release);
  };
  return {state, commands, read, calls, commission,
    cleanup: () => rmSync(state, {recursive: true, force: true})};
}

test('every USB install makes the app HOME and grants the microphone, with no Meta steps', async () => {
  const p = panel();
  try {
    const result = await p.commission();
    assert.equal(result.permissionsVerified, true);
    assert.equal(result.home, true);
    assert.equal(result.microphone, 'granted');
    assert.equal(result.vendor, undefined);
    assert.equal(p.read('home'), 'io.github.maxlyth.hapaneld/.DashboardActivity');
    assert.ok(p.calls().includes('pm grant io.github.maxlyth.hapaneld android.permission.RECORD_AUDIO'));
    assert.ok(!p.commands.some(command => command.includes('HAPANELD_VENDOR')));
    assert.ok(!p.calls().some(call => /screensaver|alohaapps/.test(call)));
  } finally { p.cleanup(); }
});

test('a panel without a microphone is not granted one, and a HOME Android refuses is reported', async () => {
  const p = panel({microphone: false});
  writeFileSync(join(p.state, 'refuse.home'), '');
  try {
    const result = await p.commission();
    assert.equal(result.permissionsVerified, true);
    assert.equal(result.home, false);
    assert.equal(result.microphone, 'unsupported');
    assert.ok(!p.calls().some(call => call.includes('RECORD_AUDIO')));
  } finally { p.cleanup(); }
});

test('a Portal gets Meta setup read back and recorded, and a retry changes nothing more', async () => {
  const p = panel({portal: true});
  try {
    const result = await p.commission();
    assert.equal(result.home, true);
    assert.equal(result.vendor.converged, true);
    assert.deepEqual(result.vendor.record,
      [META_SCREENSAVER_COMPONENTS, META_SCREENSAVER_DEFAULT_COMPONENT, 'default']);
    assert.equal(p.read('screensaver_components'), '');
    assert.equal(p.read('screensaver_default_component'), '');
    assert.equal(p.read(`appop.${META_CONTROL_CENTER}.SYSTEM_ALERT_WINDOW`), 'deny');
    const record = p.read('restore-record');
    rmSync(join(p.state, 'calls'));

    const again = await p.commission();

    assert.equal(again.vendor.converged, true);
    assert.equal(p.read('restore-record'), record);
    assert.ok(!p.calls().some(call => /set-home-activity|screensaver|alohaapps/.test(call)), p.calls().join('\n'));
  } finally { p.cleanup(); }
});

// A panel that keeps HOME on Android's chooser after set-home-activity.
function chooserPanel(chooser = CHOOSER) {
  const p = panel({portal: true});
  writeFileSync(join(p.state, 'refuse.home'), '');
  writeFileSync(join(p.state, 'chooser.xml'), chooser);
  writeFileSync(join(p.state, 'chooser.tap'), '817 623\n');
  writeFileSync(join(p.state, 'chooser.home'), 'io.github.maxlyth.hapaneld/.DashboardActivity\n');
  return p;
}

test('a chooser offering the app has its Always pressed, once', async () => {
  const p = chooserPanel();
  try {
    const result = await p.commission();
    assert.equal(result.home, true);
    assert.equal(result.homeChooser, 'tapped');
    assert.equal(p.read('home'), 'io.github.maxlyth.hapaneld/.DashboardActivity');
    const calls = p.calls();
    assert.ok(calls.indexOf('input keyevent 3') < calls.indexOf('input tap 817 623'));
    assert.ok(!calls.includes('input keyevent 4'));
    assert.equal(existsSync(join(p.state, 'chooser-dump.xml')), false);
    rmSync(join(p.state, 'calls'));

    const again = await p.commission();

    assert.equal(again.homeChooser, 'kept');
    assert.ok(!p.calls().some(call => call.startsWith('input')));
  } finally { p.cleanup(); }
});

for (const [name, chooser, outcome] of [
  ['offers another app', CHOOSER.replaceAll('Use ha-paneld as Home', 'Use Portal Launcher as Home'), 'other_app'],
  ['has no Always button', CHOOSER.replaceAll('android:id/button_always', 'android:id/button_other'), 'no_button'],
]) {
  test(`a chooser that ${name} is dismissed and never pressed`, async () => {
    assert.notEqual(chooser, CHOOSER);
    const p = chooserPanel(chooser);
    try {
      const result = await p.commission();
      assert.equal(result.permissionsVerified, true);
      assert.equal(result.home, false);
      assert.equal(result.homeChooser, outcome);
      assert.ok(p.calls().includes('input keyevent 3'));
      assert.ok(!p.calls().some(call => call.startsWith('input tap')));
      assert.ok(p.calls().includes('input keyevent 4'));
      assert.equal(p.read('home'), null);
    } finally { p.cleanup(); }
  });
}
