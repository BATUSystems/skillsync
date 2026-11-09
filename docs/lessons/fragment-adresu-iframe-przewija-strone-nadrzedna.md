---
title: "Fragment adresu iframe z id istniejącym w jego dokumencie przewija także stronę nadrzędną (skok strony, niestabilny N-R1)"
tags: [prototype, e2e, iframe, scroll, flaky, testing, chrome]
paths: ['library/skills/prototype/assets/repo/prototype.js', 'tests/e2e/prototype/nr.mjs', 'tests/e2e/prototype/widok.mjs']
source: 'NR1-V23, wydanie 1.0.1; pomiar próbkami requestAnimationFrame na N-R1 (DejaVu, obciążenie CPU); D249, D277'
verified: 2026-11-09
---
**Objaw:** `N-R1` (mobile) padał co kilka biegów z `openShift` = `closeShift` ≈ −3000…−3300 px; V-23 (skok strony przy pierwszym dopasowaniu
wysokości iframe widoku) był znanym defektem, a poprawka w handlerze `height` (D28c) nie pomagała.

**Przyczyna (zmierzona):** iframe widoku ładował się z `?embed=view#sN`, a jego dokument ma element o id `sN`. Przy ładowaniu Chrome przewija
ten element do widoku, a w ramce tego samego pochodzenia przewinięcie obejmuje też okno nadrzędne. Żadne `scrollTo`, `scrollBy`,
`scrollIntoView` ani `focus` w kodzie strony się nie wywołuje, więc nie widać tego w stosach. W N-R1 leniwa ramka s7 ładuje się dopiero, gdy
panel zmniejsza układ i ramka wchodzi w okno; wtedy strona skacze o ok. 3000 px. To nie scroll anchoring (`overflow-anchor: none` niczego nie zmienia).

**Reguła:**
- Fragment adresu osadzanej ramki nie może być równy id elementu jej dokumentu: dodaj przedrostek (`#view-sN`), a instancja osadzona
  zdejmuje przedrostek i przyjmuje też dawny zapis.
- Ramki porównania iteracji (`?embed=compare#sN`) ładują zamrożone, starsze wydania, więc ich fragmentu nie zmieniaj bez migracji iteracji.
- Niestabilny scenariusz układu odtwarzaj deterministycznie, zanim zgadniesz przyczynę: obciążenie CPU (po jednym procesie
  na rdzeń) opóźnia ładowanie leniwych ramek i wywołuje wyścig w każdym biegu (przed poprawką 6/6 FAIL, po poprawce 20/20 PASS z DejaVu i zwykłym fontem).
- Wyścig diagnozuj próbkami `requestAnimationFrame` (scrollY, górna krawędź ekranu, wysokości ramek) i znacznikami zdarzeń
  `load` ramek oraz wiadomości `postMessage`; podejrzewaną przyczynę sprawdź na kopii silnika poza repo, zanim ruszysz kod.

**Jak wykryć:** `grep -n "embed=view#" library/skills/prototype/assets/repo/prototype.js` (fragment musi mieć przedrostek); scenariusz V-24.
