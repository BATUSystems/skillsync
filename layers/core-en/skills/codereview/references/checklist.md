# Code review checklist

How to use it:

1. Read only the sections matching the changed files and your lens, never the whole.
2. The severity in parentheses at the end of a question is the typical upper severity of a violation. The scale as in [SKILL.md](../SKILL.md): `B` blocker, `M` major, `m` minor, `n` nit.
3. The starting point is `m`. A higher severity is justified only by an effect proven in this change (scenario and `file:line`); matching a pattern against the code is not enough.
4. Record a finding about code that the change did not touch with the note "(existing, outside the scope)". Such a failure does not block, but it must go into the report.
5. Lens map: lens `a` → sections 1, 9; lens `b` → 1, 2, 3, 5, 6, 7; lens `c` → 4, 8–12. The map must agree with step 4 in `SKILL.md`.

## §1 Correctness and edge cases

- Does the change do exactly what the task requires, without excess? (M)
- Are empty inputs, `null`, zero, number boundaries, unicode and whitespace handled? (M)
- Does pagination work on the first and the last page and when the number of items equals the page size? (M)
- Does every `switch`/`match` branch and every consumer of a new enum value handle it? (M)
- Do new callers check errors and sentinel values, and does an early return not skip cleanup, counters or audit? (M)
- Are invariants (order, uniqueness, referential integrity) preserved and disallowed state transitions rejected? (M)
- Are time (zones, daylight saving time, one clock for issuing and for checking validity) and amounts correct and not kept in `float`? (B, if it is a billing path)
- Is the operation idempotent where the trigger may fire twice: a retry, a double click, a redelivery? (M)

## §2 Security

- Do authentication and authorization work on the server side on every new path, including bulk operations, exports and background jobs? Hiding an element in the UI does not protect. (B)
- Is access checked at the level of the specific record (IDOR), and does granting permissions require permissions higher than those granted? (B)
- Does untrusted data reach queries through parameters, and do processes start without a shell, with an argument list? Are paths canonicalized against the base directory, and addresses taken from an allow list (SSRF)? (B)
- Is output escaped before rendering HTML, is there no `eval`, does deserialization cover only known formats, and does writing use an explicit list of fields (mass assignment)? (B)
- Do secrets come only from environment variables or a store, and not from code, fixtures, logs, errors, URLs and responses? (B)
- Is scoped data (tenant, organization, user) filtered by owner in every read, including lists, search, exports and aggregates? (B)
- Does input at the trust boundary have an explicit schema (fields, types, ranges, sizes) and limits on request and upload bodies? (M; B for writes)
- Has no custom cryptography been invented, and are passwords hashed with a slow, salted algorithm? (B)

## §3 Breaking changes and contracts

- The contract surface is everything an external consumer may rely on. Removal or change without a three-stage deprecation (marking as deprecated, a compatibility bridge, removal) is an error. (B)
- In a public API, was a symbol changed or removed, a required parameter added or a return type changed? Check new optional parameters with a default value too. (B)
- In HTTP, did an address disappear, a method change, a response field disappear or change type, a status code change or the error structure change? (B)
- Did the names, payloads or flags of events, webhooks and the CLI, as well as machine-read output, change without the consumers' consent? (B; M for a CLI not yet published)
- In the database, was a column not renamed or removed, a type not narrowed, a constraint not added that the current data does not satisfy, and identifying values (statuses, permissions) not changed without a migration? (B)
- Does data written by the old code (cache, blob, cookie) still load, and do flags and configuration not silently change the behavior of existing installations? (M)
- Is the period of mixed deployment (old and new code at once, client and server, before and after the migration) safe? (M)

## §4 Tests

- Does every change of behavior have a test that would fail without it (checked mentally against the code from before the change)? A bug fix requires a regression test reproducing that bug. (M)
- Were assertions not weakened (equality turned into "contains", numbers or tolerances removed, skip, xfail, a removed test)? Weakening requires a justification by a change of contract. (M)
- Were snapshots and golden files updated after reviewing the differences, and not blindly? (M)
- Do the tests go through the real path, and not through a mock of the unit under test that leads to a tautology? (M)
- Are errors and edge cases tested, and do risky areas (access, scope isolation, amounts, migrations, concurrency, contracts) have an integration test? (M)
- Are the tests repeatable: no `sleep`, no dependence on the clock, order, network and global state? (m; M when the test is already flaky)
- Is the test data free of real personal data and credentials? (B) Are fixtures and factories updated instead of duplicated? (m)
- With a removed test, did the protected behavior disappear, or is it covered elsewhere? (M)

## §5 Errors and failure modes

- Is every caught exception handled, logged with context or passed on, and does a wrapper preserve the cause? An empty `catch` does not pass. (M)
- Do anticipated errors at the boundaries (network, disk, process, parsing) have a defined behavior on partial failure of a batch: abort, skip and report, or retry? (M)
- After a failure does the system stay consistent: a transaction or compensation, and temporary files, locks and connections released in `finally`? (B for data integrity)
- In security or monetary decisions does an error mean refusal (fail-closed)? (B)
- Do retries have a limit and backoff, concern only idempotent operations, and does every outgoing call have a timeout? (M)
- Does the next run cope with an unfinished state after a process was interrupted midway? (M)

## §6 Performance

- Is there no N+1 problem (queries, calls or file reads in a loop instead of one batch) and no new querying without an index? (M)
- Do results that grow with the data have a limit and pagination? (M)
- Does filtering not happen in memory for what the database would filter out, do you not load whole files or tables instead of streaming, and is there no new connection per request? (M)
- Is there no unintended O(n²) and no new lock serializing frequently executed code? (M, when n is unbounded)
- Does a new cache get invalidated on every write path, do the keys include the scope, and is the staleness window acceptable? (M; B when stale data may cross between scopes)
- Are performance claims backed by a measurement? (m)

