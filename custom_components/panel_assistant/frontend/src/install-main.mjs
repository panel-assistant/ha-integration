import { Adb, AdbDaemonTransport } from '@yume-chan/adb';
import AdbWebCredentialStore from '@yume-chan/adb-credential-web';
import { AdbDaemonWebUsbDeviceManager } from '@yume-chan/adb-daemon-webusb';
import { boundedUsb } from './usb-bounds.mjs';
import { inspectSessionTarget } from './session-target.mjs';
import { openJobStore } from './job-store.mjs';
import { createUsbTransactionPorts } from './usb-transaction-ports.mjs';
import { createInstallController } from './install-controller.mjs';
import { installProgress, errorView } from './install-view.mjs';
import { frontendMessages, formatFrontendMessage, installerLocale } from './frontend-localization.mjs';
import { handOverThenOpen, handoffOptions, receiveReleaseHandoff, requestSetupHandover } from './release-handoff.mjs';
import { readSetupUrl } from './panel-address.mjs';
import { renderJourney } from './wizard-look.mjs';

// A guided wizard for people who have never used a terminal. One step is on
// screen at a time; the single Install press is the consent for everything
// that follows, including granting the app its permissions. Every safety check
// below still runs, and technical detail only reaches "Details for support".

const element = id => document.getElementById(id);
const connect = element('connect');
const install = element('install');
let language = installerLocale();
let journeyStop = 1;
const bindings = new Map();
function bind(node, keys, group = 'installer', property = 'textContent') {
  const render = () => { const messages = frontendMessages(group, language); node[property] = keys.map(key => messages[key]).join(' '); };
  bindings.set(node, render);
  render();
}
function message(id, key, group = 'installer') { bind(element(id), [key], group); }
for (const node of document.querySelectorAll('[data-screen-message]')) bind(node, [node.dataset.screenMessage]);
for (const node of document.querySelectorAll('[data-screen-placeholder]')) bind(node, [node.dataset.screenPlaceholder], 'installer', 'placeholder');
function translatePage() {
  language = installerLocale();
  document.documentElement.lang = language;
  for (const render of bindings.values()) render();
  for (const node of document.querySelectorAll('[data-screen-aria]')) node.setAttribute('aria-label', frontendMessages('installer', language)[node.dataset.screenAria]);
  renderJourney(element('journey'), journeyStop, document, language);
  renderSupport();
}
window.addEventListener('languagechange', translatePage);
window.addEventListener('popstate', translatePage);

const STEPS = ['preparing', 'connect', 'allow', 'confirm', 'progress', 'done', 'error'];
const manager = window.isSecureContext && navigator.usb
  ? new AdbDaemonWebUsbDeviceManager(boundedUsb(navigator.usb)) : undefined;
const supported = Boolean(manager && navigator.locks && globalThis.indexedDB);
let release, raw, adb, sessionAdb, store, controller;
let handoff, handoffSettings, incomingAuthenticate;
let busy = false, quarantined = false;
let receipt = null;
let deadline;
let rejectStop;
const stopPromise = new Promise((_, reject) => { rejectStop = reject; });
void stopPromise.catch(() => {});
const newNonce = () => Array.from(crypto.getRandomValues(new Uint8Array(16)),
  byte => byte.toString(16).padStart(2, '0')).join('');

// Where each screen sits in the whole journey. Version was chosen in Home
// Assistant; Set up continues on the panel's own wizard. An error stays put.
const JOURNEY_STOP = Object.freeze({ preparing: 1, connect: 1, allow: 1, confirm: 2, progress: 2, done: 3 });
function show(step) {
  for (const name of STEPS) element(`step-${name}`).hidden = name !== step;
  if (Object.hasOwn(JOURNEY_STOP, step)) { journeyStop = JOURNEY_STOP[step]; renderJourney(element('journey'), journeyStop, document, language); }
}

// Support detail is collected, never shown unless the person opens it.
const supportLines = [];
const started = performance.now();
function support(label, value, template = null) {
  const at = ((performance.now() - started) / 1000).toFixed(1);
  supportLines.push({ label: label === 'Setup handover' ? 'setupHandover' : label, at, value, template });
  renderSupport();
}
function renderSupport() {
  const labels = frontendMessages('support', language);
  element('support-log').textContent = supportLines.map(({ label, at, value, template }) => {
    const text = template ? formatFrontendMessage(frontendMessages(template.group ?? 'installer', language)[template.messageKey], template.values ?? {})
      : typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    return `${labels[label]} (+${at}s)\n${text}`;
  }).join('\n\n');
}

function progress(stepKey, percent) {
  message('activity', stepKey);
  element('progress-fill').style.width = `${percent}%`;
  element('step-progress').querySelector('.bar').setAttribute('aria-valuenow', String(percent));
}

