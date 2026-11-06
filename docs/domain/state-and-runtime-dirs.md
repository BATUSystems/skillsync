---
title: Stan lokalny (.github/.skillsync-state.json, .skillsync/)
tags: [skillsync, state, runs, dispatch]
paths: ['skillsync', 'skillsync_workflow/storage.py', 'skillsync_workflow/runs.py']
source: odczyt skillsync:load_state/save_state/place, skillsync_workflow/storage.py, runs.py (_store_path, _slug, _write, _scan_dirs, retrospective), retro.py:_store, retro.py:_decide_proposal, retro_scan.py:retro_scan, selection.py, quality.py:log_directory 2026-11-06
verified: 2026-11-06
---
Reguła:
- `.github/.skillsync-state.json` — stan generatora w trybie copy: klucze `github-skill:<n>`, `claude-skill:<n>`, `claude-agent:<n>.md`, `codex-skill:<n>`, `codex-agent:<plik>.toml`,
  `codex-instructions:skillsync-instructions.md`; wartość = hash biblioteki z chwili kopiowania (baza porównania trójstronnego). Lokalny, dopisywany do `.github/.gitignore` przy pierwszym zapisie.
- `.skillsync/` — lokalny stan workflow (logi `logs/<uuid>/`, `cache/smart-test/`, `runs/<run-id>.json`, `environments/`, `e2e/`, `claims/`, `upgrade/`, `providers/`, `dispatch/<run-id>.yaml`).
  Nigdy nie jest commitowany: `link` dopisuje `.skillsync/` do `.gitignore` projektów z `feature`.
- `runs/` (przebiegi): `<id>.json` i `<id>.lock`; `id` musi pasować do `_slug` (`[A-Za-z0-9][A-Za-z0-9_.-]{0,79}`). Ścieżka przez `runs.py:_store_path` (zakaz dowiązań w `.skillsync`, `runs` i samym pliku). W dispatch `<run>.<paczka>`; ponowienie `<run>.<paczka>.2` to ta sama tożsamość logiczna.
- `runs/<id>/` to katalog raportów przebiegu: tworzy go procedura, nie `checkpoint`. `retro` czyta go tylko jako sygnały (liczniki statusów), nie jako rekordy. `metrics --classify` używa go wyłącznie do wykrycia `no_data`. Katalog nie zastępuje pliku `<id>.json`; katalog bez przypisanego pliku `<id>.json` (lub `<id>.*.json`) to przebieg `no_data`.
- `retro` i `retro-scan` nic nie zapisują (ani plików, ani katalogów). Przy szukaniu katalogów pomijane są `*.lock` i `*.json` (`runs.py:_scan_dirs`).
- Pliki retro przebiegu leżą w `runs/<id>/` i są poza gitem: `retro-signals.json` (sygnały i propozycje, zapisuje orkiestrator), `retro-decisions.json` (historia stanów propozycji, schemat 1), `retro-P<n>.diff` albo `retro-P<n>-<k>.diff` (diff propozycji). Plik wejścia skanu (np. `retro-scan-input.json`) też leży w katalogu przebiegu; jego nazwę wybiera orkiestrator. Nie jest zamrożony i nie jest dowodem checkpointu.
- `retro-decide --proposal` dopisuje wpis do `retro-decisions.json` w katalogu przebiegu, a potem zapisuje sekcję `proposals:` w rejestrze `docs/retro/decisions.yaml` (śledzony w gicie). Każdy zapis jest atomowy; kolejność i stany: [retro-proposals.md](retro-proposals.md).
- Pozostały stan przechodzi przez `storage.py:state_path`: zakaz symlinków i ścieżek poza projektem; zapis atomowy (plik tymczasowy + `os.replace`), JSON ≤ 2 MB. Przebiegi mają własną kontrolę ścieżki (`runs.py:_store_path`) i ten sam limit 2 MB (`runs.py:_write`, `_read`).

Dlaczego: stan i dowody są per maszyna i per stan kodu; dowód związany jest z fingerprintem (HEAD + diffy + pliki nieśledzone, z wyłączeniem `.skillsync/`).

Wyjątki i przykłady: cache `smart-test` zapisuje plan, nigdy wynik wykonania — wyniki wymagają świeżego uruchomienia. `checkpoint` blokuje zapis katalogiem `<run-id>.lock`; po awarii procesu zostaje i trzeba go usunąć ręcznie.
Stan dyspozycji `.skillsync/dispatch/<run-id>.yaml` zapisuje wyłącznie skill `dispatch` — generator go nie tworzy ani nie waliduje.

Gdzie w kodzie: `skillsync:save_state`, `skillsync_workflow/storage.py:state_path`, `skillsync_workflow/runs.py:checkpoint`, `skillsync_workflow/runs.py:_store_path`, `skillsync_workflow/runs.py:_scan_dirs`, `skillsync_workflow/retro.py:retro`, `skillsync_workflow/selection.py:_cache_path`.
