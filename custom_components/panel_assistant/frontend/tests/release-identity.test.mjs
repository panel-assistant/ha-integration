import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { LEGACY_PACKAGE_ID, PACKAGE_ID } from '../src/app-identity.mjs';
import { buildTagPackageId, buildTagVersionCode, descriptorIdentityValid, githubApkName, githubApkNameTag,
  githubApkNames, githubDescriptorName, isBuildTag, isBuildVersionName, isGithubTag,
  isRcTag, isStableTag } from '../src/release-identity.mjs';

// The same file the integration's own suite reads, so a rule that moves on one
// side alone fails on the other.
const corpus = JSON.parse(readFileSync(
  new URL('../../../../tests/fixtures/release-identity-corpus.json', import.meta.url), 'utf8'));
const androidProducer = JSON.parse(readFileSync(
  new URL('../../../../tests/fixtures/android_producer_v1.json', import.meta.url), 'utf8'));

test('the Android descriptor producer and browser agree on every release tag', () => {
  const verdicts = new Map(androidProducer.releaseTagVerdicts.map(({ tag, accepted }) => [tag, accepted]));
  assert.deepEqual(new Set(verdicts.keys()), new Set(corpus.tags.map(({ tag }) => tag)));
  for (const { tag } of corpus.tags) assert.equal(verdicts.get(tag), isGithubTag(tag), tag);
});

test('the shared corpus classifies every release identity', () => {
  for (const { tag, kind } of corpus.tags) {
    assert.equal(isStableTag(tag), kind === 'stable', tag);
    assert.equal(isRcTag(tag), kind === 'prerelease', tag);
    assert.equal(isGithubTag(tag), kind === 'stable' || kind === 'prerelease', tag);
    assert.equal(isBuildTag(tag), kind === 'build', tag);
  }
  for (const { tag, kind, versionCode } of corpus.tags) {
    assert.equal(buildTagVersionCode(tag), kind === 'build' ? versionCode : null, tag);
  }
});

test('a bare build tag names an old-app build, and -successor this app', () => {
  for (const { tag, kind, versionCode } of corpus.tags.filter(({ kind }) => kind === 'build')) {
    assert.equal(buildTagPackageId(tag), LEGACY_PACKAGE_ID, tag);
    assert.equal(buildTagPackageId(`${tag}-successor`), PACKAGE_ID, tag);
    assert.equal(buildTagVersionCode(`${tag}-successor`), versionCode, tag);
  }
  for (const tag of ['v0.9.7', 'build-0-successor', 'build-772-successor-successor', '']) {
    assert.equal(buildTagPackageId(tag), null, tag);
  }
});

test("the shared corpus names this app's APK assets, and the old app's only as a frozen name", () => {
  for (const { tag, legacy, successor, successorUnlisted } of corpus.apkNames) {
    // The old app's stem is frozen in old releases' asset names.
    assert.equal(githubApkName(tag, LEGACY_PACKAGE_ID), legacy);
    assert.equal(githubApkName(tag, PACKAGE_ID), successor);
    assert.equal(githubApkName(tag), successor);
    // From app 0.9.10 the app's APK has no `.apk` suffix; only this installer
    // and the integration know its name.
    assert.deepEqual(githubApkNames(tag), [successor, successorUnlisted]);
    assert.equal(githubApkNameTag(successor), tag);
    // An old-app asset names no release this installer can drive.
    assert.equal(githubApkNameTag(legacy), null);
    const version = tag.slice(1);
    for (const apkName of [successor, successorUnlisted]) {
      assert.equal(descriptorIdentityValid({ releaseTag: tag, versionName: version, apkName }, tag), true);
    }
    for (const apkName of [legacy, 'ha-paneld.apk']) {
      assert.equal(descriptorIdentityValid({ releaseTag: tag, versionName: version, apkName }, tag), false);
    }
  }
  for (const name of ['ha-paneld-manual-setup-required.apk', 'panel-assistant-v1.2-manual-setup-required.apk',
    'other-v1.2.3-manual-setup-required.apk', 'ha-paneld-v1.2.3.apk', '', undefined]) {
    assert.equal(githubApkNameTag(name), null, String(name));
  }
});

test('the shared corpus names the install descriptor', () => {
  for (const { tag, name } of corpus.descriptorNames) {
    assert.equal(githubDescriptorName(tag), name);
  }
});

test("the shared corpus bounds a feed build's own version name", () => {
  for (const { value, accepted } of corpus.versionNames) {
    assert.equal(isBuildVersionName(value), accepted, value);
  }
});
