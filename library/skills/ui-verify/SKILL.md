---
name: ui-verify
description: Pomiar elementów UI względem wzorca (pudełko, kolor, font, kształt), porównanie pikseli i regresja zrzutów przez Playwright i systemowy Chrome; wynik PASS, FAIL, UNVERIFIED albo NOT_RUN z kodami 0, 1, 2, 3; audyt dostępności `a11y` (kontrast z pikseli, rozmiar celów, fokus, kolejność Tab, konsola) z wyjątkami i ręcznymi potwierdzeniami.
---

# ui-verify — pomiar i porównanie wyglądu UI

Skill dostarcza polecenia Node (ESM) do weryfikacji wyglądu: `measure` (pomiar elementu względem wzorca),
`pixel-diff` (porównanie dwóch zrzutów PNG), `capture` (macierz zrzutów strony), `diff` (porównanie dwóch
katalogów zrzutów), `a11y` (audyt dostępności strony z werdyktami) oraz `install` (zależności do cache
użytkownika). Każde polecenie ma `--selftest`, który działa bez sieci i bez przeglądarki.

Zasada: wynik bez dowodu to UNVERIFIED (kod 3), nigdy PASS. Zrzut bez asercji pozytywnej nie dowodzi,
że strona się wyrenderowała.

## Kiedy używać

- Odtworzenie elementu według wzorca (strona referencyjna albo kandydat): pudełko, kolory, font, promień
  ramki i kształt renderowanego elementu.
- Porównanie zrzutu elementu z eksportem projektu graficznego (PNG): `pixel-diff`.
- Regresja wyglądu przed i po zmianie: `capture` przed, `capture` po, potem `diff`.
- Nie używaj do testów logiki ani oceny treści. Mierzony jest tylko element, nie położenie jego potomków.
  Audyt dostępności (kontrast, rozmiar celów, fokus, kolejność Tab, konsola) to polecenie `a11y`, opisane
  w sekcji „Audyt dostępności (`a11y`)”. Granice: `references/boundaries.md`.
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
| `node scripts/a11y.mjs --config <plik.json> <outDir>` (flagi w sekcji „Audyt dostępności (`a11y`)”) | konfiguracja stron i kontroli, opcjonalnie plik wyjątków i `expected.json` | `a11y.json`, `a11y.md`; wynik na stdout (Markdown albo `--json`) |
| `node scripts/css-scope.mjs [--json] [--config <plik.json>] check\|diff\|compile\|run\|selftest` | CSS albo SCSS i konfiguracja JSON | wiersze PASS, FAIL, INFO, WARN; przy `run` linia `RESULT:`; zapis CSS tylko z `--out` |

Flagi wspólne `measure` i `capture`: `--allow-origin <wzorzec>` (powtarzalna), `--allow-trace` (zapis trace
i storageState), `--json`. `--config` jest względny do bieżącego katalogu, `url` i `outDir` względem katalogu
konfiguracji. `--diff-out` zapisuje pod bieżącym katalogiem. W `measure`, `pixel-diff`, `capture` i `diff` nieznana flaga, także `--help`, to błąd 2; `css-scope` ma własne zasady (`--help` daje kod 0, zob. niżej).

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
przypadki wbudowane i z konfiguracji. Brak `sass.js` daje kod 3 (NOT_RUN) dla `compile` i `run`, chyba że
któraś kontrola jest FAIL; wtedy kod 1. Pozostałe polecenia go nie wymagają. Po zmianie zestawu zależności
ponownie uruchom `node scripts/install.mjs` (zmienia się hash cache).

Konfiguracja to plik JSON (dane, nie kod): `root`, wyjątki nazwane (`prefix`, `root-alias`, `exact-rule`,
każdy z `reason`), `important.max`, `diff.allowAtRules`. Flagi `--config` (wymagany dla `diff`, `run`, `compile`),
`--root` (tylko `check`, bez `--config`), `--out` (tylko `compile` i `run`), `--scss-dir`, `--entry`, `--without`
(tylko `compile` i `run`; `--without` tylko `run`) i `--json`. Flaga bez znaczenia dla polecenia to błąd 2.
`--help` wypisuje użycie i daje kod 0. Ścieżki z flag są względne do cwd, ścieżki z konfiguracji do katalogu
konfiguracji. Symlinki w katalogu SCSS są odrzucane (FAIL). Schemat, przypadki selftestu, format wyniku i granice:
`references/css-scope.md`.

Kody wyjścia jak w tabeli poniżej (0 PASS, 1 FAIL, 2 błąd użycia, konfiguracji lub parsowania, 3 NOT_RUN).
Granice: LibSass 3.6 nie jest starym buildem gulp-sass 4 Generatora, `!important` jest liczony, nie oceniany.
Wyjątek `exact-rule` w `diff` jest oceniany tak jak w `check`.

