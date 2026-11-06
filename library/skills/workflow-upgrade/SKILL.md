---
name: workflow-upgrade
description: Plan revisioned workflow artifact upgrades with three-way conflicts,
  local-edit preservation, backups and guarded rollback.
---

# workflow-upgrade

Źródłem jest biblioteka. Zdefiniuj workflow.upgrade.revision i artifacts source/target.
`skillsync upgrade .` pokazuje decyzje i trzy hashe: base/local/incoming. Pierwsza adopcja
innego istniejącego pliku jest konfliktem, a równoczesne zmiany obu stron nie są nadpisywane.
Apply: --action apply --run; backup przed zapisem, pending chroni przerwaną transakcję.
Rollback: --action rollback --run [--backup ID] nie nadpisuje późniejszych lokalnych edycji.
Targets ograniczone do providers/scripts/docs workflow. Nie migruj tym kodu aplikacji,
_base.md ani plików generowanych AGENTS/TOML. Te ostatnie aktualizuje skillsync link.
Zweryfikuj końcowe pliki i ponowne linkowanie. Przy konflikcie zachowaj lokalne dane.

Kontrakty i przykłady: [workflow v2](../workflow/references/eight.md).
