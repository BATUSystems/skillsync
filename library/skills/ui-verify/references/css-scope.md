# css-scope — kontrola zakresu CSS nakładki

Polecenie `node scripts/css-scope.mjs` pilnuje, że arkusz nakładki (overlay) tylko dodaje reguły pod jednym
korzeniem klasy i nie usuwa ani nie zmienia reguł bazowych. Dostarcza pięć poleceń: `check`, `diff`, `compile`,
`run`, `selftest`. Konfiguracja jest danymi (plik JSON), nie kodem. Ścieżki `scripts/` są względne do katalogu skilla.

Zasada: wynik bez dowodu nie jest PASS. Brak zależności `sass.js` to NOT_RUN (kod 3), nie PASS.

## Polecenia

| Polecenie | Wejście | Co sprawdza | Zależność sass.js |
|---|---|---|---|
| `check <plik.css>` | jeden plik CSS, `--config` albo `--root` | każdy selektor pod korzeniem lub w wyjątku, limit `!important` | nie |
| `diff <z.css> <bez.css>` | dwa pliki CSS i `--config` (wymagany) | nic nie znika ani się nie zmienia; dodane reguły są w zakresie | nie |
| `compile` | `--config` albo flagi `--scss-dir` i `--entry` | kompilacja SCSS do CSS (LibSass) | tak |
| `run` | `--config` (wymagany) | selftest, kompilacja, zakres, limit `!important`, diff z `without` (jeśli ustawiony) | tak |
| `selftest` | opcjonalnie `--config` | przypadki wbudowane i przypadki z konfiguracji | nie |

Dodatkowo `node scripts/css-scope.mjs --selftest` uruchamia 21 wewnętrznych przypadków (parser, zakres, diff,
konfiguracja, ścieżki, kody wyjścia, maska sekretów). Wynik to wiersze `PASS <nazwa>` i `selftest css-scope: N/21 passed`.

### Kiedy używać

- Nakładka tematu lub motywu ma być ograniczona do jednego korzenia (np. `.root`) i nie może zmieniać stylów
  bazowych: `run` z `without` (bazowy arkusz skompilowany osobno) albo `diff` na gotowych CSS.
- Chcesz zatrzymać narastanie `!important`: `important.max` w konfiguracji.
- Chcesz tylko sprawdzić jeden gotowy plik CSS: `check`.

### Flagi

| Flaga | Polecenia | Znaczenie |
|---|---|---|
| `--config <plik.json>` | check (opcjonalnie), diff, compile, run, selftest (opcjonalnie) | konfiguracja; ścieżka względna do cwd; katalog konfiguracji to katalog tego pliku |
| `--root <.klasa>` | check, bez `--config` | korzeń zakresu jako flaga; bez wyjątków i bez limitu `!important`; razem z `--config` to błąd (kod 2) |
| `--out <plik.css>` | compile, run | zapis CSS wyniku; względem cwd; ma pierwszeństwo przed `out` z konfiguracji |
| `--scss-dir <katalog>` | compile, run | katalog SCSS; względny do cwd; nadpisuje `scssDir` |
| `--entry <plik.scss>` | compile, run | plik wejściowy (sama nazwa); nadpisuje `entry` |
| `--without <plik.scss>` | run | plik bazowy do diff (sama nazwa); nadpisuje `without` |
| `--json` | wszystkie | jeden obiekt JSON zamiast wierszy |
| `--help` | wszystkie | wypisuje użycie i flagi na stdout, kod 0 |

Flagi `--scss-dir`, `--entry`, `--without` są dozwolone tylko dla `compile` i `run`; dla innych poleceń to kod 2.
Flaga nadpisująca wyłącza odpowiadający klucz z pliku konfiguracji jeszcze przed walidacją, więc błędna wartość
w pliku dla tego klucza nie blokuje przebiegu.

Uwaga: `--out` przy `check`, `diff` i `selftest` jest przyjmowany i ignorowany bez komunikatu. `--root` przy
poleceniach innych niż `check` jest ignorowany bez komunikatu. `--without` przy `compile` jest przyjmowany i nie jest
używany. Patrz „Niespójności” niżej.

Liczba argumentów pozycyjnych musi się zgadzać: `check` jeden plik, `diff` dwa pliki, pozostałe zero. Inaczej kod 2.

## Schemat konfiguracji

