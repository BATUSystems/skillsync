# Kontrola motywu prototypów: theme_check.py

Kontrola motywu to jedno źródło sprawdzania motywu prototypów w skillu `prototype`. Zamiast trzech kopii o różnej poprawności (strażnik skilla, kopia w projekcie, skrypt projektowy) jest jeden skrypt `scripts/theme_check.py`, który wykonuje to, co opisuje konfiguracja JSON. Parser CSS jest wspólny (`scripts/css_tokens.py`), kontrast liczy `scripts/theme_colors.py`, reguły stylu są w `scripts/theme_proto.py`. Skrypt używa tylko biblioteki standardowej (Python 3.11+) i niczego nie zapisuje.

Kontrola sprawdza motyw projektu, nie skill. Strażnik skilla to `scripts/check_tokens.py` (zob. [style.md](style.md)).

## Do czego służy

- pokrycie listy wymaganych tokenów w jasnym bloku motywu;
- zgodność bloków ciemnych z wybranym trybem (`separate`, `mirror`, `none`);
- zakaz konstrukcji w plikach tokenów (`color-scheme`, `prefers-color-scheme`);
- zgodność kopii plików z ich źródłem (źródłem może być plik projektu albo skilla);
- kontrast par kolorów (WCAG 2.x) liczony z jasnego bloku;
- zakaz literałów w stylu prototypu (`project.css`, `index.html`): kolory, fonty, promienie, wagi, interlinie, rozmiary i `z-index`.

Każda kontrola włącza się własnym kluczem konfiguracji. Brak klucza oznacza, że kontroli nie ma; nigdy nie jest pomijana po cichu. Brak danych wejściowych daje kod 2 albo UNVERIFIED, nigdy PASS.

## Wywołanie

    python3 <skill>/scripts/theme_check.py --config docs/design/theme-check.json --project .
    python3 <skill>/scripts/theme_check.py --config docs/design/theme-check.json --project . --only tokens,dark
    python3 <skill>/scripts/theme_check.py --config docs/design/theme-check.json --project . --json
    python3 <skill>/scripts/theme_check.py --config - --project . < theme-check.json
    python3 <skill>/scripts/theme_check.py --selftest

- `--config <plik.json>` to konfiguracja. `--config -` czyta ją ze standardowego wejścia i wymaga `--project`.
- `--project <katalog>` to korzeń ścieżek konfiguracji. Domyślnie jest nim katalog, w którym leży plik konfiguracji, a nie katalog bieżący; podawaj go jawnie.
- `--skill-root <katalog>` to katalog skilli dla źródeł `skill:`. Bez niego skrypt szuka w projekcie (zob. „Ścieżki i bezpieczeństwo”).
- `--only <kontrola>[,…]` uruchamia wybrane kontrole (`tokens`, `dark`, `forbid`, `copies`, `copyDirs`, `contrast`, `prototype`). Nazwa musi być w konfiguracji, inaczej kod 2. Pominięte kontrole są zgłaszane jako INFO.
- `--json` zamiast linii tekstowych daje wynik jako JSON (zob. „Wyjście i kody”).
- `--selftest` uruchamia fikstury syntetyczne (część w pamięci, część w katalogu tymczasowym, który sprząta): każda kontrola musi umieć paść. Projektu nie czyta.

Wejście programowe, dla opakowań, które budują konfigurację w pamięci:

    import sys
    sys.path.insert(0, "<skill>/scripts")
    import theme_check
    res = theme_check.run(config, project, skill_root=None, only=None)
    res.code, res.status, res.findings, res.error

`config` to słownik, `project` to ścieżka (napis albo `Path`). `run` nie rzuca wyjątków przy błędach konfiguracji ani przy nieoczekiwanym błędzie: zwraca `error` i kod 2.

## Konfiguracja

