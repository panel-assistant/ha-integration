import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import * as synced from '../src/tokens.mjs';
import { WIZARD_CSS } from '../src/wizard-look.mjs';

const { TOKENS_CSS, COMPONENTS_CSS, TOKENS_SOURCE } = synced;

test('every synced copy is the app file it names, unedited', () => {
  assert.match(TOKENS_SOURCE.commit, /^[0-9a-f]{40}$/);
  assert.deepEqual(Object.keys(TOKENS_SOURCE.sha256).sort(), ['COMPONENTS_CSS', 'ICON_SVG', 'NAME_SVG', 'TOKENS_CSS']);
  for (const [name, sha256] of Object.entries(TOKENS_SOURCE.sha256)) {
    assert.equal(createHash('sha256').update(synced[name], 'utf8').digest('hex'), sha256,
      `${name} in src/tokens.mjs differs from the app commit it names; run sync-tokens.mjs instead of editing it`);
  }
});

test('the wizard look is the synced tokens and components, and adds no colour of its own', () => {
  assert.ok(WIZARD_CSS.startsWith(TOKENS_CSS + COMPONENTS_CSS));
  const defined = new Set([...TOKENS_CSS.matchAll(/--([\w-]+):/g)].map(match => match[1]));
  for (const css of [COMPONENTS_CSS, WIZARD_CSS.slice(TOKENS_CSS.length)]) {
    assert.doesNotMatch(css.replace(/\/\*[^]*?\*\//g, ''), /#[0-9a-fA-F]{3,8}\b|rgba?\(/, 'a colour literal outside the tokens');
    for (const [, name] of css.matchAll(/var\(--([\w-]+)\)/g)) assert.ok(defined.has(name), `--${name} is not a token`);
  }
});
