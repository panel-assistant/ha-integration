import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createInstallController} from '../src/install-controller.mjs';

const target = {model: 'Test panel', serial: 'serial', primaryAbi: 'arm64-v8a', androidSdk: 33,
  rootMode: 'rootless', usbVendorId: 1, usbProductId: 2, usbSerial: 'usb'};

async function fixture() {
  const artifact = {apkSha256: 'a'.repeat(64)};
  let receipt = {id: 'b'.repeat(32), phase: 'healthy', target, artifact};
  let release = {kind: 'authenticated-apk-bytes', descriptor: artifact};
  let current = true, grants = 0, lockAvailable = true;
  let grant = async () => {grants++; return {permissionsVerified: true};};
  let loads = 0, later = null;
  const controller = createInstallController({store: {load: async () => (++loads > 2 && later ? {...receipt, ...later} : receipt)},
    ports: {authenticate: async () => release, inspect: async () => ({installed: true}),
      removeSetAside: async () => 'absent',
      commissionPermissions: async (...args) => grant(...args)},
    ensureCurrent: () => {if (!current) throw new Error('session_closed');},
    locks: {request: async (name, options, callback) => {
      assert.match(name, /^ha-paneld-usb:[a-f0-9]{64}$/);
      assert.deepEqual(options, {mode: 'exclusive', ifAvailable: true});
      return callback(lockAvailable ? {} : null);
    }}});
  await controller.preview(target);
  return {controller, get grants() {return grants;}, setReceipt: value => {receipt = {...receipt, ...value};},
    setRelease: value => {release = value;}, stop: () => {current = false;},
    denyLock: () => {lockAvailable = false;}, setGrant: value => {grant = value;},
    // Applies from the grant's own read onwards: preview and install have read.
    changeBeforeGrant: value => {later = value;}};
}

test('the Install press is the only consent, and grants only from a healthy job', async () => {
  const f = await fixture();
  for (const confirmation of [undefined, false, 1, 'yes']) {
    await assert.rejects(f.controller.install(confirmation), /confirmation_required/);
  }
  assert.equal(f.grants, 0);
  assert.deepEqual((await f.controller.install(true)).permissions, {permissionsVerified: true});
  const g = await fixture();
  g.changeBeforeGrant({phase: 'installed'});
  await assert.rejects(g.controller.install(true), /transaction_invalid/);
  assert.equal(g.grants, 0);
});

test('fresh authentication, job identity and session guard fail before grants', async () => {
  for (const change of [f => f.setReceipt({id: 'c'.repeat(32)}), f => f.stop(), f => f.denyLock(),
    f => f.setRelease({kind: 'authenticated-apk-bytes', descriptor: {apkSha256: 'f'.repeat(64)}})]) {
    const f = await fixture(); change(f);
    await assert.rejects(f.controller.install(true));
    assert.equal(f.grants, 0);
  }
});

test('the permission grant holds the controller against preview or a second install', async () => {
  const f = await fixture();
  let finish, entered;
  const running = new Promise(resolve => {entered = resolve;});
  f.setGrant(() => new Promise(resolve => {finish = resolve; entered();}));
  const first = f.controller.install(true);
  await running;
  await assert.rejects(f.controller.install(true), /transaction_busy/);
  await assert.rejects(f.controller.preview(target), /transaction_busy/);
  finish({permissionsVerified: true});
  await first;
});

function finishedJobFixture({installed = true, descriptor} = {}) {
  const artifact = {apkSha256: 'a'.repeat(64)};
  let stored = {id: 'b'.repeat(32), revision: 6, phase: 'healthy', target, artifact};
  const retired = [], inspected = [];
  const release = {kind: 'authenticated-apk-bytes', descriptor: descriptor ?? artifact};
  const controller = createInstallController({
    store: {load: async () => stored,
      retire: async (key, revision) => {retired.push(revision); stored = null;},
      discard: async () => {throw new Error('a discard would be wrong here');},
      create: async () => (stored = {id: 'c'.repeat(32), revision: 0, phase: 'healthy', target,
        artifact: release.descriptor})},
    ports: {authenticate: async () => release,
      inspect: async receipt => {inspected.push(receipt.phase); return {installed};},
      removeSetAside: async () => 'absent',
      commissionPermissions: async () => ({permissionsVerified: true})},
    locks: {request: async (name, options, callback) => callback({})}});
  return {controller, retired, inspected};
}

test('a finished job resumes only while the panel still runs its app', async () => {
  const same = finishedJobFixture({installed: true});
  const preview = await same.controller.preview(target);
  assert.equal(preview.receipt.phase, 'healthy', 'permissions and setup can still follow');
  assert.deepEqual(same.retired, []);
});

test('a finished job whose app was replaced is retired, not a lock-out', async () => {
  const changed = finishedJobFixture({installed: false});
  const preview = await changed.controller.preview(target);
  assert.equal(preview.receipt, null, 'the next install starts fresh');
  assert.deepEqual(changed.retired, [], 'the preview keeps the record');
  assert.deepEqual(changed.inspected, ['healthy', 'installed', 'prepared'],
    'then checked for an exact install, then inspected as a new install');
  await changed.controller.install(true);
  assert.deepEqual(changed.retired, [6], 'the Install press retires it at the exact revision inspected');
});

