# Manual scenario: 3 cards, 4 packages, 1 dependency, 1 leased resource

Goal: verify that the dispatcher asks about cards, probes, assigns, handles a lease
and the merge queue, and resumes from the state file after a restart. A "dry run" version for
manual mode (Codex without messaging between sessions) — at the end. Rules: [SKILL.md](../SKILL.md),
protocol: [multi-session.md](../../feature/references/multi-session.md), state: [state.md](state.md).
The scenario describes the `direct` mode (`PUSH SLOT` on the base, merge queue); in `pr` mode the cards do not get a `PUSH SLOT`, they report the status `review`, and a human merges the PR.

## Data

Repo `/home/user/Repos/Acme/App`, main branch `main` (`1a2b3c4`), `run-id` `2026-10-02-a`.
Goal: `/dispatch close packages W1–W4`. Manifest: `dispatch.max_parallel: 3`,
`leased_resources: [{name: e2e, ports: [5500, 4250], databases: [app_e2e]}]`,
`guards: {min_free_disk_gb: 6, max_load: 12}`, `id_registries: [SEC]`.

| Package | Scope | Dependency | Leased resource |
|---|---|---|---|
| W1 | API endpoint + migration (high risk) | — | — |
| W2 | list view (UI) | — | `e2e` |
| W3 | report export, migration | — | `e2e` |
| W4 | contract tests and API documentation | **W1** (requires W1 on `main`) | — |

## Run

**0. Model gate.** The dispatcher reads its model from the context. `claude-sonnet-5-5` →
"The Dispatcher requires Opus 5.5 or Fable 5.1; switch `/model opus` and repeat `/dispatch …`.
I am not starting." After the switch `claude-opus-5-5` → continue. (Repeated in every turn.)

**1. Question about cards.** `ListAgents` returns: "Manager" (this session), two sessions "New chat"
(duplicate name), "Fabrikam old" (waiting for 2 h). The dispatcher shows the list with identifiers
and states and asks (multiple choice): which may be used. The user picks both "New chat"
and "Fabrikam old". Aliases: `Dyspozytor`, `karta-1`, `karta-2`, `karta-3` (order of selection).
The host does not rename programmatically → a table "current name → alias"; the user renames manually;
the dispatcher verifies `ListAgents` (3 different names, matching the identifiers). State save.

**2. Probe.** `PROBE` to 3 cards. `karta-1` and `karta-2`: `FREE`. `karta-3` ("Fabrikam old"):
no answer → repeated probe → no answer = `unavailable`; request to the user to restart the card.
After the restart a new probe: `FREE` (old identifier replaced, mapping updated).
The dispatcher did not wait for the ghost session with an assignment.

**3. Resources.** `df` 38 GB free, load 2.1 → guards met; limit 3 (≤ `max_parallel`). Warning:
E2E at load > 12 may be unstable; the lease serializes `e2e`, so 3 cards are acceptable.

**4. Dispatch plan.** Graph: W4 depends on W1; critical path W1 → W4. In parallel: W1, W2, W3
(disjoint files; W2/W3 share `e2e` → lease). Plan: `karta-1`←W1, `karta-2`←W2,
`karta-3`←W3, W4 after W1 lands on `main` (`karta-1` preferred, same area). Merge queue:
W1, W2, W3, W4 (the dependency W4→W1 takes precedence over the rule "contract changes last"; W1 is additive for W2/W3, so it goes in first and unblocks W4). The user: "OK, plan". The dispatcher saves the state and does **not** treat this as GO
for the packages. It registers the claims (`task-claim`, `--run-id 2026-10-02-a.karta-1` etc., `--ttl 14400`, from the repo root directory) for W1–W3.

**5. Assignment.** `ASSIGN` to each card (after a current probe): header
`dispatch: 2026-10-02-a from Dyspozytor (session <dispatcher id>)`, `/feature` with the package, `input_commit=1a2b3c4`,
worktree `../App-wt/W1`, database `app_integration_test_W1`, forbidden areas, lease/PUSH
SLOT/ID rules, signature `karta-1/W1`, the note "GO is given by the user in this card". The cards create the worktrees
and databases themselves. The user gives GO to the package plans locally in the cards.

**6. Identifiers.** `karta-1/W1`: `ID REQUEST SEC W1`; `karta-3/W3`: `ID REQUEST SEC W3`.
The dispatcher checks the state registry and the repo (last SEC-32): `ID GRANT SEC W1 SEC-33`,
`ID GRANT SEC W3 SEC-34`. Until then the cards used `SEC-TBD-<package>-1`.

**7. Lease.** `karta-2/W2`: `LEASE REQUEST e2e W2` → the dispatcher checks ports 5500/4250
(free), no processes, load → `LEASE GRANT e2e W2`. `karta-3/W3`: `LEASE REQUEST e2e W3` →
`LEASE QUEUED e2e W3` (position 1). `karta-2/W2`: `LEASE RELEASE e2e W2 exit=0 log=.../e2e-W2.log`
→ the dispatcher reads the log (number of scenarios, exit 0, log time after the last code change),
checks that the ports are free → `LEASE GRANT e2e W3`.

