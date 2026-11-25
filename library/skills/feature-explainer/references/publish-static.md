# Publikacja: statyczny plik w repo (droga domyślna)

Domyślna droga we wszystkich hostach. Strona to katalog ze statycznym HTML i obrazami obok, bez serwera i bez kroku budowy.

## Układ

    docs/explainers/<slug>/
      index.html
      img/            (opcjonalnie; obrazy mogą też leżeć obok index.html)
        przeglad.jpg
        karta-wymagane-2z3.jpg

- `<slug>`: krótka nazwa funkcji małymi literami z myślnikami.
- Wszystkie ścieżki do obrazów **względne** (`img/przeglad.jpg`), żeby katalog działał po skopiowaniu i po otwarciu z dysku.
- Brak `data:`, brak zewnętrznych skryptów i czcionek.
- Użytkownik może wskazać inny katalog wyjściowy (np. poza repo, gdy strona nie ma trafić do historii); wtedy zapisz tam
  i powiedz, gdzie leży.

## Kroki

1. Skopiuj `assets/template.html` do `docs/explainers/<slug>/index.html` i wypełnij według [page-structure.md](page-structure.md).
2. Skopiuj obrazy do katalogu strony.
3. Otwórz stronę lokalnie (z dysku albo statycznym serwerem plików na wolnym porcie) i sprawdź: oba motywy, szerokość 390 px
   bez poziomego przewijania, wszystkie obrazy widoczne, kotwice spisu treści działają.
4. Zapisz zmiany w repo zgodnie z trybem dostarczania projektu (commit i dalsze kroki wg zasad projektu; skill sam nie commituje).

## Przekazanie odbiorcy

- Odbiorca z dostępem do repo: podaj ścieżkę `docs/explainers/<slug>/index.html` i gałąź lub commit.
- Odbiorca bez dostępu do repo: wyślij cały katalog (archiwum) albo zaproponuj drogę prywatnej strony w hoście
  ([publish-artifact.md](publish-artifact.md)); wybiera użytkownik.
- Powiedz wprost, że zrzuty są z danych przykładowych i że strona ma datę oraz wersję.

## Aktualizacja

Edytuj ten sam `index.html`, podmień obrazy o tych samych nazwach, zmień datę i wersję w nagłówku, powtórz kontrolę.
