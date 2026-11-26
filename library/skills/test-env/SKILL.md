---
name: test-env
description: Plan and manage repeatable project environments with readiness checks,
  freshness, cold/warm starts and verified process cleanup.
---

# test-env

Przed testami integracyjnymi poznaj rzeczywistą recepturę repo: zależności, bazę, porty,
seed, migracje, start i health. Nie zgaduj polecenia ani nie zamykaj cudzych procesów.
Zapisz zaufane argv w workflow.environment i izolowane resources. Przejrzyj
`skillsync env .`, potem `--action up --run`. Sprawdź deskryptor i cold/warm start.
Po pracy `--action down --run`; na porażce zachowaj dowody. inputs i TTL unieważniają
reuse, health musi sprawdzać usługę. Brak receptury daje NOT_RUN. Linux ma sprawdzanie
PID/UID/starttime/session; Windows lifecycle pozostaje BLOCKED do wdrożenia backendu.
Launchery: `env . --generate --run`. Nie ogłaszaj startu Windows jako zweryfikowanego.

Migawka lokalnej bazy SQL Server przed zapisem testowym: `scripts/db_snapshot.py` (`backup`, `restore`, `verify`, `prune`), przepis
[db-snapshot](references/db-snapshot.md). Cofnięcie wymaga `--confirm` z nazwą bazy. `backup --sha256` zapisuje skrót kopii (bez niego `restore` ostrzega); kopię bez pliku pomocniczego przywraca `--allow-missing-sidecar`. Ogólny przykład dla LocalDB (`--server "(localdb)\MSSQLLocalDB"`, baza zawsze jawnie) jest w przepisie; preset projektu (serwer, baza) zostaje w projekcie. `restore --move existing|new` odtwarza kopię o innych ścieżkach plików na istniejącej bazie albo tworzy bazę, której nie ma (`new`, opcjonalnie `--move-dir`; test na żywo nowego kodu: NOT_RUN). Starą kopię sprawdza bez przywracania `verify` (plik, rozmiar, SHA-256 z pliku pomocniczego i na silniku nagłówek oraz `VERIFYONLY`; `--offline` bez silnika; niczego nie zmienia; kody 0, 1 kopia uszkodzona lub niezgodna, 2 nie dało się sprawdzić, 3 dry-run). Stare kopie usuwa ręcznie `prune` (też `--confirm`; tylko kopie tego narzędzia, bez kontaktu z silnikiem). Na silniku niezweryfikowane.

Kontrakty i przykłady: [workflow v2](../workflow/references/eight.md).