Plik JSON, najwyżej 1 000 000 znaków. Nieznany klucz (także na poziomie wyjątku i progu) to błąd kodu 2
z nazwą klucza.

| Klucz | Typ | Wymagany | Domyślnie | Uwagi |
|---|---|---|---|---|
| `version` | liczba `1` | tak | — | inna wartość to błąd |
| `root` | klasa `.nazwa` | tak | — | wzorzec `^\.[A-Za-z_][A-Za-z0-9_-]*$` |
| `scssDir` | ścieżka względna | nie | brak | względna do katalogu konfiguracji; wymagana dla compile/run, chyba że `--scss-dir` |
| `entry` | nazwa `*.scss` | nie | brak | wzorzec `^[A-Za-z0-9._-]+\.scss$`; wymagany dla compile/run, chyba że `--entry` |
| `without` | nazwa `*.scss` | nie | brak | plik bazowy dla diff w `run` |
| `out` | ścieżka względna `*.css` | nie | brak | względna do katalogu konfiguracji; brak = brak zapisu |
| `exceptions` | tablica | nie | `[]` | wyjątki nazwane, patrz niżej |
| `selftest` | tablica | nie | `[]` | przypadki własne projektu, patrz niżej |
| `important` | obiekt `{ "max": n }` | nie | brak limitu | `max` to liczba całkowita `>= 0`; pusty obiekt = bez limitu |
| `diff` | obiekt `{ "allowAtRules": [...] }` | nie | `["font-face"]` | dozwolone wartości: `font-face`, `keyframes`, `page` |

