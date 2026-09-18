/** Fixed read-only inventory. A clean result is not permission to install. */
export const MAX_PREFLIGHT_BYTES = 32 * 1024;
import { ACCEPTED_PACKAGE_IDS, LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID,
  counterpartOf } from './app-identity.mjs';

const BASES = ['/data/user/0', '/data/data', '/data/user_de/0'];
// Residue is probed per accepted application id: only the data of the package
// being installed makes a target unclean.
export const RESIDUE_PROBES = Object.freeze(ACCEPTED_PACKAGE_IDS.flatMap(
  packageId => BASES.map(base => Object.freeze({ packageId, path: `${base}/${packageId}` }))));
const PROPERTIES = [
  ['MODEL', 'ro.product.model'], ['SERIAL', 'ro.serialno'],
  ['ABI', 'ro.product.cpu.abi'], ['SDK', 'ro.build.version.sdk'],
];
const NAMES = [...PROPERTIES.map(([name]) => name),
  'UID', 'SECURE', 'DEBUGGABLE', 'SU', 'LIVE',
  ...ACCEPTED_PACKAGE_IDS.flatMap((_, i) => [`PACKAGE${i}`, `RETAINED${i}`]),
  ...BASES.map((_, i) => `BASE${i}`), ...RESIDUE_PROBES.map((_, i) => `RESIDUE${i}`)];
const SU_OBSERVATION = 'if command -v su >/dev/null 2>&1; then echo present; ' +
  'else hapaneld_su_status=$?; if [ "$hapaneld_su_status" -eq 1 ]; then echo absent; ' +
  'else echo abnormal; fi; fi';

export class PreflightError extends Error {
  constructor(code) { super(code); this.code = code; }
}
function fail(code = 'target_response_invalid') { throw new PreflightError(code); }
function fullMatch(pattern, value) {
  return typeof value === 'string' && pattern.exec(value)?.[0] === value;
}
function checkNonce(nonce) {
  if (!fullMatch(/^[0-9a-f]{32}$/, nonce)) fail('invalid_request');
}

/**
 * Decide whether this panel may receive this package, and how.
 *
 * A panel already holding the package being installed is refused exactly as
 * before. The one admitted exception is the identity migration: a panel running
 * the legacy package and not the successor may receive the successor beside it,
 * because the panel performs the handover itself. Its legacy data is what that
 * handover migrates, so it is not residue; legacy residue with no legacy
 * package to migrate from still is.
 */
export function classifyTarget(targetPackageId, installed, residue) {
  if (installed.includes(targetPackageId) || residue.has(targetPackageId)) fail('target_not_clean');
  const counterpart = counterpartOf(targetPackageId);
  const migrationCandidate = targetPackageId === SUCCESSOR_PACKAGE_ID &&
    installed.includes(counterpart);
  // Anything else present is refused. Once a migration is admitted, the only
  // package left that can be installed or have left data behind is that
  // counterpart, so this one check covers both.
  if (!migrationCandidate && (installed.length || residue.size)) fail('target_not_clean');
  return migrationCandidate;
}

/** No peer-controlled values, paths or commands enter this shell program. */
export function buildPreflight(nonce) {
  checkNonce(nonce);
  const sections = [...PROPERTIES.map(([name, property]) => [name, `getprop ${property}`]),
    ['UID', 'id -u'], ['SECURE', 'getprop ro.secure'],
    ['DEBUGGABLE', 'getprop ro.debuggable'], ['SU', SU_OBSERVATION],
    ['LIVE', 'pm path android'],
    ...ACCEPTED_PACKAGE_IDS.flatMap((packageId, i) => [
      [`PACKAGE${i}`, `pm path ${packageId}`],
      [`RETAINED${i}`, `pm list packages -u ${packageId}`]]),
    ...BASES.map((path, i) => [`BASE${i}`,
      `if [ -L ${path} ] || [ -d ${path} ]; then if ls -1A ${path} >/dev/null 2>&1; ` +
      'then echo readable; else echo unreadable; fi; else echo unreadable; fi']),
    ...RESIDUE_PROBES.map(({ path }, i) => [`RESIDUE${i}`,
      `if [ -e ${path} ] || [ -L ${path} ]; then echo present; else echo absent; fi`]),
  ];
  return [`echo HAPANELD_PREFLIGHT_BEGIN:${nonce}`,
    ...sections.flatMap(([name, command]) => [
      `echo HAPANELD_PREFLIGHT_${name}_BEGIN:${nonce}`, command,
      `echo HAPANELD_PREFLIGHT_${name}_END:${nonce}:$?`,
    ]), `echo HAPANELD_PREFLIGHT_END:${nonce}`].join('; ');
}

function decode(body) {
  let text;
  if (typeof body === 'string') {
    if (!body.length || body.length > MAX_PREFLIGHT_BYTES ||
        new TextEncoder().encode(body).length > MAX_PREFLIGHT_BYTES) fail();
    text = body;
  } else if (body instanceof Uint8Array) {
    if (!body.byteLength || body.byteLength > MAX_PREFLIGHT_BYTES) fail();
    try { text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(body); }
    catch { fail(); }
  } else fail();
  text = text.replaceAll('\r\n', '\n');
  // Reject controls, lone surrogates and non-printing Unicode, not merely ANSI.
  if (!text.endsWith('\n') || /[\p{C}\p{Zl}\p{Zp}]/u.test(text.replaceAll('\n', ''))) fail();
  return text.slice(0, -1).split('\n');
}

