# Subagent reports: file + summary

Read when writing an assignment for a subagent and when receiving a report.

## Path

Full report: `.skillsync/runs/<run-id>/<step>-<role>.md` (`.skillsync/` is ignored by git).
`<run-id>` is the plan/task slug, `<step>` the plan step number. The path is given by the assignment.

## Summary for the orchestrator (≤ 15 lines)

1. status: `PASS` / `FAIL` / `BLOCKED` / `NOT_RUN` / `CHANGES_REQUIRED` (always, also on a failure);
2. changed files (a list or a count plus the directory);
3. blockers (explicitly, also "none");
4. the number of "Discoveries";
5. the path of the full report.

The summary must not hide failures: status and blockers are always in the summary.

## Roles with write access and read-only roles

- Roles with Write (`implementer`, `test-writer`, `test-runner`, `scribe`, `mechanic`) write the full report
  to a file and return a summary.
- Read-only roles (`explore`, `reviewer`, `verifier`) have no Write. Two permitted paths:
  (a) the orchestrator writes their full report to a file if it is needed later (e.g. a change map,
  a list of findings); (b) the role returns a summary plus a details section limited to ≤ 60 lines.
  The assignment says which path applies.
  A review-loop reviewer's report is the record's evidence: always write it (a), right after it returns, together with `<step>-N-decyzje.md` ([recording](review-loop.md)), not after the fact.

Exception to the write scope: `mechanic` (Edit + Write), `test-runner` (writing via Bash to logs) and `scribe`
(`docs/**`) may write the report only to `.skillsync/runs/<run-id>/` — nothing outside that directory
(`mechanic` has Write only for this purpose).

## When the orchestrator reads the full report

Only on `FAIL`/`BLOCKED`/`CHANGES_REQUIRED`, with the number of discoveries > 0 and before review
(material for the reviewer). Before ticking a step it checks the actual files and command results anyway.

## Discoveries and the relationship

"Discoveries" (domain knowledge, outdated documentation, pitfalls; each with a source: file:line,
ticket, data, user answer) are in the full report, and the summary has their count.
Report project-specific terms as confirmed definitions with a source or as
candidates with a hypothesis of the meaning and a question to clarify. Do not present a guess from a class
name as an established business meaning; before work read the bootstrap records you were given.
Subagents do not read the relationship file or the personality extension; this applies only to the main session.
