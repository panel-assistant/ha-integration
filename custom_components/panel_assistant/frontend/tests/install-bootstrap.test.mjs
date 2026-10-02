import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';

test('the actual installer bootstrap refuses saved offline files before opening USB', () => {
  const main = new URL('../src/install-main.mjs', import.meta.url).href;
  const script = `
    import assert from 'node:assert/strict';
    const elements = new Map();
    const node = id => {
      if (!elements.has(id)) elements.set(id, { hidden: false, dataset: {}, style: {},
        listeners: new Map(), addEventListener(type, fn) { this.listeners.set(type, fn); },
        querySelector() { return { setAttribute() {} }; } });
      return elements.get(id);
    };
    globalThis.document = { getElementById: node, querySelectorAll: () => [] };
    globalThis.window = { isSecureContext: true, opener: null, location: { hash: '' },
      addEventListener() {} };
    Object.defineProperty(globalThis, 'navigator', { value: { locks: {}, usb: {
      addEventListener() {}, requestDevice() { assert.fail('offline bootstrap opened USB'); }
    } }, configurable: true });
    globalThis.indexedDB = {};
    await import(${JSON.stringify(main)});
    assert.equal(node('step-error').hidden, false);
    assert.equal(node('step-connect').hidden, true);
    await node('connect').listeners.get('click')();
    assert.ok(node('error-text').textContent.length > 0);
  `;
  assert.doesNotThrow(() => execFileSync(process.execPath, ['--input-type=module', '-e', script], { stdio: 'pipe' }));
});
