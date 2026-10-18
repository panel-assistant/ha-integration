import assert from 'node:assert/strict';
import {LAUNCH_COMPONENTS, PACKAGE_ID,
  accessibilityComponentFor, homeComponentFor} from '../../src/app-identity.mjs';

// Up to app 0.9.9 the classes stay in the old package; app 0.9.10 moved them
// into the app's own.
export const [OLD_CLASSES] = LAUNCH_COMPONENTS[PACKAGE_ID];

// A panel answering the USB installer's shell commands for one healthy job.
// `verifyLines` rewrites the permission verification's lines before they are framed.
export function permissionPanel({badTarget = false, badApk = false, badRead = false, failGrant = false,
  badVerify = false, rootMode = 'rootless', sdk = 33, launchComponent = OLD_CLASSES,
  verifyLines = lines => lines} = {}) {
  const target = {model: 'Test panel', serial: 'serial', primaryAbi: 'arm64-v8a', androidSdk: sdk,
    rootMode, usbVendorId: 1, usbProductId: 2, usbSerial: 'usb'};
  const artifact = {apkSize: 1234, apkSha256: 'a'.repeat(64),
    packageId: launchComponent.split('/')[0], launchComponent};
  const release = {kind: 'authenticated-apk-bytes', descriptor: artifact};
  const receipt = {phase: 'healthy', target, artifact};
  const commands = [];
  const adb = {async createSocket(command) {
    commands.push(command);
    const n = command.match(/BEGIN:([a-f0-9]{32})/)[1];
    let body;
    if (command.includes('HAPANELD_POSTURE_BEGIN:')) {
      const fields = {MODEL: badTarget ? 'Different panel' : target.model, SERIAL: target.serial,
        ABI: target.primaryAbi, SDK: String(sdk), UID: rootMode === 'root_adbd' ? '0' : '2000',
        SECURE: '1', DEBUGGABLE: '0', SU: rootMode === 'root_su' ? 'present' : 'absent'};
      body = [`HAPANELD_POSTURE_BEGIN:${n}`, ...Object.entries(fields).flatMap(([key, value]) =>
        [`HAPANELD_POSTURE_${key}_BEGIN:${n}`, value, `HAPANELD_POSTURE_${key}_END:${n}:0`]),
      `HAPANELD_POSTURE_END:${n}`, ''].join('\n');
    } else if (command.includes('HAPANELD_INSTALLED_BEGIN:')) {
      const path = '/data/app/test/base.apk';
      body = [`HAPANELD_INSTALLED_BEGIN:${n}`, 'present', path,
        `HAPANELD_INSTALLED_MODE_BEGIN:${n}`, '81a4', `HAPANELD_INSTALLED_MODE_END:${n}:0`,
        `HAPANELD_INSTALLED_SIZE_BEGIN:${n}`, '1234', `HAPANELD_INSTALLED_SIZE_END:${n}:0`,
        `HAPANELD_INSTALLED_SHA_BEGIN:${n}`, `${badApk ? 'b'.repeat(64) : artifact.apkSha256}  ${path}`,
        `HAPANELD_INSTALLED_SHA_END:${n}:0`, `HAPANELD_INSTALLED_END:${n}`, ''].join('\n');
    } else if (command.includes('HAPANELD_LAUNCH_BEGIN:')) {
      body = `HAPANELD_LAUNCH_BEGIN:${n}\nStatus: ok\nHAPANELD_LAUNCH_END:${n}:0\n`;
    } else if (command.includes('HAPANELD_VENDOR_BEGIN:')) {
      body = `HAPANELD_VENDOR_BEGIN:${n}\ndevice:other\nHAPANELD_VENDOR_END:${n}:0\n`;
    } else if (command.includes('HAPANELD_PERMISSIONS_READ_BEGIN:')) {
      body = `HAPANELD_PERMISSIONS_READ_BEGIN:${n}\n${badRead ? "cannot read settings" : 'com.other/.Reader'}\nHAPANELD_PERMISSIONS_READ_END:${n}:0\n`;
    } else if (command.includes('HAPANELD_PERMISSIONS_GRANT_BEGIN:')) {
      body = `HAPANELD_PERMISSIONS_GRANT_BEGIN:${n}\nhome_chooser:kept\nHAPANELD_PERMISSIONS_GRANT_END:${n}:${failGrant ? 1 : 0}\n`;
    } else {
      assert.ok(command.includes('HAPANELD_PERMISSIONS_VERIFY_BEGIN:'));
      body = [`HAPANELD_PERMISSIONS_VERIFY_BEGIN:${n}`, ...verifyLines([`com.other/.Reader:${accessibilityComponentFor(launchComponent)}`, '1',
        `WRITE_SETTINGS: ${badVerify ? 'deny' : 'allow'}`, 'SYSTEM_ALERT_WINDOW: allow',
        'REQUEST_INSTALL_PACKAGES: allow',
        sdk >= 33 ? 'android.permission.POST_NOTIFICATIONS: granted=true, flags=[]' : 'not_required',
        'granted', 'granted', 'granted', `home:${homeComponentFor(launchComponent)}`, 'device:generic']),
        `HAPANELD_PERMISSIONS_VERIFY_END:${n}:0`, ''].join('\n');
    }
    return {readable: new ReadableStream({start(controller) {
      controller.enqueue(new TextEncoder().encode(body)); controller.close();
    }}), close: async () => {}};
  }};
  const usbDevice = {vendorId: 1, productId: 2, serialNumber: 'usb'};
  return {adb, usbDevice, target, release, receipt, commands};
}
