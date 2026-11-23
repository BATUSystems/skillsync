# Plan: required content and approval

Read when writing the plan `docs/plans/YYYY-MM-DD-<slug>.md`.

The plan header has a line `Run: <run-id>` with the identifier used in `checkpoint`; the hook and `doctor` check closure by it.
The optional line `Handoff: <branch>` records the user's answer on where “CP” hands the work over in `direct` mode (the AGENTS core); without it
“CP” pushes only the working branch. The value is the branch name alone (valid according to `git check-ref-format --branch`); other text (a placeholder, with a note) is ignored, so do not write placeholder text, omit the line or ask.
The line `Directory: worktree <directory-name>` or `Directory: main` records the choice of the working directory from “GO plan” (a name, not an absolute path; no line = worktree);
the question, conditions and resumption: [workdir](workdir.md).
The line `Ceremony: demo` (optional with `full`) records the value of `workflow.ceremony` from `skillsync route`; a mismatch with `route` at “GO implement”
is a question to the user ([demo ceremony](ceremony-demo.md)).
With `specs.tracked: false` the plan is local (ignored by git): its durability is given by the snapshot, and acceptance closes the consent entry with `planState: done` (a consent field, not git history; [spec-store](spec-store.md)).

The plan of a large change has in its header a line `Spec: <docs/specs/YYYY-MM-DD-slug.md>` (like `Run:`, a separate row, the path alone),
and from “GO plan” `Spec-sha: <git hash-object of the spec>` (the plan draft, [plan-large](plan-large.md) §5). A plan with a spec does not repeat its content:
it has the literal headings `Goal` (the goal, with a link to the spec), `Acceptance criteria (mapping)` (the acceptance criteria mapping: a table of spec criterion → steps → evidence; the criteria
and the FRD requirements map are in the spec), `TESTS` (the tests: commands), `Documentation and knowledge` (documentation and knowledge) and the steps, resources, version, Progress and Result. With an FRD the plan
records the sha of the FRD file and the assumptions from `GAPS` ([frd-intake](frd-intake.md)). Statuses, sha and resumption of the spec: [spec](spec.md).

**Summary for humans.** The plan starts with the section `## Summary for humans` (the heading is literal, right after the metadata lines),
at most one screen: up to 40 lines and up to 4000 characters (that is what the status page shows; longer text is truncated with the `…` character). Four blocks:
**What we change**, **What we do not do**, **Risks**, **Decisions to make** (each with a recommendation). Do not repeat the plan's content in it.
`skillsync readiness --plan` and `skillsync doctor` warn (a warning, no block) when a `draft` or `approved` plan lacks this section
or it is empty; `in_progress`, `done` and `archived` plans without it are valid. `skillsync status` gives the section in `plans[].summary`.

A plan with the status `draft` contains: the goal, the acceptance criteria (with a spec: the mapping), steps with role, files and step risk, dependencies,
instructions/skills to read, TESTS, resources, risks, **Documentation and knowledge**, **Version and changelog**
(a version proposal; a question to the owner) and open questions. With a `Spec:` line the scope, contracts, migration and risks are in the spec, and the path is relative to the project directory. The knowledge section lists the documents
made outdated by the change, the expected domain knowledge and the owner of the write
([rules](../../workflow/references/knowledge.md)); “not applicable” requires a justification. Use also
the `domain` records returned by `route`. TESTS: commands, cwd, environment, tests to adapt,
new tests and a comparison with the baseline for old failures ([details](tests-baseline.md)).

The section **Domain terminology** contains confirmed terms to add/update,
candidates with a hypothesis of the meaning and a source/question to clarify, or a justified absence of new
terms. Before interpreting the task read the records with `load: bootstrap`; only definitions confirmed with a source
go into the glossary ([rules](../../workflow/references/knowledge.md)).

Version: a proposal and a question in act II, the answer at the latest at “GO implement”; the number goes into the diff before round 1 of the final
`code` loop ([loop](review-loop.md)).

