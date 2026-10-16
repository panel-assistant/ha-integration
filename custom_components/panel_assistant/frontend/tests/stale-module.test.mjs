import test from 'node:test';
import assert from 'node:assert/strict';

// The module is evaluated once per address, as a browser does: each build's address carries its number.
let build = 0;
const evaluate = () => import(`../src/stale-module.mjs?v=${++build}`);

function page({ defined = [], storage = new Map(), storageThrows = false } = {}) {
  const reloads = [];
  globalThis.customElements = { get: name => (defined.includes(name) ? class {} : undefined) };
  globalThis.sessionStorage = {
    getItem: key => { if (storageThrows) throw new Error('blocked'); return storage.get(key) ?? null; },
    setItem: (key, value) => { if (storageThrows) throw new Error('blocked'); storage.set(key, value); },
  };
  globalThis.location = { reload: () => reloads.push(build) };
  return reloads;
}

test('a fresh page runs the module without reloading', async () => {
  const reloads = page();
  await evaluate();
  assert.deepEqual(reloads, []);
});

test('a new build loaded into a page still running the old sidebar reloads it once', async () => {
  const reloads = page({ defined: ['panel-assistant-sidebar'] });
  await evaluate();
  assert.equal(reloads.length, 1);
});

test('a build that already reloaded this tab does not reload it again', async () => {
  // The reload did not help (the old elements are still defined when the same build runs again).
  const address = new URL(`../src/stale-module.mjs?v=${build + 1}`, import.meta.url).href;
  const reloads = page({ defined: ['panel-assistant-sidebar'], storage: new Map([['panel-assistant-module-reloaded', address]]) });
  await evaluate();
  assert.deepEqual(reloads, []);
});

test('the installer element counts as the old module too', async () => {
  const reloads = page({ defined: ['panel-assistant-usb-install'] });
  await evaluate();
  assert.equal(reloads.length, 1);
});

test('without session storage the page is never reloaded', async () => {
  const reloads = page({ defined: ['panel-assistant-sidebar'], storageThrows: true });
  await evaluate();
  assert.deepEqual(reloads, []);
});
