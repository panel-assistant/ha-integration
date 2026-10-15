import { frontendMessages, formatFrontendMessage } from './frontend-localization.mjs';
import { TOKENS_CSS, COMPONENTS_CSS, ICON_SVG, NAME_SVG } from './tokens.mjs';
// One look for the whole journey: the Home Assistant page, the installer window
// and the panel's own setup wizard read as one wizard. Colours, components (card, pill, buttons,
// the step dots, the wizard column) and the brand mark and name are the app's own, synced by
// sync-tokens.mjs; only layout the app has no rule for is written here. Selectors use both :root
// and :host so the same text styles a page and a shadow root.

export const BRAND_ICON = `data:image/svg+xml,${encodeURIComponent(ICON_SVG)}`;

// The integration's own version and build, which Home Assistant passes in the panel config.
export function versionText(config, language = 'en') {
  const version = config?.version;
  const build = config?.build;
  if (typeof version !== 'string' || !/^[0-9A-Za-z.+-]{1,32}$/.test(version) || !Number.isSafeInteger(build) || build < 0) return '';
  return formatFrontendMessage(frontendMessages('sidebar', language).versionLabel, { version, build });
}

// The app's header lock-up (PageShell.kt brandLockup): the mark, the name drawn inline so it takes
// the text colour, and the version caption beneath, filled in as text by the caller.
const NAME = NAME_SVG.replace(/<!--[^]*?-->\s*/g, '').trim()
  .replace('<svg ', '<svg class="brand-name" role="img" aria-label="Panel Assistant" ');
export const BRAND_HTML = `<div class="wiz-brand"><img class="logo" src="${BRAND_ICON}" alt="">`
  + `<span class="brand">${NAME}<small class="brand-version" id="brand-version"></small></span></div>`;

// The four stops a person passes through. The last one is the panel's own setup.
export const JOURNEY = Object.freeze(Object.values(frontendMessages('journey', 'en')));

// Static markup for the first paint; the names are fixed text, never input.
export function journeyHtml(current) {
  return JOURNEY.map((name, index) => index < current ? `<li class="done">${name}</li>`
    : index === current ? `<li class="current" aria-current="step">${name}</li>` : `<li>${name}</li>`).join('');
}

export function renderJourney(list, current, doc = document, language = 'en') {
  const items = Object.values(frontendMessages('journey', language)).map((name, index) => {
    const item = doc.createElement('li');
    item.textContent = name;
    if (index < current) item.className = 'done';
    if (index === current) { item.className = 'current'; item.setAttribute('aria-current', 'step'); }
    return item;
  });
  list.replaceChildren(...items);
}

export const WIZARD_CSS = `${TOKENS_CSS}${COMPONENTS_CSS}
:root,:host{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--text)}
*,*::before,*::after{box-sizing:border-box}
.wiz-brand{display:flex;margin:0 0 14px}
.wiz-brand .brand{flex:0 1 auto;min-width:0}
.wiz-brand .brand-name{max-width:100%;height:auto}
.wiz a.wiz-primary{display:flex;align-items:center;justify-content:center;width:100%;min-height:46px;margin-top:16px;font-size:1rem}
.bar{height:.5rem;border-radius:1rem;background:var(--divider);overflow:hidden;margin:1rem 0 .9rem}
.bar>div{height:100%;width:0;background:var(--primary);transition:width .4s ease}
@media (prefers-reduced-motion:reduce){.bar>div{transition:none}}
[hidden]{display:none!important}
`;
