# Coordination loop

Read on every message from a card (the dispatcher's table of reactions).

| Card message | Dispatcher reaction |
|---|---|
| `FREE` / `BUSY` / `STATUS` | update the state (including `last_message` from the `date` output, so that the pulse does not consider the card idle); with `waiting-on=user` do not nudge; a message without a first `date:` line (the `date` output) → ask for a repeat, do not write hours from the body into the state |
| `LEASE REQUEST` | check the actual state (ports, processes, disk, load, database) → `LEASE GRANT` or `LEASE QUEUED` |
| `LEASE RELEASE` | verify the exit code and the log, processes stopped, ports free → only then the next GRANT |
| `PUSH SLOT` | merge queue (`direct` mode; in `pr` there is no `PUSH SLOT` on the base, readiness = status `review`); `PUSH GRANT` one at a time; contract changes last |
| `PUSHED <hash>` | `direct` mode: `git ls-remote` — the hash on the remote repo; `MASTER MOVED` to the other cards. `pr` mode: no `PUSHED` on the base; you detect the PR merge by the PR state at the host and `git ls-remote` of the base, then `MASTER MOVED` |
| `ID REQUEST` | assign a number from the registry (state + repo); until then the card uses a working label |
| `REPORT DONE` | [package acceptance](#package-acceptance-report-done); mark the package; assign the next one |
| `REPORT BLOCKED` | examine the evidence; escalation to the user or reallocation |
| main branch red | confirm the evidence on a clean master; `MASTER RED` with one owner of the fix |

Monitoring without messages: once after an event (e.g. the user returns) check the cards, the remote
branch and the disk. An idle card without a report → one `STATUS?` probe. With `dispatch.pulse` enabled, a tick of the [pulse](pulse.md)
does this cyclically (read-only, probe only past the threshold, `PushNotification` notification); the reaction to `waiting-on=user`
is unchanged: you do not nudge the cards, the pulse notifies the user.

## Package acceptance (`REPORT DONE`)

Check the evidence in the logs and `skillsync retro <main-repo> --run-id <run>.<package>`. You assess the last attempt (`.N` with the largest N), on a retry directly through `--run-id <run>.<package>.N`.
An earlier `incomplete` attempt is allowed, and its absence with a known retry means rejection; when the previous worktree and the copy do not exist, the user decides. Rejection: `REPORT REJECTED` with a reason.
- Estimates: `skillsync dashboard metrics <main-repo> <run>.<package>[.N] --json` should have at least one series in `estimates[]` ([worker mode](../../feature/references/worker-mode.md)). A missing series is a remark in the report and a request to apply the rule in the next package, not a rejection.
- `incomplete` through a step ending in `NOT_RUN` or `BLOCKED`: `REPORT BLOCKED` or a user decision; never a conversion to `PASS`.
- Any other `incomplete`, also a step ending in `FAIL` with `--final` present, as well as `no_data`, `unreadable`, a missing run or `rubric.evidence.status` other than `available`: rejection. Lost or changed evidence gives `partial` permanently: a new attempt `.N` or a user decision.
- A report without a first line `date: <date output>` or with hours in the body that are not in the logs or in that `date`: `REPORT REJECTED reason=missing date line` (rule: [multi-session](../../feature/references/multi-session.md#verification-and-reports)). Times in the dispatch state also only from `date` or the logs.
- `unexplained`: the card supplies the cause or new evidence with a new record, not by editing a file.
- A missing log or a log older than the code: rejection with a request for an `evidence-gap` record; the gap entry in the package's `evidence` stays after it is filled in.
- With `retro=<n>` > 0 check whether the `retro-*` files are in the copy of the run in the main checkout, and pass the user a list of "YES <run-id>/P<n>" commands to be issued in an ordinary `feature` session in the main checkout (not to the dispatcher) for proposals in the `deferred` state from the copies of `retro-signals.json` and `retro-decisions.json` (you do not grant the consent yourself; cards give proposals also from signal repetitions, not only from user corrections); missing files with `retro=<n>` > 0 is a rejection as with missing evidence. `REPORT DONE` without the `retro=` field is a rejection with a request to complete the report.
- A package changes `docs/domain`, `docs/lessons` or the manifest: `skillsync doctor` in the package worktree, compared with the base. A new error = rejection; a bad index also blocks `skillsync route`. Missing installed skills in the worktree is expected.
