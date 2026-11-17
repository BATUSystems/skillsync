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

- **Knowledge directories as a list.** `workflow.knowledge.domain` and `workflow.knowledge.lessons` accept a string (as before) or a list of up to 8 strings, so a project made of several repositories can load the knowledge records of a sibling repository (`domain: [docs/domain, ../repo-a/docs/domain]`). A string is equivalent to a one-element list; errors name the element (`workflow.knowledge.domain[1] must end with docs/domain`); a repeated directory (also through `..` or a symlink) is rejected. `route` returns the records in list order (layer lessons first, `bootstrap` records from every directory); a directory without `index.yaml` is skipped. `doctor` warns per element (directory exists / does not exist), ignores the listed directories in the scattered-knowledge check and warns when the `bootstrap` records of all directories exceed 24576 bytes. `knowledge init` with a list writes only inside the project root and reports the other directories in the new `skipped` field. Description and the known dependency on the clone directory name: `docs/workflow.md`.

### Changed

- **Prototypes: `skillsync prototype serve` no longer serves the journal as a static file.** `GET /<slug>/comments.json` and `GET /<slug>/comments.js` (and the same names at the root) answer `404` for every role and with or without `--login-to-view`, like `questions.json` and like `serve --demo` already did; deeper files of those names (prototype data) are still served. The journal is read through `GET /_api/v1/prototypes/<slug>/operations`, which gives the same operations to the same sessions (it did not filter by role either), so confidentiality does not change, consistency does. The engine is not affected: under `serve` it reads the API, and on plain static hosting (no skillsync API) it still reads `comments.json` from that host. A script that fetched `comments.json` over HTTP from `serve` must switch to the API endpoint (upgrade: `docs/upgrading.md`, "Przejście 1.8.0 → 1.9.0"). The pinning test `tests/test_skillsync_prototype_comments_visibility.py` now pins the 404.

### Fixed

- **Prototypes: the engine dialogs "Zaloguj się, żeby komentować" and "Ustaw własne hasło" had a broken layout** (in every theme, also without a client theme; the same markup since the first release): the login fields sat inside a second rounded frame (the card of `.form-group` from `components.css`), the footer `.anno-dialog-actions` had no rules, so "Anuluj" and "Zaloguj" touched each other, stuck to the left edge and to the frame, and a focused field showed two rings (2 px outline plus the 3 px glow). Now: one container, a label next to its field and an even gap between the pairs, a footer row aligned to the right with a gap (like the existing rule for `dialog menu`), and a single focus ring (border in `--ring` plus the glow; the outline stays transparent for forced colors). The change is only in `prototype.css`, so it does not change the hash of any screen and does not invalidate an acceptance (`prototype.css` and `prototype.js` are not part of the hash); no migration step, `skillsync prototype migrate` copies the new sheet.
- **Status page: the "Wynik" section shows formatted Markdown instead of raw text.** The block under the acts printed the `## Wynik` section of the plan as plain text (`###`, `**…**`, backticks and table rows were visible as typed). `GET /api/status` now adds, to `result`, the additive fields `html` (visible part), `moreHtml` and `moreLines` (collapsed rest), rendered by the same safe renderer as the document view (`/api/doc`): headings, lists, bold, inline code, tables, paragraphs; raw HTML of the source is escaped and only safe link schemes are links. The section is masked as a whole before it is cut or rendered, and the page inserts the HTML only through the host's `mountHtml`, never with its own `innerHTML`. "Pokaż resztę (N linii)" cuts only between Markdown blocks (never inside a table, list, code block or paragraph); the rule is in `docs/dashboard-contract.md`. `result.text` and `result.path` stay as they were, `dashboard show` and `skillsync status` are unchanged (no `html` there), a client without `html` still gets the plain text. Other places of the page that show plan or spec text as plain text (step titles and notes, expert question text) are unchanged.
- `skillsync route` no longer ends `BLOCKED` ("is not in the subpath of …") when the knowledge directory lies outside the project (`domain: ../repo-a/docs/domain`) or a changed file lies outside the base of the directory. Record `paths` are matched in the coordinates of the owner directory; a file outside its base is prefixed with the project directory name (`repo-b/src/**`).

## [1.8.0] - 2026-11-17

Upgrading from 1.7.0: [docs/upgrading.md](docs/upgrading.md), section "Przejście 1.7.0 → 1.8.0" (the point numbers below refer to it).

### Added

- **Status page on the local network.** `skillsync dashboard serve <project>` without port flags runs two sockets: the read socket on port 5140 on all interfaces and the consent socket on port 5141 on `127.0.0.1` only (see Breaking). A `dashboard:` section in the machine config and in the project manifest sets where it listens: `local_only: true|false` (true: both sockets on 127.0.0.1; false: no preference), `read_bind` and `approve_bind` (a literal IP, `localhost`, `0.0.0.0` or `::`). An explicit bind wins over the `local_only` shortcut of the same source. Precedence: CLI flag (`--read-bind`/`--approve-bind`; for the read socket then `--host`, which never moves the consent socket) over the manifest over the machine config over the defaults. An unknown key: `doctor` warns and `serve` is BLOCKED with the key name; a bad value: an error with the key name, never the value; no environment variable for this setting. The `dashboard:` setting of a project manifest can only narrow (a loopback address or `local_only: true`); a wider address there is an error of `doctor` and blocks `serve`, and an unreadable machine config blocks `serve` instead of silently dropping the setting. `--host` in the two-port mode moves only the read socket; `serve --host ADDR` and `serve --approve-host NAME` without port flags end with BLOCKED and a hint.
- `dashboard url --lan` prints the addresses of the read socket for other devices, one per line (`http://<IP>:<port>/`); in `--json` the new field `lanUrls`. BLOCKED `read_not_exposed` (exit 2) when the read socket listens on loopback only. State files of `dashboard serve` have a new field `bind`: the address the socket really listens on.
- The read socket accepts its own machine name as the `Host` header (`socket.gethostname()`, its short form and `<short>.local`): a text match without DNS, case-insensitive, read-only; an empty or localhost-like name is ignored, and a full name with another suffix (for example `laptop.lan`) still gets 403 (add it with `--read-host`). The consent socket is unchanged (loopback and `--approve-host`). The 403 for a `Host` outside the list names the flag that opens THIS socket (`--read-host` for the read socket, `--approve-host` for the consent socket, both in the single-port mode) and the cleaned `Host` of the request.
- Startup messages of `dashboard serve`: one warning per read socket that listens beyond loopback (plans, specifications, results, secrets masked heuristically, unencrypted traffic) with the hint `--read-bind 127.0.0.1` or `dashboard.local_only: true`; a line about the default listen address change and how to return to it, printed by releases 1.8 and 1.9 only; the machine's IPv4 addresses (one per line; an IPv6-only network gets none) plus the address by machine name (`http://<name>.local:5140/`); a line that consent is local-only with the hint `--approve-bind 0.0.0.0` (the token travels over unencrypted HTTP); "page on this machine only" when both sockets listen on loopback; on macOS the application firewall may also ask for access at the first listen. A busy port is BLOCKED with the port number, the flags to change (`--read-port`/`--approve-port`) and its owner (the project from the state files, or "another process"); before each bind every port is probed with a loopback connection on every system, because on macOS/BSD and Windows a narrower address can bind next to someone else's wildcard one. The server does not move to another port.
- Windows: both sockets of the two-port mode get `SO_EXCLUSIVEADDRUSE`, so a second process cannot take over the narrower address; the first bind on `0.0.0.0` triggers the Windows Firewall prompt (allowing it on a private network gives a view from a phone, refusing keeps the page local).
- **`skillsync test-run <project> [--run RUN] [--step ID] [--junit FILE|DIR ...] [--allow-no-count REASON] [--allow-skips] [--format auto|none|NAME] -- <command...>`**: runs a test command of the project through the shared runner and records its events for the status page (a step of a plan). The output and the exit code of the command are passed through unchanged; `PASS` is recorded only for a known, positive number of executed tests (the rule of `validate`: zero tests, an unknown count and skipped tests are `BLOCKED` with the same reasons; so is a clean exit code with a failure summary in the output, `FAILED (failures=N)`, `N failed in Xs`), the list of the tests comes from a JUnit XML or TRX report (`--junit`, or the default places of dotnet, Maven, Gradle and karma-junit-reporter). The command after the first standalone `--` is cut off before argparse, so every supported Python reads the call the same way. The events file is read as untrusted input (a line too long or too deep, NaN, infinity, a number out of range, a value of a kind JSON does not have is skipped; `tests.skipped` names what was left out), SIGTERM ends the command and writes `end`, the echo of the output cannot cost the record, and the command line is masked also after secret flags (`--token VALUE`, `-api-key VALUE`, `-p` for mysql and sshpass; a heuristic, limits in `docs/security.md`). `validate` and `smart-test` do not record test events (without an observer their result and log are unchanged).
- `skillsync test-run` adapters read the output of `unittest`, `pytest`, `dotnet test`, `cargo test` (`cargo-test`: several test threads, a test appears after its end; `cargo-test-serial`: `--test-threads=1`, the start of a test is read from the unfinished line), Vitest (`verbose` reporter), Karma + Jasmine (`progress` reporter, names from the `karma-junit-reporter` report), Playwright Test (`list` reporter, one test per project), Cypress (`spec` reporter) and `ng test` (Vitest or Karma, told apart by the lines). The line about the choice of the adapter goes to stderr (`adapter <name>`, or `no adapter for <tool>` with the way out `--format <name>` / `--format none`) and says how to get the list of tests (`--reporter=verbose`, `--reporters=verbose` for `ng test`; for Karma the junit reporter and `--junit <file>`). The tool is found behind `npx`, `npm exec`, `pnpm`, `yarn`, `timeout`, `env`, `time`, `nice`, `systemd-run`, `uv run` and `python -m`; the table of the tools and the syntax of the wrappers is data (`skillsync_workflow/data/test_tools.json`, schema 1, shared with the hook below). None of the web tools prints a line when a single test starts, so a test is `waiting` until its result (`completion`) and Karma shows counters only (`counts`); the `end` record takes its counters from the adapter when the stream is complete, `source: adapter`. Fixtures are recordings of real runs; per-tool limits and what was not verified: `docs/test-adapters.md`.
- **Status page, the tab "Testy"** (hidden until the first run): a section per plan step that ran tests, header = step id and title, counts, newest first; only the running section is open, finished ones are folded (a red chip and the count on the tab show failures); a progress bar "passed / all" and a link to the section next to the step on the "Postęp" tab. A test with no result is `waiting` after the end of the run, a live local process is never shown as interrupted for silence, and on Windows the liveness probe reads the exit code of the process (`OpenProcess`, `GetExitCodeProcess`), so a run whose process died shows as interrupted. New optional JSON fields `tests` and `progress.phases[].steps[].tests` (schema 1, additive) and the read-only route `GET /api/tests` (limit/offset, validated names).
- **Test steps** (point 8 of the upgrade): mark a step `[T]` in the plan (`## Progress` line or the step table); `checkpoint --status PASS` of such a step needs, as `--evidence`, the events file of a passing `test-run` of THIS step made on the current content (the tree of the working state without `.skillsync/` and `docs/plans/`; the tree at the start and at the end of the run must be equal, the newest run on the same content decides), or `--no-tests <reason>` (recorded, `doctor` warns `tests-waived`, `skillsync status` shows `needsHuman: tests`). `doctor` also warns `tests-not-run` and `tests-step-not-marked`. The gate protects against skipping the tests, not against forging the evidence file. `skillsync test-run --import <file> --source-sha <sha>` stores an events file written on ANOTHER machine (validated, byte for byte; accepted as evidence only for a clean tree); `doctor` warns `runs-not-ignored` (unmasked results in run files that git tracks or does not ignore) and `tests-reporter-missing` (an Angular project without a JUnit/JSON reporter). `plans[].needsHuman` of `skillsync status` has a new value `tests`: a consumer must accept unknown values. New manifest key `tests.mask` (default off, see Security).
- **Delete guard hook for Claude Code** (`library/hooks/guard-commands.py`, PreToolUse on `Bash`; point 9 of the upgrade): refuses permanent deletion (`rm -r`, `command rm -r`, `git clean -f`, `git worktree remove --force`, `find -delete`, `rmtree` in `python -c`, PowerShell `Remove-Item -Recurse`, `rd /s`) unless every target is under a temporary directory, the session scratchpad, a regenerable project directory (`node_modules`, `.venv`, `__pycache__`, `dist`, `build`, `hooks.delete_allow`) or the command carries a one-time exception approved by the user (`SKILLSYNC_GUARD_EXCEPT='<reason>'`, trail in `.skillsync/hook-exceptions.jsonl`). The refusal names a ready replacement (`trash -- <paths>`). An exception prefix before `bash -c '...'` or `eval` covers the whole inner text; the wrapper list (timeout, nice, env, sudo, xargs, flock, systemd-run, stdbuf) is open, so an unknown wrapper hides the command, and `rmtree` in `python -c`, `xargs rm -r`, PowerShell and `rd` forms are refused without a target check. `find ... -delete` whose every `-name` is a regenerable artifact passes. The parser reads quotes, heredocs, comments, redirections, `$(...)`, `bash -c` and common wrappers, so text in arguments (`grep "rm -rf"`, commit messages) is not a command; it fails open (an error of the hook never blocks). On Windows Git Bash drive paths (`/c/work/x`, `/cygdrive/c/x`) are mapped to the drive; an unquoted native path (`rm -rf C:\work\x`) loses its backslashes in that shell and stays refused (quote it or use `C:/work/x`). A path rooted without a drive (`/data/x`, `hooks.delete_allow: /data/x`) means the same on Python 3.13 for Windows as before (the hook has its own `isabs`; `os.path.isabs` says False there since 3.13). A PowerShell tool outside `Bash` is not seen by the hook. Manifest: `hooks.guard_delete: deny | off` (default `deny` in projects with the `feature`, `agentflow` or `dispatch` skill, otherwise `off`) and `hooks.delete_allow`. `skillsync link` copies the script and merges one entry into `.claude/settings.local.json` (backup, foreign entries untouched); `link --dry-run` shows it; `doctor` reports the state.
- **Test commands warning** (second part of the same hook, `hooks.guard_tests: warn | off`, `hooks.test_wrappers`, `hooks.wide_modules`; default `warn` in projects with the `feature`, `agentflow` or `dispatch` skill): when an agent runs a WIDE test run outside `skillsync test-run` (`unittest discover`, `pytest` without a file, `dotnet test` without `--filter`, `cargo test`, `ng test`, `npm test`, `make test`, ...) the hook adds a warning to the context of the model. It only warns, never blocks; it names the program of the command (not any word of the line, so `echo pytest` is silent), reads the same table of tools as `test-run` (copied by `link` next to the hook as `test-tools.json`, with `guard-tests.py`), and a missing or broken file or an error of the classifier silences only this part: the refusal of deletion is unaffected. One-time exception: the `SKILLSYNC_GUARD_EXCEPT='<reason>'` prefix (trail with `kind: tests`). The text reaches the model together with the result of the command (after it ran); whether the model sees it on a live session is NOT_VERIFIED until measured. Limits: `library/skills/workflow/references/test-run.md`.
- **Prototypes: `prototype.dark_theme: false`** in the project manifest turns off the dark theme of the prototypes and the "Motyw" button of the engine bar (also the row of the "⋯" menu). The page stays light whatever the system and the stored choice say, with no dark flash at the first paint. `init` and `migrate` write `data-theme="light" data-proto-dark="off"` on the `<html>` tag (outside `<head>` and the screens), and the engine copy honours it; a screen hash and an acceptance do not change. The default (`true` or no key) changes nothing; a non-boolean value is a validation error. `sync --check` warns while the prototype lacks the marks or an engine that knows them (`prototype migrate`).
- **Prototypes: `prototype serve --demo`** is a read-only show without accounts: no users file, no login, no comments, threads, acceptance, expert answers, questions, restore or login link, and the server refuses it (one gate before the session and every route: `405 demo_read_only` for any method but GET and HEAD, `404` for `/_*` and every API route but `GET /_api/v1/session`, which says `server.demo: true`). The static files are a positive list (screens, engine, style sheets, media, `prototype.json` trimmed to `slug` and `title`; never `comments.json`, `comments.js`, `README.md`, `iterations/`, `questions.json`, dot files, also through a link or an encoded path); the gallery is the show variant (no comment counts, acceptance or requirements path); errors repeat nothing of the request and show no stack, and a broken request line gets a status line and the fixed body on every Python. Listens on `0.0.0.0` by default (`--host 127.0.0.1` narrows); the startup message says the pages are public, lists the addresses and how to narrow. `--demo` with `--login-to-view` is BLOCKED. The engine bar shows "Pokaz · tylko do odczytu" and no comment, login or acceptance tools.
- Skill `dispatch`: `references/preview.md`, the template of the order for a live preview (`dashboard serve`, `prototype serve`): an isolated state (`XDG_DATA_HOME`, `XDG_CACHE_HOME`, `SKILLSYNC_CONFIG`, and `HOME`/`LOCALAPPDATA`/`USERPROFILE`), a port from the pool assigned to the session, this machine only by default, stop by the recorded PID, a check that the real state directory got no file, and a check before a wide test run.
- Library skills `feature`, `dispatch` and `agent-runtime-pitfalls`: the session report template starts with `date: <output of date>`; event times come from logs or `date` (phrase guard in `tests/test_skillsync_pitfall_phrases.py`).
- `docs/measurements/`: records of measurements (date, system, version, command, result) and the guard `tests/test_skillsync_measurements.py`: a documentation table row that says "zmierzone"/"measured" must link to an existing record, every other row of a table with a "Pomiar" column must be marked "niezmierzone" or "oczekiwane".

