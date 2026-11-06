# Pomiar narzędzi i skuteczności modeli

Suite offline cases definiuje id, argv (lista bez shell), fixture (ścieżka → tekst), cwd,
timeout, expected_exit, expected_output. Każdy case dostaje świeży katalog tymczasowy.
Placeholdery argv: {python}, {skillsync}, {project}. --run wykonuje komendy z suite.
Operator odpowiada za zaufany zestaw; fixture nie jest sandboxem uprawnień procesu.

Zadania modeli (w osobnym kontrolowanym pilotażu):

| Rodzina | Cel | Kryteria ukryte przed implementerem |
|---|---|---|
| Bugfix | naprawa konkretnego triggera | fail-to-pass + istniejące pass-to-pass |
| API | zmiana klient/serwer | kontrakt, walidacja, uprawnienia, błędy |
| Dane | migracja istniejących rekordów | backfill, rollback, brak utraty |
| Pełna funkcja | backend + interakcja UI | przepływ użytkownika, dostępność, regresje |

Przypnij repo/stan początkowy, wersję workflow, model, klient, środowisko i budżet.
Wykonaj kilka powtórzeń, sprawdź poprawne rozwiązanie referencyjne i negatywne kontrole.
Testy wynikowe są niezależne od implementera. Mierz czas, realnie dostępny koszt/tokeny,
próby naprawy i wynik w budżecie. Brak kosztu pozostaje unknown; nie mieszaj walut/jednostek.
Checkpoint `--cost-unit` domyślnie USD; metryki sumują osobno każdą jednostkę, bez przeliczeń.
Jedna próba ani wyższe pokrycie instrukcjami nie dowodzą przyczynowej poprawy skuteczności.
