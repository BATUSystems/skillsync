---
title: Rozwijanie listy skilli (alias, towarzysze, tagi, profile)
tags: [skillsync, skills, feature, expansion]
paths: ['skillsync', 'skillsync_workflow/common.py', 'skillsync_workflow/maintenance.py']
source: odczyt skillsync:expand_skills i skillsync_workflow/common.py:expand_project_skills; tests/test_skillsync_dispatch.py, skill-profiles.yaml 2026-10-21
verified: 2026-10-21
---
Reguła: `manifest.skills` jest rozwijane do jednej listy wspólnej dla wszystkich hostów (Copilot, Claude, Codex, Cursor): nazwy, `@tag` (z `config.tags`), wildcardy `*`/`?`, skille profili,
alias `agentflow` → `feature` (gdy `feature` istnieje w bibliotece) i — gdy lista zawiera `feature` lub `agentflow` — towarzysze `WORKFLOW_COMPANIONS`
(rdzeń: `workflow`, `pre-implement`, `smart-test`, `bugfix`, `codereview`; tylko istniejące w bibliotece). Pozostałe skille workflow (`workflow-retro`, `task-claim`, `workflow-upgrade`,
`skill-profiles`, `project-guard`, `dispatch`, `ci-stabilize`, `tracker-workflow`) dostarcza profil `workflow-extended`, włączany w manifeście.

Dlaczego: jeden skill-wejście (`feature`) ma zasilać cały pakiet workflow bez wypisywania go w każdym manifeście; alias zapewnia ciągłość po zmianie nazwy `agentflow` → `feature`.

Wyjątki i przykłady:
- **Dwie niezależne implementacje**: `skillsync:expand_skills` (widzi każdy katalog biblioteki) i `skillsync_workflow/common.py:expand_project_skills` (wymaga `SKILL.md`, czyta `tags` z configu).
  Stałe i lista towarzyszy są zduplikowane; entry ładuje pakiet tolerancyjnie. Zmiana reguł = obie implementacje; zgodność pilnują `SharedConstantsTests` i `ExpandSkillsTests`.
- Tagi pochodzą z `~/.config/skillsync/config.yaml` (per maszyna), nie z biblioteki.
- Profile (`workflow.skill_profiles.selected`, domyślnie `core`, `testing`; dawny `delivery` wchłonął `workflow-extended`) dodają skille do jawnej listy; `selected: []` to opt-out. Profil z nieistniejącym skillem → błąd.
- `doctor` ostrzega o `agentflow` w `skills:` (przestarzałe), ale nadal go akceptuje.

Gdzie w kodzie: `skillsync:expand_skills`, `skillsync:has_feature`, `skillsync_workflow/common.py:expand_project_skills`, `skillsync_workflow/maintenance.py:resolve_profile_skills`.
