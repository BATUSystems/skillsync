---
name: smart-test
description: Select affected checks from real git changes, explicit path rules and module dependencies; use conservative fallback and the shared validation runner.
---

# Tests of the changed area

`skillsync smart-test <project> --base <real-base>` plans the tests and justifies the choice.
Without --base it analyzes staged/unstaged/untracked; it does not guess the base from the upstream of a feature branch.
`--files <paths>` serves an explicit scope. `--run` executes the plan through the shared runner; run the tests of a plan step through `skillsync test-run --run <id> --step <id> -- <command>` ([test-run](../workflow/references/test-run.md)).

The mapping is in `workflow.testing.rules`, the module dependencies in the project configuration.
Shared files, deletions/unknown scope and the lack of a certain map require a conservative fallback.
Read `uncovered` to complete the mapping (rules/modules) instead of living on the full fallback; `test_only`/`direct_files` (with `testing.test_paths`) are a hint, not a change of the selection. `summary` after `--run` counts checks, not tests (`tests_counted: null` = no data, do not infer). Review the report: the cache stores the plan, not a confirmation of tests. A new state of code,
configuration or database invalidates the selection. An empty list does not mean success.

Run tests only with a grant of databases/ports/build. A missing environment gives BLOCKED;
zero executed tests or only skips do not give PASS.
After a change of a shared contract and before finishing run the required wider final profile.
