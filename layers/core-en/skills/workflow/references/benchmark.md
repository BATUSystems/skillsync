# Measuring tools and model effectiveness

An offline suite defines cases with id, argv (a list, no shell), fixture (path → text), cwd,
timeout, expected_exit, expected_output. Each case gets a fresh temporary directory.
argv placeholders: {python}, {skillsync}, {project}. --run executes the commands from the suite.
The operator is responsible for a trusted suite; a fixture is not a sandbox of the process permissions.

Model tasks (in a separate controlled pilot):

| Family | Goal | Criteria hidden from the implementer |
|---|---|---|
| Bugfix | fix of a specific trigger | fail-to-pass + existing pass-to-pass |
| API | client/server change | contract, validation, permissions, errors |
| Data | migration of existing records | backfill, rollback, no loss |
| Full feature | backend + UI interaction | user flow, accessibility, regressions |

Pin the repo/initial state, the workflow version, the model, the client, the environment and the budget.
Run several repetitions, check the correct reference solution and negative controls.
The result tests are independent of the implementer. Measure time, the really available cost/tokens,
fix attempts and the result within budget. A missing cost stays unknown; do not mix currencies/units.
Checkpoint `--cost-unit` defaults to USD; metrics sum each unit separately, with no conversion.
A single attempt or higher instruction coverage does not prove a causal improvement in effectiveness.