Ścieżki w konfiguracji (`scssDir`, `out`) nie mogą być bezwzględne, nie mogą zawierać `..` ani `\`
i muszą leżeć w katalogu konfiguracji (walidacja leksykalna, patrz „Ścieżki”).

### Wyjątki (`exceptions`)

Każdy wyjątek ma: `id` (`[a-z0-9-]{1,40}`, unikalny), `kind` (jedna z trzech wartości), `reason`
(niepusty tekst; bez niego walidacja odrzuca wyjątek). Powód (`reason`) trafia do opisu naruszeń, a wyjątek,
który w danym przebiegu nie zadziałał, daje ostrzeżenie `WARN scope: exception <id> was not used` (bez wpływu na werdykt).

| `kind` | Pola | Dopasowanie |
|---|---|---|
| `prefix` | `value` (tekst, bez znaków sterujących) | selektor zaczyna się od `value` jako zwykły prefiks napisu (bez granicy słowa) |
| `root-alias` | `value` (klasa `.nazwa`, różna od `root`), opcjonalnie `onlyPropertyPrefix` (tekst zaczynający się od `--`) | selektor to klasa `value` z granicą (nie `.nazwa-x`); z `onlyPropertyPrefix` każda deklaracja reguły musi mieć nazwę zaczynającą się od tego prefiksu |
| `exact-rule` | `selector` (jeden selektor, po zwinięciu spacji) i `declarations` (niepusta tablica napisów z `:`) | reguła o dokładnie tym selektorze i dokładnie tych deklaracjach (po normalizacji, bez nadmiarowych i bez brakujących) |

Przykłady neutralne:

```json
{ "id": "menu-host", "kind": "prefix", "value": ".menu-host > .item", "reason": "menu rendered outside the root" }
{ "id": "theme-hook", "kind": "root-alias", "value": ".root-theme", "onlyPropertyPrefix": "--tok-", "reason": "design tokens only" }
{ "id": "focus-target", "kind": "exact-rule", "selector": ".root-theme main[tabindex=\"-1\"]:focus:not(:focus-visible)", "declarations": ["outline:0!important"], "reason": "programmatic focus target" }
```

Zasady wyjątków:
- `exact-rule` ma pierwszeństwo nad korzeniem: reguła o tym selektorze z innymi deklaracjami jest błędem,
  nawet gdy selektor zaczyna się od korzenia.
- `exact-rule` działa tylko dla pojedynczego selektora; grupa `a, b` z tym selektorem to błąd.
- `exact-rule` nie jest uwzględniany w `diff` (dopasowanie w diff obejmuje tylko korzeń, `prefix` i `root-alias`).
  Dodana w nakładce reguła o takim selektorze poza korzeniem to „ADDED OUTSIDE SCOPE”.
- Deklaracje `exact-rule` są normalizowane: spacje między tokenami są usuwane, nazwa właściwości jest
  pisana małymi literami; wartość nie jest zmieniana co do wielkości liter. Zapis `outline: 0 !important`
  i `outline:0!important` jest równoważny.

### Przypadki selftestu (`selftest`)

Każdy przypadek: `name` (1–120 znaków, unikalny), `css` (napis do 20 000 znaków), `expect`
(`accepted` albo `rejected`). Przypadek przechodzi, gdy kontrola zakresu daje oczekiwany wynik.

```json
{ "name": "root element accepted", "css": ".root a { color: red }", "expect": "accepted" }
```

Przypadki wbudowane (`builtinCases`, zawsze uruchamiane przez `run` i `selftest`, przeciw wbudowanej
konfiguracji z korzeniem `.root` i trzema wyjątkami: `menu-host`, `theme-hook`, `skip-link`):

| Nazwa | Oczekiwanie |
|---|---|
| root-element, root-compound, root-in-media | accepted |
| element-outside-root, body-outside-root, outside-root-in-media | rejected |
| prefix-exception | accepted |
| prefix-other-child | rejected |
| alias-token-properties | accepted |
| alias-other-property, alias-mixed-properties | rejected |
| exact-rule-expanded, exact-rule-compressed | accepted |
| exact-rule-extra-declaration, exact-rule-none-instead-of-zero, exact-rule-grouped | rejected |
| exact-rule-other-alias-element, exact-rule-other-attribute-value, exact-rule-focus-visible | rejected |

Przypadki z `selftest` w konfiguracji są sprawdzane przeciw konfiguracji użytkownika. Niezgodność to FAIL
(kod 1) z listą przypadków.

### `important.max`

- Liczone są wystąpienia `!important` poza komentarzami i napisami (także `! important`), bez oceny treści.
- Limit to jedyna ocena: liczba większa niż `max` daje FAIL (kod 1).
- Bez `important` w konfiguracji tylko informacja `INFO important: count N (no limit configured)` (przy `run`).

### `diff.allowAtRules`

Dozwolone dodane at-reguły w diff: `font-face` (domyślnie), `keyframes` (także z prefiksem dostawcy), `page`.
Reguła `@keyframes` dodana w nakładce jest błędem, dopóki nie stoi na liście. Pozostałe dodane at-bloki i
instrukcje (np. `@import`) są błędem, jeśli są dodane. Usunięcie któregokolwiek z nich też jest błędem.

## Parser (`lib/css-parse.mjs`)

- Komentarze `/* … */` są zastępowane spacjami (znaki nowej linii zostają). Nieskończony komentarz to błąd parsowania.
- Napisy w `"` i `'` są pomijane (z obsługą `\`): nawiasy klamrowe, przecinki i `!important` w napisach
  nie liczą się. Nieskończony napis to błąd parsowania.
- Przecinek w `:where(a, b)` i w nawiasach nie dzieli selektora; średnik w nawiasach nie dzieli deklaracji.
- Grupy, których reguły są sprawdzane: `@media`, `@supports`, `@document`, `@-moz-document`, `@layer`,
  `@container`, `@scope`, `@starting-style`. Korzeń obowiązuje także w środku grupy.
- `@keyframes` (także z prefiksem dostawcy): kroki nie są sprawdzane pod kątem zakresu; w diff traktowane jako całość.
- Pozostałe at-bloki (np. `@font-face`, `@page`): ich treść nie jest sprawdzana pod kątem zakresu w `check`
  i `run`; w diff są oceniane przez `diff.allowAtRules`.
- Instrukcje zakończone `;` na poziomie zero (`@import`, `@charset`) nie są sprawdzane pod kątem zakresu.
- Niezrównoważone klamry: błąd z linią i kolumną (`Unbalanced brace … at line L, column C`); nadmiarowe `}`:
  `Unexpected closing brace`.
- Liczba reguł w wyniku (pole `rules`, linia „scope OK: N rules”) to liczba `{` poza komentarzami i napisami.
  Liczy więc także bloki grup, at-bloki i kroki `@keyframes`. Nie jest to liczba selektorów.

## Format wyniku

Tekst (domyślnie) to wiersze `PASS|FAIL|INFO|WARN <nazwa>: <szczegóły>`, a pod nimi do 50 nieprawidłowych
selektorów (`  <selektor>`, z dopiskiem „and N more”). Przykłady wierszy:

- `PASS scope: scope OK: 12 rules, all under .root (+ 3 exceptions), !important: 0`
- `FAIL scope: 2 selectors outside scope .root (14 rules)`
- `INFO important-count: 4` (tylko w `check`), `FAIL important: 4 > 3`
- `WARN scope: exception menu-host was not used`
- `PASS diff: with 40, without 38; added 2 (allowed 2), removed 0` i pod nim do 10 przykładów:
  `REMOVED/CHANGED: <kontekst><selektor>` (kontekst to łańcuch grup, np. `@media print >> `; bez deklaracji, do 160 znaków), `ADDED OUTSIDE SCOPE: …`
- `PASS compile: main.scss -> 1834 chars` albo `FAIL compile: main.scss: <błąd LibSass>` (błąd do 4000 znaków)
- `PASS <nazwa przypadku> (expected accepted, got accepted)` dla przypadków selftestu
- `selftest: N/M PASS` na końcu `selftest`; w `run` wiersz `PASS selftest: 19/19 cases` albo `FAIL selftest: <nazwa> (expected …, got …)`
- `selftest css-scope: N/21 passed` na końcu `--selftest`
- `RESULT: PASS|FAIL|NOT_RUN` na końcu `run`
- Brak sass.js: `INFO compile: not run: sass.js is not installed (node scripts/install.mjs)` i wiersze na stderr

`--json` drukuje jeden obiekt w jednym wierszu:
`{ "command", "verdict", "exit", "checks": [ { "name", "verdict", "detail", "examples"? } ], "counts": { … } }`.
W trybie JSON nie ma linii `RESULT:`; werdykt jest w polu `verdict`. Pole `examples` pojawia się tylko, gdy jest niepuste.
`counts`: `check` daje `rules`, `important`, `bad`; `diff` daje `with`, `without`, `added`, `addedAllowed`, `removed`;
`compile` daje `files` i `chars`; `run` łączy `selftest` (`passed`, `total`), `chars`, `rules`, `important` i liczniki diff;
`selftest` daje `passed` i `total`.

Ostrzeżenia `WARN` nie zmieniają werdyktu. Werdykt to FAIL, jeśli jest choć jeden FAIL; w przeciwnym razie PASS.

## Kody wyjścia

| Kod | Znaczenie |
|---|---|
| 0 | PASS: wszystkie kontrole przeszły; `--help` |
| 1 | FAIL: selektor poza zakresem, usunięta lub zmieniona reguła, dodana reguła poza zakresem lub niedozwolona at-reguła, limit `!important`, błąd kompilacji SCSS (także bazowego w `run`), niezgodność przypadków selftestu |
| 2 | błąd użycia (zła flaga, zła liczba argumentów, flaga niedozwolona dla polecenia), konfiguracji (JSON, schemat, wyjątek), pliku CSS (brak, nie zwykły plik, ponad 20 MB), parsowania CSS (z linią), ścieżki (polityka), cache (niebezpieczny), błąd wewnętrzny |
| 3 | NOT_RUN: brak `sass.js` w cache dla `compile` i `run` |

Kod 2 nigdy nie jest mapowany na 1. Nieobsłużony wyjątek kończy się kodem 2.

Uwaga do kodu 3 w `run`: gdy `sass.js` brakuje, wynik to NOT_RUN, nawet jeśli selftest już zgłosił FAIL (wiersz
`FAIL selftest` zostaje w wyjściu, ale kod to 3). Patrz „Niespójności”.

## Ścieżki i polityka

- Pliki CSS w `check` i `diff`: `path.resolve` względem cwd (absolutna ścieżka też przechodzi). Kontrola:
  zwykły plik, najwyżej 20 MB, odczyt UTF-8. Bez polityki katalogu.
