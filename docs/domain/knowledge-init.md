---
title: Szkielet dokumentacji projektu (knowledge init)
tags: [skillsync, knowledge, docs, lessons, domain]
paths: ['skillsync_workflow/knowledge.py', 'skillsync_workflow/quality.py']
source: odczyt skillsync_workflow/knowledge.py, quality.py:knowledge_locations; tests/test_skillsync_knowledge_init.py 2026-10-21; plan zadania (poza rdzeniem) (G6)
verified: 2026-10-21
---
Reguła: `skillsync knowledge init <projekt> [--dry-run]` tworzy tylko BRAKUJĄCE `docs/domain/index.yaml` (`entries: []`), `docs/lessons/index.yaml` (`lessons: []`) i `docs/README.md`;
istniejących plików nie nadpisuje (zapis w trybie `x`). Zwraca JSON `{created, existing, skipped}`. `doctor` w projekcie z `feature` ostrzega „Missing lessons index” (analogicznie do indeksu domeny).

Dlaczego: pierwszy zapis wiedzy nie powinien wymagać ręcznego tworzenia indeksów; `route` i `doctor` zakładają ich format.

Wyjątki: lokalizacje wyznaczają `workflow.knowledge.domain` (np. `../docs/domain`) i `workflow.knowledge.lessons` (kończy się `docs/lessons`, domyślnie obok domain); `docs/README.md` trafia do katalogu `docs/` domeny.
`route` czyta lekcje z tej samej lokalizacji. Pliki powstają nieśledzone — commit w repo projektu.
Lista katalogów (`workflow.knowledge.domain: [docs/domain, ../repo-a/docs/domain]`): tworzone są tylko pliki wewnątrz korzenia projektu, pozostałe trafiają do pola `skipped` wyniku (`{path, reason}`); forma tekstowa bez zmian, także dla wspólnego katalogu nadrzędnego. `docs/README.md` powstaje przy pierwszym katalogu domeny w projekcie. Opis listy: `docs/workflow.md`, „Katalogi wiedzy”.

Gdzie w kodzie: `skillsync_workflow/knowledge.py:init_knowledge`, `quality.py:knowledge_locations`.
