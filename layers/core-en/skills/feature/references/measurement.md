# Interruption and measurement

Read before the first checkpoint of a run, when interrupting work, resuming, or recording measurements.

## Progress report: after every step or every 30 minutes

A progress report is a line `- HH:MM done: …; in progress: …; risk: …[; waiting: …]` in `.skillsync/runs/<run-id>/checkpoints.md`
(read by a human and the status page). Append it **after every step OR every 30 minutes, whichever comes first**. An agent has no
clock: on every subagent return and before every long operation check the time (`date +%H:%M`) and, when 30 minutes have passed since the last
line, append a `in progress: …` line even without a finished step. The long work of a single subagent does not excuse
the report: after about 35 minutes of silence with a plan `in_progress` the status page shows "no report for N min".
The report "one line per substep": after every substep `Fn.k` append one line (the substep, the result or sha, the time), or every 30 minutes,
whichever comes first. This line fills the existing format `- HH:MM done: …` (the substep and the result or sha in `done`), and does not replace it.
The report does not replace the `skillsync checkpoint` record (below) nor the reverse.

## Report segment syntax

The segment keywords are literal and stay as written: `done:` (done), `in progress:` (in progress), `risk:` (risk), `waiting:` (waiting), `running:` (stage in progress), `who:` (who).

- Segments are separated by `;`, but a `;` inside brackets `()`, `[]`, `{}` does not end a segment (a step description in brackets may contain it).
  Always close an unbalanced bracket; on an error the line splits at every `;`, an id after such a `;` may be lost from the reading, and the status page
  always shows a warning about an unclosed bracket (`unbalanced-bracket`).
- **Every element** of the `in progress: …` and `done: …` lists starts with a step id (`F1.2`), elements are separated by a comma, and the description goes in brackets:
  `in progress: F1.1 (contracts; tests), F1.2`. Ids are joined in one element by `/`, `+`, `&`, `‖`, `||`. A role before an id
  (`implementer: F1.1`) and a second id inside a description are skipped; the status page then shows a warning about skipped fragments.
- `running: <stage>[, <stage>]` says which stage is in progress now (e.g. `spec_review`, `prototyp`, `plan`); the page reads only the latest line, so
  repeat the segment in every line while the stage lasts, and omit it in the first line after it ends. `waiting:` in the same line wins.
- `waiting: <what> (who: <role>[ <login>])` names the addressee (`acceptor`, `developer`, `expert`, `commenter`, `agent`, `acceptance`);
  the page writes "Waiting for: <role>". Without the suffix the role is chosen by the source of the item.

## Estimate before a long operation: `eta:`

Before starting a long operation (spec, prototype and its iterations, plan, every implementation step, a long test run) estimate how much
you need, and append a report line with the segment `eta: <operation> until HH:MM`; compute the time from `date +%H:%M`, never from an estimate:

```
- 14:05 in progress: F1.2; eta: drawing 12 prototype screens until 15:10
```

The status page shows "<operation>: ends about 15:10" with a countdown in the tile of the active act and at the active step, and after the deadline "exceeded by N min", so a human
does not have to ask "how is it going?". While the operation lasts, EVERY report line (also the one required every 30 minutes or after a substep)
repeats `eta:` with the same text of the operation; another text or other steps in "in progress" is a NEW operation for the page (a new estimate, its own settlement).
When the operation exceeds the ETA, do NOT stop the work: give a new deadline in the nearest report. After the operation ends, the next report line is without `eta:`
(the estimate disappears; `eta: -` ends it explicitly). An estimate with steps (`in progress: F1.2; eta: …`) goes through the lines with `waiting:`: after the reaction of the user
you do NOT have to repeat `eta:`, the page moves the deadline by the time the user was waited for (write waiting for CI, a LEASE and other resources as `waiting: … (who: agent)`:
it is then not taken out of the error of the estimate). An estimate without steps (spec, plan, review) disappears at `waiting:`. At `done:` of a step that exceeded the estimate
by more than 10 minutes add one line of the discrepancy ("estimate 40 min, took 65 min, waiting for the user 15 min"); `skillsync dashboard metrics` counts the same series
(`estimates[]`) and the retrospective uses their numbers ([retro-quality](retro-quality.md), trigger 8).
The estimate should be honest, not optimistic: better to give a later time and finish earlier.

## Long runs of sub-agents: `agent-run` and `review-round`

Run a review round with `skillsync review-round start|freeze` (see the [review loop](review-loop.md)); open any other sub-agent run that is going to take more than a few minutes (implementer, test-runner) with `skillsync agent-run start --run-id <id> --role <role> --kind run [--step <step>] [--eta HH:MM]` (prints `agent=a-<k>`; the directory `.skillsync/runs/<id>` must exist, `mkdir -p`) and close it with `agent-run end --run-id <id> --agent a-<k> --result <result>` after its report, before the checkpoint of the step; move the term with `agent-run eta --run-id <id> --agent a-<k> --eta HH:MM` when it is exceeded. The status page reads the log: it shows the role, round, start, term and result, and raises no silence during the run. State the term of such a run only here, not in the `eta:` of the report (`eta:` stays for the operations of the main session). A sub-agent never calls these commands itself.

## Checkpoint after every result (mandatory)

The record is written by the orchestrator, not in parallel, after every step result, also `FAIL`, `BLOCKED` and `NOT_RUN`:
`skillsync checkpoint <project> --run-id <id> --step <step> --status <status> --evidence <file> --next <description>`.

