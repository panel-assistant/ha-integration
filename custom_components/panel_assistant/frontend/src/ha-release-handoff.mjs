import { frontendLocale } from './frontend-localization.mjs';
import { MAX_FEED_BYTES, MAX_TAG_LENGTH, isBuildTag, isGithubTag } from './release-identity.mjs';

const API = '/api/panel_assistant/usb/release';
// The installer page reads the panel's address over USB but holds no Home
// Assistant credential, so it asks this window to tell the panel Home Assistant
// set it up. The server validates the address; this only refuses obvious junk.
const HANDOVER_API = '/api/panel_assistant/usb/handover';
const HANDOVER_MS = 35000;
const IPV4 = /(?:[0-9]{1,3}\.){3}[0-9]{1,3}/;
const OUTCOME = /[a-z_]{1,48}/;
const MAX_APK = 64 * 1024 * 1024;
// A reloaded installer window (a back button, a refresh) asks again with the
// same nonce. The verified bytes stay available to it this long, and no longer
// than the window stays open.
const SERVE_MS = 30 * 60 * 1000;
const FIELDS = ['id', 'tag', 'checksum', 'checksum_signature', 'descriptor',
  'descriptor_signature', 'apk_size', 'apk_sha256'];
// A feed build is described by the signed feed itself, never by GitHub's files.
const FEED_FIELDS = ['id', 'tag', 'feed', 'feed_signature', 'apk_size', 'apk_sha256'];
const MAX_METADATA = 8192;
const MAX_FEED_METADATA = Math.ceil(MAX_FEED_BYTES / 3) * 4 + MAX_METADATA;
const matches = (pattern, value) => typeof value === 'string' && pattern.exec(value)?.[0] === value;
const keys = (value, expected) => value !== null && typeof value === 'object' &&
  !Array.isArray(value) && Object.keys(value).length === expected.length &&
  expected.every((key) => Object.hasOwn(value, key));
export class HandoffError extends Error {
  constructor(code) { super(code); this.name = 'HandoffError'; this.code = code; }
}
function requireValid(value, code = 'invalid_response') {
  if (!value) throw new HandoffError(code);
}
function decode(value, maximum, exact = false) {
  requireValid(typeof value === 'string' && value.length <= Math.ceil(maximum / 3) * 4 &&
    matches(/(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?/, value));
  const raw = atob(value);
  requireValid(btoa(raw) === value && raw.length > 0 &&
    (exact ? raw.length === maximum : raw.length <= maximum));
  return Uint8Array.from(raw, (character) => character.charCodeAt(0));
}
async function readBounded(response, limit, signal, exactSize = null) {
  requireValid(response.status === 200 && !response.redirected && response.body);
  const length = response.headers.get('content-length');
  if (length !== null) {
    requireValid(matches(/0|[1-9][0-9]*/, length));
    const size = Number(length);
    requireValid(Number.isSafeInteger(size) && size <= limit &&
      (exactSize === null || size === exactSize));
  }
  const reader = response.body.getReader();
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener('abort', cancel, { once: true });
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      requireValid(!signal.aborted, 'cancelled');
      const next = await reader.read();
      requireValid(!signal.aborted, 'cancelled');
      if (next.done) break;
      size += next.value.byteLength;
      requireValid(size <= limit && (exactSize === null || size <= exactSize));
      chunks.push(next.value);
    }
    requireValid(size > 0 && (length === null || size === Number(length)) &&
      (exactSize === null || size === exactSize));
    return new Blob(chunks);
  } finally {
    signal.removeEventListener('abort', cancel);
    cancel();
    reader.releaseLock();
  }
}

