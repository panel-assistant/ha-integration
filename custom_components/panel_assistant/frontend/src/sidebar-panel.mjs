// The Panel Assistant sidebar: a top menu owned by the integration above a
// same-origin frame of the selected panel's own interface, proxied by Home
// Assistant. Copy is keyed English; locales arrive in a later slice.
export const SIDEBAR_MESSAGES = Object.freeze({
  title: 'Panel Assistant',
  versionLabel: '{version} build {build}',
  menu: 'Open navigation',
  choosePanel: 'Panel',
  addPanel: 'Add panel',
  integrationSettings: 'Integration settings',
  reachable: 'reachable',
  unreachable: 'unreachable',
  not_loaded: 'not loaded',
  loading: 'Loading…',
  empty: 'No panels are attached yet.',
  failed: 'The panel could not be opened. It will be tried again shortly.',
  admin: 'An administrator must open this page.',
  unreachableBody: 'Home Assistant cannot reach this panel right now.',
  notLoadedBody: 'This panel is not loaded in Home Assistant.',
  closed: 'This panel was closed.',
  frameTitle: 'Panel interface',
});

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
      typeof row.title !== 'string' || row.title.length > 256 || !STATES.has(row.state)) throw Error('invalid panel');
    ids.add(row.entry_id);
    return { entry_id: row.entry_id, title: row.title, state: row.state };
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
      :host{display:block;height:100%;background:var(--primary-background-color,#fafafa);color:var(--primary-text-color,#212121)}
      [hidden]{display:none!important}
      .root{display:flex;flex-direction:column;height:100%}
      header{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:4px 12px;background:var(--app-header-background-color,var(--primary-color,#03a9f4));color:var(--app-header-text-color,#fff);border-bottom:1px solid var(--divider-color,#e0e0e0)}
      h1{font-size:1.25rem;font-weight:400;margin:0}
      #version{margin:0 8px 0 0;font-size:.875rem;opacity:.8}
      button,select,a{font:inherit;min-height:44px;min-width:44px;box-sizing:border-box;border-radius:6px}
      button{display:inline-flex;align-items:center;justify-content:center;padding:0;color:inherit;background:transparent;border:0;cursor:pointer}
      button svg{width:24px;height:24px;fill:currentColor}
      label{display:flex;align-items:center;gap:8px;flex:1 1 200px;min-width:0}
      select{flex:1;min-width:0;max-width:100%;padding:0 8px;color:var(--primary-text-color,#212121);background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#e0e0e0)}
      a{display:inline-flex;align-items:center;padding:0 12px;color:inherit;text-decoration:underline}
      #slot{display:contents}
      #status{margin:0;padding:16px;color:var(--secondary-text-color,#727272)}
      iframe{flex:1;border:0;width:100%;display:block;background:var(--card-background-color,#fff)}
    </style><div class="root"><header>
      <button id="menu" type="button"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z"/></svg></button>
      <h1 data-message="title"></h1><span id="version"></span>
      <label id="picker"><span data-message="choosePanel"></span><select id="panels"></select></label>
      <a id="add" data-message="addPanel"></a>
      <a id="settings" data-message="integrationSettings"></a>
      <div id="slot"></div>
    </header><p id="status" role="status" aria-live="polite"></p><iframe id="frame"></iframe></div>`;
    const root = this.shadowRoot;
    for (const element of root.querySelectorAll('[data-message]')) element.textContent = SIDEBAR_MESSAGES[element.dataset.message];
    const menu = root.querySelector('#menu');
    menu.setAttribute('aria-label', SIDEBAR_MESSAGES.menu);
    menu.hidden = true;
    menu.addEventListener('click', () => this.dispatchEvent(new CustomEvent('hass-toggle-menu', { bubbles: true, composed: true })));
    root.querySelector('#frame').setAttribute('title', SIDEBAR_MESSAGES.frameTitle);
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
    this.shadowRoot.querySelector('#menu').hidden = !this.#narrow;
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
        option.textContent = `${panel.title} (${SIDEBAR_MESSAGES[panel.state]})`;
        select.append(option);
      }
    }
    select.value = this.#selected ?? '';
    root.querySelector('#picker').hidden = panels.length === 0;
    const panel = panels.find(row => row.entry_id === this.#selected);
    const session = this.#session;
    const frame = root.querySelector('#frame');
    let key = '';
    if (!this.#admin()) key = 'admin';
    else if (this.#listState !== 'ready') key = this.#listState;
    else if (session?.state === 'closed' && session.entryId === panel?.entry_id) key = 'closed';
    else if (panel?.state === 'unreachable') key = 'unreachableBody';
    else if (panel?.state === 'not_loaded') key = 'notLoadedBody';
    else if (session?.state === 'failed') key = session.code === 'not_loaded' ? 'notLoadedBody' : 'failed';
    else if (session?.state !== 'open' && !frame.getAttribute('src')) key = 'loading';
    const status = root.querySelector('#status');
    status.textContent = key ? SIDEBAR_MESSAGES[key] : '';
    status.hidden = !key;
    frame.hidden = !frame.getAttribute('src');
  }
}

if (!customElements.get('panel-assistant-sidebar')) customElements.define('panel-assistant-sidebar', PanelAssistantSidebar);