Draft statuses in act II: `draft` (the plan being written or in the `plan` loop), `draft (waiting for GO implement)` (the loop finished, `readiness`
done), `draft (waiting for spec)` (a substantive change of the spec in act II; “GO plan” removes the suffix). After “GO implement” re-read any changes
by the user, compare `Spec-sha` and set `approved`, then `in_progress`. A plan approved by `skillsync plan approve` or
by a button (changes only the plan): resumption according to the table in [spec](spec.md), e.g. with a `draft` spec STOP and a question.
Plans from before these rules, also large ones, remain valid without a spec: no migration, resumption as before.
Statuses: `draft`, `approved`, `in_progress`, `done`, `archived`; record a block in parentheses after the value (`in_progress (blocked — reason)`), not as a status. `skillsync plan archive|unarchive <project> <file>` switches only `done` ↔ `archived`.
For contracts/API/data/high risk before writing code use `pre-implement`: read
the actual consumers and write the sections Contracts, Migration, Risks: in the spec, when it exists (act I, before the `spec` loop), otherwise in the plan; TESTS always in the plan.
`skillsync readiness <project> --plan <file> --risk high --files <paths>` checks the structure;
the substantive correctness is judged by the orchestrator/reviewer; with a spec `readiness` runs in act II, before “GO implement”. A `direct` plan
with plan risk high or at the owner's request goes through the [review loop](review-loop.md) in the `plan` profile; other plans may get one round.

## Steps, Exec and Progress

A plan step is a phase: a coherent piece that ends with a working application, a green test tier and a commit (usually 5–8 per
plan, e.g. model and migration; rules and engine; API; UI; integration and e2e tests; closing). A dozen small steps multiply
handovers between roles (implementer → test-writer → review → verifier → commit) and it is they, not the scope, that lengthen the run.
A step has a fixed id and a row in a table with the columns `Id | Step | Role | Exec | Step risk | Files`:

| Id | Step | Role | Exec | Step risk | Files |
|---|---|---|---|---|---|

