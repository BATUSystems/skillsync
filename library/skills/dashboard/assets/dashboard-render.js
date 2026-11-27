/* dashboard — wspólny renderer „JSON (kontrakt schema 1) → DOM” strony `skillsync dashboard serve` i webview Skippstera.
   Czysty widok: nie zna transportu ani pamięci przeglądarki (wszystko wstrzykuje host w `deps`), a DOM buduje wyłącznie
   przez createElement/createElementNS/textContent/setAttribute (zgodność z CSP webview: bez wstrzykiwania HTML, bez atrybutu
   stylu, bez handlerów inline). Kontrakt: library/skills/dashboard/SKILL.md, sekcja „Renderer”.
   Wewnętrzne API renderera (nie publiczny kontrakt CLI); odstępstwa od pierwotnego szkicu w specu DASH-2 (zaakceptowane przez Dyspozytora):
   nazwy `create`/`update` (aliasy `createDashboard`/`render`); brak `deps.transport` (transport to host przez `actions`); opisy przez
   `setDescriptions(map)`; wymagane `deps.now`; `actions.approve({kind, sha, path, word, planState})` zwraca Promise `{ok, word, text}`;
   `actions.openLink(kind, id, url)` zwraca bool (true = host obsłużył); nowe `actions.loadDoc/selectPlan`; nowe `deps`:
   `canApprove`, `texts` (obiekt albo funkcja `(lang) => obiekt`), `theme`, `prototypeUrl`, `mountHtml`, `onTitle`, `onNavigate`, `scroll`, `timers`, `raf`, `cancelRaf`, `viewport`,
   `sprite`; `approvals.viewer` (kontrakt DASH-1) obsługiwane; `href` z JSON-a tylko http(s). Pełna tabela: SKILL.md.
   CORE-2000-UNIFIED, dodatki (host bez nich rysuje stronę jak dotąd): `deps.session()` zwraca null (host bez sesji) albo `{state: "loading"|"anonymous"|"user",
   name, roles, approver, mustChange, loginAllowed, expired, legacy, tab, passwordMin, passwordSignIn[, alerts][, login]}` (`alerts`: serie nieudanych logowań `{at, account, failures}`, baner tylko dla `approver`); z sesją zgody wymagają roli `approver` i potwierdzenia
   w oknie (bez hasła), `actions.login/logout/changePassword({current, next})` dają nagłówek konta; `actions.approve` dostaje też `branch`, `target` i `mode`
   pokazanej wersji, a odmowa może nieść `code`. `board.sessionChanged()` po zmianie sesji, `board.closeDialogs()` gdy host chowa stronę.
   Zakładka „Prototypy” (lista z `prototypes[]`) jest dla każdego hosta; dodatki opcjonalne: `deps.prototypeFrame === true` (wolno osadzić prototyp w ramce),
   `actions.loadPrototype({slug})` → `{ok, data}` albo `{ok: false, code, text}` i `actions.prototypeAction({slug, action, body})` → `{ok, code, text}`
   (akceptacje i wątki; tylko z sesją `deps.session`). */
