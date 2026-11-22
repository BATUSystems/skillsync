# Dispatch start and package claims

Read at the start of `/dispatch <goal>` (steps 1–5) and when registering packages as `task-claim`.

## Start

1. Load the project instructions, the manifest `.github/.skillsync.yaml` (section `dispatch`, see [config.md](config.md))
   and the project's index/plan of packages, if there is one. Create a `run-id` and the state file
   `.skillsync/dispatch/<run-id>.yaml` (ignored by git; `skillsync` appends `.skillsync/`
   to the gitignore: check it).
2. Detect the cards (adapter) and show their list: name, identifier, state, start time. **Ask**
   which may be used (multiple choice). "Idle" does not mean "free". Cloud sessions have no
   local resources (DB, ports); by default they are not used.
3. **Assign aliases**: you = `Dyspozytor`, cards = `karta-1`, `karta-2`, … (in the order of selection,
   names do not come back within a `run-id`; the list is in the file `imiona.md` in the `references/` directory of the `dispatch` skill (a names overlay in the personal layer)). Names given by the user take precedence.
   Renaming a session, if the host does not expose it programmatically, is manual: show a table
   "current name → alias" and verify on the list after the change ([adapter](claude.md)).
   Until then address by identifier.
4. Send every card a `PROBE`. No answer (after one retry) = the card is unavailable;
   do not wait for it, ask the user to restart/close it and give the package to another.
5. Check the resources (`df`, `/proc/loadavg`, ports from `leased_resources`). Propose a parallelism
   limit: min(number of cards, `dispatch.max_parallel`), lower with little disk or
   high load. Warn that high load spoils tests (E2E/Vitest timeouts were observed at
   load 15–20); for E2E tests recommend 2 cards.


## Package claims

4. Register packages as `task-claim` tasks. **Only the dispatcher keeps the claims**, from the repository
   root (claims live in `.skillsync/claims` relative to the cwd; cards work in worktrees and do not
   touch them): `skillsync claim . --task-id <package> --run-id <run-id>.<alias> --action acquire
   --ttl 14400 --run`. Identifiers are slugs `[A-Za-z0-9][A-Za-z0-9_.-]{0,79}` (no `/`, together
   ≤ 80 characters; `<package>` must also fit this pattern). Without `--ttl` 900 s applies, and the
   heartbeat goes only on events, so give `--ttl` explicitly (max 86400; recommendation 14400 = 4 h).
   Renew (`--action renew --run`) on card events. After expiry `renew` gives BLOCKED;
   the renewal is `acquire --takeover --run` by the dispatcher after a `PROBE` of the card (alive and still working
   on the package) and a record of the event in the state file. Reallocation: `--action transfer --to-run-id
   <run-id>.<new-alias>` (only with an active claim) or a takeover after checking.
