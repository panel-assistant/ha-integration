// Refresh packaged branding from the website, or compare it with the current published bytes.
// node tools/brand/sync.mjs sync | check [--offline]
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const manifestFile = new URL('assets.json', import.meta.url);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
async function published(url) {
  const response = await fetch(url, { headers: { 'User-Agent': 'Panel-Assistant-Brand-Sync/1.0', 'Cache-Control': 'no-cache' }, signal: AbortSignal.timeout(30000) });
  if (!response.ok) throw Error(`${url}: HTTP ${response.status}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  if (/text\/html/i.test(response.headers.get('content-type') || '')) throw Error(`${url}: received HTML instead of an asset`);
  return bytes;
}
export async function run(command, offline = false) {
  if (!['sync', 'check'].includes(command) || (command === 'sync' && offline)) throw Error('usage: node tools/brand/sync.mjs sync | check [--offline]');
  const manifest = JSON.parse(readFileSync(manifestFile, 'utf8'));
  const files = new Map();
  for (const asset of manifest.assets) {
    if (asset.url !== `https://panel-assistant.io/brand/${asset.name}`) throw Error(`invalid canonical URL: ${asset.url}`);
    const destination = new URL(asset.path, root);
    const bytes = command === 'sync' ? await published(asset.url) : readFileSync(destination);
    if (command === 'sync') {
      asset.sha256 = hash(bytes);

    } else {
      if (hash(bytes) !== asset.sha256) throw Error(`${asset.path}: packaged copy differs from its manifest; run sync`);
      if (!offline && !bytes.equals(await published(asset.url))) throw Error(`${asset.path}: website branding changed; run sync`);
    }
    files.set(asset.name, bytes);
  }
  if (command === 'sync') {
    for (const asset of manifest.assets) writeFileSync(new URL(asset.path, root), files.get(asset.name));
    writeFileSync(manifestFile, JSON.stringify(manifest, null, 2) + '\n');
  }
  console.log(`brand: ${command} passed (${manifest.assets.length} website assets${offline ? ', offline' : ', latest published bytes'})`);
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [command, flag] = process.argv.slice(2);
  if (flag && flag !== '--offline') throw Error('unknown option');
  run(command, flag === '--offline').catch(error => { console.error(error.message); process.exitCode = 1; });
}
