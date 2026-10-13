import { readBounded } from './bounded-stream.mjs';
import { PANEL_HTTP_SERVICE } from './panel-http.mjs';
import { isBuildVersionName } from './release-identity.mjs';

export class UsbHealthError extends Error {
  constructor(code) { super(code); this.name = 'UsbHealthError'; this.code = code; }
}
const fail = code => { throw new UsbHealthError(code); };
const malformed = () => fail('health_malformed');
const encoder = new TextEncoder();
const versionPattern = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?$/;
const releaseVersion = value => typeof value === 'string' && value.length <= 63 && versionPattern.test(value) && !value.includes('\n');
// A dev build from the signed feed reports its own versionName, which is free-form.
const validVersion = value => releaseVersion(value) || isBuildVersionName(value);
const knownFields = new Set(['panel', 'build', 'cfg', 'ha', 'ha_net', 'ha_resp', 'ha_net_p95', 'ha_net_n', 'ha_net_miss', 'ha_net_age']);

// The native endpoint returns a text line, not a JSON status document.
function healthVersion(body) {
  if (encoder.encode(body).length > 512) malformed();
  const line = body.endsWith('\n') ? body.slice(0, -1) : body;
  if (/[^\x20-\x7e]/.test(line)) malformed();
  const tokens = line.split(' ');
  if (tokens.length < 5 || tokens[0] !== 'ha-paneld' || !validVersion(tokens[1])) malformed();
  const fields = new Map();
  for (const token of tokens.slice(2)) {
    const split = token.indexOf('=');
    const key = token.slice(0, split), value = token.slice(split + 1);
    if (split < 1 || !/^[a-z][a-z0-9_]*$/.test(key) || !value || (knownFields.has(key) && fields.has(key))) malformed();
    if (!fields.has(key)) fields.set(key, value);
  }
  const build = fields.get('build');
  if (!/^[a-z0-9](?:[a-z0-9_]*[a-z0-9])?$/.test(fields.get('panel') ?? '') ||
      !/^[0-9a-f]{8}$/.test(fields.get('cfg') ?? '') ||
      !(releaseVersion(build) || (typeof build === 'string' && /^(0|[1-9][0-9]{0,18})$/.test(build) && BigInt(build) <= 9223372036854775807n))) malformed();
  return tokens[1];
}