Plik JSON z `"version": 1`. Nieznany klucz na dowolnym poziomie to kod 2 z komunikatem, który nie cytuje treści pliku. Ścieżki w konfiguracji są względne do `--project`.

    {
      "version": 1,
      "tokens": {"files": ["docs/design/tokens.css"], "required": "skill:prototype/assets/theme/required-tokens.txt", "assets": null},
      "dark": "separate",
      "forbid": ["color-scheme", "prefers-color-scheme"],
      "copies": [{"source": "skill:<skill>/assets/theme/default.css", "copy": "docs/design/theme.css", "mode": "auto"}],
      "copyDirs": [{"source": "skill:<skill>/assets/fonts", "copy": "docs/prototypes/fonts", "include": ["*.woff2", "LICENSE"]}],
      "contrast": {
        "pairs": [{"name": "tekst na tle", "fg": "--foreground", "bg": "--background", "min": 4.5, "deviation": "D12"}],
        "pairsFrom": {"file": "docs/design/palette.json", "path": "contrastPairs"},
        "deviations": {"file": "docs/domain/deviations.md", "pattern": "^###\\s+(D\\d+)\\b"}
      },
      "prototype": {
        "dirs": ["docs/prototypes/*"], "dirMarker": "index.html", "files": ["project.css", "index.html"],
        "knownTokensFrom": ["tokens.css", "theme.css"], "onMissingFile": "fail",
        "rules": {"color": true, "font": true, "radius": {"allow": ["0", "50%", "999px", "inherit"]}, "weight": true, "lineHeight": true, "size": false, "zIndex": "tokens"}
      }
    }

Przykład pokazuje wszystkie klucze. Prawdziwa konfiguracja ma tylko potrzebne kontrole. Nazwy `docs/design/...`, `docs/prototypes/*` i `<skill>` są przykładowe.

| Klucz | Wartość | Domyślnie | Uwagi |
|---|---|---|---|
| `version` | `1` | wymagany | inna wartość to kod 2 |
| `tokens.files` | lista ścieżek projektu | wymagany w `tokens` | brak pliku to FAIL `brak pliku tokenów` |
| `tokens.required` | ścieżka listy (`skill:…` albo ścieżka projektu) albo `null` | wymagany w `tokens` | brak listy to kod 2; `null` wyłącza pokrycie i zawsze daje WARN |
| `tokens.assets` | katalog zasobów szablonu albo `null` | `null` | wymaga `required` innego niż `null` |
| `dark` | `separate`, `mirror` albo `none` | brak; wartość jest wymagana, gdy klucz istnieje | wymaga `tokens` |
| `forbid` | niepusta lista: `color-scheme`, `prefers-color-scheme` | brak | wymaga `tokens` |
| `copies` | niepusta lista obiektów `source`, `copy`, `mode` | brak | `mode`: `auto`, `text` albo `bytes` |
| `copyDirs` | niepusta lista obiektów `source`, `copy`, `include` | brak | `include` jest wymagany, niepusta lista wzorców |
| `contrast.pairs` | lista par | `[]` | para: `fg` i `bg` wymagane; `name`, `min`, `deviation` opcjonalne |
| `contrast.pairs[].name` | napis | `<fg> na <bg>` | nazwa w komunikatach |
| `contrast.pairs[].min` | liczba dodatnia | `4.5` | próg kontrastu |
| `contrast.pairsFrom` | `{file, path}` | `null` | plik JSON; `path` z kropkami, indeks liczbowy dla list; pod ścieżką lista par o tych samych kluczach |
| `contrast.deviations` | `{file, pattern}` | `null` | `pattern` to regex do 200 znaków; grupa 1, a bez grupy cały dopasowany tekst, to identyfikator |
| `prototype.dirs` | niepusta lista wzorców katalogów | wymagany | `fnmatch` na każdym segmencie ścieżki, bez `**` |
| `prototype.dirMarker` | ścieżka względna w katalogu | `index.html` | katalog bez znacznika nie jest prototypem |
| `prototype.files` | niepusta lista ścieżek względnych | `project.css`, `index.html` | pliki stylu do sprawdzenia |
| `prototype.knownTokensFrom` | lista ścieżek względnych w katalogu prototypu | `[]` | tokeny z nich i z `tokens.files` tworzą zbiór znanych tokenów |
| `prototype.onMissingFile` | `fail` albo `skip` | `fail` | `skip` zgłasza INFO zamiast FAIL |
| `prototype.rules` | obiekt reguł | `{}` (wszystkie wyłączone) | zob. kontrola `prototype` |

## Ścieżki i bezpieczeństwo

