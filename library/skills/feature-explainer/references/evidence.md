# Dowody: od kodu do strony

## Notatki robocze

Przed pisaniem prowadź tabelę (plik roboczy poza stroną, np. w katalogu tymczasowym):

| Reguła (słowami odbiorcy) | Plik:linia | Potwierdzona | Uwagi |
|---|---|---|---|
| „Postęp liczą tylko kroki wymagane” | `ścieżka/do/pliku:42` | tak | kroki opcjonalne pomijane |

Zasady:

- Reguła trafia na stronę dopiero po potwierdzeniu w kodzie (przeczytanie ścieżki wykonania, nie samej nazwy funkcji).
- Zapisuj linię, na której reguła faktycznie zapada (warunek, wzór), nie tylko plik.
- Reguły z konfiguracji potwierdź zarówno w kodzie, który ją czyta, jak i w przykładowej konfiguracji.
- Pominięty przypadek brzegowy (brak danych, pusta lista, dzielenie przez zero) też ma notatkę.

## Rozjazd dokument–kod

Gdy rekord wiedzy lub starsza dokumentacja mówi co innego niż kod:

1. Opisz na stronie to, co robi **kod**.
2. Zgłoś rozjazd osobno (użytkownikowi, w raporcie): co mówi dokument, co robi kod, gdzie (`plik:linia`).
3. Nie poprawiaj dokumentu po cichu w ramach tej strony; poprawka dokumentu to osobna decyzja.

## Niezależna weryfikacja

Opcjonalnie drugi agent dostaje stronę i dostęp do kodu, ale nie Twoje notatki. Pyta się go o: zgodność faktów z kodem,
brakujące niuanse, miejsca niezrozumiałe dla laika. Dopisz tylko to, co wniesie, i oznacz zmiany w raporcie.

## Statusy odpowiedzi w FAQ

| Status | Znaczenie | Co dopisać |
|---|---|---|
| Reguła | zachowanie jest zgodne z regułami | która reguła i liczby przykładu |
| Konfiguracja | zależy od ustawień instalacji | gdzie to się ustawia, jaka wartość daje skutek |
| Do analizy | nie ma jeszcze wyjaśnienia | czego potrzeba od odbiorcy, żeby potwierdzić (zrzut z danych przykładowych, krok odtworzenia) |
| Błąd | zachowanie jest sprzeczne z regułami | opis rozbieżności bez obietnicy terminu poprawki |

Status „Błąd” lub „Do analizy” nie jest zobowiązaniem: strona nie podaje terminów i decyzji, których nikt nie zatwierdził.
Po poprawce błędu zmień status, zaktualizuj regułę i datę.
