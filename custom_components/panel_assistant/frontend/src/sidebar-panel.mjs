import { frontendMessages, frontendLocale, formatFrontendMessage } from './frontend-localization.mjs';
import { TOKENS_CSS, COMPONENTS_CSS } from './tokens.mjs';
import { BRAND_ICON, versionText } from './wizard-look.mjs';
// The Panel Assistant sidebar: a top menu owned by the integration above a
// same-origin frame of the selected panel's own interface, proxied by Home
// Assistant. Copy follows the current Home Assistant user language.


// Same Octocat path and repo-link convention as the panel's own on-device shell (PaneldServer.kt).
const REPO_URL = 'https://github.com/panel-assistant/ha-integration';
const GH_ICON = 'M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12';

export const SELECTION_KEY = 'panel_assistant.sidebar.entry';
export const ADD_PANEL_PATH = '/config/integrations/dashboard/add?domain=panel_assistant';
export const SETTINGS_PATH = '/config/integrations/integration/panel_assistant';
const STATES = new Set(['reachable', 'unreachable', 'not_loaded', 'restarting']);
const REASONS = new Set(['update', 'settings', 'recovery', 'reboot']);
// Include a short app restart notice even when this sidebar was already open.
const REFRESH_MS = 5000;
const NOTIFICATION_EVENTS = new Set(['current', 'added', 'updated', 'removed']);

export function parsePanels(value) {
  if (!value || !Array.isArray(value.panels) || value.panels.length > 200) throw Error('invalid panels');
  const ids = new Set();
  return value.panels.map(row => {
    if (!row || typeof row.entry_id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(row.entry_id) || ids.has(row.entry_id) ||
      typeof row.title !== 'string' || row.title.length > 256 || !STATES.has(row.state) ||
      (row.state === 'restarting' && !REASONS.has(row.reason)) ||
      (row.device_id !== null && row.device_id !== undefined && typeof row.device_id !== 'string')) throw Error('invalid panel');
    ids.add(row.entry_id);
    return { entry_id: row.entry_id, title: row.title, state: row.state, device_id: row.device_id ?? null,
      ...(row.state === 'restarting' ? { reason: row.reason } : {}) };
  });
}

// Only the proxy's own root path is ever loaded into the same-origin frame.
export function embedToken(url) {
  return typeof url === 'string' ? url.match(/^\/api\/panel_assistant\/embed\/([A-Za-z0-9_-]{43})\/$/)?.[1] ?? null : null;
}

export { versionText };

// The selected panel's title, safely interpolated (textContent, never markup) into the loading copy.
export function openingText(title, language = 'en') {
  return formatFrontendMessage(frontendMessages('sidebar', language).opening, { panel: typeof title === 'string' ? title : '' });
}

export function navigate(path) {
  history.pushState(null, '', path);
  window.dispatchEvent(new CustomEvent('location-changed', { detail: { replace: false } }));
}

function readSelection() {
  try { return localStorage.getItem(SELECTION_KEY); } catch { return null; }
}

function writeSelection(entryId) {
  try { localStorage.setItem(SELECTION_KEY, entryId); } catch { /* storage unavailable */ }
}

