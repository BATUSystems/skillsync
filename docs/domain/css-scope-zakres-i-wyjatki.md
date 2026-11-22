---
title: 'Kontrola zakresu CSS (ui-verify css-scope): korzeń zakresu, wyjątki nazwane i granice dowodu'
tags: [skillsync, ui-verify, css, scss, scope, overlay, exceptions, testing]
paths: ['library/skills/ui-verify/scripts/css-scope.mjs', 'library/skills/ui-verify/scripts/lib/css-*.mjs', 'library/skills/ui-verify/references/css-scope.md', 'library/skills/ui-verify/package.json', 'tests/css_scope/**']
source: plan zadania (poza rdzeniem)
verified: 2026-11-22
---
Reguła: arkusz nakładki (overlay) może tylko dodawać reguły pod jednym korzeniem klasy (`root`), a każdy selektor
spoza korzenia musi trafić w jawnie nazwany wyjątek z powodem. Nakładka nie może usuwać ani zmieniać reguł bazowych:
porównanie z wersją bez nakładki (`diff`) wykrywa usunięcie lub zmianę, a dodana reguła poza korzeniem to naruszenie.
Wyjątki są danymi w pliku JSON, nie wyjątkami w kodzie.

Dlaczego: nakładka działająca poza korzeniem zmienia wygląd całej strony, a jej autor tego nie widzi. Nazwany wyjątek
z powodem pokazuje, co zostało świadomie dopuszczone i kto to uzasadnił. Brak wyjątku nie jest cichym PASS.

Wyjątki i przykłady:
- `prefix`: selektor zaczynający się od wskazanego napisu (np. element renderowany poza korzeniem). Dopasowanie
  jest napisowe, bez granicy słowa, więc wyjątek trzeba zawężać.
- `root-alias`: inna klasa zakresu (np. klasa motywu) dopuszczona tylko dla właściwości z prefiksem `--`
  (tokeny projektowe). Reguła tej klasy z innymi właściwościami jest naruszeniem.
- `exact-rule`: jeden selektor z dokładnie wskazanym zestawem deklaracji (np. wyłączenie obrysu przy programowym
  fokusie). Każda zmiana tej reguły (dodatkowa deklaracja, inna wartość, grupa selektorów) jest naruszeniem.
  Nie działa w `diff`.
- Limit `!important` (`important.max`) to jedyna ocena treści deklaracji; pozostałe `!important` są tylko liczone.
- Kontrola działa na CSS po kompilacji SCSS, więc dowodem nie jest samo źródło SCSS. Kompilacja jest LibSass 3.6
  (`sass.js` 0.11.1), a nie buildem Generator; wynik może się różnić od Generatora.
- Brak `sass.js` to NOT_RUN (kod 3), nie PASS i nie FAIL. Instaluje go `node scripts/install.mjs`.

Gdzie w kodzie:
- `library/skills/ui-verify/scripts/lib/css-scope-core.mjs` (`checkScope`: korzeń, wyjątki, `exact-rule` przed korzeniem)
- `library/skills/ui-verify/scripts/lib/css-config.mjs` (walidacja schematu, `root-alias` różne od `root`, `reason` wymagane, wbudowane przypadki)
- `library/skills/ui-verify/scripts/lib/css-diff.mjs` (`diffCss`: usunięte i dodane poza zakresem, `allowAtRules`)
- `library/skills/ui-verify/scripts/lib/css-parse.mjs` (komentarze, napisy, grupy, liczba `{`)
- `library/skills/ui-verify/scripts/css-scope.mjs` (polecenia, kody wyjścia, `FAIL` dla `run` przy błędzie SCSS)
- testy: `tests/css_scope/css-scope-core.test.mjs`, `tests/css_scope/css-config.test.mjs`, `tests/css_scope/css-diff.test.mjs`, `tests/css_scope/css-scope-cli.test.mjs`
