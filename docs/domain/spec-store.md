---
title: 'Spec i plan poza gitem: tryb nieśledzony (`tracked: false`), migawka, archiwum speców i odbiór planu'
tags: [skillsync, workflow, specs, snapshot, archive, git, approvals, closure]
paths: ['skillsync_workflow/specs.py', 'skillsync_workflow/spec_snapshot.py', 'skillsync_workflow/status.py', 'skillsync_workflow/quality.py', 'skillsync_workflow/test_gate.py', 'skillsync_workflow/retro.py', 'library/hooks/check-closure.py', 'library/skills/feature/references/spec-store.md', 'tests/test_skillsync_specs*.py', 'tests/test_skillsync_spec_snapshot.py', 'tests/test_skillsync_repo_hygiene.py', 'tests/test_skillsync_closure.py']
source: "spec CORE-SPECSTORE (2026-11-19; K1–K15, sekcje Ustalenia, Kontrakty, Ryzyka, Migracja); skillsync_workflow/specs.py (`specs_info`, `specs_findings`), spec_snapshot.py (`snapshot`, `check`, `_checkout_kind`), status.py (`record_approval`, `plan_state_for_run`, `acceptance_pending`); tests/test_skillsync_repo_hygiene.py:42-45"
verified: 2026-11-19
---
Reguła: spec i plan projektu nie muszą być w historii gita. Tryb nieśledzony (`tracked: false`) włącza się, gdy reguła w śledzonym `.gitignore` korzenia repozytorium ignoruje oba katalogi (`/docs/specs/` i `/docs/plans/`), albo gdy manifest ustawia `workflow.specs.tracked: false`. Klucz wygrywa z wykrywaniem. Tryb śledzony (`tracked: true`) jest domyślny i zachowuje dotychczasowe zachowanie.

Źródło prawdy: `skillsync route <projekt>`, pole `specs {tracked, archive, source}`. Procedura nie zgaduje trybu po stanie gita.

