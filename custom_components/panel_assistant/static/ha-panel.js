const j = Object.freeze({
  title: "Panel Assistant",
  menu: "Open navigation",
  choosePanel: "Panel",
  addPanel: "Add panel",
  integrationSettings: "Integration settings",
  reachable: "reachable",
  unreachable: "unreachable",
  not_loaded: "not loaded",
  loading: "Loading…",
  empty: "No panels are attached yet.",
  failed: "The panel could not be opened. It will be tried again shortly.",
  admin: "An administrator must open this page.",
  unreachableBody: "Home Assistant cannot reach this panel right now.",
  notLoadedBody: "This panel is not loaded in Home Assistant.",
  closed: "This panel was closed.",
  frameTitle: "Panel interface"
}), V = "panel_assistant.sidebar.entry", ie = "/config/integrations/dashboard/add?domain=panel_assistant", se = "/config/integrations/integration/panel_assistant", re = /* @__PURE__ */ new Set(["reachable", "unreachable", "not_loaded"]), oe = 3e4;
function de(n) {
  if (!n || !Array.isArray(n.panels) || n.panels.length > 200) throw Error("invalid panels");
  const e = /* @__PURE__ */ new Set();
  return n.panels.map((t) => {
    if (!t || typeof t.entry_id != "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(t.entry_id) || e.has(t.entry_id) || typeof t.title != "string" || t.title.length > 256 || !re.has(t.state)) throw Error("invalid panel");
    return e.add(t.entry_id), { entry_id: t.entry_id, title: t.title, state: t.state };
  });
}
function le(n) {
  return typeof n == "string" ? n.match(/^\/api\/panel_assistant\/embed\/([A-Za-z0-9_-]{43})\/$/)?.[1] ?? null : null;
}
function ce(n) {
  history.pushState(null, "", n), window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: !1 } }));
}
function he() {
  try {
    return localStorage.getItem(V);
  } catch {
    return null;
  }
}
function ue(n) {
  try {
    localStorage.setItem(V, n);
  } catch {
  }
}
class ge extends HTMLElement {
  #e;
  #d = !1;
  #t;
  #s = null;
  #h = "loading";
  #a = 0;
  #o = "";
  #r = null;
  #n = null;
  #i = null;
  #u = () => this.#I();
  constructor() {
    super(), this.attachShadow({ mode: "open" }), this.shadowRoot.innerHTML = `<style>
      :host{display:block;height:100%;background:var(--primary-background-color,#fafafa);color:var(--primary-text-color,#212121)}
      [hidden]{display:none!important}
      .root{display:flex;flex-direction:column;height:100%}
      header{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:4px 12px;background:var(--app-header-background-color,var(--primary-color,#03a9f4));color:var(--app-header-text-color,#fff);border-bottom:1px solid var(--divider-color,#e0e0e0)}
      h1{font-size:1.25rem;font-weight:400;margin:0 8px 0 0}
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
      <h1 data-message="title"></h1>
      <label id="picker"><span data-message="choosePanel"></span><select id="panels"></select></label>
      <a id="add" data-message="addPanel"></a>
      <a id="settings" data-message="integrationSettings"></a>
      <div id="slot"></div>
    </header><p id="status" role="status" aria-live="polite"></p><iframe id="frame"></iframe></div>`;
    const e = this.shadowRoot;
    for (const a of e.querySelectorAll("[data-message]")) a.textContent = j[a.dataset.message];
    const t = e.querySelector("#menu");
    t.setAttribute("aria-label", j.menu), t.hidden = !0, t.addEventListener("click", () => this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: !0, composed: !0 }))), e.querySelector("#frame").setAttribute("title", j.frameTitle);
    for (const [a, s] of [["add", ie], ["settings", se]]) {
      const i = e.querySelector(`#${a}`);
      i.setAttribute("href", s), i.addEventListener("click", (r) => {
        r.defaultPrevented || r.button !== 0 || r.metaKey || r.ctrlKey || r.shiftKey || r.altKey || (r.preventDefault(), ce(s));
      });
    }
    e.querySelector("#panels").addEventListener("change", (a) => this.#w(a.target.value)), this.#c();
  }
  get hass() {
    return this.#e;
  }
  set hass(e) {
    const t = this.#e;
    if (this.#e = e, !!this.isConnected) {
      if (t?.connection !== e?.connection || t?.user?.id !== e?.user?.id || t?.user?.is_admin !== e?.user?.is_admin) {
        this.#M();
        return;
      }
      (t?.language !== e?.language || !!t?.themes?.darkMode != !!e?.themes?.darkMode) && (this.#l(), this.#f());
    }
  }
  get narrow() {
    return this.#d;
  }
  set narrow(e) {
    this.#d = e === !0, this.shadowRoot.querySelector("#menu").hidden = !this.#d;
  }
  connectedCallback() {
    clearInterval(this.#i), this.#M(), this.#i = setInterval(() => this.#p(), oe);
  }
  disconnectedCallback() {
    clearInterval(this.#i), this.#i = null, this.#a++, this.#y(), this.#l();
  }
  #g() {
    return this.#e?.user?.is_admin === !0;
  }
  #y() {
    this.#t?.removeEventListener?.("ready", this.#u), this.#t = void 0;
  }
  #M() {
    this.#y(), this.#l(), this.#s = null, this.#o = "", this.#h = "loading", this.#g() && this.#e.connection && (this.#t = this.#e.connection, this.#t.addEventListener("ready", this.#u)), this.#p();
  }
  async #p() {
    const e = ++this.#a;
    if (!this.#g()) {
      this.#c();
      return;
    }
    let t;
    try {
      let a;
      try {
        a = await this.#e.callWS({ type: "panel_assistant/embed_panels" });
      } catch (s) {
        if (this.#s) return;
        throw s;
      }
      t = de(a);
    } catch {
      if (e !== this.#a) return;
      this.#s = null, this.#h = "failed", this.#l(), this.#c();
      return;
    }
    if (e === this.#a) {
      if (this.#s = t, this.#h = t.length ? "ready" : "empty", !t.some((a) => a.entry_id === this.#r)) {
        const a = he();
        this.#r = t.some((s) => s.entry_id === a) ? a : t[0]?.entry_id ?? null;
      }
      this.#f();
    }
  }
  #w(e) {
    !this.#s?.some((t) => t.entry_id === e) || e === this.#r || (this.#r = e, ue(e), this.#l(), this.#f());
  }
  // Opens a session when the selected panel is reachable and none is live for it.
  #f() {
    const e = this.#s?.find((a) => a.entry_id === this.#r), t = this.#n;
    !e || e.state !== "reachable" ? t && !(t.state === "closed" && t.entryId === e?.entry_id) && this.#l() : (!t || t.entryId !== e.entry_id || !["opening", "open"].includes(t.state)) && (this.#l(), this.#b(e.entry_id, null, null)), this.#c();
  }
  #b(e, t, a) {
    const s = this.#e, i = { entryId: e, token: t, url: a, state: "opening", code: null, unsubscribe: null };
    this.#n = i;
    const r = {
      type: "panel_assistant/embed_session",
      entry_id: e,
      language: s.language,
      theme: s.themes?.darkMode ? "dark" : "light",
      ...t ? { resume: t } : {}
    };
    i.unsubscribe = Promise.resolve().then(() => s.connection.subscribeMessage((h) => this.#x(i, h), r, { resubscribe: !1 })), i.unsubscribe.catch((h) => {
      this.#n === i && (i.state = "failed", i.code = h?.code ?? null, this.#m(), this.#c());
    });
  }
  #x(e, t) {
    if (!(this.#n !== e || !t))
      if (t.kind === "opened") {
        const a = le(t.url);
        if (!a) {
          this.#l(), this.#n = { entryId: e.entryId, state: "failed", code: null, unsubscribe: null }, this.#c();
          return;
        }
        e.token = a, e.url = t.url, e.state = "open";
        const s = this.shadowRoot.querySelector("#frame");
        s.getAttribute("src") !== t.url && s.setAttribute("src", t.url), this.#c();
      } else t.kind === "closed" && (this.#l(), this.#n = { entryId: e.entryId, state: "closed", code: null, unsubscribe: null }, this.#c(), this.#p());
  }
  // The connection came back; subscriptions made with resubscribe:false are gone, and
  // their unsubscribe functions must not be called: command ids restart per socket.
  #I() {
    const e = this.#n;
    !this.isConnected || !e || !["opening", "open"].includes(e.state) || (this.#b(e.entryId, e.token, e.url), this.#c());
  }
  #l() {
    const e = this.#n;
    this.#n = null, e && (e.state = "ended", e.unsubscribe?.then((t) => t()).catch(() => {
    }), this.#m());
  }
  #m() {
    this.shadowRoot.querySelector("#frame").removeAttribute("src");
  }
  #c() {
    const e = this.shadowRoot, t = e.querySelector("#panels"), a = this.#g() ? this.#s ?? [] : [], s = JSON.stringify(a);
    if (s !== this.#o) {
      this.#o = s, t.replaceChildren();
      for (const d of a) {
        const p = document.createElement("option");
        p.value = d.entry_id, p.textContent = `${d.title} (${j[d.state]})`, t.append(p);
      }
    }
    t.value = this.#r ?? "", e.querySelector("#picker").hidden = a.length === 0;
    const i = a.find((d) => d.entry_id === this.#r), r = this.#n, h = e.querySelector("#frame");
    let l = "";
    this.#g() ? this.#h !== "ready" ? l = this.#h : r?.state === "closed" && r.entryId === i?.entry_id ? l = "closed" : i?.state === "unreachable" ? l = "unreachableBody" : i?.state === "not_loaded" ? l = "notLoadedBody" : r?.state === "failed" ? l = r.code === "not_loaded" ? "notLoadedBody" : "failed" : r?.state !== "open" && !h.getAttribute("src") && (l = "loading") : l = "admin";
    const c = e.querySelector("#status");
    c.textContent = l ? j[l] : "", c.hidden = !l, h.hidden = !h.getAttribute("src");
  }
}
customElements.get("panel-assistant-sidebar") || customElements.define("panel-assistant-sidebar", ge);
const W = 64, Z = 256 * 1024, pe = 2147483647, fe = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/, ye = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc[1-9][0-9]*$/, O = /^build-([1-9][0-9]{0,9})$/, Me = /^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$/, z = (n, e) => typeof e == "string" && e.length <= W && n.exec(e)?.[0] === e, F = (n) => z(fe, n), J = (n) => z(ye, n);
function K(n) {
  if (!z(O, n)) return null;
  const e = Number(O.exec(n)[1]);
  return e <= pe ? e : null;
}
const R = (n) => K(n) !== null, be = (n) => z(Me, n), me = (n, e) => `${n} build ${e}`, P = "/api/panel_assistant/usb/release", B = 64 * 1024 * 1024, we = 1800 * 1e3, xe = [
  "id",
  "tag",
  "checksum",
  "checksum_signature",
  "descriptor",
  "descriptor_signature",
  "apk_size",
  "apk_sha256"
], Ie = ["id", "tag", "feed", "feed_signature", "apk_size", "apk_sha256"], X = 8192, Ae = Math.ceil(Z / 3) * 4 + X, N = (n, e) => typeof e == "string" && n.exec(e)?.[0] === e, U = (n, e) => n !== null && typeof n == "object" && !Array.isArray(n) && Object.keys(n).length === e.length && e.every((t) => Object.hasOwn(n, t));
class D extends Error {
  constructor(e) {
    super(e), this.name = "HandoffError", this.code = e;
  }
}
function g(n, e = "invalid_response") {
  if (!n) throw new D(e);
}
function x(n, e, t = !1) {
  g(typeof n == "string" && n.length <= Math.ceil(e / 3) * 4 && N(/(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?/, n));
  const a = atob(n);
  return g(btoa(a) === n && a.length > 0 && (t ? a.length === e : a.length <= e)), Uint8Array.from(a, (s) => s.charCodeAt(0));
}
async function H(n, e, t, a = null) {
  g(n.status === 200 && !n.redirected && n.body);
  const s = n.headers.get("content-length");
  if (s !== null) {
    g(N(/0|[1-9][0-9]*/, s));
    const c = Number(s);
    g(Number.isSafeInteger(c) && c <= e && (a === null || c === a));
  }
  const i = n.body.getReader(), r = () => {
    i.cancel().catch(() => {
    });
  };
  t.addEventListener("abort", r, { once: !0 });
  const h = [];
  let l = 0;
  try {
    for (; ; ) {
      g(!t.aborted, "cancelled");
      const c = await i.read();
      if (g(!t.aborted, "cancelled"), c.done) break;
      l += c.value.byteLength, g(l <= e && (a === null || l <= a)), h.push(c.value);
    }
    return g(l > 0 && (s === null || l === Number(s)) && (a === null || l === a)), new Blob(h);
  } finally {
    t.removeEventListener("abort", r), r(), i.releaseLock();
  }
}
function je(n, e, {
  rcTag: t = null,
  onState: a = () => {
  },
  windowObject: s = window,
  timeoutMs: i = 3e5
} = {}) {
  let r, h;
  const l = new Promise((o, b) => {
    r = o, h = b;
  }), c = new AbortController();
  let d = !1, p, m, M, E, k = !1, v = !1, I, C, L;
  const S = () => {
    clearInterval(C), clearTimeout(L), I = void 0, s.removeEventListener("message", _);
  }, A = (o) => {
    try {
      a(o);
    } catch {
    }
  }, w = (o = null) => {
    if (!d) {
      if (d = !0, c.abort(), clearTimeout(m), A(o ?? "verified"), o) {
        S(), h(new D(o));
        return;
      }
      C = setInterval(() => {
        p.closed && S();
      }, 2e3), L = setTimeout(S, we), r();
    }
  };
  async function ee() {
    try {
      A("preparing"), g(!d, "cancelled");
      const o = await n.fetchWithAuth(P, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(t === null ? {} : { release_candidate: t }),
        redirect: "error",
        signal: c.signal
      });
      g(!d, "cancelled"), g(o.headers.get("content-type")?.split(";")[0].trim() === "application/json");
      const b = R(t), u = JSON.parse(await (await H(
        o,
        b ? Ae : X,
        c.signal
      )).text());
      g(!d, "cancelled"), g(U(u, b ? Ie : xe) && N(/[0-9a-f]{32}/, u.id) && typeof u.tag == "string" && u.tag.length <= W && (t === null ? F(u.tag) : u.tag === t) && N(/[0-9a-f]{64}/, u.apk_sha256) && Number.isSafeInteger(u.apk_size) && u.apk_size > 0 && u.apk_size <= B);
      const te = b ? {
        tag: u.tag,
        feed: x(u.feed, Z),
        feedSignature: x(u.feed_signature, 256, !0)
      } : {
        tag: u.tag,
        checksum: x(u.checksum, 512),
        checksumSignature: x(u.checksum_signature, 256, !0),
        descriptor: x(u.descriptor, 4096),
        descriptorSignature: x(u.descriptor_signature, 256, !0)
      };
      A("downloading"), g(!d, "cancelled");
      const ne = await n.fetchWithAuth(`${P}/${u.id}/apk`, {
        method: "GET",
        redirect: "error",
        signal: c.signal
      });
      g(!d, "cancelled");
      const ae = await H(ne, B, c.signal, u.apk_size);
      g(!d && !p.closed, "window_closed"), v = !0, I = { type: "ha-paneld/usb-bundle", nonce: E, bundle: te, apk: ae }, p.postMessage(I, M), A("verifying");
    } catch (o) {
      w(o instanceof D ? o.code : "delivery_failed");
    }
  }
  function _(o) {
    if (!(o.source !== p || o.origin !== M || !U(o.data, ["type", "nonce"]) || o.data.nonce !== E)) {
      if (o.data.type === "ha-paneld/usb-ready") {
        !k && !d ? (k = !0, ee()) : I && !p.closed && p.postMessage(I, M);
        return;
      }
      d || (o.data.type === "ha-paneld/usb-verified" && v ? w() : o.data.type === "ha-paneld/usb-error" && w("verification_failed"));
    }
  }
  try {
    g(n && typeof n.fetchWithAuth == "function" && (t === null || J(t) || R(t)) && Number.isSafeInteger(i) && i > 0 && i <= 3e5, "invalid_request");
    const o = new URL(e);
    g(!o.username && !o.password && !o.hash && (o.protocol === "https:" || o.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(o.hostname)), "invalid_destination"), M = o.origin;
    const b = new Uint8Array(16);
    s.crypto.getRandomValues(b), E = Array.from(b, (u) => u.toString(16).padStart(2, "0")).join(""), o.hash = new URLSearchParams({ ha_origin: s.location.origin, nonce: E, rc: t ?? "" }).toString(), s.addEventListener("message", _), p = s.open(o.href, "_blank"), g(p, "popup_blocked"), m = setTimeout(() => w("timeout"), i), A("waiting");
  } catch (o) {
    w(o instanceof D ? o.code : "invalid_request");
  }
  return { completion: l, cancel: () => {
    w("cancelled"), S();
  } };
}
const q = 30, Q = 500, Y = 128 * 1024, T = (n, e) => n !== null && typeof n == "object" && !Array.isArray(n) && Object.keys(n).length === e.length && e.every((t) => Object.hasOwn(n, t));
function f(n) {
  if (!n) throw new Error("Invalid release catalogue");
}
function Ee(n) {
  const e = K(n.tag), t = typeof n.name == "string" ? n.name.split(" ")[0] : null;
  return e !== null && n.prerelease === !0 && be(t) && n.name === me(t, e);
}
function Se(n) {
  f(T(n, ["releases"]) && Array.isArray(n.releases) && n.releases.length <= q + Q);
  const e = /* @__PURE__ */ new Set();
  let t = 0, a = 0, s = 0;
  return Object.freeze(n.releases.map((i) => T(i, ["tag", "prerelease", "name"]) ? (f(Ee(i) && !e.has(i.tag) && ++s <= Q), e.add(i.tag), Object.freeze({ tag: i.tag, prerelease: !0, name: i.name })) : (f(T(i, ["tag", "prerelease"]) && typeof i.prerelease == "boolean" && (i.prerelease ? J(i.tag) : F(i.tag)) && !e.has(i.tag) && ++a <= q), e.add(i.tag), i.prerelease || f(++t <= 1), Object.freeze({ tag: i.tag, prerelease: i.prerelease }))));
}
async function De(n, { signal: e, timeoutMs: t = 15e3 } = {}) {
  const a = new AbortController(), s = () => a.abort();
  e?.addEventListener("abort", s, { once: !0 }), e?.aborted && s();
  const i = setTimeout(s, t);
  let r, h;
  const l = new Promise((c, d) => {
    h = () => d(new Error("Release catalogue cancelled"));
  });
  a.signal.addEventListener("abort", h, { once: !0 });
  try {
    return f(!a.signal.aborted), await Promise.race([l, (async () => {
      const c = await n.fetchWithAuth("/api/panel_assistant/usb/releases", {
        method: "GET",
        redirect: "error",
        signal: a.signal
      });
      f(!a.signal.aborted && c.status === 200 && !c.redirected && c.body && c.headers.get("content-type")?.split(";")[0].trim() === "application/json");
      const d = c.headers.get("content-length");
      f(d === null || /^(0|[1-9][0-9]*)$/.exec(d)?.[0] === d && Number(d) <= Y), r = c.body.getReader();
      const p = [];
      let m = 0;
      for (; ; ) {
        const M = await r.read();
        if (f(!a.signal.aborted), M.done) break;
        m += M.value.byteLength, f(m <= Y), p.push(M.value);
      }
      return f(m > 0 && (d === null || m === Number(d))), Se(JSON.parse(await new Blob(p).text()));
    })()]);
  } finally {
    clearTimeout(i), e?.removeEventListener("abort", s), a.signal.removeEventListener("abort", h), a.abort(), r && r.cancel().catch(() => {
    });
  }
}
const Ne = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMDgiIGhlaWdodD0iMTA4IiB2aWV3Qm94PSIwIDAgMTA4IDEwOCI+CjxwYXRoIGQ9Ik0yOCwzMiBoNTIgYTQsNCAwIDAgMSA0LDQgdjM3IGE0LDQgMCAwIDEgLTQsNCBoLTUyIGE0LDQgMCAwIDEgLTQsLTQgdi0zNyBhNCw0IDAgMCAxIDQsLTQgeiIgZmlsbD0iIzM3NDc0RiIvPgo8cGF0aCBkPSJNMjksMzUgaDUwIGEyLDIgMCAwIDEgMiwyIHYzNSBhMiwyIDAgMCAxIC0yLDIgaC01MCBhMiwyIDAgMCAxIC0yLC0yIHYtMzUgYTIsMiAwIDAgMSAyLC0yIHoiIGZpbGw9IiMwRTE2MjAiLz4KPGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoNDIuMDAsNDIuNTApIHNjYWxlKDAuMTAwMCkiPgo8cGF0aCBmaWxsPSIjRjJGNEY5IiBkPSJNMjQwIDIyNC44MTNDMjQwIDIzMy4wNjMgMjMzLjI1IDIzOS44MTMgMjI1IDIzOS44MTNIMTVDNi43NSAyMzkuODEzIDAgMjMzLjA2MyAwIDIyNC44MTNWMTM0LjgxM0MwIDEyNi41NjMgNC43NyAxMTUuMDQzIDEwLjYxIDEwOS4yMDNMMTA5LjM5IDEwLjQyM0MxMTUuMjIgNC41OTMwNCAxMjQuNzcgNC41OTMwNCAxMzAuNiAxMC40MjNMMjI5LjM5IDEwOS4yMTNDMjM1LjIyIDExNS4wNDMgMjQwIDEyNi41NzMgMjQwIDEzNC44MjNWMjI0LjgyM1YyMjQuODEzWiIvPgo8cGF0aCBmaWxsPSIjMThCQ0YyIiBkPSJNMjI5LjM5IDEwOS4yMDNMMTMwLjYxIDEwLjQyM0MxMjQuNzggNC41OTMwNCAxMTUuMjMgNC41OTMwNCAxMDkuNCAxMC40MjNMMTAuNjEgMTA5LjIwM0M0Ljc4IDExNS4wMzMgMCAxMjYuNTYzIDAgMTM0LjgxM1YyMjQuODEzQzAgMjMzLjA2MyA2Ljc1IDIzOS44MTMgMTUgMjM5LjgxM0gxMDcuMjdMNjYuNjQgMTk5LjE4M0M2NC41NSAxOTkuOTAzIDYyLjMyIDIwMC4zMTMgNjAgMjAwLjMxM0M0OC43IDIwMC4zMTMgMzkuNSAxOTEuMTEzIDM5LjUgMTc5LjgxM0MzOS41IDE2OC41MTMgNDguNyAxNTkuMzEzIDYwIDE1OS4zMTNDNzEuMyAxNTkuMzEzIDgwLjUgMTY4LjUxMyA4MC41IDE3OS44MTNDODAuNSAxODIuMTQzIDgwLjA5IDE4NC4zNzMgNzkuMzcgMTg2LjQ2M0wxMTEgMjE4LjA5M1YxMDIuMjEzQzEwNC4yIDk4Ljg3MyA5OS41IDkxLjg5MyA5OS41IDgzLjgyM0M5OS41IDcyLjUyMyAxMDguNyA2My4zMjMgMTIwIDYzLjMyM0MxMzEuMyA2My4zMjMgMTQwLjUgNzIuNTIzIDE0MC41IDgzLjgyM0MxNDAuNSA5MS44OTMgMTM1LjggOTguODczIDEyOSAxMDIuMjEzVjE4My40ODNMMTYwLjQ2IDE1Mi4wMjNDMTU5Ljg0IDE1MC4wNjMgMTU5LjUgMTQ3Ljk4MyAxNTkuNSAxNDUuODIzQzE1OS41IDEzNC41MjMgMTY4LjcgMTI1LjMyMyAxODAgMTI1LjMyM0MxOTEuMyAxMjUuMzIzIDIwMC41IDEzNC41MjMgMjAwLjUgMTQ1LjgyM0MyMDAuNSAxNTcuMTIzIDE5MS4zIDE2Ni4zMjMgMTgwIDE2Ni4zMjNDMTc3LjUgMTY2LjMyMyAxNzUuMTIgMTY1Ljg1MyAxNzIuOTEgMTY1LjAzM0wxMjkgMjA4Ljk0M1YyMzkuODIzSDIyNUMyMzMuMjUgMjM5LjgyMyAyNDAgMjMzLjA3MyAyNDAgMjI0LjgyM1YxMzQuODIzQzI0MCAxMjYuNTczIDIzNS4yMyAxMTUuMDUzIDIyOS4zOSAxMDkuMjEzVjEwOS4yMDNaIi8+CjwvZz4KPC9zdmc+Cg==", ze = Object.freeze(["Version", "Connect", "Install", "Set up"]);
function Te(n) {
  return ze.map((e, t) => t < n ? `<li class="done">${e}</li>` : t === n ? `<li class="current" aria-current="step">${e}</li>` : `<li>${e}</li>`).join("");
}
const $ = "--bg:#f2f3f5;--card:#fff;--card-head:#e7ebef;--card-border:#d9dde3;--divider:#e4e7ec;--input-bg:#fafbfc;--border:#c4cad2;--border-strong:#b6bec8;--text:#1b2430;--dim:#6a7480;--accent:#1e56a8;--ok:#3f7d49;--bad:#a02c20;--disabled-bg:#e2e5e9;--disabled-fg:#9aa3ad;--shadow:rgba(0,0,0,.18)", G = "--bg:#111;--card:#181818;--card-head:#222;--card-border:#242424;--divider:#2a2a2a;--input-bg:#161616;--border:#383838;--border-strong:#444;--text:#eee;--dim:#888;--accent:#9af;--ok:#8a8;--bad:#ffb3a6;--disabled-bg:#222;--disabled-fg:#666;--shadow:#000", ke = `
:root,:host{color-scheme:light dark;${$};--primary:#2557a7;--primary-text:#fff;
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media (prefers-color-scheme:dark){:root,:host{${G}}}
:host([theme=light]){color-scheme:light;${$}}
:host([theme=dark]){color-scheme:dark;${G}}
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
`, y = Object.freeze({
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
class ve extends HTMLElement {
  #e;
  #d;
  #t;
  // A finished transfer keeps answering a reloaded installer window until this
  // page goes away or a new transfer starts.
  #s;
  #h = "ready";
  #a;
  #o = "loading";
  #r = [];
  constructor() {
    super(), this.attachShadow({ mode: "open" }), this.shadowRoot.innerHTML = `<style>${ke}
      :host{display:block;min-height:100%;background:var(--bg);padding:24px 16px}
      .card p.status{color:var(--text);margin:14px 0 0}
    </style><main class="wiz">
      <div class="wiz-brand"><img src="${Ne}" alt=""><span>ha-paneld</span></div>
      <ol class="wiz-dots" aria-label="Progress">${Te(0)}</ol>
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
      e.textContent = y[e.dataset.message];
    this.shadowRoot.querySelector("#start").addEventListener("click", () => this.#u()), this.shadowRoot.querySelector("#cancel").addEventListener("click", () => this.#t?.cancel()), this.shadowRoot.querySelector("#retry").addEventListener("click", () => this.#n()), this.shadowRoot.querySelector("#release").addEventListener("change", () => this.#i()), this.#i();
  }
  set hass(e) {
    const t = this.#e?.user?.id !== e?.user?.id || this.#e?.user?.is_admin !== e?.user?.is_admin || this.#e?.connection !== e?.connection || this.#e?.auth !== e?.auth;
    this.#e = e;
    const a = e?.themes?.darkMode;
    typeof a == "boolean" && this.setAttribute?.("theme", a ? "dark" : "light"), t && (this.#t?.cancel(), this.#n()), this.#i();
  }
  set panel(e) {
    const t = this.#d?.config?.installer_url !== e?.config?.installer_url;
    t && this.#t?.cancel(), this.#d = e, t && this.#n(), this.#i();
  }
  connectedCallback() {
    this.#n();
  }
  disconnectedCallback() {
    this.#t?.cancel(), this.#s?.cancel(), this.#s = void 0, this.#a?.abort(), this.#a = void 0;
  }
  async #n() {
    if (this.#a?.abort(), this.#a = void 0, this.#r = [], this.#o = "loading", this.shadowRoot.querySelector("#release").replaceChildren(), this.#i(), !this.isConnected || this.#e?.user?.is_admin !== !0 || !this.#d?.config?.installer_url) return;
    const e = new AbortController();
    this.#a = e;
    try {
      const t = await De(this.#e, { signal: e.signal });
      if (this.#a !== e) return;
      this.#r = t, this.#o = t.length ? "ready" : "empty";
      const a = this.shadowRoot.querySelector("#release"), s = document.createElement("option");
      s.value = "", s.textContent = y.choose, s.disabled = !0, a.append(s);
      const i = t.find((r) => !r.prerelease)?.tag ?? "";
      for (const r of t) {
        const h = document.createElement("option");
        h.value = r.tag;
        const l = r.tag === i ? y.recommended : r.name ? y.devBuild : r.prerelease ? y.testing : "";
        h.textContent = `${r.name ?? r.tag.replace(/^v/, "")}${l ? ` (${l})` : ""}`, a.append(h);
      }
      a.value = i;
    } catch {
      if (this.#a !== e) return;
      this.#o = "catalogError";
    } finally {
      this.#a === e && (this.#a = void 0, this.#i());
    }
  }
  #i() {
    const e = this.#e?.user?.is_admin === !0, t = typeof this.#d?.config?.installer_url == "string" && this.#d.config.installer_url.length > 0, a = this.#r.find((h) => h.tag === this.shadowRoot.querySelector("#release").value);
    this.shadowRoot.querySelector("#start").disabled = !e || !t || !!this.#t || !a, this.shadowRoot.querySelector("#cancel").disabled = !this.#t, this.shadowRoot.querySelector("#release").disabled = !!this.#t || this.#o !== "ready";
    const s = this.shadowRoot.querySelector("#catalog-status");
    s.textContent = e && t && this.#o !== "ready" ? y[this.#o] : "", s.hidden = !s.textContent, this.shadowRoot.querySelector("#retry").hidden = !e || !t || !["catalogError", "empty"].includes(this.#o), this.shadowRoot.querySelector("#cancel").hidden = !this.#t;
    const i = e ? t ? this.#h : "unavailable" : "admin", r = this.shadowRoot.querySelector("#status");
    r.textContent = Object.hasOwn(y, i) ? y[i] : y.failed, r.hidden = !r.textContent;
  }
  #u() {
    if (this.#t || this.#e?.user?.is_admin !== !0) return;
    const e = this.#r.find((a) => a.tag === this.shadowRoot.querySelector("#release").value);
    if (!e || !this.isConnected) return;
    this.#s?.cancel(), this.#s = void 0;
    const t = je(this.#e, this.#d?.config?.installer_url, {
      rcTag: e.prerelease ? e.tag : null,
      onState: (a) => {
        this.#h = a, this.#i();
      }
    });
    this.#t = t, this.#i(), t.completion.then(() => {
      this.#t === t && (this.#s = t);
    }, () => {
    }).finally(() => {
      this.#t === t && (this.#t = void 0), this.#i();
    });
  }
}
customElements.get("panel-assistant-usb-install") || customElements.define("panel-assistant-usb-install", ve);
