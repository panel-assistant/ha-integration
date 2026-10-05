import test from 'node:test';
import assert from 'node:assert/strict';
import { frontendLocale, frontendMessages, installerLocale, formatFrontendMessage, ENGLISH_MESSAGES } from '../src/frontend-localization.mjs';
import { FRONTEND_TRANSLATIONS } from '../src/translations/index.mjs';

test('every admitted frontend catalogue preserves English coverage and placeholders', () => {
  assert.deepEqual(Object.keys(FRONTEND_TRANSLATIONS).sort(), ['en', 'de', 'es', 'fr', 'it', 'zh-Hans'].sort());
  const frozen = ['Home Assistant', 'Panel Assistant', 'ha-paneld', 'USB', 'Chrome', 'Edge', 'GitHub', 'Pickles'];
  const count = (text, literal) => text.split(literal).length - 1;
  for (const [language, catalogue] of Object.entries(FRONTEND_TRANSLATIONS)) {
    for (const [group, english] of Object.entries(ENGLISH_MESSAGES)) {
      assert.deepEqual(Object.keys(catalogue[group]).sort(), Object.keys(english).sort(), `${language}.${group}`);
      assert.deepEqual(frontendMessages(group, language), catalogue[group], `${language}.${group} must not silently fall back`);
      for (const [key, source] of Object.entries(english)) {
        const target = catalogue[group][key];
        assert.equal(typeof target, 'string');
        if (source) assert.ok(target.length > 0, `${language}.${group}.${key} is empty`);
        for (const literal of frozen.filter(value => source.includes(value))) assert.equal(count(target, literal), count(source, literal), `${language}.${group}.${key} changed ${literal}`);
      }
    }
  }
});
test('current locale lookup accepts regional Tier A signals without activating new locales', () => {
  for (const [signal, expected] of [['pt-BR', 'en'], ['cs-CZ', 'en'], ['de-DE', 'de'], ['es_MX', 'es'], ['zh-CN', 'zh-Hans'], ['zh-CN-u-nu-hanidec', 'zh-Hans'], ['zh-SG-u-ca-chinese', 'zh-Hans'], ['zh-Hans-CN', 'zh-Hans'], ['zh-TW', 'en'], ['zh-TW-u-nu-hanidec', 'en'], ['zh-Hant-CN', 'en'], ['ja', 'en'], [null, 'en']]) assert.equal(frontendLocale(signal), expected);
});
test('standalone installer uses explicit inherited language, then browser language, then English', () => {
  assert.equal(installerLocale({ search: '?lang=it-IT' }, { language: 'de' }), 'it');
  assert.equal(installerLocale({ search: '?lang=ja' }, { language: 'de' }), 'en');
  assert.equal(installerLocale({ search: '' }, { languages: ['fr-CA'], language: 'de' }), 'fr');
  assert.equal(installerLocale({ search: '' }, { language: 'ja' }), 'en');
});
test('missing, empty or malformed translated placeholders safely show authoritative English', () => {
  const fixture = { de: { sidebar: { more: '★ More', opening: '★ {wrong}', restarting: '', versionLabel: '★ {build}' } } };
  const messages = frontendMessages('sidebar', 'de', fixture);
  assert.equal(messages.more, '★ More');
  for (const key of ['opening', 'restarting', 'versionLabel', 'admin']) assert.equal(messages[key], ENGLISH_MESSAGES.sidebar[key]);
  assert.deepEqual(frontendMessages('sidebar', 'ja', fixture), ENGLISH_MESSAGES.sidebar);
  assert.equal(formatFrontendMessage('{panel} and {panel}', { panel: '<b>$&</b>' }), '<b>$&</b> and <b>$&</b>');
});

test('other supported signals retain their existing authoritative-English fallback', () => {
  for (const locale of ['nl', 'pl', 'uk']) for (const group of Object.keys(ENGLISH_MESSAGES)) assert.deepEqual(frontendMessages(group, locale), ENGLISH_MESSAGES[group]);
});