test('a finished job for another release is retired without asking the panel about it', async () => {
  const other = finishedJobFixture({installed: false, descriptor: {apkSha256: 'f'.repeat(64)}});
  const preview = await other.controller.preview(target);
  assert.equal(preview.receipt, null);
  assert.deepEqual(other.inspected, ['installed', 'prepared']);
  await other.controller.install(true);
  assert.deepEqual(other.retired, [6]);
});

function freshFixture({installed}) {
  const artifact = {apkSha256: 'a'.repeat(64)};
  const created = [], inspected = [];
  let stored = null;
  const release = {kind: 'authenticated-apk-bytes', descriptor: artifact};
  const receiptFor = phase => ({id: 'c'.repeat(32), revision: 0, phase, target, artifact});
  const controller = createInstallController({
    store: {load: async () => stored,
      create: async () => {created.push('prepared'); stored = receiptFor('prepared'); return stored;},
      adopt: async () => {created.push('installed'); stored = receiptFor('installed'); return stored;},
      advance: async (key, revision, phase) => (stored = {...stored, revision: revision + 1, phase})},
    ports: {authenticate: async () => release,
      inspect: async receipt => {inspected.push(receipt.phase);
        return {target, clean: !installed, staged: false, installed, healthy: installed};},
      launch: async () => {}, removeSetAside: async () => 'absent',
      commissionPermissions: async () => ({permissionsVerified: true})},
    locks: {request: async (name, options, callback) => callback({})}});
  return {controller, created, inspected};
}

test('a panel already running exactly this build is adopted, not refused', async () => {
  const f = freshFixture({installed: true});
  const preview = await f.controller.preview(target);
  assert.equal(preview.adopt, true);
  assert.deepEqual(f.inspected, ['installed'], 'no clean check that would refuse it');
  const {receipt} = await f.controller.install(true);
  assert.deepEqual(f.created, ['installed'], 'the job starts at installed');
  assert.equal(receipt.phase, 'healthy', 'then launch and health still run');
});

test('a clean panel still gets a fresh install from the start', async () => {
  const f = freshFixture({installed: false});
  const preview = await f.controller.preview(target);
  assert.equal(preview.adopt, false);
  assert.deepEqual(f.inspected, ['installed', 'prepared']);
  await assert.rejects(f.controller.install(false), /confirmation_required/);
});

