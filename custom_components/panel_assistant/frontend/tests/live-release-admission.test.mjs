import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { receiveReleaseHandoff } from '../src/release-handoff.mjs';
import { createUsbTransactionPorts } from '../src/usb-transaction-ports.mjs';
import { ACCEPTED_PACKAGE_IDS } from '../src/app-identity.mjs';
import { githubApkName } from '../src/release-identity.mjs';
import { RESIDUE_PROBES } from '../src/preflight.mjs';
import { startReleaseHandoff } from '../src/ha-release-handoff.mjs';

const fixture = name => readFileSync(new URL(`./fixtures/${name}`, import.meta.url));
const feed = new Uint8Array(fixture('build-feed-golden.json'));
const signature = new Uint8Array(Buffer.from(fixture('build-feed-golden.json.sig.b64').toString().trim(), 'base64'));
const apk = new Blob([fixture('build-feed-golden-772.apk.txt')]);
const build = JSON.parse(new TextDecoder().decode(feed)).builds[0];
const key = await crypto.subtle.importKey('spki',
  Buffer.from(fixture('build-feed-golden.pub.pem').toString().replace(/-----[A-Z ]+-----|\s/g, ''), 'base64'),
  { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify']);
const metadata = () => ({ id: 'a'.repeat(32), tag: 'build-772', feed: btoa(String.fromCharCode(...feed)),
  feed_signature: btoa(String.fromCharCode(...signature)), apk_size: apk.size, apk_sha256: build.apkSha256 });
const json = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const tick = () => new Promise(resolve => setImmediate(resolve));

async function session({ rcTag = null, admission = () => json(metadata()), initial = metadata(),
  selectedApk = apk, verificationKey = key } = {}) {
  const haListeners = new Set(), usbListeners = new Set(), calls = [], requests = [], replies = [];
  let hash;
  const child = { closed: false, postMessage(data, origin) {
    replies.push(data);
    for (const listener of usbListeners) void listener({ source: opener, origin: 'https://ha.example', data });
  } };
  const opener = { closed: false, postMessage(data, origin) {
    requests.push(data);
    for (const listener of haListeners) listener({ source: child, origin: 'https://installer.example', data });
  } };
  const sender = startReleaseHandoff({ async fetchWithAuth(url, init) {
    calls.push([url, init]);
    if (calls.length === 1) return json(initial);
    if (calls.length === 2) return new Response(selectedApk);
    return admission();
  } }, 'https://installer.example/', { rcTag, windowObject: {
    crypto, location: { origin: 'https://ha.example' },
    addEventListener: (_, fn) => haListeners.add(fn), removeEventListener: (_, fn) => haListeners.delete(fn),
    open: url => { hash = new URL(url).hash; return child; },
  } });
  const windowObject = { crypto, opener, isSecureContext: true, location: { hash },
    addEventListener: (_, fn) => usbListeners.add(fn), removeEventListener: (_, fn) => usbListeners.delete(fn) };
  const receiver = receiveReleaseHandoff({ windowObject, verificationKey, timeoutMs: 100 });
  const accepted = await receiver.completion;
  await sender.completion;
  return { ...accepted, sender, receiver, opener, calls, requests, replies, usbListeners,
    close() { receiver.cancel(); sender.cancel(); } };
}

test('PA default feed admission preserves the original default and binds exact bytes each time', async () => {
  const f = await session();
  try {
    assert.equal(f.release.descriptor.releaseTag, 'build-772');
    await f.authenticate(); await f.authenticate();
    assert.equal(f.calls.length, 4);
    assert.ok(f.calls.filter(([, init]) => init.method === 'POST').every(([, init]) => init.body === '{}'));
    const requests = f.requests.filter(message => message.type === 'ha-paneld/usb-admission');
    assert.equal(requests.length, 2);
    assert.notEqual(requests[0].requestId, requests[1].requestId);
    assert.equal(requests[0].tag, 'build-772');
    assert.equal(requests[0].apkSha256, build.apkSha256);
    assert.equal(f.calls.filter(([, init]) => init.method === 'GET').length, 1);
  } finally { f.close(); }
});

test('an explicit feed choice remains explicit on fresh live PA admission', async () => {
  const f = await session({ rcTag: 'build-772' });
  try {
    await f.authenticate();
    assert.equal(f.calls.at(-1)[1].body, '{"release_candidate":"build-772"}');
  } finally { f.close(); }
});

for (const [name, admission] of [
  ['offline PA', () => { throw new Error('offline'); }],
  ['denied PA', () => new Response('{}', { status: 403 })],
  ['changed recommended tag', () => json({ ...metadata(), tag: 'build-773' })],
  ['changed exact APK', () => json({ ...metadata(), apk_sha256: 'b'.repeat(64) })],
  ['malformed response', () => json({ ...metadata(), extra: true })],
]) test(`cached signed bytes cannot authorize mutation after ${name}`, async () => {
  const f = await session({ admission });
  try { await assert.rejects(f.authenticate(), /handoff_invalid/); }
  finally { f.close(); }
});

test('closed opener and an older opener that never answers fresh admission fail closed', async () => {
  const f = await session();
  try {
    f.opener.closed = true;
    await assert.rejects(f.authenticate(), /handoff_invalid/);
    f.opener.closed = false;
    f.opener.postMessage = () => {};
    await assert.rejects(f.authenticate(), /handoff_invalid/);
  } finally { f.close(); }
});

test('saved signed bundle without a live HA opener cannot enter the production handoff', () => {
  assert.throws(() => receiveReleaseHandoff({ windowObject: { isSecureContext: true, opener: null,
    location: { hash: '' } } }), /handoff_invalid/);
});

for (const changed of [{ tag: 'build-773' }, { apkSha256: 'b'.repeat(64) }, { admitted: false }, { extra: true }])
  test(`stale, wrong-origin, wrong-source and malformed replies cannot admit the next request: ${Object.keys(changed)[0]}`, async () => {
  let releaseAdmission;
  const f = await session({ admission: () => new Promise(resolve => { releaseAdmission = resolve; }) });
  try {
    const pending = f.authenticate();
    while (!releaseAdmission) await tick();
    const request = f.requests.at(-1);
    const result = { ...request, type: 'ha-paneld/usb-admission-result', admitted: true };
    for (const listener of f.usbListeners) {
      listener({ source: {}, origin: 'https://ha.example', data: result });
      listener({ source: f.opener, origin: 'https://evil.example', data: result });
      listener({ source: f.opener, origin: 'https://ha.example', data: { ...result, requestId: '0'.repeat(32) } });
    }
    let settled = false;
    void pending.finally(() => { settled = true; }).catch(() => {});
    await tick(); assert.equal(settled, false);
    for (const listener of f.usbListeners) listener({ source: f.opener, origin: 'https://ha.example',
      data: { ...result, ...changed } });
    await assert.rejects(pending, /handoff_invalid/);
    releaseAdmission(json(metadata()));
    await tick();
  } finally { f.close(); }
});

for (const phase of ['staging', 'installing']) for (const changedArtifact of [false, true])
  test(`${changedArtifact ? 'Artifact change' : 'PA loss'} after the final observation prevents ${phase} APK mutation`, async () => {
  let online = true;
  const f = await session({ admission: () => online || changedArtifact ? json(metadata()) : new Response('{}', { status: 403 }) });
  const target = { model: 'Test Panel', serial: 'SERIAL-1', primaryAbi: 'arm64-v8a', androidSdk: 34,
    rootMode: 'rootless', usbVendorId: 1, usbProductId: 2, usbSerial: 'usb' };
  const receipt = { id: 'a'.repeat(32), phase, target };
  const commands = [];
  const adb = { async createSocket(command) {
    commands.push(command);
    const n = command.match(/BEGIN:([a-f0-9]{32})/)?.[1];
    const section = (prefix, name, values, status = 0) =>
      [`${prefix}_${name}_BEGIN:${n}`, ...values, `${prefix}_${name}_END:${n}:${status}`];
    let body;
    const identity = { MODEL: target.model, SERIAL: target.serial, ABI: target.primaryAbi, SDK: '34',
      UID: '2000', SECURE: '1', DEBUGGABLE: '0', SU: 'absent' };
    if (command.includes('HAPANELD_POSTURE_BEGIN:')) {
      body = [`HAPANELD_POSTURE_BEGIN:${n}`, ...Object.entries(identity).flatMap(([name, value]) =>
        section('HAPANELD_POSTURE', name, [value])), `HAPANELD_POSTURE_END:${n}`, ''].join('\n');
    } else if (command.includes('HAPANELD_PREFLIGHT_BEGIN:')) {
      const lines = [`HAPANELD_PREFLIGHT_BEGIN:${n}`, ...Object.entries(identity).flatMap(([name, value]) =>
        section('HAPANELD_PREFLIGHT', name, [value])),
        ...section('HAPANELD_PREFLIGHT', 'LIVE', ['package:/system/framework/framework-res.apk'])];
      ACCEPTED_PACKAGE_IDS.forEach((_, index) => lines.push(
        ...section('HAPANELD_PREFLIGHT', `PACKAGE${index}`, [], 1),
        ...section('HAPANELD_PREFLIGHT', `RETAINED${index}`, [])));
      for (let index = 0; index < 3; index++) lines.push(...section('HAPANELD_PREFLIGHT', `BASE${index}`, ['readable']));
      RESIDUE_PROBES.forEach((_, index) => lines.push(...section('HAPANELD_PREFLIGHT', `RESIDUE${index}`, ['absent'])));
      body = [...lines, `HAPANELD_PREFLIGHT_END:${n}`, ''].join('\n');
    } else if (command.includes('HAPANELD_PATH_BEGIN:')) {
      online = false;
      body = `HAPANELD_PATH_BEGIN:${n}\nabsent\nHAPANELD_PATH_END:${n}:0\n`;
    } else if (command.includes('HAPANELD_PREPARE_BEGIN:')) {
      body = `HAPANELD_PREPARE_BEGIN:${n}\nHAPANELD_PREPARE_END:${n}:0\n`;
    } else if (command.includes('HAPANELD_STAGED_BEGIN:')) {
      online = false;
      body = [`HAPANELD_STAGED_BEGIN:${n}`, ...section('HAPANELD_STAGED', 'MODE', ['81a4']),
        ...section('HAPANELD_STAGED', 'SIZE', [String(apk.size)]),
        ...section('HAPANELD_STAGED', 'SHA', [`${build.apkSha256}  /data/local/tmp/ha-paneld-install-${receipt.id}.apk`]),
        `HAPANELD_STAGED_END:${n}`, ''].join('\n');
    } else { assert.fail(`APK mutation reached: ${command}`); }
    return { readable: new ReadableStream({ start(controller) {
      controller.enqueue(new TextEncoder().encode(body)); controller.close();
    } }), close: async () => {} };
  } };
  const ports = createUsbTransactionPorts({ adb, usbDevice: { vendorId: 1, productId: 2, serialNumber: 'usb' },
    authenticate: async () => {
      const current = await f.authenticate();
      return !online && changedArtifact ? { ...current, descriptor: { ...current.descriptor,
        versionCode: current.descriptor.versionCode + 1 } } : current;
    }, quarantine() {} });
  try {
    const authenticated = await ports.authenticate();
    await assert.rejects(ports[phase === 'staging' ? 'stage' : 'install'](receipt, authenticated),
      changedArtifact ? /artifact_changed/ : /handoff_invalid/);
    assert.ok(!commands.some(command => command === 'sync:' || command.includes('HAPANELD_INSTALL_BEGIN:')));
    assert.equal(f.calls.length, 4, 'fresh PA admission occurred after the final panel read');
  } finally { f.close(); }
});

test('cached explicit signed bytes cannot authorize mutation after offline PA', async () => {
  const f = await session({ rcTag: 'build-772', admission: () => { throw new Error('offline'); } });
  try { await assert.rejects(f.authenticate(), /handoff_invalid/); }
  finally { f.close(); }
});

for (const [tag, rcTag] of [['v0.9.8', 'v0.9.8'], ['v0.9.8-rc2', null]]) {
  test(`live handoff admits ${tag} while preserving its original selection`, async () => {
    const pair = await crypto.subtle.generateKey({ name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048,
      publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' }, false, ['sign', 'verify']);
    const encoder = new TextEncoder();
    const descriptor = { ...build, releaseTag: tag, versionName: tag.slice(1),
      apkName: githubApkName(tag, build.packageId), schema: 'io.github.maxlyth.hapaneld.install.v1' };
    for (const field of ['apkPath', 'commit', 'published']) delete descriptor[field];
    const bytes = encoder.encode(`${JSON.stringify(Object.fromEntries(Object.keys(descriptor).sort()
      .map(field => [field, descriptor[field]])))}\n`);
    const checksum = encoder.encode(`${build.apkSha256}  ${descriptor.apkName}\n`);
    const sign = async value => btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.sign(
      'RSASSA-PKCS1-v1_5', pair.privateKey, value))));
    const initial = { id: 'a'.repeat(32), tag, checksum: btoa(String.fromCharCode(...checksum)),
      checksum_signature: await sign(checksum), descriptor: btoa(String.fromCharCode(...bytes)),
      descriptor_signature: await sign(bytes), apk_size: apk.size, apk_sha256: build.apkSha256 };
    const f = await session({ rcTag, initial, verificationKey: pair.publicKey, admission: () => json(initial) });
    try {
      assert.equal(f.release.descriptor.releaseTag, tag);
      await f.authenticate();
      assert.equal(f.calls.at(-1)[1].body, rcTag === null ? '{}' : JSON.stringify({ release_candidate: tag }));
    } finally { f.close(); }
  });
}
