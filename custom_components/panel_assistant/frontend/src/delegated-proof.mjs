/** Fixed read-only delegated-root proof; never elevates an installation command. */
import { RESIDUE_PROBES } from './preflight.mjs';
import { decodeLines, frameProgram, readSections } from './shell-session.mjs';

export const SU_PREFIXES = Object.freeze(['su 0', 'su 0 sh -c', 'su root', 'su root sh -c', 'su -c']);
export const MAX_DELEGATED_BYTES = 32 * 1024;

const BASES = ['/data/user/0', '/data/data', '/data/user_de/0'];
const NAMES = ['UID', ...BASES.map((_, i) => `BASE${i}`),
  ...RESIDUE_PROBES.map((_, i) => `RESIDUE${i}`)];
export class DelegatedProofError extends Error {
  constructor(code) { super(code); this.code = code; }
}
function fail(code = 'target_response_invalid') { throw new DelegatedProofError(code); }
function checkNonce(nonce) {
  if (typeof nonce !== 'string' || !/^[0-9a-f]{32}$/.test(nonce) || nonce.length !== 32) fail('invalid_request');
}

export function buildDelegatedProof(prefix, nonce) {
  checkNonce(nonce);
  if (!SU_PREFIXES.includes(prefix)) fail('invalid_request');
  const sections = [['UID', 'id -u'],
    ...BASES.map((path, i) => [`BASE${i}`,
      `if [ -d ${path} ] && ls -1A ${path} >/dev/null 2>&1; then echo readable; else echo unreadable; fi`]),
    ...RESIDUE_PROBES.map(({ path }, i) => [`RESIDUE${i}`,
      `if [ -e ${path} ] || [ -L ${path} ]; then echo present; else echo absent; fi`]),
  ];
  const payload = frameProgram('SU', nonce, sections);
  return `echo HAPANELD_DELEGATE_BEGIN:${nonce}; ${prefix} '${payload}'; echo HAPANELD_DELEGATE_END:${nonce}:$?`;
}

/**
 * False permits another fixed dialect, never mutation. True proves only this
 * response. Root sees data directories the shell cannot, so this is the
 * authoritative residue reading. Data for either panel application is refused.
 */
export function parseDelegatedProof(body, nonce) {
  checkNonce(nonce);
  const lines = decodeLines(body, MAX_DELEGATED_BYTES, fail);
  if (lines[0] !== `HAPANELD_DELEGATE_BEGIN:${nonce}`) fail();
  const end = new RegExp(`^HAPANELD_DELEGATE_END:${nonce}:([0-9]{1,3})$`).exec(lines.at(-1));
  if (!end) fail();
  // Reject duplicate or injected outer frames even when a dialect reports failure.
  if (lines.slice(1, -1).some(line => line.startsWith('HAPANELD_DELEGATE_'))) fail();
  if (Number(end[1]) !== 0) return false;
  const sections = readSections(lines.slice(1, -1), nonce, 'SU', NAMES, fail);
  const isOne = (section, value) => section.status === 0 && section.values.length === 1 && section.values[0] === value;
  if (!isOne(sections.UID, '0')) fail('root_state_ambiguous');
  for (let i = 0; i < 3; i++) {
    if (!isOne(sections[`BASE${i}`], 'readable')) fail('root_state_ambiguous');
  }
  RESIDUE_PROBES.forEach((_, i) => {
    const section = sections[`RESIDUE${i}`];
    if (isOne(section, 'present')) fail('target_not_clean');
    if (!isOne(section, 'absent')) fail();
  });
  return true;
}
