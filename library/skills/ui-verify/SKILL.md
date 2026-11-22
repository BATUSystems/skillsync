---
name: ui-verify
description: Pomiar elementów UI względem wzorca (pudełko, kolor, font, kształt), porównanie pikseli i regresja zrzutów przez Playwright i systemowy Chrome; wynik PASS, FAIL, UNVERIFIED albo NOT_RUN z kodami 0, 1, 2, 3.
---

# ui-verify — pomiar i porównanie wyglądu UI

Skill dostarcza polecenia Node (ESM) do weryfikacji wyglądu: `measure` (pomiar elementu względem wzorca),
`pixel-diff` (porównanie dwóch zrzutów PNG), `capture` (macierz zrzutów strony), `diff` (porównanie dwóch
katalogów zrzutów) oraz `install` (zależności do cache użytkownika). Każde polecenie ma `--selftest`,
który działa bez sieci i bez przeglądarki.

Zasada: wynik bez dowodu to UNVERIFIED (kod 3), nigdy PASS. Zrzut bez asercji pozytywnej nie dowodzi,
że strona się wyrenderowała.

## Kiedy używać

- Odtworzenie elementu według wzorca (strona referencyjna albo kandydat): pudełko, kolory, font, promień
  ramki i kształt renderowanego elementu.
- Porównanie zrzutu elementu z eksportem projektu graficznego (PNG): `pixel-diff`.
- Regresja wyglądu przed i po zmianie: `capture` przed, `capture` po, potem `diff`.
- Nie używaj do audytu dostępności, testów logiki ani oceny treści. Mierzony jest tylko element, nie
  położenie jego potomków. Granice: `references/boundaries.md`.
- Skill nie jest linkowany domyślnie: profile domyślne to `core` i `testing`. Trafia do projektu przez
  profil `ui` (`workflow.skill_profiles.selected`) albo przez wybór ręczny w manifeście.

## Polecenia

Ścieżki `scripts/` są względne do katalogu skilla w projekcie (np. `.claude/skills/ui-verify/scripts/`).

| Polecenie | Wejście | Wyjście |
|---|---|---|
| `node scripts/install.mjs [--check] [--lock-timeout <s>] [--json]` | `package.json` i lockfile skilla | zależności w cache użytkownika; `--check` tylko weryfikuje |
| `node scripts/measure.mjs --config <plik.json> <outDir>` | konfiguracja z parami `reference` i `candidate` | `measure.json`, `measure.txt`, `<id>.diff.png` przy FAIL kształtu |
| `node scripts/pixel-diff.mjs [--max-pct <n>] [--mask x,y,w,h]... [--crop] [--diff-out <plik.png>] [--no-tolerate-1px] [--json] <wzorzec.png> <kandydat.png>` | dwa pliki PNG | wiersz `PASS pixel-diff …` albo `FAIL pixel-diff …` |
| `node scripts/capture.mjs --config <plik.json> <outDir>` | konfiguracja stron, szerokości i trybów | zrzuty `<id>__<szerokość>-<tryb>.png`, `manifest.json`, `capture.txt` |
| `node scripts/diff.mjs [--max-pct <n>] [--json] <przed> <po>` | dwa katalogi z `capture` | wiersz na zrzut i wiersz `result` |
| `node scripts/css-scope.mjs [--json] [--config <plik.json>] check\|diff\|compile\|run\|selftest` | CSS albo SCSS i konfiguracja JSON | wiersze PASS, FAIL, INFO, WARN; przy `run` linia `RESULT:`; zapis CSS tylko z `--out` |

Flagi wspólne `measure` i `capture`: `--allow-origin <wzorzec>` (powtarzalna), `--allow-trace` (zapis trace
i storageState), `--json`. `--config` jest względny do bieżącego katalogu, `url` i `outDir` względem katalogu
konfiguracji. `--diff-out` zapisuje pod bieżącym katalogiem. Nieznana flaga, także `--help`, to błąd 2.

## Procedura agenta

1. Sprawdź zależności: `node scripts/install.mjs --check`. Kod 0 oznacza gotowość. Kod 3: uruchom
   `node scripts/install.mjs` (wymaga npm i dostępu do rejestru; proxy i `.npmrc` użytkownika działają
   przez npm). Nie instaluj do katalogu projektu.
2. Sprawdź przeglądarkę (sekcja „Przeglądarka”). Brak Chrome to kod 3 z listą sprawdzonych miejsc.
   Takiego przebiegu nie zgłaszaj jako wykonanego.
