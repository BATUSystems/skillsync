/* dashboard — klient: SSE (/events) + /api/status, fallback na polling co 5 s. Bez zależności. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k) { try { return localStorage.getItem("dashboard:" + k) ?? localStorage.getItem("status-board:" + k); } catch { return null; } },
    set(k, v) { try { v == null ? localStorage.removeItem("dashboard:" + k) : localStorage.setItem("dashboard:" + k, v); } catch { /* brak pamięci */ } },
  };

  const STATE_LABEL = { done: "zrealizowany", active: "w trakcie", planned: "zaplanowany" };
  const STEP_LABEL = { done: "zrobiony", active: "w toku", planned: "zaplanowany", reported: "zrobiony wg checkpointu (niezaznaczony w planie)" };
  const STATUS_PL = { draft: "szkic", approved: "zatwierdzony", in_progress: "w realizacji", done: "zakończony", implemented: "wdrożony" };
  const ACT_ICON = { spec: "i-spec", plan: "i-plan", real: "i-run" };
  const POLL_MS = 5000, WATCHDOG_MS = 40000;

  const st = {
    plan: store.get("plan"), data: null, prev: null, view: "progress", doc: null, docData: null,
    live: "connecting", es: null, pollTimer: null, lastEvent: 0, fetching: null, again: false, lastFetchOk: 0,
    armed: null, armedTimer: null, approving: false, notice: null,
  };

  const icon = (id, cls = "ic") => `<svg class="${cls}" aria-hidden="true"><use href="#${id}"/></svg>`;
  const statusChip = (s) => {
    if (!s) return "";
    const cls = s === "done" || s === "approved" || s === "implemented" ? "s-done" : s === "in_progress" ? "s-active" : s === "draft" ? "s-warn" : "s-planned";
    return `<span class="chip ${cls}">${esc(STATUS_PL[s] || s)}</span>`;
  };
  const fmtDur = (ms) => {
    if (!(ms >= 0)) return "—";
    const s = Math.floor(ms / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${m}:${String(ss).padStart(2, "0")}`;
  };
  const fmtAgo = (iso) => {
    if (!iso) return "—";
    const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
    if (s < 10) return "przed chwilą";
    if (s < 60) return `${s} s temu`;
    if (s < 3600) return `${Math.floor(s / 60)} min temu`;
    return new Date(iso).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
  };
  const fmtTime = (iso) => iso ? new Date(iso).toLocaleString("pl-PL", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" }) : "—";

  // ------------------------------------------------------------------ dane
  async function refresh() {
    if (st.fetching) { st.again = true; return st.fetching; }
    const q = st.plan ? "?plan=" + encodeURIComponent(st.plan) : "";
    st.fetching = (async () => {
      try {
        const r = await fetch("/api/status" + q, { cache: "no-store" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const data = await r.json();
        st.prev = st.data; st.data = data; st.lastFetchOk = Date.now();
        if (st.plan && !data.plans.some((p) => p.path === st.plan)) { st.plan = null; store.set("plan", null); }
        render();
        if (st.view === "docs" && st.doc) loadDoc(st.doc, true);
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
    const el = $("live");
    el.className = "live is-" + mode;
    const txt = { live: "Na żywo", polling: "Odświeżam co 5 s", down: "Brak połączenia", connecting: "Łączę…" }[mode];
    const tip = {
      live: "Zmiany w planie, specu i checkpointach pojawiają się od razu (SSE).",
      polling: "Kanał na żywo niedostępny — strona pyta serwer co 5 s i sama wróci do trybu na żywo.",
      down: "Serwer statusu nie odpowiada. Strona ponawia próby automatycznie.",
      connecting: "Łączę z serwerem statusu…",
    }[mode];
    el.querySelector(".live-text").textContent = txt;
    el.dataset.tip = tip;
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

  // ------------------------------------------------------------------ render
  function render() {
    const d = st.data;
    if (!d) return;
    const plan = d.plan;
    const specAct = d.mode === "spec" && d.spec && d.spec.exists;
    const runAct = d.mode === "run";
    const title = plan ? plan.title.replace(/^Plan:\s*/i, "")
      : specAct ? (d.spec.title || d.spec.path).replace(/^Spec:\s*/i, "")
      : runAct ? "Akt I: specyfikacja w przygotowaniu" : "Brak planu w realizacji";
    $("title").textContent = title;
    const waiting = d.waiting || [];
    document.title = (waiting.length ? "⏳ Czeka · " : "") + (plan ? `${d.progress.percent}% · ` : specAct || runAct ? "Akt I · " : "") + title + " — status";
    renderWaiting(d);

    const run = d.run || {};
    const meta = [];
    if (run.id) meta.push(`<span class="m" data-tip="Identyfikator przebiegu (.skillsync/runs/${esc(run.id)})">${icon("i-flag")}<span class="mono">${esc(run.id)}</span></span>`);
    if (plan) meta.push(`<span class="m" data-tip="Status planu (nagłówek Status:)">${statusChip(plan.status)}</span>`);
    else if (specAct) meta.push(`<span class="m" data-tip="Akt I: spec bez planu (plan powstaje przy „GO plan”); status speca">${statusChip(d.spec.status)}</span>`);
    if (run.startIso) meta.push(`<span class="m" data-tip="Czas od startu aktu III (RUN= w checkpoints.md, start ${esc(fmtTime(run.startIso))})">${icon("i-clock")}<span class="mono" id="elapsed">${fmtDur(Date.now() - Date.parse(run.startIso))}</span></span>`);
    meta.push(`<span class="m" data-tip="Ostatnia zmiana planu, speca lub checkpointów">${icon("i-sync")}<span id="ago" data-iso="${esc(d.lastUpdate || "")}">${esc(fmtAgo(d.lastUpdate))}</span></span>`);
    if (d.git && d.git.branch) {
      const g = d.git;
      const host = g.remote ? (g.remote.host === "azure" ? "Azure DevOps" : "GitHub") : "";
      const name = `${esc(g.branch)}${g.head ? " @ " + esc(g.head.slice(0, 7)) : ""}`;
      const branch = g.branchUrl && g.branchPushed
        ? `<a class="mono git-link" href="${esc(g.branchUrl)}" target="_blank" rel="noopener" data-tip="Otwórz gałąź w ${host}">${name}</a>`
        : `<span class="mono" data-tip="${g.branchUrl ? "Gałąź tylko lokalnie (brak na origin), bez linku" : "Gałąź i HEAD repozytorium"}">${name}</span>`;
      meta.push(`<span class="m">${icon("i-branch")}${branch}</span>`);
    }
    if (plan && plan.pr) meta.push(`<span class="m"><a class="chip s-active git-link" href="${esc(plan.pr.url)}" target="_blank" rel="noopener" data-tip="Pull request z linii PR: planu">PR #${esc(plan.pr.number)}</a></span>`);
    $("meta").innerHTML = meta.join("");

    $("planBtnText").textContent = plan ? plan.path.split("/").pop().replace(/\.md$/, "") + (d.planAuto ? " (bieżący)" : "")
      : specAct ? d.spec.path.split("/").pop().replace(/\.md$/, "") + " (akt I)" : runAct ? "przebieg (akt I)" : "Plan";
    renderProtoLink(d);
    renderPlanMenu();
    renderActs(d);
    renderPhases(d);
    renderFeed(d);
    renderDocList(d);
    $("foot").innerHTML = `<span>dashboard ${esc(d.app.version)}</span><span>projekt: <span class="mono">${esc(d.project.path)}</span></span><span>${d.approvals && d.approvals.enabled && d.approvals.here ? "odczyt + zgody" : "tylko odczyt"} · odświeżono ${esc(new Date(d.generatedAt).toLocaleTimeString("pl-PL"))}</span>`;
  }

  // ------------------------------------------------------------------ opisy (dymki): /api/descriptions
  const GATE_KEY = { spec: "gate_go_plan", plan: "gate_go_implement", cp: "gate_cp", ready: "gate_ready" };
  function desc(key, extra) {
    if (!key) return extra ? `data-tip="${esc(extra)}"` : "";
    return `data-desc="${esc(key)}"${extra ? ` data-desc-extra="${esc(extra)}"` : ""} tabindex="0" aria-describedby="descTip"`;
  }
  function showDesc(el) {
    const tipEl = $("descTip");
    const item = (st.desc || {})[el.dataset.desc];
    const extra = el.dataset.descExtra;
    if (!item && !extra) return hideDesc();
    tipEl.innerHTML = (item ? `<strong>${esc(item.title)}</strong><span>${esc(item.text)}</span>` : "")
      + (extra ? `<em>${esc(extra)}</em>` : "");
    tipEl.hidden = false;
    const r = el.getBoundingClientRect(), w = Math.min(380, window.innerWidth - 32);
    tipEl.style.width = w + "px";
    const left = Math.max(16, Math.min(r.left, window.innerWidth - w - 16));
    const below = r.bottom + 8 + tipEl.offsetHeight < window.innerHeight;
    tipEl.style.left = left + "px";
    tipEl.style.top = (below ? r.bottom + 8 : Math.max(8, r.top - tipEl.offsetHeight - 8)) + "px";
    st.descFor = el;
  }
  function hideDesc() { $("descTip").hidden = true; st.descFor = null; st.descPinned = false; }
  document.addEventListener("mouseover", (e) => { const el = e.target.closest("[data-desc]"); if (el && !st.descPinned) showDesc(el); });
  document.addEventListener("mouseout", (e) => { const el = e.target.closest("[data-desc]"); if (el && !st.descPinned && !el.contains(e.relatedTarget)) hideDesc(); });
  document.addEventListener("focusin", (e) => { const el = e.target.closest("[data-desc]"); if (el) showDesc(el); });
  document.addEventListener("focusout", (e) => { if (e.target.closest("[data-desc]") && !st.descPinned) hideDesc(); });
  document.addEventListener("pointerup", (e) => { // dotyk: stuknięcie otwiera i zamyka
    if (e.pointerType !== "touch") return;
    const el = e.target.closest("[data-desc]");
    if (el && st.descFor === el && st.descPinned) return hideDesc();
    if (el) { showDesc(el); st.descPinned = true; } else if (st.descPinned) hideDesc();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("descTip").hidden) hideDesc(); });
  window.addEventListener("scroll", () => { if (!$("descTip").hidden) hideDesc(); }, { passive: true });
  fetch("/api/descriptions", { cache: "no-store" }).then((r) => r.json()).then((x) => { st.desc = x; }).catch(() => { st.desc = {}; });

  // Readiness (akt II): brief gotowości z .skillsync/runs/<run>/readiness.md; źródło pliku = readiness.source z JSON.
  const READY_STATE = { PASS: ["is-pass", "PASS"], BLOCKED: ["is-blocked", "BLOCKED"] };
  function readinessTile(d) {
    const r = d.readiness || {};
    const [cls, label] = READY_STATE[r.status] || ["is-none", "brak danych"];
    const sha = r.planSha ? String(r.planSha).slice(0, 8) : "";
    const stale = Boolean(r.planSha && d.plan && d.plan.fileSha && !String(d.plan.fileSha).startsWith(String(r.planSha)));
    const facts = [r.date ? `<span>${esc(r.date)}</span>` : "", sha ? `<span class="mono">plan ${esc(sha)}</span>` : "",
      stale ? `<span class="chip s-warn">brief dla innej wersji planu</span>` : ""].join("");
    const open = r.source ? `<button class="act-doc" type="button" data-open-doc="${esc(r.source)}" data-tip="${esc(r.source)} w zakładce Dokumenty">${icon("i-doc")}readiness.md</button>` : "";
    return `<div class="readiness ${cls}"><span class="stage readiness-chip" ${desc("stage_readiness", r.source || "brak pliku readiness.md w przebiegu")}>${cls === "is-pass" ? icon("i-check") : ""}Readiness: ${esc(label)}</span>${facts ? `<span class="readiness-facts">${facts}</span>` : ""}${open}</div>`;
  }
  // Pytania (akt I): blokujące i założenia ze speca; widoczne dla każdego (bez zależności od approvals.here/viewer).
  // Założenia nie są akcją „Odpowiedz na pytania”: tylko lista do korekty przy prototypie.
  function questionsBlock(d) {
    const bq = d.blockingQuestions || { count: 0, items: [] };
    const as = d.assumptions || { count: 0, items: [] };
    if (!(d.spec && d.spec.exists) && !bq.count && !as.count) return "";
    const list = (items, line) => items.length ? `<ul class="q-list">${items.map(line).join("")}</ul>` : `<div class="q-none">brak</div>`;
    const blocking = list(bq.items || [], (q) => `<li><span class="mono q-id">${esc(q.id)}</span><span>${esc(q.question)}</span></li>`);
    const assumed = list(as.items || [], (q) => `<li><span class="mono q-id">${esc(q.id)}</span><span>${esc(q.question)} — przyjęte: <strong>${esc(q.accepted || "—")}</strong></span></li>`);
    return `<div class="questions" role="group" aria-label="Pytania">
      <div class="q-title">Pytania</div>
      <div class="q-group${bq.count ? " is-blocking" : ""}"><div class="q-head" ${desc("questions_blocking")}>Pytania blokujące (${esc((bq.items || []).length || bq.count || 0)})</div>${blocking}</div>
      <div class="q-group"><div class="q-head" ${desc("questions_assumptions")}>Założenia do korekty przy prototypie (${esc((as.items || []).length || as.count || 0)})</div>${assumed}</div></div>`;
  }
  // Podkroki Fn.k z czasem (akt III): rozwijana lista; stan rozwinięcia przeżywa odświeżenie (st.substepsOpen).
  function substepsBlock(d) {
    const items = d.substeps || [];
    if (!items.length) return "";
    const time = (s) => s.seconds != null ? fmtDur(s.seconds * 1000) : s.missingData ? "brak danych" : esc(STEP_LABEL[s.state] || s.state);
    return `<details class="substeps"${st.substepsOpen ? " open" : ""}><summary ${desc("substeps_time")}>Podkroki i czas (${items.length})</summary><ul class="sub-list">${items.map((s) =>
      `<li class="${s.missingData ? "is-nodata" : ""}"><span class="mono q-id">${esc(s.id)}</span><span class="tt">${esc(s.title)}</span><span class="mono sub-t">${time(s)}</span></li>`).join("")}</ul></details>`;
  }

  function renderActs(d) {
    const acts = d.acts;
    const anyActive = acts.some((a) => a.state === "active");
    $("acts").innerHTML = acts.map((a) => {
      const glow = a.state === "active" || (!anyActive && a.key === "real" && a.state === "done");
      const stages = (a.stages || []).map((s) => `<span class="stage${s.done ? " is-done" : ""}" ${desc(s.key, s.tip)}>${s.done ? icon("i-check") : ""}${esc(s.label)}</span>`).join("");
      const warn = a.warning ? `<div class="act-warn" ${desc("warning_pre_implement", a.warning)}>⚠ ${esc(a.warning)}</div>` : "";
      let body = "";
      if (a.key === "real") {
        const p = d.progress;
        const cur = p.current;
        const barCls = a.state === "done" ? "is-done" : a.state === "active" ? "is-active" : "";
        body = `
          <div class="big"><span class="pct">${a.state === "done" && p.percent < 100 ? "✓ " : ""}${p.percent}%</span>${p.pendingPercent ? `<span class="pend" data-tip="Kroki zgłoszone jako zrobione w checkpointach (${esc(p.reported.join(", "))}), jeszcze niezaznaczone [x] w planie — czekają na review">(+${p.pendingPercent}% do weryfikacji)</span>` : ""}<span class="of" data-tip="Kroki odhaczone [x] w planie">${p.done} z ${p.total} kroków zrobionych</span></div>
          ${bar(p.percent, p.pendingPercent, barCls, "Postęp realizacji")}
          ${cur ? `<div class="now" data-tip="Teraz wykonywane: ${esc(p.active.join(", "))}, ${cur.index}. pozycja z ${p.total} w planie (${p.activeSource === "checkpoint" ? "z ostatniego checkpointu „w toku”" : "pierwszy niezaznaczony krok"})"><span class="dot-live"></span><span class="txt">teraz: <span class="mono">${esc(cur.id)}</span> ${esc(cur.title)}</span>${p.parallel ? `<span class="par">+${p.parallel} równolegle</span>` : ""}</div>`
              : `<div class="act-detail">${esc(a.detail || "")}</div>`}
          <div class="stages">${stages}</div>
          ${substepsBlock(d)}
          <div class="act-actions">${approveButton(d, "cp", "act-approve")}${approveButton(d, "ready", "act-approve")}</div>`;
      } else {
        const doc = a.key === "spec" ? (d.spec && d.spec.exists ? { path: d.spec.path, label: "Otwórz specyfikację" } : null)
          : a.key === "plan" ? (d.plan ? { path: d.plan.path, label: "Otwórz plan" } : null) : null;
        const docLink = doc ? `<button class="act-doc" type="button" data-open-doc="${esc(doc.path)}" data-tip="${esc(doc.path)} w zakładce Dokumenty">${icon("i-doc")}${doc.label}</button>` : "";
        const approve = approveButton(d, a.key === "spec" ? "spec" : "plan", "act-approve");
        const extra = a.key === "plan" ? readinessTile(d) : "";
        const after = a.key === "spec" ? questionsBlock(d) : "";
        body = `<div class="act-detail">${esc(a.detail || " ")}</div><div class="stages">${stages}</div>${extra}${warn}<div class="act-actions">${docLink}${approve}</div>${after}`;
      }
      return `<div class="act s-${a.state}${glow ? " s-active" : ""}" data-key="${a.key}">
        <div class="roman" aria-hidden="true">${a.roman}</div>
        <div class="act-head" ${desc("act_" + a.key)}><div class="act-icon">${icon(ACT_ICON[a.key])}</div>
          <div class="act-name"><small>Akt ${a.roman}</small>${esc(a.name)}</div>
          <span class="chip s-${a.state}" style="margin-left:auto">${esc(STATE_LABEL[a.state])}</span></div>
        ${body}</div>`;
    }).join("");
    animateBars($("acts"));
  }

  function renderPhases(d) {
    const p = d.progress;
    const prevStates = {};
    if (st.prev) st.prev.progress.phases.forEach((ph) => ph.steps.forEach((s) => { prevStates[s.id] = s.state; }));
    if (!d.plan && d.mode === "spec") {
      $("phases").innerHTML = `<div class="phase"><div class="empty">Akt I: spec <span class="mono">${esc(d.spec.path)}</span> (${esc(d.spec.status || "—")}) nie ma jeszcze planu. Kroki pojawią się tu same, gdy po „GO plan” powstanie plan w <span class="mono">docs/plans</span>.</div></div>`;
      return;
    }
    if (!d.plan && d.mode === "run") {
      $("phases").innerHTML = `<div class="phase"><div class="empty">Akt I: przebieg <span class="mono">${esc(d.run.id)}</span> ruszył, specyfikacja jest w przygotowaniu. Spec pojawi się tu sam po zapisaniu w <span class="mono">docs/specs</span>, a kroki po „GO plan”.</div></div>`;
      return;
    }
    if (!d.plan) {
      $("phases").innerHTML = `<div class="phase"><div class="empty">Brak planu ze statusem draft, approved albo in_progress w <span class="mono">docs/plans</span>. Plan pojawi się tu sam po zapisaniu pliku.</div></div>`;
      return;
    }
    if (!p.phases.length) {
      $("phases").innerHTML = `<div class="phase"><div class="empty">Plan nie ma jeszcze sekcji <span class="mono">## Progress</span> z krokami <span class="mono">- [ ] S1 Tytuł</span>.</div></div>`;
      return;
    }
    st.openPhases = st.openPhases || {};
    $("phases").innerHTML = p.phases.map((ph, i) => {
      // Faza zamknięta zwija się do nagłówka (k/n); kliknięcie albo Enter rozwija.
      const folded = ph.state === "done" && ph.total > 1 && !st.openPhases[ph.name];
      return `
      <section class="phase s-${ph.state}${folded ? " is-folded" : ""}">
        <div class="phase-head" ${desc("phase", `${ph.done}/${ph.total} podkroków`)}${ph.state === "done" && ph.total > 1 ? ` role="button" tabindex="0" aria-expanded="${!folded}" data-phase-toggle="${esc(ph.name)}"` : ""}><div>${ph.label ? `<div class="lbl">${esc(ph.label)}</div>` : ""}<div class="nm">${esc(ph.short)}</div></div><span class="n">${ph.state === "done" ? icon("i-check") : ""}${ph.done}/${ph.total}</span></div>
        ${bar(ph.percent, ph.pendingPercent, "thin " + (ph.state === "done" ? "is-done" : ""), ph.short)}
        <ul class="steps">${ph.steps.map((s) => {
          const changed = st.prev && prevStates[s.id] && prevStates[s.id] !== s.state;
          const tip = `${s.id} ${s.title} — ${STEP_LABEL[s.state]}${s.sha ? " · commit " + s.sha : ""}${s.note && !s.sha ? " · " + s.note : ""}`;
          const mark = s.state === "done" || s.state === "reported" ? icon("i-check") : "";
          return `<li class="step s-${s.state}${changed ? " flash" : ""}" ${desc(/^zamknięcie/i.test(s.title) ? "closure" : "substep", tip)}><span class="st" aria-label="${esc(STEP_LABEL[s.state])}">${mark}</span><span class="sid">${esc(s.id)}</span><span class="tt">${esc(s.title)}</span></li>`;
        }).join("")}</ul>
      </section>`;
    }).join("");
    animateBars($("phases"));
  }

  function togglePhase(e) {
    const h = e.target.closest("[data-phase-toggle]");
    if (!h || (e.type === "keydown" && e.key !== "Enter" && e.key !== " ")) return;
    e.preventDefault();
    const name = h.dataset.phaseToggle;
    st.openPhases[name] = !st.openPhases[name];
    renderPhases(st.data);
  }
  $("phases").addEventListener("click", togglePhase);
  $("phases").addEventListener("keydown", togglePhase);

  function bar(pct, pending, cls, label) {
    const tip = pending ? ` data-tip="${pct}% zaznaczone w planie + ${pending}% zrobione wg checkpointu (czeka na weryfikację)"` : "";
    return `<div class="bar ${cls}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" aria-label="${esc(label)}"${pending ? ` aria-valuetext="${pct}%, plus ${pending}% do weryfikacji"` : ""}${tip}>`
      + `<div class="fill" data-w="${pct}"></div>${pending ? `<div class="fill-pending" data-w="${pending}"></div>` : ""}</div>`;
  }

  function animateBars(root) {
    requestAnimationFrame(() => root.querySelectorAll(".fill[data-w], .fill-pending[data-w]").forEach((f) => { f.style.width = f.dataset.w + "%"; }));
  }

  function decorateCheckpoint(text) {
    let h = esc(text);
    h = h.replace(/(?<![\w-])([A-Z]{1,3}-?\d+[a-z]?)(?![\w-])/g, '<span class="sidc">$1</span>');
    h = h.replace(/\b(zrobione|w toku|ryzyko|PASS|FAIL|BLOCKED|restart sesji)\b/gi, '<span class="ph">$1</span>');
    return h;
  }

  function renderFeed(d) {
    const items = (d.run && d.run.checkpoints) || [];
    const prevTop = st.prev && st.prev.run && st.prev.run.checkpoints && st.prev.run.checkpoints[0];
    const prevCount = (st.prev && st.prev.run && st.prev.run.checkpoints || []).length;
    const fresh = st.prev ? Math.max(0, items.length - prevCount) : 0;
    $("feed").innerHTML = items.length
      ? items.map((c, i) => `<li class="${i < fresh || (i === 0 && prevTop && prevTop.text !== c.text) ? "is-new" : ""}"><span class="t">${esc(c.time)}</span><span>${decorateCheckpoint(c.text)}</span></li>`).join("")
      : `<li><span></span><span class="empty">Brak checkpointów — pojawią się, gdy przebieg zapisze <span class="mono">checkpoints.md</span>.</span></li>`;
  }

  // ------------------------------------------------------------------ plan
  function renderPlanMenu() {
    const d = st.data;
    const cur = d.plan && d.plan.path;
    const items = [`<li role="option" data-plan="" aria-selected="${d.planAuto}"><span>Automatycznie: bieżący plan</span><span class="sub">in_progress → approved → draft</span></li>`]
      .concat(d.plans.slice().reverse().map((p) => `<li role="option" data-plan="${esc(p.path)}" aria-selected="${!d.planAuto && p.path === cur}"><span><span>${esc(p.title.replace(/^Plan:\s*/i, ""))}</span><br><span class="sub">${esc(p.path)}</span></span>${statusChip(p.status)}</li>`));
    $("planMenu").innerHTML = items.join("");
  }
  function toggleMenu(open) {
    const menu = $("planMenu");
    const show = open ?? menu.hidden;
    menu.hidden = !show;
    $("planBtn").setAttribute("aria-expanded", String(show));
  }
  $("planBtn").addEventListener("click", (e) => { e.stopPropagation(); toggleMenu(); });
  $("planMenu").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-plan]");
    if (!li) return;
    st.plan = li.dataset.plan || null;
    store.set("plan", st.plan);
    st.prev = null;
    toggleMenu(false);
    refresh();
  });
  document.addEventListener("click", () => toggleMenu(false));

  // ------------------------------------------------------------------ sesja czeka na człowieka
  const WAIT_SOURCE = { silence: "czas od ostatniej linii checkpoints.md (próg z serwera)", checkpoint: "znacznik „czeka:” w ostatnim checkpoincie", plan: "status planu", spec: "status speca", prototype: "akceptacja ekranów prototypu" };
  function protoUrl(d, slug) {
    return `${location.protocol}//${location.hostname}:${d.prototype.port}/${encodeURIComponent(slug)}/`;
  }
  // Zgoda przyciskiem („GO plan”, „GO implement”): serwer woła skillsync spec|plan approve z sha pokazanej wersji.
  // Dwa kliknięcia (uzbrojenie na 5 s), tylko z przeglądarki na tej maszynie (approvals.here).
  // Stany (z serwera): brak pozycji = akt jeszcze nie nadszedł (przycisku nie ma); waiting = aktywny;
  // running = nieaktywny „czeka na…”; after = nieaktywny „zgoda dana HH:MM” (drugi zapis niemożliwy).
  function approveButton(d, kind, cls) {
    const ap = d.approvals;
    const a = ap && ap.enabled && (ap.actions || []).find((x) => x.kind === kind);
    if (!a) return "";
    if (a.state === "after") {
      const at = a.given && a.given.at ? new Date(a.given.at).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" }) : "";
      return `<button class="${cls} is-given" type="button" disabled data-state="after" ${desc(GATE_KEY[kind], a.reason)}>${icon("i-check")}${esc(a.word)}${a.target ? " → " + esc(a.target) : ""}${at ? " " + esc(at) : ""}</button>`;
    }
    if (a.state !== "waiting") return `<button class="${cls} is-waiting" type="button" disabled data-state="running" ${desc(GATE_KEY[kind], a.reason)}>${esc(a.word)}${a.target ? " → " + esc(a.target) : ""}</button>`;
    if (!ap.here) {
      // Widz z hosta --read-host (approvals.viewer): zgody daje prowadzący, widz nie ma sesji, więc nie pokazujemy instrukcji wpisania słowa.
      const tip = ap.viewer === true ? "Zgody daje prowadzący na komputerze z sesją." : `Zgoda tylko z przeglądarki na komputerze z sesją (localhost albo --approve-host); tu wpisz „${a.word}” w sesji`;
      return `<button class="${cls}" type="button" disabled data-state="waiting" ${desc(GATE_KEY[kind], tip)}>${icon("i-check")}${esc(a.word)}</button>`;
    }
    const armed = st.armed === kind;
    const to = a.target ? ` → ${a.target}` : "";
    const label = st.approving && armed ? "Zapisuję…" : armed ? `Na pewno? Kliknij, by dać „${a.word}”${to}` : `Zatwierdź „${a.word}”${to}`;
    const what = kind === "cp" || kind === "ready" ? `${a.reason ? a.reason + "; " : ""}HEAD ${a.sha.slice(0, 8)}` : `${a.path} (wersja ${a.sha.slice(0, 8)})`;
    return `<button class="${cls}${armed ? " is-armed" : ""}" type="button" data-state="waiting" data-approve="${kind}" ${desc(GATE_KEY[kind], `Zgoda jak wpisanie „${a.word}” w sesji — ${what}`)}>${icon("i-check")}${esc(label)}</button>`;
  }
  async function approve(kind) {
    const a = ((st.data && st.data.approvals && st.data.approvals.actions) || []).find((x) => x.kind === kind);
    if (!a || st.approving) return;
    if (st.armed !== kind) {
      st.armed = kind;
      clearTimeout(st.armedTimer);
      st.armedTimer = setTimeout(() => { st.armed = null; render(); }, 5000);
      return render();
    }
    clearTimeout(st.armedTimer);
    st.approving = true; render();
    let res;
    try {
      const r = await fetch("/api/approve", { method: "POST", cache: "no-store", headers: { "Content-Type": "application/json", "X-Status-Board": "1" },
        body: JSON.stringify({ kind, path: a.path, sha: a.sha, plan: st.plan || null }) });
      const body = await r.json().catch(() => ({}));
      res = r.ok ? { ok: true, word: body.word, text: body.next } : { ok: false, word: a.word, text: body.error || `Błąd ${r.status}` };
    } catch {
      res = { ok: false, word: a.word, text: "Brak połączenia z serwerem statusu." };
    }
    st.approving = false; st.armed = null; st.notice = res;
    render(); refresh();
  }
  function wordTools(d, word) {
    const url = d.session && d.session.url;
    return `<button class="wait-go wait-copy" type="button" data-copy-word="${esc(word)}" data-tip="Kopiuje „${esc(word)}” do schowka">Kopiuj „${esc(word)}”</button>`
      + (url ? `<a class="wait-go wait-session" href="${esc(url)}" target="_blank" rel="noopener" data-tip="Otwiera sesję (adres z --session-url)">Otwórz sesję</a>` : "");
  }
  // Brak raportu postępu od N min (plan in_progress, sesja nie zgłosiła „czeka:”): sesja mogła się zawiesić.
  function silenceMinutes(d) {
    const s = d && d.silence;
    if (!s || !s.since) return 0;
    const minutes = Math.floor((Date.now() - Date.parse(s.since)) / 60000);
    return minutes >= s.thresholdMin ? minutes : 0;
  }
  function renderWaiting(d) {
    const el = $("waitBanner");
    const quiet = silenceMinutes(d);
    st.silenceShown = quiet > 0;
    const items = (d.waiting || []).concat(quiet ? [{ what: `brak raportu od ${quiet} min`, source: "silence", since: null,
      action: "Raport postępu powinien być po każdym kroku albo co 30 minut. Sesja mogła się zawiesić albo długo pracuje bez raportu: zajrzyj do sesji.", link: null }] : []);
    const n = st.notice;
    const note = n ? `<div class="wait-item${n.ok ? " is-ok" : " is-err"}">${icon(n.ok ? "i-check" : "i-bell")}<div class="wait-txt"><strong>${n.copied ? `„${esc(n.word)}” w schowku` : n.ok ? `Zgoda „${esc(n.word)}” zapisana` : `Nie zapisano „${esc(n.word)}”`}</strong><span>${esc(n.text)}</span></div>${n.ok && !n.copied ? wordTools(d, "dalej") : ""}<button class="wait-x" type="button" data-notice-close aria-label="Zamknij">×</button></div>` : "";
    if (!items.length && !note) { el.hidden = true; el.innerHTML = ""; return; }
    el.hidden = false;
    el.innerHTML = note + items.map((w) => {
      let link = "";
      if (w.link && w.link.kind === "doc") link = `<button class="wait-go" type="button" data-wait-doc="${esc(w.link.path)}">Otwórz ${esc(w.link.path.split("/").pop())}</button>`;
      else if (w.link && w.link.kind === "prototype") link = `<a class="wait-go" href="${esc(protoUrl(d, w.link.slug))}" target="_blank" rel="noopener">Otwórz prototyp ${esc(w.link.slug)}</a>`;
      const doc = w.approve === "spec" ? d.spec : w.approve === "plan" ? d.plan : null;
      const act = w.approve && ((d.approvals && d.approvals.actions) || []).find((x) => x.kind === w.approve);
      const given = (doc && ["approved", "in_progress", "done", "implemented"].includes(doc.status)) || Boolean(act && act.state === "after");
      const tools = given ? wordTools(d, "dalej")
        : (w.approve ? approveButton(d, w.approve, "wait-go wait-approve") : "") + (w.word ? wordTools(d, w.word) : "");
      const action = given ? `Zgoda „${w.word}” jest już w pliku; napisz w sesji „dalej”, jeśli sesja jeszcze nie ruszyła.` : w.action;
      const since = w.since ? ` od ${esc(w.since)}` : "";
      return `<div class="wait-item" ${desc(w.source === "silence" ? "banner_silence" : "banner_wait")}>${icon("i-bell")}<div class="wait-txt"><strong>${w.source === "silence" ? `Brak raportu postępu: ${esc(w.what.replace(/^brak raportu /, ""))}` : `Sesja czeka na Ciebie${since}: ${esc(w.what)}`}</strong><span>${esc(action)}</span><small>Źródło: ${esc(WAIT_SOURCE[w.source] || w.source)}. ${w.source === "silence" ? "Zniknie po następnym raporcie." : "Baner zniknie, gdy sesja ruszy."}</small></div><div class="wait-actions">${link}${tools}</div></div>`;
    }).join("");
  }
  async function copyWord(btn) {
    const word = btn.dataset.copyWord;
    try { await navigator.clipboard.writeText(word); btn.textContent = `Skopiowano „${word}”`; }
    catch { btn.textContent = `Wpisz w sesji: ${word}`; }
    setTimeout(() => { btn.textContent = `Kopiuj „${word}”`; }, 2500);
  }
  function actionClick(e) {
    const ap = e.target.closest("[data-approve]");
    if (ap) { approve(ap.dataset.approve); return true; }
    const cp = e.target.closest("[data-copy-word]");
    if (cp) { copyWord(cp); return true; }
    if (e.target.closest("[data-notice-close]")) { st.notice = null; render(); return true; }
    return false;
  }
  $("waitBanner").addEventListener("click", (e) => {
    if (actionClick(e)) return;
    const b = e.target.closest("[data-wait-doc]");
    if (!b) return;
    st.doc = b.dataset.waitDoc;
    setView("docs");
  });
  // toggle nie bubbluje: nasłuch w fazie przechwytywania na kontenerze aktów.
  $("acts").addEventListener("toggle", (e) => { if (e.target.matches && e.target.matches("details.substeps")) st.substepsOpen = e.target.open; }, true);
  $("acts").addEventListener("click", (e) => {
    if (actionClick(e)) return;
    const b = e.target.closest("[data-open-doc]");
    if (!b) return;
    st.doc = b.dataset.openDoc;
    setView("docs");
  });

  // ------------------------------------------------------------------ prototyp (przy zakładkach: często używany)
  function renderProtoLink(d) {
    const el = $("protoLink");
    const pr = d.prototype;
    if (!pr || !pr.slug) { el.hidden = true; el.innerHTML = ""; return; }
    const acc = d.spec && d.spec.prototypeAcceptance;
    const accTxt = acc && acc.screens ? `${acc.accepted}/${acc.screens}` : "";
    const lbl = `<span class="lbl">Prototyp${accTxt ? " · " : ""}</span>`;
    const url = protoUrl(d, pr.slug);
    el.hidden = false;
    el.innerHTML = pr.up
      ? `<a class="proto-link" aria-label="Prototyp ${esc(pr.slug)}${accTxt ? ", zaakceptowane " + esc(accTxt) : ""}" href="${esc(url)}" target="_blank" rel="noopener" data-tip="Otwórz prototyp ${esc(pr.slug)} (port ${pr.port})${acc && acc.screens ? `; zaakceptowane ekrany ${acc.accepted} z ${acc.screens}` : ""}">${icon("i-proto")}${lbl}${esc(accTxt)}</a>`
      : `<span class="proto-link is-off" aria-label="Prototyp ${esc(pr.slug)}: serwer wyłączony" data-tip="Serwer prototypów (port ${pr.port}) nie działa — uruchom go, żeby otworzyć ${esc(pr.slug)}">${icon("i-proto")}${lbl}${esc(accTxt)}<span class="lbl"> (serwer wyłączony)</span></span>`;
  }

  // ------------------------------------------------------------------ dokumenty
  function renderDocList(d) {
    const groups = d.docs || [];
    $("docCount").textContent = groups.reduce((n, g) => n + g.items.length, 0) || "";
    $("docList").innerHTML = groups.map((g) => `<h3>${esc(g.label)}</h3>` + g.items.map((it) => `
      <button class="doc-item" data-doc="${esc(it.path)}" aria-current="${it.path === st.doc}" data-tip="${esc(it.path)} · zmieniony ${esc(fmtTime(it.mtime))}">
        ${icon("i-doc")}<span class="nm">${esc(g.key === "run" ? it.path.split("/").pop() : g.key === "task" ? it.title : it.title.replace(/^(Plan|Spec):\s*/i, ""))}</span>${statusChip(it.status)}
      </button>`).join("")).join("");
    if (!st.doc && groups[0]) st.doc = groups[0].items[0].path;
  }
  $("docList").addEventListener("click", (e) => {
    const b = e.target.closest("[data-doc]");
    if (!b) return;
    loadDoc(b.dataset.doc);
  });

  async function loadDoc(path, live = false) {
    const view = $("docView");
    const scroll = live ? window.scrollY : 0;
    st.doc = path;
    if (!live) location.hash = "docs:" + path;
    document.querySelectorAll(".doc-item").forEach((b) => b.setAttribute("aria-current", String(b.dataset.doc === path)));
    try {
      const r = await fetch("/api/doc?path=" + encodeURIComponent(path), { cache: "no-store" });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
      if (live && st.docData && st.docData.path === data.path && st.docData.mtime === data.mtime) return;
      st.docData = data;
      view.innerHTML = `<div class="doc-bar">${icon("i-doc")}<span class="mono">${esc(data.path)}</span>${statusChip(data.status)}<span>zmieniony ${esc(fmtTime(data.mtime))}</span>${data.truncated ? '<span class="chip s-warn">obcięty do 2 MB</span>' : ""}${live ? '<span class="chip s-active">odświeżono</span>' : ""}</div><div class="md">${data.html}</div>`;
      if (live) window.scrollTo(0, scroll);
      else if (matchMedia("(max-width: 1100px)").matches && st.view === "docs") view.scrollIntoView({ block: "start" }); // lista jest nad podglądem
      else window.scrollTo(0, 0);
    } catch (err) {
      view.innerHTML = `<div class="empty">Nie udało się wczytać dokumentu: ${esc(err.message)}. <button class="btn" id="retryDoc">Spróbuj ponownie</button></div>`;
      $("retryDoc").addEventListener("click", () => loadDoc(path));
    }
  }
  $("docView").addEventListener("click", (e) => {
    const a = e.target.closest("a.md-doc, a.md-anchor");
    if (!a) return;
    e.preventDefault();
    if (a.classList.contains("md-doc")) loadDoc(a.dataset.doc);
    else { const t = document.getElementById(a.dataset.anchor); if (t) t.scrollIntoView({ behavior: "smooth" }); }
  });

  // ------------------------------------------------------------------ zakładki
  function setView(view, push = true) {
    st.view = view;
    ["progress", "docs"].forEach((v) => {
      $("view-" + v).hidden = v !== view;
      $("tab-" + v).setAttribute("aria-selected", String(v === view));
    });
    if (view === "docs" && st.doc) loadDoc(st.doc);
    if (push) location.hash = view === "docs" && st.doc ? "docs:" + st.doc : view;
  }
  // ------------------------------------------------------------------ motyw
  const darkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const isDark = () => (document.documentElement.dataset.theme || (darkQuery && darkQuery.matches ? "dark" : "light")) === "dark";
  function syncThemeBtn() {
    $("themeBtn").setAttribute("aria-pressed", String(isDark()));
  }
  function toggleTheme() {
    const next = isDark() ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("theme", next);
    syncThemeBtn();
  }
  $("themeBtn").addEventListener("click", toggleTheme);
  if (darkQuery && darkQuery.addEventListener) darkQuery.addEventListener("change", syncThemeBtn);
  syncThemeBtn();

  $("tab-progress").addEventListener("click", () => setView("progress"));
  $("tab-docs").addEventListener("click", () => setView("docs"));
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea, select, [contenteditable]") || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "p" || e.key === "P") setView("progress");
    else if (e.key === "d" || e.key === "D") setView("docs");
    else if (e.key === "t" || e.key === "T") toggleTheme();
    else if (e.key === "Escape") toggleMenu(false);
  });

  // ------------------------------------------------------------------ zegar
  setInterval(() => {
    const d = st.data;
    if (!d) return;
    const el = $("elapsed");
    if (el && d.run.startIso) el.textContent = fmtDur(Date.now() - Date.parse(d.run.startIso));
    const ago = $("ago");
    if (ago) ago.textContent = fmtAgo(ago.dataset.iso);
    const quiet = silenceMinutes(d);
    if ((quiet > 0) !== Boolean(st.silenceShown) || (quiet && quiet !== st.silenceMin)) { st.silenceMin = quiet; renderWaiting(d); }
  }, 1000);

  // ------------------------------------------------------------------ start
  const hash = decodeURIComponent(location.hash.slice(1));
  if (hash.startsWith("docs:")) { st.doc = hash.slice(5); }
  setLive("connecting");
  refresh().then(() => { if (hash.startsWith("docs")) setView("docs", false); });
  connect();
})();