// Close each acquired resource independently. Late chooser/authentication
// completions call this again, so resources acquired after cancellation close too.
function closeResources() {
  const closingAdb = adb, closingRaw = raw;
  for (const resource of [closingAdb, closingRaw]) {
    if (!resource) continue;
    let timer;
    void Promise.race([
      Promise.resolve().then(() => resource.close()),
      new Promise(resolve => { timer = setTimeout(resolve, 5000); }),
    ]).catch(() => {}).finally(() => clearTimeout(timer));
  }
  try { store?.close(); } catch { /* The connection is already closed. */ }
}

// Any safety fault ends this connection. The saved job survives in this
// browser, so pressing Try again reconnects and carries on from where it stopped.
function quarantine(key = 'installErrorConnection', group = 'errors') {
  // After success, closing our own USB connection (or the person unplugging)
  // is not a failure: release quietly and never flash the error screen.
  if (finished) { quarantined = true; closeResources(); return; }
  if (!quarantined) {
    support('stopped', null, { messageKey: key, group });
    quarantined = true;
    clearTimeout(deadline);
    handoff?.cancel();
    rejectStop(new Error('session_closed'));
    message('error-text', key, group);
    show('error');
  }
  closeResources();
}

function ensureCurrent() {
  if (quarantined) throw new Error('session_closed');
  if (!incomingAuthenticate || !window.opener || window.opener.closed) {
    quarantine('handoffFailure', 'installer');
    throw new Error('handoff_invalid');
  }
}

// Guard every command entry, including calls following an awaited observation.
// Resource cleanup uses the original Adb instance and remains allowed after stop.
function guardedAdb(value) {
  return new Proxy(value, {
    get(target, key) {
      const member = Reflect.get(target, key, target);
      return typeof member === 'function' ? (...args) => {
        ensureCurrent();
        return member.apply(target, args);
      } : member;
    },
  });
}

// A USB step that fails closes the session itself before its own rejection
// arrives, so every other pending step then reports session_closed. Keep the
// first real code: that is the one that explains what happened.
let firstFailure;
function noteFailure(error) {
  const code = error?.code ?? error?.message;
  if (!firstFailure && code && code !== 'session_closed') {
    firstFailure = code;
    support('cause', String(code));
    message('error-text', errorView(code), 'errors');
  }
  throw error;
}
function fail(error) {
  support('error', String(error?.code ?? error?.message ?? error));
  quarantine(errorView(firstFailure ?? error?.code));
}

function releaseReady(verified) {
  release = verified;
  support('release', release.descriptor);
  show(supported ? 'connect' : 'error');
  if (!supported) message('error-text', 'unsupported');
}

// Starting again stays in this window: the Home Assistant tab hands a reloaded
// window the same verified release, and the saved job resumes where it stopped.
element('retry').addEventListener('click', () => { window.location.reload(); });
let leaving = false;
let finished = false;
window.addEventListener('pagehide', () => { if (!leaving) quarantine('pageClosed', 'installer'); });

