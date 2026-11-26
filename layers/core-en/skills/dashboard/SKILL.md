---
name: dashboard
description: "Status page of a feature run: acts, progress, checkpoints, spec and plan preview; command skillsync dashboard serve, the only write is consents from localhost."
user-invokable: true
---

# dashboard: live status of a `feature` run

The server is part of the generator (`skillsync dashboard serve`, Python 3.11+ stdlib, no pip, no CDN, works offline);
this skill supplies the page (`assets/`: HTML, CSS, JS) and the tooltip descriptions (`descriptions.json`). The server reads the files of the
`feature` skill in the project and shows them as a page for viewers (projector, browser through an SSH tunnel), refreshed live
while the agents work. The page state is the same JSON as `skillsync dashboard show` (contract `schema: 1`,
`docs/dashboard-contract.md` in the skillsync repo). It does not execute commands from data. The only write is the user's consent from
a button (below: "Consent buttons").

## Starting

```bash
skillsync dashboard serve <project> [--port N | --read-port N --approve-port M] [--read-bind ADDR] [--approve-bind ADDR] [--allow-proxy-headers] [--prototype-port N] [--host H] [--approve-host NAME ...] [--read-host NAME ...] [--no-approvals] [--plan docs/plans/<file>.md] [--session-url https://…] [--layer id=path ...] [--standalone] [--all-tabs] [--json]
skillsync dashboard url [<project>] [--port N] [--read | --lan] [--all-tabs] [--json]   # the address with the token of a running server
skillsync dashboard show <project> [--json] [--plan docs/plans/<file>.md]   # one-off JSON snapshot (the same as /api/status)
```

- The server runs in the foreground (Ctrl+C or SIGTERM ends it; on Windows Ctrl+C or CTRL_BREAK to the process group, see docs/dashboard-contract.md "How to stop the server on Windows", result `{"status": "PASS", "stopped": true}`); a refusal to start
  (port out of range, no manifest, no `assets/index.html` in the active layers, a bad `--session-url` or `--plan`)
  = `BLOCKED`, code 2. The page and `descriptions.json` come from the `dashboard` skill of the active layers (`--layer` as in other
  commands; a later layer wins per file).

- Without port flags the server runs in the two-socket mode: read on **5140** (all interfaces, including the local network),
  consents on **5141** (this machine only). The allowed port range is **5100–5199** (never 4444–4448, 9222, 9333). One project server
  per git project (all its worktrees in one page, below: "Worktree tabs and History"); the port is chosen per project so that several projects can run in parallel. A busy port = BLOCKED with the port
  number and the owner (no fallback to another port).
- Two-socket mode, listening: read on `0.0.0.0`, consents on `127.0.0.1`. A phone on the same network opens the page at
  `http://<machine IP>:5140/` (addresses: `skillsync dashboard url <project> --lan`). Read: `--read-bind`, then `--host`
  (moves only the read socket), then `dashboard:` (`local_only`, `read_bind`, `approve_bind`) in the manifest, then in the machine config.
  Consents: `--approve-bind`, then `dashboard:`, finally `127.0.0.1`; `--host` does not touch them.
- `--host` and `--approve-host` without ports are BLOCKED with a hint: the old mode `--port N --host A` / `--port N --approve-host H`,
  the new one: `--read-bind` and `--approve-bind`.
- The `--port N` mode (one socket, the old mode) listens on `127.0.0.1`. Through the tunnel `ssh -R N:127.0.0.1:N …` the browser on the other side
  sends `Host: localhost`, so it works without flags; when viewers enter under the tunnel's name: `serve --port N --read-host <tunnel name>` (read) or `--approve-host <tunnel name>` (read and consents), otherwise 403. `--port N --host 0.0.0.0` = all interfaces (preview from a phone or
  another machine in a **trusted** network); every endpoint, however, answers only under a `Host` header from loopback or from
  `--approve-host`, so the address from the address bar of another device (name or IP) must be given in `--approve-host`
  (otherwise 403). `--approve-host` allows read and consents; `--read-host <address>` (repeatable) allows read only:
  a demo through a tunnel in which viewers send `Host: <name>:5140` sees everything (including assumptions and the "waiting:" banner),
  but `POST /api/approve` from the viewer's browser gets 403, and `/api/status` has `approvals.viewer: true` and `here: false`; consents are given
  by the presenter under localhost. This is not authentication: a client forging the `Host` header (e.g. `curl -H 'Host: localhost'`
  through a tunnel) will record a consent, and a tunnel rewriting `Host` to localhost gives every viewer the write; with untrusted
  viewers use `--no-approvals`. The two-instance model for untrusted viewers: the presenter on loopback with consents, the viewers on a separate instance `skillsync dashboard serve <project> --port <other port> --no-approvals --read-host <host>` (no write, the same project; only `approvals.jsonl` is shared). The viewer also sees the `/api/doc` preview: the document and the whole response are masked (mask before cutting, UTF-16 after the BOM decoded, NUL stripped), but masking is a heuristic, not a guarantee. A preview without consents for everyone = `--no-approvals`. At start the server
  prints a warning and the LAN addresses to open.
  The prototype link uses the host under which the page was opened, so it also works from another device (when the prototypes also listen on the LAN).
