---
title: 'Kontrola zakresu CSS (ui-verify css-scope): korzeń zakresu, wyjątki nazwane i granice dowodu'
tags: [skillsync, ui-verify, css, scss, scope, overlay, exceptions, testing]
paths: ['library/skills/ui-verify/scripts/css-scope.mjs', 'library/skills/ui-verify/scripts/lib/css-*.mjs', 'library/skills/ui-verify/references/css-scope.md', 'library/skills/ui-verify/package.json', 'tests/css_scope/**']
source: plan zadania (poza rdzeniem); zachowanie według kodu i testów tests/css_scope
verified: 2026-11-22
---
Reguła: arkusz nakładki (overlay) może tylko dodawać reguły pod jednym korzeniem klasy (`root`), a każdy selektor
spoza korzenia musi trafić w jawnie nazwany wyjątek z powodem. Nakładka nie może usuwać ani zmieniać reguł bazowych:
porównanie z wersją bez nakładki (`diff`) wykrywa usunięcie lub zmianę, a dodana reguła poza korzeniem to naruszenie.
Wyjątki są danymi w pliku JSON, nie wyjątkami w kodzie.

Dlaczego: nakładka działająca poza korzeniem zmienia wygląd całej strony, a jej autor tego nie widzi. Nazwany wyjątek
z powodem pokazuje, co zostało świadomie dopuszczone i kto to uzasadnił. Brak wyjątku nie jest cichym PASS.

Reguły granic (stan bieżący):
- Korzeń to pełna klasa: `.root-x`, `.rooté` ani `.root\,evil` nie są korzeniem.
- Zagnieżdżona reguła (ciało reguły z `{`) jest poza zakresem (`nested rule is not supported`); w diff dodana taka reguła także.
- Dodana reguła w diff jest oceniana tą samą logiką co w check (korzeń, wyjątki).
- Napis z klamrą lub przecinkiem nie zmienia liczby reguł ani podziału selektora; uszkodzony CSS (błąd rozbioru) jest błędem użycia (kod 2), a w selfteście FAIL.

Wyjątki i przykłady:
- `prefix`: selektor zaczynający się od wskazanego napisu (np. element renderowany poza korzeniem). Dopasowanie
  jest napisowe, bez granicy słowa, więc wyjątek trzeba zawężać.
- `root-alias`: inna klasa zakresu (np. klasa motywu) dopuszczona tylko dla właściwości z prefiksem `--`
  (tokeny projektowe). Reguła tej klasy z innymi właściwościami jest naruszeniem.
- `exact-rule`: jeden selektor z dokładnie wskazanym zestawem deklaracji (np. wyłączenie obrysu przy programowym
  fokusie). Każda zmiana tej reguły (dodatkowa deklaracja, inna wartość, grupa selektorów) jest naruszeniem.
  W `diff` ta sama ocena: dokładnie ta reguła dodana w nakładce przechodzi, inna o tym selektorze poza korzeniem nie.
- Limit `!important` (`important.max`) to jedyna ocena treści deklaracji; pozostałe `!important` są tylko liczone.
- Kontrola działa na CSS po kompilacji SCSS, więc dowodem nie jest samo źródło SCSS. Kompilacja jest LibSass 3.6
  (`sass.js` 0.11.1), a nie starym buildem gulp-sass 4 Generatora; wynik może się różnić od Generatora.
- Brak `sass.js` to NOT_RUN (kod 3), nie PASS; jeśli inna kontrola jest FAIL, wynik to FAIL (kod 1).
  Instaluje go `node scripts/install.mjs`; zmiana zestawu zależności zmienia hash cache i wymaga ponownej instalacji.

Gdzie w kodzie:
- `library/skills/ui-verify/scripts/lib/css-scope-core.mjs`: `buildRootPattern` (granica klasy korzenia), `evaluatePrepared`
  (ocena jednej reguły: korzeń, prefiks, alias, `exact-rule`; wspólna dla check i diff), `checkScope` (drzewo reguł, zagnieżdżenia, licznik)
- `library/skills/ui-verify/scripts/lib/css-config.mjs`: `validateConfig`, `parseConfig` (BOM, limit rozmiaru), `toScopeConfig`,
  `builtinConfig`, `builtinCases` (19 przypadków), `runCases` (wynik `error` to FAIL)
- `library/skills/ui-verify/scripts/lib/css-diff.mjs`: `diffCss` (usunięte lub zmienione, dodane poza zakresem, `allowAtRules`; ocena dodanych reguł przez `evaluatePrepared`)
- `library/skills/ui-verify/scripts/lib/css-parse.mjs`: `scan` (komentarze, napisy, grupy, at-bloki liściowe, `@keyframes`, zagnieżdżenia), `walkRules`,
  `splitSelectors`, `splitDeclarations`, `normalizeDeclaration`, `countBraces`, `countImportant`, `collapseWhitespace`, `CssParseError`
- `library/skills/ui-verify/scripts/lib/css-compile.mjs`: `compileScss` (symlinki, limity, ostrzeżenia `skipped`)
- `library/skills/ui-verify/scripts/css-scope.mjs`: `main` (flagi i kody wyjścia), `cmdCheck`, `cmdDiff`, `cmdCompile`, `cmdRun`,
  `cmdSelftest`, `outTarget` (reguły `--out`), `scssParams`, `loadConfig`
- testy (11 plików w `tests/css_scope/`): `css-parse.test.mjs`, `css-scope-core.test.mjs`, `css-diff.test.mjs`,
  `css-config.test.mjs`, `css-compile.test.mjs`, `css-compile-real.test.mjs`, `css-differential.test.mjs`,
  `css-scope-cli.test.mjs`, `css-scope-cli-paths.test.mjs`, `css-scope-cli-real.test.mjs`, `css-deps.test.mjs`
