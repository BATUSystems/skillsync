/* dashboard — adapter strony: transport HTTP (/api/status, /api/doc, /api/approve, /api/descriptions), SSE (/events)
   z fallbackiem na polling co 5 s i watchdogiem, pamięć localStorage, motyw, adres w #hash. Widok rysuje wspólny
   renderer `dashboard-render.js` (window.DashboardRenderer, ten sam w webview Skippstera). Bez zależności.
   Serwer projektu z wieloma zakładkami (worktree): /api/tabs, `?tab=<id>` w adresie i w trasach, pasek zakładek `createTabBar`, stan per zakładka
   (plan, widok, token). Serwer z jedną zakładką (albo starszy bez /api/tabs) działa jak dotąd: bez paska i bez parametru `tab`. */
(() => {
  "use strict";
  const store = {
    get(k) { try { return localStorage.getItem("dashboard:" + k) ?? localStorage.getItem("status-board:" + k); } catch { return null; } },
    set(k, v) { try { if (v == null) { localStorage.removeItem("dashboard:" + k); localStorage.removeItem("status-board:" + k); } else localStorage.setItem("dashboard:" + k, v); } catch { /* brak pamięci */ } },
  };
  const POLL_MS = 5000, WATCHDOG_MS = 40000;
  // Token zgód (approvals.tokenRequired): serwer drukuje adres z fragmentem `#zgoda=<token>` (`skillsync dashboard url` też). Serwer z wieloma zakładkami
  // daje parę `<id>.<token>` (token niesie zakładkę), a człowiek może wkleić kilka: `#zgoda=id1.tok1,id2.tok2`. Fragment nie trafia do serwera ani do
  // proxy; strona zapamiętuje każdy token w localStorage tego originu (parę pod `approveToken:<id>`, goły token pod `approveToken`) i czyści pasek adresu.
  // Bez tokenu zakładki strona jest dla niej tylko do odczytu. Goły token działa tylko na serwerze z jedną zakładką.
  const TOKEN_KEY = "approveToken";
  const TAB_RE = /^(?:main|[0-9a-f]{12})$/;
  const PAIR_RE = /^(main|[0-9a-f]{12})\.[A-Za-z0-9_.-]{16,128}$/;
  const BARE_RE = /^[A-Za-z0-9_.-]{16,128}$/;
  const FRAGMENT_RE = /^#zgoda=([A-Za-z0-9_.,-]{16,2200})$/;
  const clearAddressBar = () => { try { history.replaceState(null, "", location.pathname + location.search); } catch { location.hash = ""; } };
  function takeTokenFromHash() {
    const raw = location.hash || "";
    if (!raw.startsWith("#zgoda=")) return false;
    const found = FRAGMENT_RE.exec(raw);
    const items = found ? found[1].split(",") : [];
    const valid = Boolean(found) && items.length <= 16 && items.every((item) => PAIR_RE.test(item) || BARE_RE.test(item));
    clearAddressBar();   // a rejected token does not stay in the address bar either
    if (!valid) return false;
    for (const item of items) {
      const pair = PAIR_RE.exec(item);
      if (pair) store.set(TOKEN_KEY + ":" + pair[1], item);
      else if (st.multi) st.staleBare = true;   // a bare token does not fit a server with several tabs: not stored, the page says so
      else store.set(TOKEN_KEY, item);
    }
    return true;
  }
  const readTab = () => {
    const m = /(?:^|[?&])tab=([^&#]*)/.exec(location.search || "");
    if (!m) return null;
    try { const v = decodeURIComponent(m[1]); return TAB_RE.test(v) ? v : null; } catch { return null; }
  };
  const searchWith = (search, id) => "?" + ["tab=" + id].concat(String(search || "").replace(/^\?/, "").split("&").filter((part) => part && !/^tab=/.test(part))).join("&");
  // Zakładki: `tab` to zakładka, którą pokazuje strona (tylko serwer z więcej niż jedną zakładką; wtedy każde żądanie niesie `tab`), `current` to domyślna zakładka
  // serwera (katalog uruchomienia). Bez /api/tabs (starszy serwer) strona jest jednozakładkowa, jak dotąd.
  const st = { plan: store.get("plan"), tab: readTab(), current: null, multi: false, tabs: [], names: {}, hidden: null, showAll: false, gone: {}, light: {}, tabsApi: true, started: false,
    unknownId: null, staleBare: false, desc: null, pack: null, pendingView: null, outcome: null, baseTitle: "", barLang: "", beat: 0, data: null,
    live: "connecting", es: null, pollTimer: null, lastEvent: 0, fetching: null, again: false, lastFetchOk: 0 };
  const tabId = () => (st.multi ? st.tab : null);                 // the tab sent to the server (none = the default tab, as before)
  const activeTab = () => (st.multi ? st.tab : st.current);       // the tab whose token and state the page uses
  const planKey = () => (tabId() ? "plan:" + tabId() : "plan");
  const tabQuery = (first) => (tabId() ? (first ? "?" : "&") + "tab=" + tabId() : "");
  function tokenFor(tab) {
    const pair = tab ? store.get(TOKEN_KEY + ":" + tab) : null;
    if (pair) return pair;
    return st.multi ? null : store.get(TOKEN_KEY);   // a bare token does not fit a server with several tabs
  }
  const approveToken = () => tokenFor(activeTab());
  function forgetToken(tab) {   // the server refused the token (of the tab the request was made for): its pair, or the bare token of a one-tab server
    store.set(tab && store.get(TOKEN_KEY + ":" + tab) ? TOKEN_KEY + ":" + tab : TOKEN_KEY, null);
  }
  takeTokenFromHash();

  // ------------------------------------------------------------------ motyw (pierwszy wybór ustawia theme.js w <head>)
  const darkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const isDark = () => (document.documentElement.dataset.theme || (darkQuery && darkQuery.matches ? "dark" : "light")) === "dark";
  const theme = {
    isDark,
    toggle() {
      const next = isDark() ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      store.set("theme", next);
    },
  };

  // ------------------------------------------------------------------ transport
  async function postApprove({ kind, path, sha, word, planState }) {
    const tab = tabId(), pair = activeTab();   // the tab of THIS request: the user may switch while it is in flight
    try {
      const token = approveToken();
      const headers = { "Content-Type": "application/json", "X-Status-Board": "1" };
      if (token) headers["X-Approve-Token"] = token;
      const r = await fetch("/api/approve", { method: "POST", cache: "no-store", headers,
        body: JSON.stringify({ kind, path, sha, plan: st.plan || null, ...(tab ? { tab } : {}), ...(planState ? { planState } : {}) }) });
      const body = await r.json().catch(() => ({}));
      if (r.status === 403 && token && body.code === "approve_token") { forgetToken(pair); refresh(); renderBar(); }  // nieaktualny (restart serwera, druga zakładka)
      if (r.status === 410 && body.code === "tabGone" && tab) tabMissing(tab);   // the worktree is gone: nothing was written
      reportOther(tab, word, r.ok, body.error || "HTTP " + r.status);
      return r.ok ? { ok: true, word: body.word, text: body.next } : { ok: false, word, text: body.error || board.t("ui.httpError", { status: r.status }) };
    } catch {
      return { ok: false, word, text: board.t("ui.noConnection") };   // the page text of the current language (the renderer owns the catalog)
    }
  }
  // The consent for a feedback draft (FB3): the same headers and token rules as postApprove; the answer is `{ok, already, sha12}` or `{ok: false, text}`.
  async function postFeedbackApprove({ draft, sha }) {
    const tab = tabId(), pair = activeTab();
    try {
      const token = approveToken();
      const headers = { "Content-Type": "application/json", "X-Status-Board": "1" };
      if (token) headers["X-Approve-Token"] = token;
      const r = await fetch("/api/feedback-approve", { method: "POST", cache: "no-store", headers, body: JSON.stringify({ draft, sha, ...(tab ? { tab } : {}) }) });
      const body = await r.json().catch(() => ({}));
      if (r.status === 403 && token && body.code === "approve_token") { forgetToken(pair); refresh(); renderBar(); }
      if (r.status === 410 && body.code === "tabGone" && tab) tabMissing(tab);
      reportOther(tab, "feedback", r.ok, body.error || "HTTP " + r.status);
      return r.ok ? { ok: true, already: Boolean(body.already), sha12: body.sha12 } : { ok: false, text: body.error || board.t("ui.httpError", { status: r.status }) };
    } catch (err) {
      return { ok: false, text: board.t("ui.noConnection") || String((err && err.message) || err) };
    }
  }
  async function fetchDoc(path) {
    const asked = tabId();
    const r = await fetch("/api/doc?path=" + encodeURIComponent(path) + tabQuery(false), { cache: "no-store" });
    const data = await r.json();
    if (asked !== tabId()) throw new Error("tab changed");   // the answer of a tab the user has left is never drawn
    if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
    return data;
  }

  async function fetchTests(q) {
    const params = new URLSearchParams({ run: q.run || "", step: q.step || "", attempt: String(q.attempt || 0), group: q.group || "", limit: "500", offset: String(q.offset || 0) });
    const asked = tabId();
    const r = await fetch("/api/tests?" + params.toString() + tabQuery(false), { cache: "no-store" });
    const data = await r.json();
    if (asked !== tabId()) throw new Error("tab changed");
    if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
    return data;
  }

  // <to-top> przycisk „na górę” (170-B): haczyk dla renderera. Metryki okna przez requestAnimationFrame (jeden odczyt na klatkę, nasłuch
  // pasywny), przewijanie płynne, a przy prefers-reduced-motion natychmiastowe.
  function createToTop(win) {
    const metrics = () => ({ y: win.scrollY, viewport: win.innerHeight });
    return {
      subscribe(cb) {
        let frame = 0;
        const run = () => { frame = 0; cb(metrics()); };
        const on = () => { if (!frame) frame = win.requestAnimationFrame(run); };
        win.addEventListener("scroll", on, { passive: true });
        win.addEventListener("resize", on, { passive: true });
        cb(metrics());
        return () => {
          win.removeEventListener("scroll", on); win.removeEventListener("resize", on);
          if (frame) { win.cancelAnimationFrame(frame); frame = 0; }
        };
      },
      go() {
        const calm = typeof win.matchMedia === "function" && win.matchMedia("(prefers-reduced-motion: reduce)").matches;
        win.scrollTo({ top: 0, left: 0, behavior: calm ? "auto" : "smooth" });
      },
    };
  }
  // </to-top>

  // One renderer per tab: switching destroys it and creates a new one, so nothing of the tab that was left (an armed CP/READY/GO, the caches of documents and
  // tests, an open dialog) can reach the next one. The page keeps what is not a state of a tab (descriptions, theme, live state, visibility).
  let board = makeBoard();
  function makeBoard() {
    return window.DashboardRenderer.create(document.getElementById("app"), {
    ...(st.pack ? { initial: st.pack } : {}),   // the language of the page, until the status of this tab says it
    actions: {
      approve: postApprove,
      feedbackApprove: postFeedbackApprove,
      loadDoc: fetchDoc,
      loadTests: fetchTests,
      refresh: () => refresh(),
      selectPlan: (path) => { st.plan = path || null; refresh(); },
    },
    storage: { get: (k) => store.get(k === "plan" ? planKey() : k), set: (k, v) => store.set(k === "plan" ? planKey() : k, v) },   // the plan is remembered per tab
    hasApproveToken: () => Boolean(approveToken()),
    theme,
    now: () => Date.now(),
    prototypeOrigin: (port) => `${location.protocol}//${location.hostname}:${port}`,
    prototypeUrl: (port, slug) => `${location.protocol}//${location.hostname}:${port}/${encodeURIComponent(slug)}/`,
    // Podgląd Markdown: HTML renderuje i escapuje serwer (/api/doc); strona działa pod CSP script-src 'self'.
    mountHtml: (el, html) => { el.innerHTML = html; },
    onTitle: (title) => { st.baseTitle = title; syncTitle(); },
    onNavigate: (hash) => { location.hash = hash; if (st.multi && st.tab) store.set("view:" + st.tab, hash); },   // the view is remembered per tab
    scroll: {
      save: () => window.scrollY,
      restore: (y) => window.scrollTo(0, y),
      reveal: (view, inDocs) => {
        if (matchMedia("(max-width: 1100px)").matches && inDocs) view.scrollIntoView({ block: "start" }); // lista jest nad podglądem
        else window.scrollTo(0, 0);
      },
      toTop: createToTop(window),
    },
    });
  }

  // ------------------------------------------------------------------ zakładki projektu (pasek, stan per zakładka)
  const barRoot = document.getElementById("wtabs");
  const appRoot = document.getElementById("app");
  const bar = typeof window.DashboardRenderer.createTabBar === "function" && barRoot && barRoot.ownerDocument
    ? window.DashboardRenderer.createTabBar(barRoot, { t: (key, params) => board.t(key, params), panelId: "app", panel: appRoot, onSelect: (id) => selectTab(id),
      onToggleAll: () => { st.showAll = !st.showAll; renderBar(); }, onDismiss: dismiss })
    : null;
  const openWaiting = (data) => Array.isArray(data && data.waiting) && data.waiting.some((w) => {   // as the page does: an item whose consent is already given is not waiting
    const act = w && w.approve && ((data.approvals && data.approvals.actions) || []).find((x) => x.kind === w.approve);
    return !(act && act.state === "after");
  });
  function syncTitle() {
    const name = st.multi && st.tab && st.names[st.tab] ? st.names[st.tab].name : "";
    document.title = name && st.baseTitle ? st.baseTitle + " \u00b7 " + name : st.baseTitle;
  }
  function barModel() {
    const tabs = st.tabs.map((tab) => ({ id: tab.id, name: tab.name, sub: tab.branch && tab.branch !== tab.name ? tab.branch : "", active: tab.id === st.tab,
      waiting: tab.id === st.tab ? openWaiting(st.data) : Boolean(st.light[tab.id]), readOnly: !tokenFor(tab.id), gone: false }));
    const ghost = st.tab && st.gone[st.tab] && !tabs.some((tab) => tab.id === st.tab);
    if (ghost) tabs.push({ id: st.tab, name: st.gone[st.tab].name, sub: "", active: true, waiting: false, readOnly: true, gone: true });
    const alerts = [];
    if (ghost) alerts.push({ kind: "error", title: ["ui.wt.goneTitle", { name: st.gone[st.tab].name }], body: ["ui.wt.goneBody", {}], dismiss: null });
    if (st.outcome) alerts.push(st.outcome.ok ? { kind: "info", title: ["ui.wt.otherDone", { word: st.outcome.word, name: st.outcome.name }], body: null, dismiss: "outcome" }
      : { kind: "warn", title: ["ui.wt.otherRefused", { word: st.outcome.word, name: st.outcome.name }], body: ["ui.wt.otherReason", { reason: st.outcome.reason }], dismiss: "outcome" });
    if (st.live === "down") alerts.push({ kind: "error", title: ["ui.wt.lostTitle", {}], body: ["ui.wt.lostBody", {}], dismiss: null });
    if (st.staleBare && !tokenFor(st.tab)) alerts.push({ kind: "warn", title: ["ui.wt.staleTitle", {}], body: ["ui.wt.staleBody", {}], dismiss: null });
    if (st.unknownId) alerts.push({ kind: "warn", title: ["ui.wt.unknownTab", { id: st.unknownId }], body: null, dismiss: "unknown" });
    if (!store.get("terminalNote")) alerts.push({ kind: "info", title: ["ui.wt.noteTitle", {}], body: ["ui.wt.noteBody", {}], dismiss: "note" });
    return { tabs, archive: null, hidden: st.hidden, showAll: st.showAll, lang: board.lang(), alerts };
  }
  function renderBar() {
    if (!bar) return;
    if (!st.multi) { bar.update({ tabs: [] }); } else {
      bar.update(barModel());
      if (st.barLang !== board.lang()) { st.barLang = board.lang(); bar.relabel(); }
    }
    syncTitle();
    syncBarHeight();
  }
  // The row of the tabs is sticky: the header of the page sticks below it (`--wt-h` is its height, 0 when the bar is hidden). The height changes with the fonts
  // and the width of the window, so it is read again after a resize of the row itself.
  function syncBarHeight() {
    const root = document.documentElement;
    if (!root.style || typeof root.style.setProperty !== "function") return;
    const row = st.multi && barRoot && !barRoot.hidden ? barRoot.firstElementChild : null;
    root.style.setProperty("--wt-h", (row ? Math.ceil(row.offsetHeight || 0) : 0) + "px");
    if (typeof ResizeObserver === "function") {
      if (!st.ro) st.ro = new ResizeObserver(() => syncBarHeight());
      st.ro.disconnect();
      if (row) st.ro.observe(row);
    }
  }
  if (document.fonts && document.fonts.ready && typeof document.fonts.ready.then === "function") document.fonts.ready.then(() => syncBarHeight());
  // A consent that finished after the user had gone to another tab: the page of that tab is not touched, the bar says how it ended and the flags are fetched again.
  function reportOther(tab, word, ok, reason) {
    if (!tab || tab === tabId()) return;
    st.outcome = { ok, word: String(word || ""), name: st.names[tab] ? st.names[tab].name : tab, reason: String(reason || "").slice(0, 200) };
    refresh();
    renderBar();
  }
  function dismiss(key) {
    if (key === "outcome") st.outcome = null;
    if (key === "note") store.set("terminalNote", "1");
    if (key === "unknown") st.unknownId = null;
    renderBar();
  }
  function markGone(id) {
    if (!id || st.gone[id]) return;
    st.gone[id] = { name: st.names[id] ? st.names[id].name : id };
    renderBar();
  }
  function tabMissing(id) { markGone(id); loadTabs(); }
  // The list of the tabs (`/api/tabs`, no token): the server may gain or lose a tab at any moment. A server without the route (404) or with another shape is a
  // one-tab server for this page; a network error changes nothing (the next poll tries again).
  async function loadTabs() {
    if (!st.tabsApi) return;
    try {
      const r = await fetch("/api/tabs", { cache: "no-store" });
      if (r.status === 404) { st.tabsApi = false; return; }   // an older server has no such route; any other failure is tried again at the next poll
      if (!r.ok) return;
      const data = await r.json();
      if (!data || !Array.isArray(data.tabs)) { st.tabsApi = false; return; }
      const list = data.tabs.filter((tab) => tab && typeof tab.id === "string" && TAB_RE.test(tab.id) && typeof tab.name === "string")
        .map((tab) => ({ id: tab.id, name: tab.name, branch: typeof tab.branch === "string" ? tab.branch : "", kind: tab.kind }));
      st.tabs = list;
      for (const tab of list) { st.names[tab.id] = { name: tab.name }; delete st.gone[tab.id]; }
      st.current = TAB_RE.test(data.current) ? data.current : list.length ? list[0].id : null;
      // worktrees that have no tab (inactive, over the limit): optional, only what the server lists; they have no token until `dashboard serve` runs in them
      st.hidden = Array.isArray(data.hidden) ? data.hidden.filter((item) => item && typeof item.name === "string").map((item) => ({ name: item.name,
        lastActivity: typeof item.lastActivity === "string" ? item.lastActivity : null, reason: typeof item.reason === "string" ? item.reason : "" })).slice(0, 64) : null;
      const wasMulti = st.multi;
      st.multi = list.length > 1;
      if (!st.multi) { st.tab = null; st.gone = {}; }
      else if (!st.tab) st.tab = st.current;
      else if (!list.some((tab) => tab.id === st.tab) && !st.gone[st.tab]) {
        if (!st.names[st.tab]) {   // the address names a tab the server never had: the default tab, and the address says so
          st.unknownId = st.tab; st.tab = st.current;
          try { history.replaceState(null, "", location.pathname + searchWith(location.search, st.tab) + location.hash); } catch { /* the address stays */ }
        } else markGone(st.tab);   // a tab the page knew has gone
      }
      if (st.multi && !wasMulti) {   // a second tab appeared: a bare token no longer fits, the plan is remembered per tab from now on
        if (store.get(TOKEN_KEY)) { store.set(TOKEN_KEY, null); st.staleBare = true; }
        st.plan = store.get("plan:" + st.tab) ?? (st.tab === st.current ? store.get("plan") : null);
        if (st.started) {   // the list came late: what is on the page is the default tab; if the address names another one, start over with its own renderer and view
          if (st.tab !== st.current) { resetBoard(); st.pendingView = store.get("view:" + st.tab) || ""; }
          refresh(); connect();
        }
      }
      if (!st.multi && wasMulti) { st.plan = store.get("plan"); if (st.started) { resetBoard(); refresh(); connect(); } }
      renderBar();
    } catch { /* no connection: the page shows it through the live state */ }
  }
  function selectTab(id, { push = true } = {}) {
    if (!st.multi || !TAB_RE.test(id) || id === st.tab) return;
    delete st.gone[st.tab];
    st.tab = id;
    st.unknownId = null;
    st.plan = store.get("plan:" + id) ?? (id === st.current ? store.get("plan") : null);
    const saved = push ? store.get("view:" + id) || "" : decodeHash();
    st.pendingView = saved;   // applied when the status of THIS tab has been drawn
    if (push) { try { history.pushState(null, "", location.pathname + searchWith(location.search, id) + (saved ? "#" + saved : "")); } catch { /* the address stays */ } }
    resetBoard();
    renderBar();
    connect();
    refresh();
  }
  // A new renderer for the tab: empty until the status of the tab arrives (no button of the tab that was left is clickable meanwhile).
  function resetBoard() {
    board.destroy();
    board = makeBoard();
    board.setDescriptions(st.desc || {});
    board.syncTheme();
    board.setLive(st.live);
    board.setVisible(document.visibilityState !== "hidden");
    st.data = null;   // (st.light stays: it lists every tab, the new renderer needs it for the bar)
  }
  const decodeHash = () => { try { return decodeURIComponent(location.hash.slice(1)); } catch { return ""; } };
  // The view the address (or the remembered view of the tab) names: `docs:<path>`, `tests[:<step>]`, `questions`, else the progress.
  function applyDoc(hash) { if (hash.startsWith("docs:")) board.setDoc(hash.slice(5)); if (hash.startsWith("tests")) board.setTestsStep(hash.startsWith("tests:") ? hash.slice(6) : null); }
  function applyView(hash, reset) {
    if (reset) { board.setDoc(null); board.setTestsStep(null); applyDoc(hash); }
    if (hash.startsWith("docs")) board.setView("docs", false); else if (hash === "questions") board.setView("questions", false);
    else if (hash.startsWith("tests")) board.setView("tests", false); else if (reset) board.setView("progress", false);
  }

  // ------------------------------------------------------------------ dane
  async function refresh() {
    if (st.fetching) { st.again = true; return st.fetching; }
    st.fetching = (async () => {
      const asked = tabId();
      const q = (st.plan ? "?plan=" + encodeURIComponent(st.plan) : "") + (asked ? (st.plan ? "&" : "?") + "tab=" + asked : "");
      try {
        const r = await fetch("/api/status" + q, { cache: "no-store" });
        if (r.status === 404 && asked) {
          const body = await r.json().catch(() => ({}));
          if (body && body.code === "unknownTab") { tabMissing(asked); return; }   // the worktree is gone (or excluded)
        }
        if (!r.ok) throw new Error("HTTP " + r.status);
        const data = await r.json();
        if (asked !== tabId()) { st.again = true; return; }   // the answer of a tab the user has left: fetch the current one
        st.lastFetchOk = Date.now();
        // `lang` of the status (project language; absent in an older server = pl) is the language of the page for assistive technology and hyphenation
        document.documentElement.lang = typeof data.lang === "string" && /^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/.test(data.lang) ? data.lang : "pl";
        if (st.plan && !(data.plans || []).some((p) => p.path === st.plan)) { st.plan = null; store.set(planKey(), null); }
        st.data = data;
        st.light = {};
        if (Array.isArray(data.worktrees)) for (const w of data.worktrees) if (w && typeof w.id === "string") st.light[w.id] = Boolean(w.waiting);   // the light flag of the other tabs
        st.pack = { lang: data.lang, texts: data.texts };
        board.update(data);
        if (st.pendingView !== null) { const view = st.pendingView; st.pendingView = null; applyView(view, true); }   // the remembered view of the tab, after ITS status
        renderBar();
        if (st.live === "down") setLive(st.es && st.es.readyState === 1 ? "live" : "polling");
      } catch (e) {
        if (st.live !== "live") setLive("down");
      } finally {
        st.fetching = null;
        if (st.again) { st.again = false; refresh(); }
      }
    })();
    return st.fetching;
  }

  // ------------------------------------------------------------------ na żywo
  function setLive(mode) {
    st.live = mode;
    board.setLive(mode);
    renderBar();
  }
  function startPolling() {
    if (st.pollTimer) return;
    st.pollTimer = setInterval(refresh, POLL_MS);
  }
  function stopPolling() { clearInterval(st.pollTimer); st.pollTimer = null; }
  function connect() {   // one stream, for the tab on the page
    if (!("EventSource" in window)) { setLive("polling"); startPolling(); return; }
    if (st.es) st.es.close();
    const es = new EventSource("/events" + tabQuery(true));
    st.es = es;
    const seen = () => { st.lastEvent = Date.now(); };
    es.addEventListener("hello", () => { seen(); setLive("live"); stopPolling(); refresh(); });
    es.addEventListener("change", () => { seen(); refresh(); });
    es.addEventListener("ping", seen);
    es.onerror = () => {
      setLive(st.lastFetchOk && Date.now() - st.lastFetchOk < 30000 ? "polling" : "down");
      startPolling();
      if (es.readyState === 2) setTimeout(connect, POLL_MS);
    };
  }
  setInterval(() => { // tunel potrafi „zawisnąć” bez błędu: brak pingów = połącz ponownie
    if (st.es && st.live === "live" && Date.now() - st.lastEvent > WATCHDOG_MS) { setLive("polling"); startPolling(); connect(); }
  }, 5000);
  // The list of the tabs follows the server (a worktree added or removed); the flags of the other tabs come with the status, so it is fetched now and then.
  setInterval(() => { loadTabs(); if (st.multi && st.live === "live" && ++st.beat % 3 === 0) refresh(); }, POLL_MS);

  // ------------------------------------------------------------------ opisy (dymki), zdarzenia dokumentu, zegar
  fetch("/api/descriptions", { cache: "no-store" }).then((r) => r.json()).then((x) => { st.desc = x; board.setDescriptions(x); }).catch(() => { board.setDescriptions({}); });
  document.addEventListener("keydown", (e) => board.handleKey(e));
  document.addEventListener("click", () => board.closeMenu());
  document.addEventListener("visibilitychange", () => board.setVisible(document.visibilityState !== "hidden")); // odliczanie toastu stoi w tle
  board.setVisible(document.visibilityState !== "hidden");
  window.addEventListener("scroll", () => board.hideTip(), { passive: true });
  window.addEventListener("resize", syncBarHeight);
  if (darkQuery && darkQuery.addEventListener) darkQuery.addEventListener("change", () => board.syncTheme());
  setInterval(() => board.tick(), 1000);

  // Nowy adres z tokenem wklejony do tej samej karty (po restarcie serwera) zmienia tylko fragment: bez przeładowania, więc czytamy go tu.
  window.addEventListener("hashchange", () => { if (takeTokenFromHash()) { refresh(); renderBar(); } });
  // Back and forward move between the tabs the page has shown (the address `?tab=<id>` is the state).
  window.addEventListener("popstate", () => { if (st.multi) { const id = readTab(); selectTab(id && st.tabs.some((tab) => tab.id === id) ? id : st.current, { push: false }); } });

  // ------------------------------------------------------------------ start
  (async () => {
    // never rejects; a server that does not answer the list in time is a one-tab server for now (the poll corrects it): which tabs the server has decides what every request carries
    let patience = null;
    await Promise.race([loadTabs(), new Promise((resolve) => { patience = setTimeout(resolve, 3000); })]);
    if (typeof clearTimeout === "function") clearTimeout(patience);
    st.started = true;
    let hash = decodeHash();
    if (!hash && st.multi && st.tab) hash = store.get("view:" + st.tab) || "";
    applyDoc(hash);
    setLive("connecting");
    refresh().then(() => applyView(hash, false));
    connect();
  })();
})();