- **Two sockets** (`serve --read-port 5140 --approve-port 5141`): the role depends on the port, not on the `Host` header.
  Expose the **read port** (5140) through a tunnel or a link to an outside person (`skillsync dashboard url <project> --read`):
  it has no consent route at all (`POST /api/approve` = 404) and no token. The **consent port** (5141) serves only you: the address
  with the token is given by `skillsync dashboard url <project>`; **never point a tunnel or a link at it**.
  Consents from a phone in a trusted network: `--approve-bind 0.0.0.0` (or `approve_bind: 0.0.0.0`); then consents enter at
  `http://<machine IP>:5141/` without `--approve-host`, and the token goes over unencrypted HTTP. A page for this machine only:
  `dashboard.local_only: true` or `--read-bind 127.0.0.1`. In an untrusted network (hotel, conference, hotspot) use
  `dashboard.local_only: true` and enter consents through the tunnel `ssh -L 5141:127.0.0.1:5141 <host>`. Give the read port to
  a tunnel or a link for an outside person (`skillsync dashboard url <project> --read`), never the consent port.
- Read in this mode has no login: everyone on a network that sees port 5140 sees the plans, specs, results and run reports
  (secrets masked heuristically). The traffic is unencrypted.
- **One socket** (`--port N`): listening on `127.0.0.1` (unless `--host`), the role from the `Host` header.
- Before starting the first server stop other `dashboard serve` on ports 5140 and 5141 (or on those given by `--read-port`/`--approve-port`):
  the ports must be free, otherwise `BLOCKED` with the port number and the owner, without side effects.
- Restarting a running instance: take the flags (`--read-bind`, `--read-host`, `--approve-host`, ports) from a record, not from the command line of the process you found: `dashboard:` in the manifest and the machine config give only `local_only`, `read_bind` and `approve_bind`, while hosts and ports come only from a command recorded in the project instructions; without a record, ask the user for the full list of hosts. After the restart check that every viewer so far (phone, tunnel) still gets the page and not a 403 with the `Host` header.
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
  An explicit `--plan` or a plan choice on the page switches this mode off. The alternative (a plan skeleton at the start of act I in `feature`) is
  a core change, outside this skill.
- The Act I tile has an "Open specification" button, Act II "Open plan" (when the file exists): a jump to the
  **Documents** tab with the file open.
