---
title: "Sterownik CDP w E2E prototypu: Page.navigate z samym hashem wiesza go, clickEl przewija stronę, a cel okna trzeba sprawdzać po treści"
tags: [testing, e2e, cdp, chrome, prototype, driver]
paths: ['.skillsync/runs/**/tools/e2e-*.mjs', 'library/skills/prototype/assets/repo/prototype.js']
source: '.skillsync/runs/2026-11-04-prototype-komentarze.karta-2/3.4-e2e-s2-test-runner.md:61 (timeout 124 przy acc.go()), :90-91 (pułapki: hash, clickEl); 3.4-e2e-test-runner.md:71-72 (clickEl przewija, is-current, element zasłonięty przy hit true); 3.4-e2e-s3-test-runner.md:38 (rawKeyDown dla Tab/Esc, keyDown z text dla Enter); tools/e2e-lib.mjs:53-54 (go() czeka na loadEventFired), :64-70 (clickEl: scrollIntoView, elementFromPoint), :78-79 (keyDown/keyUp z windowsVirtualKeyCode); tools/e2e-f3s3-lib.mjs:12-14 (rawKeyDown dla Tab/Esc z windowsVirtualKeyCode, modifiers 8 = Shift); kod library/skills/prototype/assets/repo/prototype.js:2526-2527, 2540 (currentScreen, przełączanie is-current)'
verified: 2026-11-05
---
**Objaw:**
- Skrypt E2E z `acc.go()` na tym samym dokumencie z innym hashem wisi i kończy się timeoutem (exit 124, bez wyników). `Page.loadEventFired` nie przychodzi (`3.4-e2e-s2-test-runner.md:61`).
- `clickEl` przewija stronę do celu, a `is-current` przechodzi na inny ekran. Okno akceptacji celuje wtedy w inny ekran niż ten, na którym stał kursor (`3.4-e2e-test-runner.md:71`).
- `clickEl` zgłasza „element zasłonięty”, mimo że diagnostyka trafia w przycisk (`hitIsBtn: true`, `3.4-e2e-test-runner.md:72`).

**Przyczyna:**
- `Page.navigate` na ten sam dokument, zmieniający tylko hash, nie emituje `loadEventFired`, a `go()` na tym czeka (`tools/e2e-lib.mjs:53-54`).
- `clickEl` wywołuje `scrollIntoView` (`e2e-lib.mjs:65`). Przewinięcie zmienia ekran „bieżący” (`is-current`, `prototype.js:2540`), więc cel liczony przy kliknięciu bywa inny niż zamierzony.

**Reguła:**
- Zmianę hasha na tej samej stronie rób przez `location.hash = '#s2'` i czekaj na skutek w DOM, nie przez `Page.navigate`.
- Cel okna weryfikuj po treści okna (`dialog[open]` i tytuł „Ten ekran (s2 …)”), nie po `is-current`.
- Klikaj DOM-em (`el.click()`), gdy scenariusz nie testuje kliku kursorem (`3.4-e2e-s2-test-runner.md:91`). Klik wskaźnikiem tylko tam, gdzie tego wymaga przypadek.
- Tab i Esc wysyłaj jako `rawKeyDown` z `windowsVirtualKeyCode`, `nativeVirtualKeyCode` i `modifiers` (Shift = 8). Enter wysyłaj jako `keyDown` z `text: '\r'` (`tools/e2e-f3s3-lib.mjs:12-14`, `e2e-lib.mjs:78-79`).

**Jak wykryć:**
- `grep -n "Page.navigate" .skillsync/runs/**/tools/*.mjs`: wywołania z samym `#` w URL zamień na `location.hash`.
- Przed kliknięciem w oknie sprawdź treść `dialog[open]` (oczekiwany ekran w tytule), a nie `is-current`.
- Komunikat „element zasłonięty” przy `hit: true` w diagnostyce to błąd sterownika, nie produktu.

**Granice:** pułapki z sesji s2–s4 F3 (Chrome 157, headless). Nie sprawdzano, czy brak `windowsVirtualKeyCode` daje inny wynik niż `rawKeyDown`; reguła opiera się na tym, co robią działające skrypty. Pułapka z celami w `title` ramek iframe nie została potwierdzona w raportach, więc nie jest tu opisana.
