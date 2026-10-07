import { verifyApkBundle } from './apk-verifier.mjs';
import { MAX_FEED_BYTES, MAX_TAG_LENGTH, isBuildTag, isGithubTag } from './release-identity.mjs';
import { exactKeys, newNonce } from './shared.mjs';

const fail = () => { throw new Error('handoff_invalid'); };

export function handoffOptions(hash) {
  if (!hash) return null;
  const query = new URLSearchParams(hash.slice(1));
  if ([...query.keys()].sort().join(',') !== 'ha_origin,nonce,rc') fail();
  const origin = query.get('ha_origin'), nonce = query.get('nonce'), rc = query.get('rc');
  const url = new URL(origin);
  if (!['http:', 'https:'].includes(url.protocol) || url.origin !== origin ||
      !/^[0-9a-f]{32}$/.test(nonce) || nonce.length !== 32 ||
      (rc !== '' && !isGithubTag(rc) && !isBuildTag(rc))) fail();
  return Object.freeze({ origin, nonce, expectedRcTag: rc || null });
}

// A feed build arrives as the signed feed; a GitHub release as its four signed
// files. The authenticated selected tag decides which shape is acceptable.
const GITHUB_BYTES = { checksum: 512, checksumSignature: 256, descriptor: 4096, descriptorSignature: 256 };
const FEED_BYTES = { feed: MAX_FEED_BYTES, feedSignature: 256 };

function snapshot(message) {
  const limits = isBuildTag(message.bundle?.tag) ? FEED_BYTES : GITHUB_BYTES;
  if (!exactKeys(message, ['type', 'nonce', 'bundle', 'apk']) ||
      !exactKeys(message.bundle, ['tag', ...Object.keys(limits)])) fail();
  const bundle = { tag: message.bundle.tag };
  if (typeof bundle.tag !== 'string' || bundle.tag.length > MAX_TAG_LENGTH) fail();
  for (const [key, maximum] of Object.entries(limits)) {
    const value = message.bundle[key];
    if (!(value instanceof Uint8Array) || value.byteLength < 1 || value.byteLength > maximum ||
        (typeof SharedArrayBuffer !== 'undefined' && value.buffer instanceof SharedArrayBuffer)) fail();
    bundle[key] = value.slice();
  }
  if (!(message.apk instanceof Blob)) fail();
  const size = Object.getOwnPropertyDescriptor(Blob.prototype, 'size').get.call(message.apk);
  if (size < 1 || size > 64 * 1024 * 1024) fail();
  const apk = Blob.prototype.slice.call(message.apk, 0, size);
  return { bundle, apk };
}

