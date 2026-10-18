import test from 'node:test';
import assert from 'node:assert/strict';
import {createUsbTransactionPorts} from '../src/usb-transaction-ports.mjs';
import {LAUNCH_COMPONENTS, PACKAGE_ID} from '../src/app-identity.mjs';
import {OLD_CLASSES, permissionPanel} from './fixtures/permission-panel.mjs';

const [, MOVED] = LAUNCH_COMPONENTS[PACKAGE_ID];

function fixture(options = {}) {
  const {adb, usbDevice, release, receipt, commands} = permissionPanel(options);
  let quarantine = 0;
  const ports = createUsbTransactionPorts({adb,
    usbDevice, authenticate: async () => release,
    quarantine: () => {quarantine++;}});
  return {ports, receipt, release, commands, get quarantined() {return quarantine;}};
}

test('permissions require current USB posture and exact installed APK before any grants', async () => {
  for (const rootMode of ['rootless', 'root_adbd', 'root_su']) {
    for (const sdk of [32, 33]) {
      const f = fixture({rootMode, sdk});
      await f.ports.authenticate();
      assert.deepEqual(await f.ports.commissionPermissions(f.receipt, f.release),
        {permissionsVerified: true, notificationsRequired: sdk >= 33, camera: 'granted', microphone: 'granted', secureSettings: 'granted', home: true, homeChooser: 'kept',
          device: 'generic'});
      assert.equal(f.commands.length, 6);
      assert.match(f.commands[5], /HAPANELD_VENDOR_BEGIN/);
      assert.match(f.commands[0], /HAPANELD_POSTURE_BEGIN/);
      assert.match(f.commands[1], /HAPANELD_INSTALLED_BEGIN/);
      assert.match(f.commands[3], /HAPANELD_PERMISSIONS_GRANT_BEGIN/);
      assert.equal(f.quarantined, 0);
      assert.ok(!f.commands.some(command => /su -c|reboot|force-stop|tcpip|setprop/.test(command)));
    }
  }
});

test('mismatched target, APK or malformed preservation read cannot reach grant', async () => {
  for (const options of [{badTarget: true}, {badApk: true}, {badRead: true}]) {
    const f = fixture(options); await f.ports.authenticate();
    await assert.rejects(f.ports.commissionPermissions(f.receipt, f.release));
    assert.equal(f.quarantined, 1);
    assert.ok(!f.commands.some(command => command.includes('PERMISSIONS_GRANT_BEGIN')));
    await assert.rejects(f.ports.authenticate(), /transaction_unavailable/);
  }
});

test('grant failure or denied readback quarantines connection and never claims completion', async () => {
  for (const options of [{failGrant: true}, {badVerify: true}]) {
    const f = fixture(options); await f.ports.authenticate();
    await assert.rejects(f.ports.commissionPermissions(f.receipt, f.release), /permissions_unverified/);
    assert.equal(f.quarantined, 1);
  }
  const f = fixture(); await f.ports.authenticate();
  await assert.rejects(f.ports.commissionPermissions({...f.receipt, phase: 'installed'}, f.release), /transaction_invalid/);
  assert.equal(f.commands.length, 0);
});

test('the launch step grants notifications before the first start from Android 13, and not below', async () => {
  for (const sdk of [32, 33, 34]) {
    const f = fixture({sdk}); await f.ports.authenticate();
    await f.ports.launch({...f.receipt, phase: 'launching'}, f.release);
    const launch = f.commands.find(command => command.includes('HAPANELD_LAUNCH_BEGIN:'));
    assert.ok(launch, 'the launch command ran');
    const grant = launch.indexOf(`pm grant ${PACKAGE_ID} android.permission.POST_NOTIFICATIONS`);
    const start = launch.indexOf('am start -W');
    assert.ok(start > 0);
    if (sdk >= 33) assert.ok(grant > 0 && grant < start, `sdk ${sdk}: grant precedes the first start`);
    else assert.equal(grant, -1, `sdk ${sdk}: no grant where the permission is not a runtime one`);
    assert.equal(f.quarantined, 0);
  }
});

test('a build whose classes moved package is started and granted by its own classes', async () => {
  const f = fixture({launchComponent: MOVED}); await f.ports.authenticate();
  await f.ports.launch({...f.receipt, phase: 'launching'}, f.release);
  assert.deepEqual(await f.ports.commissionPermissions(f.receipt, f.release),
    {permissionsVerified: true, notificationsRequired: true, camera: 'granted', microphone: 'granted', secureSettings: 'granted', home: true, homeChooser: 'kept',
      device: 'generic'});
  const launch = f.commands.find(command => command.includes('HAPANELD_LAUNCH_BEGIN:'));
  assert.ok(launch.includes(`am start -W -n ${MOVED} -p ${PACKAGE_ID};`));
  const grant = f.commands.find(command => command.includes('HAPANELD_PERMISSIONS_GRANT_BEGIN:'));
  assert.ok(grant.includes("settings put secure enabled_accessibility_services 'com.other/.Reader:"
    + "io.panelassistant.android/io.panelassistant.android.input.PanelAccessibilityService'"));
  assert.ok(!f.commands.some(command => command.includes('io.github.maxlyth.hapaneld.')));
  assert.equal(f.quarantined, 0);
});
