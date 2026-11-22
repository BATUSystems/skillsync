# Review: material for the reviewer and scope of the check

Read before preparing a review assignment.

The orchestrator prepares for the `reviewer` the full material in files: the working and staged diff,
a list of new files (the agent reads their content directly), the baseline, the plan, the FRD file indicated in the plan, the spec from the `Spec:` line and the test logs. With an `approved` spec first compare `Spec-sha` ([spec](spec.md)).
It passes paths, not only the implementer's summary. The reviewer works only
in the read mode defined by the adapter. A missing material is reported as `BLOCKED`;
the orchestrator provides it without widening the role's permissions.

The reviewer checks correctness and criteria, not only conformance with the plan — the plan too may be
wrong. It also checks whether the documentation matches the new behavior and whether the reported discoveries
were persisted; a lack without justification is a blocking finding. It verifies contracts between
layers and the risks specific to the change. For UI include running the most important path
in a browser if tools are available; record the lack of such verification explicitly.
Select the checklist: `skillsync review <projekt> --risk <low|medium|high> --files <paths>`
and [review/QA](../../workflow/references/review.md). The CLI does not issue a review verdict.

Code review goes through the [`kod` loop](review-loop.md): findings → fixes by the appropriate agent → tests of the touched areas
(broader when a shared contract changed; in the final loop the final suite) → the next round in a new `reviewer` subagent,
which checks the closure of findings. The diff scope in rounds (full or the fixes with touched files) is set by the loop according to
plan risk. `verifier` only for targeted claims outside the loop (e.g. "line X changed").
Without changes do not repeat passed checks. A new write invalidates the corresponding part of the verification.