## Kody wyjścia

| Kod | Znaczenie |
|---|---|
| 0 | PASS: wszystko wykazane (dla `diff`: każdy zrzut SAME) |
| 1 | FAIL: weryfikacja wykryła różnicę. Tylko to |
| 2 | Błąd użycia, konfiguracji, adaptera, niebezpieczna ścieżka lub cache, błąd wewnętrzny, padnięcie Chrome, timeout nawigacji |
| 3 | NOT_RUN: brak zależności, Chrome, npm, sieci lub blokady instalacji; albo UNVERIFIED (brak asercji pozytywnej, brak elementu, brak manifestu, kształt pominięty, zły stan strony) |

UNVERIFIED zawsze daje 3, nigdy 0. Łączenie werdyktów i formaty wierszy: `references/verdicts.md`.

## Audyt dostępności (`a11y`)

Polecenie: `node scripts/a11y.mjs [--selftest] [--json] [--allow-origin <wzorzec>]... [--exceptions <plik.json>]
[--expect <expected.json>] [--expect-page <id>] [--now <RRRR-MM-DD>] --config <plik.json> <outDir>`.

Flagi:

- `--config <plik.json>` (wymagana): konfiguracja wersji 1. Ścieżka względna do bieżącego katalogu; `outDir`
  względny do katalogu konfiguracji. Schemat: `references/a11y.md`.
- `--allow-origin <wzorzec>` (powtarzalna): polityka originów jak w `measure` i `capture`, ale `a11y` stosuje ją też do podzasobów i przekierowań (nie tylko do nawigacji) i sprawdza `file:` przez realpath. Zablokowany zasób (CDN, obraz tła) zmienia render, więc i pomiar kontrastu, a daje tylko ostrzeżenie `blocked-origin`: dodaj origin zasobów przez `--allow-origin`.
- `--exceptions <plik.json>`: plik wyjątków i potwierdzeń zamiast `exceptionsFile` z konfiguracji. Leży pod
  katalogiem konfiguracji.
- `--expect <expected.json>` i `--expect-page <id>`: tryb odbioru. Kod wyjścia wynika wtedy wyłącznie z porównania
  z oczekiwaniami (pierwsza strona, jeśli nie podano `--expect-page`).
- `--now <RRRR-MM-DD>`: data biegu do terminów wyjątków i potwierdzeń (domyślnie dzisiaj, UTC). Nadpisanie
  zapisuje ostrzeżenie `now-overridden`.
- `--json`: JSON na stdout zamiast Markdown. `--selftest`: testy czystej logiki, bez sieci i przeglądarki.

Pięć kontroli (szósty element, wyjątki i potwierdzenia z pliku, nie jest kontrolą) i ich reguły (szczegóły i limity: `references/a11y.md`):

1. `contrast`: kontrast tekstu z renderowanych pikseli. Tło jest próbkowane ze zrzutu z ukrytym tekstem, a nie
   składane z warstw. Wynik to najgorszy piksel, wobec progu 4,5 (3,0 dla dużego tekstu: 24 px, albo 18,67 px
   przy wadze 700 i więcej). Nieprzekształcalny kolor, nakładka, obcięcie, `filter`, `mix-blend-mode`,
   `opacity` grupy z własnym tłem czy `background-clip: text` to UNVERIFIED z przyczyną, nigdy zgadywany wynik.
2. `target-size`: cele dotykowe i klawiaturowe WCAG 2.5.8 (AA), minimum 24 x 24 CSS px. Wyjątki w tej
   kolejności: równoważny cel (etykieta 24 x 24), odstęp (okrąg 24 px nie trafia w inny cel), tekst w zdaniu
   (`inline`, EXEMPT), natywna kontrolka agenta użytkownika (EXEMPT).
3. `focus`: wskaźnik fokusu z klawiatury mierzony pikselami (przed i po naciśnięciu Tab, prostokąt +8 px).
   Mniej niż `focus.minDiffPx` (domyślnie 4) różnych pikseli to FAIL `focus-keyboard-indicator`. Sygnały stylu
   (outline, cień, kolor) są tylko dowodem. Fokus myszą jest opcjonalny (`focus.mouse`, lista selektorów).
   Opcjonalnie (domyślnie `off`) dwie reguły kontrastu wskaźnika z tych samych zrzutów: `focus-contrast`
   (WCAG 1.4.11, `focus.contrast`) i `focus-appearance` (WCAG 2.4.13, poziom AAA, `focus.appearance`); tryby
   `off`, `report` (INFO, bez wpływu na wynik) i `fail`. Szczegóły i granice: `references/a11y.md`.