- Ścieżka projektu jest względna do `--project`: bez `..`, bez litery dysku i bez separatora na początku (`/` albo `\`). Po `realpath` musi leżeć pod `realpath(--project)`; dowiązanie wychodzące poza projekt to kod 2. Tak samo sprawdzane są wszystkie czytane pliki (tokeny, pary, odstępstwa, kopie, pliki prototypów). Plik większy niż 2 MiB albo niebędący UTF-8 to kod 2.
- Źródło `skill:<nazwa>/<ścieżka>`: `<nazwa>` to jedna nazwa skilla pasująca do `[a-z0-9][a-z0-9-]*` (bez `/` i `..`), a ścieżka jest względna i bez `..`.
- `--skill-root` jest jawny: skill to `<skill-root>/<nazwa>`. Bez niego skrypt szuka w projekcie w kolejności `.claude/skills`, `.agents/skills`, `.github/skills` i wybiera pierwszy katalog zawierający dany skill (osobno dla każdego skilla). Skrypt nie wylicza katalogu skilli przez `resolve()` własnego pliku ani z katalogów użytkownika. Brak skilla to kod 2.
- Dowiązanie skilla do biblioteki jest stanem normalnym. Źródła `skill:` nie są sprawdzane przez `realpath` w projekcie.
- Ścieżki bezwzględne (`/x`, `\x`, `C:x`) odrzuca własna kontrola skryptu; strażnik `isabs` generatora skanuje tylko generator.
- Wzorzec odstępstw ma najwyżej 200 znaków. Błędny regex to kod 2 bez cytowania wzorca. Linie pliku odstępstw dłuższe niż 500 znaków są pomijane z WARN.
- Zagnieżdżenie CSS głębsze niż 200 poziomów to kod 2.

## Kontrole

### tokens

Pokrycie listy wymaganych tokenów w jasnym bloku (`:root`, `@theme`, `@theme inline`). Token zdefiniowany tylko w bloku ciemnym to FAIL `token … zdefiniowany tylko w bloku ciemnym` z numerem linii. Token nigdzie nie zdefiniowany to FAIL `brak definicji wymaganego tokenu`. Brak jasnego bloku w ogóle to FAIL. Pusta lista to UNVERIFIED.

Gdy podano `assets`, zbiór tokenów używanych przez zasoby szablonu (`var(--x)`) musi być równy liście. Brakujący i nadmiarowy token to FAIL w obu kierunkach.

### dark

Porównanie bloków ciemnych według trybu (definicje bloków: zob. „Definicje bloków”):

- `separate`: bloki ciemne mają te same tokeny i te same wartości (po normalizacji białych znaków). Token obecny w jednym bloku i brakujący w drugim to FAIL, różne wartości to FAIL. Jeden blok ciemny daje PASS z adnotacją, bo nie ma z czym go porównać. Brak bloków ciemnych daje UNVERIFIED. Tryb nie wymaga obu rodzin bloków (`.dark`/`[data-theme]` i `@media`); porównuje te, które są.
- `mirror`: blok ciemny ma być identyczny z jasnym: te same tokeny z tymi samymi wartościami. Brak bloku ciemnego to FAIL `brak bloku ciemnego`. Tryb odpowiada projektowi z wyłączonym ciemnym motywem (uwaga: klucz manifestu `prototype.dark_theme: false` i lustrzany blok ciemny w pliku tokenów to dwa różne znaczenia „ciemny wyłączony”; plik tokenów BEZ bloku ciemnego w takim projekcie wymaga trybu `none`, a nie `mirror`) (`prototype.dark_theme: false` w manifeście).
- `none`: blok ciemny nie może istnieć, więc każdy taki blok to FAIL. Deklaracje spoza tokenów w bloku ciemnym to WARN.

Selektor ze słowem `dark`, którego silnik nie rozpoznaje (np. `html[data-theme=dark]` albo `body.dark`), daje WARN.

### forbid

Szuka zakazanych konstrukcji w plikach z `tokens.files`, z pominięciem komentarzy: `color-scheme:` oraz każde wystąpienie `prefers-color-scheme`, także w `@media (prefers-color-scheme: …)`. Każde wystąpienie to FAIL z numerem linii.

### copies i copyDirs

`copies`: kopia (`copy`) ma być równa źródłu. Brak źródła to kod 2, brak kopii to FAIL `brak kopii`, różna zawartość to FAIL. Pliki tekstowe (`.css`, `.html`, `.htm`, `.js`, `.mjs`, `.json`, `.md`, `.txt`, `.svg`, `.xml`, `.yaml`, `.yml`, `.csv`) porównuje się po normalizacji: bez BOM, końce linii do `\n`, jeden końcowy `\n`. `mode: "bytes"` porównuje bajty, `mode: "text"` wymusza tekst, `auto` wybiera po rozszerzeniu. Pozostałe pliki (np. fonty) są porównywane bajt w bajt.

`copyDirs`: porównuje pliki katalogu źródłowego wybrane przez `include`. To jawna lista wzorców `fnmatchcase`: wzorzec bez `/` dopasowuje nazwę pliku, wzorzec z `/` dopasowuje ścieżkę względną. Wzorce wybierają pliki, więc podawaj je jawnie (np. `*.woff2`, `LICENSE`), a nie wszystko naraz. Gdy żaden plik nie pasuje, wynik to UNVERIFIED. Brak katalogu źródłowego to kod 2, brak pliku w kopii to FAIL.

### contrast

Pary kolorów z jasnego bloku. Kolor pary to nazwa tokenu (`--x`), alias `var(--x)` (do 5 poziomów) albo literał koloru.

- Obsługiwane formaty: `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`, `rgb()`, `rgba()`, `hsl()`, `hsla()`, `white`, `black`, `transparent` oraz `oklch()`, `oklab()`, `lab()`, `lch()`. Przestrzenie OKLab, Lab i LCH są przeliczane do sRGB wg CSS Color 4. Wynik poza gamutem sRGB jest przycinany, a finding dostaje `outOfGamut: true` (w tekście: „kolor poza gamutem sRGB przycięty”).
- Alfa: kolor `fg` z alfą jest składany nad `bg`, a `bg` z alfą nad białym.
- Inne formaty (`color()`, `color-mix()`, `hwb()`, przestrzenie szerokiego gamutu, nazwane kolory, `calc()`) to UNVERIFIED z polem `reason: color-format`. Cykl aliasów albo łańcuch dłuższy niż 5 poziomów to UNVERIFIED z `reason: alias`. Nigdy wyjątek, nigdy PASS.
- Próg WCAG to `min` (domyślnie 4,5), werdykt z niezaokrąglonego stosunku, próg luminancji 0,04045.
- Token pary, którego nie ma w jasnym bloku, to FAIL z `reason: missing-token`. Pary z tokenami wymagają `tokens` w konfiguracji (kod 2 bez niego).
- Para z `deviation`: gdy identyfikator jest w pliku odstępstw, poniżej minimum daje WARN; gdy go tam nie ma, FAIL. Para bez `deviation` poniżej minimum to FAIL. Para z `deviation` bez `deviations` w konfiguracji to kod 2.
- Pary z `pairsFrom` są czytane z pliku JSON pod wskazaną ścieżką; brak pliku to kod 2.

**Uwaga:** kontrast liczony jest tylko z JASNEGO bloku. Para, która przechodzi w jasnym motywie, a w ciemnym nie, daje PASS. Kontrast motywu ciemnego wymaga osobnego sprawdzenia poza tym skryptem.

### prototype

Reguły stylu dla plików z `prototype.files` w każdym katalogu prototypu. Katalog prototypu pasuje do `dirs` i ma `dirMarker`. Zero takich katalogów daje UNVERIFIED. Sprawdzane są arkusze CSS oraz bloki `<style>` i atrybuty `style` w HTML; `url()` i napisy są maskowane.

Znane tokeny to `knownTokensFrom` z katalogu prototypu plus tokeny z `tokens.files`. `var(--x)` spoza tego zbioru to FAIL `nieznany token`. Pusty zbiór znanych tokenów to FAIL. Fallback `var(--x, wartość)` jest skanowany jak zwykła wartość.

Reguły z `rules` (klucz obecny = reguła włączona):

- `color` (`true`): zakaz literałów koloru: funkcje kolorów (`rgb`, `hsl`, `hwb`, `lab`, `lch`, `oklab`, `oklch`, `color`, `color-mix`, `light-dark` i in.), hex i nazwy kolorów CSS oraz systemowe. `transparent` i `currentcolor` są dozwolone.
- `font` (`true`): `font-family` tylko przez `var()` albo `inherit`. Lista `var(--a), serif` to FAIL, bo literał stoi poza `var()`.
- `radius` (`true` albo `{"allow": […]}`): w `border*radius` (także z prefiksem vendor) poza `allow` dozwolone jest tylko `var()`. `true` oznacza brak dozwolonych wartości.
- `weight` (`true`): `font-weight` tylko przez `var()`; słowa `normal`, `bold` i liczby to FAIL.
- `lineHeight` (`true`): `line-height` tylko przez `var()`.
- `size` (`true`): w `font` i `font-size` zakaz literałów rozmiaru: `px`, `rem`, `em`, `pt`, `pc`, `cm`, `mm`, `in`, `vw`, `vh`, `vmin`, `vmax`, `ch`, `ex`, `%`. Nie dotyczy `font-weight`.
- `zIndex`: `tokens` (tylko `var(--z-index-*)`), `forbid` (każdy `z-index` to FAIL), `allow` (nie sprawdzane; domyślnie).

`onMissingFile`: `fail` (domyślnie) traktuje brak pliku z `files` jako FAIL; `skip` zgłasza INFO `pominięto brak pliku`.

## Definicje bloków

Zgodnie z silnikiem prototypów (`extract_tokens` w generatorze; zgodność pilnuje `tests/test_skillsync_css_tokens.py`):

- Jasny blok: selektor dokładnie `:root` (także na liście po przecinku), `@theme` albo `@theme inline`. Może leżeć w `@layer` na dowolnej głębokości, ale nie w `@media`, `@supports` ani w innym bloku.
- Ciemny blok: selektor dokładnie `.dark`, `[data-theme="dark"]` albo `:root[data-theme="dark"]` (cudzysłowy normalizowane). Selektor ze słowem `dark` w innym znaczeniu (`.card-dark`, `.dark .card`) nie jest ciemnym blokiem.
- `@media (prefers-color-scheme: dark)` jest rozpoznawany osobno. Uczestniczy w trybie `dark` i w `forbid`. Silnik pomija go z ostrzeżeniem.
- Wartość tokenu to ostatnia definicja w kolejności źródła (w pliku większy numer linii, między plikami późniejszy plik).

## Wyjście i kody

Każdy wynik to linia `STATUS kontrola: opis [plik:linia]`, np.:

    FAIL forbid: zakazana konstrukcja color-scheme [docs/design/tokens.css:3]

Ostatnia linia: `theme_check: <STATUS> (kod N): X PASS, Y FAIL, Z WARN, W UNVERIFIED`. `STATUS` to `PASS`, `FAIL`, `UNVERIFIED` albo `ERROR` (kod 2).

`--json` drukuje obiekt `{"status", "code", "error", "findings": [...]}`. Każdy finding ma `status`, `check`, `message`, `file`, `line` i pola dodatkowe: `reason` (contrast), `ratio`, `min`, `outOfGamut`. Błąd wywołania to na stderr `theme_check: BŁĄD: <komunikat>`, a w trybie JSON także obiekt z `status: "ERROR"` na stdout.

Kody wyjścia:

- `0`: PASS (także PASS z WARN i INFO).
- `1`: co najmniej jedno FAIL.
- `2`: błąd wywołania, konfiguracji albo brak źródła (lista, skill, katalog kopii, plik par, plik odstępstw); ścieżka wychodząca z projektu; błąd wewnętrzny.
- `3`: UNVERIFIED bez FAIL, czyli nic nie zmierzono: pusta konfiguracja, zero plików, zero katalogów prototypu, brak bloków ciemnych w `separate`, brak pasujących plików do `include`, brak par.

Pierwszeństwo: 2, potem 1, potem 3. `--selftest` zwraca 0, gdy każda fikstura daje oczekiwany wynik (`theme_check: selftest OK (N przypadków)`), i 1 z listą rozbieżności na stderr, gdy któraś nie daje.

## Siatka bezpieczeństwa

- Błąd konfiguracji (`ConfigError`) i każdy nieoczekiwany wyjątek kończą się kodem 2. Nieoczekiwany wyjątek daje komunikat `błąd wewnętrzny (<typ>)` bez treści plików.
- Komunikaty błędów nie cytują treści konfiguracji, plików ani wzorca odstępstw.
- Skrypt niczego nie zapisuje w projekcie (selftest używa katalogu tymczasowego).
- Plik konfiguracji może mieć BOM.

## Uwagi

- `separate` nie wymaga obu rodzin bloków ciemnych: porównuje te, które są. Zero bloków to UNVERIFIED, jeden blok to PASS z adnotacją.
- Selektor z `dark`, którego silnik nie rozpoznaje, daje WARN (nie PASS i nie FAIL).
- `fnmatchcase` (w `include` i w `dirs`) rozróżnia wielkość liter, także na Windows. `SOURCE.txt` albo `LICENSE` trzeba wymienić w `include` wprost.
- Normalizacja tekstu kończy plik dokładnie jednym `\n`.
- `tokens.required: null` wyłącza pokrycie i zawsze daje WARN. `--only tokens` przy `null` to kod 2.
- Para z `deviation` wymaga `contrast.deviations`; bez niego kod 2.
- Klucz `dark` wymaga wartości (brak wartości domyślnej).

## Migracja z własnych skryptów kontroli motywu

Przepis dla projektu, który ma własny skrypt kontroli motywu.

1. Spisz kontrole starego skryptu i przypisz każdą do klucza z tabeli w „Konfiguracja”. Kontrole ogólne (pokrycie listy, bloki ciemne, zakaz konstrukcji, kopie, kontrast par, styl prototypu) przechodzą do konfiguracji.
2. Zostaje w projekcie to, co zależy od projektu: wartości z narzędzi projektowych, generator pliku tokenów i jego równość, treść pliku odstępstw, reguły arkusza wymagające wiedzy o projekcie, osadzanie fontów. Ta część to osobny skrypt projektowy uruchamiany obok `theme_check.py`; oba muszą dać kod 0.
3. Wybierz tryb `dark`: `separate`, gdy oba bloki mają mieć te same wartości; `mirror`, gdy projekt wyłącza ciemny motyw; `none`, gdy blok ciemny nie może istnieć.
4. Ustaw listę wymaganych tokenów (`required`) i sprawdź, że stary skrypt widzi tę samą listę. Brak listy to kod 2, a nie cicha pominięcie.
5. Uruchom nową kontrolę obok starej na aktualnym motywie i porównaj wyniki. Przełącz się, gdy wyniki są zgodne albo różnice są opisane.
6. Usuń stary skrypt w osobnym kroku.

Różnice zaostrzeń względem typowych skryptów projektowych (nowa kontrola może wykryć więcej; to celowe):

- `font-family` z `var()` plus literał (np. `var(--font), serif`) to FAIL; sama lista `var()` jest dozwolona.
- `size` obejmuje `%`, `vw`, `vh`, `ch` oraz pozostałe jednostki z listy (`vmin`, `vmax`, `ex`); nie dotyczy `font-weight`, bo to osobna reguła `weight`.
- `zIndex` w trybie `tokens` wymaga wyłącznie `var(--z-index-*)`; `forbid` zakazuje wszystkiego; `allow` nie sprawdza.
- `radius: true` oznacza brak dozwolonych wartości: każdy literał promienia to FAIL.
- Reguły promienia obejmują prefiksy vendor (`-webkit-border-radius`, `-moz-border-radius`).
- `var(--x)` spoza zbioru znanych tokenów to FAIL, także w fallbacku `var(--x, wartość)`.
- Blok ciemny z deklaracją spoza tokenów w trybie `none` to WARN.
- Katalog pasujący do `dirs` bez `dirMarker` nie jest prototypem. Prototyp bez pliku z `files` to FAIL (albo INFO przy `skip`).
- Kontrast: formaty spoza listy i pary bez wyniku to UNVERIFIED, nie pominięcie.
- Dowiązanie wychodzące poza projekt to kod 2, nie cichy odczyt.
