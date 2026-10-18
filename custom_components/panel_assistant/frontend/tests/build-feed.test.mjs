import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ReleaseVerificationError, parseUnauthenticatedDescriptor,
  verifyReleaseBundle } from '../src/release-verifier.mjs';
import { ApkVerificationError, verifyApkBundle } from '../src/apk-verifier.mjs';
import { handoffOptions, receiveReleaseHandoff } from '../src/release-handoff.mjs';
import { LEGACY_PACKAGE_ID, PACKAGE_ID } from '../src/app-identity.mjs';
import { ALGORITHM, SIGNER, apkOf, buildTag, bundle, canonical, document, entry, release, serialise,
  sha256 } from './signed-feed.mjs';

const stranger = await crypto.subtle.generateKey(ALGORITHM, false, ['sign', 'verify']);
const encoder = new TextEncoder();
const OLD_APP_LAUNCH = 'io.github.maxlyth.hapaneld/.MainActivity';
// A build of the old app, as feeds up to 0.9.10 carried it.
const oldApp = (code, overrides = {}) => entry(code, { packageId: LEGACY_PACKAGE_ID,
  launchComponent: OLD_APP_LAUNCH, ...overrides });
const verify = (value, tag = value.tag, key = release.publicKey) =>
  verifyReleaseBundle(value, { expectedRcTag: tag }, key);
const refuses = promise => assert.rejects(promise, ReleaseVerificationError);

test('a signed feed build becomes the v1 install descriptor the installer already uses', async () => {
  const build = await entry(772);
  const result = await verify(await bundle(await document()));
  assert.equal(result.kind, 'authenticated-release-metadata');
  assert.deepEqual({ ...result.descriptor }, {
    apkName: `${build.apkSha256}.apk`, apkSha256: build.apkSha256, apkSize: build.apkSize,
    databaseCompatibility: build.databaseCompatibility, launchComponent: build.launchComponent,
    minSdk: 26, packageId: PACKAGE_ID, releaseTag: 'build-772-successor',
    schema: 'io.panelassistant.android.install.v1', signerCertificateSha256: SIGNER,
    supportedAbis: ['arm64-v8a', 'armeabi-v7a'], versionCode: 772, versionName: '0.9.7-rc4',
  });
  assert.ok(Object.isFrozen(result.descriptor) && Object.isFrozen(result.descriptor.supportedAbis));
});

test('a feed signed under the new schema name is read like one under the old', async () => {
  const result = await verify(await bundle(await document({ schema: 'io.panelassistant.android.buildfeed.v1' })));
  assert.equal(result.descriptor.versionCode, 772);
});

test('the tag number chooses exactly the build with that versionCode', async () => {
  const middle = await entry(771, { versionName: '0.9.7-rc3+dev.1' });
  const feed = await document({ builds: [await entry(772), middle, await entry(770)] });
  const { descriptor } = await verify(await bundle(feed, { tag: buildTag(771) }));
  assert.equal(descriptor.versionCode, 771);
  assert.equal(descriptor.apkSha256, middle.apkSha256);
  assert.equal(descriptor.versionName, '0.9.7-rc3+dev.1');
});

