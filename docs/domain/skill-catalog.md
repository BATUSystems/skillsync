---
title: Katalog skilli, INDEX.md i ostrzeżenia doctor
tags: [skillsync, skills, catalog, doctor, organizations]
paths: ['skillsync_workflow/catalog.py', 'skillsync_workflow/quality.py', 'skillsync']
source: odczyt skillsync_workflow/catalog.py:79 (render_index), :129-131 (index_cache_path), :134-144 (cmd_index, --check), quality.py:doctor; skillsync:3687-3691 (flagi skills index); tests/test_skillsync_catalog_map.py 2026-10-21; plan docs/plans/2026-10-21-agent-optimization.md (G3)
verified: 2026-11-05
---
Reguła: `workflow/skill-catalog.yaml` (biblioteka, `version: 1`) opisuje każdy skill: `scope` (org|client|project|tech|workflow|tool|personal), opcjonalnie `org` i `tech[]`,
oraz `orgs.<org>: {skill, projects: [wzorce fnmatch]}`. `skillsync skills index` generuje z niego indeks do pliku cache `<cache>/skills-index/INDEX-<ids>.md` albo na stdout (`--stdout`); indeks nigdy nie jest zapisywany do warstwy. `--check` zwraca exit 1, gdy plik w cache jest nieaktualny. Skill bez wpisu trafia do „Nieskatalogowane”.
`doctor` (gdy katalog istnieje) wystawia wyłącznie `warning`: „Skill of another organization”, „Missing organization skill”, „Technology not detected”, a przy błędnym katalogu jeden „Invalid skill catalog: …”.
`suggest` (tylko odczyt) zwraca `add`/`remove`/`reasons`; `add` zawiera skill organizacji oraz skill `tech` tylko, gdy żaden skill projektu nie pokrywa wykrytej technologii.

Dlaczego: skill cudzej firmy nie może wnosić kontekstu do projektu, a ręczny audyt manifestów jest kosztowny.

Wyjątki i pułapki:
- Organizacje projektu to wszystkie `orgs` pasujące do `project.name`; bez `project.name` używany jest `<katalog-nadrzędny>/<katalog>` (dlatego katalog zawiera też wzorce dla worktree, np. `<projekt>-wt/*`). Bez dopasowanej organizacji brak ostrzeżeń o organizacjach.
- Technologie bez detektora (`sqlserver`, `web`) nie dają ostrzeżenia. Detektory: dotnet, angular, django, capacitor, hugo (konfig tylko obok `content/` lub `themes/`), rust, python, node; głębokość ≤ 4, pomijane `node_modules`, `.git`, `bin`, `obj`, `.venv`.
- Skill klienta bywa jednocześnie skillem organizacji o tej samej nazwie (`scope: client` z `org: <org>` i wpis `orgs.<org>`).
- Opis skilla > 200 B (UTF-8) to osobny `warning` doctora.

Gdzie w kodzie: `skillsync_workflow/catalog.py` (`load_catalog`, `catalog_findings`, `suggest`, `render_index`), `skillsync:cmd_skills_index`.
