import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, mkdirSync, writeFileSync, symlinkSync, existsSync, lstatSync, rmSync,
  readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {buildSetAsideRemoval, parseSetAsideRemoval} from '../src/cleanup-contract.mjs';
import {stagingPath} from '../src/staging-contract.mjs';
import {createInstallController} from '../src/install-controller.mjs';

const nonce = 'c'.repeat(32);
const job = 'b'.repeat(32);
const other = 'e'.repeat(32);

// Runs the exact program the panel would run, against a scratch copy of
// /data/local/tmp, so the file tests below see real symlinks and neighbours.
function panel() {
  const root = mkdtempSync(join(tmpdir(), 'set-aside-'));
  mkdirSync(join(root, 'data/local/tmp'), {recursive: true});
  const local = path => join(root, path);
  // adbd's shell service delivers stderr on the same stream as stdout.
  const run = (jobId, prelude = '') => {
    const program = buildSetAsideRemoval(nonce, jobId).replace(/\/data(?=[/ \]])/g, `${root}/data`);
    return parseSetAsideRemoval(execFileSync('sh', ['-c', `{ ${prelude}${program}; } 2>&1`],
      {encoding: 'utf8'}), nonce);
  };
  return {root, local, run, done: () => rmSync(root, {recursive: true, force: true})};
}

test('a set-aside job\'s staged file is removed, and only that one', () => {
  const p = panel();
  try {
    writeFileSync(p.local(stagingPath(job)), 'partial apk');
    writeFileSync(p.local(stagingPath(other)), 'another job');
    assert.equal(p.run(job), 'removed');
    assert.equal(existsSync(p.local(stagingPath(job))), false);
    assert.equal(readFileSync(p.local(stagingPath(other)), 'utf8'), 'another job',
      'a different job\'s file is untouched');
    assert.equal(p.run(job), 'absent', 'a second run finds nothing and removes nothing');
  } finally { p.done(); }
});

test('an rm the panel denies is reported as failed, not as a broken answer', () => {
  const p = panel();
  try {
    writeFileSync(p.local(stagingPath(job)), 'held');
    assert.equal(p.run(job, 'rm() { echo "rm: Permission denied" >&2; return 1; }; '), 'failed');
    assert.ok(existsSync(p.local(stagingPath(job))));
  } finally { p.done(); }
});

test('a symlink at the set-aside path is refused and reported, and its target survives', () => {
  const p = panel();
  try {
    const victim = p.local('data/local/victim.apk');
    writeFileSync(victim, 'not ours');
    symlinkSync(victim, p.local(stagingPath(job)));
    assert.equal(p.run(job), 'symlink');
    assert.ok(lstatSync(p.local(stagingPath(job))).isSymbolicLink(), 'the link is left in place');
    assert.equal(readFileSync(victim, 'utf8'), 'not ours');
  } finally { p.done(); }
});

test('a directory at the set-aside path is never removed', () => {
  const p = panel();
  try {
    mkdirSync(p.local(stagingPath(job)));
    assert.equal(p.run(job), 'not_removable');
    assert.ok(lstatSync(p.local(stagingPath(job))).isDirectory());
  } finally { p.done(); }
});

test('a hard-linked file at the set-aside path is never removed', () => {
  const q = panel();
  try {
    const kept = q.local('data/local/kept.apk');
    writeFileSync(kept, 'shared inode');
    execFileSync('ln', [kept, q.local(stagingPath(job))]);
    assert.equal(q.run(job), 'not_removable');
    assert.ok(existsSync(q.local(stagingPath(job))));
  } finally { q.done(); }
});

test('nothing is removed through a symlinked parent', () => {
  const r = panel();
  try {
    const elsewhere = r.local('elsewhere');
    mkdirSync(elsewhere);
    writeFileSync(join(elsewhere, `ha-paneld-install-${job}.apk`), 'behind a link');
    rmSync(r.local('data/local/tmp'), {recursive: true});
    symlinkSync(elsewhere, r.local('data/local/tmp'));
    assert.equal(r.run(job), 'not_removable');
    assert.ok(existsSync(join(elsewhere, `ha-paneld-install-${job}.apk`)));
  } finally { r.done(); }
});

