/* dashboard — wspólny renderer „JSON (kontrakt schema 1) → DOM” strony `skillsync dashboard serve` i webview Skippstera.
   Czysty widok: nie zna transportu ani pamięci przeglądarki (wszystko wstrzykuje host w `deps`), a DOM buduje wyłącznie
   przez createElement/createElementNS/textContent/setAttribute (zgodność z CSP webview: bez wstrzykiwania HTML, bez atrybutu
   stylu, bez handlerów inline). Kontrakt: library/skills/dashboard/SKILL.md, sekcja „Renderer”.
   Wewnętrzne API renderera (nie publiczny kontrakt CLI); odstępstwa od pierwotnego szkicu w specu DASH-2 (zaakceptowane przez Dyspozytora):
   nazwy `create`/`update` (aliasy `createDashboard`/`render`); brak `deps.transport` (transport to host przez `actions`); opisy przez
   `setDescriptions(map)`; wymagane `deps.now`; `actions.approve({kind, sha, path, word, planState})` zwraca Promise `{ok, word, text}`;
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
  // Strona serwera wymaga tokenu zgód (approvals.tokenRequired), a ta karta go nie ma: przyciski nieaktywne z jednym powodem (ui.token.required).
  const ID_RE = /(?<![\w-])([A-Z]{1,3}-?\d+[a-z]?)(?![\w-])/g;
  const PHRASE_RE = /\b(zrobione|w toku|ryzyko|PASS|FAIL|BLOCKED|restart sesji)\b/gi;
  const FORBIDDEN_ATTR = /^(?:on|style$|srcdoc$)/i;

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
    "ui.ago.day": "{n} d temu",
    "ui.ago.hour": "{n} h temu",
    "ui.ago.justNow": "przed chwilą",
    "ui.ago.min": "{n} min temu",
    "ui.ago.sec": "{n} s temu",
    "ui.ahead.label": { "few": "{n} niewypchnięte", "many": "{n} niewypchniętych", "one": "{n} niewypchnięty" },
    "ui.ahead.tip": { "few": "{n} commity lokalnie przed {remote}; „CP” je wypchnie", "many": "{n} commitów lokalnie przed {remote}; „CP” je wypchnie", "one": "{n} commit lokalnie przed {remote}; „CP” je wypchnie" },
    "ui.ahead.tipNoRemote": { "few": "{n} commity lokalnie przed {remote} (nie ma jej jeszcze na origin); „CP” je wypchnie", "many": "{n} commitów lokalnie przed {remote} (nie ma jej jeszcze na origin); „CP” je wypchnie", "one": "{n} commit lokalnie przed {remote} (nie ma jej jeszcze na origin); „CP” je wypchnie" },
    "ui.approve.confirm": "Na pewno? Kliknij, by dać „{word}”{to}",
    "ui.approve.hereOnlyTip": "Zgoda tylko z przeglądarki na komputerze z sesją (localhost albo --approve-host); tu wpisz „{word}” w sesji",
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
    "ui.eta.over": "{op}: przekroczono ETA o {over}",
    "ui.eta.until": "{op}, koniec ok. {until}",
    "ui.expert.toAll": "do wszystkich ekspertów",
    "ui.expert.toList": "do: {list}",
    "ui.expert.toast": "Pytania do eksperta: {open} otwartych ({blocking} blokujących)",
    "ui.feed.title": "Ostatnie checkpointy",
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
    "ui.proto.open": "Otwórz prototyp {slug}",
    "ui.protoTab.aria": "Prototyp {slug}",
    "ui.protoTab.ariaAccepted": "Prototyp {slug}, zaakceptowane {acc}",
    "ui.protoTab.ariaOff": "Prototyp {slug}: serwer wyłączony",
    "ui.protoTab.label": "Prototyp",
    "ui.protoTab.off": "(serwer wyłączony)",
    "ui.protoTab.tip": "Otwórz prototyp {slug} (port {port})",
    "ui.protoTab.tipAccepted": "Otwórz prototyp {slug} (port {port}); zaakceptowane ekrany {accepted} z {screens}",
    "ui.protoTab.tipOff": "Serwer prototypów (port {port}) nie działa — uruchom go, żeby otworzyć {slug}",
    "ui.q.blocking": "blokujące",
    "ui.q.commentsAfter": { "few": "{n} komentarze po akceptacji", "many": "{n} komentarzy po akceptacji", "one": "{n} komentarz po akceptacji" },
    "ui.q.itemsTitle": "Pytania do eksperta",
    "ui.q.iteration": "iteracja {n}",
    "ui.q.noProtos": "Brak prototypów",
    "ui.q.noQuestions": "Brak pytań",
    "ui.q.open": "Otwórz pytania",
    "ui.q.openAria": "Otwórz pytania przebiegu {slug}",
    "ui.q.openProto": "Otwórz prototyp",
    "ui.q.openThreads": { "few": "{n} wątki otwarte", "many": "{n} wątków otwartych", "one": "{n} wątek otwarty" },
    "ui.q.protoTitle": "Prototypy",
    "ui.q.screens": { "few": "{accepted} z {n} ekranów zaakceptowanych", "many": "{accepted} z {n} ekranów zaakceptowanych", "one": "{accepted} z {n} ekranu zaakceptowanych" },
    "ui.q.stale": "nieaktualne {n}",
    "ui.q.textMissing": "treść niedostępna",
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
    "ui.tab.questions": "Pytania",
    "ui.tab.questionsTip": "Prototypy i pytania do eksperta",
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
    "ui.token.required": "Zgody wymagają adresu z tokenem: otwórz adres z konsoli serwera albo z `skillsync dashboard url` (bez niego strona jest tylko do odczytu).",
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
    const texts = deps.texts || {};
    const L = { lang: "pl", locale: "pl-PL", plural: "pl", texts: null };
    function setLanguage(d) {
      const lang = d && typeof d.lang === "string" && /^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/.test(d.lang) ? d.lang : "pl";
      const own = lang !== "pl" && d && d.texts && typeof d.texts === "object" && !Array.isArray(d.texts) ? d.texts : null;
      const changed = lang !== L.lang || own !== L.texts;
      L.lang = lang; L.locale = localeOf(lang); L.texts = own; L.plural = own ? lang : "pl";
      return changed;
    }
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
    const hostLive = texts.live && typeof texts.live === "object" ? texts.live : {};
    const hostTip = texts.liveTip && typeof texts.liveTip === "object" ? texts.liveTip : {};
    const liveText = (mode) => (Object.prototype.hasOwnProperty.call(hostLive, mode) ? hostLive[mode] : LIVE_KEY[mode] ? tr(LIVE_KEY[mode]) : undefined);
    const liveTip = (mode) => (Object.prototype.hasOwnProperty.call(hostTip, mode) ? hostTip[mode] : LIVE_TIP_KEY[mode] ? tr(LIVE_TIP_KEY[mode]) : undefined);
    const noConnection = () => (typeof texts.noConnection === "string" ? texts.noConnection : tr("ui.noConnection"));
    // Czy ten host może dawać zgody: domyślnie approvals.here z JSON-a (/api/status); host bez tego pola (webview) podaje canApprove.
    const canApprove = (d) => (typeof deps.canApprove === "function" ? Boolean(deps.canApprove(d)) : Boolean(d.approvals && d.approvals.here));
    const viewport = typeof deps.viewport === "function" ? deps.viewport
      : () => { const v = doc.defaultView; return v ? { width: v.innerWidth, height: v.innerHeight } : { width: 1024, height: 768 }; };

    const st = {
      data: null, prev: null, view: "progress", doc: null, docData: null, desc: null,
      armed: null, armedTimer: null, approving: false, notice: null, noticeTimer: null, visible: true, openPhases: {},
      testsOpen: {}, testsAttempt: {}, testsLoading: {}, testsError: {}, testsLoaded: {}, testsStep: null, silenceShown: false, silenceMin: 0, descFor: null, descPinned: false, docButtons: [], docGroupsOpen: {}, docGroupSeen: null, docFocus: null, docRebuilding: false, timers: new Set(), frames: new Set(), destroyed: false,
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
    ui.protoLink = h("span", { id: "protoLink", class: "tab-extra", hidden: true });
    ui.result = h("section", { class: "result-box", id: "resultBox", "aria-labelledby": "resultTitle", hidden: true, on: { click: mdClick } });
    ui.glossary = h("div", { class: "glossary-box", id: "glossaryBox" });
    ui.qProtos = h("div", { class: "q-block", id: "qProtos" });
    ui.qItems = h("div", { class: "q-block", id: "qItems", on: { click: mdClick } });
    ui.acts = h("div", { class: "acts", id: "acts", on: { click: mdClick } },
      h("div", { class: "act skeleton" }), h("div", { class: "act skeleton" }), h("div", { class: "act skeleton wide" }));
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
      ui.acts, ui.result, ui.glossary,
      h("div", { class: "work" }, ui.phases,
        h("aside", { class: "feed-box", "aria-labelledby": "feedTitle" },
          h("h2", { id: "feedTitle", "data-desc": "checkpoints", tabindex: "0" }, svg("svg", { class: "ic" }, svg("use", { href: "#i-flag" })), labelNode("ui.feed.title")),
          ui.parseWarn, ui.feed)));
    ui["view-tests"] = h("section", { id: "view-tests", role: "tabpanel", "aria-labelledby": "tab-tests", hidden: true }, ui.tests);
    ui["view-docs"] = h("section", { id: "view-docs", role: "tabpanel", "aria-labelledby": "tab-docs", hidden: true },
      h("div", { class: "docs" }, ui.docList, ui.docView));
    ui["view-questions"] = h("section", { id: "view-questions", role: "tabpanel", "aria-labelledby": "tab-questions", hidden: true },
      ui.qProtos, ui.qItems);
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
      h("div", { class: "top-right" }, ui.themeBtn || null, ui.live, h("div", { class: "plan-pick" }, ui.planBtn, ui.planMenu))),
      ui.waitBanner,
      ui.tabs = h("nav", { class: "tabs", role: "tablist", "aria-label": tr("ui.tabs.aria"), on: { keydown: tabKey } }, ui["tab-progress"], ui["tab-tests"], ui["tab-docs"], ui["tab-questions"], ui.protoLink),
      h("main", null, ui["view-progress"], ui["view-tests"], ui["view-docs"], ui["view-questions"]),
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
        const host = g.remote ? (g.remote.host === "azure" ? "Azure DevOps" : "GitHub") : "";
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
        h("span", null, trRich("ui.foot.project", { path: project.path })),
        h("span", null, tr("ui.foot.status", { mode: tr(d.approvals && d.approvals.enabled && canApprove(d) && !isViewer(d) ? "ui.foot.readApprove" : "ui.foot.readOnly"),
          time: new Date(d.generatedAt).toLocaleTimeString(L.locale) })));
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
      const tip = isActive ? (quiet ? tr(since ? "ui.stage.quietTipSince" : "ui.stage.quietTip", { n: quiet, since }) : tr("ui.stage.activeSince", { since: since || "—" })) + (s.tip ? " · " + s.tip : "") : s.tip;
      return h("span", { class: "stage" + (isDone ? " is-done" : "") + (isActive ? " is-active" : "") + (quiet ? " is-quiet" : "") + (screens ? " has-screens" : ""), "data-state": s.state ? state : null, ...desc(s.key, tip) },
        isDone ? icon("i-check") : null, isActive ? h("span", { class: "stage-dot", "aria-hidden": "true" }) : null, s.label,
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
          body = [h("div", { class: "act-detail" }, a.detail || " "), actTimeLine(d, a.key), h("div", { class: "stages" }, stages), extra, warn, h("div", { class: "act-actions" }, docLink, approve)];
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
            return h("li", { class: `step s-${s.state}${changed ? " flash" : ""}${s.tests ? " has-tests" : ""}`, ...desc(/^(?:zamknięcie|closure)/i.test(s.title || "") ? "closure" : "substep", tip) },
              h("span", { class: "st", "aria-label": stepLabel(tr, s.state) ?? "" }, mark), h("span", { class: "sid" }, s.id), inl("tt", s.title, s.titleHtml),
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
      const skipped = Array.isArray(t.skipped) ? skippedNote(t.skipped) : null;
      const secs = t.sections.filter((sec) => sec && typeof sec === "object");
      if (skipped && !secs.length) {
        fill(ui.tests, skipped, h("p", { class: "empty" }, tr("ui.tests.empty")));
        ui["view-tests"].hidden = st.view !== "tests";
        return;
      }
      fill(ui.tests,
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
        const at = a.given && a.given.at ? new Date(a.given.at).toLocaleTimeString(L.locale, { hour: "2-digit", minute: "2-digit" }) : "";
        return h("button", { class: `${cls} is-given`, type: "button", disabled: true, "data-state": "after", "data-gate": kind, ...desc(GATE_KEY[kind], a.reason) },
          icon("i-check"), `${a.word}${to}${at ? " " + at : ""}`);
      }
      if (a.state !== "waiting") {
        return h("button", { class: `${cls} is-waiting`, type: "button", disabled: true, "data-state": "running", "data-gate": kind, ...desc(GATE_KEY[kind], viewer ? tr("ui.viewer.readOnly") : a.reason) }, `${a.word}${to}`);
      }
      // Host bez hasApproveToken (webview Skippstera: zgody idą przez CLI) nigdy nie jest „bez tokenu”.
      const needsToken = !viewer && Boolean(ap.here && ap.tokenRequired && typeof deps.hasApproveToken === "function" && !deps.hasApproveToken());
      if (!canApprove(d) || viewer || needsToken) {
        return h("button", { class: cls, type: "button", disabled: true, "aria-disabled": "true", "data-state": "waiting", "data-gate": kind,
          ...desc(GATE_KEY[kind], viewer ? tr("ui.viewer.readOnly") : needsToken ? tr("ui.token.required") : tr("ui.approve.hereOnlyTip", { word: a.word })) }, icon("i-check"), a.word);
      }
      const armed = st.armed === kind;
      const label = st.approving && armed ? tr("ui.approve.saving") : tr(armed ? "ui.approve.confirm" : "ui.approve.label", { word: a.word, to });
      const sha = String(a.sha || "");
      const what = kind === "cp" || kind === "ready" ? `${a.reason ? a.reason + "; " : ""}HEAD ${sha.slice(0, 8)}` : tr("ui.approve.versionOf", { path: a.path, sha: sha.slice(0, 8) });
      return h("button", { class: `${cls}${armed ? " is-armed" : ""}`, type: "button", "data-state": "waiting", "data-approve": kind, "data-gate": kind,
        ...desc(GATE_KEY[kind], tr("ui.approve.tip", { word: a.word, what })), on: { click: () => approve(kind) } }, icon("i-check"), label);
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
        res = await actions.approve({ kind, sha: a.sha, path: a.path, word: a.word, planState: a.planState });
      } catch {
        res = { ok: false, word: a.word, text: noConnection() };
      }
      if (st.destroyed) return;
      st.approving = false; st.armed = null;
      if (res) setNoticeState(res);
      render();
      if (typeof actions.refresh === "function") actions.refresh();
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
      const text = tr("ui.expert.toast", { open, blocking });
      const to = runsAddressees(runs);
      return h("div", { class: "wait-item is-info", "data-questions-toast": "" },
        icon("i-bell"),
        h("div", { class: "wait-txt" }, h("strong", null, text), to ? h("span", { "data-addressees": "" }, to) : null),
        url ? h("a", { class: "wait-go", href: url, target: "_blank", rel: "noopener",
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
        url ? h("a", { class: "wait-go", href: url, target: "_blank", rel: "noopener", on: { click: linkClick("prototype", slug, url) } }, tr("ui.proto.open", { slug })) : null);
    }
    // Toast „szacowany koniec” (`d.eta` z `eta:` najnowszego checkpointu): stan, nie wynik akcji, więc bez timera i bez „×”. Serwer nie czyta
    // zegara, więc przekroczenie liczy `now` względem `untilIso` (bez `untilIso` tylko godzina). Tylko tekst z `h()`.
    function etaText(d) {
      const e = d && d.eta;
      if (!e || typeof e.operation !== "string" || !e.operation || typeof e.until !== "string") return null;
      const late = e.untilIso ? Math.floor((now() - Date.parse(e.untilIso)) / 60000) : 0;
      const over = late >= 60 ? tr("ui.dur.hm", { h: Math.floor(late / 60), m: late % 60 }) : tr("ui.dur.min", { n: late });
      return late >= 1 ? tr("ui.eta.over", { op: e.operation, over }) : tr("ui.eta.until", { op: e.operation, until: e.until });
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
          h("strong", null, n.ok ? tr("ui.toast.approved", { word: n.word }) : tr("ui.toast.notSaved", { word: n.word })),
          h("span", null, n.text)),
        n.ok ? wordTools(d, "dalej") : null,
        h("button", { class: "wait-x", type: "button", "data-notice-close": "", "aria-label": tr("ui.close"), on: { click: () => { setNoticeState(null); render(); } } }, "×")) : null;
      const qnote = questionsToast(d);
      const enote = etaToast(d);
      const cnote = commentsToast(d);
      if (!items.length && !note && !qnote && !enote && !cnote) { el.hidden = true; fill(el); return; }
      el.hidden = false;
      fill(el, enote, qnote, cnote, note, items.map((w) => {
        let link = null;
        if (w.link && w.link.kind === "doc") {
          link = h("button", { class: "wait-go", type: "button", "data-wait-doc": w.link.path, on: { click: () => openDoc(w.link.path) } }, tr("ui.wait.openDoc", { name: baseName(w.link.path) }));
        } else if (w.link && w.link.kind === "prototype") {
          const url = protoUrl(d, w.link.slug);
          link = h("a", { class: "wait-go", href: safeUrl(url), target: "_blank", rel: "noopener", on: { click: linkClick("prototype", w.link.slug, url) } }, tr("ui.proto.open", { slug: w.link.slug }));
        } else if (w.link && w.link.kind === "expert") {
          const url = safeUrl(expertsUrl(d, w.link.url));
          link = url ? h("a", { class: "wait-go", href: url, target: "_blank", rel: "noopener", "data-wait-expert": "",
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

    // ---------------------------------------------------------------- prototyp (przy zakładkach)
    function renderProtoLink(d) {
      const el = ui.protoLink;
      const pr = d.prototype;
      if (!pr || !pr.slug) { el.hidden = true; fill(el); return; }
      const acc = d.spec && d.spec.prototypeAcceptance;
      const accTxt = acc && acc.screens ? `${acc.accepted}/${acc.screens}` : "";
      const lbl = h("span", { class: "lbl" }, tr("ui.protoTab.label") + (accTxt ? " · " : ""));
      const url = protoUrl(d, pr.slug);
      el.hidden = false;
      fill(el, pr.up
        ? h("a", { class: "proto-link", "aria-label": accTxt ? tr("ui.protoTab.ariaAccepted", { slug: pr.slug, acc: accTxt }) : tr("ui.protoTab.aria", { slug: pr.slug }), href: safeUrl(url), target: "_blank", rel: "noopener",
          "data-tip": acc && acc.screens ? tr("ui.protoTab.tipAccepted", { slug: pr.slug, port: pr.port, accepted: acc.accepted, screens: acc.screens }) : tr("ui.protoTab.tip", { slug: pr.slug, port: pr.port }),
          on: { click: linkClick("prototype", pr.slug, url) } }, icon("i-proto"), lbl, accTxt)
        : h("span", { class: "proto-link is-off", "aria-label": tr("ui.protoTab.ariaOff", { slug: pr.slug }),
          "data-tip": tr("ui.protoTab.tipOff", { slug: pr.slug, port: pr.port }) },
          icon("i-proto"), lbl, accTxt, h("span", { class: "lbl" }, " " + tr("ui.protoTab.off"))));
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

    // ---------------------------------------------------------------- zakładka „Pytania” (prototypy i pytania do eksperta)
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
    // Zakładka „Pytania” dotyczy bieżącego przebiegu: serwer daje `expertQuestions.current` (obiekt albo null = ukryta).
    // Brak klucza `current` (starszy serwer): przybliżenie lokalne po `prototype.slug` (prototypy, pytania, licznik z `runs[]`).
    function questionsView(d) {
      const eq = d.expertQuestions && typeof d.expertQuestions === "object" ? d.expertQuestions : null;
      const objs = (v) => (Array.isArray(v) ? v : []).filter((x) => x && typeof x === "object");
      if (eq && Object.prototype.hasOwnProperty.call(eq, "current")) {
        const c = eq.current;
        if (!c || typeof c !== "object" || Array.isArray(c)) return null;
        return { protos: objs(c.prototypes), items: objs(c.items), open: c.open };
      }
      const slug = d.prototype && typeof d.prototype === "object" && typeof d.prototype.slug === "string" ? d.prototype.slug : "";
      if (!slug || !(Array.isArray(d.prototypes) || (eq && Array.isArray(eq.items)))) return null;
      const run = (eq && Array.isArray(eq.runs) ? eq.runs : []).find((r) => r && typeof r === "object" && r.slug === slug);
      return { protos: objs(d.prototypes).filter((p) => p.slug === slug), items: objs(eq && eq.items).filter((q) => q.runSlug === slug), open: run ? run.open : null };
    }
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
        p.iteration != null && p.iteration !== "" ? h("span", { class: "q-fact" }, tr("ui.q.iteration", { n: p.iteration })) : null,
        screens != null ? h("span", { class: "q-fact" }, tr("ui.q.screens", { accepted: accepted || 0 }, screens)
          + (stale ? ", " + tr("ui.q.stale", { n: stale }) : "")) : null,
        wr ? h("span", { class: "chip s-active q-writing" }, tr("ui.q.writing") + (wDone != null ? " " + (wPlan ? tr("ui.q.writingOfPlan", { done: wDone, plan: wPlan }, wPlan) : tr("ui.q.writingDone", { done: wDone }, wDone)) : "")) : null,
        h("span", { class: "q-fact" }, tr("ui.q.openThreads", {}, openThreads)),
        caa > 0 ? h("span", { class: "chip s-warn q-after" }, tr("ui.q.commentsAfter", {}, caa)) : null,
      ];
      return h("li", { class: "q-row q-proto", "data-proto": slug },
        mono(slug || "—"), h("span", { class: "q-facts" }, facts),
        url ? h("a", { class: "q-go", href: url, target: "_blank", rel: "noopener", "aria-label": tr("ui.proto.open", { slug }), on: { click: linkClick("prototype", slug, url) } }, tr("ui.q.openProto")) : null);
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
        url ? h("a", { class: "q-go", href: url, target: "_blank", rel: "noopener", "aria-label": tr("ui.q.openAria", { slug: slug || "" }).trim(),
          on: { click: linkClick("experts", slug, url) } }, tr("ui.q.open")) : null);
    }
    function renderQuestionsTab(d) {
      const qv = questionsView(d);
      ui["tab-questions"].hidden = !qv;
      if (!qv) {
        fill(ui.qProtos); fill(ui.qItems); ui.qCount.textContent = "";
        ui["view-questions"].hidden = true;
        if (st.view === "questions") setView("progress", false);
        return;
      }
      const protos = qv.protos, items = qv.items.slice(0, 50);
      ui.qCount.textContent = Number.isFinite(qv.open) && qv.open > 0 ? String(qv.open) : "";
      fill(ui.qProtos, h("h2", { class: "q-title" }, tr("ui.q.protoTitle")),
        protos.length ? h("ul", { class: "q-list" }, protos.map((p) => protoRow(d, p))) : h("div", { class: "empty" }, tr("ui.q.noProtos")));
      fill(ui.qItems, h("h2", { class: "q-title" }, tr("ui.q.itemsTitle")),
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

  return { create, createDashboard: create, buildSprite, SPRITE, version: 1, defaultTexts: DEFAULT_TEXTS };
});