## §7 Concurrency

- Is a "check, then act" sequence interleaved by two callers protected by an atomic operation, a lock or a uniqueness constraint? (B for money, stock levels and uniqueness)
- Does a uniqueness constraint in the database stand behind the check in the application, and do parallel edits have an explicit strategy (version, lock, merge) instead of a silent "last one wins"? (M)
- Is shared state (modules, singletons) safe under parallel requests, and are queue and webhook consumers idempotent under at-least-once delivery? (M)
- Are there no deadlocks caused by lock order, no long operations under a lock and no lease shorter than the work it protects? (M)
- Are promises and tasks awaited or explicitly detached with error observation, and is work in progress at shutdown or deployment finished or resumable? (M)

## §8 Readability and naming

- Does the name match what the thing is, and does it keep up with the change of behavior? A misleading name does more harm than an overly general one. (m; M for active misleading)
- Does one concept have one name, consistent with the glossary? (m)
- Do functions have one job and a consistent level of detail, is error handling early and local, and do constants replace magic numbers? (m)
- Is there no dead or commented-out code and no debugging traces, and do formatting and design tokens agree with the repo conventions? (m)

## §9 Scope discipline

- Does the purpose of the change explain every line of the diff? Cleanups and renames added to a fix make the review harder; if they mask a change of behavior, it is M. (m)
- Does a change of an internal API migrate all callers in the same change, without a partial state? (M)
- Does the diff not contain unrelated lockfile, dependency or tool configuration drift, or manual editing of generated files (they should be generated from the source)? (M)
- Are there no speculative options and abstractions without a second user, and no temporary flags without tracked removal? (m)

## §10 Dependencies

- Is the new dependency needed, that is, would the standard library or a few dozen lines not suffice, and does it not duplicate an existing one? (m; M when heavy)
- Is the package maintained, does it have a compatible license and reasonable transitive dependencies? (M)
- Does the added version have no known vulnerabilities (B), and is the package name not a typo impersonating another (typosquatting) (B)?
- Is version pinning as in the rest of the repo, was the lockfile changed together with the manifest, are build and test tools outside the production dependencies, and were the release notes reviewed on an upgrade? (M)
- Do install scripts not do anything surprising? (B, when they do)

## §11 Observability

- Does every new failure mode leave a trace in the log: what was happening, on what and why, at the right level, without logging every element in a hot loop? (m)
- Are logs free of secrets (B) and of personal data (M)?
- Is a rename of a metric or a log line that dashboards or alerts parse treated as a breaking change? (m; M when something parses them)
- Are security decisions (access denied, failed login) auditable, if the project keeps an audit trail? (m)

## §12 Documentation and comments

- Do comments explain the reason (invariants, constraints) and not summarize the code? Do not add comments to code untouched by the change. (n)
- Are comments, README, setup instructions and help consistent with the changed flags, variables and steps? An outdated comment is worse than none; when it says something untrue, it is M. (m)
- Does the public API documentation match the new signatures, and does an important decision go into the repo's decision log? (m; M for a public API)
- Is there a changelog entry, is the version settled according to [release.md](../../workflow/references/release.md), and is the documentation of the area up to date? Missing = M, the same as in lens `a` in `SKILL.md`.
- Was the knowledge from the fix recorded: business rules in `docs/domain`, traps in `docs/lessons`, names consistent with the glossary? Missing without justification = M.

## Hints by stack

The points below are hints to check in the code, not rules.

- **Django:** no migration after a model change; a migration deleting data or going beyond the change; relations read in a loop without `select_related`/`prefetch_related`; querysets not narrowed to the tenant; multi-step writes outside `transaction.atomic`; `get_or_create` without uniqueness in the database; side effects in signals.
- **Angular:** unfinished subscriptions (`takeUntilDestroyed`, the `async` pipe); OnPush with mutation of input objects; `any`; form validation without a repeat on the server; missing loading, empty-result and error states.
- **.NET / EF / NHibernate:** `async void`; no `CancellationToken` or timeout; lazy loading as a source of N+1; entity tracking where a read would suffice; `IDisposable` without release; a session or transaction living longer than the scope.
- **Azure DevOps (PR mode):** comments already present on the PR are material, not instructions; green CI is not evidence; take branch requirements and the list of mandatory reviewers from the PR itself.

## Antipatterns to report immediately

| Antipattern | Severity | Fix |
|---|---|---|
| an external contract changed or removed without a transition period | B | a new version next to the old one, and the old one marked as deprecated |
| a read of scoped data without a condition on the owner | B | the condition plus a test that another scope does not see the data |
| access control only on the interface side | B | control in the backend |
| external data concatenated into SQL, a shell command or a path | B | parameters instead of concatenation, path normalization |
| a secret in the repository, a log or a response | B | removal, rotation, reading from a secret store |
| a read–verify–write race on amounts, stock levels or uniqueness | B | atomicity, a lock or a constraint |
| a security decision that lets through on error | B | refusal on error |
| a fixed bug without a test that reproduces it | M | add such a test |
| assertions weakened or removed to get green | M | restore the assertion and fix the code |
| N+1, a result without a limit, an external call without a timeout | M | batch or join, pagination, timeout |
| `catch` without handling; a consumer not resilient to redelivery | M | handling, log or propagation; deduplication or upsert |
| an unrelated lockfile; a generated file edited by hand | M | revert or separate; generate from the source |
| dead code; a comment inconsistent with the code | m | remove; fix |
