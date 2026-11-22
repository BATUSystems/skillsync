# css-scope — kontrola zakresu CSS nakładki

Polecenie `node scripts/css-scope.mjs` pilnuje, że arkusz nakładki (overlay) tylko dodaje reguły pod jednym
korzeniem klasy i nie usuwa ani nie zmienia reguł bazowych. Dostarcza pięć poleceń: `check`, `diff`, `compile`,
`run`, `selftest`. Konfiguracja jest danymi (plik JSON), nie kodem. Ścieżki `scripts/` są względne do katalogu skilla.

Zasada: wynik bez dowodu nie jest PASS. Brak zależności `sass.js` to NOT_RUN (kod 3), chyba że któraś kontrola
jest FAIL; wtedy wynik to FAIL (kod 1).

## Polecenia

| Polecenie | Wejście | Co sprawdza | Zależność sass.js |
|---|---|---|---|
| `check <plik.css>` | jeden plik CSS, `--config` albo `--root` | każdy selektor pod korzeniem lub w wyjątku, limit `!important` | nie |
| `diff <z.css> <bez.css>` | dwa pliki CSS i `--config` (wymagany) | nic nie znika ani się nie zmienia; dodane reguły są oceniane tą samą funkcją co w `check` | nie |
| `compile` | `--config` (wymagany; `--scss-dir` i `--entry` mogą nadpisać klucze) | kompilacja SCSS do CSS (LibSass) | tak |
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
| `--config <plik.json>` | check (zamiast `--root`), diff (wymagany), compile (wymagany), run (wymagany), selftest (opcjonalnie) | konfiguracja; ścieżka względna do cwd; katalog konfiguracji to katalog tego pliku |
| `--root <.klasa>` | check, bez `--config` | korzeń zakresu jako flaga; bez wyjątków i bez limitu `!important`; razem z `--config` to błąd (kod 2) |
| `--out <plik.css>` | compile, run | zapis CSS wyniku; względem cwd; ma pierwszeństwo przed `out` z konfiguracji; musi kończyć się `.css` i nie może wskazywać pliku wejściowego SCSS ani pliku konfiguracji |
| `--scss-dir <katalog>` | compile, run | katalog SCSS; względny do cwd; nadpisuje `scssDir` |
| `--entry <plik.scss>` | compile, run | plik wejściowy (sama nazwa); nadpisuje `entry` |
| `--without <plik.scss>` | run | plik bazowy do diff (sama nazwa); nadpisuje `without` |
| `--json` | wszystkie | jeden obiekt JSON zamiast wierszy |
| `--help` | wszystkie | wypisuje użycie i flagi na stdout, kod 0 |

Flagi bez znaczenia dla polecenia są błędem kodu 2 (nie są ignorowane): `--scss-dir`, `--entry`, `--without`
tylko dla `compile` i `run`; `--without` nie dotyczy `compile`; `--out` tylko dla `compile` i `run`;
`--root` tylko dla `check`.

Flaga nadpisująca wyłącza odpowiadający klucz z pliku konfiguracji jeszcze przed walidacją, więc błędna wartość
w pliku dla tego klucza nie blokuje przebiegu.

Liczba argumentów pozycyjnych musi się zgadzać: `check` jeden plik, `diff` dwa pliki, pozostałe zero. Inaczej kod 2.

## Schemat konfiguracji

Plik JSON, nie więcej niż 1 000 000 bajtów (rozmiar sprawdzany przed odczytem; BOM UTF-8 na początku jest zdejmowany).
Parser dodatkowo odrzuca tekst powyżej 1 000 000 znaków. Nieznany klucz (także na poziomie wyjątku i progu) to błąd
kodu 2 z nazwą klucza.

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
i muszą leżeć w katalogu konfiguracji (walidacja leksykalna, patrz „Ścieżki i polityka”).

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
- `exact-rule` w `diff` jest oceniany tak jak w `check`: dodana w nakładce reguła o dokładnie tym selektorze
  i zestawie deklaracji przechodzi; inna reguła o tym selektorze poza korzeniem to „ADDED OUTSIDE SCOPE”.
- Deklaracje `exact-rule` są normalizowane: wszystkie białe znaki poza napisami są usuwane, nazwa właściwości jest
  pisana małymi literami; wartość nie jest zmieniana co do wielkości liter. Zapis `outline: 0 !important`
  i `outline:0!important` jest równoważny, a `a b` i `ab` są równe.

### Przypadki selftestu (`selftest`)

