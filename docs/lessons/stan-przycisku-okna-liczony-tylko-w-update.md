---
title: "Stan przycisku okna liczy się tylko w update(): każde otwarcie okna musi przeliczyć go jawnie, bo skutek uboczny cudzego wywołania maskuje brak"
tags: [prototype, dialog, accept, state, aria, e2e]
paths: ['library/skills/prototype/assets/repo/prototype.js', 'tests/test_skillsync_prototype_engine.py', '.skillsync/runs/**/tools/e2e-*.mjs']
source: '.skillsync/runs/2026-11-04-prototype-komentarze.karta-2/3.4-e2e-s2-test-runner.md:33 (E3-08: dlgBtnOnline, probe-reopen open1 i reopenOnline: disabled "true", hint null); 3.4-e2e-s3-test-runner.md:32, :101 (offline.json: btnAria=false po ponownym otwarciu); code-review-3.5-eng-2-poprawki.md:8 (przyczyna, poprawka); kod library/skills/prototype/assets/repo/prototype.js:5074-5078 (loadProject, update), 5100 (wywołanie loadProject tylko dla zakresu projektu), 5213 (przycisk z aria-disabled=true), 5241-5244 (acceptRefresh, update, draw)'
verified: 2026-11-05
---
**Objaw:** w oknie „Akceptacja” przycisk „Akceptuję” startuje z `aria-disabled="true"` i pustym powodem, mimo że serwer i sieć są w porządku. Po cyklu offline → online i ponownym otwarciu okna przycisk nie wraca (`3.4-e2e-s2-test-runner.md:33`: `disabled: "true"`, `hint: null`). Ten sam stan był widoczny przy otwarciu bez wcześniejszego offline (`open1`), więc nie była to wina sieci.

**Przyczyna:** `aria-disabled` i powód liczy tylko `update()`. Wcześniej `openAcceptDialog` kończyło się `loadProject()`, które wywoływało `update()` jako skutek uboczny. Po przejściu na leniwe `loadProject` (lista tylko dla zakresu „projekt”, `prototype.js:5100`) ten skutek zniknął i nowe okno nie dostawało przeliczenia (`code-review-3.5-eng-2-poprawki.md:8`). `loadProject` nadal wywołuje `update()` (5078, 5085, 5089), ale dla okna w zakresie „ten ekran” nie jest wołany.

**Reguła:**
- Każde otwarcie okna, także leniwie inicjalizowanego, wywołuje jawnie przeliczenie stanu przed rysowaniem: w `openAcceptDialog` to `update(); draw(true);` (`prototype.js:5243-5244`).
- Nie traktuj skutku ubocznego innej funkcji (`loadProject`, `markChanged`) jako źródła stanu przycisku.
- Zmiana sieci odświeża otwarte okno przez `acceptRefresh` (5241) i zdarzenia `online`/`offline` → `markChanged` → `acceptRefresh` (`code-review-3.5-eng-2-poprawki.md:8`).
- Test E2E „otwarcie po zmianie stanu sieci”: okno online → offline → klik zablokowanego przycisku (toast) → Esc → online → ponowne otwarcie. Oczekiwane: „Akceptuję” bez `aria-disabled` albo z widocznym powodem, nigdy `true` bez powodu. Wynik PASS w s3: `btnAria=false` (`3.4-e2e-s3-test-runner.md:32`).

**Jak wykryć:**
- Po otwarciu okna w CDP: `[...document.querySelectorAll('dialog[open] button')].find(b => b.textContent.includes('Akceptuję'))`; `aria-disabled="true"` bez tekstu w `#anno-accept-hint` (lub odpowiedniku) to błąd.
- `grep -n "openAcceptDialog\|update(); draw" library/skills/prototype/assets/repo/prototype.js`: przed `draw(true)` musi stać `update()`.

**Granice:** potwierdzone na silniku z s3 (`199d8fb3…`). Na silniku z s4 (`dc10d211…`) stanu akceptacji nie sprawdzano osobno (`3.4-e2e-s4-test-runner.md:91`).
