# Werdykty, kody wyjścia i łączenie

## Werdykty

| Werdykt | Znaczenie | Kod |
|---|---|---|
| PASS | dowód zgodności: wszystkie pozycje PASS | 0 |
| FAIL | wykryta różnica (pomiar, kształt, pixel-diff, brak zrzutu w porównaniu) | 1 |
| UNVERIFIED | brak dowodu: brak asercji, brak elementu, brak manifestu, brak PASS po obu stronach | 3 |
| NOT_RUN | nie uruchomiono: brak zależności, Chrome, npm, sieci lub blokady | 3 |
| błąd narzędzia | konfiguracja, adapter, ścieżka, padnięcie Chrome, timeout, wyjątek | 2 |

Kod 1 znaczy wyłącznie „weryfikacja wykryła różnicę”. Błąd narzędzia nigdy nie daje 1.

## Łączenie

- Ogólna reguła (`combine`): jeśli jest FAIL, wynik to FAIL. PASS tylko wtedy, gdy lista jest niepusta i
  wszystkie pozycje są PASS. W każdym innym przypadku UNVERIFIED.
- `measure`: wynik pary to łączenie pomiaru i kształtu. Kształt SKIPPED liczy się jako UNVERIFIED (para nie
  jest PASS bez dowodu pikselowego); kształt ACCEPTED (z `acceptShape`) liczy się jak PASS. Wynik
  przebiegu to łączenie par.
- `capture`: pozycja to asercja (`PASS` → PASS, `FAIL` → FAIL, `NONE` → UNVERIFIED). Wynik to łączenie pozycji.
- `diff`: wpis `SAME` → PASS, `DIFFERENT` i `MISSING` → FAIL, `UNVERIFIED` → UNVERIFIED. `ADDED` nie
  wpływa na wynik. Wynik to łączenie wpisów.
- `pixel-diff`: nie ma UNVERIFIED. Kod 3 pojawia się tylko przy braku zależności.
- Pusty zestaw w `diff` (`no-shots`) to UNVERIFIED, nie PASS.

## Powody (pole `reason` i `shape.reason`)

| Polecenie | Powód | Znaczenie |
|---|---|---|
| `measure` | `element-not-found` | selektor nie znalazł elementu po jednej ze stron (UNVERIFIED) |
| `measure`, `capture` | `page-state-mismatch` | końcowy adres inny niż zadany (przekierowanie); UNVERIFIED |
| `measure` | `shape-skipped` | kształt pominięty, a pomiar nie dał FAIL; para UNVERIFIED |
| `measure` (kształt) | `box-mismatch` | wymiary różnią się więcej niż `sizeTolerancePx`; kształt SKIPPED |
| `measure` (kształt) | tekst `acceptShape.reason` | kształt ACCEPTED (liczony jak PASS) |
| `capture` | `assertion-failed` | asercja FAIL (element lub tekst nieobecny) |
| `capture` | `no-assertion` | brak `assert` (UNVERIFIED) |
| `diff` | `no-positive-assertion` | po jednej stronie asercja nie jest PASS (UNVERIFIED) |
| `diff` | `size-mismatch` | różne wymiary zrzutów (DIFFERENT) |
| `diff` | `no-manifest` | brak `manifest.json` w jednym z katalogów (UNVERIFIED, kod 3) |
| `diff` | `no-shots` | brak zrzutów do porównania (UNVERIFIED) |
| `pixel-diff` | `size-mismatch` | różne wymiary bez `--crop` (FAIL) |

## Formaty wyjścia (stdout)

- `measure` (tekst): wiersz `PASS|FAIL|UNVERIFIED <id>`, potem `diff <prop> actual … expected …`,
  `approved <prop> … (reason: …)`, `shape PASS|FAIL <pct>%` (z ` cropped` przy przycięciu) albo
  `shape SKIPPED (box-mismatch)` albo `shape ACCEPTED (reason: …; was …)`. Na końcu `result <wynik> (exit <n>)`. `--json`: obiekt z `verdict`,
  `exit`, `pairs[]` (`id`, `verdict`, `measure`, `shape`).
- `capture`: wiersze `PASS|FAIL|UNVERIFIED <nazwa> [powód] (assertion X)`, potem `result`. Plik
  `manifest.json` zapisuje to samo bez bajtów PNG.
- `diff`: wiersze `SAME|DIFFERENT|MISSING|ADDED|UNVERIFIED <nazwa> [powód] [procent%]`, potem `result`.
- `pixel-diff`: `PASS pixel-diff <pct>% (<różne>/<razem>)` albo `FAIL pixel-diff <pct>% (…)`; przy
  rozmiarze: `FAIL pixel-diff size-mismatch (<A> vs <B>)`. `--json`: `verdict`, `pct`, `diffCount`,
  `total`, `sizeMismatch`, `cropped`.

Komunikaty zależności i Chrome są na stderr, z instrukcją i kodem 3.

## Kody wyjścia według polecenia

| Polecenie | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| `measure` | PASS | FAIL pomiaru lub kształtu | konfiguracja (także literówka w `styleProps`), adapter, ścieżka, niedozwolona nawigacja | brak zależności/Chrome, UNVERIFIED |
| `pixel-diff` | PASS | FAIL (próg, rozmiar) | użycie, błąd wewnętrzny | brak zależności |
| `capture` | PASS | FAIL asercji | konfiguracja, adapter, ścieżka, login bez zmiennej lub z niedozwoloną zmienną, niedozwolona nawigacja | brak zależności/Chrome, UNVERIFIED |
| `diff` | SAME wszystkich | DIFFERENT, MISSING | manifest, użycie, błąd wewnętrzny | brak zależności, UNVERIFIED |
| `install` | zainstalowane lub już są | (nie używany) | cache niebezpieczny (także przy ładowaniu: obcy właściciel, zapis g/o, względna ścieżka), błąd wewnętrzny | npm, sieć, proxy, blokada, `--check` bez instalacji |
