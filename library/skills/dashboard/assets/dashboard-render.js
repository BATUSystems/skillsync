/* dashboard — wspólny renderer „JSON (kontrakt schema 1) → DOM” strony `skillsync dashboard serve` i webview Skippstera.
   Czysty widok: nie zna transportu ani pamięci przeglądarki (wszystko wstrzykuje host w `deps`), a DOM buduje wyłącznie
   przez createElement/createElementNS/textContent/setAttribute (zgodność z CSP webview: bez wstrzykiwania HTML, bez atrybutu
   stylu, bez handlerów inline). Kontrakt: library/skills/dashboard/SKILL.md, sekcja „Renderer”.
   Wewnętrzne API renderera (nie publiczny kontrakt CLI); odstępstwa od pierwotnego szkicu w specu DASH-2 (zaakceptowane przez Dyspozytora):
   nazwy `create`/`update` (aliasy `createDashboard`/`render`); brak `deps.transport` (transport to host przez `actions`); opisy przez
   `setDescriptions(map)`; wymagane `deps.now`; `actions.approve({kind, sha, path, word})` zwraca Promise `{ok, word, text}`;
   `actions.openLink(kind, id, url)` zwraca bool (true = host obsłużył); nowe `actions.loadDoc/selectPlan`; nowe `deps`:
   `canApprove`, `texts`, `theme`, `prototypeUrl`, `mountHtml`, `onTitle`, `onNavigate`, `scroll`, `timers`, `raf`, `cancelRaf`, `viewport`,
   `sprite`; `approvals.viewer` (kontrakt DASH-1) obsługiwane; `href` z JSON-a tylko http(s). Pełna tabela: SKILL.md. */
