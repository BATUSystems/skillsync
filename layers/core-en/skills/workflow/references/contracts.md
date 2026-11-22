# Contract check before implementation

The scope is chosen by risk, not by the urgency of the ticket. For high risk or contracts, check:

- Public API/DTOs, data formats, event names and their consumers; a missing/changed field,
  HTTP status, nullability, idempotency and compatibility of the client with the old/new version.
- Existing data: backfill, schema migration, deploy before/after the migration, rollback.
- Permissions, tenant/organization scope, validation of untrusted input.
- Transactions, concurrency, retries, cache and the order of side effects.
- A check of the changed boundary in a real flow, not only a unit test of a helper.

Every conclusion refers to a real file/consumer. A lack of sufficient evidence
means a readiness gap. The CLI check verifies structure; review judges truthfulness.
A product contracts document may be named in workflow.routing of the local manifest.