4. `tab-order`: sekwencja Tab do końca albo do `tab.maxStops`. Reguły: `tab-positive-tabindex` (FAIL domyślnie,
   `tab.positiveTabindex: "warn"` obniża), `tab-unreachable` (widoczny, fokusowalny cel nieodwiedzony),
   `tab-trap` (pętla poza dialogiem modalnym), `tab-order-visual` (OSTRZEŻENIE; FAIL przy `order.mode: "fail"`).
5. `console`: `console.error`, `pageerror`, nieudane żądania i odpowiedzi 4xx/5xx dla dozwolonych originów to FAIL.
   `console.warning` idzie tylko do raportu. Żądanie spoza originów jest przerywane i liczone w `warnings`
   jako `blocked-origin`. Treść jest oczyszczana i maskowana.
6. Wyjątki i potwierdzenia z pliku projektu: nie są kontrolą, tylko zmieniają wynik pozostałych (poniżej).

Werdykty elementu: PASS, FAIL, EXEMPT (wyłączony przez WCAG albo niewyrenderowany, z powodem), EXCEPTED (FAIL
objęty wyjątkiem z `reason`), CONFIRMED (UNVERIFIED o przyczynie pomiarowej potwierdzony ręcznie), UNVERIFIED
(nie da się zmierzyć, z powodem), INFO (tylko raport, bez wpływu na wynik).

Zasada wyniku: EXCEPTED i CONFIRMED liczą się jak PASS, a UNVERIFIED nie daje PASS. Bieg z co najmniej jedną
zmierzoną kontrolą treści (kontrast, cele, fokus albo kolejność Tab dała PASS, FAIL, EXCEPTED lub CONFIRMED) i bez FAIL oraz bez
UNVERIFIED to PASS (kod 0). Sama konsola nie wystarcza. Bieg bez żadnej zmierzonej kontroli treści (pusta strona,
same EXEMPT) albo bez asercji pozytywnej to UNVERIFIED `no-samples` lub `no-assertion` (kod 3). Dobierz `assert` tak,
by wskazywał element istniejący tylko w docelowym widoku (nie `#root` powłoki, która jest przed renderem). Asercja niespełniona to FAIL
`assertion-failed`. Kody: 0 PASS, 1 FAIL, 2 błąd użycia, konfiguracji lub wewnętrzny, 3 UNVERIFIED albo brak
zależności, Chrome, asercji czy elementu z `--expect`.

Procedura agenta:

1. `node scripts/install.mjs --check` (kod 3: `node scripts/install.mjs`). Bez tego nie ma biegu.
2. Przeglądarka: sekcja „Przeglądarka”. Brak Chrome to kod 3 z listą sprawdzonych miejsc; takiego biegu nie
   zgłaszaj jako wykonanego.
3. Konfiguracja: plik JSON w repo projektu, bez sekretów, z neutralnymi nazwami. Każda strona ma `assert`
   (bez asercji wynik to UNVERIFIED). Logowanie przez `valueEnv`, nigdy literał.
4. Bieg: `node scripts/a11y.mjs --config <plik> <outDir>`. Katalog wyjściowy trzymaj poza kontrolą wersji.
5. Czytaj `<outDir>/a11y.json` (`result`, `exit`, `summary`, `pages[].findings`, `warnings`) i `<outDir>/a11y.md`
   (tabela na regułę, lista FAIL, UNVERIFIED z przyczyną, wyjątki i potwierdzenia). Zgłoś wynik kodem i wierszem
   `result`, a nie opisem.
6. FAIL naprawia się w kodzie, nie wyjątkiem. UNVERIFIED czytasz przez `reason`: limity (`page-too-tall`,
   `truncated`, `sequence-truncated`, `layout-shift`) nie mają ścieżki wyjątku; przyczyny pomiarowe można
   rozstrzygnąć tylko potwierdzeniem, a tylko za zgodą człowieka.
7. Wyjątek i ręczne potwierdzenie dopisuje człowiek albo agent za jego wyraźną zgodą. Każdy wpis ma `reason`,
   a potwierdzenie także `checkedBy` i `expires` (najwyżej 12 miesięcy od daty biegu). Wyjątek bez `reason` to
   błąd 2, a wpis po terminie przestaje działać (ostrzeżenie `exception-expired`).
8. Progów (`thresholds`) nie luzuj bez `reason` (błąd 2). Zejście poniżej 4,5 albo 3,0 z `reason` daje
   ostrzeżenie `threshold-lowered`. Progu nie zmieniaj po to, żeby bieg przeszedł.

