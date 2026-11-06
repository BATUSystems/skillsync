---
title: Rekordy bootstrap i wczesne ładowanie glosariusza domenowego
tags: [skillsync, knowledge, domain, terminology, routing]
paths: ['skillsync_workflow/quality.py', 'tests/test_skillsync_workflow.py']
source: "Kontrakt wdrożony w Skillsync, plan docs/plans/2026-10-27-domain-glossary-bootstrap.md"
verified: 2026-10-27
---

## Reguła

Wpis indeksu domenowego może mieć opcjonalne `load: bootstrap`. Router `skillsync route`
zwraca taki rekord w każdym wyniku niezależnie od tematów i ścieżek. Rekordy bez `load`
zachowują dotychczasowe dopasowanie; `doctor` akceptuje brak pola albo dokładnie `bootstrap`,
a inne jawne wartości zgłasza jako error.

## Zastosowanie

Oznaczaj tak krótki `docs/domain/glossary.md`, który agent ma przeczytać przed interpretacją
ticketu i wyborem plików. Router zwraca metadane i ścieżkę; agent sam odczytuje treść. Szczegóły
pozostają w rekordach tematycznych. Definicje wymagają źródła, a kandydaci pozostają w planie
do potwierdzenia.

## Konsumenci i dowody

- Kod: `skillsync_workflow/quality.py` (`knowledge_indexes`, `route`, `doctor`).
- Testy: `tests/test_skillsync_workflow.py`, kontrakty routingu i walidacji `load`.
- Instrukcje: biblioteka `instructions/_base.md` (rdzeń dla wszystkich hostów),
  `library/skills/workflow/references/knowledge.md` i `library/skills/feature/SKILL.md`.
- Nazwa „bootstrap” nie dotyczy tematu routingu `bootstrap` (test-env); to osobne mechanizmy.
- Propagacja instrukcji/skilli: task VS Code `skillsync: link` wykonuje
  `skillsync link ${workspaceFolder}` przy `folderOpen`. Nie jest to task `skillsync: sync`.