### Changed

- `prototype sync --check` now fails (`FAIL`, exit 1, field `missing`) when the theme does not define every token of `required-tokens.txt` (see Breaking). `sync` without `--check`, `init` and `migrate` still only warn.
- Documentation of the prototypes: which selectors `tokens_from` reads (`.dark`, `[data-theme="dark"]`; the `@media (prefers-color-scheme: dark)` block is skipped with a warning), the order of the style sheets and its effect on specificity (tokens first, overriding rules in `project.css`), and which engine files enter a screen hash (`components.css`, `screens.css`, tokens and the project sheets do; `prototype.css` and `prototype.js` do not; the old text said a change of `prototype.css` invalidates acceptances). `docs/security.md` says who reads the comments of a prototype today: the static `comments.json` and `GET /_api/v1/prototypes/<slug>/operations` give the same operations to every role, and `--login-to-view` is the only gate of both (no change of behaviour, pinned by a test).
- `docs/dashboard-contract.md`, "how to stop the server on Windows": the table has a "Pomiar" column (linking to `docs/measurements/`) that separates what was measured (Ctrl+C, CTRL_BREAK, `Stop-Process`, `taskkill /F`, `Popen.terminate()`, `taskkill` without `/F`) from what is only expected from the system documentation (Windows Terminal, Task Manager "End task").
- Repository and CI (no change to the product code): the log of every CI run (Python 3.12 and newer) ends with the 30 slowest tests; the time limits of the `test` jobs are Windows 75, macOS 60 and Ubuntu 45 minutes; `scripts/merge_modules.py` computes the test modules to run at a merge from the diff, with a reason per module (it also selects the tests that run `link` and compare its output, and the prototype engine test when the engine or its copy changes); `scripts/platform_sensitive.py` warns (never gates) when a diff touches system-dependent code or a host hook (`library/hooks/`); `tests/tmp_paths.py` and `tests/test_skillsync_tmp_path_guard.py` guard paths built from data in tests; `tests/cli_inprocess.py` runs the `skillsync` entry point inside the test process; the E2E suite of the prototype engine has four new scenarios in a real Chrome (`tests/e2e/prototype/lt.mjs`: `dark_theme: false`, the default theme, the show in the browser and over HTTP); a local macOS matrix has its own skip baseline (`macos-uv/*`); the tests of the Tests tab isolate `HOME`, `LOCALAPPDATA` and `XDG_*` instead of `XDG_*` only (on macOS and Windows they wrote state files into the real data directory), and the server helper of the tests refuses to start with the real home.

### Fixed

- Commands with two positional arguments or an optional positional (`prototype sync <path> --json <slug>`, `prototype gate`, `prototype question wait`/`merge`, `prototype merge-comments` and the other commands of that shape, 72 in all) no longer fail with `unrecognized arguments` when an option stands between the positionals on Python 3.11 and earlier: the leaf parsers read their arguments intermixed there (`skillsync_workflow/argtools.py`); Python 3.12 and later are unchanged. A test generated from the parser definitions guards every such command.
- `skillsync layers update <layer>` (a layer without `--ref`): a `git fetch` that failed (no access to the remote) was reported as `BLOCKED (no-fast-forward)`; it is now `BLOCKED (fetch-failed)` with git's reason (credentials masked as in the other outputs). A diverged history stays `no-fast-forward`. Exit codes are unchanged.
- `skillsync doctor` ("Installed skill copy differs from source") and `skillsync link` ("exists and is not managed by skillsync"): both messages now say what to do (delete the copy, run `skillsync link`; changes made by hand in it are lost). No flag replaces foreign copies.
- Status page, the Progress tab: the grid of phases touched the "Pojęcia" (glossary) block above it (no gap at all, on a phone and on a wide screen alike). The grid now has a top margin equal to the gap between its cards in every layout.
- Lines of project text are now split with `common.file_lines` (CRLF, LF and a lone CR; never at VT, FF, FS, GS, RS, NEL, LS or PS, which arrive in text pasted from documents and web pages) in four more readers: the first line of a pre-implement report (`skillsync status`), the sections of risk of a spec (`pre_implement_sections`), the first line of a README in the repo map (`skillsync map`) and the first line of a skill in `skillsync list`. Before, such a character inside the line cut it: a risk section starting with `TODO<char>text` looked empty, a verdict line `Werdykt: READY<char>other text` was recorded as READY, a README purpose or a skill description ended at the character. The hunk lines of `git log -p` that `skillsync status` reads to see whether a plan was reopened are cut the same way. Not changed on purpose: the scanners of `leakguard` and the line numbers of `prototype` (they must agree with git and editors), and the import of logins (a change of meaning).
- `dashboard serve` and `prototype serve` on Windows: closing the console window (the "Close" button) now stops the server cleanly instead of ending it at once. The server registers a console control handler (`SetConsoleCtrlHandler` through `ctypes`, only on Windows and only in `serve`); on `CTRL_CLOSE_EVENT` the handler hands the stop to the main thread the way CTRL_BREAK does and waits for the cleanup (state files removed, sockets closed) for at most `min(4 s, system hung-application timeout - 1 s)`. Measured on Windows 11 with a classic console (hidden window closed with `WM_CLOSE`): before, the process ended in under 10 ms with exit code 0xC000013A and the state file stayed; after, the state file is removed (cleanup took 0.47 s). Windows Terminal (a pseudoconsole) and Task Manager "End task" are not measured. Linux and macOS are unchanged.