export class PanelAssistantSidebar extends HTMLElement {
  get #messages() { return frontendMessages('sidebar', this.#hass?.language); }
  #hass; #panel; #route; #narrow = false; #connection; #panels = null; #listState = 'loading'; #listGeneration = 0;
  #signature = ''; #selected = null; #session = null; #timer = null; #alerts = null;
  #onReady = () => this.#reconnected();
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    // Only fixed markup is HTML. Copy and panel titles are assigned as text.
    this.shadowRoot.innerHTML = `<style>${TOKENS_CSS}${COMPONENTS_CSS}
      :host{display:block;height:100vh;height:100dvh;overflow:hidden;background:var(--primary-background-color,#fafafa);color:var(--primary-text-color,#212121)}
      [hidden]{display:none!important}
      .root{display:flex;flex-direction:column;height:100%;overflow:hidden}
      header{display:flex;flex-wrap:wrap;flex-shrink:0;align-items:center;gap:8px;min-height:56px;padding:4px 12px 4px 20px;font-size:1rem;background:var(--app-header-background-color,var(--primary-color,#03a9f4));color:var(--app-header-text-color,#fff);border-bottom:1px solid var(--divider-color,#e0e0e0);box-sizing:border-box}
      h1{font-size:1.25rem;font-weight:400;margin:0}
      #icon{width:48px;height:36px;flex-shrink:0}
      #version{margin:0 8px 0 0;font-size:.875rem;opacity:.8}
      button,select,a{font:inherit;font-size:1rem;min-height:44px;box-sizing:border-box;border-radius:6px}
      button{display:inline-flex;align-items:center;justify-content:center;min-width:44px;padding:0;color:inherit;background:transparent;border:0;cursor:pointer}
      button svg{width:24px;height:24px;fill:currentColor}
      #menu,#overflow{position:relative;flex-shrink:0;width:48px;height:48px;border-radius:50%}
      /* The alert dot is Home Assistant's own menu-button dot. */
      #dot{pointer-events:none;position:absolute;top:9px;inset-inline-end:7px;width:12px;height:12px;box-sizing:content-box;background:var(--accent-color,#ff9800);border-radius:50%;border:2px solid var(--app-header-background-color,var(--primary-color,#03a9f4))}
      label{display:flex;align-items:center;gap:8px;flex:0 0 auto;max-width:100%;min-width:0}
      select{flex:1 1 auto;width:auto;min-width:0;max-width:100%;text-overflow:ellipsis;padding:0 8px;color:#212121;background:#fff;border:1px solid rgba(0,0,0,.15)}
      #spacer{flex:1 1 auto}
      a{display:inline-flex;align-items:center;gap:6px;min-height:44px;padding:0 12px;color:inherit;text-decoration:none}
      a svg{width:20px;height:20px;fill:currentColor}
      #github,#add,#settings,#device{min-height:36px}
      #github{min-width:36px;padding:0;justify-content:center}
      #github svg{width:20px;height:20px}
      #add{background:#fff;color:#0288d1;padding:0 14px}
      #add svg{width:18px;height:18px}
      #settings{min-width:36px;padding:0;justify-content:center}
      #settings svg{width:22px;height:22px}
      #device{min-width:36px;padding:0;justify-content:center}
      #device svg{width:20px;height:20px}
      #slot,#more{display:contents}
      .menu-icon,.item-label,#backdrop{display:none}
      #add-label{display:inline}
      /* Loading and failure fill the pane as one shared card: a pill word, one line, a quiet next step. */
      .state{flex:1;min-height:0;overflow:auto;display:flex;align-items:center;justify-content:safe center;padding:20px}
      .state .card{width:100%;max-width:520px;margin:0;display:flex;flex-direction:column;align-items:flex-start;gap:12px;color:var(--text)}
      .state .card p{margin:0;line-height:1.5}
      .state .title{font-size:1.5rem;font-weight:300;line-height:1.25;overflow-wrap:anywhere}
      .state .dim{color:var(--dim)}
      #pickles{width:180px;max-width:100%;height:180px;object-fit:contain;flex-shrink:0;align-self:center}
      #status{margin:0;padding:16px;color:var(--secondary-text-color,#727272)}
      iframe{flex:1;border:0;width:100%;display:block;background:var(--card-background-color,#fff)}
      /* On a phone the header is one row like Home Assistant's own panels: menu button, picker, and a
         vertical ellipsis that opens the remaining links as a menu. The picker caption stays for screen readers. */
      [data-narrow] header{position:relative;flex-wrap:nowrap;gap:4px;padding:3px 4px}
      [data-narrow] #icon,[data-narrow] #spacer{display:none}
      [data-narrow] #picker{flex:1 1 auto;min-width:0}
      [data-narrow] #picker>span{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
      [data-narrow] select{flex:1 1 auto;width:100%;min-width:0;text-overflow:ellipsis}
      [data-narrow] #more{display:none}
      [data-narrow] #more[data-open]{display:flex;flex-direction:column;position:absolute;z-index:2;top:calc(100% - 4px);inset-inline-end:4px;min-width:200px;max-width:calc(100% - 8px);padding:8px 0;box-sizing:border-box;background:var(--card-background-color,#fff);color:var(--primary-text-color,#212121);border-radius:4px;box-shadow:0 2px 4px -1px rgba(0,0,0,.2),0 4px 5px rgba(0,0,0,.14),0 1px 10px rgba(0,0,0,.12)}
      [data-narrow] #more a{justify-content:flex-start;gap:16px;width:100%;min-height:48px;padding:0 16px;border-radius:0;background:none;color:inherit}
      [data-narrow] #more a:focus-visible,[data-narrow] #more a:hover{background:var(--secondary-background-color,rgba(0,0,0,.06))}
      [data-narrow] #more a svg{width:24px;height:24px;flex-shrink:0;fill:var(--secondary-text-color,#727272)}
      [data-narrow] #more .menu-icon{display:block}
      [data-narrow] #more .wide-icon{display:none}
      [data-narrow] #more .item-label{display:inline}
      [data-narrow] #device{order:1}
      [data-narrow] #add{order:2}
      [data-narrow] #settings{order:3}
      [data-narrow] #github{order:4}
      [data-narrow] #backdrop:not([hidden]){display:block;position:fixed;inset:0;z-index:1}
    </style><div class="root" id="root"><header>
      <button id="menu" type="button"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z"/></svg><span id="dot"></span></button>
      <img id="icon" src="${BRAND_ICON}" alt="">
      <h1 id="title" data-message="title"></h1><span id="version"></span>
      <label id="picker"><span data-message="choosePanel"></span><select id="panels"></select></label>
      <button id="overflow" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="more"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12,16A2,2 0 0,1 14,18A2,2 0 0,1 12,20A2,2 0 0,1 10,18A2,2 0 0,1 12,16M12,10A2,2 0 0,1 14,12A2,2 0 0,1 12,14A2,2 0 0,1 10,12A2,2 0 0,1 12,10M12,4A2,2 0 0,1 14,6A2,2 0 0,1 12,8A2,2 0 0,1 10,6A2,2 0 0,1 12,4Z"/></svg></button>
      <div id="backdrop"></div>
      <div id="more">
      <a id="device"><svg class="wide-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z"/></svg><svg class="menu-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M19,18H5V6H19M21,4H3C1.89,4 1,4.89 1,6V18A2,2 0 0,0 3,20H21A2,2 0 0,0 23,18V6C23,4.89 22.1,4 21,4Z"/></svg><span class="item-label" data-message="showDevice"></span></a>
      <div id="spacer"></div>
      <a id="github" href="${REPO_URL}" target="_blank" rel="noopener"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${GH_ICON}"/></svg><span class="item-label" data-message="github"></span></a>
      <a id="add"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z"/></svg><span id="add-label" class="item-label" data-message="addPanel"></span></a>
      <a id="settings"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12,15.5A3.5,3.5 0 0,1 8.5,12A3.5,3.5 0 0,1 12,8.5A3.5,3.5 0 0,1 15.5,12A3.5,3.5 0 0,1 12,15.5M19.43,12.97C19.47,12.65 19.5,12.33 19.5,12C19.5,11.67 19.47,11.34 19.43,11L21.54,9.37C21.73,9.22 21.78,8.95 21.66,8.73L19.66,5.27C19.54,5.05 19.27,4.96 19.05,5.05L16.56,6.05C16.04,5.66 15.5,5.32 14.87,5.07L14.5,2.42C14.46,2.18 14.25,2 14,2H10C9.75,2 9.54,2.18 9.5,2.42L9.13,5.07C8.5,5.32 7.96,5.66 7.44,6.05L4.95,5.05C4.73,4.96 4.46,5.05 4.34,5.27L2.34,8.73C2.22,8.95 2.27,9.22 2.46,9.37L4.57,11C4.53,11.34 4.5,11.67 4.5,12C4.5,12.33 4.53,12.65 4.57,12.97L2.46,14.63C2.27,14.78 2.22,15.05 2.34,15.27L4.34,18.73C4.46,18.95 4.73,19.03 4.95,18.95L7.44,17.94C7.96,18.34 8.5,18.68 9.13,18.93L9.5,21.58C9.54,21.82 9.75,22 10,22H14C14.25,22 14.46,21.82 14.5,21.58L14.87,18.93C15.5,18.68 16.04,18.34 16.56,17.94L19.05,18.95C19.27,19.03 19.54,18.95 19.66,18.73L21.66,15.27C21.78,15.05 21.73,14.78 21.54,14.63L19.43,12.97Z"/></svg><span class="item-label" data-message="integrationSettings"></span></a>
      </div>
      <div id="slot"></div>
    </header><div id="failure" class="state" hidden><section class="card"><span id="failure-pill" class="pill dot"></span><img id="pickles" src="/panel_assistant/usb/pickles.svg" alt=""><p id="failure-status" class="title" role="status" aria-live="polite"></p><p id="pickles-story" data-message="picklesStory"></p><p id="next-step" class="dim"></p></section></div><p id="status" role="status" aria-live="polite"></p><div id="loading" class="state" hidden><section class="card"><span class="pill dot" data-message="pillOpening"></span><p id="loading-text" class="title" role="status" aria-live="polite"></p><p id="loading-hint" class="wiz-status dim" data-message="loadingHint"></p></section></div><iframe id="frame"></iframe></div>`;
    // Pickles art: https://github.com/maxlyth/pickles. Recopy static/pickles.svg for upstream fixes.
    const root = this.shadowRoot;
    for (const element of root.querySelectorAll('[data-message]')) element.textContent = this.#messages[element.dataset.message];
    const menu = root.querySelector('#menu');
    menu.setAttribute('aria-label', this.#messages.menu);
    menu.hidden = true;
    menu.addEventListener('click', () => this.dispatchEvent(new CustomEvent('hass-toggle-menu', { bubbles: true, composed: true })));
    const overflow = root.querySelector('#overflow');
    overflow.setAttribute('aria-label', this.#messages.more);
    overflow.setAttribute('title', this.#messages.more);
    overflow.hidden = true;
    root.querySelector('#backdrop').hidden = true;
    root.querySelector('#dot').hidden = true;
    overflow.addEventListener('click', () => this.#setMenuOpen(!this.#menuOpen()));
    // The backdrop covers the frame too, so a tap anywhere outside the menu closes it.
    root.querySelector('#backdrop').addEventListener('click', () => this.#setMenuOpen(false));
    root.querySelector('#root').addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !this.#menuOpen()) return;
      this.#setMenuOpen(false);
      overflow.focus?.();
    });
    root.querySelector('#frame').setAttribute('title', this.#messages.frameTitle);
    const settings = root.querySelector('#settings');
    settings.setAttribute('aria-label', this.#messages.integrationSettings);
    settings.setAttribute('title', this.#messages.integrationSettings);
    const github = root.querySelector('#github');
    github.setAttribute('aria-label', this.#messages.github);
    github.setAttribute('title', this.#messages.github);
    github.addEventListener('click', () => this.#setMenuOpen(false));
    const device = root.querySelector('#device');
    device.setAttribute('aria-label', this.#messages.device);
    device.setAttribute('title', this.#messages.device);
    device.addEventListener('click', event => {
      const path = device.getAttribute('href');
      this.#setMenuOpen(false);
      if (!path || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      navigate(path);
    });
    for (const [id, path] of [['add', ADD_PANEL_PATH], ['settings', SETTINGS_PATH]]) {
      const link = root.querySelector(`#${id}`);
      link.setAttribute('href', path);
      link.addEventListener('click', event => {
        this.#setMenuOpen(false);
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        navigate(path);
      });
    }
    root.querySelector('#panels').addEventListener('change', event => this.#choose(event.target.value));
    this.#render();
  }

  get hass() { return this.#hass; }
  set hass(value) {
    const previous = this.#hass;
    this.#hass = value;
    // The shared tokens follow Home Assistant's own light or dark choice, not only the device's.
    const dark = value?.themes?.darkMode;
    if (typeof dark === 'boolean') this.setAttribute?.('theme', dark ? 'dark' : 'light');
    if (!this.isConnected) return;
    this.#syncAlerts();
    if (previous?.connection !== value?.connection || previous?.user?.id !== value?.user?.id ||
      previous?.user?.is_admin !== value?.user?.is_admin) { this.#restart(); return; }
    if (previous?.language !== value?.language || Boolean(previous?.themes?.darkMode) !== Boolean(value?.themes?.darkMode)) {
      this.#endSession();
      this.#reconcile();
    }
  }
  get panel() { return this.#panel; }
  set panel(value) {
    this.#panel = value;
    this.shadowRoot.querySelector('#version').textContent = versionText(value?.config, this.#hass?.language);
  }
  get route() { return this.#route; }
  set route(value) {
    const previous = this.#route?.path;
    this.#route = value;
    if (previous !== value?.path) this.#choose(value?.path?.slice(1));
  }
  get narrow() { return this.#narrow; }
  set narrow(value) {
    this.#narrow = value === true;
    const root = this.shadowRoot;
    const box = root.querySelector('#root');
    if (this.#narrow) box.setAttribute('data-narrow', ''); else box.removeAttribute('data-narrow');
    root.querySelector('#menu').hidden = !this.#narrow;
    root.querySelector('#overflow').hidden = !this.#narrow;
    root.querySelector('#title').hidden = this.#narrow;
    root.querySelector('#version').hidden = this.#narrow;
    // The same links are inline on a wide screen and menu items on a phone.
    const more = root.querySelector('#more');
    if (this.#narrow) more.setAttribute('role', 'menu'); else more.removeAttribute('role');
    for (const id of ['device', 'github', 'add', 'settings']) {
      const link = root.querySelector(`#${id}`);
      if (this.#narrow) link.setAttribute('role', 'menuitem'); else link.removeAttribute('role');
    }
    if (!this.#narrow) this.#setMenuOpen(false);
    this.#syncAlerts();
  }

  #menuOpen() { return this.shadowRoot.querySelector('#more').getAttribute('data-open') !== null; }
  #setMenuOpen(open) {
    const root = this.shadowRoot;
    const more = root.querySelector('#more');
    const show = open && this.#narrow;
    if (show) more.setAttribute('data-open', ''); else more.removeAttribute('data-open');
    root.querySelector('#backdrop').hidden = !show;
    root.querySelector('#overflow').setAttribute('aria-expanded', String(show));
  }

  // Home Assistant's menu button shows a dot while persistent notifications exist; on a phone this
  // header replaces it, so it keeps the dot from the same subscription.
  #syncAlerts() {
    const connection = this.isConnected && this.#narrow ? this.#hass?.connection : undefined;
    const alerts = this.#alerts;
    if (alerts?.connection === connection) return;
    if (alerts) {
      this.#alerts = null;
      alerts.unsubscribe?.then(unsubscribe => unsubscribe()).catch(() => {});
      this.shadowRoot.querySelector('#dot').hidden = true;
    }
    if (!connection?.subscribeMessage) return;
    const next = { connection, notifications: {}, unsubscribe: null };
    this.#alerts = next;
    next.unsubscribe = Promise.resolve()
      .then(() => connection.subscribeMessage(event => this.#onNotifications(next, event), { type: 'persistent_notification/subscribe' }));
    next.unsubscribe.catch(() => {});
  }

  #onNotifications(alerts, event) {
    if (this.#alerts !== alerts || !NOTIFICATION_EVENTS.has(event?.type) || !event.notifications || typeof event.notifications !== 'object') return;
    // `current` is the server's whole snapshot (also after a reconnect resubscribes), so it replaces what is held.
    if (event.type === 'current') alerts.notifications = { ...event.notifications };
    else if (event.type === 'removed') for (const id of Object.keys(event.notifications)) delete alerts.notifications[id];
    else alerts.notifications = { ...alerts.notifications, ...event.notifications };
    this.shadowRoot.querySelector('#dot').hidden = Object.keys(alerts.notifications).length === 0;
  }

  connectedCallback() {
    clearInterval(this.#timer);
    this.#restart();
    this.#syncAlerts();
    this.#timer = setInterval(() => this.#loadList(), REFRESH_MS);
  }
  disconnectedCallback() {
    clearInterval(this.#timer);
    this.#timer = null;
    this.#listGeneration++;
    this.#detach();
    this.#endSession();
    this.#syncAlerts();
  }

  #admin() { return this.#hass?.user?.is_admin === true; }
  #detach() {
    this.#connection?.removeEventListener?.('ready', this.#onReady);
    this.#connection = undefined;
  }
  #restart() {
    this.#detach();
    this.#endSession();
    this.#panels = null;
    this.#signature = '';
    this.#listState = 'loading';
    if (this.#admin() && this.#hass.connection) {
      this.#connection = this.#hass.connection;
      this.#connection.addEventListener('ready', this.#onReady);
    }
    this.#render();
    this.#loadList();
  }

  async #loadList() {
    const generation = ++this.#listGeneration;
    if (!this.#admin()) { this.#render(); return; }
    let panels;
    try {
      let reply;
      try {
        reply = await this.#hass.callWS({ type: 'panel_assistant/embed_panels' });
      } catch (error) {
        // A refresh that fails in transit (a WebSocket outage) keeps the last list and its
        // session, so the reconnect can still resume the frame.
        if (this.#panels) return;
        throw error;
      }
      panels = parsePanels(reply);
    } catch {
      if (generation !== this.#listGeneration) return;
      this.#panels = null;
      this.#listState = 'failed';
      this.#endSession();
      this.#render();
      return;
    }
    if (generation !== this.#listGeneration) return;
    this.#panels = panels;
    this.#listState = panels.length ? 'ready' : 'empty';
    if (!panels.some(panel => panel.entry_id === this.#selected)) {
      const requested = this.#route?.path?.slice(1);
      const stored = panels.some(panel => panel.entry_id === requested) ? requested : readSelection();
      this.#selected = panels.some(panel => panel.entry_id === stored) ? stored : panels[0]?.entry_id ?? null;
    }
    this.#reconcile();
  }

  #choose(entryId) {
    if (!this.#panels?.some(panel => panel.entry_id === entryId) || entryId === this.#selected) return;
    this.#selected = entryId;
    writeSelection(entryId);
    this.#endSession();
    this.#reconcile();
  }

  // Opens a session when the selected panel is reachable and none is live for it.
  #reconcile() {
    const panel = this.#panels?.find(row => row.entry_id === this.#selected);
    const session = this.#session;
    if (!panel || panel.state !== 'reachable') {
      if (session && !(session.state === 'closed' && session.entryId === panel?.entry_id)) this.#endSession();
    } else if (!session || session.entryId !== panel.entry_id || !['opening', 'open'].includes(session.state)) {
      this.#endSession();
      this.#subscribe(panel.entry_id, null, null);
    }
    this.#render();
  }

  #subscribe(entryId, resume, url) {
    const hass = this.#hass;
    const session = { entryId, token: resume, url, state: 'opening', code: null, unsubscribe: null };
    this.#session = session;
    const message = {
      type: 'panel_assistant/embed_session', entry_id: entryId, language: hass.language,
      theme: hass.themes?.darkMode ? 'dark' : 'light', ...(resume ? { resume } : {}),
    };
    session.unsubscribe = Promise.resolve()
      .then(() => hass.connection.subscribeMessage(event => this.#onEvent(session, event), message, { resubscribe: false }));
    session.unsubscribe.catch(error => {
      if (this.#session !== session) return;
      session.state = 'failed';
      session.code = error?.code ?? null;
      this.#clearFrame();
      this.#render();
    });
  }

  #onEvent(session, event) {
    if (this.#session !== session || !event) return;
    if (event.kind === 'opened') {
      const token = embedToken(event.url);
      if (!token) {
        this.#endSession();
        this.#session = { entryId: session.entryId, state: 'failed', code: null, unsubscribe: null };
        this.#render();
        return;
      }
      session.token = token;
      session.url = event.url;
      session.state = 'open';
      const frame = this.shadowRoot.querySelector('#frame');
      if (frame.getAttribute('src') !== event.url) frame.setAttribute('src', event.url);
      this.#render();
    } else if (event.kind === 'closed') {
      // The server has ended this subscription; drop it and let the list decide when to reopen.
      this.#endSession();
      this.#session = { entryId: session.entryId, state: 'closed', code: null, unsubscribe: null };
      this.#render();
      this.#loadList();
    }
  }

  // The connection came back; subscriptions made with resubscribe:false are gone, and
  // their unsubscribe functions must not be called: command ids restart per socket.
  #reconnected() {
    const session = this.#session;
    if (!this.isConnected || !session || !['opening', 'open'].includes(session.state)) return;
    this.#subscribe(session.entryId, session.token, session.url);
    this.#render();
  }

  #endSession() {
    const session = this.#session;
    this.#session = null;
    if (!session) return;
    session.state = 'ended';
    // A failed subscription has nothing to end, and the server has already ended a closed one.
    session.unsubscribe?.then(unsubscribe => unsubscribe()).catch(() => {});
    this.#clearFrame();
  }

  #clearFrame() { this.shadowRoot.querySelector('#frame').removeAttribute('src'); }

  #render() {
    const root = this.shadowRoot;
    const language = this.#hass?.language;
    if (this.isConnected) this.setAttribute?.('lang', frontendLocale(language));
    const messages = this.#messages;
    for (const node of root.querySelectorAll('[data-message]')) node.textContent = messages[node.dataset.message];
    for (const [id, key] of [['menu', 'menu'], ['overflow', 'more'], ['settings', 'integrationSettings'], ['github', 'github'], ['device', 'device'], ['add', 'addPanel']]) {
      root.querySelector(`#${id}`).setAttribute('aria-label', messages[key]);
      root.querySelector(`#${id}`).setAttribute('title', messages[key]);
    }
    root.querySelector('#frame').setAttribute('title', messages.frameTitle);
    root.querySelector('#version').textContent = versionText(this.#panel?.config, language);
    const select = root.querySelector('#panels');
    const panels = this.#admin() ? this.#panels ?? [] : [];
    const signature = JSON.stringify([panels, frontendLocale(language)]);
    if (signature !== this.#signature) {
      this.#signature = signature;
      select.replaceChildren();
      for (const panel of panels) {
        const option = document.createElement('option');
        option.value = panel.entry_id;
        // Reachable is the expected, silent case; only a problem state earns a suffix.
        option.textContent = panel.state === 'reachable' ? panel.title : panel.state === 'restarting'
          ? `${panel.title} · ${formatFrontendMessage(this.#messages.restarting, { reason: frontendMessages('sidebarReason', language)[panel.reason] })}`
          : `${panel.title} (${this.#messages[panel.state]})`;
        select.append(option);
      }
    }
    select.value = this.#selected ?? '';
    root.querySelector('#picker').hidden = panels.length === 0;
    const panel = panels.find(row => row.entry_id === this.#selected);
    const device = root.querySelector('#device');
    device.hidden = !panel?.device_id;
    if (panel?.device_id) device.setAttribute('href', `/config/devices/device/${encodeURIComponent(panel.device_id)}`);
    const session = this.#session;
    const frame = root.querySelector('#frame');
    let key = '';
    let opening = false;
    if (!this.#admin()) key = 'admin';
    else if (this.#listState === 'loading') opening = true;
    else if (this.#listState !== 'ready') key = this.#listState;
    else if (session?.state === 'closed' && session.entryId === panel?.entry_id) key = 'closed';
    else if (panel?.state === 'restarting') key = 'restarting';
    else if (panel?.state === 'unreachable') key = 'unreachableBody';
    else if (panel?.state === 'not_loaded') key = 'notLoadedBody';
    else if (session?.state === 'failed') key = session.code === 'not_loaded' ? 'notLoadedBody' : 'failed';
    else if (session?.state !== 'open' && !frame.getAttribute('src')) opening = true;
    const status = root.querySelector('#status');
    status.textContent = key === 'restarting' ? formatFrontendMessage(this.#messages.restarting, { reason: frontendMessages('sidebarReason', language)[panel.reason] }) : key ? this.#messages[key] : '';
    const failure = ['unreachableBody', 'notLoadedBody', 'failed', 'closed'].includes(key);
    root.querySelector('#failure').hidden = !failure;
    root.querySelector('#failure-status').textContent = failure ? status.textContent : '';
    root.querySelector('#next-step').textContent = failure ? this.#messages[{ unreachableBody: 'unreachableNext', notLoadedBody: 'notLoadedNext', failed: 'failedNext', closed: 'closedNext' }[key]] : '';
    root.querySelector('#failure-pill').textContent = failure ? this.#messages[{ unreachableBody: 'pillUnreachable', notLoadedBody: 'pillNotLoaded', failed: 'pillRetrying', closed: 'pillClosed' }[key]] : '';
    status.hidden = !key || failure;
    const loading = root.querySelector('#loading');
    loading.hidden = !opening;
    const loadingText = root.querySelector('#loading-text');
    loadingText.hidden = !panel;
    if (opening) loadingText.textContent = panel ? openingText(panel.title, language) : '';
    frame.hidden = !frame.getAttribute('src');
  }
}

if (!customElements.get('panel-assistant-sidebar')) customElements.define('panel-assistant-sidebar', PanelAssistantSidebar);