- `--config`: względny do cwd. Katalog konfiguracji = katalog tego pliku; od niego liczą się `scssDir` i `out`.
- `scssDir` i `out` z konfiguracji: względne do katalogu konfiguracji. Walidacja i ponowne sprawdzenie
  używają `resolveInside` (leksykalnie: bez `..`, bez ścieżki bezwzględnej, bez `\`, wynik w katalogu).
- `--scss-dir` i `--out`: względne do cwd, `resolveInside(cwd, …)`.
- `--entry`, `--without` i klucze `entry`, `without`: tylko nazwa pliku `^[A-Za-z0-9._-]+\.scss$`.
- Odczyt SCSS: tylko pliki pierwszego poziomu `scssDir` o nazwie zgodnej z wzorcem; entry musi być wśród nich.
  Pozostałe nazwy są pomijane. Limity: 500 plików, 2 000 000 bajtów na plik.
- Zapis: tylko CSS wyniku, tylko do `--out` albo `config.out`, przez jedno gardło zapisu (`createSink`):
  `resolveInside` plus `assertRealInside` (realpath najbliższego istniejącego przodka musi leżeć w realpath katalogu
  bazowego, a sam cel nie może być symlinkiem). Bez `--out` i bez `out` nic nie jest zapisywane.
- `run --out` zapisuje CSS po udanej kompilacji, także gdy kontrola zakresu potem da FAIL.
- Sekrety: wyjście przechodzi przez maskowanie (`UI_VERIFY_SECRET_ENV`); css-scope nie wymaga sekretów.

## Zależność sass.js

- `sass.js` 0.11.1 jest przypięty w `package.json` i `package-lock.json` skilla. Instaluje go
  `node scripts/install.mjs` razem z pozostałymi zależnościami (`npm ci --ignore-scripts --omit=dev`,
  do cache `<hash>`, nigdy do projektu). `install.mjs --check` sprawdza też `sass.js`.
- Ładowanie: wyłącznie z cache (`UI_VERIFY_CACHE_DIR` albo domyślny katalog systemowy), z tą samą kontrolą
  bezpieczeństwa cache co przy innych zależnościach; wersja zgodna z lockfile.
- Bez `sass.js`: `compile` i `run` kończą się kodem 3 z podpowiedzią `node scripts/install.mjs`.
  `check`, `diff` i `selftest` działają bez niego.
- Sieć: poza instalacją (`install.mjs`) css-scope nie wykonuje żadnych połączeń.

## Granice

- LibSass 3.6 przez `sass.js` 0.11.1 to nie build Generator (gulp-sass). Wynik kompilacji może różnić się
  od kompilacji w Generatorze; dowodem zgodności nie jest sam wynik css-scope.
- `!important` jest liczony, nie oceniany (poza limitem `important.max`).
- `exact-rule` nie dopuszcza nowej reguły w `diff` (patrz wyżej).
- Pusta reguła (`.root-theme {}`) przy `onlyPropertyPrefix` przechodzi, bo nie ma deklaracji do sprawdzenia.
- Prefiks `prefix` dopasowuje się jako napis: `.menu-host > .item` przepuszcza też `.menu-host > .item-x`.
  Zapisz wyjątek tak wąsko, jak to możliwe.
- Brak przepisywania CSS; kontrola nie naprawia nakładki.
- Symlinki w `scssDir` są czytane według celu (odczyt nie sprawdza realpath). Zapis jest chroniony.
- Pliki CSS z `check` i `diff` nie podlegają polityce ścieżek (patrz wyżej).
- Nowe flagi tylko dla `compile` i `run` (`--scss-dir`, `--entry`, `--without`). `--out` działa tylko w tych
  dwóch poleceniach; w pozostałych jest ignorowany (patrz „Niespójności”).
- Normalizacja `exact-rule` usuwa spacje między tokenami wartości, więc `a b` i `ab` są równe.

## Niespójności kod ↔ dokument (do decyzji orkiestratora)

- `run` z brakującym `sass.js` i FAIL z selftestu daje NOT_RUN (kod 3), a nie FAIL (kod 1). Reguła „FAIL wygrywa”
  z `lib/verdict.mjs` (`combine`) tu nie obowiązuje.
- `--out` przy `check`, `diff`, `selftest` oraz `--root` przy innych poleceniach są ignorowane bez błędu.
  Nagłówek skryptu tego nie mówi.
- `--without` przy `compile` jest przyjmowany i nie używany.
- Ostrzeżenia z `compileScss` (pominięte pliki o niedozwolonej nazwie) nie trafiają do wyniku CLI.
- Nagłówek skryptu opisuje brak symlinków tylko przy zapisie; odczyt `scssDir` podąża za symlinkami (bez `assertRealInside`).
  Dokument mówi to wprost; nagłówek nie.
- Pole `rules` w wyniku liczy wszystkie bloki `{`, nie reguły.
