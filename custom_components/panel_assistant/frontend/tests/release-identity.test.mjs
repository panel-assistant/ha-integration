import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID } from '../src/app-identity.mjs';
import { buildTagVersionCode, descriptorIdentityValid, githubApkName, githubApkNameTag,
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
    assert.equal(isRcTag(tag), kind === 'rc', tag);
    assert.equal(isGithubTag(tag), kind === 'stable' || kind === 'rc', tag);
    assert.equal(isBuildTag(tag), kind === 'build', tag);
  }
  for (const { tag, kind, versionCode } of corpus.tags) {
    assert.equal(buildTagVersionCode(tag), kind === 'build' ? versionCode : null, tag);
  }
});

test('the shared corpus names both identities APK assets, and reads either back', () => {
  for (const { tag, legacy, successor, successorUnlisted } of corpus.apkNames) {
    assert.equal(githubApkName(tag, LEGACY_PACKAGE_ID), legacy);
    assert.equal(githubApkName(tag, SUCCESSOR_PACKAGE_ID), successor);
    // From app 0.9.10 the new app's APK has no `.apk` suffix; only this
    // installer and the integration know its name.
    assert.deepEqual(githubApkNames(tag), [legacy, successor, successorUnlisted]);
    // The successor's own asset name used to be unreadable here while the
    // integration already resolved it.
    assert.equal(githubApkNameTag(legacy), tag);
    assert.equal(githubApkNameTag(successor), tag);
    const version = tag.slice(1);
    for (const apkName of [legacy, successor, successorUnlisted]) {
      assert.equal(descriptorIdentityValid({ releaseTag: tag, versionName: version, apkName }, tag), true);
    }
    assert.equal(descriptorIdentityValid(
      { releaseTag: tag, versionName: version, apkName: 'ha-paneld.apk' }, tag), false);
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
