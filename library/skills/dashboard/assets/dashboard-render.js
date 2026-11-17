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
  const WAIT_SOURCE = { silence: "czas od ostatniej linii checkpoints.md (próg z serwera)", checkpoint: "znacznik „czeka:” w ostatnim checkpoincie", plan: "status planu", spec: "status speca", prototype: "akceptacja ekranów prototypu", writing: "znacznik zapisu ekranów prototypu (.writing.json)", expert: "otwarte pytania blokujące do eksperta" };
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
  // Strona serwera wymaga tokenu zgód (approvals.tokenRequired), a ta karta go nie ma: przyciski nieaktywne z jednym powodem.
  const TOKEN_TEXT = "Zgody wymagają adresu z tokenem: otwórz adres z konsoli serwera albo z `skillsync dashboard url` (bez niego strona jest tylko do odczytu).";
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
    "i-up": ["0 0 24 24", [["path", { "d": "M12 19V6M5.5 12.5L12 6l6.5 6.5", "fill": "none", "stroke": "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" }]]],
    "i-flag": ["0 0 24 24", [["path", { "d": "M5 21V4M5 4h11l-2 4 2 4H5", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round" }]]],
    "i-sun": ["0 0 24 24", [["circle", { "cx": "12", "cy": "12", "r": "4.2", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8" }], ["path", { "d": "M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round" }]]],
    "i-moon": ["0 0 24 24", [["path", { "d": "M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linejoin": "round" }]]],
    "i-proto": ["0 0 24 24", [["rect", { "x": "3", "y": "4", "width": "18", "height": "14", "rx": "2", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8" }], ["path", { "d": "M3 8h18M8 21h8", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round" }], ["circle", { "cx": "6", "cy": "6", "r": ".9", "fill": "currentColor" }]]],
    "i-bell": ["0 0 24 24", [["path", { "d": "M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linejoin": "round" }], ["path", { "d": "M10 20.5a2.2 2.2 0 0 0 4 0", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round" }]]],
    "i-flask": ["0 0 24 24", [["path", { "d": "M9 3h6M10 3v6l-5.5 9a2 2 0 0 0 1.7 3h11.6a2 2 0 0 0 1.7-3L14 9V3", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round" }], ["path", { "d": "M7.5 14h9", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linecap": "round" }]]],
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
  // Czas trwania z sekund do czytelnego tekstu („1 h 05 min”, „40 min”); `rough`: zaokrąglenie do 5 min od 10 min (przedziały prognozy nie udają dokładności).
  const humanDur = (sec, rough) => {
    let m = Math.round(Number(sec) / 60);
    if (!Number.isFinite(m) || m < 0) return "—";
    if (rough && m >= 10) m = Math.round(m / 5) * 5;
    if (m < 60) return `${m} min`;
    const hh = Math.floor(m / 60), mm = m % 60;
    return mm ? `${hh} h ${String(mm).padStart(2, "0")} min` : `${hh} h`;
  };
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
  // Tekst bez znaczników Markdown w linii (do dymków i atrybutów); bez `html` (brak formatowania) tekst bez zmian.
  const plainInline = (text, html) => {
    const t = String(text ?? "");
    if (typeof html !== "string" || !html) return t;
    return t.replace(/(`+)(.+?)\1/g, "$2").replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/<(https?:\/\/[^>\s]+)>/g, "$1")
      .replace(/\*\*(\S(?:.*?\S)?)\*\*/g, "$1").replace(/__(\S(?:.*?\S)?)__/g, "$1").replace(/~~(\S(?:.*?\S)?)~~/g, "$1");
  };
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
    const { h, desc, st, inl } = this;
    const items = d.substeps || [];
    if (!items.length) return null;
    const time = (s) => (s.seconds != null ? fmtDur(s.seconds * 1000) : s.missingData ? "brak danych" : esc(STEP_LABEL[s.state] || s.state));
    const box = h("details", { class: "substeps", open: Boolean(st.substepsOpen) },
      h("summary", desc("substeps_time"), "Podkroki i czas (" + items.length + ")"),
      h("ul", { class: "sub-list" }, items.map((s) => h("li", { class: s.missingData ? "is-nodata" : "" },
        h("span", { class: "mono q-id" }, esc(s.id)), inl("tt", esc(s.title), s.titleHtml), h("span", { class: "mono sub-t" }, time(s))))));
    box.addEventListener("toggle", () => { st.substepsOpen = box.hasAttribute("open"); });
    return box;
  }

  // Czas pracy i czekania na człowieka (akt III): sumy na fazę i na przebieg, dymek z oknami czekania; nieznane to „brak danych”.
  function timingBlock(d) {
    const { h, desc, st } = this;
    const t = d.timing;
    const list = (v) => (Array.isArray(v) ? v : []);
    if (!t || typeof t !== "object" || !t.available || !(list(t.stages).length || list(t.windows).length)) return null;
    const dur = (s) => (s != null ? fmtDur(s * 1000) : "brak danych");
    const clock = (iso) => (typeof iso === "string" && iso.length >= 16 ? iso.slice(11, 16) : "?");
    const part = (work, wait, more) => "praca " + dur(work) + " · czekanie " + dur(wait) + (more && wait != null ? "+" : "");
    const windowLine = (w) => esc(w.what) + ": " + clock(w.startedAt)
      + (w.unknown ? " (okno nieznane: brak zgody i kolejnego checkpointu)"
        : "–" + clock(w.endedAt) + ", " + dur(w.seconds) + (w.source === "approval" ? ", do zgody" : ", do kolejnego checkpointu"));
    const tip = list(t.windows).length ? list(t.windows).map(windowLine).join("\n") : "Brak okien czekania.";
    const totals = t.totals || {};
    const rows = list(t.stages).map((s) => h("li", { class: s.complete ? "" : "is-nodata" },
      h("span", { class: "mono q-id" }, esc(s.phase)),
      h("span", { class: "tt" }, esc(s.name)),
      h("span", { class: "mono sub-t" }, part(s.workSeconds, s.waitSeconds) + (s.complete ? "" : " (niepełne)"))));
    rows.push(h("li", { class: totals.complete ? "" : "is-nodata" },
      h("span", { class: "mono q-id" }, "Σ"),
      h("span", { class: "tt" }, "Razem" + (totals.unknownWindows ? " (okna nieznane: " + totals.unknownWindows + ")" : "")),
      h("span", { class: "mono sub-t" }, part(totals.workSeconds, totals.waitSeconds, totals.unknownWindows > 0) + (totals.complete ? "" : " (niepełne)"))));
    const box = h("details", { class: "timing-box", open: Boolean(st.timingOpen) },
      h("summary", desc("timing_time", tip), "Czas pracy i czekania"),
      h("ul", { class: "timing-list" }, rows));
    box.addEventListener("toggle", () => { st.timingOpen = box.hasAttribute("open"); });
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
      testsOpen: {}, testsAttempt: {}, testsLoading: {}, testsError: {}, testsLoaded: {}, testsStep: null, silenceShown: false, silenceMin: 0, descFor: null, descPinned: false, docButtons: [], timers: new Set(), frames: new Set(), destroyed: false,
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
    // Kontekst sekcji modułu (readinessTile, substepsBlock, timingBlock); openDoc to deklaracja funkcji niżej (hoisting).
    // Formatowanie w linii (pogrubienie, kursywa, kod, bezpieczny link): serwer dodaje `<pole>Html` obok tekstu (`titleHtml`, `whatHtml`, `textHtml`),
    // gdy tekst ma formatowanie; HTML wstawia wyłącznie host przez `deps.mountHtml`. Bez pola albo bez `mountHtml` zostaje zwykły tekst.
    // Dymki, `title` i `aria-*` zawsze czysty tekst: `plain` zdejmuje znaczniki (tylko gdy serwer oznaczył tekst jako sformatowany).
    const inl = (cls, text, html) => {
      if (typeof html === "string" && html && typeof deps.mountHtml === "function") {
        const node = h("span", { class: cls });
        deps.mountHtml(node, html);
        return node;
      }
      return h("span", { class: cls }, text);
    };
    const kit = { h, icon, desc, openDoc: (path) => openDoc(path), st, inl, plain: plainInline };
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
    ui.waitBanner = h("div", { id: "waitBanner", class: "wait", role: "status", "aria-live": "polite", hidden: true, on: { click: mdClick } });
    ui.docCount = h("span", { class: "count", id: "docCount" });
    ui["tab-progress"] = h("button", { role: "tab", id: "tab-progress", "aria-controls": "view-progress", "aria-selected": "true",
      "data-tip": "Akty, postęp i checkpointy (klawisz P)", on: { click: () => setView("progress") } },
      svg("svg", { class: "ic" }, svg("use", { href: "#i-run" })), "Postęp");
    ui["tab-docs"] = h("button", { role: "tab", id: "tab-docs", "aria-controls": "view-docs", "aria-selected": "false",
      "data-tip": "Spec, plan i pliki przebiegu (klawisz D)", on: { click: () => setView("docs") } },
      svg("svg", { class: "ic" }, svg("use", { href: "#i-doc" })), "Dokumenty ", ui.docCount);
    ui.tCount = h("span", { class: "count", id: "tCount" });
    ui["tab-tests"] = h("button", { role: "tab", id: "tab-tests", "aria-controls": "view-tests", "aria-selected": "false", hidden: true,
      "data-tip": "Wyniki testów kroków planu", on: { click: () => setView("tests") } },
      svg("svg", { class: "ic" }, svg("use", { href: "#i-flask" })), "Testy ", ui.tCount);
    ui.tests = h("div", { class: "tests-view", id: "testsView" });
    ui.qCount = h("span", { class: "count", id: "qCount" });
    ui["tab-questions"] = h("button", { role: "tab", id: "tab-questions", "aria-controls": "view-questions", "aria-selected": "false", hidden: true,
      "data-tip": "Prototypy i pytania do eksperta", on: { click: () => setView("questions") } },
      svg("svg", { class: "ic" }, svg("use", { href: "#i-bell" })), "Pytania ", ui.qCount);
    ui.protoLink = h("span", { id: "protoLink", class: "tab-extra", hidden: true });
    ui.result = h("section", { class: "result-box", id: "resultBox", "aria-labelledby": "resultTitle", hidden: true, on: { click: mdClick } });
    ui.glossary = h("div", { class: "glossary-box", id: "glossaryBox" });
    ui.qProtos = h("div", { class: "q-block", id: "qProtos" });
    ui.qItems = h("div", { class: "q-block", id: "qItems", on: { click: mdClick } });
    ui.acts = h("div", { class: "acts", id: "acts", on: { click: mdClick } },
      h("div", { class: "act skeleton" }), h("div", { class: "act skeleton" }), h("div", { class: "act skeleton wide" }));
    ui.phases = h("div", { class: "phases", id: "phases", on: { click: mdClick } });
    ui.feed = h("ol", { class: "feed", id: "feed" });
    ui.parseWarn = h("div", { class: "parse-warn", id: "parseWarn", hidden: true });
    ui.docList = h("nav", { class: "doc-list", id: "docList", "aria-label": "Dokumenty zadania" });
    ui.docView = h("article", { class: "doc-view", id: "docView", "aria-live": "polite", on: { click: mdClick } },
      h("div", { class: "empty" }, previewMode() ? "Wybierz dokument z listy po lewej. Podgląd odświeża się sam, gdy plik się zmieni." : "Wybierz dokument z listy po lewej."));
    ui["view-progress"] = h("section", { id: "view-progress", role: "tabpanel", "aria-labelledby": "tab-progress" },
      ui.acts, ui.result, ui.glossary,
      h("div", { class: "work" }, ui.phases,
        h("aside", { class: "feed-box", "aria-labelledby": "feedTitle" },
          h("h2", { id: "feedTitle", "data-desc": "checkpoints", tabindex: "0" }, svg("svg", { class: "ic" }, svg("use", { href: "#i-flag" })), "Ostatnie checkpointy"),
          ui.parseWarn, ui.feed)));
    ui["view-tests"] = h("section", { id: "view-tests", role: "tabpanel", "aria-labelledby": "tab-tests", hidden: true }, ui.tests);
    ui["view-docs"] = h("section", { id: "view-docs", role: "tabpanel", "aria-labelledby": "tab-docs", hidden: true },
      h("div", { class: "docs" }, ui.docList, ui.docView));
    ui["view-questions"] = h("section", { id: "view-questions", role: "tabpanel", "aria-labelledby": "tab-questions", hidden: true },
      ui.qProtos, ui.qItems);
    ui.foot = h("footer", { class: "foot", id: "foot" });
    ui.descTip = h("div", { id: "descTip", class: "desc-tip", role: "tooltip", hidden: true });

    // ---------------------------------------------------------------- przycisk „na górę” (widok dokumentu, 170-B)
    // Host podaje `deps.scroll.toTop = {subscribe(cb) → unsubscribe, go()}`; `cb` dostaje {y, viewport, height} przewijanego okna. Bez haczyka
    // (webview Skippstera, starsze adaptery) przycisku nie ma. Renderer liczy próg z histerezą, host przewija i pilnuje ruchu.
    const toTopHook = deps.scroll && deps.scroll.toTop && typeof deps.scroll.toTop.subscribe === "function" && typeof deps.scroll.toTop.go === "function"
      ? deps.scroll.toTop : null;
    const toTop = { shown: false, y: 0, viewport: 0, failed: false, focused: false, unsub: null };
    const TO_TOP_SHOW = 0.75, TO_TOP_HIDE = 0.5;   // ułamki wysokości okna: pokaż powyżej, schowaj poniżej
    ui.toTop = toTopHook ? h("button", { type: "button", id: "toTop", class: "to-top", hidden: true, "aria-label": "Wróć na górę strony", "data-tip": "Na górę",
      on: { click: () => toTopClick(), focus: () => { toTop.focused = true; }, blur: () => { toTop.focused = false; } } }, icon("i-up")) : null;
    if (toTopHook) root.classList.add("has-to-top");   // CSS: zapas pod dokumentem tylko, gdy przycisk jest możliwy
    function toTopSync() {
      if (!ui.toTop) return;
      const eligible = st.view === "docs" && Boolean(st.docData) && !toTop.failed && toTop.viewport > 0;
      let shown = eligible && toTop.shown;
      if (eligible && !shown && toTop.y > toTop.viewport * TO_TOP_SHOW) shown = true;
      else if (eligible && shown && toTop.y < toTop.viewport * TO_TOP_HIDE) shown = false;
      if (!shown && toTop.focused) { toTop.focused = false; focusDocsTab(); }   // ukrywany przycisk z fokusem oddaje go zakładce, fokus nie ginie
      toTop.shown = shown;
      ui.toTop.hidden = !shown;
    }
    function focusDocsTab() { if (typeof ui["tab-docs"].focus === "function") ui["tab-docs"].focus({ preventScroll: true }); }
    function toTopClick() {
      focusDocsTab();   // przycisk chowa się sam, gdy płynne przewijanie zejdzie poniżej progu (metryki z hosta)
      toTopHook.go();
    }
    if (toTopHook) {
      toTop.unsub = toTopHook.subscribe((m) => {
        toTop.y = Math.max(0, Number(m && m.y) || 0); toTop.viewport = Math.max(0, Number(m && m.viewport) || 0);
        toTopSync();
      });
    }

    // Wrapper z container-type: układ zależy od szerokości kolumny hosta (app.css, @container), nie okna. Dymek zostaje poza nim
    // (inline-size containment tworzy blok zawierający dla position: fixed).
    root.appendChild(h("div", { class: "dash-wrap" }, h("header", { class: "top" },
      h("div", { class: "brand" },
        h("div", { class: "logo", "aria-hidden": "true" }, svg("svg", null, svg("use", { href: "#i-run" }))),
        h("div", { class: "brand-text" }, ui.title, ui.meta)),
      h("div", { class: "top-right" }, ui.themeBtn || null, ui.live, h("div", { class: "plan-pick" }, ui.planBtn, ui.planMenu))),
      ui.waitBanner,
      h("nav", { class: "tabs", role: "tablist", "aria-label": "Widok", on: { keydown: tabKey } }, ui["tab-progress"], ui["tab-tests"], ui["tab-docs"], ui["tab-questions"], ui.protoLink),
      h("main", null, ui["view-progress"], ui["view-tests"], ui["view-docs"], ui["view-questions"]),
      ui.foot));
    root.appendChild(ui.descTip);
    if (ui.toTop) root.appendChild(ui.toTop);   // poza .dash-wrap: containment tworzy blok zawierający dla position: fixed

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
      focusin: (e) => {
        const el = descOf(e);
        if (el) showDesc(el);
        // which control of the commits panel holds the focus (render() rebuilds the header and restores it)
        const t = e.target;
        st.focusKey = t && t.id === "commitsBtn" ? "btn" : t && typeof t.getAttribute === "function" && /\bcommits-close\b/.test(t.getAttribute("class") || "") ? "close" : null;
      },
      focusout: (e) => { if (descOf(e) && !st.descPinned) hideDesc(); },
      pointerup: (e) => { // dotyk: stuknięcie otwiera i zamyka
        if (e.pointerType !== "touch") return;
        const el = descOf(e);
        if (el && st.descFor === el && st.descPinned) return hideDesc();
        if (el) { showDesc(el); st.descPinned = true; } else if (st.descPinned) hideDesc();
      },
      click: (e) => {
        toggleMenu(false);
        const inside = e && e.target && typeof e.target.closest === "function" && e.target.closest("#commitsPanel");
        if (st.commitsOpen && !inside) setCommits(false);
      },
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
      const waitsForYou = waiting.filter((w) => w.source !== "writing");   // „agent pisze ekrany” to praca sesji, nie czekanie na człowieka
      const progress = d.progress || {};
      if (typeof deps.onTitle === "function") {
        deps.onTitle((waitsForYou.length ? "⏳ Czeka · " : "") + (plan ? `${progress.percent}% · ` : specAct || runAct ? "Akt I · " : "") + title + " — status");
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
        meta.push(h("span", { class: "m", "data-tip": `Czas od startu przebiegu (RUN= w checkpoints.md, start ${fmtTime(run.startIso)})` }, icon("i-clock"), ui.elapsed));
      }
      ui.ago = h("span", { id: "ago", "data-iso": d.lastUpdate || "" }, fmtAgo(d.lastUpdate));
      meta.push(h("span", { class: "m", "data-tip": "Ostatnia zmiana planu, speca lub checkpointów" }, icon("i-sync"), ui.ago));
      {   // the panel stays open only while there is a list to show (a push that empties it must not leave a flag that reopens it later)
        const dl = d.delivery || {};
        if (!(d.git && d.git.branch && typeof dl.ahead === "number" && dl.ahead > 0 && Array.isArray(dl.commits) && dl.commits.length)) st.commitsOpen = false;
      }
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
          const label = `${ahead} ${plural(ahead, "niewypchnięty", "niewypchnięte", "niewypchniętych")}`;
          const cls = `chip ${calm ? "s-planned" : "s-warn"} ahead-chip`;
          // `delivery.commits` (opcjonalne): plakietka staje się przyciskiem, panel pokazuje listę; tematy tylko jako tekst (gniazdo odczytu bez logowania)
          const list = Array.isArray(dv.commits) ? dv.commits.filter((c) => c && typeof c === "object") : [];
          if (!list.length) st.commitsOpen = false;   // no list: nothing to keep open when it comes back
          if (!list.length) meta.push(h("span", { class: "m" }, h("span", { class: cls, "data-tip": tip }, label)));
          else {
            const open = st.commitsOpen === true;
            const more = typeof dv.commitsMore === "number" && dv.commitsMore > 0 ? Math.floor(dv.commitsMore) : 0;
            const btn = h("button", { type: "button", id: "commitsBtn", class: `${cls} ahead-btn`, "data-tip": tip, "aria-expanded": String(open), "aria-controls": "commitsPanel",
              on: { click: (e) => { e.stopPropagation(); setCommits(!st.commitsOpen); const b = root.querySelector("#commitsBtn"); if (b && typeof b.focus === "function") b.focus({ preventScroll: true }); } } }, label);
            const panel = h("div", { id: "commitsPanel", class: "commits-panel", role: "region", "aria-labelledby": "commitsTitle", hidden: !open },
              h("div", { class: "commits-head" },
                h("strong", { id: "commitsTitle" }, `Niewypchnięte commity (${ahead})`, g.branch ? " · " : "", g.branch ? h("span", { class: "mono" }, g.branch) : null),
                h("button", { type: "button", class: "commits-close", "aria-label": "Zamknij listę commitów", "data-tip": "Zamknij (Esc)", on: { click: (e) => { e.stopPropagation(); setCommits(false); const b = root.querySelector("#commitsBtn"); if (b && typeof b.focus === "function") b.focus(); } } }, "×")),
              h("ul", { class: "commits-list" }, list.map((c) => h("li", null, h("span", { class: "mono commit-sha" }, String(c.sha == null ? "" : c.sha)),
                h("span", { class: "commit-subject" }, String(c.subject == null ? "" : c.subject))))),
              h("p", { class: "commits-more" }, more ? `Pokazano ${list.length} ${plural(list.length, "najnowszy", "najnowsze", "najnowszych")}; starszych: ${more}.` : `Wszystkie: ${list.length}.`));
            meta.push(h("span", { class: "m commits-wrap" }, btn, panel));
          }
        }
      }
      if (plan && plan.pr) {
        meta.push(h("span", { class: "m" }, h("a", { class: "chip s-active git-link", href: safeUrl(plan.pr.url), target: "_blank", rel: "noopener",
          "data-tip": "Pull request z linii PR: planu", on: { click: linkClick("pr", plan.pr.number, plan.pr.url) } }, `PR #${plan.pr.number}`)));
      }
      const focusKept = st.commitsOpen ? st.focusKey : null;
      const listTop = st.commitsOpen && ui.meta.querySelector ? (ui.meta.querySelector("#commitsPanel") || {}).scrollTop : 0;
      fill(ui.meta, meta);
      if (focusKept && ui.meta.querySelector) {   // render() rebuilds the header: keep the keyboard focus (and the scroll position) of the open panel
        const again = ui.meta.querySelector(focusKept === "btn" ? "#commitsBtn" : ".commits-close");
        if (again && typeof again.focus === "function") again.focus({ preventScroll: true });
      }
      if (listTop) { const pnl = ui.meta.querySelector("#commitsPanel"); if (pnl) pnl.scrollTop = listTop; }

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
      renderParseWarn(d);
      renderDocList(d);
      renderResult(d);
      renderGlossary();
      renderQuestionsTab(d);
      renderTestsTab(d);
      const app = d.app || {}, project = d.project || {};
      fill(ui.foot,
        h("span", null, `dashboard ${app.version ?? ""}`),
        h("span", null, "projekt: ", mono(project.path)),
        h("span", null, `${d.approvals && d.approvals.enabled && canApprove(d) && !isViewer(d) ? "odczyt + zgody" : "tylko odczyt"} · odświeżono ${new Date(d.generatedAt).toLocaleTimeString("pl-PL")}`));
    }

    // ---------------------------------------------------------------- akty
    // Kafelek etapu: `state` (done/active/planned) i `since` są opcjonalne; bez `state` stan wynika z `done` (jak dotąd). Etap „Prototyp”
    // (`stage_prototype`) przy aktywnym znaczniku zapisu pokazuje „N z M ekranów” (bez M: „N ekranów”) i szacowany koniec z `eta`.
    function stageTile(d, s, fills) {
      const state = s.state === "done" || s.state === "active" || s.state === "planned" ? s.state : s.done ? "done" : "planned";
      const isDone = state === "done", isActive = state === "active";
      const since = isActive && typeof s.since === "string" && s.since ? s.since : "";
      const wr = s.key === "stage_prototype" && d.prototype && d.prototype.writing && typeof d.prototype.writing === "object" ? d.prototype.writing : null;
      const done = wr && Number.isFinite(wr.screensDone) && wr.screensDone >= 0 ? wr.screensDone : null;
      const planned = wr && Number.isFinite(wr.screensPlanned) && wr.screensPlanned > 0 ? wr.screensPlanned : null;
      let screens = null;
      if (done != null) {
        const eta = etaText(d);
        const label = planned ? `${done} z ${planned} ekranów` : `${done} ${plural(done, "ekran", "ekrany", "ekranów")}`;
        let meter = null;
        if (planned) {
          const pct = Math.max(0, Math.min(100, Math.round((done / planned) * 100)));
          const fillEl = h("span", { class: "fill", "data-w": String(pct) });
          fills.push(fillEl);
          meter = h("span", { class: "bar thin stage-bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(planned), "aria-valuenow": String(Math.min(done, planned)),
            "aria-label": `Ekrany prototypu: ${label}` }, fillEl);
        }
        screens = h("span", { class: "stage-screens", "data-screens": "", title: eta || null }, meter, h("span", { class: "stage-screens-txt" }, label));
      }
      const quiet = isActive ? quietMinutes(d) : 0;
      const tip = isActive ? (quiet ? `brak aktywności od ${quiet} min (ostatni raport), etap oznaczony jako trwający${since ? " od " + since : ""}` : `trwa od ${since || "—"}`) + (s.tip ? " · " + s.tip : "") : s.tip;
      return h("span", { class: "stage" + (isDone ? " is-done" : "") + (isActive ? " is-active" : "") + (quiet ? " is-quiet" : "") + (screens ? " has-screens" : ""), "data-state": s.state ? state : null, ...desc(s.key, tip) },
        isDone ? icon("i-check") : null, isActive ? h("span", { class: "stage-dot", "aria-hidden": "true" }) : null, s.label,
        isActive ? h("span", { class: "sr-only" }, quiet ? ` (brak aktywności od ${quiet} min)` : " (w trakcie" + (since ? ", od " + since : "") + ")") : null, screens);
    }
    // POMIAR i PROGNOZA wyglądają i brzmią inaczej: pomiar („Pomiar:”, ciągła ramka) to czas, który minął, z checkpointów i zgód; prognoza („Prognoza:”,
    // przerywana ramka, kursywa) to ekstrapolacja z tempa przebiegu: zawsze przedział, z podanym powodem, gdy brak liczby.
    const clockOf = (ms) => new Date(ms).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
    function actTimeLine(d, key) {
      const t = d.timing && typeof d.timing === "object" ? d.timing : null;
      const a = t && Array.isArray(t.acts) ? t.acts.find((x) => x && x.key === key) : null;
      if (!a) return null;
      const from = typeof a.startedAt === "string" && a.startedAt.length >= 16 ? a.startedAt.slice(11, 16) : "?";
      let text;
      if (!a.unknown) text = `praca ${humanDur(a.workSeconds)}, czekanie ${humanDur(a.waitSeconds)}`;
      else if (a.reason === "no-approval") text = (d.acts || []).some((x) => x.key === key && x.state === "active") ? `trwa od ${from}` : "czas nieznany: zgody nie ma w pliku zgód";
      else if (a.reason === "open-window") text = "czas nieznany: okno czekania bez końca";
      else text = "czas nieznany: niespójne znaczniki";
      const tip = `Zmierzone z checkpointów i pliku zgód: od ${from}${a.endedAt ? " do " + String(a.endedAt).slice(11, 16) : ""}, minus okna czekania. To pomiar, nie prognoza.`;
      return h("div", { class: "measure", "data-measure": key, ...desc("measure_act", tip) }, h("span", { class: "lbl" }, "Pomiar:"), " " + text);
    }
    function forecastLine(d) {
      const f = d.forecast && typeof d.forecast === "object" ? d.forecast : null;
      if (!f || f.scope !== "plan") return null;
      const tip = "Prognoza, nie pomiar: mediana i kwartyle odstępów między zakończonymi krokami bez czekania × zostało kroków. Nie uwzględnia rozmiaru kroków ani czekania na ludzi; to rząd wielkości, nie obietnica.";
      let text;
      if (typeof f.reason === "string") {
        if (f.reason === "not-enough-data") text = `za mało danych (zakończone kroki z czasem: ${Number.isInteger(f.have) ? f.have : 0}, potrzeba co najmniej ${Number.isInteger(f.need) ? f.need : 4})`;
        else if (f.reason === "no-run-start") text = "brak czasu bezwzględnego w checkpoints.md (RUN=)";
        else return null;
        return h("div", { class: "forecast is-none", "data-forecast": "none", ...desc("forecast", tip) }, h("span", { class: "lbl" }, "Prognoza:"), " " + text);
      }
      const nums = [f.lowSeconds, f.medianSeconds, f.highSeconds];
      if (!nums.every((n) => Number.isFinite(n) && n >= 0) || !Number.isInteger(f.remaining)) return null;
      const range = `${humanDur(f.lowSeconds, true)}–${humanDur(f.highSeconds, true)}`;
      const basis = `zostało ${f.remaining} ${plural(f.remaining, "krok", "kroki", "kroków")}, tempo z ${f.samples} ${plural(f.samples, "odstępu", "odstępów", "odstępów")} między zakończonymi`;
      const t0 = now();
      const end = f.paused ? "wstrzymana: sesja czeka na człowieka, czekanie nie jest liczone" : `koniec ok. ${clockOf(t0 + f.lowSeconds * 1000)}–${clockOf(t0 + f.highSeconds * 1000)}`;
      text = `do końca kroków ok. ${range} pracy (${basis}), bez czekania na zgody; ${end}`;
      return h("div", { class: "forecast" + (f.paused ? " is-paused" : ""), "data-forecast": f.paused ? "paused" : "range", ...desc("forecast", tip) }, h("span", { class: "lbl" }, "Prognoza:"), " " + text);
    }
    function renderActs(d) {
      const acts = d.acts || [];
      const anyActive = acts.some((a) => a.state === "active");
      const fills = [];
      fill(ui.acts, acts.map((a) => {
        const glow = a.state === "active" || (!anyActive && a.key === "real" && a.state === "done");
        const stages = (a.stages || []).map((s) => stageTile(d, s, fills));
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
            bar(p.percent, p.pendingPercent, barCls, "Postęp realizacji", fills, p.activePercent),
            legend(p),
            cur ? h("div", { class: "now", "data-tip": `Teraz wykonywane: ${(p.active || []).join(", ")}, ${cur.index}. pozycja z ${p.total} w planie (${p.activeSource === "checkpoint" ? "z ostatniego checkpointu „w toku”" : "pierwszy niezaznaczony krok"})` },
              h("span", { class: "dot-live" }), h("span", { class: "txt" }, "teraz: ", mono(cur.id), " ", inl("", cur.title ?? "", cur.titleHtml)),
              p.parallel ? h("span", { class: "par" }, `+${p.parallel} równolegle`) : null)
              : h("div", { class: "act-detail" }, a.detail || ""),
            forecastLine(d),
            h("div", { class: "stages" }, stages),
            substepsBlock.call(kit, d),
            timingBlock.call(kit, d),
            h("div", { class: "act-actions" }, approveButton(d, "cp", "act-approve"), approveButton(d, "ready", "act-approve")),
          ];
        } else {
          const docRef = a.key === "spec" ? (d.spec && d.spec.exists ? { path: d.spec.path, label: "Otwórz specyfikację" } : null)
            : a.key === "plan" ? (d.plan ? { path: d.plan.path, label: "Otwórz plan" } : null) : null;
          const docLink = docRef ? h("button", { class: "act-doc", type: "button", "data-open-doc": docRef.path, "data-tip": `${docRef.path} w zakładce Dokumenty`,
            on: { click: () => openDoc(docRef.path) } }, icon("i-doc"), docRef.label) : null;
          const approve = approveButton(d, a.key === "spec" ? "spec" : "plan", "act-approve");
          const extra = a.key === "plan" ? readinessTile.call(kit, d) : null;
          body = [h("div", { class: "act-detail" }, a.detail || " "), actTimeLine(d, a.key), h("div", { class: "stages" }, stages), extra, warn, h("div", { class: "act-actions" }, docLink, approve)];
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
            const tip = `${s.id} ${plainInline(s.title, s.titleHtml)} — ${STEP_LABEL[s.state]}${s.sha ? " · commit " + s.sha : ""}${s.note && !s.sha ? " · " + plainInline(s.note, s.titleHtml) : ""}`;
            const mark = s.state === "done" || s.state === "reported" ? icon("i-check") : null;
            return h("li", { class: `step s-${s.state}${changed ? " flash" : ""}${s.tests ? " has-tests" : ""}`, ...desc(/^zamknięcie/i.test(s.title || "") ? "closure" : "substep", tip) },
              h("span", { class: "st", "aria-label": STEP_LABEL[s.state] ?? "" }, mark), h("span", { class: "sid" }, s.id), inl("tt", s.title, s.titleHtml),
              stepTests(s, fills));
          })));
      }));
      animateBars(fills);
    }

    // ---------------------------------------------------------------- testy (180-A): pasek przy kroku i zakładka „Testy”
    const TEST_STATE = { passed: "zielony", failed: "czerwony", running: "uruchomiony", skipped: "pominięty", waiting: "oczekuje" };
    const TEST_MARK = { passed: "✓", failed: "✕", skipped: "–", running: "", waiting: "" };
    const num = (v) => (Number.isFinite(v) && v > 0 ? Math.floor(v) : 0);
    // Pasek „ile przeszło z ilu” przy kroku planu i przycisk do jego sekcji; kroki bez testów nie mają niczego.
    function stepTests(step, fills) {
      const t = step && step.tests;
      if (!t || typeof t !== "object" || !t.counts) return null;
      const c = t.counts, total = num(c.total) || num(c.passed) + num(c.failed) + num(c.skipped) + num(c.running) + num(c.waiting);
      if (!total) return null;
      const pct = (n) => Math.min(100, Math.round((100 * num(n)) / total));
      const label = `${num(c.passed)} z ${total} ${plural(total, "testu", "testów", "testów")} przeszło${num(c.failed) ? `, ${num(c.failed)} ${plural(num(c.failed), "czerwony", "czerwone", "czerwonych")}` : ""}${num(c.running) ? `, ${num(c.running)} trwa` : ""}`;
      const parts = [["g", c.passed], ["r", c.failed], ["a", c.running]].map(([cls, n]) => {
        const el = h("i", { class: cls, "data-w": String(pct(n)) });
        fills.push(el);
        return el;
      });
      return h("span", { class: "stbar" },
        h("span", { class: "tbar", role: "img", "aria-label": label }, parts),
        h("span", { class: "n" }, `${num(c.passed)}/${total}`),
        h("button", { type: "button", class: "tolink", "data-test-step": step.id, "aria-label": `Pokaż testy kroku ${step.id}`, "data-tip": "Pokaż testy kroku",
          on: { click: (e) => { e.stopPropagation(); showTests(step.id); } } }, svg("svg", { class: "ic" }, svg("use", { href: "#i-flask" }))));
    }
    // Przejście do sekcji kroku: zakładka Testy, sekcja rozwinięta, fokus na jej nagłówku.
    function showTests(stepId) {
      st.testsStep = stepId;
      st.testsOpen[stepId] = true;
      setView("tests");
      render();
      const btn = root.querySelector(`#tsec-btn-${CSS_ID(stepId)}`);
      if (btn && typeof btn.focus === "function") btn.focus({ preventScroll: false });
    }
    const CSS_ID = (v) => String(v == null ? "_none" : v).replace(/[^A-Za-z0-9_-]/g, "_");
    function countChips(c) {
      const out = [];
      if (num(c.failed)) out.push(h("span", { class: "chip s-bad" }, `✕ ${num(c.failed)} ${plural(num(c.failed), "czerwony", "czerwone", "czerwonych")}`));
      if (num(c.passed)) out.push(h("span", { class: "chip s-done" }, `${num(c.passed)} ${plural(num(c.passed), "zielony", "zielone", "zielonych")}`));
      if (num(c.running)) out.push(h("span", { class: "chip s-warn" }, `${num(c.running)} ${plural(num(c.running), "uruchomiony", "uruchomione", "uruchomionych")}`));
      if (num(c.waiting)) out.push(h("span", { class: "chip s-planned" }, `${num(c.waiting)} oczekuje`));
      if (num(c.skipped)) out.push(h("span", { class: "chip s-planned" }, `${num(c.skipped)} ${plural(num(c.skipped), "pominięty", "pominięte", "pominiętych")}`));
      return out;
    }
    function testRow(t) {
      const state = TEST_STATE[t.state] ? t.state : "waiting";
      return h("li", { class: `t is-${state}` },
        h("span", { class: `tst ${state}`, role: "img", "aria-label": TEST_STATE[state] }, TEST_MARK[state]),
        h("span", { class: "nm" }, String(t.name ?? t.id ?? "")),
        h("span", { class: "du" }, Number.isFinite(t.ms) ? `${t.ms} ms` : ""),
        t.msg ? h("pre", { class: "msg" }, String(t.msg)) : null);
    }
    function groupBlock(sec, attempt, group, key) {
      const gk = `${key}|${attempt.n}|${group.name}`;
      const loaded = st.testsLoaded[gk];
      const shown = Array.isArray(loaded) ? loaded : group.tests || [];
      const total = num(group.total);
      const canLoad = deps.actions && typeof deps.actions.loadTests === "function";
      const more = total > shown.length && canLoad && !st.testsLoading[gk];
      return h("div", { class: `tgrp s-${group.state || "passed"}` },
        h("div", { class: "tgh" }, h("span", { class: `tst ${group.state === "failed" ? "failed" : group.state === "running" ? "running" : group.state === "waiting" ? "waiting" : "passed"}`, "aria-hidden": "true" },
          group.state === "failed" ? "✕" : group.state === "passed" ? "✓" : ""), h("span", { class: "gn mono" }, String(group.name)),
          h("span", { class: "gc" }, `${num(group.counts && group.counts.passed)}/${total}`)),
        shown.length ? h("ul", { class: "tlist" }, (Array.isArray(loaded) ? shown : shown.slice(0, 200)).map(testRow)) : null,
        st.testsError[gk] ? h("div", { class: "terr", role: "alert" }, "Nie udało się pobrać listy testów tej grupy.") : null,
        more ? h("button", { type: "button", class: "tmore", on: { click: () => {
          st.testsLoading[gk] = true; st.testsError[gk] = false;
          const have = Array.isArray(st.testsLoaded[gk]) ? st.testsLoaded[gk] : [];
          Promise.resolve(deps.actions.loadTests({ run: st.data && st.data.run && st.data.run.id, step: sec.step && sec.step.id, attempt: attempt.n, group: group.name, offset: have.length }))
            .then((body) => { if (body && Array.isArray(body.tests)) st.testsLoaded[gk] = have.concat(body.tests); else st.testsError[gk] = true; })
            .catch(() => { st.testsError[gk] = true; })
            .finally(() => { st.testsLoading[gk] = false; render(); });
        } } }, Array.isArray(loaded) ? `Pokaż kolejne (${total - shown.length})` : `Pokaż wszystkie (${total})`) : null);
    }
    function testSection(sec, fills) {
      const step = sec.step || null, key = step ? step.id : "_none";
      const attempts = Array.isArray(sec.attempts) ? sec.attempts.filter((a) => a && typeof a === "object") : [];
      if (!attempts.length) return null;
      const idx = Math.min(st.testsAttempt[key] || 0, attempts.length - 1), att = attempts[idx];
      const running = sec.state === "running";
      const open = key in st.testsOpen ? st.testsOpen[key] : running;   // rozwinięta tylko trwająca; zakończone zwinięte (także z porażkami)
      const id = CSS_ID(key), c = att.counts || {};
      const total = num(c.total) || num(c.passed) + num(c.failed) + num(c.skipped) + num(c.running) + num(c.waiting);
      const state = sec.state === "passed" ? "zielony" : sec.state === "failed" ? "czerwony" : sec.state === "running" ? "trwa" : sec.state === "interrupted" ? "przerwany" : "";
      const head = h("button", { type: "button", id: `tsec-btn-${id}`, class: "tsec-head", "aria-expanded": String(open), "aria-controls": open ? `tsec-body-${id}` : null,
        on: { click: () => { st.testsOpen[key] = !open; render(); const b = root.querySelector(`#tsec-btn-${id}`); if (b && typeof b.focus === "function") b.focus({ preventScroll: true }); } } },
        h("span", { class: "chev", "aria-hidden": "true" }, open ? "▾" : "▸"),
        h("span", { class: "sh" }, h("b", null, step ? h("span", { class: "mono" }, step.id) : null, step ? " " : "", step ? (step.title || "") : "Bez kroku"),
          h("span", { class: "sub" }, `bieg ${att.n} z ${attempts.length > 1 ? attempts[0].n : att.n} · ${att.startedAt ? fmtTime(att.startedAt) : "—"}${Number.isFinite(att.durationMs) ? " · " + fmtDur(att.durationMs) : ""}`)),
        h("span", { class: "sum", ...(running ? { "aria-live": "polite", "aria-atomic": "true" } : {}) }, state ? h("span", { class: `chip ${sec.state === "passed" ? "s-done" : sec.state === "failed" ? "s-bad" : sec.state === "running" ? "s-active" : "s-warn"}` }, state) : null, countChips(c)));
      const body = open ? h("div", { class: "tsec-body", id: `tsec-body-${id}` },
        att.command ? h("div", { class: "cmd mono" }, String(att.command)) : null,
        h("div", { class: "tprogress", role: "img", "aria-label": `${num(c.passed)} z ${total || "?"} testów zielonych` }, ["g", "r", "a"].map((cls, i) => {
          const el = h("i", { class: cls, "data-w": String(total ? Math.min(100, Math.round((100 * num([c.passed, c.failed, c.running][i])) / total)) : 0) });
          fills.push(el);
          return el;
        })),
        h("div", { class: "tlevel", "data-tip": "lista testów = nazwy i stany pojedynczych testów (z raportu JUnit/TRX albo ze strumienia); liczniki = same liczby (np. „Executed N of M”); polecenie = tylko stan polecenia, bez wyników testów; „bez stanu uruchomiony” = narzędzie wypisuje test dopiero po jego zakończeniu, więc do tego czasu test oczekuje" },
          `poziom danych: ${att.levels === "tests" ? "lista testów" : att.levels === "counts" ? "liczniki" : "polecenie"}${att.live === "completion" ? " (wyniki po zakończeniu każdego testu, bez stanu „uruchomiony”)" : ""}${att.reason ? " · " + att.reason : ""}${att.truncated ? " · lista obcięta (limit)" : ""}`),
        attempts.length > 1 ? h("div", { class: "tatt", role: "group", "aria-label": "Biegi kroku" }, attempts.map((a, i) => h("button", { type: "button", class: `tab-att${i === idx ? " is-on" : ""}`, "aria-pressed": String(i === idx),
          on: { click: () => { st.testsAttempt[key] = i; render(); } } }, `bieg ${a.n}: ${a.state === "passed" ? "✓" : a.state === "failed" ? "✕" : a.state === "running" ? "…" : "!"} ${num(a.counts && a.counts.failed) ? num(a.counts.failed) + " czerwone" : ""}`.trim()))) : null,
        (att.groups || []).map((g) => groupBlock(sec, att, g, key))) : null;
      return h("section", { class: `tsec s-${sec.state || "passed"}`, id: `tsec-${id}` }, head, body);
    }
    function renderTestsTab(d) {
      const t = d.tests && typeof d.tests === "object" && Array.isArray(d.tests.sections) ? d.tests : null;
      ui["tab-tests"].hidden = !t;
      if (!t) {
        fill(ui.tests); ui.tCount.textContent = "";
        ui["view-tests"].hidden = true;
        if (st.view === "tests") setView("progress", false);
        return;
      }
      const failed = num(t.failedTotal);
      ui.tCount.textContent = failed ? String(failed) : "";
      ui.tCount.setAttribute("class", failed ? "count is-bad" : "count");
      const fills = [];
      fill(ui.tests,
        h("div", { class: "tlegend", role: "list" },
          ["waiting", "running", "passed", "failed"].map((k) => h("span", { role: "listitem" }, h("span", { class: `tst ${k}`, "aria-hidden": "true" }, TEST_MARK[k]), ` ${TEST_STATE[k]}`))),
        t.sections.filter((sec) => sec && typeof sec === "object").map((sec) => testSection(sec, fills)));
      animateBars(fills);
      ui["view-tests"].hidden = st.view !== "tests";
    }

    // Dwa odcienie paska (tylko gdy `activePercent` > 0): ciemniejszy = zweryfikowane, jaśniejszy = zgłoszone i w toku (pending + active).
    const activeOf = (v) => (Number.isFinite(v) && v > 0 ? v : 0);
    function legend(p) {
      const active = activeOf(p.activePercent);
      if (!active) return null;
      return h("div", { class: "bar-legend", "data-bar-legend": "" }, h("span", { class: "sw sw-dark", "aria-hidden": "true" }), `${p.percent}% zweryfikowane, `,
        h("span", { class: "sw sw-light", "aria-hidden": "true" }), `${(p.pendingPercent || 0) + active}% zgłoszone lub w toku`);
    }
    function bar(pct, pending, cls, label, fills, activePct) {
      const active = activeOf(activePct);
      const attrs = { class: `bar ${cls}${active ? " has-active" : ""}`, role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct), "aria-label": String(label ?? "") };
      if (pending) {
        attrs["aria-valuetext"] = `${pct}%, plus ${pending}% do weryfikacji`;
        attrs["data-tip"] = `${pct}% zaznaczone w planie + ${pending}% zrobione wg checkpointu (czeka na weryfikację)`;
      }
      if (active) {
        attrs["aria-valuetext"] = `${pct}% zweryfikowane, ${(pending || 0) + active}% zgłoszone lub w toku`;
        attrs["data-tip"] = `${pct}% zaznaczone w planie; ${(pending || 0) + active}% to kroki zgłoszone w checkpointach (${pending || 0}% zrobione, czeka na weryfikację) lub w toku (${active}%)`;
      }
      const done = h("div", { class: "fill", "data-w": String(pct) });
      fills.push(done);
      const pend = pending ? h("div", { class: "fill-pending", "data-w": String(pending) }) : null;
      if (pend) fills.push(pend);
      const act = active ? h("div", { class: "fill-active", "data-w": String(active) }) : null;
      if (act) fills.push(act);
      return h("div", attrs, done, pend, act);
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
    // Wiersz z formatowaniem w linii (`textHtml` z serwera, tylko gdy tekst ma formatowanie) dostaje je przez `deps.mountHtml`; bez niego dotychczasowe
    // wyróżnienie id kroków i fraz (`decorateCheckpoint`). W wierszu sformatowanym wyróżnienie id i fraz nie działa.
    const feedText = (c) => (typeof c.textHtml === "string" && c.textHtml && typeof deps.mountHtml === "function" ? inl("", c.text, c.textHtml) : h("span", null, decorateCheckpoint(c.text)));
    function renderFeed(d) {
      const items = (d.run && d.run.checkpoints) || [];
      const prevTop = st.prev && st.prev.run && st.prev.run.checkpoints && st.prev.run.checkpoints[0];
      const prevCount = ((st.prev && st.prev.run && st.prev.run.checkpoints) || []).length;
      const fresh = st.prev ? Math.max(0, items.length - prevCount) : 0;
      fill(ui.feed, items.length
        ? items.map((c, i) => h("li", { class: i < fresh || (i === 0 && prevTop && prevTop.text !== c.text) ? "is-new" : "" },
          h("span", { class: "t" }, c.time), feedText(c)))
        : h("li", null, h("span"), h("span", { class: "empty" }, "Brak checkpointów — pojawią się, gdy przebieg zapisze ", mono("checkpoints.md"), ".")));
    }

    // Fragmenty checkpointów pominięte przez parser (run.parseWarnings): jedna linia, szczegóły w dymku (tylko tekst).
    const PARSE_REASON = { "no-step-id": "bez id kroku na początku", "later-id": "id kroku nie na początku elementu", "unbalanced-bracket": "niezamknięty nawias, lista dzielona po każdym „;”" };
    function renderParseWarn(d) {
      const list = d.run && Array.isArray(d.run.parseWarnings) ? d.run.parseWarnings.filter((x) => x && typeof x === "object") : [];
      const el = ui.parseWarn;
      if (!list.length) { el.hidden = true; fill(el); return; }
      const reasons = [...new Set(list.map((x) => PARSE_REASON[x.reason] || String(x.reason ?? "")))].filter(Boolean);
      const lines = list.map((x) => `${x.time ?? ""} ${x.segment ?? ""}: „${x.fragment ?? ""}” (${PARSE_REASON[x.reason] || String(x.reason ?? "")})`.trim());
      el.hidden = false;
      el.setAttribute("data-count", String(list.length));
      const n = list.length;
      fill(el, icon("i-flag"), h("span", { ...desc("parse_warnings", lines.join("\n")) },
        `${n} ${plural(n, "fragment raportu pominięto", "fragmenty raportu pominięto", "fragmentów raportu pominięto")}${reasons.length ? ` (${reasons.join("; ")})` : ""}`));
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
    // Panel niewypchniętych commitów (170-A): stan w `st`, żeby przeżył odświeżenie danych; Esc i klik poza panelem zamykają.
    function setCommits(open) {
      if ((st.commitsOpen === true) === open) return;
      st.commitsOpen = open;
      st.focusKey = open ? "btn" : null;
      if (st.data) render();
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
      // Host bez hasApproveToken (webview Skippstera: zgody idą przez CLI) nigdy nie jest „bez tokenu”.
      const needsToken = !viewer && Boolean(ap.here && ap.tokenRequired && typeof deps.hasApproveToken === "function" && !deps.hasApproveToken());
      if (!canApprove(d) || viewer || needsToken) {
        return h("button", { class: cls, type: "button", disabled: true, "aria-disabled": "true", "data-state": "waiting", "data-gate": kind,
          ...desc(GATE_KEY[kind], viewer ? VIEWER_TEXT : needsToken ? TOKEN_TEXT : `Zgoda tylko z przeglądarki na komputerze z sesją (localhost albo --approve-host); tu wpisz „${a.word}” w sesji`) }, icon("i-check"), a.word);
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
    // M2 (review): a stage that is `active` keeps its state in the data (a dead session leaves `trwa:` behind), but the pulse ends with the
    // run's silence: `silence.since`/`thresholdMin` when the plan runs, else the last report time of the run with the same 35 minutes the server
    // uses for `silence` (SILENCE_MIN). Returns the minutes of silence or 0.
    const QUIET_DEFAULT_MIN = 35;
    function quietMinutes(d) {
      const sil = d && d.silence && d.silence.since ? d.silence : null;
      const since = sil ? sil.since : d && d.run && typeof d.run.lastReportAt === "string" ? d.run.lastReportAt : null;
      const threshold = sil && Number.isFinite(sil.thresholdMin) ? sil.thresholdMin : QUIET_DEFAULT_MIN;
      const t = since ? Date.parse(since) : NaN;
      if (!Number.isFinite(t)) return 0;
      const minutes = Math.floor((now() - t) / 60000);
      return minutes >= threshold ? minutes : 0;
    }
    function silenceMinutes(d) {
      const s = d && d.silence;
      if (!s || !s.since) return 0;
      const minutes = Math.floor((now() - Date.parse(s.since)) / 60000);
      return minutes >= s.thresholdMin ? minutes : 0;
    }
    // Adres strony pytań: `runUrl` (`/_experts/<slug>`, tylko poprawna ścieżka) albo lista `/_experts`; tylko gdy host zna adres serwera prototypów.
    function expertsUrl(d, runUrl) {
      const origin = typeof deps.prototypeOrigin === "function" ? deps.prototypeOrigin(d.prototype && d.prototype.port) : null;
      if (!origin) return null;
      const path = typeof runUrl === "string" && /^\/_experts\/[a-z0-9-]+$/.test(runUrl) ? runUrl : "/_experts";
      return String(origin).replace(/\/+$/, "") + path;
    }
    // Toast „Pytania do eksperta”: stan z plików pytań WSZYSTKICH przebiegów (`d.expertQuestions`), nie z widocznego speca. To stan, nie wynik
    // akcji, więc bez timera i bez „×”: znika dopiero, gdy open = 0. Odnośnik tylko, gdy host zna adres serwera prototypów (`deps.prototypeOrigin`).
    function questionsToast(d) {
      const eq = d.expertQuestions;
      const open = eq && Number.isFinite(eq.open) ? eq.open : 0;
      if (open <= 0) return null;
      const blocking = Number.isFinite(eq.blocking) ? eq.blocking : 0;
      const runs = Array.isArray(eq.runs) ? eq.runs : [];
      const url = safeUrl(expertsUrl(d, runs.length === 1 ? runs[0].url : null));
      const text = `Pytania do eksperta: ${open} otwartych (${blocking} blokujących)`;
      const to = runsAddressees(runs);
      return h("div", { class: "wait-item is-info", "data-questions-toast": "" },
        icon("i-bell"),
        h("div", { class: "wait-txt" }, h("strong", null, text), to ? h("span", { "data-addressees": "" }, to) : null),
        url ? h("a", { class: "wait-go", href: url, target: "_blank", rel: "noopener",
          on: { click: linkClick("experts", runs.length === 1 ? String(runs[0].slug || "") : "", url) } }, "Odpowiedz na pytania") : null);
    }
    // Adresaci: "all" = wszyscy eksperci, lista loginów = „do: a, b”, pusta lista albo brak = nic.
    const loginsOf = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === "string" && x.trim()).map((x) => x.trim()) : []);
    function addresseesText(v) {
      if (v === "all") return "do wszystkich ekspertów";
      const l = loginsOf(v);
      return l.length ? "do: " + l.join(", ") : null;
    }
    function runsAddressees(runs) {
      const vals = runs.map((r) => (r && typeof r === "object" ? r.addressees : null));
      if (vals.includes("all")) return addresseesText("all");
      return addresseesText([...new Set(vals.flatMap(loginsOf))].sort());
    }
    // Toast „N nowych komentarzy po akceptacji”: stan z `prototype.commentsAfterAcceptance` (bez timera i bez „×”), znika przy count 0 albo braku klucza.
    function commentsToast(d) {
      const pr = d.prototype && typeof d.prototype === "object" ? d.prototype : null;
      const c = pr && pr.commentsAfterAcceptance && typeof pr.commentsAfterAcceptance === "object" ? pr.commentsAfterAcceptance : null;
      const n = c && Number.isFinite(c.count) ? c.count : 0;
      if (n <= 0) return null;
      const ids = (Array.isArray(c.screens) ? c.screens : []).map((x) => (x && typeof x.id === "string" ? x.id : "")).filter(Boolean);
      const text = `${n} ${plural(n, "nowy komentarz", "nowe komentarze", "nowych komentarzy")} po akceptacji${ids.length ? ` (${plural(ids.length, "ekran", "ekrany", "ekranów")} ${ids.join(", ")})` : ""}`;
      const slug = typeof pr.slug === "string" && pr.slug ? pr.slug : "";
      const url = slug ? safeUrl(protoUrl(d, slug)) : null;
      return h("div", { class: "wait-item is-info", "data-comments-toast": "" },
        icon("i-bell"),
        h("div", { class: "wait-txt" }, h("strong", null, text)),
        url ? h("a", { class: "wait-go", href: url, target: "_blank", rel: "noopener", on: { click: linkClick("prototype", slug, url) } }, `Otwórz prototyp ${slug}`) : null);
    }
    // Toast „szacowany koniec” (`d.eta` z `eta:` najnowszego checkpointu): stan, nie wynik akcji, więc bez timera i bez „×”. Serwer nie czyta
    // zegara, więc przekroczenie liczy `now` względem `untilIso` (bez `untilIso` tylko godzina). Tylko tekst z `h()`.
    function etaText(d) {
      const e = d && d.eta;
      if (!e || typeof e.operation !== "string" || !e.operation || typeof e.until !== "string") return null;
      const late = e.untilIso ? Math.floor((now() - Date.parse(e.untilIso)) / 60000) : 0;
      const over = late >= 60 ? `${Math.floor(late / 60)} h ${late % 60} min` : `${late} min`;
      return late >= 1 ? `${e.operation}: przekroczono ETA o ${over}` : `${e.operation}, koniec ok. ${e.until}`;
    }
    function etaToast(d) {
      const text = etaText(d);
      st.etaText = text;
      return text ? h("div", { class: "wait-item is-info", "data-eta-toast": "" }, icon("i-clock"), h("div", { class: "wait-txt" }, h("strong", null, text))) : null;
    }
    // „Czeka na: <label> (login)” z waiting[].who; bez who (albo bez label) null = dotychczasowy tekst.
    function whoText(w) {
      const who = w && w.who && typeof w.who === "object" ? w.who : null;
      const label = who && typeof who.label === "string" ? who.label.trim() : "";
      if (!label) return null;
      const login = typeof who.login === "string" ? who.login.trim() : "";
      return `Czeka na: ${label}${login ? ` (${login})` : ""}`;
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
      const enote = etaToast(d);
      const cnote = commentsToast(d);
      if (!items.length && !note && !qnote && !enote && !cnote) { el.hidden = true; fill(el); return; }
      el.hidden = false;
      fill(el, enote, qnote, cnote, note, items.map((w) => {
        let link = null;
        if (w.link && w.link.kind === "doc") {
          link = h("button", { class: "wait-go", type: "button", "data-wait-doc": w.link.path, on: { click: () => openDoc(w.link.path) } }, `Otwórz ${baseName(w.link.path)}`);
        } else if (w.link && w.link.kind === "prototype") {
          const url = protoUrl(d, w.link.slug);
          link = h("a", { class: "wait-go", href: safeUrl(url), target: "_blank", rel: "noopener", on: { click: linkClick("prototype", w.link.slug, url) } }, `Otwórz prototyp ${w.link.slug}`);
        } else if (w.link && w.link.kind === "expert") {
          const url = safeUrl(expertsUrl(d, w.link.url));
          link = url ? h("a", { class: "wait-go", href: url, target: "_blank", rel: "noopener", "data-wait-expert": "",
            on: { click: linkClick("experts", w.link.slug ? String(w.link.slug) : "", url) } }, "Odpowiedz na pytania") : null;
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
            silence ? h("strong", null, `Brak raportu postępu: ${String(w.what).replace(/^brak raportu /, "")}`)
              : h("strong", null, w.source === "writing" ? `Sesja pracuje${since}: ` : `${whoText(w) || "Sesja czeka na Ciebie"}${since}: `, inl("", w.what, w.whatHtml)),
            given ? h("span", null, action) : inl("", action, w.actionHtml),
            h("small", null, `Źródło: ${WAIT_SOURCE[w.source] || w.source}. ${silence ? "Zniknie po następnym raporcie." : w.source === "writing" ? "Zniknie, gdy agent skończy pisać albo znacznik wygaśnie." : "Baner zniknie, gdy sesja ruszy."}`)),
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


    // ---------------------------------------------------------------- Wynik planu i pojęcia (pod aktami)
    const RESULT_VISIBLE_LINES = 12;
    // `d.result` = {text, path[, html, moreHtml, moreLines]} albo null. Serwer (`/api/status`) dodaje `html` (widoczna część) i `moreHtml` (zwinięta reszta)
    // wyrenderowane tym samym bezpiecznym rendererem Markdown co `/api/doc` (HTML wstawia host przez `deps.mountHtml`; renderer nie parsuje HTML).
    // Bez `html` (stary serwer, `dashboard show`, host bez `mountHtml`) zostaje tekst w <pre> (textContent), długi w <details> po pierwszych liniach.
    function renderResult(d) {
      const r = d.result && typeof d.result === "object" && typeof d.result.text === "string" && d.result.text.trim() ? d.result : null;
      const el = ui.result;
      if (!r) { el.hidden = true; fill(el); return; }
      const rich = typeof r.html === "string" && typeof deps.mountHtml === "function";
      let headNode, more = null;
      if (rich) {
        headNode = h("div", { class: "md result-md", "data-result-head": "" });
        deps.mountHtml(headNode, r.html);
        if (typeof r.moreHtml === "string" && r.moreHtml) {
          const n = Number.isInteger(r.moreLines) && r.moreLines > 0 ? r.moreLines : 0;
          const restNode = h("div", { class: "md result-md", "data-result-rest": "" });
          deps.mountHtml(restNode, r.moreHtml);
          more = h("details", { class: "result-more", open: Boolean(st.resultOpen) },
            h("summary", null, n ? `Pokaż resztę (${n} ${plural(n, "linia", "linie", "linii")})` : "Pokaż resztę"), restNode);
        }
      } else {
        const lines = r.text.replace(/\s+$/, "").split(/\r?\n/);
        const head = lines.slice(0, RESULT_VISIBLE_LINES).join("\n"), rest = lines.slice(RESULT_VISIBLE_LINES).join("\n");
        headNode = h("pre", { class: "result-text", "data-result-head": "" }, head);
        if (lines.length > RESULT_VISIBLE_LINES) {
          more = h("details", { class: "result-more", open: Boolean(st.resultOpen) },
            h("summary", null, `Pokaż resztę (${lines.length - RESULT_VISIBLE_LINES} ${plural(lines.length - RESULT_VISIBLE_LINES, "linia", "linie", "linii")})`),
            h("pre", { class: "result-text", "data-result-rest": "" }, rest));
        }
      }
      if (more) more.addEventListener("toggle", () => { st.resultOpen = more.hasAttribute("open"); });
      el.hidden = false;
      fill(el,
        h("h2", { id: "resultTitle", ...desc("legend_result") }, icon("i-flag"), "Wynik"),
        typeof r.path === "string" && r.path ? h("div", { class: "result-path" }, "Plan: ", mono(r.path)) : null,
        headNode, more);
    }
    // Legenda pojęć (osobna od `legend(p)`, legendy paska): zawsze obecna, zwinięta; teksty z descriptions.json, awaryjnie z renderera.
    const GLOSSARY = [
      ["legend_act", "Akt", "Jeden z trzech wielkich kroków przebiegu: spec, plan, realizacja."],
      ["legend_stage", "Etap", "Kafelek w akcie; pulsujący wskaźnik oznacza etap, który trwa teraz."],
      ["legend_phase", "Faza", "Grupa kroków w planie; zrobiona, gdy zrobione są wszystkie jej kroki."],
      ["legend_step", "Krok", "Jedna pozycja listy Progress w planie z identyfikatorem, np. F1.2."],
      ["legend_consent", "Zgoda GO / CP / READY", "Decyzja człowieka: GO plan, GO implement, CP (commit i push), READY (draft PR gotowy do przeglądu)."],
      ["legend_result", "Wynik", "Sekcja „Wynik” planu: co faktycznie zrobiono, z kontrolami i odstępstwami."],
      ["legend_debt", "Dług", "Świadomie odłożona sprawa zapisana w planie albo retrospektywie."],
      ["legend_eta", "ETA", "Szacowany koniec bieżącej operacji podany przez agenta w raporcie postępu."],
      ["legend_verify", "Do weryfikacji", "Kroki zgłoszone jako zrobione, ale jeszcze niezaznaczone w planie; jaśniejszy odcień paska."],
    ];
    function renderGlossary() {
      const map = st.desc || {};
      const box = h("details", { class: "glossary", open: Boolean(st.glossaryOpen) },
        h("summary", null, icon("i-doc"), "Pojęcia"),
        h("dl", { class: "glossary-list" }, GLOSSARY.map(([key, title, text]) => {
          const item = map[key] && typeof map[key] === "object" ? map[key] : null;
          return h("div", { "data-glossary": key },
            h("dt", null, item && item.title ? item.title : title), h("dd", null, item && item.text ? item.text : text));
        })));
      box.addEventListener("toggle", () => { st.glossaryOpen = box.hasAttribute("open"); });
      fill(ui.glossary, box);
    }

    // ---------------------------------------------------------------- zakładka „Pytania” (prototypy i pytania do eksperta)
    const Q_STATE = { open: "otwarte", answered: "odpowiedziane", withdrawn: "wycofane" };
    const Q_TEXT_MAX = 160;
    const nonNeg = (v) => (Number.isFinite(v) && v >= 0 ? v : null);
    function fmtAge(iso) {
      const t = typeof iso === "string" ? Date.parse(iso) : NaN;
      if (!Number.isFinite(t)) return "—";
      const s = Math.max(0, Math.round((now() - t) / 1000));
      if (s < 10) return "przed chwilą";
      if (s < 60) return `${s} s temu`;
      if (s < 3600) return `${Math.floor(s / 60)} min temu`;
      if (s < 86400) return `${Math.floor(s / 3600)} h temu`;
      return `${Math.floor(s / 86400)} d temu`;
    }
    const questionsTabShown = (d) => Array.isArray(d.prototypes) || Boolean(d.expertQuestions && typeof d.expertQuestions === "object" && Array.isArray(d.expertQuestions.items));
    function protoRow(d, p) {
      const slug = typeof p.slug === "string" ? p.slug : "";
      const screens = nonNeg(p.screens), accepted = nonNeg(p.accepted), stale = nonNeg(p.stale);
      const threads = p.threads && typeof p.threads === "object" ? p.threads : {};
      const openThreads = (nonNeg(threads.open) || 0) + (nonNeg(threads.awaiting) || 0);
      const wr = p.writing && typeof p.writing === "object" ? p.writing : null;
      const wDone = wr ? nonNeg(wr.screensDone) : null, wPlan = wr && Number.isFinite(wr.screensPlanned) && wr.screensPlanned > 0 ? wr.screensPlanned : null;
      const caa = p.commentsAfterAcceptance && typeof p.commentsAfterAcceptance === "object" && Number.isFinite(p.commentsAfterAcceptance.count) ? p.commentsAfterAcceptance.count : 0;
      const url = slug ? safeUrl(protoUrl(d, slug)) : null;
      const facts = [
        p.iteration != null && p.iteration !== "" ? h("span", { class: "q-fact" }, `iteracja ${p.iteration}`) : null,
        screens != null ? h("span", { class: "q-fact" }, `${accepted || 0} z ${screens} ${plural(screens, "ekranu", "ekranów", "ekranów")} zaakceptowanych`
          + (stale ? `, nieaktualne ${stale}` : "")) : null,
        wr ? h("span", { class: "chip s-active q-writing" }, "agent pisze…" + (wDone != null ? ` ${wDone}${wPlan ? " z " + wPlan : ""} ${plural(wPlan || wDone, "ekran", "ekrany", "ekranów")}` : "")) : null,
        h("span", { class: "q-fact" }, `${openThreads} ${plural(openThreads, "wątek otwarty", "wątki otwarte", "wątków otwartych")}`),
        caa > 0 ? h("span", { class: "chip s-warn q-after" }, `${caa} ${plural(caa, "komentarz", "komentarze", "komentarzy")} po akceptacji`) : null,
      ];
      return h("li", { class: "q-row q-proto", "data-proto": slug },
        mono(slug || "—"), h("span", { class: "q-facts" }, facts),
        url ? h("a", { class: "q-go", href: url, target: "_blank", rel: "noopener", "aria-label": `Otwórz prototyp ${slug}`, on: { click: linkClick("prototype", slug, url) } }, "Otwórz prototyp") : null);
    }
    function questionRow(d, q) {
      const text = typeof q.text === "string" ? (q.text.length > Q_TEXT_MAX ? q.text.slice(0, Q_TEXT_MAX) + "…" : q.text) : null;
      const textHtml = text != null && typeof q.textHtml === "string" && q.textHtml && typeof deps.mountHtml === "function" ? q.textHtml : null;
      const to = (q.addressees === "all" || (Array.isArray(q.addressees) && !q.addressees.length)) ? "do wszystkich ekspertów" : addresseesText(q.addressees);
      const url = safeUrl(expertsUrl(d, q.url));
      const slug = typeof q.runSlug === "string" ? q.runSlug : "";
      return h("li", { class: `q-row q-item is-${typeof q.state === "string" ? q.state : "open"}`, "data-question": q.id == null ? "" : String(q.id) },
        h("span", { class: "q-head" }, mono(q.id == null ? "—" : String(q.id)), slug ? h("span", { class: "q-run" }, slug) : null,
          q.blocking === true ? h("span", { class: "chip s-warn q-blocking" }, "blokujące") : null,
          h("span", { class: "chip " + (q.state === "open" ? "s-active" : "s-planned") }, Q_STATE[q.state] || String(q.state ?? "—")),
          h("span", { class: "q-age" }, fmtAge(q.at))),
        textHtml ? inl("q-text", text, textHtml) : h("span", { class: "q-text" + (text == null ? " is-missing" : "") }, text == null ? "treść niedostępna" : text),
        to ? h("span", { class: "q-to", "data-addressees": "" }, to) : null,
        url ? h("a", { class: "q-go", href: url, target: "_blank", rel: "noopener", "aria-label": `Otwórz pytania przebiegu ${slug || ""}`.trim(),
          on: { click: linkClick("experts", slug, url) } }, "Otwórz pytania") : null);
    }
    function renderQuestionsTab(d) {
      const shown = questionsTabShown(d);
      ui["tab-questions"].hidden = !shown;
      if (!shown) {
        fill(ui.qProtos); fill(ui.qItems); ui.qCount.textContent = "";
        ui["view-questions"].hidden = true;
        if (st.view === "questions") setView("progress", false);
        return;
      }
      const eq = d.expertQuestions && typeof d.expertQuestions === "object" ? d.expertQuestions : {};
      const protos = (Array.isArray(d.prototypes) ? d.prototypes : []).filter((p) => p && typeof p === "object");
      const items = (Array.isArray(eq.items) ? eq.items : []).filter((q) => q && typeof q === "object").slice(0, 50);
      ui.qCount.textContent = Number.isFinite(eq.open) && eq.open > 0 ? String(eq.open) : "";
      fill(ui.qProtos, h("h2", { class: "q-title" }, "Prototypy"),
        protos.length ? h("ul", { class: "q-list" }, protos.map((p) => protoRow(d, p))) : h("div", { class: "empty" }, "Brak prototypów"));
      fill(ui.qItems, h("h2", { class: "q-title" }, "Pytania do eksperta"),
        items.length ? h("ul", { class: "q-list" }, items.map((q) => questionRow(d, q))) : h("div", { class: "empty" }, "Brak pytań"));
      ui["view-questions"].hidden = st.view !== "questions";
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
        st.docData = data; toTop.failed = false; toTopSync();
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
        toTop.failed = true; toTopSync();
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
      if ((view === "questions" && ui["tab-questions"].hidden) || (view === "tests" && ui["tab-tests"].hidden)) view = st.view = "progress";
      ["progress", "tests", "docs", "questions"].forEach((v) => {
        ui["view-" + v].hidden = v !== view;
        ui["tab-" + v].setAttribute("aria-selected", String(v === view));
      });
      if (view === "docs" && st.doc) loadDoc(st.doc);
      if (push) navigate(view === "docs" && st.doc ? "docs:" + st.doc : view === "tests" && st.testsStep ? "tests:" + st.testsStep : view);
      toTopSync();
    }
    // Strzałki w liście zakładek (WAI-ARIA): lewo/prawo, Home/End przechodzą po widocznych zakładkach i je włączają.
    function tabKey(e) {
      const keys = ["ArrowRight", "ArrowLeft", "Home", "End"];
      if (!keys.includes(e.key)) return;
      const tabs = ["progress", "tests", "docs", "questions"].filter((v) => !ui["tab-" + v].hidden);
      const cur = tabs.indexOf(st.view);
      const next = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1
        : (Math.max(cur, 0) + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      e.preventDefault();
      setView(tabs[next]);
      if (typeof ui["tab-" + tabs[next]].focus === "function") ui["tab-" + tabs[next]].focus();
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
      else if (e.key === "Escape") {
        toggleMenu(false);
        if (st.commitsOpen) {
          setCommits(false);
          const btn = root.querySelector("#commitsBtn");
          if (btn && typeof btn.focus === "function") btn.focus();
        }
      }
    }
    // Zegar (host woła co 1 s): czas aktu III, „x min temu”, próg ciszy.
    function tick() {
      const d = st.data;
      if (!d) return;
      if (ui.elapsed && d.run && d.run.startIso) ui.elapsed.textContent = fmtDur(now() - Date.parse(d.run.startIso));
      if (ui.ago) ui.ago.textContent = fmtAgo(ui.ago.getAttribute("data-iso"));
      const open = (d.waiting || []).filter((w) => !waitDone(d, w)).length;
      if (open !== st.waitOpen) { st.waitOpen = open; render(); return; } // zgoda „zestarzała się” bez nowych danych z serwera
      if (etaText(d) !== (st.etaText ?? null)) { renderWaiting(d); return; }   // ETA zmienia się z zegarem (przekroczenie co minutę)
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
      setDescriptions(map) { st.desc = map && typeof map === "object" ? map : {}; renderGlossary(); },
      setNotice(notice) { setNoticeState(notice); render(); },
      setVisible(visible) {
        st.visible = visible !== false;
        if (!st.visible) { cancel(st.noticeTimer); st.noticeTimer = null; }
        else if (st.notice && !st.noticeTimer) setNoticeState(st.notice);
      },
      setLive,
      setView,
      setDoc(path) { st.doc = path || null; },
      setTestsStep(step) { st.testsStep = step || null; if (step) st.testsOpen[step] = true; },
      tick,
      handleKey,
      closeMenu() { toggleMenu(false); },
      hideTip() { if (!ui.descTip.hidden) hideDesc(); },
      syncTheme,
      state() { return { view: st.view, doc: st.doc, armed: st.armed, approving: st.approving, notice: st.notice }; },
      destroy() {
        st.destroyed = true;
        if (toTop.unsub) toTop.unsub();
        st.timers.forEach((t) => timers.clear(t));
        st.frames.forEach((f) => cancelRaf(f));
        st.frames.clear();
        st.timers.clear();
        for (const type of Object.keys(rootListeners)) root.removeEventListener(type, rootListeners[type]);
        root.textContent = "";
        root.classList.remove("dashboard-root", "has-to-top");
      },
    };
    board.render = board.update; // nazwa ze specu Skippstera (createDashboard(...).render(json))
    return board;
  }

  return { create, createDashboard: create, buildSprite, SPRITE, version: 1 };
});