3. Przygotuj konfigurację JSON w repo projektu, bez sekretów, z neutralnymi nazwami. Schematy:
   `references/configuration.md`.
4. Pomiar elementu: `node scripts/measure.mjs --config <plik> <outDir>`. Czytaj `<outDir>/measure.txt`:
   `diff` to różnica pudełka lub stylu, `approved` to zatwierdzone odstępstwo, `shape` to porównanie
   pikseli elementu.
5. Porównanie zrzutów: pojedyncza para PNG daje `pixel-diff`. Zestaw stron i szerokości daje `capture`
   (przed i po zmianie) oraz `diff <przed> <po>`.
6. Zgłoś wynik kodem wyjścia i wierszem `result`, a nie opisem. Przy kodzie 3 podaj przyczynę: brak
   zależności, brak Chrome, brak asercji albo brak elementu.
7. Progi (`maxPct`, `--max-pct`), tolerancje i odstępstwa (`deviations`) zmienia człowiek. Odstępstwo
   wymaga `reason`; walidacja odrzuca je bez niego.

## Kontrola zakresu CSS (`css-scope`)

Używaj, gdy arkusz nakładki (overlay) ma tylko dodawać reguły pod jednym korzeniem klasy i nie może
zmieniać stylów bazowych. `check` sprawdza jeden plik CSS, `diff` porównuje z wersją bez nakładki,
`compile` i `run` kompilują SCSS przez LibSass (`sass.js` 0.11.1, instalowany przez `node scripts/install.mjs`).
`run` łączy selftest, kompilację, zakres, limit `!important` i diff z plikiem bazowym. `selftest` uruchamia
przypadki wbudowane i z konfiguracji. Brak `sass.js` daje kod 3 dla `compile` i `run`; pozostałe polecenia go nie wymagają.

Konfiguracja to plik JSON (dane, nie kod): `root`, wyjątki nazwane (`prefix`, `root-alias`, `exact-rule`,
każdy z `reason`), `important.max`, `diff.allowAtRules`. Flagi `--config`, `--root` (tylko `check`), `--out`,
`--scss-dir`, `--entry`, `--without` (tylko `compile` i `run`) i `--json`. Ścieżki z flag są względne do cwd,
ścieżki z konfiguracji do katalogu konfiguracji. Schemat, przypadki selftestu, format wyniku i granice:
`references/css-scope.md`.

Kody wyjścia jak w tabeli poniżej. Granice: LibSass 3.6 nie jest buildem Generator, `!important` jest liczony,
nie oceniany, a wyjątek `exact-rule` nie dopuszcza nowych reguł w `diff`.

## Kody wyjścia

| Kod | Znaczenie |
|---|---|
| 0 | PASS: wszystko wykazane (dla `diff`: każdy zrzut SAME) |
| 1 | FAIL: weryfikacja wykryła różnicę. Tylko to |
| 2 | Błąd użycia, konfiguracji, adaptera, niebezpieczna ścieżka lub cache, błąd wewnętrzny, padnięcie Chrome, timeout nawigacji |
| 3 | NOT_RUN: brak zależności, Chrome, npm, sieci lub blokady instalacji; albo UNVERIFIED (brak asercji pozytywnej, brak elementu, brak manifestu, kształt pominięty, zły stan strony) |

UNVERIFIED zawsze daje 3, nigdy 0. Łączenie werdyktów i formaty wierszy: `references/verdicts.md`.

## Konfiguracja measure (przykład)

```json
{
  "version": 1,
  "viewport": { "width": 400, "height": 200, "deviceScaleFactor": 1 },
  "secretEnv": [],
  "pairs": [
    {
      "id": "example-button",
      "reference": { "url": "reference/index.html", "selector": "#target" },
      "candidate": { "url": "http://localhost:3000/", "selector": "#target" },
      "deviations": [{ "prop": "fontSize", "reason": "zatwierdzone w przeglądzie projektu" }]
    }
  ]
}
```

`url` bez schematu to ścieżka względna do katalogu konfiguracji (plik `file://` pod tym katalogiem).

## Konfiguracja capture (przykład)

```json
{
  "version": 1,
  "widths": [375, 1280],
  "modes": ["light", "dark"],
  "secretEnv": ["EXAMPLE_LOGIN_PASSWORD"],
  "login": {
    "url": "http://localhost:3000/login",
    "steps": [
      { "fill": { "selector": "#user", "valueEnv": "EXAMPLE_LOGIN_USER" } },
      { "fill": { "selector": "#password", "valueEnv": "EXAMPLE_LOGIN_PASSWORD" } },
      { "click": "button[type=submit]" }
    ]
  },
  "pages": [
    {
      "id": "home",
      "url": "http://localhost:3000/",
      "assert": { "selector": "h1", "text": "Przykładowy tytuł" },
      "masks": [{ "selector": "[data-volatile]" }]
    }
  ]
}
```

