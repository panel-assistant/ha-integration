import { fullMatch } from './shared.mjs';
import { decodeLines, frameProgram, readSections } from './shell-session.mjs';
import { IDENTITY_SECTIONS, readIdentity, readRootMode, validIdentity } from './preflight.mjs';
/** Read-only identity/root observation: the preflight program without package or residue sections.
 * USB session binding belongs to the caller. */
export const MAX_POSTURE_BYTES = 32 * 1024;
export class PostureError extends Error {
  constructor(code) { super(code); this.code = code; }
}
function fail(code = 'target_response_invalid') { throw new PostureError(code); }
function checkNonce(nonce) {
  if (!fullMatch(/^[0-9a-f]{32}$/, nonce)) fail('invalid_request');
}
const NAMES = IDENTITY_SECTIONS.map(([name]) => name);
export function buildPosture(nonce) {
  checkNonce(nonce);
  return frameProgram('POSTURE', nonce, IDENTITY_SECTIONS);
}

/** Root SU here means presence only: this command never executes su or proves UID0. */
export function parsePosture(body, nonce, expectedTarget) {
  checkNonce(nonce);
  if (!expectedTarget || !validIdentity(expectedTarget) ||
      !['rootless', 'root_adbd', 'root_su'].includes(expectedTarget.rootMode)) fail('invalid_request');
  const observed = parseObservedPosture(body, nonce);
  for (const key of Object.keys(observed)) {
    if (observed[key] !== expectedTarget[key]) fail('target_changed');
  }
  return observed;
}

// Discovery only: not clean-install admission or proof that delegated root works.
export function parseObservedPosture(body, nonce) {
  checkNonce(nonce);
  const s = readSections(decodeLines(body, MAX_POSTURE_BYTES, fail), nonce, 'POSTURE', NAMES, fail);
  return Object.freeze({ ...readIdentity(s, fail), rootMode: readRootMode(s, fail) });
}