test('an old-app build at the same number is skipped, never chosen', async () => {
  const oldApk = encoder.encode('old app APK');
  const old = await oldApp(772, { apkSha256: await sha256(oldApk), apkSize: oldApk.length });
  old.apkPath = `apks/${old.apkSha256}.apk`;
  const current = await entry(772);
  const feed = await document({ builds: [old, current] });
  const signed = await bundle(feed);
  const { descriptor } = await verify(signed);
  assert.equal(descriptor.packageId, PACKAGE_ID);
  assert.equal(descriptor.apkSha256, current.apkSha256);
  const verified = await verifyApkBundle(signed, new Blob([apkOf(772)]),
    { expectedRcTag: signed.tag }, release.publicKey);
  assert.equal(verified.descriptor.packageId, PACKAGE_ID);
  // The bare tag names the old app's build, which this installer cannot drive.
  await refuses(verify(await bundle(feed, { tag: 'build-772' })));
  await refuses(verify(await bundle(feed, { tag: 'build-772-successor-extra' })));
  await refuses(verify(await bundle(feed, { tag: 'build-772-unknown' })));
  const mismatched = { ...descriptor, packageId: LEGACY_PACKAGE_ID };
  assert.throws(() => parseUnauthenticatedDescriptor(encoder.encode(`${canonical(mismatched)}\n`),
    { tag: 'build-772-successor', apkSha256: current.apkSha256 }));
  // An old-app descriptor under its own bare tag is refused too.
  const oldDescriptor = { ...descriptor, packageId: LEGACY_PACKAGE_ID, launchComponent: OLD_APP_LAUNCH,
    releaseTag: 'build-772' };
  assert.throws(() => parseUnauthenticatedDescriptor(encoder.encode(`${canonical(oldDescriptor)}\n`),
    { tag: 'build-772', apkSha256: current.apkSha256 }));
});

// The Python feed parser drops a build it cannot drive before its duplicate
// check, so such a build never refuses the feed it shares a number with.
test('a build this installer cannot drive is skipped before the duplicate check', async () => {
  const other = encoder.encode('other APK');
  const otherSha = await sha256(other);
  const undrivable = { apkSha256: otherSha, apkPath: `apks/${otherSha}.apk`, apkSize: other.length };
  for (const extra of [
    [await oldApp(772, undrivable), await oldApp(772, undrivable)],
    [await entry(772, { ...undrivable, launchComponent: OLD_APP_LAUNCH })],
  ]) {
    const { descriptor } = await verify(await bundle(await document({ builds: [...extra, await entry(772)] })));
    assert.equal(descriptor.packageId, PACKAGE_ID);
    assert.equal(descriptor.apkSha256, (await entry(772)).apkSha256);
  }
});

for (const [name, overrides] of [
  ['unsupported package', { packageId: 'io.github.other' }],
  ['unsupported launch component', { launchComponent: 'io.panelassistant.android/.Other' }],
  ['old-app build', { packageId: LEGACY_PACKAGE_ID, launchComponent: OLD_APP_LAUNCH }],
  ['old-app launcher under the new id', { launchComponent: OLD_APP_LAUNCH }],
]) {
  test(`an authentic ${name} does not hide a supported build`, async () => {
    const feed = await document({ builds: [await entry(773, overrides), await entry(772)] });
    const signed = await bundle(feed);
    const verified = await verifyApkBundle(signed, new Blob([apkOf(772)]),
      { expectedRcTag: buildTag(772) }, release.publicKey);
    assert.equal(verified.descriptor.versionCode, 772);
    await refuses(verify(await bundle(feed, { tag: buildTag(773) })));
    await refuses(verify(await bundle(feed, { tag: 'build-773' })));
    signed.feedSignature = new Uint8Array(256);
    await refuses(verify(signed));
    delete signed.feedSignature;
    await refuses(verify(signed));
  });
  test(`malformed metadata on an ${name} still refuses the whole feed`, async () => {
    const feed = await document({ builds: [await entry(773, { ...overrides, commit: 'bad' }), await entry(772)] });
    await refuses(verify(await bundle(feed)));
  });
}

test('the APK bytes must be exactly the signed size and SHA-256', async () => {
  const signed = await bundle(await document());
  const verified = await verifyApkBundle(signed, new Blob([apkOf(772)]), { expectedRcTag: buildTag(772) }, release.publicKey);
  assert.equal(verified.kind, 'authenticated-apk-bytes');
  assert.equal(verified.descriptor.releaseTag, buildTag(772));
  const wrongHash = apkOf(772).slice(); wrongHash[0] ^= 1;
  await assert.rejects(verifyApkBundle(signed, new Blob([wrongHash]), { expectedRcTag: buildTag(772) }, release.publicKey),
    ApkVerificationError);
  await assert.rejects(verifyApkBundle(signed, new Blob([apkOf(772), 'x']), { expectedRcTag: buildTag(772) }, release.publicKey),
    ApkVerificationError);
});

