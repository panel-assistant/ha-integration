import test from 'node:test';
import assert from 'node:assert/strict';
import {createInstallController} from '../src/install-controller.mjs';

// The whole install journey behind the one Install press, run against a
// scripted panel and an in-memory job store that both outlive a session, the
// way the panel and this browser's saved job outlive a closed tab. Every port
// call, store write and lock is written to one log, and each journey is judged
// against the invariants that make an install safe to resume.

const target = {model: 'Test panel', serial: 'serial', primaryAbi: 'arm64-v8a', androidSdk: 33,
  rootMode: 'rootless', usbVendorId: 1, usbProductId: 2, usbSerial: 'usb'};
const artifact = {apkSha256: 'a'.repeat(64), versionName: '0.9.8-rc2', versionCode: 880};
const EDGES = {prepared: ['staging', 'recovery_required'], staging: ['staged', 'recovery_required'],
  staged: ['installing', 'recovery_required'], installing: ['installed', 'recovery_required'],
  installed: ['launching', 'recovery_required'], launching: ['healthy', 'recovery_required'],
  healthy: [], recovery_required: ['cleanup_pending', 'prepared'],
  cleanup_pending: ['prepared', 'recovery_required']};
const PENDING = {stage: 'staging', install: 'installing', launch: 'launching', cleanup: 'cleanup_pending'};

// What outlives a session: the panel itself and this browser's saved job.
function world() {
  const log = [];
  const panel = {file: 'none', installed: false, healthy: false, copies: []};
  let stored = null;
  const store = {
    load: async () => stored,
    create: async (t, a) => {
      stored = {id: 'c'.repeat(32), revision: 0, phase: 'prepared', target: t, artifact: a};
      log.push('create');
      return stored;
    },
    advance: async (key, revision, phase) => {
      assert.equal(revision, stored.revision, 'every write names the revision it read');
      assert.ok(EDGES[stored.phase].includes(phase), `${stored.phase} -> ${phase} is a legal edge`);
      stored = {...stored, revision: revision + 1, phase};
      log.push(`write:${phase}`);
      return stored;
    },
  };
  return {log, panel, store, removed: [], get stored() {return stored;}};
}

// One USB session against that world. `interrupt` names an actuator after
// which the session drops, the way a closed tab or a pulled cable ends it.
// `after` runs once an actuator has acted, to change the world under the install.
function session(w, {interrupt, grant = {permissionsVerified: true}, after = () => {},
  onStagedCopy = () => {}} = {}) {
  let current = true, release = artifact, otherTab = false;
  const held = new Set();
  const port = (name, body) => async (...args) => {
    assert.ok(current, `${name} is never called on a closed session`);
    w.log.push(name);
    return body(...args);
  };
  const actuator = (name, body) => port(name, async (...args) => {
    // A mutation outside the device lock is recorded, never allowed to pass.
    if (held.size !== 1) w.log.push(`unlocked:${name}`);
    const result = await body(...args);
    after(name, change);
    if (interrupt === name) {current = false; throw new Error('session_closed');}
    return result;
  });
  const change = {release: value => {release = value;}, otherTab: () => {otherTab = true;}};
  const ports = {
    authenticate: port('authenticate', async () => ({kind: 'authenticated-apk-bytes', descriptor: release})),
    inspect: port('inspect', async () => ({target, clean: !w.panel.installed,
      staged: w.panel.file === 'good', installed: w.panel.installed, healthy: w.panel.healthy})),
    inspectRecovery: port('inspectRecovery', async () => ({target, clean: !w.panel.installed,
      absent: w.panel.file === 'none', removable: w.panel.file !== 'none'})),
    stage: actuator('stage', async () => {w.panel.file = w.panel.copies.shift() ?? 'good';}),
    install: actuator('install', async () => {
      if (w.panel.file === 'good' && !w.panel.installRefused) w.panel.installed = true;
    }),
    launch: actuator('launch', async () => {w.panel.healthy = w.panel.installed && !w.panel.neverHealthy;}),
    cleanup: actuator('cleanup', async () => {w.panel.file = 'none';}),
    // The job's own staged copy, named by its id; this panel holds one job's.
    removeSetAside: actuator('remove', async jobId => {
      w.removed.push(jobId);
      if (w.panel.file === 'none') return 'absent';
      w.panel.file = 'none';
      return 'removed';
    }),
    commissionPermissions: actuator('grant', async () => grant),
  };
  const locks = {request: async (name, options, callback) => {
    assert.deepEqual(options, {mode: 'exclusive', ifAvailable: true});
    if (held.has(name) || otherTab) return callback(null);
    held.add(name); w.log.push('lock');
    try {return await callback({});} finally {held.delete(name); w.log.push('unlock');}
  }};
  const controller = createInstallController({store: w.store, ports, locks, onStagedCopy,
    ensureCurrent: () => {if (!current) throw new Error('session_closed');}});
  return {controller, close: () => {current = false;}};
}

