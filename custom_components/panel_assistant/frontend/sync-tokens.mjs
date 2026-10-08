// Copy the Android app's generated tokens.css into src/tokens.mjs, pinned to one app commit.
//
//   node sync-tokens.mjs <panel-assistant/android checkout> <commit>
//
// tools/tokens/tokens.json in the app is the one source of the UI's colours; its generator writes
// tools/tokens/tokens.css, and this copies that file byte for byte. tests/tokens.test.mjs fails when
// the copy no longer matches the hash recorded here, so a hand edit cannot pass as a sync.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';

const [checkout, ref] = process.argv.slice(2);
if (!checkout || !ref) {
  console.error('usage: node sync-tokens.mjs <panel-assistant/android checkout> <commit>');
  process.exit(2);
}
const path = 'tools/tokens/tokens.css';
const commit = execFileSync('git', ['-C', checkout, 'rev-parse', '--verify', `${ref}^{commit}`], { encoding: 'utf8' }).trim();
const css = execFileSync('git', ['-C', checkout, 'show', `${commit}:${path}`], { encoding: 'utf8' });
const sha256 = createHash('sha256').update(css, 'utf8').digest('hex');
const source = { repository: 'panel-assistant/android', path, commit, sha256 };
writeFileSync(new URL('./src/tokens.mjs', import.meta.url),
  `// Synced by sync-tokens.mjs from ${source.repository} ${path} at ${commit}; do not edit.\n`
  + `export const TOKENS_SOURCE = Object.freeze(${JSON.stringify(source)});\n`
  + `export const TOKENS_CSS = ${JSON.stringify(css)};\n`);
console.log(`tokens: synced ${path} at ${commit} (sha256 ${sha256})`);
