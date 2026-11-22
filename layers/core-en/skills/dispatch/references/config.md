# Configuration: the `dispatch` manifest section

Read at the start of a dispatch and when resources, isolation or dispatcher files are missing.

Optional. No section = `/dispatch` works, but ask the user manually about resources, isolation and dispatcher
files.

```yaml
dispatch:
  max_parallel: 3
  worktree_root: ../{project}-wt            # worktree per package: {worktree_root}/{package}
  setup: ["skillsync link {worktree}"]      # commands run by the card
  isolated_resources:                       # assigned permanently per package
    test_db: "app_integration_test_{package}"
    env: { APP_TEST_CONNSTR: "Host=localhost;Database={test_db}" }
  leased_resources:                         # indivisible, through leases
    - { name: e2e, ports: [5500, 4250], databases: [app_e2e], command: scripts/ci/e2e.sh }
  owned_files: ["docs/prompts/README.md"]   # dispatcher files
  additive_files: ["docs/lessons/index.yaml"]
  id_registries: [SEC, ADR, package, e2e_scenario, migration_prefix]
  guards: { min_free_disk_gb: 6, max_load: 12 }
  pulse:                                    # optional dispatcher pulse (opt-in, see pulse.md)
    enabled: false                          # disabled by default
    interval_minutes: 20                    # 1-60 (ScheduleWakeup limit)
    idle_card_minutes: 30                   # a working/busy card silent for at least this long -> STATUS? probe
    user_wait_notify_minutes: 15            # a question to the user waiting at least this long -> PushNotification
    lease_overdue_minutes: 240              # a lease not released for at least this long -> PushNotification
```

Pulse: [pulse.md](pulse.md); the thresholds are computed by `skillsync dispatch --pulse`.
