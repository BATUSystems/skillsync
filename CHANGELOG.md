# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

The generator version (`skillsync --version`) and the version of the core layer (`layer.yaml` in `library/`)
are separate numbers. When they do not match, `skillsync doctor` reports a warning.
What counts as MAJOR, MINOR and PATCH: [docs/upgrading.md](docs/upgrading.md).

Every release has a **Breaking** section that lists incompatible changes. When a release has none, the section says "None."

## [Unreleased]

### Added

- Status page and the writing marker: `/api/status` and `dashboard show` have `prototype.writing` (`null` or `{since, expiresAt, renewedAt}`, never `by`). While the
  marker is valid, `waiting` has the item `source: "writing"` ("agent pisze ekrany (do HH:MM)") instead of the call to accept the prototype (also
  when the call came from a checkpoint `czeka:`); the page shows "Sesja pracuje od HH:MM". The page server watches `.writing.json` and its validity, so the page
  also refreshes when the marker expires.
- `waiting` item `source: "expert"` ("odpowiedź eksperta (N blokujących)", link `{kind: "expert", slug, url}` like the questions toast) when `expertQuestions.blocking > 0`
  and the newest checkpoint has no `czeka:` marker.
- `POST /_api/v1/prototypes/<slug>/writing/release`: removes the writing marker (idempotent, `{writing: null, released}`); only `developer` and `acceptor`
  (403 `forbidden` for `commenter` and `expert`, 401 without a session, same checks as `restore`). The engine banner has a "Zwolnij" button for those roles.

### Changed

- `prototype writing start` on a valid marker keeps `since` (the start of the writing) and `by` (unless `--by` is given) and sets the new field `renewedAt`; the window
  of at most one hour counts from `renewedAt`, so a long writing renewed again and again stays one marker. `writing` in `list`, the API and `status` has `renewedAt`.
- `silence` (no report) is also off while a blocking question to an expert is open (the session waits explicitly); the card title gets "⏳ Czeka" only for items that wait for a person, not for the writing item.
- The engine banner says "Agent jeszcze pisze ekrany (do HH:MM)" (the hour from `expiresAt`, browser time). The 409 `writing_in_progress` has a Polish message for people
  ("Agent jeszcze pisze ekrany tego prototypu (do HH:MM)…", host time of the server), so a copy of the engine without the banner shows readable text; the code and fields are unchanged.

### Compatibility

- `.writing.json` stays `version: 1`; `renewedAt` is optional. A core 1.3.0 reads a file renewed for more than an hour after `since` as no marker (acceptance unblocks); do not mix
  versions while the agent writes for longer than an hour. Times outside 0…2^53 (ms) make the file invalid.

### Breaking

None.

## [1.3.0] - 2026-11-13

### Added

- `skillsync prototype writing <projekt> <slug> start|done|status [--ttl <s>] [--by <login>]`: a marker "the agent is still writing screens"
  (`<root>/<slug>/.writing.json`, TTL 60-3600 s, default 900; `start` again renews it). The marker is a dotfile outside the screen hashes,
  `prototype_version` and `assets_hash`, is not served and is not copied to `iterations/`; `.writing.json` is added to the prototype
  `.gitignore` (by `init`, `migrate` and the first `start`). Exit codes: 0, and 2 (BLOCKED `needs_migration`, `lock_timeout`, `invalid_args`). `--ttl` and `--by` are validated for every action (`status --ttl`, `done --by`); `--by` must start with a letter or a digit.
- Acceptance and restore are blocked while a valid marker exists: 409 `writing_in_progress` on browser `accept` operations,
  on `POST /_api/v1/project/accept` (with the list `writing`) and on `restore`; 409 `partial` carries `writing` when the marker appears
  during a project acceptance. A marker that is corrupt, expired, from the future or longer than an hour counts as absent.
- The field `writing` (`null` or `{since, expiresAt}`) in `GET …/operations`, `GET /_api/v1/project` (per prototype) and `prototype list`;
  the live gallery marks a prototype whose agent is still writing (the static `index.html` does not).
- Prototype engine: the banner "the agent is still writing screens" and disabled accept and restore buttons that return without reloading
  once the marker is gone (an older copy of the engine shows the generic error until `skillsync prototype migrate`).
- `skillsync leakcheck --text FILE` and `--stdin`: the leak check for text outside git (a PR description, a comment, release notes), with the same target, identities and patterns as `--range`, scanned in the strictest path class. The report names the label (file name or `<stdin>`), the category and the position, never the text. No git repository needed; the text is never a command argument. The skills `pr-run`, `feature` and `dispatch` now require this check for a description written to a file before `gh pr create`, `gh pr edit --body` and `gh release create`.
- Review profile `docs` of the role `reviewer` (`library/skills/feature/references/review-loop.md`, `library/agents/reviewer.md`): every promise in docs, CHANGELOG and notes has evidence or is marked unconfirmed, instructions match the tools that check them, skills do not contradict each other, current state only, no names of people, clients or events in the public core. `skillsync review` returns `profile` (`docs` when the change touches only `docs/**`, skill `.md` files, `CHANGELOG*` or release notes, otherwise `kod`) and `docsFiles`, adds the docs checklist items for docs files, and does not change the risk or `readiness`. Paths and items can be overridden with `workflow.review.docs_paths` and `workflow.review.docs_checklist`.
- Dashboard: `timing`, the work time and the waiting time for a human per step and per plan phase, counted by the status page itself (`show` and `dashboard metrics`, additive in `schema: 1`). A waiting window opens at a checkpoint with `czeka:` and closes at the consent named in it (`approvals.jsonl`) or at the next checkpoint; a window that nothing closes and a step without its marks are `unknown`, never estimated. The status page shows a collapsible "Czas pracy i czekania" section with sums per phase and a tooltip with the windows. Contract: `docs/dashboard-contract.md` (section `timing`).

### Changed

- `prototype accept` refuses with BLOCKED `writing_in_progress` before asking for the password and again under the lock; `prototype gate`
  reports FAIL with the reason `writing` (`screen: null`) per prototype with a valid marker; `prototype wait` does not return `accepted`
  while the marker is valid; `prototype iterate` warns. With `--project`, `accepted` may be empty when the marker is found in the first prototype.
