# Review: materiał dla reviewera i zakres sprawdzenia

Czytaj przed przygotowaniem zlecenia review.

Orkiestrator przygotowuje dla `reviewer` pełny materiał w plikach: diff roboczy i staged,
listę nowych plików (ich treść agent czyta bezpośrednio), stan początkowy, plan, plik FRD wskazany w planie, spec z linii `Spec:` i logi testów. Przy specu `approved` najpierw porównaj `Spec-sha` ([spec](spec.md)).
Przekazuje ścieżki, nie tylko streszczenie implementera. Reviewer pracuje wyłącznie
w trybie odczytu określonym przez adapter. Brak materiału zgłasza jako `BLOCKED`;
orkiestrator dostarcza go bez rozszerzania uprawnień roli.

Reviewer sprawdza poprawność i kryteria, nie tylko zgodność z planem — plan także może być
błędny. Sprawdza też, czy dokumentacja odpowiada nowemu zachowaniu i czy zgłoszone odkrycia
zostały utrwalone; brak bez uzasadnienia to uwaga blokująca. Weryfikuje kontrakty między
warstwami i ryzyka właściwe zmianie. Dla UI uwzględnij wykonanie najważniejszej ścieżki
w przeglądarce, jeśli narzędzia są dostępne; brak takiej weryfikacji zapisz jawnie.
Checklistę dobierz: `skillsync review <projekt> --risk <low|medium|high> --files <ścieżki>`
oraz [review/QA](../../workflow/references/review.md). CLI nie wystawia werdyktu review.

Review kodu idzie [pętlą `kod`](review-loop.md): uwagi → poprawki odpowiedniego agenta → testy dotkniętych obszarów
(szerzej, gdy zmienił się wspólny kontrakt; w pętli końcowej zestaw końcowy) → kolejna runda w nowym podumyśle `reviewer`,
która sprawdza zamknięcie uwag. Zakres diffu w rundach (pełny albo poprawki z dotkniętymi plikami) ustala pętla według
ryzyka planu. `verifier` tylko dla punktowych twierdzeń poza pętlą (np. „linia X zmieniona”).
Bez zmian nie powtarzaj zaliczonych sprawdzeń. Nowy zapis unieważnia odpowiednią część weryfikacji.
