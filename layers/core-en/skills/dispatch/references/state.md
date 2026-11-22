# Dispatch state file

Path: `.skillsync/dispatch/<run-id>.yaml` in the directory of the repository the dispatch concerns.
The `.skillsync/` directory is ignored by git (local, per machine, no commit/push).
The file is the dispatch memory after a restart — card names and the conversation context are lost.

Rules:
- It is written **only by the dispatcher** (cards only report), atomically (temporary file +
  rename), after every event that changes the state and before every turn that may be interrupted.
- No secrets and no raw authentication data (connection strings only from templates,
  without passwords). Logs and evidence — as paths, not content.
- `run-id`: `YYYY-MM-DD-<slug>[-<letter>]`; one active dispatcher per `run-id`.
  Taking over a `run-id` by a new session = `/dispatch resume` (below), not a parallel write.
- Write the dispatch and package `status` only with values from the schema (a package also `go`). Other values are shown by `skillsync dispatch` as `unknown`.
- The bookkeeping of the [pulse](pulse.md) (last tick, findings, reported markers) is in a SEPARATE file `.skillsync/dispatch/<run-id>.pulse.json`, not in the dispatch
  file (a write there would refresh the file modification time and kill the `stale-state` finding); the `skillsync dispatch` view does not read it.
- A read-only view for IDEs and tools: `skillsync dispatch <path> [--run-id <id>] [--json]`. A whitelist of fields, no free
  text, `authoritative: false`; the live truth is the cards and the remote repo. Contract: `docs/domain/dispatch-state-view.md` in Skillsync.

## Schema

```yaml
version: 1
run_id: 2026-10-02-fabrikam-a
goal: "Closing packages F05–F08"
repo: /home/user/Repos/Org/App          # one repo per dispatch
base_branch: main
base_commit: 1a2b3c4                    # master at the time of the start
mode: messages                          # messages | manual (Codex manual mode)
status: running                         # planning | running | draining | done | aborted
model_gate: { required: "opus-5.5|fable-5.1", confirmed: "claude-opus-5-5", at: "2026-10-02T10:05:00Z" }
parallel_limit: 2                       # min(cards, dispatch.max_parallel), lowered by guards
cards:
  - alias: Dyspozytor
    session_id: "<id>"
    session_name: "Manager"
    state: dispatcher
  - alias: karta-1                     # key; name from the imiona.md list; does not come back within a run-id
    session_id: "<id>"                  # null in manual mode without an identifier
    session_name: "Fabrikam F05"           # last confirmed name
    state: working                      # probed | free | busy | working | waiting_user | unavailable | released
    package: F05
    last_probe: "2026-10-02T10:07:00Z"
    last_message: "2026-10-02T11:42:00Z"
    worktree: ../app-wt/F05             # created by the card (reported)
    branch: f05-notifications
    test_db: app_integration_test_F05
packages:
  - id: F05
    title: "Notifications"
    depends_on: []
    risk: medium                        # low | medium | high
    areas: ["src/Notifications/**"]
    forbidden_for_others: true
    resources: [e2e]                    # leased resources the package needs
    status: in_progress                 # todo | assigned | in_progress | review | done | blocked
    card: karta-1
    input_commit: 1a2b3c4
    result_commit: null                 # hash/branch from REPORT DONE
    evidence: []                        # paths of logs verified by the dispatcher and entries about evidence gaps (do not remove)
    claim: { task_id: F05, run_id: "2026-10-02-fabrikam-a.karta-1", ttl: 14400 }   # slug without "/", ≤ 80 characters; claims only from the repo root directory
leases:
  - resource: e2e
    holder: F05                         # package
    card: karta-1
    granted_at: "2026-10-02T12:00:00Z"
    released_at: null
    release: null                       # { exit: 0, log: ".../e2e.log", verified: true }
    queue: [F06]                        # queue of waiting packages (FIFO)
merge_queue:                            # PUSH SLOT order (direct mode; in pr a human merges)
  - { package: F06, state: waiting }    # waiting | granted | pushed | verified
  - { package: F05, state: waiting }
master:
  head: 1a2b3c4                         # last verified hash of the remote branch
  red: null                             # { hash, symptom, owner: F05 } on MASTER RED
id_registry:                            # assigned identifiers (the dispatcher is the only source)
  SEC: [{ id: SEC-33, package: F05, label: SEC-TBD-F05-1 }]
  ADR: []
decisions:                              # user decisions and open questions
  - { id: D1, question: "RBAC scope in F06", state: open, asked: "2026-10-02T11:00:00Z" }
  - { id: D2, question: "Order of F05/F06", state: answered, answer: "F06 after F05", by: user, at: "2026-10-02T10:20:00Z" }
debt:
  - { package: F05, item: "No Vitest test for the empty state", source: "REPORT DONE" }
events:                                 # short journal (last ~50) for diagnostics and resume
  - { at: "2026-10-02T12:00:00Z", msg: "LEASE GRANT e2e F05" }
```

The fields are minimal; add only what is needed for resume. Keep an unknown field.

Times read by the pulse (`cards[].last_message`, `cards[].last_probe`, `decisions[].asked`, `leases[].granted_at`): ISO with an offset (e.g. the output of
`date -Iseconds`); a time without a zone and text are skipped by the pulse (for cards and leases the `age-unknown` finding). Write a question to the user with `asked` and
`state: open` (after the answer `state: answered`): a question without `asked` is INVISIBLE to the pulse (no finding, not even `age-unknown`), so it will not count
how long it has been waiting.

## Resume

`/dispatch resume <run-id>` (the model gate applies, as always):

1. Load the file. No file or `status: done|aborted` — report and ask the user.
2. Do not trust old card states: `ListAgents` (or manual mode: ask for the window markings),
   match the cards by identifier, then by name/alias. Changed names → ask for a
   renewed renaming per the table from the adapter. Cards that are not there — `unavailable`.
3. Send `PROBE`/`STATUS?` to cards in the states `working|waiting_user|busy|assigned`; the results
   are the current truth, the file — a hypothesis.
4. Reconcile with the facts: `git ls-remote` (whether `PUSHED` really are on the remote repo, whether
   `master.head` changed → `MASTER MOVED` to the cards), the lease state (processes, ports:
   a lease without a live card is `released` only after checking, never automatically),
   `task-claim` (who holds the package). In `pr` mode also reconcile the PR state of every package in `review` at the host (open, merged,
   closed); a merge by a human unblocks the dependent cards.
5. Show the user the reconstructed `status` and the discrepancies; after their acceptance resume the loop. With `dispatch.pulse.enabled: true`
   also print the `/loop` line of the pulse ([pulse.md](pulse.md#start-and-life-of-the-loop); the loop dies with the session).
   Do not resume assignments, leases or PUSH SLOT without this confirmation.
6. Record the `resumed` event and the dispatcher's new `session_id` in the state.

A damaged or inconsistent file: keep a copy (`<run-id>.yaml.bak`), reconstruct the state from the facts
(cards, remote branch, claims) and mark which fields are unconfirmed.
