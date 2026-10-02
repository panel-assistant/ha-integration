const f = Object.freeze({
  title: "Panel Assistant",
  versionLabel: "{version} build {build}",
  menu: "Open navigation",
  choosePanel: "Panel",
  more: "More options",
  addPanel: "Add panel",
  integrationSettings: "Integration settings",
  device: "This panel's Home Assistant device",
  showDevice: "Show device",
  github: "GitHub",
  unreachable: "unreachable",
  restarting: "Restarting ({reason})",
  not_loaded: "not loaded",
  opening: "Opening {panel}…",
  loadingHint: "Usually takes a few seconds",
  empty: "No panels are attached yet.",
  failed: "The panel could not be opened. It will be tried again shortly.",
  admin: "An administrator must open this page.",
  unreachableBody: "Home Assistant cannot reach this panel right now.",
  notLoadedBody: "This panel is not loaded in Home Assistant.",
  closed: "This panel was closed.",
  frameTitle: "Panel interface",
  picklesStory: "Pickles the panda has escaped and is causing havoc. He’s slow and stubborn, so getting him back may take a moment.",
  unreachableNext: "Check that the panel is powered on and connected to your network.",
  notLoadedNext: "Open Integration settings to check this panel’s connection.",
  failedNext: "Wait a moment while we try again, or choose another panel.",
  closedNext: "Choose another panel, or wait for this panel to reconnect."
}), de = "data:image/png;base64,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", le = "https://github.com/panel-assistant/ha-integration", ce = "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12", K = "panel_assistant.sidebar.entry", he = "/config/integrations/dashboard/add?domain=panel_assistant", ge = "/config/integrations/integration/panel_assistant", ue = /* @__PURE__ */ new Set(["reachable", "unreachable", "not_loaded", "restarting"]), pe = /* @__PURE__ */ new Set(["update", "settings", "recovery", "reboot"]), fe = 5e3, Ae = /* @__PURE__ */ new Set(["current", "added", "updated", "removed"]);
function be(i) {
  if (!i || !Array.isArray(i.panels) || i.panels.length > 200) throw Error("invalid panels");
  const e = /* @__PURE__ */ new Set();
  return i.panels.map((t) => {
    if (!t || typeof t.entry_id != "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(t.entry_id) || e.has(t.entry_id) || typeof t.title != "string" || t.title.length > 256 || !ue.has(t.state) || t.state === "restarting" && !pe.has(t.reason) || t.device_id !== null && t.device_id !== void 0 && typeof t.device_id != "string") throw Error("invalid panel");
    return e.add(t.entry_id), {
      entry_id: t.entry_id,
      title: t.title,
      state: t.state,
      device_id: t.device_id ?? null,
      ...t.state === "restarting" ? { reason: t.reason } : {}
    };
  });
}
function ye(i) {
  return typeof i == "string" ? i.match(/^\/api\/panel_assistant\/embed\/([A-Za-z0-9_-]{43})\/$/)?.[1] ?? null : null;
}
function me(i) {
  const e = i?.version, t = i?.build;
  return typeof e != "string" || !/^[0-9A-Za-z.+-]{1,32}$/.test(e) || !Number.isSafeInteger(t) || t < 0 ? "" : f.versionLabel.replace("{version}", e).replace("{build}", String(t));
}
function Ie(i) {
  return f.opening.replace("{panel}", typeof i == "string" ? i : "");
}
function G(i) {
  history.pushState(null, "", i), window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: !1 } }));
}
function xe() {
  try {
    return localStorage.getItem(K);
  } catch {
    return null;
  }
}
function Me(i) {
  try {
    localStorage.setItem(K, i);
  } catch {
  }
}
class we extends HTMLElement {
  #e;
  #o;
  #t;
  #i = !1;
  #g;
  #n = null;
  #r = "loading";
  #d = 0;
  #h = "";
  #a = null;
  #s = null;
  #p = null;
  #f = null;
  #I = () => this.#L();
  constructor() {
    super(), this.attachShadow({ mode: "open" }), this.shadowRoot.innerHTML = `<style>
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
      #menu,#overflow{position:relative;flex-shrink:0;width:48px;height:48px;border-radius:50%}
      /* The alert dot is Home Assistant's own menu-button dot. */
      #dot{pointer-events:none;position:absolute;top:9px;inset-inline-end:7px;width:12px;height:12px;box-sizing:content-box;background:var(--accent-color,#ff9800);border-radius:50%;border:2px solid var(--app-header-background-color,var(--primary-color,#03a9f4))}
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
      #slot,#more{display:contents}
      .menu-icon,.item-label,#backdrop{display:none}
      #add-label{display:inline}
      #failure{flex:1;min-height:0;overflow:auto;box-sizing:border-box;display:flex;flex-direction:column;align-items:center;justify-content:safe center;gap:12px;padding:20px;text-align:center}
      #pickles{width:180px;max-width:100%;height:180px;object-fit:contain;flex-shrink:0}
      #pickles-story,#next-step{margin:0;max-width:440px;line-height:1.5}
      #next-step{color:var(--secondary-text-color,#727272)}
      #failure-status{margin:0;max-width:440px;line-height:1.5;color:var(--primary-text-color,#212121)}
      #status{margin:0;padding:16px;color:var(--secondary-text-color,#727272)}
      #loading{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;padding:16px;text-align:center;color:var(--secondary-text-color,#727272)}
      .spinner{animation:pa-spin .9s linear infinite}
      @keyframes pa-spin{to{transform:rotate(360deg)}}
      #loading-text{margin:0;font-size:.9375rem;color:var(--primary-text-color,#212121)}
      #loading-hint{margin:4px 0 0;font-size:.8125rem}
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
      <img id="icon" src="${de}" alt="">
      <h1 id="title" data-message="title"></h1><span id="version"></span>
      <label id="picker"><span data-message="choosePanel"></span><select id="panels"></select></label>
      <button id="overflow" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="more"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12,16A2,2 0 0,1 14,18A2,2 0 0,1 12,20A2,2 0 0,1 10,18A2,2 0 0,1 12,16M12,10A2,2 0 0,1 14,12A2,2 0 0,1 12,14A2,2 0 0,1 10,12A2,2 0 0,1 12,10M12,4A2,2 0 0,1 14,6A2,2 0 0,1 12,8A2,2 0 0,1 10,6A2,2 0 0,1 12,4Z"/></svg></button>
      <div id="backdrop"></div>
      <div id="more">
      <a id="device"><svg class="wide-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z"/></svg><svg class="menu-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M19,18H5V6H19M21,4H3C1.89,4 1,4.89 1,6V18A2,2 0 0,0 3,20H21A2,2 0 0,0 23,18V6C23,4.89 22.1,4 21,4Z"/></svg><span class="item-label" data-message="showDevice"></span></a>
      <div id="spacer"></div>
      <a id="github" href="${le}" target="_blank" rel="noopener"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${ce}"/></svg><span class="item-label" data-message="github"></span></a>
      <a id="add"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z"/></svg><span id="add-label" class="item-label" data-message="addPanel"></span></a>
      <a id="settings"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12,15.5A3.5,3.5 0 0,1 8.5,12A3.5,3.5 0 0,1 12,8.5A3.5,3.5 0 0,1 15.5,12A3.5,3.5 0 0,1 12,15.5M19.43,12.97C19.47,12.65 19.5,12.33 19.5,12C19.5,11.67 19.47,11.34 19.43,11L21.54,9.37C21.73,9.22 21.78,8.95 21.66,8.73L19.66,5.27C19.54,5.05 19.27,4.96 19.05,5.05L16.56,6.05C16.04,5.66 15.5,5.32 14.87,5.07L14.5,2.42C14.46,2.18 14.25,2 14,2H10C9.75,2 9.54,2.18 9.5,2.42L9.13,5.07C8.5,5.32 7.96,5.66 7.44,6.05L4.95,5.05C4.73,4.96 4.46,5.05 4.34,5.27L2.34,8.73C2.22,8.95 2.27,9.22 2.46,9.37L4.57,11C4.53,11.34 4.5,11.67 4.5,12C4.5,12.33 4.53,12.65 4.57,12.97L2.46,14.63C2.27,14.78 2.22,15.05 2.34,15.27L4.34,18.73C4.46,18.95 4.73,19.03 4.95,18.95L7.44,17.94C7.96,18.34 8.5,18.68 9.13,18.93L9.5,21.58C9.54,21.82 9.75,22 10,22H14C14.25,22 14.46,21.82 14.5,21.58L14.87,18.93C15.5,18.68 16.04,18.34 16.56,17.94L19.05,18.95C19.27,19.03 19.54,18.95 19.66,18.73L21.66,15.27C21.78,15.05 21.73,14.78 21.54,14.63L19.43,12.97Z"/></svg><span class="item-label" data-message="integrationSettings"></span></a>
      </div>
      <div id="slot"></div>
    </header><div id="failure" hidden><img id="pickles" src="/panel_assistant/usb/pickles.svg" alt=""><p id="pickles-story" data-message="picklesStory"></p><p id="failure-status" role="status" aria-live="polite"></p><p id="next-step"></p></div><p id="status" role="status" aria-live="polite"></p><div id="loading" hidden><svg class="spinner" viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="19" stroke="var(--divider-color,#e0e0e0)" stroke-width="4" fill="none"></circle><circle cx="24" cy="24" r="19" stroke="var(--app-header-background-color,var(--primary-color,#03a9f4))" stroke-width="4" stroke-linecap="round" stroke-dasharray="119.4" stroke-dashoffset="89.5" fill="none"></circle></svg><p id="loading-text" role="status" aria-live="polite"></p><p id="loading-hint" data-message="loadingHint"></p></div><iframe id="frame"></iframe></div>`;
    const e = this.shadowRoot;
    for (const s of e.querySelectorAll("[data-message]")) s.textContent = f[s.dataset.message];
    const t = e.querySelector("#menu");
    t.setAttribute("aria-label", f.menu), t.hidden = !0, t.addEventListener("click", () => this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: !0, composed: !0 })));
    const n = e.querySelector("#overflow");
    n.setAttribute("aria-label", f.more), n.setAttribute("title", f.more), n.hidden = !0, e.querySelector("#backdrop").hidden = !0, e.querySelector("#dot").hidden = !0, n.addEventListener("click", () => this.#u(!this.#x())), e.querySelector("#backdrop").addEventListener("click", () => this.#u(!1)), e.querySelector("#root").addEventListener("keydown", (s) => {
      s.key !== "Escape" || !this.#x() || (this.#u(!1), n.focus?.());
    }), e.querySelector("#frame").setAttribute("title", f.frameTitle);
    const r = e.querySelector("#settings");
    r.setAttribute("aria-label", f.integrationSettings), r.setAttribute("title", f.integrationSettings);
    const a = e.querySelector("#github");
    a.setAttribute("aria-label", f.github), a.setAttribute("title", f.github), a.addEventListener("click", () => this.#u(!1));
    const l = e.querySelector("#device");
    l.setAttribute("aria-label", f.device), l.setAttribute("title", f.device), l.addEventListener("click", (s) => {
      const g = l.getAttribute("href");
      this.#u(!1), !(!g || s.defaultPrevented || s.button !== 0 || s.metaKey || s.ctrlKey || s.shiftKey || s.altKey) && (s.preventDefault(), G(g));
    });
    for (const [s, g] of [["add", he], ["settings", ge]]) {
      const d = e.querySelector(`#${s}`);
      d.setAttribute("href", g), d.addEventListener("click", (c) => {
        this.#u(!1), !(c.defaultPrevented || c.button !== 0 || c.metaKey || c.ctrlKey || c.shiftKey || c.altKey) && (c.preventDefault(), G(g));
      });
    }
    e.querySelector("#panels").addEventListener("change", (s) => this.#v(s.target.value)), this.#c();
  }
  get hass() {
    return this.#e;
  }
  set hass(e) {
    const t = this.#e;
    if (this.#e = e, !!this.isConnected) {
      if (this.#A(), t?.connection !== e?.connection || t?.user?.id !== e?.user?.id || t?.user?.is_admin !== e?.user?.is_admin) {
        this.#w();
        return;
      }
      (t?.language !== e?.language || !!t?.themes?.darkMode != !!e?.themes?.darkMode) && (this.#l(), this.#m());
    }
  }
  get panel() {
    return this.#o;
  }
  set panel(e) {
    this.#o = e, this.shadowRoot.querySelector("#version").textContent = me(e?.config);
  }
  get route() {
    return this.#t;
  }
  set route(e) {
    const t = this.#t?.path;
    this.#t = e, t !== e?.path && this.#v(e?.path?.slice(1));
  }
  get narrow() {
    return this.#i;
  }
  set narrow(e) {
    this.#i = e === !0;
    const t = this.shadowRoot, n = t.querySelector("#root");
    this.#i ? n.setAttribute("data-narrow", "") : n.removeAttribute("data-narrow"), t.querySelector("#menu").hidden = !this.#i, t.querySelector("#overflow").hidden = !this.#i, t.querySelector("#title").hidden = this.#i, t.querySelector("#version").hidden = this.#i;
    const r = t.querySelector("#more");
    this.#i ? r.setAttribute("role", "menu") : r.removeAttribute("role");
    for (const a of ["device", "github", "add", "settings"]) {
      const l = t.querySelector(`#${a}`);
      this.#i ? l.setAttribute("role", "menuitem") : l.removeAttribute("role");
    }
    this.#i || this.#u(!1), this.#A();
  }
  #x() {
    return this.shadowRoot.querySelector("#more").getAttribute("data-open") !== null;
  }
  #u(e) {
    const t = this.shadowRoot, n = t.querySelector("#more"), r = e && this.#i;
    r ? n.setAttribute("data-open", "") : n.removeAttribute("data-open"), t.querySelector("#backdrop").hidden = !r, t.querySelector("#overflow").setAttribute("aria-expanded", String(r));
  }
  // Home Assistant's menu button shows a dot while persistent notifications exist; on a phone this
  // header replaces it, so it keeps the dot from the same subscription.
  #A() {
    const e = this.isConnected && this.#i ? this.#e?.connection : void 0, t = this.#f;
    if (t?.connection === e || (t && (this.#f = null, t.unsubscribe?.then((r) => r()).catch(() => {
    }), this.shadowRoot.querySelector("#dot").hidden = !0), !e?.subscribeMessage)) return;
    const n = { connection: e, notifications: {}, unsubscribe: null };
    this.#f = n, n.unsubscribe = Promise.resolve().then(() => e.subscribeMessage((r) => this.#D(n, r), { type: "persistent_notification/subscribe" })), n.unsubscribe.catch(() => {
    });
  }
  #D(e, t) {
    if (!(this.#f !== e || !Ae.has(t?.type) || !t.notifications || typeof t.notifications != "object")) {
      if (t.type === "current") e.notifications = { ...t.notifications };
      else if (t.type === "removed") for (const n of Object.keys(t.notifications)) delete e.notifications[n];
      else e.notifications = { ...e.notifications, ...t.notifications };
      this.shadowRoot.querySelector("#dot").hidden = Object.keys(e.notifications).length === 0;
    }
  }
  connectedCallback() {
    clearInterval(this.#p), this.#w(), this.#A(), this.#p = setInterval(() => this.#y(), fe);
  }
  disconnectedCallback() {
    clearInterval(this.#p), this.#p = null, this.#d++, this.#M(), this.#l(), this.#A();
  }
  #b() {
    return this.#e?.user?.is_admin === !0;
  }
  #M() {
    this.#g?.removeEventListener?.("ready", this.#I), this.#g = void 0;
  }
  #w() {
    this.#M(), this.#l(), this.#n = null, this.#h = "", this.#r = "loading", this.#b() && this.#e.connection && (this.#g = this.#e.connection, this.#g.addEventListener("ready", this.#I)), this.#y();
  }
  async #y() {
    const e = ++this.#d;
    if (!this.#b()) {
      this.#c();
      return;
    }
    let t;
    try {
      let n;
      try {
        n = await this.#e.callWS({ type: "panel_assistant/embed_panels" });
      } catch (r) {
        if (this.#n) return;
        throw r;
      }
      t = be(n);
    } catch {
      if (e !== this.#d) return;
      this.#n = null, this.#r = "failed", this.#l(), this.#c();
      return;
    }
    if (e === this.#d) {
      if (this.#n = t, this.#r = t.length ? "ready" : "empty", !t.some((n) => n.entry_id === this.#a)) {
        const n = this.#t?.path?.slice(1), r = t.some((a) => a.entry_id === n) ? n : xe();
        this.#a = t.some((a) => a.entry_id === r) ? r : t[0]?.entry_id ?? null;
      }
      this.#m();
    }
  }
  #v(e) {
    !this.#n?.some((t) => t.entry_id === e) || e === this.#a || (this.#a = e, Me(e), this.#l(), this.#m());
  }
  // Opens a session when the selected panel is reachable and none is live for it.
  #m() {
    const e = this.#n?.find((n) => n.entry_id === this.#a), t = this.#s;
    !e || e.state !== "reachable" ? t && !(t.state === "closed" && t.entryId === e?.entry_id) && this.#l() : (!t || t.entryId !== e.entry_id || !["opening", "open"].includes(t.state)) && (this.#l(), this.#E(e.entry_id, null, null)), this.#c();
  }
  #E(e, t, n) {
    const r = this.#e, a = { entryId: e, token: t, url: n, state: "opening", code: null, unsubscribe: null };
    this.#s = a;
    const l = {
      type: "panel_assistant/embed_session",
      entry_id: e,
      language: r.language,
      theme: r.themes?.darkMode ? "dark" : "light",
      ...t ? { resume: t } : {}
    };
    a.unsubscribe = Promise.resolve().then(() => r.connection.subscribeMessage((s) => this.#j(a, s), l, { resubscribe: !1 })), a.unsubscribe.catch((s) => {
      this.#s === a && (a.state = "failed", a.code = s?.code ?? null, this.#C(), this.#c());
    });
  }
  #j(e, t) {
    if (!(this.#s !== e || !t))
      if (t.kind === "opened") {
        const n = ye(t.url);
        if (!n) {
          this.#l(), this.#s = { entryId: e.entryId, state: "failed", code: null, unsubscribe: null }, this.#c();
          return;
        }
        e.token = n, e.url = t.url, e.state = "open";
        const r = this.shadowRoot.querySelector("#frame");
        r.getAttribute("src") !== t.url && r.setAttribute("src", t.url), this.#c();
      } else t.kind === "closed" && (this.#l(), this.#s = { entryId: e.entryId, state: "closed", code: null, unsubscribe: null }, this.#c(), this.#y());
  }
  // The connection came back; subscriptions made with resubscribe:false are gone, and
  // their unsubscribe functions must not be called: command ids restart per socket.
  #L() {
    const e = this.#s;
    !this.isConnected || !e || !["opening", "open"].includes(e.state) || (this.#E(e.entryId, e.token, e.url), this.#c());
  }
  #l() {
    const e = this.#s;
    this.#s = null, e && (e.state = "ended", e.unsubscribe?.then((t) => t()).catch(() => {
    }), this.#C());
  }
  #C() {
    this.shadowRoot.querySelector("#frame").removeAttribute("src");
  }
  #c() {
    const e = this.shadowRoot, t = e.querySelector("#panels"), n = this.#b() ? this.#n ?? [] : [], r = JSON.stringify(n);
    if (r !== this.#h) {
      this.#h = r, t.replaceChildren();
      for (const A of n) {
        const w = document.createElement("option");
        w.value = A.entry_id, w.textContent = A.state === "reachable" ? A.title : A.state === "restarting" ? `${A.title} (${f.restarting.replace("{reason}", A.reason)})` : `${A.title} (${f[A.state]})`, t.append(w);
      }
    }
    t.value = this.#a ?? "", e.querySelector("#picker").hidden = n.length === 0;
    const a = n.find((A) => A.entry_id === this.#a), l = e.querySelector("#device");
    l.hidden = !a?.device_id, a?.device_id && l.setAttribute("href", `/config/devices/device/${encodeURIComponent(a.device_id)}`);
    const s = this.#s, g = e.querySelector("#frame");
    let d = "", c = !1;
    this.#b() ? this.#r !== "ready" ? d = this.#r : s?.state === "closed" && s.entryId === a?.entry_id ? d = "closed" : a?.state === "restarting" ? d = "restarting" : a?.state === "unreachable" ? d = "unreachableBody" : a?.state === "not_loaded" ? d = "notLoadedBody" : s?.state === "failed" ? d = s.code === "not_loaded" ? "notLoadedBody" : "failed" : s?.state !== "open" && !g.getAttribute("src") && (c = !0) : d = "admin";
    const p = e.querySelector("#status");
    p.textContent = d === "restarting" ? f.restarting.replace("{reason}", a.reason) : d ? f[d] : "";
    const b = ["unreachableBody", "notLoadedBody", "failed", "closed"].includes(d);
    e.querySelector("#failure").hidden = !b, e.querySelector("#failure-status").textContent = b ? p.textContent : "", e.querySelector("#next-step").textContent = b ? f[{ unreachableBody: "unreachableNext", notLoadedBody: "notLoadedNext", failed: "failedNext", closed: "closedNext" }[d]] : "", p.hidden = !d || b;
    const y = e.querySelector("#loading");
    y.hidden = !c, c && (e.querySelector("#loading-text").textContent = Ie(a?.title)), g.hidden = !g.getAttribute("src");
  }
}
customElements.get("panel-assistant-sidebar") || customElements.define("panel-assistant-sidebar", we);
const ve = "io.github.maxlyth.hapaneld", $ = "io.panelassistant.android", ee = 64, te = 256 * 1024, Ee = 2147483647, Ce = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/, De = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc[1-9][0-9]*$/, Y = /^build-([1-9][0-9]{0,9})(-successor)?$/, je = /^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$/, N = (i, e) => typeof e == "string" && e.length <= ee && i.exec(e)?.[0] === e, ie = (i) => N(Ce, i), ne = (i) => N(De, i);
function ae(i) {
  if (!N(Y, i)) return null;
  const e = Number(Y.exec(i)[1]);
  return e <= Ee ? e : null;
}
const Q = (i) => ae(i) !== null, Le = (i) => Q(i) ? i.endsWith("-successor") ? $ : ve : null, ke = (i) => N(je, i), Se = (i, e) => `${i} build ${e}`, U = "/api/panel_assistant/usb/release", ze = "/api/panel_assistant/usb/handover", Ne = 35e3, Be = /(?:[0-9]{1,3}\.){3}[0-9]{1,3}/, Te = /[a-z_]{1,48}/, F = 64 * 1024 * 1024, Oe = 1800 * 1e3, Qe = [
  "id",
  "tag",
  "checksum",
  "checksum_signature",
  "descriptor",
  "descriptor_signature",
  "apk_size",
  "apk_sha256"
], Re = ["id", "tag", "feed", "feed_signature", "apk_size", "apk_sha256"], re = 8192, Pe = Math.ceil(te / 3) * 4 + re, j = (i, e) => typeof e == "string" && i.exec(e)?.[0] === e, T = (i, e) => i !== null && typeof i == "object" && !Array.isArray(i) && Object.keys(i).length === e.length && e.every((t) => Object.hasOwn(i, t));
class z extends Error {
  constructor(e) {
    super(e), this.name = "HandoffError", this.code = e;
  }
}
function u(i, e = "invalid_response") {
  if (!i) throw new z(e);
}
function D(i, e, t = !1) {
  u(typeof i == "string" && i.length <= Math.ceil(e / 3) * 4 && j(/(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?/, i));
  const n = atob(i);
  return u(btoa(n) === i && n.length > 0 && (t ? n.length === e : n.length <= e)), Uint8Array.from(n, (r) => r.charCodeAt(0));
}
async function V(i, e, t, n = null) {
  u(i.status === 200 && !i.redirected && i.body);
  const r = i.headers.get("content-length");
  if (r !== null) {
    u(j(/0|[1-9][0-9]*/, r));
    const d = Number(r);
    u(Number.isSafeInteger(d) && d <= e && (n === null || d === n));
  }
  const a = i.body.getReader(), l = () => {
    a.cancel().catch(() => {
    });
  };
  t.addEventListener("abort", l, { once: !0 });
  const s = [];
  let g = 0;
  try {
    for (; ; ) {
      u(!t.aborted, "cancelled");
      const d = await a.read();
      if (u(!t.aborted, "cancelled"), d.done) break;
      g += d.value.byteLength, u(g <= e && (n === null || g <= n)), s.push(d.value);
    }
    return u(g > 0 && (r === null || g === Number(r)) && (n === null || g === n)), new Blob(s);
  } finally {
    t.removeEventListener("abort", l), l(), a.releaseLock();
  }
}
function He(i, e, {
  rcTag: t = null,
  onState: n = () => {
  },
  windowObject: r = window,
  timeoutMs: a = 3e5
} = {}) {
  let l, s;
  const g = new Promise((o, m) => {
    l = o, s = m;
  }), d = new AbortController();
  let c = !1, p, b, y, A, w = !1, R = !1, v, P, H, B = !1;
  const k = () => {
    clearInterval(P), clearTimeout(H), v = void 0, r.removeEventListener("message", q);
  }, L = (o) => {
    try {
      n(o);
    } catch {
    }
  }, E = (o = null) => {
    if (!c) {
      if (c = !0, d.abort(), clearTimeout(b), L(o ?? "verified"), o) {
        k(), s(new z(o));
        return;
      }
      P = setInterval(() => {
        p.closed && k();
      }, 2e3), H = setTimeout(k, Oe), l();
    }
  };
  async function se() {
    try {
      L("preparing"), u(!c, "cancelled");
      const o = await i.fetchWithAuth(U, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(t === null ? {} : { release_candidate: t }),
        redirect: "error",
        signal: d.signal
      });
      u(!c, "cancelled"), u(o.headers.get("content-type")?.split(";")[0].trim() === "application/json");
      const m = Q(t), h = JSON.parse(await (await V(
        o,
        m ? Pe : re,
        d.signal
      )).text());
      u(!c, "cancelled"), u(T(h, m ? Re : Qe) && j(/[0-9a-f]{32}/, h.id) && typeof h.tag == "string" && h.tag.length <= ee && (t === null ? ie(h.tag) : h.tag === t) && j(/[0-9a-f]{64}/, h.apk_sha256) && Number.isSafeInteger(h.apk_size) && h.apk_size > 0 && h.apk_size <= F);
      const M = m ? {
        tag: h.tag,
        feed: D(h.feed, te),
        feedSignature: D(h.feed_signature, 256, !0)
      } : {
        tag: h.tag,
        checksum: D(h.checksum, 512),
        checksumSignature: D(h.checksum_signature, 256, !0),
        descriptor: D(h.descriptor, 4096),
        descriptorSignature: D(h.descriptor_signature, 256, !0)
      };
      L("downloading"), u(!c, "cancelled");
      const C = await i.fetchWithAuth(`${U}/${h.id}/apk`, {
        method: "GET",
        redirect: "error",
        signal: d.signal
      });
      u(!c, "cancelled");
      const S = await V(C, F, d.signal, h.apk_size);
      u(!c && !p.closed, "window_closed"), R = !0, v = { type: "ha-paneld/usb-bundle", nonce: A, bundle: M, apk: S }, p.postMessage(v, y), L("verifying");
    } catch (o) {
      E(o instanceof z ? o.code : "delivery_failed");
    }
  }
  async function oe(o) {
    if (B || !c || !v || p.closed || !T(o, ["type", "nonce", "address"]) || !j(Be, o.address)) return;
    B = !0;
    const m = (M, C = {}) => {
      try {
        p.closed || p.postMessage({ type: M, nonce: A, ...C }, y);
      } catch {
      }
    };
    m("ha-paneld/usb-handover-accepted");
    let h;
    try {
      const M = await i.fetchWithAuth(ze, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ address: o.address }),
        redirect: "error",
        signal: AbortSignal.timeout(Ne)
      }), C = await M.json().catch(() => null), S = M.status === 200 ? C?.outcome : C?.error;
      h = j(Te, S) ? S : `http_${M.status}`;
    } catch {
      h = "request_failed";
    } finally {
      B = !1;
    }
    m("ha-paneld/usb-handover-result", { outcome: h });
  }
  function q(o) {
    if (!(o.source !== p || o.origin !== y)) {
      if (o.data?.type === "ha-paneld/usb-handover" && o.data.nonce === A) {
        oe(o.data);
        return;
      }
      if (!(!T(o.data, ["type", "nonce"]) || o.data.nonce !== A)) {
        if (o.data.type === "ha-paneld/usb-ready") {
          !w && !c ? (w = !0, se()) : v && !p.closed && p.postMessage(v, y);
          return;
        }
        c || (o.data.type === "ha-paneld/usb-verified" && R ? E() : o.data.type === "ha-paneld/usb-error" && E("verification_failed"));
      }
    }
  }
  try {
    u(i && typeof i.fetchWithAuth == "function" && (t === null || ne(t) || Q(t)) && Number.isSafeInteger(a) && a > 0 && a <= 3e5, "invalid_request");
    const o = new URL(e);
    u(!o.username && !o.password && !o.hash && (o.protocol === "https:" || o.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(o.hostname)), "invalid_destination"), y = o.origin;
    const m = new Uint8Array(16);
    r.crypto.getRandomValues(m), A = Array.from(m, (h) => h.toString(16).padStart(2, "0")).join(""), o.hash = new URLSearchParams({ ha_origin: r.location.origin, nonce: A, rc: t ?? "" }).toString(), r.addEventListener("message", q), p = r.open(o.href, "_blank"), u(p, "popup_blocked"), b = setTimeout(() => E("timeout"), a), L("waiting");
  } catch (o) {
    E(o instanceof z ? o.code : "invalid_request");
  }
  return { completion: g, cancel: () => {
    E("cancelled"), k();
  } };
}
const W = 30, Z = 500, _ = 128 * 1024, O = (i, e) => i !== null && typeof i == "object" && !Array.isArray(i) && Object.keys(i).length === e.length && e.every((t) => Object.hasOwn(i, t));
function I(i) {
  if (!i) throw new Error("Invalid release catalogue");
}
function qe(i) {
  const e = ae(i.tag), t = typeof i.name == "string" ? i.name.split(" ")[0] : null, n = Le(i.tag) === $ ? " (Panel Assistant)" : "";
  return e !== null && i.prerelease === !0 && ke(t) && i.name === `${Se(t, e)}${n}`;
}
function Ge(i) {
  I(O(i, ["releases"]) && Array.isArray(i.releases) && i.releases.length <= W + Z);
  const e = /* @__PURE__ */ new Set();
  let t = 0, n = 0, r = 0;
  return Object.freeze(i.releases.map((a) => O(a, ["tag", "prerelease", "name"]) ? (I(qe(a) && !e.has(a.tag) && ++r <= Z), e.add(a.tag), Object.freeze({ tag: a.tag, prerelease: !0, name: a.name })) : (I(O(a, ["tag", "prerelease"]) && typeof a.prerelease == "boolean" && (a.prerelease ? ne(a.tag) : ie(a.tag)) && !e.has(a.tag) && ++n <= W), e.add(a.tag), a.prerelease || I(++t <= 1), Object.freeze({ tag: a.tag, prerelease: a.prerelease }))));
}
async function Ye(i, { signal: e, timeoutMs: t = 15e3 } = {}) {
  const n = new AbortController(), r = () => n.abort();
  e?.addEventListener("abort", r, { once: !0 }), e?.aborted && r();
  const a = setTimeout(r, t);
  let l, s;
  const g = new Promise((d, c) => {
    s = () => c(new Error("Release catalogue cancelled"));
  });
  n.signal.addEventListener("abort", s, { once: !0 });
  try {
    return I(!n.signal.aborted), await Promise.race([g, (async () => {
      const d = await i.fetchWithAuth("/api/panel_assistant/usb/releases", {
        method: "GET",
        redirect: "error",
        signal: n.signal
      });
      I(!n.signal.aborted && d.status === 200 && !d.redirected && d.body && d.headers.get("content-type")?.split(";")[0].trim() === "application/json");
      const c = d.headers.get("content-length");
      I(c === null || /^(0|[1-9][0-9]*)$/.exec(c)?.[0] === c && Number(c) <= _), l = d.body.getReader();
      const p = [];
      let b = 0;
      for (; ; ) {
        const y = await l.read();
        if (I(!n.signal.aborted), y.done) break;
        b += y.value.byteLength, I(b <= _), p.push(y.value);
      }
      return I(b > 0 && (c === null || b === Number(c))), Ge(JSON.parse(await new Blob(p).text()));
    })()]);
  } finally {
    clearTimeout(a), e?.removeEventListener("abort", r), n.signal.removeEventListener("abort", s), n.abort(), l && l.cancel().catch(() => {
    });
  }
}
const Ue = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMDgiIGhlaWdodD0iMTA4IiB2aWV3Qm94PSIwIDAgMTA4IDEwOCI+CjxwYXRoIGQ9Ik0yOCwzMiBoNTIgYTQsNCAwIDAgMSA0LDQgdjM3IGE0LDQgMCAwIDEgLTQsNCBoLTUyIGE0LDQgMCAwIDEgLTQsLTQgdi0zNyBhNCw0IDAgMCAxIDQsLTQgeiIgZmlsbD0iIzM3NDc0RiIvPgo8cGF0aCBkPSJNMjksMzUgaDUwIGEyLDIgMCAwIDEgMiwyIHYzNSBhMiwyIDAgMCAxIC0yLDIgaC01MCBhMiwyIDAgMCAxIC0yLC0yIHYtMzUgYTIsMiAwIDAgMSAyLC0yIHoiIGZpbGw9IiMwRTE2MjAiLz4KPGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoNDIuMDAsNDIuNTApIHNjYWxlKDAuMTAwMCkiPgo8cGF0aCBmaWxsPSIjRjJGNEY5IiBkPSJNMjQwIDIyNC44MTNDMjQwIDIzMy4wNjMgMjMzLjI1IDIzOS44MTMgMjI1IDIzOS44MTNIMTVDNi43NSAyMzkuODEzIDAgMjMzLjA2MyAwIDIyNC44MTNWMTM0LjgxM0MwIDEyNi41NjMgNC43NyAxMTUuMDQzIDEwLjYxIDEwOS4yMDNMMTA5LjM5IDEwLjQyM0MxMTUuMjIgNC41OTMwNCAxMjQuNzcgNC41OTMwNCAxMzAuNiAxMC40MjNMMjI5LjM5IDEwOS4yMTNDMjM1LjIyIDExNS4wNDMgMjQwIDEyNi41NzMgMjQwIDEzNC44MjNWMjI0LjgyM1YyMjQuODEzWiIvPgo8cGF0aCBmaWxsPSIjMThCQ0YyIiBkPSJNMjI5LjM5IDEwOS4yMDNMMTMwLjYxIDEwLjQyM0MxMjQuNzggNC41OTMwNCAxMTUuMjMgNC41OTMwNCAxMDkuNCAxMC40MjNMMTAuNjEgMTA5LjIwM0M0Ljc4IDExNS4wMzMgMCAxMjYuNTYzIDAgMTM0LjgxM1YyMjQuODEzQzAgMjMzLjA2MyA2Ljc1IDIzOS44MTMgMTUgMjM5LjgxM0gxMDcuMjdMNjYuNjQgMTk5LjE4M0M2NC41NSAxOTkuOTAzIDYyLjMyIDIwMC4zMTMgNjAgMjAwLjMxM0M0OC43IDIwMC4zMTMgMzkuNSAxOTEuMTEzIDM5LjUgMTc5LjgxM0MzOS41IDE2OC41MTMgNDguNyAxNTkuMzEzIDYwIDE1OS4zMTNDNzEuMyAxNTkuMzEzIDgwLjUgMTY4LjUxMyA4MC41IDE3OS44MTNDODAuNSAxODIuMTQzIDgwLjA5IDE4NC4zNzMgNzkuMzcgMTg2LjQ2M0wxMTEgMjE4LjA5M1YxMDIuMjEzQzEwNC4yIDk4Ljg3MyA5OS41IDkxLjg5MyA5OS41IDgzLjgyM0M5OS41IDcyLjUyMyAxMDguNyA2My4zMjMgMTIwIDYzLjMyM0MxMzEuMyA2My4zMjMgMTQwLjUgNzIuNTIzIDE0MC41IDgzLjgyM0MxNDAuNSA5MS44OTMgMTM1LjggOTguODczIDEyOSAxMDIuMjEzVjE4My40ODNMMTYwLjQ2IDE1Mi4wMjNDMTU5Ljg0IDE1MC4wNjMgMTU5LjUgMTQ3Ljk4MyAxNTkuNSAxNDUuODIzQzE1OS41IDEzNC41MjMgMTY4LjcgMTI1LjMyMyAxODAgMTI1LjMyM0MxOTEuMyAxMjUuMzIzIDIwMC41IDEzNC41MjMgMjAwLjUgMTQ1LjgyM0MyMDAuNSAxNTcuMTIzIDE5MS4zIDE2Ni4zMjMgMTgwIDE2Ni4zMjNDMTc3LjUgMTY2LjMyMyAxNzUuMTIgMTY1Ljg1MyAxNzIuOTEgMTY1LjAzM0wxMjkgMjA4Ljk0M1YyMzkuODIzSDIyNUMyMzMuMjUgMjM5LjgyMyAyNDAgMjMzLjA3MyAyNDAgMjI0LjgyM1YxMzQuODIzQzI0MCAxMjYuNTczIDIzNS4yMyAxMTUuMDUzIDIyOS4zOSAxMDkuMjEzVjEwOS4yMDNaIi8+CjwvZz4KPC9zdmc+Cg==", Fe = Object.freeze(["Version", "Connect", "Install", "Set up"]);
function Ve(i) {
  return Fe.map((e, t) => t < i ? `<li class="done">${e}</li>` : t === i ? `<li class="current" aria-current="step">${e}</li>` : `<li>${e}</li>`).join("");
}
const J = "--bg:#f2f3f5;--card:#fff;--card-head:#e7ebef;--card-border:#d9dde3;--divider:#e4e7ec;--input-bg:#fafbfc;--border:#c4cad2;--border-strong:#b6bec8;--text:#1b2430;--dim:#6a7480;--accent:#1e56a8;--ok:#3f7d49;--bad:#a02c20;--disabled-bg:#e2e5e9;--disabled-fg:#9aa3ad;--shadow:rgba(0,0,0,.18)", X = "--bg:#111;--card:#181818;--card-head:#222;--card-border:#242424;--divider:#2a2a2a;--input-bg:#161616;--border:#383838;--border-strong:#444;--text:#eee;--dim:#888;--accent:#9af;--ok:#8a8;--bad:#ffb3a6;--disabled-bg:#222;--disabled-fg:#666;--shadow:#000", We = `
:root,:host{color-scheme:light dark;${J};--primary:#2557a7;--primary-text:#fff;
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media (prefers-color-scheme:dark){:root,:host{${X}}}
:host([theme=light]){color-scheme:light;${J}}
:host([theme=dark]){color-scheme:dark;${X}}
*,*::before,*::after{box-sizing:border-box}
.wiz{max-width:520px;margin:0 auto;padding:4px 0 24px;color:var(--text)}
.wiz-brand{display:flex;align-items:center;gap:.5em;font-size:1.3rem;font-weight:700;margin:0 0 14px}
.wiz-brand img{width:2.1em;height:2.1em;border-radius:6px;flex:none}
.wiz-dots{display:flex;gap:14px;justify-content:center;list-style:none;margin:4px 0 14px;padding:0;flex-wrap:wrap}
.wiz-dots li{display:flex;align-items:center;gap:6px;font-size:.8rem;color:var(--dim)}
.wiz-dots li::before{content:"";flex:none;width:.55rem;height:.55rem;border-radius:50%;background:var(--dim);opacity:.35}
.wiz-dots li.current{color:var(--text);font-weight:600}
.wiz-dots li.current::before{background:var(--primary);opacity:1}
.wiz-dots li.done::before{background:var(--ok);opacity:1}
.card{background:var(--card);border:1px solid var(--card-border);border-radius:12px;padding:14px 16px;margin:0 0 14px}
.card h2{margin:-14px -16px 12px;padding:9px 16px;background:var(--card-head);border-bottom:1px solid var(--divider);
  border-radius:12px 12px 0 0;font-size:1.15rem;line-height:1.35;color:var(--text)}
.card p{line-height:1.5;margin:2px 0 14px;color:var(--dim)}
.card p.lead{color:var(--text);font-size:1.05rem}
.card label{display:block;margin:14px 0 5px;font-weight:700;font-size:.92rem;color:var(--accent)}
.card select{display:block;width:100%;font:inherit;font-size:1rem;min-height:42px;padding:8px 10px;margin:0 0 6px;
  background:var(--input-bg);border:1px solid var(--border);color:var(--text);border-radius:6px}
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
.error h2{color:var(--bad)}
[hidden]{display:none!important}
`, x = Object.freeze({
  title: "Install ha-paneld on a panel",
  introduction: "Plug the panel into this computer with a USB cable. A new window will find it and install the app.",
  release: "Version",
  loading: "Loading versions…",
  catalogError: "The list of versions couldn’t be loaded.",
  empty: "No versions are available yet. Try again later.",
  choose: "Choose a version",
  recommended: "recommended",
  testing: "test version",
  devBuild: "dev build",
  retry: "Try again",
  start: "Continue",
  cancel: "Cancel",
  ready: "",
  unavailable: "The installer isn’t available. Update Panel Assistant, then try again.",
  admin: "Ask a Home Assistant administrator to install panels.",
  waiting: "Continue in the new window.",
  preparing: "Getting the app ready…",
  downloading: "Getting the app ready…",
  verifying: "Getting the app ready…",
  verified: "Continue in the new window.",
  cancelled: "Cancelled.",
  popup_blocked: "Your browser blocked the new window. Allow pop-ups for this page, then press Continue.",
  invalid_request: "Choose a version first.",
  failed: "That didn’t work. Press Continue to try again."
});
class Ze extends HTMLElement {
  #e;
  #o;
  #t;
  // A finished transfer keeps answering a reloaded installer window until this
  // page goes away or a new transfer starts.
  #i;
  #g = "ready";
  #n;
  #r = "loading";
  #d = [];
  constructor() {
    super(), this.attachShadow({ mode: "open" }), this.shadowRoot.innerHTML = `<style>${We}
      :host{display:block;min-height:100%;background:var(--bg);padding:24px 16px}
      .card p.status{color:var(--text);margin:14px 0 0}
    </style><main class="wiz">
      <div class="wiz-brand"><img src="${Ue}" alt=""><span>ha-paneld</span></div>
      <ol class="wiz-dots" aria-label="Progress">${Ve(0)}</ol>
      <section class="card">
        <h2 data-message="title"></h2>
        <p class="lead" data-message="introduction"></p>
        <label for="release" data-message="release"></label>
        <select id="release" aria-describedby="catalog-status"></select>
        <p id="catalog-status" role="status" aria-live="polite"></p>
        <button id="retry" class="secondary" data-message="retry"></button>
        <button id="start" class="primary" data-message="start"></button>
        <p id="status" class="status" role="status" aria-live="polite"></p>
        <button id="cancel" class="secondary" data-message="cancel"></button>
      </section>
    </main>`;
    for (const e of this.shadowRoot.querySelectorAll("[data-message]"))
      e.textContent = x[e.dataset.message];
    this.shadowRoot.querySelector("#start").addEventListener("click", () => this.#s()), this.shadowRoot.querySelector("#cancel").addEventListener("click", () => this.#t?.cancel()), this.shadowRoot.querySelector("#retry").addEventListener("click", () => this.#h()), this.shadowRoot.querySelector("#release").addEventListener("change", () => this.#a()), this.#a();
  }
  set hass(e) {
    const t = this.#e?.user?.id !== e?.user?.id || this.#e?.user?.is_admin !== e?.user?.is_admin || this.#e?.connection !== e?.connection || this.#e?.auth !== e?.auth;
    this.#e = e;
    const n = e?.themes?.darkMode;
    typeof n == "boolean" && this.setAttribute?.("theme", n ? "dark" : "light"), t && (this.#t?.cancel(), this.#h()), this.#a();
  }
  set panel(e) {
    const t = this.#o?.config?.installer_url !== e?.config?.installer_url;
    t && this.#t?.cancel(), this.#o = e, t && this.#h(), this.#a();
  }
  connectedCallback() {
    this.#h();
  }
  disconnectedCallback() {
    this.#t?.cancel(), this.#i?.cancel(), this.#i = void 0, this.#n?.abort(), this.#n = void 0;
  }
  async #h() {
    if (this.#n?.abort(), this.#n = void 0, this.#d = [], this.#r = "loading", this.shadowRoot.querySelector("#release").replaceChildren(), this.#a(), !this.isConnected || this.#e?.user?.is_admin !== !0 || !this.#o?.config?.installer_url) return;
    const e = new AbortController();
    this.#n = e;
    try {
      const t = await Ye(this.#e, { signal: e.signal });
      if (this.#n !== e) return;
      this.#d = t, this.#r = t.length ? "ready" : "empty";
      const n = this.shadowRoot.querySelector("#release"), r = document.createElement("option");
      r.value = "", r.textContent = x.choose, r.disabled = !0, n.append(r);
      const a = t.find((l) => !l.prerelease)?.tag ?? "";
      for (const l of t) {
        const s = document.createElement("option");
        s.value = l.tag;
        const g = l.tag === a ? x.recommended : l.name ? x.devBuild : l.prerelease ? x.testing : "";
        s.textContent = `${l.name ?? l.tag.replace(/^v/, "")}${g ? ` (${g})` : ""}`, n.append(s);
      }
      n.value = a;
    } catch {
      if (this.#n !== e) return;
      this.#r = "catalogError";
    } finally {
      this.#n === e && (this.#n = void 0, this.#a());
    }
  }
  #a() {
    const e = this.#e?.user?.is_admin === !0, t = typeof this.#o?.config?.installer_url == "string" && this.#o.config.installer_url.length > 0, n = this.#d.find((s) => s.tag === this.shadowRoot.querySelector("#release").value);
    this.shadowRoot.querySelector("#start").disabled = !e || !t || !!this.#t || !n, this.shadowRoot.querySelector("#cancel").disabled = !this.#t, this.shadowRoot.querySelector("#release").disabled = !!this.#t || this.#r !== "ready";
    const r = this.shadowRoot.querySelector("#catalog-status");
    r.textContent = e && t && this.#r !== "ready" ? x[this.#r] : "", r.hidden = !r.textContent, this.shadowRoot.querySelector("#retry").hidden = !e || !t || !["catalogError", "empty"].includes(this.#r), this.shadowRoot.querySelector("#cancel").hidden = !this.#t;
    const a = e ? t ? this.#g : "unavailable" : "admin", l = this.shadowRoot.querySelector("#status");
    l.textContent = Object.hasOwn(x, a) ? x[a] : x.failed, l.hidden = !l.textContent;
  }
  #s() {
    if (this.#t || this.#e?.user?.is_admin !== !0) return;
    const e = this.#d.find((n) => n.tag === this.shadowRoot.querySelector("#release").value);
    if (!e || !this.isConnected) return;
    this.#i?.cancel(), this.#i = void 0;
    const t = He(this.#e, this.#o?.config?.installer_url, {
      rcTag: e.prerelease ? e.tag : null,
      onState: (n) => {
        this.#g = n, this.#a();
      }
    });
    this.#t = t, this.#a(), t.completion.then(() => {
      this.#t === t && (this.#i = t);
    }, () => {
    }).finally(() => {
      this.#t === t && (this.#t = void 0), this.#a();
    });
  }
}
customElements.get("panel-assistant-usb-install") || customElements.define("panel-assistant-usb-install", Ze);