(function (scope, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory();
  else scope.DashboardRenderer = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  // Per-value keys of the text catalog (`ui.*`); the texts come from the language catalog (DEFAULT_TEXTS below, `texts` of the status JSON; see `tr` in create()).
  const STATE_KEY = { done: "ui.state.done", active: "ui.state.active", planned: "ui.state.planned" };
  const STEP_KEY = { done: "ui.step.done", active: "ui.step.active", planned: "ui.step.planned", reported: "ui.step.reported" };
  const STATUS_KEY = { draft: "ui.status.draft", approved: "ui.status.approved", in_progress: "ui.status.inProgress", done: "ui.status.done", implemented: "ui.status.implemented" };
  const ACT_ICON = { spec: "i-spec", plan: "i-plan", real: "i-run" };
  const GATE_KEY = { spec: "gate_go_plan", plan: "gate_go_implement", cp: "gate_cp", ready: "gate_ready" };
  const WAIT_SOURCE_KEY = { silence: "ui.waitSource.silence", checkpoint: "ui.waitSource.checkpoint", plan: "ui.waitSource.plan", spec: "ui.waitSource.spec", prototype: "ui.waitSource.prototype", writing: "ui.waitSource.writing", expert: "ui.waitSource.expert" };
  const LIVE_KEY = { live: "ui.live.live", polling: "ui.live.polling", down: "ui.live.down", connecting: "ui.live.connecting" };
  const LIVE_TIP_KEY = { live: "ui.liveTip.live", polling: "ui.liveTip.polling", down: "ui.liveTip.down", connecting: "ui.liveTip.connecting" };
  const ARM_MS = 5000;
  // Widz zdalny (approvals.viewer, Host z --read-host; brak pola = false): odczyt bez zapisu. Przyciski zgody są nieaktywne
  // z jednym powodem (ui.viewer.readOnly), a renderer nie woła actions.approve.
  const isViewer = (d) => { const ap = d && d.approvals; return Boolean(ap && ap.viewer === true); };
  // Refusals of a consent that have a text of the catalog (the code of the server's answer); any other refusal shows the server's own text.
  const CONSENT_ERR_KEY = { login_required: "ui.consent.err.loginRequired", session_expired: "ui.consent.err.sessionExpired",
    forbidden_role: "ui.consent.err.forbiddenRole", password_change_required: "ui.consent.err.passwordChange", consent_refused: "ui.consent.err.consentRefused",
    delivery_changed: "ui.consent.err.deliveryChanged", tabChanged: "ui.consent.err.tabChanged" };
  const PASSWORD_ERR_KEY = { invalid_current: "ui.session.pwInvalidCurrent", weak_password: "ui.session.pwShort", rate_limited: "ui.session.pwLimited",
    busy: "ui.session.pwLimited", unauthorized: "ui.session.expired", secure_connection_required: "ui.session.needsTls" };
  const PASSWORD_MIN = 10, PASSWORD_MIN_APPROVER = 14;
  // Zakładka „Prototypy”: ramka prototypu nie dostaje okien, nawigacji karty ani pobrań; odmowy akcji na prototypie mające tekst w katalogu.
  const PROTO_SANDBOX = "allow-scripts allow-same-origin allow-forms";
  const PROTO_SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const PROTO_PRIVILEGED = ["acceptor", "developer"];
  const PROTO_ERR_KEY = { stale_view: "ui.proto.err.stale", forbidden_role: "ui.proto.err.forbiddenRole", login_required: "ui.consent.err.loginRequired",
    session_expired: "ui.consent.err.sessionExpired", password_change_required: "ui.consent.err.passwordChange", consent_refused: "ui.consent.err.consentRefused",
    prototypes_not_served: "ui.proto.err.otherTab", cid_conflict: "ui.proto.err.cidConflict", tabChanged: "ui.consent.err.tabChanged" };
  const PROTO_ACTION_KEY = { accept: "ui.proto.act.accept", unaccept: "ui.proto.act.unaccept", restore: "ui.proto.act.restore", resolve: "ui.proto.act.resolve",
    delete: "ui.proto.act.delete", "project/accept": "ui.proto.act.project" };
  const PROTO_SCREEN_KEY = { accepted: ["ui.proto.screen.accepted", "s-active"], stale: ["ui.proto.screen.stale", "s-warn"], pending: ["ui.proto.screen.pending", "s-planned"] };
  // `path` seen from `base` (both absolute, either separator): the relative path of a worktree beside the main one.
  function relativePath(base, path) {
    const split = (v) => String(v).split(/[\\/]+/).filter(Boolean);
    const from = split(base), to = split(path);
    let i = 0;
    while (i < from.length && i < to.length && from[i] === to[i]) i += 1;
    if (!i) return String(path);
    return from.slice(i).map(() => "..").concat(to.slice(i)).join("/") || ".";
  }
  const ID_RE = /(?<![\w-])([A-Z]{1,3}-?\d+[a-z]?)(?![\w-])/g;
  const PHRASE_RE = /\b(zrobione|w toku|ryzyko|PASS|FAIL|BLOCKED|restart sesji)\b/gi;
  // Feedback drafts (FB3): the text of a draft and of the tracker quote is data of unknown origin. It is only ever put in as text, and here the bidi
  // overrides and the control characters are removed once more (the server cleans them too). The line breaks and tabs of the body survive.
  const FB_STRIP = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\p{Cf}]/gu;
  const fbBody = (v) => (typeof v === "string" || typeof v === "number" ? String(v).replace(FB_STRIP, "") : "");
  const fbLine = (v) => fbBody(v).replace(/[\t\r\n\u2028\u2029]+/g, " ").trim();
  const FB_STATE_KEY = { waiting: "ui.feedback.state.waiting", after: "ui.feedback.state.after", stale: "ui.feedback.state.stale", blocked: "ui.feedback.state.blocked", sent: "ui.feedback.state.sent" };
  const FB_VISIBLE = 10;
  const FORBIDDEN_ATTR = /^(?:on|style$|srcdoc$)/i;

  // A window of a decision (consent, password, feedback, an action on a prototype, a change in the History) sits in the top layer of the browser,
  // a `<dialog>` opened with `showModal()`: nothing of the page, whatever its style says, can lie over its buttons. A host or a DOM without
  // `showModal` keeps the window as it was. `createDialog(doc)` tells which of the two this document is.
  const topLayerIn = (doc) => typeof doc.createElement("dialog").showModal === "function";
  // Opens a window that is in the document already. Escape (`cancel`) and a close forced by the browser go to `onCancel`, which may refuse (a
  // write in flight): the window then stays open.
  function showTop(node, onCancel) {
    const show = () => { try { node.showModal(); } catch { node.setAttribute("open", ""); } };
    node.addEventListener("cancel", (ev) => { ev.preventDefault(); onCancel(); });
    node.addEventListener("close", () => {
      if (!node.parentNode || node.open) return;
      onCancel();
      if (node.parentNode) show();
    });
    show();
  }

  // ------------------------------------------------------------------ teksty interfejsu (katalog `ui.*`)
  // Wbudowany polski katalog: brak pola `texts` w statusie (stary serwer, język pl) = dokładnie te teksty. Źródłem prawdy jest
  // skillsync_workflow/data/i18n/pl.json (klucze `ui.*`); test porównuje oba. Blok niżej generuje scripts/sync_renderer_defaults.py (`--check` sprawdza zgodność), nie edytuj go ręcznie.
  // Wartość to napis albo obiekt form liczby mnogiej {one, few, many, other}; parametry to `{nazwa}`, a fragment w odwrotnych apostrofach
  // renderer składa w węzeł `mono` (bez innerHTML).
  // <i18n-defaults>
  const DEFAULT_TEXTS = {
    "ui.act.openPlan": "Otwórz plan",
    "ui.act.openSpec": "Otwórz specyfikację",
    "ui.act.title": "Akt {roman}",
    "ui.agents.count": "w toku: {n}",
    "ui.agents.hideDone": "ukryj zakończone",
    "ui.agents.omitted": "Pominięto starsze: {n}",
    "ui.agents.over": "przekroczono ETA o {over}",
    "ui.agents.progressAria": "Upływ czasu biegu: {role}",
    "ui.agents.progressText": "{pct}% czasu do ETA",
    "ui.agents.recent": "Zakończone",
    "ui.agents.reportTip": "Otwórz raport {name}",
    "ui.agents.round": "runda {n}",
    "ui.agents.roundOn": "runda {round} trwa od {since}",
    "ui.agents.roundOnNoRound": "runda trwa od {since}",
    "ui.agents.roundStale": "runda nie zakończona",
    "ui.agents.shifted": "ETA przesunięte ×{n}",
    "ui.agents.showDone": "pokaż zakończone ({n})",
    "ui.agents.since": "od {since}",
    "ui.agents.stale": "nie zakończono",
    "ui.agents.step": "krok {step}",
    "ui.agents.stepRounds": { "few": "{n} rundy review kroku", "many": "{n} rund review kroku", "one": "{n} runda review kroku" },
    "ui.agents.title": "Biegi podumysłów",
    "ui.agents.took": "trwał {dur}",
    "ui.agents.until": "koniec ok. {until}",
    "ui.agents.untimed": "czas nieznany",
    "ui.agents.working": "Trwa: {role}, runda {round}, od {since}",
    "ui.agents.workingNoRound": "Trwa: {role}, od {since}",
    "ui.ago.day": "{n} d temu",
    "ui.ago.hour": "{n} h temu",
    "ui.ago.justNow": "przed chwilą",
    "ui.ago.min": "{n} min temu",
    "ui.ago.sec": "{n} s temu",
    "ui.ahead.label": { "few": "{n} niewypchnięte", "many": "{n} niewypchniętych", "one": "{n} niewypchnięty" },
    "ui.ahead.tip": { "few": "{n} commity lokalnie przed {remote}; „CP” je wypchnie", "many": "{n} commitów lokalnie przed {remote}; „CP” je wypchnie", "one": "{n} commit lokalnie przed {remote}; „CP” je wypchnie" },
    "ui.ahead.tipNoRemote": { "few": "{n} commity lokalnie przed {remote} (nie ma jej jeszcze na origin); „CP” je wypchnie", "many": "{n} commitów lokalnie przed {remote} (nie ma jej jeszcze na origin); „CP” je wypchnie", "one": "{n} commit lokalnie przed {remote} (nie ma jej jeszcze na origin); „CP” je wypchnie" },
    "ui.approve.confirm": "Na pewno? Kliknij, by dać „{word}”{to}",
    "ui.approve.hereOnlyTip": "Zgoda ze strony tylko lokalnie albo przez TLS, po zalogowaniu kontem approver; tu wpisz „{word}” w sesji",
    "ui.approve.label": "Zatwierdź „{word}”{to}",
    "ui.approve.saving": "Zapisuję…",
    "ui.approve.tip": "Zgoda jak wpisanie „{word}” w sesji — {what}",
    "ui.approve.versionOf": "{path} (wersja {sha})",
    "ui.bar.legendReported": "{n}% zgłoszone lub w toku",
    "ui.bar.legendVerified": "{pct}% zweryfikowane, ",
    "ui.bar.tipActive": "{pct}% zaznaczone w planie; {reported}% to kroki zgłoszone w checkpointach ({pending}% zrobione, czeka na weryfikację) lub w toku ({active}%)",
    "ui.bar.tipPending": "{pct}% zaznaczone w planie + {pending}% zrobione wg checkpointu (czeka na weryfikację)",
    "ui.bar.valueTextActive": "{pct}% zweryfikowane, {reported}% zgłoszone lub w toku",
    "ui.bar.valueTextPending": "{pct}%, plus {pending}% do weryfikacji",
    "ui.checkpoints.empty": "Brak checkpointów — pojawią się, gdy przebieg zapisze `checkpoints.md`.",
    "ui.checkpoints.skipLine": "{time} {segment}: „{fragment}” ({reason})",
    "ui.checkpoints.skipped": { "few": "{n} fragmenty raportu pominięto", "many": "{n} fragmentów raportu pominięto", "one": "{n} fragment raportu pominięto" },
    "ui.close": "Zamknij",
    "ui.comments.after": { "few": "{n} nowe komentarze po akceptacji{screens}", "many": "{n} nowych komentarzy po akceptacji{screens}", "one": "{n} nowy komentarz po akceptacji{screens}" },
    "ui.comments.screens": { "few": " (ekrany {ids})", "many": " (ekranów {ids})", "one": " (ekran {ids})" },
    "ui.commits.all": "Wszystkie: {n}.",
    "ui.commits.closeAria": "Zamknij listę commitów",
    "ui.commits.closeTip": "Zamknij (Esc)",
    "ui.commits.shown": { "few": "Pokazano {n} najnowsze; starszych: {more}.", "many": "Pokazano {n} najnowszych; starszych: {more}.", "one": "Pokazano {n} najnowszy; starszych: {more}." },
    "ui.commits.title": "Niewypchnięte commity ({n})",
    "ui.consent.branch": "Gałąź robocza",
    "ui.consent.cancel": "Anuluj",
    "ui.consent.confirm": "Zatwierdź",
    "ui.consent.doc": "Dokument",
    "ui.consent.err.consentRefused": "Serwer odrzucił żądanie. Odśwież stronę i spróbuj ponownie. Nic nie zapisano.",
    "ui.consent.err.deliveryChanged": "Gałąź, cel albo tryb dostarczania zmieniły się. Stan został odświeżony, zdecyduj ponownie. Nic nie zapisano.",
    "ui.consent.err.forbiddenRole": "Twoje konto nie ma prawa do tej zgody. Nic nie zapisano.",
    "ui.consent.err.loginRequired": "Zaloguj się, aby zatwierdzić. Nic nie zapisano.",
    "ui.consent.err.passwordChange": "Najpierw zmień hasło tego konta. Nic nie zapisano.",
    "ui.consent.err.sessionExpired": "Sesja wygasła, zaloguj się ponownie. Nic nie zapisano.",
    "ui.consent.err.tabChanged": "Worktree zakładki zmienił się lub został wykluczony. Stan został odświeżony. Nic nie zapisano.",
    "ui.consent.mode": "Tryb",
    "ui.consent.none": "brak",
    "ui.consent.note": "Zgoda dotyczy dokładnie tego sha. Jesteś zalogowany, więc hasło nie jest potrzebne.",
    "ui.consent.prBase": "Baza PR (cel)",
    "ui.consent.run": "przebieg {run}",
    "ui.consent.saving": "Zapisuję…",
    "ui.consent.sha": "Skrót sha",
    "ui.consent.stale": "Stan zmienił się od otwarcia okna i nic nie wysłano. Sprawdź i zdecyduj ponownie.",
    "ui.consent.tab": "Zakładka",
    "ui.consent.target": "Gałąź docelowa",
    "ui.consent.title": "Potwierdź zgodę: {word}",
    "ui.consent.word": "Słowo",
    "ui.delivery.invalid": "tryb dostarczania: błąd",
    "ui.delivery.label": "tryb: {mode}",
    "ui.delivery.labelBase": "tryb: {mode}, baza: {base}",
    "ui.delivery.noBase": "brak",
    "ui.delivery.srcDefault": "domyślny (brak delivery.mode w manifeście)",
    "ui.delivery.srcManifest": "z manifestu",
    "ui.delivery.tip": "Tryb dostarczania: {mode}; baza: {base}; źródło: {source}",
    "ui.delivery.tipInvalid": "Nieprawidłowy tryb dostarczania w manifeście (delivery.mode albo delivery.base): nie zakładaj »pr«, zapytaj użytkownika",
    "ui.delivery.word": "zgoda na oddanie: {word}",
    "ui.doc.loadError": "Nie udało się wczytać dokumentu: {error}. ",
    "ui.doc.refreshed": "odświeżono",
    "ui.doc.retry": "Spróbuj ponownie",
    "ui.doc.truncated": "obcięty do 2 MB",
    "ui.docTitle.actOne": "Akt I · ",
    "ui.docTitle.full": "{wait}{lead}{title} — status",
    "ui.docTitle.waiting": "⏳ Czeka · ",
    "ui.docs.changed": "zmieniony {time}",
    "ui.docs.changedTip": "{path} · zmieniony {time}",
    "ui.docs.empty": "Wybierz dokument z listy po lewej.",
    "ui.docs.emptyPreview": "Wybierz dokument z listy po lewej. Podgląd odświeża się sam, gdy plik się zmieni.",
    "ui.docs.group": "Dokumenty zadania",
    "ui.docs.openTip": "{path} w zakładce Dokumenty",
    "ui.dur.h": "{h} h",
    "ui.dur.hm": "{h} h {m} min",
    "ui.dur.min": "{n} min",
    "ui.eta.devTip": "szacunek {est}, było {act}, czekanie na użytkownika {wait}",
    "ui.eta.left": "zostało {t}",
    "ui.eta.over": "{op}: przekroczono ETA o {over}",
    "ui.eta.overBy": "przekroczono o {t}",
    "ui.eta.paused": "wstrzymane: czeka na {what}",
    "ui.eta.pausedUser": "wstrzymane: czeka na użytkownika",
    "ui.eta.shifted": "przesunięte o {t} (czekanie na użytkownika)",
    "ui.eta.until": "{op}: koniec ok. {until}",
    "ui.expert.toAll": "do wszystkich ekspertów",
    "ui.expert.toList": "do: {list}",
    "ui.expert.toast": "Pytania do eksperta: {open} otwartych ({blocking} blokujących)",
    "ui.feed.title": "Ostatnie checkpointy",
    "ui.feedback.ack": "Przeczytałem treść i potwierdzam, że nie zawiera nazw klientów",
    "ui.feedback.approve": "Daj zgodę na wysyłkę",
    "ui.feedback.approveAria": "Daj zgodę na wysyłkę szkicu {draft}",
    "ui.feedback.bodyLabel": "Treść zgłoszenia {draft}: dokładnie to, co poleci do trackera",
    "ui.feedback.cancel": "Anuluj",
    "ui.feedback.confirm": "Zgoda",
    "ui.feedback.count": { "few": "{n} szkice", "many": "{n} szkiców", "one": "{n} szkic" },
    "ui.feedback.dDraft": "Szkic",
    "ui.feedback.dMode": "Tryb",
    "ui.feedback.dSha": "Skrót treści (sha12)",
    "ui.feedback.dTarget": "Cel",
    "ui.feedback.dialogTitle": "Potwierdź zgodę na wysyłkę {draft}",
    "ui.feedback.genericWarning": "Uwaga: nazwy klientów nie są sprawdzane (skan ogólny). Przeczytaj treść uważnie przed zgodą.",
    "ui.feedback.given": "Zgoda dana {time} (źródło: {source})",
    "ui.feedback.modeComment": "komentarz do #{item}",
    "ui.feedback.modeCreate": "nowe zgłoszenie",
    "ui.feedback.noView": "Podgląd bez treści: uruchom `skillsync feedback draft {draft}`.",
    "ui.feedback.older": "Starsze szkice ({n})",
    "ui.feedback.quote": "CYTAT Z TRACKERA (nie od nas): {title}",
    "ui.feedback.reason": "Powód: {reason}",
    "ui.feedback.refused": "Zgoda nie została zapisana: {text}",
    "ui.feedback.saved": "Zgoda zapisana dla {draft} ({sha}). Szkic wysyła dopiero sesja główna.",
    "ui.feedback.saving": "Zapisuję…",
    "ui.feedback.scan": "Skan wycieków: {status}, pokrycie: {coverage}.",
    "ui.feedback.scanWarnings": "Uwagi skanu: {warnings}.",
    "ui.feedback.sendCmd": "Wysyłka w sesji głównej: `skillsync feedback send {draft}`",
    "ui.feedback.stale": "Podgląd nieaktualny: uruchom `skillsync feedback draft {draft}`, żeby odświeżyć szkic.",
    "ui.feedback.state.after": "Zgoda dana",
    "ui.feedback.state.blocked": "Zablokowany",
    "ui.feedback.state.sent": "Wysłany",
    "ui.feedback.state.stale": "Podgląd nieaktualny",
    "ui.feedback.state.waiting": "Czeka na zgodę",
    "ui.feedback.tagsLabel": "Tagi",
    "ui.feedback.target": "Cel: {target}",
    "ui.feedback.title": "Zgłoszenia do warstw",
    "ui.feedback.warning": "Ostrzeżenie: {warning}",
    "ui.foot.project": "projekt: `{path}`",
    "ui.foot.readApprove": "odczyt + zgody",
    "ui.foot.readOnly": "tylko odczyt",
    "ui.foot.status": "{mode} · odświeżono {time}",
    "ui.forecast.basis": "zostało {left}, tempo z {gaps} między zakończonymi",
    "ui.forecast.end": "koniec ok. {from}–{to}",
    "ui.forecast.fewMoments": "zakończone kroki w zbyt mało różnych momentach ({have}, potrzeba co najmniej {need}): kroki zgłoszone jednym checkpointem to jedna obserwacja",
    "ui.forecast.gaps": { "few": "{n} odstępów", "many": "{n} odstępów", "one": "{n} odstępu" },
    "ui.forecast.label": "Prognoza:",
    "ui.forecast.noRunStart": "brak czasu bezwzględnego w checkpoints.md (RUN=)",
    "ui.forecast.notEnough": "za mało danych (zakończone kroki z czasem: {have}, potrzeba co najmniej {need})",
    "ui.forecast.paused": "wstrzymana: sesja czeka na człowieka, czekanie nie jest liczone",
    "ui.forecast.pausedTail": "; wstrzymane: sesja czeka na człowieka",
    "ui.forecast.perStep": "ok. {dur}",
    "ui.forecast.perStepLess": "mniej niż minuta",
    "ui.forecast.range": "orientacyjnie: do końca kroków ok. {range} pracy ({basis}), bez czekania na zgody; {end}. Rząd wielkości, nie obietnica.",
    "ui.forecast.samplesSteps": { "few": "{n} zakończonych kroków", "many": "{n} zakończonych kroków", "one": "{n} zakończonego kroku" },
    "ui.forecast.stepsLeft": { "few": "{n} kroki", "many": "{n} kroków", "one": "{n} krok" },
    "ui.forecast.tempo": "orientacyjnie, tempo: {unit} pracy na krok, z {samples}; zostało {left}{tail}",
    "ui.forecast.tipRange": "Prognoza, nie pomiar: pierwszy i trzeci kwartyl pracy na krok (z odstępów między zakończonymi krokami, bez czekania) × zostało kroków; godzina końca = chwila odświeżenia strony + ten zakres. Nie uwzględnia rozmiaru kroków ani czekania na ludzi. Orientacyjnie: powtórka na prawdziwych przebiegach pokazała, że to rząd wielkości, nie obietnica.",
    "ui.forecast.tipTempo": "Prognoza, nie pomiar: mediana pracy na krok z odstępów między zakończonymi krokami, bez czekania. Ten widok nie podaje sumy ani godziny końca. Nie uwzględnia rozmiaru kroków ani czekania na ludzi; orientacyjnie, nie obietnica.",
    "ui.git.headTip": "Gałąź i HEAD repozytorium",
    "ui.git.localOnlyTip": "Gałąź tylko lokalnie (brak na origin), bez linku",
    "ui.git.openBranch": "Otwórz gałąź w {host}",
    "ui.glossary.actText": "Jeden z trzech wielkich kroków przebiegu: spec, plan, realizacja.",
    "ui.glossary.actTitle": "Akt",
    "ui.glossary.consentText": "Decyzja człowieka: GO plan, GO implement, CP (commit i push), READY (draft PR gotowy do przeglądu).",
    "ui.glossary.consentTitle": "Zgoda GO / CP / READY",
    "ui.glossary.debtText": "Świadomie odłożona sprawa zapisana w planie albo retrospektywie.",
    "ui.glossary.debtTitle": "Dług",
    "ui.glossary.etaText": "Szacowany koniec bieżącej operacji podany przez agenta w raporcie postępu.",
    "ui.glossary.etaTitle": "ETA",
    "ui.glossary.phaseText": "Grupa kroków w planie; zrobiona, gdy zrobione są wszystkie jej kroki.",
    "ui.glossary.phaseTitle": "Faza",
    "ui.glossary.resultText": "Sekcja „Wynik” planu: co faktycznie zrobiono, z kontrolami i odstępstwami.",
    "ui.glossary.resultTitle": "Wynik",
    "ui.glossary.stageText": "Kafelek w akcie; pulsujący wskaźnik oznacza etap, który trwa teraz.",
    "ui.glossary.stageTitle": "Etap",
    "ui.glossary.stepText": "Jedna pozycja listy Progress w planie z identyfikatorem, np. F1.2.",
    "ui.glossary.stepTitle": "Krok",
    "ui.glossary.title": "Pojęcia",
    "ui.glossary.verifyText": "Kroki zgłoszone jako zrobione, ale jeszcze niezaznaczone w planie; jaśniejszy odcień paska.",
    "ui.glossary.verifyTitle": "Do weryfikacji",
    "ui.history.abandon": "Porzuć",
    "ui.history.abandoned": "porzucony",
    "ui.history.abandonedTip": "Plan zatwierdzony bez postępu: żaden krok nie jest odhaczony, nie ma lokalnego przebiegu i plik nie zmienił się od ponad 14 dni. Można go porzucić (status „archived”).",
    "ui.history.archive": "Archiwizuj",
    "ui.history.auditFailedBody": "Sprawdź katalog `.skillsync/audit` zakładki {tab}: wiersz audytu `done` nie powstał.",
    "ui.history.auditFailedTitle": "Status planu {title} zmieniony, ale audyt nie został zapisany.",
    "ui.history.cancel": "Anuluj",
    "ui.history.confirmAbandonBody": "Plan „{title}” (zakładka {tab}) zmieni status z `approved` na `archived` i dostanie linię „Porzucony” z dzisiejszą datą (UTC); „Przywróć” cofa obie zmiany. W audycie zostanie wiersz z czasem, zakładką i sha pliku `{sha}`.",
    "ui.history.confirmAbandonTitle": "Porzucić plan?",
    "ui.history.confirmArchiveBody": "Plan „{title}” (zakładka {tab}) zmieni status z `done` na `archived`. Zmienia się tylko linia „Status”; w audycie zostanie wiersz z czasem, zakładką i sha pliku `{sha}`.",
    "ui.history.confirmArchiveTitle": "Zarchiwizować plan?",
    "ui.history.confirmCopies": "Kopie tego planu w innych zakładkach ({tabs}) nie zmienią się.",
    "ui.history.confirmRestoreApprovedBody": "Porzucony plan „{title}” (zakładka {tab}) zmieni status z `archived` na `approved` i straci linię „Porzucony”. W audycie zostanie wiersz z czasem, zakładką i sha pliku `{sha}`.",
    "ui.history.confirmUnarchiveBody": "Plan „{title}” (zakładka {tab}) zmieni status z `archived` na `done`. Zmienia się tylko linia „Status”; w audycie zostanie wiersz z czasem, zakładką i sha pliku `{sha}`.",
    "ui.history.confirmUnarchiveTitle": "Przywrócić plan?",
    "ui.history.confirmWarn": "Uwaga: zmiana linii „Status” w pliku śledzonym zabrudzi `git status` tego worktree i może dać konflikt przy scaleniu; audyt leży w katalogu zakładki i zniknie wraz z usuniętym worktree.",
    "ui.history.copies": "w {n} zakładkach",
    "ui.history.copiesTip": "Ten sam plik (ten sam sha) jest w zakładkach: {tabs}. Zmiana statusu dotyczy tylko kopii w zakładce {owner}.",
    "ui.history.dismiss": "Zamknij",
    "ui.history.doneTitle": "Plan {title}: status zmieniony na {to}.",
    "ui.history.empty": "Brak zakończonych planów.",
    "ui.history.err.approvalsOff": "Serwer działa bez zgód (`--no-approvals`): status planu nie może być zmieniony.",
    "ui.history.err.auditRefused": "Nie udało się zapisać audytu, więc status planu nie został zmieniony.",
    "ui.history.err.auditUnavailable": "Nie można zapisać audytu, więc nic nie zmieniono.",
    "ui.history.err.badRequest": "Serwer odrzucił żądanie jako niepoprawne.",
    "ui.history.err.consentContext": "Zmiany są możliwe tylko lokalnie albo przez HTTPS. Nic nie zapisano.",
    "ui.history.err.consentRefused": "Serwer odrzucił żądanie. Odśwież stronę i spróbuj ponownie. Nic nie zapisano.",
    "ui.history.err.forbiddenRole": "Zmiana statusu wymaga roli approver. Nic nie zapisano.",
    "ui.history.err.locked": "Plan jest właśnie zmieniany przez inny zapis. Spróbuj za chwilę.",
    "ui.history.err.loginRequired": "Zaloguj się, aby zmienić status. Nic nie zapisano.",
    "ui.history.err.network": "Brak połączenia z serwerem. Nie wiadomo, czy zmiana została zapisana; odśwież listę.",
    "ui.history.err.notAbandoned": "Ten plan nie spełnia już definicji porzuconego. Lista jest czytana ponownie.",
    "ui.history.err.other": "Serwer odmówił zmiany statusu (HTTP {status}).",
    "ui.history.err.passwordChange": "Najpierw zmień hasło tego konta. Nic nie zapisano.",
    "ui.history.err.planGone": "Plan nie istnieje już w tej zakładce. Lista jest czytana ponownie.",
    "ui.history.err.serverError": "Błąd serwera. Nic nie zapisano.",
    "ui.history.err.sessionExpired": "Sesja wygasła, zaloguj się ponownie. Nic nie zapisano.",
    "ui.history.err.shaMismatch": "Plan zmienił się od chwili, gdy go widziałeś. Nic nie zapisano; lista jest czytana ponownie.",
    "ui.history.err.statusNotAllowed": "Ta zmiana nie jest możliwa dla obecnego statusu planu. Lista jest czytana ponownie.",
    "ui.history.err.tabChanged": "Worktree zakładki zmienił się lub został wykluczony. Nic nie zapisano.",
    "ui.history.err.tabGone": "Worktree tej zakładki został usunięty. Nic nie zapisano.",
    "ui.history.failedTitle": "Nie zmieniono statusu planu {title}.",
    "ui.history.groupEmpty": "brak zakończonych planów",
    "ui.history.heading": "Zakończone plany wszystkich zakładek",
    "ui.history.listAria": "Zakończone plany, według zakładek",
    "ui.history.loadError": "Nie udało się wczytać historii: {error}",
    "ui.history.loading": "Wczytuję historię…",
    "ui.history.mainDir": "katalog główny",
    "ui.history.moreRows": "Pokaż więcej ({n})",
    "ui.history.needLogin": "Zmiana statusu wymaga zalogowania na konto z rolą approver.",
    "ui.history.needRole": "Zmiana statusu wymaga roli approver.",
    "ui.history.networkTitle": "Nie potwierdzono zmiany statusu planu {title}.",
    "ui.history.noAction": "Dla tego planu nie ma zmiany statusu.",
    "ui.history.previewAria": "Podgląd planu",
    "ui.history.previewEmpty": "Wybierz plan z listy, aby zobaczyć podgląd.",
    "ui.history.previewError": "Nie udało się wczytać planu: {error}",
    "ui.history.previewLoading": "Wczytuję plan…",
    "ui.history.previewMeta": "{tab} · {status} · tylko do odczytu",
    "ui.history.previewTruncated": "Podgląd jest ucięty",
    "ui.history.refresh": "Odśwież",
    "ui.history.retry": "Spróbuj ponownie",
    "ui.history.saving": "Zapisuję…",
    "ui.history.shown": "Pokazano {shown} z {count}.",
    "ui.history.staleError": "Nie udało się odświeżyć listy: {error}",
    "ui.history.status.approved": "zatwierdzony",
    "ui.history.status.archived": "zarchiwizowany",
    "ui.history.status.done": "zakończony",
    "ui.history.truncated": "Lista jest ucięta limitami serwera (200 planów na zakładkę, 1000 wierszy), starsze plany nie są tu widoczne.",
    "ui.history.unarchive": "Przywróć",
    "ui.history.viewerOnly": "Zmiana statusu jest możliwa tylko na stronie zgód; ta strona jest tylko do odczytu.",
    "ui.httpError": "Błąd {status}",
    "ui.live.connecting": "Łączę…",
    "ui.live.down": "Brak połączenia",
    "ui.live.live": "Na żywo",
    "ui.live.polling": "Odświeżam co 5 s",
    "ui.liveTip.connecting": "Łączę z serwerem statusu…",
    "ui.liveTip.down": "Serwer statusu nie odpowiada. Strona ponawia próby automatycznie.",
    "ui.liveTip.live": "Zmiany w planie, specu i checkpointach pojawiają się od razu (SSE).",
    "ui.liveTip.polling": "Kanał na żywo niedostępny — strona pyta serwer co 5 s i sama wróci do trybu na żywo.",
    "ui.measure.label": "Pomiar:",
    "ui.measure.since": "trwa od {from}",
    "ui.measure.tip": "Zmierzone z checkpointów i pliku zgód: od {from}, minus okna czekania. To pomiar, nie prognoza.",
    "ui.measure.tipRange": "Zmierzone z checkpointów i pliku zgód: od {from} do {to}, minus okna czekania. To pomiar, nie prognoza.",
    "ui.measure.unknownInconsistent": "czas nieznany: niespójne znaczniki",
    "ui.measure.unknownNoApproval": "czas nieznany: zgody nie ma w pliku zgód",
    "ui.measure.unknownOpenWindow": "czas nieznany: okno czekania bez końca",
    "ui.measure.workWait": "praca {work}, czekanie {wait}",
    "ui.meta.lastChangeTip": "Ostatnia zmiana planu, speca lub checkpointów",
    "ui.meta.planStatusTip": "Status planu (nagłówek Status:)",
    "ui.meta.runIdTip": "Identyfikator przebiegu (.skillsync/runs/{id})",
    "ui.meta.runStartTip": "Czas od startu przebiegu (RUN= w checkpoints.md, start {time})",
    "ui.meta.specNoPlanTip": "Akt I: spec bez planu (plan powstaje przy „GO plan”); status speca",
    "ui.noConnection": "Brak połączenia z serwerem statusu.",
    "ui.noData": "brak danych",
    "ui.page.title": "Status przebiegu",
    "ui.parseReason.laterId": "id kroku nie na początku elementu",
    "ui.parseReason.noStepId": "bez id kroku na początku",
    "ui.parseReason.unbalancedBracket": "niezamknięty nawias, lista dzielona po każdym „;”",
    "ui.phase.noPlan": "Brak planu ze statusem draft, approved albo in_progress w `docs/plans`. Plan pojawi się tu sam po zapisaniu pliku.",
    "ui.phase.noProgress": "Plan nie ma jeszcze sekcji `## Progress` z krokami `- [ ] S1 Tytuł`.",
    "ui.phase.runNoSpec": "Akt I: przebieg `{id}` ruszył, specyfikacja jest w przygotowaniu. Spec pojawi się tu sam po zapisaniu w `docs/specs`, a kroki po „GO plan”.",
    "ui.phase.specNoPlan": "Akt I: spec `{path}` ({status}) nie ma jeszcze planu. Kroki pojawią się tu same, gdy po „GO plan” powstanie plan w `docs/plans`.",
    "ui.phase.substeps": "{done}/{total} podkroków",
    "ui.plan.autoAria": "Plan wybrany automatycznie; inny plan wybierzesz z listy",
    "ui.plan.autoBadge": "wybrany automatycznie",
    "ui.plan.autoRow": "Automatycznie: bieżący plan",
    "ui.plan.btn": "Plan",
    "ui.plan.pathAuto": "Plan: {path} (wybrany automatycznie; inny wybierzesz z listy)",
    "ui.plan.pathManual": "Plan: {path} (wybrany ręcznie)",
    "ui.plan.pickTip": "Wybierz plan (domyślnie: bieżący w realizacji)",
    "ui.plan.runAct": "przebieg (akt I)",
    "ui.plan.specAct": "{name} (akt I)",
    "ui.pr.tip": "Pull request z linii PR: planu",
    "ui.progress.aria": "Postęp realizacji",
    "ui.progress.currentTip": "Teraz wykonywane: {ids}, {index}. pozycja z {total} w planie ({source})",
    "ui.progress.doneTip": "Kroki odhaczone [x] w planie",
    "ui.progress.firstUnchecked": "pierwszy niezaznaczony krok",
    "ui.progress.fromCheckpoint": "z ostatniego checkpointu „w toku”",
    "ui.progress.now": "teraz:",
    "ui.progress.of": "{done} z {total} kroków zrobionych",
    "ui.progress.parallel": "+{n} równolegle",
    "ui.progress.pendingLabel": "(+{n}% do weryfikacji)",
    "ui.progress.pendingTip": "Kroki zgłoszone jako zrobione w checkpointach ({ids}), jeszcze niezaznaczone [x] w planie — czekają na review",
    "ui.proto.acceptProject": "Akceptacja całego projektu",
    "ui.proto.acceptedBy": "akceptacja: {who}",
    "ui.proto.act.accept": "Akceptuj ekran",
    "ui.proto.act.delete": "Usuń wątek",
    "ui.proto.act.project": "Akceptuj cały projekt",
    "ui.proto.act.resolve": "Rozstrzygnij wątek",
    "ui.proto.act.restore": "Przywróć iterację",
    "ui.proto.act.unaccept": "Cofnij akceptację",
    "ui.proto.anonymous": "Czytasz bez logowania. Aby akceptować i komentować, zaloguj się na tej stronie.",
    "ui.proto.back": "Wróć do listy",
    "ui.proto.consentsElsewhere": "Przyciski zgód są ukryte, dopóki na ekranie jest ramka prototypu: zgody wykonasz na innej zakładce.",
    "ui.proto.done": "Zapisano: {action}",
    "ui.proto.embed": "Pokaż w zakładce",
    "ui.proto.err.cidConflict": "Ta operacja koliduje z inną. Nic nie zapisano, spróbuj ponownie.",
    "ui.proto.err.forbiddenRole": "Ta sesja nie ma roli do tej operacji. Nic nie zapisano.",
    "ui.proto.err.otherTab": "Origin prototypów pokazuje inny katalog niż ta zakładka. Nic nie zapisano.",
    "ui.proto.err.stale": "Prototyp zmienił się od wyświetlenia. Widok został odświeżony, zdecyduj ponownie. Nic nie zapisano.",
    "ui.proto.format1": "Prototyp w starym formacie: najpierw `skillsync prototype migrate`.",
    "ui.proto.frameClose": "Zamknij ramkę",
    "ui.proto.frameTitle": "Prototyp {slug}",
    "ui.proto.from": "Z iteracji",
    "ui.proto.gate.fail": "bramka planu: brak akceptacji",
    "ui.proto.gate.pass": "bramka planu: PASS",
    "ui.proto.gate.writing": "bramka planu: czeka na koniec zapisu",
    "ui.proto.handoff": "Zaloguj w prototypach",
    "ui.proto.handoffTip": "Przekazuje sesję jednorazowym kodem na origin prototypów (otwiera galerię). Ta sesja pozwala komentować, nie zatwierdzać ani akceptować.",
    "ui.proto.hash": "hash {hash}",
    "ui.proto.loading": "Wczytywanie…",
    "ui.proto.manage": "Akceptacje i wątki",
    "ui.proto.noOrigin": "Adres originu prototypów nie jest znany dla tego żądania (na przykład za proxy). Uruchom serwer z `--prototype-url`.",
    "ui.proto.noScreens": "Brak ekranów",
    "ui.proto.noSourceWrites": "Przywracanie wymaga kopii roboczej git.",
    "ui.proto.noThreads": "Brak wątków",
    "ui.proto.note": "Zapis dotyczy dokładnie tej wersji, którą widzisz. Jeśli prototyp się zmienił, nic nie zostanie zapisane.",
    "ui.proto.off": "Serwer prototypów (port {port}) nie działa.",
    "ui.proto.open": "Otwórz prototyp {slug}",
    "ui.proto.otherTab": "Origin prototypów pokazuje prototypy innego katalogu niż ta zakładka: otwarcie i akceptacje są tu niedostępne.",
    "ui.proto.problems": "Ekrany niejednoznaczne (nie można ich zaakceptować): {list}",
    "ui.proto.projectTitle": "Wszystkie prototypy",
    "ui.proto.reason": "Powód",
    "ui.proto.reasonRequired": "Podaj powód przywrócenia.",
    "ui.proto.refused": "Nie zapisano: {action}",
    "ui.proto.reload": "Odśwież widok",
    "ui.proto.row.hash": "Hash ekranu",
    "ui.proto.row.prototype": "Prototyp",
    "ui.proto.row.prototypes": "Wersje",
    "ui.proto.row.screen": "Ekran",
    "ui.proto.row.thread": "Wątek",
    "ui.proto.row.version": "Wersja",
    "ui.proto.screen.accepted": "zaakceptowany",
    "ui.proto.screen.pending": "czeka na akceptację",
    "ui.proto.screen.stale": "nieaktualny",
    "ui.proto.screens": "Ekrany",
    "ui.proto.screensCount": { "few": "{n} ekrany", "many": "{n} ekranów", "one": "{n} ekran" },
    "ui.proto.single": "Tryb --single-origin: prototypy działają na originie strony; zgody i akceptacje ze strony oraz ramka są wyłączone.",
    "ui.proto.threads": "Wątki",
    "ui.proto.version": "wersja {version}",
    "ui.proto.writingBlocks": "Agent pisze ekrany: akceptacja i przywracanie są zablokowane do końca zapisu.",
    "ui.q.accepted": "przyjęte: {value}",
    "ui.q.assumption": "założenie",
    "ui.q.blocking": "blokujące",
    "ui.q.commentsAfter": { "few": "{n} komentarze po akceptacji", "many": "{n} komentarzy po akceptacji", "one": "{n} komentarz po akceptacji" },
    "ui.q.countTip": "{realize} do realizacji, {expert} do eksperta",
    "ui.q.groupExpert": "Pytania do eksperta domenowego",
    "ui.q.groupRealize": "Pytania do realizacji",
    "ui.q.itemsTitle": "Pytania do eksperta",
    "ui.q.iteration": "iteracja {n}",
    "ui.q.more": "+{n} więcej",
    "ui.q.noQuestions": "Brak pytań",
    "ui.q.noRealize": "Brak pytań do realizacji",
    "ui.q.open": "Otwórz pytania",
    "ui.q.openAria": "Otwórz pytania przebiegu {slug}",
    "ui.q.openProto": "Otwórz prototyp",
    "ui.q.openThreads": { "few": "{n} wątki otwarte", "many": "{n} wątków otwartych", "one": "{n} wątek otwarty" },
    "ui.q.screens": { "few": "{accepted} z {n} ekranów zaakceptowanych", "many": "{accepted} z {n} ekranów zaakceptowanych", "one": "{accepted} z {n} ekranu zaakceptowanych" },
    "ui.q.sessionWaits": "sesja czeka",
    "ui.q.stale": "nieaktualne {n}",
    "ui.q.textMissing": "treść niedostępna",
    "ui.q.withExpert": "u eksperta",
    "ui.q.writing": "agent pisze…",
    "ui.q.writingDone": { "few": "{done} ekrany", "many": "{done} ekranów", "one": "{done} ekran" },
    "ui.q.writingOfPlan": { "few": "{done} z {plan} ekrany", "many": "{done} z {plan} ekranów", "one": "{done} z {plan} ekran" },
    "ui.qState.answered": "odpowiedziane",
    "ui.qState.open": "otwarte",
    "ui.qState.withdrawn": "wycofane",
    "ui.readiness.label": "Readiness: {status}",
    "ui.readiness.noFile": "brak pliku readiness.md w przebiegu",
    "ui.readiness.otherVersion": "brief dla innej wersji planu",
    "ui.readiness.planSha": "plan {sha}",
    "ui.result.more": { "few": "Pokaż resztę ({n} linie)", "many": "Pokaż resztę ({n} linii)", "one": "Pokaż resztę ({n} linia)" },
    "ui.result.moreNoCount": "Pokaż resztę",
    "ui.result.path": "Plan: `{path}`",
    "ui.result.title": "Wynik",
    "ui.screens.count": { "few": "{n} ekrany", "many": "{n} ekranów", "one": "{n} ekran" },
    "ui.screens.ofPlanned": "{done} z {planned} ekranów",
    "ui.screens.protoTip": "Ekrany prototypu: {label}",
    "ui.session.anonymous": "odczyt bez logowania",
    "ui.session.approverAlert": "Nieudane logowania na konto z rolą approver „{account}”: {failures} w ciągu 15 minut (ostatnia seria: {time}). Jeśli to nie Ty, zmień hasło tego konta i sprawdź `run/audit.jsonl`.",
    "ui.session.changePassword": "Zmień hasło",
    "ui.session.expired": "Sesja wygasła, zaloguj się ponownie. Zgodę trzeba potem ponowić.",
    "ui.session.legacy": "Adres z tokenem zgód już nie działa i został usunięty z paska adresu. Aby zatwierdzać, zaloguj się na tej stronie.",
    "ui.session.login": "Zaloguj",
    "ui.session.logout": "Wyloguj",
    "ui.session.logoutFailed": "Nie udało się wylogować. Spróbuj ponownie.",
    "ui.session.mustChange": "Najpierw zmień hasło tego konta.",
    "ui.session.needLogin": "Zaloguj się, aby zatwierdzać.",
    "ui.session.needRole": "Zgody wymagają roli approver.",
    "ui.session.needRoleBody": "Zgody wymagają roli approver. Twoje role: {roles}.",
    "ui.session.needsTls": "Logowanie wymaga HTTPS albo dostępu lokalnego.",
    "ui.session.noRoles": "brak",
    "ui.session.passwordSignIn": "Ta sesja nie pochodzi z logowania hasłem i nie zatwierdza. Aby zatwierdzać, zaloguj się hasłem na tej stronie.",
    "ui.session.pwChanged": "Hasło zmienione. Inne sesje tego konta zostały zakończone.",
    "ui.session.pwCurrent": "Obecne hasło",
    "ui.session.pwFailed": "Nie udało się zmienić hasła.",
    "ui.session.pwInvalidCurrent": "Obecne hasło jest błędne.",
    "ui.session.pwLimited": "Za dużo prób. Spróbuj ponownie za chwilę.",
    "ui.session.pwMismatch": "Powtórzone hasło jest inne.",
    "ui.session.pwNew": "Nowe hasło (co najmniej {min} znaków)",
    "ui.session.pwRepeat": "Powtórz nowe hasło",
    "ui.session.pwSame": "Nowe hasło musi różnić się od obecnego.",
    "ui.session.pwSave": "Zapisz hasło",
    "ui.session.pwShort": "Nowe hasło jest za krótkie (co najmniej {min} znaków).",
    "ui.session.pwTitle": "Zmiana hasła",
    "ui.session.singleOrigin": "Tryb --single-origin: zgody ze strony są wyłączone, bo skrypt prototypu działa na tym samym originie i mógłby odczytać hasło. Zgodę daje CLI albo słowo wpisane w sesji agenta; pełną ochronę daje serwer bez `--single-origin`.",
    "ui.silence.action": "Raport postępu powinien być po każdym kroku albo co 30 minut. Sesja mogła się zawiesić albo długo pracuje bez raportu: zajrzyj do sesji.",
    "ui.silence.short": "od {n} min",
    "ui.silence.title": "Brak raportu postępu: {what}",
    "ui.silence.what": "brak raportu od {n} min",
    "ui.skipReason.notStepDir": "katalog spoza kroków",
    "ui.skipReason.unreadable": "nieczytelny",
    "ui.stage.activeSince": "trwa od {since}",
    "ui.stage.inProgressSinceSr": "(w trakcie, od {since})",
    "ui.stage.inProgressSr": "(w trakcie)",
    "ui.stage.quietSr": "(brak aktywności od {n} min)",
    "ui.stage.quietTip": "brak aktywności od {n} min (ostatni raport), etap oznaczony jako trwający",
    "ui.stage.quietTipSince": "brak aktywności od {n} min (ostatni raport), etap oznaczony jako trwający od {since}",
    "ui.state.active": "w trakcie",
    "ui.state.done": "zrealizowany",
    "ui.state.planned": "zaplanowany",
    "ui.status.approved": "zatwierdzony",
    "ui.status.done": "zakończony",
    "ui.status.draft": "szkic",
    "ui.status.implemented": "wdrożony",
    "ui.status.inProgress": "w realizacji",
    "ui.step.active": "w toku",
    "ui.step.commit": "commit {sha}",
    "ui.step.done": "zrobiony",
    "ui.step.planned": "zaplanowany",
    "ui.step.reported": "zrobiony wg checkpointu (niezaznaczony w planie)",
    "ui.substeps.title": "Podkroki i czas ({n})",
    "ui.tab.docs": "Dokumenty",
    "ui.tab.docsTip": "Spec, plan i pliki przebiegu (klawisz D)",
    "ui.tab.progress": "Postęp",
    "ui.tab.progressTip": "Akty, postęp i checkpointy (klawisz P)",
    "ui.tab.progressTipPlain": "Akty, postęp i checkpointy",
    "ui.tab.prototypes": "Prototypy",
    "ui.tab.prototypesTip": "Prototypy projektu: akceptacje, wątki, bramka planu (klawisz P)",
    "ui.tab.questions": "Pytania",
    "ui.tab.questionsTip": "Pytania do realizacji i do eksperta",
    "ui.tab.tests": "Testy",
    "ui.tab.testsTip": "Wyniki testów kroków planu",
    "ui.tabs.aria": "Widok",
    "ui.testState.failed": "czerwony",
    "ui.testState.passed": "zielony",
    "ui.testState.running": "uruchomiony",
    "ui.testState.skipped": "pominięty",
    "ui.testState.waiting": "oczekuje",
    "ui.tests.atLeast": "co najmniej {n}",
    "ui.tests.attemptBtn": "bieg {n}: {mark} {red}",
    "ui.tests.attemptRed": "{n} czerwone",
    "ui.tests.attemptSub": "bieg {n} z {of} · {time}{dur}",
    "ui.tests.attempts": "Biegi kroku",
    "ui.tests.barPassed": { "few": "{passed} z {total} testów przeszło", "many": "{passed} z {total} testów przeszło", "one": "{passed} z {total} testu przeszło" },
    "ui.tests.barRunning": "{n} trwa",
    "ui.tests.cmdFailed": "kod {exit}, żaden test nie zgłosił porażki",
    "ui.tests.cmdFailedTip": "kod {exit}, żaden test nie zgłosił porażki (wyjątek poza raportowanymi testami); wyniki ok dotyczą tylko testów, które zdążyły się wykonać",
    "ui.tests.countGreen": { "few": "{n} zielone", "many": "{n} zielonych", "one": "{n} zielony" },
    "ui.tests.countRed": { "few": "{n} czerwone", "many": "{n} czerwonych", "one": "{n} czerwony" },
    "ui.tests.countRunning": { "few": "{n} uruchomione", "many": "{n} uruchomionych", "one": "{n} uruchomiony" },
    "ui.tests.countSkipped": { "few": "{n} pominięte", "many": "{n} pominiętych", "one": "{n} pominięty" },
    "ui.tests.countWaiting": "{n} oczekuje",
    "ui.tests.empty": "Brak odczytanych wyników.",
    "ui.tests.greenAria": "{n} z {total} testów zielonych",
    "ui.tests.groupError": "Nie udało się pobrać listy testów tej grupy.",
    "ui.tests.groupsNote": "Pokazano {shown} z {total} grup (najpierw nieudane i w toku); liczniki powyżej obejmują wszystkie testy.",
    "ui.tests.interrupted": "przerwany",
    "ui.tests.levelCommand": "polecenie",
    "ui.tests.levelCounts": "liczniki",
    "ui.tests.levelLabel": "poziom danych: {level}",
    "ui.tests.levelTests": "lista testów",
    "ui.tests.levelTip": "lista testów = nazwy i stany pojedynczych testów (z raportu JUnit/TRX albo ze strumienia); liczniki = same liczby (np. „Executed N of M”); polecenie = tylko stan polecenia, bez wyników testów; „bez stanu uruchomiony” = narzędzie wypisuje test dopiero po jego zakończeniu, więc do tego czasu test oczekuje",
    "ui.tests.liveCompletion": "(wyniki po zakończeniu każdego testu, bez stanu „uruchomiony”)",
    "ui.tests.moreItems": "i {n} więcej",
    "ui.tests.noStep": "Bez kroku",
    "ui.tests.publicRaw": "Czytasz bez logowania: wyniki testów są surowe, czyli dokładnie to, co wypisały testy (mogą w nich być ścieżki, adresy i sekrety). Maskuje je tylko ustawienie `tests.mask: on`.",
    "ui.tests.running": "trwa",
    "ui.tests.showAll": "Pokaż wszystkie ({n})",
    "ui.tests.showAria": "Pokaż testy kroku {id}",
    "ui.tests.showMore": "Pokaż kolejne ({n})",
    "ui.tests.showTip": "Pokaż testy kroku",
    "ui.tests.skippedNote": "Pominięto {count} {noun} wyników testów (nie dało się ich uwzględnić): {shown}{more}.",
    "ui.tests.skippedNoun": { "few": "pliki lub katalogi", "many": "plików lub katalogów", "one": "plik lub katalog" },
    "ui.tests.truncated": "lista obcięta (limit)",
    "ui.tests.unreadable": "Nie udało się odczytać wyników testów.",
    "ui.theme.aria": "Przełącz motyw jasny/ciemny",
    "ui.theme.tip": "Motyw jasny/ciemny (klawisz T)",
    "ui.timing.incomplete": "(niepełne)",
    "ui.timing.noWindows": "Brak okien czekania.",
    "ui.timing.part": "praca {work} · czekanie {wait}{plus}",
    "ui.timing.title": "Czas pracy i czekania",
    "ui.timing.toApproval": "do zgody",
    "ui.timing.toNextCheckpoint": "do kolejnego checkpointu",
    "ui.timing.total": "Razem",
    "ui.timing.totalUnknown": "Razem (okna nieznane: {n})",
    "ui.timing.windowUnknown": "(okno nieznane: brak zgody i kolejnego checkpointu)",
    "ui.title.loading": "Wczytuję status…",
    "ui.title.noPlan": "Brak planu w realizacji",
    "ui.title.specPreparing": "Akt I: specyfikacja w przygotowaniu",
    "ui.toTop.aria": "Wróć na górę strony",
    "ui.toTop.tip": "Na górę",
    "ui.toast.approved": "Zgoda „{word}” zapisana",
    "ui.toast.notSaved": "Nie zapisano „{word}”",
    "ui.viewer.readOnly": "Zgody daje prowadzący na komputerze z sesją.",
    "ui.wait.answer": "Odpowiedz na pytania",
    "ui.wait.givenAction": "Zgoda „{word}” jest już w pliku; napisz w sesji „dalej”, jeśli sesja jeszcze nie ruszyła.",
    "ui.wait.headWaiting": "{who}{since}: ",
    "ui.wait.headWorking": "Sesja pracuje{since}: ",
    "ui.wait.openDoc": "Otwórz {name}",
    "ui.wait.openSession": "Otwórz sesję",
    "ui.wait.openSessionTip": "Otwiera sesję (adres z --session-url)",
    "ui.wait.sessionWaiting": "Sesja czeka na Ciebie",
    "ui.wait.since": " od {since}",
    "ui.wait.source": "Źródło: {src}. {hint}",
    "ui.wait.vanishDefault": "Baner zniknie, gdy sesja ruszy.",
    "ui.wait.vanishSilence": "Zniknie po następnym raporcie.",
    "ui.wait.vanishWriting": "Zniknie, gdy agent skończy pisać albo znacznik wygaśnie.",
    "ui.wait.who": "Czeka na: {label}",
    "ui.wait.whoLogin": "Czeka na: {label} ({login})",
    "ui.waitSource.checkpoint": "znacznik „czeka:” w ostatnim checkpoincie",
    "ui.waitSource.expert": "otwarte pytania blokujące do eksperta",
    "ui.waitSource.plan": "status planu",
    "ui.waitSource.prototype": "akceptacja ekranów prototypu",
    "ui.waitSource.silence": "czas od ostatniej linii checkpoints.md (próg z serwera)",
    "ui.waitSource.spec": "status speca",
    "ui.waitSource.writing": "znacznik zapisu ekranów prototypu (.writing.json)",
    "ui.workdir.main": "główny checkout",
    "ui.workdir.mainPath": "Główny checkout: {path}",
    "ui.workdir.worktree": "worktree",
    "ui.workdir.worktreeNamed": "worktree: {name}",
    "ui.wt.aria": "Zakładki projektu",
    "ui.wt.consentScope": "Ta zgoda dotyczy tylko worktree {name} i sha {sha}. Inne zakładki jej nie widzą i nie zmieniają.",
    "ui.wt.goneBody": "Zgody są niedostępne (410), nic nie zostało zapisane. Przełącz się na inną zakładkę.",
    "ui.wt.goneSub": "usunięty",
    "ui.wt.goneTitle": "Worktree {name} został usunięty",
    "ui.wt.hiddenHint": "Te worktree nie mają zakładki: są nieaktywne albo poza limitem widocznych. Aby dodać zakładkę, uruchom `skillsync dashboard serve` w jego katalogu.",
    "ui.wt.hiddenName": "worktree",
    "ui.wt.hiddenTitle": "Nieaktywne i bez śladu pracy",
    "ui.wt.hiddenWhen": "ostatnia aktywność",
    "ui.wt.hiddenWhy": "powód ukrycia",
    "ui.wt.hideAll": "ukryj nieaktywne",
    "ui.wt.history": "Historia",
    "ui.wt.historyCount": "Zakończone plany: {n}",
    "ui.wt.lostBody": "Dotyczy wszystkich zakładek. Uruchom ponownie `skillsync dashboard serve` w dowolnym katalogu projektu, a strona połączy się sama.",
    "ui.wt.lostTitle": "Utracono połączenie z serwerem projektu",
    "ui.wt.never": "brak",
    "ui.wt.noteBody": "Zamknięcie tego terminala zamknie stronę wszystkim zakładkom.",
    "ui.wt.noteClose": "Rozumiem",
    "ui.wt.noteTitle": "Serwer projektu działa w terminalu, w którym go uruchomiono",
    "ui.wt.otherDone": "Zgoda {word} w zakładce {name}: zapisana.",
    "ui.wt.otherReason": "{reason}",
    "ui.wt.otherRefused": "Zgoda {word} w zakładce {name}: odrzucona.",
    "ui.wt.readOnly": "tylko odczyt",
    "ui.wt.readOnlyNote": "Zakładka {name} jest tylko do odczytu.",
    "ui.wt.readOnlyTip": "Ta zakładka jest tylko do odczytu.",
    "ui.wt.showAll": "pokaż wszystkie (+{n})",
    "ui.wt.unknownTab": "Nie ma zakładki {id}: pokazuję zakładkę domyślną.",
    "ui.wt.waitTip": "czeka na Ciebie",
    "ui.wt.why.inactive": "nieaktywny",
    "ui.wt.why.limit": "poza limitem widocznych zakładek",
    "ui.wt.why.other": "ukryty",
    "ui.wt.why.trace": "bez śladu skillsync",
  };
  // </i18n-defaults>
  const PARAM_RE = /\{(\w+)\}/g;
  const CODE_RE = /`([^`]*)`/g;
  // Podstawia `{nazwa}` z `params` jednokrotnie: wstawione wartości nie są ponownie czytane (parametr z `{x}` albo znacznikami zostaje tekstem).
  const fillParams = (template, params) => String(template).replace(PARAM_RE, (whole, name) => (params && Object.prototype.hasOwnProperty.call(params, name) ? String(params[name] ?? "") : whole));
  const ruleCache = {};
  // Kategoria liczby mnogiej: Intl.PluralRules(lang) (przeglądarki i Node), a bez niego własna reguła (pl: 1 / 2-4 poza 12-14 / reszta; inne: 1 / reszta).
  // Liczby niecałkowite i ujemne to `other`, jak w odpowiedniku Pythona (skillsync_workflow/i18n.py).
  function pluralCategory(lang, n) {
    if (!Number.isInteger(n) || n < 0) return "other";
    if (typeof Intl !== "undefined" && typeof Intl.PluralRules === "function") {
      try { return (ruleCache[lang] = ruleCache[lang] || new Intl.PluralRules(lang)).select(n); } catch (e) { /* nieznany tag języka: reguła własna */ }
    }
    if (lang === "pl") return n === 1 ? "one" : n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14) ? "few" : "many";
    return n === 1 ? "one" : "other";
  }
  // Forma z obiektu form liczby mnogiej: własna kategoria, potem `other`, potem `many` (jak pick_plural w Pythonie).
  function pickForm(lang, forms, n) {
    for (const key of [pluralCategory(lang, n), "other", "many"]) if (typeof forms[key] === "string") return forms[key];
    const first = Object.keys(forms).find((key) => typeof forms[key] === "string");
    return first ? forms[first] : "";
  }
  // Szablon z fragmentami kodu w odwrotnych apostrofach -> lista tekstów i węzłów `code(tekst)`; backtick jest rozpoznawany w szablonie, więc
  // parametr zawierający backtick nigdy nie tworzy fragmentu kodu.
  function richParts(template, params, code) {
    const out = [];
    let last = 0, m;
    CODE_RE.lastIndex = 0;
    while ((m = CODE_RE.exec(template))) {
      if (m.index > last) out.push(fillParams(template.slice(last, m.index), params));
      out.push(code(fillParams(m[1], params)));
      last = m.index + m[0].length;
    }
    if (last < template.length) out.push(fillParams(template.slice(last), params));
    return out;
  }
  // Locale do formatowania czasu: pl zostaje `pl-PL` (jak dotąd), en to `en-GB` (24 godziny), inny język własny tag.
  const localeOf = (lang) => (lang === "pl" ? "pl-PL" : lang === "en" ? "en-GB" : String(lang || "pl"));
  const stepLabel = (tr, state) => (Object.prototype.hasOwnProperty.call(STEP_KEY, state) ? tr(STEP_KEY[state]) : undefined);

  // Sprite ikon (symbole <symbol id="i-…">): [viewBox, [[tag, atrybuty], …]]. Renderer tworzy go sam przy create().
  const SPRITE = {
    "i-folder": ["0 0 24 24", [["path", { "d": "M3 6.5A1.5 1.5 0 0 1 4.5 5H10l2 2.5h7.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z", "fill": "none", "stroke": "currentColor", "stroke-width": "1.8", "stroke-linejoin": "round" }]]],
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
  const fmtTime = (iso, locale) => iso ? new Date(iso).toLocaleString(locale, { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" }) : "—";
  // Czas trwania z sekund do czytelnego tekstu („1 h 05 min”, „40 min”; jednostki z katalogu przez `tr`); `rough`: zaokrąglenie do 5 min od 10 min (przedziały prognozy nie udają dokładności).
  const humanDur = (sec, rough, tr) => {
    let m = Math.round(Number(sec) / 60);
    if (!Number.isFinite(m) || m < 0) return "—";
    if (rough && m >= 10) m = Math.round(m / 5) * 5;
    if (m < 60) return tr("ui.dur.min", { n: m });
    const hh = Math.floor(m / 60), mm = m % 60;
    return mm ? tr("ui.dur.hm", { h: hh, m: String(mm).padStart(2, "0") }) : tr("ui.dur.h", { h: hh });
  };
  // Widok prognozy: "range" (domyślnie, decyzja właściciela produktu: zakres pracy do końca kroków i orientacyjna godzina końca, z dopiskiem „orientacyjnie”
  // i „rząd wielkości, nie obietnica”) albo "tempo" (samo tempo pracy na krok i liczba pozostałych kroków, bez sumy i godziny). Powtórka na prawdziwych
  // przebiegach pokazała ograniczoną dokładność zakresu (docs/dashboard-contract.md, sekcja `forecast`). Przełączenie = ta stała albo `deps.forecastView`.
  const FORECAST_VIEW = "range";
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
    const { h, icon, desc, openDoc, tr } = this;
    const r = d.readiness || {};
    const [cls, label] = READY_STATE[r.status] || ["is-none", tr("ui.noData")];
    const sha = r.planSha ? String(r.planSha).slice(0, 8) : "";
    const stale = Boolean(r.planSha && d.plan && d.plan.fileSha && !String(d.plan.fileSha).startsWith(String(r.planSha)));
    const facts = [r.date ? h("span", null, esc(r.date)) : null, sha ? h("span", { class: "mono" }, tr("ui.readiness.planSha", { sha: esc(sha) })) : null,
      stale ? h("span", { class: "chip s-warn" }, tr("ui.readiness.otherVersion")) : null].filter(Boolean);
    const open = r.source ? h("button", { class: "act-doc", type: "button", "data-open-doc": esc(r.source), "data-tip": tr("ui.docs.openTip", { path: esc(r.source) }),
      on: { click: () => openDoc(r.source) } }, icon("i-doc"), "readiness.md") : null;
    return h("div", { class: "readiness " + cls },
      h("span", { class: "stage readiness-chip", ...desc("stage_readiness", r.source || tr("ui.readiness.noFile")) },
        cls === "is-pass" ? icon("i-check") : null, tr("ui.readiness.label", { status: esc(label) })),
      facts.length ? h("span", { class: "readiness-facts" }, facts) : null,
      open);
  }
  // Podkroki Fn.k z czasem (akt III): rozwijana lista; stan rozwinięcia przeżywa odświeżenie (st.substepsOpen).
  function substepsBlock(d) {
    const { h, desc, st, inl, tr } = this;
    const STEP_LABEL = { done: tr("ui.step.done"), active: tr("ui.step.active"), planned: tr("ui.step.planned"), reported: tr("ui.step.reported") };   // the labels in the current language
    const items = d.substeps || [];
    if (!items.length) return null;
    const time = (s) => (s.seconds != null ? fmtDur(s.seconds * 1000) : s.missingData ? tr("ui.noData") : esc(STEP_LABEL[s.state] || s.state));
    const box = h("details", { class: "substeps", open: Boolean(st.substepsOpen) },
      h("summary", desc("substeps_time"), tr("ui.substeps.title", { n: items.length })),
      h("ul", { class: "sub-list" }, items.map((s) => h("li", { class: s.missingData ? "is-nodata" : "" },
        h("span", { class: "mono q-id" }, esc(s.id)), inl("tt", esc(s.title), s.titleHtml), h("span", { class: "mono sub-t" }, time(s))))));
    box.addEventListener("toggle", () => { st.substepsOpen = box.hasAttribute("open"); });
    return box;
  }

  // Czas pracy i czekania na człowieka (akt III): sumy na fazę i na przebieg, dymek z oknami czekania; nieznane to „brak danych”.
  function timingBlock(d) {
    const { h, desc, st, tr } = this;
    const t = d.timing;
    const list = (v) => (Array.isArray(v) ? v : []);
    if (!t || typeof t !== "object" || !t.available || !(list(t.stages).length || list(t.windows).length)) return null;
    const dur = (s) => (s != null ? fmtDur(s * 1000) : tr("ui.noData"));
    const clock = (iso) => (typeof iso === "string" && iso.length >= 16 ? iso.slice(11, 16) : "?");
    const part = (work, wait, more) => tr("ui.timing.part", { work: dur(work), wait: dur(wait), plus: more && wait != null ? "+" : "" });
    const windowLine = (w) => esc(w.what) + ": " + clock(w.startedAt)
      + (w.unknown ? " " + tr("ui.timing.windowUnknown")
        : "–" + clock(w.endedAt) + ", " + dur(w.seconds) + ", " + tr(w.source === "approval" ? "ui.timing.toApproval" : "ui.timing.toNextCheckpoint"));
    const tip = list(t.windows).length ? list(t.windows).map(windowLine).join("\n") : tr("ui.timing.noWindows");
    const totals = t.totals || {};
    const rows = list(t.stages).map((s) => h("li", { class: s.complete ? "" : "is-nodata" },
      h("span", { class: "mono q-id" }, esc(s.phase)),
      h("span", { class: "tt" }, esc(s.name)),
      h("span", { class: "mono sub-t" }, part(s.workSeconds, s.waitSeconds) + (s.complete ? "" : " " + tr("ui.timing.incomplete")))));
    rows.push(h("li", { class: totals.complete ? "" : "is-nodata" },
      h("span", { class: "mono q-id" }, "Σ"),
      h("span", { class: "tt" }, totals.unknownWindows ? tr("ui.timing.totalUnknown", { n: totals.unknownWindows }) : tr("ui.timing.total")),
      h("span", { class: "mono sub-t" }, part(totals.workSeconds, totals.waitSeconds, totals.unknownWindows > 0) + (totals.complete ? "" : " " + tr("ui.timing.incomplete")))));
    const box = h("details", { class: "timing-box", open: Boolean(st.timingOpen) },
      h("summary", desc("timing_time", tip), tr("ui.timing.title")),
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
    // Teksty: `deps.texts` (szew wtyczek: live, liveTip, noConnection) > `texts` ze statusu (pole `texts`, tylko dla języka innego niż pl) > wbudowany polski.
    // `lang` ze statusu (brak pola = pl) wybiera locale czasu; reguła liczby mnogiej idzie za językiem katalogu (bez `texts` wbudowany polski ma polskie formy).
    // `deps.texts` bywa obiektem albo funkcją `(lang) => obiekt`, wołaną przy każdym odczycie z aktualnym językiem (host zmienia teksty po zmianie języka);
    // wyjątek lub wynik inny niż obiekt = brak nadpisania.
    const L = { lang: "pl", locale: "pl-PL", plural: "pl", texts: null };
    function setLanguage(d) {
      const lang = d && typeof d.lang === "string" && /^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/.test(d.lang) ? d.lang : "pl";
      const own = lang !== "pl" && d && d.texts && typeof d.texts === "object" && !Array.isArray(d.texts) ? d.texts : null;
      const changed = lang !== L.lang || own !== L.texts;
      L.lang = lang; L.locale = localeOf(lang); L.texts = own; L.plural = own ? lang : "pl";
      return changed;
    }
    // `deps.initial` = `{ lang, texts }` of an earlier status: a renderer made for another tab of the same server draws its skeleton in the language of the page, not in Polish.
    if (deps.initial && typeof deps.initial === "object") setLanguage(deps.initial);
    const raw = (key) => {
      const v = L.texts ? L.texts[key] : undefined;
      if (typeof v === "string" || (v && typeof v === "object" && !Array.isArray(v))) return v;
      return Object.prototype.hasOwnProperty.call(DEFAULT_TEXTS, key) ? DEFAULT_TEXTS[key] : key;
    };
    // Tekst klucza: forma liczby mnogiej wg `n` (dostępnego też jako `{n}`), parametry `{nazwa}`; nieznany klucz zwraca klucz.
    const tr = (key, params, n) => {
      const v = raw(key);
      return fillParams(typeof v === "object" ? pickForm(L.plural, v, n) : v, n === undefined ? params : Object.assign({ n }, params));
    };
    // Jak `tr`, ale fragmenty w odwrotnych apostrofach to węzły `mono`; wynik to lista tekstów i węzłów dla h().
    const trRich = (key, params, n) => {
      const v = raw(key);
      return richParts(typeof v === "object" ? pickForm(L.plural, v, n) : v, n === undefined ? params : Object.assign({ n }, params), (text) => mono(text));
    };
    const fmtTimeL = (iso) => fmtTime(iso, L.locale);
    const clockL = (ms) => new Date(ms).toLocaleTimeString(L.locale, { hour: "2-digit", minute: "2-digit" });
    const dur = (sec, rough) => humanDur(sec, rough, tr);
    // `deps.texts`: obiekt albo funkcja `(lang) => obiekt`, czytane leniwie przy każdym użyciu (host może zmienić teksty z językiem statusu).
    // Wyjątek funkcji, rzucający getter albo wynik inny niż obiekt = brak nadpisania (wbudowany katalog).
    const hostText = (pick, fallback) => {
      try {
        let t = deps.texts;
        if (typeof t === "function") t = t(L.lang);
        return t && typeof t === "object" && !Array.isArray(t) ? pick(t) : fallback;
      } catch (e) { return fallback; }
    };
    const hostLive = () => hostText((t) => (t.live && typeof t.live === "object" ? t.live : {}), {});
    const hostTip = () => hostText((t) => (t.liveTip && typeof t.liveTip === "object" ? t.liveTip : {}), {});
    const liveText = (mode) => { const h = hostLive(); return Object.prototype.hasOwnProperty.call(h, mode) ? h[mode] : LIVE_KEY[mode] ? tr(LIVE_KEY[mode]) : undefined; };
    const liveTip = (mode) => { const h = hostTip(); return Object.prototype.hasOwnProperty.call(h, mode) ? h[mode] : LIVE_TIP_KEY[mode] ? tr(LIVE_TIP_KEY[mode]) : undefined; };
    const noConnection = () => { const v = hostText((t) => t.noConnection, undefined); return typeof v === "string" ? v : tr("ui.noConnection"); };
    // Czy ten host może dawać zgody: domyślnie approvals.here z JSON-a (/api/status); host bez tego pola (webview) podaje canApprove.
    const canApprove = (d) => (typeof deps.canApprove === "function" ? Boolean(deps.canApprove(d)) : Boolean(d.approvals && d.approvals.here));
    const viewport = typeof deps.viewport === "function" ? deps.viewport
      : () => { const v = doc.defaultView; return v ? { width: v.innerWidth, height: v.innerHeight } : { width: 1024, height: 768 }; };

    const st = {
      data: null, prev: null, view: "progress", doc: null, docData: null, desc: null,
      armed: null, armedTimer: null, approving: false, consent: null, password: null, sessionBusy: false, protoFrame: null, protoDetail: null, protoDialog: null, protoSeq: 0, protoFocus: null, protoRefocus: null, fb: { dialog: null, sending: false, result: "", olderOpen: false }, notice: null, noticeTimer: null, visible: true, openPhases: {},
      testsOpen: {}, testsAttempt: {}, testsLoading: {}, testsError: {}, testsLoaded: {}, testsStep: null, silenceShown: false, silenceMin: 0, agentsOpen: null, agentsShowDone: false, agentsSig: "", descFor: null, descPinned: false, docButtons: [], docGroupsOpen: {}, docGroupSeen: null, docFocus: null, docRebuilding: false, timers: new Set(), frames: new Set(), destroyed: false,
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
    // Tekst wartości z mapy kluczy katalogu (`STATE_KEY`, ...); wartość spoza mapy to `undefined`, jak dawniej przy mapach tekstów.
    const word = (map, value) => (Object.prototype.hasOwnProperty.call(map, value) ? tr(map[value]) : undefined);
    const mono = (text) => h("span", { class: "mono" }, text);
    const statusChip = (s) => {
      if (!s) return null;
      const cls = s === "done" || s === "approved" || s === "implemented" ? "s-done" : s === "in_progress" ? "s-active" : s === "draft" ? "s-warn" : "s-planned";
      return h("span", { class: "chip " + cls }, word(STATUS_KEY, s) || s);
    };
    const fmtAgo = (iso) => {
      if (!iso) return "—";
      const s = Math.max(0, Math.round((now() - Date.parse(iso)) / 1000));
      if (s < 10) return tr("ui.ago.justNow");
      if (s < 60) return tr("ui.ago.sec", { n: s });
      if (s < 3600) return tr("ui.ago.min", { n: Math.floor(s / 60) });
      return new Date(iso).toLocaleTimeString(L.locale, { hour: "2-digit", minute: "2-digit" });
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
    const kit = { h, icon, desc, openDoc: (path) => openDoc(path), st, inl, plain: plainInline, tr };
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
    // Teksty szkieletu powstają przed pierwszym statusem (a więc po polsku): po zmianie języka `relabel()` ustawia je na nowo (wołane przez update()).
    const relabelers = [];
    const relabel = () => relabelers.forEach((fn) => fn());
    const labelAttr = (el, name, key) => { relabelers.push(() => el.setAttribute(name, tr(key))); };
    const labelNode = (key, tail = "") => {
      const node = doc.createTextNode(tr(key) + tail);
      relabelers.push(() => { node.textContent = tr(key) + tail; });
      return node;
    };
    ui.title = h("h1", { id: "title" }, tr("ui.title.loading"));
    // the heading says "loading" (built-in Polish) until the first status; that status brings the language, so there is nothing to relabel here
    ui.meta = h("div", { class: "meta", id: "meta" });
    ui.live = h("span", { id: "live", class: "live is-connecting", role: "status", "aria-live": "polite", "data-tip": liveTip("connecting") },
      h("span", { class: "live-dot" }), ui.liveText = h("span", { class: "live-text" }, liveText("connecting")));
    st.liveMode = "connecting";
    relabelers.push(() => setLive(st.liveMode));
    if (theme) {
      ui.themeBtn = h("button", { id: "themeBtn", class: "btn theme-btn", type: "button", "aria-label": tr("ui.theme.aria"),
        "data-tip": tr("ui.theme.tip"), on: { click: toggleTheme } },
        icon("i-moon", "ic i-moon"), icon("i-sun", "ic i-sun"));
      labelAttr(ui.themeBtn, "aria-label", "ui.theme.aria"); labelAttr(ui.themeBtn, "data-tip", "ui.theme.tip");
    }
    ui.planBtnText = h("span", { id: "planBtnText" }, tr("ui.plan.btn"));
    ui.planAuto = h("span", { id: "planAuto", class: "chip plan-auto", hidden: true,
      "aria-label": tr("ui.plan.autoAria") }, labelNode("ui.plan.autoBadge"));
    labelAttr(ui.planAuto, "aria-label", "ui.plan.autoAria");
    ui.planBtn = h("button", { id: "planBtn", class: "btn", "aria-haspopup": "listbox", "aria-expanded": "false",
      "data-tip": tr("ui.plan.pickTip"), on: { click: (e) => { e.stopPropagation(); toggleMenu(); } } },
      svg("svg", { class: "ic" }, svg("use", { href: "#i-plan" })), ui.planBtnText, ui.planAuto, svg("svg", { class: "ic chev" }, svg("use", { href: "#i-chev" })));
    ui.planMenu = h("ul", { id: "planMenu", class: "menu", role: "listbox", hidden: true });
    // The account of the session (`deps.session`) and what it means for the consents; both stay hidden for a host without sessions.
    ui.account = h("div", { class: "account", id: "account", hidden: true });
    ui.sessionBar = h("div", { class: "session-bar", id: "sessionBar", role: "status", "aria-live": "polite", hidden: true });
    ui.waitBanner = h("div", { id: "waitBanner", class: "wait", role: "status", "aria-live": "polite", hidden: true, on: { click: mdClick } });
    ui.docCount = h("span", { class: "count", id: "docCount" });
    ui["tab-progress"] = h("button", { role: "tab", id: "tab-progress", "aria-controls": "view-progress", "aria-selected": "true",
      "data-tip": tr("ui.tab.progressTip"), on: { click: () => setView("progress") } },
      svg("svg", { class: "ic" }, svg("use", { href: "#i-run" })), labelNode("ui.tab.progress"));
    labelAttr(ui["tab-progress"], "data-tip", "ui.tab.progressTip");
    ui["tab-docs"] = h("button", { role: "tab", id: "tab-docs", "aria-controls": "view-docs", "aria-selected": "false",
      "data-tip": tr("ui.tab.docsTip"), on: { click: () => setView("docs") } },
      svg("svg", { class: "ic" }, svg("use", { href: "#i-doc" })), labelNode("ui.tab.docs", " "), ui.docCount);
    labelAttr(ui["tab-docs"], "data-tip", "ui.tab.docsTip");
    ui.tCount = h("span", { class: "count", id: "tCount" });
    ui["tab-tests"] = h("button", { role: "tab", id: "tab-tests", "aria-controls": "view-tests", "aria-selected": "false", hidden: true,
      "data-tip": tr("ui.tab.testsTip"), on: { click: () => setView("tests") } },
      svg("svg", { class: "ic" }, svg("use", { href: "#i-flask" })), labelNode("ui.tab.tests", " "), ui.tCount);
    labelAttr(ui["tab-tests"], "data-tip", "ui.tab.testsTip");
    ui.tests = h("div", { class: "tests-view", id: "testsView" });
    ui.qCount = h("span", { class: "count", id: "qCount" });
    ui["tab-questions"] = h("button", { role: "tab", id: "tab-questions", "aria-controls": "view-questions", "aria-selected": "false", hidden: true,
      "data-tip": tr("ui.tab.questionsTip"), on: { click: () => setView("questions") } },
      svg("svg", { class: "ic" }, svg("use", { href: "#i-bell" })), labelNode("ui.tab.questions", " "), ui.qCount);
    labelAttr(ui["tab-questions"], "data-tip", "ui.tab.questionsTip");
    ui.result = h("section", { class: "result-box", id: "resultBox", "aria-labelledby": "resultTitle", hidden: true, on: { click: mdClick } });
    ui.glossary = h("div", { class: "glossary-box", id: "glossaryBox" });
    // The section of feedback drafts exists in the DOM only while there is something to show; the live region of its result is persistent.
    ui.fbHost = h("div", { class: "fb-host", id: "fbHost" });
    ui.fbCount = h("span", { class: "fb-count" });
    ui.fbList = h("div", { class: "fb-list" });
    ui.fbResult = h("div", { class: "fb-result", role: "status", "aria-live": "polite" });
    ui.fbSection = h("section", { class: "fb-section", "data-feedback": "", "aria-labelledby": "fbTitle" },
      h("div", { class: "fb-head" }, h("h2", { id: "fbTitle" }, icon("i-flag"), labelNode("ui.feedback.title")), ui.fbCount),
      ui.fbList, ui.fbResult);
    ui.qItems = h("div", { class: "q-block", id: "qItems", on: { click: mdClick } });
    ui.acts = h("div", { class: "acts", id: "acts", on: { click: mdClick } },
      h("div", { class: "act skeleton" }), h("div", { class: "act skeleton" }), h("div", { class: "act skeleton wide" }));
    ui.agentsBox = h("section", { class: "agents-box", id: "agentsBox", "data-agents": "", hidden: true, "aria-labelledby": "agentsTitle", on: { click: mdClick } });
    ui.phases = h("div", { class: "phases", id: "phases", on: { click: mdClick } });
    ui.feed = h("ol", { class: "feed", id: "feed", on: { click: mdClick } });
    ui.parseWarn = h("div", { class: "parse-warn", id: "parseWarn", hidden: true });
    ui.docList = h("nav", { class: "doc-list", id: "docList", "aria-label": tr("ui.docs.group") });
    labelAttr(ui.docList, "aria-label", "ui.docs.group");
    const docEmptyText = () => tr(previewMode() ? "ui.docs.emptyPreview" : "ui.docs.empty");
    ui.docEmpty = h("div", { class: "empty" }, docEmptyText());
    relabelers.push(() => { if (ui.docEmpty.parentNode === ui.docView) ui.docEmpty.textContent = docEmptyText(); });
    ui.docView = h("article", { class: "doc-view", id: "docView", "aria-live": "polite", on: { click: mdClick } }, ui.docEmpty);
    ui["view-progress"] = h("section", { id: "view-progress", role: "tabpanel", "aria-labelledby": "tab-progress" },
      ui.acts, ui.agentsBox, ui.result, ui.fbHost, ui.glossary,
      h("div", { class: "work" }, ui.phases,
        h("aside", { class: "feed-box", "aria-labelledby": "feedTitle" },
          h("h2", { id: "feedTitle", "data-desc": "checkpoints", tabindex: "0" }, svg("svg", { class: "ic" }, svg("use", { href: "#i-flag" })), labelNode("ui.feed.title")),
          ui.parseWarn, ui.feed)));
    ui["view-tests"] = h("section", { id: "view-tests", role: "tabpanel", "aria-labelledby": "tab-tests", hidden: true }, ui.tests);
    ui["view-docs"] = h("section", { id: "view-docs", role: "tabpanel", "aria-labelledby": "tab-docs", hidden: true },
      h("div", { class: "docs" }, ui.docList, ui.docView));
    ui.qRealize = h("div", { class: "q-block", id: "qRealize" });
    ui.qRealizeGroup = h("section", { class: "q-group", "data-group": "realize" }, ui.qRealize);
    ui.qExpertGroup = h("section", { class: "q-group", "data-group": "expert" }, ui.qExpertHead = h("h2", { class: "q-group-title" }), ui.qItems);
    ui["view-questions"] = h("section", { id: "view-questions", role: "tabpanel", "aria-labelledby": "tab-questions", hidden: true },
      ui.qRealizeGroup, ui.qExpertGroup);
    ui.foot = h("footer", { class: "foot", id: "foot" });
    ui.descTip = h("div", { id: "descTip", class: "desc-tip", role: "tooltip", hidden: true });

    // ---------------------------------------------------------------- przycisk „na górę” (widok dokumentu i widok Testy)
    // Host podaje `deps.scroll.toTop = {subscribe(cb) → unsubscribe, go()}`; `cb` dostaje {y, viewport, height} przewijanego okna. Bez haczyka
    // (webview Skippstera, starsze adaptery) przycisku nie ma. Renderer liczy próg z histerezą, host przewija i pilnuje ruchu.
    const toTopHook = deps.scroll && deps.scroll.toTop && typeof deps.scroll.toTop.subscribe === "function" && typeof deps.scroll.toTop.go === "function"
      ? deps.scroll.toTop : null;
    const toTop = { shown: false, y: 0, viewport: 0, failed: false, focused: false, unsub: null };
    const TO_TOP_SHOW = 0.75, TO_TOP_HIDE = 0.5;   // ułamki wysokości okna: pokaż powyżej, schowaj poniżej
    ui.toTop = toTopHook ? h("button", { type: "button", id: "toTop", class: "to-top", hidden: true, "aria-label": tr("ui.toTop.aria"), "data-tip": tr("ui.toTop.tip"),
      on: { click: () => toTopClick(), focus: () => { toTop.focused = true; }, blur: () => { toTop.focused = false; } } }, icon("i-up")) : null;
    if (ui.toTop) { labelAttr(ui.toTop, "aria-label", "ui.toTop.aria"); labelAttr(ui.toTop, "data-tip", "ui.toTop.tip"); }
    if (toTopHook) root.classList.add("has-to-top");   // CSS: zapas pod dokumentem tylko, gdy przycisk jest możliwy
    function toTopSync() {
      if (!ui.toTop) return;
      const eligible = ((st.view === "docs" && Boolean(st.docData) && !toTop.failed) || st.view === "tests") && toTop.viewport > 0;
      let shown = eligible && toTop.shown;
      if (eligible && !shown && toTop.y > toTop.viewport * TO_TOP_SHOW) shown = true;
      else if (eligible && shown && toTop.y < toTop.viewport * TO_TOP_HIDE) shown = false;
      if (!shown && toTop.focused) { toTop.focused = false; focusViewTab(); }   // ukrywany przycisk z fokusem oddaje go zakładce, fokus nie ginie
      toTop.shown = shown;
      ui.toTop.hidden = !shown;
    }
    function focusViewTab() { const tab = ui["tab-" + st.view]; if (tab && typeof tab.focus === "function") tab.focus({ preventScroll: true }); }
    function toTopClick() {
      focusViewTab();   // przycisk chowa się sam, gdy płynne przewijanie zejdzie poniżej progu (metryki z hosta)
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
      h("div", { class: "top-right" }, ui.themeBtn || null, ui.live, h("div", { class: "plan-pick" }, ui.planBtn, ui.planMenu), ui.account)),
      ui.sessionBar,
      ui.waitBanner,
      ui.tabs = h("nav", { class: "tabs", role: "tablist", "aria-label": tr("ui.tabs.aria"), on: { keydown: tabKey } }, ui["tab-progress"], ui["tab-tests"], ui["tab-docs"], ui["tab-questions"]),
      ui.main = h("main", null, ui["view-progress"], ui["view-tests"], ui["view-docs"], ui["view-questions"]),
      ui.foot));
    root.appendChild(ui.descTip);
    labelAttr(ui.tabs, "aria-label", "ui.tabs.aria");
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
        st.protoFocus = protoFocusKey(t);
      },
      focusout: (e) => { st.protoFocus = null; if (descOf(e) && !st.descPinned) hideDesc(); },
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
      dropStaleDialogs(d);
      renderAccount(d);
      renderPrototypesTab(d);   // before the banner and the acts: it may close a frame, and their consent buttons depend on that
      const plan = d.plan;
      const specAct = d.mode === "spec" && d.spec && d.spec.exists;
      const runAct = d.mode === "run";
      const title = plan ? String(plan.title || "").replace(/^Plan:\s*/i, "")
        : specAct ? String(d.spec.title || d.spec.path).replace(/^Spec:\s*/i, "")
        : runAct ? tr("ui.title.specPreparing") : tr("ui.title.noPlan");
      ui.title.textContent = title;
      const waiting = (d.waiting || []).filter((w) => !waitDone(d, w));
      const waitsForYou = waiting.filter((w) => w.source !== "writing");   // „agent pisze ekrany” to praca sesji, nie czekanie na człowieka
      const progress = d.progress || {};
      if (typeof deps.onTitle === "function") {
        deps.onTitle(tr("ui.docTitle.full", { wait: waitsForYou.length ? tr("ui.docTitle.waiting") : "",
          lead: plan ? `${progress.percent}% · ` : specAct || runAct ? tr("ui.docTitle.actOne") : "", title }));
      }
      renderWaiting(d);

      const run = d.run || {};
      const meta = [];
      ui.elapsed = null; ui.ago = null;
      if (run.id) meta.push(h("span", { class: "m", "data-tip": tr("ui.meta.runIdTip", { id: run.id }) }, icon("i-flag"), mono(run.id)));
      if (plan) meta.push(h("span", { class: "m", "data-tip": tr("ui.meta.planStatusTip") }, statusChip(plan.status)));
      else if (specAct) meta.push(h("span", { class: "m", "data-tip": tr("ui.meta.specNoPlanTip") }, statusChip(d.spec.status)));
      if (run.startIso) {
        ui.elapsed = h("span", { class: "mono", id: "elapsed" }, fmtDur(now() - Date.parse(run.startIso)));
        meta.push(h("span", { class: "m", "data-tip": tr("ui.meta.runStartTip", { time: fmtTimeL(run.startIso) }) }, icon("i-clock"), ui.elapsed));
      }
      ui.ago = h("span", { id: "ago", "data-iso": d.lastUpdate || "" }, fmtAgo(d.lastUpdate));
      meta.push(h("span", { class: "m", "data-tip": tr("ui.meta.lastChangeTip") }, icon("i-sync"), ui.ago));
      {   // the panel stays open only while there is a list to show (a push that empties it must not leave a flag that reopens it later)
        const dl = d.delivery || {};
        if (!(d.git && d.git.branch && typeof dl.ahead === "number" && dl.ahead > 0 && Array.isArray(dl.commits) && dl.commits.length)) st.commitsOpen = false;
      }
      // Katalog roboczy (project.worktree): chip bez akcji; brak pola, `null` i nieznany `kind` = bez chipu. Pełne ścieżki w dymku (data-desc-extra).
      const pushWorkdir = () => {
        const wt = d.project && d.project.worktree;
        if (!wt || typeof wt !== "object" || (wt.kind !== "worktree" && wt.kind !== "main")) return;
        const label = wt.kind === "worktree" ? (typeof wt.name === "string" && wt.name ? tr("ui.workdir.worktreeNamed", { name: wt.name }) : tr("ui.workdir.worktree")) : tr("ui.workdir.main");
        const paths = [];
        if (typeof wt.path === "string" && wt.path) paths.push(wt.path);
        if (wt.kind === "worktree" && typeof wt.mainPath === "string" && wt.mainPath) paths.push(tr("ui.workdir.mainPath", { path: wt.mainPath }));
        meta.push(h("span", { class: "m" }, icon("i-folder"),
          h("span", Object.assign({ class: "chip s-planned workdir" }, desc("workdir_chip", paths.join("\n") || null)), h("span", { class: "workdir-name" }, label))));
      };
      if (d.git && d.git.branch) {
        const g = d.git;
        const host = g.remote ? ({ azure: "Azure DevOps", github: "GitHub", gitlab: "GitLab" }[g.remote.host] || String(g.remote.host || "")) : "";
        const name = `${g.branch}${g.head ? " @ " + String(g.head).slice(0, 7) : ""}`;
        const branch = g.branchUrl && g.branchPushed
          ? h("a", { class: "mono git-link", href: safeUrl(g.branchUrl), target: "_blank", rel: "noopener", "data-tip": tr("ui.git.openBranch", { host }),
            on: { click: linkClick("branch", g.branch, g.branchUrl) } }, name)
          : h("span", { class: "mono", "data-tip": g.branchUrl ? tr("ui.git.localOnlyTip") : tr("ui.git.headTip") }, name);
        meta.push(h("span", { class: "m" }, icon("i-branch"), branch));
        pushWorkdir();
        // `git.branchPushed` = origin/<gałąź> istnieje; liczba commitów przed origin to `delivery.ahead` (null = nieznana, 0 = nic do wypchnięcia: bez znacznika).
        const dv = d.delivery || {};
        const ahead = typeof dv.ahead === "number" && dv.ahead > 0 ? dv.ahead : 0;
        const remoteName = dv.remote || `origin/${g.branch}`;
        if (ahead) {
          // w trybie `direct` lokalne commity są normą do „CP”: ostrzeżenie dopiero, gdy plan jest zakończony
          const calm = dv.mode === "direct" && !(plan && plan.status === "done");
          const tip = tr(dv.pushed === false ? "ui.ahead.tipNoRemote" : "ui.ahead.tip", { remote: remoteName }, ahead);
          const label = tr("ui.ahead.label", {}, ahead);
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
                h("strong", { id: "commitsTitle" }, tr("ui.commits.title", { n: ahead }), g.branch ? " · " : "", g.branch ? h("span", { class: "mono" }, g.branch) : null),
                h("button", { type: "button", class: "commits-close", "aria-label": tr("ui.commits.closeAria"), "data-tip": tr("ui.commits.closeTip"), on: { click: (e) => { e.stopPropagation(); setCommits(false); const b = root.querySelector("#commitsBtn"); if (b && typeof b.focus === "function") b.focus(); } } }, "×")),
              h("ul", { class: "commits-list" }, list.map((c) => h("li", null, h("span", { class: "mono commit-sha" }, String(c.sha == null ? "" : c.sha)),
                h("span", { class: "commit-subject" }, String(c.subject == null ? "" : c.subject))))),
              h("p", { class: "commits-more" }, more ? tr("ui.commits.shown", { more }, list.length) : tr("ui.commits.all", { n: list.length })));
            meta.push(h("span", { class: "m commits-wrap" }, btn, panel));
          }
        }
      } else pushWorkdir();
      {   // tryb dostarczania (`delivery.mode`, `base`, `source`): chip przy gałęzi; zły tryb (`invalid`) to ostrzeżenie, nie cichy `pr`
        const dv = d.delivery && typeof d.delivery === "object" ? d.delivery : null;
        const invalid = !!dv && (dv.mode === "invalid" || dv.source === "invalid");
        if (dv && (invalid || (typeof dv.mode === "string" && dv.mode))) {
          const base = typeof dv.base === "string" && dv.base ? dv.base : "";
          const label = invalid ? tr("ui.delivery.invalid") : base ? tr("ui.delivery.labelBase", { mode: dv.mode, base }) : tr("ui.delivery.label", { mode: dv.mode });
          const src = dv.source === "default" ? tr("ui.delivery.srcDefault") : tr("ui.delivery.srcManifest");
          const tip = invalid ? tr("ui.delivery.tipInvalid") : tr("ui.delivery.tip", { mode: dv.mode, base: base || tr("ui.delivery.noBase"), source: src });
          meta.push(h("span", { class: "m" }, h("span", { class: `chip ${invalid ? "s-warn" : "s-planned"} delivery-mode`, "data-delivery": invalid ? "invalid" : dv.mode, "data-tip": tip }, label)));
          // którą zgodą oddaje się pracę w tym trybie; tylko dla hosta z sesjami (strona, która może zatwierdzać)
          const word = invalid || !sessionInfo() ? "" : dv.mode === "direct" ? "CP" : dv.mode === "pr" ? "READY" : "";
          if (word) meta.push(h("span", { class: "m" }, h("span", { class: "chip s-planned delivery-word", "data-delivery-word": word }, tr("ui.delivery.word", { word }))));
        }
      }
      if (plan && plan.pr) {
        meta.push(h("span", { class: "m" }, h("a", { class: "chip s-active git-link", href: safeUrl(plan.pr.url), target: "_blank", rel: "noopener",
          "data-tip": tr("ui.pr.tip"), on: { click: linkClick("pr", plan.pr.number, plan.pr.url) } }, `PR #${plan.pr.number}`)));
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
        : specAct ? tr("ui.plan.specAct", { name: baseName(d.spec.path).replace(/\.md$/, "") }) : runAct ? tr("ui.plan.runAct") : tr("ui.plan.btn");
      const autoPick = Boolean(plan && d.planAuto === true);
      ui.planAuto.hidden = !autoPick;
      ui.planBtn.setAttribute("data-tip", plan
        ? tr(autoPick ? "ui.plan.pathAuto" : "ui.plan.pathManual", { path: plan.path })
        : tr("ui.plan.pickTip"));
      renderPlanMenu();
      renderActs(d);
      renderAgents(d);
      renderPhases(d);
      renderFeed(d);
      renderParseWarn(d);
      renderDocList(d);
      renderResult(d);
      renderFeedback(d);
      renderGlossary();
      renderQuestionsTab(d);
      renderTestsTab(d);
      const app = d.app || {}, project = d.project || {};
      fill(ui.foot,
        h("span", null, `dashboard ${app.version ?? ""}`),
        h("span", null, trRich("ui.foot.project", { path: project.path })),
        h("span", null, tr("ui.foot.status", { mode: tr(d.approvals && d.approvals.enabled && canApprove(d) && !isViewer(d) && !sessionBlock() ? "ui.foot.readApprove" : "ui.foot.readOnly"),
          time: new Date(d.generatedAt).toLocaleTimeString(L.locale) })));
    }

    // ---------------------------------------------------------------- akty
    // Kafelek etapu: `state` (done/active/planned) i `since` są opcjonalne; bez `state` stan wynika z `done` (jak dotąd). Etap „Prototyp”
    // (`stage_prototype`) przy aktywnym znaczniku zapisu pokazuje „N z M ekranów” (bez M: „N ekranów”) i szacowany koniec z `eta`.
    function stageTile(d, s, fills) {
      let state = s.state === "done" || s.state === "active" || s.state === "planned" ? s.state : s.done ? "done" : "planned";
      // N16: a round of review in the journal (`agents.running`, kind review, profile of this tile) lights the green dot on the label; in round 1 (no report yet,
      // the tile is not done) it also makes the tile `active` from the start of the run, and the run wins over a hand-written `trwa:` (its `since`).
      const live = reviewRunFor(d, s.key);
      const liveOn = Boolean(live) && !live.stale;
      if (liveOn && state !== "done") state = "active";
      const isDone = state === "done", isActive = state === "active";
      const since = liveOn && isActive ? agentClock(live.run.startedAt) : isActive && typeof s.since === "string" && s.since ? s.since : "";
      const roundText = live ? (live.stale ? tr("ui.agents.roundStale")
        : Number.isInteger(live.run.round) ? tr("ui.agents.roundOn", { round: live.run.round, since: agentClock(live.run.startedAt) }) : tr("ui.agents.roundOnNoRound", { since: agentClock(live.run.startedAt) })) : "";
      const wr = s.key === "stage_prototype" && d.prototype && d.prototype.writing && typeof d.prototype.writing === "object" ? d.prototype.writing : null;
      const done = wr && Number.isFinite(wr.screensDone) && wr.screensDone >= 0 ? wr.screensDone : null;
      const planned = wr && Number.isFinite(wr.screensPlanned) && wr.screensPlanned > 0 ? wr.screensPlanned : null;
      let screens = null;
      if (done != null) {
        const eta = etaText(d);
        const label = planned ? tr("ui.screens.ofPlanned", { done, planned }) : tr("ui.screens.count", {}, done);
        let meter = null;
        if (planned) {
          const pct = Math.max(0, Math.min(100, Math.round((done / planned) * 100)));
          const fillEl = h("span", { class: "fill", "data-w": String(pct) });
          fills.push(fillEl);
          meter = h("span", { class: "bar thin stage-bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(planned), "aria-valuenow": String(Math.min(done, planned)),
            "aria-label": tr("ui.screens.protoTip", { label }) }, fillEl);
        }
        screens = h("span", { class: "stage-screens", "data-screens": "", title: eta || null }, meter, h("span", { class: "stage-screens-txt" }, label));
      }
      const quiet = isActive ? quietMinutes(d) : 0;
      const baseTip = isActive ? (quiet ? tr(since ? "ui.stage.quietTipSince" : "ui.stage.quietTip", { n: quiet, since }) : tr("ui.stage.activeSince", { since: since || "—" })) + (s.tip ? " · " + s.tip : "") : s.tip;
      const tip = roundText ? (baseTip ? baseTip + " · " : "") + roundText : baseTip;
      const dot = live ? h("span", { class: "stage-dot run-dot" + (live.stale ? " is-stale" : ""), "data-run-dot": "", "aria-hidden": "true" }) : isActive ? h("span", { class: "stage-dot", "aria-hidden": "true" }) : null;
      return h("span", { class: "stage" + (isDone ? " is-done" : "") + (isActive ? " is-active" : "") + (quiet ? " is-quiet" : "") + (screens ? " has-screens" : ""), "data-state": s.state ? state : null, ...desc(s.key, tip) },
        isDone ? icon("i-check") : null, dot, s.label,
        roundText ? h("span", { class: "stage-round", "data-run-round": "" }, roundText) : null,
        isActive ? h("span", { class: "sr-only" }, " " + (quiet ? tr("ui.stage.quietSr", { n: quiet }) : tr(since ? "ui.stage.inProgressSinceSr" : "ui.stage.inProgressSr", { since }))) : null, screens);
    }
    // POMIAR i PROGNOZA wyglądają i brzmią inaczej: pomiar („Pomiar:”, ciągła ramka) to czas, który minął, z checkpointów i zgód; prognoza („Prognoza:”,
    // przerywana ramka, kursywa) to ekstrapolacja z tempa przebiegu (domyślnie przedział i orientacyjna godzina końca, widok `tempo` samo tempo), z podanym powodem, gdy brak liczby.
    function actTimeLine(d, key) {
      const t = d.timing && typeof d.timing === "object" ? d.timing : null;
      const a = t && Array.isArray(t.acts) ? t.acts.find((x) => x && x.key === key) : null;
      if (!a) return null;
      const from = typeof a.startedAt === "string" && a.startedAt.length >= 16 ? a.startedAt.slice(11, 16) : "?";
      let text;
      if (!a.unknown) text = tr("ui.measure.workWait", { work: dur(a.workSeconds), wait: dur(a.waitSeconds) });
      else if (a.reason === "no-approval") text = (d.acts || []).some((x) => x.key === key && x.state === "active") ? tr("ui.measure.since", { from }) : tr("ui.measure.unknownNoApproval");
      else if (a.reason === "open-window") text = tr("ui.measure.unknownOpenWindow");
      else text = tr("ui.measure.unknownInconsistent");
      const tip = a.endedAt ? tr("ui.measure.tipRange", { from, to: String(a.endedAt).slice(11, 16) }) : tr("ui.measure.tip", { from });
      return h("div", { class: "measure", "data-measure": key, ...desc("measure_act", tip) }, h("span", { class: "lbl" }, tr("ui.measure.label")), " " + text);
    }
    function forecastLine(d) {
      const f = d.forecast && typeof d.forecast === "object" ? d.forecast : null;
      if (!f || f.scope !== "plan") return null;
      const view = deps.forecastView === "range" || deps.forecastView === "tempo" ? deps.forecastView : FORECAST_VIEW;
      const tip = tr(view === "range" ? "ui.forecast.tipRange" : "ui.forecast.tipTempo");
      let text;
      if (typeof f.reason === "string") {
        if (f.reason === "not-enough-data") text = tr("ui.forecast.notEnough", { have: Number.isInteger(f.have) ? f.have : 0, need: Number.isInteger(f.need) ? f.need : 4 });
        else if (f.reason === "few-moments") text = tr("ui.forecast.fewMoments", { have: Number.isInteger(f.have) ? f.have : 0, need: Number.isInteger(f.need) ? f.need : 3 });
        else if (f.reason === "no-run-start") text = tr("ui.forecast.noRunStart");
        else return null;
        return h("div", { class: "forecast is-none", "data-forecast": "none", ...desc("forecast", tip) }, h("span", { class: "lbl" }, tr("ui.forecast.label")), " " + text);
      }
      const nums = [f.lowSeconds, f.medianSeconds, f.highSeconds];
      if (!nums.every((n) => Number.isFinite(n) && n >= 0) || !Number.isInteger(f.remaining)) return null;
      if (view === "tempo") {
        if (f.remaining < 1) return null;
        const per = f.medianSeconds / f.remaining;
        const unit = per < 60 ? tr("ui.forecast.perStepLess") : tr("ui.forecast.perStep", { dur: dur(per, true) });
        const tail = f.paused ? tr("ui.forecast.pausedTail") : "";
        return h("div", { class: "forecast" + (f.paused ? " is-paused" : ""), "data-forecast": "tempo", ...desc("forecast", tip) }, h("span", { class: "lbl" }, tr("ui.forecast.label")),
          " " + tr("ui.forecast.tempo", { unit, samples: tr("ui.forecast.samplesSteps", {}, f.samples), left: tr("ui.forecast.stepsLeft", {}, f.remaining), tail }));
      }
      const lo = dur(f.lowSeconds, true), hi = dur(f.highSeconds, true), range = lo === hi ? lo : `${lo}–${hi}`;
      const basis = tr("ui.forecast.basis", { left: tr("ui.forecast.stepsLeft", {}, f.remaining), gaps: tr("ui.forecast.gaps", {}, f.samples) });
      const t0 = now();
      const end = f.paused ? tr("ui.forecast.paused") : tr("ui.forecast.end", { from: clockL(t0 + f.lowSeconds * 1000), to: clockL(t0 + f.highSeconds * 1000) });
      text = tr("ui.forecast.range", { range, basis, end });
      return h("div", { class: "forecast" + (f.paused ? " is-paused" : ""), "data-forecast": f.paused ? "paused" : "range", ...desc("forecast", tip) }, h("span", { class: "lbl" }, tr("ui.forecast.label")), " " + text);
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
              p.pendingPercent ? h("span", { class: "pend", "data-tip": tr("ui.progress.pendingTip", { ids: (p.reported || []).join(", ") }) }, tr("ui.progress.pendingLabel", { n: p.pendingPercent })) : null,
              h("span", { class: "of", "data-tip": tr("ui.progress.doneTip") }, tr("ui.progress.of", { done: p.done, total: p.total }))),
            bar(p.percent, p.pendingPercent, barCls, tr("ui.progress.aria"), fills, p.activePercent),
            legend(p),
            cur ? h("div", { class: "now", "data-tip": tr("ui.progress.currentTip", { ids: (p.active || []).join(", "), index: cur.index, total: p.total, source: tr(p.activeSource === "checkpoint" ? "ui.progress.fromCheckpoint" : "ui.progress.firstUnchecked") }) },
              h("span", { class: "dot-live" }), h("span", { class: "txt" }, tr("ui.progress.now") + " ", mono(cur.id), " ", inl("", cur.title ?? "", cur.titleHtml)),
              p.parallel ? h("span", { class: "par" }, tr("ui.progress.parallel", { n: p.parallel })) : null)
              : h("div", { class: "act-detail" }, a.detail || ""),
            a.state === "active" ? etaLine(d, "act") : null,
            forecastLine(d),
            h("div", { class: "stages" }, stages),
            substepsBlock.call(kit, d),
            timingBlock.call(kit, d),
            h("div", { class: "act-actions" }, approveButton(d, "cp", "act-approve"), approveButton(d, "ready", "act-approve")),
          ];
        } else {
          const docRef = a.key === "spec" ? (d.spec && d.spec.exists ? { path: d.spec.path, label: tr("ui.act.openSpec") } : null)
            : a.key === "plan" ? (d.plan ? { path: d.plan.path, label: tr("ui.act.openPlan") } : null) : null;
          const docLink = docRef ? h("button", { class: "act-doc", type: "button", "data-open-doc": docRef.path, "data-tip": tr("ui.docs.openTip", { path: docRef.path }),
            on: { click: () => openDoc(docRef.path) } }, icon("i-doc"), docRef.label) : null;
          const approve = approveButton(d, a.key === "spec" ? "spec" : "plan", "act-approve");
          const extra = a.key === "plan" ? readinessTile.call(kit, d) : null;
          body = [h("div", { class: "act-detail" }, a.detail || " "), actTimeLine(d, a.key), a.state === "active" ? etaLine(d, "act") : null, h("div", { class: "stages" }, stages), extra, warn, h("div", { class: "act-actions" }, docLink, approve)];
        }
        return h("div", { class: `act s-${a.state}${glow ? " s-active" : ""}`, "data-key": a.key },
          h("div", { class: "roman", "aria-hidden": "true" }, a.roman),
          h("div", { class: "act-head", ...desc("act_" + a.key) },
            h("div", { class: "act-icon" }, icon(ACT_ICON[a.key])),
            h("div", { class: "act-name" }, h("small", null, tr("ui.act.title", { roman: a.roman })), a.name),
            h("span", { class: `chip s-${a.state} chip-end` }, word(STATE_KEY, a.state))),
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
        return emptyPhase(trRich("ui.phase.specNoPlan", { path: d.spec && d.spec.path, status: (d.spec && d.spec.status) || "—" }));
      }
      if (!d.plan && d.mode === "run") {
        return emptyPhase(trRich("ui.phase.runNoSpec", { id: d.run && d.run.id }));
      }
      if (!d.plan) {
        return emptyPhase(trRich("ui.phase.noPlan"));
      }
      if (!phases.length) {
        return emptyPhase(trRich("ui.phase.noProgress"));
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
        const headAttrs = { class: "phase-head", ...desc("phase", tr("ui.phase.substeps", { done: ph.done, total: ph.total })) };
        if (foldable) Object.assign(headAttrs, { role: "button", tabindex: "0", "aria-expanded": String(!folded), "data-phase-toggle": ph.name, on: { click: toggle, keydown: toggle } });
        return h("section", { class: `phase s-${ph.state}${folded ? " is-folded" : ""}` },
          h("div", headAttrs,
            h("div", null, ph.label ? h("div", { class: "lbl" }, ph.label) : null, h("div", { class: "nm" }, ph.short)),
            h("span", { class: "n" }, ph.state === "done" ? icon("i-check") : null, `${ph.done}/${ph.total}`)),
          bar(ph.percent, ph.pendingPercent, "thin " + (ph.state === "done" ? "is-done" : ""), ph.short, fills),
          h("ul", { class: "steps" }, (ph.steps || []).map((s) => {
            const changed = st.prev && prevStates[s.id] && prevStates[s.id] !== s.state;
            const tip = `${s.id} ${plainInline(s.title, s.titleHtml)} — ${stepLabel(tr, s.state)}${s.sha ? " · " + tr("ui.step.commit", { sha: s.sha }) : ""}${s.note && !s.sha ? " · " + plainInline(s.note, s.titleHtml) : ""}`;
            const mark = s.state === "done" || s.state === "reported" ? icon("i-check") : null;
            const rounds = stepRounds(d, s.id);
            const ev = etaView(d);
            const stepEta = ev && ev.steps.indexOf(s.id) >= 0 && s.state !== "done" ? etaLine(d, "step", true) : null;
            const dev = stepDeviation(d, s.id);
            return h("li", { class: `step s-${s.state}${changed ? " flash" : ""}${s.tests ? " has-tests" : ""}`, ...desc(/^(?:zamknięcie|closure)/i.test(s.title || "") ? "closure" : "substep", tip) },
              h("span", { class: "st", "aria-label": stepLabel(tr, s.state) ?? "" }, mark), h("span", { class: "sid" }, s.id), inl("tt", s.title, s.titleHtml),
              rounds ? h("span", { class: "step-rounds", "data-step-rounds": "" }, tr("ui.agents.stepRounds", {}, rounds)) : null,
              stepEta, dev ? h("span", { class: "step-dev", "data-step-dev": "", "data-tip": dev }, icon("i-clock")) : null,
              stepTests(s, fills));
          })));
      }));
      animateBars(fills);
    }

    // ---------------------------------------------------------------- testy (180-A): pasek przy kroku i zakładka „Testy”
    const TEST_STATE_KEY = { passed: "ui.testState.passed", failed: "ui.testState.failed", running: "ui.testState.running", skipped: "ui.testState.skipped", waiting: "ui.testState.waiting" };
    const TEST_MARK = { passed: "✓", failed: "✕", skipped: "–", running: "", waiting: "" };
    const num = (v) => (Number.isFinite(v) && v > 0 ? Math.floor(v) : 0);
    // Pasek „ile przeszło z ilu” przy kroku planu i przycisk do jego sekcji; kroki bez testów nie mają niczego.
    function stepTests(step, fills) {
      const t = step && step.tests;
      if (!t || typeof t !== "object" || !t.counts) return null;
      const c = t.counts, total = num(c.total) || num(c.passed) + num(c.failed) + num(c.skipped) + num(c.running) + num(c.waiting);
      if (!total) return null;
      const pct = (n) => Math.min(100, Math.round((100 * num(n)) / total));
      const label = tr("ui.tests.barPassed", { passed: num(c.passed), total }, total) + (num(c.failed) ? ", " + tr("ui.tests.countRed", {}, num(c.failed)) : "")
        + (num(c.running) ? ", " + tr("ui.tests.barRunning", { n: num(c.running) }) : "");
      const parts = [["g", c.passed], ["r", c.failed], ["a", c.running]].map(([cls, n]) => {
        const el = h("i", { class: cls, "data-w": String(pct(n)) });
        fills.push(el);
        return el;
      });
      return h("span", { class: "stbar" },
        h("span", { class: "tbar", role: "img", "aria-label": label }, parts),
        h("span", { class: "n" }, `${num(c.passed)}/${total}`),
        h("button", { type: "button", class: "tolink", "data-test-step": step.id, "aria-label": tr("ui.tests.showAria", { id: step.id }), "data-tip": tr("ui.tests.showTip"),
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
      if (num(c.failed)) out.push(h("span", { class: "chip s-bad" }, "✕ " + tr("ui.tests.countRed", {}, num(c.failed))));
      if (num(c.passed)) out.push(h("span", { class: "chip s-done" }, tr("ui.tests.countGreen", {}, num(c.passed))));
      if (num(c.running)) out.push(h("span", { class: "chip s-warn" }, tr("ui.tests.countRunning", {}, num(c.running))));
      if (num(c.waiting)) out.push(h("span", { class: "chip s-planned" }, tr("ui.tests.countWaiting", { n: num(c.waiting) })));
      if (num(c.skipped)) out.push(h("span", { class: "chip s-planned" }, tr("ui.tests.countSkipped", {}, num(c.skipped))));
      return out;
    }
    function testRow(t) {
      const state = Object.prototype.hasOwnProperty.call(TEST_STATE_KEY, t.state) ? t.state : "waiting";
      return h("li", { class: `t is-${state}` },
        h("span", { class: `tst ${state}`, role: "img", "aria-label": tr(TEST_STATE_KEY[state]) }, TEST_MARK[state]),
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
        st.testsError[gk] ? h("div", { class: "terr", role: "alert" }, tr("ui.tests.groupError")) : null,
        more ? h("button", { type: "button", class: "tmore", on: { click: () => {
          st.testsLoading[gk] = true; st.testsError[gk] = false;
          const have = Array.isArray(st.testsLoaded[gk]) ? st.testsLoaded[gk] : [];
          Promise.resolve(deps.actions.loadTests({ run: st.data && st.data.run && st.data.run.id, step: sec.step && sec.step.id, attempt: attempt.n, group: group.name, offset: have.length }))
            .then((body) => { if (body && Array.isArray(body.tests)) st.testsLoaded[gk] = have.concat(body.tests); else st.testsError[gk] = true; })
            .catch(() => { st.testsError[gk] = true; })
            .finally(() => { st.testsLoading[gk] = false; render(); });
        } } }, Array.isArray(loaded) ? tr("ui.tests.showMore", { n: total - shown.length }) : tr("ui.tests.showAll", { n: total })) : null);
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
      const state = sec.state === "passed" ? tr("ui.testState.passed") : sec.state === "failed" ? tr("ui.testState.failed") : sec.state === "running" ? tr("ui.tests.running") : sec.state === "interrupted" ? tr("ui.tests.interrupted") : "";
      // polecenie zawiodło poza raportowanymi testami: kod ≠ 0 przy zerze porażek (poziomy tests/counts), pokazana próba zakończona
      const cmdFailed = att.state !== "running" && (att.levels === "tests" || att.levels === "counts") && typeof att.exit === "number" && Number.isFinite(att.exit) && att.exit !== 0 && !num(c.failed);
      const head = h("button", { type: "button", id: `tsec-btn-${id}`, class: "tsec-head", "aria-expanded": String(open), "aria-controls": open ? `tsec-body-${id}` : null,
        on: { click: () => { st.testsOpen[key] = !open; render(); const b = root.querySelector(`#tsec-btn-${id}`); if (b && typeof b.focus === "function") b.focus({ preventScroll: true }); } } },
        h("span", { class: "chev", "aria-hidden": "true" }, open ? "▾" : "▸"),
        h("span", { class: "sh" }, h("b", null, step ? h("span", { class: "mono" }, step.id) : null, step ? " " : "", step ? (step.title || "") : tr("ui.tests.noStep")),
          h("span", { class: "sub" }, tr("ui.tests.attemptSub", { n: att.n, of: attempts.length > 1 ? attempts[0].n : att.n, time: att.startedAt ? fmtTimeL(att.startedAt) : "—",
            dur: Number.isFinite(att.durationMs) ? " · " + fmtDur(att.durationMs) : "" }))),
        h("span", { class: "sum", ...(running ? { "aria-live": "polite", "aria-atomic": "true" } : {}) }, state ? h("span", { class: `chip ${sec.state === "passed" ? "s-done" : sec.state === "failed" ? "s-bad" : sec.state === "running" ? "s-active" : "s-warn"}` }, state) : null, countChips(c),
          cmdFailed ? h("span", { class: "chip s-bad cmd-failed", "data-tip": tr("ui.tests.cmdFailedTip", { exit: att.exit }) }, tr("ui.tests.cmdFailed", { exit: att.exit })) : null));
      const body = open ? h("div", { class: "tsec-body", id: `tsec-body-${id}` },
        cmdFailed ? h("div", { class: "tnote cmd-failed-note" }, tr("ui.tests.cmdFailedTip", { exit: att.exit })) : null,
        att.command ? h("div", { class: "cmd mono" }, String(att.command)) : null,
        h("div", { class: "tprogress", role: "img", "aria-label": tr("ui.tests.greenAria", { n: num(c.passed), total: total || "?" }) }, ["g", "r", "a"].map((cls, i) => {
          const el = h("i", { class: cls, "data-w": String(total ? Math.min(100, Math.round((100 * num([c.passed, c.failed, c.running][i])) / total)) : 0) });
          fills.push(el);
          return el;
        })),
        h("div", { class: "tlevel", "data-tip": tr("ui.tests.levelTip") },
          tr("ui.tests.levelLabel", { level: tr(att.levels === "tests" ? "ui.tests.levelTests" : att.levels === "counts" ? "ui.tests.levelCounts" : "ui.tests.levelCommand") })
            + (att.live === "completion" ? " " + tr("ui.tests.liveCompletion") : "") + (att.reason ? " · " + att.reason : "") + (att.truncated ? " · " + tr("ui.tests.truncated") : "")),
        attempts.length > 1 ? h("div", { class: "tatt", role: "group", "aria-label": tr("ui.tests.attempts") }, attempts.map((a, i) => h("button", { type: "button", class: `tab-att${i === idx ? " is-on" : ""}`, "aria-pressed": String(i === idx),
          on: { click: () => { st.testsAttempt[key] = i; render(); } } }, tr("ui.tests.attemptBtn", { n: a.n, mark: a.state === "passed" ? "✓" : a.state === "failed" ? "✕" : a.state === "running" ? "…" : "!",
          red: num(a.counts && a.counts.failed) ? tr("ui.tests.attemptRed", { n: num(a.counts.failed) }) : "" }).trim()))) : null,
        groupsNote(att),
        (att.groups || []).map((g) => groupBlock(sec, att, g, key))) : null;
      return h("section", { class: `tsec s-${sec.state || "passed"}`, id: `tsec-${id}` }, head, body);
    }
    const SKIP_REASON_KEY = { "not a step directory": "ui.skipReason.notStepDir", unreadable: "ui.skipReason.unreadable" };
    const SKIP_LIMIT = 20;   // MAX_SKIPPED serwera: lista nie niesie sumy, więc przy pełnej liście liczba to dolne ograniczenie
    function skippedNote(list) {
      const items = list.filter((e) => e && typeof e === "object" && typeof e.path === "string");
      if (!items.length) return null;
      const n = items.length;
      if (n === 1 && items[0].path === "tests") return h("p", { class: "tskipped", role: "note" }, tr("ui.tests.unreadable"));
      const count = list.length >= SKIP_LIMIT ? tr("ui.tests.atLeast", { n }) : String(n);
      const noun = tr("ui.tests.skippedNoun", {}, n);
      const why = (r) => { const t = typeof r === "string" ? (Object.prototype.hasOwnProperty.call(SKIP_REASON_KEY, r) ? tr(SKIP_REASON_KEY[r]) : r) : ""; return t ? ` (${t})` : ""; };
      const shown = items.slice(0, 5).map((e) => `${e.path}${why(e.reason)}`).join(", ");
      const more = n > 5 ? " " + tr("ui.tests.moreItems", { n: n - 5 }) : "";
      return h("p", { class: "tskipped", role: "note" }, tr("ui.tests.skippedNote", { count, noun, shown, more }));
    }
    function groupsNote(att) {   // the server lists at most MAX_GROUPS groups of an attempt and counts the rest
      const omitted = num(att.groupsOmitted);
      if (!(omitted > 0)) return null;
      const total = num(att.groupsTotal);
      if (!(total > omitted)) return null;   // a foreign JSON without the total: no note rather than a nonsense count
      return h("p", { class: "tgroups-note", role: "note" }, tr("ui.tests.groupsNote", { shown: total - omitted, total }));
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
      // A reader without a session is told that the results are as the tests printed them (the server says when: no session and no `tests.mask`).
      const raw = t.publicRaw === true ? h("p", { class: "session-note is-warn", "data-tests-raw": "" }, trRich("ui.tests.publicRaw")) : null;
      const skipped = Array.isArray(t.skipped) ? skippedNote(t.skipped) : null;
      const secs = t.sections.filter((sec) => sec && typeof sec === "object");
      if (skipped && !secs.length) {
        fill(ui.tests, raw, skipped, h("p", { class: "empty" }, tr("ui.tests.empty")));
        ui["view-tests"].hidden = st.view !== "tests";
        return;
      }
      fill(ui.tests,
        raw,
        skipped,
        h("div", { class: "tlegend", role: "list" },
          ["waiting", "running", "passed", "failed"].map((k) => h("span", { role: "listitem" }, h("span", { class: `tst ${k}`, "aria-hidden": "true" }, TEST_MARK[k]), ` ${tr(TEST_STATE_KEY[k])}`))),
        secs.map((sec) => testSection(sec, fills)));
      animateBars(fills);
      ui["view-tests"].hidden = st.view !== "tests";
    }

    // Dwa odcienie paska (tylko gdy `activePercent` > 0): ciemniejszy = zweryfikowane, jaśniejszy = zgłoszone i w toku (pending + active).
    const activeOf = (v) => (Number.isFinite(v) && v > 0 ? v : 0);
    function legend(p) {
      const active = activeOf(p.activePercent);
      if (!active) return null;
      return h("div", { class: "bar-legend", "data-bar-legend": "" }, h("span", { class: "sw sw-dark", "aria-hidden": "true" }), tr("ui.bar.legendVerified", { pct: p.percent }),
        h("span", { class: "sw sw-light", "aria-hidden": "true" }), tr("ui.bar.legendReported", { n: (p.pendingPercent || 0) + active }));
    }
    function bar(pct, pending, cls, label, fills, activePct) {
      const active = activeOf(activePct);
      const attrs = { class: `bar ${cls}${active ? " has-active" : ""}`, role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct), "aria-label": String(label ?? "") };
      if (pending) {
        attrs["aria-valuetext"] = tr("ui.bar.valueTextPending", { pct, pending });
        attrs["data-tip"] = tr("ui.bar.tipPending", { pct, pending });
      }
      if (active) {
        attrs["aria-valuetext"] = tr("ui.bar.valueTextActive", { pct, reported: (pending || 0) + active });
        attrs["data-tip"] = tr("ui.bar.tipActive", { pct, reported: (pending || 0) + active, pending: pending || 0, active });
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
    // Wiersz z formatowaniem w linii (`textHtml` z serwera, tylko gdy tekst ma formatowanie; serwer wyróżnia w nim id kroków i frazy tak samo jak
    // `decorateCheckpoint`) dostaje je przez `deps.mountHtml`; bez niego dotychczasowe wyróżnienie po stronie strony (`decorateCheckpoint`).
    const feedText = (c) => (typeof c.textHtml === "string" && c.textHtml && typeof deps.mountHtml === "function" ? inl("", c.text, c.textHtml) : h("span", null, decorateCheckpoint(c.text)));
    function renderFeed(d) {
      const items = (d.run && d.run.checkpoints) || [];
      const prevTop = st.prev && st.prev.run && st.prev.run.checkpoints && st.prev.run.checkpoints[0];
      const prevCount = ((st.prev && st.prev.run && st.prev.run.checkpoints) || []).length;
      const fresh = st.prev ? Math.max(0, items.length - prevCount) : 0;
      fill(ui.feed, items.length
        ? items.map((c, i) => h("li", { class: i < fresh || (i === 0 && prevTop && prevTop.text !== c.text) ? "is-new" : "" },
          h("span", { class: "t" }, c.time), feedText(c)))
        : h("li", null, h("span"), h("span", { class: "empty" }, trRich("ui.checkpoints.empty"))));
    }

    // Fragmenty checkpointów pominięte przez parser (run.parseWarnings): jedna linia, szczegóły w dymku (tylko tekst).
    const PARSE_REASON_KEY = { "no-step-id": "ui.parseReason.noStepId", "later-id": "ui.parseReason.laterId", "unbalanced-bracket": "ui.parseReason.unbalancedBracket" };
    const parseReason = (reason) => word(PARSE_REASON_KEY, reason) || String(reason ?? "");
    function renderParseWarn(d) {
      const list = d.run && Array.isArray(d.run.parseWarnings) ? d.run.parseWarnings.filter((x) => x && typeof x === "object") : [];
      const el = ui.parseWarn;
      if (!list.length) { el.hidden = true; fill(el); return; }
      const reasons = [...new Set(list.map((x) => parseReason(x.reason)))].filter(Boolean);
      const lines = list.map((x) => tr("ui.checkpoints.skipLine", { time: x.time ?? "", segment: x.segment ?? "", fragment: x.fragment ?? "", reason: parseReason(x.reason) }).trim());
      el.hidden = false;
      el.setAttribute("data-count", String(list.length));
      const n = list.length;
      fill(el, icon("i-flag"), h("span", { ...desc("parse_warnings", lines.join("\n")) },
        tr("ui.checkpoints.skipped", {}, n) + (reasons.length ? ` (${reasons.join("; ")})` : "")));
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
          h("span", null, tr("ui.plan.autoRow")), h("span", { class: "sub" }, "in_progress → approved → draft")),
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
    // Origin prototypów: status serwera go nazywa (`prototype.url`, null = nie do ustalenia) i mówi, czy pokazuje prototypy tej zakładki (`served`);
    // bez klucza (`dashboard show`, starszy serwer) adres składa host z portu.
    const protoInfo = (d) => (d && d.prototype && typeof d.prototype === "object" ? d.prototype : {});
    const originKnown = (d) => Object.prototype.hasOwnProperty.call(protoInfo(d), "url");
    function protoOrigin(d) {
      const pr = protoInfo(d);
      if (originKnown(d)) return pr.served === false || !safeUrl(pr.url) ? null : safeUrl(pr.url).replace(/\/+$/, "");
      const origin = typeof deps.prototypeOrigin === "function" ? deps.prototypeOrigin(pr.port) : null;
      return origin ? String(origin).replace(/\/+$/, "") : null;
    }
    function protoUrl(d, slug) {
      if (originKnown(d)) { const base = protoOrigin(d); return base && PROTO_SLUG.test(String(slug)) ? `${base}/${slug}/` : null; }
      return typeof deps.prototypeUrl === "function" ? deps.prototypeUrl(protoInfo(d).port, slug) : null;
    }
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
      if (a.state !== "after" && frameOnScreen()) return null;   // no consent beside a frame a prototype script draws in (an overlay could imitate it)
      if (a.state !== "after" && d.singleOrigin === true) {
        return h("button", { class: cls, type: "button", disabled: true, "aria-disabled": "true", "data-state": "waiting", "data-gate": kind,
          ...desc(GATE_KEY[kind], tr("ui.proto.single")) }, icon("i-check"), a.word);
      }
      if (a.state !== "after" && !viewer && anonymous()) return null;   // no consent buttons without a session: the bar under the header asks to sign in
      if (a.state === "after") {
        const at = a.given && a.given.at ? new Date(a.given.at).toLocaleTimeString(L.locale, { hour: "2-digit", minute: "2-digit" }) : "";
        return h("button", { class: `${cls} is-given`, type: "button", disabled: true, "data-state": "after", "data-gate": kind, ...desc(GATE_KEY[kind], a.reason) },
          icon("i-check"), `${a.word}${to}${at ? " " + at : ""}`);
      }
      if (a.state !== "waiting") {
        return h("button", { class: `${cls} is-waiting`, type: "button", disabled: true, "data-state": "running", "data-gate": kind, ...desc(GATE_KEY[kind], viewer ? tr("ui.viewer.readOnly") : a.reason) }, `${a.word}${to}`);
      }
      // With a session (`deps.session`) the consent also needs a signed-in account with the role `approver`; an anonymous reader never gets here.
      const block = viewer ? null : sessionBlock();
      if (!canApprove(d) || viewer || block) {
        return h("button", { class: cls, type: "button", disabled: true, "aria-disabled": "true", "data-state": "waiting", "data-gate": kind,
          ...desc(GATE_KEY[kind], viewer ? tr("ui.viewer.readOnly") : block ? tr(block) : tr("ui.approve.hereOnlyTip", { word: a.word })) }, icon("i-check"), a.word);
      }
      const armed = st.armed === kind;
      const label = st.approving && armed ? tr("ui.approve.saving") : tr(armed ? "ui.approve.confirm" : "ui.approve.label", { word: a.word, to });
      const sha = String(a.sha || "");
      const what = kind === "cp" || kind === "ready" ? `${a.reason ? a.reason + "; " : ""}HEAD ${sha.slice(0, 8)}` : tr("ui.approve.versionOf", { path: a.path, sha: sha.slice(0, 8) });
      return h("button", { class: `${cls}${armed ? " is-armed" : ""}`, type: "button", "data-state": "waiting", "data-approve": kind, "data-gate": kind,
        ...desc(GATE_KEY[kind], tr("ui.approve.tip", { word: a.word, what })), on: { click: () => approve(kind) } }, icon("i-check"), label);
    }
    // What the page shows when the human decides: the offer (sha, path, plan state) and the branch and target of the delivery.
    function consentRequest(d, a) {
      const dv = d.delivery && typeof d.delivery === "object" ? d.delivery : {};
      const text = (v) => (typeof v === "string" ? v : null);
      return { kind: a.kind, sha: a.sha, path: a.path, word: a.word, planState: a.planState, branch: text(dv.branch), target: text(dv.target), mode: text(dv.mode), base: text(dv.base) };
    }
    async function sendConsent(request) {
      let res;
      try {
        if (typeof actions.approve !== "function") throw new Error("brak actions.approve");
        res = await actions.approve(request);
      } catch {
        res = { ok: false, word: request.word, text: noConnection() };
      }
      if (res && res.ok !== true && Object.prototype.hasOwnProperty.call(CONSENT_ERR_KEY, res.code)) res = Object.assign({}, res, { text: tr(CONSENT_ERR_KEY[res.code]) });
      return res;
    }
    async function approve(kind) {
      const d = st.data;
      const a = ((d && d.approvals && d.approvals.actions) || []).find((x) => x.kind === kind);
      if (!a || st.approving || isViewer(d) || !canApprove(d) || sessionBlock() || d.singleOrigin === true || frameOnScreen()) return;
      if (sessionInfo()) return openConsent(a);   // a session: the window with the decision, one click, no password
      if (st.armed !== kind) {
        st.armed = kind;
        cancel(st.armedTimer);
        st.armedTimer = later(() => { st.armed = null; render(); }, ARM_MS);
        return render();
      }
      cancel(st.armedTimer);
      st.approving = true; render();
      const res = await sendConsent(consentRequest(st.data, a));
      if (st.destroyed) return;
      st.approving = false; st.armed = null;
      if (res) setNoticeState(res);
      render();
      if (typeof actions.refresh === "function") actions.refresh();
    }

    // ---------------------------------------------------------------- sesja: nagłówek konta, okno zgody, zmiana hasła
    function sessionInfo() {
      if (typeof deps.session !== "function") return null;
      const s = deps.session();
      return s && typeof s === "object" ? s : null;
    }
    // The key of the reason why the session of the page cannot consent now, null when it can (or the host has no sessions).
    function sessionBlock() {
      const s = sessionInfo();
      if (!s) return null;
      if (s.state !== "user") return "ui.session.needLogin";
      if (s.mustChange) return "ui.session.mustChange";
      return s.approver ? null : s.passwordSignIn ? "ui.session.passwordSignIn" : "ui.session.needRole";
    }
    function anonymous() { const s = sessionInfo(); return Boolean(s && s.state !== "user"); }
    function roleList(s) { return (Array.isArray(s.roles) ? s.roles : []).map(fbLine).filter(Boolean); }
    // The newest series of failed sign-ins of every account in `alerts` of the session (a host without the field has none).
    function latestAlerts(s) {
      const found = new Map();
      for (const item of Array.isArray(s.alerts) ? s.alerts : []) {
        if (item && typeof item.account === "string" && Number.isInteger(item.failures) && typeof item.at === "string" && !Number.isNaN(Date.parse(item.at))) found.set(item.account, item);
      }
      return Array.from(found.values());
    }
    function sessionButton(key, run, extra) {
      return h("button", Object.assign({ type: "button", class: "btn", on: { click: run } }, extra || {}), tr(key));
    }
    function loginButton(s) {
      if (typeof actions.login !== "function") return null;
      const blocked = s.loginAllowed === false;
      return sessionButton("ui.session.login", () => actions.login(), { "data-session": "login", disabled: blocked ? true : null,
        "data-tip": blocked ? tr("ui.session.needsTls") : null });
    }
    async function logout() {
      if (st.sessionBusy || typeof actions.logout !== "function") return;
      st.sessionBusy = true;
      let res = null;
      try { res = await actions.logout(); } catch { res = null; }
      if (st.destroyed) return;
      st.sessionBusy = false;
      if (!res || res.ok !== true) { setNoticeState({ ok: false, word: tr("ui.session.logout"), text: tr("ui.session.logoutFailed") }); render(); }
    }
    function renderAccount(d) {
      const s = sessionInfo();
      const account = [], bar = [];
      if (s && s.state === "anonymous") {
        account.push(h("span", { class: "chip s-planned", "data-session-state": "anonymous" }, tr("ui.session.anonymous")), loginButton(s));
      } else if (s && s.state === "user") {
        const roles = roleList(s);
        account.push(h("span", { class: "account-name", "data-session-state": "user" }, fbLine(s.name)),
          roles.map((role) => h("span", { class: `chip ${role === "approver" ? "s-active" : "s-planned"}`, "data-role": role }, role)),
          typeof actions.changePassword === "function" ? sessionButton("ui.session.changePassword", () => openPassword(), { "data-session": "password" }) : null,
          typeof actions.logout === "function" ? sessionButton("ui.session.logout", () => logout(), { "data-session": "logout" }) : null);
      }
      ui.account.hidden = !account.length;
      fill(ui.account, account);
      const single = Boolean(d && d.singleOrigin === true);
      const say = (cls, text, button) => bar.push(h("div", { class: `session-note ${cls}` }, h("span", null, text), button || null));
      if (single) say("is-warn", trRich("ui.session.singleOrigin"));   // permanent: consents from the page are off, and why
      if (s && s.state === "user" && s.approver) for (const alert of latestAlerts(s)) {
        bar.push(h("div", { class: "session-note is-warn", role: "alert", "data-session-alert": "" }, h("span", null, tr("ui.session.approverAlert",
          { account: fbLine(alert.account), failures: alert.failures, time: fmtTimeL(alert.at) }))));
      }
      if (s) {
        const ap = d && d.approvals;
        const pending = Boolean(!single && ap && ap.enabled && !isViewer(d) && (ap.actions || []).some((a) => a.state !== "after"));
        if (s.legacy) say("is-warn", trRich("ui.session.legacy"));
        if (s.state === "anonymous" && s.expired) say("is-warn", tr("ui.session.expired"), loginButton(s));
        else if (s.state === "anonymous" && pending) say("", tr(s.loginAllowed === false ? "ui.session.needsTls" : "ui.session.needLogin"), loginButton(s));
        else if (s.state === "user" && s.mustChange) say("is-warn", tr("ui.session.mustChange"),
          typeof actions.changePassword === "function" ? sessionButton("ui.session.changePassword", () => openPassword(), { "data-session": "password" }) : null);
        else if (s.state === "user" && s.passwordSignIn) say("is-warn", tr("ui.session.passwordSignIn"), loginButton(s));
        else if (s.state === "user" && !s.approver && pending) say("", tr("ui.session.needRoleBody", { roles: roleList(s).join(", ") || tr("ui.session.noRoles") }));
      }
      ui.sessionBar.hidden = !bar.length;
      fill(ui.sessionBar, bar);
    }
    // One modal window of the page (outside the parts that render() rebuilds): focus inside, Tab wraps, Escape and the backdrop cancel unless it is sending.
    // The frame of a window around its box: `<dialog>` (then it is the dialog for a reader of the screen too) or the layer over the page of before.
    function windowFrame(attrs, titleId, onCancel, box) {
      const backdrop = h("div", { class: "fb-backdrop", on: { click: onCancel } });
      if (!topLayerIn(doc)) {
        box.setAttribute("role", "dialog");
        box.setAttribute("aria-modal", "true");
        box.setAttribute("aria-labelledby", titleId);
        return h("div", attrs, backdrop, box);
      }
      return h("dialog", Object.assign({ "aria-labelledby": titleId }, attrs), backdrop, box);
    }
    // Puts a window on the page (`windowFrame`); the focus is the caller's.
    function openWindow(node, onCancel) {
      root.appendChild(node);
      if (node.localName === "dialog") showTop(node, onCancel);
    }
    function modal(name, titleKey, params, content, buttons, onCancel) {
      const titleId = name + "Title";
      const dialog = h("div", { class: "fb-dialog dlg-box", tabindex: "-1" },
        h("h3", { id: titleId }, tr(titleKey, params)), content, h("div", { class: "fb-dialog-actions" }, buttons));
      dialog.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") { onCancel(); if (typeof ev.preventDefault === "function") ev.preventDefault(); if (typeof ev.stopPropagation === "function") ev.stopPropagation(); return; }
        if (ev.key !== "Tab") return;
        const items = Array.prototype.filter.call(dialog.querySelectorAll("input, select, button"), (x) => !x.hasAttribute("disabled"));
        if (!items.length) return;
        const first = items[0], last = items[items.length - 1], at = ev.target;
        if (ev.shiftKey && at === first) { last.focus(); if (typeof ev.preventDefault === "function") ev.preventDefault(); }
        else if (!ev.shiftKey && at === last) { first.focus(); if (typeof ev.preventDefault === "function") ev.preventDefault(); }
      });
      return windowFrame({ class: "fb-modal dlg-modal", "data-dialog": name }, titleId, onCancel, dialog);
    }
    function removeNode(node) { if (node && node.parentNode) node.parentNode.removeChild(node); }
    function closeConsent(restoreFocus) {
      const c = st.consent;
      if (!c) return;
      st.consent = null;
      removeNode(c.node);
      if (restoreFocus) { const b = root.querySelector(`button[data-approve="${c.request.kind}"]`); if (b && typeof b.focus === "function") b.focus(); }
    }
    function baseOf(d) { const base = d.delivery && d.delivery.base; return typeof base === "string" ? base : ""; }
    function openConsent(a) {
      if (st.consent || st.password || st.protoDialog) return;
      const d = st.data, s = sessionInfo() || {};
      const request = consentRequest(d, a);
      const dv = d.delivery && typeof d.delivery === "object" ? d.delivery : {};
      const delivery = a.kind === "cp" || a.kind === "ready";
      const doc_ = a.kind === "spec" ? d.spec : d.plan;
      const docTitle = doc_ && typeof doc_.title === "string" ? doc_.title : "";
      const docText = delivery ? [tr("ui.consent.run", { run: a.path }), docTitle].filter(Boolean).join(" · ") : [docTitle, a.path].filter(Boolean).join(" · ");
      const wt = d.project && d.project.worktree;
      const where = wt && typeof wt.path === "string" && typeof wt.mainPath === "string" && wt.kind === "worktree" ? relativePath(wt.mainPath, wt.path) : "";
      const tabName = s.tab || (wt && typeof wt.name === "string" ? wt.name : "") || tr("ui.workdir.main");
      // READY makes the draft PR ready: its target is the base of the PR, never the working branch.
      const ready = a.kind === "ready";
      const target = (ready ? baseOf(d) : a.target || request.target || request.branch) || tr("ui.consent.none");
      const row = (key, label, value, cls) => (value ? [h("dt", null, tr(label)), h("dd", { class: cls || null, "data-consent": key }, value)] : null);
      const confirm = h("button", { type: "button", class: "fb-confirm", "data-consent-confirm": "" }, tr("ui.consent.confirm"));
      const cancelBtn = h("button", { type: "button", class: "fb-cancel", "data-consent-cancel": "" }, tr("ui.consent.cancel"));
      const c = { request, node: null, sending: false };
      const close = () => { if (!c.sending) closeConsent(true); };
      cancelBtn.addEventListener("click", close);
      confirm.addEventListener("click", async () => {
        if (c.sending || st.consent !== c || sessionBlock()) return;
        c.sending = true; st.approving = true;
        fbSetDisabled(confirm, true); fbSetDisabled(cancelBtn, true);
        confirm.textContent = tr("ui.consent.saving");
        const res = await sendConsent(request);
        if (st.destroyed) return;
        c.sending = false; st.approving = false;
        closeConsent(true);
        if (res) setNoticeState(res);
        render();
        if (typeof actions.refresh === "function") actions.refresh();
      });
      c.node = modal("consent", "ui.consent.title", { word: a.word }, [
        h("dl", { class: "fb-d-list" },
          row("word", "ui.consent.word", a.word, "mono"),
          row("doc", "ui.consent.doc", docText),
          row("tab", "ui.consent.tab", where ? `${tabName} (${where})` : tabName),
          row("sha", "ui.consent.sha", String(a.sha || "").slice(0, 7), "mono"),
          delivery && request.branch && (ready || request.branch !== target) ? row("branch", "ui.consent.branch", request.branch, "mono") : null,
          row("target", ready ? "ui.consent.prBase" : "ui.consent.target", target, "mono"),
          row("mode", "ui.consent.mode", typeof dv.mode === "string" ? dv.mode : "", "mono")),
        h("p", { class: "dlg-note" }, tr("ui.consent.note"))], [cancelBtn, confirm], close);
      st.consent = c;
      openWindow(c.node, close);
      cancelBtn.focus();   // Enter on a freshly opened window cancels, it never consents
    }
    function closePassword() {
      const p = st.password;
      if (!p) return;
      st.password = null;
      p.inputs.forEach((input) => { input.value = ""; });
      removeNode(p.node);
      const b = root.querySelector('button[data-session="password"]');
      if (b && typeof b.focus === "function") b.focus();
    }
    function openPassword() {
      const s = sessionInfo();
      if (st.password || st.consent || st.protoDialog || !s || s.state !== "user" || typeof actions.changePassword !== "function") return;
      if (st.protoFrame) { closeProtoFrame(); render(); }   // no password field beside a frame a prototype script draws in
      // The server knows the stored account (a login-link session does not show `approver` among its roles); a host without that fact falls back to the roles.
      const min = Number.isInteger(s.passwordMin) && s.passwordMin > 0 ? s.passwordMin : s.approver || roleList(s).includes("approver") ? PASSWORD_MIN_APPROVER : PASSWORD_MIN;
      const p = { node: null, inputs: [], sending: false };
      const field = (name, key, autocomplete) => {
        const input = h("input", { type: "password", id: "pw-" + name, name, autocomplete, "data-pw": name });
        p.inputs.push(input);
        return [h("label", { for: "pw-" + name }, tr(key, { min })), input];
      };
      const fields = [field("current", "ui.session.pwCurrent", "current-password"), field("next", "ui.session.pwNew", "new-password"),
        field("repeat", "ui.session.pwRepeat", "new-password")];
      const error = h("p", { class: "dlg-error", role: "alert", "data-pw-error": "" });
      const save = h("button", { type: "button", class: "fb-confirm", "data-pw-save": "" }, tr("ui.session.pwSave"));
      const cancelBtn = h("button", { type: "button", class: "fb-cancel", "data-pw-cancel": "" }, tr("ui.consent.cancel"));
      const close = () => { if (!p.sending) closePassword(); };
      const submit = async () => {
        if (p.sending || st.password !== p) return;
        const [current, next, repeat] = p.inputs.map((input) => String(input.value || ""));
        const problem = Array.from(next).length < min ? "ui.session.pwShort" : next !== repeat ? "ui.session.pwMismatch" : next === current ? "ui.session.pwSame" : null;
        if (problem) { error.textContent = tr(problem, { min }); return; }
        p.sending = true; error.textContent = "";
        fbSetDisabled(save, true); fbSetDisabled(cancelBtn, true);
        let res = null;
        try { res = await actions.changePassword({ current, next }); } catch { res = null; }
        if (st.destroyed) return;
        p.sending = false;
        if (res && res.ok === true) { closePassword(); setNoticeState({ ok: true, word: "", title: tr("ui.session.pwChanged"), text: "" }); render(); return; }
        fbSetDisabled(save, false); fbSetDisabled(cancelBtn, false);
        error.textContent = !res ? noConnection() : Object.prototype.hasOwnProperty.call(PASSWORD_ERR_KEY, res.code) ? tr(PASSWORD_ERR_KEY[res.code], { min }) : tr("ui.session.pwFailed");
      };
      save.addEventListener("click", submit);
      cancelBtn.addEventListener("click", close);
      for (const input of p.inputs) input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { if (typeof ev.preventDefault === "function") ev.preventDefault(); submit(); } });
      p.node = modal("password", "ui.session.pwTitle", {}, [h("div", { class: "dlg-fields" }, fields), error], [cancelBtn, save], close);
      st.password = p;
      openWindow(p.node, close);
      p.inputs[0].focus();
    }
    // A window stays only for what the human saw: another version, branch or target, or a lost right to consent, closes it (nothing is sent).
    function dropStaleDialogs(d) {
      const s = sessionInfo();
      if (st.password && !st.password.sending && !(s && s.state === "user")) closePassword();
      // the right to act on prototypes is gone (session, password to change, another directory): the view and the window of an action close
      if (!canManage(d) && !(st.protoDialog && st.protoDialog.sending)) { closeProtoDialog(); closeProtoDetail(); }
      if (st.protoDetail && st.protoDetail.owner !== accountKey()) closeProtoDetail();   // another account or other roles: its view and window are not these
      const c = st.consent;
      if (!c || c.sending) return;
      const a = ((d.approvals && d.approvals.actions) || []).find((x) => x.kind === c.request.kind);
      if (sessionBlock() || isViewer(d) || !canApprove(d) || d.singleOrigin === true) { closeConsent(false); return; }
      const now = a && a.state === "waiting" ? consentRequest(d, a) : null;
      if (!now || ["sha", "path", "planState", "branch", "target", "mode", "base"].some((key) => now[key] !== c.request[key])) {
        closeConsent(false);
        setNoticeState({ ok: false, word: c.request.word, text: tr("ui.consent.stale") });
      }
    }
    function wordTools(d, word) {
      const url = d.session && d.session.url;
      return url ? h("a", { class: "wait-go wait-session", href: safeUrl(url), target: "_blank", rel: "noopener", "data-tip": tr("ui.wait.openSessionTip"),
        on: { click: linkClick("session", url, url) } }, tr("ui.wait.openSession")) : null;
    }
    // Brak raportu postępu od N min (plan in_progress, sesja nie zgłosiła „czeka:”): sesja mogła się zawiesić.
    // M2 (review): a stage that is `active` keeps its state in the data (a dead session leaves `trwa:` behind), but the pulse ends with the
    // run's silence: `silence.since`/`thresholdMin` when the plan runs, else the last report time of the run with the same 35 minutes the server
    // uses for `silence` (SILENCE_MIN). Returns the minutes of silence or 0.
    const QUIET_DEFAULT_MIN = 35;
    function quietMinutes(d) {
      if (liveRuns(d).length) return 0;   // N16: a run of the journal that is not stale is the activity (the quiet returns after `end` or after the run goes stale)
      const sil = d && d.silence && d.silence.since ? d.silence : null;
      const since = sil ? sil.since : d && d.run && typeof d.run.lastReportAt === "string" ? d.run.lastReportAt : null;
      const threshold = sil && Number.isFinite(sil.thresholdMin) ? sil.thresholdMin : QUIET_DEFAULT_MIN;
      const t = since ? Date.parse(since) : NaN;
      if (!Number.isFinite(t)) return 0;
      const minutes = Math.floor((now() - t) / 60000);
      return minutes >= threshold ? minutes : 0;
    }
    function silenceMinutes(d) {
      if (liveRuns(d).length) return 0;
      const s = d && d.silence;
      if (!s || !s.since) return 0;
      const minutes = Math.floor((now() - Date.parse(s.since)) / 60000);
      return minutes >= s.thresholdMin ? minutes : 0;
    }
    // Adres strony pytań: `runUrl` (`/_experts/<slug>`, tylko poprawna ścieżka) albo lista `/_experts`; tylko gdy host zna adres serwera prototypów.
    function expertsUrl(d, runUrl) {
      const origin = protoOrigin(d);
      if (!origin) return null;
      const path = typeof runUrl === "string" && /^\/_experts\/[a-z0-9-]+$/.test(runUrl) ? runUrl : "/_experts";
      return origin + path;
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
      const text = tr("ui.expert.toast", { open, blocking });
      const to = runsAddressees(runs);
      return h("div", { class: "wait-item is-info", "data-questions-toast": "" },
        icon("i-bell"),
        h("div", { class: "wait-txt" }, h("strong", null, text), to ? h("span", { "data-addressees": "" }, to) : null),
        url ? h("a", { class: "wait-go", href: url, target: "_blank", rel: "noopener noreferrer",
          on: { click: linkClick("experts", runs.length === 1 ? String(runs[0].slug || "") : "", url) } }, tr("ui.wait.answer")) : null);
    }
    // Adresaci: "all" = wszyscy eksperci, lista loginów = „do: a, b”, pusta lista albo brak = nic.
    const loginsOf = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === "string" && x.trim()).map((x) => x.trim()) : []);
    function addresseesText(v) {
      if (v === "all") return tr("ui.expert.toAll");
      const l = loginsOf(v);
      return l.length ? tr("ui.expert.toList", { list: l.join(", ") }) : null;
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
      const text = tr("ui.comments.after", { screens: ids.length ? tr("ui.comments.screens", { ids: ids.join(", ") }, ids.length) : "" }, n);
      const slug = typeof pr.slug === "string" && pr.slug ? pr.slug : "";
      const url = slug ? safeUrl(protoUrl(d, slug)) : null;
      return h("div", { class: "wait-item is-info", "data-comments-toast": "" },
        icon("i-bell"),
        h("div", { class: "wait-txt" }, h("strong", null, text)),
        url ? h("a", { class: "wait-go", href: url, target: "_blank", rel: "noopener noreferrer", on: { click: linkClick("prototype", slug, url) } }, tr("ui.proto.open", { slug })) : null);
    }
    // Szacowany koniec (`d.eta`): wiersz w kafelku aktywnego aktu i przy aktywnym kroku (nie w pasku powiadomień), z odliczaniem na żywo. Serwer nie czyta
    // zegara; godzinę bierzemy z `untilIso` (po przesunięciu o czekanie na użytkownika, czas lokalny), bez niej z `until`. Odliczanie aktualizuje `tick()` W MIEJSCU
    // (`textContent` elementów `[data-eta-countdown]`), bez `render()`, więc fokus i animacje zostają. Tylko tekst z `h()`.
    function etaView(d) {
      const e = d && d.eta;
      if (!e || typeof e !== "object" || typeof e.operation !== "string" || !e.operation || typeof e.until !== "string") return null;
      const ms = typeof e.untilIso === "string" ? Date.parse(e.untilIso) : NaN;
      const steps = Array.isArray(e.steps) ? e.steps.filter((x) => typeof x === "string") : [];
      return { op: e.operation, ms: Number.isFinite(ms) ? ms : null, clock: Number.isFinite(ms) ? clockL(ms) : e.until, steps, paused: e.paused === true,
        shift: Number.isFinite(e.shiftSeconds) && e.shiftSeconds > 0 ? Math.floor(e.shiftSeconds) : 0 };
    }
    function etaDur(ms) {
      const s = Math.max(0, Math.floor(ms / 1000));
      if (s < 3600) return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
      return tr("ui.dur.hm", { h: Math.floor(s / 3600), m: String(Math.floor((s % 3600) / 60)).padStart(2, "0") });
    }
    function etaWaitWhat(d) {
      const w = (d.waiting || []).find((x) => x && typeof x.what === "string" && !waitDone(d, x) && !(x.who && x.who.role === "agent"));
      return w ? w.what : null;
    }
    function etaCountdown(d, v) {
      if (v.paused) { const what = etaWaitWhat(d); return what ? tr("ui.eta.paused", { what }) : tr("ui.eta.pausedUser"); }
      if (v.ms == null) return "";
      const left = v.ms - now();
      return left >= 0 ? tr("ui.eta.left", { t: etaDur(left) }) : tr("ui.eta.overBy", { t: etaDur(-left) });
    }
    function etaText(d) {   // one line for the prototype stage (`title`)
      const v = etaView(d);
      if (!v) return null;
      const late = v.ms != null && !v.paused ? Math.floor((now() - v.ms) / 60000) : 0;
      const over = late >= 60 ? tr("ui.dur.hm", { h: Math.floor(late / 60), m: late % 60 }) : tr("ui.dur.min", { n: late });
      return late >= 1 ? tr("ui.eta.over", { op: v.op, over }) : tr("ui.eta.until", { op: v.op, until: v.clock });
    }
    // Rozbieżność szacunku zakończonej serii z `d.estimates` (tylko `late`/`early`): dymek „szacunek X, było Y, czekanie na użytkownika Z” przy krokach serii.
    function stepDeviation(d, id) {
      const rows = d && Array.isArray(d.estimates) ? d.estimates : [];
      const row = rows.find((r) => r && (r.state === "late" || r.state === "early") && Array.isArray(r.steps) && r.steps.indexOf(id) >= 0
        && Number.isFinite(r.estimatedSeconds) && Number.isFinite(r.actualSeconds) && Number.isFinite(r.userWaitSeconds));
      return row ? tr("ui.eta.devTip", { est: dur(row.estimatedSeconds), act: dur(row.actualSeconds), wait: dur(row.userWaitSeconds) }) : null;
    }
    function etaLine(d, scope, short) {
      const v = etaView(d);
      if (!v) return null;
      const tip = v.shift ? tr("ui.eta.shifted", { t: dur(v.shift) }) : null;
      const text = short ? tr("ui.eta.until", { op: "", until: v.clock }).replace(/^:\s*/, "") : tr("ui.eta.until", { op: v.op, until: v.clock });
      const left = etaCountdown(d, v);
      const count = h("span", { class: "eta-count", ...(left ? { role: "timer", "aria-live": "off" } : {}), "data-eta-countdown": "", "data-eta-label": text }, left);
      if (left) count.setAttribute("aria-label", text + ", " + left);   // without a countdown (no `untilIso`) the span is plain and has neither role nor label
      const over = !v.paused && v.ms != null && now() > v.ms;
      return h("div", { class: "eta-line" + (short ? " is-short" : "") + (v.paused ? " is-paused" : "") + (over ? " is-over" : ""), "data-eta-line": scope, ...(tip ? { "data-tip": tip } : {}) },
        icon("i-clock"), h("span", { class: "eta-txt" }, text), count);
    }
    // Co sekundę (z `tick`): tylko teksty odliczania i klasa „po terminie”; `aria-label` odnawiany raz na minutę.
    function updateEtaCountdowns(d) {
      const v = etaView(d);
      if (!v || !ui.acts || !ui.acts.querySelectorAll) return;
      const text = etaCountdown(d, v);
      const minute = Math.floor(now() / 60000) + (!v.paused && v.ms != null && now() > v.ms ? "o" : "");   // the label is renewed once a minute and when the term passes
      [ui.acts, ui.phases].forEach((root) => {
        if (!root || !root.querySelectorAll) return;
        root.querySelectorAll("[data-eta-countdown]").forEach((el) => {
          if (el.textContent !== text) el.textContent = text;
          if (el.getAttribute("data-minute") !== minute) {
            el.setAttribute("data-minute", minute);
            if (text) el.setAttribute("aria-label", (el.getAttribute("data-eta-label") || "") + ", " + text);
          }
          const line = el.parentNode;
          if (line && line.getAttribute) {
            const classes = (line.getAttribute("class") || "").split(/\s+/).filter((c) => c && c !== "is-over");
            if (!v.paused && v.ms != null && now() > v.ms) classes.push("is-over");
            const next = classes.join(" ");
            if (next !== line.getAttribute("class")) line.setAttribute("class", next);
          }
        });
      });
    }
    // ---------------------------------------------------------------- biegi podumysłów i rundy review (N16)
    // `d.agents` (opcjonalne; z dziennika agents.jsonl): {running[], recent[], omitted}. Serwer nie czyta zegara, więc przeterminowanie liczy tu `now`:
    // bieg `running` jest przeterminowany, gdy teraz > max(startedAt + 45 min, etaIso + 15 min). Przeterminowany nie liczy się do „Trwa”, nie tłumi ciszy
    // i nie świeci kropki. Wartości z dziennika trafiają na stronę tylko jako tekst; brak albo uszkodzone pole = brak sekcji.
    const AGENT_STALE_START_MS = 45 * 60000, AGENT_STALE_ETA_MS = 15 * 60000;
    const REVIEW_STAGE_PROFILES = { stage_spec_review: ["spec"], stage_plan_review: ["plan"], code_review: ["code", "final"] };
    const agentClock = (iso) => (typeof iso === "string" && iso.length >= 16 ? iso.slice(11, 16) : "?");
    function agentsOf(d) {
      const a = d && d.agents && typeof d.agents === "object" && !Array.isArray(d.agents) ? d.agents : null;
      if (!a) return null;
      const list = (v) => (Array.isArray(v) ? v.filter((r) => r && typeof r === "object" && typeof r.role === "string" && Number.isFinite(Date.parse(r.startedAt))) : []);
      return { running: list(a.running), recent: list(a.recent), omitted: a.omitted && typeof a.omitted === "object" ? a.omitted : {} };
    }
    function agentStale(r) {
      const started = Date.parse(r.startedAt), eta = r.etaIso ? Date.parse(r.etaIso) : NaN;
      return now() > Math.max(started + AGENT_STALE_START_MS, Number.isFinite(eta) ? eta + AGENT_STALE_ETA_MS : -Infinity);
    }
    function liveRuns(d) {
      const a = agentsOf(d);
      return a ? a.running.filter((r) => !agentStale(r)) : [];
    }
    // The newest round of review for a stage tile (`stage_spec_review` → spec, `stage_plan_review` → plan, `code_review` → code and final); a live one before a stale one.
    function reviewRunFor(d, key) {
      const profiles = REVIEW_STAGE_PROFILES[key], a = profiles ? agentsOf(d) : null;
      if (!a) return null;
      const mine = a.running.filter((r) => r.kind === "review" && profiles.includes(r.profile));
      if (!mine.length) return null;
      const byStart = (x, y) => Date.parse(y.startedAt) - Date.parse(x.startedAt);
      const live = mine.filter((r) => !agentStale(r)).sort(byStart), stale = mine.filter(agentStale).sort(byStart);
      return live.length ? { run: live[0], stale: false } : { run: stale[0], stale: true };
    }
    // The marker next to a plan step: the rounds of review of that step that the page knows (the running ones and the recent ones of the journal).
    // The rounds of review of a step are counted from the frozen reports, like the label `Review ×N` (not from the runs shown, which are capped):
    // `code-review-<step>-N.md` (any letter case, also `-rN`); a report with a `decisions` segment is not a round.
    function stepRounds(d, id) {
      const files = d.run && Array.isArray(d.run.files) ? d.run.files : [];
      const step = String(id || "").toLowerCase();
      if (!step) return 0;
      return files.filter((f) => {
        const name = typeof f === "string" ? f.split("/").pop().toLowerCase() : "";
        return name.startsWith("code-review-" + step + "-") && /^-r?\d+\.md$/.test(name.slice(("code-review-" + step).length));
      }).length;
    }
    function agentsSig(d) {
      const a = agentsOf(d);
      if (!a) return "";
      return a.running.map((r) => {
        const eta = r.etaIso ? Date.parse(r.etaIso) : NaN;
        return [r.agent, agentStale(r) ? 1 : 0, Number.isFinite(eta) && now() - eta >= 60000 ? 1 : 0].join(":");   // the minutes past the deadline are NOT part of it: they are written in place (`updateAgentBars`)
      }).join("|") + (workingToast(d) ? "|working" : "");
    }
    function agentPct(r) {
      const started = Date.parse(r.startedAt), eta = r.etaIso ? Date.parse(r.etaIso) : NaN;
      if (!Number.isFinite(eta) || eta <= started) return null;
      return Math.max(0, Math.min(100, Math.round(((now() - started) / (eta - started)) * 100)));
    }
    // The note of a running row: the moves of the deadline and "not finished" / "the ETA was exceeded by N min" (the minutes come from the clock).
    function agentNote(r) {
      const eta = r.etaIso ? Date.parse(r.etaIso) : NaN;
      const late = Number.isFinite(eta) ? Math.floor((now() - eta) / 60000) : 0;
      const over = late >= 60 ? tr("ui.dur.hm", { h: Math.floor(late / 60), m: late % 60 }) : tr("ui.dur.min", { n: late });
      return [Number.isInteger(r.etaShifts) && r.etaShifts > 0 ? tr("ui.agents.shifted", { n: r.etaShifts }) : null,
        agentStale(r) ? tr("ui.agents.stale") : late >= 1 ? tr("ui.agents.over", { over }) : null].filter(Boolean).join(" · ");
    }
    // The elapsed-time bars and the minutes past the deadline move with the clock WITHOUT a re-render (a render would restart the bars and drop the keyboard focus): `tick` calls this.
    function updateAgentBars(d) {
      const a = agentsOf(d);
      if (!a) return;
      ui.agentsBox.querySelectorAll(".agent-bar").forEach((bar) => {
        const r = a.running.find((x) => String(x.agent || "") === bar.getAttribute("data-agent"));
        const pct = r ? agentPct(r) : null;
        if (pct == null) return;
        bar.setAttribute("aria-valuenow", String(pct));
        bar.setAttribute("aria-valuetext", tr("ui.agents.progressText", { pct }));
        const fillEl = bar.querySelector(".fill");
        if (fillEl) fillEl.style.width = pct + "%";
      });
      ui.agentsBox.querySelectorAll(".agent-note").forEach((note) => {
        const row = note.parentNode, r = row ? a.running.find((x) => String(x.agent || "") === row.getAttribute("data-agent")) : null;
        const text = r ? agentNote(r) : "";
        if (text && note.textContent !== text) note.textContent = text;
      });
    }
    function workingToast(d) {
      const live = liveRuns(d);
      const sil = d && d.silence && d.silence.since ? Math.floor((now() - Date.parse(d.silence.since)) / 60000) : 0;
      const threshold = d && d.silence && Number.isFinite(d.silence.thresholdMin) ? d.silence.thresholdMin : QUIET_DEFAULT_MIN;
      if (!live.length || !Number.isFinite(sil) || sil < threshold) return null;
      const r = live.sort((x, y) => Date.parse(y.startedAt) - Date.parse(x.startedAt))[0];
      const text = Number.isInteger(r.round) ? tr("ui.agents.working", { role: String(r.role), round: r.round, since: agentClock(r.startedAt) })
        : tr("ui.agents.workingNoRound", { role: String(r.role), since: agentClock(r.startedAt) });
      return h("div", { class: "wait-item is-info", "data-working-toast": "" }, icon("i-clock"), h("div", { class: "wait-txt" }, h("strong", null, text)));
    }
    function agentParts(r) {
      return [String(r.role), Number.isInteger(r.round) ? tr("ui.agents.round", { n: r.round }) : null, typeof r.step === "string" && r.step ? tr("ui.agents.step", { step: r.step }) : null].filter(Boolean);
    }
    function runRow(r, fills) {
      const stale = agentStale(r), eta = r.etaIso ? Date.parse(r.etaIso) : NaN;
      const late = Number.isFinite(eta) ? Math.floor((now() - eta) / 60000) : 0;
      const pct = agentPct(r);
      const text = agentParts(r).concat([tr("ui.agents.since", { since: agentClock(r.startedAt) }), typeof r.eta === "string" && r.eta ? tr("ui.agents.until", { until: r.eta }) : null]).filter(Boolean).join(" · ");
      let meter = null;
      if (pct != null && !stale) {
        const fillEl = h("span", { class: "fill", "data-w": String(pct) });
        fills.push(fillEl);
        meter = h("span", { class: "bar thin agent-bar", "data-agent": String(r.agent || ""), role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct),
          "aria-valuetext": tr("ui.agents.progressText", { pct }), "aria-label": tr("ui.agents.progressAria", { role: String(r.role) }) }, fillEl);
      }
      const note = agentNote(r);
      return h("li", { class: "agent-row" + (stale ? " is-stale" : late >= 1 ? " is-over" : ""), "data-agent": String(r.agent || "") },
        h("span", { class: "stage-dot run-dot" + (stale ? " is-stale" : ""), "aria-hidden": "true" }), h("span", { class: "agent-txt" }, text),
        meter, note ? h("span", { class: "agent-note" }, note) : null);
    }
    // A finished run: the row text opens the report (a button named "Open the report <file>: <row text>", so the visible text is part of the name); without a report it is plain text.
    function recentRow(d, r) {
      const files = d.run && Array.isArray(d.run.files) ? d.run.files : [];
      const path = typeof r.report === "string" && r.report ? files.find((f) => typeof f === "string" && f.endsWith("/" + r.report)) : null;
      const text = agentParts(r).concat([typeof r.result === "string" ? r.result : null, r.measured === false ? tr("ui.agents.untimed") : Number.isFinite(r.seconds) ? tr("ui.agents.took", { dur: dur(r.seconds) }) : null]).filter(Boolean).join(" · ");
      const tip = path ? tr("ui.agents.reportTip", { name: baseName(path) }) : "";
      return h("li", { class: "agent-row is-done", "data-agent": String(r.agent || "") }, icon("i-check"),
        path ? h("button", { class: "agent-txt agent-link", type: "button", "data-open-doc": path, "data-tip": tip, "aria-label": tip + ": " + text, on: { click: () => openDoc(path) } }, text)
          : h("span", { class: "agent-txt" }, text));
    }
    function renderAgents(d) {
      const el = ui.agentsBox, a = agentsOf(d);
      st.agentsSig = agentsSig(d);
      if (!a || (!a.running.length && !a.recent.length)) { el.hidden = true; fill(el); return; }
      const fills = [];
      const omittedRunning = Number.isFinite(a.omitted.running) ? a.omitted.running : 0, omittedRecent = Number.isFinite(a.omitted.recent) ? a.omitted.recent : 0;
      const open = st.agentsOpen == null ? a.running.length > 0 : st.agentsOpen;
      // The finished runs sit behind a switch (closed by default). Its state is the click on the switch, kept in `st.agentsShowDone`; the click changes the box in place
      // (no rebuild), so the focus stays on the button. Nothing else sets it.
      const doneList = a.recent.length ? h("ul", { class: "agents-list is-recent", id: "agentsRecent", "data-agents-recent": "", "aria-label": tr("ui.agents.recent"), ...(st.agentsShowDone ? {} : { hidden: "" }) }, a.recent.map((r) => recentRow(d, r))) : null;
      const omittedNote = h("p", { class: "agents-omitted" });
      const syncOmitted = () => { const n = omittedRunning + (st.agentsShowDone ? omittedRecent : 0); omittedNote.textContent = n ? tr("ui.agents.omitted", { n }) : ""; omittedNote.hidden = !n; };
      const doneLabel = () => (st.agentsShowDone ? tr("ui.agents.hideDone") : tr("ui.agents.showDone", { n: a.recent.length }));
      const doneToggle = a.recent.length ? h("button", { class: "agents-more", type: "button", "data-agents-toggle": "", "aria-controls": "agentsRecent", "aria-expanded": String(!!st.agentsShowDone),
        on: { click: () => { st.agentsShowDone = !st.agentsShowDone; doneList.hidden = !st.agentsShowDone; doneToggle.setAttribute("aria-expanded", String(st.agentsShowDone)); doneToggle.textContent = doneLabel(); syncOmitted(); } } }, doneLabel()) : null;
      syncOmitted();
      const box = h("details", { class: "agents-details", ...(open ? { open: "" } : {}) },
        h("summary", { ...desc("agents_box"), on: { click: () => { st.agentsOpen = !box.hasAttribute("open"); } } }, icon("i-clock"), h("h2", { id: "agentsTitle" }, tr("ui.agents.title")),
          a.running.length ? h("span", { class: "agents-count" }, tr("ui.agents.count", { n: a.running.length })) : null),
        a.running.length ? h("ul", { class: "agents-list", "data-agents-running": "" }, a.running.map((r) => runRow(r, fills))) : null,
        doneToggle, doneList, omittedNote);
      // The choice of the user is read from the click on the summary (the keyboard activates a summary with a click too): the click comes BEFORE the default action
      // flips `open`, so the new state is the opposite of the attribute now. The `toggle` event is not used: a browser fires it asynchronously also for the `open`
      // attribute that this render set, and that would be taken for a choice (the section would never fold after the runs).
      el.hidden = false;
      fill(el, box);
      if (fills.length) animateBars(fills);
    }
    // „Czeka na: <label> (login)” z waiting[].who; bez who (albo bez label) null = dotychczasowy tekst.
    function whoText(w) {
      const who = w && w.who && typeof w.who === "object" ? w.who : null;
      const label = who && typeof who.label === "string" ? who.label.trim() : "";
      if (!label) return null;
      const login = typeof who.login === "string" ? who.login.trim() : "";
      return login ? tr("ui.wait.whoLogin", { label, login }) : tr("ui.wait.who", { label });
    }
    function renderWaiting(d) {
      const el = ui.waitBanner;
      const quiet = silenceMinutes(d);
      st.silenceShown = quiet > 0;
      const items = (d.waiting || []).filter((w) => !waitDone(d, w)).concat(quiet ? [{ what: tr("ui.silence.what", { n: quiet }), short: tr("ui.silence.short", { n: quiet }), source: "silence", since: null,
        action: tr("ui.silence.action"), link: null }] : []);
      const n = st.notice;
      const note = n ? h("div", { class: "wait-item" + (n.ok ? " is-ok" : " is-err") },
        icon(n.ok ? "i-check" : "i-bell"),
        h("div", { class: "wait-txt" },
          h("strong", null, n.title ? n.title : n.ok ? tr("ui.toast.approved", { word: n.word }) : tr("ui.toast.notSaved", { word: n.word })),
          h("span", null, n.text)),
        n.ok && !n.title ? wordTools(d, "dalej") : null,
        h("button", { class: "wait-x", type: "button", "data-notice-close": "", "aria-label": tr("ui.close"), on: { click: () => { setNoticeState(null); render(); } } }, "×")) : null;
      const qnote = questionsToast(d);
      const cnote = commentsToast(d);
      const wnote = workingToast(d);
      if (!items.length && !note && !qnote && !cnote && !wnote) { el.hidden = true; fill(el); return; }
      el.hidden = false;
      const framed = frameOnScreen() && items.some((w) => w.approve)
        ? h("div", { class: "wait-item is-info", "data-frame-consents": "" }, icon("i-bell"), h("div", { class: "wait-txt" }, h("strong", null, tr("ui.proto.consentsElsewhere")))) : null;
      fill(el, wnote, qnote, cnote, note, framed, items.map((w) => {
        let link = null;
        if (w.link && w.link.kind === "doc") {
          link = h("button", { class: "wait-go", type: "button", "data-wait-doc": w.link.path, on: { click: () => openDoc(w.link.path) } }, tr("ui.wait.openDoc", { name: baseName(w.link.path) }));
        } else if (w.link && w.link.kind === "prototype") {
          const url = protoUrl(d, w.link.slug);
          link = originKnown(d) && !url ? null
            : h("a", { class: "wait-go", href: safeUrl(url), target: "_blank", rel: "noopener noreferrer", on: { click: linkClick("prototype", w.link.slug, url) } }, tr("ui.proto.open", { slug: w.link.slug }));
        } else if (w.link && w.link.kind === "expert") {
          const url = safeUrl(expertsUrl(d, w.link.url));
          link = url ? h("a", { class: "wait-go", href: url, target: "_blank", rel: "noopener noreferrer", "data-wait-expert": "",
            on: { click: linkClick("experts", w.link.slug ? String(w.link.slug) : "", url) } }, tr("ui.wait.answer")) : null;
        }
        const docRef = w.approve === "spec" ? d.spec : w.approve === "plan" ? d.plan : null;
        const act = w.approve && ((d.approvals && d.approvals.actions) || []).find((x) => x.kind === w.approve);
        const given = Boolean(docRef && ["approved", "in_progress", "done", "implemented"].includes(docRef.status)) || Boolean(act && act.state === "after");
        const tools = given ? wordTools(d, "dalej")
          : [w.approve ? approveButton(d, w.approve, "wait-go wait-approve") : null, w.word ? wordTools(d, w.word) : null];
        const action = given ? tr("ui.wait.givenAction", { word: w.word }) : w.action;
        const since = w.since ? tr("ui.wait.since", { since: w.since }) : "";
        const silence = w.source === "silence";
        return h("div", { class: "wait-item", ...desc(silence ? "banner_silence" : "banner_wait") },
          icon("i-bell"),
          h("div", { class: "wait-txt" },
            silence ? h("strong", null, tr("ui.silence.title", { what: typeof w.short === "string" ? w.short : String(w.what).replace(/^(?:brak raportu|no report) /, "") }))
              : h("strong", null, w.source === "writing" ? tr("ui.wait.headWorking", { since }) : tr("ui.wait.headWaiting", { who: whoText(w) || tr("ui.wait.sessionWaiting"), since }), inl("", w.what, w.whatHtml)),
            given ? h("span", null, action) : inl("", action, w.actionHtml),
            h("small", null, tr("ui.wait.source", { src: word(WAIT_SOURCE_KEY, w.source) || w.source, hint: tr(silence ? "ui.wait.vanishSilence" : w.source === "writing" ? "ui.wait.vanishWriting" : "ui.wait.vanishDefault") }))),
          h("div", { class: "wait-actions" }, link, tools));
      }));
    }

    // ---------------------------------------------------------------- zakładka „Prototypy”
    // Lista wszystkich prototypów (`prototypes[]`), widoczna bez logowania; zakładka powstaje w DOM dopiero, gdy jest co pokazać. „Otwórz” prowadzi na
    // OSOBNY origin prototypów (nowa karta bez `opener` i `Referer`, albo ramka z sandboxem, gdy host ma `deps.prototypeFrame`). Akceptacje i wątki:
    // osobny widok BEZ ramki, z hashami i wersją pobranymi przez `actions.loadPrototype` i wysyłanymi dokładnie tak, jak je pokazano.
    const protoRows = (d) => (Array.isArray(d && d.prototypes) ? d.prototypes : []).filter((p) => p && typeof p === "object" && typeof p.slug === "string" && p.slug);
    const frameOnScreen = () => Boolean(st.protoFrame) && st.view === "prototypes";
    const framePossible = (d) => deps.prototypeFrame === true && protoInfo(d).frame === true && d.singleOrigin !== true && Boolean(protoOrigin(d));
    function canManage(d) {
      const s = sessionInfo();
      return Boolean(d && s && s.state === "user" && !s.mustChange && d.singleOrigin !== true && !isViewer(d) && protoInfo(d).served !== false
        && typeof actions.loadPrototype === "function" && typeof actions.prototypeAction === "function");
    }
    const accountKey = () => { const s = sessionInfo(); return s ? JSON.stringify([s.state, s.login || s.name, roleList(s)]) : ""; };
    const privileged = () => { const s = sessionInfo(); return Boolean(s && roleList(s).some((role) => PROTO_PRIVILEGED.includes(role))); };
    function ensureProtoTab() {
      if (ui["tab-prototypes"]) return;
      ui.pCount = h("span", { class: "count", id: "pCount" });
      ui["tab-prototypes"] = h("button", { role: "tab", id: "tab-prototypes", "aria-controls": "view-prototypes", "aria-selected": "false",
        "data-tip": tr("ui.tab.prototypesTip"), on: { click: () => setView("prototypes") } }, icon("i-proto"), labelNode("ui.tab.prototypes", " "), ui.pCount);
      labelAttr(ui["tab-prototypes"], "data-tip", "ui.tab.prototypesTip");
      ui.protoNotes = h("div", { class: "proto-notes", id: "protoNotes" });
      ui.protoList = h("div", { class: "q-block", id: "protoList" });
      ui.protoDetail = h("div", { class: "proto-detail", id: "protoDetail", hidden: true });
      ui.protoFrames = h("div", { class: "proto-frames", id: "protoFrames" });
      ui["view-prototypes"] = h("section", { id: "view-prototypes", role: "tabpanel", "aria-labelledby": "tab-prototypes", hidden: true },
        ui.protoNotes, ui.protoFrames, ui.protoList, ui.protoDetail);
      ui.tabs.appendChild(ui["tab-prototypes"]);
      ui.main.appendChild(ui["view-prototypes"]);
    }
    function gateChip(p) {
      if (p.writing && typeof p.writing === "object") return h("span", { class: "chip s-warn q-gate", "data-gate-state": "writing" }, tr("ui.proto.gate.writing"));
      if (p.gate !== "pass" && p.gate !== "fail") return null;
      return h("span", { class: `chip ${p.gate === "pass" ? "s-active" : "s-warn"} q-gate`, "data-gate-state": p.gate }, tr(p.gate === "pass" ? "ui.proto.gate.pass" : "ui.proto.gate.fail"));
    }
    function protoRow(d, p) {
      const slug = p.slug;
      const screens = nonNeg(p.screens), accepted = nonNeg(p.accepted), stale = nonNeg(p.stale);
      const threads = p.threads && typeof p.threads === "object" ? p.threads : {};
      const openThreads = (nonNeg(threads.open) || 0) + (nonNeg(threads.awaiting) || 0);
      const wr = p.writing && typeof p.writing === "object" ? p.writing : null;
      const wDone = wr ? nonNeg(wr.screensDone) : null, wPlan = wr && Number.isFinite(wr.screensPlanned) && wr.screensPlanned > 0 ? wr.screensPlanned : null;
      const caa = p.commentsAfterAcceptance && typeof p.commentsAfterAcceptance === "object" && Number.isFinite(p.commentsAfterAcceptance.count) ? p.commentsAfterAcceptance.count : 0;
      const url = safeUrl(protoUrl(d, slug));
      const facts = [
        p.iteration != null && p.iteration !== "" ? h("span", { class: "q-fact" }, tr("ui.q.iteration", { n: p.iteration })) : null,
        screens != null ? h("span", { class: "q-fact" }, tr("ui.q.screens", { accepted: accepted || 0 }, screens)
          + (stale ? ", " + tr("ui.q.stale", { n: stale }) : "")) : null,
        wr ? h("span", { class: "chip s-active q-writing" }, tr("ui.q.writing") + (wDone != null ? " " + (wPlan ? tr("ui.q.writingOfPlan", { done: wDone, plan: wPlan }, wPlan) : tr("ui.q.writingDone", { done: wDone }, wDone)) : "")) : null,
        h("span", { class: "q-fact" }, tr("ui.q.openThreads", {}, openThreads)),
        caa > 0 ? h("span", { class: "chip s-warn q-after" }, tr("ui.q.commentsAfter", {}, caa)) : null,
        gateChip(p),
      ];
      return h("li", { class: "q-row q-proto", "data-proto": slug },
        mono(slug), typeof p.title === "string" && p.title ? h("span", { class: "q-name" }, p.title) : null, h("span", { class: "q-facts" }, facts),
        h("span", { class: "q-actions" },
          canManage(d) ? h("button", { type: "button", class: "btn", "data-proto-manage": slug, on: { click: () => openProtoDetail(slug) } }, tr("ui.proto.manage")) : null,
          url && framePossible(d) ? h("button", { type: "button", class: "btn", "data-proto-embed": slug, on: { click: () => openProtoFrame(slug) } }, tr("ui.proto.embed")) : null,
          url ? h("a", { class: "q-go", href: url, target: "_blank", rel: "noopener noreferrer", "data-proto-open": slug, "aria-label": tr("ui.proto.open", { slug }), on: { click: linkClick("prototype", slug, url) } }, tr("ui.q.openProto")) : null));
    }
    function protoNotes(d) {
      const pr = protoInfo(d), s = sessionInfo(), notes = [];
      const say = (cls, ...kids) => notes.push(h("div", { class: `session-note ${cls}` }, ...kids));
      if (d.singleOrigin === true) say("is-warn", h("span", null, tr("ui.proto.single")));
      else if (pr.served === false) say("is-warn", h("span", null, tr("ui.proto.otherTab")));
      else if (originKnown(d) && !protoOrigin(d)) say("is-warn", h("span", null, trRich("ui.proto.noOrigin")));
      else if (!originKnown(d) && pr.slug && pr.up === false) say("is-warn", h("span", null, tr("ui.proto.off", { port: pr.port })));
      if (s && s.state === "anonymous") say("", h("span", null, tr("ui.proto.anonymous")), loginButton(s));
      else if (s && s.state === "user" && !s.mustChange && d.singleOrigin !== true) {
        // a top-level navigation to the origin of the prototypes: it asks this page for a one-time code and opens a session there (never inside a frame)
        const base = originKnown(d) ? protoOrigin(d) : null;
        const url = base ? base + "/_handoff/begin" : null;
        const buttons = [
          url ? h("a", { class: "btn", href: url, target: "_blank", rel: "noopener noreferrer", "data-proto-handoff": "", "data-tip": tr("ui.proto.handoffTip"),
            on: { click: linkClick("handoff", "", url) } }, tr("ui.proto.handoff")) : null,
          canManage(d) && privileged() && !st.protoFrame ? h("button", { type: "button", class: "btn", "data-proto-project": "", on: { click: () => openProtoDetail(null) } }, tr("ui.proto.acceptProject")) : null,
        ];
        if (buttons.some(Boolean)) say("", buttons);
      }
      return notes;
    }
    // The list of the tab is rebuilt with every status: the button that had the keyboard focus gets it back (by what it is, not by its node).
    const PROTO_FOCUS = ["data-proto-manage", "data-proto-embed", "data-proto-open", "data-proto-handoff", "data-proto-project", "data-session"];
    function protoFocusKey(t) {
      const view = ui["view-prototypes"];
      if (!view || !t || typeof t.getAttribute !== "function" || !view.contains(t)) return null;
      const name = PROTO_FOCUS.find((attr) => t.getAttribute(attr) !== null);
      return name ? [name, t.getAttribute(name)] : null;
    }
    function restoreProtoFocus(key) {
      if (!key || st.view !== "prototypes" || st.consent || st.password || st.protoDialog) return;
      const found = Array.prototype.find.call(ui["view-prototypes"].querySelectorAll("[" + key[0] + "]"), (x) => x.getAttribute(key[0]) === key[1]);
      if (!found || (st.protoDetail && ui.protoList.contains(found)) || typeof found.focus !== "function") return;
      st.protoFocus = key;
      found.focus({ preventScroll: true });
    }
    function renderPrototypesTab(d) {
      const rows = protoRows(d);
      const focused = st.protoFocus;   // read before the rebuild: removing the focused button reports a lost focus
      // `P` opens the prototypes when there are any: the tip of the progress tab names the key only while it still leads there
      ui["tab-progress"].setAttribute("data-tip", tr(rows.length ? "ui.tab.progressTipPlain" : "ui.tab.progressTip"));
      if (!rows.length) {
        closeProtoFrame(); closeProtoDetail();
        if (!ui["tab-prototypes"]) return;
        ui["tab-prototypes"].hidden = true; ui["view-prototypes"].hidden = true;
        fill(ui.protoNotes); fill(ui.protoList); ui.pCount.textContent = "";
        if (st.view === "prototypes") setView("progress", false);
        return;
      }
      ensureProtoTab();
      ui["tab-prototypes"].hidden = false;
      ui.pCount.textContent = String(rows.length);
      const f = st.protoFrame;
      if (f && (!framePossible(d) || !rows.some((p) => p.slug === f.slug) || protoUrl(d, f.slug) !== f.url)) closeProtoFrame();
      const dt = st.protoDetail;
      if (dt && dt.slug && !rows.some((p) => p.slug === dt.slug)) closeProtoDetail();
      fill(ui.protoNotes, protoNotes(d));
      fill(ui.protoList, h("ul", { class: "q-list" }, rows.map((p) => protoRow(d, p))));
      ui.protoList.hidden = Boolean(st.protoDetail);
      ui["view-prototypes"].hidden = st.view !== "prototypes";
      restoreProtoFocus(focused);
    }
    function openProtoFrame(slug) {
      const d = st.data, url = d ? protoUrl(d, slug) : null;
      if (!url || !framePossible(d) || st.protoDialog) return;
      if (st.consent) { if (st.consent.sending) return; closeConsent(false); }
      closeProtoDetail(); closeProtoFrame();
      const node = h("div", { class: "proto-frame", "data-proto-frame": slug },
        h("div", { class: "proto-frame-head" }, mono(url),
          h("button", { type: "button", class: "btn", "data-proto-frame-close": "", on: { click: () => { closeProtoFrame(); render(); } } }, tr("ui.proto.frameClose"))),
        h("iframe", { sandbox: PROTO_SANDBOX, referrerpolicy: "no-referrer", title: tr("ui.proto.frameTitle", { slug }), src: url }));
      st.protoFrame = { slug, url, node };
      ui.protoFrames.appendChild(node);
      render();
    }
    function closeProtoFrame() {
      const f = st.protoFrame;
      if (!f) return;
      st.protoFrame = null;
      removeNode(f.node);
    }
    function closeProtoDetail() {
      if (!st.protoDetail) return;
      st.protoDetail = null; st.protoSeq += 1; st.protoRefocus = null;
      if (st.protoDialog && !st.protoDialog.sending) closeProtoDialog();
      fill(ui.protoDetail); ui.protoDetail.hidden = true; ui.protoList.hidden = false;
    }
    // `slug` null = the whole project. The frame is closed first: the buttons of an acceptance are never drawn beside it.
    function openProtoDetail(slug) {
      if (!st.data || !canManage(st.data)) return;
      closeProtoFrame(); closeProtoDetail();
      st.protoDetail = { slug, owner: accountKey(), state: "loading", data: null, error: "" };
      render();
      loadProtoDetail();
    }
    async function loadProtoDetail() {
      const dt = st.protoDetail;
      if (!dt) return;
      const seq = ++st.protoSeq;
      dt.state = "loading"; dt.data = null;
      renderProtoDetail();
      let res = null;
      try { res = await actions.loadPrototype({ slug: dt.slug }); } catch { res = null; }
      if (st.destroyed || st.protoDetail !== dt || seq !== st.protoSeq) return;   // another view was opened meanwhile
      if (res && res.ok === true && res.data && typeof res.data === "object") { dt.state = "ready"; dt.data = res.data; }
      else { dt.state = "error"; dt.error = !res ? noConnection() : protoRefusal(res); }
      renderProtoDetail();
    }
    const protoRefusal = (res) => (Object.prototype.hasOwnProperty.call(PROTO_ERR_KEY, res.code) ? tr(PROTO_ERR_KEY[res.code]) : String(res.text || ""));
    const short = (value) => String(value || "").slice(0, 12);
    const objects = (value) => (Array.isArray(value) ? value : []).filter((x) => x && typeof x === "object");
    function renderProtoDetail() {
      const dt = st.protoDetail;
      if (!dt) return;
      ui.protoDetail.hidden = false;
      ui.protoList.hidden = true;
      const head = h("div", { class: "proto-detail-head" },
        h("button", { type: "button", class: "btn", "data-proto-back": "", on: { click: () => { closeProtoDetail(); render(); } } }, tr("ui.proto.back")),
        h("h2", { class: "q-group-title" }, dt.slug ? mono(dt.slug) : tr("ui.proto.projectTitle"), " ",
          dt.data && typeof dt.data.title === "string" ? h("span", { class: "q-name" }, dt.data.title) : null),
        dt.state === "loading" ? null : h("button", { type: "button", class: "btn", "data-proto-reload": "", on: { click: () => { if (!st.protoDialog) loadProtoDetail(); } } }, tr("ui.proto.reload")));
      if (dt.state !== "ready") {
        fill(ui.protoDetail, head, h("div", { class: "empty", role: "status" }, dt.state === "loading" ? tr("ui.proto.loading") : dt.error));
        if (dt.state !== "loading") refocusProtoAction();
        return;
      }
      fill(ui.protoDetail, head, dt.slug ? protoScreens(dt) : protoProject(dt));
      refocusProtoAction();
    }
    // The button of an action (`[action, target]`) of the view on the screen, else the way back to the list.
    function focusProtoAction(key) {
      if (!st.protoDetail || !key) return;
      const found = Array.prototype.find.call(ui.protoDetail.querySelectorAll("[data-proto-act]"),
        (b) => b.getAttribute("data-proto-act") === key[0] && (b.getAttribute("data-proto-target") || "") === key[1]);
      const to = found || ui.protoDetail.querySelector("[data-proto-back]");
      if (to && typeof to.focus === "function") to.focus({ preventScroll: true });
    }
    function refocusProtoAction() {   // after a write: the view was read again, the focus goes back to where the action started
      const key = st.protoRefocus;
      st.protoRefocus = null;
      focusProtoAction(key);
    }
    const actButton = (action, onClick, extra) => h("button", Object.assign({ type: "button", class: "btn", "data-proto-act": action, on: { click: onClick } }, extra || {}), tr(PROTO_ACTION_KEY[action]));
    const warnNote = (text) => h("div", { class: "session-note is-warn" }, h("span", null, text));
    function protoProject(dt) {
      const rows = objects(dt.data.prototypes);
      const writing = rows.some((p) => p.writing && p.format !== 1);
      const versions = {};
      rows.forEach((p) => { if (typeof p.slug === "string") versions[p.slug] = typeof p.version === "string" ? p.version : null; });
      const shown = rows.map((p) => `${p.slug} ${p.version ? short(p.version) : "—"}`).join(", ");
      return [
        writing ? warnNote(tr("ui.proto.writingBlocks")) : null,
        h("ul", { class: "q-list" }, rows.map((p) => h("li", { class: "q-row", "data-project-proto": String(p.slug) },
          mono(String(p.slug)), h("span", { class: "q-name" }, String(p.title || "")),
          h("span", { class: "q-facts" }, h("span", { class: "q-fact" }, tr("ui.proto.screensCount", {}, nonNeg(p.screens) || 0)),
            h("span", { class: "q-fact" }, tr("ui.proto.version", { version: p.version ? short(p.version) : "—" })),
            p.writing ? h("span", { class: "chip s-active q-writing" }, tr("ui.q.writing")) : null)))),
        h("div", { class: "q-actions" }, actButton("project/accept", () => openProtoDialog({ action: "project/accept", target: "", body: { versions },
          rows: [["versions", "ui.proto.row.prototypes", shown]] }), { disabled: writing || !rows.length ? true : null })),
      ];
    }
    function protoScreens(dt) {
      const data = dt.data, can = data.can && typeof data.can === "object" ? data.can : {};
      const writing = Boolean(data.writing);
      const iterations = (Array.isArray(data.iterations) ? data.iterations : []).filter((n) => Number.isInteger(n));
      const slug = dt.slug;
      const versionRow = ["version", "ui.proto.row.version", short(data.version)];
      const screens = objects(data.screens), threads = objects(data.threads), problems = objects(data.problems);
      const screenRow = (s) => {
        const id = String(s.id);
        const [stateKey, cls] = Object.prototype.hasOwnProperty.call(PROTO_SCREEN_KEY, s.state) ? PROTO_SCREEN_KEY[s.state] : PROTO_SCREEN_KEY.pending;
        const rows = [["screen", "ui.proto.row.screen", [id, s.title].filter(Boolean).join(" · ")], versionRow];
        return h("li", { class: "q-row", "data-screen": id },
          mono(id), h("span", { class: "q-name" }, String(s.title || "")),
          h("span", { class: "q-facts" }, h("span", { class: `chip ${cls}` }, tr(stateKey)),
            s.acceptedBy ? h("span", { class: "q-fact" }, tr("ui.proto.acceptedBy", { who: s.acceptedBy })) : null,
            h("span", { class: "q-fact" }, tr("ui.proto.hash", { hash: short(s.hash) }))),
          h("span", { class: "q-actions" },
            can.accept === true && s.state !== "accepted" && !writing ? actButton("accept", () => openProtoDialog({ action: "accept", target: id, slug,
              body: { screens: { [id]: s.hash }, version: data.version }, rows: rows.concat([["hash", "ui.proto.row.hash", short(s.hash)]]) }), { "data-proto-target": id }) : null,
            s.canUnaccept === true ? actButton("unaccept", () => openProtoDialog({ action: "unaccept", target: id, slug, body: { screens: [id] }, rows }), { "data-proto-target": id }) : null,
            can.restore === true && iterations.length && !writing ? actButton("restore", () => openProtoDialog({ action: "restore", target: id, slug,
              body: { screen: id, version: data.version }, rows, iterations }), { "data-proto-target": id }) : null));
      };
      const threadRow = (t) => {
        const id = String(t.id);
        const rows = [["thread", "ui.proto.row.thread", [t.screen, t.text].filter(Boolean).join(" · ")]];
        return h("li", { class: "q-row", "data-thread": id },
          h("span", { class: "q-head" }, mono(String(t.screen || "—")), h("span", { class: "chip s-planned" }, String(t.status || "")),
            h("span", { class: "q-run" }, String(t.author || "")), h("span", { class: "q-age" }, fmtAge(t.at))),
          h("span", { class: "q-text" }, String(t.text || "")),
          h("span", { class: "q-actions" },
            t.canResolve === true ? actButton("resolve", () => openProtoDialog({ action: "resolve", target: id, slug, body: { threadId: id }, rows }), { "data-proto-target": id }) : null,
            t.canDelete === true ? actButton("delete", () => openProtoDialog({ action: "delete", target: id, slug, body: { threadId: id }, rows }), { "data-proto-target": id }) : null));
      };
      return [
        h("div", { class: "q-facts" }, data.iteration != null ? h("span", { class: "q-fact" }, tr("ui.q.iteration", { n: data.iteration })) : null,
          data.version ? h("span", { class: "q-fact", "data-proto-version": "" }, tr("ui.proto.version", { version: short(data.version) })) : null),
        data.format === 1 ? warnNote(trRich("ui.proto.format1")) : null,
        writing ? warnNote(tr("ui.proto.writingBlocks")) : null,
        problems.length ? warnNote(tr("ui.proto.problems", { list: problems.map((x) => `${x.screen || "—"}: ${x.problem}`).join(", ") })) : null,
        data.sourceWrites === false ? h("div", { class: "session-note" }, h("span", null, tr("ui.proto.noSourceWrites"))) : null,
        h("h3", { class: "q-title" }, tr("ui.proto.screens")),
        screens.length ? h("ul", { class: "q-list" }, screens.map(screenRow)) : h("div", { class: "empty" }, tr("ui.proto.noScreens")),
        h("h3", { class: "q-title" }, tr("ui.proto.threads")),
        threads.length ? h("ul", { class: "q-list" }, threads.map(threadRow)) : h("div", { class: "empty" }, tr("ui.proto.noThreads")),
        nonNeg(data.threadsMore) ? h("div", { class: "q-more" }, tr("ui.q.more", { n: data.threadsMore })) : null,
      ];
    }
    function closeProtoDialog(restoreFocus) {
      const w = st.protoDialog;
      if (!w) return;
      st.protoDialog = null;
      removeNode(w.node);
      if (restoreFocus) focusProtoAction(w.back);
    }
    // The window of one action: what it does, to what, in which tab; one click, no password. It sends the values of the view it was opened from.
    function openProtoDialog({ action, target, slug, body, rows, iterations }) {
      if (st.protoDialog || st.consent || st.password || !st.data || !canManage(st.data)) return;
      const s = sessionInfo() || {}, wt = st.data.project && st.data.project.worktree;
      const tabName = s.tab || (wt && typeof wt.name === "string" ? wt.name : "") || tr("ui.workdir.main");
      const w = { node: null, sending: false, back: [action, target] };
      const cid = `page-${Math.floor(now()).toString(36)}-${Math.random().toString(36).slice(2, 10)}`;   // one id per window: a repeated send writes once
      const row = ([key, label, value]) => (value ? [h("dt", null, tr(label)), h("dd", { "data-proto-row": key }, String(value))] : null);
      let from = null, reason = null;
      if (action === "restore") {
        from = h("select", { id: "protoFrom", "data-proto-from": "" }, iterations.map((n) => h("option", { value: n }, String(n))));
        from.value = String(iterations[iterations.length - 1]);
        reason = h("input", { type: "text", id: "protoReason", maxlength: "500", "data-proto-reason": "" });
      }
      const error = h("p", { class: "dlg-error", role: "alert", "data-proto-error": "" });
      const confirm = h("button", { type: "button", class: "fb-confirm", "data-proto-confirm": "" }, tr("ui.consent.confirm"));
      const cancelBtn = h("button", { type: "button", class: "fb-cancel", "data-proto-cancel": "" }, tr("ui.consent.cancel"));
      const close = () => { if (!w.sending) closeProtoDialog(true); };
      cancelBtn.addEventListener("click", close);
      confirm.addEventListener("click", async () => {
        if (w.sending || st.protoDialog !== w || !canManage(st.data)) return;
        const request = Object.assign({ cid }, body);
        if (action === "restore") {
          const why = String(reason.value || "").trim();
          if (!why) { error.textContent = tr("ui.proto.reasonRequired"); return; }
          Object.assign(request, { from: Number(from.value), reason: why });
        }
        w.sending = true;
        fbSetDisabled(confirm, true); fbSetDisabled(cancelBtn, true);
        confirm.textContent = tr("ui.consent.saving");
        let res = null;
        try { res = await actions.prototypeAction({ slug: slug || null, action, body: request }); } catch { res = null; }
        if (st.destroyed) return;
        w.sending = false;
        closeProtoDialog();
        const ok = Boolean(res && res.ok === true), what = tr(PROTO_ACTION_KEY[action]);
        setNoticeState({ ok, word: what, title: tr(ok ? "ui.proto.done" : "ui.proto.refused", { action: what }), text: ok ? "" : res ? protoRefusal(res) : noConnection() });
        render();
        if (st.protoDetail) { st.protoRefocus = w.back; loadProtoDetail(); }   // whatever the answer: what is shown next is what the server has now
        if (typeof actions.refresh === "function") actions.refresh();
      });
      w.node = modal("proto-action", PROTO_ACTION_KEY[action], {}, [
        h("dl", { class: "fb-d-list" },
          row(["proto", "ui.proto.row.prototype", slug || tr("ui.proto.projectTitle")]), rows.map(row), row(["tab", "ui.consent.tab", tabName])),
        action === "restore" ? h("div", { class: "dlg-fields" }, h("label", { for: "protoFrom" }, tr("ui.proto.from")), from,
          h("label", { for: "protoReason" }, tr("ui.proto.reason")), reason) : null,
        h("p", { class: "dlg-note" }, tr("ui.proto.note")), error], [cancelBtn, confirm], close);
      st.protoDialog = w;
      openWindow(w.node, close);
      cancelBtn.focus();   // Enter on a freshly opened window cancels, it never acts
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
            h("summary", null, n ? tr("ui.result.more", {}, n) : tr("ui.result.moreNoCount")), restNode);
        }
      } else {
        const lines = r.text.replace(/\s+$/, "").split(/\r?\n/);
        const head = lines.slice(0, RESULT_VISIBLE_LINES).join("\n"), rest = lines.slice(RESULT_VISIBLE_LINES).join("\n");
        headNode = h("pre", { class: "result-text", "data-result-head": "" }, head);
        if (lines.length > RESULT_VISIBLE_LINES) {
          more = h("details", { class: "result-more", open: Boolean(st.resultOpen) },
            h("summary", null, tr("ui.result.more", {}, lines.length - RESULT_VISIBLE_LINES)),
            h("pre", { class: "result-text", "data-result-rest": "" }, rest));
        }
      }
      if (more) more.addEventListener("toggle", () => { st.resultOpen = more.hasAttribute("open"); });
      el.hidden = false;
      fill(el,
        h("h2", { id: "resultTitle", ...desc("legend_result") }, icon("i-flag"), tr("ui.result.title")),
        typeof r.path === "string" && r.path ? h("div", { class: "result-path" }, trRich("ui.result.path", { path: r.path })) : null,
        headNode, more);
    }

    // ---------------------------------------------------------------- zgłoszenia do warstw (FB3): kafelki szkiców i okno zgody
    // `d.feedback` = {enabled, total, drafts[]}: sekcja istnieje tylko przy enabled i total > 0. Element pełny: {draft, state, sha, sha12, generic, given, reason, view};
    // rola tylko do odczytu dostaje {draft, state}. Przycisk zgody tylko dla stanu `waiting` z podglądem (`view`), roli z prawem zgody i hosta z `actions.feedbackApprove`.
    const fbCanConsent = (d) => typeof actions.feedbackApprove === "function" && canApprove(d) && !isViewer(d) && !sessionBlock();
    const fbTarget = (t) => { const o = t && typeof t === "object" ? t : {}; return [o.organization, o.project, o.area, o.type].map(fbLine).filter(Boolean).join(" / "); };
    const fbMode = (v) => (v.mode === "comment" ? tr("ui.feedback.modeComment", { item: fbLine(v.item) }) : tr("ui.feedback.modeCreate"));
    const fbDraftButton = (draft) => Array.prototype.find.call(root.querySelectorAll("button.fb-approve"), (b) => b.getAttribute("data-draft") === draft) || null;
    function fbSetDisabled(el, on) { if (!el) return; if (on) el.setAttribute("disabled", ""); else el.removeAttribute("disabled"); }
    function setFbResult(text) { st.fb.result = text; ui.fbResult.textContent = text; }
    function closeFbDialog(restoreFocus) {
      const f = st.fb, dlg = f.dialog;
      if (!dlg) return;
      f.dialog = null;
      if (dlg.node.parentNode) dlg.node.parentNode.removeChild(dlg.node);
      if (restoreFocus) { const b = fbDraftButton(dlg.draft); if (b && typeof b.focus === "function") b.focus(); }
    }
    function openFbDialog(e) {
      const f = st.fb;
      if (f.dialog || f.sending || !e || typeof e.sha !== "string" || !e.view) return;
      const v = e.view, generic = e.generic === true;
      const dlg = { draft: fbLine(e.draft), sha: e.sha, generic, node: null };
      const ack = generic ? h("input", { type: "checkbox", class: "fb-ack", id: "fbAck" }) : null;
      const confirm = h("button", { type: "button", class: "fb-confirm" }, tr("ui.feedback.confirm"));
      const cancel = h("button", { type: "button", class: "fb-cancel" }, tr("ui.feedback.cancel"));
      const sync = () => { const blocked = f.sending || (generic && !(ack && ack.checked === true)); fbSetDisabled(confirm, blocked); };
      const send = async () => {
        if (f.sending || !f.dialog || f.dialog !== dlg) return;
        if (generic && !(ack && ack.checked === true)) return;
        f.sending = true; sync(); fbSetDisabled(cancel, true);
        confirm.textContent = tr("ui.feedback.saving");
        let res = null;
        try { res = await actions.feedbackApprove({ draft: dlg.draft, sha: dlg.sha }); } catch { res = null; }
        if (st.destroyed) return;
        f.sending = false;
        closeFbDialog(true);
        if (res && typeof res === "object" && res.ok === true) setFbResult(tr("ui.feedback.saved", { draft: dlg.draft, sha: fbLine(res.sha12 || dlg.sha.slice(0, 12)) }));
        else if (res && typeof res === "object") setFbResult(tr("ui.feedback.refused", { text: fbLine(res.text) }));
        else setFbResult(noConnection());
        if (res && typeof res === "object" && typeof actions.refresh === "function") actions.refresh();
      };
      confirm.addEventListener("click", send);
      cancel.addEventListener("click", () => { if (!f.sending) closeFbDialog(true); });
      if (ack) for (const type of ["click", "input", "change"]) ack.addEventListener(type, sync);
      const row = (cls, label, value) => [h("dt", null, label), h("dd", { class: cls }, value)];
      const dialog = h("div", { class: "fb-dialog", tabindex: "-1" },
        h("h3", { id: "fbDlgTitle" }, tr("ui.feedback.dialogTitle", { draft: dlg.draft })),
        h("dl", { class: "fb-d-list" },
          row("fb-d-sha mono", tr("ui.feedback.dSha"), fbLine(e.sha12 || e.sha.slice(0, 12))),
          row("fb-d-draft mono", tr("ui.feedback.dDraft"), dlg.draft),
          row("fb-d-target", tr("ui.feedback.dTarget"), fbTarget(v.target)),
          row("fb-d-mode", tr("ui.feedback.dMode"), fbMode(v))),
        generic ? h("p", { class: "fb-generic-warning" }, tr("ui.feedback.genericWarning")) : null,
        generic ? h("label", { class: "fb-ack-row", for: "fbAck" }, ack, h("span", null, tr("ui.feedback.ack"))) : null,
        h("div", { class: "fb-dialog-actions" }, cancel, confirm));
      dialog.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") { if (!f.sending) closeFbDialog(true); if (typeof ev.preventDefault === "function") ev.preventDefault(); if (typeof ev.stopPropagation === "function") ev.stopPropagation(); return; }
        if (ev.key !== "Tab") return;
        const items = Array.prototype.filter.call(dialog.querySelectorAll("input, button"), (x) => !x.hasAttribute("disabled"));
        if (!items.length) return;
        const first = items[0], last = items[items.length - 1], at = ev.target;
        if (ev.shiftKey && at === first) { last.focus(); if (typeof ev.preventDefault === "function") ev.preventDefault(); }
        else if (!ev.shiftKey && at === last) { first.focus(); if (typeof ev.preventDefault === "function") ev.preventDefault(); }
      });
      const dismiss = () => { if (!f.sending) closeFbDialog(true); };
      dlg.node = windowFrame({ class: "fb-modal" }, "fbDlgTitle", dismiss, dialog);
      f.dialog = dlg;
      sync();
      openWindow(dlg.node, dismiss);
      (ack || cancel).focus();
    }
    function fbCard(e, d, consent) {
      const draft = fbLine(e.draft), state = fbLine(e.state);
      const v = e.view && typeof e.view === "object" ? e.view : null;
      const label = FB_STATE_KEY[state] ? tr(FB_STATE_KEY[state]) : "";
      const sha12 = typeof e.sha12 === "string" ? fbLine(e.sha12) : "";
      const parts = [h("div", { class: "fb-card-head" }, h("span", { class: "fb-id mono" }, draft), label ? h("span", { class: "fb-state" }, label) : null,
        sha12 ? h("code", { class: "fb-sha mono" }, sha12) : null)];
      if (state === "stale") {
        parts.push(h("p", { class: "fb-stale" }, trRich("ui.feedback.stale", { draft })));
        if (e.reason) parts.push(h("p", { class: "fb-reason" }, tr("ui.feedback.reason", { reason: fbLine(e.reason) })));
      } else if (state === "blocked") {
        parts.push(h("p", { class: "fb-reason" }, tr("ui.feedback.reason", { reason: fbLine(e.reason) })));
      } else if (state === "after") {
        const g = e.given && typeof e.given === "object" ? e.given : null;
        if (g) {
          const at = g.at ? new Date(g.at) : null;
          const time = at && !Number.isNaN(at.getTime()) ? at.toLocaleTimeString(L.locale, { hour: "2-digit", minute: "2-digit" }) : "";
          parts.push(h("p", { class: "fb-given" }, tr("ui.feedback.given", { time, source: fbLine(g.source) })));
          parts.push(h("p", { class: "fb-cmd" }, trRich("ui.feedback.sendCmd", { draft })));
        }
      } else if (state === "waiting" && sha12 && !v) {
        parts.push(h("p", { class: "fb-stale" }, trRich("ui.feedback.noView", { draft })));
      } else if (state === "waiting" && v) {
        const scan = v.scan && typeof v.scan === "object" ? v.scan : {};
        const warns = (Array.isArray(scan.warnings) ? scan.warnings : []).map(fbLine).filter(Boolean);
        const tags = (Array.isArray(v.tags) ? v.tags : []).map(fbLine);
        parts.push(h("p", { class: "fb-mode" }, fbMode(v)));
        if (v.mode === "comment" && v.item_title) parts.push(h("p", { class: "fb-quote" }, tr("ui.feedback.quote", { title: fbLine(v.item_title) })));
        parts.push(h("p", { class: "fb-target" }, tr("ui.feedback.target", { target: fbTarget(v.target) })));
        parts.push(h("h3", { class: "fb-title" }, fbLine(v.title)));
        if (tags.length) parts.push(h("ul", { class: "fb-tags", "aria-label": tr("ui.feedback.tagsLabel") }, tags.map((t) => h("li", null, t))));
        parts.push(h("pre", { class: "fb-body", tabindex: "0", "aria-label": tr("ui.feedback.bodyLabel", { draft }) }, fbBody(v.body)));
        parts.push(h("p", { class: "fb-scan" }, tr("ui.feedback.scan", { status: fbLine(scan.status), coverage: fbLine(scan.coverage) }),
          warns.length ? " " + tr("ui.feedback.scanWarnings", { warnings: warns.join("; ") }) : "",
          v.warning ? " " + tr("ui.feedback.warning", { warning: fbLine(v.warning) }) : ""));
        if (consent && typeof e.sha === "string") {
          parts.push(h("div", { class: "fb-actions" }, h("button", { type: "button", class: "fb-approve", "data-draft": draft,
            "aria-label": tr("ui.feedback.approveAria", { draft }), on: { click: () => openFbDialog(e) } }, icon("i-check"), tr("ui.feedback.approve"))));
        }
      }
      return h("article", { class: "fb-card", "data-draft": draft, "data-state": state }, parts);
    }
    function renderFeedback(d) {
      const fb = d.feedback && typeof d.feedback === "object" ? d.feedback : null;
      const total = fb && Number.isFinite(fb.total) ? fb.total : 0;
      if (!fb || fb.enabled !== true || total <= 0) {
        closeFbDialog(false);
        if (ui.fbSection.parentNode) ui.fbSection.parentNode.removeChild(ui.fbSection);
        st.fb.sig = null;
        return;
      }
      const drafts = (Array.isArray(fb.drafts) ? fb.drafts : []).filter((x) => x && typeof x === "object");
      const consent = fbCanConsent(d);
      const dlg = st.fb.dialog;
      if (dlg) {   // the dialog stays only for the very version that the human saw
        const e = drafts.find((x) => fbLine(x.draft) === dlg.draft);
        if (!(e && e.state === "waiting" && e.view && e.sha === dlg.sha && consent) && !st.fb.sending) closeFbDialog(false);
      }
      // an unchanged status (the periodic refresh) does not rebuild the tiles: the keyboard focus and the scroll position of a long body stay
      const sig = JSON.stringify([L.lang, L.texts ? 1 : 0, consent, total, fb.drafts]);
      if (sig === st.fb.sig && ui.fbSection.parentNode === ui.fbHost) return;
      st.fb.sig = sig;
      if (ui.fbSection.parentNode !== ui.fbHost) ui.fbHost.appendChild(ui.fbSection);
      ui.fbCount.textContent = tr("ui.feedback.count", {}, total);
      const cards = drafts.map((e) => fbCard(e, d, consent));
      const older = cards.slice(FB_VISIBLE);
      let more = null;
      if (older.length) {
        more = h("details", { class: "fb-older", open: Boolean(st.fb.olderOpen) }, h("summary", null, tr("ui.feedback.older", { n: older.length })), older);
        more.addEventListener("toggle", () => { st.fb.olderOpen = more.hasAttribute("open"); });
      }
      fill(ui.fbList, cards.slice(0, FB_VISIBLE), more);
    }
    // Legenda pojęć (osobna od `legend(p)`, legendy paska): zawsze obecna, zwinięta; teksty z descriptions.json, awaryjnie z renderera.
    // Pary (klucz opisu, id w katalogu: `ui.glossary.<id>Title` i `...Text`).
    const GLOSSARY = [["legend_act", "act"], ["legend_stage", "stage"], ["legend_phase", "phase"], ["legend_step", "step"], ["legend_consent", "consent"],
      ["legend_result", "result"], ["legend_debt", "debt"], ["legend_eta", "eta"], ["legend_verify", "verify"]];
    function renderGlossary() {
      const map = st.desc || {};
      const box = h("details", { class: "glossary", open: Boolean(st.glossaryOpen) },
        h("summary", null, icon("i-doc"), tr("ui.glossary.title")),
        h("dl", { class: "glossary-list" }, GLOSSARY.map(([key, id]) => {
          const title = tr(`ui.glossary.${id}Title`), text = tr(`ui.glossary.${id}Text`);
          const item = map[key] && typeof map[key] === "object" ? map[key] : null;
          return h("div", { "data-glossary": key },
            h("dt", null, item && item.title ? item.title : title), h("dd", null, item && item.text ? item.text : text));
        })));
      box.addEventListener("toggle", () => { st.glossaryOpen = box.hasAttribute("open"); });
      fill(ui.glossary, box);
    }

    // ---------------------------------------------------------------- zakładka „Pytania” (do realizacji i do eksperta; prototypy mają własną zakładkę)
    const Q_STATE_KEY = { open: "ui.qState.open", answered: "ui.qState.answered", withdrawn: "ui.qState.withdrawn" };
    const Q_TEXT_MAX = 160;
    const nonNeg = (v) => (Number.isFinite(v) && v >= 0 ? v : null);
    function fmtAge(iso) {
      const t = typeof iso === "string" ? Date.parse(iso) : NaN;
      if (!Number.isFinite(t)) return "—";
      const s = Math.max(0, Math.round((now() - t) / 1000));
      if (s < 10) return tr("ui.ago.justNow");
      if (s < 60) return tr("ui.ago.sec", { n: s });
      if (s < 3600) return tr("ui.ago.min", { n: Math.floor(s / 60) });
      if (s < 86400) return tr("ui.ago.hour", { n: Math.floor(s / 3600) });
      return tr("ui.ago.day", { n: Math.floor(s / 86400) });
    }
    // Zakładka „Pytania” ma dwie grupy: do realizacji (pytania blokujące i założenia ze speca widocznego przebiegu) oraz do eksperta
    // domenowego (`expertQuestions.current`: pytania przebiegu). Widoczna, gdy którakolwiek grupa ma treść.
    // `expertView`: obiekt albo null (ukryta grupa eksperta). Brak klucza `current` (starszy serwer): przybliżenie lokalne po `prototype.slug`.
    function expertView(d) {
      const eq = d.expertQuestions && typeof d.expertQuestions === "object" ? d.expertQuestions : null;
      const objs = (v) => (Array.isArray(v) ? v : []).filter((x) => x && typeof x === "object");
      if (eq && Object.prototype.hasOwnProperty.call(eq, "current")) {
        const c = eq.current;
        if (!c || typeof c !== "object" || Array.isArray(c)) return null;
        return { items: objs(c.items), open: c.open };
      }
      const slug = d.prototype && typeof d.prototype === "object" && typeof d.prototype.slug === "string" ? d.prototype.slug : "";
      if (!slug || !(Array.isArray(d.prototypes) || (eq && Array.isArray(eq.items)))) return null;
      const run = (eq && Array.isArray(eq.runs) ? eq.runs : []).find((r) => r && typeof r === "object" && r.slug === slug);
      return { items: objs(eq && eq.items).filter((q) => q.runSlug === slug), open: run ? run.open : null };
    }
    // Pozycje `blockingQuestions.items[]` / `assumptions.items[]`: tylko obiekty z `id` (tekst albo liczba) i tekstem `question`; reszta pomijana.
    const Q_EXPERT_REF = /\((?:ekspert|expert):\s*qn-[0-9a-f]+\)/i;   // pytanie oddane ekspertowi (dług D17; zapis zależy od języka specu): parser speca widzi je jako zwykłe Q
    const Q_ID_IN_TEXT = /(^|[^\w-])(Q\d+)(?![\w-])/g;
    function specItems(group) {
      const items = group && typeof group === "object" && !Array.isArray(group) && Array.isArray(group.items) ? group.items : [];
      return items.filter((x) => x && typeof x === "object" && (typeof x.id === "string" ? x.id !== "" : Number.isFinite(x.id)) && typeof x.question === "string");
    }
    // Id pytań, na które czeka sesja: pozycje `waiting[]` ze `source: "checkpoint"` (`czeka: odpowiedź Q3`); granica identyfikatora, wiele id w jednej pozycji.
    function sessionWaitIds(d) {
      const ids = new Set();
      (Array.isArray(d.waiting) ? d.waiting : []).forEach((w) => {
        if (!w || typeof w !== "object" || w.source !== "checkpoint" || typeof w.what !== "string") return;
        for (const m of w.what.matchAll(Q_ID_IN_TEXT)) ids.add(m[2]);
      });
      return ids;
    }
    function realizeView(d) {
      const blocking = specItems(d.blockingQuestions), assumptions = specItems(d.assumptions);
      return { blocking, assumptions, waits: sessionWaitIds(d), open: blocking.filter((q) => !Q_EXPERT_REF.test(q.question)).length };
    }
    function questionsView(d) {
      const realize = realizeView(d), expert = expertView(d);
      const expertOpen = expert && Number.isFinite(expert.open) && expert.open > 0 ? expert.open : 0;
      const hasRealize = realize.blocking.length > 0 || realize.assumptions.length > 0;
      const hasExpert = !!expert && (expert.items.length > 0 || expertOpen > 0);
      return { realize, expert, expertOpen, visible: hasRealize || hasExpert };
    }
    function questionRow(d, q) {
      const text = typeof q.text === "string" ? (q.text.length > Q_TEXT_MAX ? q.text.slice(0, Q_TEXT_MAX) + "…" : q.text) : null;
      const textHtml = text != null && typeof q.textHtml === "string" && q.textHtml && typeof deps.mountHtml === "function" ? q.textHtml : null;
      const to = (q.addressees === "all" || (Array.isArray(q.addressees) && !q.addressees.length)) ? tr("ui.expert.toAll") : addresseesText(q.addressees);
      const url = safeUrl(expertsUrl(d, q.url));
      const slug = typeof q.runSlug === "string" ? q.runSlug : "";
      return h("li", { class: `q-row q-item is-${typeof q.state === "string" ? q.state : "open"}`, "data-question": q.id == null ? "" : String(q.id) },
        h("span", { class: "q-head" }, mono(q.id == null ? "—" : String(q.id)), slug ? h("span", { class: "q-run" }, slug) : null,
          q.blocking === true ? h("span", { class: "chip s-warn q-blocking" }, tr("ui.q.blocking")) : null,
          h("span", { class: "chip " + (q.state === "open" ? "s-active" : "s-planned") }, word(Q_STATE_KEY, q.state) || String(q.state ?? "—")),
          h("span", { class: "q-age" }, fmtAge(q.at))),
        textHtml ? inl("q-text", text, textHtml) : h("span", { class: "q-text" + (text == null ? " is-missing" : "") }, text == null ? tr("ui.q.textMissing") : text),
        to ? h("span", { class: "q-to", "data-addressees": "" }, to) : null,
        url ? h("a", { class: "q-go", href: url, target: "_blank", rel: "noopener noreferrer", "aria-label": tr("ui.q.openAria", { slug: slug || "" }).trim(),
          on: { click: linkClick("experts", slug, url) } }, tr("ui.q.open")) : null);
    }
    function specQuestionRow(q, waits) {
      const id = String(q.id), text = q.question.length > Q_TEXT_MAX ? q.question.slice(0, Q_TEXT_MAX) + "…" : q.question;
      const withExpert = Q_EXPERT_REF.test(q.question);
      const mark = withExpert ? h("span", { class: "chip s-planned q-with-expert" }, tr("ui.q.withExpert"))
        : waits.has(id) ? h("span", { class: "chip s-active q-session-waits" }, tr("ui.q.sessionWaits")) : null;
      return h("li", { class: "q-row q-realize" + (withExpert ? " is-with-expert" : ""), "data-question": id },
        h("span", { class: "q-head" }, mono(id), mark),
        h("span", { class: "q-text", title: q.question }, text));
    }
    function assumptionRow(a) {
      const id = String(a.id), text = a.question.length > Q_TEXT_MAX ? a.question.slice(0, Q_TEXT_MAX) + "…" : a.question;
      const accepted = typeof a.accepted === "string" ? a.accepted.trim() : "";
      return h("li", { class: "q-row q-assume", "data-assumption": id },
        h("span", { class: "q-head" }, mono(id), h("span", { class: "chip s-planned q-assumption" }, tr("ui.q.assumption")),
          accepted ? h("span", { class: "q-accepted" }, tr("ui.q.accepted", { value: accepted })) : null),
        h("span", { class: "q-text", title: a.question }, text));
    }
    const Q_LIST_MAX = 50;
    // Grupa realizacji ma jeden limit na wszystkie wiersze (najpierw pytania, potem założenia); nadmiar to jedna notka „+N więcej”.
    function realizeLists(rz) {
      const room = Math.max(0, Q_LIST_MAX - Math.min(rz.blocking.length, Q_LIST_MAX));
      const shownQ = rz.blocking.slice(0, Q_LIST_MAX), shownA = rz.assumptions.slice(0, room);
      const more = rz.blocking.length + rz.assumptions.length - shownQ.length - shownA.length;
      const note = more > 0 ? h("li", { class: "q-more" }, tr("ui.q.more", { n: more })) : null;
      const lists = [];
      if (shownQ.length) lists.push(h("ul", { class: "q-list" }, shownQ.map((q) => specQuestionRow(q, rz.waits)), shownA.length ? null : note));
      if (shownA.length) lists.push(h("ul", { class: "q-list" }, shownA.map(assumptionRow), note));
      return lists;
    }
    function groupTitle(key, count) {
      return [tr(key), count > 0 ? " " : null, count > 0 ? h("span", { class: "count" }, String(count)) : null];
    }
    function renderQuestionsTab(d) {
      const qv = questionsView(d);
      ui["tab-questions"].hidden = !qv.visible;
      if (!qv.visible) {
        fill(ui.qRealize); fill(ui.qItems); fill(ui.qExpertHead); ui.qCount.textContent = "";
        ui["view-questions"].hidden = true;
        if (st.view === "questions") setView("progress", false);
        return;
      }
      const rz = qv.realize, ex = qv.expert;
      const total = rz.open + qv.expertOpen;
      ui.qCount.textContent = total > 0 ? String(total) : "";
      ui["tab-questions"].setAttribute("data-tip", total > 0 ? tr("ui.q.countTip", { realize: rz.open, expert: qv.expertOpen }) : tr("ui.tab.questionsTip"));
      const rzRows = rz.blocking.length || rz.assumptions.length;
      fill(ui.qRealize, h("h2", { class: "q-group-title" }, groupTitle("ui.q.groupRealize", rz.open)),
        rzRows ? realizeLists(rz) : h("div", { class: "empty" }, tr("ui.q.noRealize")));
      fill(ui.qExpertHead, groupTitle("ui.q.groupExpert", qv.expertOpen));
      const items = ex ? ex.items.slice(0, Q_LIST_MAX) : [];
      fill(ui.qItems, h("h3", { class: "q-title" }, tr("ui.q.itemsTitle")),
        items.length ? h("ul", { class: "q-list" }, items.map((q) => questionRow(d, q))) : h("div", { class: "empty" }, tr("ui.q.noQuestions")));
      ui["view-questions"].hidden = st.view !== "questions";
    }

    // ---------------------------------------------------------------- dokumenty
    // Grupy listy dokumentów to <details>: task i run domyślnie rozwinięte, reszta zwinięta; stan przeżywa odświeżenie (st.docGroupsOpen).
    function docGroupOpen(key) { return Object.prototype.hasOwnProperty.call(st.docGroupsOpen, key) ? st.docGroupsOpen[key] : key === "task" || key === "run"; }
    function docGroupOf(path) {
      const g = ((st.data && st.data.docs) || []).find((x) => (x.items || []).some((it) => it.path === path));
      return g ? g.key : null;
    }
    // Wybrany dokument rozwija swoją grupę raz po zmianie st.doc (bez przebudowy listy); ręczne zwinięcie przeżywa odświeżenie.
    function revealDocGroup(path) {
      const key = docGroupOf(path);
      if (!path || key == null || key + "\n" + path === st.docGroupSeen) return;
      st.docGroupSeen = key + "\n" + path;
      if (docGroupOpen(key)) return;
      st.docGroupsOpen[key] = true;
      const box = Array.prototype.find.call(ui.docList.querySelectorAll("details"), (x) => x.getAttribute("data-doc-group") === key);
      if (box) box.setAttribute("open", "");
    }
    function renderDocList(d) {
      const groups = d.docs || [];
      ui.docCount.textContent = groups.reduce((n, g) => n + (g.items || []).length, 0) || "";
      st.docButtons = [];
      const first = groups[0] && groups[0].items && groups[0].items[0] ? groups[0].items[0].path : null;
      revealDocGroup(st.doc || first);
      st.docRebuilding = true;
      try {
      fill(ui.docList, groups.map((g) => {
        const items = (g.items || []).map((it) => {
          const name = g.key === "run" ? baseName(it.path) : g.key === "task" ? it.title : String(it.title || "").replace(/^(Plan|Spec):\s*/i, "");
          const b = h("button", { class: "doc-item", "data-doc": it.path, "aria-current": String(it.path === st.doc), "data-tip": tr("ui.docs.changedTip", { path: it.path, time: fmtTimeL(it.mtime) }),
            on: { click: () => (previewMode() ? loadDoc(it.path) : openDoc(it.path)) } },
            icon("i-doc"), h("span", { class: "nm" }, name), statusChip(it.status));
          st.docButtons.push(b);
          return b;
        });
        const box = h("details", { class: "doc-group", "data-doc-group": g.key, open: docGroupOpen(g.key) },
          h("summary", { on: { focus: () => { st.docFocus = g.key; }, blur: () => { if (!st.docRebuilding) st.docFocus = null; } } }, `${g.label} (${items.length})`),
          items);
        box.addEventListener("toggle", () => { if (box.isConnected !== false) st.docGroupsOpen[g.key] = box.hasAttribute("open"); });
        return box;
      }));
      } finally { st.docRebuilding = false; }
      if (st.docFocus) {
        const sm = Array.prototype.find.call(ui.docList.querySelectorAll("details"), (x) => x.getAttribute("data-doc-group") === st.docFocus);
        const el = sm && sm.querySelector("summary");
        if (!el) st.docFocus = null;
        else if (typeof el.focus === "function") el.focus({ preventScroll: true });
      }
      if (!st.doc && first) st.doc = first;
    }
    // Otwarcie dokumentu: z actions.loadDoc podgląd w zakładce Dokumenty (strona), bez niego actions.openDoc (host, np. edytor).
    function openDoc(path) {
      if (previewMode()) { st.doc = path; revealDocGroup(path); setView("docs"); }
      else if (typeof actions.openDoc === "function") actions.openDoc(path);
    }
    async function loadDoc(path, live = false) {
      if (!previewMode()) return;
      const view = ui.docView;
      const scroll = live && deps.scroll ? deps.scroll.save() : 0;
      st.doc = path;
      revealDocGroup(path);
      if (!live) navigate("docs:" + path);
      st.docButtons.forEach((b) => b.setAttribute("aria-current", String(b.getAttribute("data-doc") === path)));
      try {
        const data = await actions.loadDoc(path);
        if (st.destroyed) return;
        if (live && st.docData && st.docData.path === data.path && st.docData.mtime === data.mtime) return;
        st.docData = data; toTop.failed = false; toTopSync();
        const md = h("div", { class: "md" });
        fill(view,
          h("div", { class: "doc-bar" }, icon("i-doc"), mono(data.path), statusChip(data.status), h("span", null, tr("ui.docs.changed", { time: fmtTimeL(data.mtime) })),
            data.truncated ? h("span", { class: "chip s-warn" }, tr("ui.doc.truncated")) : null,
            live ? h("span", { class: "chip s-active" }, tr("ui.doc.refreshed")) : null),
          md);
        // Treść Markdown (HTML z serwera) wstawia host: renderer nie parsuje HTML.
        if (typeof deps.mountHtml === "function") deps.mountHtml(md, data.html);
        if (deps.scroll) { if (live) deps.scroll.restore(scroll); else deps.scroll.reveal(view, st.view === "docs"); }
      } catch (err) {
        if (st.destroyed) return;
        toTop.failed = true; toTopSync();
        fill(view, h("div", { class: "empty" }, tr("ui.doc.loadError", { error: (err && err.message) || String(err) }),
          h("button", { class: "btn", id: "retryDoc", on: { click: () => loadDoc(path) } }, tr("ui.doc.retry"))));
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
    const VIEWS = ["progress", "tests", "docs", "questions", "prototypes"];
    const views = () => VIEWS.filter((v) => ui["tab-" + v]);   // the tab of the prototypes exists only once there is one to show
    function setView(view, push = true) {
      st.view = view;
      if (!views().includes(view) || ((view === "questions" || view === "tests" || view === "prototypes") && ui["tab-" + view].hidden)) view = st.view = "progress";
      views().forEach((v) => {
        ui["view-" + v].hidden = v !== view;
        ui["tab-" + v].setAttribute("aria-selected", String(v === view));
      });
      if (view !== "prototypes" && st.protoFrame) { closeProtoFrame(); render(); }   // a frame lives only on the screen of its tab; the consents come back
      if (view === "docs" && st.doc) loadDoc(st.doc);
      if (push) navigate(view === "docs" && st.doc ? "docs:" + st.doc : view === "tests" && st.testsStep ? "tests:" + st.testsStep : view);
      toTopSync();
    }
    // Strzałki w liście zakładek (WAI-ARIA): lewo/prawo, Home/End przechodzą po widocznych zakładkach i je włączają.
    function tabKey(e) {
      const keys = ["ArrowRight", "ArrowLeft", "Home", "End"];
      if (!keys.includes(e.key)) return;
      const tabs = views().filter((v) => !ui["tab-" + v].hidden);
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
      if (e.key === "Escape" && st.fb.dialog) { if (!st.fb.sending) closeFbDialog(true); return; }
      if (st.consent || st.password || st.protoDialog) {   // a window is open: the keys of the page do nothing behind it
        if (e.key !== "Escape") return;
        if (st.protoDialog) { if (!st.protoDialog.sending) closeProtoDialog(true); }
        else if (st.consent && !st.consent.sending) closeConsent(true);
        else if (st.password && !st.password.sending) closePassword();
        return;
      }
      if (e.key === "Escape" && !ui.descTip.hidden) hideDesc();
      const t = e.target;
      if ((t && typeof t.closest === "function" && t.closest("input, textarea, select, [contenteditable]")) || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "p" || e.key === "P") setView(ui["tab-prototypes"] && !ui["tab-prototypes"].hidden ? "prototypes" : "progress");
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
      if (agentsSig(d) !== st.agentsSig) { render(); return; }
      updateAgentBars(d);   // N16: a run went stale or overran its ETA with the clock only
      updateEtaCountdowns(d);   // ETA: odliczanie w miejscu (bez render(), fokus zostaje)
      const quiet = silenceMinutes(d);
      if ((quiet > 0) !== Boolean(st.silenceShown) || (quiet && quiet !== st.silenceMin)) { st.silenceMin = quiet; renderWaiting(d); }
    }
    function setLive(mode) {
      st.liveMode = mode;
      ui.live.setAttribute("class", "live is-" + mode);
      ui.liveText.textContent = liveText(mode) ?? "";
      ui.live.setAttribute("data-tip", liveTip(mode) ?? "");
    }

    syncTheme();

    const board = {
      update(json) {
        st.prev = st.data;
        st.data = json;
        if (setLanguage(json)) relabel();   // `lang`/`texts` of the status (absent = Polish): the skeleton texts follow the language
        render();
        if (st.view === "docs" && st.doc) loadDoc(st.doc, true);
      },
      setDescriptions(map) { st.desc = map && typeof map === "object" ? map : {}; renderGlossary(); },
      setNotice(notice) { setNoticeState(notice); render(); },
      // The session of the page changed (`deps.session`): the header, the buttons and an open window follow it, with or without a status.
      sessionChanged() { if (st.data) render(); else renderAccount(null); },
      // The host hides the page (the History tab): an open window does not wait behind it. A request already sent is not affected.
      closeDialogs() {
        if (st.consent && !st.consent.sending) closeConsent(false);
        if (st.password && !st.password.sending) closePassword();
        if (st.protoDialog && !st.protoDialog.sending) closeProtoDialog();
        if (st.fb.dialog && !st.fb.sending) closeFbDialog(false);   // a window in the top layer of a hidden page would block the page shown instead
        if (st.protoFrame) { closeProtoFrame(); if (st.data) render(); }   // a hidden page keeps no frame running
      },
      setVisible(visible) {
        st.visible = visible !== false;
        if (!st.visible) { cancel(st.noticeTimer); st.noticeTimer = null; }
        else if (st.notice && !st.noticeTimer) setNoticeState(st.notice);
      },
      setLive,
      // Text of the current language (the adapter uses it for its own messages): `t(key, params, n)`; `lang()` is the language of the last status.
      t: tr,
      lang: () => L.lang,
      setView,
      setDoc(path) { st.doc = path || null; revealDocGroup(st.doc); },
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

  // ------------------------------------------------------------------ pasek zakładek projektu (CORE-1180-MULTIWT, serwer z wieloma zakładkami)
  // Osobny od `create`: wnętrze zakładki to dzisiejsza strona (ten sam `create`), a pasek stoi nad nią w osobnym korzeniu. Czysty widok: model dostarcza host
  // (app.js), teksty bierze z `deps.t(key, params)` (katalog `ui.wt.*`), nic nie czyta z sieci ani z pamięci. Model `update(model)`:
  //   tabs: [{ id, name, sub, active, waiting, readOnly, gone }], archive: null | { count, active } (the tab of the finished plans, id `history`),
  //   hidden: null | [{ id, name, lastActivity, reason }] (worktree bez zakładki), showAll: bool, lang,
  //   alerts: [{ kind: "info" | "warn" | "error", title: [key, params], body: [key, params], dismiss: string | null }].
  // Wszystko, co w modelu jest daną (nazwa, gałąź), trafia do DOM wyłącznie jako tekst. `deps`: onSelect(id), onToggleAll(), onDismiss(key), `panel` (element strony
  // zakładki: dostaje role=tabpanel i aria-labelledby aktywnej zakładki, gdy pasek jest widoczny), `panelId` (jego id, do aria-controls).
  const WT_KEYS = ["ArrowRight", "ArrowLeft", "Home", "End"];
  function createTabBar(root, deps) {
    if (!root || !root.ownerDocument) throw new TypeError("dashboard-render: createTabBar(root, deps) wymaga elementu DOM");
    deps = deps || {};
    const doc = root.ownerDocument;
    const { h, fill } = builder(doc);
    const t = typeof deps.t === "function" ? deps.t : (key) => key;
    const call = (name, ...args) => { if (typeof deps[name] === "function") deps[name](...args); };
    const plain = (text) => String(text).replace(/`/g, "");
    const rich = (text) => splitBy(String(text), /`([^`]*)`/g, (code) => h("code", { class: "wt-code" }, code));
    const say = (pair) => (Array.isArray(pair) ? t(pair[0], pair[1] || {}) : "");
    const when = (iso, lang) => { const ms = Date.parse(iso); return Number.isFinite(ms) ? new Date(ms).toLocaleDateString(localeOf(lang)) : t("ui.wt.never"); };
    let model = null, sig = null, pendingFocus = null, focused = null, hosts = null, shownAlerts = [], shownNote = null;
    const panel = deps.panel && typeof deps.panel.setAttribute === "function" ? deps.panel : null;
    const archivePanel = deps.archivePanel && typeof deps.archivePanel.setAttribute === "function" ? deps.archivePanel : null;
    const labelPanel = (el, labelledBy) => {
      if (labelledBy) { el.setAttribute("role", "tabpanel"); el.setAttribute("aria-labelledby", labelledBy); }
      else { el.removeAttribute("role"); el.removeAttribute("aria-labelledby"); }
    };
    function syncPanel() {
      const id = activeId();
      const shown = Boolean(model && model.tabs && model.tabs.length && id !== null);
      if (archivePanel) {   // with its own panel the archive tab labels that one, and the page of a tab only when a tab is selected
        labelPanel(archivePanel, shown && id === "history" ? "wt-tab-history" : null);
        if (panel) labelPanel(panel, shown && id !== "history" ? "wt-tab-" + id : null);
        return;
      }
      if (panel) labelPanel(panel, shown ? "wt-tab-" + id : null);
    }

    const ids = () => {
      const list = ((model && model.tabs) || []).map((tab) => String(tab.id));
      if (model && model.archive) list.push("history");
      return list;
    };
    // Every control the keyboard can stay on has `data-wt-focus` (`tab:<id>`, `all`, `dismiss:<key>`): after a redraw the focus goes back to it.
    const focusKey = (key) => { const el = root.querySelector('[data-wt-focus="' + key + '"]'); if (el && typeof el.focus === "function") el.focus({ preventScroll: true }); };
    const focusTab = (id) => focusKey("tab:" + id);
    function pick(id) { pendingFocus = "tab:" + id; call("onSelect", id); if (pendingFocus === "tab:" + id) { pendingFocus = null; focusTab(id); } }
    function onKey(e) {
      if (!WT_KEYS.includes(e.key) || e.ctrlKey || e.metaKey || e.altKey) return;
      const list = ids();
      if (!list.length) return;
      const target = e.target && typeof e.target.getAttribute === "function" ? e.target.getAttribute("data-wt-tab") : null;
      const here = list.indexOf(target !== null && list.includes(target) ? target : String(activeId()));
      const next = e.key === "Home" ? 0 : e.key === "End" ? list.length - 1 : (Math.max(here, 0) + (e.key === "ArrowRight" ? 1 : -1) + list.length) % list.length;
      e.preventDefault();
      pick(list[next]);
    }
    function activeId() {
      if (model && model.archive && model.archive.active) return "history";
      const tab = ((model && model.tabs) || []).find((x) => x.active);
      return tab ? tab.id : null;
    }
    function tabButton(tab, active) {
      const sr = [];
      if (tab.waiting) sr.push(t("ui.wt.waitTip"));
      if (tab.gone) sr.push(t("ui.wt.goneSub"));
      const tip = tab.gone ? "" : tab.readOnly ? plain(t("ui.wt.readOnlyTip")) : tab.waiting ? t("ui.wt.waitTip") : "";
      return h("button", { type: "button", role: "tab", id: "wt-tab-" + tab.id, "data-wt-tab": tab.id, "data-wt-focus": "tab:" + tab.id, "aria-selected": String(active), "aria-controls": deps.panelId || null,
        tabindex: active ? "0" : "-1", class: "wt-tab" + (active ? " is-active" : "") + (tab.gone ? " is-gone" : "") + (tab.waiting ? " is-waiting" : ""), title: tip || null,
        on: { click: () => pick(tab.id) } },
      tab.waiting ? h("span", { class: "wt-dot", "aria-hidden": "true" }) : null,
      h("span", { class: "wt-name" }, tab.name),
      tab.gone ? h("span", { class: "wt-sub" }, t("ui.wt.goneSub")) : tab.sub ? h("span", { class: "wt-sub" }, tab.sub) : null,
      tab.readOnly && active ? h("span", { class: "wt-ro" }, t("ui.wt.readOnly")) : null,
      sr.length ? h("span", { class: "wt-sr" }, sr.join(", ")) : null);
    }
    function archiveButton(entry) {
      const active = Boolean(entry.active);
      return h("button", { type: "button", role: "tab", id: "wt-tab-history", "data-wt-tab": "history", "data-wt-focus": "tab:history", "aria-selected": String(active), "aria-controls": deps.archivePanelId || deps.panelId || null,
        tabindex: active ? "0" : "-1", class: "wt-tab wt-tab-hist" + (active ? " is-active" : ""), on: { click: () => pick("history") } },
      h("span", { class: "wt-name" }, t("ui.wt.history")),
      Number.isFinite(entry.count) && entry.count > 0
        ? h("span", { class: "wt-count", title: t("ui.wt.historyCount", { n: entry.count }), "aria-label": t("ui.wt.historyCount", { n: entry.count }) }, String(entry.count)) : null);
    }
    function hiddenTable(m) {
      const rows = m.hidden.map((item) => h("tr", null,
        h("td", { class: "wt-mono" }, item.name),
        h("td", null, item.lastActivity ? when(item.lastActivity, m.lang) : t("ui.wt.never")),
        h("td", null, Object.prototype.hasOwnProperty.call({ inactive: 1, limit: 1, trace: 1 }, item.reason) ? t("ui.wt.why." + item.reason) : t("ui.wt.why.other"))));
      return h("section", { class: "wt-hidden", id: "wt-hidden", "aria-label": t("ui.wt.hiddenTitle") },
        h("div", { class: "wt-hidden-title" }, t("ui.wt.hiddenTitle")),
        h("p", { class: "wt-hint" }, rich(t("ui.wt.hiddenHint"))),
        h("table", { class: "wt-table" },
          h("thead", null, h("tr", null, h("th", { scope: "col" }, t("ui.wt.hiddenName")), h("th", { scope: "col" }, t("ui.wt.hiddenWhen")), h("th", { scope: "col" }, t("ui.wt.hiddenWhy")))),
          h("tbody", null, rows)));
    }
    // After a dismiss the focus goes to the selected tab (the button that had it is gone).
    function dismissAlert(key) {
      const id = activeId();
      pendingFocus = id !== null ? "tab:" + id : null;
      call("onDismiss", key);
      if (pendingFocus) { const want = pendingFocus; pendingFocus = null; focusKey(want); }
    }
    function alertBox(a) {
      const kind = a.kind === "error" || a.kind === "warn" ? a.kind : "info";
      return h("div", { class: "wt-alert wt-alert-" + kind, role: kind === "error" ? "alert" : "status" },
        h("div", { class: "wt-alert-text" }, h("div", { class: "wt-alert-title" }, rich(say(a.title))), a.body ? h("div", null, rich(say(a.body))) : null),
        a.dismiss ? h("button", { type: "button", class: "wt-alert-close", "data-wt-dismiss": a.dismiss, "data-wt-focus": "dismiss:" + a.dismiss, on: { click: () => dismissAlert(a.dismiss) } }, t("ui.wt.noteClose")) : null);
    }
    // The alerts live in a host of their own that is not rebuilt with the rest of the bar: an alert that is the same (kind, texts, language) keeps its element, so a
    // screen reader does not hear `role="alert"` again at every change of the model. Only the tail from the first change is taken out and put in again.
    function syncAlerts(m) {
      const next = (m.alerts || []).map((a) => ({ key: JSON.stringify([m.lang || "", a.kind, a.title, a.body, a.dismiss]), a }));
      let i = 0;
      while (i < shownAlerts.length && i < next.length && shownAlerts[i].key === next[i].key) i++;
      for (const gone of shownAlerts.slice(i)) hosts.alerts.removeChild(gone.el);
      const fresh = shownAlerts.slice(0, i);
      for (const item of next.slice(i)) { item.el = alertBox(item.a); hosts.alerts.appendChild(item.el); fresh.push(item); }
      shownAlerts = fresh;
      hosts.alerts.hidden = !shownAlerts.length;
    }
    function render() {
      const m = model;
      let inside = false;
      try { inside = Boolean(root.matches(":focus-within")); } catch (e) { inside = false; }   // the page is redrawn by the polling: a tab that has the focus keeps it
      const had = inside ? focused : null;
      const buttons = (m.tabs || []).map((tab) => tabButton(tab, Boolean(tab.active) && !(m.archive && m.archive.active)));
      if (m.archive) buttons.push(h("span", { class: "wt-sep", "aria-hidden": "true" }), archiveButton(m.archive));
      const hiddenCount = Array.isArray(m.hidden) ? m.hidden.length : 0;
      if (!hosts) {
        hosts = { top: h("div", { class: "wt-top" }), alerts: h("div", { class: "wt-alerts", hidden: true }), note: h("div", { class: "wt-notehost" }) };
        shownAlerts = []; shownNote = null;
        fill(root, hosts.top, hosts.alerts, hosts.note);
      }
      fill(hosts.top,
        h("div", { class: "wt-row" },
          h("div", { class: "wt-tabs", role: "tablist", "aria-label": t("ui.wt.aria"), on: { keydown: onKey } }, buttons),
          hiddenCount ? h("button", { type: "button", class: "wt-all", "data-wt-focus": "all", "aria-expanded": String(Boolean(m.showAll)), "aria-controls": "wt-hidden", on: { click: () => call("onToggleAll") } },
            m.showAll ? t("ui.wt.hideAll") : t("ui.wt.showAll", { n: hiddenCount })) : null),
        hiddenCount && m.showAll ? hiddenTable(m) : null);
      syncAlerts(m);
      const noteKey = Array.isArray(m.note) ? JSON.stringify([m.lang || "", m.note]) : null;   // the same note keeps its element (no new role="status" announcement)
      if (!shownNote || shownNote.key !== noteKey) {
        fill(hosts.note, noteKey ? h("p", { class: "wt-note", role: "status" }, rich(say(m.note))) : null);
        shownNote = { key: noteKey };
      }
      const want = pendingFocus || had;
      pendingFocus = null;
      if (want) focusKey(want);
    }
    root.classList.add("wt-bar");
    root.addEventListener("focusin", (e) => { const key = e.target && typeof e.target.getAttribute === "function" ? e.target.getAttribute("data-wt-focus") : null; if (key) focused = key; });
    root.addEventListener("focusout", () => { focused = null; });
    return {
      update(next) {
        const text = JSON.stringify(next || {});
        if (text === sig) return;
        sig = text;
        model = JSON.parse(text);
        model.tabs = Array.isArray(model.tabs) ? model.tabs.filter((tab) => tab && /^[A-Za-z0-9_-]{1,32}$/.test(String(tab.id))) : [];
        if (!model.tabs || !model.tabs.length) { fill(root); hosts = null; shownAlerts = []; shownNote = null; root.hidden = true; syncPanel(); return; }
        root.hidden = false;
        render();
        syncPanel();
      },
      // The language changed (the texts come from `deps.t`): draw again with the same model.
      relabel() { if (model && model.tabs && model.tabs.length) render(); },
      focusActive() { const id = activeId(); if (id !== null) focusTab(id); },
      destroy() { sig = null; model = null; hosts = null; shownAlerts = []; shownNote = null; syncPanel(); fill(root); root.classList.remove("wt-bar"); },
    };
  }

  // ------------------------------------------------------------------ zakładka „Historia” (CORE-1180-MULTIWT M-6)
  // Czysty widok listy zakończonych planów wszystkich zakładek, z podglądem i zmianą statusu (archiwizuj/porzuć/przywróć). Host (app.js) robi transport, tokeny i stan; widok
  // dostaje gotowy model i nic nie wie o tokenach (tylko `canAct`). Model `update(model)`:
  //   lang, state: "loading" | "ready" | "error", error: string, stale: string (błąd odświeżenia przy starej liście), refreshing: bool,
  //   groups: [{ id, name, branch, main, items: [{ key, title, path, status: done|archived|approved, copies, copyNames: [string], ownerName, abandoned, action: archive|unarchive|abandon|null, restoreTo?: "approved"|null,
  //     canAct, why: null | "login" | "role" | "password" | "viewer", mtime, selected }] }], count, shown, truncated, more: number (wierszy do pokazania po „Pokaż więcej”),
  //   preview: null | { state: "loading" | "ready" | "error", item (jak wyżej), html, error, truncated },
  //   confirm: null | { action, restoreTo?, title, tab, sha, copyNames: [string], busy }, notice: null | { kind, title: [key, params], bodyText, body: [key, params] }.
  // `deps`: t(key, params), mountHtml(el, html), onSelect(key), onAction(key), onConfirm(), onCancel(), onMore(), onReload(), onDismiss(), `panelId`.
  // Dane z modelu (tytuły, ścieżki, nazwy zakładek, komunikat serwera) trafiają do DOM wyłącznie jako tekst; jedynym HTML jest podgląd Markdown z serwera (`mountHtml`).
  const AH_KEYS = ["ArrowDown", "ArrowUp", "Home", "End"];
  // Label, title of the question and its text for every action of a row (`restore`: the question of an abandoned plan that goes back to `approved`).
  const AH_ACTIONS = {
    archive: ["ui.history.archive", "ui.history.confirmArchiveTitle", "ui.history.confirmArchiveBody"],
    abandon: ["ui.history.abandon", "ui.history.confirmAbandonTitle", "ui.history.confirmAbandonBody"],
    unarchive: ["ui.history.unarchive", "ui.history.confirmUnarchiveTitle", "ui.history.confirmUnarchiveBody"],
    restore: ["ui.history.unarchive", "ui.history.confirmUnarchiveTitle", "ui.history.confirmRestoreApprovedBody"],
  };
  const AH_WHY = { viewer: "ui.history.viewerOnly", login: "ui.history.needLogin", role: "ui.history.needRole", password: "ui.session.mustChange" };
  function createArchiveView(root, deps) {
    if (!root || !root.ownerDocument) throw new TypeError("dashboard-render: createArchiveView(root, deps) wymaga elementu DOM");
    deps = deps || {};
    const doc = root.ownerDocument;
    const { h, fill } = builder(doc);
    const t = typeof deps.t === "function" ? deps.t : (key) => key;
    const call = (name, ...args) => { if (typeof deps[name] === "function") deps[name](...args); };
    const plain = (text) => String(text).replace(/`/g, "");
    const rich = (text) => splitBy(String(text), /`([^`]*)`/g, (code) => h("code", { class: "wh-code" }, code));
    // Names (tabs, plans) come as data: a backtick in them would open a code fragment of `rich`, so it is dropped before the text is built.
    const clean = (params) => { const out = {}; for (const k of Object.keys(params || {})) out[k] = typeof params[k] === "string" ? plain(params[k]) : params[k]; return out; };
    const say = (pair) => (Array.isArray(pair) ? t(pair[0], clean(pair[1])) : "");
    const statusText = (status) => (status === "done" || status === "archived" || status === "approved" ? t("ui.history.status." + status) : String(status));
    const day = (iso, lang) => { const ms = Date.parse(iso); return Number.isFinite(ms) ? new Date(ms).toLocaleDateString(localeOf(lang)) : ""; };
    let model = null, focused = null, want = null, confirmShown = false, lastIn = null;
    const sigs = { head: null, notice: null, list: null, prev: null };
    const parts = {
      head: h("div", { class: "wh-head" }),
      notice: h("div", { class: "wh-notice" }),
      list: h("nav", { class: "wh-list", "aria-label": "" }),
      prev: h("section", { class: "wh-prev", "aria-label": "" }),
    };
    const body = h("div", { class: "wh-body" }, parts.list, parts.prev);
    const inside = () => { try { return Boolean(root.matches(":focus-within")); } catch (e) { return false; } };
    const focusKey = (key) => {
      if (!key) return false;
      for (const el of root.querySelectorAll("[data-ah-focus]")) {
        if (el.getAttribute("data-ah-focus") === key) { if (typeof el.focus === "function") el.focus({ preventScroll: true }); return true; }
      }
      return false;
    };
    // A part that is drawn again keeps the focus: the control that had it is found again by its key (a row by the key of the plan).
    function redraw(name, sig, draw) {
      if (sigs[name] === sig) return;
      sigs[name] = sig;
      const had = inside() ? focused : null;
      want = null;
      draw();
      if (want) focusKey(want);   // the part asked for the focus itself (the question of a confirmation)
      else if (had && !focusKey(had) && (had === "cancel" || had === "confirm")) focusKey("act");   // the question is gone: back to the button that opened it
    }

    function onListKey(e) {
      if (!AH_KEYS.includes(e.key) || e.ctrlKey || e.metaKey || e.altKey) return;
      const rows = Array.from(parts.list.querySelectorAll(".wh-row"));
      if (!rows.length) return;
      const at = rows.indexOf(e.target);
      const next = e.key === "Home" ? 0 : e.key === "End" ? rows.length - 1 : Math.min(rows.length - 1, Math.max(0, (at < 0 ? 0 : at) + (e.key === "ArrowDown" ? 1 : -1)));
      e.preventDefault();
      if (typeof rows[next].focus === "function") rows[next].focus({ preventScroll: true });
    }
    function rowButton(item) {
      const chips = [h("span", { class: "wh-chip wh-chip-" + (item.status === "done" || item.status === "archived" || item.status === "approved" ? item.status : "other") }, statusText(item.status))];
      if (item.abandoned) chips.push(h("span", { class: "wh-chip wh-chip-warn", title: t("ui.history.abandonedTip") }, t("ui.history.abandoned")));
      if (item.copies > 1) chips.push(h("span", { class: "wh-chip", title: t("ui.history.copiesTip", { tabs: (item.copyNames || []).join(", "), owner: item.ownerName }) }, t("ui.history.copies", { n: item.copies })));
      return h("button", { type: "button", class: "wh-row" + (item.selected ? " is-sel" : ""), "data-ah-focus": "row:" + item.key, "aria-current": item.selected ? "true" : null,
        on: { click: () => call("onSelect", item.key) } },
      h("span", { class: "wh-row-title" }, item.title || item.path),
      h("span", { class: "wh-row-meta" }, chips, h("span", { class: "wh-path" }, item.path), item.mtime ? h("span", { class: "wh-date" }, day(item.mtime, model.lang)) : null));
    }
    function drawList(m) {
      parts.list.setAttribute("aria-label", t("ui.history.listAria"));
      if (m.state === "loading" && !(m.groups || []).length) { fill(parts.list, h("p", { class: "wh-state", role: "status" }, t("ui.history.loading"))); return; }
      if (m.state === "error" && !(m.groups || []).length) {
        fill(parts.list, h("p", { class: "wh-state", role: "alert" }, t("ui.history.loadError", { error: m.error || "" })),
          h("button", { type: "button", class: "wh-btn", "data-ah-focus": "retry", on: { click: () => call("onReload") } }, t("ui.history.retry")));
        return;
      }
      const total = (m.groups || []).reduce((n, g) => n + (g.items || []).length, 0);
      if (!total && !m.count) { fill(parts.list, h("p", { class: "wh-state", role: "status" }, t("ui.history.empty"))); return; }
      fill(parts.list,
        m.stale ? h("p", { class: "wh-state wh-stale", role: "status" }, t("ui.history.staleError", { error: m.stale })) : null,
        (m.groups || []).map((g) => h("section", { class: "wh-group", "aria-labelledby": "wh-g-" + g.id },
          h("div", { class: "wh-gname", id: "wh-g-" + g.id }, h("span", { class: "wh-gtitle" }, g.name), g.main ? h("span", { class: "wh-sub" }, t("ui.history.mainDir")) : g.branch ? h("span", { class: "wh-sub" }, g.branch) : null),
          (g.items || []).length ? h("ul", { class: "wh-rows" }, g.items.map((item) => h("li", null, rowButton(item)))) : h("p", { class: "wh-state wh-faint" }, t("ui.history.groupEmpty")))),
        h("p", { class: "wh-foot" }, t("ui.history.shown", { shown: total, count: m.count }),
          m.more > 0 ? h("button", { type: "button", class: "wh-btn", "data-ah-focus": "more", on: { click: () => call("onMore") } }, t("ui.history.moreRows", { n: m.more })) : null),
        m.truncated ? h("p", { class: "wh-state wh-faint" }, t("ui.history.truncated")) : null);
    }

    function actionBox(item) {
      if (item.action && item.canAct) {
        return h("button", { type: "button", class: "wh-btn wh-btn-act", "data-ah-focus": "act", on: { click: () => call("onAction", item.key) } }, t((AH_ACTIONS[item.action] || AH_ACTIONS.unarchive)[0]));
      }
      const hint = !item.action ? (item.abandoned ? t("ui.history.abandonedTip") : t("ui.history.noAction"))
        : t(Object.prototype.hasOwnProperty.call(AH_WHY, item.why) ? AH_WHY[item.why] : AH_WHY.login);
      const login = item.action && item.why === "login" && deps && typeof deps.onLogin === "function"
        ? h("button", { type: "button", class: "wh-btn", "data-ah-focus": "login", on: { click: () => call("onLogin") } }, t("ui.session.login")) : null;
      return login ? h("div", { class: "wh-why" }, h("p", { class: "wh-hint" }, rich(hint)), login) : h("p", { class: "wh-hint" }, rich(hint));
    }
    function confirmBox(c) {
      const [label, title, bodyKey] = AH_ACTIONS[c.action === "unarchive" && c.restoreTo === "approved" ? "restore" : c.action] || AH_ACTIONS.unarchive;
      const params = clean({ title: c.title, tab: c.tab, sha: String(c.sha || "").slice(0, 12) });
      const top = topLayerIn(doc);
      const named = top ? {} : { role: "alertdialog", "aria-labelledby": "wh-ct", "aria-describedby": "wh-cb" };
      const box = h("div", Object.assign({ class: "wh-confirm", on: { keydown: (e) => { if (e.key === "Escape") { e.preventDefault(); if (!c.busy) call("onCancel"); } } } }, named),
        h("div", { class: "wh-ctitle", id: "wh-ct" }, t(title)),
        h("div", { id: "wh-cb" },
          h("p", null, rich(t(bodyKey, params))),
          (c.copyNames || []).length ? h("p", { class: "wh-hint" }, t("ui.history.confirmCopies", { tabs: plain(c.copyNames.join(", ")) })) : null,
          h("p", { class: "wh-hint" }, rich(t("ui.history.confirmWarn")))),
        h("div", { class: "wh-cfoot" },
          h("button", { type: "button", class: "wh-btn", "data-ah-focus": "cancel", disabled: c.busy ? true : null, on: { click: () => call("onCancel") } }, t("ui.history.cancel")),
          h("button", { type: "button", class: "wh-btn wh-btn-act", "data-ah-focus": "confirm", disabled: c.busy ? true : null, "aria-busy": c.busy ? "true" : null, on: { click: () => call("onConfirm") } },
            c.busy ? t("ui.history.saving") : t(label))));
      return top ? h("dialog", { class: "wh-dialog", "aria-labelledby": "wh-ct", "aria-describedby": "wh-cb" }, box) : box;
    }
    function drawPreview(m) {
      parts.prev.setAttribute("aria-label", t("ui.history.previewAria"));
      const p = m.preview;
      if (!p || !p.item) { fill(parts.prev, h("p", { class: "wh-state wh-faint" }, t("ui.history.previewEmpty"))); return; }
      const item = p.item;
      // The links of the Markdown preview (`a.md-doc`, `a.md-anchor`, href "#") are for the page of a tab: here they neither jump nor touch the address. An anchor
      // inside the plan scrolls to it; a link to another document does nothing (the plan is read-only here).
      const docBox = h("div", { class: "wh-doc", on: { click: (e) => {
        const a = e.target && typeof e.target.closest === "function" ? e.target.closest("a.md-doc, a.md-anchor") : null;
        if (!a) return;
        e.preventDefault();
        if (a.classList.contains("md-anchor")) {
          const id = a.getAttribute("data-anchor");
          for (const el of docBox.querySelectorAll("[id]")) if (el.getAttribute("id") === id && typeof el.scrollIntoView === "function") { el.scrollIntoView({ behavior: "smooth" }); break; }
        }
      } } });
      if (p.state === "loading") docBox.appendChild(h("p", { class: "wh-state", role: "status" }, t("ui.history.previewLoading")));
      else if (p.state === "error") {
        docBox.appendChild(h("p", { class: "wh-state", role: "alert" }, t("ui.history.previewError", { error: p.error || "" })));
        docBox.appendChild(h("button", { type: "button", class: "wh-btn", "data-ah-focus": "retryPreview", on: { click: () => call("onRetryPreview") } }, t("ui.history.retry")));
      }
      else {
        const md = h("div", { class: "md" });
        docBox.appendChild(md);
        if (typeof deps.mountHtml === "function") deps.mountHtml(md, p.html || "");
      }
      fill(parts.prev,
        h("div", { class: "wh-hhead" },
          h("div", { class: "wh-htext" }, h("div", { class: "wh-htitle" }, item.title || item.path),
            h("div", { class: "wh-hmeta" }, t("ui.history.previewMeta", { tab: item.ownerName, status: statusText(item.status) }), p.truncated ? " · " + t("ui.history.previewTruncated") : "")),
          actionBox(item)),
        m.confirm ? confirmBox(m.confirm) : null,
        docBox);
      const question = m.confirm ? parts.prev.querySelector("dialog.wh-dialog") : null;
      if (question) showTop(question, () => { if (!m.confirm.busy) call("onCancel"); });
      if (m.confirm && !confirmShown) want = "cancel";
      // The question closed (a button that was disabled while the write was in flight has lost the focus): back to the button that opened it, or to the row.
      if (!m.confirm && confirmShown && (lastIn === "cancel" || lastIn === "confirm")) { want = root.querySelector('[data-ah-focus="act"]') ? "act" : "row:" + item.key; }
      if (!m.confirm) lastIn = null;
      confirmShown = Boolean(m.confirm);
    }
    function drawNotice(m) {
      const n = m.notice;
      if (!n) { fill(parts.notice); return; }
      const kind = n.kind === "error" || n.kind === "warn" ? n.kind : "info";
      fill(parts.notice, h("div", { class: "wh-alert wh-alert-" + kind, role: kind === "info" ? "status" : "alert" },
        h("div", { class: "wh-alert-text" }, h("div", { class: "wh-alert-title" }, rich(say(n.title))),
          n.body ? h("div", null, rich(say(n.body))) : n.bodyText ? h("div", null, String(n.bodyText)) : null),
        h("button", { type: "button", class: "wh-btn", "data-ah-focus": "dismiss", on: { click: () => call("onDismiss") } }, t("ui.history.dismiss"))));
    }
    function drawHead(m) {
      fill(parts.head, h("h2", { class: "wh-title" }, t("ui.history.heading")),
        h("button", { type: "button", class: "wh-btn", "data-ah-focus": "refresh", disabled: m.refreshing ? true : null, "aria-busy": m.refreshing ? "true" : null, on: { click: () => call("onReload") } }, t("ui.history.refresh")));
    }
    function render(m) {
      const lang = m.lang || "";
      redraw("head", JSON.stringify([lang, Boolean(m.refreshing)]), () => drawHead(m));
      redraw("notice", JSON.stringify([lang, m.notice || null]), () => drawNotice(m));
      redraw("list", JSON.stringify([lang, m.state, m.error, m.stale, m.groups, m.count, m.truncated, m.more]), () => drawList(m));
      redraw("prev", JSON.stringify([lang, m.preview, m.confirm]), () => drawPreview(m));
    }
    root.classList.add("wh-root");
    fill(root, parts.head, parts.notice, body);
    parts.list.addEventListener("keydown", onListKey);
    root.addEventListener("focusin", (e) => { const key = e.target && typeof e.target.getAttribute === "function" ? e.target.getAttribute("data-ah-focus") : null; if (key) { focused = key; lastIn = key; } });
    root.addEventListener("focusout", () => { focused = null; });
    return {
      update(next) { model = JSON.parse(JSON.stringify(next || {})); render(model); },
      // The language changed (the texts come from `deps.t`): every part is drawn again with the same model.
      relabel() { if (!model) return; for (const name of Object.keys(sigs)) sigs[name] = null; render(model); },
      focusFirst() { const first = root.querySelector(".wh-row"); if (first && typeof first.focus === "function") first.focus({ preventScroll: true }); },
      destroy() { model = null; for (const name of Object.keys(sigs)) sigs[name] = null; fill(root); root.classList.remove("wh-root"); },
    };
  }

  return { create, createDashboard: create, createTabBar, createArchiveView, buildSprite, SPRITE, version: 1, defaultTexts: DEFAULT_TEXTS };
});
