---
name: smart-test
description: Select affected checks from real git changes, explicit path rules and module dependencies; use conservative fallback and the shared validation runner.
---

# Testy zmienionego obszaru

`skillsync smart-test <projekt> --base <rzeczywista-baza>` planuje testy i uzasadnia wybór.
Bez --base analizuje staged/unstaged/untracked; nie zgaduje bazy z upstreamu feature branch.
`--files <ścieżki>` służy do jawnego zakresu. `--run` wykonuje plan przez wspólny runner.

Mapowanie jest w `workflow.testing.rules`, zależności modułów w konfiguracji projektu.
Wspólne pliki, usunięcia/nieznany zakres i brak pewnej mapy wymagają konserwatywnego fallbacku.
Czytaj `uncovered`, by uzupełnić mapowanie (reguły/moduły) zamiast żyć na pełnym fallbacku; `test_only`/`direct_files` (przy `testing.test_paths`) to wskazówka, nie zmiana wyboru. `summary` po `--run` liczy kontrole, nie testy (`tests_counted: null` = brak danych, nie wnioskuj). Przeglądaj raport: cache przechowuje plan, nie potwierdzenie testów. Nowy stan kodu,
konfiguracji lub bazy unieważnia dobór. Pusta lista nie oznacza sukcesu.

Testy uruchamiaj wyłącznie z przydziałem baz/portów/build. Brak środowiska daje BLOCKED;
zero wykonanych testów albo same pominięcia nie daje PASS.
Po zmianie wspólnego kontraktu oraz przed zakończeniem wykonaj wymagany szerszy profil final.
