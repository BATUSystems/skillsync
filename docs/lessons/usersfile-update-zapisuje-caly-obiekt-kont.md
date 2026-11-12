---
title: "UsersFile.update zapisuje cały obiekt kont: zmiana jednego konta nie może normalizować pozostałych"
tags: [skillsync, python, prototype, users, compatibility, migration]
paths: ['skillsync_workflow/prototype_server.py', 'tests/test_skillsync_prototype_roles.py']
source: skillsync_workflow/prototype_server.py (`UsersFile.update`, `roles_of`, `primary_role`, `role_fields`), tests/test_skillsync_prototype_roles.py, docs/upgrading.md
verified: 2026-11-12
---
**Objaw:** zmiana hasła jednego konta zmienia w pliku kont rekordy innych kont (np. przepisane `role` na `roles`), a po wycofaniu rdzenia starszy odczyt widzi inne uprawnienia niż przed zmianą.

**Przyczyna:** `UsersFile.update(change)` czyta cały plik, woła `change(users)` na liście kont i zapisuje cały obiekt z powrotem (`json.dumps` całych danych, kopia `.bak`). Normalizacja pól w `change()` albo przy zapisie dotyka więc wszystkich rekordów, nie tylko modyfikowanego.

**Reguła:** normalizację ról robi tylko czysty akcesor przy odczycie (`roles_of`, `primary_role`; nie mutuje), a `change()` zmienia wyłącznie wybrany rekord (`role_fields` dla niego). Zapis ma być zgodny wstecz: `role` zawsze najwyższa z zbioru ról rozumianego przez starszy rdzeń, `roles` tylko dla `expert` albo wielu ról. Walidator ma osobną stałą zbioru ról rozumianego przez starszy rdzeń (`LEGACY_ROLES`: `commenter`, `acceptor`, `developer`), więc `role: expert` jest odrzucane.

**Jak wykryć:** testy `passwd` konta A nie zmienia bajtów rekordów B i odczyt nie mutuje pliku (`AccessorAndValidationTests`, `WriteCompatibilityTests` w `tests/test_skillsync_prototype_roles.py`).