test('an already installed build is announced, not failed', () => {
  const source = readFileSync(new URL('../src/install-main.mjs', import.meta.url), 'utf8');
  const branch = source.slice(source.indexOf('if (!receipt && preview.adopt)'));
  assert.ok(branch.length < source.length, 'the adopt branch exists');
  const block = branch.slice(0, branch.indexOf('if (receipt) await installAll()'));
  assert.match(block, /screen\.alreadyInstalledHeading/);
  assert.match(block, /screen\.alreadyInstalledBody/);
  assert.match(block, /screen\.continueSetup/);
  assert.ok(!/fail\(|quarantine\(/.test(block), 'it never routes to the error screen');
});

test('after success nothing can flash the error screen', () => {
  const source = readFileSync(new URL('../src/install-main.mjs', import.meta.url), 'utf8');
  const quarantine = source.slice(source.indexOf('function quarantine('), source.indexOf('function ensureCurrent('));
  const guard = quarantine.indexOf('if (finished)');
  assert.ok(guard >= 0, 'quarantine checks for a finished install');
  assert.ok(guard < quarantine.indexOf("show('error')"), 'before it can show the error screen');
  const finish = source.slice(source.indexOf('function finish('));
  assert.ok(finish.indexOf('finished = true') < finish.indexOf("show('done')"),
    'success is recorded before anything that can close the connection');
});

// A job left part-way through for one release, with the person now offered
// another: the store answers with the saved receipt, the ports with the new
// release. Nothing on the panel is touched by a discard.
function strandedJobFixture({phase = 'staged', descriptor, lockAvailable = true} = {}) {
  const artifact = {apkSha256: 'a'.repeat(64), versionName: '0.9.7-rc4', versionCode: 770,
    releaseTag: 'build-770'};
  let stored = {id: 'b'.repeat(32), revision: 3, phase, target, artifact};
  const discarded = [], inspected = [], created = [];
  const release = {kind: 'authenticated-apk-bytes', descriptor: descriptor ?? artifact};
  const state = {installed: false};
  const controller = createInstallController({
    store: {load: async () => stored,
      discard: async (key, revision) => {discarded.push(revision); stored = null;},
      retire: async () => {throw new Error('a retire would be wrong here');},
      create: async () => {created.push('prepared'); return (stored = {id: 'c'.repeat(32),
        revision: 0, phase: 'healthy', target, artifact: release.descriptor});},
      adopt: async () => {created.push('installed'); return (stored = {id: 'c'.repeat(32),
        revision: 0, phase: 'healthy', target, artifact: release.descriptor});}},
    ports: {authenticate: async () => release,
      inspect: async receipt => {inspected.push(receipt.phase);
        return {target, clean: true, staged: false, installed: state.installed,
          healthy: false};},
      inspectRecovery: async receipt => {inspected.push(`recovery:${receipt.phase}`); return {};},
      removeSetAside: async () => 'absent',
      commissionPermissions: async () => ({permissionsVerified: true})},
    locks: {request: async (name, options, callback) => callback(lockAvailable ? {} : null)}});
  return {controller, discarded, inspected, created,
    set installed(value) {state.installed = value;}};
}

test('an unfinished job for a version the person moved on from is set aside, not a dead end', async () => {
  for (const phase of ['prepared', 'staging', 'staged', 'installing', 'installed', 'launching',
    'recovery_required', 'cleanup_pending']) {
    const f = strandedJobFixture({phase, descriptor: {apkSha256: 'f'.repeat(64)}});
    const preview = await f.controller.preview(target);
    assert.equal(preview.receipt, null, `${phase}: the chosen version installs from the start`);
    assert.deepEqual(f.discarded, [], `${phase}: the preview keeps the record`);
    assert.deepEqual(preview.discarded,
      {versionName: '0.9.7-rc4', versionCode: 770, releaseTag: 'build-770'},
      `${phase}: the version set aside is named, so the person can be told`);
    assert.equal(preview.adopt, false);
    await f.controller.install(true);
    assert.deepEqual(f.discarded, [3], `${phase}: the Install press discards it at the exact revision inspected`);
    assert.deepEqual(f.created, ['prepared'], `${phase}: then starts the chosen version`);
  }
});

test('an unfinished job whose release is unchanged still resumes untouched', async () => {
  const f = strandedJobFixture({phase: 'staged'});
  const preview = await f.controller.preview(target);
  assert.equal(preview.receipt.phase, 'staged', 'the saved job carries on where it stopped');
  assert.equal(preview.discarded, null);
  assert.deepEqual(f.discarded, [], 'nothing is discarded while the release still matches');
  assert.deepEqual(f.created, []);
});

test('a saved job is never discarded without the panel lock', async () => {
  const f = strandedJobFixture({descriptor: {apkSha256: 'f'.repeat(64)}, lockAvailable: false});
  await f.controller.preview(target);
  await assert.rejects(f.controller.install(true), /transaction_busy/);
  assert.deepEqual(f.discarded, []);
  assert.deepEqual(f.created, []);
});

test('setting a version aside is explained on the step the person is already on', () => {
  const source = readFileSync(new URL('../src/install-main.mjs', import.meta.url), 'utf8');
  const branch = source.slice(source.indexOf('if (preview.discarded)'),
    source.indexOf('if (receipt) await installAll()'));
  assert.ok(branch.length > 0, 'the discarded branch exists');
  assert.match(branch, /screen\.restartedDifferentVersion/);
  assert.ok(!/fail\(|quarantine\(/.test(branch), 'it never routes to the error screen');
  assert.ok(!/addEventListener|\.disabled|confirm\(/.test(branch),
    'it adds no second consent and no further choice');
  const messages = readFileSync(new URL('../src/install-screen-messages.mjs', import.meta.url), 'utf8');
  const sentence = /restartedDifferentVersion: '([^']+)'/.exec(messages)?.[1];
  assert.ok(sentence && !/[{}<>]|JSON|job|phase|artifact/.test(sentence),
    'the reason is one plain sentence, with no technical detail');
});

test('a job stalled in recovery on a panel that already runs it converges', async () => {
  for (const phase of ['recovery_required', 'cleanup_pending']) {
    const f = strandedJobFixture({phase});
    // The panel runs exactly this job's own app, so there is nothing to recover.
    f.installed = true;
    const preview = await f.controller.preview(target);
    assert.equal(preview.receipt, null, `${phase}: the stalled record is set aside`);
    assert.equal(preview.adopt, true, `${phase}: the panel is adopted, not refused`);
    assert.equal(preview.discarded, null, 'the release never changed, so nothing is announced');
    assert.ok(!f.inspected.some(entry => entry.startsWith('recovery:')),
      `${phase}: the recovery observation that demands a clean panel never runs`);
    await f.controller.install(true);
    assert.deepEqual(f.discarded, [3], `${phase}: discarded at its exact revision`);
    assert.deepEqual(f.created, ['installed'], `${phase}: and the panel adopted`);
  }
});

test('a job stalled in recovery on a panel without it still recovers', async () => {
  const f = strandedJobFixture({phase: 'recovery_required'});
  const preview = await f.controller.preview(target);
  assert.equal(preview.receipt.phase, 'recovery_required', 'recovery is still the way out');
  assert.deepEqual(f.discarded, []);
  assert.ok(f.inspected.includes('recovery:recovery_required'));
});
