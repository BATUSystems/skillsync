---
title: 'Rundy review i dziennik biegów podumysłów: polecenia review-round i agent-run, numeracja z plików, pole reviewRounds'
tags: [skillsync, review, review-round, agent-run, agents-jsonl, dashboard, status, doctor]
paths: ['skillsync_workflow/agent_runs.py', 'skillsync_workflow/review_rounds.py', 'skillsync_workflow/status.py', 'skillsync_workflow/quality.py', 'library/skills/feature/**', 'tests/test_skillsync_agent_runs.py', 'tests/test_skillsync_review_round*.py', 'tests/test_skillsync_review_rounds_status.py']
source: 'spec i plan CORE-1150-N16R (23.11.2026; rozpoznanie przebiegów 22–23.11: od 22.11 żadnego raportu review w przebiegach), kod i testy jak w polu paths'
verified: 2026-11-23
---
Reguła: rundę review zapisuje polecenie, nie pamięć sesji. `skillsync review-round start --kind spec|plan|code` zakłada katalog przebiegu, nadaje numer rundy i otwiera bieg w dzienniku; `freeze` zapisuje raport `<profil>-review-N.md` i zamyka bieg. Strona statusu liczy `Review ×N` z tych plików, więc raport niezapisany nie istnieje dla obserwatora.

Fakty i decyzje:
- Numer N wynika wyłącznie z plików raportów (`spec-review-N.md`, `plan-review-N.md`, `code-review-N.md`, `code-review-<krok>-N.md`; ten sam regex co strona, z wariantem `rN`, bez `-decyzje` i `-verifier`). Dziennik nie wpływa na N, bo wtedy rundy przerwane i zamrożone rozjeżdżałyby numerację z licznikiem na stronie.
- Dziennik `.skillsync/runs/<run-id>/agents.jsonl` pisze wyłącznie orkiestrator (`agent-run`, `review-round`); jedna linia na zdarzenie, tylko pola z list zamkniętych, bez tekstu sesji i raportów. Zapis pod blokującą blokadą `.agents.lock` (ponawianie do 10 s; blokada katalogiem `.lock` z `checkpoint` kończy się od razu błędem i nie nadaje się do równoległych podumysłów). Id `a-<k>` liczone z całego pliku. Wcześniejszy otwarty bieg tej samej pracy kończy się jako `ABORTED`: dla rund review ten sam profil i krok w dowolnej rundzie, dla innych biegów ta sama rola, krok i runda.
- Przebieg dla speca bez planu (akt I jest głównym scenariuszem): `Przebieg:` speca, katalog z linii `Plan:`, nazwa pliku speca; pierwszy istniejący, inaczej nowy pod pierwszą nazwą. Dla `plan` i `code`: `Przebieg:` bieżącego planu (`dashboard.choose_plan`).
- Ostrzeżenie `plans[].reviewRounds` i wpis `info` w `doctor`: plan `approved`/`in_progress` z `Spec:` i `Spec-sha:`, którego przebieg nie ma raportu `spec-review`, zakończonej rundy `spec` w dzienniku ani rekordu checkpointu `spec-review`, i tylko gdy katalog przebiegu istnieje albo plan jest bieżący. Plany `done` i bez katalogu to historia (katalogi przebiegów znikają ze sprzątaniem worktree), więc brak katalogu nie jest sygnałem.
- Rozpoznanie 23.11 (heurystyka po nazwach i nagłówkach, rzędy wielkości): do 21.11 było ok. 75 raportów `spec-review` w ok. 25 przebiegach, od 22.11 ani jednego, choć 7 z 8 speców z 22–23.11 ma w nagłówku ślad review. Przyczyny: katalog przebiegu przy specu zwykle nie istnieje, instrukcja była tylko na końcu `review-loop.md`, reviewer ma tylko odczyt, numer N ręczny, hook `check-subagent-report` pomija reviewera.

Wyjątki i uwagi: hook SubagentStop dla reviewera nie jest zabezpieczeniem (nie zna run-id ani profilu, strzela przed `freeze`, tylko Claude); twarda bramka „GO plan” przy braku raportu odradzana (zgody mają być danymi, lekkie ceremonie nie robią pętli). Strona i kontrakt `agents`: `docs/dashboard-contract.md`.

Gdzie w kodzie: `skillsync_workflow/agent_runs.py` (`start_locked`, `open_runs`, `locked`), `skillsync_workflow/review_rounds.py` (`resolve_run_id`, `next_number`, `freeze`, `spec_review_recorded`), `skillsync_workflow/status.py` (`_review_gap`, `_settle_review_gaps`).