### Security

- **Test results are NOT masked by default**, on the status page (both sockets, also when the read port is visible in the local network) and in the run files `.skillsync/runs/<run>/tests/`: test names, groups and failure messages are stored and shown as they are (a failure message may carry the content of files the test reads). Only these leaves are exempt: the command line of a run and every other field of the page keep the masking (a heuristic, limits in `docs/security.md`). `doctor` warns when `.skillsync/runs` is tracked in git or not ignored. Do not paste test messages into pull requests, results or messages that leave the machine. Set `tests.mask: on` in the manifest to mask them (only new events are masked).
- The read socket of the status page is visible to every device on the local network by default (plans, specifications and results are readable there, secrets are masked heuristically, the traffic is unencrypted); the consent socket stays on this machine unless `--approve-bind` is set. The pages of `prototype serve --demo` are public by design.

### Compatibility

- Prototypes (point 7 of the upgrade): the new engine behaviour (`dark_theme`, the show label and hidden tools) reaches an existing prototype only through `skillsync prototype migrate`, which copies `prototype.js` and writes the `<html>` marks; neither changes a screen hash, so accepted screens stay accepted (`acceptance.lost: 0`). Without `migrate` an old engine copy ignores the marks (the theme button stays) and shows comment tools in the show (the server refuses every write anyway).
- Claude Code hooks (point 9 of the upgrade): projects with the `feature`, `agentflow` or `dispatch` skill get a new `PreToolUse` entry in the ignored `.claude/settings.local.json` (and `.claude/hooks/guard-commands.py`) on the next `skillsync link`, and `guard-tests.py` and `test-tools.json` next to it; commands that delete recursively outside temporary directories are then refused with a replacement, and wide test runs outside `test-run` get a warning. The entry and the files are removed only when both `hooks.guard_delete` and `hooks.guard_tests` are `off`: `hooks: {guard_delete: off}` alone keeps the hook while the test warning is on. A tracked `settings.local.json` is never written.
- Consumers of the JSON of the status page and of `skillsync status` (point 8 of the upgrade): new optional fields `tests`, `progress.phases[].steps[].tests`, `lanUrls` (`dashboard url --json`) and `bind` (state files), and the new value `tests` of `plans[].needsHuman`; schemas stay `schema: 1`, unknown fields and values must be tolerated.

### Breaking

- `prototype sync --check` exits 1 (`FAIL`) for a prototype whose theme lacks required tokens, where it used to exit 0 with only a warning. A CI job that ran `sync --check` on a project with an incomplete theme turns red; the fix is to define the tokens listed in the message (no switch to ignore the check). Migration: `docs/upgrading.md`, point 6.
- Default listen address of the status page: two-port mode (a change of behaviour, no change of flags or schemas). `skillsync dashboard serve <project>` without port flags now runs two sockets: the read socket on port 5140 on all interfaces (`0.0.0.0`) and the consent socket on port 5141 on `127.0.0.1` only. Before, one socket on 127.0.0.1 served both roles. Migration (`docs/upgrading.md`, points 2 to 5): (1) without flags the read socket is in the network and the consent address with its token is `skillsync dashboard url <project>`; (2) the read socket also listens on all interfaces with explicit `--read-port/--approve-port`, with `--read-port` alone and with `--no-approvals` (it listened on loopback before): add `--read-bind 127.0.0.1` or set `dashboard.local_only: true` to keep it local; whoever gave consents from a phone must add `--approve-bind 0.0.0.0`; (3) to keep the old behaviour (this machine only) set `dashboard.local_only: true` in the machine config or pass `--read-bind 127.0.0.1`; the single-port mode stays available as `--port 5140`; (4) `serve --host ADDR` and `serve --approve-host NAME` without port flags now end with BLOCKED and a hint (they used to mean one port with the `Host` gate); the old mode, also behind a tunnel, is `serve --port 5140 --approve-host <tunnel name>`; in the two-port mode `--host` moves only the read socket, and consents from the network need `--approve-bind 0.0.0.0`.

## [1.7.0] - 2026-11-16

### Added

- Status page: the "N unpushed" badge in the header lists the unpushed commits (short hash and first line of the message, newest first, up to 20, the rest as "and N older"). The data is the new optional `delivery.commits` / `delivery.commitsMore` field of the dashboard JSON (schema 1, additive); messages are masked for secrets before they are cut and rendered as plain text.
- Status page, document view (`#docs:<path>`): a floating "back to top" button (an icon, bottom right, 44 px, both themes) that appears after scrolling about three quarters of a screen and takes the page back to the top; keyboard and screen reader friendly (a `button` with the label "Wróć na górę strony", the focus moves to the "Dokumenty" tab), instant instead of smooth scrolling under `prefers-reduced-motion`. The renderer draws it only when the host passes the optional adapter hook `scroll.toTop` (`{subscribe, go}`), so the editor webview that shares the renderer is unchanged. No change to the status JSON or the HTTP routes.

### Fixed

- Plan table of phases (`prototype gate --plan`, `readiness --plan`): a table row that held VT, FF, FS, GS, RS, NEL, LS or PS (text pasted from a document or a web page) was cut at that character, so a `[UI]` mark behind it was lost and the rest of the table was ignored; the gate then saw a plan without UI phases and did not ask for a prototype. The table is read with `file_lines` now. The same for the line check of generated ignore files (`link`: an entry such as `skills/` was taken for present when a line of the file only started with it and held one of these characters; the bytes of the existing text are never rewritten) and for the header of a plan that `prototype gate` reads (a `Prototyp:` line that held such a character was cut to the part before it, so the gate could take a shorter path for the prototype; the line is read whole now and refused as `invalid_prototype_line`, and a `Bramka prototypu:` reason with the character is read whole; behavior change: a `Prototyp:` line that ENDED in such a character was accepted after the cut and is refused now, and the message of a refused `Prototyp:` or `Bramka prototypu:` line names the invisible character and its position, for example "invisible character U+2028 at position 31: remove it", and shows the line with `\\u2028` instead of the raw character; the reason code does not change) and for the fence stripper of the instruction check of `doctor` (a code block with such a character changed the state of the fence).
- Status page, `dashboard show`, `skillsync status` (the plan summary and the `## Wynik` section) and the consent readers: the text of project files is split into lines by the new `common.file_lines` (a line ends at CRLF, LF or a lone CR) and no longer by `str.splitlines()`, which also cut at VT, FF, FS, GS, RS, NEL (U+0085), LS (U+2028) and PS (U+2029). Before, a step title, a header value (`Spec:`), a checkpoint line or a question that held one of these characters, as text pasted from a document or a web page often does, was cut there without a message (the note and the sha of a step, or `czeka:` of a checkpoint, were lost); now the line stays whole and is shown as text. An `approvals.jsonl` entry with such a character in a JSON string was split into two broken lines and disappeared (`status.read_approvals`, the duplicate check of `approvals record` and the status page); it is read again. Plans, specs and checkpoints with Windows line ends already gave the same status as with LF; the new tests (`tests/test_skillsync_line_ends.py`) pin it with explicit bytes on every platform, and a guard forbids `splitlines()` and `split('\n')` on project text in `dashboard.py` and `dashboard_http.py`. The remaining readers of the package that still use `splitlines()` (`status.py` outside the plan summary and the consent readers, `prototype.py`, `leakguard.py` and a few more) keep it for now; the plan table, the ignore-file check and the fence stripper are converted (see the next entry).
- `dashboard serve` and `prototype serve` on Windows: CTRL_BREAK (`SIGBREAK`, sent with `os.kill(pid, signal.CTRL_BREAK_EVENT)` to a server started with `CREATE_NEW_PROCESS_GROUP`) now stops the server like Ctrl+C: exit code 0, the `finally` runs, the state files (both in the two-port mode) are removed. Before, the console default handler ended the server at once (exit code 0xC000013A) and left the state file with the consent token behind. A second CTRL_BREAK within 10 s of the first is ignored, so it cannot interrupt the cleanup. Linux and macOS are unchanged (the handler is registered only on `win32`). Hard ways to stop (`Stop-Process`, `taskkill /F`, closing the console window) still leave the state file; the next start on the same port replaces it. The table "how to stop the server on Windows" is in `docs/dashboard-contract.md`.

### Compatibility

- Plan header of the prototype gate: a `Prototyp:` or `Bramka prototypu:` line that holds an invisible line separator pasted from a document or a web page (VT, FF, FS, GS, RS, NEL, LS, PS) is refused with a message that names the character and its position; before, the line was cut at that character and the rest was ignored (a line that only ENDED in such a character was accepted). Remove the character.
- Dashboard JSON (schema 1): `delivery.commits` and `delivery.commitsMore` are new, always present keys (`null` and `0` without git); a renderer of 1.6.1 ignores them.

### Breaking

None.

## [1.6.1] - 2026-11-16

### Changed

- Behavior change of the file lists (`readiness`, `review`, `route`, `--files`): a file that is a link to another file of the project keeps its own name in the list instead of taking the name of its target. Glob rules match the path as git lists it; no default rule of the library matched a link by the name of its target.
- `skillsync doctor` warns (warning, not an error) when a file that `link` generates (skill links, instructions, `.mcp.json`) is listed in a `.gitignore` and tracked in git anyway, with the `git rm --cached ...` command: such files change with every `link` and a new worktree starts with stale links. The manifest is not counted (it may be tracked on purpose).
- Documentation: after `git worktree add` run `skillsync link <worktree>`; run `checkpoint`, `readiness` and `dashboard serve` in the same tree in which the code is written (`docs/workflow.md`, skill `feature`, `worker-mode.md`).
- Docs: status signals: a plan renamed after the closing word lights `acceptance` again until a new word, and the CLI signal and the status page label "odbiór" are two independent rules (`docs/workflow.md`, `docs/domain/statuses-and-exit-codes.md`).
- Docs: pre-push matrix and `docs/development.md`: rules for matrix runs on remote machines (incremental logs, process-tree death check by PID, one port pool per process, module lists without shell aliases, background start on macOS without `setsid`) and a PowerShell 5 pitfall.
- Docs: `docs/upgrading.md`: new section "Windows z instalacji uv" (update order, per-directory `layers list`, stale editor startup task, two `skillsync` commands on PATH).
- Tests only, no change of the product: servers started in a thread (`HttpCase` and the other test servers) bind through the retrying helper `bind_free`, and the tests of `prototype serve` and of the SIGINT start window retry a lost bind on another port, so a port that `free_port()` reported free but another process took before the bind no longer fails a test at random. `tests/serve_start.py` on Windows kills the whole process tree (`taskkill /T` by the own pid), ignores a state file left by a killed predecessor and retries only a lost bind. The subprocess tests of `dashboard serve` (`dashboard_server`, `dashboard_approve_token`) now run on Windows up to the stop; the assertions about a graceful stop stay POSIX-only, because the server handles only SIGTERM there.
- Tests: the E2E cleanup suite gains parsing of macOS-style `ps` rows from text (BSD `stat`, `etime`, one-digit day), the DST and zone tests, and a stranger-group case where the listed helpers carry another start time. `tests/skip-baseline.json`: nine more `posix-only` entries for `windows-latest/copy`.

