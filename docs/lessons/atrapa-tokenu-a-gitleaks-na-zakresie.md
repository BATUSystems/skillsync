---
title: 'Zielony leakcheck nie oznacza zielonego gitleaks: literał atrapy w teście blokuje gałąź dopiero po zebraniu historii'
tags: [skillsync, tests, secrets, gitleaks, leakcheck, release]
paths: ['tests/**', 'skillsync_workflow/leakguard.py']
source: 'wydanie 1.19.0 (paczka maskowania sekretów): atrapa w zmiennej środowiska testu przeszła leakcheck przy commicie, a gitleaks zablokował historię gałęzi wydania'
verified: 2026-11-26
---
**Objaw:** każdy commit gałęzi przechodzi `skillsync leakcheck`, a `gitleaks` na gałęzi wydania zgłasza sekret (np. `generic-api-key`) w pliku testu. Poprawka w kolejnym commicie nie wystarcza, bo literał zostaje w historii: gałąź trzeba spłaszczyć.

**Przyczyna:** `leakcheck` i `gitleaks` mają różne reguły. Ten sam zakres commitów daje `leakcheck --range` PASS i jedno znalezisko `gitleaks`. Test maskowania z natury zawiera coś, co wygląda jak sekret, np. wartość podstawianą do zmiennej `API_TOKEN`.

**Reguła:** atrapę sekretu w teście składaj z fragmentów w czasie wykonania (`'abc' + 'def' + str(123456)`), także wartość zmiennej środowiska i nagłówka `Authorization`; nie zostawiaj jej jako literału. Przed zgłoszeniem gałęzi do integracji sprawdź cały jej zakres: `gitleaks git --no-banner --redact --log-opts="<baza>..HEAD"`. `leakcheck` przy commicie tego nie zastępuje.

**Jak wykryć:** porównaj na zakresie gałęzi `skillsync leakcheck . --range <baza>..HEAD` z `gitleaks git --log-opts="<baza>..HEAD"`; znalezisko tylko w drugim to literał atrapy do złożenia z fragmentów, zanim gałąź trafi do historii wydania.
