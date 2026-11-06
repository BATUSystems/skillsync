---
name: observed-e2e
description: Record actual browser flows and evidence before deriving scenarios and
  executing existing native E2E runners.
---

# observed-e2e

Najpierw obejrzyj rzeczywistą ścieżkę w dostępnej przeglądarce i jej stany błędu.
Zapisz obserwację, kroki action/locator/expected, czas UTC, fingerprint źródeł i lokalne
dowody w .skillsync/e2e. Nie wymyślaj selektorów i nie wpisuj sekretów do rekordów.
`skillsync e2e . --action record --observation .skillsync/e2e/input.json --run`
waliduje dowody i tworzy scenariusz danych; samo zapisanie nie oznacza wykonania testu.
Dostosuj istniejący runner do scenariusza, użyj dynamicznych fixtures i sprzątania.
Skonfiguruj workflow.e2e.checks (argv, kind: e2e); wymagane rzeczywiście wykonane testy.
`e2e . --action run --run` sprawdza aktualność obserwacji/dowodów i wykonuje runner.
Trace/screenshot/log porażki przechowuj lokalnie. Brak browsera/runnera raportuj BLOCKED.

Kontrakty i przykłady: [workflow v2](../workflow/references/eight.md).
