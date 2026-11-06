# Kontrola kontraktów przed implementacją

Zakres dobiera ryzyko, nie pilność ticketa. Dla high albo kontraktów sprawdź:

- Publiczne API/DTO, formaty danych, nazwy zdarzeń i ich konsumenci; brak/zmiana pola,
  status HTTP, nullability, idempotencja i zgodność klienta ze starą/nową wersją.
- Istniejące dane: backfill, migracja schematu, deploy przed/po migracji, rollback.
- Uprawnienia, zakres tenant/organizacja, walidacja niezaufanego inputu.
- Transakcje, współbieżność, ponowienia, cache i kolejność skutków ubocznych.
- Kontrolę zmienionej granicy w realnym przepływie, nie tylko unit helpera.

Każdy wniosek odnosi się do rzeczywistego pliku/konsumenta. Brak wystarczających dowodów
oznacza lukę w gotowości. Kontrola CLI sprawdza strukturę; review ocenia prawdziwość.
Dokument kontraktów produktowych może być wskazany w workflow.routing lokalnego manifestu.
