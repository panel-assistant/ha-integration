import test from 'node:test';
import assert from 'node:assert/strict';
import { PANEL_HTTP_SERVICE } from '../src/panel-http.mjs';
import { readUsbHealth } from '../src/usb-health.mjs';

// A panel whose adbd only accepts the NUL-terminated form, as hall-class adbd does.
function strictPanel(response) {
  const opened = [];
  return {
    opened,
    async createSocket(service) {
      opened.push(service);
      if (service !== 'tcp:8888\0') throw new Error('Socket open failed');
      const bytes = new TextEncoder().encode(response);
      return {
        writable: new WritableStream(),
        readable: new ReadableStream({ start(c) { c.enqueue(bytes); c.close(); } }),
        close: async () => {},
      };
    },
  };
}

const health = 'ha-paneld 1.2.3 panel=hall_panel build=42 cfg=0123abcd\n';
const ok = body => `HTTP/1.1 200 OK\r\nContent-Length: ${body.length}\r\n\r\n${body}`;

test('the app port is opened NUL-terminated, the way host adb opens it', () => {
  assert.equal(PANEL_HTTP_SERVICE, 'tcp:8888\0');
});

test('the health read reaches a panel that refuses an unterminated service name', async () => {
  const adb = strictPanel(ok(health));
  assert.equal(await readUsbHealth(adb, { versionName: '1.2.3' }, { quarantine: () => {} }), true);
  assert.deepEqual(adb.opened, [PANEL_HTTP_SERVICE]);
});

test('the health read recognises a dev build by its free-form versionName', async () => {
  const devBuild = 'ha-paneld 0.9.7-rc3+dev.1 panel=hall_panel build=771 cfg=0123abcd\n';
  assert.equal(await readUsbHealth(strictPanel(ok(devBuild)), { versionName: '0.9.7-rc3+dev.1' }, { quarantine: () => {} }), true);
  assert.equal(await readUsbHealth(strictPanel(ok(devBuild)), { versionName: '0.9.7-rc3' }, { quarantine: () => {} }), false);
  await assert.rejects(readUsbHealth(strictPanel(ok(devBuild)), { versionName: '0.9.7 rc3' }, { quarantine: () => {} }),
    { code: 'invalid_request' });
});

const echoHealth = 'ha-paneld 1.2.3 panel=hall_panel build=1794013376319 cfg=0123abcd' +
  ` did=${'a'.repeat(64)} identity=install legacy_did=${'b'.repeat(64)}` +
  ' pkg=io.panelassistant.android vc=2375 proto=5-5 setup_complete=1 home_pending=0' +
  ' ha=normal ha_reason=unknown ha_grace_ms=0 ha_net=severe ha_net_cause=loss' +
  ' ha_net_source=echo ha_net_echo_n=30 ha_net_echo_miss=7 ha_net_echo_p95=7 ha_net_echo_bursts=6' +
  ' ha_resp=healthy ha_net_p95=37 ha_net_n=30 ha_net_miss=0 ha_net_age=456\n';
const chunked = body => 'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n' +
  `${body.length.toString(16)}\r\n${body}\r\n0\r\n\r\n`;
const atSize = size => echoHealth.trimEnd() + ' extension=' +
  'x'.repeat(size - echoHealth.length - ' extension='.length) + '\n';

for (const [encoding, response] of [['content-length', ok], ['chunked', chunked]]) {
  test(`USB health accepts echo evidence through the 1024-byte bound over ${encoding}`, async () => {
    assert.ok(echoHealth.length > 512 && echoHealth.length < 1024);
    for (const body of [echoHealth, atSize(1024)]) {
      const adb = strictPanel(response(body));
      assert.equal(await readUsbHealth(adb, { versionName: '1.2.3' }, {
        quarantine() { assert.fail('valid health must keep the USB session'); },
      }), true);
      assert.deepEqual(adb.opened, [PANEL_HTTP_SERVICE]);
    }
  });

  test(`USB health rejects a body above 1024 bytes over ${encoding}`, async () => {
    let quarantined = 0;
    await assert.rejects(readUsbHealth(strictPanel(response(atSize(1025))), { versionName: '1.2.3' }, {
      quarantine() { quarantined++; },
    }), { code: 'health_malformed' });
    assert.equal(quarantined, 1);
  });
}
