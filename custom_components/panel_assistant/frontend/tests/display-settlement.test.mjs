import test from 'node:test';
import assert from 'node:assert/strict';
import {createInstallController} from '../src/install-controller.mjs';
import {createUsbTransactionPorts} from '../src/usb-transaction-ports.mjs';
import {handOverThenOpen} from '../src/release-handoff.mjs';
import {PACKAGE_ID, accessibilityComponentFor, homeComponentFor} from '../src/app-identity.mjs';

const tick = () => new Promise(resolve => setImmediate(resolve));
const target = {model: 'Test panel', serial: 'serial', primaryAbi: 'arm64-v8a', androidSdk: 32,
  rootMode: 'rootless', usbVendorId: 1, usbProductId: 2, usbSerial: 'usb'};
const launchComponent = `${PACKAGE_ID}/${PACKAGE_ID}.MainActivity`;
const artifact = {apkSize: 1234, apkSha256: 'a'.repeat(64), versionName: '1.2.3',
  packageId: PACKAGE_ID, launchComponent};

async function fixture({shelly = true, pending = true, hold = false, density = 216, ambiguous = false, unhealthy = false, homeState = 'ready', malformedStatus = false} = {}) {
  let receipt = {id: 'b'.repeat(32), phase: 'healthy', target, artifact};
  let release = {kind: 'authenticated-apk-bytes', descriptor: artifact};
  let current = true, running = true, settled = false, finishSettlement;
  let apkHash = artifact.apkSha256, model = target.model;
  const events = [];
  const shell = body => ({readable: new ReadableStream({start(controller) {
    controller.enqueue(new TextEncoder().encode(body)); controller.close();
  }}), close: async () => {}});
  const adb = {async createSocket(command) {
    if (!command.startsWith('shell:')) {
      let response;
      return {readable: new ReadableStream({start(controller) { response = controller; }}),
        writable: new WritableStream({write(bytes) {
          const request = new TextDecoder().decode(bytes);
          const homeProof = request.startsWith('GET /api/v1/status?home_proof=1 HTTP/1.1');
          assert.ok(homeProof || request.startsWith('GET /api/v1/health HTTP/1.1'));
          events.push(homeProof ? 'home-proof' : 'health');
          const body = homeProof ? (malformedStatus ? '{garbage' : JSON.stringify({
            renderer: {mode: 'external'},
            home_ui: {state: homeState, reason: 'configured_surface', evidence: 'foreground'},
          })) : `ha-paneld ${running && !unhealthy ? artifact.versionName : '1.2.2'} panel=generic build=123 cfg=12345678\n`;
          response.enqueue(new TextEncoder().encode(`HTTP/1.1 200 OK\r\nContent-Length: ${body.length}\r\n\r\n${body}`));
          response.close();
        }}), close: async () => {}};
    }
    const n = command.match(/BEGIN:([a-f0-9]{32})/)[1];
    const step = command.match(/HAPANELD_([A-Z_]+)_BEGIN/)[1];
    const section = (name, value) => [`HAPANELD_${step}_${name}_BEGIN:${n}`, value, `HAPANELD_${step}_${name}_END:${n}:0`];
    const frame = (lines, status = 0) => shell([`HAPANELD_${step}_BEGIN:${n}`, ...lines,
      `HAPANELD_${step}_END:${n}:${status}`, ''].join('\n'));
    if (step === 'POSTURE') {
      const fields = {MODEL: model, SERIAL: target.serial, ABI: target.primaryAbi, SDK: '32',
        UID: '2000', SECURE: '1', DEBUGGABLE: '0', SU: 'absent'};
      return shell([`HAPANELD_${step}_BEGIN:${n}`, ...Object.entries(fields).flatMap(([key, value]) => section(key, value)),
        `HAPANELD_${step}_END:${n}`, ''].join('\n'));
    }
    if (step === 'INSTALLED') {
      const path = '/data/app/test/base.apk';
      return shell([`HAPANELD_${step}_BEGIN:${n}`, 'present', path, ...section('MODE', '81a4'),
        ...section('SIZE', '1234'), ...section('SHA', `${apkHash}  ${path}`),
        `HAPANELD_${step}_END:${n}`, ''].join('\n'));
    }
    if (step === 'DISPLAY') {
      if (density !== 216) { pending = true; density = 216; events.push('sizing-changed'); }
      else events.push('sizing-equal');
      // The panel reads back the text size the program told it to write.
      const scale = command.match(/settings put system font_scale (\S+)/)?.[1] ?? '1';
      return frame(['record:density=none', 'record:font_scale=1', `density=${density}`, `font_scale=${scale}`]);
    }
    if (step === 'VENDOR_DISPLAY_SETTLEMENT') {
      if (!shelly) { events.push('vendor-skipped'); return frame(['skipped']); }
      events.push('native-resume');
      if (ambiguous) return frame(['error']);
      const finish = () => {
        if (pending) { running = false; pending = false; events.push('native-shutdown'); }
        settled = true;
        return frame(['settled']);
      };
      if (hold) return new Promise(resolve => { finishSettlement = () => resolve(finish()); });
      return finish();
    }
    if (step === 'LAUNCH') {
      events.push('launch');
      assert.equal(settled, true, 'final app launch requires completed native shutdown/resume');
      running = true;
      return frame(['Status: ok']);
    }
    if (step === 'READY') return frame([]);
    if (step === 'PERMISSIONS_READ') return frame(['com.other/.Reader']);
    if (step === 'PERMISSIONS_GRANT') { events.push('grants'); return frame(['home_chooser:kept']); }
    if (step === 'PERMISSIONS_VERIFY') return frame([
      `com.other/.Reader:${accessibilityComponentFor(launchComponent)}`, '1', 'WRITE_SETTINGS: allow',
      'SYSTEM_ALERT_WINDOW: allow', 'REQUEST_INSTALL_PACKAGES: allow', 'not_required',
      'granted', 'granted', 'granted', `home:${homeComponentFor(launchComponent)}`, 'device:generic']);
    if (step === 'VENDOR') return frame(['device:other']);
    if (step === 'SETASIDE') return frame(['absent']);
    throw new Error(`unexpected command ${step}`);
  }};
  const ports = createUsbTransactionPorts({adb, usbDevice: {vendorId: 1, productId: 2, serialNumber: 'usb'},
    authenticate: async () => release, quarantine() { current = false; },
    ensureCurrent() { if (!current) throw new Error('session_closed'); }});
  // Healthy receipts contain only installation progress: every setup continuation
  // must settle again, including an interrupted prior continuation.
  const controller = createInstallController({store: {load: async () => receipt}, ports,
    ensureCurrent() { if (!current) throw new Error('session_closed'); },
    locks: {request: async (name, options, callback) => callback({})}});
  await controller.preview(target);
  await controller.install(true);
  events.length = 0;
  return {controller, events, finishSettlement: () => finishSettlement(),
    get running() { return running; }, get receipt() { return receipt; },
    stop: () => { current = false; },
    setReceipt: value => { receipt = {...receipt, ...value}; },
    setRelease: value => { release = value; },
    setHash: value => { apkHash = value; }, setModel: value => { model = value; }};
}

