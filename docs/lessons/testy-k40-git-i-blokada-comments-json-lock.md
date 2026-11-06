---
title: "Commit przed iteracją (K40) i testy git: add z :(exclude) na ignorowanym pliku daje błąd, a pusty comments.json.lock łamie asercje „nic nie zapisano”"
tags: [skillsync, git, prototype, testing, commit, lock, tests]
paths: ['skillsync_workflow/prototype.py', 'tests/test_skillsync_prototype_restore.py', 'tests/test_skillsync_prototype_server.py', 'tests/test_skillsync_prototype.py']
source: .skillsync/runs/2026-11-04-prototype-komentarze.karta-2/2.1-2.3-implementer.md (Odkrycie 1, smoke1); 2.4-test-writer.md (Odkrycia 6-9); skillsync_workflow/prototype.py:1888-1891 (komentarz i `git add -A -- <pathspec>`); tests/test_skillsync_prototype_restore.py:114-118 (snapshot bez locka), :497 (CommitBeforeIterationTests), :639-640 (useConfigOnly, GIT_CONFIG_GLOBAL), :605-612 (commit -- <plik>).
verified: 2026-11-04
---
**Objaw (A, commit K40):** `git add -A -- <katalog> ':(exclude)<katalog>/comments.json.lock'` kończy się błędem „paths are ignored”, bo wykluczany plik jest w `.gitignore`. Krok `iterate` nie może zamrozić wersji.

**Objaw (B, brak zmian):** test oczekuje commitu przed iteracją, a go nie ma, bo jedyną zmianą w katalogu był ignorowany plik (lock).

**Objaw (C, test konfliktu):** `git commit -a` w teście konfliktu commituje też niezwiązaną zmianę strony; asercje o stanie gałęzi przestają pasować.

**Objaw (D, tożsamość):** test „brak tożsamości” przechodzi, bo git zgaduje autora z hostname.

**Objaw (E, lock):** po odmowie (BLOCKED, FAIL, 409) asercja „nic nie zapisano” pada, bo w katalogu prototypu pojawił się `comments.json.lock` o zerowym rozmiarze.

**Przyczyna:**
- A: jawnie wymieniona ścieżka liczy się jak dodawana, więc git odrzuca ją, gdy jest ignorowana.
- B: `add` pomija ignorowane pliki; bez stagowanych zmian nie ma commita.
- C: `-a` obejmuje cały worktree, nie tylko pliki z testu.
- D: bez `user.useConfigOnly` git dobiera tożsamość automatycznie.
- E: każda operacja pod blokadą tworzy `comments.json.lock` i go nie usuwa; plik jest ignorowany przez `.gitignore` prototypu.

**Reguła:**
- Commit przed iteracją: `git add -A -- <pathspec>` bez `:(exclude)`. Ochronę blokady daje `.gitignore` prototypu (`comments.json.lock`, `*.tmp`). Po `add` sprawdzić `git diff --cached --quiet --`; brak zmian = brak commita, bez błędu.
- W testach commitować tylko wskazane pliki: `git commit -- <plik>`, nie `-a`.
- Testy tożsamości: repo tymczasowe z `git config user.useConfigOnly true`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, pusty `EMAIL` (wzór: `CommitBeforeIterationTests`).
- Asercje „brak zapisu” porównują drzewo bez `comments.json.lock` (wzór: `RestoreFixture.snapshot`). Obecność pustego locka nie jest zapisem.
- Fikstura „init” zostawia niezacommitowaną zmianę, bo brak zmian w katalogu daje brak commita (raport 2.4, odkrycie 6).

**Jak wykryć:**
- `git check-ignore -v <katalog>/comments.json.lock` w projekcie testowym; `rg -n ":\(exclude\)" skillsync_workflow/prototype.py` nie może wskazywać `add`.
- `python3 -m unittest tests.test_skillsync_prototype_restore.CommitBeforeIterationTests -v` (12 testów, repo tymczasowe).
- `rg -n "'commit', '-a'|commit -a" tests/` nie daje trafień w testach K40.
- Po odmowie w teście: `ls -la <root>/<slug>/` pokazuje pusty `comments.json.lock`; snapshot bez pomijania go musi paść.
