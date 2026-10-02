import test from 'node:test';
import assert from 'node:assert/strict';
import { ReleaseVerificationError, verifyReleaseBundle } from '../src/release-verifier.mjs';
import { LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID, launchComponentFor } from '../src/app-identity.mjs';
import { githubApkName } from '../src/release-identity.mjs';

// A signed GitHub release bundle, end to end: checksum line, install descriptor
// and both detached signatures, for either application id.
const ALGORITHM = { name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048,
  publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' };
const release = await crypto.subtle.generateKey(ALGORITHM, false, ['sign', 'verify']);
const stranger = await crypto.subtle.generateKey(ALGORITHM, false, ['sign', 'verify']);
const SIGNER = 'ac6193307fb0b70113aae205d7549406f96e063bc5491b67b1d5694a34b0e339';
const HASH = 'a'.repeat(64);
const OTHER_HASH = 'b'.repeat(64);
const encoder = new TextEncoder();

// The release workflow's canonical descriptor bytes: sorted keys, no spaces, newline.
function descriptorBytes(fields) {
  return encoder.encode(`${JSON.stringify(Object.fromEntries(
    Object.keys(fields).sort().map(key => [key, fields[key]])))}\n`);
}
function descriptor(tag, packageId, overrides = {}) {
  return {
    apkName: githubApkName(tag, packageId), apkSha256: HASH, apkSize: 4096,
    databaseCompatibility: 'hapaneld-db:v1:ha-paneld.db:11:14',
    launchComponent: launchComponentFor(packageId), minSdk: 26, packageId,
    releaseTag: tag, schema: 'io.github.maxlyth.hapaneld.install.v1',
    signerCertificateSha256: SIGNER, supportedAbis: ['arm64-v8a', 'armeabi-v7a'],
    versionCode: 880, versionName: tag.slice(1), ...overrides,
  };
}
const sign = async (bytes, key = release.privateKey) =>
  new Uint8Array(await crypto.subtle.sign('RSASSA-PKCS1-v1_5', key, bytes));
async function bundle(tag, packageId, { checksumName, checksumHash = HASH, checksumLine, fields = {},
  checksumKey, descriptorKey } = {}) {
  const checksum = encoder.encode(checksumLine ??
    `${checksumHash}  ${checksumName ?? githubApkName(tag, packageId)}\n`);
  const body = descriptorBytes(descriptor(tag, packageId, fields));
  return { tag, checksum, checksumSignature: await sign(checksum, checksumKey),
    descriptor: body, descriptorSignature: await sign(body, descriptorKey) };
}
const verify = (value, expectedRcTag) =>
  verifyReleaseBundle(value, { expectedRcTag }, release.publicKey);
const refuses = promise => assert.rejects(promise, ReleaseVerificationError);

const RELEASES = [['v0.9.8', null], ['v0.9.8', 'v0.9.8'], ['v0.9.8-rc2', 'v0.9.8-rc2']];
const IDENTITIES = [['legacy', LEGACY_PACKAGE_ID], ['successor', SUCCESSOR_PACKAGE_ID]];

for (const [tag, expectedRcTag] of RELEASES) {
  for (const [label, packageId] of IDENTITIES) {
    test(`a signed ${label} ${tag} bundle authenticates under its own APK name`, async () => {
      const result = await verify(await bundle(tag, packageId), expectedRcTag);
      assert.equal(result.kind, 'authenticated-release-metadata');
      assert.equal(result.descriptor.packageId, packageId);
      assert.equal(result.descriptor.apkName, githubApkName(tag, packageId));
      assert.equal(result.descriptor.apkSha256, HASH);
      assert.equal(result.descriptor.releaseTag, tag);
    });
  }
}

test('the checksum must name the exact APK the signed descriptor names', async () => {
  const tag = 'v0.9.8-rc2';
  // Each identity's checksum paired with the other identity's descriptor: both
  // halves are validly signed and agree on the hash, only the names differ.
  await refuses(verify(await bundle(tag, SUCCESSOR_PACKAGE_ID,
    { checksumName: githubApkName(tag, LEGACY_PACKAGE_ID) }), tag));
  await refuses(verify(await bundle(tag, LEGACY_PACKAGE_ID,
    { checksumName: githubApkName(tag, SUCCESSOR_PACKAGE_ID) }), tag));
  await refuses(verify(await bundle(tag, SUCCESSOR_PACKAGE_ID,
    { checksumName: githubApkName('v0.9.8-rc1', SUCCESSOR_PACKAGE_ID) }), tag));
  await refuses(verify(await bundle(tag, SUCCESSOR_PACKAGE_ID,
    { checksumName: 'panel-assistant.apk' }), tag));
});

test('the checksum line is exactly one sha256sum record, nothing more', async () => {
  const name = githubApkName('v0.9.8', SUCCESSOR_PACKAGE_ID);
  for (const checksumLine of [`${HASH}x  ${name}\n`, `${HASH}  x${name}\n`, `${HASH} *${name}\n`,
    `${HASH}  ${name}`, `${HASH}  ${name}\n\n`]) {
    await refuses(verify(await bundle('v0.9.8', SUCCESSOR_PACKAGE_ID, { checksumLine }), null));
  }
});

test('the checksum hash must be the hash the signed descriptor names', async () => {
  for (const [, packageId] of IDENTITIES) {
    await refuses(verify(await bundle('v0.9.8', packageId, { checksumHash: OTHER_HASH }), null));
  }
});

test('the descriptor must name the tag the bundle carries', async () => {
  for (const [, packageId] of IDENTITIES) {
    await refuses(verify(await bundle('v0.9.8-rc2', packageId,
      { fields: { releaseTag: 'v0.9.8-rc1' } }), 'v0.9.8-rc2'));
  }
});

test('an APK name carrying another tag is refused even when both signed halves agree on it', async () => {
  for (const [, packageId] of IDENTITIES) {
    const name = githubApkName('v0.9.8-rc1', packageId);
    await refuses(verify(await bundle('v0.9.8-rc2', packageId,
      { fields: { apkName: name }, checksumName: name }), 'v0.9.8-rc2'));
  }
});

test('either half signed by any other key is refused for both identities', async () => {
  for (const [, packageId] of IDENTITIES) {
    await refuses(verify(await bundle('v0.9.8', packageId, { checksumKey: stranger.privateKey }), null));
    await refuses(verify(await bundle('v0.9.8', packageId, { descriptorKey: stranger.privateKey }), null));
  }
});

test('byte verifier default alone never admits an unsolicited RC', async () => {
  await refuses(verify(await bundle('v0.9.8-rc2', LEGACY_PACKAGE_ID), null));
});
