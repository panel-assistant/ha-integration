// The one definition of every release identity the installer reads. A GitHub
// release is a v-tag; a signed feed build is build-<versionCode>-successor for
// this app, never a GitHub tag. The bare build-<versionCode> names an old-app
// build and is still read, so such a build is refused by its id rather than
// misread; both tag spellings are frozen. Every check imports these rules
// instead of repeating a pattern.
import { LEGACY_PACKAGE_ID, PACKAGE_ID } from './app-identity.mjs';

export const MAX_TAG_LENGTH = 64;
// The signed build feed document, in exact bytes.
export const MAX_FEED_BYTES = 256 * 1024;
const MAX_VERSION_CODE = 2147483647;
const STABLE = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/;
const RC = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-(?:rc|preview)[1-9][0-9]*$/;
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
  tag.endsWith('-successor') ? PACKAGE_ID : LEGACY_PACKAGE_ID;
/** A feed build's versionName: free-form within this pattern, unlike a GitHub tag. */
export const isBuildVersionName = value => matches(VERSION_NAME, value);
// One stem per application id, the same rule the integration applies. The
// old app's `ha-paneld` stem is frozen in the asset names of old releases; no
// release this installer can drive is named by it.
const APK_SUFFIX = '-manual-setup-required.apk';
const apkStem = packageId => (packageId === LEGACY_PACKAGE_ID ? 'ha-paneld' : 'panel-assistant');

/** The exact asset name a GitHub release publishes one identity's APK under. */
export const githubApkName = (tag, packageId = PACKAGE_ID) =>
  `${apkStem(packageId)}-${tag}${APK_SUFFIX}`;
// From app 0.9.10 the app's APK carries a `.bin` suffix, so a release has no
// `.apk` asset for an updater or installer that cannot drive a build whose
// classes moved package.
const UNLISTED_SUFFIX = '.bin';
/** Every APK asset name a release of this tag may carry for this app. */
export const githubApkNames = tag => [githubApkName(tag), githubApkName(tag) + UNLISTED_SUFFIX];
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
