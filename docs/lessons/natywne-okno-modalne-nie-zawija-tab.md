---
title: "Natywne dialog.showModal() w Chrome nie zawija Tab: okno silnika potrzebuje własnej pułapki fokusu i blokady Esc w keydown"
tags: [prototype, dialog, focus, keyboard, accessibility, e2e, engine]
paths: ['library/skills/prototype/assets/repo/prototype.js', 'tests/test_skillsync_prototype_engine.py', 'tests/fixtures/prototype/engine-v2/**', '.skillsync/runs/**/tools/e2e-*.mjs']
source: '.skillsync/runs/2026-11-04-prototype-komentarze.karta-2/3.4-e2e-s2-test-runner.md:39 (tabLog OUT BODY), :76 (hipoteza showModal); 3.4-e2e-s3-test-runner.md:48, :97 (Esc → BODY w oknie hasła); 3.4-e2e-s4-test-runner.md:21 (a-forced: Tab n+2 bez BODY, Esc x2 zostaje); code-review-3.5-eng-2-poprawki.md:47; code-review-3.5-eng-2b-poprawki.md:6-7, :19; kod library/skills/prototype/assets/repo/prototype.js:1116-1130 (dialogEscapeAllowed, dialogTabTarget), 4201 (role=dialog tylko dla fallbacku), 4215-4220 (cancel), 4221-4231 (keydown: Tab, Esc), 4241 (showModal)'
verified: 2026-11-05
---
**Objaw:** okno otwarte przez natywne `showModal()` w headless Chrome nie zawija Tab. Po „Akceptuję” Tab przenosi fokus na `BODY`, a potem poza okno (`OUT BODY`, `3.4-e2e-s2-test-runner.md:39`). Okno „Ustaw własne hasło” wymuszone przez `mustChange` zamykało się przy Esc, a fokus ląduł na `BODY` (`3.4-e2e-s3-test-runner.md:48`).

**Przyczyna:** natywny modal nie zawija fokusu, a Esc obsługuje zdarzenie `cancel`. Samo `cancel.preventDefault()` nie wystarcza: Chrome przy powtórnym Esc zamyka modal mimo preventDefault (`code-review-3.5-eng-2b-poprawki.md:19`). Blokadę trzeba też założyć w `keydown`. W kodzie `openDialog` (`prototype.js:4193`) jest to: `cancel` z `dialogEscapeAllowed` (4215-4220), pułapka Tab przez `dialogTabTarget` (4221-4229), `keydown` Esc z `preventDefault` (4231) i dopiero `showModal()` (4241).

**Reguła:**
- Okna silnika otwieraj tylko przez `openDialog`. To jedyne miejsce, gdzie powstaje modalne `dialog` (`showModal`, `prototype.js:4241`) albo fallback `role="dialog"` (4201). Nie dodawaj `showModal()` ani `<dialog>` poza nim.
- Tab zawijaj przez `dialogTabTarget(dialogFocusable(node), document.activeElement, shift)`: z ostatniego na pierwszy, z pierwszego (Shift+Tab) na ostatni, fokus poza listą na skrajny.
- Gdy okno nie wolno zamknąć Esc (`dialogEscapeAllowed` = false), blokuj Esc w `cancel` i w `keydown`.
- Test: czysta funkcja `dialogTabTarget` (przypadki: środek, ostatni, pierwszy z Shift, element spoza listy) oraz E2E pełnego obiegu Tab (n+2 kroków bez `BODY` i bez wyjścia z okna) i Esc ×2 na oknie wymuszonym (okno i fokus zostają).

**Jak wykryć:**
- `grep -n "showModal\|role: useNative" library/skills/prototype/assets/repo/prototype.js`: każde trafienie w `openDialog`.
- E2E (CDP, `Input.dispatchKeyEvent` z `rawKeyDown`): w logu tabulatora brak wpisu `BODY` ani `OUT`.
- Po Esc: `document.activeElement` nie może być `BODY`, a `dialog[open]` musi istnieć przy oknie wymuszonym.

**Granice:** zachowanie potwierdzone w Chrome 157 (headless, sesje s2–s4). Inne przeglądarki nie były sprawdzane. Okno „Iteracja N (bieżąca)” nie ma wyniku (NOT_RUN w s4, `3.4-e2e-s4-test-runner.md:48`), więc nie uznawaj go za pokryte tą regułą.
