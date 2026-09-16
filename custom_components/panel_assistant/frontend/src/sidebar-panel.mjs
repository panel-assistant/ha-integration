// The Panel Assistant sidebar: a top menu owned by the integration above a
// same-origin frame of the selected panel's own interface, proxied by Home
// Assistant. Copy is keyed English; locales arrive in a later slice.
export const SIDEBAR_MESSAGES = Object.freeze({
  title: 'Panel Assistant',
  versionLabel: '{version} build {build}',
  menu: 'Open navigation',
  choosePanel: 'Panel',
  addPanel: 'Add panel',
  addPanelShort: 'Add',
  integrationSettings: 'Integration settings',
  device: "This panel's Home Assistant device",
  github: 'GitHub',
  unreachable: 'unreachable',
  not_loaded: 'not loaded',
  opening: 'Opening {panel}…',
  loadingHint: 'Usually takes a few seconds',
  empty: 'No panels are attached yet.',
  failed: 'The panel could not be opened. It will be tried again shortly.',
  admin: 'An administrator must open this page.',
  unreachableBody: 'Home Assistant cannot reach this panel right now.',
  notLoadedBody: 'This panel is not loaded in Home Assistant.',
  closed: 'This panel was closed.',
  frameTitle: 'Panel interface',
});

// Small enough to inline; avoids registering a second static path just for one icon.
const BRAND_ICON = 'data:image/png;base64,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';

// Same Octocat path and repo-link convention as the panel's own on-device shell (PaneldServer.kt).
const REPO_URL = 'https://github.com/panel-assistant/ha-integration';
const GH_ICON = 'M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12';

export const SELECTION_KEY = 'panel_assistant.sidebar.entry';
export const ADD_PANEL_PATH = '/config/integrations/dashboard/add?domain=panel_assistant';
export const SETTINGS_PATH = '/config/integrations/integration/panel_assistant';
const STATES = new Set(['reachable', 'unreachable', 'not_loaded']);
const REFRESH_MS = 30000;

export function parsePanels(value) {
  if (!value || !Array.isArray(value.panels) || value.panels.length > 200) throw Error('invalid panels');
  const ids = new Set();
  return value.panels.map(row => {
    if (!row || typeof row.entry_id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(row.entry_id) || ids.has(row.entry_id) ||
      typeof row.title !== 'string' || row.title.length > 256 || !STATES.has(row.state) ||
      (row.device_id !== null && row.device_id !== undefined && typeof row.device_id !== 'string')) throw Error('invalid panel');
    ids.add(row.entry_id);
    return { entry_id: row.entry_id, title: row.title, state: row.state, device_id: row.device_id ?? null };
  });
}

// Only the proxy's own root path is ever loaded into the same-origin frame.
export function embedToken(url) {
  return typeof url === 'string' ? url.match(/^\/api\/panel_assistant\/embed\/([A-Za-z0-9_-]{43})\/$/)?.[1] ?? null : null;
}

// The integration's own version and build, which Home Assistant passes in the panel config.
export function versionText(config) {
  const version = config?.version;
  const build = config?.build;
  if (typeof version !== 'string' || !/^[0-9A-Za-z.+-]{1,32}$/.test(version) || !Number.isSafeInteger(build) || build < 0) return '';
  return SIDEBAR_MESSAGES.versionLabel.replace('{version}', version).replace('{build}', String(build));
}