test('the removal names one fixed path and never globs', () => {
  const program = buildSetAsideRemoval(nonce, job);
  assert.equal((program.match(/\brm /g) ?? []).length, 1);
  assert.ok(program.includes(`rm ${stagingPath(job)};`), 'rm takes exactly the derived path');
  assert.ok(!/\*|\?\?|rm -/.test(program.replaceAll('$?', '')), 'no glob and no recursive or forced removal');
  for (const bad of ['../../etc', '*', 'B'.repeat(32), 'b'.repeat(31)]) {
    assert.throws(() => buildSetAsideRemoval(nonce, bad), /invalid_request/);
  }
});

test('a removal answer is accepted only when framed exactly', () => {
  const body = result => `HAPANELD_SETASIDE_BEGIN:${nonce}\n${result}\nHAPANELD_SETASIDE_END:${nonce}:0\n`;
  for (const result of ['removed', 'absent', 'symlink', 'not_removable', 'failed']) {
    assert.equal(parseSetAsideRemoval(body(result), nonce), result);
  }
  for (const bad of [body('deleted'), body('removed').replace(nonce, 'd'.repeat(32)),
    `HAPANELD_SETASIDE_BEGIN:${nonce}\nremoved\n`, body('removed') + 'x\n']) {
    assert.throws(() => parseSetAsideRemoval(bad, nonce), /cleanup_response_invalid/);
  }
});

const target = {model: 'Test panel', serial: 'serial', primaryAbi: 'arm64-v8a', androidSdk: 33,
  rootMode: 'rootless', usbVendorId: 1, usbProductId: 2, usbSerial: 'usb'};

// A job for one release left part-way, with the person now installing another
// (or, for `same`, a stalled job on a panel that already runs its release).
function setAsideFixture({phase = 'staged', same = false, lockAvailable = true, outcome = 'removed'} = {}) {
  const old = {apkSha256: 'a'.repeat(64), versionName: '0.9.7-rc4', versionCode: 770,
    releaseTag: 'build-770'};
  const chosen = same ? old : {apkSha256: 'f'.repeat(64)};
  let stored = {id: job, revision: 3, phase, target, artifact: old};
  const calls = [], reported = [];
  const release = {kind: 'authenticated-apk-bytes', descriptor: chosen};
  let lock = lockAvailable, beforeLock = () => {};
  // A fresh controller is a fresh page over the same saved jobs and panel.
  const open = () => createInstallController({
    store: {load: async () => stored,
      discard: async (key, revision) => {calls.push(`discard:${revision}`); stored = null;},
      retire: async (key, revision) => {calls.push(`retire:${revision}`); stored = null;},
      create: async () => {calls.push('create'); stored = {id: 'd'.repeat(32), revision: 0,
        phase: 'healthy', target, artifact: chosen}; return stored;},
      adopt: async () => {calls.push('adopt'); stored = {id: 'd'.repeat(32), revision: 0,
        phase: 'healthy', target, artifact: chosen}; return stored;}},
    ports: {authenticate: async () => release,
      inspect: async () => ({target, clean: !same, staged: false, installed: same, healthy: same}),
      inspectRecovery: async () => ({}),
      removeSetAside: async (jobId, where, bytes) => {
        calls.push(`remove:${jobId}`);
        assert.deepEqual(where, target);
        assert.equal(bytes, release);
        return outcome;
      },
      commissionPermissions: async () => ({permissionsVerified: true})},
    onSetAside: value => reported.push(value),
    locks: {request: async (name, options, callback) => {
      beforeLock();
      calls.push('lock');
      try {return await callback(lock ? {} : null);} finally {calls.push('unlock');}
    }}});
  return {controller: open(), open, calls, reported, denyLock: () => {lock = false;},
    get stored() {return stored;}, replace: value => {stored = value;},
    onLock: value => {beforeLock = value;}};
}

