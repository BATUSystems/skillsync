# Konfiguracja: sekcja `dispatch` manifestu

Czytaj przy starcie dyspozycji i gdy brakuje zasobów, izolacji lub plików dyspozytora.

Opcjonalna. Brak sekcji = `/dispatch` działa, ale o zasoby, izolację i pliki dyspozytora
pytaj użytkownika ręcznie.

```yaml
dispatch:
  max_parallel: 3
  worktree_root: ../{project}-wt            # worktree per paczka: {worktree_root}/{package}
  setup: ["skillsync link {worktree}"]      # komendy wykonuje karta
  isolated_resources:                       # przydzielane na stałe per paczka
    test_db: "app_integration_test_{package}"
    env: { APP_TEST_CONNSTR: "Host=localhost;Database={test_db}" }
  leased_resources:                         # niepodzielne, przez dzierżawy
    - { name: e2e, ports: [5500, 4250], databases: [app_e2e], command: scripts/ci/e2e.sh }
  owned_files: ["docs/prompts/README.md"]   # pliki dyspozytora
  additive_files: ["docs/lessons/index.yaml"]
  id_registries: [SEC, ADR, package, e2e_scenario, migration_prefix]
  guards: { min_free_disk_gb: 6, max_load: 12 }
```