- `<project>` (project): the directory you work in; in dispatch the package worktree.
- `--run-id`: the plan slug; in dispatch `<run>.<package>`, and on a retry in a new worktree `<run>.<package>.2`.
- `--status`: record `CHANGES_REQUIRED` as `FAIL`; `spec-review` and `plan-review` rounds without `--cause-key`, `code-review*` rounds with a substantive key (steps and exception: `review-loop.md`). An intended test red is a `PASS` of a separate step with a red log, not a failure.
- `--evidence`: required for `PASS`; for `FAIL` give the failure log. Every record has its own file in `.skillsync/runs/<run-id>/` (`<step>-<attempt>.log`), without overwriting. The plan or any other tracked file is not evidence: it will change and permanently yield `partial`.
- A retry and the closing of a failure go under **the same name** `--step` with the next `--attempt`, also when the failure is closed by another role, e.g. the next `code-review` round after fixes. A new step name leaves the old one at `FAIL` and yields `incomplete`. Exception: re-closing `final-2`, because `final` already has `PASS`. Check `retro` also during the run, not only at closing.
- On a problem (failure, retry, non-execution): `--cause <description>`, `--cause-key <stable slug>`, `--attempt <n>` (counts the step's attempts in this run, not `.N`); `--owner instruction|skill|hook|route|test|tool` only when certain, otherwise omit. `warnings` in the write result means that a run ended this way will get the class `unexplained`: immediately append a record of this step with a cause or new evidence.
- Known numbers, increment in this record: `--denied <n>` (rejected actions), `--inherited <n>` (inherited failures).
- Closing: a separate step `final` with the log of the final checks and `--status PASS --final`, on re-closing `final-2`. A repeated step is a problem, and a record after `--final` reopens the run. Without closing the run has the class `incomplete`.
- A step that ends `NOT_RUN` or `BLOCKED` stays so: do not change it to `PASS`. The run stays `incomplete`; give the reason in the "Result".
- Do not put secrets or session content in `--step`, `--cause-key` or the evidence name: these fields go into the report.

Recording only `PASS` gives a false "clean": the tool does not detect a failure without a record.
A checkpoint that cannot be made (no CLI or manifest): `NOT_RUN` in the "Result" with a reason; in dispatch `REPORT BLOCKED`.

## Self-improvement loop (at closing, mandatory)

The quality retrospective ([retro-quality](retro-quality.md)) comes before `final`. Plan `done` requires a prior `--final` record of the run from the `Run:` line. The Stop hook blocks the turn once per session and plan when it is missing, and `doctor` reports a warning. Record `CHANGES_REQUIRED` as `FAIL` with a key from `workflow/cause-keys.yaml` (`spec-review` and `plan-review` rounds without a key, [loop](review-loop.md)); `unrecorded_signals` in `retro` shows the skipped ones. The key names what the review found (`docs-behind-code`, `test-gap`, `state-rule-gap`, another list item, as a last resort `other`), not the mere fact of findings: `review-changes-required` is historical, and a new record under it returns as `recurred`. Re-recording a step after a fix: `--attempt N` with the same `--cause-key`, otherwise the run gets the class `unexplained`; record a second `--final` only when the state changed after the first.

1. `skillsync retro <project> --run-id <id>`: the run class and the rubric for the "Result"; explain `incomplete`/`unexplained`, do not fix the records.
2. Candidates are counted across the whole project (threshold: the same `cause_key` in ≥ 2 runs). Present to the user `recurred`
   (the fix did not work), then `ready`; `needs_owner`/`needs_case` with what is missing; skip `decided`. `BLOCKED` due to
   `decisions.status: invalid` fix in the registry before the decisions.
3. A candidate fix = one smallest owner and a case that fails first ([lessons](../../workflow/references/lessons.md)).
   Exception: a quality retrospective proposal with owner `skill`, `instruction`, `docs` or `route`, accepted by "YES P<n>" or "YES <run-id>/P<n>", does not require a case that fails first or a separate `bugfix`/`feature` ([retro-quality](retro-quality.md)); code (`test`, `hook`) unchanged. Step 4 (a separate `bugfix`/`feature`) still applies to `skillsync retro` candidates from failures.
   Without the user's consent change nothing; a proposal does not block `done` of the current run.
4. Record the decision with the command `skillsync retro-decide <project> --cause-key K --decision approved|rejected`, with `approved` with `--owner` and `--case`;
   do not edit the YAML by hand. After the fix: `--decision approved --replace --ref <commit>`, which moves the date to the day of the fix. Implement the fix as a separate `bugfix`/`feature` with this case as the oracle.
5. Subsequent runs verify: `recurred` returns to step 3 with a new case, and `recurred` with `approved` and `fixed: false` means the fix is still pending; `decided` is not proposed again.
   A one-off problem (one run) stays a record with `--cause-key`; do not create a rule for it (exception: a user correction in the quality retrospective, [retro-quality](retro-quality.md)).

## Resume and measurement

A checkpoint is a record of the operator's assessment, not an automatic confirmation of correctness; it is bound by a fingerprint of code and evidence.
`skillsync resume` checks freshness before resuming, and `metrics` aggregates real
measurements. Recording time/cost is optional; a missing measurement stays unknown.
Lead recurring conclusions through `workflow-retro` (if installed — `workflow-extended`), not by growing the core.
