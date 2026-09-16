const I = Object.freeze({
  title: "Panel Assistant",
  versionLabel: "{version} build {build}",
  menu: "Open navigation",
  choosePanel: "Panel",
  addPanel: "Add panel",
  addPanelShort: "Add",
  integrationSettings: "Integration settings",
  reachable: "reachable",
  unreachable: "unreachable",
  not_loaded: "not loaded",
  opening: "Opening {panel}…",
  loadingHint: "Usually takes a few seconds",
  empty: "No panels are attached yet.",
  failed: "The panel could not be opened. It will be tried again shortly.",
  admin: "An administrator must open this page.",
  unreachableBody: "Home Assistant cannot reach this panel right now.",
  notLoadedBody: "This panel is not loaded in Home Assistant.",
  closed: "This panel was closed.",
  frameTitle: "Panel interface"
}), ae = "data:image/png;base64,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", W = "panel_assistant.sidebar.entry", re = "/config/integrations/dashboard/add?domain=panel_assistant", se = "/config/integrations/integration/panel_assistant", oe = /* @__PURE__ */ new Set(["reachable", "unreachable", "not_loaded"]), de = 3e4;
function le(i) {
  if (!i || !Array.isArray(i.panels) || i.panels.length > 200) throw Error("invalid panels");
  const e = /* @__PURE__ */ new Set();
  return i.panels.map((t) => {
    if (!t || typeof t.entry_id != "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(t.entry_id) || e.has(t.entry_id) || typeof t.title != "string" || t.title.length > 256 || !oe.has(t.state)) throw Error("invalid panel");
    return e.add(t.entry_id), { entry_id: t.entry_id, title: t.title, state: t.state };
  });
}
function ce(i) {
  return typeof i == "string" ? i.match(/^\/api\/panel_assistant\/embed\/([A-Za-z0-9_-]{43})\/$/)?.[1] ?? null : null;
}
function he(i) {
  const e = i?.version, t = i?.build;
  return typeof e != "string" || !/^[0-9A-Za-z.+-]{1,32}$/.test(e) || !Number.isSafeInteger(t) || t < 0 ? "" : I.versionLabel.replace("{version}", e).replace("{build}", String(t));
}
function ge(i) {
  return I.opening.replace("{panel}", typeof i == "string" ? i : "");
}
function ue(i) {
  history.pushState(null, "", i), window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: !1 } }));
}
function Ae() {
  try {
    return localStorage.getItem(W);
  } catch {
    return null;
  }
}
function pe(i) {
  try {
    localStorage.setItem(W, i);
  } catch {
  }
}
class fe extends HTMLElement {
  #i;
  #d;
  #e = !1;
  #s;
  #o = null;
  #n = "loading";
  #a = 0;
  #h = "";
  #r = null;
  #t = null;
  #g = null;
  #f = () => this.#w();
  constructor() {
    super(), this.attachShadow({ mode: "open" }), this.shadowRoot.innerHTML = `<style>
      :host{display:block;height:100vh;height:100dvh;background:var(--primary-background-color,#fafafa);color:var(--primary-text-color,#212121)}
      [hidden]{display:none!important}
      .root{display:flex;flex-direction:column;height:100%}
      header{display:flex;flex-wrap:wrap;align-items:center;gap:8px;min-height:56px;padding:4px 12px 4px 20px;background:var(--app-header-background-color,var(--primary-color,#03a9f4));color:var(--app-header-text-color,#fff);border-bottom:1px solid var(--divider-color,#e0e0e0);box-sizing:border-box}
      h1{font-size:1.25rem;font-weight:400;margin:0}
      #icon{width:26px;height:26px;border-radius:6px;flex-shrink:0}
      #version{margin:0 8px 0 0;font-size:.875rem;opacity:.8}
      button,select,a{font:inherit;min-height:44px;box-sizing:border-box;border-radius:6px}
      button{display:inline-flex;align-items:center;justify-content:center;min-width:44px;padding:0;color:inherit;background:transparent;border:0;cursor:pointer}
      button svg{width:24px;height:24px;fill:currentColor}
      #menu{background:rgba(255,255,255,.18);border-radius:8px}
      #menu svg{width:26px;height:26px}
      label{display:flex;align-items:center;gap:8px;flex:0 0 auto}
      select{flex:0 0 auto;width:auto;padding:0 8px;color:var(--primary-text-color,#212121);background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#e0e0e0)}
      #spacer{flex:1 1 auto}
      a{display:inline-flex;align-items:center;gap:6px;min-height:44px;padding:0 12px;color:inherit;text-decoration:none}
      a svg{width:20px;height:20px;fill:currentColor}
      #add{background:var(--card-background-color,#fff);color:var(--app-header-background-color,var(--primary-color,#03a9f4));padding:0 14px}
      #add svg{width:18px;height:18px}
      #settings{min-width:44px;padding:0;justify-content:center}
      #settings svg{width:22px;height:22px}
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
      <img id="icon" src="${ae}" alt="">
      <h1 id="title" data-message="title"></h1><span id="version"></span>
      <label id="picker"><span data-message="choosePanel"></span><select id="panels"></select></label>
      <div id="spacer"></div>
      <a id="add"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z"/></svg><span id="add-label" data-message="addPanel"></span></a>
      <a id="settings"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12,15.5A3.5,3.5 0 0,1 8.5,12A3.5,3.5 0 0,1 12,8.5A3.5,3.5 0 0,1 15.5,12A3.5,3.5 0 0,1 12,15.5M19.43,12.97C19.47,12.65 19.5,12.33 19.5,12C19.5,11.67 19.47,11.34 19.43,11L21.54,9.37C21.73,9.22 21.78,8.95 21.66,8.73L19.66,5.27C19.54,5.05 19.27,4.96 19.05,5.05L16.56,6.05C16.04,5.66 15.5,5.32 14.87,5.07L14.5,2.42C14.46,2.18 14.25,2 14,2H10C9.75,2 9.54,2.18 9.5,2.42L9.13,5.07C8.5,5.32 7.96,5.66 7.44,6.05L4.95,5.05C4.73,4.96 4.46,5.05 4.34,5.27L2.34,8.73C2.22,8.95 2.27,9.22 2.46,9.37L4.57,11C4.53,11.34 4.5,11.67 4.5,12C4.5,12.33 4.53,12.65 4.57,12.97L2.46,14.63C2.27,14.78 2.22,15.05 2.34,15.27L4.34,18.73C4.46,18.95 4.73,19.03 4.95,18.95L7.44,17.94C7.96,18.34 8.5,18.68 9.13,18.93L9.5,21.58C9.54,21.82 9.75,22 10,22H14C14.25,22 14.46,21.82 14.5,21.58L14.87,18.93C15.5,18.68 16.04,18.34 16.56,17.94L19.05,18.95C19.27,19.03 19.54,18.95 19.66,18.73L21.66,15.27C21.78,15.05 21.73,14.78 21.54,14.63L19.43,12.97Z"/></svg></a>
      <div id="slot"></div>
    </header><p id="status" role="status" aria-live="polite"></p><div id="loading" hidden><svg class="spinner" viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="19" stroke="var(--divider-color,#e0e0e0)" stroke-width="4" fill="none"></circle><circle cx="24" cy="24" r="19" stroke="var(--app-header-background-color,var(--primary-color,#03a9f4))" stroke-width="4" stroke-linecap="round" stroke-dasharray="119.4" stroke-dashoffset="89.5" fill="none"></circle></svg><p id="loading-text" role="status" aria-live="polite"></p><p id="loading-hint" data-message="loadingHint"></p></div><iframe id="frame"></iframe></div>`;
    const e = this.shadowRoot;
    for (const a of e.querySelectorAll("[data-message]")) a.textContent = I[a.dataset.message];
    const t = e.querySelector("#menu");
    t.setAttribute("aria-label", I.menu), t.hidden = !0, t.addEventListener("click", () => this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: !0, composed: !0 }))), e.querySelector("#frame").setAttribute("title", I.frameTitle);
    const n = e.querySelector("#settings");
    n.setAttribute("aria-label", I.integrationSettings), n.setAttribute("title", I.integrationSettings);
    for (const [a, r] of [["add", re], ["settings", se]]) {
      const s = e.querySelector(`#${a}`);
      s.setAttribute("href", r), s.addEventListener("click", (d) => {
        d.defaultPrevented || d.button !== 0 || d.metaKey || d.ctrlKey || d.shiftKey || d.altKey || (d.preventDefault(), ue(r));
      });
    }
    e.querySelector("#panels").addEventListener("change", (a) => this.#m(a.target.value)), this.#c();
  }
  get hass() {
    return this.#i;
  }
  set hass(e) {
    const t = this.#i;
    if (this.#i = e, !!this.isConnected) {
      if (t?.connection !== e?.connection || t?.user?.id !== e?.user?.id || t?.user?.is_admin !== e?.user?.is_admin) {
        this.#y();
        return;
      }
      (t?.language !== e?.language || !!t?.themes?.darkMode != !!e?.themes?.darkMode) && (this.#l(), this.#p());
    }
  }
  get panel() {
    return this.#d;
  }
  set panel(e) {
    this.#d = e, this.shadowRoot.querySelector("#version").textContent = he(e?.config);
  }
  get narrow() {
    return this.#e;
  }
  set narrow(e) {
    this.#e = e === !0;
    const t = this.shadowRoot;
    t.querySelector("#menu").hidden = !this.#e, t.querySelector("#title").hidden = this.#e, t.querySelector("#version").hidden = this.#e, t.querySelector("#add-label").textContent = I[this.#e ? "addPanelShort" : "addPanel"];
  }
  connectedCallback() {
    clearInterval(this.#g), this.#y(), this.#g = setInterval(() => this.#A(), de);
  }
  disconnectedCallback() {
    clearInterval(this.#g), this.#g = null, this.#a++, this.#I(), this.#l();
  }
  #u() {
    return this.#i?.user?.is_admin === !0;
  }
  #I() {
    this.#s?.removeEventListener?.("ready", this.#f), this.#s = void 0;
  }
  #y() {
    this.#I(), this.#l(), this.#o = null, this.#h = "", this.#n = "loading", this.#u() && this.#i.connection && (this.#s = this.#i.connection, this.#s.addEventListener("ready", this.#f)), this.#A();
  }
  async #A() {
    const e = ++this.#a;
    if (!this.#u()) {
      this.#c();
      return;
    }
    let t;
    try {
      let n;
      try {
        n = await this.#i.callWS({ type: "panel_assistant/embed_panels" });
      } catch (a) {
        if (this.#o) return;
        throw a;
      }
      t = le(n);
    } catch {
      if (e !== this.#a) return;
      this.#o = null, this.#n = "failed", this.#l(), this.#c();
      return;
    }
    if (e === this.#a) {
      if (this.#o = t, this.#n = t.length ? "ready" : "empty", !t.some((n) => n.entry_id === this.#r)) {
        const n = Ae();
        this.#r = t.some((a) => a.entry_id === n) ? n : t[0]?.entry_id ?? null;
      }
      this.#p();
    }
  }
  #m(e) {
    !this.#o?.some((t) => t.entry_id === e) || e === this.#r || (this.#r = e, pe(e), this.#l(), this.#p());
  }
  // Opens a session when the selected panel is reachable and none is live for it.
  #p() {
    const e = this.#o?.find((n) => n.entry_id === this.#r), t = this.#t;
    !e || e.state !== "reachable" ? t && !(t.state === "closed" && t.entryId === e?.entry_id) && this.#l() : (!t || t.entryId !== e.entry_id || !["opening", "open"].includes(t.state)) && (this.#l(), this.#M(e.entry_id, null, null)), this.#c();
  }
  #M(e, t, n) {
    const a = this.#i, r = { entryId: e, token: t, url: n, state: "opening", code: null, unsubscribe: null };
    this.#t = r;
    const s = {
      type: "panel_assistant/embed_session",
      entry_id: e,
      language: a.language,
      theme: a.themes?.darkMode ? "dark" : "light",
      ...t ? { resume: t } : {}
    };
    r.unsubscribe = Promise.resolve().then(() => a.connection.subscribeMessage((d) => this.#x(r, d), s, { resubscribe: !1 })), r.unsubscribe.catch((d) => {
      this.#t === r && (r.state = "failed", r.code = d?.code ?? null, this.#b(), this.#c());
    });
  }
  #x(e, t) {
    if (!(this.#t !== e || !t))
      if (t.kind === "opened") {
        const n = ce(t.url);
        if (!n) {
          this.#l(), this.#t = { entryId: e.entryId, state: "failed", code: null, unsubscribe: null }, this.#c();
          return;
        }
        e.token = n, e.url = t.url, e.state = "open";
        const a = this.shadowRoot.querySelector("#frame");
        a.getAttribute("src") !== t.url && a.setAttribute("src", t.url), this.#c();
      } else t.kind === "closed" && (this.#l(), this.#t = { entryId: e.entryId, state: "closed", code: null, unsubscribe: null }, this.#c(), this.#A());
  }
  // The connection came back; subscriptions made with resubscribe:false are gone, and
  // their unsubscribe functions must not be called: command ids restart per socket.
  #w() {
    const e = this.#t;
    !this.isConnected || !e || !["opening", "open"].includes(e.state) || (this.#M(e.entryId, e.token, e.url), this.#c());
  }
  #l() {
    const e = this.#t;
    this.#t = null, e && (e.state = "ended", e.unsubscribe?.then((t) => t()).catch(() => {
    }), this.#b());
  }
  #b() {
    this.shadowRoot.querySelector("#frame").removeAttribute("src");
  }
  #c() {
    const e = this.shadowRoot, t = e.querySelector("#panels"), n = this.#u() ? this.#o ?? [] : [], a = JSON.stringify(n);
    if (a !== this.#h) {
      this.#h = a, t.replaceChildren();
      for (const p of n) {
        const f = document.createElement("option");
        f.value = p.entry_id, f.textContent = `${p.title} (${I[p.state]})`, t.append(f);
      }
    }
    t.value = this.#r ?? "", e.querySelector("#picker").hidden = n.length === 0;
    const r = n.find((p) => p.entry_id === this.#r), s = this.#t, d = e.querySelector("#frame");
    let c = "", l = !1;
    this.#u() ? this.#n !== "ready" ? c = this.#n : s?.state === "closed" && s.entryId === r?.entry_id ? c = "closed" : r?.state === "unreachable" ? c = "unreachableBody" : r?.state === "not_loaded" ? c = "notLoadedBody" : s?.state === "failed" ? c = s.code === "not_loaded" ? "notLoadedBody" : "failed" : s?.state !== "open" && !d.getAttribute("src") && (l = !0) : c = "admin";
    const h = e.querySelector("#status");
    h.textContent = c ? I[c] : "", h.hidden = !c;
    const A = e.querySelector("#loading");
    A.hidden = !l, l && (e.querySelector("#loading-text").textContent = ge(r?.title)), d.hidden = !d.getAttribute("src");
  }
}
customElements.get("panel-assistant-sidebar") || customElements.define("panel-assistant-sidebar", fe);
const Z = 64, J = 256 * 1024, Ie = 2147483647, ye = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/, Me = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc[1-9][0-9]*$/, Q = /^build-([1-9][0-9]{0,9})$/, be = /^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$/, L = (i, e) => typeof e == "string" && e.length <= Z && i.exec(e)?.[0] === e, X = (i) => L(ye, i), K = (i) => L(Me, i);
function _(i) {
  if (!L(Q, i)) return null;
  const e = Number(Q.exec(i)[1]);
  return e <= Ie ? e : null;
}
const O = (i) => _(i) !== null, me = (i) => L(be, i), xe = (i, e) => `${i} build ${e}`, P = "/api/panel_assistant/usb/release", R = 64 * 1024 * 1024, we = 1800 * 1e3, Ee = [
  "id",
  "tag",
  "checksum",
  "checksum_signature",
  "descriptor",
  "descriptor_signature",
  "apk_size",
  "apk_sha256"
], Ce = ["id", "tag", "feed", "feed_signature", "apk_size", "apk_sha256"], $ = 8192, De = Math.ceil(J / 3) * 4 + $, j = (i, e) => typeof e == "string" && i.exec(e)?.[0] === e, H = (i, e) => i !== null && typeof i == "object" && !Array.isArray(i) && Object.keys(i).length === e.length && e.every((t) => Object.hasOwn(i, t));
class v extends Error {
  constructor(e) {
    super(e), this.name = "HandoffError", this.code = e;
  }
}
function u(i, e = "invalid_response") {
  if (!i) throw new v(e);
}
function x(i, e, t = !1) {
  u(typeof i == "string" && i.length <= Math.ceil(e / 3) * 4 && j(/(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?/, i));
  const n = atob(i);
  return u(btoa(n) === i && n.length > 0 && (t ? n.length === e : n.length <= e)), Uint8Array.from(n, (a) => a.charCodeAt(0));
}
async function G(i, e, t, n = null) {
  u(i.status === 200 && !i.redirected && i.body);
  const a = i.headers.get("content-length");
  if (a !== null) {
    u(j(/0|[1-9][0-9]*/, a));
    const l = Number(a);
    u(Number.isSafeInteger(l) && l <= e && (n === null || l === n));
  }
  const r = i.body.getReader(), s = () => {
    r.cancel().catch(() => {
    });
  };
  t.addEventListener("abort", s, { once: !0 });
  const d = [];
  let c = 0;
  try {
    for (; ; ) {
      u(!t.aborted, "cancelled");
      const l = await r.read();
      if (u(!t.aborted, "cancelled"), l.done) break;
      c += l.value.byteLength, u(c <= e && (n === null || c <= n)), d.push(l.value);
    }
    return u(c > 0 && (a === null || c === Number(a)) && (n === null || c === n)), new Blob(d);
  } finally {
    t.removeEventListener("abort", s), s(), r.releaseLock();
  }
}
function ve(i, e, {
  rcTag: t = null,
  onState: n = () => {
  },
  windowObject: a = window,
  timeoutMs: r = 3e5
} = {}) {
  let s, d;
  const c = new Promise((o, b) => {
    s = o, d = b;
  }), l = new AbortController();
  let h = !1, A, p, f, C, N = !1, S = !1, w, k, B;
  const D = () => {
    clearInterval(k), clearTimeout(B), w = void 0, a.removeEventListener("message", T);
  }, E = (o) => {
    try {
      n(o);
    } catch {
    }
  }, m = (o = null) => {
    if (!h) {
      if (h = !0, l.abort(), clearTimeout(p), E(o ?? "verified"), o) {
        D(), d(new v(o));
        return;
      }
      k = setInterval(() => {
        A.closed && D();
      }, 2e3), B = setTimeout(D, we), s();
    }
  };
  async function ee() {
    try {
      E("preparing"), u(!h, "cancelled");
      const o = await i.fetchWithAuth(P, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(t === null ? {} : { release_candidate: t }),
        redirect: "error",
        signal: l.signal
      });
      u(!h, "cancelled"), u(o.headers.get("content-type")?.split(";")[0].trim() === "application/json");
      const b = O(t), g = JSON.parse(await (await G(
        o,
        b ? De : $,
        l.signal
      )).text());
      u(!h, "cancelled"), u(H(g, b ? Ce : Ee) && j(/[0-9a-f]{32}/, g.id) && typeof g.tag == "string" && g.tag.length <= Z && (t === null ? X(g.tag) : g.tag === t) && j(/[0-9a-f]{64}/, g.apk_sha256) && Number.isSafeInteger(g.apk_size) && g.apk_size > 0 && g.apk_size <= R);
      const te = b ? {
        tag: g.tag,
        feed: x(g.feed, J),
        feedSignature: x(g.feed_signature, 256, !0)
      } : {
        tag: g.tag,
        checksum: x(g.checksum, 512),
        checksumSignature: x(g.checksum_signature, 256, !0),
        descriptor: x(g.descriptor, 4096),
        descriptorSignature: x(g.descriptor_signature, 256, !0)
      };
      E("downloading"), u(!h, "cancelled");
      const ie = await i.fetchWithAuth(`${P}/${g.id}/apk`, {
        method: "GET",
        redirect: "error",
        signal: l.signal
      });
      u(!h, "cancelled");
      const ne = await G(ie, R, l.signal, g.apk_size);
      u(!h && !A.closed, "window_closed"), S = !0, w = { type: "ha-paneld/usb-bundle", nonce: C, bundle: te, apk: ne }, A.postMessage(w, f), E("verifying");
    } catch (o) {
      m(o instanceof v ? o.code : "delivery_failed");
    }
  }
  function T(o) {
    if (!(o.source !== A || o.origin !== f || !H(o.data, ["type", "nonce"]) || o.data.nonce !== C)) {
      if (o.data.type === "ha-paneld/usb-ready") {
        !N && !h ? (N = !0, ee()) : w && !A.closed && A.postMessage(w, f);
        return;
      }
      h || (o.data.type === "ha-paneld/usb-verified" && S ? m() : o.data.type === "ha-paneld/usb-error" && m("verification_failed"));
    }
  }
  try {
    u(i && typeof i.fetchWithAuth == "function" && (t === null || K(t) || O(t)) && Number.isSafeInteger(r) && r > 0 && r <= 3e5, "invalid_request");
    const o = new URL(e);
    u(!o.username && !o.password && !o.hash && (o.protocol === "https:" || o.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(o.hostname)), "invalid_destination"), f = o.origin;
    const b = new Uint8Array(16);
    a.crypto.getRandomValues(b), C = Array.from(b, (g) => g.toString(16).padStart(2, "0")).join(""), o.hash = new URLSearchParams({ ha_origin: a.location.origin, nonce: C, rc: t ?? "" }).toString(), a.addEventListener("message", T), A = a.open(o.href, "_blank"), u(A, "popup_blocked"), p = setTimeout(() => m("timeout"), r), E("waiting");
  } catch (o) {
    m(o instanceof v ? o.code : "invalid_request");
  }
  return { completion: c, cancel: () => {
    m("cancelled"), D();
  } };
}
const Y = 30, q = 500, U = 128 * 1024, z = (i, e) => i !== null && typeof i == "object" && !Array.isArray(i) && Object.keys(i).length === e.length && e.every((t) => Object.hasOwn(i, t));
function y(i) {
  if (!i) throw new Error("Invalid release catalogue");
}
function je(i) {
  const e = _(i.tag), t = typeof i.name == "string" ? i.name.split(" ")[0] : null;
  return e !== null && i.prerelease === !0 && me(t) && i.name === xe(t, e);
}
function Le(i) {
  y(z(i, ["releases"]) && Array.isArray(i.releases) && i.releases.length <= Y + q);
  const e = /* @__PURE__ */ new Set();
  let t = 0, n = 0, a = 0;
  return Object.freeze(i.releases.map((r) => z(r, ["tag", "prerelease", "name"]) ? (y(je(r) && !e.has(r.tag) && ++a <= q), e.add(r.tag), Object.freeze({ tag: r.tag, prerelease: !0, name: r.name })) : (y(z(r, ["tag", "prerelease"]) && typeof r.prerelease == "boolean" && (r.prerelease ? K(r.tag) : X(r.tag)) && !e.has(r.tag) && ++n <= Y), e.add(r.tag), r.prerelease || y(++t <= 1), Object.freeze({ tag: r.tag, prerelease: r.prerelease }))));
}
async function ze(i, { signal: e, timeoutMs: t = 15e3 } = {}) {
  const n = new AbortController(), a = () => n.abort();
  e?.addEventListener("abort", a, { once: !0 }), e?.aborted && a();
  const r = setTimeout(a, t);
  let s, d;
  const c = new Promise((l, h) => {
    d = () => h(new Error("Release catalogue cancelled"));
  });
  n.signal.addEventListener("abort", d, { once: !0 });
  try {
    return y(!n.signal.aborted), await Promise.race([c, (async () => {
      const l = await i.fetchWithAuth("/api/panel_assistant/usb/releases", {
        method: "GET",
        redirect: "error",
        signal: n.signal
      });
      y(!n.signal.aborted && l.status === 200 && !l.redirected && l.body && l.headers.get("content-type")?.split(";")[0].trim() === "application/json");
      const h = l.headers.get("content-length");
      y(h === null || /^(0|[1-9][0-9]*)$/.exec(h)?.[0] === h && Number(h) <= U), s = l.body.getReader();
      const A = [];
      let p = 0;
      for (; ; ) {
        const f = await s.read();
        if (y(!n.signal.aborted), f.done) break;
        p += f.value.byteLength, y(p <= U), A.push(f.value);
      }
      return y(p > 0 && (h === null || p === Number(h))), Le(JSON.parse(await new Blob(A).text()));
    })()]);
  } finally {
    clearTimeout(r), e?.removeEventListener("abort", a), n.signal.removeEventListener("abort", d), n.abort(), s && s.cancel().catch(() => {
    });
  }
}
const Ne = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMDgiIGhlaWdodD0iMTA4IiB2aWV3Qm94PSIwIDAgMTA4IDEwOCI+CjxwYXRoIGQ9Ik0yOCwzMiBoNTIgYTQsNCAwIDAgMSA0LDQgdjM3IGE0LDQgMCAwIDEgLTQsNCBoLTUyIGE0LDQgMCAwIDEgLTQsLTQgdi0zNyBhNCw0IDAgMCAxIDQsLTQgeiIgZmlsbD0iIzM3NDc0RiIvPgo8cGF0aCBkPSJNMjksMzUgaDUwIGEyLDIgMCAwIDEgMiwyIHYzNSBhMiwyIDAgMCAxIC0yLDIgaC01MCBhMiwyIDAgMCAxIC0yLC0yIHYtMzUgYTIsMiAwIDAgMSAyLC0yIHoiIGZpbGw9IiMwRTE2MjAiLz4KPGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoNDIuMDAsNDIuNTApIHNjYWxlKDAuMTAwMCkiPgo8cGF0aCBmaWxsPSIjRjJGNEY5IiBkPSJNMjQwIDIyNC44MTNDMjQwIDIzMy4wNjMgMjMzLjI1IDIzOS44MTMgMjI1IDIzOS44MTNIMTVDNi43NSAyMzkuODEzIDAgMjMzLjA2MyAwIDIyNC44MTNWMTM0LjgxM0MwIDEyNi41NjMgNC43NyAxMTUuMDQzIDEwLjYxIDEwOS4yMDNMMTA5LjM5IDEwLjQyM0MxMTUuMjIgNC41OTMwNCAxMjQuNzcgNC41OTMwNCAxMzAuNiAxMC40MjNMMjI5LjM5IDEwOS4yMTNDMjM1LjIyIDExNS4wNDMgMjQwIDEyNi41NzMgMjQwIDEzNC44MjNWMjI0LjgyM1YyMjQuODEzWiIvPgo8cGF0aCBmaWxsPSIjMThCQ0YyIiBkPSJNMjI5LjM5IDEwOS4yMDNMMTMwLjYxIDEwLjQyM0MxMjQuNzggNC41OTMwNCAxMTUuMjMgNC41OTMwNCAxMDkuNCAxMC40MjNMMTAuNjEgMTA5LjIwM0M0Ljc4IDExNS4wMzMgMCAxMjYuNTYzIDAgMTM0LjgxM1YyMjQuODEzQzAgMjMzLjA2MyA2Ljc1IDIzOS44MTMgMTUgMjM5LjgxM0gxMDcuMjdMNjYuNjQgMTk5LjE4M0M2NC41NSAxOTkuOTAzIDYyLjMyIDIwMC4zMTMgNjAgMjAwLjMxM0M0OC43IDIwMC4zMTMgMzkuNSAxOTEuMTEzIDM5LjUgMTc5LjgxM0MzOS41IDE2OC41MTMgNDguNyAxNTkuMzEzIDYwIDE1OS4zMTNDNzEuMyAxNTkuMzEzIDgwLjUgMTY4LjUxMyA4MC41IDE3OS44MTNDODAuNSAxODIuMTQzIDgwLjA5IDE4NC4zNzMgNzkuMzcgMTg2LjQ2M0wxMTEgMjE4LjA5M1YxMDIuMjEzQzEwNC4yIDk4Ljg3MyA5OS41IDkxLjg5MyA5OS41IDgzLjgyM0M5OS41IDcyLjUyMyAxMDguNyA2My4zMjMgMTIwIDYzLjMyM0MxMzEuMyA2My4zMjMgMTQwLjUgNzIuNTIzIDE0MC41IDgzLjgyM0MxNDAuNSA5MS44OTMgMTM1LjggOTguODczIDEyOSAxMDIuMjEzVjE4My40ODNMMTYwLjQ2IDE1Mi4wMjNDMTU5Ljg0IDE1MC4wNjMgMTU5LjUgMTQ3Ljk4MyAxNTkuNSAxNDUuODIzQzE1OS41IDEzNC41MjMgMTY4LjcgMTI1LjMyMyAxODAgMTI1LjMyM0MxOTEuMyAxMjUuMzIzIDIwMC41IDEzNC41MjMgMjAwLjUgMTQ1LjgyM0MyMDAuNSAxNTcuMTIzIDE5MS4zIDE2Ni4zMjMgMTgwIDE2Ni4zMjNDMTc3LjUgMTY2LjMyMyAxNzUuMTIgMTY1Ljg1MyAxNzIuOTEgMTY1LjAzM0wxMjkgMjA4Ljk0M1YyMzkuODIzSDIyNUMyMzMuMjUgMjM5LjgyMyAyNDAgMjMzLjA3MyAyNDAgMjI0LjgyM1YxMzQuODIzQzI0MCAxMjYuNTczIDIzNS4yMyAxMTUuMDUzIDIyOS4zOSAxMDkuMjEzVjEwOS4yMDNaIi8+CjwvZz4KPC9zdmc+Cg==", Se = Object.freeze(["Version", "Connect", "Install", "Set up"]);
function ke(i) {
  return Se.map((e, t) => t < i ? `<li class="done">${e}</li>` : t === i ? `<li class="current" aria-current="step">${e}</li>` : `<li>${e}</li>`).join("");
}
const F = "--bg:#f2f3f5;--card:#fff;--card-head:#e7ebef;--card-border:#d9dde3;--divider:#e4e7ec;--input-bg:#fafbfc;--border:#c4cad2;--border-strong:#b6bec8;--text:#1b2430;--dim:#6a7480;--accent:#1e56a8;--ok:#3f7d49;--bad:#a02c20;--disabled-bg:#e2e5e9;--disabled-fg:#9aa3ad;--shadow:rgba(0,0,0,.18)", V = "--bg:#111;--card:#181818;--card-head:#222;--card-border:#242424;--divider:#2a2a2a;--input-bg:#161616;--border:#383838;--border-strong:#444;--text:#eee;--dim:#888;--accent:#9af;--ok:#8a8;--bad:#ffb3a6;--disabled-bg:#222;--disabled-fg:#666;--shadow:#000", Be = `
:root,:host{color-scheme:light dark;${F};--primary:#2557a7;--primary-text:#fff;
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media (prefers-color-scheme:dark){:root,:host{${V}}}
:host([theme=light]){color-scheme:light;${F}}
:host([theme=dark]){color-scheme:dark;${V}}
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
`, M = Object.freeze({
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
class Te extends HTMLElement {
  #i;
  #d;
  #e;
  // A finished transfer keeps answering a reloaded installer window until this
  // page goes away or a new transfer starts.
  #s;
  #o = "ready";
  #n;
  #a = "loading";
  #h = [];
  constructor() {
    super(), this.attachShadow({ mode: "open" }), this.shadowRoot.innerHTML = `<style>${Be}
      :host{display:block;min-height:100%;background:var(--bg);padding:24px 16px}
      .card p.status{color:var(--text);margin:14px 0 0}
    </style><main class="wiz">
      <div class="wiz-brand"><img src="${Ne}" alt=""><span>ha-paneld</span></div>
      <ol class="wiz-dots" aria-label="Progress">${ke(0)}</ol>
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
      e.textContent = M[e.dataset.message];
    this.shadowRoot.querySelector("#start").addEventListener("click", () => this.#g()), this.shadowRoot.querySelector("#cancel").addEventListener("click", () => this.#e?.cancel()), this.shadowRoot.querySelector("#retry").addEventListener("click", () => this.#r()), this.shadowRoot.querySelector("#release").addEventListener("change", () => this.#t()), this.#t();
  }
  set hass(e) {
    const t = this.#i?.user?.id !== e?.user?.id || this.#i?.user?.is_admin !== e?.user?.is_admin || this.#i?.connection !== e?.connection || this.#i?.auth !== e?.auth;
    this.#i = e;
    const n = e?.themes?.darkMode;
    typeof n == "boolean" && this.setAttribute?.("theme", n ? "dark" : "light"), t && (this.#e?.cancel(), this.#r()), this.#t();
  }
  set panel(e) {
    const t = this.#d?.config?.installer_url !== e?.config?.installer_url;
    t && this.#e?.cancel(), this.#d = e, t && this.#r(), this.#t();
  }
  connectedCallback() {
    this.#r();
  }
  disconnectedCallback() {
    this.#e?.cancel(), this.#s?.cancel(), this.#s = void 0, this.#n?.abort(), this.#n = void 0;
  }
  async #r() {
    if (this.#n?.abort(), this.#n = void 0, this.#h = [], this.#a = "loading", this.shadowRoot.querySelector("#release").replaceChildren(), this.#t(), !this.isConnected || this.#i?.user?.is_admin !== !0 || !this.#d?.config?.installer_url) return;
    const e = new AbortController();
    this.#n = e;
    try {
      const t = await ze(this.#i, { signal: e.signal });
      if (this.#n !== e) return;
      this.#h = t, this.#a = t.length ? "ready" : "empty";
      const n = this.shadowRoot.querySelector("#release"), a = document.createElement("option");
      a.value = "", a.textContent = M.choose, a.disabled = !0, n.append(a);
      const r = t.find((s) => !s.prerelease)?.tag ?? "";
      for (const s of t) {
        const d = document.createElement("option");
        d.value = s.tag;
        const c = s.tag === r ? M.recommended : s.name ? M.devBuild : s.prerelease ? M.testing : "";
        d.textContent = `${s.name ?? s.tag.replace(/^v/, "")}${c ? ` (${c})` : ""}`, n.append(d);
      }
      n.value = r;
    } catch {
      if (this.#n !== e) return;
      this.#a = "catalogError";
    } finally {
      this.#n === e && (this.#n = void 0, this.#t());
    }
  }
  #t() {
    const e = this.#i?.user?.is_admin === !0, t = typeof this.#d?.config?.installer_url == "string" && this.#d.config.installer_url.length > 0, n = this.#h.find((d) => d.tag === this.shadowRoot.querySelector("#release").value);
    this.shadowRoot.querySelector("#start").disabled = !e || !t || !!this.#e || !n, this.shadowRoot.querySelector("#cancel").disabled = !this.#e, this.shadowRoot.querySelector("#release").disabled = !!this.#e || this.#a !== "ready";
    const a = this.shadowRoot.querySelector("#catalog-status");
    a.textContent = e && t && this.#a !== "ready" ? M[this.#a] : "", a.hidden = !a.textContent, this.shadowRoot.querySelector("#retry").hidden = !e || !t || !["catalogError", "empty"].includes(this.#a), this.shadowRoot.querySelector("#cancel").hidden = !this.#e;
    const r = e ? t ? this.#o : "unavailable" : "admin", s = this.shadowRoot.querySelector("#status");
    s.textContent = Object.hasOwn(M, r) ? M[r] : M.failed, s.hidden = !s.textContent;
  }
  #g() {
    if (this.#e || this.#i?.user?.is_admin !== !0) return;
    const e = this.#h.find((n) => n.tag === this.shadowRoot.querySelector("#release").value);
    if (!e || !this.isConnected) return;
    this.#s?.cancel(), this.#s = void 0;
    const t = ve(this.#i, this.#d?.config?.installer_url, {
      rcTag: e.prerelease ? e.tag : null,
      onState: (n) => {
        this.#o = n, this.#t();
      }
    });
    this.#e = t, this.#t(), t.completion.then(() => {
      this.#e === t && (this.#s = t);
    }, () => {
    }).finally(() => {
      this.#e === t && (this.#e = void 0), this.#t();
    });
  }
}
customElements.get("panel-assistant-usb-install") || customElements.define("panel-assistant-usb-install", Te);
