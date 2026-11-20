---
title: 'Reguła procedury sprzeczna ze strażnikiem repozytorium: przed wpisaniem kroku do procedury sprawdź strażników testów higieny'
tags: [skillsync, workflow, procedura, git, hygiene, specs, commit]
paths: ['library/skills/feature/**', 'library/skills/pr-run/**', 'library/skills/bugfix/SKILL.md', 'library/instructions/_base.md', 'tests/test_skillsync_repo_hygiene.py']
source: "spec poza publicznym rdzeniem, sekcja Problem: dwa wystąpienia; tests/test_skillsync_repo_hygiene.py:42-45 (`test_no_tracked_specs_or_plans`), :47-61 (odwołania do datowanych plików); library/instructions/_base.md (reguła „GO plan = commit” przed zmianą)"
verified: 2026-11-19
---
**Objaw:** w dwóch pakietach z 18.11 krok „GO plan” zacommitował spec i plan. W pierwszym pełny bieg padł na `test_no_tracked_specs_or_plans` (publiczny rdzeń nie śledzi `docs/specs` ani `docs/plans`), naprawa była `git rm --cached` po fakcie. W drugim blob speca został w historii gałęzi roboczej i zniknął dopiero przy squashu wydania.

**Przyczyna:** procedura (`_base.md`, `feature/SKILL.md`, `plan-large.md` i kilkanaście innych plików) opisywała commit speca i planu jako obowiązkowy krok, a nikt nie sprawdził tej reguły względem strażnika higieny repozytorium. Test strażnika żyje w `tests/`, więc agent wykonujący procedurę go nie widzi, dopóki bieg nie padnie.

**Reguła:** przed wpisaniem kroku, który dodaje pliki do commita (instrukcja, skill, szablon PR, checkpoint), przeczytaj testy strażników repozytorium (`tests/test_skillsync_repo_hygiene.py`) i sprawdź, czy krok nie narusza żadnego z nich. Gdy strażnik zabrania pliku, procedura ma opisać tryb bez tego pliku w commicie, a nie wymagać commita. W rdzeniu trwałość speca daje tryb nieśledzony: reguła `/docs/specs/` i `/docs/plans/` w `.gitignore` i migawka (`skillsync spec snapshot`), nie commit. Nie dodawaj `git add -f` na tych katalogach.

**Jak wykryć:**
- `python3 -m unittest tests.test_skillsync_repo_hygiene` (w CI na każdym PR; lokalnie pomija się bez gita).
- `git ls-files docs/specs docs/plans` musi być pusty w rdzeniu.
- `grep -rn "GO plan" library/` i sprawdzenie, czy każde miejsce z „commit” ma wariant dla trybu nieśledzonego (`specs.tracked: false`).
