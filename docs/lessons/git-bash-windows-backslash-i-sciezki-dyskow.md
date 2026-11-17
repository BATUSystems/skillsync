---
title: 'Git Bash na Windows: niecytowany backslash znika, a /c/... to dysk: narzędzie czytające polecenie powłoki musi czytać je jak powłoka'
tags: [skillsync, windows, git-bash, shell, hooks, parsing]
paths: ['library/hooks/guard-commands.py', 'skillsync_workflow/guardhook.py', 'tests/test_skillsync_guard_hook.py']
source: 'prace nad wydaniem 1.8.0: poprawka haka ochronnego dla Windows (polecenia z Git Bash)'
verified: 2026-11-17
---
**Objaw:** hak sprawdzający polecenie powłoki (np. blokada trwałego usuwania) na Windows ocenia inny cel niż ten, który wykona powłoka (przepuszcza albo blokuje bez powodu): ścieżka `C:\work\x` po przejściu przez powłokę wygląda inaczej niż w tekście polecenia, a `/c/work/x` czytane jako ścieżka POSIX nie wskazuje dysku.

**Przyczyna:** w Git Bash niecytowany backslash jest znakiem ucieczki i znika (`C:\work\x` → `C:workx`), a `/c/...` oznacza dysk `C:`. Narzędzie, które tnie polecenie własnym tokenizerem i ocenia cel po tekście, widzi coś innego niż powłoka, która polecenie wykona.

**Reguła:** narzędzie czytające polecenie powłoki czyta je jak powłoka, której używa host (na Windows Git Bash): niecytowany backslash w ścieżce znika przed oceną celu, a `/c/...` i `/cygdrive/c/...` są mapowane na dysk (`C:/...`), przy założeniu domyślnych montowań Git Bash (nie WSL ani własne fstab). Wątpliwość oznacza odmowę: `\\?\C:\...`, ścieżki UNC i wszystko, czego nie da się rozwiązać, zostają odrzucone. Testy cytują ścieżkę natywną, a ścieżki w formie powłoki (korzenie i wyjątki także) mają własne przypadki.

**Jak wykryć:** test wiersza poleceń z `C:\...` bez cudzysłowu; różnica między tym, co hak ocenił, a tym, co wykonała powłoka (log haka obok `bash -c 'echo ...'`).