(function (scope, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory();
  else scope.DashboardRenderer = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const STATE_LABEL = { done: "zrealizowany", active: "w trakcie", planned: "zaplanowany" };
  const STEP_LABEL = { done: "zrobiony", active: "w toku", planned: "zaplanowany", reported: "zrobiony wg checkpointu (niezaznaczony w planie)" };
  const STATUS_PL = { draft: "szkic", approved: "zatwierdzony", in_progress: "w realizacji", done: "zakończony", implemented: "wdrożony" };
  const ACT_ICON = { spec: "i-spec", plan: "i-plan", real: "i-run" };
  const GATE_KEY = { spec: "gate_go_plan", plan: "gate_go_implement", cp: "gate_cp", ready: "gate_ready" };
  const WAIT_SOURCE = { silence: "czas od ostatniej linii checkpoints.md (próg z serwera)", checkpoint: "znacznik „czeka:” w ostatnim checkpoincie", plan: "status planu", spec: "status speca", prototype: "akceptacja ekranów prototypu" };
  const LIVE_TEXT = { live: "Na żywo", polling: "Odświeżam co 5 s", down: "Brak połączenia", connecting: "Łączę…" };
  const LIVE_TIP = {
    live: "Zmiany w planie, specu i checkpointach pojawiają się od razu (SSE).",
    polling: "Kanał na żywo niedostępny — strona pyta serwer co 5 s i sama wróci do trybu na żywo.",
    down: "Serwer statusu nie odpowiada. Strona ponawia próby automatycznie.",
    connecting: "Łączę z serwerem statusu…",
  };
  const ARM_MS = 5000;
  // Widz zdalny (approvals.viewer, Host z --read-host; brak pola = false): odczyt bez zapisu. Przyciski zgody są nieaktywne
  // z jednym powodem, a renderer nie woła actions.approve.
  const isViewer = (d) => { const ap = d && d.approvals; return Boolean(ap && ap.viewer === true); };
  const VIEWER_TEXT = "Zgody daje prowadzący na komputerze z sesją.";
  const ID_RE = /(?<![\w-])([A-Z]{1,3}-?\d+[a-z]?)(?![\w-])/g;
  const PHRASE_RE = /\b(zrobione|w toku|ryzyko|PASS|FAIL|BLOCKED|restart sesji)\b/gi;
  const FORBIDDEN_ATTR = /^(?:on|style$|srcdoc$)/i;

  // Sprite ikon (symbole <symbol id="i-…">): [viewBox, [[tag, atrybuty], …]]. Renderer tworzy go sam przy create().
  const SPRITE = {
    "i-spec": ["0 0 24 24", [["path", { "d": "M6 2h9l5 5v15H6z", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linejoin": "round" }], ["path", { "d": "M14 2v6h6M9 13h8M9 17h6", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round" }]]],
    "i-plan": ["0 0 24 24", [["path", { "d": "M9 6h11M9 12h11M9 18h11", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round" }], ["path", { "d": "M3.5 6l1.5 1.5L7.5 5M3.5 12l1.5 1.5 2.5-2.5", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round" }], ["circle", { "cx": "5", "cy": "18", "r": "1.6", "fill": "currentColor" }]]],
    "i-run": ["0 0 24 24", [["path", { "d": "M12 15l-3-3c1-4 4-8 11-9-1 7-5 10-9 11zM9 12H5l2-4h4M12 15v4l4-2v-4", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linejoin": "round" }], ["path", { "d": "M6 16c-2 1-2 4-2 4s3 0 4-2", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round" }]]],
    "i-check": ["0 0 24 24", [["path", { "d": "M5 12.5l4.5 4.5L19 7.5", "fill": "none", "stroke": "currentColor", "stroke-width": "2.6", "stroke-linecap": "round", "stroke-linejoin": "round" }]]],
    "i-clock": ["0 0 24 24", [["circle", { "cx": "12", "cy": "12", "r": "9", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8" }], ["path", { "d": "M12 7v5l3 2", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round" }]]],
    "i-sync": ["0 0 24 24", [["path", { "d": "M20 11a8 8 0 0 0-14.3-4.3L4 9M4 13a8 8 0 0 0 14.3 4.3L20 15", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round" }], ["path", { "d": "M4 4v5h5M20 20v-5h-5", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round" }]]],
    "i-branch": ["0 0 24 24", [["circle", { "cx": "6", "cy": "5", "r": "2.2", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8" }], ["circle", { "cx": "6", "cy": "19", "r": "2.2", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8" }], ["circle", { "cx": "18", "cy": "8", "r": "2.2", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8" }], ["path", { "d": "M6 7.2v9.6M18 10.2c0 4-6 3-11.2 6.6", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8" }]]],
    "i-doc": ["0 0 24 24", [["path", { "d": "M6 2h9l5 5v15H6z", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linejoin": "round" }]]],
    "i-flag": ["0 0 24 24", [["path", { "d": "M5 21V4M5 4h11l-2 4 2 4H5", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round" }]]],
    "i-sun": ["0 0 24 24", [["circle", { "cx": "12", "cy": "12", "r": "4.2", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8" }], ["path", { "d": "M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round" }]]],
    "i-moon": ["0 0 24 24", [["path", { "d": "M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linejoin": "round" }]]],
    "i-proto": ["0 0 24 24", [["rect", { "x": "3", "y": "4", "width": "18", "height": "14", "rx": "2", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8" }], ["path", { "d": "M3 8h18M8 21h8", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round" }], ["circle", { "cx": "6", "cy": "6", "r": ".9", "fill": "currentColor" }]]],
    "i-bell": ["0 0 24 24", [["path", { "d": "M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linejoin": "round" }], ["path", { "d": "M10 20.5a2.2 2.2 0 0 0 4 0", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round" }]]],
    "i-chev": ["0 0 24 24", [["path", { "d": "M7 10l5 5 5-5", "fill": "none", "stroke": "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" }]]],
  };

  // ------------------------------------------------------------------ budowa DOM (dane zawsze jako tekst)
  function builder(doc) {
    function dress(el, attrs, kids) {
      if (attrs) {
        for (const k of Object.keys(attrs)) {
          const v = attrs[k];
          if (v == null || v === false) continue;
          if (k === "on") { for (const type of Object.keys(v)) el.addEventListener(type, v[type]); continue; }
          if (FORBIDDEN_ATTR.test(k)) throw new Error("dashboard-render: zakazany atrybut " + k);
          el.setAttribute(k, v === true ? "" : String(v));
        }
      }
      put(el, kids);
      return el;
    }
    function put(el, kids) {
      for (const kid of kids) {
        if (kid == null || kid === false || kid === "") continue;
        if (Array.isArray(kid)) put(el, kid);
        else if (typeof kid === "object" && typeof kid.nodeType === "number") el.appendChild(kid);
        else el.appendChild(doc.createTextNode(String(kid)));
      }
    }
    const h = (tag, attrs, ...kids) => dress(doc.createElement(tag), attrs, kids);
    const svg = (tag, attrs, ...kids) => dress(doc.createElementNS(SVG_NS, tag), attrs, kids);
    const fill = (el, ...kids) => { el.textContent = ""; put(el, kids); return el; };
    return { h, svg, fill };
  }

  function buildSprite(doc) {
    const { svg } = builder(doc);
    return svg("svg", { width: "0", height: "0", class: "sprite", "aria-hidden": "true" },
      Object.keys(SPRITE).map((id) => svg("symbol", { id, viewBox: SPRITE[id][0] },
        SPRITE[id][1].map(([tag, attrs]) => svg(tag, attrs)))));
  }

  // ------------------------------------------------------------------ formatowanie (czas tylko z deps.now)
  const fmtDur = (ms) => {
    if (!(ms >= 0)) return "—";
    const s = Math.floor(ms / 1000), hh = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
    return hh ? `${hh}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${m}:${String(ss).padStart(2, "0")}`;
  };
  const fmtTime = (iso) => iso ? new Date(iso).toLocaleString("pl-PL", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" }) : "—";
  const baseName = (path) => String(path || "").split("/").pop();
  // Adres z JSON-a do href: tylko http(s); inny schemat (javascript:, data:, …) = brak href (element zostaje tekstem).
  const safeUrl = (url) => (typeof url === "string" && /^https?:\/\/[^\s]/i.test(url.trim()) ? url.trim() : null);

  function splitBy(text, re, make) {
    const out = [];
    let last = 0, m;
    re.lastIndex = 0;
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index));
      out.push(make(m[1]));
      last = m.index + m[0].length;
      if (!m[0].length) re.lastIndex += 1;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }

  // ------------------------------------------------------------------ sekcje aktów: readiness, pytania, podkroki
  // Funkcje modułu wołane z kontekstem egzemplarza (`this` = {h, icon, desc, openDoc, st}); wartości z danych zawsze
  // przez esc(): tu esc daje tekst węzła (String), bo DOM powstaje z textContent, nie z szablonu HTML.
  const esc = (v) => String(v ?? "");
  const READY_STATE = { PASS: ["is-pass", "PASS"], BLOCKED: ["is-blocked", "BLOCKED"] };
  // Readiness (akt II): brief gotowości z .skillsync/runs/<run>/readiness.md; źródło pliku = readiness.source z JSON.
  // „Brief dla innej wersji planu”: skrócony readiness.planSha nie jest prefiksem pełnego plan.fileSha.
  function readinessTile(d) {
    const { h, icon, desc, openDoc } = this;
    const r = d.readiness || {};
    const [cls, label] = READY_STATE[r.status] || ["is-none", "brak danych"];
    const sha = r.planSha ? String(r.planSha).slice(0, 8) : "";
    const stale = Boolean(r.planSha && d.plan && d.plan.fileSha && !String(d.plan.fileSha).startsWith(String(r.planSha)));
    const facts = [r.date ? h("span", null, esc(r.date)) : null, sha ? h("span", { class: "mono" }, "plan " + esc(sha)) : null,
      stale ? h("span", { class: "chip s-warn" }, "brief dla innej wersji planu") : null].filter(Boolean);
    const open = r.source ? h("button", { class: "act-doc", type: "button", "data-open-doc": esc(r.source), "data-tip": esc(r.source) + " w zakładce Dokumenty",
      on: { click: () => openDoc(r.source) } }, icon("i-doc"), "readiness.md") : null;
    return h("div", { class: "readiness " + cls },
      h("span", { class: "stage readiness-chip", ...desc("stage_readiness", r.source || "brak pliku readiness.md w przebiegu") },
        cls === "is-pass" ? icon("i-check") : null, "Readiness: " + esc(label)),
      facts.length ? h("span", { class: "readiness-facts" }, facts) : null,
      open);
  }
  // Podkroki Fn.k z czasem (akt III): rozwijana lista; stan rozwinięcia przeżywa odświeżenie (st.substepsOpen).
  function substepsBlock(d) {
    const { h, desc, st } = this;
    const items = d.substeps || [];
    if (!items.length) return null;
    const time = (s) => (s.seconds != null ? fmtDur(s.seconds * 1000) : s.missingData ? "brak danych" : esc(STEP_LABEL[s.state] || s.state));
    const box = h("details", { class: "substeps", open: Boolean(st.substepsOpen) },
      h("summary", desc("substeps_time"), "Podkroki i czas (" + items.length + ")"),
      h("ul", { class: "sub-list" }, items.map((s) => h("li", { class: s.missingData ? "is-nodata" : "" },
        h("span", { class: "mono q-id" }, esc(s.id)), h("span", { class: "tt" }, esc(s.title)), h("span", { class: "mono sub-t" }, time(s))))));
    box.addEventListener("toggle", () => { st.substepsOpen = box.hasAttribute("open"); });
    return box;
  }

  // ------------------------------------------------------------------ renderer
  function create(root, deps) {
    if (!root || !root.ownerDocument) throw new TypeError("dashboard-render: create(root, deps) wymaga elementu DOM");
    deps = deps || {};
    if (typeof deps.now !== "function") throw new TypeError("dashboard-render: deps.now() (ms) jest wymagane");
    const doc = root.ownerDocument;
    const { h, svg, fill } = builder(doc);
    const actions = deps.actions || {};
    const storage = deps.storage || { get: () => null, set: () => {} };
    const theme = deps.theme || null;
    const now = deps.now;
    const raf = typeof deps.raf === "function" ? deps.raf : typeof requestAnimationFrame === "function" ? requestAnimationFrame : (fn) => { fn(); return 0; };
    const cancelRaf = typeof deps.cancelRaf === "function" ? deps.cancelRaf : typeof cancelAnimationFrame === "function" ? cancelAnimationFrame : () => {};
    const timers = deps.timers && typeof deps.timers.set === "function" && typeof deps.timers.clear === "function" ? deps.timers
      : { set: (fn, ms) => setTimeout(fn, ms), clear: (t) => clearTimeout(t) };
    const texts = deps.texts || {};
    const liveText = Object.assign({}, LIVE_TEXT, texts.live);
    const liveTip = Object.assign({}, LIVE_TIP, texts.liveTip);
    const noConnection = typeof texts.noConnection === "string" ? texts.noConnection : "Brak połączenia z serwerem statusu.";
    // Czy ten host może dawać zgody: domyślnie approvals.here z JSON-a (/api/status); host bez tego pola (webview) podaje canApprove.
    const canApprove = (d) => (typeof deps.canApprove === "function" ? Boolean(deps.canApprove(d)) : Boolean(d.approvals && d.approvals.here));
    const viewport = typeof deps.viewport === "function" ? deps.viewport
      : () => { const v = doc.defaultView; return v ? { width: v.innerWidth, height: v.innerHeight } : { width: 1024, height: 768 }; };

    const st = {
      data: null, prev: null, view: "progress", doc: null, docData: null, desc: null,
      armed: null, armedTimer: null, approving: false, notice: null, noticeTimer: null, visible: true, openPhases: {},
      silenceShown: false, silenceMin: 0, descFor: null, descPinned: false, docButtons: [], timers: new Set(), frames: new Set(), destroyed: false,
    };
    const later = (fn, ms) => { const t = timers.set(() => { st.timers.delete(t); if (!st.destroyed) fn(); }, ms); st.timers.add(t); return t; };
    const cancel = (t) => { if (t) { timers.clear(t); st.timers.delete(t); } };
    // Toast wyniku zgody gaśnie sam (ok 10 s, błąd 30 s), a odliczanie biegnie tylko przy widocznej karcie (host woła `setVisible`),
    // żeby komunikat o błędzie nie zniknął, zanim ktokolwiek go zobaczy. „×” zamyka od razu.
    const NOTICE_MS = { ok: 10000, err: 30000 };
    function setNoticeState(n) {
      cancel(st.noticeTimer); st.noticeTimer = null;
      st.notice = n || null;
      if (st.notice && st.visible) st.noticeTimer = later(() => { st.noticeTimer = null; st.notice = null; render(); }, st.notice.ok ? NOTICE_MS.ok : NOTICE_MS.err);
    }
    // Element banera „czeka” jest nieaktualny, gdy jego zgoda jest zapisana, a sesja już ruszyła: raportowała po zgodzie (`run.lastReportAt` to
    // mtime całego checkpoints.md, więc to sygnał ruchu, nie treści) albo zgoda ma ponad 10 min (kafelek aktu pokazuje „zgoda dana”, a ciszę
    // pilnuje osobny baner `silence`). Zgoda świeża i bez raportu zostaje z podpowiedzią „napisz »dalej«”. Wartość `delivery.ahead` nie jest
    // sygnałem wykonania: `pr-run` wypycha po każdym commicie, a READY nie zmienia liczby commitów.
    const CONSENT_FRESH_MS = 10 * 60000;
    function waitDone(d, w) {
      const act = w.approve && ((d.approvals && d.approvals.actions) || []).find((x) => x.kind === w.approve);
      if (!act || act.state !== "after") return false;
      const at = act.given && act.given.at ? Date.parse(act.given.at) : NaN;
      if (!Number.isFinite(at)) { // zgoda ze statusu pliku (bez wpisu w dzienniku): spec/plan, gdy plan już trwa albo się skończył
        const plan = d.plan && d.plan.status;
        return (w.approve === "spec" || w.approve === "plan") && (plan === "in_progress" || plan === "done");
      }
      const reported = d.run && d.run.lastReportAt ? Date.parse(d.run.lastReportAt) : NaN;
      if (Number.isFinite(reported) && reported > at) return true;
      return now() - at > CONSENT_FRESH_MS;
    }

    const icon = (id, cls = "ic") => svg("svg", { class: cls, "aria-hidden": "true" }, svg("use", { href: "#" + id }));
    const plural = (n, one, few, many) => (n === 1 ? one : n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14) ? few : many);
    const mono = (text) => h("span", { class: "mono" }, text);
    const statusChip = (s) => {
      if (!s) return null;
      const cls = s === "done" || s === "approved" || s === "implemented" ? "s-done" : s === "in_progress" ? "s-active" : s === "draft" ? "s-warn" : "s-planned";
      return h("span", { class: "chip " + cls }, STATUS_PL[s] || s);
    };
    const fmtAgo = (iso) => {
      if (!iso) return "—";
      const s = Math.max(0, Math.round((now() - Date.parse(iso)) / 1000));
      if (s < 10) return "przed chwilą";
      if (s < 60) return `${s} s temu`;
      if (s < 3600) return `${Math.floor(s / 60)} min temu`;
      return new Date(iso).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
    };
    // Dymek z descriptions.json: klucz + opcjonalny dopisek; bez klucza tylko krótki dymek CSS (data-tip).
    function desc(key, extra) {
      if (!key) return extra ? { "data-tip": extra } : {};
      return { "data-desc": key, "data-desc-extra": extra || null, tabindex: "0", "aria-describedby": "descTip" };
    }
    // Kontekst sekcji modułu (readinessTile, substepsBlock); openDoc to deklaracja funkcji niżej (hoisting).
    const kit = { h, icon, desc, openDoc: (path) => openDoc(path), st };
    // Odnośnik zewnętrzny: host może przejąć otwarcie (webview), strona zostawia zwykłe <a target=_blank>.
    const linkClick = (kind, id, url) => (e) => {
      if (typeof actions.openLink === "function" && actions.openLink(kind, id, url) === true) e.preventDefault();
    };
    const navigate = (hash) => { if (typeof deps.onNavigate === "function") deps.onNavigate(hash); };
    const previewMode = () => typeof actions.loadDoc === "function";

    // ---------------------------------------------------------------- szkielet strony
    const ui = {};
    root.classList.add("dashboard-root");
    if (deps.sprite !== false) root.appendChild(buildSprite(doc));
    ui.title = h("h1", { id: "title" }, "Wczytuję status…");
    ui.meta = h("div", { class: "meta", id: "meta" });
    ui.live = h("span", { id: "live", class: "live is-connecting", role: "status", "aria-live": "polite", "data-tip": liveTip.connecting },
      h("span", { class: "live-dot" }), ui.liveText = h("span", { class: "live-text" }, liveText.connecting));
    if (theme) {
      ui.themeBtn = h("button", { id: "themeBtn", class: "btn theme-btn", type: "button", "aria-label": "Przełącz motyw jasny/ciemny",
        "data-tip": "Motyw jasny/ciemny (klawisz T)", on: { click: toggleTheme } },
        icon("i-moon", "ic i-moon"), icon("i-sun", "ic i-sun"));
    }
    ui.planBtnText = h("span", { id: "planBtnText" }, "Plan");
    ui.planAuto = h("span", { id: "planAuto", class: "chip plan-auto", hidden: true,
      "aria-label": "Plan wybrany automatycznie; inny plan wybierzesz z listy" }, "wybrany automatycznie");
    ui.planBtn = h("button", { id: "planBtn", class: "btn", "aria-haspopup": "listbox", "aria-expanded": "false",
      "data-tip": "Wybierz plan (domyślnie: bieżący w realizacji)", on: { click: (e) => { e.stopPropagation(); toggleMenu(); } } },
      svg("svg", { class: "ic" }, svg("use", { href: "#i-plan" })), ui.planBtnText, ui.planAuto, svg("svg", { class: "ic chev" }, svg("use", { href: "#i-chev" })));
    ui.planMenu = h("ul", { id: "planMenu", class: "menu", role: "listbox", hidden: true });
    ui.waitBanner = h("div", { id: "waitBanner", class: "wait", role: "status", "aria-live": "polite", hidden: true });
    ui.docCount = h("span", { class: "count", id: "docCount" });
    ui["tab-progress"] = h("button", { role: "tab", id: "tab-progress", "aria-controls": "view-progress", "aria-selected": "true",
      "data-tip": "Akty, postęp i checkpointy (klawisz P)", on: { click: () => setView("progress") } },
      svg("svg", { class: "ic" }, svg("use", { href: "#i-run" })), "Postęp");
    ui["tab-docs"] = h("button", { role: "tab", id: "tab-docs", "aria-controls": "view-docs", "aria-selected": "false",
      "data-tip": "Spec, plan i pliki przebiegu (klawisz D)", on: { click: () => setView("docs") } },
      svg("svg", { class: "ic" }, svg("use", { href: "#i-doc" })), "Dokumenty ", ui.docCount);
    ui.protoLink = h("span", { id: "protoLink", class: "tab-extra", hidden: true });
    ui.acts = h("div", { class: "acts", id: "acts" },
      h("div", { class: "act skeleton" }), h("div", { class: "act skeleton" }), h("div", { class: "act skeleton wide" }));
    ui.phases = h("div", { class: "phases", id: "phases" });
    ui.feed = h("ol", { class: "feed", id: "feed" });
    ui.docList = h("nav", { class: "doc-list", id: "docList", "aria-label": "Dokumenty zadania" });
    ui.docView = h("article", { class: "doc-view", id: "docView", "aria-live": "polite", on: { click: mdClick } },
      h("div", { class: "empty" }, previewMode() ? "Wybierz dokument z listy po lewej. Podgląd odświeża się sam, gdy plik się zmieni." : "Wybierz dokument z listy po lewej."));
    ui["view-progress"] = h("section", { id: "view-progress", role: "tabpanel", "aria-labelledby": "tab-progress" },
      ui.acts,
      h("div", { class: "work" }, ui.phases,
        h("aside", { class: "feed-box", "aria-labelledby": "feedTitle" },
          h("h2", { id: "feedTitle", "data-desc": "checkpoints", tabindex: "0" }, svg("svg", { class: "ic" }, svg("use", { href: "#i-flag" })), "Ostatnie checkpointy"),
          ui.feed)));
    ui["view-docs"] = h("section", { id: "view-docs", role: "tabpanel", "aria-labelledby": "tab-docs", hidden: true },
      h("div", { class: "docs" }, ui.docList, ui.docView));
    ui.foot = h("footer", { class: "foot", id: "foot" });
    ui.descTip = h("div", { id: "descTip", class: "desc-tip", role: "tooltip", hidden: true });

    // Wrapper z container-type: układ zależy od szerokości kolumny hosta (app.css, @container), nie okna. Dymek zostaje poza nim
    // (inline-size containment tworzy blok zawierający dla position: fixed).
    root.appendChild(h("div", { class: "dash-wrap" }, h("header", { class: "top" },
      h("div", { class: "brand" },
        h("div", { class: "logo", "aria-hidden": "true" }, svg("svg", null, svg("use", { href: "#i-run" }))),
        h("div", { class: "brand-text" }, ui.title, ui.meta)),
      h("div", { class: "top-right" }, ui.themeBtn || null, ui.live, h("div", { class: "plan-pick" }, ui.planBtn, ui.planMenu))),
      ui.waitBanner,
      h("nav", { class: "tabs", role: "tablist", "aria-label": "Widok" }, ui["tab-progress"], ui["tab-docs"], ui.protoLink),
      h("main", null, ui["view-progress"], ui["view-docs"]),
      ui.foot));
    root.appendChild(ui.descTip);

    // ---------------------------------------------------------------- dymki opisów (descriptions.json)
    function showDesc(el) {
      const tipEl = ui.descTip;
      const item = (st.desc || {})[el.getAttribute("data-desc")];
      const extra = el.getAttribute("data-desc-extra");
      if (!item && !extra) return hideDesc();
      fill(tipEl, item ? [h("strong", null, item.title), h("span", null, item.text)] : null, extra ? h("em", null, extra) : null);
      tipEl.hidden = false;
      const vp = viewport();
      const r = el.getBoundingClientRect(), w = Math.min(380, vp.width - 32);
      tipEl.style.width = w + "px";
      const left = Math.max(16, Math.min(r.left, vp.width - w - 16));
      const below = r.bottom + 8 + tipEl.offsetHeight < vp.height;
      tipEl.style.left = left + "px";
      tipEl.style.top = (below ? r.bottom + 8 : Math.max(8, r.top - tipEl.offsetHeight - 8)) + "px";
      st.descFor = el;
    }
    function hideDesc() { ui.descTip.hidden = true; st.descFor = null; st.descPinned = false; }
    const descOf = (e) => (e.target && typeof e.target.closest === "function" ? e.target.closest("[data-desc]") : null);
    const rootListeners = {
      mouseover: (e) => { const el = descOf(e); if (el && !st.descPinned) showDesc(el); },
      mouseout: (e) => { const el = descOf(e); if (el && !st.descPinned && !el.contains(e.relatedTarget)) hideDesc(); },
      focusin: (e) => { const el = descOf(e); if (el) showDesc(el); },
      focusout: (e) => { if (descOf(e) && !st.descPinned) hideDesc(); },
      pointerup: (e) => { // dotyk: stuknięcie otwiera i zamyka
        if (e.pointerType !== "touch") return;
        const el = descOf(e);
        if (el && st.descFor === el && st.descPinned) return hideDesc();
        if (el) { showDesc(el); st.descPinned = true; } else if (st.descPinned) hideDesc();
      },
      click: () => toggleMenu(false),
    };
    for (const type of Object.keys(rootListeners)) root.addEventListener(type, rootListeners[type]);

    // ---------------------------------------------------------------- widok całości
    function render() {
      const d = st.data;
      if (!d) return;
      const plan = d.plan;
      const specAct = d.mode === "spec" && d.spec && d.spec.exists;
      const runAct = d.mode === "run";
      const title = plan ? String(plan.title || "").replace(/^Plan:\s*/i, "")
        : specAct ? String(d.spec.title || d.spec.path).replace(/^Spec:\s*/i, "")
        : runAct ? "Akt I: specyfikacja w przygotowaniu" : "Brak planu w realizacji";
      ui.title.textContent = title;
      const waiting = (d.waiting || []).filter((w) => !waitDone(d, w));
      const progress = d.progress || {};
      if (typeof deps.onTitle === "function") {
        deps.onTitle((waiting.length ? "⏳ Czeka · " : "") + (plan ? `${progress.percent}% · ` : specAct || runAct ? "Akt I · " : "") + title + " — status");
      }
      renderWaiting(d);

      const run = d.run || {};
      const meta = [];
      ui.elapsed = null; ui.ago = null;
      if (run.id) meta.push(h("span", { class: "m", "data-tip": `Identyfikator przebiegu (.skillsync/runs/${run.id})` }, icon("i-flag"), mono(run.id)));
      if (plan) meta.push(h("span", { class: "m", "data-tip": "Status planu (nagłówek Status:)" }, statusChip(plan.status)));
      else if (specAct) meta.push(h("span", { class: "m", "data-tip": "Akt I: spec bez planu (plan powstaje przy „GO plan”); status speca" }, statusChip(d.spec.status)));
      if (run.startIso) {
        ui.elapsed = h("span", { class: "mono", id: "elapsed" }, fmtDur(now() - Date.parse(run.startIso)));
        meta.push(h("span", { class: "m", "data-tip": `Czas od startu aktu III (RUN= w checkpoints.md, start ${fmtTime(run.startIso)})` }, icon("i-clock"), ui.elapsed));
      }
      ui.ago = h("span", { id: "ago", "data-iso": d.lastUpdate || "" }, fmtAgo(d.lastUpdate));
      meta.push(h("span", { class: "m", "data-tip": "Ostatnia zmiana planu, speca lub checkpointów" }, icon("i-sync"), ui.ago));
      if (d.git && d.git.branch) {
        const g = d.git;
        const host = g.remote ? (g.remote.host === "azure" ? "Azure DevOps" : "GitHub") : "";
        const name = `${g.branch}${g.head ? " @ " + String(g.head).slice(0, 7) : ""}`;
        const branch = g.branchUrl && g.branchPushed
          ? h("a", { class: "mono git-link", href: safeUrl(g.branchUrl), target: "_blank", rel: "noopener", "data-tip": `Otwórz gałąź w ${host}`,
            on: { click: linkClick("branch", g.branch, g.branchUrl) } }, name)
          : h("span", { class: "mono", "data-tip": g.branchUrl ? "Gałąź tylko lokalnie (brak na origin), bez linku" : "Gałąź i HEAD repozytorium" }, name);
        meta.push(h("span", { class: "m" }, icon("i-branch"), branch));
        // `git.branchPushed` = origin/<gałąź> istnieje; liczba commitów przed origin to `delivery.ahead` (null = nieznana, 0 = nic do wypchnięcia: bez znacznika).
        const dv = d.delivery || {};
        const ahead = typeof dv.ahead === "number" && dv.ahead > 0 ? dv.ahead : 0;
        const remoteName = dv.remote || `origin/${g.branch}`;
        if (ahead) {
          // w trybie `direct` lokalne commity są normą do „CP”: ostrzeżenie dopiero, gdy plan jest zakończony
          const calm = dv.mode === "direct" && !(plan && plan.status === "done");
          const tip = `${ahead} ${plural(ahead, "commit", "commity", "commitów")} lokalnie przed ${remoteName}${dv.pushed === false ? " (nie ma jej jeszcze na origin)" : ""}; „CP” je wypchnie`;
          meta.push(h("span", { class: "m" }, h("span", { class: `chip ${calm ? "s-planned" : "s-warn"} ahead-chip`, "data-tip": tip },
            `${ahead} ${plural(ahead, "niewypchnięty", "niewypchnięte", "niewypchniętych")}`)));
        }
      }
      if (plan && plan.pr) {
        meta.push(h("span", { class: "m" }, h("a", { class: "chip s-active git-link", href: safeUrl(plan.pr.url), target: "_blank", rel: "noopener",
          "data-tip": "Pull request z linii PR: planu", on: { click: linkClick("pr", plan.pr.number, plan.pr.url) } }, `PR #${plan.pr.number}`)));
      }
      fill(ui.meta, meta);

      ui.planBtnText.textContent = plan ? baseName(plan.path).replace(/\.md$/, "")
        : specAct ? baseName(d.spec.path).replace(/\.md$/, "") + " (akt I)" : runAct ? "przebieg (akt I)" : "Plan";
      const autoPick = Boolean(plan && d.planAuto === true);
      ui.planAuto.hidden = !autoPick;
      ui.planBtn.setAttribute("data-tip", plan
        ? `Plan: ${plan.path}${autoPick ? " (wybrany automatycznie; inny wybierzesz z listy)" : " (wybrany ręcznie)"}`
        : "Wybierz plan (domyślnie: bieżący w realizacji)");
      renderProtoLink(d);
      renderPlanMenu();
      renderActs(d);
      renderPhases(d);
      renderFeed(d);
      renderDocList(d);
      const app = d.app || {}, project = d.project || {};
      fill(ui.foot,
        h("span", null, `dashboard ${app.version ?? ""}`),
        h("span", null, "projekt: ", mono(project.path)),
        h("span", null, `${d.approvals && d.approvals.enabled && canApprove(d) && !isViewer(d) ? "odczyt + zgody" : "tylko odczyt"} · odświeżono ${new Date(d.generatedAt).toLocaleTimeString("pl-PL")}`));
    }

    // ---------------------------------------------------------------- akty
    function renderActs(d) {
      const acts = d.acts || [];
      const anyActive = acts.some((a) => a.state === "active");
      const fills = [];
      fill(ui.acts, acts.map((a) => {
        const glow = a.state === "active" || (!anyActive && a.key === "real" && a.state === "done");
        const stages = (a.stages || []).map((s) => h("span", { class: "stage" + (s.done ? " is-done" : ""), ...desc(s.key, s.tip) }, s.done ? icon("i-check") : null, s.label));
        const warn = a.warning ? h("div", { class: "act-warn", ...desc("warning_pre_implement", a.warning) }, "⚠ " + a.warning) : null;
        let body;
        if (a.key === "real") {
          const p = d.progress || {};
          const cur = p.current;
          const barCls = a.state === "done" ? "is-done" : a.state === "active" ? "is-active" : "";
          body = [
            h("div", { class: "big" },
              h("span", { class: "pct" }, `${a.state === "done" && p.percent < 100 ? "✓ " : ""}${p.percent}%`),
              p.pendingPercent ? h("span", { class: "pend", "data-tip": `Kroki zgłoszone jako zrobione w checkpointach (${(p.reported || []).join(", ")}), jeszcze niezaznaczone [x] w planie — czekają na review` }, `(+${p.pendingPercent}% do weryfikacji)`) : null,
              h("span", { class: "of", "data-tip": "Kroki odhaczone [x] w planie" }, `${p.done} z ${p.total} kroków zrobionych`)),
            bar(p.percent, p.pendingPercent, barCls, "Postęp realizacji", fills),
            cur ? h("div", { class: "now", "data-tip": `Teraz wykonywane: ${(p.active || []).join(", ")}, ${cur.index}. pozycja z ${p.total} w planie (${p.activeSource === "checkpoint" ? "z ostatniego checkpointu „w toku”" : "pierwszy niezaznaczony krok"})` },
              h("span", { class: "dot-live" }), h("span", { class: "txt" }, "teraz: ", mono(cur.id), " " + (cur.title ?? "")),
              p.parallel ? h("span", { class: "par" }, `+${p.parallel} równolegle`) : null)
              : h("div", { class: "act-detail" }, a.detail || ""),
            h("div", { class: "stages" }, stages),
            substepsBlock.call(kit, d),
            h("div", { class: "act-actions" }, approveButton(d, "cp", "act-approve"), approveButton(d, "ready", "act-approve")),
          ];
        } else {
          const docRef = a.key === "spec" ? (d.spec && d.spec.exists ? { path: d.spec.path, label: "Otwórz specyfikację" } : null)
            : a.key === "plan" ? (d.plan ? { path: d.plan.path, label: "Otwórz plan" } : null) : null;
          const docLink = docRef ? h("button", { class: "act-doc", type: "button", "data-open-doc": docRef.path, "data-tip": `${docRef.path} w zakładce Dokumenty`,
            on: { click: () => openDoc(docRef.path) } }, icon("i-doc"), docRef.label) : null;
          const approve = approveButton(d, a.key === "spec" ? "spec" : "plan", "act-approve");
          const extra = a.key === "plan" ? readinessTile.call(kit, d) : null;
          body = [h("div", { class: "act-detail" }, a.detail || " "), h("div", { class: "stages" }, stages), extra, warn, h("div", { class: "act-actions" }, docLink, approve)];
        }
        return h("div", { class: `act s-${a.state}${glow ? " s-active" : ""}`, "data-key": a.key },
          h("div", { class: "roman", "aria-hidden": "true" }, a.roman),
          h("div", { class: "act-head", ...desc("act_" + a.key) },
            h("div", { class: "act-icon" }, icon(ACT_ICON[a.key])),
            h("div", { class: "act-name" }, h("small", null, `Akt ${a.roman}`), a.name),
            h("span", { class: `chip s-${a.state} chip-end` }, STATE_LABEL[a.state])),
          body);
      }));
      animateBars(fills);
    }

    // ---------------------------------------------------------------- fazy i kroki
    function emptyPhase(...kids) { fill(ui.phases, h("div", { class: "phase" }, h("div", { class: "empty" }, kids))); }
    function renderPhases(d) {
      const p = d.progress || {};
      const phases = p.phases || [];
      const prevStates = {};
      if (st.prev && st.prev.progress) (st.prev.progress.phases || []).forEach((ph) => (ph.steps || []).forEach((s) => { prevStates[s.id] = s.state; }));
      if (!d.plan && d.mode === "spec") {
        return emptyPhase("Akt I: spec ", mono(d.spec && d.spec.path), ` (${(d.spec && d.spec.status) || "—"}) nie ma jeszcze planu. Kroki pojawią się tu same, gdy po „GO plan” powstanie plan w `, mono("docs/plans"), ".");
      }
      if (!d.plan && d.mode === "run") {
        return emptyPhase("Akt I: przebieg ", mono(d.run && d.run.id), " ruszył, specyfikacja jest w przygotowaniu. Spec pojawi się tu sam po zapisaniu w ", mono("docs/specs"), ", a kroki po „GO plan”.");
      }
      if (!d.plan) {
        return emptyPhase("Brak planu ze statusem draft, approved albo in_progress w ", mono("docs/plans"), ". Plan pojawi się tu sam po zapisaniu pliku.");
      }
      if (!phases.length) {
        return emptyPhase("Plan nie ma jeszcze sekcji ", mono("## Progress"), " z krokami ", mono("- [ ] S1 Tytuł"), ".");
      }
      const fills = [];
      fill(ui.phases, phases.map((ph) => {
        // Faza zamknięta zwija się do nagłówka (k/n); kliknięcie albo Enter rozwija.
        const foldable = ph.state === "done" && ph.total > 1;
        const folded = foldable && !st.openPhases[ph.name];
        const toggle = (e) => {
          if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          st.openPhases[ph.name] = !st.openPhases[ph.name];
          renderPhases(st.data);
        };
        const headAttrs = { class: "phase-head", ...desc("phase", `${ph.done}/${ph.total} podkroków`) };
        if (foldable) Object.assign(headAttrs, { role: "button", tabindex: "0", "aria-expanded": String(!folded), "data-phase-toggle": ph.name, on: { click: toggle, keydown: toggle } });
        return h("section", { class: `phase s-${ph.state}${folded ? " is-folded" : ""}` },
          h("div", headAttrs,
            h("div", null, ph.label ? h("div", { class: "lbl" }, ph.label) : null, h("div", { class: "nm" }, ph.short)),
            h("span", { class: "n" }, ph.state === "done" ? icon("i-check") : null, `${ph.done}/${ph.total}`)),
          bar(ph.percent, ph.pendingPercent, "thin " + (ph.state === "done" ? "is-done" : ""), ph.short, fills),
          h("ul", { class: "steps" }, (ph.steps || []).map((s) => {
            const changed = st.prev && prevStates[s.id] && prevStates[s.id] !== s.state;
            const tip = `${s.id} ${s.title} — ${STEP_LABEL[s.state]}${s.sha ? " · commit " + s.sha : ""}${s.note && !s.sha ? " · " + s.note : ""}`;
            const mark = s.state === "done" || s.state === "reported" ? icon("i-check") : null;
            return h("li", { class: `step s-${s.state}${changed ? " flash" : ""}`, ...desc(/^zamknięcie/i.test(s.title || "") ? "closure" : "substep", tip) },
              h("span", { class: "st", "aria-label": STEP_LABEL[s.state] ?? "" }, mark), h("span", { class: "sid" }, s.id), h("span", { class: "tt" }, s.title));
          })));
      }));
      animateBars(fills);
    }

    function bar(pct, pending, cls, label, fills) {
      const attrs = { class: `bar ${cls}`, role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct), "aria-label": String(label ?? "") };
      if (pending) {
        attrs["aria-valuetext"] = `${pct}%, plus ${pending}% do weryfikacji`;
        attrs["data-tip"] = `${pct}% zaznaczone w planie + ${pending}% zrobione wg checkpointu (czeka na weryfikację)`;
      }
      const done = h("div", { class: "fill", "data-w": String(pct) });
      fills.push(done);
      const pend = pending ? h("div", { class: "fill-pending", "data-w": String(pending) }) : null;
      if (pend) fills.push(pend);
      return h("div", attrs, done, pend);
    }
    // Szerokość paska przez CSSOM (dozwolone w CSP bez 'unsafe-inline'), w następnej klatce, żeby zadziałało przejście.
    function animateBars(fills) {
      let id = null, ran = false;
      id = raf(() => { ran = true; st.frames.delete(id); if (!st.destroyed) fills.forEach((f) => { f.style.width = f.getAttribute("data-w") + "%"; }); });
      if (id != null && !ran) st.frames.add(id); // raf może wykonać się od razu (host bez animacji)
    }

    // ---------------------------------------------------------------- checkpointy
    function decorateCheckpoint(text) {
      return splitBy(String(text ?? ""), ID_RE, (id) => h("span", { class: "sidc" }, id))
        .flatMap((part) => (typeof part === "string" ? splitBy(part, PHRASE_RE, (w) => h("span", { class: "ph" }, w)) : [part]));
    }
    function renderFeed(d) {
      const items = (d.run && d.run.checkpoints) || [];
      const prevTop = st.prev && st.prev.run && st.prev.run.checkpoints && st.prev.run.checkpoints[0];
      const prevCount = ((st.prev && st.prev.run && st.prev.run.checkpoints) || []).length;
      const fresh = st.prev ? Math.max(0, items.length - prevCount) : 0;
      fill(ui.feed, items.length
        ? items.map((c, i) => h("li", { class: i < fresh || (i === 0 && prevTop && prevTop.text !== c.text) ? "is-new" : "" },
          h("span", { class: "t" }, c.time), h("span", null, decorateCheckpoint(c.text))))
        : h("li", null, h("span"), h("span", { class: "empty" }, "Brak checkpointów — pojawią się, gdy przebieg zapisze ", mono("checkpoints.md"), ".")));
    }

    // ---------------------------------------------------------------- wybór planu
    function renderPlanMenu() {
      const d = st.data;
      const cur = d.plan && d.plan.path;
      const pick = (path) => () => {
        storage.set("plan", path);
        st.prev = null;
        toggleMenu(false);
        if (typeof actions.selectPlan === "function") actions.selectPlan(path);
      };
      fill(ui.planMenu,
        h("li", { role: "option", "data-plan": "", "aria-selected": String(d.planAuto), on: { click: pick(null) } },
          h("span", null, "Automatycznie: bieżący plan"), h("span", { class: "sub" }, "in_progress → approved → draft")),
        (d.plans || []).slice().reverse().map((p) => h("li", { role: "option", "data-plan": p.path, "aria-selected": String(!d.planAuto && p.path === cur), on: { click: pick(p.path || null) } },
          h("span", null, h("span", null, String(p.title || "").replace(/^Plan:\s*/i, "")), h("br"), h("span", { class: "sub" }, p.path)),
          statusChip(p.status))));
    }
    function toggleMenu(open) {
      const show = open ?? ui.planMenu.hidden;
      ui.planMenu.hidden = !show;
      ui.planBtn.setAttribute("aria-expanded", String(show));
    }

    // ---------------------------------------------------------------- zgody i baner „sesja czeka”
    const protoUrl = (d, slug) => (typeof deps.prototypeUrl === "function" ? deps.prototypeUrl(d.prototype && d.prototype.port, slug) : null);
    // Zgoda przyciskiem („GO plan”, „GO implement”, „CP”, „READY”): dwa kliknięcia (uzbrojenie na 5 s), potem actions.approve
    // z sha pokazanej wersji. Stany (z JSON-a): brak pozycji = przycisku nie ma; waiting = aktywny (tylko gdy canApprove: domyślnie approvals.here);
    // inny = nieaktywny „czeka na…”; after = nieaktywny „zgoda dana HH:MM”. Widz (isViewer) ma każdy przycisk poza „after”
    // nieaktywny z powodem VIEWER_TEXT i nigdy nie woła actions.approve.
    function approveButton(d, kind, cls) {
      const ap = d.approvals;
      const a = ap && ap.enabled && (ap.actions || []).find((x) => x.kind === kind);
      if (!a) return null;
      const viewer = isViewer(d);
      const to = a.target ? " → " + a.target : "";
      if (a.state === "after") {
        const at = a.given && a.given.at ? new Date(a.given.at).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" }) : "";
        return h("button", { class: `${cls} is-given`, type: "button", disabled: true, "data-state": "after", "data-gate": kind, ...desc(GATE_KEY[kind], a.reason) },
          icon("i-check"), `${a.word}${to}${at ? " " + at : ""}`);
      }
      if (a.state !== "waiting") {
        return h("button", { class: `${cls} is-waiting`, type: "button", disabled: true, "data-state": "running", "data-gate": kind, ...desc(GATE_KEY[kind], viewer ? VIEWER_TEXT : a.reason) }, `${a.word}${to}`);
      }
      if (!canApprove(d) || viewer) {
        return h("button", { class: cls, type: "button", disabled: true, "data-state": "waiting", "data-gate": kind,
          ...desc(GATE_KEY[kind], viewer ? VIEWER_TEXT : `Zgoda tylko z przeglądarki na komputerze z sesją (localhost albo --approve-host); tu wpisz „${a.word}” w sesji`) }, icon("i-check"), a.word);
      }
      const armed = st.armed === kind;
      const label = st.approving && armed ? "Zapisuję…" : armed ? `Na pewno? Kliknij, by dać „${a.word}”${to}` : `Zatwierdź „${a.word}”${to}`;
      const sha = String(a.sha || "");
      const what = kind === "cp" || kind === "ready" ? `${a.reason ? a.reason + "; " : ""}HEAD ${sha.slice(0, 8)}` : `${a.path} (wersja ${sha.slice(0, 8)})`;
      return h("button", { class: `${cls}${armed ? " is-armed" : ""}`, type: "button", "data-state": "waiting", "data-approve": kind, "data-gate": kind,
        ...desc(GATE_KEY[kind], `Zgoda jak wpisanie „${a.word}” w sesji — ${what}`), on: { click: () => approve(kind) } }, icon("i-check"), label);
    }
    async function approve(kind) {
      const a = ((st.data && st.data.approvals && st.data.approvals.actions) || []).find((x) => x.kind === kind);
      if (!a || st.approving || isViewer(st.data) || !canApprove(st.data)) return;
      if (st.armed !== kind) {
        st.armed = kind;
        cancel(st.armedTimer);
        st.armedTimer = later(() => { st.armed = null; render(); }, ARM_MS);
        return render();
      }
      cancel(st.armedTimer);
      st.approving = true; render();
      let res;
      try {
        if (typeof actions.approve !== "function") throw new Error("brak actions.approve");
        res = await actions.approve({ kind, sha: a.sha, path: a.path, word: a.word });
      } catch {
        res = { ok: false, word: a.word, text: noConnection };
      }
      if (st.destroyed) return;
      st.approving = false; st.armed = null;
      if (res) setNoticeState(res);
      render();
      if (typeof actions.refresh === "function") actions.refresh();
    }
    function wordTools(d, word) {
      const url = d.session && d.session.url;
      return url ? h("a", { class: "wait-go wait-session", href: safeUrl(url), target: "_blank", rel: "noopener", "data-tip": "Otwiera sesję (adres z --session-url)",
        on: { click: linkClick("session", url, url) } }, "Otwórz sesję") : null;
    }
    // Brak raportu postępu od N min (plan in_progress, sesja nie zgłosiła „czeka:”): sesja mogła się zawiesić.
    function silenceMinutes(d) {
      const s = d && d.silence;
      if (!s || !s.since) return 0;
      const minutes = Math.floor((now() - Date.parse(s.since)) / 60000);
      return minutes >= s.thresholdMin ? minutes : 0;
    }
    // Toast „Pytania do eksperta”: stan z plików pytań WSZYSTKICH przebiegów (`d.expertQuestions`), nie z widocznego speca. To stan, nie wynik
    // akcji, więc bez timera i bez „×”: znika dopiero, gdy open = 0. Odnośnik tylko, gdy host zna adres serwera prototypów (`deps.prototypeOrigin`).
    function questionsToast(d) {
      const eq = d.expertQuestions;
      const open = eq && Number.isFinite(eq.open) ? eq.open : 0;
      if (open <= 0) return null;
      const blocking = Number.isFinite(eq.blocking) ? eq.blocking : 0;
      const runs = Array.isArray(eq.runs) ? eq.runs : [];
      const origin = typeof deps.prototypeOrigin === "function" ? deps.prototypeOrigin(d.prototype && d.prototype.port) : null;
      let url = null;
      if (origin) {
        const path = runs.length === 1 && typeof runs[0].url === "string" && /^\/_experts\/[a-z0-9-]+$/.test(runs[0].url) ? runs[0].url : "/_experts";
        url = safeUrl(String(origin).replace(/\/+$/, "") + path);
      }
      const text = `Pytania do eksperta: ${open} otwartych (${blocking} blokujących)`;
      return h("div", { class: "wait-item is-info", "data-questions-toast": "" },
        icon("i-bell"),
        h("div", { class: "wait-txt" }, h("strong", null, text)),
        url ? h("a", { class: "wait-go", href: url, target: "_blank", rel: "noopener",
          on: { click: linkClick("experts", runs.length === 1 ? String(runs[0].slug || "") : "", url) } }, "Odpowiedz na pytania") : null);
    }
    function renderWaiting(d) {
      const el = ui.waitBanner;
      const quiet = silenceMinutes(d);
      st.silenceShown = quiet > 0;
      const items = (d.waiting || []).filter((w) => !waitDone(d, w)).concat(quiet ? [{ what: `brak raportu od ${quiet} min`, source: "silence", since: null,
        action: "Raport postępu powinien być po każdym kroku albo co 30 minut. Sesja mogła się zawiesić albo długo pracuje bez raportu: zajrzyj do sesji.", link: null }] : []);
      const n = st.notice;
      const note = n ? h("div", { class: "wait-item" + (n.ok ? " is-ok" : " is-err") },
        icon(n.ok ? "i-check" : "i-bell"),
        h("div", { class: "wait-txt" },
          h("strong", null, n.ok ? `Zgoda „${n.word}” zapisana` : `Nie zapisano „${n.word}”`),
          h("span", null, n.text)),
        n.ok ? wordTools(d, "dalej") : null,
        h("button", { class: "wait-x", type: "button", "data-notice-close": "", "aria-label": "Zamknij", on: { click: () => { setNoticeState(null); render(); } } }, "×")) : null;
      const qnote = questionsToast(d);
      if (!items.length && !note && !qnote) { el.hidden = true; fill(el); return; }
      el.hidden = false;
      fill(el, qnote, note, items.map((w) => {
        let link = null;
        if (w.link && w.link.kind === "doc") {
          link = h("button", { class: "wait-go", type: "button", "data-wait-doc": w.link.path, on: { click: () => openDoc(w.link.path) } }, `Otwórz ${baseName(w.link.path)}`);
        } else if (w.link && w.link.kind === "prototype") {
          const url = protoUrl(d, w.link.slug);
          link = h("a", { class: "wait-go", href: safeUrl(url), target: "_blank", rel: "noopener", on: { click: linkClick("prototype", w.link.slug, url) } }, `Otwórz prototyp ${w.link.slug}`);
        }
        const docRef = w.approve === "spec" ? d.spec : w.approve === "plan" ? d.plan : null;
        const act = w.approve && ((d.approvals && d.approvals.actions) || []).find((x) => x.kind === w.approve);
        const given = Boolean(docRef && ["approved", "in_progress", "done", "implemented"].includes(docRef.status)) || Boolean(act && act.state === "after");
        const tools = given ? wordTools(d, "dalej")
          : [w.approve ? approveButton(d, w.approve, "wait-go wait-approve") : null, w.word ? wordTools(d, w.word) : null];
        const action = given ? `Zgoda „${w.word}” jest już w pliku; napisz w sesji „dalej”, jeśli sesja jeszcze nie ruszyła.` : w.action;
        const since = w.since ? ` od ${w.since}` : "";
        const silence = w.source === "silence";
        return h("div", { class: "wait-item", ...desc(silence ? "banner_silence" : "banner_wait") },
          icon("i-bell"),
          h("div", { class: "wait-txt" },
            h("strong", null, silence ? `Brak raportu postępu: ${String(w.what).replace(/^brak raportu /, "")}` : `Sesja czeka na Ciebie${since}: ${w.what}`),
            h("span", null, action),
            h("small", null, `Źródło: ${WAIT_SOURCE[w.source] || w.source}. ${silence ? "Zniknie po następnym raporcie." : "Baner zniknie, gdy sesja ruszy."}`)),
          h("div", { class: "wait-actions" }, link, tools));
      }));
    }

    // ---------------------------------------------------------------- prototyp (przy zakładkach)
    function renderProtoLink(d) {
      const el = ui.protoLink;
      const pr = d.prototype;
      if (!pr || !pr.slug) { el.hidden = true; fill(el); return; }
      const acc = d.spec && d.spec.prototypeAcceptance;
      const accTxt = acc && acc.screens ? `${acc.accepted}/${acc.screens}` : "";
      const lbl = h("span", { class: "lbl" }, `Prototyp${accTxt ? " · " : ""}`);
      const url = protoUrl(d, pr.slug);
      el.hidden = false;
      fill(el, pr.up
        ? h("a", { class: "proto-link", "aria-label": `Prototyp ${pr.slug}${accTxt ? ", zaakceptowane " + accTxt : ""}`, href: safeUrl(url), target: "_blank", rel: "noopener",
          "data-tip": `Otwórz prototyp ${pr.slug} (port ${pr.port})${acc && acc.screens ? `; zaakceptowane ekrany ${acc.accepted} z ${acc.screens}` : ""}`,
          on: { click: linkClick("prototype", pr.slug, url) } }, icon("i-proto"), lbl, accTxt)
        : h("span", { class: "proto-link is-off", "aria-label": `Prototyp ${pr.slug}: serwer wyłączony`,
          "data-tip": `Serwer prototypów (port ${pr.port}) nie działa — uruchom go, żeby otworzyć ${pr.slug}` },
          icon("i-proto"), lbl, accTxt, h("span", { class: "lbl" }, " (serwer wyłączony)")));
    }

    // ---------------------------------------------------------------- dokumenty
    function renderDocList(d) {
      const groups = d.docs || [];
      ui.docCount.textContent = groups.reduce((n, g) => n + (g.items || []).length, 0) || "";
      st.docButtons = [];
      fill(ui.docList, groups.map((g) => [h("h3", null, g.label), (g.items || []).map((it) => {
        const name = g.key === "run" ? baseName(it.path) : g.key === "task" ? it.title : String(it.title || "").replace(/^(Plan|Spec):\s*/i, "");
        const b = h("button", { class: "doc-item", "data-doc": it.path, "aria-current": String(it.path === st.doc), "data-tip": `${it.path} · zmieniony ${fmtTime(it.mtime)}`,
          on: { click: () => (previewMode() ? loadDoc(it.path) : openDoc(it.path)) } },
          icon("i-doc"), h("span", { class: "nm" }, name), statusChip(it.status));
        st.docButtons.push(b);
        return b;
      })]));
      if (!st.doc && groups[0] && groups[0].items && groups[0].items[0]) st.doc = groups[0].items[0].path;
    }
    // Otwarcie dokumentu: z actions.loadDoc podgląd w zakładce Dokumenty (strona), bez niego actions.openDoc (host, np. edytor).
    function openDoc(path) {
      if (previewMode()) { st.doc = path; setView("docs"); }
      else if (typeof actions.openDoc === "function") actions.openDoc(path);
    }
    async function loadDoc(path, live = false) {
      if (!previewMode()) return;
      const view = ui.docView;
      const scroll = live && deps.scroll ? deps.scroll.save() : 0;
      st.doc = path;
      if (!live) navigate("docs:" + path);
      st.docButtons.forEach((b) => b.setAttribute("aria-current", String(b.getAttribute("data-doc") === path)));
      try {
        const data = await actions.loadDoc(path);
        if (st.destroyed) return;
        if (live && st.docData && st.docData.path === data.path && st.docData.mtime === data.mtime) return;
        st.docData = data;
        const md = h("div", { class: "md" });
        fill(view,
          h("div", { class: "doc-bar" }, icon("i-doc"), mono(data.path), statusChip(data.status), h("span", null, `zmieniony ${fmtTime(data.mtime)}`),
            data.truncated ? h("span", { class: "chip s-warn" }, "obcięty do 2 MB") : null,
            live ? h("span", { class: "chip s-active" }, "odświeżono") : null),
          md);
        // Treść Markdown (HTML z serwera) wstawia host: renderer nie parsuje HTML.
        if (typeof deps.mountHtml === "function") deps.mountHtml(md, data.html);
        if (deps.scroll) { if (live) deps.scroll.restore(scroll); else deps.scroll.reveal(view, st.view === "docs"); }
      } catch (err) {
        if (st.destroyed) return;
        fill(view, h("div", { class: "empty" }, `Nie udało się wczytać dokumentu: ${(err && err.message) || String(err)}. `,
          h("button", { class: "btn", id: "retryDoc", on: { click: () => loadDoc(path) } }, "Spróbuj ponownie")));
      }
    }
    function mdClick(e) {
      const a = e.target && typeof e.target.closest === "function" ? e.target.closest("a.md-doc, a.md-anchor") : null;
      if (!a) return;
      e.preventDefault();
      if (a.classList.contains("md-doc")) { loadDoc(a.getAttribute("data-doc")); return; }
      const id = a.getAttribute("data-anchor");
      const target = Array.prototype.find.call(root.querySelectorAll("[id]"), (x) => x.getAttribute("id") === id);
      if (target) target.scrollIntoView({ behavior: "smooth" });
    }

    // ---------------------------------------------------------------- zakładki, motyw, klawisze
    function setView(view, push = true) {
      st.view = view;
      ["progress", "docs"].forEach((v) => {
        ui["view-" + v].hidden = v !== view;
        ui["tab-" + v].setAttribute("aria-selected", String(v === view));
      });
      if (view === "docs" && st.doc) loadDoc(st.doc);
      if (push) navigate(view === "docs" && st.doc ? "docs:" + st.doc : view);
    }
    function syncTheme() { if (theme && ui.themeBtn) ui.themeBtn.setAttribute("aria-pressed", String(Boolean(theme.isDark()))); }
    function toggleTheme() { if (!theme) return; theme.toggle(); syncTheme(); }
    // Klawisze strony (host podpina do dokumentu): Esc zamyka dymek; P/D zakładki, T motyw, Esc menu planu.
    function handleKey(e) {
      if (e.key === "Escape" && !ui.descTip.hidden) hideDesc();
      const t = e.target;
      if ((t && typeof t.closest === "function" && t.closest("input, textarea, select, [contenteditable]")) || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "p" || e.key === "P") setView("progress");
      else if (e.key === "d" || e.key === "D") setView("docs");
      else if (e.key === "t" || e.key === "T") toggleTheme();
      else if (e.key === "Escape") toggleMenu(false);
    }
    // Zegar (host woła co 1 s): czas aktu III, „x min temu”, próg ciszy.
    function tick() {
      const d = st.data;
      if (!d) return;
      if (ui.elapsed && d.run && d.run.startIso) ui.elapsed.textContent = fmtDur(now() - Date.parse(d.run.startIso));
      if (ui.ago) ui.ago.textContent = fmtAgo(ui.ago.getAttribute("data-iso"));
      const open = (d.waiting || []).filter((w) => !waitDone(d, w)).length;
      if (open !== st.waitOpen) { st.waitOpen = open; render(); return; } // zgoda „zestarzała się” bez nowych danych z serwera
      const quiet = silenceMinutes(d);
      if ((quiet > 0) !== Boolean(st.silenceShown) || (quiet && quiet !== st.silenceMin)) { st.silenceMin = quiet; renderWaiting(d); }
    }
    function setLive(mode) {
      ui.live.setAttribute("class", "live is-" + mode);
      ui.liveText.textContent = liveText[mode] ?? "";
      ui.live.setAttribute("data-tip", liveTip[mode] ?? "");
    }

    syncTheme();

    const board = {
      update(json) {
        st.prev = st.data;
        st.data = json;
        render();
        if (st.view === "docs" && st.doc) loadDoc(st.doc, true);
      },
      setDescriptions(map) { st.desc = map && typeof map === "object" ? map : {}; },
      setNotice(notice) { setNoticeState(notice); render(); },
      setVisible(visible) {
        st.visible = visible !== false;
        if (!st.visible) { cancel(st.noticeTimer); st.noticeTimer = null; }
        else if (st.notice && !st.noticeTimer) setNoticeState(st.notice);
      },
      setLive,
      setView,
      setDoc(path) { st.doc = path || null; },
      tick,
      handleKey,
      closeMenu() { toggleMenu(false); },
      hideTip() { if (!ui.descTip.hidden) hideDesc(); },
      syncTheme,
      state() { return { view: st.view, doc: st.doc, armed: st.armed, approving: st.approving, notice: st.notice }; },
      destroy() {
        st.destroyed = true;
        st.timers.forEach((t) => timers.clear(t));
        st.frames.forEach((f) => cancelRaf(f));
        st.frames.clear();
        st.timers.clear();
        for (const type of Object.keys(rootListeners)) root.removeEventListener(type, rootListeners[type]);
        root.textContent = "";
        root.classList.remove("dashboard-root");
      },
    };
    board.render = board.update; // nazwa ze specu Skippstera (createDashboard(...).render(json))
    return board;
  }

  return { create, createDashboard: create, buildSprite, SPRITE, version: 1 };
});
