---
title: 'Wspólny /tmp psuje cudze biegi testów (prywatny TMPDIR dla każdego biegu), a test zależny od node po cichu się pomija'
tags: [skillsync, tests, testing, agents, tmp, tmpdir, concurrency, flaky, mutation, node, nvm, prototype]
paths: ['tests/test_skillsync_knowledge_init.py', 'tests/test_skillsync_prototype.py', 'tests/test_skillsync_prototype_engine.py', 'tests/test_skillsync_prototype_server.py', 'tests/test_skillsync_prototype_accept.py', 'tests/test_skillsync_prototype_restore.py', 'tests/fixtures/prototype/engine-v2/SOURCE.md', '.skillsync/runs/**/mut/**']
source: "'.skillsync/runs/2026-11-03-skillsync-prototypes.P3b-prototype-integracja: logs/engine-tests-node16.log (5 skip), logs/final-unittest-1.log (FAIL 2: /tmp/x), evidence/tmp-x-copy-of-prototype.py; P2: logs/final-unittest-1.log (pusty /tmp/x); decyzja koordynatora przebiegu D9 i odbiór P3b; .skillsync/runs/2026-11-04-prototype-komentarze.<karta>/logs/9.1-full-final/full.log (2 FAIL, /tmp/x) i full-r2.log (izolowany TMPDIR, 1815 OK); journalctl --user 2026-11-05 12:59:31; tests/test_skillsync_knowledge_init.py:86-91 (poprawiony wzorzec); tests/test_skillsync_prototype.py:382 (wzorzec nadal obecny); .skillsync/runs/2026-11-05-retro-jakosciowe/3.4-unittest.log (obcy pusty /tmp/x); 2026-11-05 testy narzędzi warstwy roboczej: .skillsync/runs/2026-11-04-podzial-skillsync.<karta>/2.2bt-mutacje-r2.log (strażnik /tmp w mutacjach), stan dyspozytora .skillsync/dispatch/2026-11-04-podzial-skillsync.yaml (zdarzenia 15:25 i 15:30); plan zadania (poza rdzeniem), Wynik, krok 2.2a (pełna chronologia /tmp/x)"
verified: 2026-11-05
---
**Objaw:** (1) pełny zestaw nagle pada w `test_skillsync_knowledge_init` (`test_escaping_paths_fail_without_creating_files`,
asercja, że `<rodzic tempdira>/x` nie istnieje), bez żadnej zmiany w `knowledge`; w `/tmp` leży obcy plik `x`. Zdarzyło się
trzy razy (P2/P3b 2026-11-03, przebieg 2026-11-05 12:59, retrospektywa jakościowa 2026-11-05). (2) Strażnik `/tmp`
w biegach mutacji zgłasza nowe katalogi `/tmp/tmp*` (z `room/`, `scratch/`, `s.jsonl`, `o.json`) i puste `/tmp/tmp.*`, które
nie pochodzą z mutowanego kodu. (3) Test krzyżowy z silnikiem JS (`test_skillsync_prototype_engine`) w zwykłym
`unittest discover` kończy się „OK (skipped=…)”, choć nic nie porównał. (4) Test prototypu sprawdza
`self.root.parent / 'x'` (`tests/test_skillsync_prototype.py:382`), więc ten sam błąd może wrócić z innego modułu.

**Przyczyna:** (1) testy, które sprawdzają „nic nie powstało poza katalogiem testu” przez `tempdir.parent`, zakładają
wyłączność katalogu nadrzędnego, a przy domyślnym `TMPDIR` jest nim wspólne `/tmp`. Dowolny inny bieg (mutacje podumysłów,
stan przejściowy w trakcie edycji, inna sesja) może zostawić plik o tej samej nazwie; w P3b była to kopia
`skillsync_workflow/prototype.py`, w P2 pusty plik. Pliki `/tmp/x` (0 B) zostawiły m.in. skrypt jednorazowy z `MUT_DIR=/tmp`
(test-writer kroku 2.1t) i bieg testów prototypu, którego źródła nie ustalono ponad wątpliwość. (2) Do `/tmp` piszą też testy
narzędzi uruchamiane bez prywatnego `TMPDIR`: podumysły uruchamiały testy narzędzi warstwy roboczej bez `TMPDIR`, a test nie
sprzątał swoich katalogów (poprawka: `addCleanup` i strażnik katalogu tymczasowego). `TMPDIR=$(mktemp -d)` bez `-p` sam tworzy
katalog w `/tmp`, więc „prywatny” `TMPDIR` też ląduje we wspólnym `/tmp`. (3) Domyślny `node` w PATH na tej maszynie to v16
(nvm), a test wymaga node ≥ 18 i się pomija. `/tmp` jest tmpfs, więc dowody tam zapisane znikają po restarcie.

