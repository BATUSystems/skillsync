---
title: 'Dwa ciężkie biegi na jednej maszynie dają fałszywe porażki testów czasowych: jeden bieg naraz pod blokadą katalogową'
tags: [testing, remote, macos, timeouts, load, locking, evidence]
paths: ['library/skills/feature/references/prepush-matrix.md', 'tests/**']
source: 'zdarzenie z 2026-11-26: dwa równoległe biegi na maszynie macOS, czerwone testy z limitami czasu w obu; test z limitem żądania padał w tych warunkach także na czystym main'
verified: 2026-11-26
---
**Objaw:** przy równoległej pracy na jednej maszynie padają testy z limitem czasu (np. odpowiedź serwera albo hak w kilka sekund), za każdym razem inne, a w pojedynczym biegu bywają zielone.

**Przyczyna:** drugi ciężki bieg zabiera procesor i dysk; test bliski limitu przekracza go bez zmiany w produkcie. Rozłączne katalogi i porty tego nie usuwają. macOS nie ma `flock`.

**Reguła:** na maszynie testowej działa jeden ciężki bieg naraz. Przed biegiem `mkdir ~/mac141/.heavy.lock` (kod różny od 0: zajęte, czekaj), po biegu `rmdir`, także w `trap` przy błędzie i przerwaniu. Blokady po cudzym biegu nie usuwaj bez potwierdzenia właściciela. Porażkę czasową z obciążonej maszyny porównaj z czystym `main` w tych samych warunkach, zanim przypiszesz ją zmianie albo ją odrzucisz.

**Jak wykryć:** `uptime` z wysokim obciążeniem i więcej niż jeden proces `unittest` z różnych katalogów w trakcie biegu.