### Fixed

- `skillsync layers use <id> --match <glob>`: when a `layer_rules` entry with exactly the same `match` already exists, the layer is added to that entry's `use` (no duplicate; core, org, personal order; a personal layer replaces the personal one) instead of appending a second entry that never takes effect because the first matching rule wins. A layer already present in that entry stays where it stands (the order decides which layer wins; `PASS`, nothing written). A flow list `use: [a, b]` is edited in place (the rest of the file, comments outside the list included, stays byte for byte; comments inside a multi-line list are lost, and a CRLF file gets LF endings, the original stays in the backup); a block list, an anchor or tag in front of the list, or any other unusual layout makes the command rewrite the whole file without comments (backup kept). A different `match` still creates a new rule; `--position` together with an existing rule of the same `match` is refused (`BLOCKED`, `bad-argument`).
- `layers check`, `doctor` and `link`: a `layer_rules` entry whose `match` repeats an earlier entry is reported as a warning (`rule-unreachable`), because the first matching rule wins and the later one never takes effect.
- `skillsync readiness`, `review`, `route`, `smart-test`, `validate` and `guard`: a changed file whose path lies in the project is no longer an escape from it, or a reason to refuse the command, only because it is a link. Before, a project that tracks the skill links of `sync.method: symlink` in git got `BLOCKED` ("Path escapes project: .github/skills/<skill>", "Zmieniony plik poza projektem przez dowiązanie" or "Workflow artifact symlinks are forbidden") as soon as `link` repointed such a link. A link as the LAST component of a path passes (git lists it so; a link to a directory outside the project too, with or without a trailing slash); a link in the middle of the path, `..` that leaves the project and an absolute path outside stay refused, because the parent directory is resolved. `..` after a link (`a/link/..`) is the real parent of the link target on POSIX, so it leaves the project and is refused; on Windows `..` is collapsed lexically before the system looks at links (`a\\link\\..\\x` is `a\\x`), so the path stays in the project, as it does for what the system opens. `guard` neither opens nor follows a changed link: the report gets `skipped: [{file, reason: "symlink"}]` (only when not empty); when every file covered by a rule is such a link (the usual state after `link`), the status is `PASS` with the reason "No content was checked", so `validate` is not blocked for lack of evidence, while a plain file next to the links is still scanned and a violation is `FAIL`. The paths a command opens (plan, environment, roles, prototypes) are checked as before.
- `skillsync link` and `skillsync doctor` on a project that tracks its skill links in git and was cloned or checked out into a worktree with `core.symlinks` off (the default of many Git for Windows installations): git writes each tracked link as a small regular file with the target as text. `doctor` named it "Invalid installed skill copy" and `link` skipped such a file under `.claude/skills` as "not managed by skillsync", so the repair never came. Now `doctor` says that a regular file stands where the skill link should be and gives `skillsync link <project>`, and `link` replaces the file, but only when git tracks that path as a link (mode 120000) and the file is small; an ordinary tracked file of the user is still left alone.
- `skillsync doctor`: "Missing installed skill" and a skill link whose target is gone (the links of a fresh `git worktree` of a project that tracks them) now say what to do (`skillsync link <project>`). The `reason` text of these two findings changed: it ends with `; run \`skillsync link <project>\`` (and the second one reads "Installed skill symlink points to a target that does not exist"), so a consumer of the JSON of `doctor` must match the phrase `Missing installed skill` by its beginning, not by equality.
- `skillsync checkpoint --evidence` says that it expects the path of a file in the project (not a description), in the message and in `--help`; a file outside the project is refused as before.
- `skillsync readiness --plan <plan of another tree>`: the message names the cause ("The plan lies outside the project ... run the command from the directory of the tree that holds the plan") instead of "Path escapes project".
- Status page, `Host` rule: an older good answer of the name resolution of this machine now serves a slow resolver for at most 5 minutes after the last successful lookup (`HOST_LOOKUP_STALE_MAX`); before, it was renewed every 15 s for ever, so an address that no longer belonged to the machine could stay accepted as local. After the limit the answer is empty (an unlisted `.255` address is refused, as before when there is no answer; `--approve-host <IP>` still lets one in). The contract names the trust boundary of the machine name resolution.
- `skillsync status`: the `acceptance` signal no longer stays silent when a plan was reopened after the closing word and closed again. An entry in `approvals.jsonl` closes a plan only if no later commit changed its `Status:` line (first 30 lines of the plan) to a word other than `done` (one extra `git log` per plan with a matching entry; any git error means the entry closes nothing). Edits of the text after `done` and `Status:` lines and mentions of status in the body do not count as a reopening.
- Prototype E2E environment (`tests/e2e/prototype/env.py`): the start time of a process read from `ps` is no longer moved by an hour in the repeated local hour of a DST change or by a zone change (`lstart` is resolved against `etime`); `chrome.pid` is written atomically (temporary file, then `os.replace`); an unreadable PID file is removed with a message on stderr instead of silently; a plain-number PID file of an unknown kind (not `chrome*`, `static-*`, `serve-*`) is kept, reported on stderr and never signalled (it does not count as a survivor).
- Prototype E2E environment on Windows: every command (`prepare`, `chrome`, `stop`) ends at once with `BLOCKED` on stderr and exit code 2 instead of a stack trace half way through a start; Windows was never supported there.
- Status page, progress report parser: a checkpoint line with an unclosed bracket (e.g. `w toku: F1.1 (a; b, F1.2; ryzyko: brak`) is split at every `;` as before, which could silently drop the ids after it; `run.parseWarnings` now names it with the reason `unbalanced-bracket` (segment `raport`, fragment up to 80 characters from the element that holds the bracket, redacted before the cut, once per checkpoint), and the page shows the reason in its warning line. Additive (schema 1); a renderer that does not know the reason shows it as sent.
- A plan with Windows line ends (CRLF; a checkout with `core.autocrlf` or an editor on Windows) no longer loses its link to the run: the `Przebieg:` line is read also when it ends in `\r\n`, so the closing signal `needsHuman: acceptance` of `skillsync status`, the run report of a plan, the pre-implement report lookup and the closure check of `quality` work for such plans (before, the line did not match and the run was treated as missing, with a false warning about the `Przebieg:` line). The other header, section and step readers of plans and specs were checked with CRLF and answer as with LF.

### Security

- `common.redact` (every JSON report of the CLI, the status page, `dashboard show`, `skillsync status`) masks more: a key followed by a quote before the colon (JSON pairs such as `"password": "x y"`; a quoted value is masked whole up to the first closing quote that no backslash escapes, so `\\"` inside a JSON string stays in the value; an unterminated quote takes the rest of the line, also in a file with Windows line ends; quotes inside a bare value belong to it), the key names `secret_key`, `api-key`, `apikey`, `access_key` and `private_key` (also as the end of a longer name), `Authorization: Basic` and `Digest`, and a multi-line secret from the environment in a file with Windows line ends. Not masked as before: bare token formats without a key, variables whose names do not say so, values shorter than 8 characters, and the words of a bare value after its first space.
- `skillsync status`: the status text of a document, the plan summary and the text of error messages are masked as a whole before markers are stripped, the text is cut or whitespace is collapsed (before, a secret from the environment that held `*` or a backtick, or lay on a cut, could show up changed or in part). Plans and specs are read through the same decoding as the status page: UTF-16 with a byte order mark is read as text and NUL characters are dropped before the masking; bytes that are not valid UTF-8 (without a byte order mark) stay a finding without a row, as before.
- Status page and `dashboard show`: the text of plans, specs and checkpoints is now masked as a whole right after it is read, before any parser cuts, splits or trims it (the final masking of the status stays as a second layer). Before, a secret from the environment that contained `,` or `;`, or lay on a cut (the first 160 characters of an expert question, the 4 KiB of `result`), could show up in part, e.g. as a prefix in `waiting[].what`. Text without secrets is read exactly as before.
- Status page, `GET /api/doc`: since the route exists (1.1.0) a document was served without masking secrets; since 1.6.0 that includes the read socket, which needs no login. The whole document is now masked before it is parsed and rendered, and the whole answer (the file name in `path` and in the fallback title too) once more, for every served root (`docs/specs`, `docs/plans`, `docs/design`, `docs/domain` and the reports of a run). UTF-16 files with a byte order mark are decoded as such and NUL characters are dropped before the masking (a secret split by NULs was glued together again by the renderer). Documents that contain no secret, no NUL character and no UTF-16 byte order mark render exactly as before; a file that has NUL characters or starts with a UTF-16 byte order mark is now read as text (before, its characters were separated by NULs). The masking is a heuristic, not a guarantee: it covers key patterns (`password`, `token`, `secret`, `api_key`, `authorization`, `bearer`, credentials in URLs) and the values of environment variables whose names say so and that have at least 8 characters; keep secrets out of project documents.
- Status page, `run.parseWarnings` (served on the read port without a login): a fragment is now cut out of the checkpoint text only after the whole text has been masked, for every reason (`no-step-id`, `later-id`, `unbalanced-bracket`). Before, the element was split before masking, so a secret from the environment that contained `,` or `;` was cut apart and shown unmasked in the fragment of a skipped element (released in 1.6.0).
- Prototype server and engine: a warning that the text of comments, thread replies, expert answers and answer comments is visible to others and is not masked ("Treść jest widoczna dla innych i nie jest maskowana: nie wpisuj haseł, kluczy ani tokenów."): under the fields of `/_experts` (own answer, answer comment, question comment), under the comment pop-up, the thread reply field, the edit of an entry and the restore reason of the engine (only when a server answered the session), and in the start warning `PUBLIC_READ_WARNING` ("... i nie są maskowane"). The server still stores and serves the text unchanged (no redaction, no scan); the documentation (`docs/prototypes.md`, the `prototype` skill) says so and recommends `--login-to-view` for prototypes shown outside the team.

### Compatibility

- The notes in the comment pop-up, the thread reply, the entry edit and the restore reason reach an existing prototype only after `skillsync prototype migrate` (the engine file `prototype.js` is copied; the stylesheets are byte for byte unchanged, so the acceptance of screens stays valid: `acceptance` of the `migrate` result is `{"lost": 0, "screens": []}`). An older engine copy keeps working without the notes.