Granice w skrócie: mierzony jest tekst i cele w otwartym shadow DOM (do 10 poziomów). Closed shadow DOM i iframe
z innego originu nie są audytowane (ostrzeżenia `shadow-not-audited`, `frames-not-audited`). Elementy `fixed`
i `sticky` są mierzone w początkowym przewinięciu. Kontrast wskaźnika fokusu (1.4.11, 2.4.13) jest mierzony tylko
w regułach opt-in; kontrast komponentów nietekstowych (1.4.11 poza wskaźnikiem) nie jest mierzony. Nie mierzy się
czytników ekranu, zoomu, reflow, reduced motion ani forced colors; to zostaje ręcznie (`web-a11y-testing`). Kliknięcia myszą (`focus.mouse`) wykonują akcje strony, więc tylko z jawną listą selektorów.
Skrypt działa w głównym świecie strony: audytuje własną aplikację, nie nieufną stronę. Pełna lista: `references/a11y.md`.


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
  `storageState.json` mogą zawierać dane logowania. Przy zapisie tych plików `capture` zakłada `.gitignore` z ich nazwami w `outDir`,
  ale tylko gdy `outDir` leży pod katalogiem konfiguracji i nie ma tam jeszcze `.gitignore` (istniejącego nie zmienia).

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
- `trace.zip` i `storageState.json` powstają tylko z `--allow-trace` (ostrzeżenie na stderr), z trybem 0600 (POSIX; na Windows
  tryb nie jest egzekwowany). `measure` przyjmuje tę flagę, ale nie zapisuje trace.
- Granica: maska działa na tekście. PNG i `trace.zip` zapisywane są bajtowo, bez maskowania, a `storageState.json` przechodzi przez maskę jak tekst (znane wartości
  sekretów zastąpione `***`, reszta nie); żaden z nich nie daje gwarancji, a maska zna tylko wartości ze zmiennych środowiska, nie tokeny sesji. Trace zawiera m.in. wartości pól formularza
  (także hasła z logowania), nagłówki i treści sieci, ciasteczka, migawki DOM i klatki ekranu; brak znanego sekretu w pliku
  niczego nie dowodzi. Zrzut PNG pokazuje to, co było na ekranie: obszary z danymi wrażliwymi zamaluj maską selektorową
  (`masks`) przed zrzutem. Nie wklejaj do czatu ani nie commituj `storageState.json`, `trace.zip` ani zrzutów z danymi osobowymi.
- Limit dekodera PNG: plik ponad 64 MiB (nie jest nawet wczytywany), nagłówek deklarujący ponad 16384 px na bok lub 50 M px (albo
  wymiar 0), przeplot Adam7, ponad 100000 chunków, ścieżka niebędąca zwykłym plikiem, pierwszy chunk inny niż IHDR o długości 13, drugi IHDR oraz PLTE ponad 768 B, niepodzielny przez 3 albo powtórzony są odrzucane przed dekodowaniem kodem 2 (ochrona
  przed bombą dekompresyjną); dotyczy `pixel-diff`, `diff`, `measure`, `capture` i `a11y`.
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
- Sandbox Chrome jest włączony domyślnie. `UI_VERIFY_NO_SANDBOX=1` dodaje `--no-sandbox`; to zmienna testowa
  (e2e w CI i na stacji), nie ustawienie do zwykłego pomiaru ani audytu.

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
| `UI_VERIFY_NO_SANDBOX` | `1` wyłącza sandbox Chrome; zmienna testowa (e2e w CI i na stacji), domyślnie sandbox jest włączony |
| `UI_VERIFY_SECRET_ENV` | nazwy zmiennych po przecinku, których wartości są maskowane; dopuszczone też jako `valueEnv` logowania |

Katalogi cache dla każdego systemu opisuje dokumentacja konfiguracji maszyny.

## Odbiór (fixtures)

- `fixtures/cases.json` zawiera trzy syntetyczne przypadki (pary HTML i PNG):
  `mirrored-icon` (pixel-diff FAIL, zakres 5–25 %), `shifted-flag` (FAIL, zakres 2–20 %) i
  `near-identical` (PASS, zakres 0,02–0,05 %). Selftest `pixel-diff` sprawdza je, gdy zależności są w cache.
- Ta dokumentacja nie podaje wyników przebiegów z przeglądarką. Dowód z Chrome to osobny krok.
- Fikstury audytu `a11y`: `fixtures/a11y/` (konfiguracje `*.config.json`, strony HTML, `exceptions.json`,
  `contrast/expected.json`). Opis i sposób uruchomienia: `references/a11y.md`.

## Pliki pomocnicze

- `references/configuration.md`: pełne schematy `measure` i `capture`.
- `references/verdicts.md`: werdykty, kody wyjścia, łączenie i formaty wierszy.
- `references/boundaries.md`: granice narzędzi i znane luki.
- `references/a11y.md`: audyt `a11y`: schemat konfiguracji, limity, plik wyjątków i potwierdzeń, `expected.json`,
  schemat `a11y.json`, kody przyczyn EXEMPT i UNVERIFIED, granice pomiaru, fikstury.
- `references/css-scope.md`: schemat konfiguracji `css-scope`, wyjątki, parser, format wyniku, ścieżki, granice.
