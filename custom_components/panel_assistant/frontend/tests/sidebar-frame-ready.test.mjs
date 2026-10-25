import test from 'node:test';
import assert from 'node:assert/strict';
import { SIDEBAR_MESSAGES, intervals, tick, TOKEN, TOKEN2, url, row, fakeHass, mount, frameWindow, fromWindow } from './fixtures/sidebar-harness.mjs';

// The frame shows a panel page only once its sandboxed document says it is one, and an open whose
// document never says anything ends on the retrying failure rather than an endless Opening.

for (const reason of ['reboot', 'update', 'recovery']) test(`${reason} keeps the shared waiting view until a usable panel page loads`, async () => {
  const hass = fakeHass({ panels: [{ ...row('one', 'restarting'), reason }], dark: true });
  const { $, subs } = await mount(hass);
  assert.equal($('#loading').hidden, false);
  assert.equal($('#loading-text').textContent, `Restarting (${reason})`);
  assert.equal($('#loading-hint').textContent, SIDEBAR_MESSAGES.restartingNext);
  assert.equal($('#status').hidden, true, 'restart copy belongs to the full pane');
  assert.equal($('#frame').hidden, true);
  hass.panels = [row('one')]; intervals[0].fn(); await tick();
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  const frame = $('#frame');
  assert.equal($('#loading').hidden, false, 'the returning document is still loading');
  frame.contentDocument = null;
  frame.contentWindow = frameWindow();
  fromWindow(frameWindow(), { type: 'panel-assistant-ready' });
  assert.equal(frame.hidden, true, 'another window cannot show the frame');
  // Chromium delivers the retry document's message before its load, WebKit after it.
  fromWindow(frame.contentWindow, { type: 'panel-assistant-wait' });
  frame.fire('load');
  assert.equal(frame.hidden, true, 'a self-refreshing 503 remains behind the waiting view');
  frame.fire('load');
  fromWindow(frame.contentWindow, { type: 'panel-assistant-wait' });
  assert.equal(frame.hidden, true);
  assert.equal($('#loading').hidden, false);
  frame.fire('load');
  fromWindow(frame.contentWindow, { type: 'panel-assistant-ready' });
  assert.equal(frame.hidden, false);
  assert.equal($('#loading').hidden, true, 'the real page returns without refreshing the sidebar');
});

test('a retry message after its own load does not hold back the next page', async () => {
  const { $, subs } = await mount(fakeHass());
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  const frame = $('#frame');
  frame.contentDocument = null;
  frame.contentWindow = frameWindow();
  // WebKit's order for the retry document, then the page it refreshes into.
  frame.fire('load');
  fromWindow(frame.contentWindow, { type: 'panel-assistant-wait' });
  assert.equal(frame.hidden, true);
  frame.fire('load');
  fromWindow(frame.contentWindow, { type: 'panel-assistant-ready' });
  assert.equal(frame.hidden, false);
  assert.equal($('#loading').hidden, true);
});

test('switching to a panel whose page says nothing shows the retrying failure, and the refresh opens it again', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { $, subs } = await mount(fakeHass({ panels: [row('shown'), row('silent')] }));
  const frame = $('#frame');
  frame.contentWindow = frameWindow();
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  frame.fire('load');
  fromWindow(frame.contentWindow, { type: 'panel-assistant-ready' });
  assert.equal(frame.hidden, false);
  $('#panels').fire('change', { target: { value: 'silent' } }); await tick();
  assert.equal(subs[1].message.entry_id, 'silent');
  subs[1].callback({ kind: 'opened', url: url(TOKEN2) });
  // An error status or a network error page loads without the embed view's script.
  frame.fire('load');
  t.mock.timers.tick(1999);
  assert.equal($('#loading').hidden, false);
  t.mock.timers.tick(1);
  assert.equal($('#loading').hidden, true, 'not an endless Opening');
  assert.equal($('#failure').hidden, false);
  assert.equal($('#failure-status').textContent, SIDEBAR_MESSAGES.failed);
  assert.equal($('#failure-pill').textContent, SIDEBAR_MESSAGES.pillRetrying);
  assert.equal(frame.getAttribute('src'), null);
  intervals[0].fn(); await tick();
  assert.equal(subs.length, 3);
  assert.equal(subs[2].message.entry_id, 'silent');
  subs[2].callback({ kind: 'opened', url: url(TOKEN) });
  // WebKit can deliver a page's word just after its load.
  frame.fire('load');
  t.mock.timers.tick(500);
  fromWindow(frame.contentWindow, { type: 'panel-assistant-ready' });
  t.mock.timers.tick(150_000);
  assert.equal(frame.hidden, false);
  assert.equal($('#failure').hidden, true);
  assert.equal($('#loading').hidden, true);
});

