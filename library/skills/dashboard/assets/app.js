/* dashboard — adapter strony: transport HTTP (/api/status, /api/doc, /api/approve, /api/descriptions, sesja /_api/v1/*), SSE (/events)
   z fallbackiem na polling co 5 s i watchdogiem, pamięć localStorage, motyw, adres w #hash. Widok rysuje wspólny
   renderer `dashboard-render.js` (window.DashboardRenderer, ten sam w webview Skippstera). Bez zależności.
   Serwer projektu z wieloma zakładkami (worktree): /api/tabs, `?tab=<id>` w adresie i w trasach, pasek zakładek `createTabBar`, stan per zakładka
   (plan, widok) i zakładka „Historia” (`?tab=history`, widok `createArchiveView`: /api/history, /api/plan-status). Serwer z jedną zakładką
   (albo starszy bez /api/tabs) działa jak dotąd: bez paska i bez parametru `tab`. */
(() => {
  "use strict";
  const store = {
    get(k) { try { return localStorage.getItem("dashboard:" + k) ?? localStorage.getItem("status-board:" + k); } catch { return null; } },
    set(k, v) { try { if (v == null) { localStorage.removeItem("dashboard:" + k); localStorage.removeItem("status-board:" + k); } else localStorage.setItem("dashboard:" + k, v); } catch { /* brak pamięci */ } },
  };
  const POLL_MS = 5000, WATCHDOG_MS = 40000;
  // Zgody daje zalogowana sesja originu strony (ciasteczko HttpOnly, token CSRF tylko w pamięci tej karty). Token zgód `#zgoda=` z 1.x już nie istnieje:
  // fragment jest usuwany z paska adresu (strona mówi, dlaczego), a zapamiętane stare tokeny znikają z localStorage przy starcie.
  const TAB_RE = /^(?:main|[0-9a-f]{12})$/;
  const HIST = "history";   // the id of the tab of finished plans in the address (`?tab=history`); it is no worktree, so it never matches TAB_RE
  const ARCHIVE_PAGE = 100, ARCHIVE_POLL_MS = 15000, SESSION_POLL_MS = 30000;
  const OLD_TOKEN_KEY = /^(?:dashboard|status-board):approveToken(?::|$)/;
  const clearAddressBar = () => { try { history.replaceState(null, "", location.pathname + location.search); } catch { location.hash = ""; } };
  function dropOldTokens() {
    try {
      const keys = ["dashboard:approveToken", "status-board:approveToken"];
      for (let i = 0; i < localStorage.length; i += 1) keys.push(localStorage.key(i));
      for (const key of keys) if (OLD_TOKEN_KEY.test(key || "")) localStorage.removeItem(key);
    } catch { /* brak pamięci */ }
  }
  function dropTokenFromHash() {
    if (!(location.hash || "").startsWith("#zgoda=")) return false;
    clearAddressBar();   // the secret of an old address does not stay in the address bar
    return true;
  }
  const readTab = () => {
    const m = /(?:^|[?&])tab=([^&#]*)/.exec(location.search || "");
    if (!m) return null;
    try { const v = decodeURIComponent(m[1]); return TAB_RE.test(v) || v === HIST ? v : null; } catch { return null; }
  };
  const searchWith = (search, id) => "?" + ["tab=" + id].concat(String(search || "").replace(/^\?/, "").split("&").filter((part) => part && !/^tab=/.test(part))).join("&");
  // Zakładki: `tab` to zakładka, którą pokazuje strona (tylko serwer z więcej niż jedną zakładką; wtedy każde żądanie niesie `tab`), `current` to domyślna zakładka
  // serwera (katalog uruchomienia). Bez /api/tabs (starszy serwer) strona jest jednozakładkowa, jak dotąd.
  const firstTab = readTab();
  const st = { plan: store.get("plan"), tab: firstTab === HIST ? null : firstTab, archive: firstTab === HIST, current: null, multi: false, tabs: [], names: {}, hidden: null, showAll: false, gone: {}, light: {}, tabsApi: true, started: false,
    unknownId: null, desc: null, pack: null, pendingView: null, outcome: null, baseTitle: "", barLang: "", beat: 0, data: null,
    hcount: { value: null, at: 0, seq: 0, inflight: false },
    hist: { state: "idle", data: null, error: "", stale: "", limit: ARCHIVE_PAGE, sel: null, preview: null, confirm: null, notice: null, seq: 0, pseq: 0, refreshing: false, inflight: false, again: false, flight: new Set() },
    live: "connecting", es: null, pollTimer: null, lastEvent: 0, fetching: null, again: false, lastFetchOk: 0 };
  const inArchive = () => st.multi && st.archive;                  // the page shows the finished plans of all tabs, not the page of a tab
  const tabId = () => (st.multi ? st.tab : null);                 // the tab sent to the server (none = the default tab, as before)
  const planKey = () => (tabId() ? "plan:" + tabId() : "plan");
  const tabQuery = (first) => (tabId() ? (first ? "?" : "&") + "tab=" + tabId() : "");
  dropOldTokens();
  // The session of the page. `state`: loading (not asked yet), anonymous, user, none (a server without session routes, e.g. the read socket).
  const sess = { state: "loading", user: null, csrf: null, loginAllowed: true, passwordMin: null, passwordSignIn: false, expired: false, legacy: dropTokenFromHash(), seq: 0, sig: "" };
  const rolesOf = (user) => (user && Array.isArray(user.roles) ? user.roles.filter((role) => typeof role === "string") : []);
  const canConsent = () => sess.state === "user" && !sess.user.mustChange && rolesOf(sess.user).includes("approver");

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
  // ---- the session: read from the server, never from the browser's storage
  function sessionModel() {
    if (sess.state === "none") return null;
    const user = sess.user, tab = st.multi ? st.tab : null;
    return { state: sess.state, name: user ? user.name || user.firstName || user.login || "" : "", roles: rolesOf(user), approver: rolesOf(user).includes("approver"),
      mustChange: Boolean(user && user.mustChange), loginAllowed: sess.loginAllowed, passwordMin: sess.passwordMin, passwordSignIn: sess.passwordSignIn, expired: sess.expired, legacy: sess.legacy,
      tab: tab && st.names[tab] ? st.names[tab].name : "" };
  }
  function sessionChanged() {
    const sig = JSON.stringify([sess.state, sess.user && sess.user.login, rolesOf(sess.user), Boolean(sess.user && sess.user.mustChange), sess.loginAllowed, sess.passwordSignIn, sess.expired]);
    if (sig === sess.sig) return false;
    const first = sess.sig === "";
    sess.sig = sig;
    if (board.sessionChanged) board.sessionChanged();
    if (st.multi) renderBar();   // before the list of the tabs came there is no bar to draw
    return !first;
  }
  // `lost` = the server just refused a write for want of a session: the page says that it has expired. A late answer is dropped.
  async function loadSession({ lost = false } = {}) {
    const mine = ++sess.seq;
    try {
      const r = await fetch("/_api/v1/session", { cache: "no-store", credentials: "same-origin" });
      const body = r.ok ? await r.json().catch(() => null) : null;
      if (mine !== sess.seq) return;
      if (r.status === 404 || (r.ok && !(body && typeof body === "object" && "user" in body))) { sess.state = "none"; sess.user = null; sess.csrf = null; }
      else if (!r.ok) return;   // the next poll asks again
      else {
        const user = body.user && typeof body.user === "object" ? body.user : null;
        if (!user && (sess.state === "user" || lost)) sess.expired = true;
        if (user) sess.expired = false;
        sess.state = user ? "user" : "anonymous";
        sess.user = user;
        sess.csrf = user && typeof body.csrf === "string" ? body.csrf : null;
        const server = body.server && typeof body.server === "object" ? body.server : {};
        sess.loginAllowed = server.loginAllowed !== false;
        sess.passwordMin = user && Number.isInteger(server.passwordMin) ? server.passwordMin : null;
        sess.passwordSignIn = Boolean(user && server.passwordSignIn === true);
      }
      if (sessionChanged() && st.started) { refresh(); if (inArchive()) loadArchive({ silent: true }); }   // what the server shows depends on the session
    } catch { /* no connection: the live state says it */ }
  }
  function writeHeaders() {
    const headers = { "Content-Type": "application/json", "X-Status-Board": "1" };
    if (sess.csrf) headers["X-Skillsync-CSRF"] = sess.csrf;
    return headers;
  }
  // What the page does after a refused write: ask the server again for what it knows (the session, the status, the tabs).
  function afterRefusal(code, tab) {
    if (code === "login_required" || code === "session_expired") loadSession({ lost: true });
    if (code === "forbidden_role" || code === "password_change_required" || code === "consent_refused") loadSession();
    if (code === "tabGone" && tab) tabMissing(tab);
    if (code === "delivery_changed" || code === "tabChanged") refresh();
    if (code === "tabChanged") loadTabs();
  }
  async function postConsent(route, payload, word) {
    const tab = tabId();   // the tab of THIS request: the user may switch while it is in flight
    const r = await fetch(route, { method: "POST", cache: "no-store", credentials: "same-origin", headers: writeHeaders(),
      body: JSON.stringify({ ...payload, ...(tab ? { tab } : {}) }) });
    const body = (await r.json().catch(() => ({}))) || {};
    if (!r.ok) afterRefusal(body.code, tab);
    reportOther(tab, word, r.ok, body.error || "HTTP " + r.status);
    return { r, body };
  }
  async function postApprove({ kind, path, sha, word, planState, branch, target, mode }) {
    try {
      const { r, body } = await postConsent("/api/approve", { kind, path, sha, plan: st.plan || null, ...(planState ? { planState } : {}),
        branch: branch ?? null, target: target ?? null, mode: mode ?? null }, word);   // what the page showed: the server refuses another branch, target or mode
      return r.ok ? { ok: true, word: body.word, text: body.next }
        : { ok: false, word, code: body.code, text: body.error || board.t("ui.httpError", { status: r.status }) };
    } catch {
      return { ok: false, word, text: board.t("ui.noConnection") };   // the page text of the current language (the renderer owns the catalog)
    }
  }
  // The consent for a feedback draft (FB3): the same headers and session as postApprove; the answer is `{ok, already, sha12}` or `{ok: false, text}`.
  async function postFeedbackApprove({ draft, sha }) {
    try {
      const { r, body } = await postConsent("/api/feedback-approve", { draft, sha }, "feedback");
      return r.ok ? { ok: true, already: Boolean(body.already), sha12: body.sha12 }
        : { ok: false, code: body.code, text: body.error || board.t("ui.httpError", { status: r.status }) };
    } catch (err) {
      return { ok: false, text: board.t("ui.noConnection") || String((err && err.message) || err) };
    }
  }
  // Signing in happens on the sign-in page of this origin (the only place where the password of an account is typed first) and comes back here.
  function goToLogin() {
    location.assign("/_login?next=" + encodeURIComponent(location.pathname + location.search + location.hash));
  }
  async function postSession(route, payload) {
    const r = await fetch(route, { method: "POST", cache: "no-store", credentials: "same-origin", headers: writeHeaders(), body: JSON.stringify(payload) });
    const body = r.status === 204 ? {} : (await r.json().catch(() => ({}))) || {};
    return { r, code: typeof body.error === "string" ? body.error : typeof body.code === "string" ? body.code : "" };
  }
  async function logout() {
    try {
      const { r } = await postSession("/_api/v1/logout", {});
      if (!r.ok && r.status !== 401) { loadSession(); return { ok: false }; }   // 401: the session had ended already, which is what was asked for
      sess.state = "anonymous"; sess.user = null; sess.csrf = null; sess.expired = false;   // asked for: no word of an expired session
      if (sessionChanged()) { refresh(); if (inArchive()) loadArchive({ silent: true }); }
      loadSession();
      return { ok: true };
    } catch { return { ok: false }; }
  }
  async function changePassword({ current, next }) {
    try {
      const { r, code } = await postSession("/_api/v1/password", { current, new: next });
      if (r.ok) { await loadSession(); return { ok: true }; }   // a new cookie and a new CSRF token
      if (r.status === 401 || code === "invalid_csrf") loadSession({ lost: r.status === 401 });
      return { ok: false, code };
    } catch { return { ok: false }; }
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

  // The only place where HTML reaches the page: the Markdown preview rendered and escaped by the server (/api/doc), for the page of a tab and for the history.
  const markdown = { mountHtml: (el, html) => { el.innerHTML = html; } };

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
      login: goToLogin,
      logout,
      changePassword,
    },
    storage: { get: (k) => store.get(k === "plan" ? planKey() : k), set: (k, v) => store.set(k === "plan" ? planKey() : k, v) },   // the plan is remembered per tab
    session: sessionModel,
    theme,
    now: () => Date.now(),
    prototypeOrigin: (port) => `${location.protocol}//${location.hostname}:${port}`,
    prototypeUrl: (port, slug) => `${location.protocol}//${location.hostname}:${port}/${encodeURIComponent(slug)}/`,
    // Podgląd Markdown: HTML renderuje i escapuje serwer (/api/doc); strona działa pod CSP script-src 'self'.
    mountHtml: markdown.mountHtml,
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
  const histRoot = document.getElementById("whistory");
  const arch = typeof window.DashboardRenderer.createArchiveView === "function" && histRoot && histRoot.ownerDocument
    ? window.DashboardRenderer.createArchiveView(histRoot, { t: (key, params) => board.t(key, params), ...markdown, panelId: "whistory",
      onSelect: (key) => archiveSelect(key), onAction: (key) => archiveAsk(key), onConfirm: () => archiveConfirm(),
      onCancel: () => { if (!st.hist.confirm || !st.hist.confirm.busy) { st.hist.confirm = null; renderArchive(); } },
      onRetryPreview: () => archiveRetryPreview(),
      onMore: () => { st.hist.limit += ARCHIVE_PAGE; renderArchive(); }, onReload: () => loadArchive(), onDismiss: () => { st.hist.notice = null; renderArchive(); }, onLogin: goToLogin })
    : null;
  const bar = typeof window.DashboardRenderer.createTabBar === "function" && barRoot && barRoot.ownerDocument
    ? window.DashboardRenderer.createTabBar(barRoot, { t: (key, params) => board.t(key, params), panelId: "app", panel: appRoot, archivePanelId: "whistory", archivePanel: histRoot,
      onSelect: (id) => (id === HIST ? selectArchive() : selectTab(id)),
      onToggleAll: () => { st.showAll = !st.showAll; renderBar(); }, onDismiss: dismiss })
    : null;
  const openWaiting = (data) => Array.isArray(data && data.waiting) && data.waiting.some((w) => {   // as the page does: an item whose consent is already given is not waiting
    const act = w && w.approve && ((data.approvals && data.approvals.actions) || []).find((x) => x.kind === w.approve);
    return !(act && act.state === "after");
  });
  // The consent that waits in the tab on the page (its worktree and the short sha it will be written for): the bar says the consent is not meant for any other tab.
  function consentScope() {
    const data = st.data;
    if (!st.multi || !st.tab || inArchive() || !openWaiting(data)) return null;
    const ap = data.approvals || {};
    if (ap.enabled === false || ap.here === false || !(canConsent() || sess.state === "none")) return null;   // the sentence belongs to a consent the page can give
    const shas = [];
    for (const w of data.waiting) {   // every consent button that is active now (state `waiting`), with the sha it will be written for
      const act = w && w.approve && (ap.actions || []).find((x) => x.kind === w.approve);
      if (act && act.state === "waiting" && typeof act.sha === "string" && act.sha && !shas.includes(act.sha.slice(0, 8))) shas.push(act.sha.slice(0, 8));
    }
    return shas.length ? { name: st.names[st.tab] ? st.names[st.tab].name : st.tab, sha: shas.join(", ") } : null;
  }
  function syncTitle() {
    const name = inArchive() ? board.t("ui.wt.history") : st.multi && st.tab && st.names[st.tab] ? st.names[st.tab].name : "";
    document.title = name && st.baseTitle ? st.baseTitle + " \u00b7 " + name : st.baseTitle;
  }
  // The counter of the History tab is the number of rows of /api/history (`count`), read in the background (no token, no `tab` in the address) only while the page is
  // visible and at most once per HCOUNT_EVERY ms (after a refresh of the list of tabs or when the page becomes visible); opening the History and a finished change of a
  // plan status read the list at once (loadArchive, which also sets the counter). A late answer is dropped. Until it comes (or when it fails, or the page is hidden)
  // the bar shows the largest hint `tabs[].history` (never the sum: one plan lives in many tabs), and nothing when no tab has the hint.
  const HCOUNT_EVERY = 60000;
  function historyCount() {
    if (st.hcount.value !== null) return st.hcount.value;
    const hints = st.tabs.filter((tab) => Number.isInteger(tab.history)).map((tab) => tab.history);
    return hints.length ? Math.max(...hints) : null;
  }
  async function refreshHistoryCount() {
    const hc = st.hcount, now = Date.now();
    if (!st.multi || hc.inflight || document.visibilityState === "hidden" || now - hc.at < HCOUNT_EVERY) return;
    hc.at = now; hc.inflight = true;
    const mine = ++hc.seq;
    try {
      const r = await fetch("/api/history", { cache: "no-store", priority: "low" });
      const body = r.ok ? await r.json().catch(() => null) : null;
      if (mine !== hc.seq) return;
      if (body && Number.isFinite(body.count) && body.count >= 0) { hc.value = body.count; renderBar(); }
    } catch { /* the hint stays */ } finally { if (mine === hc.seq) hc.inflight = false; }
  }
  function barModel() {
    const tabs = st.tabs.map((tab) => ({ id: tab.id, name: tab.name, sub: tab.branch && tab.branch !== tab.name ? tab.branch : "", active: tab.id === st.tab && !inArchive(),
      waiting: tab.id === st.tab ? openWaiting(st.data) : Boolean(st.light[tab.id]), readOnly: false, gone: false }));
    const ghost = st.tab && st.gone[st.tab] && !tabs.some((tab) => tab.id === st.tab);
    if (ghost) tabs.push({ id: st.tab, name: st.gone[st.tab].name, sub: "", active: !inArchive(), waiting: false, readOnly: true, gone: true });
    const alerts = [];
    if (ghost) alerts.push({ kind: "error", title: ["ui.wt.goneTitle", { name: st.gone[st.tab].name }], body: ["ui.wt.goneBody", {}], dismiss: null });
    if (st.outcome) alerts.push(st.outcome.ok ? { kind: "info", title: ["ui.wt.otherDone", { word: st.outcome.word, name: st.outcome.name }], body: null, dismiss: "outcome" }
      : { kind: "warn", title: ["ui.wt.otherRefused", { word: st.outcome.word, name: st.outcome.name }], body: ["ui.wt.otherReason", { reason: st.outcome.reason }], dismiss: "outcome" });
    const scope = consentScope();
    if (scope) alerts.push({ kind: "info", title: ["ui.wt.consentScope", scope], body: null, dismiss: null });
    if (st.live === "down") alerts.push({ kind: "error", title: ["ui.wt.lostTitle", {}], body: ["ui.wt.lostBody", {}], dismiss: null });
    if (st.unknownId) alerts.push({ kind: "warn", title: ["ui.wt.unknownTab", { id: st.unknownId }], body: null, dismiss: "unknown" });
    if (!store.get("terminalNote")) alerts.push({ kind: "info", title: ["ui.wt.noteTitle", {}], body: ["ui.wt.noteBody", {}], dismiss: "note" });
    return { tabs, archive: { count: historyCount(), active: inArchive() }, hidden: st.hidden, showAll: st.showAll, lang: board.lang(), alerts, note: null };
  }
  function renderBar() {
    if (!bar) return;
    if (!st.multi) { bar.update({ tabs: [] }); } else {
      bar.update(barModel());
      if (st.barLang !== board.lang()) { st.barLang = board.lang(); bar.relabel(); }
    }
    syncTitle();
    syncBarHeight();
    renderArchive();
  }
  // The row of the tabs is sticky: the header of the page sticks below it (`--wt-h` is its height, 0 when the bar is hidden). The height changes with the fonts
  // and the width of the window, so it is read again after a resize of the row itself.
  function syncBarHeight() {
    const root = document.documentElement;
    if (!root.style || typeof root.style.setProperty !== "function") return;
    const top = st.multi && barRoot && !barRoot.hidden ? barRoot.firstElementChild : null;   // the host of the row and the list of hidden worktrees
    const row = top ? top.firstElementChild : null;
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
    if (id === st.tab) { if (st.es) { st.es.close(); st.es = null; } stopPolling(); }   // nothing to follow: no stream and no poll for a tab that is gone
    renderBar();
  }
  function tabMissing(id) { markGone(id); loadTabs({ resume: false }); }   // no restart of the stream from here: the list and the status may disagree for a moment
  // The list of the tabs (`/api/tabs`, no token): the server may gain or lose a tab at any moment. A server without the route (404) or with another shape is a
  // one-tab server for this page; a network error changes nothing (the next poll tries again).
  async function loadTabs({ resume = true } = {}) {
    if (!st.tabsApi) return;
    try {
      const r = await fetch("/api/tabs", { cache: "no-store" });
      if (r.status === 404) { st.tabsApi = false; return; }   // an older server has no such route; any other failure is tried again at the next poll
      if (!r.ok) return;
      const data = await r.json();
      if (!data || !Array.isArray(data.tabs)) { st.tabsApi = false; return; }
      const list = data.tabs.filter((tab) => tab && typeof tab.id === "string" && TAB_RE.test(tab.id) && typeof tab.name === "string")
        .map((tab) => ({ id: tab.id, name: tab.name, branch: typeof tab.branch === "string" ? tab.branch : "", kind: tab.kind,
          history: Number.isInteger(tab.history) && tab.history >= 0 ? tab.history : null }));   // `history`: a hint of the counter (done + archived plans of the tab), not the number of rows of /api/history; none = an older server
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
      if (st.multi && !wasMulti) {   // a second tab appeared: the plan is remembered per tab from now on
        st.plan = store.get("plan:" + st.tab) ?? (st.tab === st.current ? store.get("plan") : null);
        if (st.started) {   // the list came late: what is on the page is the default tab; if the address names another one, start over with its own renderer and view
          if (st.tab !== st.current) { resetBoard(); st.pendingView = store.get("view:" + st.tab) || ""; }
          refresh(); connect();
        }
      }
      if (!st.multi && wasMulti) { st.archive = false; st.plan = store.get("plan"); if (st.started) { resetBoard(); refresh(); connect(); } }
      if (resume && st.started && st.multi && st.tab && !st.es && !st.gone[st.tab] && "EventSource" in window) { refresh(); connect(); }   // the stream was dropped for a tab that was gone and the tab is listed again
      showPanels();
      renderBar();
      refreshHistoryCount();
      if (st.started && st.archive && st.multi && !wasMulti) loadArchive();
    } catch { /* no connection: the page shows it through the live state */ }
  }
  // The page of the tab or the history: one of the two roots is shown (the renderer of the tab stays alive behind the history, so nothing of the tab is lost).
  function showPanels() {
    const archive = inArchive();
    appRoot.hidden = archive;
    if (histRoot) histRoot.hidden = !archive;
  }
  function selectArchive({ push = true } = {}) {
    if (!st.multi || st.archive || !arch) return;
    st.archive = true;
    if (board.closeDialogs) board.closeDialogs();
    if (push) { try { history.pushState(null, "", location.pathname + searchWith(location.search, HIST)); } catch { /* the address stays */ } }
    showPanels();
    renderBar();
    loadArchive();
  }
  function selectTab(id, { push = true } = {}) {
    if (!st.multi || !TAB_RE.test(id)) return;
    if (id === st.tab && st.archive) {   // back from the history to the tab the page was on: its renderer is still there
      st.archive = false;
      const view = store.get("view:" + id) || "";
      if (push) { try { history.pushState(null, "", location.pathname + searchWith(location.search, id) + (view ? "#" + view : "")); } catch { /* the address stays */ } }
      showPanels();
      renderBar();
      return;
    }
    if (id === st.tab) return;
    st.archive = false;
    showPanels();
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

  // ------------------------------------------------------------------ Historia: zakończone plany wszystkich zakładek (GET /api/history, POST /api/plan-status)
  // The list is read by everybody. A status change needs the session with the role `approver` (CSRF in the header of that one request, never in the model of
  // the view) and names the tab that OWNS the file of the row. An answer that arrives after the user left, or after a newer load, is dropped.
  const PLAN_STATES = ["done", "archived", "approved"];
  // code of a refusal -> the key `ui.history.err.<key>` of the catalog
  const PLAN_ERRORS = { login_required: "loginRequired", session_expired: "sessionExpired", forbidden_role: "forbiddenRole", consent_refused: "consentRefused",
    password_change_required: "passwordChange", consent_context: "consentContext", audit_unavailable: "auditUnavailable", approvals_disabled: "approvalsOff",
    approvalsOff: "approvalsOff", badRequest: "badRequest", planGone: "planGone", shaMismatch: "shaMismatch", statusNotAllowed: "statusNotAllowed",
    notAbandoned: "notAbandoned", tabChanged: "tabChanged", locked: "locked", tabGone: "tabGone", auditRefused: "auditRefused", serverError: "serverError" };
  const text = (v, max) => (typeof v === "string" ? v.slice(0, max || 400) : "");
  const rowKey = (item) => item.tab + "|" + item.path;
  function cleanArchive(body) {   // the shape of GET /api/history; a row of another shape is dropped, not drawn
    if (!body || !Array.isArray(body.items) || !Array.isArray(body.tabs)) return null;
    const tabs = body.tabs.filter((tab) => tab && typeof tab.id === "string" && TAB_RE.test(tab.id) && typeof tab.name === "string")
      .map((tab) => ({ id: tab.id, name: text(tab.name, 200), branch: text(tab.branch, 200), main: Boolean(tab.main) })).slice(0, 64);
    const items = body.items.filter((it) => it && typeof it.tab === "string" && TAB_RE.test(it.tab) && typeof it.path === "string" && it.path && PLAN_STATES.includes(it.status) && typeof it.sha === "string")
      .map((it) => ({ tab: it.tab, tabName: text(it.tabName, 200), path: text(it.path, 400), title: text(it.title, 400), status: it.status, sha: text(it.sha, 64),
        copyTabs: Array.isArray(it.copyTabs) ? it.copyTabs.filter((id) => typeof id === "string" && TAB_RE.test(id)).slice(0, 64) : [it.tab],
        abandoned: Boolean(it.abandoned), action: it.action === "archive" || it.action === "unarchive" || it.action === "abandon" ? it.action : null,
        restoreTo: it.restoreTo === "approved" ? "approved" : null, mtime: typeof it.mtime === "string" ? it.mtime : "" }))
      .slice(0, 1000);
    for (const it of items) it.copies = Math.max(1, it.copyTabs.length);
    return { here: Boolean(body.here), truncated: Boolean(body.truncated), count: Number.isFinite(body.count) ? body.count : items.length, tabs, items };
  }
  const archiveItem = (key) => (st.hist.data ? st.hist.data.items.find((it) => rowKey(it) === key) || null : null);
  const archiveTabName = (id, fallback) => (st.names[id] ? st.names[id].name : fallback || id);
  const archiveCanAct = (item) => Boolean(item.action && st.hist.data && st.hist.data.here && !st.gone[item.tab] && canConsent());
  const archiveWhy = () => (sess.state !== "user" ? "login" : sess.user.mustChange ? "password" : canConsent() ? null : "role");
  function archiveModel() {
    const hs = st.hist, data = hs.data;
    const itemModel = (it) => ({ key: rowKey(it), title: it.title, path: it.path, status: it.status, copies: it.copies, copyNames: it.copyTabs.map((id) => archiveTabName(id)),
      ownerName: archiveTabName(it.tab, it.tabName), abandoned: it.abandoned, action: it.action, restoreTo: it.restoreTo, canAct: archiveCanAct(it),
      why: !it.action ? null : !data.here ? "viewer" : archiveWhy(), mtime: it.mtime, selected: hs.sel === rowKey(it) });
    const model = { lang: board.lang(), state: hs.state === "idle" ? "loading" : hs.state, error: hs.error, stale: hs.stale, refreshing: hs.refreshing, groups: [], count: 0, truncated: false, more: 0,
      preview: null, confirm: null, notice: hs.notice };
    if (!data) return model;
    const shown = data.items.slice(0, hs.limit);
    model.groups = data.tabs.map((tab) => ({ id: tab.id, name: archiveTabName(tab.id, tab.name), branch: tab.branch, main: tab.main, items: shown.filter((it) => it.tab === tab.id).map(itemModel) }));
    model.count = data.count; model.truncated = data.truncated; model.more = Math.max(0, data.items.length - shown.length);
    const sel = hs.sel ? archiveItem(hs.sel) : null;
    if (sel && hs.preview && hs.preview.key === hs.sel) {
      const pv = hs.preview;
      model.preview = { state: pv.state, item: itemModel(sel), html: pv.state === "ready" ? pv.html : "", error: pv.error, truncated: pv.truncated };
      if (hs.confirm) model.confirm = { action: hs.confirm.action, restoreTo: sel.restoreTo, title: sel.title || sel.path, tab: archiveTabName(sel.tab, sel.tabName), sha: sel.sha, busy: hs.confirm.busy,
        copyNames: sel.copyTabs.filter((id) => id !== sel.tab).map((id) => archiveTabName(id)) };
    }
    return model;
  }
  function renderArchive() {
    if (arch && inArchive()) arch.update(archiveModel());
  }
  async function loadArchive({ silent = false } = {}) {
    const hs = st.hist;
    if (hs.inflight && silent) { hs.again = true; return; }   // one silent load at a time: a burst of `change` is one more load afterwards
    const mine = ++hs.seq;
    hs.inflight = true;
    hs.refreshing = Boolean(hs.data);
    if (!hs.data) hs.state = "loading";
    renderArchive();
    try {
      const r = await fetch("/api/history", { cache: "no-store" });
      const body = (await r.json().catch(() => ({}))) || {};
      if (mine !== hs.seq) return;   // a newer load took over
      if (!r.ok) throw new Error(text(body && body.error, 300) || "HTTP " + r.status);
      const data = cleanArchive(body);
      if (!data) throw new Error(board.t("ui.httpError", { status: r.status }));
      hs.data = data; hs.state = "ready"; hs.error = ""; hs.stale = ""; st.hcount.value = data.count; st.hcount.at = Date.now();
      reconcileArchive();
    } catch (err) {
      if (mine !== hs.seq) return;
      const message = (err && err.message) || board.t("ui.noConnection");
      if (hs.data) hs.stale = message; else { hs.state = "error"; hs.error = message; }
    } finally {
      if (mine === hs.seq) { hs.refreshing = false; hs.inflight = false; renderBar(); if (hs.again) { hs.again = false; loadArchive({ silent: true }); } }
    }
  }
  // The new list against what the page holds: a row that is gone (or changed under an open question) closes the selection or the question.
  function reconcileArchive() {
    const hs = st.hist, item = hs.sel ? archiveItem(hs.sel) : null;
    if (hs.sel && !item) { hs.sel = null; hs.preview = null; if (hs.confirm && !hs.confirm.busy) { hs.confirm = null; hs.notice = { kind: "warn", title: ["ui.history.err.planGone", {}], body: null }; } return; }
    if (item && hs.confirm && !hs.confirm.busy && (item.sha !== hs.confirm.sha || item.action !== hs.confirm.action)) {
      hs.confirm = null; hs.notice = { kind: "warn", title: ["ui.history.err.shaMismatch", {}], body: null };
    }
    if (item && hs.preview && hs.preview.sha !== item.sha) loadArchivePreview(item);
  }
  const plainTitle = (item) => (item.title || item.path).replace(/`/g, "'");
  function archiveSelect(key) {
    const item = archiveItem(key), hs = st.hist;
    if (!item || (hs.confirm && hs.confirm.busy)) return;   // while a change is in flight the page stays on its row
    if (hs.sel === key && hs.preview && hs.preview.state !== "error") return;   // the same row again retries a preview that failed
    hs.sel = key; hs.confirm = null;
    loadArchivePreview(item);
  }
  async function loadArchivePreview(item) {
    const hs = st.hist, mine = ++hs.pseq, key = rowKey(item);
    hs.preview = { key, sha: item.sha, state: "loading", html: "", error: "", truncated: false };
    renderArchive();
    try {
      const r = await fetch("/api/doc?path=" + encodeURIComponent(item.path) + "&tab=" + item.tab, { cache: "no-store" });   // the root of the OWNER of the row
      const body = (await r.json().catch(() => ({}))) || {};
      if (mine !== hs.pseq) return;   // another plan was picked meanwhile
      if (!r.ok) throw new Error(text(body && body.error, 300) || "HTTP " + r.status);
      hs.preview = { key, sha: item.sha, state: "ready", html: typeof body.html === "string" ? body.html : "", error: "", truncated: Boolean(body.truncated) };
    } catch (err) {
      if (mine !== hs.pseq) return;
      hs.preview = { key, sha: item.sha, state: "error", html: "", error: (err && err.message) || board.t("ui.noConnection"), truncated: false };
    }
    renderArchive();
  }
  function archiveRetryPreview() {
    const hs = st.hist, item = hs.sel ? archiveItem(hs.sel) : null;
    if (item && hs.preview && hs.preview.state === "error") loadArchivePreview(item);
  }
  function archiveAsk(key) {
    const item = archiveItem(key), hs = st.hist;
    if (!item || !archiveCanAct(item) || hs.flight.has(key) || (hs.confirm && hs.confirm.busy)) return;
    hs.notice = null;
    hs.confirm = { key, action: item.action, sha: item.sha, busy: false };
    renderArchive();
  }
  async function archiveConfirm() {
    const hs = st.hist, ask = hs.confirm;
    if (!ask || ask.busy) return;
    const item = archiveItem(ask.key);
    if (!item || item.sha !== ask.sha || item.action !== ask.action || !archiveCanAct(item)) {
      hs.confirm = null;
      const why = archiveWhy();   // the right was lost while the question was open: say so, nothing is sent
      if (item && why) hs.notice = { kind: "error", title: ["ui.history.failedTitle", { title: plainTitle(item) }],
        body: ["ui.history.err." + (why === "role" ? "forbiddenRole" : why === "password" ? "passwordChange" : sess.expired ? "sessionExpired" : "loginRequired"), {}] };
      renderArchive();
      return;
    }
    const owner = item.tab, title = plainTitle(item);
    if (hs.flight.has(ask.key)) return;   // one write per row at a time
    hs.flight.add(ask.key);
    ask.busy = true;
    renderArchive();
    let notice;
    try {
      const r = await fetch("/api/plan-status", { method: "POST", cache: "no-store", credentials: "same-origin", headers: writeHeaders(),
        body: JSON.stringify({ tab: owner, path: item.path, sha: item.sha, action: ask.action }) });
      const body = (await r.json().catch(() => ({}))) || {};
      if (r.ok) {
        const to = PLAN_STATES.includes(body.to) ? body.to : ask.action === "unarchive" ? "done" : "archived";
        notice = body && body.auditFailed
          ? { kind: "warn", title: ["ui.history.auditFailedTitle", { title }], body: ["ui.history.auditFailedBody", { tab: archiveTabName(owner, item.tabName) }] }
          : { kind: "info", title: ["ui.history.doneTitle", { title, to: board.t("ui.history.status." + to) }], body: null };
      } else {
        const code = typeof body.code === "string" && Object.prototype.hasOwnProperty.call(PLAN_ERRORS, body.code) ? body.code : "";
        afterRefusal(code, owner);
        notice = { kind: "error", title: ["ui.history.failedTitle", { title }], bodyText: text(body && body.error, 300),
          body: code ? ["ui.history.err." + PLAN_ERRORS[code], { tab: archiveTabName(owner, item.tabName) }]   // a known code: the text of the catalog, not the server's
            : text(body && body.error, 300) ? null : ["ui.history.err.other", { status: r.status }] };   // an unknown code: the server's text, or the generic line without one
      }
    } catch {
      notice = { kind: "error", title: ["ui.history.networkTitle", { title }], body: ["ui.history.err.network", {}] };   // it is not known whether the change was written
    }
    hs.flight.delete(ask.key);
    if (hs.confirm === ask) hs.confirm = null;
    hs.notice = notice;
    renderBar();
    loadArchive({ silent: true });   // whatever the answer: the list is read again (the owner tab gets the usual `change` of its stream)
    refresh();
  }

  // ------------------------------------------------------------------ dane
  // On a server with several tabs the finished plans (done, archived) are listed in the History tab, not in the plan menu of a tab; the plan the page shows stays.
  function withoutFinishedPlans(data) {
    if (!Array.isArray(data.plans)) return data;
    const shown = data.plan && data.plan.path;
    return { ...data, plans: data.plans.filter((p) => !p || (p.status !== "done" && p.status !== "archived") || p.path === shown) };
  }
  async function refresh() {
    if (st.tab && st.gone[st.tab]) return null;   // the tab is gone: nothing to ask for until the list of tabs has it again
    if (st.fetching) { st.again = true; return st.fetching; }
    st.fetching = (async () => {
      const asked = tabId();
      const q = (st.plan ? "?plan=" + encodeURIComponent(st.plan) : "") + (asked ? (st.plan ? "&" : "?") + "tab=" + asked : "");
      try {
        const r = await fetch("/api/status" + q, { cache: "no-store" });
        if (r.status === 404 && asked) {
          const body = await r.json().catch(() => ({}));
          if (body && body.code === "unknownTab") {   // the worktree is gone (or excluded): the server answered, so the connection is alive
            st.lastFetchOk = Date.now();
            if (st.live === "down") setLive("polling");
            tabMissing(asked); return;
          }
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
        board.update(st.multi ? withoutFinishedPlans(data) : data);
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
    if (st.tab && st.gone[st.tab]) { if (st.es) { st.es.close(); st.es = null; } stopPolling(); return; }   // a tab that is gone has no stream
    if (!("EventSource" in window)) { setLive("polling"); startPolling(); return; }
    if (st.es) st.es.close();
    const es = new EventSource("/events" + tabQuery(true));
    st.es = es;
    const seen = () => { st.lastEvent = Date.now(); };
    es.addEventListener("hello", () => { seen(); setLive("live"); stopPolling(); loadSession(); refresh(); });   // a restarted server has forgotten every session
    es.addEventListener("change", () => { seen(); refresh(); if (inArchive()) loadArchive({ silent: true }); });
    es.addEventListener("ping", seen);
    es.onerror = () => {
      if (st.es !== es) return;   // a stream that was closed on purpose (the tab is gone, or another tab was chosen) says nothing about the connection
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
  // The History tab has no stream of its own: it follows `change` of the tab on the page (above) and this poll, only while it is shown and the page is visible.
  setInterval(() => { if (inArchive() && document.visibilityState !== "hidden") loadArchive({ silent: true }); }, ARCHIVE_POLL_MS);

  // The session ends by itself (idle, maximum) or from another tab: asked now and then, only while the page is visible.
  setInterval(() => { if (document.visibilityState !== "hidden") loadSession(); }, SESSION_POLL_MS);

  // ------------------------------------------------------------------ opisy (dymki), zdarzenia dokumentu, zegar
  fetch("/api/descriptions", { cache: "no-store" }).then((r) => r.json()).then((x) => { st.desc = x; board.setDescriptions(x); }).catch(() => { board.setDescriptions({}); });
  document.addEventListener("keydown", (e) => {
    if (!inArchive()) { board.handleKey(e); return; }
    // The page of the tab is hidden behind the History: its keys (views, Escape) do nothing there; the theme key stays.
    const t = e.target;
    if ((t && typeof t.closest === "function" && t.closest("input, textarea, select, [contenteditable]")) || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "t" || e.key === "T") theme.toggle();
  });
  document.addEventListener("click", () => board.closeMenu());
  document.addEventListener("visibilitychange", () => { board.setVisible(document.visibilityState !== "hidden"); if (document.visibilityState !== "hidden") refreshHistoryCount(); if (document.visibilityState !== "hidden") loadSession(); if (inArchive() && document.visibilityState !== "hidden") loadArchive({ silent: true }); }); // odliczanie toastu stoi w tle
  board.setVisible(document.visibilityState !== "hidden");
  window.addEventListener("scroll", () => board.hideTip(), { passive: true });
  window.addEventListener("resize", syncBarHeight);
  if (darkQuery && darkQuery.addEventListener) darkQuery.addEventListener("change", () => board.syncTheme());
  setInterval(() => board.tick(), 1000);

  // Stary adres z tokenem wklejony do tej samej karty zmienia tylko fragment: bez przeładowania, więc usuwamy go tu.
  window.addEventListener("hashchange", () => { if (dropTokenFromHash()) { sess.legacy = true; if (board.sessionChanged) board.sessionChanged(); } });
  // Back from the sign-in page out of the browser's cache: the session is asked for again.
  window.addEventListener("pageshow", (e) => { if (e && e.persisted) loadSession(); });
  // Back and forward move between the tabs the page has shown (the address `?tab=<id>` is the state).
  window.addEventListener("popstate", () => {
    if (!st.multi) return;
    const id = readTab();
    if (id === HIST) { selectArchive({ push: false }); return; }
    selectTab(id && st.tabs.some((tab) => tab.id === id) ? id : st.current, { push: false });
  });

  // ------------------------------------------------------------------ start
  (async () => {
    // never rejects; a server that does not answer the list in time is a one-tab server for now (the poll corrects it): which tabs the server has decides what every request carries
    loadSession();
    let patience = null;
    await Promise.race([loadTabs(), new Promise((resolve) => { patience = setTimeout(resolve, 3000); })]);
    if (typeof clearTimeout === "function") clearTimeout(patience);
    st.started = true;
    if (inArchive()) { showPanels(); renderBar(); loadArchive(); }
    let hash = decodeHash();
    if (!hash && st.multi && st.tab) hash = store.get("view:" + st.tab) || "";
    applyDoc(hash);
    setLive("connecting");
    // refresh() is null for a tab that is gone: there is no status to apply the view to
    Promise.resolve(refresh()).then(() => applyView(hash, false));
    connect();
  })();
})();
