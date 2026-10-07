import { isBuildTag, isRcTag, isStableTag } from './release-identity.mjs';
import { readBoundedResponse } from './ha-release-handoff.mjs';
import { exactKeys } from './shared.mjs';

// GitHub lists at most 30 releases; a signed build feed holds at most 500 builds.
const MAX_GITHUB_CHOICES = 30;
const MAX_FEED_CHOICES = 500;
const MAX_BYTES = 128 * 1024;
function requireValid(value) { if (!value) throw new Error('Invalid release catalogue'); }

// Home Assistant names a feed build (version, build number, app); the
// browser shows that name and checks only its shape.
const MAX_NAME_LENGTH = 128;
const feedChoice = release => isBuildTag(release.tag) && typeof release.prerelease === 'boolean' &&
  typeof release.name === 'string' && release.name.length > 0 && release.name.length <= MAX_NAME_LENGTH;

export function parseReleaseCatalog(value) {
  requireValid(exactKeys(value, ['releases']) && Array.isArray(value.releases) &&
    value.releases.length <= MAX_GITHUB_CHOICES + MAX_FEED_CHOICES);
  const seen = new Set();
  let githubCount = 0, feedCount = 0;
  return Object.freeze(value.releases.map(release => {
    if (exactKeys(release, ['tag', 'prerelease', 'name'])) {
      requireValid(feedChoice(release) && !seen.has(release.tag) && ++feedCount <= MAX_FEED_CHOICES);
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
