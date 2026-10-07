/** Fixed read-only inventory. A clean result is not permission to install. */
export const MAX_PREFLIGHT_BYTES = 32 * 1024;
import { ACCEPTED_PACKAGE_IDS } from './app-identity.mjs';
import { fullMatch } from './shared.mjs';
import { decodeLines, frameProgram, readSections } from './shell-session.mjs';

const BASES = ['/data/user/0', '/data/data', '/data/user_de/0'];
// Either accepted application's data makes a target unclean.
export const RESIDUE_PROBES = Object.freeze(ACCEPTED_PACKAGE_IDS.flatMap(
  packageId => BASES.map(base => Object.freeze({ packageId, path: `${base}/${packageId}` }))));
// Identity and root observation, shared verbatim by the USB posture program.
export const IDENTITY_SECTIONS = Object.freeze([
  ['MODEL', 'getprop ro.product.model'], ['SERIAL', 'getprop ro.serialno'],
  ['ABI', 'getprop ro.product.cpu.abi'], ['SDK', 'getprop ro.build.version.sdk'],
  ['UID', 'id -u'], ['SECURE', 'getprop ro.secure'],
  ['DEBUGGABLE', 'getprop ro.debuggable'],
  ['SU', 'if command -v su >/dev/null 2>&1; then echo present; ' +
    'else hapaneld_su_status=$?; if [ "$hapaneld_su_status" -eq 1 ]; then echo absent; ' +
    'else echo abnormal; fi; fi'],
]);
const SECTIONS = [...IDENTITY_SECTIONS, ['LIVE', 'pm path android'],
  ...ACCEPTED_PACKAGE_IDS.flatMap((packageId, i) => [
    [`PACKAGE${i}`, `pm path ${packageId}`],
    [`RETAINED${i}`, `pm list packages -u ${packageId}`]]),
  ...BASES.map((path, i) => [`BASE${i}`,
    `if [ -L ${path} ] || [ -d ${path} ]; then if ls -1A ${path} >/dev/null 2>&1; ` +
    'then echo readable; else echo unreadable; fi; else echo unreadable; fi']),
  ...RESIDUE_PROBES.map(({ path }, i) => [`RESIDUE${i}`,
    `if [ -e ${path} ] || [ -L ${path} ]; then echo present; else echo absent; fi`]),
];
const NAMES = SECTIONS.map(([name]) => name);

export class PreflightError extends Error {
  constructor(code) { super(code); this.code = code; }
}
function fail(code = 'target_response_invalid') { throw new PreflightError(code); }
function checkNonce(nonce) {
  if (!fullMatch(/^[0-9a-f]{32}$/, nonce)) fail('invalid_request');
}

/** Existing panels and identity migrations must be orchestrated by PA. */
export function classifyTarget(targetPackageId, installed, residue) {
  if (!ACCEPTED_PACKAGE_IDS.includes(targetPackageId)) fail('invalid_request');
  if (installed.length || residue.size) fail('target_not_clean');
  return false;
}

/** No peer-controlled values, paths or commands enter this shell program. */
export function buildPreflight(nonce) {
  checkNonce(nonce);
  return frameProgram('PREFLIGHT', nonce, SECTIONS);
}

function one(section, allowed, f = fail) {
  if (section.status !== 0 || section.values.length !== 1 ||
      (allowed && !allowed.includes(section.values[0]))) f();
  return section.values[0];
}
export function validIdentity({ model, serial, primaryAbi, androidSdk }) {
  return typeof model === 'string' && model === model.trim() &&
    Array.from(model).length >= 1 && Array.from(model).length <= 128 &&
    !/[\p{C}\p{Zl}\p{Zp}]|[^\S ]/u.test(model) &&
    fullMatch(/^[A-Za-z0-9._:-]{1,128}$/, serial) &&
    fullMatch(/^[A-Za-z0-9_.-]{1,64}$/, primaryAbi) &&
    Number.isSafeInteger(androidSdk) && androidSdk >= 1 && androidSdk <= 100;
}
/** Reads IDENTITY_SECTIONS output; f raises the caller's own error type. */
export function readIdentity(s, f = fail) {
  const sdk = one(s.SDK, null, f);
  const identity = { model: one(s.MODEL, null, f), serial: one(s.SERIAL, null, f),
    primaryAbi: one(s.ABI, null, f), androidSdk: Number(sdk) };
  if (!fullMatch(/^[0-9]{1,3}$/, sdk) || !validIdentity(identity)) f();
  return identity;
}
/** Root SU means presence only: this never executes su or proves UID0. */
export function readRootMode(s, f = fail) {
  const uid = one(s.UID, null, f), secure = one(s.SECURE, ['0', '1'], f);
  const debuggable = one(s.DEBUGGABLE, ['0', '1'], f), su = one(s.SU, ['absent', 'present'], f);
  if (uid === '0') return 'root_adbd';
  if (uid === '2000' && su === 'present') return 'root_su';
  if (uid === '2000' && secure === '1' && debuggable === '0' && su === 'absent') return 'rootless';
  f('root_state_ambiguous');
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
  const s = readSections(decodeLines(body, MAX_PREFLIGHT_BYTES, fail), nonce, 'PREFLIGHT', NAMES, fail);
  const { model, serial, primaryAbi, androidSdk } = readIdentity(s);

  // Existing or retained package wins before root evaluation or any later su proof.
  if (s.LIVE.status !== 0 || !s.LIVE.values.length || !s.LIVE.values.every(isPackagePath)) fail();
  // Either accepted id means this is an existing panel, managed through PA.
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
  classifyTarget(descriptor.packageId, installed, residue);
  const rootMode = readRootMode(s);
  if (rootMode === 'root_adbd' && readable.includes('unreadable')) fail('root_state_ambiguous');
  if (androidSdk < descriptor.minSdk || !descriptor.supportedAbis.includes(primaryAbi)) fail('target_incompatible');
  return Object.freeze({ model, serial, primaryAbi, androidSdk, rootMode,
    installationAdmission: rootMode === 'root_su' ? 'delegated_root_proof_required' : 'clean_preflight' });
}