Wykrywanie (`specs_info`, `skillsync_workflow/specs.py`):
- Klucz `workflow.specs` czytany jest z surowego manifestu projektu; `workflow/defaults.yaml` i `workflow/projects/<slug>.yaml` go ignorują. Wtedy `source: manifest`.
- Bez klucza: `git check-ignore --no-index -v` dla `docs/specs/x.md` i `docs/plans/x.md` w środowisku izolowanym (bez `HOME`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`). Liczy się tylko reguła z pliku `.gitignore` o ścieżce względnej w drzewie repozytorium. Negacja `!`, `.git/info/exclude`, `core.excludesFile` i globalny plik `.gitignore` nie liczą się: tryb nie zależy od maszyny.
- Oba katalogi ignorowane = `source: gitignore`, `tracked: false`. Jeden katalog, brak gita albo błąd gita = `source: default`, `tracked: true`.
- Projekt, który nie jest korzeniem repozytorium (zagnieżdżony), dostaje `default`: reguły nadrzędnego `.gitignore` go nie dotyczą.
- Para `tracked: true` z regułą obu katalogów = `source: invalid` (błąd `doctor`). Procedura nie zakłada trybu i pyta, bo `git add -f` na tych katalogach jest zakazany.

Konsekwencje w procedurze (`library/skills/feature/references/spec-store.md`):
- „GO plan” nie robi commita speca i planu. Commit lokalny obejmuje tylko to, co śledzone (np. prototyp). Dowodem kroku `go-plan` jest migawka, nie pusty commit.
- Szkic speca z głównego checkoutu przenosi się do worktree: kopia, porównanie `git hash-object` źródła i kopii, dopiero potem źródło do kosza. Rozbieżność sha = STOP.
- Sprzątanie worktree wymaga `skillsync spec snapshot --check --to <korzeń kopii poza worktree>` z `PASS` (albo archiwum z `PASS`). Sama migawka w worktree nie chroni, bo znika razem z nim.

Migawka (`skillsync spec snapshot`):
- Kopiuje spec (z linii `Spec:` planu o `Przebieg: <id>`) i plan do `.skillsync/runs/<id>/docs/specs|plans/`, a przy `workflow.specs.archive` także do `<archiwum>/<projekt>/<id>/specs|plans/`.
- Compare-and-swap osobno dla każdego celu; sha ostatniego zapisu na parę (cel, plik) trzyma `docs/.saved.json` w katalogu przebiegu. Cel zmieniony przez kogoś innego (albo bez wpisu i z innym sha) = `BLOCKED` (kod 2) do decyzji człowieka, czyli `--replace`.
- Wynik mieszany: kod najwyższej wagi (2 > 1 > 0). Zapis atomowy (plik tymczasowy i `os.replace`), bez dowiązań w ścieżce celu, prawa 0700 (katalogi) i 0600 (pliki).
- `--check` niczego nie zapisuje. W połączonym worktree bez celu poza nim wynik to `BLOCKED`, bo zapomniane `--to` nie może dać `PASS`.

Archiwum speców (`workflow.specs.archive`): opcjonalne; tylko `~/` albo ścieżka bezwzględna (bezwzględna daje ostrzeżenie `doctor`), bez `..`. Jest kopią poza repozytorium. Nie jest magazynem przebiegów ani pytań i nie ma nic wspólnego z `skillsync plan archive` ani statusem planu `archived`.

Odbiór planu i `planState`:
- Wpis `CP` albo `READY` w `approvals.jsonl` dostaje pole `planState` (status planu z `Przebieg:` w chwili zgody). Pole `plan` zostaje ścieżką planu.
- Przy `tracked: false` odbiór (`needsHuman: acceptance`) zamyka ostatni wpis pary (przebieg, słowo) z `planState: done`, gdy bieżący plan jest `done`. Historii gita się nie używa; zmiana innych linii planu (np. `Oddanie:`) nie otwiera odbioru.
- Duplikat zgody: ta sama para (słowo, sha, `planState`) względem ostatniego wpisu tego słowa w przebiegu.
- Stan przycisku `after` w tym trybie: wpis bez pola przy planie `done` nie daje „zgody danej”, więc stara zgoda nie blokuje nowej. Przy `tracked: true` oceną rządzi git, jak dotąd.
- Dashboard `POST /api/approve` przyjmuje `planState`; gdy bieżący status planu jest inny, odpowiada `409`, a nie-napis daje `400`. Kontrakt: [dashboard-contract](../dashboard-contract.md).

Hook zamknięcia (`library/hooks/check-closure.py`) czyta także pliki ignorowane (`git status --ignored`, wpisy `!!`). Plan `done` bez rekordu `final` albo `in_progress` ze wszystkimi krokami odhaczonymi blokuje turę raz na sesję. Projekty dostają to po `skillsync link`, bo kopia hooka się odświeża.

Bramka testów: `work_tree_id` nie zmienia się po edycji speca i planu przy regule (`git add -A` pomija ignorowane); `TREE_EXCLUDES` bez zmian. Retrospektywa (`retro.py`, `_report_dir`) pomija poddrzewo `docs/` katalogu przebiegu, bo to migawka, a nie raport.

Gdzie w kodzie: `skillsync_workflow/specs.py`, `spec_snapshot.py`, `status.py` (`acceptance_pending`, `record_approval`), `quality.py` (`route`, `doctor`), `library/hooks/check-closure.py`. Testy: `tests/test_skillsync_specs.py`, `test_skillsync_spec_snapshot.py`, `test_skillsync_specs_integration.py`, `test_skillsync_repo_hygiene.py`.

Ryzyka rezydualne (zaakceptowane, do świadomości):
- Brak blokady współbieżnej dla `spec snapshot`; skutek to fałszywy `BLOCKED`, rozwiązywany przez `--replace`.
- Nieczytelny `docs/.saved.json` bez `--replace` nie jest ruszany; cele z innym sha dają `BLOCKED`.
- Stary klient dashboardu nie wysyła `planState`. Serwer przyjmuje wtedy zgodę bez kontroli, więc klik ze starej strony przy `tracked: false` może zamknąć odbiór, którego nie widać.
- `redact` nie maskuje tokenów `ghp_` w nazwie podanej przez `--spec`.
- Plan otwarty i zamknięty ponownie bez nowej zgody zamyka odbiór bez nowego `CP`.
- Dowody `test-run` sprzed wpisu `.gitignore` mają inny identyfikator drzewa i trzeba je powtórzyć.
