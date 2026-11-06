---
title: Rozwijanie skilli ma dwie implementacje
tags: [skillsync, skills, expansion, tests]
paths: ['skillsync_workflow/cli.py', 'skillsync_workflow/common.py', 'tests/test_skillsync_dispatch.py']
---
Dowód: `skillsync_workflow/cli.py:expand_skills` (generator; widzi każdy katalog biblioteki) i `skillsync_workflow/common.py:expand_project_skills` (workflow; wymaga `SKILL.md`, czyta `tags` z configu) rozwijają `skills:` niezależnie.
Lista towarzyszy `WORKFLOW_COMPANIONS` i stałe `WORKFLOW_SKILL`/`LEGACY_WORKFLOW_SKILL` są zduplikowane, bo entry ładuje pakiet tolerancyjnie (`except ModuleNotFoundError`, starsze instalacje Windows).
Zmiana tylko jednej strony daje rozjazd: `link` instaluje inny zestaw skilli niż ten, który waliduje `doctor`.

Reguła: każdą zmianę reguł rozwijania (alias, towarzysze, tagi, wildcardy, profile) wprowadzaj w obu implementacjach i uruchom `tests/test_skillsync_dispatch.py`
(`SharedConstantsTests`, `ExpandSkillsTests`) — pilnują zgodności stałych i wyników. Nie wprowadzaj importu pakietu na poziomie modułu w `skillsync_workflow/cli.py` tam, gdzie dziś jest ładowany tolerancyjnie.

Dotknięte obszary: `skillsync_workflow/cli.py`, `skillsync_workflow/common.py`, `skillsync_workflow/maintenance.py` (profile). Granice aktualności: do czasu wydzielenia jednej wspólnej implementacji.
