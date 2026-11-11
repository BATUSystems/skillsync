# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), wersje: [SemVer](https://semver.org/lang/pl/).

## [Unreleased]

### Changed
- Skill `skillsync`: tabela komend opisuje pole `preImplement` w `status`, `warnings[]` w `plan approve` i flagę `--run-id` w `readiness` (zapis `readiness.md`).

### Fixed
- `skills/prototype` (`assets/repo/components.css`, `references/screen-patterns.md`): lista rozwijana otwarta w oknie lub szufladzie nie jest już obcinana przez `overflow` ciała okna ani chowana pod stopką — klasa `popover-float` (rodzeństwo okna) i wzorzec z regułą „okno mieści się bez paska przewijania”.

## [1.0.2] - 2026-11-11

### Removed
- `skills/go-jira` (przestarzały); wpis `go-jira` w manifeście rozwija się do `acli`. Usunięty z `workflow/skill-catalog.yaml`.

### Changed
- `skills/acli`: sekcja „Pola własne” (tworzenie: `create --from-json` z `additionalAttributes`; edycja istniejącego zgłoszenia: REST API na jawne polecenie, token nigdy w argumencie polecenia ani w logu), bez odesłań do go-jira; `skills/skillsync` bez `go-jira` w przykładzie.
- `skills/dispatch` (Krok 0, `references/claude.md`): bramka modelu dyspozytora to Opus 5.5 lub Fable 5.1 (najmocniejszy model rodziny 5); podział modeli kart ustala użytkownik.
- `skills/feature/references/ticket-intake.md`: nowy punkt 9 „Pytania w trakcie przebiegu” — przed pytaniem produktowym odczytaj ponownie zgłoszenie i komentarze, niepotwierdzone przesłanki oznacz jako założenie (retro, P1).
- `skills/feature/references/plan-large.md`: liczba rund review wynika z kategorii pętli w `review-loop.md` (pętla pełna tylko dla strażnika wycieków, migracji i bezpieczeństwa) (retro, P2).
- `skills/agent-runtime-pitfalls`: nie uruchamiaj skryptu pomocniczego po samej nazwie; wynik „0 testów” to brak dowodu (retro, P3).

## [1.0.1] - 2026-11-09

### Changed
- `skills/prototype` (silnik prototypów: `assets/repo/prototype.js` i `prototype.css`): fragment adresu ramki widoku to `#view-sN` zamiast `#sN`;
  ładowanie ramki nie przewija strony nadrzędnej (V-23, N-R1), a dokładne dopasowanie fragmentu ma pierwszeństwo.
- `skills/agent-runtime-pitfalls`: mutant po zawieszonym `cp -i` zostaje na dysku (sprawdź `diff -q`/`git diff` przed dalszą pracą);
  na Windows `write_text` zamienia LF na CRLF i psuje skróty liczone w testach, a ścieżki w JSON idą przez `as_posix()`.

## [1.0.0] - 2026-11-08

### Changed
- Tryb dostarczania (`delivery.mode`): `instructions/_base.md`, `feature`, `pr-run`, `worker-mode`, `multi-session`, `dispatch`
  i `workflow/references/release.md` opisują `pr` (domyślny) i `direct`, słowa zgody `CP`, `CPD`, `READY` (z wariantami `!`),
  commity po każdym sprawdzonym kroku planu i zakaz merge/complete PR. `skills/skillsync/SKILL.md` opisuje klucz `delivery` i pole `route`.

## [0.9.0]

### Added
- Pierwsze wydanie warstwy core po podziale biblioteki na warstwy (core, org, personal).
- Lekcje rdzenia w `workflow/lessons/`: uogólniona wiedza o testowaniu (zawężone biegi, Playwright i
  `TestServer`, Angular, kontrakty API, coverage wycinka, django-tenants, izolacja zasobów).
