/* dashboard — adapter strony: transport HTTP (/api/status, /api/doc, /api/approve, /api/descriptions), SSE (/events)
   z fallbackiem na polling co 5 s i watchdogiem, pamięć localStorage, motyw, adres w #hash. Widok rysuje wspólny
   renderer `dashboard-render.js` (window.DashboardRenderer, ten sam w webview Skippstera). Bez zależności. */
(() => {
  "use strict";
  const store = {
    get(k) { try { return localStorage.getItem("dashboard:" + k) ?? localStorage.getItem("status-board:" + k); } catch { return null; } },
    set(k, v) { try { v == null ? localStorage.removeItem("dashboard:" + k) : localStorage.setItem("dashboard:" + k, v); } catch { /* brak pamięci */ } },
  };
  const POLL_MS = 5000, WATCHDOG_MS = 40000;
  const st = { plan: store.get("plan"), live: "connecting", es: null, pollTimer: null, lastEvent: 0, fetching: null, again: false, lastFetchOk: 0 };

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
  async function postApprove({ kind, path, sha, word }) {
    try {
      const r = await fetch("/api/approve", { method: "POST", cache: "no-store", headers: { "Content-Type": "application/json", "X-Status-Board": "1" },
        body: JSON.stringify({ kind, path, sha, plan: st.plan || null }) });
      const body = await r.json().catch(() => ({}));
      return r.ok ? { ok: true, word: body.word, text: body.next } : { ok: false, word, text: body.error || `Błąd ${r.status}` };
    } catch {
      return { ok: false, word, text: "Brak połączenia z serwerem statusu." };
    }
  }
  async function fetchDoc(path) {
    const r = await fetch("/api/doc?path=" + encodeURIComponent(path), { cache: "no-store" });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
    return data;
  }

  const board = window.DashboardRenderer.create(document.getElementById("app"), {
    actions: {
      approve: postApprove,
      loadDoc: fetchDoc,
      refresh: () => refresh(),
      selectPlan: (path) => { st.plan = path || null; refresh(); },
      copyText: (text) => navigator.clipboard.writeText(text),
    },
    storage: store,
    theme,
    now: () => Date.now(),
    prototypeUrl: (port, slug) => `${location.protocol}//${location.hostname}:${port}/${encodeURIComponent(slug)}/`,
    // Podgląd Markdown: HTML renderuje i escapuje serwer (/api/doc); strona działa pod CSP script-src 'self'.
    mountHtml: (el, html) => { el.innerHTML = html; },
    onTitle: (title) => { document.title = title; },
    onNavigate: (hash) => { location.hash = hash; },
    scroll: {
      save: () => window.scrollY,
      restore: (y) => window.scrollTo(0, y),
      reveal: (view, inDocs) => {
        if (matchMedia("(max-width: 1100px)").matches && inDocs) view.scrollIntoView({ block: "start" }); // lista jest nad podglądem
        else window.scrollTo(0, 0);
      },
    },
  });

  // ------------------------------------------------------------------ dane
  async function refresh() {
    if (st.fetching) { st.again = true; return st.fetching; }
    const q = st.plan ? "?plan=" + encodeURIComponent(st.plan) : "";
    st.fetching = (async () => {
      try {
        const r = await fetch("/api/status" + q, { cache: "no-store" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const data = await r.json();
        st.lastFetchOk = Date.now();
        if (st.plan && !data.plans.some((p) => p.path === st.plan)) { st.plan = null; store.set("plan", null); }
        board.update(data);
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
  }
  function startPolling() {
    if (st.pollTimer) return;
    st.pollTimer = setInterval(refresh, POLL_MS);
  }
  function stopPolling() { clearInterval(st.pollTimer); st.pollTimer = null; }
  function connect() {
    if (!("EventSource" in window)) { setLive("polling"); startPolling(); return; }
    if (st.es) st.es.close();
    const es = new EventSource("/events");
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

  // ------------------------------------------------------------------ opisy (dymki), zdarzenia dokumentu, zegar
  fetch("/api/descriptions", { cache: "no-store" }).then((r) => r.json()).then((x) => { board.setDescriptions(x); }).catch(() => { board.setDescriptions({}); });
  document.addEventListener("keydown", (e) => board.handleKey(e));
  document.addEventListener("click", () => board.closeMenu());
  window.addEventListener("scroll", () => board.hideTip(), { passive: true });
  if (darkQuery && darkQuery.addEventListener) darkQuery.addEventListener("change", () => board.syncTheme());
  setInterval(() => board.tick(), 1000);

  // ------------------------------------------------------------------ start
  const hash = decodeURIComponent(location.hash.slice(1));
  if (hash.startsWith("docs:")) board.setDoc(hash.slice(5));
  setLive("connecting");
  refresh().then(() => { if (hash.startsWith("docs")) board.setView("docs", false); });
  connect();
})();
