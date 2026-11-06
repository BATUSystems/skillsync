---
title: Po przeniesieniu generatora zostają stare ścieżki
tags: [skillsync, documentation, relocation]
paths: ['skillsync-windows.ps1', 'tests/windows/**', 'docs/**']
---
Dowód (2026-10-02, po wydzieleniu generatora z `~/.local/scripts/dev/`): odniesienia do `dev\skillsync`/`dev/skillsync` zostały w `skillsync-windows.ps1` (komentarz i komunikat), `tests/windows/skillsync-windows-check.ps1`,
w skillach i instrukcjach biblioteki (`library/skills/workflow/references/configuration.md`, `_base.md`: „repo scripts”), w `workflow/lessons/index.yaml`
oraz w `workflow/projects/BATUSystems-scripts.yaml` (kontrole `skillsync-unit` i `workflow-doctor` na `dev/skillsync`) — wszystkie poprawione w tym samym zadaniu po przeglądzie grepem.

Reguła: po przeniesieniu lub przemianowaniu narzędzia przeszukaj ścieżkę/nazwę w: repo (kod, testy, skrypty Windows), bibliotece (`skills/`, `instructions/`, `workflow/`), manifestach projektów i dokumentacji
(`grep -rn 'dev/skillsync' ~/.local/copilot-library ~/Repos`), a wynik wprowadź w tym samym zadaniu. Komunikaty użytkownika w skryptach to też dokumentacja.

Dotknięte obszary: skrypty Windows, skill `skillsync`, `_base.md`, `library/skills/workflow/references/configuration.md`, konfiguracje `workflow/projects/*`. Granice aktualności: lista konkretnych plików odzwierciedla stan z dnia przeniesienia — sprawdź ponownie grepem.
