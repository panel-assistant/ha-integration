import { frontendMessages, frontendLocale, formatFrontendMessage } from './frontend-localization.mjs';
import { startReleaseHandoff } from './ha-release-handoff.mjs';
import { fetchReleaseCatalog } from './release-catalog.mjs';
import { BRAND_HTML, WIZARD_CSS, journeyHtml, renderJourney, versionText } from './wizard-look.mjs';

// Stable keys keep presentation separate from the release-transfer protocol.
// This is the first stop of the same wizard the installer window and the
// panel's own setup continue, so it speaks the same plain one-line language.

export class HaPaneldUsbInstallPanel extends HTMLElement {
  #hass;
  get #messages() { return frontendMessages('haInstall', this.#hass?.language); }
  #panel;
  #transfer;
  // A finished transfer keeps answering a reloaded installer window until this
  // page goes away or a new transfer starts.
  #served;
  #status = 'ready';
  #catalogRequest;
  #catalogState = 'loading';
  #releases = [];
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    // Only fixed markup is HTML. Configuration and translations use textContent.
    this.shadowRoot.innerHTML = `<style>${WIZARD_CSS}
      :host{display:block;min-height:100%;background:var(--bg);padding:24px 16px}
      #release{border:0;padding:0;margin:14px 0;min-width:0}
      #release legend{font-size:.92rem;font-weight:600;margin-bottom:5px}
      #release-choices{max-height:min(240px,40vh)}
    </style><main class="wiz">
      ${BRAND_HTML}
      <ol id="journey" class="wiz-dots">${journeyHtml(0)}</ol>
      <section class="wiz-step"><div class="card">
        <h2 data-message="title"></h2>
        <p class="wiz-lead" data-message="introduction"></p>
        <fieldset id="release" aria-describedby="catalog-status"><legend data-message="release"></legend><div id="release-choices" class="choice-list"></div></fieldset>
        <p id="catalog-status" role="status" aria-live="polite"></p>
        <button id="retry" class="pbtn wiz-secondary" data-message="retry"></button>
        <button id="start" class="pbtn primary wiz-primary" data-message="start"></button>
        <p id="status" role="status" aria-live="polite"></p>
        <button id="cancel" class="pbtn wiz-secondary" data-message="cancel"></button>
      </div></section>
    </main>`;
    for (const element of this.shadowRoot.querySelectorAll('[data-message]')) {
      element.textContent = this.#messages[element.dataset.message];
    }
    this.shadowRoot.querySelector('#start').addEventListener('click', () => this.#start());
    this.shadowRoot.querySelector('#cancel').addEventListener('click', () => this.#transfer?.cancel());
    this.shadowRoot.querySelector('#retry').addEventListener('click', () => this.#loadCatalog());
    this.shadowRoot.querySelector('#release').addEventListener('change', () => this.#render());
    this.#render();
  }
  set hass(value) {
    const changed = this.#hass?.user?.id !== value?.user?.id ||
      this.#hass?.user?.is_admin !== value?.user?.is_admin || this.#hass?.connection !== value?.connection ||
      this.#hass?.auth !== value?.auth;
    const relabel = this.#hass?.language !== value?.language;
    this.#hass = value;
    // Follow Home Assistant's own light or dark choice, not only the device's.
    const dark = value?.themes?.darkMode;
    if (typeof dark === 'boolean') this.setAttribute?.('theme', dark ? 'dark' : 'light');
    if (changed) { this.#transfer?.cancel(); this.#loadCatalog(); }
    // Home Assistant pushes a new object on every state change. Keep the focused choice and selection stable on routine state updates.
    if (changed || relabel) this.#render();
  }
  set panel(value) {
    const changed = this.#panel?.config?.installer_url !== value?.config?.installer_url;
    if (changed) this.#transfer?.cancel();
    this.#panel = value;
    if (changed) this.#loadCatalog();
    this.#render();
  }
  connectedCallback() { this.#loadCatalog(); }
  disconnectedCallback() { this.#transfer?.cancel(); this.#served?.cancel(); this.#served = undefined; this.#catalogRequest?.abort(); this.#catalogRequest = undefined; }
  async #loadCatalog() {
    this.#catalogRequest?.abort();
    this.#catalogRequest = undefined;
    this.#releases = [];
    this.#catalogState = 'loading';
    this.shadowRoot.querySelector('#release-choices').replaceChildren();
    this.#render();
    if (!this.isConnected || this.#hass?.user?.is_admin !== true || !this.#panel?.config?.installer_url) return;
    const request = new AbortController();
    this.#catalogRequest = request;
    try {
      const releases = await fetchReleaseCatalog(this.#hass, { signal: request.signal });
      if (this.#catalogRequest !== request) return;
      this.#releases = releases;
      this.#catalogState = releases.length ? 'ready' : 'empty';
      const choices = this.shadowRoot.querySelector('#release-choices');
      for (const [index, release] of [null, ...releases].entries()) {
        const label = document.createElement('label');
        const input = document.createElement('input');
        input.type = 'radio'; input.name = 'release'; input.value = release?.tag ?? '';
        input.checked = index === 0;
        const text = document.createElement('span');
        label.append(input, text);
        choices.append(label);
      }
    } catch {
      if (this.#catalogRequest !== request) return;
      this.#catalogState = 'catalogError';
    } finally {
      if (this.#catalogRequest === request) { this.#catalogRequest = undefined; this.#render(); }
    }
  }
  #render() {
    const root = this.shadowRoot;
    const language = this.#hass?.language;
    if (this.isConnected) this.setAttribute?.('lang', frontendLocale(language));
    for (const node of root.querySelectorAll('[data-message]')) node.textContent = this.#messages[node.dataset.message];
    root.querySelector('#brand-version').textContent = versionText(this.#panel?.config, language);
    const journey = root.querySelector('#journey');
    journey.setAttribute('aria-label', frontendMessages('installer', language).progress);
    renderJourney(journey, 0, document, language);
    const options = root.querySelector('#release-choices').children;
    if (options.length) {
      options[0].querySelector('span').textContent = this.#messages.choose;
      for (const [index, release] of this.#releases.entries()) {
        const note = release.name ? this.#messages.devBuild : release.prerelease ? this.#messages.testing : '';
        options[index + 1].querySelector('span').textContent = `${release.name ?? release.tag.replace(/^v/, '')}${note ? ` (${note})` : ''}`;
      }
    }
    const allowed = this.#hass?.user?.is_admin === true;
    const configured = typeof this.#panel?.config?.installer_url === 'string' &&
      this.#panel.config.installer_url.length > 0;
    const selection = this.shadowRoot.querySelector('#release input:checked')?.value;
    const selected = selection === '' ? this.#releases[0] : this.#releases.find(release => release.tag === selection);
    this.shadowRoot.querySelector('#start').disabled = !allowed || !configured || Boolean(this.#transfer) || !selected;
    this.shadowRoot.querySelector('#cancel').disabled = !this.#transfer;
    this.shadowRoot.querySelector('#release').disabled = Boolean(this.#transfer) || this.#catalogState !== 'ready';
    const catalog = this.shadowRoot.querySelector('#catalog-status');
    catalog.textContent = allowed && configured && this.#catalogState !== 'ready' ? this.#messages[this.#catalogState] : '';
    catalog.hidden = !catalog.textContent;
    this.shadowRoot.querySelector('#retry').hidden = !allowed || !configured || !['catalogError', 'empty'].includes(this.#catalogState);
    this.shadowRoot.querySelector('#cancel').hidden = !this.#transfer;
    const key = !allowed ? 'admin' : !configured ? 'unavailable' : this.#status;
    const status = this.shadowRoot.querySelector('#status');
    status.textContent = Object.hasOwn(this.#messages, key) ? this.#messages[key] : this.#messages.failed;
    status.hidden = !status.textContent;
  }
  #start() {
    if (this.#transfer || this.#hass?.user?.is_admin !== true) return;
    const selection = this.shadowRoot.querySelector('#release input:checked')?.value;
    const release = selection === '' ? this.#releases[0] : this.#releases.find(item => item.tag === selection);
    if (!release || !this.isConnected) return;
    this.#served?.cancel();
    this.#served = undefined;
    const transfer = startReleaseHandoff(this.#hass, this.#panel?.config?.installer_url, {
      rcTag: selection || null,
      language: frontendLocale(this.#hass?.language),
      onState: state => { this.#status = state; this.#render(); },
    });
    this.#transfer = transfer;
    this.#render();
    void transfer.completion.then(() => {
      if (this.#transfer === transfer) this.#served = transfer;
    }, () => {}).finally(() => {
      if (this.#transfer === transfer) this.#transfer = undefined;
      this.#render();
    });
  }
}

if (!customElements.get('panel-assistant-usb-install')) {
  customElements.define('panel-assistant-usb-install', HaPaneldUsbInstallPanel);
}
