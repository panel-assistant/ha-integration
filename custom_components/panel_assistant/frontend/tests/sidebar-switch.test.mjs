import test from 'node:test';
import assert from 'node:assert/strict';
import { fakeHass, mount, tick, TOKEN, TOKEN2, url, store, fromWindow, SIDEBAR_MESSAGES } from './fixtures/sidebar-harness.mjs';

test('switching panels rejects the old page and keeps the new page sandboxed and responsive', async () => {
  store.clear();
  const { $, subs } = await mount(fakeHass());
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  const oldFrame = $('#frame');
  const oldWindow = oldFrame.contentWindow;
  const sandbox = oldFrame.getAttribute('sandbox');
  $('#panels').value = 'two';
  $('#panels').fire('change', { target: $('#panels') });
  await tick();
  subs[1].callback({ kind: 'opened', url: url(TOKEN2) });
  const frame = $('#frame');
  assert.equal(frame.getAttribute('src'), url(TOKEN2));
  assert.equal(frame.getAttribute('sandbox'), sandbox);
  assert.equal(frame.getAttribute('title'), SIDEBAR_MESSAGES.frameTitle);
  fromWindow(oldWindow, { type: 'panel-assistant-ready' });
  assert.equal($('#loading').hidden, false, 'the previous page cannot reveal the new page');
  fromWindow(frame.contentWindow, { type: 'panel-assistant-ready' });
  assert.equal($('#loading').hidden, true);
  assert.equal(frame.hidden, false);
});
