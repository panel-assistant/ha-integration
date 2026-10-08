import test from 'node:test';
import assert from 'node:assert/strict';
import { cpSync, mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const repo = fileURLToPath(new URL('../../../../', import.meta.url));
test('brand CLI rejects stale published, packaged and generated artwork', () => {
  const root = mkdtempSync(join(tmpdir(), 'pa-brand-'));
  try {
    cpSync(join(repo, 'tools/brand'), join(root, 'tools/brand'), { recursive: true });
    const manifest = JSON.parse(readFileSync(join(root, 'tools/brand/manifest.json'), 'utf8'));
    for (const { path } of [...manifest.assets, ...manifest.generated]) {
      mkdirSync(dirname(join(root, path)), { recursive: true });
      cpSync(join(repo, path), join(root, path));
    }
    // Supply published bytes through fetch's response seam; the CLI still compares canonical
    // URLs and runs every packaged/generated check as it does in CI.
    const published = Object.fromEntries(manifest.assets.map(a => [a.url, readFileSync(join(repo, a.path)).toString('base64')]));
    const preload = join(root, 'published.mjs');
    writeFileSync(preload, `const assets = ${JSON.stringify(published)};\n`
      + 'globalThis.fetch = async url => new Response(process.env.BRAND_DRIFT ? Buffer.from("changed") : Buffer.from(assets[url], "base64"), {status:200});\n');
    const run = drift => spawnSync(process.execPath, ['--import', preload, join(root, 'tools/brand/sync.mjs'), 'check'],
      { encoding: 'utf8', env: { ...process.env, BRAND_DRIFT: drift ? '1' : '' } });
    assert.equal(run(false).status, 0);
    let result = run(true);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /website branding changed/);
    const assetPath = join(root, manifest.assets[0].path);
    const original = readFileSync(assetPath);
    writeFileSync(assetPath, 'changed');
    result = run(false);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /packaged copy differs/);
    writeFileSync(assetPath, original);
    writeFileSync(join(root, manifest.generated[0].path), 'changed');
    result = run(false);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /generated brand differs/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
