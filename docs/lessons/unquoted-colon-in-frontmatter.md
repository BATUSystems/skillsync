---
title: Niecytowany title z dwukropkiem łamie YAML rekordu
tags: [skillsync, yaml, frontmatter, doctor]
paths: ['docs/lessons/**', 'docs/domain/**']
---
Objaw: `skillsync doctor` zwraca `error` „mapping values are not allowed here” dla rekordu lekcji lub domeny.
Przyczyna: wartość frontmattera z `: ` w środku (np. `title: Moq: nieustawiony Task`) nie jest cytowana, więc YAML czyta drugi dwukropek jako nowy klucz.
Dowód: `doctor` na demo-shop (3 rekordy `docs/lessons`/`docs/domain`) i DEMO, scratchpad `g-checks.log` 2026-10-21. Nawrót 2026-11-05 w Skillsync: 3 lekcje z „: ” w `source` (`doctor` FAIL, pełny zestaw testów przechodził), `.skillsync/runs/2026-11-05-lekcje-frontmatter/oracle-red.log`.
Reguła: wartości zawierające `:` (oraz `#` na początku słowa, `[`, `{`) cytuj: `title: "Moq: nieustawiony Task"`; dotyczy każdego pola, także długiego `source`.
Jak wykryć: `skillsync doctor <projekt>`; w Skillsync także test `tests/test_skillsync_lessons_frontmatter.py`; ewentualnie `python3 -c "import yaml;yaml.safe_load(open(plik).read().split('---')[1])"`.
