---
name: task-claim
description: Coordinate whole tasks across sessions with atomic run-owned leases,
  heartbeats, expiry, transfer and explicit takeover.
---

# task-claim

Przy implementacji współdzielonego zadania wybierz stabilny task-id (np. identyfikator
trackera) i unikalny run-id sesji. `skillsync claim . --task-id X --run-id R --action acquire
--run` rezerwuje zadanie atomowo. Aktywna obca rezerwacja blokuje zapis. Ponowne acquire
właściciela jest idempotentne; heartbeat wymaga renew. TTL nie dowodzi śmierci sesji.
Wygasłą rezerwację przejmuj dopiero po sprawdzeniu kontekstu i jawnie przez --takeover.
Transfer: --action transfer --to-run-id S --run, tylko aktywny właściciel; po pracy release.
Plan bez --run nie zmienia rezerwacji. Claim nie zastępuje izolacji plików/baz/portów
ani blokad zasobów testów. Przy BLOCKED zapisz checkpoint i rozstrzygnij konflikt.

Kontrakty i przykłady: [workflow v2](../workflow/references/eight.md).