test('refuses a tampered byte in an otherwise valid canonical feed', async () => {
  const signed = await bundle(await document());
  const at = new TextDecoder().decode(signed.feed).indexOf('"apkSize":') + '"apkSize":'.length;
  assert.match(String.fromCharCode(signed.feed[at]), /[1-8]/);
  signed.feed[at] += 1;
  await refuses(verify(signed));
});

test('refuses a feed signed by any other key', async () => {
  await refuses(verify(await bundle(await document(), { key: stranger.privateKey })));
});

for (const [name, render] of [
  ['indented', value => `${JSON.stringify(value, null, 1)}\n`],
  ['missing newline', value => canonical(value)],
  ['unsorted keys', value => `${JSON.stringify({ schema: value.schema, channel: value.channel, builds: value.builds })}\n`],
  ['escaped spelling', value => `${canonical(value).replace('"maintainer"', '"m\\u0061intainer"')}\n`],
]) {
  test(`refuses signed but non-canonical bytes: ${name}`, async () => {
    await refuses(verify(await bundle(encoder.encode(render(await document())))));
  });
}

test('refuses any byte outside ASCII, even when signed', async () => {
  const text = `${canonical(await document())}\n`.replace('0.9.7-rc4', '0.9.7-rc4é');
  await refuses(verify(await bundle(encoder.encode(text))));
});

for (const [name, change] of [
  ['unknown build field', async feed => { feed.builds[0].extra = 1; }],
  ['missing build field', async feed => { delete feed.builds[0].commit; }],
  ['unknown feed field', async feed => { feed.url = 'https://feed.example/'; }],
  ['missing feed field', async feed => { delete feed.channel; }],
  ['unknown channel', async feed => { feed.channel = 'nightly'; }],
  ['wrong schema', async feed => { feed.schema = 'io.github.maxlyth.hapaneld.buildfeed.v2'; }],
  ['wrong new schema', async feed => { feed.schema = 'io.panelassistant.android.buildfeed.v2'; }],
  ['wrong signer', async feed => { feed.builds[0].signerCertificateSha256 = 'f'.repeat(64); }],
  ['non-string package', async feed => { feed.builds[1].packageId = null; }],
  ['non-string launch component', async feed => { feed.builds[1].launchComponent = null; }],
  ['wrong ABIs', async feed => { feed.builds[0].supportedAbis = ['armeabi-v7a', 'arm64-v8a']; }],
  ['a single ABI', async feed => { feed.builds[0].supportedAbis = ['arm64-v8a']; }],
  ['apkPath not content-addressed', async feed => { feed.builds[0].apkPath = feed.builds[1].apkPath; }],
  ['apkPath outside apks/', async feed => { feed.builds[0].apkPath = `${feed.builds[0].apkSha256}.apk`; }],
  ['duplicate versionCode', async feed => { feed.builds[1] = await entry(772, { versionName: '0.9.8' }); }],
  ['bad versionName', async feed => { feed.builds[0].versionName = '-0.9.7'; }],
  ['bad commit', async feed => { feed.builds[0].commit = 'abc'; }],
  ['bad published time', async feed => { feed.builds[0].published = '2026-09-11 10:00:00'; }],
  ['bad database compatibility', async feed => { feed.builds[2].databaseCompatibility = 'hapaneld-db:v1:ha-paneld.db:0:1'; }],
  ['float size', async feed => { feed.builds[0].apkSize = 1.5; }],
  ['minSdk above bound', async feed => { feed.builds[0].minSdk = 101; }],
  // Python's feed parser would accept this range; the descriptor it becomes
  // would then be refused by the job store, so the installer refuses it first.
  ['inverted database range on the chosen build', async feed => { feed.builds[0].databaseCompatibility = 'hapaneld-db:v1:ha-paneld.db:14:11'; }],
]) {
  test(`refuses ${name}`, async () => {
    const feed = await document();
    await change(feed);
    await refuses(verify(await bundle(feed)));
  });
}

