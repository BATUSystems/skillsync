# Struktura strony

Strona ma dziewięć sekcji w tej kolejności. Gotowy szkielet: `assets/template.html` (przykład syntetyczny do zastąpienia).

| # | Sekcja | Zawartość |
|---|---|---|
| 1 | Nagłówek | nazwa funkcji, jedno zdanie celu, stan na datę i gałąź lub wersję, informacja o danych przykładowych, spis treści z kotwicami |
| 2 | Przegląd | zrzut przeglądowy z opisem elementów; kto widzi ekran i co trafia na listę |
| 3 | Anatomia | wycinek jednego elementu i numerowana lista jego części (co to jest, skąd się bierze); rozróżnij pojęcia łatwe do pomylenia |
| 4 | Reguły | tabele „warunek → wynik” w kolejności sprawdzania, wzory z przykładem liczbowym, wyjątki i pierwszeństwa |
| 5 | Akcje | przyciski i przejścia: reguła wyboru |
| 6 | Galeria przykładów | siatka 3, 2 i 1 kolumny; każdy przypadek z wyjaśnieniem |
| 7 | Konfiguracja | gdzie się ustawia, przykład oznaczony jako przykładowy, skutek braku konfiguracji |
| 8 | Pytania / FAQ | każde pytanie odbiorcy: przyczyna zgodna z regułami, stan, czego potrzeba do potwierdzenia |
| 9 | Stopka | źródło reguł (dokument i plik kodu), kontakt |

## Zasady treści

- **Język odbiorcy.** Etykiety z interfejsu w cudzysłowach, krótkie zdania, bez żargonu implementacyjnego w tekście głównym.
  Nazwy klas i plików tylko w `code` i tam, gdzie pomagają zgłaszać błędy.
- **Pojęcia łatwe do pomylenia** rozdziel wprost (np. surowy status a etap uproszczony z konfiguracji).
- **Kolejność w tabelach reguł** numeruj tylko wtedy, gdy ma znaczenie (pierwsza pasująca reguła wygrywa).
- **Wzory** w osobnym bloku, zawsze z przykładem liczbowym.
- **Podpis „dlaczego”** pod każdym przykładem: reguła i liczby. Sam opis wyglądu nie wystarcza.
- **Nagłówek** zawsze zawiera datę, wersję lub gałąź oraz zdanie, że zrzuty pochodzą z danych przykładowych.
- **Konfiguracja** zawsze z ostrzeżeniem, że wartości są przykładowe, a identyfikatory różnią się w każdej instalacji.
- **FAQ** bez obietnic poprawek; stany według [evidence.md](evidence.md).

## Forma

- Ustaw atrybut `lang` na język strony.
- `<header>` i `<footer>` strony mają mieć `id` (kontrola `sections` liczy je tylko z `id`).
- Jeden plik `index.html`, tokeny CSS dla motywu jasnego i ciemnego (przełączane przez `prefers-color-scheme`).
- Tabele w kontenerach z przewijaniem poziomym; strona na szerokości telefonu nie przewija się poziomo.
- Brak zewnętrznych skryptów i czcionek. Obrazy jako pliki obok strony, ścieżki względne, każdy z `alt`.
- Szablon możesz skracać (sekcja bez treści dostaje jedno zdanie, dlaczego jej nie ma), ale nie zmieniaj kolejności.
