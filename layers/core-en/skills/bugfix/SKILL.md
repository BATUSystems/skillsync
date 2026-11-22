---
name: bugfix
description: Small bug fix with root cause proven from evidence, a narrow patch, a regression test and independent review; larger changes go to feature.
argument-hint: '<bug description | KEY-123>'
---

# Bugfix — a small, verified bug fix

The procedure covers checking the report, a brief with a cause based on evidence, a regression test red before the fix, a minimal change, checks, knowledge, changelog, the review loop and the closing of the run. When the change grows, you move to `feature`.

## Procedure

1. **Step 1 — state of the problem.** Check whether the bug still occurs, whether there is no fix or PR already, as well as the working state of the repo and the open-plans gate ([feature](../feature/SKILL.md#reconnaissance-and-plan), step 1). Distinguish reproducing the bug from a conclusion drawn from the code. A lack of reproduction is an explicit limitation, not a closing of the report.
2. **Step 2 — brief.** Write the chain trigger → code path (`file:line`) → wrong result, with the scope, the expected behavior, the regression test and the risks. State the confidence of each thesis about the cause explicitly: reproduced or read from the code. Weak confidence means `LOW_CONFIDENCE`, no widening of the scope and a question to the user. A contract change routes to `pre-implement`. Subagents get a report path in the brief and hand back a summary of 15 lines ([reports](../feature/references/reports.md)); they do not read the relationship file.
3. **Step 3 — regression test (oracle) before the fix.** Before the fix the test must be red for the right reason and visible on the starting code; show the failure in the report ([oracle](references/oracles.md)). If an automated test is not feasible (environment, configuration, UI), note the limitation, assign `LOW_CONFIDENCE` and attach a manual verification (steps and result). Do not fabricate a red test. Then the implementer removes the source of the defect, not its effect, and corrects the existing test; assertions stay strong, and `sleep` and silencing errors are out.
4. **Step 4 — checks.** The runner launches `smart-test` and the final profile, if required; the step's tests go through `skillsync test-run --step` ([test-run](../workflow/references/test-run.md)).
5. **Step 5 — knowledge.** The `scribe` role records where the bug came from and which rule it concerned: in the documentation of the area and in `docs/domain` (business rule) or `docs/lessons` (technical pitfall), according to [knowledge.md](../workflow/references/knowledge.md). The reviewer checks the write.
6. **Step 6 — changelog and version.** A changelog entry per [release.md](../workflow/references/release.md); you propose a number (most often patch) and ask the user about the version. Without an answer the number does not change. When the report came in by key, consent was already given by “GO implement” (the version recorded in the brief) and you do not ask a second time. Condition for entering the loop: before its first round the diff contains the documentation, the changelog and the agreed version, and the final profile (step 4) passes on this state.
7. **Step 7 — the `code` loop.** The final loop per [review-loop](../feature/references/review-loop.md). The first round assesses the current diff; the owner of a finding fixes it, after which you rerun the tests the fix concerns and the final profile. The closing of findings is assessed by the next round in a fresh `reviewer` subagent; `verifier` only for single claims outside the loop. Without a PASS from the loop you will not close the task; STOP rules out closing.
8. **Step 8 — codereview.** Before you open a PR, propose `codereview` (the “before PR” mode) for finished changes.

**Entry from a ticket key** (also `/bugfix <KEY>`): first [ticket-intake](../feature/references/ticket-intake.md). The brief from step 2 becomes the plan `docs/plans/YYYY-MM-DD-<key>.md` with the branch names (base and working) and the proposed version. Then STOP until “GO implement”, before the test and the fix come into being (the brief is a shortened version of act II). When the fix and the review are ready, the code stays uncommitted: a summary, STOP for a human's review, and the PR only after “do the PR” ([pr-after-approval](../feature/references/pr-after-approval.md)). In this variant step 8 is replaced by a rule: you launch `codereview` only with System Impact High or risk high, in other cases you only propose it; only after the PRs are created write `done` and `--final`. Without a key the run does not change.

**A bug described in an FRD** (`docs/frd/`): a one-off assessment per [frd-intake](../feature/references/frd-intake.md) performed before the brief (when the input is a key, `ticket-intake` does it). In the brief's plan the requirement map, the sha of the FRD file and the assumptions; the `reviewer` checks them against the file.

**Outside the variants:** a report of new behavior instead of a bug means a question whether we move to `feature`. A fix that reveals a contract or data change not described in the brief requires a question about a [spec](../feature/references/spec.md); you do not widen the scope on your own, and consent means moving to `feature`.

**With `specs.tracked: false`** ([spec-store](../feature/references/spec-store.md)) the bugfix brief/plan lies in the ignored `docs/plans` and does not enter commits; durability comes from `skillsync spec snapshot`, not from git history.

**A `skillsync retro` candidate** in state `ready` or `recurred`, approved by the user, may be a report: its `failing_case` becomes the starting point for the oracle, and the fix is led by one owner. After it, record `ref` with the command `skillsync retro-decide --decision approved --replace --ref <commit>` ([measurement](../feature/references/measurement.md)).

## Final rules

- For a minor edit do not launch the whole team of roles; move a growing scope to `feature`.
- Guard the task's approval, the baseline and the publication boundaries.
- Record a checkpoint after each step result, also after a failure, and when a run is interrupted. Closing the run goes in this order: first the `retro-quality` checkpoint with the quality retrospective ([retro-quality](../feature/references/retro-quality.md)), then the `final` step with `--status PASS --final`, and last `skillsync retro` ([measurement](../feature/references/measurement.md)).
