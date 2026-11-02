import test from 'node:test';
import assert from 'node:assert/strict';
import { fakeHass, mount, tick, TOKEN, TOKEN2, url, store, row, pushed } from './fixtures/sidebar-harness.mjs';

for (const failure of ['unreachable', 'session refused', 'invalid embed URL']) {
  test(`the panel picker retains the last served tab through ${failure}`, async t => {
    store.clear();
    const hass = fakeHass({ panels: [row('one'), row('two', failure === 'unreachable' ? 'unreachable' : 'reachable')] });
    if (failure === 'session refused') {
      const subscribe = hass.connection.subscribeMessage.bind(hass.connection);
      hass.connection.subscribeMessage = (callback, message, options) => message.entry_id === 'two'
        ? Promise.reject(new Error('cannot_open')) : subscribe(callback, message, options);
    }
    const { panel, $, subs } = await mount(hass, false, '/one/configure/voice');
    t.after(() => { panel.disconnectedCallback(); store.clear(); });
    subs[0].callback({ kind: 'opened', url: url(TOKEN) });
    subs[0].callback({ kind: 'page', page: 'configure' });
    $('#panels').fire('change', { target: { value: 'two' } }); await tick();
    if (failure === 'invalid embed URL') subs.at(-1).callback({ kind: 'opened', url: '/untrusted' });
    assert.equal($('#failure').hidden, false, 'the intermediate panel actually failed to open');
    subs[0].callback({ kind: 'page', page: 'install' });
    const beforeDevice = pushed.length;
    $('#device').fire('click', { button: 0, preventDefault() {} });
    assert.equal(pushed[beforeDevice][2], '/panel-assistant/two/configure', 'device settings retain the same tab in their Back entry');
    $('#panels').fire('change', { target: { value: 'one' } }); await tick();
    subs.at(-1).callback({ kind: 'opened', url: url(TOKEN) });
    assert.equal($('#frame').getAttribute('src'), `${url(TOKEN)}configure`, 'return to the served tab without its old anchor');
  });
}

test('successful navigation replaces the retained tab, including Dashboard', async t => {
  store.clear();
  const { panel, $, subs } = await mount(fakeHass());
  t.after(() => { panel.disconnectedCallback(); store.clear(); });
  subs[0].callback({ kind: 'opened', url: url(TOKEN) });
  subs[0].callback({ kind: 'page', page: 'configure' });
  $('#panels').fire('change', { target: { value: 'two' } }); await tick();
  subs[1].callback({ kind: 'opened', url: url(TOKEN2) });
  assert.equal($('#frame').getAttribute('src'), `${url(TOKEN2)}configure`);
  subs[1].callback({ kind: 'page', page: 'logs' });
  subs[0].callback({ kind: 'page', page: 'install' });
  subs[1].callback({ kind: 'page', page: '../configure' });
  $('#panels').fire('change', { target: { value: 'one' } }); await tick();
  subs[2].callback({ kind: 'opened', url: url(TOKEN) });
  assert.equal($('#frame').getAttribute('src'), `${url(TOKEN)}logs`, 'only a current valid served page replaces the tab');
  subs[2].callback({ kind: 'page', page: '' });
  $('#panels').fire('change', { target: { value: 'two' } }); await tick();
  subs[3].callback({ kind: 'opened', url: url(TOKEN2) });
  assert.equal($('#frame').getAttribute('src'), url(TOKEN2), 'served Dashboard clears the previously retained path');
});