Strona bez `assert` nigdy nie daje PASS (patrz „Asercja pozytywna”).

## Nazwy zrzutów i pliki wyjściowe

- Zrzut: `<id>__<szerokość>-<tryb>.png`, np. `home__375-light.png`. Separatorem są dwa podkreślenia.
  `<id>` nie powinien zawierać `__`; walidacja tego nie zabrania, więc sprawdź to ręcznie.
  `<tryb>` to `light` albo `dark`.
- `manifest.json` (name, pageId, width, mode, assertion PASS|FAIL|NONE; bez bajtów PNG) i `capture.txt`.
- `measure`: `measure.json`, `measure.txt` oraz `<id>.diff.png`, tylko gdy kształt daje FAIL.
- Katalog wyjściowy (`outDir`) trzymaj poza kontrolą wersji (wpis w `.gitignore`): `trace.zip` i
  `storageState.json` mogą zawierać dane logowania.

## Progi i porównanie

- `pixel-diff`: próg 0,5 % (`--max-pct`). PASS, gdy procent różnych pikseli jest ≤ progu. Procent to
  różne piksele podzielone przez pole zrzutu.
- Piksel jest różny według pixelmatch (threshold 0,1, bez antyaliasingu). Odporność na przesunięcie jest
  domyślna (`--no-tolerate-1px` ją wyłącza): piksel nie jest różny, gdy w otoczeniu (2r+1)² w obu obrazach
  jest ten sam kolor; promień `r` to `shiftRadius` (domyślnie 1, w `measure` klucz `shiftPx`, patrz niżej).
  Symetria: detal tylko we wzorcu albo tylko u kandydata daje różnicę. Przesunięcie powyżej `r` pikseli
  nadal daje FAIL. Pojedynczy piksel krawędzi poniżej progu nie daje FAIL.
- Różne wymiary: FAIL `size-mismatch` (bez normalizacji). `--crop` przycina do wspólnego minimum,
  zakotwiczonego w lewym górnym rogu, i zgłasza `cropped`.
- `diff`: domyślnie `--max-pct 0`. SAME znaczy: po odporności na 1 px nie zostaje ani jeden różny piksel.
  SAME wymaga asercji PASS po obu stronach i równych wymiarów.
- `measure`: `tolerancePx` 1 (x, y, width, height i liczbowe style), `colorTolerance` 0 (kolory według
  tolerancji), `sizeTolerancePx` 1 (wymiary dla kształtu), `maxPct` 0,5, `shiftPx` (promień odporności
  na przesunięcie w pikselach ekranu, domyślnie `ceil(deviceScaleFactor)`: przy DPR 2 przesunięcie o 1 px
  CSS to 2 px ekranu), `shapeAlign` (`center` domyślnie albo `start`).
- Kształt w `measure`: piksele elementu są porównywane, gdy wymiary mieszczą się w `sizeTolerancePx`.
  Wynik łączony to FAIL, gdy pomiar LUB kształt daje FAIL. Różnice wymiarów w tolerancji dają
  przycięcie i `cropped: true`; przy `shapeAlign: center` kształt ocenia zrzut wyrównany do środka
  (nadmiar ucinany po połowie z każdej strony, nieparzysty więcej z prawej i dołu).
- Kształt pominięty (`SKIPPED`, `box-mismatch`) to brak dowodu: para jest UNVERIFIED (kod 3), także gdy
  pomiar pudełka przeszedł dzięki zatwierdzonemu odstępstwu. Odstępstwo kształtu wpisuje człowiek jako
  dane: `acceptShape: { "reason": "..." }` w parze zmienia kształt FAIL albo SKIPPED na `ACCEPTED`
  (liczony jak PASS, powód w `measure.txt` i `measure.json`). Bez `reason` walidacja odrzuca wpis.
- Maski kształtu: `masks` (wspólne), `masksReference` i `masksCandidate` (prostokąty). Wszystkie trzy listy
  są sumowane i zamalowywane na obu zrzutach, więc wynik jest symetryczny.

## Asercja pozytywna (capture)