function parseHttp(text, maximumBody) {
  const boundary = text.indexOf('\r\n\r\n');
  if (boundary < 0 || boundary > 8192) malformed();
  const lines = text.slice(0, boundary).split('\r\n');
  if (lines.some(line => /[^\x20-\x7e\t]/.test(line))) malformed();
  if (!/^HTTP\/1\.[01] 200(?: [\x20-\x7e]*)?$/.test(lines.shift())) malformed();
  if (lines.length > 64) malformed();
  const headers = new Map();
  for (const line of lines) {
    const match = /^([!#$%&'*+.^_`|~0-9A-Za-z-]+):[ \t]*([\x20-\x7e\t]*)$/.exec(line);
    if (!match || line.length > 2048) malformed();
    const name = match[1].toLowerCase();
    if (headers.has(name)) malformed();
    headers.set(name, match[2].trim());
  }
  if (headers.has('content-encoding') && headers.get('content-encoding').toLowerCase() !== 'identity') malformed();
  let body = text.slice(boundary + 4);
  const length = headers.get('content-length'), transfer = headers.get('transfer-encoding');
  if (transfer !== undefined) {
    if (length !== undefined || transfer.toLowerCase() !== 'chunked') malformed();
    let bytes = encoder.encode(body), chunks = [], total = 0;
    for (;;) {
      const end = bytes.findIndex((value, at) => value === 13 && bytes[at + 1] === 10);
      const sizeText = new TextDecoder().decode(bytes.slice(0, end));
      if (end < 1 || end > 8 || !/^[0-9a-fA-F]+$/.test(sizeText) || chunks.length > maximumBody) malformed();
      const size = Number.parseInt(sizeText, 16);
      bytes = bytes.slice(end + 2);
      if (size === 0) { if (bytes.length !== 2 || bytes[0] !== 13 || bytes[1] !== 10) malformed(); break; }
      if (size > maximumBody || total + size > maximumBody || bytes.length < size + 2 ||
          bytes[size] !== 13 || bytes[size + 1] !== 10) malformed();
      chunks.push(bytes.slice(0, size));
      total += size;
      bytes = bytes.slice(size + 2);
    }
    const decoded = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) { decoded.set(chunk, offset); offset += chunk.length; }
    try { body = new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(decoded); } catch { malformed(); }
  } else if (length !== undefined && (!/^(0|[1-9][0-9]{0,4})$/.test(length) ||
      Number(length) > maximumBody || Number(length) !== encoder.encode(body).length)) malformed();
  if (encoder.encode(body).length > maximumBody) malformed();
  return body;
}

// Internal read-only observation on the transaction's existing ADB connection.
async function readUsbApp(adb, path, parse, {
  ensureCurrent = () => {}, quarantine, timeoutMs = 5000, closeMs = 2000,
} = {}) {
  if (typeof ensureCurrent !== 'function' || typeof quarantine !== 'function' ||
      !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 5000 ||
      !Number.isSafeInteger(closeMs) || closeMs < 1 || closeMs > 2000) fail('invalid_request');
  let socket, writer, stopped = false, timer;
  const guard = () => { if (stopped) fail('health_unavailable'); ensureCurrent(); };
  const operation = (async () => {
    guard();
    socket = await adb.createSocket(PANEL_HTTP_SERVICE);
    if (stopped) {
      void Promise.resolve().then(() => socket.close()).catch(() => {});
      fail('health_unavailable');
    }
    guard();
    writer = socket.writable.getWriter();
    await writer.write(encoder.encode(`GET ${path} HTTP/1.1\r\nHost: 127.0.0.1:8888\r\nConnection: close\r\n\r\n`));
    guard();
    let text;
    try { text = await readBounded(socket.readable, 32768); }
    catch { malformed(); }
    guard();
    return parse(text);
  })();
  try {
    return await Promise.race([operation, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new UsbHealthError('health_unavailable')), timeoutMs);
    })]);
  } catch (error) {
    stopped = true;
    quarantine();
    if (error instanceof UsbHealthError) throw error;
    fail('health_unavailable');
  } finally {
    stopped = true;
    clearTimeout(timer);
    if (socket) {
      let closeTimer;
      try {
        await Promise.race([Promise.resolve().then(() => socket.close()), new Promise((_, reject) => {
          closeTimer = setTimeout(() => reject(new UsbHealthError('health_unavailable')), closeMs);
        })]);
      } catch { quarantine(); fail('health_unavailable'); }
      finally { clearTimeout(closeTimer); try { writer?.releaseLock(); } catch { /* Quarantined pending write. */ } }
    }
  }
}


export async function readUsbHealth(adb, descriptor, options = {}) {
  const expected = descriptor?.versionName;
  if (!validVersion(expected)) fail('invalid_request');
  return readUsbApp(adb, '/api/v1/health', text => healthVersion(parseHttp(text, 512)) === expected, options);
}

// Ask the app for fresh HOME/foreground proof. The app knows which configured
// surface owns the screen, including supported external dashboards.
export async function readUsbHomeProof(adb, options = {}) {
  return readUsbApp(adb, '/api/v1/status?home_proof=1', text => {
    let status;
    try { status = JSON.parse(parseHttp(text, 32768)); } catch { malformed(); }
    const home = status?.home_ui;
    const token = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
    if (!status || typeof status !== 'object' || Array.isArray(status) ||
        !home || typeof home !== 'object' || Array.isArray(home) ||
        !['ready', 'setup', 'blocked', 'unknown'].includes(home.state) ||
        typeof home.reason !== 'string' || !token.test(home.reason) ||
        typeof home.evidence !== 'string' || !token.test(home.evidence)) malformed();
    return ['ready', 'setup'].includes(home.state);
  }, options);
}
