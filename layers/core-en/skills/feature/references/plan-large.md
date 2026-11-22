# Plan for a large or unclear change

Read this file only when the plan gate in step 3 of `feature` points to `plan-large`. It is triggered by a change that adds a capability, touches the architecture or the data schema, changes an API contract, reaches several modules or phases, or starts from a brief full of critical unknowns.

Before you create anything, open `docs/plans/` and `docs/specs/`. Update a plan that already covers the task (the `reuse` mode). Resume an unfinished spec skeleton with questions (status `draft`, without a plan) instead of starting a new one ([spec](spec.md)).

Plan a bugfix, documentation, dependencies and an isolated refactor with an ordinary plan ([plan](plan-details.md)).

The spec lives under `docs/specs/YYYY-MM-DD-<slug>.md`; its format, statuses and the moment of freezing are given in [spec](spec.md). The plan points to the spec through `Spec:` and describes only the execution.

This document leads act I (spec, the `spec` loop, the GO plan consent). Act II (plan, the `plan` loop, the GO implement consent) and the state tables are described by the [review loop](review-loop.md) and [spec](spec.md).

## 1. Skeleton and a hard question gate — STOP

- Writing the spec in one step is forbidden. The first write is a skeleton: a `draft` with a part about the problem, a part about the scope and a section `Pytania otwarte` (open questions). Until GO plan is given, no plan is created.
- A critical unknown is an assumption whose error would mean rewriting a large part of the spec; typical cases: data format, reach, the place of connection to the rest of the system, choice of tool.
- Check the split: when the order covers several capabilities, each of which works on its own, ask the user whether to break them into separate plans and specs. A spec with at least 5 capabilities is two plans (a stage split): at the plan gate ask the user about the split; calibrate the estimate at about 75–105 min of work per capability with a full review loop.
- Each question gets a number `Q1`, `Q2`… and its own short line; choose a yes/no form or a set of options. A question with a reasonable default resolution may be a non-blocking assumption `[ASSUMPTION]` ([spec](spec.md#question-classes-q)); the STOP concerns blocking questions.
- After showing the skeleton with blocking questions, stop and wait for the user's answers; with `[ASSUMPTION]` assumptions alone there is no STOP.
- Until the answers arrive, only `explore` works, plus collecting data independent of the questions: who uses the module, how the code is built, which contracts already exist. A design built on guesses is forbidden.
- Report an unknown discovered later through the gate again, only for the new questions.
- When the answers arrive, the blocking questions disappear from the `Pytania otwarte` section, and the `[ASSUMPTION]` assumptions stay in the spec ([spec](spec.md#question-classes-q)); the resolutions land in `Decyzje i alternatywy`, and the spec is supplemented with acceptance criteria and the FRD map ([spec](spec.md)).

## 2. Extended sections

Placement: in the spec, outside phases and steps — those are in the plan.

- Decisions and alternatives (`Decyzje i alternatywy`): two tables — a decision with a justification and an alternative with the reason for rejection.
- `Badanie wzorców` (pattern research) only when the change brings a new mechanism. A summary: what was checked, what was chosen, what was rejected and for what reason. The name matches the spec template.
- Phases and steps live in the plan: a phase is an increment fit for release, a step a task that can be checked by a test; the application stays working after every step. Name deferred ideas explicitly, without mixing them with the MVP.
- A risk register in the `Ryzyka` section. Every item: a scenario, a severity (`critical`, `high`, `medium`, `low`), the affected area, a mitigation, the residual risk and a justification of why it is acceptable.
- Five categories to walk through: data (an interruption midway, concurrent writes, a partial write); chain effects; separation of tenants and permissions; transition between versions (old and new running in parallel, retries, backfilling); maintenance (failure detection, blast radius, volume growth).
- Every mutation has a way of reversal or an explicit declaration of irreversibility with a justification.

## 3. Review in a fresh context

- The order after the answers is not a matter of choice. Act I: `pre-implement`, if applicable (the result is read-only; the orchestrator carries over contracts, migration and risks), then the consistency report (§4), the `spec` loop, the GO plan consent. Act II: the plan, the `plan` loop, `skillsync readiness` with the `Spec:` line, the decision about the version, the GO implement consent. Loop rules: [review loop](review-loop.md).
- Each round is a separate subagent with the `reviewer` role, because an author will not conduct a harsh review of their own text. The number of rounds is set by the loop category from the [review loop](review-loop.md): a full loop only for the leak guard, migrations and security; in other cases one round, and another only after a blocker. Neither risk high nor `plan-large` alone gives a full loop; repeating a round without a blocker is an excess of ceremony (`ceremony-excess`). The checklist and the material for the `spec` and `plan` profiles are in the same document.
- The verdict `split` does not trigger a rewrite; it goes to the user as a question.

### Idea challenger (optional, for a large or costly change)

- When: the spec skeleton is already on disk and the user has not yet received the questions.
- Performer: a `reviewer` in the `spec` profile, tasked with attacking the conclusion itself, not the form.
- Five questions: does the problem exist and who does it concern; was the no-change variant and the smaller variant weighed fairly; do the pace and ramp of the rollout match the risk; which untested assumption is the riskiest; will a person without context understand the description cold.
- Translate the result into the statuses of the loop profiles of the `reviewer` role; a serious problem means `CHANGES_REQUIRED`.
- The challenger's findings reach the user as `C1`, `C2`…, in parallel with `Q…`; only the user closes them. The user's answers erase the blocking questions and the C findings; assumptions stay.

## 4. Consistency report

Place: in the spec, before approval. A table with the columns `check | status | notes`. Check obligatorily: data models against API contracts; contracts against the UI; whether the risks cover every write; whether every mutation has a reversal; whether caches and reads have an invalidation strategy.

- Attach a compliance matrix with the project instructions with the columns: rule, source, state. For every discrepancy give the rule, the source, the gap and a fix.
- A binary verdict: `consistent` or `blocked` until the fixes.

## 5. After consent

- After consent to GO plan: the spec moves to the status `approved`, and next to it a plan draft is immediately created with `Status: draft` and the lines `Spec:`, `Spec-sha:`, `Run:`, `Directory:` ([workdir](workdir.md)). With an FRD the sha of the FRD file and the assumptions from `LUKI` are added ([frd-intake](frd-intake.md)).
- “GO plan” is also one local commit: spec, plan draft and prototype (without generated files and secrets, `git add` only with explicit paths, a secret scan before the commit), on the working branch in the working directory chosen at “GO plan”: the question “own worktree” (the default) or “main checkout”, the choice in the plan line `Directory: worktree <directory-name>` or `Directory: główny`, details in [workdir](workdir.md); in a `worktree` “GO plan” creates the branch if it does not exist. No push: before “GO implement” nothing leaves the machine; push and PR go after “GO implement” according to the delivery mode. Exception: entry from a ticket key stays without a commit at “GO plan” ([pr-after-approval](pr-after-approval.md)).
- With `specs.tracked: false` “GO plan” does not commit the spec or the plan (ignored by git): instead the `skillsync spec snapshot` snapshot and the `go-plan` checkpoint; the commit covers only tracked files ([spec-store](spec-store.md)).
- Exception: when the plan already exists (a `direct` run that revealed a contract change), do not create a draft; add `Spec:` and `Spec-sha:` to the existing plan.
- The GO implement consent opens act III. Resumption, the spec sha and statuses are explained by [spec](spec.md) and [plan](plan-details.md).