const MUTATIONS = new Set(['stage', 'install', 'launch', 'cleanup']);

// The invariants every journey is held to, read from the log.
function assertSafe(log) {
  assert.deepEqual(log.filter(entry => entry.startsWith('unlocked:')), [], 'every mutation holds the device lock');
  let lockStart = -1, lastMutation = -1;
  log.forEach((entry, index) => {
    if (entry === 'lock') lockStart = index;
    if (!MUTATIONS.has(entry) && !['grant', 'remove'].includes(entry)) return;
    const hold = log.slice(lockStart, index);
    assert.ok(hold.includes('authenticate'), `${entry} at ${index}: release bytes checked again in the same lock`);
    if (entry === 'grant' || entry === 'remove') {
      assert.ok(log.slice(0, index).includes('write:healthy'), `${entry} only after the app is healthy`);
      return;
    }
    assert.equal(log[index - 1], `write:${PENDING[entry]}`, `${entry} at ${index}: intent is saved immediately before it`);
    const since = log.slice(lastMutation + 1, index);
    assert.ok(since.includes('inspect') || since.includes('inspectRecovery'),
      `${entry} at ${index}: the panel is observed after the last change and before this one`);
    lastMutation = index;
  });
}

const count = (log, entry) => log.filter(value => value === entry).length;

test('successful install: one press copies, installs, starts and grants, in that order', async () => {
  const w = world();
  const {controller} = session(w);
  const preview = await controller.preview(target);
  assert.equal(preview.receipt, null);
  assert.deepEqual(w.log.filter(entry => MUTATIONS.has(entry) || entry === 'grant' || entry === 'create'), [],
    'looking at the panel changes nothing and saves no job');

  const {receipt, permissions} = await controller.install(true);
  assert.equal(receipt.phase, 'healthy');
  assert.deepEqual(permissions, {permissionsVerified: true});
  assert.deepEqual(w.log.filter(entry => MUTATIONS.has(entry) || entry === 'grant'),
    ['stage', 'install', 'launch', 'grant']);
  assert.ok(w.log.indexOf('create') < w.log.indexOf('stage'), 'the job is saved before anything is copied');
  assertSafe(w.log);
});

test('a verified install removes its own staged copy once the app is healthy', async () => {
  const w = world();
  const reported = [];
  const {controller} = session(w, {onStagedCopy: value => reported.push(value)});
  await controller.preview(target);
  const {receipt} = await controller.install(true);
  assert.equal(receipt.phase, 'healthy');
  assert.equal(w.panel.file, 'none', 'no copy is left on the panel');
  assert.deepEqual(w.removed, [receipt.id], 'exactly this job\'s own path, once');
  const at = w.log.indexOf('remove');
  assert.ok(w.log.indexOf('write:healthy') < at, 'only after the app is proved healthy');
  assert.ok(at < w.log.indexOf('grant'), 'before the grant, which is not part of the install');
  assert.deepEqual(reported, [{jobId: receipt.id, outcome: 'removed'}], 'the result reaches the support log');
  assertSafe(w.log);
});

test('a failed or unverified install keeps its copy, and the verified retry removes it', async () => {
  // The app went on but never answered healthy: recovery finds it installed
  // and refuses, so the install fails with its copy still on the panel.
  const failed = world();
  failed.panel.neverHealthy = true;
  const broken = session(failed);
  await broken.controller.preview(target);
  await assert.rejects(broken.controller.install(true), /recovery_not_clean/);
  assert.equal(failed.panel.file, 'good', 'a failed install keeps its copy for diagnosis');
  assert.deepEqual(failed.removed, []);

  // The install took but the tab closed before it was confirmed.
  const w = world();
  const first = session(w, {interrupt: 'install'});
  await first.controller.preview(target);
  await assert.rejects(first.controller.install(true), /session_closed/);
  assert.equal(w.panel.file, 'good', 'an unverified install keeps its copy');
  assert.deepEqual(w.removed, []);

  const second = session(w);
  await second.controller.preview(target);
  const {receipt} = await second.controller.install(true);
  assert.equal(receipt.phase, 'healthy');
  assert.equal(w.panel.file, 'none', 'the retry that proves the install removes the copy');
  assert.deepEqual(w.removed, [receipt.id]);
  assertSafe(w.log);
});