- Strona z `assert` daje PASS, gdy element istnieje (a przy `text` zawiera tekst). Element jest czekany do
  `assertTimeoutMs` (klucz strony albo konfiguracji, całkowita 0–60000, domyślnie 5000; strona wygrywa;
  0 to sprawdzenie natychmiast, bez czekania). Po limicie FAIL (kod 1).
- Strona bez `assert` daje NONE, czyli UNVERIFIED (kod 3). Zrzut bez asercji nie jest dowodem.
- `diff`: SAME tylko przy PASS po obu stronach. W innym przypadku UNVERIFIED.
- Zły stan strony: gdy końcowy adres po nawigacji różni się od zadanego (przekierowanie; query i fragment
  pomijane), `measure` daje parę UNVERIFIED z powodem `page-state-mismatch`, a `capture` zrzut z asercją
  `NONE` i tym samym powodem (kod 3, nie PASS i nie FAIL).

## Sekrety

- Wartości logowania tylko ze zmiennych środowiska: `login.steps[].fill.valueEnv`. Pole `value` z literałem
  to błąd konfiguracji (wartość nie jest wypisywana). Brak zmiennej to błąd 2 przed uruchomieniem
  przeglądarki.
- Model zaufania konfiguracji: konfiguracja z obcego repozytorium nie może wyczytać dowolnych zmiennych.
  Dozwolone nazwy `valueEnv`: wymienione w `UI_VERIFY_SECRET_ENV` (lista po przecinku) albo zaczynające się
  od `UI_VERIFY_`; każda inna to błąd walidacji (kod 2) z nazwą zmiennej (bez wartości). Wcześniejsze nazwy
  spoza tej reguły trzeba przemianować (np. `APP_PASS` na `UI_VERIFY_APP_PASS`) albo dopisać do
  `UI_VERIFY_SECRET_ENV`.
- Komunikat nieobsłużonego błędu procesu (kod 2) też przechodzi przez maskowanie: sekrety z
  `UI_VERIFY_SECRET_ENV` oraz z `secretEnv` i `valueEnv` konfiguracji.
- Każdy zapis pliku, stdout, stderr i komunikat błędu przechodzi przez jedno maskowanie. Wartości z
  `secretEnv` (measure, capture), zmiennych logowania i `UI_VERIFY_SECRET_ENV` oraz ich warianty
  (URL-encode, JSON, base64, base64url) są zastępowane `***`. Wartości krótsze niż 4 znaki nie są maskowane.
- `trace.zip` i `storageState.json` powstają tylko z `--allow-trace` (ostrzeżenie na stderr). `measure`
  przyjmuje tę flagę, ale nie zapisuje trace.
- Granica: maska działa na tekście. PNG i `trace.zip` zapisywane są bajtowo, bez maskowania, a maska zna
  tylko wartości ze zmiennych środowiska, nie tokeny sesji. Nie wklejaj do czatu `storageState.json`,
  `trace.zip` ani zrzutów z danymi osobowymi.
- Sekret nigdy nie jest argumentem polecenia: błąd wypisałby go w argv.

## Polityka URL i ścieżek

- Dozwolone bez flag: `http://localhost`, `http://127.0.0.1`, `http://[::1]` (dowolny port) oraz pliki
  `file://` pod katalogiem konfiguracji (względna ścieżka w `url`).
- Inne hosty wymagają `--allow-origin <wzorzec>` (powtarzalna): `host`, `host:port`, `https://host`
  albo `*.domena`. Inne schematy są odrzucane.
- Origin jest sprawdzany przy walidacji konfiguracji, przed `goto` i dla każdej nawigacji w trakcie
  (`context.route`: nawigacje główne i ramki). Niedozwolona nawigacja jest przerywana, a operacja kończy się
  błędem 2 `navigation blocked: <origin>` (bez query i fragmentu, bo mogą nosić sekrety). Adres strony jest
  sprawdzany przed każdym wpisem i kliknięciem logowania oraz przed zrzutem: po przekierowaniu poza
  politykę sekret nie zostaje wpisany. Granica: polityka NIE obejmuje subzasobów (obrazy, skrypty, fetch).
- Symlinki: zapis pod `outDir` i strona `file://` w konfiguracji muszą leżeć w rzeczywistym katalogu
  (realpath najbliższego istniejącego przodka pod katalogiem konfiguracji lub bieżącym), a sam cel nie może
  być symlinkiem; w przeciwnym razie błąd 2 przed zapisem lub `goto`.
- `outDir` i ścieżki w konfiguracji są względne do katalogu konfiguracji. Wyjście poza ten katalog to
  błąd 2. Zapis `pixel-diff --diff-out` i katalogi `diff` są pod bieżącym katalogiem.