### Breaking

None.

## [1.6.0] - 2026-11-16

### Added

- Status page, progress report parser: `;` inside brackets `()`, `[]`, `{}` no longer ends a segment of a checkpoint line (a line whose brackets do not balance is split at every `;` as before); `‖` and `||` join step ids; `run.parseWarnings[]` (up to 10 newest, fragment up to 80 characters, redacted before the cut; reasons `no-step-id`, `later-id`) names the elements of the "w toku" / "zrobione" lists that the id reading skips. The page shows one line about them. Additive (schema 1).
- `dashboard show` and `/api/status`: `acts[].stages[].state` (`done`, `active`, `planned`) and `since`, from the new checkpoint segment `trwa: <stage>` (a `czeka:` in the same line wins), from a spec in preparation, or from a valid writing marker; `acts[0].linkWarning`; `waiting[].who` (`{role, login, label}`, from the suffix `(kto: <role> [<login>])` of `czeka:` or from the source of the item); `progress.activePercent` (a step in progress counts as half a step); `result` (the `## Wynik` section of the chosen plan, up to 4 KiB); `prototypes[]` and `prototype.commentsAfterAcceptance` (comments newer than the last valid acceptance of a screen, in undecided threads); `expertQuestions.items[]` and `expertQuestions.runs[].addressees`. All optional (schema 1).
- `skillsync prototype writing <project> <slug> start --screens N` (1-200): the planned number of screens (`prototype.writing.screensPlanned`, `screensDone` counted like the acceptance); the page shows "N z M ekranów". The marker `.writing.json` stays at version 1; a renewal without `--screens` keeps the value.
- Status page: a pulsing indicator on a stage that is going on (stopped when the run is silent), "Czeka na: <role>", the progress bar in two shades (hatching and outline, contrast of the accent with the track at least 3:1 in both themes), the block "Wynik", a collapsible glossary, the addressee of an expert question, the toast "N nowych komentarzy po akceptacji", the tab "Pytania" (prototypes and expert questions; only when the JSON has `prototypes` or `expertQuestions.items`).
- The expert questions page `/_experts` shows to whom a question is addressed (`addresseesNode`).
- `common.plan_section(text, name)`: the one reader of the `## <name>` sections of a plan.
- `Handler._approvals_role()` is the one place that decides `here` and `viewer`; the viewer gets the same `/api/status` content as the operator (no filtering of logins or texts), only without writing consents.
- `skillsync status`: `plans[].summary`, the text of the plan section `## Streszczenie dla człowieka` (at most 40 lines and 4000 characters, heading markers removed, a cut text ends with `…`; `null` when the section is absent or empty). Additive (schema 1).
- `skillsync status`: `plans[].needsHuman` has the new value `acceptance` for a closed plan (`done`) that has the human acceptance step (`Odbiór` in `## Progress`) whose run (the `Przebieg:` line) is active in the worktree (`.skillsync/runs/<run-id>/` exists) and has no closing consent word of the delivery mode (`READY` in `pr`, `CP` in `direct`, a missing `delivery` is `pr`) in its `approvals.jsonl` whose sha is a commit where this plan already has the status `done` (read through git; an earlier `CP` of the run, an unknown or junk sha, or a commit where the plan is not `done` closes nothing). Every such plan waits on its own, so plans of the same date or without a date in the name are independent; a plan whose run directory is gone is history and a later commit does not light a finished plan again. The checkbox and any line of the plan never end the wait; `closure` and `acceptance` never occur together. A consumer that does not know the value shows it raw or ignores it.
- `skillsync readiness --plan` and `skillsync doctor` warn (a warning, never a block) when a plan with the status `draft` or `approved` has no `## Streszczenie dla człowieka` section or an empty one.
- `skillsync review`: the profile `prototyp` (a change only inside the prototype root, `docs/prototypes` by default, or `--profile kod|docs|prototyp`; the report has `prototypeFiles`). The checklist is `workflow.review.prototype` of `workflow/defaults.yaml`; a mixed change gets its items for the prototype files. Paths: `workflow.review.prototype_paths`.
- `workflow.review.prototype` grows on every level: the layers, `workflow/projects/<slug>.yaml` and the manifest only add items (order: core, layers, project, manifest; equal text keeps the first place; no normalisation); a project cannot remove a core item. Layer rule `('review', 'prototype')` in `layers.DEFAULTS_APPEND_LISTS`; a layer that uses the key sets `core_min` to at least 1.6.0.
- `skillsync prototype question add --screen <prototype>#s<N>` (repeatable, together with `--ref` at most 5): a link to a screen of a prototype on the server, stored as the ref `/<prototype>/index.html#sN` (strict `fullmatch`, the slug grammar of the server). The page `/_experts` renders it as the link "Zobacz ekran sN" (`rel="noopener noreferrer"`, new tab); any other relative address stays plain text.
- Read-only preview of the questions on `/_experts` for a signed-in account without the role `developer`, `acceptor` or `expert` (the default `commenter`): the API (`GET /_api/v1/questions`, `/_api/v1/questions/<id>` and the per-run routes `GET /_api/v1/prototypes/<slug>/questions[/<id>]`, the same handlers) answers 200 with `preview: true` and a view built from a white list of fields: no login or e-mail address of any person (addressees are counted, the author of an answer or a comment is the account `name` or a plain label, a withdrawal has no author), no `origin`, no `refersTo`. Writes stay `403 forbidden` before anything else.
- The prototype gallery (static and live) links "Pytania" to `/_experts/<slug>` and "Pytania" in the header when a run has questions.
- Prototype engine: the presentation mode shows one screen at a time (`proto-screen-hidden`), with ←/→, ‹ ›, Home/End, the counter "N / M", the key P and `?mode=presentation` as entries, Esc/P/"Wyjdź (Esc)" as exits, the screen in `#sN`, the state in `sessionStorage` of the tab, and one printed screen. E2E scenarios `P-01` to `P-06` (`docs/prototype-contract/scenariusze-e2e.md`, section 16).
- `dashboard serve --read-port N --approve-port M`: two-port mode of the status page. One process, one shared state, two HTTP sockets. The role comes from the port, not from the `Host` header: the read socket has no consent route (`POST /api/approve` returns 404) and no token; the consent socket keeps the 1.5.0 checks, adds an exact `Origin` check (`http://<Host>` with the port), refuses intermediary headers (`Forwarded`, `X-Forwarded-*`, `X-Real-IP`, `Via` and others) unless `--allow-proxy-headers` is given, and records where each consent came from. The port numbers are given explicitly (the flags have no default value; 5140 for reading and 5141 for consents are the usual pair). Combinations that make no sense end with `BLOCKED` (exit 2) before anything is bound.
- `dashboard serve --read-bind ADDR` and `--approve-bind ADDR` (listen address of each socket), and `--allow-proxy-headers` (consent socket only). In two-port mode both sockets listen on `0.0.0.0` by default; `--host` sets both, `127.0.0.1` narrows a socket to this machine. A startup warning is printed for every socket that listens beyond loopback.
- Single-port mode is unchanged: `dashboard serve` without the new flags, or with `--port`, still has one socket, listens on `127.0.0.1` unless `--host` is given, and takes the role from the `Host` header.
- Migration from two instances (one of them with `--no-approvals`): one call `dashboard serve <project> --read-port 5140 --approve-port 5141` replaces both. Stop the old processes first; both ports must be free, otherwise the start is `BLOCKED` with no side effects.
- Console: one line per socket at start (the read address without a token, and the consent address with the token); a line for every consent given from a non-loopback peer; a count of refused consent tokens, reported at most once a minute, without the token.
- `dashboard url <project> --read` prints the read address (no fragment, no token). In two-port mode `dashboard url --json` adds `role` and `readUrl`.
- State files: additive `role` (`read` or `approve`) and `peer` fields (`schema: 1`); a file without `role` is a single-port file.
- `/api/status`: `approvals.role` in two-port mode (`here`, `viewer` and `tokenRequired` then follow the socket, not the `Host`). `approvals.jsonl`: optional `via` object (`port`, `host`, `peer`) on consents from the consent socket; entries without `via` are read as before.
- Tests: the contract of the new fields, the renderer (a frozen copy of the 1.5.0 renderer on the current fixtures), the contrast of the progress bar, the syntax of the progress report in the docs.
- Tests: phrase guard of the library for the plan summary, the review profile and the question rules (`tests/test_skillsync_plan_checkpoints_skills.py`); `tests/test_skillsync_plan_summary.py`, `tests/test_skillsync_plan_acceptance.py`.

### Changed

- Fixtures `tests/fixtures/dashboard/*/expected*.json` only gained keys (no line removed).
- `prototype writing … --screens` is refused (`invalid_args`) for `done` and `status`.
- The prototype engine files (`prototype.js`, `prototype.css`) carry the presentation mode: `skillsync prototype migrate` delivers them (the change of the CSS can invalidate the acceptance of screens: the list is `acceptance` of the result).

### Fixed

- `dashboard serve`: the start and the stop of the server no longer wait for the resolution of the name of this machine (a name that does not resolve can block for tens of seconds, which delayed `SIGTERM`; this also concerned `--host 0.0.0.0` since 1.5.0). The lookup runs in the background with a limit of 2 seconds, one at a time and with a cached answer; when it is too slow, a `Host` that is an address ending in `.255` and is not found by the route probe is refused (fail closed).
- Prototype E2E environment (`tests/e2e/prototype/env.py`): the cleanup after a run no longer depends on `/proc`, so on macOS it stops Chrome, its helper processes and the test servers instead of leaving them running (and no longer deletes the PID files that were the only trace of them). A process counts as ours by its identity (pid, start time, user); the tree below it is listed before the first signal, so helpers that left the process group or were re-parented are stopped too, and only processes of that list whose identity still matches are signalled, never one chosen by name. The helper processes are also recorded (each by its own identity), so that they are stopped, with whatever lives below them, when the browser itself was closed from outside; no process group is swept after the leader died, and a partial or failed process list stops the cleanup instead of passing for a complete one. A PID file is removed only after its process is confirmed gone; a survivor is reported and its file stays. Test: `tests/test_skillsync_e2e_env_cleanup.py` (a stand-in for Chrome, no browser needed).

### Security

- Two-port mode: a request on the read socket cannot write anything (no route, no token); on the consent socket the `Origin` must match the `Host` exactly, a request with intermediary headers is refused, and `via.peer` is the address of the connection, so `X-Forwarded-For` cannot change it.
- Question preview on `/_experts`: a signed-in account without a role of the experts (`200` instead of `403`) sees the text of the questions and the names of the accounts that wrote the answers and comments (the account `name`, never a login or an e-mail address; a `name` that looks like a login or an address shows as a plain label); it still cannot write (`403`).
- Status page, read socket of the two-port mode: it needs no sign-in and listens on every interface by default, and the page shows the same content to a viewer as to the operator (plans, the text of expert questions, the logins of their addressees). Anyone who can reach the port can read it. On a network that is not trusted narrow the socket with `--read-bind 127.0.0.1` or use a tunnel.
- Known residual risk, documented in `docs/workflow.md` (section "Strona statusu: dwa tryby"): the consent token travels in plain HTTP. With the default `0.0.0.0` listen address on an untrusted network, anyone who can sniff the traffic can capture the token and give consents while the server runs. Use `--approve-bind 127.0.0.1` or an SSH tunnel (`ssh -L 5141:127.0.0.1:5141`) there, and point tunnels at the read port only.