- `classifyAcceptResult` in the engine knows the result `writing`; the copy of the engine in the library is refreshed (no new exports).
- Skills `prototype`, `skillsync` and `feature` (`references/plan-details.md`) describe the marker, see the library changelog.
- `comments.json` is unchanged; `SERVER_VERSION` is unchanged.
- The core repository no longer tracks task specifications or plans: `docs/specs/**` and `docs/plans/**` are removed (they live in the operations repository; history and tags are untouched), references to specific (dated) specification and plan files that the core does not keep are replaced by a neutral description (domain records, lessons, the prototype contract, library instructions, comments), and the new test `tests/test_skillsync_repo_hygiene.py` fails in CI when any file is tracked under those directories or a tracked file outside `tests/` points at such a dated file. The project directory convention (`docs/specs/*.md`, `docs/plans/*.md`) that the generator reads is unchanged.

### Breaking

## [1.2.3] - 2026-11-13

### Added

- The page `/_experts` (and `/_experts/<slug>`) refreshes the list by itself every 15 s (paused while the tab is hidden, at once when it comes back),
  so a remote expert notices a new question, also a blocking one, without reloading. The tab title shows the number of open questions across
  all runs, `(2) Pytania do eksperta`, and `(2!) …` when one of them is blocking. A started answer or comment (text in a field, a ticked option, the focus
  in a field) is never redrawn: the list updates, and when that question changed on the server (someone answered or commented) the page says so once
  instead of overwriting the form.
- `GET /_api/v1/questions` and `GET /_api/v1/prototypes/<slug>/questions`: `runs[]` has `blocking` (open blocking questions visible to the viewer).

### Breaking

None.

## [1.2.2] - 2026-11-12

### Added

- Dashboard contract: the field `expertQuestions` (`{open, blocking, runs[{slug, open, blocking, url}]}`) counts the open questions to a domain
  expert across ALL runs of the project (`docs/prototypes/<slug>/questions.json`, read-only, no import of the prototype server), not only
  the run of the visible spec. The page shows them as a toast "Pytania do eksperta: N otwartych (M blokujących)" with a link to `/_experts/<slug>`
  (one run) or `/_experts` once the host knows the prototype server address; the toast has no timer and disappears when nothing is open.
  The dashboard watcher follows `questions.json`.

### Changed

- `skillsync prototype question purge --slug <slug>` removes only the withdrawn questions by default; `--include-open` also removes the open ones that have
  neither an answer nor a comment (a fresh question waiting for the expert is no longer removed by a plain `purge --yes`). A withdrawn question that has a comment or an answer stays. The preview lists
  what would be removed and why, and the questions it leaves alone (`skipped`).
- `skillsync prototype question record` checks the spec line it is given: the line must hold `[EKSPERT]` and the question id (the row from `answerLine`);
  otherwise FAIL `record_line_mismatch`, `record_line_out_of_range` or `record_spec_unreadable` with a hint, and nothing is written.
- The dashboard no longer draws the "Pytania" section of the spec act (the lists of blocking questions and assumptions, also with zero). The
  fields `blockingQuestions` and `assumptions` stay in the JSON contract; the description keys `questions_blocking` and `questions_assumptions`
  are gone from `descriptions.json`.

### Breaking

None for existing fields. Behaviour changes: `question purge --yes` now removes fewer questions by default (only withdrawn ones; open ones need `--include-open`), `question record` refuses a spec line that is not the `[EKSPERT]` row of the question, and the dashboard no longer draws the "Pytania" section of the spec act (the JSON fields stay).

### Fixed

- `question record` no longer reports PASS `changed: true` when the line still holds the old waiting row (`- Q1: …`) without `[EKSPERT]`.

## [1.2.1] - 2026-11-12

### Added

- Questions to a domain expert are kept per run: `<root>/<slug>/questions.json` next to the run's `comments.json`. The page `/_experts`
  lists the runs and all questions; `/_experts/<slug>` shows one run. API: `GET /_api/v1/questions[?slug=]`,
  `GET /_api/v1/prototypes/<slug>/questions` and `/_api/v1/prototypes/<slug>/questions/<id>[/answer|comment]`; a question view has a `slug` field.
- `skillsync prototype question --slug <slug>` for `add`, `list`, `export` and `wait --all`; `add` without `--slug` picks the one prototype
  whose `requirements` is the `--origin` spec (BLOCKED `slug_required` or `slug_ambiguous` otherwise). `show`, `record`, `withdraw` and `wait <id>`
  find a question by its id.
- `skillsync prototype question migrate [--dry-run] [--slug-for SPEC=SLUG]` splits the old `<root>/questions.json` into the runs by `origin.spec`.
  It is idempotent and BLOCKED (nothing written) when a question has no single run. The old file goes to the trash only after every operation
  has been read back from the run files.
- `skillsync prototype question purge --slug <slug> [--yes]` removes from one run only the withdrawn questions and the open ones without an answer
  or a comment; a question that another kept question refers to stays. Without `--yes` it only lists what would be removed.
