import { buildPreflight, parsePreflight, PreflightError } from './preflight.mjs';
import { SU_PREFIXES, buildDelegatedProof, parseDelegatedProof } from './delegated-proof.mjs';
import { ShellSessionError, readShell } from './shell-session.mjs';
import { newNonce } from './shared.mjs';


// Same-connection observation only; every future actuator still needs its own
// fresh identity/custody checks. The guard prevents work after cancellation or
// release selection changes, including between failed dialect attempts.
export async function inspectCleanTarget(adb, descriptor, ensureCurrent = () => {}) {
  async function inventory() {
    ensureCurrent();
    const nonce = newNonce();
    const body = await readShell(adb, buildPreflight(nonce), { timeoutMs: 30000 });
    ensureCurrent();
    return parsePreflight(body, nonce, descriptor);
  }
  const initial = await inventory();
  if (initial.rootMode !== 'root_su') return initial;
  let proved = false;
  let answered = false;
  for (const prefix of SU_PREFIXES) {
    ensureCurrent();
    const nonce = newNonce();
    let body;
    try {
      body = await readShell(adb, buildDelegatedProof(prefix, nonce));
    } catch (error) {
      // Some su dialects wait on stdin for a form they do not take (Magisk's
      // `su 0 '<cmd>'`); the next form may still answer.
      if (error instanceof ShellSessionError && error.code === 'shell_timeout') continue;
      throw error;
    }
    answered = true;
    ensureCurrent();
    if (parseDelegatedProof(body, nonce)) { proved = true; break; }
  }
  // No form answering at all means the panel itself is not answering.
  if (!answered) throw new ShellSessionError('shell_timeout');
  if (!proved) throw new PreflightError('root_state_ambiguous');
  const final = await inventory();
  for (const key of ['model', 'serial', 'primaryAbi', 'androidSdk', 'rootMode']) {
    if (initial[key] !== final[key]) throw new PreflightError('target_changed');
  }
  return Object.freeze({ ...final, installationAdmission: 'clean_preflight', delegatedRootVerified: true });
}
