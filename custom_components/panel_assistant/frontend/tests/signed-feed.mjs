// Signed build feeds for tests, made with a throwaway key generated per run.
// The committed golden feed holds only old-app builds, which this installer
// skips, so a test that needs a build it can drive signs one here. build-feed's
// golden test proves `canonical` produces the Python bytes.
import { LAUNCH_COMPONENTS, PACKAGE_ID } from '../src/app-identity.mjs';

export const ALGORITHM = { name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048,
  publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' };
export const release = await crypto.subtle.generateKey(ALGORITHM, false, ['sign', 'verify']);
export const SIGNER = 'ac6193307fb0b70113aae205d7549406f96e063bc5491b67b1d5694a34b0e339';
const encoder = new TextEncoder();
const hex = bytes => Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, '0')).join('');
export const sha256 = async bytes => hex(await crypto.subtle.digest('SHA-256', bytes));
// Python's json.dumps(sort_keys=True, separators=(",", ":")).
export const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]`
  : value !== null && typeof value === 'object'
    ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
    : JSON.stringify(value);
export const apkOf = code => encoder.encode(`ha-paneld test build ${code}\n`);
/** The tag a feed build of this app carries. */
export const buildTag = code => `build-${code}-successor`;

/** A feed build of this app, with its new-classes launcher. */
export async function entry(code, overrides = {}) {
  const sha = await sha256(apkOf(code));
  return { apkPath: `apks/${sha}.apk`, apkSha256: sha, apkSize: apkOf(code).length,
    commit: '0123456789abcdef0123456789abcdef01234567',
    databaseCompatibility: 'hapaneld-db:v1:ha-paneld.db:11:14',
    launchComponent: LAUNCH_COMPONENTS[PACKAGE_ID][1], minSdk: 26,
    packageId: PACKAGE_ID, published: '2026-09-11T10:00:00Z',
    signerCertificateSha256: SIGNER, supportedAbis: ['arm64-v8a', 'armeabi-v7a'],
    versionCode: code, versionName: '0.9.7-rc4', ...overrides };
}
export async function document({ builds, ...overrides } = {}) {
  return { builds: builds ?? [await entry(772), await entry(771), await entry(770)],
    channel: 'maintainer', schema: 'io.github.maxlyth.hapaneld.buildfeed.v1', ...overrides };
}
export const serialise = value => encoder.encode(`${canonical(value)}\n`);
export async function sign(feed, key = release.privateKey) {
  return new Uint8Array(await crypto.subtle.sign('RSASSA-PKCS1-v1_5', key, feed));
}
export async function bundle(feed, { tag = buildTag(772), key } = {}) {
  const bytes = feed instanceof Uint8Array ? feed : serialise(feed);
  return { tag, feed: bytes, feedSignature: await sign(bytes, key) };
}
