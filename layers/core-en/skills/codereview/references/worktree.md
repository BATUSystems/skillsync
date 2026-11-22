# Worktree for code review

Goal: review the exact code of the PR/changes without touching the main working tree. Everything below except
`git fetch` and `git worktree add` is read-only. Keep the worktree path **outside** the repository
(`<parent directory>/<repo>-review-<id>`) so that it does not end up in the index or in the diffs.

## PR mode

```bash
cd <repo>                                   # existing clone of the repository with the PR
git fetch origin <PR-source> <PR-target>    # names from sourceRefName/targetRefName (without refs/heads/)
SRC=$(git rev-parse FETCH_HEAD^{commit})    # or: git rev-parse origin/<PR-source> after the fetch
BASE=$(git merge-base origin/<PR-target> origin/<PR-source>)
git worktree add --detach ../<repo>-review-pr<ID> origin/<PR-source>
git -C ../<repo>-review-pr<ID> diff --stat $BASE...HEAD
git -C ../<repo>-review-pr<ID> diff $BASE...HEAD > <scratch>/pr<ID>.diff
```

- The PR source commit from the API (`lastMergeSourceCommit`) should equal the `HEAD` of the worktree; report a mismatch.
- A PR from a fork or from a branch unavailable to `git fetch`: build the state from files downloaded through REST
  (`items?...&version=<commit>`) and mark it in the report as a limitation.
- A large diff: write it to a file and give the reviewers the path and the list of files in order of risk.

## Before-PR mode (after `/feature` / `/bugfix`)

1. Base: `base_commit` from the plan/checkpoint; otherwise `--base <ref>`; otherwise `git merge-base HEAD origin/<default>`.
2. Commits of the branch without working changes: `git worktree add --detach ../<repo>-review-<branch> HEAD`.
3. There are uncommitted changes (staged/unstaged/untracked): a snapshot without touching the source:
   ```bash
   git worktree add --detach ../<repo>-review-wip HEAD
   git diff HEAD --binary | git -C ../<repo>-review-wip apply --index   # staged + unstaged
   git ls-files --others --exclude-standard -z | xargs -0 -I{} cp --parents {} ../<repo>-review-wip/
   ```
   Alternatively, when the snapshot fails: review in place (the reviewers are read-only anyway),
   with the diff `git diff <base>` + the list of untracked files prepared by the orchestrator.
4. Diff to review: `git diff <base>` in the snapshot (or `git diff <base>...HEAD` for the commits only).
   Exclude changes from before the task (the initial state recorded by `/feature`).

## Cleanup

`git worktree remove ../<repo>-review-<id>` (without `--force`; unsaved changes → ask) and
`git worktree prune`. Do not remove worktrees that this session did not create.
