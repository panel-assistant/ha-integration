import {stagingPath, StagingError} from './staging-contract.mjs';
const valid = value => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value) && value.length === 32;
// Only ever a single-linked regular file owned by adb shell or root, at this
// job's exact path, reached through no symlink.
const PARENTS = ['/data', '/data/local', '/data/local/tmp'].map(p => `[ ! -L ${p} ]`);
const ownFile = path => [`[ ! -L ${path} ]`, `[ -f ${path} ]`, `[ "$(stat -c %h ${path})" = 1 ]`,
  `{ [ "$(stat -c %u ${path})" = 0 ] || [ "$(stat -c %u ${path})" = 2000 ]; }`];
export function buildPrefixCleanup(nonce, jobId, observation) {
  if (!valid(nonce) || !Number.isSafeInteger(observation?.size) || observation.size < 0 ||
      observation.size > 67108864 || typeof observation.sha256 !== 'string' ||
      !/^[a-f0-9]{64}$/.test(observation.sha256) || observation.sha256.length !== 64) throw new StagingError('invalid_request');
  const path = stagingPath(jobId);
  // Only a task-scoped temporary file. No recursive removal, overwrite, package
  // mutation or elevated command. Recheck exact content immediately before rm.
  const checks = [...PARENTS, ...ownFile(path), `[ "$(stat -c %f ${path})" = 81a4 ]`,
    `[ "$(stat -c %s ${path})" = ${observation.size} ]`,
    `[ "$(sha256sum ${path})" = '${observation.sha256}  ${path}' ]`];
  return `echo HAPANELD_CLEANUP_BEGIN:${nonce}; if ${checks.join(' && ')}; then rm ${path}; else false; fi; echo HAPANELD_CLEANUP_END:${nonce}:$?`;
}
export function parsePrefixCleanup(body, nonce) {
  if (!valid(nonce) || typeof body !== 'string' || body.length > 1024) throw new StagingError('cleanup_response_invalid');
  const text = body.replaceAll('\r\n', '\n');
  if (text !== `HAPANELD_CLEANUP_BEGIN:${nonce}\nHAPANELD_CLEANUP_END:${nonce}:0\n`) throw new StagingError('cleanup_refused');
  return true;
}
// A job set aside before it finished took its release bytes with it, so its
// staged copy cannot be matched to content. It is removed on the file's own
// shape instead: no mode or content check, because a copy interrupted before
// preparation may still carry the 0666 some adbd builds give pushed files.
// Each answer is reported, never thrown: the orphan collides with nothing.
const SET_ASIDE_RESULTS = ['removed', 'absent', 'symlink', 'not_removable', 'failed'];
export function buildSetAsideRemoval(nonce, jobId) {
  if (!valid(nonce)) throw new StagingError('invalid_request');
  const path = stagingPath(jobId);
  return [`echo HAPANELD_SETASIDE_BEGIN:${nonce}`,
    `if ! { ${PARENTS.join(' && ')}; }; then echo not_removable`,
    `elif [ -L ${path} ]; then echo symlink`,
    `elif [ ! -e ${path} ]; then echo absent`,
    `elif ${[...ownFile(path), `[ "$(stat -c %s ${path})" -le 67108864 ]`].join(' && ')}; then ` +
      `rm ${path}; if [ ! -e ${path} ] && [ ! -L ${path} ]; then echo removed; else echo failed; fi`,
    // The shell service merges stderr into the answer: the checks decide it.
    'else echo not_removable; fi 2>/dev/null',
    `echo HAPANELD_SETASIDE_END:${nonce}:$?`].join('; ');
}
export function parseSetAsideRemoval(body, nonce) {
  if (!valid(nonce) || typeof body !== 'string' || body.length > 1024) throw new StagingError('cleanup_response_invalid');
  const lines = body.replaceAll('\r\n', '\n').split('\n');
  if (lines.length !== 4 || lines[0] !== `HAPANELD_SETASIDE_BEGIN:${nonce}` ||
      lines[2] !== `HAPANELD_SETASIDE_END:${nonce}:0` || lines[3] !== '' ||
      !SET_ASIDE_RESULTS.includes(lines[1])) throw new StagingError('cleanup_response_invalid');
  return lines[1];
}