A phase with FE and BE starts with a contract step (DTOs, endpoints, a contract snapshot in the commit); then FE and BE go in parallel on the same
contract, with no “alignment” pass. The test-writer works in parallel with review R1 of the phase (its tests are checked by round 2 or, in a single-round loop, a fresh `verifier` checks the test-writer's tests against the step criteria before the step is ticked (whether they cover them and whether they can fail), and the evidence (the verifier report) goes into the step checkpoint record), but only
in phases that have a test-writer in the plan. Calibrate the estimate at about 75–105 min of work per capability with a full review loop; a spec
with at least 8 capabilities is two plans ([plan-large](plan-large.md)).

Step risk is `low`, `medium` or `high`. Risk `high` goes to a step touching a contract, data, permissions or migration and has right after it its own [review loop](review-loop.md) in the `code` profile. The highest step risk is the plan risk: pass it to `readiness --risk` and it sets the scope of the diff of the final loop. Without a risk column the plan takes the risk from the `Risks` (risks) section, and when it is missing, ask a question.

Set `Exec` once, at planning: `inline` (the main session), `dispatch[:tier]` (a separate subagent for each step), `group:<id>[:tier]` (one subagent for a series of steps). No value means `inline`. `tier` is the model class: `cheap` for a fully specified step, `standard`, `capable` for a hard step; the host adapter translates the class into a model, and do not declare a model whose availability is not confirmed.

The section `## Progress` is mandatory, parsed by tools and stands at the end of the plan; its template:
```markdown
## Progress
An empty box (`- [ ]`) = to do, ticked (`- [x]`) = done; after a step lands, append its sha after ` — `, and do not change ids or titles.
PR: #123 (link: https://example.com/pr/123)

### Phase 1: Example
- [x] F1.1 BE ‖ FE code (implementer) — module 631/631
- [x] F1.2 Test tier and code commit — 1a2b3c4
- [ ] F1.3 Integration tests (test-writer) and commit
- [ ] F1.4 Review and fixes
- [ ] F1.5 `--fast` at the end of the phase and the phase checkpoint
```
- The phase is the unit of review and commits, and the **phase substep is the unit of reporting**: every phase has substeps `Fn.1…Fn.k`
  (typically: BE ‖ FE code, test tier and code commit, integration tests and commit, review and fixes, `--fast` at the end of the
  phase and a checkpoint), ticked with a sha or a result; a progress report after each substep or every 30 minutes, whichever comes
  first (one line per substep: substep, result or sha, time). Older plans with steps without a dot (`S1`, `S2a`, `K1`) are valid.
- Steps are items `- [ ]` / `- [x]` with an id and a title; tick a step only after checking the evidence (checkpoint, a command result), never on a subagent's declaration alone.
- A `Progress` item is one thing: a review and the checks after a build do not share an item with a commit or with each other (a test tier with the commit of its code, as in the pattern, is one item); otherwise its state does not say what is done.
- Plan the documentation after the last code step and collect its evidence (agreement with the code, examples, numbers) on the final state: documentation written before the rollout drifts from the code.
- Ticking and the progress line keep up with the commits: a step that has evidence and a commit is ticked at once (`[x]` with the sha), also when other subagents of the same phase are still working; do not postpone ticking to the end of the phase. The status page and `skillsync status` read `Progress`, so an understated progress misleads the human.
- The last `Progress` step is the closing of the run (role: orchestrator): “Result”, `retro-quality`, the `--final` checkpoint,
  `Status: done` of the plan and `implemented` of the spec. Without it a plan after the last `[x]` stays `in_progress` and `skillsync status`
  reports `needsHuman: closure`. With `ceremony: demo` `retro-quality` is deferred to the subsection “Debt (ceremony: demo)” in the “Result” ([demo ceremony](ceremony-demo.md)).
- **The “Acceptance” step** (role: orchestrator; a plan with it is optional, plans without it work as before) stands before the steps closing the run
  and ends when the human has received the acceptance material: a **checklist** (one line per spec criterion: what to check manually or where the
  automatic evidence is and how to see it) and a coverage table “criterion → test(s) → result” (a template in the “Result”). The agent ticks it `[x]` after
  handing over the material; that is preparation, not consent. **Acceptance is the final consent word of the delivery mode**, the same one that already ends
  the run today: `READY` in `pr` mode, `CP` in `direct` mode (no new word, button or kind of consent). After the run is closed (`done`) a plan with the
  “Acceptance” step and an active run (`.skillsync/runs/<run-id>/` exists) has `needsHuman: acceptance` until this word is entered in `.skillsync/runs/<run-id>/approvals.jsonl` (with the sha of the commit in which the plan is already `done`)
  (a status page button or `skillsync approvals record` after a word typed by the user in the session); further commits do not light up a closed plan again, and
  ticking the step or a line in the plan extinguishes nothing. `closure` and `acceptance` do not occur at once: first the closing (`in_progress`, all `[x]`),
  then `acceptance` (`done`). Findings from the acceptance return as new plan steps, not as consent.
- Phases are headings of level 3 or lower; a level-2 heading closes the `Progress` section.
- The line `PR: #<n> (link: <url>)` starts a row (it is not a list item or a quote) and stands under `## Progress`; the optional `Ticket: <KEY> (link: <url>)` carries a Jira key, e.g. `ABC-123`.
- Plans without `Progress` (older) are valid and need no migration; the optional run to a PR is described by [pr-run](../../pr-run/SKILL.md).

## Result: template

The section `## Result` is created when the run is closed and has fixed subsections (in the order below; write “none” explicitly):

```markdown
## Result
### Changes and evidence
### Review findings
| Finding (title) | Severity | What was done |
|---|---|---|
| Example finding | major | applied (commit 1a2b3c4) |
### Criteria coverage
| Criterion | Test(s) | Result |
|---|---|---|
| K1 | tests/test_x.py::test_a | PASS |
| K2 | no test: manual acceptance (justification) | — |
### Old failures and limitations
### Knowledge gained
```

- **Review findings:** all findings of severity `blocker` and `major` and every finding that changed a screen, the spec, the plan or the code, **with a title,
  severity and what was done** (applied, rejected with a reason, deferred), also when the finding was applied (an applied one does not vanish from the count);
  `minor` findings only as a number; the number of rounds according to the [review loop](review-loop.md).
- **Criteria coverage:** a table “criterion → test(s) → result”, source: the “evidence” column of the plan's criteria mapping. A missing test is
  visible as “no test” with a justification, not hidden. This is a report and documentation, not a hard gate.

## Prototype gate

A plan with a UI change has in its header (like `Spec:`, a separate row, the path alone relative to the project directory) a line
`Prototype: <root>/<slug>` for every prototype created for this change.

- Run `skillsync prototype gate <project> --plan <plan>` before asking for “GO implement” and again after receiving it.
  `readiness` with a `Prototype:` line gives BLOCKED when the gate has FAIL.
- A gate FAIL = refusal to start act III with a list of missing acceptances (`slug`, screen, reason: `brak` (missing), `nieaktualna` (outdated),
  needs_migration, `brak_ekranow` (no screens), `niejednoznaczny_ekran` (ambiguous screen), `writing` = the agent is still writing the prototype screens, an entry without a screen; after `prototype writing <project> <slug> done` or the marker expiring the gate returns to the ordinary assessment). BLOCKED (e.g. `prototype_not_found`, `invalid_prototype_line`) = a report, not a workaround.
- Exception (D16-Q2): only the owner in a card with the words “GO implement bez akceptacji prototypu” (GO implement without prototype acceptance). The agent then adds to the header
  a line `Prototype gate: skipped — <reason> — <person>, <YYYY-MM-DD>`, quoting the reason. Without these words of the owner the agent
  does not add this line and does not remove the `Prototype:` line. The dispatcher and the agent do not bypass the gate.
- A human accepts (the UI or the CLI with a password from the TTY); the agent never runs `skillsync prototype accept` or `unaccept`.
  `gate` shows `via` and `who`, so an acceptance from the CLI is visible when asking for “GO implement”.
  When `accept` is run anyway (by mistake or by another session) and returns `BLOCKED` `error: tty_required`, say plainly “kliknij Akceptacja na stronie prototypu” (click Accept on the prototype page) and wait (`prototype wait`); the safeguard stays.
- Prototype acceptance is not plan approval (“GO implement”, `plan approve`); one does not replace the other.
- The `Prototype:` line accepts only the path `<root>/<slug>`, without a description. An ambiguous description gives BLOCKED `invalid_prototype_line`, a one-word description BLOCKED `prototype_not_found`.
- `No prototype: <reason and source>` is a descriptive convention for humans (e.g. when a UI change has no prototype). The tools do not read this line: `readiness` and `prototype gate` do not recognize it, and a UI change without a `Prototype:` line and without `[UI]` markers does not trigger the prototype gate.
- Unfinished-work markers in the content of the spec and the plan block `readiness` also when they are the name of a domain state (the rule is in `spec.md`); name such states differently before “GO plan”.

### Per-phase gate (`[UI]`)

A phase touching a screen gets the marker `[UI]` in the `Id` or `Step` cell of the steps table (e.g. `F2 [UI]`). A plan with `[UI]` phases has a gate
per phase: a `[UI]` phase requires prototype acceptance before the start, and phases without the marker start after “GO implement”.
Before the start of a `[UI]` phase run `skillsync prototype gate <project> --plan <plan> --phase <Fn>`; FAIL or BLOCKED = the phase does not start.
No JSON with exit code 2 (an older `skillsync` without `--phase`) = BLOCKED.
The marker is recognized literally: `[UI]` in the middle of a `Step` cell, in backticks or outside the steps table does not count, and duplicated phase ids switch off the plan's phases (the plan returns to the whole-plan gate); write the marker at the start or the end of the `Step` cell or in the `Id` cell.
A `[UI]` phase requires a `Prototype:` line; without it `gate` and `readiness` give BLOCKED `ui_phase_without_prototype` (unless there is an owner's bypass).
A plan with a `Prototype:` line without any `[UI]` phase works as before: the whole plan is behind the gate.
With a plan with `[UI]` phases a FAIL of the whole-plan gate blocks neither phases without the marker nor `readiness`; the start of a phase is decided by `prototype gate --phase <Fn>`.
The request for “GO implement” for a plan with `[UI]` phases explicitly lists the `[UI]` phases and the phases without the marker (the fields `uiPhases` and `nonUiPhases`
from `skillsync readiness`), so that the split is approved by the user.
