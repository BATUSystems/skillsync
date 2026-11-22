# Review loop: spec, plan, code

Read before reviewing a spec, plan or code. Every `feature` act ends with a loop: review rounds in a fresh
subagent, until a round returns only trivia; the number of rounds is set by the loop category ([Loop category and transitions](#loop-category-and-transitions)). The round, severities, safeguards, states and recording are common; the profile sets the material,
checklist and stop condition. Example: the spec is reviewed by another model in a new session, and the author applies the blockers
and important findings, minor ones only when cheap, and justifies every rejection.

## Profiles and scope

Plan risk = the highest step risk in the plan (risk column, [plan](plan-details.md)); the same goes to `readiness --risk`.

| Profile | Act | Checkpoint step | Mandatory loop | Otherwise |
|---|---|---|---|---|
| `spec` | I | `spec-review` | always with `plan-large` | — |
| `plan` | II | `plan-review` | `plan-large`, plan risk high, user request | one optional round, no loop |
| `code` | III | `code-review` (final), `code-review-<step>` (after a phase) | final always; once per phase after its green rung, with no risk condition (risk decides the loop category) | — |
| `docs` | III | `code-review` (instead of the `code` profile, when the change touches only documentation) | one round; a full loop only for changes in the leak guard, migrations or security (as in "Loop category") | — |
| `prototype` | I–II (before "GO plan" or "GO implement" for a user-visible change) | `prototype-review` (report `prototype-review-N.md` in the run directory) | one round after screen drawing is finished (`prototype writing done`) and before the request for acceptance | — |

The "Mandatory loop" column says when a loop exists; how many rounds it has (full or single-round) is set by the section "Loop category and transitions".
Example: plan risk high makes the `plan` loop mandatory but not full: one round, the author applies the majors, and the closure
is checked by a fresh `verifier`; round 2 only after a blocker, after a second `REFUTED`, or after an answer to Q/C questions (exceptions from "Loop category
and transitions" and "States"). The `spec` loop is made mandatory by `plan-large`, not by risk.

A `direct` that exposes a contract change moves to a spec (`feature`, step 4), so it gets the `spec` profile.
The `prototype` profile is chosen by `skillsync review` (field `profile`, or `--profile prototype`) when the change fits within the prototype directory; the
reviewer's material: the screens' `index.html`, `prototype.json`, comments and requirements. The checklist is core (`workflow.review.prototype`:
screen and criteria counterparts, edge states, text without jargon, navigation and `sN` anchors, basic accessibility, no sensitive data,
conformance with the engine contract), and layers, the project and the manifest only add items. Freeze the report as `prototype-review-N.md`.
**Rule for listing findings:** to a human (in the plan's "Result" and in a short list at the request for "GO") show all `blocker` and `major` findings and
every finding that changed a screen, spec or plan, with title, severity and what was done (applied, rejected with a reason, deferred), also when it was applied; `minor` only as a count.
The `docs` profile is chosen by `skillsync review` (field `profile`) when the change touches only documentation (`docs/**` without code files, e.g. `.py`, `.js`, `.css`), the library skills' `.md` files, `CHANGELOG*` or release notes; for a mixed change the profile is `code`, and the `docs` items from the checklist get the documentation files (`docsFiles`). Paths and items can be overridden in the manifest (`workflow.review.docs_paths`, `workflow.review.docs_checklist`).
The idea challenger and `pre-implement` are outside the round counter. Order in act I: answers to `Q`, `pre-implement`
(when applicable), the `spec` loop, "GO plan"; in act II: plan, the `plan` loop, `readiness`, "GO implement".

## Profile material and checklist

- **`spec`**: the spec, project instructions, the FRD file (when present). One independently deployable capability (otherwise "split"),
  decisions with alternatives, contracts, data, migration, rollback, risks with residual risk, acceptance criteria
  testable and mapped to FRD requirements, a state table and failure paths, concepts with two meanings (glossary),
  conformance with the project instructions.
- **`plan`**: the plan and the spec from the `Spec:` line (without a spec: the plan alone with criteria). Criteria ↔ steps ↔ TESTS (mapping),
  step risk, resources and parallelism, state conflict between steps (a change in A breaks B's precondition), failure
  paths, hidden assumptions, documentation and knowledge, the version question.
- **`docs`**: changed documents, CHANGELOG, release notes and skills, plus the documents and tools they refer to. Every promise (behavior, command, exit code, version, "works on X") has evidence: a test, CI, a screenshot or a log; without evidence it must be marked as unconfirmed. Instructions are consistent with the tools that check them (`check_*`, validators, `doctor`): run the described command on an example or point to its test. Skills and documents do not contradict each other (ceremony vs. retro signals, command and flag names, paths). Current state only: no "formerly X", old aliases or description of the state before the change. No names of people, clients or events in the public core; PR and notes text passes `skillsync leakcheck --text`.
- **`code`**: material from [review-material](review-material.md) (working and staged diff, new files, baseline, plan,
  spec, FRD, test logs) and the `reviewer` role checklist from `skillsync review`. Diff scope:
  - loop after a step: the diff of the step's files from the plan list;
  - final loop with plan risk high: the full diff in every round;
  - final loop with lower risk: round 1 the full diff, from round 2 the fixes and the files they touched, with the full diff available for reference.

  In a full loop the closing of findings is checked by the next round's reviewer; in a single-round loop the closing of majors is checked by a fresh `verifier` (transition table). Outside a loop `verifier` serves for targeted claims.

## Round

- Every round is a **new `reviewer` subagent** in the loop profile, one assignment. Never resume the author or the previous
  reviewer (an exception to the rule "resume the same agent for fixes" from the host adapter).
- Model: determine the session model solely from the system context, without guessing. Claude: a session on a model other than the default
  reviewer (`sonnet`) → the default; a session on `sonnet` → `model: opus` in the call; session model unknown → the default
  and a record "session model undetermined". The `code` profile with plan risk high may get `model: opus` in other sessions too.
  Codex: the reviewer model is set only by `codex.agent_overrides` in the manifest, so usually "the same model" and a note of this
  in the report. The stop does not depend on a model difference.
- Assignment: the profile, paths of the material and instructions (in a project the full paths, e.g. `.claude/skills/feature/…`), and from round 2
  also the files of earlier rounds' decisions (`Id | Severity | Description | Resolution | Returns`, with the author's justification for a rejected blocker/major)
  with an instruction to open them only after the independent review (the order is declarative).
- The test-writer of a phase works in parallel with round R1 of that phase (its integration tests are checked by round 2 or, in a single-round loop, a fresh `verifier` before the step is ticked checks the test-writer's tests against the step's criteria (whether they cover them and whether they can fail), and the evidence (the verifier's report) goes into the step's checkpoint record), but only in phases that have
  a test-writer in the plan; without one, round R1 goes alone.
- With `ceremony: demo`, [ceremony demo](ceremony-demo.md) changes the number of loops: one `code` loop per run instead of a loop after every phase, plus a full loop
  for steps with the leak guard, migration or security; the final loop stays full when the whole change belongs to a full-loop category from this section. Only in `demo` does a single-round `code` loop have at most 2 rounds ([ceremony demo](ceremony-demo.md)): the second only on a blocker, at least 3 majors, fixes touching the leak guard, migrations or security, or after a second `REFUTED`; otherwise `verifier` closes the majors. Full loops and `full` mode unchanged.
- The reviewer returns: status, new findings (severity, location, problem, proposal) and from round 2 a closure table, **one line for
  each id**, outside the report length limit. It checks closure on files, not on the author's declaration. For a rejected
  blocker/major it rules "justification accepted / not accepted". A finding's return is matched by the reviewer: a new finding gives
  the id of the earlier one it returns to, and the closure row has status "open"; the orchestrator then increments "Returns".
- Severities and status: the `reviewer` role, section "Loop profiles". The author does not lower severities.

## After a round (the author, i.e. the orchestrator)

- Every blocker and major: apply or reject with a justification. Minor: apply or reject with a justification. Nit: may be skipped.
- Finding ids are assigned by the orchestrator: `R<file number>-<n>`. Resolutions in the table `Id | Severity | Description | Resolution | Returns`.
- `code` profile: fixes are made by the owner of the code or test (the step's resumed agent), then tests of the affected areas
  (broader when a shared contract changed), and in the final loop the final suite, before the next round starts.

## Stop and safeguards

The limit of 4 rounds and STOP apply to a full loop. A single-round loop ends according to the transition table in "Loop category and transitions".

**PASS** requires jointly: reviewer status `PASS`, zero blocker and major among new and open findings (closed ones
and rejections with an accepted justification do not count), a full closure table (no row for an id = an open finding)
and no Q/C questions and no "split" verdict. The `code` profile additionally:
- evidence that negative assertions and the smoke can fail;
- **after a step**: the round starts after the step's files stabilize (end of the work of that step's `implementer` and `test-writer`; exception: a test-writer working in parallel with R1 does not hold up the round, because R1 concerns production code, and its tests are checked by round 2 or a fresh `verifier` (whether the tests cover the step's criteria and can fail; evidence in the step's checkpoint record)),
  the tests of the step's area are green on the reviewed state; the step in Progress is ticked after the PASS of its loop (a step without a loop: after the tests);
- **final**: the final suite is green (logs newer than the last change), and documentation (`scribe`), changelog and the agreed
  version number are in the diff before round 1. A change to documentation, changelog, version or code after PASS invalidates the evidence
  (a new round), with two exceptions: bookkeeping entries (Progress, Result and `Run` of the plan, spec status, records
  `.skillsync/runs`, knowledge records from "Discoveries" in `docs/lessons` and `docs/domain`, "Knowledge gained") do not invalidate;
  a minor or mechanical fix after a PASS with minors needs no round if the final suite stays green on the new
  state and the fix is listed in the Result.

PASS exists only from a subagent's report: when it cannot be launched (no delegation, a limit), the round is `BLOCKED`
and a question to the user; the orchestrator does not review on its own.

A `BLOCKED`, interrupted or truncated-report round is **invalid**: it gives no PASS, is repeated with a fresh subagent
and does not count toward the limit.

**STOP and a question to the user** with both positions, no further round, when: 4 valid rounds without PASS (a separate counter
for each loop, also each `code-review-<step>`); an unaccepted justification of a blocker/major rejection; a second return of the
same blocker/major finding after applying (the first return is an ordinary fix); two invalid rounds in a row. After STOP the user decides:
- `spec` and `plan`: GO, another round with the counter from 1, or a plan change. GO after STOP without a PASS round leaves the loop's step
  at `BLOCKED` (run `incomplete`): describe the reason and the user's decision in the plan's "Result"; do not record an artificial `PASS`.
- `code`: STOP rules out `done`; the user chooses another round (counter from 1), narrowing the scope (a plan change) or plan `blocked`.

A request for "GO plan" or "GO implement" explicitly lists every blocker/major rejection with the justification and the reviewer's ruling
and the remaining minors; the plan's "Result" does the same for the code loop.

## Loop category and transitions

A full loop (up to 4 rounds, PASS condition and STOP as above) is given only to changes in the leak guard, migrations
and security. Other changes get one round, and a second only when the round finds a blocker.
Security means changes to access boundaries: permissions and consent words, authentication, secrets, reading or writing
outside the project, and executing commands from external data.

The `code` loop runs once per plan phase, after its green test rung; a round after every small step is excess ceremony
(`ceremony-excess`). The plan records each step's loop category (at the step or in the description of the loops after the steps). A step touching the leak guard,
migrations or security gets a full `code-review-<step>` loop regardless of the risk column. The spec, plan and final loop get a full loop only when the change belongs entirely to one
of these categories; steps with a full loop already have it after the step. A round after the user's answer to Q/C questions reported
from the previous round's findings (table "States") is not excess ceremony. A round after a change of the reviewed material, when the loop has
already given PASS, starts a new loop of that step and is not its next round.

| Round result | Checkpoint record | Next |
|---|---|---|
| reviewer `PASS`, zero blocker and major | `PASS` | end of loop |
| single-round loop: major without a blocker | `FAIL` with a cause key | the author applies or rejects findings with a justification and records the decision table; a fresh `verifier` checks the closure of every major and every rejection of a major; then a `PASS` record of the same step (`--attempt` + 1) with the evidence: the decision table and the verifier's report; the "Result" lists rejected majors |
| verifier: `REFUTED` for a closure | — | fix and re-verify that finding, without a new round; a second `REFUTED` of the same finding means round 2 in a fresh reviewer |
| blocker (any loop) | `FAIL` with a cause key | fixes and round 2 in a fresh reviewer; a round without a blocker ends as in the second row |
| full loop (leak guard, migration, security) | per the rules of "Stop and safeguards" and "Recording" | up to 4 rounds, PASS condition and STOP unchanged |

Round 2 and later in a single-round loop, although the previous round found no blocker, and repeating a check on an
unchanged state are a `ceremony-excess` signal of the retrospective ([retro-quality](retro-quality.md)). Exception: round 2
after a second `REFUTED` of the same finding is not excess ceremony, because the verifier has already shown that the closure does not work. Second exception: a round after the user's answer to Q/C questions reported from the previous round's findings (first row of the "States" table). Likewise in `demo` round 2 for the reasons from [ceremony demo](ceremony-demo.md) (blocker, at least 3 majors, fixes in the leak guard, migrations or security).

## States

| Situation | Action |
|---|---|
| Q/C questions or "split" from the findings (also with status `PASS`) | pause; the round that reported them counts toward the limit, the pause itself does not; after the answer the next round |
| The user changes the acceptance criteria or scope | counter from 1 in the loops it affects |
| Edit of the spec or plan after a PASS round | mechanical or minor only; a substantive change = a new round |
| Another user correction at GO | their decision, no round, recorded in the plan |
| Spec change in act II, mechanical or minor (editing, clarification without changing a decision, contract, scope or criteria) | spec fix, recomputed `Spec-sha` in the plan and a list of changes since "GO plan" in the request for "GO implement", without a new `spec` loop |
| Spec change in act II, substantive (decisions, contracts, scope, criteria) | act I from scratch: spec `draft`, plan `draft (waiting for spec)`, `spec` loop from 1, "GO plan" (removes the suffix, the plan returns to `draft`), new `Spec-sha` in the same plan, `plan` loop from 1; a `plan-review` PASS from before this consent is invalid |
| Spec change in act III | plan `in_progress (blocked — waiting for spec)`, the spec returns to `draft`, the old `Spec-sha` line disappears from the plan; `spec` loop from 1, then one GO lifting the block, `approved` and a new `Spec-sha` ([spec](spec.md)); `plan` loop only if the plan changes substantively |
| Fixes after a human's code review ([pr-after-approval](pr-after-approval.md)) | `code` loop with the counter from 1; with plan risk lower than high, round 1 already gets the fixes and touched files (full diff for reference), with high the full diff |

## Recording

- A frozen reviewer report `.skillsync/runs/<run-id>/<step>-N.md` (`spec-review-N.md`, `plan-review-N.md`,
  `code-review-N.md`, `code-review-<step>-N.md`; the record's evidence, with no later changes; N sequential within the run step,
  also after a counter reset) and separately `<step>-N-decyzje.md` with the limit counter `k/4`.
- One checkpoint under one step per loop, `--attempt` grows monotonically within the run step. A round with blocker/major =
  `FAIL` with `--cause`; a round with blocker/major and questions = `FAIL`, and the pause a separate record; an invalid round and STOP =
  `BLOCKED` with `--cause` (the evidence is what was returned, or the orchestrator's note about the interruption); a pause = `NOT_RUN`
  with `--cause`; the final round `PASS` (also with minors) with its own evidence file.
- Cause keys: `spec-review` and `plan-review` rounds **without `--cause-key`** (review findings are the normal work of a review,
  and a key about the mere fact of findings, e.g. `plan-review-changes`, after a `rejected` decision in the project registry gives a false
  `recurred` in the retro; a substantive key only when the round's findings have one common cause). `code-review*` rounds **with a substantive
  key** from `workflow/cause-keys.yaml` (`docs-behind-code`, `test-gap`, `state-rule-gap`, …), because they feed
  retro candidates; a `PASS` record after `FAIL` rounds has the next `--attempt` and the key of the last `FAIL` round. Measurement: [measurement](measurement.md).
- The plan's "Result" gives the number of rounds per act and the number of findings by severity in each round.