connect.addEventListener('click', async () => {
  if (busy || !release || quarantined) return;
  busy = true;
  deadline = setTimeout(() => quarantine('connectionTimeout', 'installer'), 45000);
  try {
    // requestDevice is invoked directly within this user gesture, never on load.
    const chosen = manager.requestDevice();
    await Promise.race([stopPromise, (async () => {
      const device = await chosen;
      if (device) raw = device.raw;
      if (quarantined) { closeResources(); return; }
      if (!device) { quarantine('noSelection', 'installer'); return; }
      ensureCurrent();
      show('allow');
      const connection = await device.connect();
      if (quarantined) { closeResources(); return; }
      ensureCurrent();
      const transport = await AdbDaemonTransport.authenticate({
        serial: device.serial, connection,
        credentialStore: new AdbWebCredentialStore('ha-paneld-usb-installer'),
        readTimeLimit: 10000,
      });
      adb = new Adb(transport);
      if (quarantined) { closeResources(); return; }
      ensureCurrent();
      void adb.disconnected.then(() => quarantine('disconnected', 'installer'), fail);
      sessionAdb = guardedAdb(adb);
      message('allow-text', 'checkingPanel');
      store = await openJobStore();
      if (quarantined) { closeResources(); return; }
      ensureCurrent();
      const target = await inspectSessionTarget(sessionAdb, release.descriptor, raw, ensureCurrent);
      ensureCurrent();
      support('panel', target);
      const ports = createUsbTransactionPorts({
        adb: sessionAdb, usbDevice: raw, ensureCurrent,
        authenticate: async () => {
          ensureCurrent();
          const verified = await incomingAuthenticate();
          ensureCurrent();
          return verified;
        },
        // The step reports its own cause next; until then say nothing more specific.
        quarantine: () => quarantine('installErrorGeneric'),
        onUploadProgress(sent, total) {
          if (sent >= total) {
            progress('stepFinishingCopy', 50);
            support('copied', null, { messageKey: 'copiedDetail', group: 'support', values: { bytes: total } });
          } else {
            progress('stepCopying', 10 + Math.floor((40 * sent) / total));
          }
        },
      });
      controller = createInstallController({ store, ports, ensureCurrent,
        onReceipt(value) {
          receipt = value;
          support('savedProgress', receipt);
          const { stepKey, percent } = installProgress(receipt);
          progress(stepKey, percent);
        },
        // Support detail only: a leftover copy is never the person's problem.
        onSetAside(value) { support('earlierCopy', value); },
        onStagedCopy(value) { support('stagedCopy', value); },
      });
      const preview = await controller.preview(target).catch(noteFailure);
      ensureCurrent();
      receipt = preview.receipt;
      support('savedProgress', receipt, receipt ? null : { messageKey: 'none', group: 'support' });
      // A job already under way resumes without asking again: the person
      // agreed to install when they started it.
      const confirmBody = [];
      // An unfinished attempt at another version was set aside rather than
      // refused. That is the person's own change of mind, so it is one
      // sentence on the step they were already on, not an error.
      if (preview.discarded) {
        support('setAside', preview.discarded);
        confirmBody.push('restartedDifferentVersion');
      }
      // The same build already on the panel is not an error: say so plainly,
      // and let the one press finish its setup.
      if (!receipt && preview.adopt) {
        support('alreadyInstalled', `${release.descriptor.versionName} (${release.descriptor.versionCode})`);
        bind(element('step-confirm').querySelector('h2'), ['alreadyInstalledHeading']);
        confirmBody.push('alreadyInstalledBody');
        bind(install, ['continueSetup']);
      } else {
        confirmBody.push('confirmBody');
      }
      bind(element('confirm-body'), confirmBody);
      if (receipt) await installAll(); else show('confirm');
    })().catch(error => { fail(error); throw error; })]);
  } catch (error) { fail(error); }
  finally { clearTimeout(deadline); busy = false; }
});

install.addEventListener('click', async () => {
  if (busy || !controller || quarantined) return;
  busy = true;
  try { await installAll(); } catch (error) { fail(error); } finally { busy = false; }
});

// The controller runs the whole install, permissions included, under the one
// consent; this screen only shows its progress and then hands over to the
// panel's own setup.
async function installAll() {
  clearTimeout(deadline);
  show('progress');
  const { stepKey, percent } = installProgress(receipt);
  progress(stepKey, percent);
  const installed = await Promise.race([stopPromise, controller.install(true).catch(noteFailure)]);
  ensureCurrent();
  support('permissions', installed.permissions);

  progress('stepOpening', 100);
  const url = await readSetupUrl(sessionAdb, newNonce);
  support('setupAddress', url, url ? null : { messageKey: 'setupMissing', group: 'support' });
  // The install is done: losing USB from here on is not a failure.
  finished = true;
  await handOverThenOpen(url, {
    handover: address => requestSetupHandover({ options: handoffSettings, address }),
    support,
    open: finish,
  });
}

function finish(url) {
  finished = true;
  show('done');
  const link = element('open-setup');
  if (!url) {
    message('done-text', 'doneManual');
    return;
  }
  message('done-text', 'doneOpening');
  link.href = url;
  link.hidden = false;
  // Same tab, so the panel's own wizard simply takes over. The visible button
  // stays in case this computer cannot reach the panel's network.
  setTimeout(() => {
    // Leaving on success is not a failure: release USB quietly, then go.
    leaving = true;
    closeResources();
    window.location.assign(url);
  }, 1200);
}

if (!supported) {
  message('error-text', 'unsupported');
  show('error');
  element('retry').hidden = true;
} else if (window.location.hash) {
  try {
    const options = handoffOptions(window.location.hash);
    handoffSettings = options;
    busy = true;
    const slow = setTimeout(() => { message('preparing-text', 'preparingSlow'); }, 15000);
    handoff = receiveReleaseHandoff({ options });
    void handoff.completion.then(({ release: verified, authenticate }) => {
      if (quarantined) return;
      incomingAuthenticate = authenticate;
      releaseReady(verified);
    }, () => {
      if (!quarantined) quarantine('handoffFailure', 'installer');
    }).finally(() => { clearTimeout(slow); busy = false; });
  } catch { quarantine('handoffFailure', 'installer'); busy = false; }
  message('retry', 'backToHa');
} else {
  quarantine('handoffFailure', 'installer');
  message('retry', 'backToHa');
}

translatePage();
