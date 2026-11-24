---
title: "Atrapa DOM zwracająca tablicę ukrywa błąd NodeList: querySelectorAll nie ma find/filter/map w przeglądarce"
tags: [skillsync, dashboard, renderer, testing, dom, nodelist]
paths: ['library/skills/dashboard/assets/dashboard-render.js', 'tests/test_skillsync_dashboard_renderer.py']
source: "przebieg CORE-1142-FB-BUTTON (23.11): review wykrył .find/.filter na wyniku querySelectorAll w oknie zgody; testy były zielone, bo atrapa DOM zwracała prawdziwą tablicę; poprawka w kodzie i w atrapie"
verified: 2026-11-23
---
Objaw: w prawdziwym Chrome Tab w oknie i Anuluj/Esc rzucały TypeError (`find is not a function`), a po udanym zapisie nie wykonywało się odświeżenie. Testy renderera przechodziły.
Przyczyna: atrapa DOM w testach (`tests/test_skillsync_dashboard_renderer.py`) zwracała z `querySelectorAll` zwykłą tablicę, a przeglądarka zwraca NodeList (indeks, `length`, `item`, `forEach`, iterator, bez `find`, `filter`, `map`). Kod produkcyjny, który wołał metody tablicowe wprost, działał tylko w atrapie.
Reguła: atrapa ma odtwarzać interfejs przeglądarki, nie jego wygodniejszy zamiennik; wynik `querySelectorAll` w atrapie to obiekt NodeList-podobny bez metod tablicowych (test kształtu atrapy + test statyczny, że kod nie łańcuchuje metod tablicowych po `querySelectorAll`). W kodzie produkcyjnym używaj `Array.from(...)` albo `Array.prototype.find.call(...)`. Każdy nowy fragment UI z klawiaturą i fokusem sprawdź także w prawdziwej przeglądarce (E2E), bo atrapa nie ma fokusu ani zdarzeń klawiatury.
Jak wykryć: `grep -nE "querySelectorAll\([^)]*\)\.(find|filter|map|some|every|reduce|includes|slice|indexOf|flatMap|sort)\(" library/skills/dashboard/assets/*.js`; test `RealNodeList` w `tests/test_skillsync_feedback_ui.py`; E2E `tests/ui_verify/feedback-dashboard-e2e.test.mjs`.