test('a panel left before its silent page fails opens the next panel undisturbed', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { $, subs } = await mount(fakeHass({ panels: [row('left'), row('next')] }));
  const frame = $('#frame');
  frame.contentWindow = frameWindow();
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  frame.fire('load');
  t.mock.timers.tick(1000);
  $('#panels').fire('change', { target: { value: 'next' } }); await tick();
  // WebKit can load a blank document into the frame whose URL was removed.
  frame.fire('load');
  subs[1].callback({ kind: 'opened', url: url(TOKEN2) });
  t.mock.timers.tick(1500);
  frame.fire('load');
  fromWindow(frame.contentWindow, { type: 'panel-assistant-ready' });
  t.mock.timers.tick(150_000);
  assert.equal(frame.getAttribute('src'), url(TOKEN2));
  assert.equal(frame.hidden, false);
  assert.equal($('#failure').hidden, true);
});

test('a frame whose connection fails without a load fails once the slowest real page would have opened', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { $, subs } = await mount(fakeHass());
  const frame = $('#frame');
  frame.contentWindow = frameWindow();
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  t.mock.timers.tick(149_999);
  assert.equal($('#loading').hidden, false, 'a slow first page (the panel uploading its bundle) keeps loading');
  t.mock.timers.tick(1);
  assert.equal($('#failure').hidden, false);
  assert.equal(frame.getAttribute('src'), null);
  // Retry documents are answers: a panel coming back stays Opening while each refresh answers.
  intervals[0].fn(); await tick();
  subs[1].callback({ kind: 'opened', url: url(TOKEN2) });
  for (let refresh = 0; refresh < 60; refresh++) {
    fromWindow(frame.contentWindow, { type: 'panel-assistant-wait' });
    frame.fire('load');
    t.mock.timers.tick(3000);
  }
  assert.equal($('#failure').hidden, true);
  assert.equal($('#loading').hidden, false);
  // A refresh whose connection fails (WebKit: no load) leaves the last answer a wait; that fails too.
  t.mock.timers.tick(150_000);
  assert.equal($('#failure').hidden, false);
});

test('a reconnect that keeps a page already shown leaves it shown', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const hass = fakeHass();
  const { $, subs } = await mount(hass);
  const frame = $('#frame');
  frame.contentWindow = frameWindow();
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  frame.fire('load');
  fromWindow(frame.contentWindow, { type: 'panel-assistant-ready' });
  hass.connection.listeners.get('ready')(); await tick();
  subs[1].callback({ kind: 'opened', url: url(TOKEN) });
  t.mock.timers.tick(600_000);
  assert.equal(frame.hidden, false, 'an idle page says nothing more and is not a failure');
  assert.equal($('#failure').hidden, true);
});

test('a reconnect that keeps a frame URL that never answered still fails it', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const hass = fakeHass();
  const { $, subs } = await mount(hass);
  const frame = $('#frame');
  frame.contentWindow = frameWindow();
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  // Home Assistant went away: the frame's connection and the socket fail together.
  t.mock.timers.tick(10_000);
  hass.connection.listeners.get('ready')(); await tick();
  subs[1].callback({ kind: 'opened', url: url(TOKEN) });
  t.mock.timers.tick(149_999);
  assert.equal($('#failure').hidden, true);
  t.mock.timers.tick(1);
  assert.equal($('#failure').hidden, false);
  assert.equal(frame.getAttribute('src'), null);
});

test('a reconnect that moves the frame to a URL that never answers fails it too', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const hass = fakeHass();
  const { $, subs } = await mount(hass);
  const frame = $('#frame');
  frame.contentWindow = frameWindow();
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  frame.fire('load');
  fromWindow(frame.contentWindow, { type: 'panel-assistant-ready' });
  hass.connection.listeners.get('ready')(); await tick();
  subs[1].callback({ kind: 'opened', url: url(TOKEN2) });
  t.mock.timers.tick(150_000);
  assert.equal($('#failure').hidden, false);
  assert.equal(frame.getAttribute('src'), null);
});

test('a page message before any frame URL shows nothing', async () => {
  const { $, subs } = await mount(fakeHass());
  const frame = $('#frame');
  frame.contentWindow = frameWindow();
  fromWindow(frame.contentWindow, { type: 'panel-assistant-ready' });
  assert.equal(frame.hidden, true);
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  assert.equal(frame.hidden, true, 'a new URL waits for its own page');
});