**8. Report and merge queue.** `REPORT DONE W1 commit=f00d111 checks=… debt=… retro=0` → the dispatcher checks the
evidence in the logs and `skillsync retro` (package acceptance), marks W1 `review→done`. `PUSH SLOT W1` → `PUSH GRANT W1` (first in the queue)
→ `PUSHED W1 9e8d7c6` → `git ls-remote origin main` = `9e8d7c6` (verified) → `MASTER MOVED
9e8d7c6 "endpoint+migration W1" rebase=yes, migration=regenerate` to `karta-2` and `karta-3`
(W3 has a migration: the later integrator regenerates it after the rebase). Dependency W4 satisfied →
`ASSIGN W4` to `karta-1` with `input_commit=9e8d7c6`.

**9. Restart.** W2 got a `PUSH GRANT` and pushed the changes (`PUSHED W2` sent), W3 holds the `e2e`
lease, W4 in progress. The dispatcher session dies (VS Code restart) before it saves `PUSHED W2`. State on
disk (fragment): `merge_queue: [{W2, granted}, {W3, waiting}]`, `leases: [{e2e, holder: W3,
released_at: null}]`, `master.head: 9e8d7c6`.

**10. Resume.** A new session, model Opus 5.5 (gate), `/dispatch resume 2026-10-02-a`.
The dispatcher loads the file; `ListAgents` shows three "New chat" (names reset) — it matches by
identifiers and asks for a manual rename per the table; verifies. `STATUS?` to `karta-2`
("DONE, pushed `a1b2c3d`"), `karta-3` ("BUSY, e2e in progress"), `karta-1` ("W4, waiting for the user").
Reconciliation: `git ls-remote` shows `main = a1b2c3d` ≠ `master.head` (`9e8d7c6`) → `W2` really
pushed (state corrected to `verified` after hash verification, `MASTER MOVED` sent to
`karta-3`/`karta-1`). The `e2e` lease: ports taken by the `karta-3` process → kept
(not released automatically). The dispatcher shows the reconstructed `/dispatch status` and the discrepancies;
after the user's acceptance it resumes the loop (`karta-3` releases the lease, push W3, `ASSIGN`/end).

**11. Completion.** After `DONE` of W3 and W4: a summary report (packages, commits, evidence, debt,
open decisions). A question about cleaning up worktrees and test databases — only with consent.

## Acceptance criteria

- Asked about cards and did not use "idle" as "free"; aliases and mapping verified on the list.
- A probe before every assignment; a ghost did not block a package.
- Consent to the plan ≠ GO; GO in the cards locally.
- IDs only from the dispatcher; the `e2e` lease one at a time, GRANT after a verified RELEASE.
- A push to the base (`direct` mode) only in a slot, hash verified on the remote repo; `MASTER MOVED` after every push.
- Resume from the file, reconciliation with the actual state (ls-remote, processes, cards) and confirmation
  by the user before resuming; model gate in every turn.

Variants for a separate run: `MASTER RED` (a fixed date in a test: one owner of the fix,
the rest do not fix), `/dispatch drain` with a user GO in the card against draining (state
updated, no dispute), a model drop midway (state save, no grants).

## "Dry run" version (Codex manual mode)

A host without messaging between sessions; the reviewer plays the user and the cards, pasting replies.
The same run, differences:

1. Step 0: the dispatcher is not sure of the model tier → asks the user to confirm
   "the strongest available reasoning model" before the start.
2. Step 1: instead of `ListAgents` it asks "how many cards and how are they marked?"; the user: "3 windows";
   the dispatcher assigns aliases and asks for the windows to be marked `karta-1, karta-2, karta-3`. `mode: manual`, `session_id: null`.
3. Every message (`PROBE`, `ASSIGN`, `LEASE GRANT`, `PUSH GRANT`, `MASTER MOVED`) is a block
   `>>> PASTE IN CARD: <Alias> … <<< END`, after which the dispatcher ends the turn and asks for the pasting of
   replies with a signature. No paste ≠ a reply; no signature → a follow-up question.
4. Ghost path: the user does not paste a reply to a `PROBE` after a repeated request → `unavailable`.
5. Verifications (`git ls-remote`, logs, `df`, load) are done by the dispatcher on its own.
6. Restart: the user opens a new session, `$dispatch resume 2026-10-02-a`; the dispatcher reads the state
   file, asks for the pasting of current `STATUS` from the cards (the dispatcher prints `STATUS?` blocks),
   reconciles with `git ls-remote` and confirms with the user.

Dry-run acceptance: all the criteria above + every block has an explicit addressee, state saved
after every event, no use of `ListAgents`/`SendMessage` and no assumed replies.
