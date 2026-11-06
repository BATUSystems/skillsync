# Start dyspozycji i claimy paczek

Czytaj przy starcie `/dispatch <cel>` (kroki 1–5) i przy rejestracji paczek jako `task-claim`.

## Start

1. Wczytaj instrukcje projektu, manifest `.github/.skillsync.yaml` (sekcja `dispatch`, patrz [config.md](config.md))
   i indeks/plan paczek projektu, jeśli istnieje. Utwórz `run-id` i plik stanu
   `.skillsync/dispatch/<run-id>.yaml` (ignorowany przez git; `skillsync` dopisuje `.skillsync/`
   do gitignore — sprawdź).
2. Wykryj karty (adapter) i pokaż ich listę: nazwa, identyfikator, stan, czas startu. **Zapytaj**,
   których wolno użyć (wybór wielokrotny). „Idle” nie znaczy „wolna”. Sesje chmurowe nie mają
   lokalnych zasobów (DB, porty) — domyślnie nieużywane.
3. **Nadaj aliasy**: ty = `Dyspozytor`, karty = `karta-1`, `karta-2`, … (kolejność wyboru,
   imiona nie wracają w obrębie `run-id`; lista: w pliku `imiona.md` w katalogu `references/` skilla `dispatch` (nakładka imion w warstwie osobistej)). Nazwy nadane przez użytkownika mają pierwszeństwo.
   Zmiana nazwy sesji, jeśli host jej nie udostępnia programowo, jest ręczna: pokaż tabelę
   „obecna nazwa → alias” i zweryfikuj na liście po zmianie ([adapter](claude.md)).
   Do tego czasu adresuj po identyfikatorze.
4. Wyślij każdej karcie `PROBE`. Brak odpowiedzi (po jednym ponowieniu) = karta niedostępna;
   nie czekaj na nią, poproś użytkownika o restart/zamknięcie i przydziel paczkę innej.
5. Zbadaj zasoby (`df`, `/proc/loadavg`, porty z `leased_resources`). Zaproponuj limit
   równoległości: min(liczba kart, `dispatch.max_parallel`), niżej przy małym dysku lub
   wysokim load. Ostrzeż, że wysoki load psuje testy (obserwowane timeouty E2E/Vitest przy
   load 15–20); przy testach E2E rekomenduj 2 karty.


## Claimy paczek

4. Rejestruj paczki jako zadania `task-claim` — **claimy prowadzi wyłącznie dyspozytor**, z katalogu
   głównego repo (claimy leżą w `.skillsync/claims` względem cwd; karty pracują w worktree i ich nie
   dotykają): `skillsync claim . --task-id <paczka> --run-id <run-id>.<alias> --action acquire
   --ttl 14400 --run`. Identyfikatory to slugi `[A-Za-z0-9][A-Za-z0-9_.-]{0,79}` (bez `/`, razem
   ≤ 80 znaków; `<paczka>` także musi spełniać ten wzorzec). Bez `--ttl` obowiązuje 900 s, a
   heartbeat idzie tylko przy zdarzeniach — podawaj jawnie `--ttl` (maks. 86400; rekomendacja 14400 = 4 h).
   Odnawiaj (`--action renew --run`) przy zdarzeniach karty. Po wygaśnięciu `renew` daje BLOCKED;
   odnowienie to `acquire --takeover --run` przez dyspozytora po `PROBE` karty (żyje i nadal pracuje
   nad paczką) i zapis zdarzenia w pliku stanu. Realokacja: `--action transfer --to-run-id
   <run-id>.<nowy-alias>` (tylko przy aktywnym claimie) albo takeover po sprawdzeniu.

