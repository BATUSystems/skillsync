# Kontrakt silnika prototypów

Kontrakt to zachowanie silnika prototypów widoczne z zewnątrz: w DOM (UI w przeglądarce), w API HTTP serwera komentarzy,
w poleceniach CLI `skillsync prototype` i w eksportach Node (`module.exports` silnika `prototype.js`). Nie jest
opisem implementacji: wewnętrzne funkcje i kod źródłowy silnika nie są kontraktem, dopóki nie są eksportowane.

## Pliki

| Plik | Zawartość | Pilnują testy |
|---|---|---|
| [scenariusze-e2e.md](scenariusze-e2e.md) | stały zestaw scenariuszy UI, API i CLI z ID, krokami i oczekiwanym wynikiem; kolumna „Krytyczny”; lista znanych defektów (sekcja 10) | `tests/test_skillsync_prototype_e2e.py` ze skryptami `tests/e2e/prototype/*.mjs` |
| [slownik-html-css.md](slownik-html-css.md) | słownik identyfikatorów interfejsu: klasy CSS, atrybuty `data-*`, role i `aria-*`, symbole `ic-*`, haki UI używane przez E2E, narzędzie `layout-probe` | scenariusze E2E oraz testy szablonów w `tests/test_skillsync_prototype_library.py` |
| [api-eksportow.md](api-eksportow.md) | 107 nazw eksportów `module.exports` z sygnaturą i zachowaniem opisanym słowami | `tests/test_skillsync_prototype_engine.py` (zbiór nazw i wywołanie każdej nazwy) |
| [tematy-dokumentow.md](tematy-dokumentow.md) | tematy wymagane w dokumentach skilla `prototype` i szablonie README, z listą znaczników, które muszą się w nich pojawić | `tests/test_skillsync_prototype_library.py` (obecność tematów) |

Źródłem wymagań W1 (K- i D-numery w tabelach) jest spec zadania (poza rdzeniem).

## Testy pilnujące kontraktu

- `tests/test_skillsync_prototype_e2e.py` uruchamia skrypty `tests/e2e/prototype/*.mjs` w headless Chrome (wymaga
  `SKILLSYNC_E2E_CHROME`, node ≥ 22, `SKILLSYNC_TEST_LIBRARY`); każdy ID scenariusza to osobny subtest.
- `tests/test_skillsync_prototype_engine.py` sprawdza eksporty silnika (wywołania, nie tekst źródła) i różnicowo fold Node
  względem Pythona.
- `tests/test_skillsync_prototype_library.py` sprawdza narzędzia i szablony biblioteki prototypu (`check_tokens.py`,
  `index.html`, `template.html`, `layout-probe.js`) oraz obecność tematów z `tematy-dokumentow.md`.

## Zasada zmiany

Zmiana zachowania silnika jest zmianą kontraktu. Zmiana zachowania, eksportu, identyfikatora z słownika albo scenariusza
wymaga zmiany odpowiedniego pliku w tym katalogu i testu w **tym samym commicie**. Commit, który zmienia zachowanie bez
aktualizacji kontraktu albo testu, jest niepełny.

## Znane defekty

Jeśli obecny silnik nie spełnia wymaganego zachowania, scenariusz jest na liście `KNOWN_DEFECTS` w module E2E i jest oczekiwaną
porażką (XFAIL) do czasu naprawy. Wpis znika z listy w commicie, który naprawia defekt.
