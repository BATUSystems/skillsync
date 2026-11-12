---
title: "Przyklejony pasek a komponenty ramki: z-index bez kontekstu nakładania wycieka"
tags: [prototype, css, z-index, sticky, stacking-context, comments, layout-probe]
paths: ['tests/fixtures/prototype/engine/**', 'tests/test_skillsync_prototype_engine.py']
source: '<projekt>/.skillsync/runs/prototyp-plan-zajec/screens/07-ZNALEZISKO-modal-nad-paskiem-900.png, 08-light-900-pinezki-pole-i-konflikt.png (raport przeglądu plan-zajec); plan zadania (poza rdzeniem) (E1, E2, sekcja Projekt rozwiązania); .skillsync/runs/2026-11-04-prototype-poprawki.Q1-prototype-poprawki/lib-1-2-3-5-implementer.md (krok 1, przegląd z-index); pomiary akceptacyjne kroku 8 (headless Chrome, CDP, elementFromPoint, 2026-11-04)'
verified: 2026-11-04
---
**Objaw:** na prototypie z 28 ekranami przy szerokości 900 px dialog `.modal` przewinięty pod `.doc-toolbar` (sticky) przykrywa pasek. `elementFromPoint` w środku przycisku „Wątki” zwraca `input` dialogu, więc klik nie otwiera panelu. Pinezki komentarzy zakotwiczone w treści dialogu leżą pod dialogiem; klik w pinezkę otwiera nowy kompozytor na elemencie pod spodem zamiast wątku.

**Przyczyna:** `.doc-toolbar` (sticky, `--z-index-sticky`) i elementy ekranu (`.modal` 40, scrim 30, toast 50, pinezki) rywalizują w jednym kontekście nakładania całego dokumentu. `z-index` z treści ekranu wygrywa z paskiem, bo nic nie zawęża jego zasięgu do ramki. Podniesienie `z-index` paska nie naprawia tego: każdy kolejny komponent z wyższą wartością wraca do tego samego błędu.

**Reguła:**
- Ramka prototypu (`.frame`) musi tworzyć kontekst nakładania: `isolation: isolate` (plus `position: relative`, który już był). `z-index` treści ekranu działa wyłącznie wewnątrz ramki, a cała ramka leży pod paskiem.
- Nie podnoś `z-index` paska i nie wyciągaj elementów ekranu ponad ramkę. Autor CSS ekranu używa tokenów `--z-index-*` (`library/skills/prototype/references/style.md`, sekcja „Warstwy”).
- Pinezki (`.anno-pin-layer`) to dziecko ramki z `z-index: var(--z-index-top)`: leżą nad treścią ramki, także nad dialogiem. Panel komentarzy (`position: fixed`, poza ramką) nadal przykrywa ramki.
- Nie wnioskuj o układzie z kodu CSS. Zrób pomiar w przeglądarce (pkt niżej).

**Jak wykryć:**
- Sonda `library/skills/prototype/scripts/layout-probe.js` w bibliotece: `await layoutProbe({target: 'sN'})` w konsoli albo przez `evaluate_script` (chrome-devtools). Pole `a1` mierzy, czy `elementFromPoint` w każdym przycisku paska trafia w ten przycisk, po przewinięciu dialogu pod pasek; `a2` mierzy, czy pinezka zakotwiczona w dialogu trafia w pinezkę. Na silniku bez `isolation` wynik jest czerwony.
- `ok: null` ze `skipped` oznacza brak dialogu albo pinezki do sprawdzenia, a nie zaliczenie.
- Sonda nie zastępuje kliku: klik wskaźnikiem (CDP Input) w „Wątki” i w pinezkę jest osobnym krokiem.
- Ręcznie: `document.elementFromPoint(x, y)` w środku przycisku paska, gdy dialog jest przewinięty pod pasek.
- Test `tests/test_skillsync_prototype_engine.py` pilnuje tylko zgodności kopii `prototype.js`, nie układu.
