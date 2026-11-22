# The `demo` ceremony

Read when choosing the scope, when `skillsync route <project>` returns `ceremony.value: demo` or the plan has the line `Ceremony: demo`.
The mode is for short demonstration runs: it lowers the cost of the process, it does not lower the control of access boundaries.

## Reading

- The value is the field `ceremony.value` from `skillsync route <project>` (the manifest key `workflow.ceremony`: `full` or `demo`).
- No `ceremony` field in the answer (an older `skillsync`) or `source: default` = `full`.
- `source: invalid` (an invalid value in the manifest) = a question to the user, as with `delivery`; do not assume `full` or `demo`.
- With `demo` the plan has in its header the line `Ceremony: demo` (like `Handoff:`, a separate row); with `full` the line is optional.
  A mismatch between the plan line and `route` at “GO implement” = a question to the user, without guessing.

## After the spec and review: the next step is a prototype

With `demo` finishing the spec and the `spec` review is not a reason to idle waiting for “GO plan”. When the spec has screens or
UI sections, the next step is a prototype (the skill `prototype`, [plan-details](plan-details.md#prototype-gate)): build it right away,
prepare a plan draft `draft (sketch — waiting for GO plan)` and stop only at the prototype acceptance (STOP, `skillsync prototype wait`).
Without screens in the spec there is no prototype: ask for “GO plan” right away. Consents are still given only by the user; a prototype is not “GO plan”.

## What `ceremony: demo` changes

- **`code` review**: `demo` changes the number of loops: one `code` review for the whole run (one round at the end, majors closed by a `verifier`) PLUS a full loop
  ([review-loop](review-loop.md#loop-category-and-transitions)) for every step with a leak guard, a migration or security. In `demo` a single-round `code` review has **at most 2 rounds**: the second only at a blocker, at at least 3 majors, when the fixes touched the leak guard, migrations or security (including permissions) or after a second `REFUTED` of the same finding; otherwise majors are closed by the `verifier`, and there is no third round (further findings: a fix and a `verifier` or a record in the “Debt”). A second round for these reasons is not `ceremony-excess`. Full loops (steps with a leak guard, a migration, security) stay unchanged in `demo`, also the limit of 4 rounds.
  The final loop remains full when the whole change belongs to a category from the section “Loop category and transitions” ([review-loop](review-loop.md#loop-category-and-transitions)). All three categories from that section stay unchanged; `demo` does not weaken them.
- **Test-writer**: only in phases with permissions or data isolation; in the others the unit tests are written by the phase `implementer`.
- **Documentation**: limited to the changelog and the “Result” of the plan.
- **Deferred**: `retro-quality` and the knowledge records (`docs/domain`, `docs/lessons`) move to the subsection “Debt (ceremony: demo)”
  in the “Result” of the plan: a list of deferred records (topic, source, proposed owner of the write). The debt is repaid by a human or the next `full` run.
- **Unchanged**: tests, the settled version, the `--final` record, closing the plan and loops according to category. The plan ends with `done` with the
  subsection “Debt (ceremony: demo)” filled in. The relaxation in the “Done” rule of the core applies only to documentation beyond the changelog and the “Result”,
  `retro-quality` and the knowledge records. `skillsync checkpoint --final` then reports the warning `retro-quality-missing`; it is expected and does not block.
  The debt replaces `retro-quality` also in the list of conditions before the state “waiting for READY/CP”.
- **The `docs` profile** (a documentation-only change, [review loop](review-loop.md)): one round also in `demo`; the second only on the conditions of the second round above.
