# Mode for untracked specs and plans (`specs.tracked: false`)

Some repositories ignore `docs/specs` and `docs/plans` (a rule in the tracked `.gitignore`). There "plan and spec = commit" does not work: the files do not reach history.
This reference describes only the deviations from the procedure; the tracked mode (`tracked: true`, the default) works unchanged.

## Detection

The source of truth is `skillsync route <project>`: the `specs` field (`tracked`, `archive`, `source`). `source` values: `manifest` (key `workflow.specs.tracked`/`archive` in `.github/.skillsync.yaml`),
`gitignore` (result of `git check-ignore --no-index -v` for `docs/specs/x.md` and `docs/plans/x.md`; both ignored = `tracked: false`), `default` (`tracked: true`), `invalid` (a wrong manifest key or `tracked: true` with a rule ignoring both directories).
`invalid` = do not assume a mode, ask. Without the CLI, check these two paths by hand with that command, limiting the sources to the `.gitignore` in the repository tree (no global ones and no `info/exclude`), and check the manifest key; the manifest wins over `.gitignore`.

## Where the spec and plan live

The spec and plan stay in the working tree of the run (`docs/specs`, `docs/plans` of the worktree or of the directory `Directory: main`); they are not in git history.
You move the spec draft from the main checkout to the worktree at "GO plan": copy, check the sha of the source and of the copy, then the source to the trash (never `rm`). Different sha = STOP and ask.

## Durability: the snapshot

Durability comes from `skillsync spec snapshot <project> --run-id <id> [--spec <file>] [--manifest <file>] [--replace] [--json]`: it copies the spec (from the plan's `Spec:` line with `Run: <id>`) and the plan to
`.skillsync/runs/<id>/docs/specs|plans/` and, when `workflow.specs.archive` is set, to an archive outside the repository (`<archive>/<project>/<id>/specs|plans/`). Repeating it is idempotent;
a target changed by someone else is `BLOCKED` (overwrite only with `--replace`, after a human decision). The snapshot lies in the worktree, so it alone does not protect against removing it.
The spec archive is optional and is neither a store of runs nor `plan archive`.

When you take a snapshot: at "GO plan" (after `Spec-sha` and the plan draft), after every change of the spec or plan worth keeping (closing a phase, a spec fix with a new `Spec-sha`) and mandatorily at the closing of the run, before the copy of the run outside the worktree.

## "GO plan" without a commit of the spec and plan

At "GO plan" you do everything as usual except the commit of the ignored files: you create the branch and the working directory (`Directory:`), write `Spec-sha` in the plan, take a snapshot and the `go-plan` checkpoint
(`skillsync checkpoint <project> --run-id <id> --step go-plan --status PASS --evidence <snapshot file> --next <next step>`; `go-plan` is a step-name convention, the evidence is the snapshot file in `.skillsync/runs/<id>/docs/`). The local commit covers only what is tracked (e.g. the prototype); when there are no tracked changes, there is no commit and you do not make an empty one.
The first commit of the run then appears in the code step. At "GO plan" you check that `Spec-sha` matches and that the snapshot exists; you run `spec snapshot --check --to <copy root>` only when cleaning up the worktree (a bare `--check` in a linked worktree without `--to` gives `BLOCKED`).

## Acceptance and consents

A `CP`/`READY` entry in `approvals.jsonl` has the field `planState` (the status of the run's plan at the moment of consent; `approvals record` fills it itself; the flag `--expect-plan-state <status>` refuses the write when the plan status is different). Acceptance (`needsHuman: acceptance`) is closed by the last entry
with `planState: done` when the plan is `done`; git history is not needed. An entry without the field when the plan is `done` does not give the state "consent given": after closing, type the word again. The same applies to a `CP`/`READY` written while the plan is `in_progress`: it does not close acceptance after `done`, the word has to be repeated while the plan is `done`. `approvals wait` does not distinguish `planState`: when waiting for consent after the plan is closed, pass `--since <STOP time>`, otherwise an old `CP` from the implementation ends the wait.

## `pr-run` and entry from a key

- PR description without a plan path: a summary of the spec and `Spec-sha`.
- The commits "plan", "plan with a `PR:` line" and the last one do not include the plan or the spec (instead of a plan commit there is a snapshot).
- No commit in step 4 of `pr-run` is an expected state: the draft PR is created only after the first code commit (a PR with no commits ahead of the base is rejected, empty commits are forbidden).
- The `PR:` line goes only into the local plan and the snapshot; `--resume` on another machine requires moving the snapshot by hand.
- `pr-after-approval`: you skip the spec and plan at `git add` (they are ignored), and the PR description summarizes the spec with `Spec-sha`.

## Worktree cleanup

Before removing the worktree (`Directory: worktree`) take a snapshot and a copy of the run outside the worktree (it contains `docs/`), then run
`skillsync spec snapshot <project> --run-id <id> --check [--to <copy root>]`. Cleanup condition: `PASS` for a target outside the worktree (archive or `--to`). `BLOCKED` (also when `--to` is forgotten
in a linked worktree, or when the archive is unavailable) = do not remove; fix the copy and repeat. With `Directory: main` the ignored files are exposed to `git clean -x`/`-X`: do not run them without a snapshot.