/** Call directly from a user click with an application-approved installer URL. */
export function startReleaseHandoff(hass, installerUrl, {
  rcTag = null, language = hass?.language, onState = () => {}, windowObject = window, timeoutMs = 300000,
} = {}) {
  let resolveCompletion;
  let rejectCompletion;
  const completion = new Promise((resolve, reject) => {
    resolveCompletion = resolve; rejectCompletion = reject;
  });
  const controller = new AbortController();
  let finished = false;
  let child;
  let timer;
  let targetOrigin;
  let nonce;
  let acceptedReady = false;
  let sent = false;
  let delivered;
  let deliveredSha256;
  let sweep;
  let serveTimer;
  let handingOver = false;
  const stopServing = () => {
    clearInterval(sweep);
    clearTimeout(serveTimer);
    delivered = undefined;
    windowObject.removeEventListener('message', receive);
  };
  const state = (status) => { try { onState(status); } catch { /* UI callbacks do not own delivery. */ } };
  const finish = (code = null) => {
    if (finished) return;
    finished = true;
    controller.abort();
    clearTimeout(timer);
    state(code ?? 'verified');
    if (code) { stopServing(); rejectCompletion(new HandoffError(code)); return; }
    sweep = setInterval(() => { if (child.closed) stopServing(); }, 2000);
    serveTimer = setTimeout(stopServing, SERVE_MS);
    resolveCompletion();
  };
  async function deliver() {
    try {
      state('preparing');
      requireValid(!finished, 'cancelled');
      const response = await hass.fetchWithAuth(API, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(rcTag === null ? {} : { release_candidate: rcTag }),
        redirect: 'error', signal: controller.signal,
      });
      requireValid(!finished, 'cancelled');
      requireValid(response.headers.get('content-type')?.split(';')[0].trim() === 'application/json');
      const metadataBytes = await readBounded(response, MAX_FEED_METADATA, controller.signal);
      let metadata;
      try { metadata = JSON.parse(await metadataBytes.text()); }
      catch { throw new HandoffError('invalid_response'); }
      const feed = isBuildTag(metadata?.tag);
      requireValid(feed || metadataBytes.size <= MAX_METADATA);
      requireValid(!finished, 'cancelled');
      requireValid(keys(metadata, feed ? FEED_FIELDS : FIELDS) && matches(/[0-9a-f]{32}/, metadata.id) &&
        typeof metadata.tag === 'string' && metadata.tag.length <= MAX_TAG_LENGTH &&
        (rcTag === null ? (isGithubTag(metadata.tag) || isBuildTag(metadata.tag)) : metadata.tag === rcTag) &&
        matches(/[0-9a-f]{64}/, metadata.apk_sha256) &&
        Number.isSafeInteger(metadata.apk_size) && metadata.apk_size > 0 && metadata.apk_size <= MAX_APK);
      const bundle = feed ? {
        tag: metadata.tag, feed: decode(metadata.feed, MAX_FEED_BYTES),
        feedSignature: decode(metadata.feed_signature, 256, true),
      } : {
        tag: metadata.tag, checksum: decode(metadata.checksum, 512),
        checksumSignature: decode(metadata.checksum_signature, 256, true),
        descriptor: decode(metadata.descriptor, 4096),
        descriptorSignature: decode(metadata.descriptor_signature, 256, true),
      };
      state('downloading');
      requireValid(!finished, 'cancelled');
      const apkResponse = await hass.fetchWithAuth(`${API}/${metadata.id}/apk`, {
        method: 'GET', redirect: 'error', signal: controller.signal,
      });
      requireValid(!finished, 'cancelled');
      const apk = await readBounded(apkResponse, MAX_APK, controller.signal, metadata.apk_size);
      requireValid(!finished && !child.closed, 'window_closed');
      sent = true;
      deliveredSha256 = metadata.apk_sha256;
      delivered = { type: 'ha-paneld/usb-bundle', nonce, bundle, apk };
      child.postMessage(delivered, targetOrigin);
      state('verifying');
    } catch (error) {
      finish(error instanceof HandoffError ? error.code : 'delivery_failed');
    }
  }
  // Historical signed bytes cannot prove that PA still admits this artifact.
  // Keep the original selection: a default RC must not become explicit consent.
  async function admit(data) {
    if (!finished || !delivered || child.closed ||
        !keys(data, ['type', 'nonce', 'requestId', 'tag', 'apkSha256']) ||
        !matches(/[0-9a-f]{32}/, data.requestId)) return;
    let admitted = false;
    try {
      requireValid(data.tag === delivered.bundle.tag && data.apkSha256 === deliveredSha256);
      const signal = AbortSignal.timeout(Math.min(timeoutMs, 10000));
      const response = await hass.fetchWithAuth(API, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(rcTag === null ? {} : { release_candidate: rcTag }),
        redirect: 'error', signal,
      });
      requireValid(response.headers.get('content-type')?.split(';')[0].trim() === 'application/json');
      const metadata = JSON.parse(await (await readBounded(response,
        isBuildTag(data.tag) ? MAX_FEED_METADATA : MAX_METADATA, signal)).text());
      requireValid(keys(metadata, isBuildTag(data.tag) ? FEED_FIELDS : FIELDS) &&
        matches(/[0-9a-f]{32}/, metadata.id) && metadata.tag === data.tag &&
        metadata.apk_sha256 === data.apkSha256 && metadata.apk_size === delivered.apk.size);
      admitted = true;
    } catch { /* Offline PA or changed admission closes the mutation path. */ }
    if (!delivered || child.closed) return;
    try {
      child.postMessage({ type: 'ha-paneld/usb-admission-result', nonce,
        requestId: data.requestId, tag: data.tag, apkSha256: data.apkSha256, admitted }, targetOrigin);
    } catch { /* The installer has gone; no admission is delivered. */ }
  }
  // Only for the window that verified the bundle this one is still serving.
  async function handOver(data) {
    if (handingOver || !finished || !delivered || child.closed ||
      !keys(data, ['type', 'nonce', 'address']) || !matches(IPV4, data.address)) return;
    handingOver = true;
    const reply = (type, extra = {}) => {
      try { if (!child.closed) child.postMessage({ type, nonce, ...extra }, targetOrigin); } catch { /* window gone */ }
    };
    // An immediate acknowledgement lets the installer tell this Home Assistant
    // from an older one that ignores the request, without waiting for the panel.
    reply('ha-paneld/usb-handover-accepted');
    let outcome;
    try {
      const response = await hass.fetchWithAuth(HANDOVER_API, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address: data.address }), redirect: 'error',
        signal: AbortSignal.timeout(HANDOVER_MS),
      });
      const body = await response.json().catch(() => null);
      const answer = response.status === 200 ? body?.outcome : body?.error;
      outcome = matches(OUTCOME, answer) ? answer : `http_${response.status}`;
    } catch {
      outcome = 'request_failed';
    } finally {
      handingOver = false;
    }
    reply('ha-paneld/usb-handover-result', { outcome });
  }
  function receive(event) {
    if (event.source !== child || event.origin !== targetOrigin) return;
    if (event.data?.type === 'ha-paneld/usb-admission' && event.data.nonce === nonce) {
      void admit(event.data);
      return;
    }
    if (event.data?.type === 'ha-paneld/usb-handover' && event.data.nonce === nonce) {
      void handOver(event.data);
      return;
    }
    if (!keys(event.data, ['type', 'nonce']) || event.data.nonce !== nonce) return;
    if (event.data.type === 'ha-paneld/usb-ready') {
      if (!acceptedReady && !finished) { acceptedReady = true; void deliver(); }
      // The same window reloaded: hand it the same verified bytes again.
      else if (delivered && !child.closed) child.postMessage(delivered, targetOrigin);
      return;
    }
    if (finished) return;
    if (event.data.type === 'ha-paneld/usb-verified' && sent) finish();
    else if (event.data.type === 'ha-paneld/usb-error') finish('verification_failed');
  }
  try {
    requireValid(hass && typeof hass.fetchWithAuth === 'function' &&
      (rcTag === null || isGithubTag(rcTag) || isBuildTag(rcTag)) &&
      Number.isSafeInteger(timeoutMs) && timeoutMs > 0 && timeoutMs <= 300000, 'invalid_request');
    const url = new URL(installerUrl);
    requireValid(!url.username && !url.password && !url.hash &&
      (url.protocol === 'https:' || (url.protocol === 'http:' &&
        ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))), 'invalid_destination');
    url.searchParams.set('lang', frontendLocale(language));
    targetOrigin = url.origin;
    const random = new Uint8Array(16);
    windowObject.crypto.getRandomValues(random);
    nonce = Array.from(random, (value) => value.toString(16).padStart(2, '0')).join('');
    url.hash = new URLSearchParams({ ha_origin: windowObject.location.origin, nonce, rc: rcTag ?? '' }).toString();
    windowObject.addEventListener('message', receive);
    child = windowObject.open(url.href, '_blank');
    requireValid(child, 'popup_blocked');
    timer = setTimeout(() => finish('timeout'), timeoutMs);
    state('waiting');
  } catch (error) {
    finish(error instanceof HandoffError ? error.code : 'invalid_request');
  }
  return { completion, cancel: () => { finish('cancelled'); stopServing(); } };
}
