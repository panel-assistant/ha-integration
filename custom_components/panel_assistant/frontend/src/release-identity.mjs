// The one definition of every release identity the installer accepts. A GitHub
// release is a v-tag; a signed feed build is build-<versionCode> for legacy or
// build-<versionCode>-successor for the new app, never a GitHub tag. Every check imports
// these rules instead of repeating a pattern.
import { ACCEPTED_PACKAGE_IDS, LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID } from './app-identity.mjs';

export const MAX_TAG_LENGTH = 64;
// The signed build feed document, in exact bytes.
export const MAX_FEED_BYTES = 256 * 1024;
const MAX_VERSION_CODE = 2147483647;
const STABLE = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/;
const RC = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc[1-9][0-9]*$/;
const BUILD = /^build-([1-9][0-9]{0,9})(-successor)?$/;
const VERSION_NAME = /^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$/;
const matches = (pattern, value) => typeof value === 'string' && value.length <= MAX_TAG_LENGTH &&
  pattern.exec(value)?.[0] === value;

export const isStableTag = tag => matches(STABLE, tag);
export const isRcTag = tag => matches(RC, tag);
export const isGithubTag = tag => isStableTag(tag) || isRcTag(tag);
/** The versionCode a build tag names, or null when the value is not a build tag. */
export function buildTagVersionCode(tag) {
  if (!matches(BUILD, tag)) return null;
  const code = Number(BUILD.exec(tag)[1]);
  return code <= MAX_VERSION_CODE ? code : null;
}
export const isBuildTag = tag => buildTagVersionCode(tag) !== null;
/** The package identity bound to a feed build tag. */
export const buildTagPackageId = tag => !isBuildTag(tag) ? null :
  tag.endsWith('-successor') ? SUCCESSOR_PACKAGE_ID : LEGACY_PACKAGE_ID;
/** A feed build's versionName: free-form within this pattern, unlike a GitHub tag. */
export const isBuildVersionName = value => matches(VERSION_NAME, value);
/** How Home Assistant names a feed build, and how the release list carries it. */
export const buildLabel = (versionName, versionCode) => `${versionName} build ${versionCode}`;
// One stem per application id, the same rule the integration applies. The
// legacy stem is frozen: shipped panel updaters resolve a release's first
// `.apk` asset, so the app that keeps the old id keeps the name they see.
const APK_SUFFIX = '-manual-setup-required.apk';
const apkStem = packageId => (packageId === LEGACY_PACKAGE_ID ? 'ha-paneld' : 'panel-assistant');

/** The exact asset name a GitHub release publishes one identity's APK under. */
export const githubApkName = (tag, packageId = LEGACY_PACKAGE_ID) =>
  `${apkStem(packageId)}-${tag}${APK_SUFFIX}`;
// From app 0.9.10 the new app's APK carries a `.bin` suffix, so a release has
// no `.apk` asset for an updater or installer that cannot drive a build whose
// classes moved package. The old app has no such build.
const UNLISTED_SUFFIX = '.bin';
/** Every APK asset name a release of this tag may carry, legacy first. */
export const githubApkNames = tag => ACCEPTED_PACKAGE_IDS.flatMap(id =>
  id === SUCCESSOR_PACKAGE_ID ? [githubApkName(tag, id), githubApkName(tag, id) + UNLISTED_SUFFIX]
    : [githubApkName(tag, id)]);
/** The tag a GitHub release APK asset name carries, or null. */
export function githubApkNameTag(name) {
  if (typeof name !== 'string' || !name.endsWith(APK_SUFFIX)) return null;
  for (const packageId of ACCEPTED_PACKAGE_IDS) {
    const prefix = `${apkStem(packageId)}-`;
    if (!name.startsWith(prefix)) continue;
    const tag = name.slice(prefix.length, -APK_SUFFIX.length);
    if (isGithubTag(tag)) return tag;
  }
  return null;
}
// The install descriptor's asset name does not follow the application id: it
// is frozen on the legacy spelling, like the descriptor schema identifier.
/** The exact asset name a GitHub release publishes its install descriptor under. */
export const githubDescriptorName = tag => `ha-paneld-${tag}-install.json`;

/** Whether a v1 install descriptor carries the identity its tag requires. A
 * GitHub release names the APK and version after its tag; a feed build names the
 * APK by its content and its tag number is exactly its versionCode.
 */
export function descriptorIdentityValid(descriptor, tag, apkSha256) {
  if (isGithubTag(tag)) {
    return descriptor.releaseTag === tag && descriptor.versionName === tag.slice(1) &&
      githubApkNames(tag).includes(descriptor.apkName);
  }
  const code = buildTagVersionCode(tag);
  return code !== null && descriptor.releaseTag === tag && descriptor.versionCode === code &&
    descriptor.packageId === buildTagPackageId(tag) &&
    isBuildVersionName(descriptor.versionName) && descriptor.apkName === `${apkSha256}.apk`;
}
