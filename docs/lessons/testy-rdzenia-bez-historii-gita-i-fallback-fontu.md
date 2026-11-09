---
title: "Testy rdzenia nie zależą od historii gita sprzed migawki; pasek prototypu nie może zależeć od szerokości fontu systemowego"
tags: [testing, e2e, prototype, ci, fonts, css, git]
paths: ['tests/e2e/prototype/widok.mjs', 'tests/e2e/prototype/nr.mjs', 'tests/fixtures/prototype/engine-v2-prev/*', 'library/skills/prototype/assets/repo/prototype.css']
source: 'CI publicznego rdzenia, run 42356489156 (job e2e: V-02 i N-R9 czerwone), naprawa w gałęzi fix/ci-e2e (D265, D266, D271); pomiar lokalny z fontconfig wymuszającym DejaVu Sans odtworzył 151 px paska'
verified: 2026-11-07
---
**Objaw:** job `e2e (ubuntu-latest)` w publicznym rdzeniu: V-02 „brak silnika sprzed D28b” oraz N-R9 „pasek 151 px > 2 × 44 + 32”.
Lokalnie oba scenariusze przechodziły.

**Przyczyny:**
- V-02 czytał stary silnik przez `git show <sha>:…` w bibliotece. Rdzeń publiczny ma osierocone, spłaszczone
  historie (migawka), więc commit nie istnieje. Test zależał od historii, której nie ma w repo.
- N-R9: stos `--font-sans` (`ui-sans-serif, system-ui, …`) rozwiązuje się na runnerze do szerszego fontu (DejaVu Sans) niż lokalnie (Noto Sans).
  Podpowiedź `.anno-save` („Zaloguj się, żeby komentować”, `white-space: nowrap`) wypychała w pasku niezalogowanego
  przycisk „⋯” do trzeciego rzędu. Lokalnie brakowało ok. 4 px. Defekt miał już silnik sprzed D28b.

**Reguła:**
- Test rdzenia nie wywołuje `git show`/`git log` na commitach sprzed migawki. Dane historyczne to kopia w
  `tests/fixtures/` z `SOURCE.md` (sha, sha256). Dla starego silnika komplet: `prototype.js` **i** pliki CSS, nie sam JS.
- Układ mierzony w E2E sprawdzaj także na szerokim foncie. Lokalnie: `FONTCONFIG_FILE` z regułami zamieniającymi
  `sans-serif`, `system-ui` i `Noto Sans` na `DejaVu Sans`, uruchomienie Chrome z tą zmienną.
- Elementy tekstowe w wierszach paska mają szerokość naturalną z górnym limitem (`max-width: 11rem; min-width: 0; text-overflow: ellipsis`),
  a nie sztywno `nowrap`. Nie dawaj im `flex-grow` ani dużej `flex-basis`: poszerzony „Zapisano” wypchnął „⋯” zalogowanego do trzeciego rzędu (V-02, 599 px). Fix w CI przez instalację fontu maskowałby defekt widoczny u użytkowników.
- Dowód scenariusza w `evidence` zaczynaj od wierszy z `ok:false`: log CI ucina dowód po ok. 800 znakach.
- Asercja „równa wzorcowi ±N” nie wytrzymuje poprawy poniżej wzorca (D271): wyrażaj ją jednostronnie („nie większe niż przed”).

**Jak wykryć:** `grep -nE "git.*show|execFileSync\('git'" tests/e2e/prototype/*.mjs` (zależność od historii);
bieg N-R9 i V-02 z `FONTCONFIG_FILE` wymuszającym DejaVu Sans.

**Niestabilność N-R1 mobile (D277), rozwiązana w NR1-V23:** przyczyną nie był font, tylko fragment adresu iframe widoku (`#sN`), który
przewijał stronę nadrzędną przy ładowaniu ramki; zob. [fragment-adresu-iframe-przewija-strone-nadrzedna](fragment-adresu-iframe-przewija-strone-nadrzedna.md).
DejaVu Sans tylko zmieniał kolejność ładowania leniwych ramek (w D277: DejaVu 2/6 FAIL, Noto Sans 0/6); pod obciążeniem CPU padały oba fonty.
Reguła baseline zostaje: przed uznaniem porażki za skutek zmiany zrób baseline na czystym `main` (osobny worktree, ten sam font i to samo
obciążenie, 6 biegów). Sam zielony pojedynczy bieg CI niczego nie dowodzi, sam czerwony też.
