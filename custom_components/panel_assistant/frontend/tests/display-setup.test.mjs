import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync, readFileSync, existsSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DISPLAY_RESTORE_RECORD, applyDisplaySizing, buildDisplaySizing, displaySizing,
  parseDisplaySizing} from '../src/display-provisioning.mjs';

// The USB installer's display program, run unchanged in a real shell whose
// `wm density` and `settings` keep one panel's state in files.
const STAND_IN = String.raw`
wm() {
  if [ -z "$2" ]; then echo "Physical density: 240"; [ ! -f "$STATE/density" ] || echo "Override density: $(cat "$STATE/density")"
  else echo "wm density $2" >> "$STATE/calls"; echo "$2" > "$STATE/density"; fi
}
settings() {
  if [ "$1" = get ]; then if [ -f "$STATE/$3" ]; then cat "$STATE/$3"; else echo null; fi
  else echo "settings $*" >> "$STATE/calls"; printf '%s\n' "$4" > "$STATE/$3"; fi
}
`;
const nonce = 'a'.repeat(32);

function panel() {
  const state = mkdtempSync(join(tmpdir(), 'display-setup-'));
  const read = name => existsSync(join(state, name)) ? readFileSync(join(state, name), 'utf8').trim() : null;
  const run = program => spawnSync('/bin/sh', ['-c', STAND_IN + program.replaceAll(DISPLAY_RESTORE_RECORD, join(state, 'record'))],
    {encoding: 'utf8', env: {PATH: process.env.PATH, STATE: state}}).stdout;
  return {state, read, run, calls: () => (read('calls') ?? '').split('\n').filter(Boolean)};
}

test('a fresh X2i gets density 216 once, its own values on record', () => {
  const p = panel();
  const sizing = displaySizing({density: 216, font_scale: 1});
  assert.equal(parseDisplaySizing(p.run(buildDisplaySizing(nonce, sizing)), nonce, sizing), 'applied');
  assert.deepEqual(p.calls(), ['wm density 216']);
  assert.equal(p.read('record'), 'density=none\nfont_scale=null');
  assert.equal(parseDisplaySizing(p.run(buildDisplaySizing(nonce, sizing)), nonce, sizing), 'applied');
  assert.deepEqual(p.calls(), ['wm density 216']);
});

test('a recommended text size is written in the spelling the network installer uses', () => {
  const p = panel();
  writeFileSync(join(p.state, 'font_scale'), '1.0\n');
  const sizing = displaySizing({density: null, font_scale: 0.9});
  assert.equal(parseDisplaySizing(p.run(buildDisplaySizing(nonce, sizing)), nonce, sizing), 'applied');
  assert.deepEqual(p.calls(), ['settings put system font_scale 0.9']);
});

test('applying never throws, and an unreadable answer is reported as such', async () => {
  const value = {density: 216, font_scale: 1};
  assert.equal(await applyDisplaySizing({adb: {}, newNonce: () => nonce, value,
    readShell: async () => 'garbage'}), 'unreadable');
  assert.equal(await applyDisplaySizing({adb: {}, newNonce: () => nonce, value: {density: 1}, readShell: async () => ''}),
    'not_recommended');
  const p = panel();
  assert.equal(await applyDisplaySizing({adb: {}, newNonce: () => nonce, value,
    readShell: async (_adb, program) => p.run(program)}), 'applied');
});
