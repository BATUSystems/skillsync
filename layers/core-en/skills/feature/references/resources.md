# Resources and parallelism

Read before assigning steps in parallel or running tests in several roles.

Parallelism needs **independent dependencies, files and resources**, not just different paths:
- One writer per file, including indexes, routing, translations and lockfiles.
  A new file outside the scope needs agreement with the orchestrator first, not a report afterwards.
- By default one active test process per shared database, port and build/coverage directory.
  This includes the tests of `test-writer`, the implementer and hooks. Parallel tests only after
  the isolation of all these resources is confirmed. A separate worktree isolates neither the database nor the ports.
- In this workflow the tests are run by the assigned agent, not by the automatic `Stop` hook.
  `skillsync` removes its own test hook in projects with `feature`; it leaves other hooks alone.
  Before testing, check that the remaining hooks do not run the same suite too.
- An agent without a resource grant reports `NOT_RUN` and hands the command to the runner. It does not wait
  forever and does not run tests on the default database "as a trial".
- In a session with a dispatcher, full test runs and mutations, also those delegated to subagents, start only after
  a `LEASE GRANT` for the resource they occupy (suite, build, port, database). The assignment gives the grant or forbids
  such runs; targeted tests (one module or one test class) on a private TMPDIR need no lease ([multiple sessions](multi-session.md)).
  A run of several modules (hundreds of tests), through `discover` or outside `test-run`, is a wide run: it needs a `LEASE GRANT`,
  and the assignment for a subagent forbids it outright when there is no grant.
- Do not change files covered by a verification in progress. Pause writes during the final
  tests and review; a change of the tested state requires a refreshed result.
- Settle the order of migrations and the backend/frontend contract before a parallel implementation.
- Several working **sessions** with a dispatcher (not the subagents of one session): resource leases,
  PUSH SLOT, assigning identifiers (e.g. SEC-*, ADR, E2E scenarios) only by
  the dispatcher; see [multiple sessions](multi-session.md).