function parseSections(body, nonce) {
  const lines = decode(body);
  if (lines[0] !== `HAPANELD_PREFLIGHT_BEGIN:${nonce}` ||
      lines.at(-1) !== `HAPANELD_PREFLIGHT_END:${nonce}`) fail();
  let offset = 1;
  const parsed = {};
  for (const name of NAMES) {
    if (lines[offset++] !== `HAPANELD_PREFLIGHT_${name}_BEGIN:${nonce}`) fail();
    const pattern = new RegExp(`^HAPANELD_PREFLIGHT_${name}_END:${nonce}:([0-9]{1,3})$`);
    const values = [];
    let status;
    while (offset < lines.length - 1) {
      const line = lines[offset++];
      const match = pattern.exec(line);
      if (match) { status = Number(match[1]); break; }
      if (line.startsWith('HAPANELD_')) fail();
      values.push(line);
    }
    if (status === undefined) fail();
    parsed[name] = { values, status };
  }
  if (offset !== lines.length - 1) fail();
  return parsed;
}

function one(section, allowed) {
  if (section.status !== 0 || section.values.length !== 1 ||
      (allowed && !allowed.includes(section.values[0]))) fail();
  return section.values[0];
}
function isPackagePath(line) { return fullMatch(/^package:\/[^ \t]+$/, line); }

/** descriptor must come from authenticated bundle verification, never raw JSON.
 * ROOT_SU is only an observation: callers must not install on its pending result.
 * Identity must also be rebound to the active USB session before any mutation.
 */
export function parsePreflight(body, nonce, descriptor) {
  checkNonce(nonce);
  if (!descriptor || !Number.isSafeInteger(descriptor.minSdk) ||
      descriptor.minSdk < 1 || descriptor.minSdk > 100 ||
      !Array.isArray(descriptor.supportedAbis) || descriptor.supportedAbis.length !== 2 ||
      descriptor.supportedAbis[0] !== 'arm64-v8a' || descriptor.supportedAbis[1] !== 'armeabi-v7a') {
    fail('invalid_request');
  }
  const s = parseSections(body, nonce);
  const model = one(s.MODEL), serial = one(s.SERIAL), primaryAbi = one(s.ABI), sdk = one(s.SDK);
  if (model !== model.trim() || Array.from(model).length < 1 || Array.from(model).length > 128 ||
      /[^\S ]/u.test(model) || !fullMatch(/^[A-Za-z0-9._:-]{1,128}$/, serial) ||
      !fullMatch(/^[A-Za-z0-9_.-]{1,64}$/, primaryAbi) || !fullMatch(/^[0-9]{1,3}$/, sdk)) fail();
  const androidSdk = Number(sdk);
  if (androidSdk < 1 || androidSdk > 100) fail();

  // Existing or retained package wins before root evaluation or any later su proof.
  if (s.LIVE.status !== 0 || !s.LIVE.values.length || !s.LIVE.values.every(isPackagePath)) fail();
  // Each accepted id is read on its own: that is what tells a clean panel from
  // one still running the legacy package, which the successor migrates.
  const installed = [];
  ACCEPTED_PACKAGE_IDS.forEach((packageId, i) => {
    const path = s[`PACKAGE${i}`], retained = s[`RETAINED${i}`];
    if (retained.values.includes(`package:${packageId}`) || path.values.some(isPackagePath)) {
      installed.push(packageId);
      return;
    }
    if (![0, 1].includes(path.status) || path.values.length ||
        retained.status !== 0 || retained.values.length) fail();
  });
  const readable = BASES.map((_, i) => one(s[`BASE${i}`], ['readable', 'unreadable']));
  const residue = new Set(RESIDUE_PROBES.flatMap(({ packageId }, i) =>
    one(s[`RESIDUE${i}`], ['absent', 'present']) === 'present' ? [packageId] : []));
  const migrationCandidate = classifyTarget(descriptor.packageId, installed, residue);
  const uid = one(s.UID), secure = one(s.SECURE, ['0', '1']);
  const debuggable = one(s.DEBUGGABLE, ['0', '1']), su = one(s.SU, ['absent', 'present']);
  let rootMode;
  if (uid === '0') rootMode = 'root_adbd';
  else if (uid === '2000' && su === 'present') rootMode = 'root_su';
  else if (uid === '2000' && secure === '1' && debuggable === '0' && su === 'absent') rootMode = 'rootless';
  else fail('root_state_ambiguous');
  if (rootMode === 'root_adbd' && readable.includes('unreadable')) fail('root_state_ambiguous');
  if (androidSdk < descriptor.minSdk || !descriptor.supportedAbis.includes(primaryAbi)) fail('target_incompatible');
  return Object.freeze({ model, serial, primaryAbi, androidSdk, rootMode, migrationCandidate,
    installationAdmission: rootMode === 'root_su' ? 'delegated_root_proof_required' : 'clean_preflight' });
}