// 500 builds cannot fit in 256 KiB, so the byte bound is the one a feed meets first.
test('refuses a valid signed feed larger than 256 KiB', async () => {
  const builds = [];
  for (let code = 1; code <= 490; code++) builds.push(await entry(code));
  const oversized = serialise(await document({ builds }));
  assert.ok(oversized.length > 256 * 1024);
  await refuses(verify(await bundle(oversized, { tag: buildTag(1) })));
});

test('refuses a tag whose number no build carries', async () => {
  await refuses(verify(await bundle(await document(), { tag: buildTag(773) })));
  await refuses(verify(await bundle(await document({ builds: [] }))));
});

test('a feed bundle needs its build tag named, and a build tag accepts only a feed', async () => {
  const signed = await bundle(await document());
  await refuses(verifyReleaseBundle(signed, {}, release.publicKey));
  await refuses(verify(signed, buildTag(771)));
  await refuses(verify({ ...signed, tag: buildTag(771) }, buildTag(772)));
  await refuses(verify({ ...signed, extra: new Uint8Array(1) }));
  await refuses(verify({ ...signed, feedSignature: signed.feedSignature.slice(1) }));
  await refuses(verify(signed, 'build-2147483648-successor'));
  // GitHub signed files never satisfy a build tag.
  await refuses(verify({ tag: buildTag(772), checksum: new Uint8Array(1), checksumSignature: new Uint8Array(256),
    descriptor: new Uint8Array(1), descriptorSignature: new Uint8Array(256) }, buildTag(772)));
  // Only a genuine public verification key replaces the embedded one.
  await refuses(verifyReleaseBundle(signed, { expectedRcTag: buildTag(772) }, release.privateKey));
});

test('the descriptor rule binds a build tag to its versionCode and content-addressed name', async () => {
  const { descriptor } = await verify(await bundle(await document()));
  const encode = value => encoder.encode(`${canonical(value)}\n`);
  const expected = { tag: buildTag(772), apkSha256: descriptor.apkSha256 };
  assert.deepEqual(parseUnauthenticatedDescriptor(encode(descriptor), expected), descriptor);
  for (const change of [{ versionCode: 771 }, { apkName: `panel-assistant-build-772-manual-setup-required.apk` },
    { versionName: '0.9.7 rc4' }, { releaseTag: buildTag(771) }, { packageId: LEGACY_PACKAGE_ID }]) {
    assert.throws(() => parseUnauthenticatedDescriptor(encode({ ...descriptor, ...change }), expected));
  }
  assert.throws(() => parseUnauthenticatedDescriptor(encode(descriptor), { ...expected, tag: buildTag(771) }));
  assert.throws(() => parseUnauthenticatedDescriptor(encode(descriptor), { ...expected, tag: 'build-772' }));
});

