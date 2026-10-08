import { frontendMessages } from './frontend-localization.mjs';
import { TOKENS_CSS } from './tokens.mjs';
// One look for the whole journey: the Home Assistant page, the installer window
// and the panel's own setup wizard (ha-paneld's info.css) read as one wizard.
// The colours are the app's tokens (src/tokens.mjs, synced by sync-tokens.mjs); the wizard shapes
// mirror info.css. Selectors use both :root and :host so the same text styles a page and a shadow root.

import { WORDMARK_SVG } from './brand.mjs';
export { BRAND_ICON } from './brand.mjs';
export const BRAND_HTML = `<div class="wiz-brand">${WORDMARK_SVG.replace('<svg ', '<svg role="img" aria-label="Panel Assistant" ')}</div>`;

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

export const WIZARD_CSS = `${TOKENS_CSS}
:root,:host{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
*,*::before,*::after{box-sizing:border-box}
.wiz{max-width:520px;margin:0 auto;padding:4px 0 24px;color:var(--text)}
.wiz-brand{display:flex;align-items:center;gap:.28em;font-size:1.3rem;font-weight:700;margin:0 0 14px}
.wiz-brand svg{display:block;height:2.1em;width:auto;max-width:100%}
.wiz-dots{display:flex;gap:14px;justify-content:center;list-style:none;margin:4px 0 14px;padding:0;flex-wrap:wrap}
.wiz-dots li{display:flex;align-items:center;gap:6px;font-size:.8rem;color:var(--dim)}
.wiz-dots li::before{content:"";flex:none;width:.55rem;height:.55rem;border-radius:50%;background:var(--dim);opacity:.35}
.wiz-dots li.current{color:var(--text);font-weight:600}
.wiz-dots li.current::before{background:var(--primary);opacity:1}
.wiz-dots li.done::before{background:var(--ok-dim);opacity:1}
.card{background:var(--card);border:1px solid var(--card-border);border-radius:12px;padding:14px 16px;margin:0 0 14px}
.card h2{margin:-14px -16px 12px;padding:9px 16px;background:var(--card-head);border-bottom:1px solid var(--divider);
  border-radius:12px 12px 0 0;font-size:1.15rem;line-height:1.35;color:var(--text)}
.card p{line-height:1.5;margin:2px 0 14px;color:var(--dim)}
.card p.lead{color:var(--text);font-size:1.05rem}
.card label{display:block;margin:14px 0 5px;font-weight:700;font-size:.92rem;color:var(--accent)}
.card select{display:block;width:100%;font:inherit;font-size:1rem;min-height:42px;padding:8px 10px;margin:0 0 6px;
  background:var(--input-bg2);border:1px solid var(--border2);color:var(--text);border-radius:6px}
button.primary,a.primary{display:block;width:100%;min-height:46px;margin-top:16px;padding:10px 16px;border:1px solid var(--primary);
  border-radius:8px;background:var(--primary);color:var(--primary-text);font:inherit;font-size:1rem;text-align:center;
  text-decoration:none;cursor:pointer}
button.secondary{display:block;width:100%;min-height:42px;margin-top:8px;padding:8px 16px;border:1px solid var(--border-strong);
  border-radius:8px;background:transparent;color:var(--accent);font:inherit;cursor:pointer}
button.primary:disabled,button.secondary:disabled{background:var(--disabled-bg);color:var(--disabled-fg);border-color:var(--divider);cursor:default}
.spinner{width:2.25rem;height:2.25rem;border-radius:50%;margin:.5rem 0 1.25rem;border:.25rem solid var(--divider);
  border-top-color:var(--primary);animation:wiz-spin .9s linear infinite}
@keyframes wiz-spin{to{transform:rotate(360deg)}}
.bar{height:.5rem;border-radius:1rem;background:var(--divider);overflow:hidden;margin:1rem 0 .9rem}
.bar>div{height:100%;width:0;background:var(--primary);transition:width .4s ease}
@media (prefers-reduced-motion:reduce){.spinner{animation-duration:3s}.bar>div{transition:none}}
.error h2{color:var(--crit-fg)}
[hidden]{display:none!important}
`;