// The selected panel's title, safely interpolated (textContent, never markup) into the loading copy.
export function openingText(title) {
  return SIDEBAR_MESSAGES.opening.replace('{panel}', typeof title === 'string' ? title : '');
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
  #hass; #panel; #narrow = false; #connection; #panels = null; #listState = 'loading'; #listGeneration = 0;
  #signature = ''; #selected = null; #session = null; #timer = null;
  #onReady = () => this.#reconnected();
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    // Only fixed markup is HTML. Copy and panel titles are assigned as text.
    this.shadowRoot.innerHTML = `<style>
      :host{display:block;height:100vh;height:100dvh;overflow:hidden;background:var(--primary-background-color,#fafafa);color:var(--primary-text-color,#212121)}
      [hidden]{display:none!important}
      .root{display:flex;flex-direction:column;height:100%;overflow:hidden}
      header{display:flex;flex-wrap:wrap;flex-shrink:0;align-items:center;gap:8px;min-height:56px;padding:4px 12px 4px 20px;font-size:1rem;background:var(--app-header-background-color,var(--primary-color,#03a9f4));color:var(--app-header-text-color,#fff);border-bottom:1px solid var(--divider-color,#e0e0e0);box-sizing:border-box}
      h1{font-size:1.25rem;font-weight:400;margin:0}
      #icon{width:36px;height:36px;border-radius:8px;flex-shrink:0}
      #version{margin:0 8px 0 0;font-size:.875rem;opacity:.8}
      button,select,a{font:inherit;font-size:1rem;min-height:44px;box-sizing:border-box;border-radius:6px}
      button{display:inline-flex;align-items:center;justify-content:center;min-width:44px;padding:0;color:inherit;background:transparent;border:0;cursor:pointer}
      button svg{width:24px;height:24px;fill:currentColor}
      #menu{background:rgba(255,255,255,.18);border-radius:8px}
      #menu svg{width:26px;height:26px}
      label{display:flex;align-items:center;gap:8px;flex:0 0 auto}
      select{flex:0 0 auto;width:auto;min-width:140px;padding:0 8px;color:#212121;background:#fff;border:1px solid rgba(0,0,0,.15)}
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
      #slot{display:contents}
      #status{margin:0;padding:16px;color:var(--secondary-text-color,#727272)}
      #loading{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;padding:16px;text-align:center;color:var(--secondary-text-color,#727272)}
      .spinner{animation:pa-spin .9s linear infinite}
      @keyframes pa-spin{to{transform:rotate(360deg)}}
      #loading-text{margin:0;font-size:.9375rem;color:var(--primary-text-color,#212121)}
      #loading-hint{margin:4px 0 0;font-size:.8125rem}
      iframe{flex:1;border:0;width:100%;display:block;background:var(--card-background-color,#fff)}
    </style><div class="root"><header>
      <button id="menu" type="button"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z"/></svg></button>
      <img id="icon" src="${BRAND_ICON}" alt="">
      <h1 id="title" data-message="title"></h1><span id="version"></span>
      <label id="picker"><span data-message="choosePanel"></span><select id="panels"></select></label>
      <a id="device"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z"/></svg></a>
      <div id="spacer"></div>
      <a id="github" href="${REPO_URL}" target="_blank" rel="noopener"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${GH_ICON}"/></svg></a>
      <a id="add"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z"/></svg><span id="add-label" data-message="addPanel"></span></a>
      <a id="settings"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12,15.5A3.5,3.5 0 0,1 8.5,12A3.5,3.5 0 0,1 12,8.5A3.5,3.5 0 0,1 15.5,12A3.5,3.5 0 0,1 12,15.5M19.43,12.97C19.47,12.65 19.5,12.33 19.5,12C19.5,11.67 19.47,11.34 19.43,11L21.54,9.37C21.73,9.22 21.78,8.95 21.66,8.73L19.66,5.27C19.54,5.05 19.27,4.96 19.05,5.05L16.56,6.05C16.04,5.66 15.5,5.32 14.87,5.07L14.5,2.42C14.46,2.18 14.25,2 14,2H10C9.75,2 9.54,2.18 9.5,2.42L9.13,5.07C8.5,5.32 7.96,5.66 7.44,6.05L4.95,5.05C4.73,4.96 4.46,5.05 4.34,5.27L2.34,8.73C2.22,8.95 2.27,9.22 2.46,9.37L4.57,11C4.53,11.34 4.5,11.67 4.5,12C4.5,12.33 4.53,12.65 4.57,12.97L2.46,14.63C2.27,14.78 2.22,15.05 2.34,15.27L4.34,18.73C4.46,18.95 4.73,19.03 4.95,18.95L7.44,17.94C7.96,18.34 8.5,18.68 9.13,18.93L9.5,21.58C9.54,21.82 9.75,22 10,22H14C14.25,22 14.46,21.82 14.5,21.58L14.87,18.93C15.5,18.68 16.04,18.34 16.56,17.94L19.05,18.95C19.27,19.03 19.54,18.95 19.66,18.73L21.66,15.27C21.78,15.05 21.73,14.78 21.54,14.63L19.43,12.97Z"/></svg></a>
      <div id="slot"></div>
    </header><p id="status" role="status" aria-live="polite"></p><div id="loading" hidden><svg class="spinner" viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="19" stroke="var(--divider-color,#e0e0e0)" stroke-width="4" fill="none"></circle><circle cx="24" cy="24" r="19" stroke="var(--app-header-background-color,var(--primary-color,#03a9f4))" stroke-width="4" stroke-linecap="round" stroke-dasharray="119.4" stroke-dashoffset="89.5" fill="none"></circle></svg><p id="loading-text" role="status" aria-live="polite"></p><p id="loading-hint" data-message="loadingHint"></p></div><iframe id="frame"></iframe></div>`;
    const root = this.shadowRoot;
    for (const element of root.querySelectorAll('[data-message]')) element.textContent = SIDEBAR_MESSAGES[element.dataset.message];
    const menu = root.querySelector('#menu');
    menu.setAttribute('aria-label', SIDEBAR_MESSAGES.menu);
    menu.hidden = true;
    menu.addEventListener('click', () => this.dispatchEvent(new CustomEvent('hass-toggle-menu', { bubbles: true, composed: true })));
    root.querySelector('#frame').setAttribute('title', SIDEBAR_MESSAGES.frameTitle);
    const settings = root.querySelector('#settings');
    settings.setAttribute('aria-label', SIDEBAR_MESSAGES.integrationSettings);
    settings.setAttribute('title', SIDEBAR_MESSAGES.integrationSettings);
    const github = root.querySelector('#github');
    github.setAttribute('aria-label', SIDEBAR_MESSAGES.github);
    github.setAttribute('title', SIDEBAR_MESSAGES.github);
    const device = root.querySelector('#device');
    device.setAttribute('aria-label', SIDEBAR_MESSAGES.device);
    device.setAttribute('title', SIDEBAR_MESSAGES.device);
    device.addEventListener('click', event => {
      const path = device.getAttribute('href');
      if (!path || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      navigate(path);
    });
    for (const [id, path] of [['add', ADD_PANEL_PATH], ['settings', SETTINGS_PATH]]) {
      const link = root.querySelector(`#${id}`);
      link.setAttribute('href', path);
      link.addEventListener('click', event => {
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
    if (!this.isConnected) return;
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
    this.shadowRoot.querySelector('#version').textContent = versionText(value?.config);
  }
  get narrow() { return this.#narrow; }
  set narrow(value) {
    this.#narrow = value === true;
    const root = this.shadowRoot;
    root.querySelector('#menu').hidden = !this.#narrow;
    root.querySelector('#title').hidden = this.#narrow;
    root.querySelector('#version').hidden = this.#narrow;
    root.querySelector('#github').hidden = this.#narrow;
    root.querySelector('#add-label').textContent = SIDEBAR_MESSAGES[this.#narrow ? 'addPanelShort' : 'addPanel'];
  }

  connectedCallback() {
    clearInterval(this.#timer);
    this.#restart();
    this.#timer = setInterval(() => this.#loadList(), REFRESH_MS);
  }
  disconnectedCallback() {
    clearInterval(this.#timer);
    this.#timer = null;
    this.#listGeneration++;
    this.#detach();
    this.#endSession();
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
      const stored = readSelection();
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
    const select = root.querySelector('#panels');
    const panels = this.#admin() ? this.#panels ?? [] : [];
    const signature = JSON.stringify(panels);
    if (signature !== this.#signature) {
      this.#signature = signature;
      select.replaceChildren();
      for (const panel of panels) {
        const option = document.createElement('option');
        option.value = panel.entry_id;
        // Reachable is the expected, silent case; only a problem state earns a suffix.
        option.textContent = panel.state === 'reachable' ? panel.title : `${panel.title} (${SIDEBAR_MESSAGES[panel.state]})`;
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
    else if (this.#listState !== 'ready') key = this.#listState;
    else if (session?.state === 'closed' && session.entryId === panel?.entry_id) key = 'closed';
    else if (panel?.state === 'unreachable') key = 'unreachableBody';
    else if (panel?.state === 'not_loaded') key = 'notLoadedBody';
    else if (session?.state === 'failed') key = session.code === 'not_loaded' ? 'notLoadedBody' : 'failed';
    else if (session?.state !== 'open' && !frame.getAttribute('src')) opening = true;
    const status = root.querySelector('#status');
    status.textContent = key ? SIDEBAR_MESSAGES[key] : '';
    status.hidden = !key;
    const loading = root.querySelector('#loading');
    loading.hidden = !opening;
    if (opening) root.querySelector('#loading-text').textContent = openingText(panel?.title);
    frame.hidden = !frame.getAttribute('src');
  }
}

if (!customElements.get('panel-assistant-sidebar')) customElements.define('panel-assistant-sidebar', PanelAssistantSidebar);
