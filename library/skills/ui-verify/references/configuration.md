# Konfiguracja ui-verify (schematy JSON)

Wspólne zasady: plik ma `"version": 1`. Klucz spoza listy to błąd walidacji (kod 2). Liczby muszą być
skończone. Identyfikatory (`id`) pasują do `[A-Za-z0-9._-]`, 1–120 znaków, nie mogą być `.` ani `..`.
Adresy `url`: `http(s)`, `file://` pod katalogiem konfiguracji albo ścieżka względna (zamieniana na
`file://` pod tym katalogiem). Pozostałe adresy wymagają `--allow-origin` (zob. SKILL.md, „Polityka URL i ścieżek”).

## measure

| Klucz | Typ | Domyślnie | Znaczenie |
|---|---|---|---|
| `version` | `1` | wymagane | wersja schematu |
| `viewport` | `{width, height, deviceScaleFactor}` | `400`, `200`, `1` | rozmiar okna każdej strony; liczby dodatnie, klucze tylko z listy |
| `tolerancePx` | liczba ≥ 0 | `1` | tolerancja x, y, width, height i liczbowych stylów (px) |
| `sizeTolerancePx` | liczba ≥ 0 | `1` | tolerancja wymiarów, przy której liczony jest kształt; różnica w tolerancji daje przycięcie |
| `colorTolerance` | liczba ≥ 0 | `0` | tolerancja porównania kolorów |
| `maxPct` | liczba ≥ 0 | `0.5` | próg procentowy pixel-diff kształtu |
| `styleProps` | lista niepustych nazw | `color`, `backgroundColor`, `borderTopColor`, `borderTopLeftRadius`, `fontSize`, `fontFamily` | właściwości CSS porównywane z wzorcem |
| `secretEnv` | lista nazw zmiennych | `[]` | wartości tych zmiennych są maskowane we wszystkich wyjściach |
| `pairs` | niepusta lista | wymagane | pary do porównania |

Para (`pairs[]`):

| Klucz | Wymagane | Znaczenie |
|---|---|---|
| `id` | tak | unikalny identyfikator (nazwa pliku `<id>.diff.png`) |
| `reference` | tak | wzorzec: `{ "url": "...", "selector": "..." }` |
| `candidate` | tak | kandydat: `{ "url": "...", "selector": "..." }` |
| `deviations` | nie | lista zatwierdzonych odstępstw (zob. niżej) |
| `masks` | nie | prostokąty `{x, y, width, height}` (liczby, `width` i `height` ≥ 0) w pikselach obrazu elementu; zamalowywane przed porównaniem kształtu |

Selektor to pierwsze dopasowanie na stronie. Brak elementu daje UNVERIFIED (kod 3).

Odstępstwo (`deviations[]`):

| Klucz | Wymagane | Znaczenie |
|---|---|---|
| `prop` | tak | nazwa właściwości, np. `fontSize` albo `width` |
| `reason` | tak | uzasadnienie, bez niego walidacja odrzuca konfigurację |
| `selector` | nie | jeśli równy selektorowi pary, jest ignorowany; inny selektor nie pasuje |
| `actual`, `expected` | nie | jeśli podane, odstępstwo dotyczy tylko tej wartości (dokładna równość) |

Zatwierdzone odstępstwo przechodzi do wyniku jako `approved` z `reason`, nie jako `diff`.

Przykład:

```json
{
  "version": 1,
  "viewport": { "width": 400, "height": 200, "deviceScaleFactor": 1 },
  "tolerancePx": 1,
  "sizeTolerancePx": 1,
  "colorTolerance": 0,
  "maxPct": 0.5,
  "secretEnv": [],
  "pairs": [
    {
      "id": "example-button",
      "reference": { "url": "reference/index.html", "selector": "#target" },
      "candidate": { "url": "http://localhost:3000/", "selector": "#target" },
      "deviations": [{ "prop": "fontSize", "reason": "zatwierdzone w przeglądzie projektu" }],
      "masks": [{ "x": 0, "y": 0, "width": 40, "height": 12 }]
    }
  ]
}
```

## capture

| Klucz | Typ | Domyślnie | Znaczenie |
|---|---|---|---|
| `version` | `1` | wymagane | wersja schematu |
| `viewport` | `{width, height, deviceScaleFactor}` | `1280`, `800`, `1` | okno; `width` jest szerokością, gdy brak `widths` |
| `widths` | lista liczb całkowitych 200–4000 | `[viewport.width]` | szerokości zrzutów (unikalne) |
| `modes` | lista z `light` i `dark` | `["light"]` | tryby kolorów (`prefers-color-scheme`) |
| `secretEnv` | lista nazw zmiennych | `[]` | maskowanie wartości |
| `login` | obiekt | brak | logowanie raz na bieg, zob. niżej |
| `pages` | niepusta lista | wymagane | strony do zrzutu |

`login`:

| Klucz | Znaczenie |
|---|---|
| `url` | adres strony logowania (ta sama polityka URL) |
| `steps` | niepusta lista kroków; każdy krok ma dokładnie jeden klucz |

Krok: `{ "fill": { "selector": "...", "valueEnv": "NAZWA_ZMIENNEJ" } }` albo `{ "click": "selektor" }`.
`fill.value` z literałem jest odrzucany. Wartość pochodzi tylko ze zmiennej, której nazwa jest w `valueEnv`.
Brak tej zmiennej w środowisku to błąd 2 przed uruchomieniem przeglądarki. Nazwy `valueEnv` są maskowane
automatycznie.

Strona (`pages[]`):

| Klucz | Typ | Znaczenie |
|---|---|---|
| `id` | identyfikator | część nazwy zrzutu |
| `url` | adres | strona do zrzutu |
| `waitFor` | selektor | element, który musi być widoczny (limit 10 s) |
| `assert` | `{selector, text?}` | asercja pozytywna: element istnieje, a przy `text` jego treść zawiera tekst |
| `masks` | lista | `{selector}` (wszystkie dopasowania, prostokąt z bounding box) albo `{x, y, width > 0, height > 0}` |
| `fullPage` | bool, domyślnie `false` | zrzut całej strony; maski selektorowe uwzględniają przewinięcie |

Strona bez `assert` nie daje PASS (UNVERIFIED, kod 3).

Przykład:

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
      "waitFor": "main",
      "assert": { "selector": "h1", "text": "Przykładowy tytuł" },
      "masks": [{ "selector": "[data-volatile]" }, { "x": 0, "y": 0, "width": 200, "height": 24 }],
      "fullPage": false
    }
  ]
}
```

Ustawienia stałe dla każdego zrzutu: animacje wyłączone, `prefers-reduced-motion: reduce`, service workery
zablokowane, pobieranie wyłączone. Jeden kontekst przeglądarki na bieg (ciasteczka wspólne dla stron).
Nawigacja 30 s, akcje 10 s.