test('the preview removes nothing; the Install press removes the set-aside file once', async () => {
  for (const [phase, same] of [['staging', false], ['staged', false], ['recovery_required', true],
    ['cleanup_pending', true], ['healthy', false]]) {
    const f = setAsideFixture({phase, same});
    const preview = await f.controller.preview(target);
    assert.deepEqual(f.calls, [], `${phase}: the preview removes nothing and keeps the saved job`);
    assert.deepEqual(preview.setAside, {jobId: job, phase, revision: 3}, `${phase}: the job is carried`);
    await f.controller.install(true);
    const removals = f.calls.filter(call => call.startsWith('remove:'));
    assert.deepEqual(removals, [`remove:${job}`, `remove:${'d'.repeat(32)}`],
      `${phase}: exactly that job's file, once, then the new job's own copy once it is healthy`);
    const at = f.calls.indexOf(`remove:${job}`);
    const letGo = phase === 'healthy' ? 'retire:3' : 'discard:3';
    assert.deepEqual(f.calls.slice(at - 2, at + 2), ['lock', letGo, `remove:${job}`, 'unlock'],
      `${phase}: the record is let go at its revision and the file removed in one lock hold`);
    assert.ok(at < f.calls.findIndex(call => ['create', 'adopt'].includes(call)),
      `${phase}: before the new job exists`);
    assert.deepEqual(f.reported, [{jobId: job, phase, outcome: 'removed'}],
      `${phase}: the job id and result reach the support log`);
  }
});

test('a preview abandoned before Install leaves the job, and the next Install press removes its file', async () => {
  for (const [phase, same] of [['staged', false], ['recovery_required', true], ['healthy', false]]) {
    const f = setAsideFixture({phase, same});
    await f.controller.preview(target);
    // The page is closed here. Nothing was let go, so nothing was lost.
    assert.equal(f.stored?.id, job, `${phase}: the set-aside job is still saved`);
    const later = f.open();
    const preview = await later.preview(target);
    assert.deepEqual(preview.setAside, {jobId: job, phase, revision: 3}, `${phase}: set aside again`);
    assert.ok(!f.calls.some(call => call.startsWith('remove:')), `${phase}: neither preview removed anything`);
    await later.install(true);
    assert.equal(f.calls.filter(call => call === `remove:${job}`).length, 1,
      `${phase}: the abandoned preview's copy is removed by the next Install press`);
  }
});

test('a set-aside file is never removed once another job holds the panel', async () => {
  const other = {id: 'e'.repeat(32), revision: 0, phase: 'prepared', target,
    artifact: {apkSha256: 'f'.repeat(64)}};
  // Another tab starts a job before this Install press, or while it waits for the lock.
  for (const when of ['before', 'at the lock']) {
    const f = setAsideFixture();
    await f.controller.preview(target);
    if (when === 'before') f.replace(other); else f.onLock(() => f.replace(other));
    await assert.rejects(f.controller.install(true), /job_conflict/, when);
    assert.ok(!f.calls.some(call => call.startsWith('remove:') || call.startsWith('discard')), when);
  }
});

test('a refused removal is reported and the one install carries on', async () => {
  const f = setAsideFixture({outcome: 'symlink'});
  await f.controller.preview(target);
  const {receipt} = await f.controller.install(true);
  assert.equal(receipt.phase, 'healthy');
  assert.deepEqual(f.reported, [{jobId: job, phase: 'staged', outcome: 'symlink'}]);
});

test('nothing is removed without the panel lock, and an unchanged job is never set aside', async () => {
  const f = setAsideFixture();
  await f.controller.preview(target);
  f.denyLock();
  await assert.rejects(f.controller.install(true), /transaction_busy/);
  assert.ok(!f.calls.some(call => call.startsWith('remove:')));
  const g = setAsideFixture({phase: 'staged', same: true});
  const preview = await g.controller.preview(target);
  assert.equal(preview.setAside, null, 'a resumable job keeps its file');
  assert.equal(preview.receipt.phase, 'staged');
});

test('the support log records the set-aside job and what became of its file', () => {
  const source = readFileSync(new URL('../src/install-main.mjs', import.meta.url), 'utf8');
  for (const name of ['onSetAside(', 'onStagedCopy(']) {
    const hook = source.slice(source.indexOf(name));
    assert.ok(hook.length < source.length, `install-main listens for ${name}`);
    assert.match(hook.slice(0, hook.indexOf('}')), /support\(/);
  }
});
