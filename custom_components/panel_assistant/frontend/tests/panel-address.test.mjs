import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { buildAddressProbe, parsePanelAddress, readSetupUrl, setupUrl } from '../src/panel-address.mjs';

const nonce = 'a'.repeat(32);
const framed = lines => `HAPANELD_ADDR_BEGIN:${nonce}\n${lines.join('\n')}\nHAPANELD_ADDR_END:${nonce}\n`;
const inet = (index, iface, address) =>
  `${index}: ${iface}    inet ${address} brd 0.0.0.0 scope global ${iface}\\       valid_lft forever preferred_lft forever`;

test('the panel is found at its private Wi-Fi address', () => {
  assert.equal(parsePanelAddress(framed([inet(3, 'wlan0', '172.16.5.20/24')]), nonce), '172.16.5.20');
  assert.equal(setupUrl('172.16.5.20'), 'http://172.16.5.20:8888/setup');
  assert.equal(setupUrl('172.16.5.20', 8889), 'http://172.16.5.20:8889/setup', 'the port the app took');
});

test('Wi-Fi is preferred over Ethernet, and both over anything else', () => {
  const both = framed([inet(2, 'eth0', '10.20.30.5/24'), inet(3, 'wlan0', '192.168.1.40/24')]);
  assert.equal(parsePanelAddress(both, nonce), '192.168.1.40');
  assert.equal(parsePanelAddress(framed([inet(2, 'eth0', '10.20.30.5/24')]), nonce), '10.20.30.5');
  assert.equal(parsePanelAddress(framed([inet(4, 'tun0', '10.8.0.2/24')]), nonce), null);
});

test('a public, reserved or malformed address never becomes a destination', () => {
  for (const address of ['8.8.8.8/24', '100.64.0.1/32', '169.254.1.2/16', '172.32.0.1/16',
    '172.15.9.9/16', '300.1.1.1/24', '192.168.1.1/40']) {
    assert.equal(parsePanelAddress(framed([inet(3, 'wlan0', address)]), nonce), null, address);
  }
});

test('output without this exact nonce is ignored', () => {
  const other = 'b'.repeat(32);
  const forged = `HAPANELD_ADDR_BEGIN:${other}\n${inet(3, 'wlan0', '192.168.1.40/24')}\nHAPANELD_ADDR_END:${other}\n`;
  assert.equal(parsePanelAddress(forged, nonce), null);
  assert.equal(parsePanelAddress(inet(3, 'wlan0', '192.168.1.40/24'), nonce), null);
  assert.equal(parsePanelAddress('x'.repeat(9000), nonce), null);
});

test('the probe refuses an unexpected nonce and no address offers no setup URL', () => {
  assert.throws(() => buildAddressProbe('; rm -rf /'), /invalid_nonce/);
  assert.equal(setupUrl(null), null);
});

test('the setup address is read by executing the live shell probe', async () => {
  const adb = { async createSocket(service) {
    assert.ok(service.startsWith('shell:'), 'the existing shell service runs the probe');
    const result = spawnSync('/bin/sh', ['-c', `
ip() { [ "$*" = "-4 -o addr show scope global" ] || return 1; printf '%s\\n' '${inet(3, 'wlan0', '192.168.1.40/24')}'; }
${service.slice('shell:'.length)}`], {encoding: 'utf8', timeout: 2000});
    assert.equal(result.status, 0);
    return { writable: new WritableStream(), close: async () => {},
      readable: new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(result.stdout)); c.close(); } }) };
  } };
  assert.equal(await readSetupUrl(adb, () => nonce), 'http://192.168.1.40:8888/setup');
});
