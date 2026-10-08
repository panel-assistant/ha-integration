// Copy the Android app's shared web look into src/tokens.mjs, pinned to one app commit.
//
//   node sync-tokens.mjs <panel-assistant/android checkout> <commit>
//
// tools/tokens/tokens.json in the app is the one source of the UI's colours; its generator writes
// tools/tokens/tokens.css, and this copies that file byte for byte. The shared components are the
// rules app/src/main/assets/info.css keeps between `component:begin` and `component:end`, copied in
// order; the brand mark and name are the app's copies of the website brand set. tests/tokens.test.mjs
// fails when a copy no longer matches the hash recorded here, so a hand edit cannot pass as a sync.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';

const [checkout, ref] = process.argv.slice(2);
if (!checkout || !ref) {
  console.error('usage: node sync-tokens.mjs <panel-assistant/android checkout> <commit>');
  process.exit(2);
}
const commit = execFileSync('git', ['-C', checkout, 'rev-parse', '--verify', `${ref}^{commit}`], { encoding: 'utf8' }).trim();
const show = path => execFileSync('git', ['-C', checkout, 'show', `${commit}:${path}`], { encoding: 'utf8' });
const sha256 = text => createHash('sha256').update(text, 'utf8').digest('hex');

const regions = [...show('app/src/main/assets/info.css')
  .matchAll(/\/\* component:begin \*\/\n([^]*?)\/\* component:end \*\//g)].map(match => match[1]);
if (!regions.length) throw Error('no component:begin/end regions in info.css at this commit');
const copies = {
  TOKENS_CSS: ['tools/tokens/tokens.css', show('tools/tokens/tokens.css')],
  COMPONENTS_CSS: ['app/src/main/assets/info.css', regions.join('')],
  ICON_SVG: ['app/src/main/assets/icon.svg', show('app/src/main/assets/icon.svg')],
  NAME_SVG: ['app/src/main/assets/name.svg', show('app/src/main/assets/name.svg')],
};
const source = { repository: 'panel-assistant/android', commit,
  sha256: Object.fromEntries(Object.entries(copies).map(([name, [, text]]) => [name, sha256(text)])) };
writeFileSync(new URL('./src/tokens.mjs', import.meta.url),
  `// Synced by sync-tokens.mjs from ${source.repository} at ${commit}; do not edit.\n`
  + `export const TOKENS_SOURCE = Object.freeze(${JSON.stringify(source)});\n`
  + Object.entries(copies).map(([name, [path, text]]) => `// ${path}\nexport const ${name} = ${JSON.stringify(text)};\n`).join(''));
console.log(`tokens: synced ${Object.keys(copies).join(', ')} at ${commit} (${regions.length} component regions)`);
