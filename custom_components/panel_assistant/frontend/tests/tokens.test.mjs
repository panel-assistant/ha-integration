import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { TOKENS_CSS, TOKENS_SOURCE } from '../src/tokens.mjs';
import { WIZARD_CSS } from '../src/wizard-look.mjs';

test('the token copy is the synced app file, unedited', () => {
  assert.match(TOKENS_SOURCE.commit, /^[0-9a-f]{40}$/);
  assert.equal(createHash('sha256').update(TOKENS_CSS, 'utf8').digest('hex'), TOKENS_SOURCE.sha256,
    'src/tokens.mjs differs from the app commit it names; run sync-tokens.mjs instead of editing it');
});

test('the wizard look takes its colours from the synced tokens', () => {
  assert.ok(WIZARD_CSS.startsWith(TOKENS_CSS));
  const rest = WIZARD_CSS.slice(TOKENS_CSS.length);
  assert.doesNotMatch(rest, /#[0-9a-fA-F]{3,8}\b|rgba?\(/, 'a colour literal outside the tokens');
  const defined = new Set([...TOKENS_CSS.matchAll(/--([\w-]+):/g)].map(match => match[1]));
  for (const [, name] of rest.matchAll(/var\(--([\w-]+)\)/g)) assert.ok(defined.has(name), `--${name} is not a token`);
});