const recommendation = {density: 216, font_scale: 1};

test('USB final acceptance waits for the pending vendor shutdown, then repairs, launches and reads fresh health', async () => {
  const f = await fixture({density: 220, pending: false, hold: true});
  let opened = false;
  const pending = handOverThenOpen('http://192.168.1.20:8888/setup', {
    handover: async () => 'handed_over', support() {},
    beforeOpen: () => f.controller.completeSetup(recommendation),
    open() { opened = true; },
  });
  await tick(); await tick();
  assert.deepEqual(f.events, ['sizing-changed', 'native-resume']);
  assert.equal(opened, false);
  f.finishSettlement();
  await pending;
  assert.deepEqual(f.events, ['sizing-changed', 'native-resume', 'native-shutdown', 'grants', 'launch', 'health', 'home-proof']);
  assert.equal(f.running, true);
  assert.equal(opened, true);
});

test('equal sizing and no sizing recommendation still consume an earlier pending vendor restart', async () => {
  for (const display of [recommendation, null]) {
    const f = await fixture();
    await f.controller.completeSetup(display);
    assert.deepEqual(f.events, [...(display ? ['sizing-equal'] : []), 'native-resume',
      'native-shutdown', 'grants', 'launch', 'health', 'home-proof']);
    f.events.length = 0;
    await f.controller.completeSetup(display);
    assert.deepEqual(f.events, [...(display ? ['sizing-equal'] : []), 'native-resume', 'grants', 'launch', 'health', 'home-proof']);
    assert.equal(f.running, true, 'repeated setup converges without another pending shutdown');
  }
});