Każdy przypadek: `name` (1–120 znaków, unikalny), `css` (napis do 20 000 znaków), `expect`
(`accepted` albo `rejected`). Przypadek przechodzi, gdy kontrola zakresu daje oczekiwany wynik.
Gdy CSS przypadku jest uszkodzony (błąd rozbioru), wynik to `got error: <komunikat>` i przypadek jest FAIL,
niezależnie od `expect`.

```json
{ "name": "root element accepted", "css": ".root a { color: red }", "expect": "accepted" }
```

Przypadki wbudowane (`builtinCases`, zawsze uruchamiane przez `run` i `selftest`, przeciw wbudowanej
konfiguracji z korzeniem `.root` i trzema wyjątkami: `menu-host`, `theme-hook`, `skip-link`; razem 19):

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

Przypadki z `selftest` w konfiguracji są sprawdzane przeciw konfiguracji użytkownika. Niezgodność albo błąd
rozbioru to FAIL (kod 1) z listą przypadków.

### `important.max`

- Liczone są wystąpienia `!important` poza komentarzami i napisami (także `! important`), bez oceny treści.
- Limit to jedyna ocena: liczba większa niż `max` daje FAIL (kod 1).
- Bez `important` w konfiguracji tylko informacja `INFO important: count N (no limit configured)` (przy `run`).
  Przy `check` jest to `INFO important-count: N`.

### `diff.allowAtRules`

- Każda dodana reguła (także w grupie, np. `@media`) jest oceniana tak jak w `check`: korzeń, prefiks, alias
  i `exact-rule`. Dodana reguła zagnieżdżona (zob. „Parser”) jest poza zakresem.
- Dodane at-bloki liściowe i `@keyframes` są dozwolone tylko, gdy są na tej liście: `font-face` (domyślnie),
  `keyframes` (także z prefiksem dostawcy), `page`. Inne at-bloki liściowe (np. `@property`) i instrukcje
  (np. `@import ...;`) dodane w nakładce są błędem.
- Grupy at-bloków (np. `@media`) nie są porównywane same; porównywane są reguły w nich.
- Usunięcie albo zmiana dowolnego elementu bazy (reguły, at-bloku, `@keyframes`) to FAIL.

## Parser (`lib/css-parse.mjs`)