test('the installer window accepts a build tag and exactly the feed bundle shape', async () => {
  const hash = rc => `#${new URLSearchParams({ ha_origin: 'https://ha.example', nonce: 'a'.repeat(32), rc })}`;
  assert.equal(handoffOptions(hash('build-772')).expectedRcTag, 'build-772');
  assert.equal(handoffOptions(hash(buildTag(772))).expectedRcTag, buildTag(772));
  assert.equal(handoffOptions(hash('v1.2.3')).expectedRcTag, 'v1.2.3');
  for (const rc of ['build-0', 'build-0772', 'build-2147483648', 'build-772 ', 'v1.2.3-rc01']) {
    assert.throws(() => handoffOptions(hash(rc)), { message: 'handoff_invalid' });
  }
  const signed = await bundle(await document());
  const deliver = async (message, key = release.publicKey) => {
    const listeners = new Set();
    const replies = [];
    const opener = { postMessage: data => replies.push(data.type) };
    const handoff = receiveReleaseHandoff({ options: handoffOptions(hash(buildTag(772))), verificationKey: key,
      windowObject: { opener, isSecureContext: true, location: { hash: '' },
        addEventListener: (_, fn) => listeners.add(fn), removeEventListener: (_, fn) => listeners.delete(fn) } });
    for (const listener of listeners) {
      await listener({ source: opener, origin: 'https://ha.example',
        data: { type: 'ha-paneld/usb-bundle', nonce: 'a'.repeat(32), ...message } });
    }
    return { handoff, replies };
  };
  const accepted = await deliver({ bundle: signed, apk: new Blob([apkOf(772)]) });
  const { release: verified } = await accepted.handoff.completion;
  assert.equal(verified.kind, 'authenticated-apk-bytes');
  assert.equal(verified.descriptor.releaseTag, buildTag(772));
  assert.deepEqual(accepted.replies, ['ha-paneld/usb-ready', 'ha-paneld/usb-verified']);
  accepted.handoff.cancel();
  const github = { tag: buildTag(772), checksum: new Uint8Array(1), checksumSignature: new Uint8Array(256),
    descriptor: new Uint8Array(1), descriptorSignature: new Uint8Array(256) };
  for (const message of [{ bundle: github, apk: new Blob([apkOf(772)]) },
    { bundle: { ...signed, extra: new Uint8Array(1) }, apk: new Blob([apkOf(772)]) }]) {
    const refused = await deliver(message);
    await assert.rejects(refused.handoff.completion, { message: 'handoff_invalid' });
    assert.deepEqual(refused.replies, ['ha-paneld/usb-ready', 'ha-paneld/usb-error']);
  }
});

// Cross-implementation: signed by Python with a throwaway key, reused by the
// Python tests. Only the test public key is committed.
const fixture = name => readFileSync(new URL(`./fixtures/${name}`, import.meta.url));
const golden = {
  feed: new Uint8Array(fixture('build-feed-golden.json')),
  signature: new Uint8Array(Buffer.from(fixture('build-feed-golden.json.sig.b64').toString('ascii').trim(), 'base64')),
  apk: new Uint8Array(fixture('build-feed-golden-772.apk.txt')),
};
const goldenKey = await crypto.subtle.importKey('spki',
  Buffer.from(fixture('build-feed-golden.pub.pem').toString('ascii').replace(/-----[A-Z ]+-----|\s/g, ''), 'base64'),
  { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify']);
const goldenBundle = (tag, feed = golden.feed) => ({ tag, feed: feed.slice(), feedSignature: golden.signature.slice() });

test('the Python-signed golden feed still authenticates, and offers none of its old-app builds', async () => {
  const text = new TextDecoder().decode(golden.feed);
  assert.equal(`${canonical(JSON.parse(text))}\n`, text, 'the test serialiser matches Python byte for byte');
  assert.equal(await crypto.subtle.verify(ALGORITHM, goldenKey, golden.signature, golden.feed), true);
  const builds = JSON.parse(text).builds;
  assert.ok(builds.length && builds.every(build => build.packageId === LEGACY_PACKAGE_ID));
  for (const { versionCode } of builds) {
    for (const tag of [`build-${versionCode}`, buildTag(versionCode)]) {
      await refuses(verifyReleaseBundle(goldenBundle(tag), { expectedRcTag: tag }, goldenKey));
    }
  }
  await assert.rejects(verifyApkBundle(goldenBundle('build-772'), new Blob([golden.apk]),
    { expectedRcTag: 'build-772' }, goldenKey));
});

test('the golden feed\'s old-app builds are skipped, not malformed: one of this app beside them is offered', async () => {
  const feed = JSON.parse(new TextDecoder().decode(golden.feed));
  feed.builds.push(await entry(772));
  const { descriptor } = await verify(await bundle(feed));
  assert.equal(descriptor.packageId, PACKAGE_ID);
  assert.equal(descriptor.versionCode, 772);
});

test('refuses a one-byte mutation of the golden feed', async () => {
  const mutated = golden.feed.slice();
  const at = new TextDecoder().decode(mutated).indexOf('"minSdk":') + '"minSdk":'.length;
  mutated[at] += 1;
  await refuses(verifyReleaseBundle(goldenBundle('build-772', mutated), { expectedRcTag: 'build-772' }, goldenKey));
});