// Signed bytes prove the artifact; fresh admission from the live PA opener
// proves current channel and protocol eligibility at transaction boundaries.
// `verificationKey` replaces the embedded release key in tests only.
export function receiveReleaseHandoff({ windowObject = window,
  options = handoffOptions(windowObject.location.hash), timeoutMs = 300000,
  verificationKey = undefined } = {}) {
  if (!options || !windowObject.opener || !windowObject.isSecureContext ||
      !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 300000) fail();
  const source = windowObject.opener;
  let stopped = false, received = false, timer, rejectCompletion, pending;
  const cleanup = () => { clearTimeout(timer); windowObject.removeEventListener('message', receive); };
  const reply = type => source.postMessage({ type, nonce: options.nonce }, options.origin);
  const cancel = () => {
    stopped = true;
    cleanup();
    pending?.reject(new Error('handoff_cancelled'));
    pending = undefined;
    rejectCompletion(new Error('handoff_cancelled'));
  };
  let resolveCompletion;
  const completion = new Promise((resolve, reject) => {
    resolveCompletion = resolve; rejectCompletion = reject;
  });
  void completion.catch(() => {});
  async function receive(event) {
    if (stopped || event.source !== source || event.origin !== options.origin ||
        event.data?.nonce !== options.nonce) return;
    if (event.data?.type === 'ha-paneld/usb-admission-result') {
      if (!pending || event.data.requestId !== pending.requestId) return;
      const current = pending;
      pending = undefined;
      clearTimeout(timer);
      if (!exactKeys(event.data, ['type', 'nonce', 'requestId', 'tag', 'apkSha256', 'admitted']) ||
          event.data.tag !== current.tag || event.data.apkSha256 !== current.apkSha256 ||
          event.data.admitted !== true || source.closed) {
        current.reject(new Error('handoff_invalid'));
      } else current.resolve();
      return;
    }
    if (received || event.data?.type !== 'ha-paneld/usb-bundle') return;
    received = true;
    try {
      const selected = snapshot(event.data);
      const verify = async () => {
        if (stopped) fail();
        const release = await verifyApkBundle(selected.bundle, selected.apk,
          { expectedRcTag: options.expectedRcTag ?? selected.bundle.tag }, verificationKey);
        if (stopped) fail();
        return release;
      };
      const release = await verify();
      const authenticate = async () => {
        const verified = await verify();
        if (source.closed || pending) fail();
        const requestId = newNonce(windowObject.crypto);
        const { releaseTag: tag, apkSha256 } = verified.descriptor;
        await new Promise((resolve, reject) => {
          pending = { requestId, tag, apkSha256, resolve, reject };
          timer = setTimeout(() => {
            pending = undefined;
            reject(new Error('handoff_invalid'));
          }, Math.min(timeoutMs, 10000));
          try {
            source.postMessage({ type: 'ha-paneld/usb-admission', nonce: options.nonce,
              requestId, tag, apkSha256 }, options.origin);
          } catch {
            clearTimeout(timer);
            pending = undefined;
            reject(new Error('handoff_invalid'));
          }
        });
        if (stopped || source.closed) fail();
        return verified;
      };
      if (stopped) return;
      clearTimeout(timer);
      reply('ha-paneld/usb-verified');
      resolveCompletion(Object.freeze({ release, authenticate }));
    } catch {
      if (stopped) return;
      stopped = true;
      cleanup();
      try { reply('ha-paneld/usb-error'); } catch { /* No credentials or diagnostics are sent. */ }
      rejectCompletion(new Error('handoff_invalid'));
    }
  }
  windowObject.addEventListener('message', receive);
  timer = setTimeout(cancel, timeoutMs);
  try { reply('ha-paneld/usb-ready'); } catch { cancel(); }
  return Object.freeze({ completion, cancel });
}

// Before the browser leaves for the panel's own setup, ask the Home Assistant
// window that opened this page to tell the panel Home Assistant set it up, so
// its wizard skips the steps Home Assistant answers. Never rejects: whatever
// happens, setup still opens, and the outcome goes to the support log. An older
// Home Assistant ignores the request, so a missing acknowledgement ends the
// wait quickly; an acknowledged one waits for the panel's own answer.
export function requestSetupHandover({ windowObject = window, options, address,
  ackMs = 3000, resultMs = 40000 } = {}) {
  return new Promise(resolve => {
    const source = windowObject.opener;
    if (!options || !source || source.closed) { resolve('no_home_assistant_window'); return; }
    let timer;
    const done = outcome => {
      clearTimeout(timer);
      windowObject.removeEventListener('message', receive);
      resolve(outcome);
    };
    function receive(event) {
      if (event.source !== source || event.origin !== options.origin ||
          event.data?.nonce !== options.nonce) return;
      if (event.data.type === 'ha-paneld/usb-handover-accepted') {
        clearTimeout(timer);
        timer = setTimeout(() => done('no_result'), resultMs);
      } else if (event.data.type === 'ha-paneld/usb-handover-result') {
        const outcome = event.data.outcome;
        done(typeof outcome === 'string' && /^[a-z0-9_]{1,48}$/.test(outcome) ? outcome : 'invalid_result');
      }
    }
    windowObject.addEventListener('message', receive);
    timer = setTimeout(() => done('no_reply'), ackMs);
    try {
      source.postMessage({ type: 'ha-paneld/usb-handover', nonce: options.nonce, address }, options.origin);
    } catch { done('no_home_assistant_window'); }
  });
}

// The order that matters: the handover lands before the panel's setup page is
// opened, because the wizard decides its steps when it loads.
export async function handOverThenOpen(url, { handover, support, open }) {
  if (url) {
    const outcome = await Promise.resolve().then(() => handover(new URL(url).hostname))
      .catch(() => 'failed');
    support('Setup handover', outcome);
  }
  open(url);
}
