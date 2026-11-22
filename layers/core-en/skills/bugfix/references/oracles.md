# Regression oracles

Read before writing a regression test, that is in step 3 of the `bugfix` skill. Choose the family matching the defect and use the project's runner and conventions.

**Overriding rule: the oracle fails on the code before the fix and fails for the right reason.** Show the failure in the report: command, exit code, message. The oracle cleans up the data it created.
Oracle families:
- **data scope (tenant/organization/user):** at least two scopes; access allowed, denial, scope `null`, “all”, wildcard; a missing or wrong scope never widens access (fail-closed).
- **atomic write:** a forced failure between stages; after it there is no partial data and no premature side effects (event, cache, index).
- **concurrent conflict:** an update on the current version passes; a stale update, deletion or action gets a structured conflict that the UI shows the user.
- **a field in the full cycle:** create, reload, edit, clear to `null`, reload; at every step the request, storage, response and UI agree.
- **runtime environment:** the same configuration and service behavior in the server, CLI, worker/queue and browser; when the bug occurs in only one place, a test on the image or the installed package.
- **cache and read after write:** a read right after a write consistent with the adopted consistency model; waiting for convergence with a time limit, without an arbitrary `sleep`.
- **migration and existing data:** a forward migration on data in the old shape, and backward if the project supports it; nothing is lost; the change covers only the entities being fixed.
- **no automated test (configuration, environment, UI):** do not fabricate an oracle; instead manual evidence (steps, expected and actual result, code state), the `LOW_CONFIDENCE` marker and an explicit limitation.
- **integrations:** duplicated, parallel, retried deliveries, after a timeout or from the wrong party; correct idempotency, cursor state and mappings; no secrets in logs and fixtures.

Stacks (project conventions take precedence):
- Django: a test in a `TestCase` with rollback; for multi-tenant data the equivalent of `TenantTestCase`.
- .NET: a test in an isolated transaction or on an in-memory database, according to the project's convention.
- Angular: a component or service test with a mock of the boundaries only (HTTP), never of the unit under test.

Rules:
- A green result must not come from weakening assertions, `sleep`, suppressing an error, changing generated files or installed dependencies.
- The fix hits the real call site, not a wrapper of the symptom; no refactors outside the scope of the fix.
- When it is unknown whose scope this is (tenant, organization, account, user), the code denies before it reaches for data.
