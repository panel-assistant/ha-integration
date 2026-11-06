import test from 'node:test';
import assert from 'node:assert/strict';
import { createUsbTransactionPorts } from '../src/usb-transaction-ports.mjs';
import { PACKAGE_ID } from '../src/app-identity.mjs';

// The app binds the next free port when 8888 is taken and keeps it. Another
// app owns 8888 on this panel; ours listens on 8889 under its own user id.
const target = {model: 'Test panel', serial: 'serial', primaryAbi: 'arm64-v8a', androidSdk: 33,
  rootMode: 'rootless', usbVendorId: 1, usbProductId: 2, usbSerial: 'usb'};
const descriptor = {apkSize: 1234, apkSha256: 'a'.repeat(64), packageId: PACKAGE_ID, versionName: '1.2.3'};
const release = {kind: 'authenticated-apk-bytes', descriptor};
const health = 'ha-paneld 1.2.3 panel=hall_panel build=42 cfg=0123abcd\n';
const http = `HTTP/1.1 200 OK\r\nContent-Length: ${health.length}\r\n\r\n${health}`;

function fakePanel({appPort}) {
  const opened = [];
  const shell = body => ({readable: new ReadableStream({start(controller) {
    controller.enqueue(new TextEncoder().encode(body)); controller.close();
  }}), close: async () => {}, writable: new WritableStream()});
  const adb = {opened, async createSocket(command) {
    const n = command.match(/BEGIN:([a-f0-9]{32})/)?.[1];
    if (!n) {
      opened.push(command);
      if (command !== `tcp:${appPort}\0`) throw new Error('Socket open failed');
      return shell(http);
    }
    const step = command.match(/HAPANELD_([A-Z]+)_BEGIN/)[1];
    const section = (name, value) => [`HAPANELD_${step}_${name}_BEGIN:${n}`, value, `HAPANELD_${step}_${name}_END:${n}:0`];
    let lines;
    if (step === 'POSTURE') {
      lines = Object.entries({MODEL: target.model, SERIAL: target.serial, ABI: target.primaryAbi, SDK: '33',
        UID: '2000', SECURE: '1', DEBUGGABLE: '0', SU: 'absent'}).flatMap(([name, value]) => section(name, value));
    } else if (step === 'INSTALLED') {
      const path = '/data/app/test/base.apk';
      lines = ['present', path, ...section('MODE', '81a4'), ...section('SIZE', '1234'),
        ...section('SHA', `${descriptor.apkSha256}  ${path}`)];
    } else if (step === 'READY') {
      return shell(`HAPANELD_READY_BEGIN:${n}\n${appPort}\nHAPANELD_READY_END:${n}:0\n`);
    } else throw new Error(`unexpected ${step}`);
    return shell([`HAPANELD_${step}_BEGIN:${n}`, ...lines, `HAPANELD_${step}_END:${n}`, ''].join('\n'));
  }};
  return adb;
}

for (const appPort of [8888, 8889]) {
  test(`a USB install finds the app listening on ${appPort} and reads its health there`, async () => {
    const adb = fakePanel({appPort});
    const ports = createUsbTransactionPorts({adb, usbDevice: {vendorId: 1, productId: 2, serialNumber: 'usb'},
      authenticate: async () => release, quarantine() {}});
    await ports.authenticate();
    const state = await ports.inspect({phase: 'launching', target}, release);
    assert.equal(state.healthy, true);
    assert.deepEqual(adb.opened, [`tcp:${appPort}\0`]);
    assert.equal(ports.appPort(), appPort);
  });
}