- Komentarze `/* … */` są zastępowane spacjami (znaki nowej linii zostają). Nieskończony komentarz to błąd parsowania (kod 2).
- Napisy w `"` i `'` są pomijane (z obsługą `\`): nawiasy klamrowe, przecinki i `!important` w napisach
  nie liczą się. Napis nieskończony albo przerwany surową nową linią (LF, CR, FF) to błąd parsowania (kod 2).
- Przecinek w `:where(a, b)`, w nawiasach i w `[a="x,y"]` nie dzieli selektora; średnik w nawiasach nie dzieli deklaracji.
- Zagnieżdżona reguła, czyli ciało reguły zawierające `{` (np. `.root a { & b { color: red } }` albo
  `.root a { @media print { … } }`), to naruszenie zakresu: `nested rule is not supported`.
- Grupy: każdy at-blok z blokiem, który nie jest liściem (np. `@media`, `@supports`, `@document`, `@-moz-document`,
  `@layer`, `@container`, `@scope`, `@starting-style` i każdy inny at-blok z blokiem), jest grupą; jego reguły są
  sprawdzane rekurencyjnie. Korzeń obowiązuje także w środku grupy.
- Liście (ciało nie jest sprawdzane pod kątem zakresu w `check` i `run`): `font-face`, `page`, `property`,
  `counter-style`, `font-feature-values`, `font-palette-values`, `position-try`, `view-transition`, `viewport`,
  `-ms-viewport`, `-webkit-viewport`.
- Nazwa at-reguły jest dekodowana z escape CSS (`@\6d edia` to `@media`) i porównywana bez względu na wielkość liter.
- `@keyframes` (także z prefiksem dostawcy): kroki nie są sprawdzane pod kątem zakresu; w diff traktowane jako całość.
- Instrukcje zakończone `;` na poziomie zero (`@import`, `@charset`, `@layer a, b;`) nie są sprawdzane pod kątem
  zakresu i nie ukrywają następnej reguły: `body { … }` po `@import url(a.css);` jest sprawdzana.
- Niezrównoważone klamry: błąd z linią i kolumną (`Unbalanced brace … at line L, column C`); nadmiarowe `}`:
  `Unexpected closing brace` (kod 2). Ten sam kod 2 dotyczy niezamkniętego komentarza, napisu i klamry.
- Liczba reguł w wyniku (pole `rules`, linia „scope OK: N rules”) to liczba `{` poza komentarzami i napisami.
  Liczy więc także bloki grup, at-bloki i kroki `@keyframes`. Nie jest to liczba selektorów.

## Format wyniku

Tekst (domyślnie) to wiersze `PASS|FAIL|INFO|WARN <nazwa>: <szczegóły>`, a pod nimi do 50 nieprawidłowych
selektorów (`  <selektor>`, z dopiskiem „and N more”). Przykłady wierszy:

- `PASS scope: scope OK: 12 rules, all under .root (+ 3 exceptions), !important: 0`
- `FAIL scope: 2 selectors outside scope .root (14 rules)`
- `INFO important-count: 4` (tylko w `check`), `FAIL important: 4 > 3`
- `WARN scope: exception menu-host was not used`
- `WARN compile: skipped <nazwa>`: w katalogu SCSS pominięty wpis, którego nazwa nie pasuje do wzorca `*.scss`
  (katalogi są pomijane bez ostrzeżenia)
- `PASS diff: with 40, without 38; added 2 (allowed 2), removed 0` i pod nim do 10 przykładów:
  `REMOVED/CHANGED: <kontekst><selektor>` (kontekst to łańcuch grup, np. `@media print >> `; bez deklaracji, do 160 znaków), `ADDED OUTSIDE SCOPE: …`
- `PASS compile: main.scss -> 1834 chars` albo `FAIL compile: main.scss: <błąd LibSass>` (błąd do 4000 znaków)
- `PASS <nazwa przypadku> (expected accepted, got accepted)` dla przypadków selftestu;
  `FAIL <nazwa przypadku> (expected accepted, got error: <komunikat>)` dla uszkodzonego CSS
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

Ostrzeżenia `WARN` nie zmieniają werdyktu. Werdykt to FAIL, jeśli jest choć jeden FAIL; w przeciwnym razie PASS
(z zastrzeżeniem NOT_RUN przy braku sass.js, zob. kody wyjścia).

## Kody wyjścia

| Kod | Znaczenie |
|---|---|
| 0 | PASS: wszystkie kontrole przeszły; `--help` |
| 1 | FAIL: selektor poza zakresem, zagnieżdżona reguła, usunięta lub zmieniona reguła, dodana reguła poza zakresem lub niedozwolona at-reguła, limit `!important`, błąd kompilacji SCSS (także bazowego w `run`), symlink w katalogu SCSS (plik o nazwie `*.scss` albo katalog wejściowy), przekroczony limit plików SCSS (liczba, rozmiar pliku, suma 16 MB), niezgodność albo uszkodzony przypadek selftestu |
| 2 | błąd użycia (zła flaga, zła liczba argumentów, flaga bez znaczenia dla polecenia, brak katalogu SCSS albo pliku wejściowego, gdy sass.js jest dostępny), konfiguracji (JSON, schemat, wyjątek, rozmiar powyżej 1 000 000 bajtów), pliku CSS (brak, nie zwykły plik, ponad 20 MB), parsowania CSS (z linią), ścieżki (polityka, `--out`), cache (niebezpieczny), błąd wewnętrzny |
| 3 | NOT_RUN: brak `sass.js` w cache dla `compile` i `run`, i żadna kontrola nie jest FAIL |

Kod 2 nigdy nie jest mapowany na 1. Nieobsłużony wyjątek kończy się kodem 2.

Kolejność w `run` i `compile`: najpierw walidacja flag i konfiguracji (kod 2), potem sprawdzenie `sass.js`
(brak = NOT_RUN, kod 3, bez czytania katalogu SCSS), potem kompilacja. Gdy `sass.js` brakuje, a jakakolwiek
wcześniejsza kontrola (np. selftest) jest FAIL, wynik to FAIL z kodem 1, a wiersz `FAIL selftest` zostaje w wyjściu.

## Ścieżki i polityka

- Pliki CSS w `check` i `diff`: `path.resolve` względem cwd (absolutna ścieżka też przechodzi). Kontrola:
  zwykły plik, najwyżej 20 MB, odczyt UTF-8. Bez polityki katalogu.
- `--config`: względny do cwd. Katalog konfiguracji = katalog tego pliku; od niego liczą się `scssDir` i `out`.
- `scssDir` i `out` z konfiguracji: względne do katalogu konfiguracji. Walidacja i ponowne sprawdzenie
  używają `resolveInside` (leksykalnie: bez `..`, bez ścieżki bezwzględnej, bez `\`, wynik w katalogu).
- `--scss-dir` i `--out`: względne do cwd, `resolveInside(cwd, …)`.
- `--entry`, `--without` i klucze `entry`, `without`: tylko nazwa pliku `^[A-Za-z0-9._-]+\.scss$`.
- Odczyt SCSS (`compileScss`): tylko pliki pierwszego poziomu `scssDir` o nazwie zgodnej z wzorcem; entry musi być wśród nich.
  Pozostałe nazwy są pomijane z ostrzeżeniem `skipped`, katalogi bez ostrzeżenia.
- Odczyt SCSS nie podąża za symlinkami: symlink-plik o nazwie `*.scss` albo symlink-katalog jako katalog SCSS
  (`scssDir`, `--scss-dir`) to FAIL (kod 1), a jego zawartość nie trafia do kompilacji. Symlink o innej nazwie jest pominięty z ostrzeżeniem.
- Limity odczytu (sprawdzane z rozmiarów przed odczytem plików): 500 plików, 2 000 000 bajtów na plik,
  16 000 000 bajtów łącznie. Przekroczenie to FAIL (kod 1).
- Zapis: tylko CSS wyniku, tylko do `--out` albo `config.out`, przez jedno gardło zapisu (`createSink`):
  `resolveInside` plus `assertRealInside` (realpath najbliższego istniejącego przodka musi leżeć w realpath katalogu
  bazowego, a sam cel nie może być symlinkiem). Bez `--out` i bez `out` nic nie jest zapisywane.
  Cel zapisu nie może być plikiem wejściowym SCSS ani plikiem konfiguracji (kod 2).
- `run --out` zapisuje CSS po udanej kompilacji, także gdy kontrola zakresu potem da FAIL.
- Sekrety: wyjście przechodzi przez maskowanie (`UI_VERIFY_SECRET_ENV`); css-scope nie wymaga sekretów.

## Zależność sass.js

- `sass.js` 0.11.1 jest przypięty w `package.json` i `package-lock.json` skilla. Instaluje go
  `node scripts/install.mjs` razem z pozostałymi zależnościami (`npm ci --ignore-scripts --omit=dev`,
  do cache `<hash>`, nigdy do projektu). `install.mjs --check` sprawdza też `sass.js`.
- Katalog cache zestawu zależności nosi hash (pierwsze 16 znaków SHA-256) z zawartości `package.json` i
  `package-lock.json`. Każda zmiana któregokolwiek z tych plików, także dodanie `sass.js`, daje nowy katalog;
  przed pierwszym użyciem trzeba ponownie uruchomić `node scripts/install.mjs`. Dotyczy to wszystkich poleceń
  ui-verify ładujących zależności.
- Ładowanie: wyłącznie z cache (`UI_VERIFY_CACHE_DIR` albo domyślny katalog systemowy), z tą samą kontrolą
  bezpieczeństwa cache co przy innych zależnościach; wersja zgodna z lockfile.
- Bez `sass.js`: `compile` i `run` kończą się kodem 3 z podpowiedzią `node scripts/install.mjs`
  (chyba że kontrola jest FAIL, zob. kody wyjścia). `check`, `diff` i `selftest` działają bez niego.
- Sieć: poza instalacją (`install.mjs`) css-scope nie wykonuje żadnych połączeń.

## Granice

- LibSass 3.6 przez `sass.js` 0.11.1 nie jest starym buildem gulp-sass 4, którego używa Generator. Wynik kompilacji
  może różnić się od kompilacji w Generatorze; dowodem zgodności nie jest sam wynik css-scope.
- `!important` jest liczony, nie oceniany (poza limitem `important.max`).
- Pusta reguła (`.root-theme {}`) przy `onlyPropertyPrefix` przechodzi, bo nie ma deklaracji do sprawdzenia.
- Prefiks `prefix` dopasowuje się jako napis: `.menu-host > .item` przepuszcza też `.menu-host > .item-x`.
  Zapisz wyjątek tak wąsko, jak to możliwe.
- Korzeń jest pełną klasą: `.root-x`, `.rooté` i `.root\,evil` nie są korzeniem; `.root,evil` nie przechodzi
  walidacji nazwy klasy (`root`, `--root`, `value` aliasu).
- Brak przepisywania CSS; kontrola nie naprawia nakładki.
- Pliki CSS z `check` i `diff` nie podlegają polityce ścieżek (patrz wyżej).
- `--out` działa tylko w `compile` i `run`; w pozostałych poleceniach jest błędem kodu 2.