- Identyfikatory (`id`): `[A-Za-z0-9._-]`, 1–120 znaków, bez `.` i `..`.

## Przeglądarka

- Kolejność wykrywania: `UI_VERIFY_CHROME`, potem `CHROME_EXECUTABLE_PATH` (ścieżka do pliku), potem
  typowe miejsca. Linux: `google-chrome`, `google-chrome-stable`, `chromium`, `chromium-browser` w PATH.
  macOS: Google Chrome.app. Windows: Chrome lub Edge w Program Files i LOCALAPPDATA.
- Brak przeglądarki to kod 3 z listą sprawdzonych miejsc. Skill nie pobiera przeglądarki.
- Sandbox Chrome jest włączony. `UI_VERIFY_NO_SANDBOX=1` dodaje `--no-sandbox` (tylko kontenery CI).

## Zależności i łańcuch dostaw

- Przypięte: `playwright-core` 1.65.0, `pngjs` 7.0.0, `pixelmatch` 8.0.0, `sass.js` 0.11.1 (dla `css-scope`;
  `package.json` i lockfile z integralnością). Zmiana wersji to zmiana plików skilla, a więc zmiana hasha cache.
- Instalacja: `npm ci --ignore-scripts --omit=dev` z lockfile, uruchamiana bez powłoki przez `node` i
  `npm-cli.js`, z `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`.
- Cache: `<katalog>/<hash>/`. Hash (16 znaków) liczony z `package.json` i lockfile skilla. Znacznik
  `.ui-verify-ready` jest zapisywany ostatni. Instalacja idzie do katalogu tymczasowego i `rename`.
  Blokada pliku czeka do 120 s (`--lock-timeout`).
- Loader ładuje moduły wyłącznie z cache (dla `playwright-core` także z `PLAYWRIGHT_MODULE_PATH`), nigdy
  z `node_modules` projektu.
- POSIX: odmowa (kod 2), gdy katalog cache nie należy do użytkownika albo jest zapisywalny dla grupy lub
  innych. Cache w katalogu skilla też jest odrzucany (kod 2). Ta sama kontrola działa przy każdym
  ładowaniu kodu z cache (nie tylko przy instalacji), a względna ścieżka `UI_VERIFY_CACHE_DIR` jest
  odrzucana (wymagana bezwzględna); to błąd konfiguracji (kod 2), nie brak środowiska (kod 3).
- Wymagania: Node ≥ 20.11, testowane na 22. Na Windows nie używa się gołego `npm`/`npm.cmd`.

## Zmienne środowiskowe

| Zmienna | Skutek |
|---|---|
| `UI_VERIFY_CACHE_DIR` | katalog główny cache (ścieżka bezwzględna); pod nim leży katalog `<hash>` |
| `PLAYWRIGHT_MODULE_PATH` | ścieżka paczki `playwright-core` albo `node_modules`, który ją zawiera (pierwszeństwo dla playwright-core) |
| `UI_VERIFY_CHROME` | ścieżka pliku Chrome/Chromium (pierwsza w kolejności) |
| `CHROME_EXECUTABLE_PATH` | ścieżka Chrome, gdy `UI_VERIFY_CHROME` nie jest ustawione |
| `UI_VERIFY_NO_SANDBOX` | `1` wyłącza sandbox Chrome (kontenery CI) |
| `UI_VERIFY_SECRET_ENV` | nazwy zmiennych po przecinku, których wartości są maskowane; dopuszczone też jako `valueEnv` logowania |

Katalogi cache dla każdego systemu opisuje dokumentacja konfiguracji maszyny.

## Odbiór (fixtures)

- `fixtures/cases.json` zawiera trzy syntetyczne przypadki (pary HTML i PNG):
  `mirrored-icon` (pixel-diff FAIL, zakres 5–25 %), `shifted-flag` (FAIL, zakres 2–20 %) i
  `near-identical` (PASS, zakres 0,02–0,05 %). Selftest `pixel-diff` sprawdza je, gdy zależności są w cache.
- Ta dokumentacja nie podaje wyników przebiegów z przeglądarką. Dowód z Chrome to osobny krok.

## Pliki pomocnicze

- `references/configuration.md`: pełne schematy `measure` i `capture`.
- `references/verdicts.md`: werdykty, kody wyjścia, łączenie i formaty wierszy.
- `references/boundaries.md`: granice narzędzi i znane luki.
- `references/css-scope.md`: schemat konfiguracji `css-scope`, wyjątki, parser, format wyniku, ścieżki, granice.
