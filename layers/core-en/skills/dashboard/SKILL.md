---
name: dashboard
description: "Status page of a feature run: acts, progress, checkpoints, prototypes, spec and plan preview; command skillsync dashboard serve, consents only from a session of an account with the approver role."
user-invokable: true
---

# dashboard: live status of a `feature` run

The server is part of the generator (`skillsync dashboard serve`, Python 3.11+ stdlib, no pip, no CDN, works offline);
this skill supplies the page (`assets/`: HTML, CSS, JS) and the tooltip descriptions (`descriptions.json`). The server reads the files of the
`feature` skill in the project and shows them as a page for viewers (projector, browser through an SSH tunnel), refreshed live
while the agents work. The page state is the same JSON as `skillsync dashboard show` (contract `schema: 1`,
`docs/dashboard-contract.md` in the skillsync repo). It does not execute commands from data. It writes only actions from a logged-in
session: consents (below: "Consent and word buttons"), plan statuses in History and actions on prototypes.

## Starting

```bash
skillsync dashboard serve <project> [--page-port N | --port N | --read-port N --approve-port M] [--read-bind ADDR] [--approve-bind ADDR] [--host H] [--read-host NAME ...] [--approve-host NAME ...] [--local-only] [--allow-remote-approvals] [--allow-plain-login] [--trust-proxy --proxy-address ADDR] [--secure-cookies] [--public-url URL] [--prototype-url URL] [--prototype-port N | --single-origin] [--no-approvals] [--plan docs/plans/<file>.md] [--session-url https://…] [--layer id=path ...] [--standalone] [--all-tabs] [--detach] [--json]
skillsync dashboard url [<project>] [--port N] [--host NAME] [--read | --lan] [--all-tabs] [--json]   # the address of a running server
skillsync dashboard stop [<project>] [--port N] [--timeout S] [--force] [--dry-run] [--json]   # graceful stop
skillsync dashboard show <project> [--json] [--plan docs/plans/<file>.md]   # one-off JSON snapshot (like /api/status, without the server fields)
```

- The server runs in the foreground (Ctrl+C or SIGTERM ends it; on Windows Ctrl+C or CTRL_BREAK to the process group, see docs/dashboard-contract.md "How to stop the server on Windows", result `{"status": "PASS", "stopped": true}`); a refusal to start
  (port out of range, no manifest, no `assets/index.html` in the active layers, a bad `--session-url` or `--plan`)
  = `BLOCKED`, code 2. The page and `descriptions.json` come from the `dashboard` skill of the active layers (`--layer` as in other
  commands; a later layer wins per file).