- The link to the prototype (with the counter of accepted screens) sits next to the **Progress**/**Documents** tabs; on a phone
  just the icon with the counter.
- The project wrapper (e.g. `scripts/status.sh`) and the port in the tunnel are added by the project, not by this skill.

## Worktree tabs and History

One page (one project server) shows all working directories of a git project: the main checkout and each of its worktrees is a tab at the top of the page,
and the "History" tab stands at the end of the bar. The inside of a tab is the ordinary status page of that directory (progress, consents, questions, tests, documents), with its own plan,
run and consent token. The tab registry refreshes itself, so a worktree created after the server started appears without a restart.

- **Adding a tab.** `skillsync dashboard serve` without server-settings flags, run in a directory of a git project with a running project server, opens no sockets:
  it adds a tab for that directory and prints its address with the token of its own tab (`/?tab=<id>#zgoda=<id>.<token>`) as the **last line of stdout**
  (messages go to stderr). Without a running server the same command starts the project server: the root is the main checkout, the start tab is the directory it was run in.
  Only a directory that is the main checkout or a worktree with a valid git back link, and is not excluded, gets a tab; exclusion is the file
  `.skillsync/no-dashboard` or the list `dashboard.worktrees.exclude` in the manifest. Adding pins the start directory (always visible), and its tab counts toward the limit of 16.
  The address of the own tab is also given by `skillsync dashboard url` run in its directory; a directory without a tab gets `BLOCKED`.
- **Tab visibility.** The other worktrees have a tab when they have a trace of skillsync work (an entry in `.skillsync/runs/`, or a plan or spec that differs from the main checkout) and were active
  in the last 7 days (`dashboard.worktrees.inactive_days`); the rest sit under "show all" together with the excess over 16 visible tabs.
  Tabs are sorted by last activity, the main checkout first; a tab with a pending consent is marked.
- **Consents.** A consent is always for the worktree and sha of its own tab; other tabs do not see it. Only its address knows the token of a tab: a tab opened without it is read only.
- **Standalone server.** When a project server is running and any server-settings flag is given (ports, bind, hosts, `--session-url`, `--poll`, `--verbose`,
  `--prototype-port`, `--allow-proxy-headers`), a separate standalone server with one tab starts, which other directories do not join and which `dashboard url` without `--port` picks only when no project server runs
  (address: `dashboard url --port N`). When no project server is running, these flags start the project server. A standalone server is always started by `--standalone`, `--no-approvals`,
  `--layer` and `--read-port` without `--approve-port`. The kind of a server is set at start and does not change.
  A preview with another layer (`--port <port> --no-approvals --layer core=<worktree>/library`) is always a standalone server on its own port.
- **Cards (agents)** in a worktree of a project with a running project server do not assign ports by hand: `skillsync dashboard serve` in their worktree adds a tab and prints
  the address. `--all-tabs` (the addresses with tokens of all tabs, the own one last) is for a human; cards do not use it.
- **Reading.** The read socket exposes the status, documents and tests of all tabs of the project (paths, branches, HEAD, plan contents); the boundary is the exclusion of a worktree.
- **Server terminal.** The project server runs as long as its process does: closing the terminal it was started in closes the page for all tabs (the page and `url` warn about it).
  After the connection is lost it is enough to run `skillsync dashboard serve` again in any directory of the project.
- **History** is a tab with finished plans (`done` and `archived`) of all tabs, grouped by tab (address `/?tab=history`); it also shows plans `approved` whose file has not changed
  for 7 days as "abandoned" (no button). The preview of a plan is read-only, but with the actions "Archive" and "Restore". The same file visible in several
  worktrees is shown once with the number of copies. Historical plans are only in History, not in the plan menu of a tab, but only on a server with more than one tab (a server with one tab shows the menu as before).
  A `done` plan has an "Archive" button (with confirmation), an `archived` plan "Restore"; other statuses have no button. The counter of the tab is the `count` of `GET /api/history`, read in the background only while the browser tab is visible
  and at most every 60 s (at once after History is opened and after a successful status change); the field `tabs[].history` of `/api/tabs` is only a hint (until the first read the bar shows its largest value). The write changes only the `Status` line of the plan (route `POST /api/plan-status`, token of the tab that owns the file, sha check, an audit row in
  `.skillsync/audit/plan-status.jsonl` of the tab's directory) and dirties the `git status` of that worktree; it does not touch `approvals.jsonl`. Excluded worktrees have no plans in History.

## What it reads (formats of the `feature` skill)

| File | What | On the page |
|---|---|---|
| `docs/plans/<plan>.md` | `# title`, `Status:`, `Spec:`, `Spec-sha:`, `Prototype:`, `Run:`; the `## Progress` section with `### Phase …` and steps `- [ ] S1 Title` / `- [x] S1 Title — sha` (ids `S2b`, `A0`, `A-1`) | header, Act II, Act III, phases |
| spec from `Spec:` | `Status:` (draft → in progress; approved/implemented → done; none → planned) | Act I |
| `.skillsync/runs/<run-id>/checkpoints.md` | `RUN=<iso>` (time since start), lines `- HH:MM text` | feed (newest on top), the `in progress:` segment |
| `.skillsync/runs/<run-id>/*.md` | names `spec-review*`, `plan-review*`, `pre-implement*`, `*prototyp*` | stages of acts I and II, document list |
| `.git/HEAD`, `.git/config` (`[remote "origin"] url`), `refs/remotes/origin/<branch>` | branch, HEAD, repository address (Azure DevOps, GitHub), whether the branch is on the server | header: the branch as a link to the repository (only when it is on `origin`; another host = plain text) |
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
- The link: the spec or plan in the **Documents** tab, the prototype on the port of the prototype server. The banner itself approves nothing:
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

**Rule:** the agent does not trigger consents. A consent is recorded only by the user (a button on the page or
`skillsync approvals record` run by them); the agent does not trigger it, simulate it or slip in entries.
The command accepts `--source cli` or `--source ide`; `source: "dashboard"` is recorded only by the page server in its own
process. The rule is not enforced technically (a process of the same user could write an entry); its trace are the
`source` and `by` fields of the entry.

Every consent button (Act I "GO plan", Act II "GO implement", Act III "CP" and — in `pr` mode — "READY") has one of
four states (`approvals.actions[].state`):

| State | When | Appearance |
|---|---|---|
| before | the act has not come yet (e.g. "GO implement" in act I, "CP" before execution starts) | no button |
| `running` (in progress) | the act is in progress, but the state is not yet waiting for consent | visible, inactive, hint "waiting for…" |
| `waiting` | GO plan: spec `draft` with the signal `waiting for GO plan` (spec/draft status or `waiting:`), prototype accepted; GO implement: plan `draft` with `waiting for GO implement`, spec `approved`; CP/READY: plan `done`, `waiting: CP|READY` or unpushed commits of the working branch | active (two clicks) |
| `after` | consent already given (spec/plan `approved`+, a CP/READY entry for the current HEAD) | inactive, "consent given HH:MM"; a second write = refusal |

The "waiting" signal is written by the session (a file status or `waiting:` in a checkpoint): the run files say only that a review round
took place, not that the loop gave PASS.

- **"GO plan"** (spec `draft`, no plan or a draft plan) and **"GO implement"** (plan `draft` without the draft suffix;
  the spec, if there is one, already `approved`): a button in the act tile and in the banner. Two clicks (the second within 5 s), then the server
  in its own process changes the status with the same core function as `skillsync spec approve` / `plan approve` with `--expect-sha`
  (`status.set_doc_status`, sha = the version shown on the page; a changed file = refusal "read again"); without
  a `skillsync` subprocess, so the CLI version in PATH does not matter. It is the consent of the session user, equivalent to typing the word; the
  `feature` procedure recognizes this status and the session acts according to the resume table in the next turn (the page hints: write
  "continue"). The button is disabled with a reason when the prototype is not accepted or "GO implement" precedes "GO plan".
- The trace of GO consents: an entry `status.record_approval` (`source: "dashboard"`) in the run's `approvals.jsonl` (`sha` = the shown
  version of the file, `shaAfter`, `file`, `run`). Without a run directory there is no trace (response `trail: null`): the consent stays
  in the `Status:` line of the file, which the session and `approvals wait --file` read anyway. The server has no log of its own.
- **"CP" and "READY" (`pr` mode) in the Act III tile** — the button records the consent, it does not open a session.
  Two clicks; the server records through `status.record_approval` (the same function as `skillsync approvals record`: file
  lock, refusal of a second entry for the same HEAD, refusal when the plan status changed = 409 with `code: plan_state_changed`) to `.skillsync/runs/<run-id>/approvals.jsonl` an entry
  `{"schema": 1, "at", "word": "CP"|"READY", "sha": <full HEAD shown on the page>, "run", "plan", "target", "branch", "ahead", "mode", "by", "source": "dashboard"}`.
  The consent applies to this HEAD: a new commit = a new consent; a repeated write for the same HEAD = refusal. The button is active when
  the plan is `done`, the newest checkpoint has `waiting: CP|READY` or the working branch has commits that are not on origin
  (`git rev-list --count origin/<working>..HEAD`, and without a branch on origin `HEAD --not --remotes=origin`; read-only).
  "CP" hands over to `origin/<working>` (core AGENTS: never to the base); in `direct` mode the plan line `Handoff: <branch>`
  changes the target to the given branch (the value must be a valid branch name alone; other text is ignored and the hint says so). The button and the entry show the target (`target`, "CP → main"); the hint
  gives the number of commits and the base. The "N unpushed" badge in the header is a button: the panel shows these commits (`delivery.commits`:
  the short hash and the first line of the description, newest first, up to 20, the rest as "and N older" from `commitsMore`); the descriptions are masked before cutting
  and inserted as text. It requires a run directory. "READY" only in `pr` mode (`delivery.mode` from
  `.github/.skillsync.yaml`; no key = `pr`, a bad value = no buttons). The page does not change the `checkpoints.md` log
  (the session writes it); after the write the tile shows "CP recorded HH:MM", and the banner "the consent is already in the file".
- A session (`feature`) waiting for consent listens for changes (`skillsync status`, field `approvals`) with a wake-up every 30 min and after
  waking checks the consent in the file: GO = the status of the spec/plan and the sha, CP/READY = an entry with `sha` equal to its HEAD. CPD, YES/NO
  and `Q` answers stay in the session (the page does not copy consent words to the clipboard).
- **The trust assumption (explicit):** the boundary is the machine. The page has no login; writing only from localhost or
  `--approve-host`. A local process that forged an entry could just as well push itself, so the entry is not
  weaker than "CP" typed in a terminal. Do not expose the page with `--approve-host` in an untrusted network.
- `--session-url` (optional): the session address given at start (e.g. Remote Control), only `https` without credentials;
  the banner then adds "Open session". It is not read from project files, so the agent cannot replace the link.
- `--no-approvals` = a read-only page (no buttons, `POST /api/approve` = 403).

### Phase substeps, tooltips with descriptions and act I signals

- The `Progress` steps may be substeps of phases (`- [ ] F1.2 …`): they are counted separately (bar, "now", the "in progress" list),
  `in progress: F1` points to the first unchecked substep of the phase, the phase shows its own k/n and collapses when closed
  (a click or Enter expands it). Old ids without a dot (`S1`, `S2a`, `A-1`) work as before.
- Every act, stage, gate (GO/CP/READY buttons), phase, substep, banner and checkpoint header has a tooltip with a static
  description: what is happening, who performs it, the result and how the page knows it is done. The descriptions are in the skill's `descriptions.json`
  (`{key: {title, text}}`, consistent with the `feature` procedure), served under `/api/descriptions`; the stage key is given by the server
  (`stages[].key`). The tooltip: hover, keyboard focus (`aria-describedby`, `role="tooltip"`), touch = a tap
  opens and closes, Esc closes. A test makes sure every used key has a description.
- Spec review: the `spec-review*` files in the run directory.
- A warning in the Act I tile "no pre-implement before GO plan" when the spec has non-empty sections `## Contracts`, `## Migration`
  or `## Data` (not "not applicable"), and there is no `pre-implement*` in the run.
- The Act I tile has no list of questions: the blocking questions and assumptions of the spec are in the "Questions" tab (group "Questions for implementation"), and the questions to the expert are shown by a toast in the banner "Questions to the expert: N open (M blocking)"
  from the `expertQuestions` field (the `docs/prototypes/<slug>/questions.json` files of all prototypes of the project, not only of the current run), with a link to
  `/_experts` or `/_experts/<slug>`; the toast has no timer and disappears when there are no open ones. Everyone sees it, also the `--read-host` viewer.
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

The "Tests" tab (hidden until the first run) shows the test results of plan steps run by `skillsync test-run`: a section per step (header = the id and title of the step, newest first), only the running one expanded, finished ones collapsed (a red chip in the header and a counter of red ones on the tab); on the "Progress" tab next to a step there is a bar "how many passed out of how many" and a button to the section (`#tests:<step>`). The data is the `tests` field of the JSON and the route `GET /api/tests` (read-only, both sockets). An attempt shows at most 200 groups (failed first, then in progress, the rest by name); when `groupsOmitted` > 0, next to the groups of the attempt a note `role="note"` is shown "Showing N of M groups (failed and in progress first); the counters above cover all tests.", and the full tests of the cut groups are in `GET /api/tests`. When `tests.skipped` is not empty, the tab shows a note (`role="note"`) "Skipped N test result files or directories (could not be taken into account): path (reason), …" (up to 5 paths, "and N more", "at least 20" at 20 entries; with empty sections also "No results read."). **Test results are not masked** (also on the read port visible in the network; `tests.mask: on` masks them), the rest of the page is; see `docs/security.md`.

## Interface and API

- `/` the page (tabs **Progress** and **Documents**, keys `P`/`D`; a light/dark theme switch in the header, key `T`, the choice in `localStorage`, the system theme by default), `/assets/*` CSS/JS.
- `/api/status[?plan=docs/plans/x.md]` the JSON of the whole view; `/api/doc?path=…` a Markdown preview (own renderer, raw HTML escaped).
- `/events` SSE: `hello`, `change` (an mtime watcher every 1 s: plans, specs, design, domain, run directories, `.git/HEAD` and the branch ref),
  `ping` every 15 s. After `change` the client fetches `/api/status` without a reload; when the SSE fails or is silent for > 40 s, it polls every 5 s
  and returns to live mode by itself. Headers `Cache-Control: no-cache`, `X-Accel-Buffering: no` (tunnel/proxy without buffering).
- Prototype: the link `http://<page host>:<port>/<slug>/` only when the prototype server on that port answers; the port is `--prototype-port`, the manifest key `prototype.port`, and without them 5130.
- `POST /api/approve` `{kind: spec|plan, path, sha}`: only an action that the page is offering right now; `/api/status` has
  `approvals {enabled, here, viewer, actions[]}`, `session {url}`, `spec.fileSha`, `plan.fileSha`, and the banner items `word`/`approve`.

### Page assets and the renderer shared with Skippster

| File | Role |
| --- | --- |
| `index.html` | the roots `<div id="wtabs">` (the tab bar, `createTabBar`; hidden with one tab), `<div id="whistory">` (History, `createArchiveView`; hidden until selected) and `<div id="app">` (the page of a tab); in `<head>` `app.css` and `theme.js`; scripts: `dashboard-render.js`, then `app.js` |
| `dashboard-render.js` | the **renderer**: a pure view JSON (`schema: 1`) → DOM, the whole page skeleton, the icon sprite, tooltips, consents; the same file is loaded by the Skippster webview |
| `app.js` | the **page adapter**: `fetch` of `/api/status`, `/api/doc`, `/api/approve` (header `X-Status-Board: 1`), `/api/descriptions`; on a server with tabs also `/api/tabs`, `/api/history`, `/api/plan-status`, `/api/feedback-approve` and the parameter `?tab=<id>` (status, documents, tests, `/events`); SSE `/events` with polling every 5 s and a 40 s watchdog (no stream and no polling for a removed tab); `localStorage` (`dashboard:plan`, `dashboard:theme`, and with tabs `dashboard:plan:<tab>`, `dashboard:view:<tab>`, `dashboard:approveToken:<id>` and `dashboard:terminalNote`); the theme; the address `#hash`; a clock every 1 s; keys at the document level |
| `theme.js` | the theme before the first paint (remembered or system) |
| `app.css` | the appearance; the renderer root has the class `dashboard-root` (`display: contents`) |
| `descriptions.json`, `descriptions.en.json` | the tooltip descriptions: Polish and English; the server picks the file by the `lang` of the status (no file = Polish). A host that copies the assets copies both |

The layout depends on the width of the host column, not of the window: the skeleton (header, tabs, `main`, footer) is in the `.dash-wrap` wrapper
(`container: dash / inline-size`), and the thresholds are `@container dash` 1100, 640, 500 and 420 px (compact mode: a panel next to the session).
The `#descTip` tooltip is outside the wrapper (containment breaks `position: fixed`). A host with its own CSS takes over these rules from `app.css` and gives `.dash-wrap` a defined width (an inline-size container has no width of its own content).

The renderer is a UMD: `window.DashboardRenderer` in the browser, `module.exports` in Node. Exports:
`create(root, deps)` (alias `createDashboard`), `createTabBar(root, deps)` (the bar of worktree tabs with alerts), `createArchiveView(root, deps)` (the History view: list, preview, confirmation), `buildSprite(doc)`, `SPRITE` (the data of the `i-…` symbols), `version`, `defaultTexts` (the built-in Polish `ui.*` catalog, `DEFAULT_TEXTS`; generated from `pl.json` by `scripts/sync_renderer_defaults.py`, do not edit by hand).
The tab bar and History use about 90 keys `ui.wt.*` and `ui.history.*` (they are in `defaultTexts`, so a host with its own catalog, like Skippster, gets them at once); a host without tabs (one tab, an editor webview) does not call these functions and supplies nothing, and a host that calls them passes `deps.t` and its own roots (`createTabBar(root, {t, panel, panelId, archivePanel, onSelect, onToggleAll, onDismiss})`, `createArchiveView(root, {t, mountHtml, onSelect, onAction, onConfirm, onCancel, onMore, onReload, onDismiss, panelId})`).
`create` changes only its own root: it adds to `root` the class `dashboard-root`, the sprite (skipped with `deps.sprite === false`,
when the host has its own) and a skeleton with fixed identifiers (`title`, `meta`, `live`, `planBtn`, `planMenu`, `waitBanner`,
`tab-progress`, `tab-docs`, `protoLink`, `acts`, `phases`, `feed`, `feedTitle`, `docList`, `docView`, `foot`, `descTip`,
`elapsed`, `ago`, `retryDoc`, `themeBtn` only with `deps.theme`; the `i-…` symbols). The identifiers are global in the document:
two instances of the renderer in one document will collide (one root per document/webview).

The working-directory chip (`project.worktree`; a host with its own sprite, `deps.sprite === false`, must have the symbol `i-folder`, without it the icon is empty, but the chip works): in the header, after the branch chip (alone without a branch) and before the "N unpushed" marker, a `span` with the icon `i-folder` (`worktree: <name>` or `main checkout`; `null`, a missing field and an unknown `kind` = no chip). The description from `descriptions.json` (`workdir_chip`), the full path (for a worktree also `Main checkout: <mainPath>`) in `data-desc-extra`; the name truncated with an ellipsis on an inner `span`. The chip has no action, so `deps` unchanged and no copying: a host without a clipboard loses nothing.
The delivery-mode chip (`delivery.mode`, `base`, `source`): in the header, after the branch block and before the PR chip; "mode: pr", "mode: direct, base: develop", a tooltip with the mode, the base and the source; an invalid mode (`invalid`) is a warning chip "delivery mode: error", not a silent `pr`; no `delivery` field = no chip.

The content from the JSON (additively, a missing key = behavior as before): in the act I and II tiles a "Measurement:" row from `timing.acts` (working and waiting time; no consent in the file = time unknown), in act III a "Forecast:" row from `forecast` (by default the range of work until the end of the steps and an indicative end time with the note "indicative"; the host option `tempo` shows only the pace per step; without data the reason; the forecast has a dashed border, the measurement a solid one); step titles (plan, "now", substeps), the "Latest checkpoints" rows, the "waiting for" items and the content of the questions to the expert with in-line formatting (`titleHtml`, `whatHtml`, `actionHtml`, `textHtml` also in `run.checkpoints[]`, inserted by `deps.mountHtml`; without the field plain text; tooltips and attributes always plain text); under the acts a "Result" block (`result`: formatted Markdown from `result.html` and `result.moreHtml`, inserted by `deps.mountHtml`, the rest in `<details>` "Show the rest (N lines)"; without `html` the plan text in `<pre>`, above 12 lines the rest in `<details>`) and a collapsible "Concepts" legend (always; texts from `legend_*` in descriptions.json, as a fallback from the renderer); in the questions toast the addressees (`expertQuestions.runs[].addressees`: "to all experts" or "to: login, …", the viewer nothing); the toast "N new comments after acceptance" from `prototype.commentsAfterAcceptance` (no timer and no "×", a link only with `deps.prototypeUrl`); the "Questions" tab (`tab-questions`, `view-questions`, `qCount`) with two groups: "Questions for implementation" (the blocking questions and assumptions of the spec of the visible run, a "session is waiting" mark by `waiting[]` with `source: "checkpoint"`, "with the expert" at a question handed to the expert) and "Domain expert questions" (the current run: `expertQuestions.current`: the prototypes and questions tied to the spec path; with no `current` key, i.e. an older server, a local filter by `prototype.slug`); the counter `qCount` = blocking questions for implementation + `current.open`, with the split in the tooltip; the tab is hidden when neither group has content; lists of up to 50 rows, arrow/Home/End navigation in the tab list and the hash `questions`; the document list is `<details class="doc-group">` groups with a `<summary>` header (label and counter): `task` and `run` open by default (can be collapsed), `specs`, `plans`, `design`, `domain` collapsed by default (the state in the renderer state, without `localStorage` and without new callbacks; choosing a document expands its group).

**Theme tokens (part of the renderer contract).** The appearance of the page depends on CSS variables from the blocks `:root` (light) and `:root[data-theme="dark"]` and `@media (prefers-color-scheme: dark)` (dark; both dark blocks have the same values) in `app.css`. A host with its own theme must define each of them, otherwise the element stays without color:
`--bg --panel --panel-2 --line --text --muted --faint --accent --accent-2 --on-accent --accent-soft --glow --done --on-done --done-soft --pending-a --pending-b --warn --warn-soft --planned --planned-soft --bad --bad-soft --radius --shadow --mono --sans`.
A new token (like `--bad-soft`, which came in 1.8.0: the background of red test entries, counters and chips) is a change visible without a warning for such a host, so **every new token goes into the CHANGELOG** and into this list; it is guarded by a test (`test_skillsync_dashboard_theme_tokens`). Release 1.9.0 adds no tokens.

The `deps` contract (only `now` is required):

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `now` | `() => number` | yes | time in ms; the renderer does not read the clock itself (missing = `TypeError`) |
| `actions.approve` | `({kind, sha, path, word}) => Promise<{ok, word, text} \| void>` | no | consent after two clicks within 5 s; the result = a message in the banner (no result: the host may give `setNotice`); an exception = the message `texts.noConnection`; then `actions.refresh()` |
| `actions.refresh` | `() => void` | no | a request for a fresh state (after a consent) |
| `actions.selectPlan` | `(path \| null) => void` | no | choosing a plan from the menu (`null` = automatic); the renderer also saves it in `storage` (`plan`) |
| `actions.loadDoc` | `(path) => Promise<{path, status, mtime, truncated, html}>` | no | like `/api/doc`; its presence enables the preview in the Documents tab |
| `actions.openDoc` | `(path) => void` | no | opening a document by the host (e.g. an editor), used when there is no `loadDoc` |
| `actions.openLink` | `(kind, id, url) => boolean` | no | `kind`: `branch`, `pr`, `session`, `prototype`, `experts` (the questions-to-the-expert toast); `true` = the host opened it itself (the renderer does `preventDefault`), otherwise the default `<a target=_blank>` |
| `canApprove` | `(json) => boolean` | no | whether this host may give consents; by default `approvals.here` from the JSON |
| `storage` | `{get(k) => string \| null, set(k, v)}` | no | preferences (`v == null` = delete); without it the plan choice is not remembered |
| `theme` | `{isDark() => boolean, toggle()}` | no | the theme switch and the `T` key; without it there is no button |
| `texts` | `{live?, liveTip?, noConnection?}` or `(lang) => {live?, liveTip?, noConnection?}` | no | the function is called at every read with the current language of the status (an exception or a result that is not an object = no override); overriding the texts of the indicator (`live`, `polling`, `down`, `connecting`), their tooltips and the no-connection message; only these keys, the other `ui.*` come from the `texts` field of the status or from the Polish catalog |
| `prototypeUrl` | `(port, slug) => string` | no | the prototype address (page: page host + port); without it the link has no `href` |
| `prototypeOrigin` | `(port) => string` | no | the address of the prototype server without a path (page: `protocol//host:port`); with it the questions-to-the-expert toast has a link `/_experts[/<slug>]`, without it it is plain text |
| `mountHtml` | `(el, html) => void` | no | inserting the HTML of the Markdown preview and the `…Html` fields from `GET /api/status` (escaped by the server); the renderer itself does not parse HTML. Without `mountHtml` (e.g. an editor plugin that reads `dashboard show` without these fields) the page shows plain text |
| `forecastView` | `"tempo"` or `"range"` | no | the end-forecast view: `range` (default, constant `FORECAST_VIEW`: a range of work and an indicative end time; replay on real runs: an order of magnitude, not a promise, see the contract) or `tempo` (just the pace per step and the number of remaining steps) |
| `onTitle` | `(text) => void` | no | the window title (`⏳ Waiting · 40% · … — status`) |
| `onNavigate` | `(hash) => void` | no | the view address: `progress`, `docs`, `docs:<path>` |
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
| `setView(view, push = true)`, `setDoc(path)` | the tab `progress`/`docs`; the chosen document (without loading) |
| `tick()` | the host calls it every 1 s: the time of act III, "x min ago", the silence threshold |
| `handleKey(event)` | the host attaches the document `keydown`: `P`/`D`/`T`/`Esc` |
| `closeMenu()`, `hideTip()` | host: a click outside the root, scrolling |
| `syncTheme()` | after a change of the system theme |
| `state()` | `{view, doc, armed, approving, notice}` (tests, diagnostics) |
| `destroy()` | cancels the timers and frames, removes the listeners, clears the root |

Requirements for the webview host: `approvals.here` is only in `/api/status` (`dashboard show` does not return it), so a host that
allows consents provides `canApprove` (otherwise the buttons are inactive with the reason "Consent only from the browser…"). The host always
takes over `actions.openLink` (returns `true`) and does not leave the default navigation in the webview. The renderer puts into `href` only `http:`/`https:` addresses
(another scheme, e.g. `javascript:`, `data:` = a link without `href`, the text unchanged).

The remote viewer: the field `approvals.viewer` (`dashboard serve --read-host`; an older server does not return the field). With
`approvals.viewer === true` (no field = false; the server then gives `approvals.here` = false) every consent button except
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

- Two-socket mode: consents only on the consent socket; `Origin` must be exactly `http://<Host>` with the port (a page from the read
  port will not write); proxy headers (`Forwarded`, `X-Forwarded-*`, `Via`) reject the write, unless
  `--allow-proxy-headers`; every consent in `approvals.jsonl` has `via` (port, `Host`, the client address from the connection).
  The residual risk: the token is in plain HTTP (when the consent socket listens outside this machine); in an untrusted network
  `dashboard.local_only: true` or an SSH tunnel.
- The page has no login: in two-socket mode everyone on a network that sees the read port sees it (plans, specs,
  checkpoints, results, run reports); they do not see or give consents.
- The `Host` check on all endpoints (page, assets, `/api/*`, `/events`, `/healthz`, `POST`): the `Host` header must
  be `localhost`/`127.0.0.1`/`[::1]` (any port) or a name given explicitly in `--approve-host` or `--read-host` (e.g. the name of the
  machine at the tunnel, an IP in the LAN); another = 403 without content (protection against DNS rebinding), the refusal in the log with `--verbose`.
  In two-socket mode both lists are supplemented with the literal IPs of this machine; the read socket additionally accepts the machine's own name
  (`socket.gethostname()`, the short form and `<short>.local`), read-only.
  A consent write only from loopback or `--approve-host`; a host from `--read-host` reads but does not write from the browser (it will not stop a client forging `Host`; untrusted viewers = `--no-approvals`).
- A consent write also requires the server token (`X-Approve-Token`): the address with the fragment `#zgoda=<token>` is printed by `dashboard serve` in the console, and a running server gives it with `skillsync dashboard url <project>` (the stderr of a running server goes to the service log and the CI logs together with the token: there give the address through `dashboard url`, not from the start line; through `--approve-host` the token goes over plain HTTP, only in a trusted network).
  The agent gives the user this address (does not paste it outside the conversation); without the token the page is read-only, the consent buttons are inactive.
- A consent write additionally: the required header `X-Status-Board: 1` (a CORS preflight, which the
  server does not handle, blocks foreign pages), `Origin` — if present — from the same host list, `Content-Type: application/json`, a 4 KB limit.
  Without authentication by the owner's decision: a developer tool on their machine; the agent can call the CLI anyway.
- The preview whitelist: `*.md` in `docs/specs`, `docs/plans`, `docs/design`, `docs/domain` and `.skillsync/runs/<run>/*.md`;
  rejected are `..`, absolute paths, hidden directories, symlinks leading outside the whitelist, files other than `.md` (e.g. `.env`).
- CSP `script-src 'self'`, apart from consents (the `Status:` line at GO, an `approvals.jsonl` entry) no write to disk (logs only on stderr), a 2 MB limit per document and 64 SSE connections.

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
