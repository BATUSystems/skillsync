# Rozszerzenia workflow v2

Czytaj, gdy zadanie jest współdzielone, dotyka integracji/UI/CI albo trackera.

Skille `task-claim`, `project-guard`, `ci-stabilize`, `tracker-workflow`, `workflow-upgrade` i `skill-profiles`
należą do profilu `workflow-extended` — używaj ich tylko, jeśli są zainstalowane.
Przy współdzielonym zadaniu użyj `task-claim` przed zapisem i renew/release w trakcie/po.
Przed integracją poznaj recepturę `test-env`; UI testuj przez `observed-e2e` na faktycznej
obserwacji. W planie wskaż skonfigurowane `project-guard` i zasoby. `ci-stabilize` prowadzi
ograniczone próby po porażce. `tracker-workflow` obsługuje tylko autoryzowane operacje
zewnętrzne. `workflow-upgrade` i `skill-profiles` dobieraj przy zmianach workflow.
Nie uruchamiaj wszystkich narzędzi w drobnym zadaniu; brak konfiguracji raportuj jawnie.
Kontrakty: [workflow v2](../../workflow/references/eight.md).