- `--detach` starts the server in the background (waits up to 30 s for the page, prints the address, the console goes to a log in the machine's state directory; `BLOCKED` `detach_failed` / `detach_timeout`). `dashboard stop` stops the page servers of this project, also foreground ones (codes: 0 stopped or there was nothing to stop, 1 still running, 2 refusal, 3 `--dry-run`); the project server is shared, so it closes the page for all tabs.
- Without port flags one page socket on **5140** (all interfaces): anyone who reaches it reads, and consents are given only by a logged-in session of an account with the `approver` role, locally or through TLS. The same process serves the prototypes from a **second origin** (`--prototype-port`; by default the page port plus 2, and when it is busy, the next free one). Page ports **5100–5199** (never 4444–4448, 9222, 9333); another number for the only socket: `--page-port`. One project
  server per git project (below: "Worktree tabs and History"); the port is chosen per project. A busy port = BLOCKED with the port
  number and the owner (no fallback to another port); a server of an older version = `legacy_server` (stop it; repo: `docs/upgrading.md`).
- Page listening: `--read-bind`, then `dashboard:` (`local_only`, `read_bind`, `approve_bind`) in the manifest, then in the machine config, finally `0.0.0.0`.
  A phone on the same network opens `http://<machine IP>:5140/` (addresses: `dashboard url <project> --lan`), but only reads: consents and `approver` login require loopback or TLS
  (`--allow-remote-approvals` accepts a consent without TLS, but an `approver` session is still created only that way; not recommended). In an untrusted network: `dashboard.local_only: true` (from another machine a tunnel `ssh -L 5140:127.0.0.1:5140 <host>`); `--local-only` rejects every peer from outside loopback.
- Behind a reverse proxy with TLS: `--trust-proxy` with `--proxy-address` (without the list the server does not start), usually also `--public-url` and `--prototype-url`; `--allow-proxy-headers` = BLOCKED.
  `--allow-plain-login` allows `commenter` and `expert` login over HTTP from the network, never `approver`.
- **`--port N`** (one socket): `127.0.0.1`, unless `--host`. **Two sockets** (`--read-port N` and/or `--approve-port M`): the read socket has no write routes and no login
  (for tunnels and links: `dashboard url <project> --read`), the consent socket (by default `127.0.0.1`, `--approve-bind`) has login and consents; `--host` moves only the read.
  `--host`, `--approve-host` and `--approve-bind` without ports are BLOCKED.
- `--single-origin` (no isolation): prototypes on the page origin; consents and actions on prototypes are off, an `approver` account does not log in, a permanent red bar.
- The `Host` check: loopback, the literal IPs of this machine and its own names (except with `--port`), `--read-host`, `--approve-host`, the name from `--public-url`; another = 403 (give the tunnel name in `--read-host`). A name does not grant the right to consent. A session belongs to a host name: `localhost`, `127.0.0.1` and an IP log in separately.
- An `approver` account is created by a **human** in their own terminal: `skillsync prototype user add|roles <project> <e-mail> --role approver --password-prompt --confirm-mixed-fleet` (a password of at least 14 characters).
  The agent does not run `prototype user add|roles` or `prototype login-link`, does not create or change an `approver` account and does not log in to it; it gives the user the address from `dashboard url`.
- Restarting a running instance: take the flags from a record, not from the command line of the process you found: `dashboard:` in the manifest and the machine config give only `local_only`, `read_bind` and `approve_bind`, while hosts, ports and addresses behind a proxy come only from a command recorded in the project instructions; without a record, ask the user. After the restart check that every viewer so far (phone, tunnel) still gets the page and not a 403.
- Without `--plan`: the current plan = the newest `docs/plans/*.md` with `Status: in_progress`, otherwise `approved`, otherwise `draft`, otherwise the last `done`
  (the name with the date, then mtime). On the page a plan switcher (remembered in the browser).
- Act I without a plan: `feature` creates the plan only at "GO plan". When the newest spec `draft`/`approved` that no plan points to
  (`Spec:`) is newer than the automatically selected plan, the page shows act I (`mode: "spec"`): the spec, the run
  (the `Run:` line of the spec, then the directory named after the plan from the `Plan:` line, then the newest `<date>-<spec slug>`), the prototype
  from `docs/prototypes/*/prototype.json` (`requirements` = spec) with screen acceptance counted as in `skillsync prototype list`
  (in progress, read-only, in memory until `comments.json`/`index.html` change); act II = "no plan (waiting for GO plan)".
  Act I before the spec (`mode: "run"`): when there is no spec yet and the newest run directory has `checkpoints.md` newer
  than the automatically selected plan and belongs to no plan (`Run:`) or spec (`<date>-<spec slug>`), the page
  shows "Act I: specification in preparation", the run-id, the last checkpoint and the `waiting:` banner; once the spec is saved the
  `spec` mode takes over.
  An explicit `--plan` or a plan choice on the page switches this mode off.
- The Act I tile has an "Open specification" button, Act II "Open plan" (when the file exists): a jump to the
  **Documents** tab with the file open.
- The **Prototypes** tab (when there are prototypes; without login): title, iteration, accepted and stale screens, the write marker, threads, comments after acceptance, the plan gate and "Open" (a new browser tab). With `prototype.frame` also "Show in tab" (a sandboxed frame; while it is on screen there are no consent buttons).
  A logged-in user has "Log in to prototypes" (session handoff without the roles `approver`, `acceptor`, `developer`) and the "Acceptances and threads" view without a frame (actions with a confirmation window, permission by roles).
- The project wrapper (e.g. `scripts/status.sh`) and the port in the tunnel are added by the project, not by this skill.

## Worktree tabs and History

One page (one project server) shows all working directories of a git project: the main checkout and each of its worktrees is a tab at the top of the page,
and the "History" tab stands at the end of the bar. The inside of a tab is the ordinary status page of that directory (progress, consents, questions, tests, documents), with its own plan,
and run. The tab registry refreshes itself, so a worktree created after the server started appears without a restart.

- **Adding a tab.** `skillsync dashboard serve` without server-settings flags, run in a directory of a git project with a running project server, opens no sockets:
  it adds a tab for that directory and prints its address (`/?tab=<id>`) as the **last line of stdout**
  (messages go to stderr). Without a running server the same command starts the project server: the root is the main checkout, the start tab is the directory it was run in.
  Only a directory that is the main checkout or a worktree with a valid git back link, and is not excluded, gets a tab; exclusion is the file
  `.skillsync/no-dashboard` or the list `dashboard.worktrees.exclude` in the manifest. Adding pins the start directory (always visible), and its tab counts toward the limit of 16.
  The address of the own tab is also given by `skillsync dashboard url` run in its directory; a directory without a tab gets `BLOCKED`.
- **Tab visibility.** The other worktrees have a tab when they have a trace of skillsync work (an entry in `.skillsync/runs/`, or a plan or spec that differs from the main checkout) and were active
  in the last 7 days (`dashboard.worktrees.inactive_days`); the rest sit under "show all" together with the excess over 16 visible tabs.
  Tabs are sorted by last activity, the main checkout first; a tab with a pending consent is marked.
- **Consents.** A consent is always for the worktree and sha of its own tab (the `tab` field of the request); other tabs do not see it. One `approver` session works on all tabs.
- **Standalone server.** When a project server is running and any server-settings flag is given (ports, bind, hosts, proxy and addresses, `--allow-*`, `--local-only`, `--secure-cookies`, `--single-origin`, `--session-url`, `--poll`, `--verbose`),
  a separate server with one tab starts, which other directories do not join and which `dashboard url` without `--port` picks only when no project server runs
  (address: `dashboard url --port N`). When no project server is running, these flags start the project server. A standalone server is always started by `--standalone`, `--no-approvals`,
  `--layer` and `--read-port` without `--approve-port`. The kind of a server is set at start and does not change.
  A preview with another layer (`--port <port> --no-approvals --layer core=<worktree>/library`) is always a standalone server on its own port.
- **Cards (agents)** do not assign ports by hand: `skillsync dashboard serve` in their worktree adds a tab and prints
  the address. `--all-tabs` (the addresses of all tabs, the own one last) is for a human; cards do not use it.
- **Reading.** The page (and the read socket) exposes the status, documents and tests of all tabs of the project (paths, branches, HEAD, plan contents); the boundary is the exclusion of a worktree.
- **Server terminal.** The project server runs as long as its process does: closing the terminal it was started in closes the page for all tabs (the page and `url` warn about it).
  After the connection is lost it is enough to run `skillsync dashboard serve` again in any directory of the project.
- **History** is a tab with finished plans (`done` and `archived`) of all tabs, grouped by tab (address `/?tab=history`); it also shows abandoned `approved` plans
  (no `[x]`, no local run, unchanged for over 14 days according to git) with the action "Abandon". The preview of a plan is read-only, but with the actions "Archive", "Abandon" and "Restore" (for an `approver` session). The same file visible in several
  worktrees is shown once with the number of copies. Historical plans are only in History, not in the plan menu of a tab, but only on a server with more than one tab (a server with one tab shows the menu as before).
  A `done` plan has an "Archive" button, an abandoned `approved` one "Abandon", an `archived` plan "Restore" (an abandoned one returns to `approved`, another to `done`); each asks for confirmation, other statuses have no button. The counter of the tab is the `count` of `GET /api/history`, read in the background only while the browser tab is visible
  and at most every 60 s (at once after History is opened and after a successful status change); the field `tabs[].history` of `/api/tabs` is only a hint (until the first read the bar shows its largest value). The write changes the `Status` line of the plan (abandoning and restoring also the `Porzucony:`/`Abandoned:` line) (route `POST /api/plan-status`, an `approver` session with CSRF, the `tab` of the file's owner, sha check, an audit row in
  `.skillsync/audit/plan-status.jsonl` of the tab's directory) and dirties the `git status` of that worktree; it does not touch `approvals.jsonl`. Excluded worktrees have no plans in History.

## What it reads (formats of the `feature` skill)

| File | What | On the page |
|---|---|---|
| `docs/plans/<plan>.md` | `# title`, `Status:`, `Spec:`, `Spec-sha:`, `Prototype:`, `Run:`; the `## Progress` section with `### Phase …` and steps `- [ ] S1 Title` / `- [x] S1 Title — sha` (ids `S2b`, `A0`, `A-1`) | header, Act II, Act III, phases |
| spec from `Spec:` | `Status:` (draft → in progress; approved/implemented → done; none → planned) | Act I |
| `.skillsync/runs/<run-id>/checkpoints.md` | `RUN=<iso>` (time since start), lines `- HH:MM text` | feed (newest on top), the `in progress:` segment |
| `.skillsync/runs/<run-id>/*.md` | names `spec-review*`, `plan-review*`, `pre-implement*`, `*prototyp*` | stages of acts I and II, document list |
| `.git/HEAD`, `.git/config` (`[remote "origin"] url`), `refs/remotes/origin/<branch>` | branch, HEAD, repository address (Azure DevOps, GitHub, gitlab.com), whether the branch is on the server | header: the branch as a link to the repository (only when it is on `origin`; another host = plain text) |
| the line `PR: #n (link: https://…)` under `## Progress` of the plan (written by `pr-run`) | PR number and address | header: a "PR #n" badge with a link |

- **Act III:** % = checked steps / all steps from `Progress`. "Now in progress" = the id after the segment `in progress:` (up to `;`)
  from the newest checkpoint that contains it; only items starting with an id count (`S2b/S2d (test-writer)` yes,
  `level 3 S1 on the snapshot` no). No phrase: the first unchecked step. The act is finished at `Status: done`.
- Steps listed after `done:` in the checkpoints and unchecked in the plan have a dashed "done per checkpoint" icon
  and a separate, lighter segment of the bar with the caption "17% (+11% to verify)" (globally and per phase). **The main percentage counts
  only `[x]`** (the source of truth); the segment disappears once the step is checked after review.

### The "session is waiting for you" banner

The session appends a progress report (a line `- HH:MM …` in `checkpoints.md`) **after every step OR every 30 minutes, whichever comes
first** (the agent has no clock, it checks the time itself; skill `feature`, `references/measurement.md`). When the plan is
`in_progress`, the newest checkpoint has no `waiting:`, and more than 35 minutes have passed since the last write of `checkpoints.md`
(`SILENCE_MIN`), the page shows "No progress report: for N min" — a signal that the session may have hung. The minutes are counted by the
browser (the field `silence {since, thresholdMin}` in `/api/status`), so the banner appears without a change of files and disappears after the
next report.

A persistent banner under the header (and `⏳ Waiting ·` in the card title) shows what the session is waiting for, what to do and a link.
It disappears when the reason disappears.

- **The marker in a checkpoint** (takes precedence): a segment `waiting: <what>` in the newest checkpoint, in the same line as
  `done: …; in progress: …; risk: …` (`risk:` = risk) (segments are separated by `;`):

  ```
  - 11:20 done: S9; in progress: —; risk: brak; waiting: CP
  ```

  Values: `GO plan`, `GO implement`, `akceptacja prototypu <slug>` (prototype acceptance), `odpowiedź Q1–Qn` (answer to Q1–Qn), `CP`, `READY`, `YES/NO <run-id>/P<n>`
  (another value = a generic message). The newest checkpoint without `waiting:` (or with `waiting: —`) removes the banner, i.e. "the session has resumed".
  A loose "waiting for …" in the text is not a marker.
- **Facts from files** (without a marker): `Status: draft (waiting for GO implement)` of the plan or `… (waiting for GO plan)` of the spec;
  a prototype with unaccepted screens before the plan enters execution (acceptance as in `skillsync prototype list`).
- The link: the spec or plan in the **Documents** tab, the prototype under the prototypes origin. The banner itself approves nothing:
  consent is given by the consent button in the act tile or the banner (section "Consent and word buttons") or by a word typed
  by the user in the session.

A sentence for instructions/prompts (consistent with the "done / in progress / risk" format): *"When you stop at a decision of
a human, append to the checkpoint a segment `waiting: <GO plan|GO implement|akceptacja prototypu <slug>|odpowiedź Q1–Qn|CP|READY|YES/NO <run-id>/P<n>>`;
write the next checkpoint after resuming without `waiting:`."*

### Estimated end (`eta`)

Before a long operation the session appends to the checkpoint a segment `eta: <operation> until HH:MM` (the time from `date`):

```
- 14:05 in progress: F1.2; eta: drawing 12 prototype screens until 15:10
```

The page shows "<operation>: end approx. 15:10" with a live countdown ("left MM:SS", after the term "exceeded by …") in the tile of the active act and at the active
step of the `in progress:` line; the notification bar no longer carries it. An estimate with steps (`in progress: F1.2; eta: …`) goes through the `waiting:` lines and
the term moves by the time the user was waited for (write waiting for CI and resources as `waiting: … (who: agent)`, it is then not counted). As long as the operation lasts, repeat `eta:`
in the next lines (after the reaction of the user the first line may omit `eta:`); when exceeded give a new deadline (without stopping work). The estimate disappears after a line
without `eta:` (except the cases of the contract), after `eta: -` and after the plan is closed; the viewer sees it the same way. The JSON fields: the `eta` and `estimates` sections of the contract.

### Consent and word buttons

**Rule:** a consent is recorded only by the user (a button after logging in or `skillsync approvals record` run by
them); the agent does not record it, trigger it, simulate it or slip in entries (the only exception: the acceptance trace in the `feature` skill). The command accepts `--source cli`
or `ide`; `source: "dashboard"` is recorded only by the page server in its own process. Trust boundaries: section "Security".
Every consent button (Act I "GO plan", Act II "GO implement", Act III "CP" and — in `pr` mode — "READY") has one of
four states (`approvals.actions[].state`):

| State | When | Appearance |
|---|---|---|
| before | the act has not come yet (e.g. "GO implement" in act I, "CP" before execution starts) | no button |
| `running` (in progress) | the act is in progress, but the state is not yet waiting for consent | visible, inactive, hint "waiting for…" |
| `waiting` | GO plan: spec `draft` with the signal `waiting for GO plan` (spec/draft status or `waiting:`), prototype accepted; GO implement: plan `draft` with `waiting for GO implement`, spec `approved`; CP/READY: plan `done`, `waiting: CP|READY` or unpushed commits of the working branch | active for an `approver` session |
| `after` | consent already given (spec/plan `approved`+, a CP/READY entry for the current HEAD) | inactive, "consent given HH:MM"; a second write = refusal |

The "waiting" signal is written by the session (a file status or `waiting:` in a checkpoint): the run files say only that a review round
took place, not that the loop gave PASS.

- A page with a session: only a logged-in user sees the buttons (inactive with a reason without the `approver` role or before a password change), and a click opens a confirmation window without a password (word, document, tab, sha, branch, target, mode); the window closes without sending when any of these values changed.
- **"GO plan"** (spec `draft`, no plan or a draft plan) and **"GO implement"** (plan `draft` without the draft suffix;
  the spec, if there is one, already `approved`): a button in the act tile and in the banner. After confirmation the server
  in its own process changes the status with the same core function as `skillsync spec approve` / `plan approve` with `--expect-sha`
  (`status.set_doc_status`, sha = the version shown on the page; a changed file = refusal "read again"); without
  a `skillsync` subprocess, so the CLI version in PATH does not matter. It is the consent of the user, equivalent to typing the word; the
  `feature` procedure recognizes this status and the session acts according to the resume table in the next turn (the page hints: write
  "continue"). The button is disabled with a reason when the prototype is not accepted or "GO implement" precedes "GO plan".
- The trace of GO consents: an entry `status.record_approval` (`source: "dashboard"`) in the run's `approvals.jsonl` (`sha` = the shown
  version of the file, `shaAfter`, `file`, `run`). Without a run directory there is no trace (response `trail: null`): the consent stays
  in the `Status:` line of the file, which the session and `approvals wait --file` read anyway. The server has no log of its own.
- **"CP" and "READY" (`pr` mode) in the Act III tile** — the button records the consent, it does not open a session.
  After confirmation the server records through `status.record_approval` (the same function as `skillsync approvals record`: file
  lock, refusal of a repeat, 409 `plan_state_changed` when the plan status changed and `delivery_changed` when the branch, target or mode changed) to `.skillsync/runs/<run-id>/approvals.jsonl` an entry
  `{"schema": 1, "at", "word": "CP"|"READY", "sha": <full HEAD shown on the page>, "run", "plan", "target", "branch", "ahead", "mode", "planState", "by", "role", "session", "via", "source": "dashboard"}` (the project server adds `worktree`).
  The consent applies to this HEAD: a new commit = a new consent; a repeated write for the same HEAD and plan status = refusal. The button is active when
  the plan is `done`, the newest checkpoint has `waiting: CP|READY` or the working branch has commits that are not on origin
  (`git rev-list --count origin/<working>..HEAD`, and without a branch on origin `HEAD --not --remotes=origin`; read-only).
  "CP" hands over to `origin/<working>` (core AGENTS: never to the base); in `direct` mode the plan line `Handoff: <branch>`
  changes the target to the given branch (the value must be a valid branch name alone; other text is ignored and the hint says so). The button and the entry show the target (`target`, "CP → main"); the hint
  gives the number of commits and the base. The "N unpushed" badge in the header is a button: the panel shows these commits (`delivery.commits`:
  the short hash and the first line of the description, newest first, up to 20, the rest as "and N older" from `commitsMore`); the descriptions are masked before cutting
  and inserted as text. It requires a run directory. "READY" only in `pr` mode (`delivery.mode` from
  `.github/.skillsync.yaml`; no key = `pr`, a bad value = no buttons). The page does not change the `checkpoints.md` log
  (the session writes it); after the write the tile shows "CP recorded HH:MM", and the banner "the consent is already in the file".
- A session (`feature`) waiting for consent listens (`skillsync approvals wait`) with a wake-up every 30 min and after waking checks the
  consent in the file according to the `feature` skill (“Waiting for consent” and “Acts and consents”). CPD, YES/NO
  and `Q` answers stay in the session (the page does not copy consent words to the clipboard).
- `--session-url` (optional): the session address given at start (e.g. Remote Control), only `https` without credentials;
  the banner then adds "Open session". It is not read from project files, so the agent cannot replace the link.
- `--no-approvals` = a read-only page (no buttons, writing a consent = 403 `approvals_disabled`).

### Phase substeps, tooltips with descriptions and act I signals

- The `Progress` steps may be substeps of phases (`- [ ] F1.2 …`): they are counted separately (bar, "now", the "in progress" list),
  `in progress: F1` points to the first unchecked substep of the phase, the phase shows its own k/n and collapses when closed
  (a click or Enter expands it). Old ids without a dot (`S1`, `S2a`, `A-1`) work as before.
- Every act, stage, gate (GO/CP/READY buttons), phase, substep, banner and checkpoint header has a tooltip with a static
  description: what is happening, who performs it, the result and how the page knows it is done. The descriptions are in the skill's `descriptions.json`
  (`{key: {title, text}}`, consistent with the `feature` procedure), served under `/api/descriptions`; the stage key is given by the server
  (`stages[].key`). The tooltip: hover, keyboard focus (`aria-describedby`, `role="tooltip"`), touch = a tap
  opens and closes, Esc closes.
- Spec review: the `spec-review*` files in the run directory.
- A warning in the Act I tile "no pre-implement before GO plan" when the spec has non-empty sections `## Contracts`, `## Migration`
  or `## Data` (not "not applicable"), and there is no `pre-implement*` in the run.
- The Act I tile has no list of questions: the blocking questions and assumptions of the spec are in the "Questions" tab (group "Questions for implementation"), and the questions to the expert are shown by a toast in the banner "Questions to the expert: N open (M blocking)"
  from the `expertQuestions` field (the `docs/prototypes/<slug>/questions.json` files of all prototypes of the project, not only of the current run), with a link to
  `/_experts` or `/_experts/<slug>`; the toast has no timer and disappears when there are no open ones. Everyone sees it, also without login.
- The Act II tile has "Readiness" from the run's `readiness.md`: PASS green, BLOCKED a warning, no file neutral; the date,
  the Plan-sha abbreviation (a warning when it differs from the sha of the plan file) and a link to the file preview.
- The Act III tile has an expandable list of `Fn.k` substeps with the time from the checkpoints or "no data".
- A stage tile with `state: active` has a pulsing indicator (animation disabled under `prefers-reduced-motion`) and a tooltip "in progress since HH:MM" (`since`);
  the "Prototype" stage, with a valid write marker, shows "N of M screens" with a bar (without `screensPlanned`: "N screens") and the estimated end from `eta`.
- The waiting banner with `waiting[].who` writes "Waiting for: <label> (login)" (a `writing` entry stays "Session working"); no `who` = "The session is waiting for you".
- The Act III progress bar with `progress.activePercent` > 0 has two shades (dark = verified, light = reported and in progress) and the caption
  "N% verified, M% reported or in progress"; `run.parseWarnings` gives one line "N report fragments skipped" above the checkpoints (details in the tooltip).
  The absence of each of these fields gives the appearance as before.

## The "Tests" tab

The "Tests" tab (hidden until the first run) shows the test results of plan steps run by `skillsync test-run`: a section per step (header = the id and title of the step, newest first), only the running one expanded, finished ones collapsed (a red chip in the header and a counter of red ones on the tab); on the "Progress" tab next to a step there is a bar "how many passed out of how many" and a button to the section (`#tests:<step>`). The data is the `tests` field of the JSON and the route `GET /api/tests` (read-only, both sockets). An attempt shows at most 200 groups (failed first, then in progress, the rest by name); when `groupsOmitted` > 0, next to the groups of the attempt a note `role="note"` is shown "Showing N of M groups (failed and in progress first); the counters above cover all tests.", and the full tests of the cut groups are in `GET /api/tests`. When `tests.skipped` is not empty, the tab shows a note (`role="note"`) "Skipped N test result files or directories (could not be taken into account): path (reason), …" (up to 5 paths, "and N more", "at least 20" at 20 entries; with empty sections also "No results read."). **Test results are not masked** (also for a reader without a session on the network; `tests.mask: on` masks them), the rest of the page is; see `docs/security.md`.

## Interface and API

- `/` the page (tabs **Progress**, **Tests**, **Documents**, **Questions**, **Prototypes**; `D` Documents, `P` Prototypes, and without them Progress; a light/dark theme switch in the header, key `T`, the choice in `localStorage`, the system theme by default), `/assets/*` CSS/JS.
- `/api/status[?plan=docs/plans/x.md]` the JSON of the whole view; `/api/doc?path=…` a Markdown preview (own renderer, raw HTML escaped).
- `/events` SSE: `hello`, `change` (an mtime watcher every 1 s: plans, specs, design, domain, run directories, `.git/HEAD` and the branch ref),
  `ping` every 15 s. After `change` the client fetches `/api/status` without a reload; when the SSE fails or is silent for > 40 s, it polls every 5 s
  and returns to live mode by itself. Headers `Cache-Control: no-cache`, `X-Accel-Buffering: no` (tunnel/proxy without buffering).
- The `prototype` fields only from `GET /api/status` (a server with a prototypes origin): `url` (the origin as seen by this requester; `null` = cannot be determined, so no link, frame or session handoff), `served` (the origin shows the prototypes of THIS tab; `false` = no links and actions), `frame` (embedding in a frame is allowed); `singleOrigin: true` only with `--single-origin`. The prototype address: `url` + `/<slug>/`. `up` checks whether the prototype server answers.
- `POST /api/approve` `{kind, path, sha, plan, branch, target, mode[, planState][, tab]}`: only an action that the page is offering right now; `tab` is required with several tabs. `/api/status` has
  `approvals {enabled, here, viewer, actions[], last}`, `session {url}` (for a logged-in user also their account), `spec.fileSha`, `plan.fileSha`, and the banner items `word`/`approve`.
- Sessions: `/_login`, `/_api/v1/session|login|logout|password|handoff`. Prototypes for logged-in users: `GET /api/prototypes[/<slug>]`, `POST /api/prototypes/<slug>/accept|unaccept|restore|resolve|delete`, `POST /api/project/accept` (read socket: 404).
- A reader without a session (also before a password change and on the read socket) gets pseudonyms instead of logins and addresses and names instead of absolute paths, and in consent entries only `word`, `at`, `sha`, `source`, `target`, `planState`; **document contents and test results are public**.
### Page assets and the renderer shared with Skippster

| File | Role |
| --- | --- |
| `index.html` | the roots `<div id="wtabs">` (the tab bar, `createTabBar`; hidden with one tab), `<div id="whistory">` (History, `createArchiveView`; hidden until selected) and `<div id="app">` (the page of a tab); in `<head>` `app.css` and `theme.js`; scripts: `dashboard-render.js`, then `app.js` |
| `dashboard-render.js` | the **renderer**: a pure view JSON (`schema: 1`) → DOM, the whole page skeleton, the icon sprite, tooltips, consents; the same file is loaded by the Skippster webview |
| `app.js` | the **page adapter**: `fetch` of `/api/status`, `/api/doc`, `/api/approve` (headers `X-Status-Board: 1` and `X-Skillsync-CSRF`), `/api/descriptions`, the session (`/_api/v1/*`) and `/api/prototypes`; on a server with tabs also `/api/tabs`, `/api/history`, `/api/plan-status`, `/api/feedback-approve` and the parameter `?tab=<id>` (status, documents, tests, `/events`); SSE `/events` with polling every 5 s and a 40 s watchdog (no stream and no polling for a removed tab); `localStorage` (`dashboard:plan`, `dashboard:theme`, and with tabs `dashboard:plan:<tab>`, `dashboard:view:<tab>` and `dashboard:terminalNote`); the theme; the address `#hash`; a clock every 1 s; keys at the document level |
| `theme.js` | the theme before the first paint (remembered or system) |
| `app.css` | the appearance; the renderer root has the class `dashboard-root` (`display: contents`) |
| `descriptions.json`, `descriptions.en.json` | the tooltip descriptions: Polish and English; the server picks the file by the `lang` of the status (no file = Polish). A host that copies the assets copies both |

The layout depends on the width of the host column, not of the window: the skeleton (header, tabs, `main`, footer) is in the `.dash-wrap` wrapper
(`container: dash / inline-size`), and the thresholds are `@container dash` 1100, 640, 500 and 420 px (compact mode: a panel next to the session).
The `#descTip` tooltip is outside the wrapper (containment breaks `position: fixed`). A host with its own CSS takes over these rules from `app.css` and gives `.dash-wrap` a defined width (an inline-size container has no width of its own content).

The renderer is a UMD: `window.DashboardRenderer` in the browser, `module.exports` in Node. Exports:
`create(root, deps)` (alias `createDashboard`), `createTabBar(root, deps)` (the bar of worktree tabs with alerts), `createArchiveView(root, deps)` (the History view: list, preview, confirmation), `buildSprite(doc)`, `SPRITE` (the data of the `i-…` symbols), `version`, `defaultTexts` (the built-in Polish `ui.*` catalog, `DEFAULT_TEXTS`; generated from `pl.json` by `scripts/sync_renderer_defaults.py`, do not edit by hand).
The tab bar and History use about 90 keys `ui.wt.*` and `ui.history.*` (they are in `defaultTexts`, so a host with its own catalog, like Skippster, gets them at once); a host without tabs (one tab, an editor webview) does not call these functions and supplies nothing, and a host that calls them passes `deps.t` and its own roots (`createTabBar(root, {t, panel, panelId, archivePanel, onSelect, onToggleAll, onDismiss})`, `createArchiveView(root, {t, mountHtml, onSelect, onAction, onConfirm, onCancel, onMore, onReload, onDismiss, onLogin, panelId})`).
`create` changes only its own root: it adds to `root` the class `dashboard-root`, the sprite (skipped with `deps.sprite === false`,
when the host has its own) and a skeleton with fixed identifiers (`title`, `meta`, `live`, `planBtn`, `planMenu`, `waitBanner`,
`tab-progress`, `tab-docs`, `tab-prototypes`, `view-prototypes`, `pCount` (these three only with a non-empty `prototypes[]`), `acts`, `phases`, `feed`, `feedTitle`, `docList`, `docView`, `foot`, `descTip`,
`elapsed`, `ago`, `retryDoc`, `themeBtn` only with `deps.theme`; the `i-…` symbols). The identifiers are global in the document:
two instances of the renderer in one document will collide (one root per document/webview).

The working-directory chip (`project.worktree`; a host with its own sprite, `deps.sprite === false`, must have the symbol `i-folder`, without it the icon is empty, but the chip works): in the header, after the branch chip (alone without a branch) and before the "N unpushed" marker, a `span` with the icon `i-folder` (`worktree: <name>` or `main checkout`; `null`, a missing field and an unknown `kind` = no chip). The description from `descriptions.json` (`workdir_chip`), the full path (for a worktree also `Main checkout: <mainPath>`) in `data-desc-extra`; the name truncated with an ellipsis on an inner `span`. The chip has no action, so `deps` unchanged and no copying: a host without a clipboard loses nothing.
The delivery-mode chip (`delivery.mode`, `base`, `source`): in the header, after the branch block and before the PR chip; "mode: pr", "mode: direct, base: develop", a tooltip with the mode, the base and the source; an invalid mode (`invalid`) is a warning chip "delivery mode: error", not a silent `pr`; no `delivery` field = no chip. A host with a session shows next to it which consent hands the work over, and in the header the account, "Log in", "Log out", "Change password" and a bar with the reason why a consent cannot be given.

The content from the JSON (additively, a missing key = behavior as before): in the act I and II tiles a "Measurement:" row from `timing.acts` (working and waiting time; no consent in the file = time unknown), in act III a "Forecast:" row from `forecast` (by default the range of work until the end of the steps and an indicative end time with the note "indicative"; the host option `tempo` shows only the pace per step; without data the reason; the forecast has a dashed border, the measurement a solid one); step titles (plan, "now", substeps), the "Latest checkpoints" rows, the "waiting for" items and the content of the questions to the expert with in-line formatting (`titleHtml`, `whatHtml`, `actionHtml`, `textHtml` also in `run.checkpoints[]`, inserted by `deps.mountHtml`; without the field plain text; tooltips and attributes always plain text); under the acts a "Result" block (`result`: formatted Markdown from `result.html` and `result.moreHtml`, inserted by `deps.mountHtml`, the rest in `<details>` "Show the rest (N lines)"; without `html` the plan text in `<pre>`, above 12 lines the rest in `<details>`) and a collapsible "Concepts" legend (always; texts from `legend_*` in descriptions.json, as a fallback from the renderer); in the questions toast the addressees (`expertQuestions.runs[].addressees`: "to all experts" or "to: login, …", the viewer nothing); the toast "N new comments after acceptance" from `prototype.commentsAfterAcceptance` (no timer and no "×", a link only with `deps.prototypeUrl`); the "Questions" tab (`tab-questions`, `view-questions`, `qCount`) with two groups: "Questions for implementation" (the blocking questions and assumptions of the spec of the visible run, a "session is waiting" mark by `waiting[]` with `source: "checkpoint"`, "with the expert" at a question handed to the expert) and "Domain expert questions" (the current run: `expertQuestions.current` = `{spec, open, blocking, items}`, the questions tied to the spec path, `null` without them; with no `current` key, i.e. an older server, a local filter by `prototype.slug`); the counter `qCount` = blocking questions for implementation + `current.open`, with the split in the tooltip; the tab is hidden when neither group has content; lists of up to 50 rows, arrow/Home/End navigation in the tab list and the hash `questions`; the document list is `<details class="doc-group">` groups with a `<summary>` header (label and counter): `task` and `run` open by default (can be collapsed), `specs`, `plans`, `design`, `domain` collapsed by default (the state in the renderer state, without `localStorage` and without new callbacks; choosing a document expands its group).

**Theme tokens (part of the renderer contract).** The appearance of the page depends on CSS variables from the blocks `:root` (light) and `:root[data-theme="dark"]` and `@media (prefers-color-scheme: dark)` (dark; both dark blocks have the same values) in `app.css`. A host with its own theme must define each of them, otherwise the element stays without color:
`--bg --panel --panel-2 --line --text --muted --faint --accent --accent-2 --on-accent --accent-soft --glow --done --on-done --done-soft --pending-a --pending-b --warn --warn-soft --planned --planned-soft --bad --bad-soft --radius --shadow --mono --sans`.
A new token is a change visible without a warning for such a host, so **every new token goes into the CHANGELOG** and into this list; it is guarded by a test (`test_skillsync_dashboard_theme_tokens`).

The `deps` contract (only `now` is required):

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `now` | `() => number` | yes | time in ms; the renderer does not read the clock itself (missing = `TypeError`) |
| `actions.approve` | `({kind, sha, path, word, planState, branch, target, mode}) => Promise<{ok, word, text[, code]} \| void>` | no | consent after two clicks within 5 s, and with `session` after a confirmation window; the result = a message in the banner (no result: the host may give `setNotice`); an exception = the message `texts.noConnection`; then `actions.refresh()` |
| `actions.refresh` | `() => void` | no | a request for a fresh state (after a consent) |
| `actions.selectPlan` | `(path \| null) => void` | no | choosing a plan from the menu (`null` = automatic); the renderer also saves it in `storage` (`plan`) |
| `actions.loadDoc` | `(path) => Promise<{path, status, mtime, truncated, html}>` | no | like `/api/doc`; its presence enables the preview in the Documents tab |
| `actions.openDoc` | `(path) => void` | no | opening a document by the host (e.g. an editor), used when there is no `loadDoc` |
| `actions.openLink` | `(kind, id, url) => boolean` | no | `kind`: `branch`, `pr`, `session`, `prototype`, `experts` (the questions-to-the-expert toast); `true` = the host opened it itself (the renderer does `preventDefault`), otherwise the default `<a target=_blank>` |
| `canApprove` | `(json) => boolean` | no | whether this host may give consents; by default `approvals.here` from the JSON |
| `session` | `() => null \| {state: "loading"\|"anonymous"\|"user", name, roles, approver, mustChange, loginAllowed, expired, legacy, tab, passwordMin, passwordSignIn}` | no | the host session: the account header, consents only for `approver`; without it the renderer works without accounts |
| `actions.login`, `actions.logout`, `actions.changePassword` | `()`, `() => {ok}`, `({current, next}) => {ok, code}` | no | the account buttons and the password change window (no password in `deps`) |
| `prototypeFrame` | `boolean` | no | `true` with `prototype.frame` turns on the frame in the Prototypes tab |
| `actions.loadPrototype`, `actions.prototypeAction` | `({slug}) => {ok, data}`, `({slug, action, body}) => {ok, code, text}` | no | the "Acceptances and threads" view (with `session`); the hashes and version from this view are sent |
| `storage` | `{get(k) => string \| null, set(k, v)}` | no | preferences (`v == null` = delete); without it the plan choice is not remembered |
| `theme` | `{isDark() => boolean, toggle()}` | no | the theme switch and the `T` key; without it there is no button |
| `texts` | `{live?, liveTip?, noConnection?}` or `(lang) => {live?, liveTip?, noConnection?}` | no | the function is called at every read with the current language of the status (an exception or a result that is not an object = no override); overriding the texts of the indicator (`live`, `polling`, `down`, `connecting`), their tooltips and the no-connection message; only these keys, the other `ui.*` come from the `texts` field of the status or from the Polish catalog |
| `prototypeUrl` | `(port, slug) => string` | no | the prototype address when the status has no `prototype.url` key (`show`, an older server); without it the link has no `href` |
| `prototypeOrigin` | `(port) => string` | no | in the same case the origin without a path (the questions-to-the-expert toast: `/_experts[/<slug>]`) |
| `mountHtml` | `(el, html) => void` | no | inserting the HTML of the Markdown preview and the `…Html` fields from `GET /api/status` (escaped by the server); the renderer itself does not parse HTML. Without `mountHtml` (e.g. an editor plugin that reads `dashboard show` without these fields) the page shows plain text |
| `forecastView` | `"tempo"` or `"range"` | no | the end-forecast view: `range` (default, constant `FORECAST_VIEW`: a range of work and an indicative end time; replay on real runs: an order of magnitude, not a promise, see the contract) or `tempo` (just the pace per step and the number of remaining steps) |
| `onTitle` | `(text) => void` | no | the window title (`⏳ Waiting · 40% · … — status`) |
| `onNavigate` | `(hash) => void` | no | the view address: `progress`, `tests`, `docs`, `docs:<path>`, `questions`, `prototypes` |
| `scroll` | `{save() => any, restore(v), reveal(el, inDocs), toTop?: {subscribe(cb) => unsubscribe, go()}}` | no | scrolling on refresh and on opening a document; the optional `toTop` enables a floating "to the top" button in the document view and in the Tests view: the host calls `cb({y, viewport})` on scroll and on window resize, the renderer shows the button above 0.75 of the window height and hides it below 0.5, and `go()` scrolls the window to the top; without `toTop` (webview) there is no button |
| `timers` | `{set(fn, ms) => id, clear(id)}` | no | the arming clock (5 s); `setTimeout` by default |
| `raf`, `cancelRaf` | `(fn) => id`, `(id) => void` | no | the bar animation frame; `requestAnimationFrame`/`cancelAnimationFrame` by default; `destroy()` cancels the pending ones |
| `viewport` | `() => {width, height}` | no | the window size for the tooltip; `ownerDocument.defaultView` by default |
| `sprite` | `boolean` | no | `false` = do not create the sprite (the host has its own) |

The methods of the object from `create`:

| Method | Meaning |
| --- | --- |
| `update(json)`, alias `render(json)` | draws the state; the previous state is used to highlight changes; in the Documents view it refreshes the preview |
| `setDescriptions(map)` | the tooltip descriptions `{key: {title, text}}` (`descriptions.json` or `descriptions.en.json`, by language) |
| `t(key, params, n)` | the text of a `ui.*` key in the language of the last status: the catalog from the `texts` of the status, a missing key falls back to the built-in Polish; `n` selects the plural form, `{name}` comes from `params`; an unknown key returns the key |
| `lang()` | the language of the last status (`pl` when there is no `lang` field) |
| `setNotice({ok, word, text} \| null)` | a message in the banner; it fades by itself (ok after 10 s, an error after 30 s), "×" closes at once |
| `setVisible(bool)` | host: the card visible/hidden (`visibilitychange`); the message countdown stops while the card is in the background and restarts after return (visible by default) |
| `setLive(mode)` | the indicator `live` / `polling` / `down` / `connecting` |
| `setView(view, push = true)`, `setDoc(path)` | the tab (as in `onNavigate`); the chosen document (without loading) |
| `tick()` | the host calls it every 1 s: the time of act III, "x min ago", the silence threshold |
| `handleKey(event)` | the host attaches the document `keydown`: `P`/`D`/`T`/`Esc` |
| `sessionChanged()`, `closeDialogs()` | after a session change; when the host hides the page (closes the windows and the frame) |
| `closeMenu()`, `hideTip()` | host: a click outside the root, scrolling |
| `syncTheme()` | after a change of the system theme |
| `state()` | `{view, doc, armed, approving, notice}` (tests, diagnostics) |
| `destroy()` | cancels the timers and frames, removes the listeners, clears the root |

Requirements for the webview host: `approvals.here` is only in `/api/status` (`dashboard show` does not return it), so a host that
allows consents provides `canApprove` (otherwise the buttons are inactive with the reason "Consent only from the browser…"). The host always
takes over `actions.openLink` (returns `true`) and does not leave the default navigation in the webview. The renderer puts into `href` only `http:`/`https:` addresses
(another scheme, e.g. `javascript:`, `data:` = a link without `href`, the text unchanged).

A reader without the right to consent: the field `approvals.viewer` (the read socket, `--no-approvals` or a request that is neither local nor through TLS; no field = false; `approvals.here` is then false). With
`approvals.viewer === true` every consent button except
"consent given" is inactive, and its tooltip has the reason "Consents are given by the presenter on the computer with the session." (instead of the state reason and
"Consent only from the browser…"); the renderer does not draw buttons for copying the consent word (never, also outside the viewer mode; no `actions.copyText` and no clipboard) and does not call
`actions.approve` (also with `canApprove`). The banner (`waiting`, silence), the Readiness section, the questions toast and the "read-only" footer unchanged.

The act sections are drawn by the renderer (`readinessTile`, `substepsBlock`, module functions with the instance context):
Readiness in act II (`readiness`; the link `readiness.source` like other documents: a preview with `loadDoc`, otherwise `openDoc`), Substeps and time in act III
(`substeps`, "no data" at `missingData`; the `details` expansion remembered in the renderer state between `update()`),
The waiting banner also has items from files: "the agent is writing screens (until HH:MM)" (`source: writing`, a valid `.writing.json` marker, replaces the call to accept the prototype) and "expert answer (N blocking)" (`source: expert`, when `expertQuestions.blocking > 0` without `waiting:` in a checkpoint), with a link as in the questions toast; the field `prototype.writing` carries `{since, expiresAt, renewedAt}`.
The working and waiting time in act III (`timing`: working and waiting-for-human time per phase and the sum of the run from the `waiting:` checkpoints and `approvals.jsonl`; a window without a close and a step without markers are "unknown", never zero or an estimate; the `timingBlock` section).
Below 500 px of width acts I and II stand in one column.

The renderer rules (webview CSP: `default-src 'none'`, scripts and styles from a nonce, no `connect-src` and no `unsafe-inline`), guarded
by the static test `tests/test_skillsync_dashboard_renderer.py`: no `fetch`, `EventSource`, `XMLHttpRequest`, `WebSocket`,
`postMessage`, `localStorage`/`sessionStorage`, cookies, global `window`/`document`/`location`/`history`/`navigator`,
access to the document outside the root (`ownerDocument.querySelector`/`getElementById`/`body`/`documentElement` …),
`setInterval`, `Date.now()`; DOM only through `createElement`/`createElementNS`/`textContent`/`setAttribute` (the `h()` helper,
data always as text: a node is only an object with `nodeType`, any other object becomes text), no `innerHTML`/`outerHTML`/
`insertAdjacentHTML`, `eval`/`Function`, the `style` attribute and `cssText` (the width of the bars and the tooltip position through the CSSOM
`el.style.width`), no inline handlers (only `addEventListener`) and no HTML markup in strings. A new renderer callback =
a change of the contract with Skippster (report before writing).

## Security

- A consent (and a status change in History): in order, a local request or TLS (exception: `--allow-remote-approvals`), `X-Status-Board: 1`, `Content-Type: application/json`, an exact `Origin` matching `Host`, `Sec-Fetch-Site: same-origin`,
  a session cookie, a CSRF token, the `approver` role, a password with no forced change pending, consents enabled; a body of up to 4 KB. Every consent and refusal goes to `.skillsync/prototype/run/audit.jsonl`; without an audit row there is no consent (503 `audit_unavailable`).
  A proxy header (`Forwarded`, `X-Forwarded-*`, `Via` and similar) makes the request non-local. An `approver` session expires after 2 h without a write, at the latest after 12 h.
- **Trust boundaries (explicit):** the boundary is the machine and the system user. The fields `by`, `role`, `session` (and `source`) in `approvals.jsonl` are an audit trail, not verification: the reader code does not check them (the conditions a session puts on `dashboard` entries are a procedure rule, not authentication),
  and a process of the same system user can append an entry to the file or run `approvals record`. The rule "the agent does not record consents" is not enforced technically.
- Reading requires no login: anyone who reaches the port sees it (plans, specs, checkpoints, results, reports); secret masking is a heuristic, traffic without TLS is in the clear.
  The `Host` check on all endpoints protects against DNS rebinding.
- The preview whitelist: `*.md` in `docs/specs`, `docs/plans`, `docs/design`, `docs/domain` and `.skillsync/runs/<run>/*.md`;
  rejected are `..`, absolute paths, hidden directories, symlinks leading outside the whitelist, files other than `.md` (e.g. `.env`).
- CSP `script-src 'self'`, frames only from the prototypes origin; the page writes nothing from data (writes: consents and their audit, plan status, actions on prototypes, server state), a 2 MB limit per document and 64 SSE connections.

## Run metrics

Generic run metrics (step times, numbers of reviews and tests from `checkpoints.md` and reports) are computed by the command
`skillsync dashboard metrics`, not by the page; the server has no `--metrics` option.

## Tests

```bash
cd <skillsync repo> && python3 -m unittest tests.test_skillsync_dashboard_server tests.test_skillsync_dashboard tests.test_skillsync_dashboard_renderer
```

`test_skillsync_dashboard_renderer` checks the renderer statically (the rules above, with a negative control) and in Node ≥ 18
(`SKILLSYNC_TEST_NODE` or `node` from PATH; otherwise skipped): it draws every `tests/fixtures/dashboard/*/expected*.json` on a
DOM shadow that throws on injecting HTML and style. It does not check the appearance (CSS, layout): that is the job of a browser.

The HTTP tests choose a free port from the range 5100–5199 (other than 5140) and close the server after the test. The fixtures in `tests/fixtures/`
are anonymized copies of the formats.

## Limitations

- The Markdown renderer is simple (headings, paragraphs, lists with checkboxes, tables, code, quotes, links); no images and no HTML.
- The stages of acts I and II are inferred from file names in the run directory; other naming gives "not completed" stages, not an error.
- The percentage depends on the discipline of checking `[x]` in the plan after every step.

- The `active` stage tile pulses only as long as the run is not silent (`silence`, and without it `run.lastReportAt` and the 35 min threshold); then an empty dot and "no activity for N min", the state in the data stays. The light shade of the progress bar (reported or in progress) has hatching and an outline in the accent color (the contrast of the accent with the track ≥ 3:1 in both themes), and the sample in the legend has the same definition (`--bar-light`).
