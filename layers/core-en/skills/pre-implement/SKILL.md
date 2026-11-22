---
name: pre-implement
description: "Readiness gate before risky changes (contracts, API, data, migration): checks real consumers, rollout, tests; verdict READY/NEEDS_CHANGES/BLOCKED. Read-only."
---

# Readiness for implementation

A gate before a risky change: contract, API, data, migration, high risk. Input: the plan, and when the plan has a `Spec:` line also the spec; the nearest module instructions; the contract rules ([contracts](../workflow/references/contracts.md)).

Structural check: `skillsync readiness <project> --plan <file> --risk high --files <paths>`, covers the plan sections and the places of change. The report alone does not excuse you from reading the consumers.

Procedure in this order:
1. Define the goal, the criteria and the minimal scope; indicate who actually benefits from this.
2. API, DTOs and events: client compatibility; rollout with the old and the new version coexisting.
3. Existing data: migration, transactions, rollback, permissions, concurrency.
4. Every risk has its own check or test; record gaps and resolutions explicitly.
5. Verdict: `READY`, `NEEDS_CHANGES` or `BLOCKED`, with evidence in the form file:line. Do not add tests that do not exist.

Severity of findings: `Critical` blocks the start; `Warning` requires a compatibility bridge (alias, double emission, re-export). Group gaps: blocking, important, optional.

Executor and report: a read-only subagent (`reviewer` with the pre-implement brief), commissioned by the orchestrator. The report
is written by the orchestrator (the role has no Write) in `.skillsync/runs/<run-id>/pre-implement.md`, and with several gates in a run
`pre-implement-<step>.md`; the first line of the report is `Verdict: READY|NEEDS_CHANGES|BLOCKED`. The status page and tools
recognize the report by the name `pre-implement*`.

Read-only mode: do not change the plan, the spec or the code; propose changes in the report. Writing the conclusions belongs to the orchestrator: contracts, migration and risks go to the spec, if it exists, before the `spec` loop starts and the GO plan consent, because after that `Spec-sha` protects the content; otherwise — to the plan.

For a large change apply [plan-large](../feature/references/plan-large.md). When an ordinary plan reveals a contract or data, ask the question about a spec instead of widening the scope yourself. A small, local change is satisfied by a short note in the plan; do not create a spec when the `plan-large` gate does not require it.

New consent is required only for going beyond the authorized scope.
