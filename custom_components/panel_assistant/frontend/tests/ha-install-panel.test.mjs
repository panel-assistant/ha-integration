import test from 'node:test';
import assert from 'node:assert/strict';
import { frontendMessages } from '../src/frontend-localization.mjs';
import { FRONTEND_TRANSLATIONS } from '../src/translations/index.mjs';

class Element {
  attributes = new Map(); children = []; listeners = new Map(); dataset = {};
  value = ''; textContent = ''; hidden = false; disabled = false;
  setAttribute(key, value) { this.attributes.set(key, value); }
  addEventListener(key, fn) { this.listeners.set(key, fn); }
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; this.value = ''; }
  fire(key) { this.listeners.get(key)?.({ target: this }); }
}
globalThis.document = { createElement: () => new Element() };
globalThis.customElements = { get() {}, define() {} };
globalThis.HTMLElement = class extends Element {
  isConnected = true;
  attachShadow() {
    const nodes = [];
    this.shadowRoot = {
      set innerHTML(html) {
        for (const [, attrs] of html.replace(/<style>[^]*?<\/style>/g, '').matchAll(/<[a-z][a-z0-9-]*([^>]*)>/g)) {
          const node = new Element();
          for (const [, key, value] of attrs.matchAll(/([a-z-]+)="([^"]*)"/g)) {
            node.setAttribute(key, value);
            if (key === 'data-message') node.dataset.message = value;
          }
          nodes.push(node);
        }
      },
      querySelector: selector => nodes.find(node => '#' + node.attributes.get('id') === selector),
      querySelectorAll: () => nodes.filter(node => node.dataset.message),
    };
  }
};
const { HaPaneldUsbInstallPanel } = await import('../src/ha-install-panel.mjs');
const tick = () => new Promise(resolve => setImmediate(resolve));
const releases = [{ tag: 'v1.2.4-rc1', prerelease: true }, { tag: 'v1.2.3', prerelease: false },
  { tag: 'build-999', prerelease: true, name: '1.2.5 build 999' }];
function mount(language = 'en') {
  let resolve;
  const pending = new Promise(done => { resolve = done; });
  const panel = new HaPaneldUsbInstallPanel();
  panel.panel = { config: { installer_url: 'https://installer.example/', version: '9.8.7', build: 654 } };
  const hass = { user: { id: 'admin', is_admin: true }, language, fetchWithAuth: () => pending };
  panel.hass = hass;
  return { panel, hass, $: selector => panel.shadowRoot.querySelector(selector),
    answer: async value => { resolve(new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })); await tick(); } };
}
for (const language of Object.keys(FRONTEND_TRANSLATIONS)) test(`${language}: the visible app version follows the recommended or explicit release`, async t => {
  const { panel, $, answer } = mount(language);
  t.after(() => panel.disconnectedCallback());
  await answer({ releases });
  const messages = frontendMessages('haInstall', language);
  assert.equal($('#selected-release')?.hidden, false, 'Continue must name the app version outside the disclosure');
  assert.equal($('#selected-release-label')?.textContent, messages.choose);
  assert.equal($('#selected-release-version')?.textContent, '1.2.4-rc1');
  assert.equal($('#start').disabled, false);
  assert.notEqual($('#brand-version').textContent, $('#selected-release-version').textContent);
  assert.equal($('#versions').attributes.has('open'), false);
  for (const [tag, version] of [['v1.2.3', '1.2.3'], ['build-999', '1.2.5 build 999'], ['', '1.2.4-rc1']]) {
    $('#release-choices').value = tag;
    $('#release').fire('change');
    assert.equal($('#selected-release-label').textContent, tag ? messages.release : messages.choose);
    assert.equal($('#selected-release-version').textContent, version);
  }
});
for (const [state, response] of [['loading', null], ['empty', { releases: [] }], ['catalogError', {}]]) test(`${state}: no version is promised and Continue stays disabled`, async t => {
  const { panel, $, answer } = mount();
  t.after(() => panel.disconnectedCallback());
  if (response) await answer(response);
  assert.equal($('#selected-release')?.hidden, true);
  assert.equal($('#start').disabled, true);
  assert.equal($('#catalog-status').textContent, frontendMessages('haInstall', 'en')[state]);
});