### Compatibility

- All new keys are optional in `schema: 1`; a renderer of 1.5.0 (including the copies in editor plugins) ignores them, and the current renderer shows the old page for a JSON without them. A core older than 1.6.0 does not know `--screens` (the skill `prototype` says to repeat the command without it).
- `plans[].summary` is additive; `plans[].needsHuman` may contain `acceptance` (a consumer must accept an unknown value).
- Layers: a layer that adds items to `workflow.review.prototype` needs `core_min` 1.6.0 (an older core has no profile and `check_layers` blocks the layer); the old lists of a layer keep working.
- A server older than 1.6.0 shows a screen ref of `question add --screen` as plain text.
- A prototype gets the presentation mode only after `skillsync prototype migrate`.
- Two-port mode: both ports (`--read-port`, `--approve-port`; the migration example uses 5140 and 5141, the ports of the two old instances) must be free. A port taken by another service, or by an old instance that is still running, makes the start `BLOCKED` with no side effects (nothing is left bound or written).

### Breaking

- `GET /_api/v1/questions`, `/_api/v1/questions/<id>` and the per-run routes `GET /_api/v1/prototypes/<slug>/questions[/<id>]` of the prototype server: a signed-in account without a role of the experts gets `200` with a read-only preview (`preview: true`) instead of `403 forbidden`. A client that used the `403` to tell the missing role must read `preview`. Writes are still refused with `403`. Nothing else in this release is incompatible: the two-port mode of the status page is opt-in and the single-port mode is unchanged.

## [1.5.0] - 2026-11-15

### Added