test('a text size with more digits than Android keeps is applied and read back as applied', async () => {
  const f = await fixture({shelly: false, pending: false});
  const result = await f.controller.completeSetup({density: 216, font_scale: 1.1234567});
  assert.equal(result.displaySizing, 'applied');
});

test('other vendors finish sizing without another app launch', async () => {
  const f = await fixture({shelly: false, pending: false});
  const result = await f.controller.completeSetup(recommendation);
  assert.equal(result.settlement, 'skipped');
  assert.deepEqual(f.events, ['sizing-equal', 'vendor-skipped']);
});

test('USB interruption preserves the owned receipt and refuses final launch or setup opening', async () => {
  const f = await fixture({hold: true});
  let opened = false;
  const pending = handOverThenOpen(null, {handover() {}, support() {},
    beforeOpen: () => f.controller.completeSetup(null), open() { opened = true; }});
  await tick(); await tick();
  f.stop();
  f.finishSettlement();
  await assert.rejects(pending, /session_closed/);
  assert.equal(opened, false);
  assert.ok(!f.events.includes('launch'));
  assert.equal(f.receipt.id, 'b'.repeat(32), 'the same installed job can resume on reconnect');
});

test('setup settlement refuses a changed job, selected release, target or installed bytes before mutation', async () => {
  for (const mutate of [f => f.setReceipt({id: 'c'.repeat(32)}),
    f => f.setRelease({kind: 'authenticated-apk-bytes', descriptor: {...artifact, apkSha256: 'c'.repeat(64)}}),
    f => f.setHash('c'.repeat(64)), f => f.setModel('Another panel')]) {
    const f = await fixture(); mutate(f);
    await assert.rejects(f.controller.completeSetup(recommendation));
    assert.deepEqual(f.events, [], 'neither sizing nor vendor resume ran');
  }
});


test('an ambiguous native settlement and stale final app health cannot open setup', async () => {
  for (const options of [{ambiguous: true}, {unhealthy: true}]) {
    const f = await fixture(options);
    let opened = false;
    await assert.rejects(handOverThenOpen(null, {handover() {}, support() {},
      beforeOpen: () => f.controller.completeSetup(null), open() { opened = true; }}));
    assert.equal(opened, false);
    if (options.ambiguous) assert.ok(!f.events.includes('launch'));
    else assert.deepEqual(f.events.slice(-2), ['launch', 'health']);
  }
});

test('settlement retains controller ownership and revalidates installed bytes after native shutdown', async () => {
  const f = await fixture({hold: true});
  const pending = f.controller.completeSetup(null);
  await tick(); await tick();
  await assert.rejects(f.controller.completeSetup(null), /transaction_busy/);
  await assert.rejects(f.controller.install(true), /transaction_busy/);
  await assert.rejects(f.controller.preview(target), /transaction_busy/);
  f.setHash('c'.repeat(64));
  f.finishSettlement();
  await assert.rejects(pending, /installed_artifact_mismatch/);
  assert.ok(!f.events.includes('launch'));
});


test('a healthy app cannot complete USB setup while the vendor still owns the screen or proof is malformed', async () => {
  for (const options of [{homeState: 'blocked'}, {homeState: 'unknown'}, {malformedStatus: true}]) {
    const f = await fixture(options);
    let opened = false;
    await assert.rejects(handOverThenOpen(null, {handover() {}, support() {},
      beforeOpen: () => f.controller.completeSetup(null), open() { opened = true; }}));
    assert.deepEqual(f.events.slice(-2), ['health', 'home-proof']);
    assert.equal(f.running, true, 'HTTP health alone was available');
    assert.equal(opened, false);
  }
});

test('fresh ready proof for an external configured dashboard and setup proof both allow USB completion', async () => {
  for (const homeState of ['ready', 'setup']) {
    const f = await fixture({homeState});
    let opened = false;
    await handOverThenOpen(null, {handover() {}, support() {},
      beforeOpen: () => f.controller.completeSetup(null), open() { opened = true; }});
    assert.deepEqual(f.events.slice(-2), ['health', 'home-proof']);
    assert.equal(opened, true);
  }
});