test('interrupted install: the saved intent survives and the next session observes instead of replaying', async () => {
  // The tab closes during the install step, once after the app went on and
  // once before it could. Either way the next session asks the panel first.
  for (const landed of [true, false]) {
    const w = world();
    w.panel.installRefused = !landed;
    const first = session(w, {interrupt: 'install'});
    await first.controller.preview(target);
    await assert.rejects(first.controller.install(true), /session_closed/);
    assert.equal(w.stored.phase, 'installing', 'the intent to install is on record');
    assert.equal(count(w.log, 'grant'), 0, 'an interrupted install grants nothing');
    assert.equal(w.log.at(-1), 'unlock', 'nothing further touches the panel once the session is gone');
    w.panel.installRefused = false;

    const mark = w.log.length;
    const second = session(w);
    const preview = await second.controller.preview(target);
    assert.equal(preview.receipt.phase, 'installing', 'the saved job resumes');
    const {receipt} = await second.controller.install(true);
    assert.equal(receipt.phase, 'healthy');
    const resumed = w.log.slice(mark);
    const firstAction = resumed.findIndex(entry => MUTATIONS.has(entry) || entry.startsWith('write:'));
    assert.ok(resumed.slice(0, firstAction).includes('inspect'), 'the panel is observed before any write');
    if (landed) {
      assert.equal(resumed[firstAction], 'write:installed', 'an install that landed is recognised');
      assert.equal(count(resumed, 'install'), 0, 'and never replayed');
    } else {
      assert.equal(resumed[firstAction], 'write:recovery_required', 'an install that did not land is recovered');
      assert.equal(count(resumed, 'install'), 1, 'and installed once more from a fresh copy');
    }
    assert.equal(count(w.log, 'grant'), 1);
    assertSafe(w.log);
  }
});

test('recovered install: a bad copy is observed, cleaned up and copied again under the same press', async () => {
  const w = world();
  w.panel.copies.push('partial');
  const {controller} = session(w);
  await controller.preview(target);
  // A job this press has only just created recovers too.
  const {receipt} = await controller.install(true);
  assert.equal(receipt.phase, 'healthy');
  assert.deepEqual(w.log.filter(entry => MUTATIONS.has(entry) || entry === 'grant'),
    ['stage', 'cleanup', 'stage', 'install', 'launch', 'grant']);
  const cleanup = w.log.indexOf('cleanup');
  assert.ok(w.log.lastIndexOf('inspectRecovery', cleanup) > w.log.lastIndexOf('stage', cleanup),
    'the leftover copy is observed before it is removed');
  assert.ok(w.log.indexOf('write:prepared') > cleanup, 'the job starts over only once the copy is gone');
  assertSafe(w.log);
});

test('a panel that never settles ends in an error, not a spinning install or a grant', async () => {
  const w = world();
  w.panel.copies.push(...Array(40).fill('partial'));
  const {controller} = session(w);
  await controller.preview(target);
  await assert.rejects(controller.install(true), /install_incomplete/);
  assert.equal(count(w.log, 'grant'), 0);
  assert.deepEqual(w.removed, [], 'an install that never settled removes nothing');
  // Twelve steps: each round is one copy that does not take and one cleanup.
  assert.equal(count(w.log, 'stage'), 6, 'stops at the bound');
  assertSafe(w.log);
});

test('a grant the panel cannot confirm is a failure, not a finished install', async () => {
  const w = world();
  const {controller} = session(w, {grant: {permissionsVerified: false}});
  await controller.preview(target);
  await assert.rejects(controller.install(true), /permissions_unverified/);
});

test('a release or another tab that changes under a running install stops it before the next step', async () => {
  const other = {...artifact, apkSha256: 'f'.repeat(64)};
  for (const [interference, code] of [[change => change.release(other), /artifact_changed/],
    [change => change.otherTab(), /transaction_busy/]]) {
    const w = world();
    const {controller} = session(w, {after: (name, change) => {if (name === 'stage') interference(change);}});
    await controller.preview(target);
    await assert.rejects(controller.install(true), code);
    assert.equal(w.stored.phase, 'staged', 'the copy that finished is on record');
    assert.equal(count(w.log, 'install'), 0, 'nothing more is done to the panel');
    assert.equal(count(w.log, 'grant'), 0);
  }
});