- CI: the workflow `.github/workflows/pr-text.yml` (job `pr-text (leakcheck)`) scans the title and body of a pull request with `skillsync leakcheck --text` (the general patterns and the additions of `.leakguard/patterns.yaml` from the base branch; it also runs after an edit of the body, without the test matrix). Until now the check of descriptions was only a rule in the skills. The job is not a required check of the ruleset (changing the ruleset is the owner's decision).
- `skillsync layers check --isolation`: identity files in `leakguard.identity_dir` that belong to a configured `leakguard.targets` entry that is not a layer (for example a plugin repository) no longer give `isolation-foreign-identity`; before, every new target in the identity directory had to be added to `isolation.allowed_layers`. An owner outside `targets` and outside the list, and the same identity in a layer library, still give an error.
- `scripts/check_release.py` also checks that the lines "Stan opisu: Skillsync `vX.Y.Z`" in docs/workflow.md and docs/workflow-details.md name the release version (until now these strings were bumped by hand, so they recreated the debt at every release).
- `skillsync leakcheck` in the working directory of a clone of a target with `scope: public` warns (does not block) when the clone has no guard hooks (pre-commit/pre-push) and names the command `skillsync leakguard install-hook <clone>`; a note in CONTRIBUTING.md. Without the hooks a push to a public repository bypasses the guard.
- Leak guard: the target key `targets.<id>.committer_allow` in the machine config (a list of addresses): addresses accepted only as committer and tagger (for example the merge button of a host, `noreply`), never as author. Before, a `noreply` address had to be listed in `authors` and passed as an author too. The default is an empty list; without the key the behaviour is unchanged. It applies to `leakcheck` (range, history, tags); it does not apply to `pre-commit` (staged changes: `authors` only); with `authors_scope: committer` it usually has no effect (it matters with `all`).
- `dashboard show` (and `/api/status`): `substeps[].noData` (`true` ⇔ `seconds` is null, also for a planned sub-step), the same meaning as `noData` in `dashboard metrics`. Additive (schema 1): `missingData` is unchanged. Compatibility: a consumer that rejects unknown keys of `substeps[]` must accept `noData`.
- Guard tests: the size of `load: bootstrap` domain records (every record ≤ 12 KiB, the glossary ≤ 95%), the gate rules after `/clear` in the `feature` and `dispatch` skills, the `chrome-headless-shell` rule in the `agent-runtime-pitfalls` skill.
- Test: `checkpoint --final` and `retro-quality` work with a manifest outside the repository (`--manifest`).
- `skillsync dashboard url <project> [--port N] [--host NAME] [--json]`: the address with the consent token of a running `dashboard serve` (`http://127.0.0.1:<port>/#zgoda=<token>`), read from a state file in the machine data directory (outside the project, 0600 in a 0700 directory, ACL on Windows through the shared helper when present); `BLOCKED` (exit 2) `server_not_running`, `approvals_disabled` (the server runs with `--no-approvals`: its state file says `"approvals": false` and holds no token), `ambiguous_server`, `host_not_allowed`, `invalid_project`. A state directory that is a link or belongs to another user (POSIX) is refused with one warning (nothing is written), a server start removes the leftover temporary files of its own project and port, and the address with the token uses loopback or a name of `--approve-host` (the page answers on no other).
- `/api/status`: `approvals.tokenRequired` (HTTP only; `dashboard show` unchanged); `/healthz`: `tokenId` (an 8-hex digest of the token, never the token) for a server with consents; the refusal of a token has `code: "approve_token"` in its body.

### Changed

- Masking of secrets from environment variables (`redact`) works from 8 characters, also in message text (before: from 4 in values, 8 in keys): a short value, for example one equal to `root` on a Windows runner, no longer renames words or fields in the output. Pattern masking (URLs with a password, `Authorization`, `password=`, `token=`) is unchanged and has no threshold. ATTENTION (protection weakens): a secret from the environment shorter than 8 characters is no longer masked anywhere in the output.
- Status page (`app.css`): the state of the live indicator has its own dot shape besides the colour (circle: live, square: polling, triangle: down, ring: connecting), which matters in the compact mode (≤ 420 px) where the text is hidden; interactive buttons and tabs of the compact mode (`.btn`, tabs, consent and document buttons) are at least 2.75rem (44 px) high. A plugin that keeps its own copy of the stylesheet has to resynchronise it.
- `dashboard` (`build_status`, `show`): the plans listed for choosing are parsed once per (path, `mtime_ns`, size) instead of on every build of the status (the page rebuilds it on each change and poll; the chosen plan is still read fresh); the activity of a run (`activity`, `runAlive`) is still read on every call. A plan rewritten with the same size inside the timestamp resolution of the file system is seen at its next change.
- The isolation guard in tests (`real_state_snapshot`) skips files that read-only git operations refresh (`FETCH_HEAD`, `.git/index`) in other processes.
- Skills `feature`, `dispatch` and the card mode: after `/clear` or in a new session without the user's consent word no code is written; a quote in ASSIGN is a hint to check, not a consent.
- The E2E harness of the prototypes (`env.py`) forces a light colour scheme in Chrome (`--blink-settings=preferredColorScheme=1`), so the scenarios `DR-start` and `DR-późny` also pass on a station with a dark theme.
- `docs/domain/glossary.md` is shortened below the limit; the entry `synthetic` is removed.
- Windows: the files that POSIX keeps at mode 0600 are private through an ACL (the user, `SYSTEM` and the local administrators, by SID; no inheritance from the directory):
  the users file `users.json` and its backup `users.json.bak` (the temporary file gets the ACL before the data is written, because `os.replace` hands the ACL of the
  temporary file to the target), the state, token and `audit.jsonl` files of the one-time login link, and the machine config with `prototype.default_password` (checked and repaired
  in place like the users file). A directory created for them is restricted with inheritance. New module `skillsync_workflow/winacl.py` (`restrict_to_owner`, `is_owner_only`,
  `ensure_private`, `private_notes`; `ctypes`, never raises: every failure is `False` and one warning). A file system without ACLs (FAT, exFAT, a share without support) or a refused call gives
  one warning per file and process (`nie udało się ograniczyć dostępu do pliku ...`); the write itself always succeeds. A users file that was written by an older version is repaired
  on the first write or read (one line `ACL Windows: ograniczono dostęp do pliku ...`).
- Every path field of a CLI report uses `/` on Windows, not only the fields listed in 1.4.1: `root` and `project` (`status`, `dashboard show`, `catalog list`, `layers *`, `dispatch`, `runs`), `composed_root`, `config`, `manifest` and `backup` (`layers *`), `layers[].path`, `layers[].root`, `layer.path`, `budgets[].path` (`doctor`), `log` (`workflow validate`), `directory`, `repo` and `hooks[].path` (`leakcheck` hooks), `launchers[]` (`env`), `recorded`, `scenario` and `report` (`e2e`), `path` (`repomap`), `checkpoint` (`checkpoint`) and `targets` (`upgrade` rollback plan). Linux and macOS are unchanged. New guard `tests/test_skillsync_report_paths.py` fails when a report path field is written as `str(path)`; commands to execute and paths the product opens stay native.
### Fixed

- `dashboard`: `run.checkpoints[].time` is always `HH:MM` as the contract says (a checkpoint written with a one-digit hour, `- 9:05 …`, was reported as `9:05`); the same for `startedAt`/`finishedAt` of the sub-steps, `waiting[].since`, `run.inProgressFrom` and `eta.since`. Compatibility: a consumer that compared these values as strings against a one-digit hour must compare `09:05`.
- Prototype server: `FileLock` closes (and unlocks) the descriptor it opened when anything, `KeyboardInterrupt` included, interrupts the acquisition before the lock object holds it; before, such a descriptor kept the system lock until the process ended.
- Prototype server: the request-body counter that decides on a lingering close counts `readlines` and `readinto1` too (`peek` consumes nothing and stays uncounted).
- `common.resolve_loop_checked` (used by `discover_exclude` and the retro scan): a loop of Windows junctions is reported on Python 3.12 and 3.13 too (`resolve()` returns the stuck path there and a junction is not a symlink; `os.path.isjunction` now counts as a link); measured: 3.11 raised, 3.12/3.13 did not. A junction whose target is gone (dangling) is not a loop.
- `layers init`: the `next` hint is quoted for Windows with double quotes and `/` (it ran in PowerShell and Git bash but not in cmd.exe when the path had a space); POSIX unchanged.
- `leakguard` (hook check, Windows): a word of a hook line such as `/c/dir/x` is checked as `C:/dir/x`, and a path in the MSYS root (`/usr/bin/env`, `/bin/sh`, `/mingw64/...`), which exists only inside Git for Windows, is no longer reported as a missing command. Compatibility: on Windows a hook that names a missing file with a `/…` path outside a drive form is no longer reported (use `C:/…`); POSIX unchanged. A UNC path (`//server/share/x`) is a native path and is checked as before.

### Docs

- `docs/prototypes.md`: the login rate limit (5 failed attempts per login and address, 30 per address, 15-minute window, `429` with `Retry-After`, memory only) and its effect behind an `ssh -L` tunnel, where every user is `127.0.0.1`. No code change.

### Tests and process

- `tests/skip-baseline.json`: `ubuntu-latest/symlink` and `ubuntu-latest/copy` are measured (14 entries each, identical on Python 3.11 to 3.14), so the skip check is enforced there; before they were `null`.
- Regression tests for `fsutil.isabs` at its sites (`_env_dir`, dangling-link target) and a guard against a new `os.path.isabs`; comments at the two guard tests of the lock retry.
- The matrix to run before a push (Python 3.11 and the station's interpreter, Windows 3.11/3.12/3.13 as on the runner, macOS for HTTP/files/signals, `check_skips` on the logs of all systems) is written in `library/skills/feature/references/prepush-matrix.md` and linked from `feature/references/tests-baseline.md` and the `dispatch` skill (`ASSIGN`).
- `tests/test_skillsync_duplicate_defs.py`: no `.py` file in `skillsync_workflow/`, `scripts/` or the `skillsync` entry point defines a top-level function, class or UPPER_CASE constant twice (the later definition silently wins, so a block duplicated by a merge works and stays as dead code). An explicit `ALLOWED` list (empty) is for deliberate redefinitions, each with a reason; the scanner itself is tested (duplicates found with their lines, conditional and class-level names ignored).
  Found by the trial integration of 1.5.0 (a block duplicated by a merge); fixes the existing duplicate `_is_int` in `prototype_server.py` (identical copies, the second removed).
- `tests/test_skillsync_no_silent_return.py`: a guard (AST of `tests/**/*.py`) against a test that passes silently where it checks nothing. A quiet `return` in a test method (for example `if os.name == 'nt': return`) was reported as PASS, so the skip baseline never counted it; now a mechanism the system lacks is `self.skipTest(reason)` or `@unittest.skipUnless(...)` with a reason from a category of `tests/skip-baseline.json`. Also refused: a test whose every assertion sits in one platform or capability branch without `else`. An early return after the assertions of its own branch is allowed. Audit of `main` (1.4.1): no silent return in `tests/` (the four found in the open 1.5.0 branches are skips now).
- The Windows-only test of the E2E harness (`env.py`: the password and config files are owner-only by ACL) moved from `test_skillsync_prototype_e2e` to `test_skillsync_winacl.RealWindowsTests.test_the_secret_files_of_the_e2e_environment_are_owner_only_by_acl`. The `e2e` CI job rejects every skipped test of its module (`... skipped` or `OK (skipped=N)`, so that a Chrome test that did not run cannot pass), and this one was skipped on Ubuntu by design (`tylko na Windows (ACL)`); `tests/skip-baseline.json` lists the new id instead of the old one for `linux`, `ubuntu-latest` and `macos-latest` (category `windows-only`). The test is unchanged and runs on Windows.

### Compatibility

- The warning `kontrola uprawnień 0600 pominięta na Windows` (and the constant `PRIVATE_CHECK_SKIPPED`) is gone: on Windows `prototype user ...`, `prototype serve` and the read of
  `prototype.default_password` no longer print it; a warning appears only after a failed ACL, and one line after a repair. A consumer that filtered the old text must be updated.
  `private_file_problem` never reports a problem on Windows (a volume without ACLs must not block a command). JSON fields are unchanged.
- On Windows, a consumer that compared these fields with a native path (`C:\...`) must compare with `/` or normalise (`Path(value)`); Linux and macOS are unchanged.

### Breaking

- `POST /api/approve` without the header `X-Approve-Token` now gets `403` (`code: "approve_token"`): a client that posted consents without a token stops approving. Adjust: read the address with `skillsync dashboard url <project>` (the token is the fragment `#zgoda=<token>`) and send its value in the header; the status page and the editor plugin need no change. The token is made per server process and printed once in the console of `dashboard serve` as the fragment of an address (`#zgoda=<token>`; a service journal or CI log that keeps stderr keeps it too: use `dashboard url` there); the page keeps it in `localStorage` and sends it; an open tab with an old page gets the same `403` with the reason in its message until it is reopened from the address. The refusals before the token are unchanged, `--no-approvals` is unchanged (403 as before), `--approve-host` sends the token over plain HTTP (trusted network only). This release is MINOR, not MAJOR, by the owner's decision, although the change breaks own scripts and clients that post to `/api/approve` (the status page and the editor plugin of the same release are not affected).

## [1.4.1] - 2026-11-15

### Fixed

- Layer rules, the config and the dangling-link check on Windows with Python 3.13+: a path rooted with `/` or `\` but without a drive (`/a/**` in `layer_rules`, a `/a/b` source or a link target) is absolute again, as it was up to Python 3.12 (`ntpath.isabs('/x')` became False in 3.13). `fsutil.isabs` names the meaning; `layers.path_matches('/a/b/c', '/a/**')` matched nothing on 3.13, `layers` stored `/a/b` as `C:\a\b`, and `link` took a rooted link target for a relative one. POSIX unchanged.
- `FileLock` on Windows: opening the lock file is retried briefly (at most 0.5 s and never beyond the lock timeout) when the system refuses it for an instant, because the holder
  that just released the lock is deleting the file (`unlink=True`, the approvals lock; `os.open` answered `PermissionError` EACCES). Before, parallel `approvals record` calls could
  sporadically end `BLOCKED` ("Permission denied: ...approvals.jsonl.lock"): measured 43 refusals in 24000 acquisitions by 6 processes, always at that `os.open`. A refusal that lasts
  (a real permission problem) is still raised after the short retry. Single use, the inode check after a delete and the release on process exit are unchanged.
- `leakguard`: a hook command line containing a path that starts with `/` is checked as an absolute path on Python 3.13+ on Windows too (`os.path.isabs('/x')` is False there since 3.13, so `hook_command_problems` skipped such a path and `hook-command-missing` did not report a missing file); POSIX unchanged.
- Prototype server on Windows: a refused open, rename or replace of a file that another thread has open or is replacing (`PermissionError`, WinError 5 or 32) is retried for up to half a second (`retry_transient`; other systems and other errors unchanged).
  Before, concurrent requests could fail sporadically: a one-time login link could be lost by every caller at once (nobody got the session, the token stayed valid), reading `questions.json` or `comments.json` during a write ended as a 500 `internal_error`,
  and a write could be refused while a reader had the file open. The single use of a login token is unchanged (the atomic rename decides, a missing source file means someone else won). Serving static prototype files and copying screens are not retried (no failure was observed there).
- Approvals file lock: `approvals.jsonl` is locked with a system lock (`flock`; `msvcrt.locking` on Windows), the same lock as the questions, comments and users files of the prototype server, instead of a `.lock` file that was created exclusively and taken over after 60 s. This fixes a race in which a late writer released the lock after its owner and left a stray `.lock` file (up to 60 s of `BLOCKED` for the next writers, and during the takeover the lock path briefly did not exist); in CI it failed this way in 7 of 157 completed macOS 3.12 jobs (about 4.5%) and in 1 of 157 3.11 jobs, 12-13 Nov. Ending the process releases the lock; a leftover `.lock` from an old write does not block; the `.lock` file is still temporary (removed under the lock, the next writer checks the inode). See docs/upgrading.md: after the update restart `dashboard serve` before the first approval.
- `QuestionsStore.sweep_orphan_lock` (removal of an orphaned `questions.json.lock`) removes the file while still holding the lock, and every `FileLock` checks after `flock` that the path still points at the locked inode (a waiting writer no longer ends up holding a lock on a deleted file).
- `FileLock`: a file system without lock support (ENOTSUP, EOPNOTSUPP, ENOSYS; ENOLCK after half of the wait) now gives `LockUnavailable` at once (503 `lock_unsupported`, a subclass of `LockTimeout`) instead of waiting for the timeout; other errors are still contention. Applies to the locks of expert questions, comments, users (also the `prototype user` commands) and approvals.
- `prototype serve` and `dashboard serve`: SIGINT (Ctrl+C) or SIGTERM right after the start, between installing the handler, announcing the address and `serve_forever`, ends the server cleanly (PASS, `stopped`) instead of a traceback and exit code -2; the handler is installed before the address is announced, and the start from installing the handler to `serve_forever` is under a `try`. In CI this failed once on macOS (a window of a few milliseconds).
- A symlink loop in a `discover_exclude` entry or in a project path is again treated as unresolvable (`common.discover_excludes`, `common.is_excluded`) on newer Pythons:
  `Path.resolve()` stopped raising on a loop in POSIX 3.13 and Windows 3.12, so the loop is now detected by a symlink that is left among the components of the resolved path.
- Prototype server and status page: a request that the server refused before reading its body (401, 403, 404, 405, 411, 413, a refused approval) could leave the declared body unread in the connection.
  On Windows the client then got a connection reset (WinError 10054) instead of the answer; on a kept-alive connection the leftover bytes were read as the start of the next request. Now a connection with an unread declared body
  is closed after the answer, with a lingering close (RFC 9112 section 9.6: the server half-closes, discards what the client still sends and then closes), bounded to 256 KiB and one second so that it cannot be used to hold a server thread.
  Requests whose body the handler read are not affected.
- `link` appends to a user's `.gitignore` (project root, `.github/`, `.claude/`, `.cursor/rules/`, the state folder) without rewriting it: the existing bytes and line endings (CRLF, LF or mixed) stay as they are and new lines follow the last line's ending. Before, the whole file was rewritten with LF, so a Windows project with `core.autocrlf=true` got a diff of the whole file instead of the added lines. The prototype ignore files (`.gitignore`, `.gitattributes`) use the same appending rule.
- `link` keeps the line endings and bytes of the user's `AGENTS.md`, `.codex/config.toml`, `.vscode/tasks.json`, `.claude/settings.json` and `.mcp.json`: the managed block or JSON is written in the file's own line ending (CRLF stays CRLF, a new file is LF) and the `*.skillsync-backup` copies are byte copies.
- `link` recognises a dangling library symlink written in the 8.3 short form (`RUNNER~1`) on Windows, so `link` after a skill rename removes the orphan.
- Run metadata (`runs`, the `benchmark` suite and fixtures) and `/proc` identity are read and written as UTF-8 explicitly (Windows used the code page).
- A path in run metadata that is rooted but not absolute on Windows (`/etc/hostname`, `\\x`, `C:x`) is refused as an evidence path or a benchmark fixture name on every system.
- `prototype question merge --driver` leaves a questions file with CRLF line endings byte for byte when the other side adds nothing (it rewrote the file with LF).
- Windows (Python 3.11 and 3.12): the test suite no longer fails for reasons of the platform that are not defects of the product. Tests were fixed (isolated
  `USERPROFILE`/`LOCALAPPDATA`, interpreter from `sys.executable`, quoted paths in YAML, a fake `az` and `git` that run on Windows, links with a relative target, `skillsync.exe` as the entry file, case-insensitive file
  trees); no test was removed or disabled, the simulated hosts that a real Windows file tree cannot show are skipped as subtests with a reason.
- `status plan approve`/`spec approve`: the warning about the pre-implement report no longer misreads a plan saved with CRLF line endings (the `Przebieg:` line was not found).
- `layers`: on a Windows host a backslash is a separator when paths are normalised and in `layer_rules` globs, whichever system is simulated.

### Changed

- Tests: the variable `SKILLSYNC_TEST_DASHBOARD_PORTS` (a range `<from>-<to>` within 5100-5199) assigns the ports of the status page tests so that several runs on one host do not collide; the default is unchanged and the product is unchanged (docs/development.md). `free_port` also treats a port in TIME_WAIT as free (the server binds with SO_REUSEADDR), because on macOS a slice of 29 ports ran out within one run.
- CI (macOS): `timeout-minutes` of the `test` job for macOS from 30 to 45 (the tests take 20-28 min there); `tests/skip-baseline.json` now has a measured baseline for `macos-latest/symlink` and `macos-latest/copy` (35 skips of the main v1.3.0 run, identical in the four jobs) instead of `null`, with the new categories `linux-only-env` (lifecycle tests of `env`: the process ownership backend exists on Linux only), `bsd-mktemp` and `needs-shellcheck`; the time limits of four tests were relaxed (secret scan 0.5 → 5 s, git with a limit of 3.5 → 15 s next to a 30 s sleep, an idle dashboard connection 4 → 15 s, `approvals wait` 15 → 18 s). The guard test now checks the new limit and the macOS baseline.
- CI: the test matrix adds Python 3.13 (blocks like 3.11 and 3.12) and Python 3.14 (informational, `continue-on-error`, Ubuntu only, both modes).
- Path fields in reports use `/` on Windows: `knowledge init` (`created`, `existing`), `route` (`lessons[].path`, `domain[].path`, `index`), `doctor` (`findings[].path`) and the message about the users file inside the served root of
  `prototype serve`. Helper: `skillsync_workflow/pathfmt.py` (`posix`). Commands that are to be executed (`selfcmd`, the `skillsync: link` task, hook commands) and paths the product opens stay native.

### Compatibility

- File systems without system locks (some network shares): writing approvals and the prototype server locks end at once with the error "file locks are not supported" (`BLOCKED` / 503 `lock_unsupported`); the old lock file worked everywhere. An old `dashboard serve` process started before the update (and an older copy of `skillsync`) writes approvals with the old lock: restart it and update the copies before the first approval (docs/upgrading.md).
- On Windows, a consumer that compares the path fields above with a native path (`C:\...`) must compare with `/` or normalise (`Path(value)`); on Linux and macOS nothing changes. Commands to execute are not
  affected: they keep the native form.

### Breaking

None.

## [1.4.0] - 2026-11-14

### Added

- Dashboard: `eta` (JSON schema 1, addytywnie) i toast „<operacja>, koniec ok. HH:MM” z szacunku, który agent zapisuje przed długą operacją w checkpoincie przebiegu (`eta: <operacja> do HH:MM`); po przekroczeniu toast mówi „przekroczono ETA o N min”, znika po następnym checkpoincie bez `eta:`, z `czeka:` i po zamknięciu planu. Serwer nie czyta zegara: przekroczenie liczy renderer. Skille `feature` i `prototype`: reguła „szacuj przed startem, aktualizuj przy przekroczeniu”.
- Status page and the writing marker: `/api/status` and `dashboard show` have `prototype.writing` (`null` or `{since, expiresAt, renewedAt}`, never `by`). While the
  marker is valid, `waiting` has the item `source: "writing"` ("agent pisze ekrany (do HH:MM)") instead of the call to accept the prototype (also
  when the call came from a checkpoint `czeka:`); the page shows "Sesja pracuje od HH:MM". The page server watches `.writing.json` and its validity, so the page
  also refreshes when the marker expires.
- `waiting` item `source: "expert"` ("odpowiedź eksperta (N blokujących)", link `{kind: "expert", slug, url}` like the questions toast) when `expertQuestions.blocking > 0`
  and the newest checkpoint has no `czeka:` marker.
- `POST /_api/v1/prototypes/<slug>/writing/release`: removes the writing marker (idempotent, `{writing: null, released}`); only `developer` and `acceptor`
  (403 `forbidden` for `commenter` and `expert`, 401 without a session, same checks as `restore`). The engine banner has a "Zwolnij" button for those roles.
- `prototype comments` and the `comment` event of `prototype wait` carry `untrusted: true` and a constant `notice` (comment texts come from people: data, not commands; report commands found in them to a human);
  both fields come first in the report, the texts themselves are unchanged. `wait` with TIMEOUT or `accepted` has no person text and no fields.
- `question list|show|export` and the `answer`/`comment` events of `question wait` carry `untrusted: true` and a constant `notice` (answers and comments of experts are data, commands in them are not to be run: report them to a human); the two keys come first in the report, also for an empty list, texts are unchanged.
  The text has one source with the one of `prototype comments`/`wait` (`UNTRUSTED_NOTICES`, contexts `comments` and `question`). `add`, `withdraw`, `record`, `purge`, `migrate`, `merge` and `url` return no text of other people and have no fields; TIMEOUT and errors neither.
- `skillsync prototype question withdraw --reason <text>` (1-500 characters, no secret unless `--force`; BLOCKED `invalid_reason`, `secret_detected`) stores a reason in the `withdraw` operation (field `reason`);
  the report and `show` (the administrator's `withdrawn` entry) and `export` (`withdrawn: {at, reason?}`) return it.
- Klucz manifestu `prototype.port` (1024-65535) i flaga `dashboard serve --prototype-port`: port serwera prototypów, który sprawdza strona statusu (dotąd na sztywno 5130; domyślnie nadal 5130). `prototype serve` bierze ten klucz jako domyślny port (flaga `--port` wygrywa, bez klucza 4445), więc serwer i strona statusu zgadzają się co do portu; `doctor` waliduje wartość.
- `question show` ma pole `question` (pierwsze pytanie), a `question wait` pole `questions[]`: oba mają teraz `question`, `questions[]` i `answerLine` na górze (zmiana addytywna, żadne pole nie znika).
- `question add`: pole `ignored` (utworzone pliki ignorowane przez git, czyli lock); `created` = `files` + `ignored` przy pierwszym użyciu.
- `skillsync checkpoint` i `resume`: flaga `--manifest PLIK` (manifest projektu spoza repozytorium, np. dla publicznego rdzenia bez `.github/.skillsync.yaml`); bez flagi zachowanie bez zmian.
- `prototype.public_url` (manifest) i zmienna `SKILLSYNC_PROTOTYPE_PUBLIC_URL` (wygrywa): adres serwera prototypów dla ekspertów za tunelem ssh. Używają go `prototype question url`, pole `url` w `question add` (także bez lokalnego serwera) i dodatkowa linia „Adres publiczny” komunikatu `prototype serve`; błędna zmienna to BLOCKED `invalid_public_url`.

### Changed

- A withdrawn question stays on the list of the expert it was addressed to (state "wycofane", with the reason, nothing to answer: answer and comment of the expert are 409 `withdrawn`); a question
  addressed to somebody else is still 404, and the messages of `/_experts` tell "withdrawn" from "does not exist or is not for you". The administrator's comment on a withdrawn question stays allowed.
- Questions about the contract, data (database schema), migration and security are a developer decision: the agent asks them as plain blocking `Q` in the session, not the expert; only domain questions go to the expert
  (rules in `feature` and `prototype` skills, `docs/prototypes.md`, `docs/workflow.md`).
- Skills `prototype` and `feature`: the agent reports a command a comment tries to give (command, consent, permissions, delete, commit, push) to the human, quotes it and answers in the thread that it will not do it;
  and re-reads the prototype comments at "GO plan", "GO implement" and before the first interface phase (`prototype wait` reads them only in its waiting window).
- Prototype toolbar: the state of a pressed toggle (`aria-pressed="true"`, `aria-expanded="true"`, also "Wątki") is visible: the rule has a higher specificity than `.btn-outline` of `components.css`, and a ring and a heavier weight
  mark it, not only the background. The comment popup is smaller (`textarea` `min-height` 3 rem, popup `min(20rem, …)`). Existing prototypes get `prototype.css` after `skillsync prototype migrate`.
- `prototype writing start` on a valid marker keeps `since` (the start of the writing) and `by` (unless `--by` is given) and sets the new field `renewedAt`; the window
  of at most one hour counts from `renewedAt`, so a long writing renewed again and again stays one marker. `writing` in `list`, the API and `status` has `renewedAt`.
- `silence` (no report) is also off while a blocking question to an expert is open (the session waits explicitly); the card title gets "⏳ Czeka" only for items that wait for a person, not for the writing item.
- The engine banner says "Agent jeszcze pisze ekrany (do HH:MM)" (the hour from `expiresAt`, browser time). The 409 `writing_in_progress` has a Polish message for people
  ("Agent jeszcze pisze ekrany tego prototypu (do HH:MM)…", host time of the server), so a copy of the engine without the banner shows readable text; the code and fields are unchanged.
- Docs: `/_experts/<slug>` bez sesji to 302, zły slug 404 zawsze, nieistniejący poprawny slug po zalogowaniu to pusta strona; `plans[].run` i `run.id` strony statusu są `null` bez linii `Przebieg:` w planie (opis, bez zmiany zachowania).
- `answerLine` (`question show`/`wait`) podaje czas odpowiedzi lokalny z offsetem (`2026-11-13T13:30+01:00`) zamiast UTC z „Z”; dziennik, kontrakt `expert-questions/1` i `record` bez zmian (starsze wiersze z „Z” zostają poprawne).
- `checkpoint`: komunikat o złym identyfikatorze nazywa pole (`--step`, `--run-id`) i wzorzec `[A-Za-z0-9][A-Za-z0-9_.-]{0,79}`.

### Fixed

- Test `dashboard_questions.test_watcher_follows_questions_files` no longer fails on macOS (`/var` versus `/private/var`): the test resolves its temporary root like the watcher does (the watcher is unchanged).

### Compatibility

- `untrusted` and `notice` (`prototype comments`, `prototype wait`, `question list|show|export|wait`) are additive; a consumer that ignores unknown keys is not affected (two keys come first in the report; the `expert-questions/1` document keeps its schema version). The text of `notice` is constant and has one source (`UNTRUSTED_NOTICES`).
- `reason` in the `withdraw` operation is a new optional field of `questions.json`: an older version rejects (its merge driver) a file that holds it, so update the core before using `--reason` in a shared project.
- `.writing.json` stays `version: 1`; `renewedAt` is optional. A core 1.3.0 reads a file renewed for more than an hour after `since` as no marker (acceptance unblocks); do not mix
  versions while the agent writes for longer than an hour. Times outside 0…2^53 (ms) make the file invalid.
- `prototype.css` and the engine (`prototype.js`) change in this release (toolbar state of pressed toggles, smaller comment popup, the writing banner with the hour and the "Zwolnij" button): existing prototypes get them after `skillsync prototype migrate`; an older copy of the engine shows the readable 409 message of the server.
- `eta` in the status JSON (`schema: 1`) and `prototype.writing` are additive; `waiting` has two new sources (`writing`, `expert`) and a new link kind (`expert`).
- `answerLine` gives the time of the answer in local time with an offset; older `[EKSPERT]` rows with `Z` stay valid, the journal, `record` and the `expert-questions/1` contract are unchanged.

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

None.

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