**Reguła:**
- Testy i podumysły piszą **tylko** do katalogu tymczasowego przebiegu, nigdy bezpośrednio do wspólnego `/tmp`.
- Test sprawdza wyłącznie katalogi, które **sam utworzył**: tempdir w tempdir. Katalog nadrzędny testu to jego własny
  katalog w `TMPDIR`, nie `tempfile.gettempdir()` ani `tempdir.parent`. Wzorzec poprawny jest w
  `tests/test_skillsync_knowledge_init.py:86-91` (projekt w `self.base / "nest" / "project"`, ucieczka sprawdzana w `self.base / "x"`).
- Każdy bieg testów zlecany podumysłowi albo uruchamiany równolegle z innymi sesjami ma prywatny `TMPDIR` **poza
  jakimkolwiek repozytorium git**: w scratchpadzie (`TMPDIR=<scratchpad>/tmp-<krok>` albo `mktemp -d -p <scratchpad>`),
  nigdy `mktemp -d` bez `-p`. Katalog przebiegu `.skillsync/runs/<id>/` leży w drzewie repo, więc jako `TMPDIR` łamie
  testy scenariuszy „poza gitem” (`test_skillsync_agents_md`, `test_skillsync_catalog_map`, `test_skillsync_prototype_server`:
  3 FAIL 2026-11-05, przy `TMPDIR` w scratchpadzie OK; `.skillsync/runs/2026-11-05-lekcje-frontmatter/outside-git-3tests.log`).
  Dotyczy także biegów mutacji i testów narzędzi (np. audytów), nie tylko zestawu końcowego.
- Mutacje, kopie i pliki robocze podumysłów zapisuj wyłącznie w katalogu przebiegu (`.skillsync/runs/<id>/mut/…`),
  nigdy bezpośrednio w `/tmp`; zlecenie podaje ten katalog jawnie.
- Test, który tworzy katalogi tymczasowe, sprząta je (`addCleanup`). Moduły piszące poza `tempdir` mają strażnika (dziś
  testy serwera, akceptacji i przywracania prototypów): prywatny katalog nadrzędny w `setUpModule` i kontrolę listingu po
  module (nowy wpis spoza katalogów testu = FAIL z listą).
- Zestaw końcowy na współdzielonej maszynie: z izolowanym `TMPDIR`, zapisanym w logu obok pozostałych zmiennych.
- Porażka testu „nic poza katalogiem” albo ostrzeżenie strażnika przy wspólnym `/tmp`: najpierw powtórka z izolowanym
  `TMPDIR`; dopiero wynik izolowany rozstrzyga, czy kod pisze poza katalog.
- Pełny zestaw z testami node uruchamiaj z `SKILLSYNC_TEST_NODE=<node ≥ 18>` (np. `nvm which 22`) i licz skip jako brak
  dowodu, nie zaliczenie. Kopię silnika w `tests/fixtures/prototype/engine-v2/` odświeżaj przy zmianie `prototype.js`
  w bibliotece (test z `SKILLSYNC_PROTOTYPE_ENGINE_V2_SOURCE` wykrywa rozjazd).

**Jak wykryć:**
- `stat -c %y /tmp/<nazwa>` (właściciel i czas utworzenia vs czas pracy podumysłów) i
  `journalctl --user --since … --until …` (biegi `systemd-run` w oknie czasu).
- Po biegu z izolowanym `TMPDIR` lista wpisów spoza `tmp*` w tym katalogu ma być pusta.
- `grep -rn "parent / ['\"]" tests/` — wzorce odwołujące się do katalogu nadrzędnego tempdira (przegląd każdego trafienia).
- W logu testów `skipped=` przy `test_skillsync_prototype_engine`.