- `question add` returns `created` (files created by the call) and `notes`; `list`, `export` and `show` report `legacy` while the old file exists.
- `question add` removes an orphaned `questions.json.lock` (a free lock without its data file) of the run it writes to.
- Expert questions: a multi-choice question. `skillsync prototype question add … --multi` (at least 2 `--option`, several `:recommended` allowed) lets the expert tick one or more variants; the `/_experts` page shows check boxes for it, and an empty set is refused in the page and on the server (400 `empty_selection`); "none of them" is the free-text answer. A multi answer is stored as `optionIds` and exported with `optionLabels` (lists, in the order of the question's options); `answerLine` and the `[EKSPERT]` row list every chosen variant. A single answer on a multi question (or a set on a single one) is 400 `wrong_answer_kind`.

### Changed

- `skillsync prototype question merge <path> <other>` (project form) requires `--slug`; the git merge driver form `--driver %O %A %B` is unchanged.
- The questions file is `<root>/<slug>/questions.json` instead of `<root>/questions.json`. The old file is read-only until `question migrate`:
  its questions show `slug: null`, answering one gives 409 `migration_required`, and the server warns at startup.
- `questions.json` is excluded from the screen hash (`assets_hash`) and from the iteration freeze, so an expert's answer does not invalidate
  an accepted screen. The server hides `questions.json` only at the project root and at the run level (also behind a symbolic link in a run);
  a deeper file of that name is an ordinary prototype file.

### Breaking

None for existing fields. Two changes need action: the questions file moves from `<root>/questions.json` to `<root>/<slug>/questions.json`
(run `skillsync prototype question migrate` once; until then the old questions are read-only), and `question merge` in project form needs `--slug`.

### Fixed

- `skillsync prototype question withdraw` reported no state; it now returns `state`, and `questions[0].state` is `withdrawn`.

### Compatibility

- Additive in `expert-questions/1`: a new optional `multi` on a question and `optionIds`/`optionLabels` on its answer; single-choice questions, free-text answers, confirmations and existing logs are unchanged. A server older than this version ignores the new fields: it shows a `multi` question as a single-choice one and an `optionIds` answer as empty, and only its merge driver refuses such a file, so update the core before using `--multi` in a shared project.

## [1.2.0] - 2026-11-12

### Added

- Questions to a domain expert. Prototype server accounts may have the new role `expert` and several roles (`roles`, next to the
  single-role field). `skillsync prototype user add|import` accept a repeated `--role`; the new `skillsync prototype user roles <login> --role …`
  replaces the role set of an existing account. `session.user` gains `roles`; `session.features` gains `questions`.
- `<root>/questions.json` (a journal of operations `ask`, `answer`, `comment`, `recorded`, `withdraw`; own file lock, 1 MiB limit,
  tracked in git) and the page `/_experts` ("Questions to the expert": options with the agent's recommendation, an own answer, a comment;
  the first answer closes the question). API: `GET /_api/v1/questions`, `GET /_api/v1/questions/<id>`,
  `POST /_api/v1/questions/<id>/answer|comment`. A session is always required, also without `--login-to-view`; the file is never served
  statically; an expert sees only questions addressed to them or to everyone (anything else is 404).
- `skillsync prototype question add|list|show|export|record|withdraw|url|wait|merge`: the agent side. `list`, `show` and `export` print
  `expert-questions/1` JSON (with `status: PASS`) read from the file, without the server. `wait <id>` returns at once for an already
  answered question and wakes on a comment; `wait --all` skips questions that already have a `recorded` operation. `add` blocks on a
  secret-like value in the text (heuristic; `--force` overrides with a warning), creates `<root>/.gitignore` and `<root>/.gitattributes`
  lines, and prints `specLine`; `show` and `wait` print `answerLine`. `merge --driver %O %A %B` is the git merge driver
  `skillsync-questions` (register it with `git config`, see `docs/prototypes.md`).
- Spec line class `[EKSPERT]` (answer of a domain expert with author and time) and the rules of the cycle in
  `library/skills/feature/references/spec.md`; the option "I do not know, ask the expert" in an ordinary question.
- Write limits on the server for questions: 60 writes per 60 s per login (429 `rate_limited` with `retryAfter`), 50 comments per question
  (409 `comment_limit`), file size thresholds 768 KiB (`ask`), 896 KiB (`comment`), 1 MiB (`answer`) with 507 `file_too_large`.
- Startup warning when expert accounts exist and the server runs without `--login-to-view`.
- New domain record `docs/domain/expert-questions.md`.

### Changed

- The account file keeps `role` (the highest role of the former set: `developer` > `acceptor` > `commenter`; an `expert`-only account gets
  `commenter`) and adds `roles` only for accounts with `expert` or with more than one role. Reading never mutates the file; a password
  change rewrites only the changed account. `session.user.role` is the highest role (`developer` > `acceptor` > `expert` > `commenter`),
  chosen by one function for all gates.
- `skillsync prototype comments --json` and `wait` add `authorRoles` and `whoRoles` next to the string fields `authorRole` and `whoRole`.
  A comment of an `expert`-only account is advisory: the agent changes a mockup only after a `decide` from a `developer` or an `acceptor`.
- Core library layer `1.2.0` (`core_min: 1.2.0`): skills `feature`, `prototype` and `skillsync` describe the new commands and the
  `[EKSPERT]` line.

### Breaking
None. The account file stays readable by the previous core: the `role` field is always present and is never higher than the account's rights,
so an older core ignores `roles` and sees an `expert` as a commenter. `comments.json` is unchanged.

### Fixed
None.

## [1.1.2] - 2026-11-12

### Changed
- Dashboard header: the branch shows "N niewypchniętych" (unpushed commits, `delivery.ahead`) or "gałąź nie wypchnięta" (`delivery.pushed: false`), so a branch on origin with
  16 local commits ahead is no longer read as pushed. No JSON change (the fields already existed); `git.branchPushed` is documented as "origin/<branch> exists".
- Library text (retro fixes): `prototype` and `feature` say what to do when `prototype accept` ends `BLOCKED tty_required` (tell the person to click Accept on the prototype page, keep waiting; the guard stays); under `ceremony: demo` the `kod` review has at most 2 rounds (the second only on a blocker, 3+ majors, or fixes touching permissions or migrations; otherwise the `verifier` closes the majors); `feature` cuts scope only after the plan's threshold is exceeded or with the lead's consent; `agent-runtime-pitfalls` gains four pitfalls (`npm ci` with the wrong npm version, a pipe that loses the test runner's exit code, a session clock that drifted from the machine, zsh `=====` as an argument and backticks inside `echo "…"`). Full review loops outside `demo` are unchanged.

### Fixed
- Prototype engine (`prototype.js`, `prototype.css`): the screen nav always shows the "home" link to the prototype list, also in the simple link strip (`data-proto-nav="strip"`),
  and the strip is used only up to 5 screens; with more screens the attribute is ignored and the dropdown screen switcher with search is built (`screenNavMode`, new engine export, contract 107 names).
  `references/screen-patterns.md`: the switcher is the default, `strip` only on explicit request. Existing prototypes get the new engine with `skillsync prototype migrate` / `skillsync prototype sync`.
- Dashboard page and panel: the "session waits for you" banner item (and the "⏳ Czeka" tab title) disappears once its consent is recorded and the session has moved on
  (it reported after the consent, or the consent is older than 10 minutes), and the consent-result toast fades by itself (10 s, an error 30 s, the countdown stands still
  while the tab is hidden via `board.setVisible`). Before, both stayed until the next checkpoint or a click on "×".

### Breaking
None.

## [1.1.1] - 2026-11-12

### Added
- `docs/lessons/test-z-wersja-rdzenia-na-sztywno-wygasa.md`: lesson on tests that hard-code a core version and expire at a version bump (derive it from `generator_version()`).
- `library/skills/dispatch/references/codex.md`: section on the BRIDGE/1 protocol (Claude Code and Codex sessions talking on one machine).
- `library/skills/agent-runtime-pitfalls`: two new pitfalls (explicit path list for `git add`; browser screenshots in a separate profile without extensions).

### Changed
- Tests (Windows): leakguard term-scope cases skip file names with characters Windows forbids (`:` in `konta: …`) on the `filename` surface only; prototype doctor/init tests run without secret-named environment variables, so a runner variable such as `*_TOKEN=root` no longer turns `prototype.root` into `prototype.[REDACTED]`. No product change; `redact()` is untouched.
- Tests (`tests/test_skillsync_retro.py`): the retro fixture writes files byte for byte (`write_exact`) instead of in text mode, so on Windows evidence files no longer get `\r\n` and their sha256 matches the digest computed from `\n`. A new test forces CRLF on Linux and shows both outcomes. No product change.
- CI: the `test` job on native Windows has a 60-minute limit (was 30, which cancelled both Windows jobs at about 80% of the suite); Ubuntu and macOS keep 30. Job names are unchanged; Windows stays non-blocking.
- Dashboard page and Skippster panel: the layout follows the width of the host column, not the window (CSS container
  queries on a new `.dash-wrap` wrapper; the tooltip stays outside it). New compact tier up to 420 px: smaller type and
  spacing, no big Roman numerals or source notes, plan badge and live label shortened to a tooltip, approval and
  "open document" buttons full width. No horizontal scroll at 360 px; JSON contract (schema 1) unchanged.
- Skill `dashboard`: the status page view is a shared renderer, `assets/dashboard-render.js` (UMD,
  `DashboardRenderer.create(root, deps)`: pure JSON → DOM without transport, browser storage, `innerHTML` or inline styles,
  so the Skippster webview loads the same file under its CSP); `assets/app.js` is the page adapter (HTTP, SSE with polling,
  `localStorage`, theme). The renderer contract is in the skill's `SKILL.md`; a static and a Node test guard it
  (`tests/test_skillsync_dashboard_renderer.py`).
- Dashboard page: the Questions, Readiness and substeps sections are drawn by the shared renderer. A remote viewer
  (`approvals.viewer`) gets only the inactive approval buttons with the reason; below 500 px the acts I and II stack in one column. Values are inserted as text (no HTML).
- Dashboard page: the "Copy „…”" buttons (approval word to the clipboard) are removed entirely, including `actions.copyText`
  in the renderer's `deps` (breaking for a host that passed it; no consumers outside Skippster). "Open session" stays.
- Dashboard page: the header marks an automatically chosen plan with a "wybrany automatycznie" badge (`planAuto: true`) and
  shows the full plan path in the button tooltip; a manually chosen plan has no badge.

### Breaking
None.

### Fixed
- Windows: `skillsync` commands and the standalone `prototype_server.py` switch stdout and stderr to UTF-8 (`errors=replace`) when
  the output is redirected and uses another encoding (cp1252). Polish messages no longer crash `prototype serve` with `UnicodeEncodeError`.
- Windows: a clone left half-done by `skillsync setup`, `layers add` or `layers init` is removed even when it holds read-only files
  (git objects); the cleanup uses the new `rmtree_force` instead of `rmtree(ignore_errors=True)`, which swallowed the error and left the clone.
- Test `leakguard pre-push` feeds the hook as bytes (text mode added `\r` to the pushed sha on Windows); the hook itself is unchanged.
- Windows: `link` recognises a dangling library symlink (e.g. after a skill was renamed in the library) regardless of the path form
  (`\\?\` prefix) and removes it as obsolete; `is_library_symlink` compares paths through the existing `_link_text` and also judges a dangling link by its own target.
- `skillsync dashboard`: the automatic plan choice no longer lets an `in_progress`/`approved` plan with all steps checked and no run directory (an orphan) hide a plan with a live run (the page showed "Act III, 35/35" for a finished, unclosed plan); within a status rank the plan with the freshest run activity wins. The JSON contract (`schema: 1`) is unchanged.
- `skillsync doctor`: the warning for an `in_progress` plan with all steps checked names the run state (no `Przebieg:` line, missing run directory, no `final` record, record already present).
- Skill `feature`: the `demo` ceremony says that after the spec and its review the next step is the prototype, not idle waiting for "GO plan".

## [1.1.0] - 2026-11-11

### Added

- Manifest key `workflow.ceremony` (`full` default | `demo`, lowercase; read only from the project manifest). `skillsync route` returns
  `ceremony {value, source: manifest|default|invalid[, error]}`; `skillsync doctor` reports an error for any other value.
- Per-phase prototype gate: the `[UI]` marker on a phase of the plan table of steps (`Id` or `Krok` cell). `skillsync prototype gate --plan <plan>
  --phase <Fn>` (new option `--phase`, new report fields `phase`, `phases` and `uiPhases`; exit codes unchanged; new BLOCKED reasons `unknown_phase` and
  `ui_phase_without_prototype`). For plans with `[UI]` phases `skillsync readiness` no longer blocks on a gate FAIL and adds `uiPhases`,
  `nonUiPhases`, `uiPhasesWaiting` and `warnings`. Plans without `[UI]` behave as before.
- Spec question classes: blocking `- Q<n>: …` and non-blocking `- Q<n> [ZAŁOŻENIE]: … — przyjęte: …`.
- "GO plan" now includes one local commit (spec, plan draft, prototype) on the working branch in a separate worktree, without push.
- `skillsync dashboard show <project> [--json] [--plan <rel>]`: the project status as a JSON contract (`schema: 1`, read-only;
  additive fields `readiness`, `assumptions`, `blockingQuestions`, `substeps`); the contract and fixtures are in
  [docs/dashboard-contract.md](docs/dashboard-contract.md) and `tests/fixtures/dashboard/`.
- `skillsync dashboard serve <project> [--port 5140] [--host 127.0.0.1] [--approve-host H] [--no-approvals]`: the status page
  with live refresh and approval buttons. The port is chosen per project so pages do not collide; the `Host` header is checked
  on every endpoint, reads from outside loopback only through `--approve-host`; approvals are recorded in-process.
- `skillsync dashboard metrics <project> <run-id> [--json] [--plan <rel>]`: per-substep times (`Fn.k`), rounds and failures of
  a run; a substep without data is marked explicitly.
- Dashboard page: act I shows a "Questions" section (blocking questions and assumptions to correct at the prototype, an
  explicit "none" when empty, visible to every viewer), act II a "Readiness" tile (PASS / BLOCKED / no data, date, short
  Plan-sha, a stale-brief marker and a link to `readiness.md` in the document preview), act III a collapsible list of
  `Fn.k` substeps with times or "no data". All values are HTML-escaped; tooltips come from new `descriptions.json` keys.
- `skillsync approvals record <project> --word … --run … --source cli|ide`: the single writer of
  `.skillsync/runs/<run-id>/approvals.jsonl` (file lock, duplicate refusal, secret masking). Only the user records approvals.
- Skill `dashboard` (status page) in the core library; domain record `docs/domain/dashboard-server.md` (server trust model).
- `skillsync dashboard serve --read-host H` (repeatable): an extra `Host` name (port ignored, case-insensitive) that may read
  everything (page, assets, `/api/*`, SSE, raw `/api/doc` preview) but not write from a viewer's browser: `POST /api/approve` answers 403 with the same message as a foreign
  `Host`. Browser-only guarantee: a client forging the `Host` header (e.g. `curl -H 'Host: localhost'` through the tunnel) can still write, and a tunnel rewriting `Host` to localhost gives every viewer write access; use `--no-approvals` for untrusted viewers: run a second instance for them, `skillsync dashboard serve <project> --port <other port> --no-approvals --read-host <host>`, while the presenter keeps the loopback instance with approvals (the server warns at start). Host values with a port, brackets, `/` or whitespace are rejected (BLOCKED). Meant for viewers of a demo through a tunnel; approvals stay with loopback and `--approve-host`. `/api/status`
  gains the additive `approvals.viewer` (true for a read-only host, where `approvals.here` is false). The server prints
  read-only hosts separately from approval hosts at start.
- `skillsync status --json`: each plan record with a valid `Spec:` line has `preImplement` (`required`, `sections`, `run`, `report`,
  `verdict`, `warning`; `null` when the plan has no valid `Spec:` line or the spec file is unreadable). `required` is true when the spec has a non-empty `Kontrakty`,
  `Migracja` or `Dane` section. `warning` is a non-blocking reminder for `draft`, `approved` and `in_progress` plans: no
  `pre-implement*.md` with the first line `Werdykt: READY|NEEDS_CHANGES|BLOCKED` in `.skillsync/runs/<run-id>/`, or the worst
  verdict is `NEEDS_CHANGES` or `BLOCKED`. The schema version is unchanged.
- `plan approve` and `spec approve` JSON: field `warnings` (a list, possibly empty) with the same pre-implement text. Status
  and exit code do not change.
- `skillsync readiness --run-id <id>`: writes `.skillsync/runs/<id>/readiness.md` atomically (first line
  `Readiness: PASS|BLOCKED`, then risk, areas, plan, issues, sections, files, note) and adds `reportFile` to the JSON. It refuses
  symlinked run paths and an invalid run id. Without the flag the behaviour is unchanged.
- `skillsync prototype list --json`: `acceptance.perScreen` — `[{id, state}]` for every screen in document order, with `state`
  `accepted`, `stale` or `pending` (accepted + stale + pending = `screens`). The sums `screens`, `accepted` and `stale` are unchanged.
- `skillsync prototype login-link <project> --user LOGIN [--path] [--port] [--users] --json`: a one-time login link for
  the running prototype server (256-bit token, 60 s, one use; result `{schema, status, url, expires}` or `BLOCKED` with a
  `code`). The server exchanges the token at `GET /_login/link` only for a loopback peer without a proxy (every refusal is
  the same 302 to `/_login`); the session has the full permissions of the account and its writes are recorded in the
  local audit log `.skillsync/prototype/run/audit.jsonl` (`{at, login, via, op, screen?}`; `comments.json` is unchanged).
  A human command: the agent does not run it.
- `serve` writes a state file `.skillsync/prototype/run/server-<port>.json` (0600; `pid`, `host`, `port`, `instance`,
  `version`, `root`, `users`) and removes it on stop; `/_api/v1/session` returns `server.instance` and `features` lists
  `login-link`. `ServerConfig(runtime_dir=...)` is optional (without it: no state file and no link endpoint).
- `serve` handles SIGTERM like Ctrl+C (the state file is removed).

### Changed

- Skill `feature`: `ceremony: demo` (one `kod` review per run plus the full loop for leak-guard, migration and security steps; documentation limited to
  the changelog and the plan "Wynik"; `retro-quality` and knowledge records deferred as "Dług (ceremony: demo)"), contract first, test-writer in
  parallel with review round 1 (only in phases that have a test-writer), two plans from five capabilities up, estimate of 75–105 min per capability,
  progress report of one line per sub-step.
- Core rules (`library/instructions/_base.md`): first commit at "GO plan" and the explicit `demo` exception in the "Done" rule.
- `status-board` in the manifest `skills:` list expands to `dashboard` only when no layer provides a skill named
  `status-board` (one rule in `common.py`, used by both skill expanders).

### Breaking
None.

### Fixed
- Prototype mockups: an open dropdown, autocomplete, calendar or menu placed inside `.modal-body`/`.drawer-body` was clipped by the body's `overflow` and hidden under the footer. New class `popover-float` (a sibling of the window, above it) and a pattern in `screen-patterns.md`.
- The prototype server request log never contains the query, also for a malformed request line (raw `requestline`).

## [1.0.2] - 2026-11-11

### Added

- `skillsync prototype wait <project> <slug> [--since ISO | --after OP-ID] [--timeout S]`: deterministic wait for the prototype
  review (for Claude Code `Monitor`): exit 0 with `event: comment` (a new thread or message by a person; `agent`, `import`,
  `server` entries do not count) or `event: accepted` (`gate` PASS), exit 3 on timeout.
- `skillsync prototype reply <project> <slug> <thread> --text …`: the agent answers in a comment thread (`by: agent`, under the
  file lock; `serve` shows it at the next poll). The prototype UI marks agent entries and thread cards with an "AI" badge.
- Skill `prototype`: after "ready for acceptance" the orchestrator waits on `prototype wait` (fallback: 30-minute `/loop`);
  an ambiguous comment gets a question in the thread (2–3 options and a recommendation, `czeka: odpowiedź w wątku #<n>`)
  instead of a guess; agent replies are neither consent nor decision.
- `prototype wait` comment events carry `authorRole`, `threadStatus`, the latest `decision` (with `whoRole`) and `reaction`
  (`implement` | `order?` | `reply`); skill `prototype` has the table "author role × thread state → agent reaction": the agent may
  answer in every thread but changes a screen only after „Wprowadź” by a developer or acceptor or on an unambiguous order from
  one of them; a commenter's comment is a proposal.

- `skillsync status --json`: field `approvals` — the latest consent per run and word from
  `.skillsync/runs/<run-id>/approvals.jsonl` (written by the status page buttons: `GO plan`, `GO implement`, `CP`, `READY`).
- `skillsync approvals wait <project> --word …`: deterministic wait for a consent (for Claude Code `Monitor`); exit 0 with the
  match, 3 on timeout; `--run`, `--sha`, `--since`, and `--file` (GO: an `approved` spec/plan also ends the wait).
- `feature` and the AGENTS core: `CP`/`READY` may also come from a status-page entry whose `sha` equals the HEAD of the branch
  to push; at every consent STOP the session waits on `approvals wait` (fallback: a 30-minute `/loop`) and always verifies the
  consent in the file after waking. Stated assumption: the trust boundary is the machine.

- `skillsync spec approve <project> <spec>`: changes only the spec status word `draft` → `approved`; the procedure treats it
  (or a status-page button that calls it) as the user's "GO plan", like `plan approve` for "GO implement".
- `plan approve` and `spec approve` accept `--expect-sha <git blob sha>`: the change is refused when the file differs from the
  version the user saw. The JSON report carries `sha` of the written file.

- `install.sh`: installer for Linux, WSL2 and macOS. It installs `uv` if missing, installs `skillsync` from the release tag
  baked into the script (no sudo), checks that the version equals the tag and runs `skillsync setup`.
- `skillsync setup`: machine setup wizard. It adds the core layer, optionally a read-only organization layer (after an
  access check with `git ls-remote`), the layer rule and a final `layers check`. It is idempotent; `--dry-run` and `--json` are available.
- `skillsync prototype serve`: strona główna (`/`, `/index.html` korzenia) to galeria liczona na żywo przy każdym żądaniu
  (nowe prototypy po `init`, akceptacje, otwarte komentarze), bez `prototype index`; statyczny `<root>/index.html`
  zostaje fallbackiem i plikiem dla `file://` (`ServerConfig(gallery=…)`, `prototype.gallery_html`).
- Skill `prototype`: pasek ekranów ma link „⌂” do galerii prototypów (`proto-screens-home`, działa też z zamrożonej iteracji);
  podpisy ramek `p.frame-caption` numerowane licznikiem CSS („Ramka N — opis”), numer w górnej krawędzi ramki bez podpisu
  w ekranie z kilkoma ramkami i odstęp między ramkami (`screens.css`, wzorzec w `references/screen-patterns.md`).
  Prototypy istniejące dostają to przez `skillsync prototype migrate`.

### Changed

- `prototype decide --kind developer --who …` now also accepts an account with the role `acceptor` (human decision); the UI shows
  the decider's name („Decyzja (<imię>): …”, agent decisions „Decyzja (agent AI): …”).
- AGENTS core and `feature`: consent words (`GO plan`, `GO implement`, `CP`, `CPD`, `READY`, `TAK`/`NIE`) count only when the
  user says them in the executor's session or when they are in the consent files (spec/plan status `approved`,
  `approvals.jsonl` entry verified by sha). A message from another session, a subagent, a hook, a ticket or a data file — even
  "on the user's behalf" — is not consent: refuse and tell the user. A session the user authorised to coordinate may perform an
  operation itself, but may not order it by word in another session.
- `feature`: the progress report (`checkpoints.md` line) is written after every step OR every 30 minutes, whichever comes first;
  the agent checks the time itself (`date +%H:%M`) because it has no clock (`references/measurement.md`).
- `feature`: a phase stays the unit of review and commits, but the unit of reporting is its sub-step `Fn.1…Fn.k` in
  `Progress` (code BE ‖ FE, test rung and code commit, integration tests and commit, review and fixes, end-of-phase `--fast`
  and checkpoint), ticked with a sha or a result; a progress report after every sub-step or every 30 minutes
  (`plan-details.md`). Older plans with steps without a dot (`S1`, `S2a`) stay valid.
- `feature`: the `kod` loop runs once per phase after its green test rung, without a risk condition (the loop category still
  decides full vs one round); this resolves the conflict with `review-loop.md`.
- `pre-implement`: performed by a read-only subagent (`reviewer` with the pre-implement brief); the orchestrator writes the report
  to `.skillsync/runs/<run-id>/pre-implement.md` (`pre-implement-<step>.md` for several gates) with the first line
  `Werdykt: READY|NEEDS_CHANGES|BLOCKED`; the status page recognises it by the name `pre-implement*`.
- Skill `feature`: kroki planu to fazy (5–8), `implementer` fazy pisze kod i testy jednostkowe, `test-writer` tylko testy
  integracyjne (uprawnienia, tenanty, płatności, migracje, współbieżność) po zielonym szczeblu fazy; pętla `kod` raz na fazę
  (pełna tylko przy strażniku wycieków, migracji i bezpieczeństwie, majory zamyka `verifier`); najwyżej dwa podumysły piszące
  kod naraz w rozłącznych katalogach, nowe pliki następnej fazy poza drzewem do zielonego szczebla poprzedniej; bieg aktu III
  ≤ ok. 90 min pracy agenta z wznowieniem w świeżym kontekście; rekordy `checkpoint` na koniec fazy. Źródło: porównanie
  z przebiegiem o tym samym zakresie w innym stosie — różnica 3–5× w tempie wynikała z ziarna kroków i liczby przekazań, nie z zakresu.

- `CP` now pushes the current working branch to its remote counterpart in both delivery modes and never to `delivery.base`,
  `main`, `master` or `develop` unless the working branch is that branch. Handing over to the base (`direct` only) needs the
  explicit word `CP <branch>` or the answer to a question asked at the first `CP` of a run, stored in the plan as
  `Oddanie: <branch>`; without an answer nothing is pushed. The fast-forward rules for pushing to the base are unchanged
  (AGENTS core, `feature`, `plan-details`, `release.md`).
- Skill `prototype` (SKILL.md): `prototype index` po `init`, usunięciu prototypu albo zmianie tytułu, gdy galerię ogląda się
  bez `serve` (plik statyczny); przed zgłoszeniem „gotowy do akceptacji” sprawdzenie sluga na stronie głównej `serve`
  (`prototype list` czyta katalogi, nie galerię).
- Zamknięcie planu: `skillsync status` oznacza plan `in_progress` ze wszystkimi krokami `Progress` odhaczonymi jako `needsHuman: ["closure"]`,
  a `skillsync doctor` ostrzega o nim; hook Stop `check-closure` blokuje turę raz, gdy taki plan jest zmieniony względem HEAD.
  `feature`: czekając na akceptację prototypu, orkiestrator może przygotować szkic planu `draft (szkic — czeka na GO plan)` bez
  `Spec-sha`; `Spec-sha` zapisuje dopiero „GO plan”.
  Plan z wejścia z klucza (`Ticket:` bez `PR:`) dostaje informację `needsHuman: ["pull_request"]` zamiast `closure` (czeka na PR-y; bez ostrzeżenia i blokady). `feature`: zamknięcie przebiegu („Wynik”, `retro-quality`, `--final`, plan `done`, spec
  `implemented`) jest obowiązkowym ostatnim krokiem `Progress` przed „czeka na READY/CP”, także przy commitach ręcznych; `pr-run`
  zapisuje `done` planu w ostatnim commicie przed READY. Nowa bramka otwartych planów przed zadaniem `feature`/`bugfix` (rdzeń AGENTS).
- Alias skilla `go-jira` → `acli` w manifestach (`skills:`) w obu miejscach rozwijania skilli (`skillsync:expand_skills`, `common.expand_project_skills`); `skillsync doctor` ostrzega, żeby zamienić `go-jira` na `acli`. `skillsync skills list` pokazuje `acli` z pochodzeniem `alias`.
- `docs/development.md`: po sklonowaniu repo do rozwoju zainstaluj hooki strażnika (`skillsync leakguard install-hook <klon>`); worktree dzielą `.git/hooks`.
- `docs/workflow.md`, `docs/workflow-details.md`: bramka modelu dyspozytora to Opus 5.5 lub Fable 5.1 (zgodnie z `library/skills/dispatch`).

### Removed
- Skill `go-jira` (przestarzały; zastąpiony przez `acli`). Wpis w manifeście nadal działa jako alias; ręczne odesłania do `skills/go-jira/` trzeba zamienić na `skills/acli/`.

### Breaking
- Role `Explore` renamed to `explore` (lower case like `implementer`, `reviewer`, `verifier`, …): `library/agents/explore.md`
  with `name: explore`, the `codex.yaml` key and all docs. Claude Code picks a subagent by the `name` in the frontmatter, so
  orchestrators must delegate to `explore`. Run `skillsync link` after updating: it removes `.claude/agents/Explore.md` and
  installs `explore.md`. Manifests with `agents: [Explore]` keep working for one release (alias, `doctor` warning), and a
  Codex `agent_overrides` entry under `Explore` still applies; replace both with `explore`.

### Fixed

- Prototype acceptance survives an engine update: `prototype.css` (engine chrome: toolbars, comment panel, dialogs) no longer
  enters the screen hash; acceptances computed with the former formula stay valid through aliases for the current file and the
  digests `migrate` records in `prototype.json` (`engineAssetHistory`) before replacing it. A change of a screen section, `<head>`
  or screen styles still invalidates the acceptance. `migrate` reports `acceptance.lost` with the same rule.
- `doctor` applies the `Explore` → `explore` role alias, so a manifest with `Explore` no longer reports a missing
  `.claude/agents/Explore.md` after `skillsync link`.

- `skillsync spec approve` and `plan approve` drop the waiting note the approval satisfies (`(… czeka na … GO plan)` for a spec,
  `(czeka na GO implement)` for a plan) instead of leaving `approved (… czeka na „GO plan”)`; other notes such as
  `czeka na spec` or `szkic` stay, as the resume table decides on them.
- Prototype threads panel: a long handle label on a thread card now wraps within the panel (the button's `nowrap`
  used to widen the panel horizontally and clip the screen heading); the group heading has the same edge spacing as the cards.

## [1.0.1] - 2026-11-09

### Added
None.

### Changed

- Leak guard: with `authors_scope: committer`, a commit authored by the local user but committed by someone else (cherry-pick, rebase, merge button) has its author checked against `authors`; the foreign committer stays a notice (`foreign-commit`).
- `layers check --isolation` looks for identity files in the library directory of a layer (`layer.yaml` root, e.g. `library/`), not in the whole clone, so examples under `docs/` of the core clone no longer raise `isolation-foreign-identity`.

### Breaking

- Leak guard: code files (`*.py`, `*.ts`, `*.js`, `*.mjs`, `*.cjs`, `*.ps1`, `*.sh`, `skillsync`, `skillsync-ctl`) outside `tests/**` and `.github/**` are in the path class `instrukcje`, also under `docs/` and in the root; before they were attributions, so an owner nick in a code docstring passed. Now it is `FAIL` in core and org targets (the owner's real name stays allowed in org), allowed in personal. File names and suffixes are compared case-insensitively. `path_class_rules` in `leakcheck --json` and `leakguard patterns --json` has a new key `code`, and `atrybucje` no longer has `suffixes` or `exact_names`; patterns and `allow` entries with `path_classes: [atrybucje]` no longer cover code files. Shipped in a PATCH release on purpose: it closes a leak gap in the leak guard released the day before, and the only consumers of the `path_class_rules` schema are the core and our own layers (D297).

### Fixed
- Windows: paths in JSON output use `/` (`status --json` plans and `plan` results, run evidence, delivery logs, e2e observation, `skills list` references).
- Windows: `redact` applies the environment-secret substitution to dict keys only for secrets of 8+ characters, so a short secret value (`root` on the Windows runner) cannot rename structural keys such as `root` or `composed_root` in JSON reports; values keep the 4+ character rule (the `delivery` report scrubber follows the same key rule).
- Prototype, mobile view: loading a view iframe no longer scrolls the page. The iframe address fragment was `#sN`, and the iframe document has an element with that id, so the browser scrolled it into view in the same-origin parent too (page jump of hundreds of pixels, also when a lazy iframe enters the window after the comment panel shrinks the layout). The fragment is now `#view-sN`; the embedded instance still accepts the old `#sN`. This fixes the V-23 known defect and the flaky `N-R1` (mobile) e2e scenario; the iteration compare frames (`?embed=compare#sN`, frozen older releases) are unchanged.

## [1.0.0] - 2026-11-08

### Added

- Layers: `core`, `org` and `personal` layers described by `layer.yaml`; machine config with `layers` and `layer_rules`;
  layers are chosen by the real path of the project.
- One resolver for layer paths across all commands.
- Layer composition: merging per file and per key, skills composed from several layers, `instructions/_base.d/`
  fragments, `allow_override` for overriding core elements.
- Layer commands: `skillsync layers init|add|use|update|list|check|migrate`; `--layer id=path` on commands that accept it.
- Leak guard: `skillsync leakcheck` (content, file names, commit metadata; `--worktree`, `--staged`, `--range`, `--history`)
  and `skillsync leakguard install-hook|patterns`; local git hooks that run the check.
- Version checks: `version` and `core_min` in `layer.yaml`; `layers init` writes `core_min` from the generator version.
  A layer whose `core_min` is above the generator version is BLOCKED (`core-min-unmet`). A core version that differs
  from the generator version is a warning (`core-version-mismatch`).
- Start versions of the program layers: `version` 1.0.0, `core_min` 0.9.0.
- `skillsync --version`; the generator also runs as `python -m skillsync_workflow`. Commands that Skillsync writes into
  artifacts (VS Code task, launchers, guard hooks, `{skillsync}`) call it through the same interpreter: from a checkout
  `python <checkout>/skillsync`, from the installed package `python -P -m skillsync_workflow`.
- `layers check` reports `hook-command-missing` (FAIL) when a guard hook of a layer calls a file that no longer exists.
- `SKILLSYNC_NO_SYMLINK` accepts `1`/`true`/`yes`/`on` and `0`/`false`/`no`/`off`; any other value warns and turns copy
  mode on.
- v1.0.0 supports Linux, WSL and macOS; native Windows is planned for v1.0.1. CI on Ubuntu and macOS blocks the release; the Windows jobs do not.
- Installation as a Python package with `uv tool install` (see [README](README.md)): `pyproject.toml` (hatchling, entry point
  `skillsync`), `scripts/check_wheel.py` (wheel contents) and `scripts/check_release.py` (CHANGELOG section for the version or tag).
- CI: Python 3.11 and 3.12 matrix, `install-smoke` (wheel built, installed as a tool, run outside the checkout), `e2e` (Chrome),
  `release-check`, and `release-smoke` after a `v*` tag. Tests run against the `library/` of the checkout.
- `tests/test_skillsync_library_smoke.py`: the shipped `library/` links and passes `doctor` in both link modes.
- Delivery mode: optional manifest section `delivery` (`mode`: `pr` | `direct`, `base`), validated by `skillsync doctor`
  and returned by `skillsync route` as `delivery {mode, base, source}`.

### Changed

- The library describes both delivery modes: the agent commits after each verified plan step on a work branch; `pr` (default) starts
  `pr-run` on "GO implement" and waits for `READY`; `direct` commits locally and delivers on `CP`. `CP` in `direct` now pushes
  `work:base` (fast-forward) instead of committing in place. `READY` and `READY!` come only from the user; merge and complete PR are never
  agent actions. In `pr` parallel cards get no `PUSH SLOT` on the base branch.
- A consistency guard (`tests/test_skillsync_delivery_mode.py`) fails on pre-mode sentences such as "commit only on command" in `library/`,
  on a base instruction without `READY`/`CP!`/`CPD!`, and on any instruction that allows merging or completing a PR.

### Breaking

- None against earlier public releases; this is the first public release.
- The `library` key of the machine config is not read. Run `skillsync layers migrate` (dry-run, then `--apply`);
  see [docs/upgrading.md](docs/upgrading.md).

### Fixed

- Prototype toolbar on phones (<600 px): the hint `.anno-save` has its natural width capped at 11rem and is ellipsized, so a wider system font (e.g. DejaVu Sans)
  no longer pushes the "⋯" button into a third row (logged-out toolbar 151 px → 117 px).
- E2E V-02 reads the pre-D28b engine from `tests/fixtures/prototype/engine-v2-prev/` instead of git history, which the public core does not have;
  V-02 now checks that the bar is not taller than the reference (+2 px) instead of equal to it (D271).

### Known issues

- E2E scenario N-R1 (comment panel, mobile view) is unstable: it sometimes fails with a page jump of about −3000 px (open shift equals close shift).
  On clean `main` 6134a8c: 2 of 6 runs fail with DejaVu Sans, 0 of 6 with the default font. Possibly related to V-23. Fix planned for 1.0.1.

[Unreleased]: https://github.com/BATUSystems/skillsync/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/BATUSystems/skillsync/releases/tag/v1.2.0
[1.1.0]: https://github.com/BATUSystems/skillsync/releases/tag/v1.1.0
[1.0.2]: https://github.com/BATUSystems/skillsync/releases/tag/v1.0.2
[1.0.1]: https://github.com/BATUSystems/skillsync/releases/tag/v1.0.1
[1.0.0]: https://github.com/BATUSystems/skillsync/releases/tag/v1.0.0
