---
title: "Klasa .btn z display:inline-flex przesłania atrybut hidden: ukryty przycisk zostaje widoczny"
tags: [prototype, css, browser, hidden, engine, buttons]
paths: ['tests/fixtures/prototype/engine-v2/**', 'tests/test_skillsync_prototype_engine.py', 'skillsync_workflow/prototype.py']
source: .skillsync/runs/2026-11-04-prototype-komentarze.karta-2/3.1-eng-implementer.md (Odkrycie 3); biblioteka library/skills/prototype/assets/repo/components.css:67-68 (.btn display:inline-flex), library/skills/prototype/assets/repo/prototype.js:4600-4603 (ukrycie także stylem); tests/fixtures/prototype/engine-v2/prototype.js:4584 i 4600 (ta sama treść w kopii silnika v2 w tym repo)
verified: 2026-11-05
---
**Objaw:** przycisk z klasą `.btn` i atrybutem `hidden` (np. „Ponów” w oknie przywracania) jest widoczny, mimo że atrybut stoi.

**Przyczyna:** autorska reguła `.btn { display: inline-flex; … }` (`components.css:67-68`) ma wyższy priorytet niż domyślna reguła przeglądarki `[hidden] { display: none }`. Atrybut `hidden` nie działa więc na elemencie, którego `display` ustawia klasa. Ten sam wzór silnik zapisuje dla `.alert` (`prototype.js` w kopii v2: linia 4584).

**Reguła:**
- Dla elementu z klasą ustawiającą `display` (`.btn`, `.alert`) ukrywaj i pokazuj także stylem, jak w `prototype.js:4600-4603`: `el.hidden = true; el.style.display = 'none';`, a przy pokazaniu `el.style.display = ''`.
- Alternatywa: jawna reguła `.btn[hidden] { display: none; }` w CSS. Dodaj ją, nie zakładaj, że istnieje. Dla elementów bez klasy z `display` (np. `.anno-banners[hidden]`, `prototype.css:169`) sama reguła `[hidden]` wystarcza.
- Nie polegaj na samym `hidden` dla przycisków z `.btn`.

**Jak wykryć:**
- `grep -n "display: *inline-flex" library/skills/prototype/assets/repo/components.css` (w bibliotece), potem `grep -n "\.hidden = \|hidden = " library/skills/prototype/assets/repo/prototype.js` i sprawdzenie, czy każdy ukrywany `.btn` ma `style.display`.
- W przeglądarce (CDP): `getComputedStyle(el).display` dla elementu z atrybutem `hidden` musi dać `none`; wartość `inline-flex` oznacza błąd.

**Granice aktualności:** reguła wynika z kaskady CSS (domyślny styl `[hidden]` przegrywa z autorskim `display`), a nie z wersji przeglądarki. Potwierdzona czytaniem CSS i kodu; w 3.1 nie uruchomiono przeglądarki (E2E NOT_RUN); dowód w przeglądarce należy do E2E okna akceptacji.
