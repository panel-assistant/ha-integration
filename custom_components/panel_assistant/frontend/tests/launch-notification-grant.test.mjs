import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {buildLaunch, parseLaunch} from '../src/install-contract.mjs';
import {LAUNCH_COMPONENTS, LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID} from '../src/app-identity.mjs';

const nonce = 'a'.repeat(32);

// Runs the real launch program against stand-ins for `pm` and `am` that record
// the order they were called in, so the test sees what the panel would run.
// The record goes to a file: the program itself silences the grant's output.
function executeLaunch(sdk, packageId, {refuseGrant = false, refuseStart = false, flags = 'USER_SENSITIVE_WHEN_GRANTED'} = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'launch-grant-'));
  const log = join(dir, 'calls');
  try {
    const program = `pm() { echo "pm $*" >> '${log}'; echo "Exception occurred while executing 'grant'"; echo denied >&2; ${refuseGrant ? 'return 255' : ':'}; }
am() { echo "am $*" >> '${log}'; echo 'Status: ok'; ${refuseStart ? 'return 1' : ':'}; }
dumpsys() { echo '    android.permission.POST_NOTIFICATIONS'; echo '      android.permission.POST_NOTIFICATIONS: granted=false, flags=[ ${flags}]'; }
: > '${log}'
${buildLaunch(nonce, packageId, LAUNCH_COMPONENTS[packageId][0], sdk)}`;
    const result = spawnSync('/bin/sh', ['-c', program], {encoding: 'utf8'});
    return {...result, calls: readFileSync(log, 'utf8').trim().split('\n').filter(Boolean)};
  } finally { rmSync(dir, {recursive: true, force: true}); }
}

test('from Android 13 the notification permission is granted before the first start', () => {
  for (const packageId of [LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID]) {
    for (const sdk of [33, 34]) {
      const result = executeLaunch(sdk, packageId);
      const {calls} = result;
      assert.equal(calls.length, 2, calls.join('\n'));
      assert.equal(calls[0], `pm grant ${packageId} android.permission.POST_NOTIFICATIONS`);
      assert.match(calls[1], /^am start -W -n /);
      assert.equal(parseLaunch(result.stdout, nonce), 'started');
    }
  }
});

test('below Android 13 nothing is granted, because nothing is asked', () => {
  const result = executeLaunch(32, LEGACY_PACKAGE_ID);
  assert.deepEqual(result.calls, [`am start -W -n io.github.maxlyth.hapaneld/.MainActivity -p ${LEGACY_PACKAGE_ID}`]);
  assert.equal(parseLaunch(result.stdout, nonce), 'started');
});

test('a refused grant never stops or changes the start; the later readback reports it', () => {
  const refused = executeLaunch(34, LEGACY_PACKAGE_ID, {refuseGrant: true});
  assert.equal(refused.calls.length, 2, 'the start still ran after the refused grant');
  assert.match(refused.stdout, /:0\n$/, 'the launch status is the start\'s, not the grant\'s');
  assert.equal(parseLaunch(refused.stdout, nonce), 'started');
  assert.ok(!refused.stdout.includes('Exception'), 'grant output never reaches the launch contract');
  assert.equal(refused.stderr, '', 'nor does its error stream');
  const startRefused = executeLaunch(34, LEGACY_PACKAGE_ID, {refuseStart: true});
  assert.equal(parseLaunch(startRefused.stdout, nonce), 'refused', 'the status is the start\'s own');
});

test('the platform level is required and bounded', () => {
  for (const sdk of [undefined, 0, 101, 33.5, '33']) {
    assert.throws(() => buildLaunch(nonce, LEGACY_PACKAGE_ID, LAUNCH_COMPONENTS[LEGACY_PACKAGE_ID][0], sdk), /invalid_request/);
  }
});

test("a person's own \"Don't allow\" is granted over, and the app still starts", () => {
  for (const flags of ['USER_SET|USER_SENSITIVE_WHEN_GRANTED', 'USER_SET|USER_FIXED', 'USER_FIXED']) {
    const result = executeLaunch(34, LEGACY_PACKAGE_ID, {flags});
    assert.equal(result.calls[0], `pm grant ${LEGACY_PACKAGE_ID} android.permission.POST_NOTIFICATIONS`, flags);
    assert.match(result.calls[1], /^am start -W -n /, flags);
    assert.equal(parseLaunch(result.stdout, nonce), 'started');
  }
});
