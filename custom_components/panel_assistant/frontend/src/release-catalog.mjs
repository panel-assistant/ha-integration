import { buildLabel, buildTagPackageId, buildTagVersionCode, isBuildVersionName, isRcTag, isStableTag } from './release-identity.mjs';
import { SUCCESSOR_PACKAGE_ID } from './app-identity.mjs';
import { readBoundedResponse } from './ha-release-handoff.mjs';
import { exactKeys } from './shared.mjs';

// GitHub lists at most 30 releases; a signed build feed holds at most 500 builds.
const MAX_GITHUB_CHOICES = 30;
const MAX_FEED_CHOICES = 500;
const MAX_BYTES = 128 * 1024;
const SEMANTIC_VERSION = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z][0-9A-Za-z.-]*))?$/;
function requireValid(value) { if (!value) throw new Error('Invalid release catalogue'); }

// A feed build is named by version and build number, with the successor clearly distinguished.
function feedName(release) {
  const code = buildTagVersionCode(release.tag);
  const versionName = typeof release.name === 'string' ? release.name.split(' ')[0] : null;
  const version = typeof versionName === 'string' ? SEMANTIC_VERSION.exec(versionName) : null;
  const prerelease = version?.[4];
  const suffix = buildTagPackageId(release.tag) === SUCCESSOR_PACKAGE_ID ? ' (Panel Assistant)' : '';
  return code !== null && version !== null && version[0] === versionName &&
    typeof release.prerelease === 'boolean' && release.prerelease === Boolean(prerelease) &&
    (!prerelease || prerelease.split('.').every(part => part && !/^0[0-9]+$/.test(part))) &&
    isBuildVersionName(versionName) &&
    release.name === `${buildLabel(versionName, code)}${suffix}`;
}

export function parseReleaseCatalog(value) {
  requireValid(exactKeys(value, ['releases']) && Array.isArray(value.releases) &&
    value.releases.length <= MAX_GITHUB_CHOICES + MAX_FEED_CHOICES);
  const seen = new Set();
  let githubCount = 0, feedCount = 0;
  return Object.freeze(value.releases.map(release => {
    if (exactKeys(release, ['tag', 'prerelease', 'name'])) {
      requireValid(feedName(release) && !seen.has(release.tag) && ++feedCount <= MAX_FEED_CHOICES);
      seen.add(release.tag);
      return Object.freeze({ tag: release.tag, prerelease: release.prerelease, name: release.name });
    }
    requireValid(exactKeys(release, ['tag', 'prerelease']) && typeof release.prerelease === 'boolean' &&
      (release.prerelease ? isRcTag(release.tag) : isStableTag(release.tag)) && !seen.has(release.tag) &&
      ++githubCount <= MAX_GITHUB_CHOICES);
    seen.add(release.tag);
    return Object.freeze({ tag: release.tag, prerelease: release.prerelease });
  }));
}

export async function fetchReleaseCatalog(hass, { signal, timeoutMs = 15000 } = {}) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) abort();
  const timer = setTimeout(abort, timeoutMs);
  let rejectAborted;
  const aborted = new Promise((_, reject) => { rejectAborted = () => reject(new Error('Release catalogue cancelled')); });
  controller.signal.addEventListener('abort', rejectAborted, { once: true });
  try {
    requireValid(!controller.signal.aborted);
    return await Promise.race([aborted, (async () => {
      const response = await hass.fetchWithAuth('/api/panel_assistant/usb/releases', {
        method: 'GET', redirect: 'error', signal: controller.signal,
      });
      requireValid(response.headers.get('content-type')?.split(';')[0].trim() === 'application/json');
      const body = await readBoundedResponse(response, MAX_BYTES, controller.signal);
      return parseReleaseCatalog(JSON.parse(await body.text()));
    })()]);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
    controller.signal.removeEventListener('abort', rejectAborted);
    controller.abort();
  }
}
