# Tests: details, runner, baseline

Read when planning final tests, on a test failure, or with old failures.

## Selection and execution

First the tests of the changed area, then the required final suite covering all
touched layers (e.g. backend, frontend, build, migrations). `tests.command` from the manifest
is a fallback, not proof of completeness. Do not run the full suite after every edit.
`skillsync smart-test` selects checks and justifies the choice, `validate --profile final`
the final plan. `--run` executes them only after the resource grant. Do not run a second
runner/hook for the same check. Without the CLI apply the same rules manually.

Runner report: `PASS`, `FAIL`, `BLOCKED` or `NOT_RUN`; command, cwd, exit code,
number of executed/skipped tests, a short diagnosis and the log path. Zero collected tests,
missing dependencies, a timeout or a build alone do not mean passed tests.

A negative assertion (something is absent, nothing happened, an empty list) and a smoke test checking a reaction
to an event require evidence that they can fail: a deliberate breaking of the mechanism under test (a mutation on a copy
or with the file restored) or a planted case that overturns them, with the log in the evidence. An assertion that
passes even without the mechanism under test is a gap (`test-gap`), not a pass. A mutation writes
nothing outside the temporary directory; afterwards `git status` shows no changes.

## Failures

On a failure route the fix to the owner of the code/test. Adjusting a test requires
justification by a contract change. A new xfail does not satisfy an acceptance criterion. A new review phase
does not reset the counter of the same defect (limit of 3 cycles in the `feature` core); other independent
steps may continue. With `blocked` give the evidence, hypotheses and the needed decision/access.

### Failure triage

Triage is mandatory after every failed run, also for tests written in this work. Record the table before the fixes:

| Test | Evidence (first error, log) | Diagnosis (product / test / environment) | Confidence (high / medium / low) | Owner (product / `test-writer` / shared) | Next step (fix or decision) |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

Every failure or group with a common cause gets exactly one of the classes: product regression; test defect (e.g. a brittle locator, a wrong assertion, fixture, cleanup); environment or data (e.g. a service unavailable, authentication drift, a collision of shared state). A row is one test, a group may be recorded in one row. A summary sentence that tests do not pass, without evidence and a diagnosis of each item, is unacceptable. Role report: `.skillsync/runs/<run-id>/<step>-test-runner.md` (see [reports](reports.md)); the orchestrator gets a summary.

### Integration and E2E tests written by `test-writer`

The project's unit test conventions (e.g. `TestCase` with rollback and fixtures in Django, isolation mechanisms in .NET) take precedence. The rules below are a pattern for integration and E2E tests:
- Before writing a test, examine the running application or path: real requests and responses. Do not guess locators or data shapes.
- Do not hard-code entity ids and do not rely on seed data; a test creates its own data and cleans it up in `finally`/teardown, also after a failure.
- Timeout and retries are set by the runner configuration, not the test code; tests are deterministic and independent of execution order.
- One scenario per test; the name describes the behavior.

## Pre-push matrix

Before `PUSH SLOT` a package has evidence from the matrix of versions and systems: [prepush-matrix](prepush-matrix.md). The full Linux loop runs with `-v` (without it `scripts/check_skips.py` exits with code 2 when the log has skips), and the local prototype E2E tolerates no skip (as in CI): details in the same reference.

## Baseline and old failures

Compare old failures by test identifiers and causes in the same environment,
not by the number of errors. The base commit does not reproduce earlier uncommitted changes:
account for the baseline. An incomplete baseline limits the conclusion — do not declare no regression
for the whole system. Record known old errors as debt outside the scope.
