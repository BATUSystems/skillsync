---
title: "Dowód potrzebny w innym checkoucie musi leżeć w gicie: magazyn .skillsync jest lokalny, a przykład linii w bloku kodu nie może udawać dowodu"
tags: [skillsync, closure, plan, runs, git, multi-machine, parser, worktree]
paths: ['skillsync_workflow/plan_closure.py', 'skillsync_workflow/protocol_tokens.py', 'skillsync_workflow/runs.py', 'library/hooks/check-closure.py', 'tests/test_skillsync_closure_verdict.py']
source: "zgłoszenie Agady (VE, 25.11.2026) i spec CORE-CLOSURE-TRACKED; raporty testów B-1…B-4a (26.11.2026): przykład linii zamknięcia w bloku kodu przed `Przebieg:` czytany jako dowód; plan zmieniony scaleniem datowany starym commitem"
verified: 2026-11-26
---
**Objaw:** plan `done` wygląda w innym klonie, worktree albo na innej maszynie na niezamknięty (albo zamknięty „na słowo”), bo jedyny dowód zamknięcia to rekord `final` w `.skillsync/runs`.

**Przyczyna:** `.skillsync/` jest poza gitem. Stan wyprowadzany z niego nie przechodzi przez commit, więc każdy czytnik w innym checkoucie musi zgadywać.

**Reguła:** (1) dowód, który ma widzieć inny checkout, zapisuj w śledzonym pliku (linia `Zamknięcie:` w nagłówku planu), a magazyn lokalny traktuj jako szczegół właściciela; (2) czytnik takiej linii czyta tylko nagłówek i pomija bloki kodu, bo dokumentacja i przykłady zawierają te same tokeny; (3) kopie czytnika (generator i samodzielny hak) pilnuje test różnicowy na jednym korpusie; (4) data „ostatniej zmiany” z gita zamiast `mtime` (klon i przełączenie gałęzi zmieniają `mtime`), a log gita z `-c`, bo domyślnie commit scalający nie wymienia plików.

**Jak wykryć:** test w świeżym klonie bez `.skillsync/runs`; plan z przykładem linii w bloku kodu przed prawdziwą linią; plan zmieniony przy rozwiązaniu konfliktu scalania.
