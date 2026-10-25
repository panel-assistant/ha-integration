import test from 'node:test';
import assert from 'node:assert/strict';
import { provisionUsbWebView } from '../src/webview.mjs';
import { readUsbWebView } from '../src/usb-health.mjs';

const state = transaction => ({ schema: 1, supported: true, eligible: true,
  candidate: { sha256: 'c'.repeat(64) }, transaction });

test('USB uses the existing ADB loopback carrier and sends only the pinned identity', async () => {
  const requests = [];
  const replies = [state(null), state({state: 'deferred', reason: 'no_privileged_route'})];
  const adb = { createSocket: async service => {
    assert.equal(service, 'tcp:8888\0');
    const body = JSON.stringify(replies.shift());
    return { writable: new WritableStream({write(bytes) {requests.push(new TextDecoder().decode(bytes));}}),
      readable: new ReadableStream({start(controller) {
        controller.enqueue(new TextEncoder().encode(`HTTP/1.1 200 OK\r\nContent-Length: ${body.length}\r\n\r\n${body}`));
        controller.close();
      }}), close: async () => {} };
  }};
  let quarantined = false;
  assert.equal(await provisionUsbWebView(adb, 'pa-usb-id', {quarantine: () => {quarantined = true;}}), 'deferred');
  assert.match(requests[0], /^GET \/api\/v1\/webview/);
  assert.match(requests[1], /^POST \/api\/v1\/webview/);
  assert.deepEqual(JSON.parse(requests[1].split('\r\n\r\n')[1]), {
    request_id: 'pa-usb-id', artifact_sha256: 'c'.repeat(64),
  });
  assert.equal(quarantined, false);
});

test('an old app refusing the optional route keeps its USB session', async () => {
  let quarantined = false;
  const adb = {createSocket: async () => ({
    writable: new WritableStream(), readable: new ReadableStream({start(controller) {
      controller.enqueue(new TextEncoder().encode('HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n'));
      controller.close();
    }}), close: async () => {},
  })};
  assert.equal(await readUsbWebView(adb, {quarantine: () => {quarantined = true;}}), null);
  assert.equal(quarantined, false);
});

test('resumed, unknown, and lost-reply operations are never submitted again', async () => {
  for (const terminal of ['installed', 'deferred', 'rejected', 'submitted_unknown']) {
    const calls = [];
    await provisionUsbWebView({}, 'id', {}, {read: async (_, options) => {
      calls.push(options); return state({state: terminal});
    }});
    assert.equal(calls.length, 1);
    assert.equal(calls[0].request, undefined);
  }
  let submissions = 0;
  assert.equal(await provisionUsbWebView({}, 'id', {}, {read: async (_, options) => {
    if (!options.request) return state(null);
    submissions++; throw new Error('reply lost');
  }}), 'unknown');
  assert.equal(submissions, 1);
});

test('a lost optional reply closes its socket and leaves setup usable; failed close retires the connection', async () => {
  for (const closeFails of [false, true]) {
    let quarantined = false, closed = false;
    const adb = { createSocket: async () => ({
      writable: new WritableStream(), readable: new ReadableStream(),
      close: async () => { closed = true; if (closeFails) throw new Error('close failed'); },
    }) };
    await assert.rejects(readUsbWebView(adb, {
      timeoutMs: 5, quarantine: () => { quarantined = true; },
      request: {request_id: 'id', artifact_sha256: 'c'.repeat(64)},
    }));
    assert.equal(closed, true);
    assert.equal(quarantined, closeFails);
  }
});

test('polling in-progress work stops at its bound without another submission', async () => {
  let reads = 0, submissions = 0;
  const result = await provisionUsbWebView({}, 'id', {}, {
    read: async (_, options) => { reads++; if (options.request) submissions++; return state({state: 'downloading'}); },
    pause: async () => {}, attempts: 2,
  });
  assert.equal(result, 'downloading');
  assert.equal(reads, 3);
  assert.equal(submissions, 0);
});
