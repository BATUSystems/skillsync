# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), wersje: [SemVer](https://semver.org/lang/pl/).

## [Unreleased]

### Breaking

None.

### Added

- **Skill `ui-verify` (`a11y`): contrast of the focus indicator (CORE-1150-T2B, part A).** Two opt-in rules, off by default: `focus-contrast` (WCAG 1.4.11, `focus.contrast`) and `focus-appearance` (WCAG 2.4.13, level AAA, `focus.appearance`), each `off`, `report` (INFO, no change to the exit code) or `fail`. They measure the pixels of the focus indicator from the screenshots taken for the keyboard rule: with focus and after the element lost it, so any other change within 8 px counts as indicator (a false PASS is possible). `focus-appearance` of an element with a side of 1 px or less, or with an effective threshold of 0, is UNVERIFIED `shape-unknown`. Configurations without the new keys are unchanged (except the digest on Windows of a configuration with `exceptionsFile`, see Fixed). Limits and reasons: `references/a11y.md` of the skill. With either rule on, the report carries the warning `focus-indicator-after-blur` that names this limit.
- **Skill `ui-verify` (`a11y`): contrast of non-text elements (CORE-1150-T2B, part B).** New opt-in check `non-text` (WCAG 1.4.11, `basis: wcag`), outside the default `checks`. It judges form controls (border band or fill against a 2 px ring), icons that are the only content of a button or link, and state indicators (`aria-selected`, `aria-pressed`, `aria-checked`, `aria-current`, `aria-expanded`) in their current state, without switching states. A component passes when its border or fill reaches 3:1; `thresholds.nonText` sets the threshold (lowering it below 3:1 needs a `reason`). Pixels the method cannot judge are UNVERIFIED (`pseudo-element-control`, `raster-graphic`, `complex-graphic`, `shape-unknown`, or the reasons of the text check); a page without candidates is UNVERIFIED `no-samples`. There is no `report` mode. Exceptions can name the element kind with `kind`; keys of such findings end in `|boundary`, `|icon` or `|state`. A rounded control with a border that does not pass the rectangular measurement is UNVERIFIED `shape-unknown` instead of FAIL. Limits, reasons and fixtures: `references/a11y.md` of the skill.

### Fixed

- **Skill `ui-verify` (`a11y`): the same configuration digest on Windows (CORE-1200-DIGEST).** With `exceptionsFile` set, the digest in the report differed on Windows from Linux and macOS. The path separators are now normalised before hashing; such a digest changes once on Windows, elsewhere nothing changes.

## [1.19.0] - 2026-11-26

### Breaking

None.

### Added

- **Skill `test-env`, tool `db-snapshot`:** the subcommand `verify` (`db_snapshot.py verify --file FILE.bak [--database NAME] [--dir D] [--offline] [--allow-missing-sidecar] [--dry-run] [--json]`) checks an old copy without restoring it and changes nothing; its own exit codes (1 means a damaged or non-matching copy); the section "verify" in `references/db-snapshot.md`, the `usage` and `limits` in `tools/index.yaml`.

### Fixed

- **Skill `prototype` (`scripts/css_tokens.py`):** `read_required` (the list of required tokens read by `check_tokens.py` and `theme_check.py`) splits lines at CRLF, LF and a lone CR only, so an entry with a rare separator (VT, FF, NEL, LS, PS) is no longer cut into two required tokens; the same rule as in the generator.
- **Scripts `db_snapshot.py` (test-env), `guard-commands.py` and `check-tests.py` (hooks) and `check_tokens.py` (prototype): output in UTF-8 on Windows.** Streams are reconfigured to `encoding='utf-8', errors='replace'`, so a redirected stdout or stderr no longer garbles Polish characters (cp1250, cp852).
- **Hook `guard-commands.py`: refuses a `git push` after a `gitleaks`/`leakcheck` scan joined by `|`, `;`, `&` or a newline** (the scan result would not gate the push). `&&` or a separate command whose result was read passes; `SKILLSYNC_GUARD_EXCEPT='<reason>'` before the push is the exception.

## [1.18.0] - 2026-11-26

### Breaking

None.

### Added

- **Skill `dashboard`:** a section "Worktree tabs and History" (adding a tab with `dashboard serve`, the address as the last stdout line, which tabs are visible, approvals per tab, the standalone server, closing the terminal of the server, the tab "History").

### Changed

- **Status page assets (`dashboard`):** a bar of worktree tabs (each directory of the project is a tab, with a waiting marker, a read-only mark for a tab opened without its token and "show all" for hidden ones), the view "History" (finished plans of all tabs, archiving and restoring with a confirmation), and the finished plans no longer listed in the plan menu of a tab on a server with several tabs. The counter of "History" comes from `/api/history`, read in the background only while the browser tab is visible and at most every 60 s. New text keys `ui.wt.*` and `ui.history.*` (PL/EN) in the catalogues of the status page.
- **Skills `dashboard`, `dispatch` (`references/preview.md`) and `skillsync`:** a card working in a worktree of the project of a running project server does not allocate ports by hand: `dashboard serve` in its worktree adds the tab and prints the address with the token of its own tab (`--all-tabs` is for a person); a preview with a foreign layer stays a separate standalone server. The rows `dashboard serve` and `dashboard url` of the skill `skillsync` list the new flags, the tab address and `unknown_tab`.

## [1.17.0] - 2026-11-25

### Breaking

None.

### Added

- **Skill `feature-explainer` (profile `ui`):** one HTML page that explains how a screen or feature works to a person outside the development team (QA, business, support): rules confirmed in code with `file:line` notes, examples on screenshots from sample data, reader questions in an FAQ. Starts only on request or with consent; a static file in the repo by default, a private page in the host optional. Page template, references, `scripts/check_page.py` and the English mirror in `layers/core-en`.
- **Hook `guard-tests`:** a rule for `vstest.console` (`/Tests:` and `/TestCaseFilter:` are a point run, the informational options are exempt).
- **Status page, header:** a chip shows the delivery mode (`delivery.mode`, `base`, `source`; "mode: pr", "mode: direct, base: develop", the tooltip names the source), after the branch block. An invalid mode (`mode: invalid` of the status, or `source: invalid`) is a warning chip "delivery mode: error" instead of a silent `pr`. No change to the status JSON, the server or the CLI (the field `delivery` already carried `mode`, `base` and `source`); new text keys `ui.delivery.*`.

### Changed

- **Skill `workflow`, `test-run` reference:** the runner families table with `vstest.console`, the note on a runner outside the default places and the TRX locations.
- **Skills `feature` (`ticket-intake`, step 5) and `bugfix` (step 7):** a ticket of the bug type also covers a sub-task of that type (e.g. `BUG Sub-task`) and goes through the `bugfix` procedure; the description of when `verifier` is used now matches the core: in a one-round loop a fresh `verifier` checks the closing of the majors (review-loop transition table), outside the loop it serves single claims (the `bugfix` text said it was only for single claims outside the loop).
- **Status page, tab "Questions":** the tab now has two groups, "Questions for implementation" (the blocking questions and the assumptions of the spec of the visible run, from `blockingQuestions` and `assumptions`; a "session is waiting" mark when a checkpoint `czeka:` names the question, "with the expert" for a question handed to the expert) and "Domain expert questions" (the earlier content). The tab is also visible when only the first group has content, and the counter on its card is the sum of the open questions of both groups (a question handed to the expert counts once; the tooltip shows the split). The status JSON, the server and the CLI are unchanged; new text keys `ui.q.groupRealize`, `ui.q.groupExpert`, `ui.q.noRealize`, `ui.q.assumption`, `ui.q.accepted`, `ui.q.sessionWaits`, `ui.q.withExpert`, `ui.q.more`, `ui.q.countTip`, changed `ui.tab.questionsTip`.
- **Skill `feature`:** a new point after "Cutting scope" on widening the scope during a run (new steps with criteria, tests and risk, the decision and its date, the version and the changelog asked again, a separate plan for a different risk layer; a decision relayed by another session is confirmed with the user first).

### Fixed

- **Stop hook `check-closure.py` and the `skillsync doctor` closure finding:** a `done` plan whose run has no trace in the local `.skillsync/runs` (the store is outside git, so a plan merged from another machine or checkout has none) no longer blocks the turn and is no longer reported by `doctor` as "done without a `final` record"; this session cannot close another checkout's run. The hook says so in one line ("run led outside this checkout; no Result section in the plan") only when the plan has no non-empty `## Wynik` section, and never suggests `--final`. A run with a local trace and no `final` record is reported as before. `skillsync status` was not affected (checked in a fresh clone: no `needsHuman` for such a plan). Run `skillsync link` to refresh the hook copy in the projects.
- **Skill `ui-verify`, a11y:** `settle` waits for lazy images that did not start loading during the scroll pass (they are made eager and awaited by `load`/`error`) before the screenshot, so a lazy image under text is no longer missing from the contrast measurement.

## [1.16.1] - 2026-11-25

### Breaking

None.

### Changed

- **Skill `workflow`, `test-run` reference:** `--format lines` for check scripts without a counter; `--allow-no-count` is not evidence of a `[T]` step, which then closes with `checkpoint --no-tests <reason>`. English translation and `translations.lock.yaml` updated.
- **Skill `test-env`, `db-snapshot`:** usage and recipe with `--sha256`, `--allow-missing-sidecar`, `--confirm` at `--dry-run` and a general LocalDB example.
- **Skill `feature`, Claude adapter:** the term "podumysł" in the Polish text.
- **Skill `dashboard`:** the card "Sub-mind runs" shows only running runs, finished ones sit behind a "show finished (N)" switch, and the report of a finished run opens from the name of the run (no "report" button); help of the card (`descriptions.json`, `descriptions.en.json`) updated.

## [1.16.0] - 2026-11-25

### Breaking

None.

### Changed

- **Skill `skillsync`:** `skillsync --help` is complete and in English, every leaf command ends with an `Exit codes:` line from `EXIT_CLASS`, and a new command or flag needs help, an `EXIT_CLASS` entry and a description in `docs/cli.md` (guarded by a test); the rows of `resources` (memory and load sources on Windows and macOS, `host.cpuBusy`, `host.sources`) and `plan-fresh` (the codes `step-id-unparsed`, `checkpoint-id-skipped`, `checkpoint-id-unknown`) are updated.
- **Skill `feature`, reference `measurement.md`:** rounds are written as `(R1)` or `round 1`, abbreviations O/K/S only in brackets (`F1.2 (K2)`), and a step id is 1-3 capital letters, an optional `-` and digits (`FT1` or `FT-1`, not `FT.1`); `plan-fresh`, `doctor` and `readiness` warn about spellings the parser does not recognise.
- English translations and `translations.lock.yaml` updated.

## [1.15.1] - 2026-11-24

### Breaking

None.

### Changed

- **Skills `feature` and `workflow`: three rules from a retrospective of a team that uses the core.** The spec guidance (`feature/references/spec.md`) requires checking the types, format and availability of data a change consumes from an existing installation against real data; until checked, it is a blocking `Q` about data, not an `[ZAŁOŻENIE]`. The review loop (`feature/references/review-loop.md`) requires the author to verify cited evidence on disk before writing a resolution. The test-run reference (`workflow/references/test-run.md`) describes a runner outside the default report locations (`--junit <file|dir>` with a report written by this run, `--allow-no-count <reason>` only for an unknown count with exit code 0, `checkpoint --no-tests <reason>`) and forbids hand-made reports, event files and step records, which bypass the `[T]` gate. Polish and `core-en`, `translations.lock.yaml` refreshed.
- **Skill `feature`: the limit of sub-agents in `references/claude.md` is 5, in total, of which at most two write code at a time** (Polish file and its `core-en` translation, `translations.lock.yaml` refreshed). It matches `SKILL.md` and `skillsync resources`; the Codex adapter keeps its own limit of 3 threads.

## [1.15.0] - 2026-11-24

### Breaking

- **`ui-verify`: a screenshot above the PNG limits is refused.** A `capture` of a page with `fullPage` that makes a screenshot taller than 16384 px or larger than 50 Mpx (for example a very long page) now ends with exit code 2 (`ImageLimitError`); before, the file was accepted. Whether Chrome returns a full-page screenshot that tall at all is not established. Workaround: a narrower viewport, screenshots of fragments (a shorter page, a `selector` or a mask instead of the whole page), or no `fullPage`. `a11y` is not affected: it cuts a page into clips of at most 16000 px and reports a larger page as `page-too-tall` (UNVERIFIED). See Security for the other limits.

### Security

- **`ui-verify`: the PNG decoder refuses decompression bombs before decoding.** `decodePng` walks the chunk headers (no decoding) and refuses, with `ImageLimitError` and exit code 2: a buffer over 64 MiB, a first chunk that is not an IHDR of length 13, a second IHDR (`pngjs` takes the last one), a zero dimension, more than 16384 px per side or 50 M px, an interlaced (Adam7) PNG, which `pngjs` inflates without any bound, and an oversized (over 768 bytes), malformed or repeated PLTE chunk (`pngjs` adds an array per palette entry with no limit, so a file under 64 MiB could demand gigabytes of heap). `pixel-diff` and `diff` check that the path is a regular file within the size limit before reading it, so a huge file or a link to `/dev/zero` is never loaded, and more than 100000 chunks are refused too (`pngjs` keeps an object per IDAT and pays CPU per chunk). `pixel-diff`, `diff`, `measure`, `capture` and `a11y` all decode through it; `diff` reports it as a read error (exit 2, not UNVERIFIED). What the walk does not refuse reaches `pngjs` as it is: buffers without a PNG signature, truncated files and bytes after IEND. `pngjs` 7.0.0 (pinned) gets no data from them and fails before allocating; a test on the real `pngjs` (valid CRCs, so it reaches the property under test) keeps that assumption honest.
- **`ui-verify capture --allow-trace`: owner-only artifacts, a `.gitignore`, and an honest warning.** `trace.zip` and `storageState.json` are written with mode 0600 (POSIX; an existing file is `chmod`ed before the new content is written), `capture` creates `<outDir>/.gitignore` listing both files only when none exists (never in the configuration directory itself, never over an existing file or symlink), and the stderr warning now names what they can hold (form field values and passwords, headers and bodies, cookies and tokens, DOM snapshots, screen frames), says the output mask does not cover them and that the absence of a known secret proves nothing. Binaries are still not masked: `trace.zip` and PNG are written as bytes, and `storageState.json` goes through the text mask for known secret values only. This is a documented boundary, not a guarantee.

### Added

- **Skill `feature`: at most 5 sub-agents, `skillsync resources --json` before a step, and the plan marked right after the review PASS (CORE-1150-AUTOKEEP).** The parallelism paragraph names the limit of 5 sub-agents in total (at most two writing code), the `allowed` field of `skillsync resources --json` as the check before a step is handed out, and the order after a review PASS: `[x]` with the sha, `agent-run end`, `zrobione:`/`w toku:` (`w toku: <step> (review)` while a review runs). The Stop hook `check-closure.py` now also calls `skillsync plan-fresh` (it needs the `--cli` argument that `skillsync link` writes into the Stop entry) and stops the turn once with the list when the plan is stale; the limit of 5 remains a recommendation. The same script also serves `PostToolUse` (entry with the matcher `Agent|Task`, timeout 5 s, written by `link`): it returns one JSON `additionalContext` that reaches the model, with the free capacity (steps with `[ ]` outside `w toku:`) and one short line per blocking finding, once per (session, list); silent without a plan, inside a sub-agent and on any error, never blocking; for a sub-agent started in the background it fires at the start. `hooks.plan_nudge: false` turns it off; the stderr sentence of the Stop hook stays a view for the person only.
- **Card marker `.skillsync/card-alias` (skills `dispatch`, `feature`, `workflow`).** `dispatch/SKILL.md` and `feature/references/worker-mode.md`: on `ASSIGN` the card writes its alias (one line) to `.skillsync/card-alias` in its working directory and removes the file after `REPORT DONE` and `REPORT BLOCKED` and before the session ends; `workflow/references/feedback.md`: `skillsync feedback send` and `approve` refuse while the marker (or `SKILLSYNC_CARD_ALIAS`) is there. The marker is local and outside git; it guards against a mistake, it is not a security boundary.
- **Skill `agent-runtime-pitfalls`: commit a step only after the subagent that touched its files reports the end, also when it works in the background.** The entry gives the check before `git add` (a leftover constant or a commented condition left by a control mutation must not reach the history) and how to find it afterwards.
- **`db_snapshot.py prune`: retention of the snapshots (test-env).** A separate, manual subcommand that deletes old copies from a local directory and never contacts the engine (`prune --database <db> --confirm <db> [--label L] [--keep N] [--older-than-days D] [--dir D] [--dry-run] [--json]`). It deletes only files it can tie to its own sidecar: a regular `.bak` named like the tool names it, with a local sidecar (`remote: false`) that matches the name, label, size and `created_utc`; everything else (foreign files, a `.bak` without a sidecar, sidecars of remote copies, links, directories) is skipped with a reason and never deleted. Per (database, label) the newest N stay (`--keep`, default 5, 3 for `pre-restore`), `--older-than-days` is an AND, nothing younger than one hour is touched, the `.bak` goes before its sidecar, and each file is re-checked right before it is deleted. Exit codes are its own: 1 after a deletion and then a failure or interruption, 2 when nothing was deleted (the directory is unchanged), 3 for `--dry-run`; `--confirm` is required also with `--dry-run`. `backup` and `restore` are unchanged and still delete nothing. Verified by tests only (in memory and on a real directory); not on Windows file locks or copies made by the SQL service account.
- **`db_snapshot.py restore --move existing|new` (test-env).** `--move none` (default) is unchanged: the same checks, the same batch, no `RESTORE FILELISTONLY` and no read of `sys.master_files`. `--move existing` restores over an existing database with `MOVE` onto the current files of that database (bound one to one by logical name and type, emitted in `FileId` order, no assumption about the order of rows; the files are read again right before the destructive batch, a layout change after the safety backup is exit 1, without a safety backup exit 2). `--move new` creates a database that does not exist: no `REPLACE`, no safety backup, an existing database is refused; the files go to `--move-dir` (an existing directory outside the repository, ASCII whitelist characters only; the tool never creates it) or to the default data and log paths of the engine (the trailing separator is cut), under names with no text from the copy (`<database>_<number>_<6 hex>.<mdf|ndf|ldf>`). The result carries `created: true`, a warning about the origin of the copy (CLR objects, `TRUSTWORTHY`; a copy from another machine needs `--allow-machine-mismatch` (and `--allow-missing-sidecar` only when `--server` differs from the one in the sidecar), the weakest identity, so `--sha256` at `backup` is advised) and an `undo_note` with the manual `DROP DATABASE` (the tool never runs it). A failed `new` is exit 1 with the state read afterwards. Characters outside the whitelist (including `Program Files (x86)` and non-ASCII letters) are refused with a hint to use `--move-dir`. Engine facts come from probes on SQL Server LocalDB 2019 (23 Nov); not verified (NOT_RUN): another user's LocalDB, a restricted login, TDE or a newer engine, foreign files in the target directory, a database left in RESTORING after a failed `new`, copies made by the SQL service account, a copy of the same database after its files were moved; the live test of the new code passed on LocalDB 2019 (23 Nov; scenarios: `existing`, `new` with `--move-dir` and with the default paths, a failed `new`, `none`), with the NOT_RUN limits above.

### Changed

- **Skill `prototype`, `references/style.md`: the light definition of a required token (D-7).** A required token counts as defined in `tokens.css` only when a light block (`:root`, `@theme`, `@theme inline`) defines it, as in `theme_check.py`; in the project's `theme.css` any block except a dark one still counts. Only a project whose layer or `--assets` replaces `theme/default.css` or `theme/required-tokens.txt` can be affected: the default theme of the core defines all 59 required tokens in its light `:root`, so a project that uses it is not.
- **`db_snapshot.py restore`: the failure message suggests `SET MULTI_USER` also in mode `none`.** When the state read after a failed destructive step is `ONLINE` with `user_access_desc` other than `MULTI_USER`, the way back first gives `ALTER DATABASE [x] SET MULTI_USER` (run by hand), then the safety backup. The batch still tries to restore `MULTI_USER` in its `CATCH`, but a failure of that attempt is swallowed and the state is only reported; other states keep their texts.
- **Skill `feature`: a spec is split into two plans from eight capabilities, not five (`SKILL.md`, `plan-details`, `plan-large`, also in `core-en`).** The user's decision: splitting a five-capability spec into smaller plans was unnecessary. The cut-scope example in `SKILL.md` uses the same threshold. The estimate of 75–105 min per capability is unchanged.
- **Skills `feature`, `dashboard`, `references/plan-details.md`: three rules from a retrospective of a client project.** `feature` (`SKILL.md`): waiting for a long run on a shared resource does not hold back ready steps (check them on every return of a subagent and before waiting, start them in parallel within the limit, record in one sentence when there are none). `dashboard` (`SKILL.md`): when restarting a running instance, take `--read-bind`, `--read-host`, `--approve-host` and ports from the saved configuration, not from the command line of the old process, and check that every earlier viewer still gets the page and not a 403. `plan-details`: a `Progress` item is one thing (review and checks after a build do not share an item with a commit), and documentation is planned after the last code step with its evidence collected on the final state. With the `core-en` translation.
- **Skill `feature`: a review round is saved right after its result.** `SKILL.md`, `references/review-loop.md` and `worker-mode.md` (with the `core-en` translation) now say where the saving rule is needed: every round of the loops `spec`, `plan` and `code` goes to `.skillsync/runs/<run-id>/<profile>-review-N.md` (the run directory is created when missing), because the status page counts `Review ×N` from these files and sessions had stopped saving them (no report of any round since 22 Nov). This is the text step of the delivery that adds the commands `agent-run` and `review-round` and the running-round indicator.
- **Retrospective: the signal `estimate-error` and the rules for estimates in `measurement.md`.** `retro-quality.md` (trigger 8, with the table of groups), `agents/reviewer.md` and `workflow/cause-keys.yaml` get the key `estimate-error`: a series from `dashboard metrics` `estimates[]` that was late by at least 10 minutes (the waiting for the user already taken out) or too big by at least 10 minutes and 30% becomes a fact with the numbers; the "Result" of a plan has a line "Estimates:". `measurement.md`: an estimate with steps survives `waiting:` and moves by the waiting for the user, a wait for CI or a LEASE is written `(who: agent)`, a discrepancy over 10 minutes is reported at `done:`. With the `core-en` translation.

### Fixed

- **Hooks on Windows: the `--cli` argument of the hook entries was unreadable, so the hook stayed silent.** The entries of `session-context` (in the released versions) and the new Stop and PostToolUse entries of `check-closure.py` carry `--cli <JSON argv>`; Git bash lost one level of backslashes in a Windows path inside that argument, the hook could not parse it and, being fail-open, did nothing. `skillsync link` now writes absolute Windows paths of the argument with `/` (UNC as `//server/…`, no `\uXXXX` escapes; POSIX unchanged) and replaces an entry saved in the old form, and `check-closure.py` leaves one event `cli-unreadable` per session in `.skillsync/hook-events.jsonl` (never the content, never a block) when `--cli` is given but unreadable. After the update run `skillsync link` in the project: the entries are then correct.

## [1.14.1] - 2026-11-23

### Fixed

- `workflow/trackers/azure-feedback.yaml`: the comment operations use `--api-version 7.1-preview` (`az` refuses `7.1-preview.4`), so `skillsync feedback` can comment on an existing item.

## [1.14.0] - 2026-11-22

### Added

- **Tools: `workflow/references/tools.md`, `supersedes` procedure, owner `tool`.** New reference on finding, creating and promoting agent tools (`skillsync route` field `tools`, header labels, records in `docs/tools/index.yaml` or `tools/index.yaml`); `workflow/references/knowledge.md` gets the agent procedure for the `superseded` warning (when `parity` may be run); the skill `skillsync` describes the keys and commands; `feature` and `bugfix` put the tools from `route` into the brief of a subagent; the retrospective owner `tool` and the cause keys `tool-duplication` and `tool-candidate` (`workflow/cause-keys.yaml`).

### Changed

- **Skill `feature` (plan-details): ticking keeps up with commits.** A step that has evidence and a commit is ticked at once (`[x]` with the sha), also when other subagents of the same phase are still working, because the status page and `skillsync status` read `Progress`.
- **Skill `dotnet-framework-48`: locating MSBuild.** `vswhere -products * -latest` can pick an instance without MSBuild; the skill gives `vswhere -latest -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe'`.

## [1.13.0] - 2026-11-22

### Added

- **Skill `dashboard`: text catalog and page language (`lang`, `texts`).** The renderer `dashboard-render.js` gets `t(key, params, n)`, `lang()` and `defaultTexts`; precedence: `texts` from the status (only for a language other than `pl`), then the built-in Polish catalog `DEFAULT_TEXTS` (generated from `skillsync_workflow/data/i18n/pl.json` by `scripts/sync_renderer_defaults.py`). Older renderers ignore the new fields. Descriptions: `descriptions.en.json` next to `descriptions.json` (the server picks the file by the project language; no file = Polish). Contract: `docs/dashboard-contract.md`, section "Język i teksty interfejsu".
- **Skill `skillsync`: manifest key `language`.** `pl` (default) or `en`; it controls only the interface (status page, server texts, descriptions, `<title>`), not the language of skills. Described in `SKILL.md`, section "Język interfejsu (`language`)"; rules: `docs/domain/i18n-ui.md`.

### Changed

- **Skill `dashboard`: status includes `lang`.** Every status JSON has the field `lang` (`pl` by default); the field `texts` appears only for a language other than `pl`. Polish texts unchanged.

## [1.12.0] - 2026-11-22

### Added

- **Hooks `session-context`, `guard-generated`, `check-subagent-report`.** Three Claude Code hooks (SessionStart; PreToolUse on `Write|Edit|NotebookEdit`; SubagentStop) in `hooks/`, with the manifest keys `hooks.session_context`, `hooks.guard_generated`, `hooks.subagent_report` (documented in `docs/manifest.md`); `doctor` reports their state and the warnings of the last 14 days.
- **`hooks.subagent_commit`: the commit boundary for sub-agents.** The argument `--subagent-commit warn|deny|off` of `guard-commands.py` refuses git and `gh pr` writes from a sub-agent (a call with `agent_id`); the main session is not affected. Default `deny` in `feature` and `dispatch` projects.
- **`dispatch`: the pulse of the dispatcher.** `references/pulse.md` (the tick, the closed list of actions, the prompt of the loop that points to the file and never to the skill, the sidecar, the permissions of an unattended tick), the key `dispatch.pulse` in `config.md`, the start, the end and the renewal of the loop in `SKILL.md` and `state.md`, and the exception of the rule about polling in `SKILL.md`, `loop.md`, `claude.md` and `codex.md` (Codex has no pulse).
- Skill `feature`, reference `claude.md`: the column `maxTurns` in the table of the roles, the explanation of the limit (a turn, the partial result, how the values were calibrated) and the result of the probe of `disallowedTools` (`F0-disallowedTools: nieskuteczne`).
- Skills `skillsync`, `dashboard` and the reference `feature/spec-store.md`: the flag `approvals record --expect-plan-state` and the `409` with `code: plan_state_changed` of the consent button.
- **`copilot-skills-yaml`: the keys of Claude Code.** The list of the frontmatter keys of Claude Code, the difference between `user-invokable` (Copilot) and `user-invocable` (Claude Code), and the rule that a custom key is only a `doctor` warning (own data goes to `metadata`); `skillsync` mentions the lint of `doctor` in the checks of a new skill.
- Skill `workflow`, reference `test-run.md`: section "Kontrakt zdarzeń" with the fields of the events of a test step, including the optional `end.changed` (up to five paths that changed while the tests ran, masked, shown only in the message of the gate and never passed to a command).
- **Skill `skillsync`: the completion command.** One line in "Instalacja i `skillsync setup`": `skillsync completion bash|zsh|powershell` (script on stdout) and `completion install|uninstall`; the installer and `setup` install it, `SKILLSYNC_NO_COMPLETION=1` skips it. Details in `docs/cli.md` of the generator.
- **Optional status line and output style (opt-in, off by default).** `hooks/statusline.py` is a status line for Claude Code (one line: session name as `sesja: <name>` without any claim about identity, project, branch and worktree, delivery mode, plan with `N/M` steps). `link` installs it only with `claude.statusline: true`, and never over a status line set by someone else. `output-styles/karta.md` is a style for card reports, installed only with `claude.output_styles: true`; the library layer is core-only. It sets no format of its own: it refers to the grammar of the card protocol (`skills/feature/references/multi-session.md`: first line `date: <output of date>`, then `STATUS <paczka> <akt> <czeka-na>` or `REPORT DONE|BLOCKED <paczka> commit=… checks=… debt=… retro=…`) and adds brevity, evidence with the command and its exit code, and an explicit `NOT_RUN`. Requires Claude Code 1.0.71 (status line) and 1.0.81 (output styles) or later; the refresh interval needs 2.1.97. Optional; VS Code display unconfirmed.

### Changed

- **`claude` key read in one place.** `doctor`, `link` and the guard hook read `claude:` through one function; only `false` and `{enabled: false}` switch Claude off, and a non-boolean `enabled` is a `doctor` error (it leaves Claude enabled). `doctor` now validates the keys under `claude:`.
- **Hooks are installed by one shared merge.** `link` writes the entries of closure, guard and the new hooks through one function; the bytes of the settings file and the backup stay as before. `link` with `claude: false` removes our hook entries and scripts.
- **`dispatch`: `disable-model-invocation: true`.** The skill is started only by the user (`/dispatch`); the pulse prompt reads `references/pulse.md` and never calls the skill. The loop of a replied probe: the dispatcher updates `last_message` from the reaction table (`loop.md`), otherwise the probe returns every `idle_card_minutes`.
- Roles `agents/*.md`: `maxTurns` in all eight roles (`explore` 40, `implementer` 170, `mechanic` 40, `reviewer` 85, `scribe` 115, `test-runner` 125, `test-writer` 165, `verifier` 45).
- **`argument-hint` in the skills with arguments.** `feature`, `bugfix`, `dispatch`, `codereview`, `pr-run` and `docx-to-markdown` have the frontmatter field `argument-hint` (autocomplete hint of Claude Code, presentation only; the notation is described in `copilot-skills-yaml`). The references to the non-existent `docs/library.md` and `docs/layers.md` in the instructions and in the `skillsync` skill point to `docs/layers-authoring.md` and the authoring rules.
- Skill `dashboard` (`SKILL.md`, "Zakładka „Testy”"): an attempt shows at most 200 groups, and a note says how many were omitted (`groupsOmitted`); the counters still cover every test.
- Skill `workflow` (`test-run.md`): a `[T]` step counts as done by a checked `- [x]` row or by a PASS checkpoint, so `tests-not-run` also works for plans with a step table.
- Skill `feature` (`prepush-matrix.md`, `resources.md`): a changed test file (helper) also reruns the guards that scan `tests/` already at the point rung; a point run is one module or test class, while several modules (hundreds of tests) or `discover` is a wide run that needs a lease grant.
- Skill `feature` (`SKILL.md`, „Testy i poprawki”): the `skillsync test-run` call names the project as the positional argument, and a command without test counts needs an explicit `--allow-no-count <reason>`.
- Skill `feature` (`retro-quality.md`, `review-loop.md`, agent `reviewer`): a late retrospective (a `final` record written before it) is closed again with `final-2`; a round after the user answered review questions (Q/C) is not `ceremony-excess`; the `plan` loop at risk high is mandatory but not full; with at least one signal the files `retro-signals.json` and `retro-decisions.json` exist before the `retro-quality` `PASS` (not as `--evidence`), and the `open` entry is written after the scan.
- Skill `feature` (`plan-details.md`): the unfinished-markers rule of `readiness` also covers domain state names, the `Prototyp:` line takes only a path (`invalid_prototype_line`, `prototype_not_found`), and `Bez prototypu:` is a convention for people that the tools do not read.
- Skill `workflow` (`test-run.md`): limits of `test-run` with wrappers (the wrapper passes the exit code, `--allow-no-count` verifies nothing, counts from an unrecognised command come from loose expressions, `--format` names the adapter, test the wrapper with a failing command).
- Skill `agent-runtime-pitfalls`: five pitfalls (Glob and Grep do not see linked skills, the host edit tool rewrites mixed line endings as LF, a literal `\0` in a byte-editing script inserts a NUL, `scan; push` is not a gate, do not preview a file with a secret even through a masking filter).

## [1.11.0] - 2026-11-21

### Breaking

- Hook `check-closure.py` reads ignored plans too (`git status --ignored`, entries `!!`): a `done` plan without a `final` record, or an `in_progress` plan with all steps checked, blocks a turn once per session also in projects that ignore `docs/plans`. Workaround: `skillsync plan archive`, a `--final` record of the run, or `hooks.stop_closure: false`. Details: `docs/upgrading.md` of the generator.

### Added

- Hook `hooks/guard-tests.py`: warnings `wide` for `env -S '…'`, `poetry run pytest` and `python3.12 -m pytest` (never blocking), and the read of `-m` behind a Python host; `skillsync link` refreshes the copies of `guard-tests.py` and `test-tools.json` next to the hook.
- Skill `dashboard`: contract field `expertQuestions.current` (the prototypes and expert questions of the current run, bound by the spec path).
- Skill `feature`, reference `spec-store.md`: the mode of specs and plans outside git (`workflow.specs.tracked: false`): no commit of the spec and plan at "GO plan", snapshot with `skillsync spec snapshot`, optional archive (`workflow.specs.archive`), closing the run and removing the worktree only after a `PASS` of `spec snapshot --check --to`.
- Skill `skillsync`: `route` field `specs {tracked, archive, source}` and commands `spec snapshot` (snapshot and `--check`).
- Skill `dashboard`: `planState` of the `CP` and `READY` actions (the server answers 409 when the plan status has changed); the contract is in `docs/dashboard-contract.md`.

### Changed

- Skills `skillsync` and `workflow` (`test-run.md`): the exit code of `test-run` (`FAIL` returns the code of the command; exit 0 without a recorded `PASS` is 2) and the trace of the run that `--run <id>` needs in `.skillsync/runs/`. Skill `dashboard`: the tab "Testy" shows a note for the skipped result files (`tests.skipped`).
- Skill `dashboard`: the `CP`/`READY` button reopens after a plan status change at the same HEAD (for entries with `planState`, also with `tracked: true`).
- Skills `feature`, `pr-run`, `bugfix` and instruction `_base.md`: when specs and plans are ignored, the procedure omits them from commits and the PR description (the PR description summarizes the spec and gives `Spec-sha`); the `_base.md` budget of 8192 bytes is kept by shortening phrases that tests do not pin.
- Skill `workflow`, reference `test-run.md`: the limits of the hook are rewritten for the new recognition (`python3 -X dev -m pytest` and `py -3 -m pytest` are seen; `tox` is a tool without a rule of a wide run; `sudo`/`nohup` stay as limits; `-m` behind a script gives `wide`; `env -S '…'` is read like the command it carries).
- A command given to `test-run` as a string is split with POSIX quoting on every system (on Windows a single quote was a literal character, so `env -S 'CI=1 npx jest'` was not unwrapped; `--grep 'a b'` is one argument, a backslash stays literal); only the detection of the tool reads the string.
- Skill `dashboard`: the tab "Pytania" shows only the current run (hidden when it has no prototypes and no questions; a server without `current` gets a local filter by `prototype.slug`); the document groups "Speki", "Plany", "Projekt" and "Domena" are collapsed by default.


### Fixed

- Hook `guard-tests`: `python -W error -m unittest tests` is classified as a point run.

## [1.10.0] - 2026-11-18

### Added

- `skills/feature/references/workdir.md` (new): the working directory of a run, chosen at "GO plan" (own worktree or main checkout); the `Katalog:` line, the conditions of the main checkout, the failure paths and the resume rules.
- `skills/dashboard`: the header chip "worktree: <name>" / "główny checkout" (renderer, `descriptions.json` key `workdir_chip`, icon `i-folder`).
- Skill `workflow`, reference `test-run.md`: a table of the test adapters (`unittest` … `node-test`) with the rule for `auto`, the level of live state and the source of the final result.
- Skill `dashboard`: the Testy tab shows "kod N, żaden test nie zgłosił porażki" for a run whose command exited non-zero with no reported failure (every tool), and the "back to top" button works in that tab when the host gives `scroll.toTop`.

### Changed

- `instructions/_base.md`: the rule "from GO plan a separate worktree and a working branch" becomes "working directory (a question at GO plan, `Katalog:` in the plan) and a working branch"; the sentence on pushing to the base is shortened to a reference to `feature`; the copy of the run and the cleanup of the worktree apply to `Katalog: worktree` only.
- `skills/feature` (`SKILL.md`; references `plan-large`, `plan-details` with the `Katalog:` header line, `spec`, `retro-quality`, `worker-mode`, `pr-after-approval`): the same rule; the push to the base also works from the main checkout; "TAK P<n>" works in `direct` with the main checkout.
- `skills/pr-run/SKILL.md`: step 3 honours `Katalog:` (the main checkout and its current branch, not a base branch); step 8 skips the main checkout.
- `skills/skillsync/SKILL.md`: the working directory rule and the `Katalog:` line.
- `skills/dashboard`: `SKILL.md` (the chip in the renderer contract) and `descriptions.json`.
- `skills/feature/references/reports.md`: the report of a review-loop reviewer is always saved right after it returns, together with `<step>-N-decyzje.md`, not after the fact.

### Fixed

- `feature` and the dashboard skill: the plan line `Oddanie:` holds only a branch name; any other text is ignored (write the name or leave the line out).

## [1.9.1] - 2026-11-18

### Changed

- No change of the layer content; the version follows the generator 1.9.1.

## [1.9.0] - 2026-11-17

### Added

- `instructions/_base.md`: a fixed rule against footers, signatures and co-author trailers of AI tools in outgoing text; it cannot be overridden by layers or host notes.
- `hooks/guard-commands.py`: a part that cannot be switched off refuses commands that write such text (commits, tags, pull requests, issues, work items) and is installed in every project with a host.
- Skill `dashboard`: inline formatting of step titles, "waiting for" items, expert questions and the checkpoint feed; the "Wynik" section as Markdown; time of acts I and II and an approximate end of the plan steps; the list of theme tokens of the renderer.
- Skill `skillsync` and `workflow/references/knowledge.md`: `workflow.knowledge.domain` and `workflow.knowledge.lessons` accept a list of directories.

### Changed

- The paragraph on the cost and capability trade-off of models moved from `instructions/_base.md` to `skills/workflow/references/planning.md` (section on models and roles).
- `feature/references/retro-quality.md`: no AI tool footers in retro records.

### Fixed

- `prototype/assets/repo/prototype.css`: the sign-in and own-password dialogs of the engine have one container, a spaced footer and a single focus ring; run `skillsync prototype migrate`, the acceptance of screens stays valid.
## [1.8.0] - 2026-11-17

### Added

- Skill `feature` (`references/multi-session.md`) and skill `dispatch` (`references/loop.md`, `references/lessons.md`): a card report to the dispatcher starts with the line `date: <literal output of date>`; times of events in the body come from logs or from `date`, never from memory; a report without the line is rejected (`REPORT REJECTED powód=brak linii date`). Skill `agent-runtime-pitfalls`: section "Czas w raportach".
- Skills and roles (`feature`, `bugfix`, `smart-test`, `workflow`, `test-runner`, `test-writer`, `implementer`, `skillsync`, `dashboard`, `pr-run`) and the new reference `workflow/references/test-run.md`: the tests of a plan step run through `skillsync test-run --run <id> --step <id> -- <command>` ; a step marked `[T]` does not close without a passing run; test results are not masked and must not be pasted into text that leaves the machine.
- Hooks `hooks/guard-commands.py` (PreToolUse on `Bash`) and `hooks/guard-tests.py`, with `test-tools.json` (a copy of `skillsync_workflow/data/test_tools.json` written by `link`): the first part refuses permanent deletion with a replacement (`trash -- <paths>`), the second warns, never blocks, about a wide test run outside `skillsync test-run`. Both parts live in one hook entry; it is removed only when `hooks.guard_delete` and `hooks.guard_tests` are both `off`. Skill `skillsync` (`SKILL.md`) lists the hook, the keys `hooks.guard_delete`, `hooks.guard_tests`, `hooks.test_wrappers`, `hooks.wide_modules` and the new commands.
- Skill `dashboard` (`dashboard-render.js`, `app.js`, `app.css`, `SKILL.md`): the "Testy" tab (a section per plan step with the counts and the tests of the runs of `skillsync test-run`, a progress bar on the "Postęp" tab), the listen addresses of the two-port mode, and `GET /api/tests`.
- Skill `prototype` (`prototype.js`, `references/style.md`, `SKILL.md`): `prototype.dark_theme: false` (`data-proto-dark="off"` on `<html>`: no dark theme, no "Motyw" button), the label and the hidden tools of the read-only show (`prototype serve --demo`), and the notes on which style sheets enter a screen hash. An existing prototype gets the engine after `skillsync prototype migrate`; screen hashes and acceptances do not change.
- Skill `dispatch`: `references/preview.md`, the template of the order for a live preview on an isolated state.
- Skill `workflow` (`references/test-run.md`): the limits of the hook that warns about test runs (it speaks after the command ran, its effect on a live session is not verified, it does not see PowerShell) and the way to get the test list per tool.

## [1.7.0] - 2026-11-16

### Added

- Skill `dashboard` (`dashboard-render.js`, `app.js`, `app.css`): the "N unpushed" badge opens a panel with the unpushed commits (`delivery.commits`, `delivery.commitsMore`); a floating "back to top" button in the document view (drawn only when the host gives the adapter hook `scroll.toTop`); on a narrow screen the commit sheet ends above that button. A renderer copy without the new field or hook shows the page as before.

### Changed

- Skill `dashboard`: `SKILL.md` names the Windows way to stop the server (Ctrl+C or CTRL_BREAK to the process group; the table is in `docs/dashboard-contract.md`).

## [1.6.1] - 2026-11-16

### Changed

- Skill `project-guard`: a changed file that is a link is skipped and never opened (`skipped` in the report); when every file covered by a rule is such a link, the status is `PASS` with the reason "No content was checked", so `validate` is not blocked.
- Skill `dashboard`: `SKILL.md` describes `/api/doc` as masked (a heuristic, not a guarantee); the page names the parser warning `unbalanced-bracket`.
- Skill `feature`: `measurement.md` (an unclosed bracket always gives a warning on the status page), `prepush-matrix.md` (commands of the mandatory steps, rules for matrix runs on remote machines), `worker-mode.md` (run `skillsync link <worktree>` after `git worktree add`, work in one tree), a clarification in `tests-baseline.md`.
- Skill `prototype`: `SKILL.md` and the engine (`prototype.js`) warn that the text of comments and answers is visible to others and is not masked; an existing prototype gets the notes after `skillsync prototype migrate`.
- Skill `skillsync`: the row of `dashboard serve` describes the masked `/api/doc`.

## [1.6.0] - 2026-11-16

### Added

- Skill `dashboard`: the page renders the stage state (`trwa`), the screens bar, "Czeka na", two shades of the progress bar, the block "Wynik", the glossary (`legend_*` in `descriptions.json`), the addressee of expert questions, the toast of comments after acceptance and the tab "Pytania"; `SKILL.md` describes them.
- Skill `feature`, reference `measurement.md`: the syntax of the progress report (brackets, an id at the start of every element, `trwa: <stage>`, `czeka: … (kto: <role> [<login>])`).
- Skill `prototype`: `writing start --screens <N>` before editing screens, with the fallback for an older core; `trwa: prototype` in the progress report.
- Skill `dashboard`: two-port mode of the status page (`--read-port`/`--approve-port`), `dashboard url --read`, and the role in `approvals.role`; `SKILL.md` describes it.
- `workflow/defaults.yaml`: `review.prototype`, the core checklist of the review profile `prototyp` (screens and criteria, edge states, plain texts, navigation and anchors, basic accessibility, no sensitive data, the engine contract).
- `skills/feature/references/plan-details.md`: the required section `## Streszczenie dla człowieka` of a plan, the template of `## Wynik` (the block "Uwagi z review" and the table of the coverage of the criteria), the acceptance step `Odbiór` (the closing consent word is the acceptance).
- `skills/feature/references/review-loop.md`: the profile `prototyp`, the report `prototype-review-N.md` and the rule that names the review remarks (blocker and major, and every remark that changed a screen, a spec or a plan, with title, weight and what was done).
- `skills/feature/references/spec.md`, `skills/prototype/SKILL.md`, `skills/skillsync/SKILL.md`: the question rules for the experts (a question about a screen carries `--screen`; plain language, an example of a good and a bad question) and the option `--screen`.
- `skills/prototype/assets/repo/prototype.js`, `prototype.css`: the presentation mode (one screen at a time).

### Changed

- `skills/skillsync/SKILL.md`: `status` (`summary`, `acceptance`), `review --profile`.

## [1.5.0] - 2026-11-15

### Added

- `skills/feature/references/prepush-matrix.md`: the matrix of checks before a push (Python versions, systems, `check_skips` on the logs of all systems); references in `skills/feature/references/tests-baseline.md` and in `skills/dispatch/SKILL.md` (the `ASSIGN` assignment).

### Changed

- `skills/dashboard/assets/app.css`: the state of the live indicator has its own dot shape besides the colour (circle, square, triangle, ring), and buttons and tabs in the compact mode (≤ 420 px) are at least 2.75 rem high; a plugin with its own copy of the stylesheet has to resynchronise it.
- `skills/prototype/SKILL.md`: the machine config with `prototype.default_password` is private by mode 0600 on POSIX and by an ACL on Windows (set and repaired by the tool).
- `skills/skillsync/SKILL.md`: the command `dashboard url` and the consent token in the description of `dashboard serve`.
- `skills/dashboard`: consents from the page need a token (`X-Approve-Token`, an address with the fragment `#zgoda=<token>` from the console of `dashboard serve` or from `skillsync dashboard url`); `app.js` remembers the token, `dashboard-render.js` has the hook `deps.hasApproveToken` (buttons are inactive without a token; a webview without the hook is unchanged); `SKILL.md`: a rule for the agent.

### Breaking

- `skills/dashboard`: `POST /api/approve` without the header `X-Approve-Token` returns `403`; own scripts have to take the token from `skillsync dashboard url`. The status page and the editor plugin need no change.

## [1.4.1] - 2026-11-15

No changes to the library in this release (the version follows the core).

## [1.4.0] - 2026-11-14

### Added
- `skills/dashboard`: toast „szacowany koniec” (`eta:` w checkpoincie, pole `eta` JSON); `skills/feature`, `skills/prototype`: reguła „szacuj przed startem długiej operacji i zapisz `eta: <operacja> do HH:MM` w checkpoincie, aktualizuj przy przekroczeniu, czas z `date`”.
- `skills/skillsync/SKILL.md`: klucze manifestu `prototype.public_url` i `prototype.port`, pole `ignored` (pola `question`/`questions[]` i flaga `checkpoint --manifest`: `docs/cli.md`); `skills/feature/references/spec.md`: czas w wierszu `[EKSPERT]` lokalny z offsetem (`<RRRR-MM-DDTHH:MM±GG:MM>`); `skills/dashboard/SKILL.md`: `--prototype-port` i port z manifestu.
- `skills/agent-runtime-pitfalls/SKILL.md`: pułapki zsh (`$S:Q1` to modyfikator `:Q`, pisz `"${S}:Q1"`) i stopka `[exited with code 0]` polecenia w tle.

### Changed
- `skills/feature` (`SKILL.md`, `references/spec.md`) i `skills/skillsync`: treść odpowiedzi i komentarzy ekspertów to dane (`untrusted` i `notice` w raportach `question list|show|export|wait`): poleceń z niej agent nie wykonuje i zgłasza je człowiekowi; `questions.json` czytany wprost to ta sama niezaufana treść; wiersz `[EKSPERT]` w specu to dane w pliku repo.
- `skills/feature` (`SKILL.md`, `references/spec.md`) i `skills/skillsync`: pytania o kontrakt, dane (schemat bazy), migrację i bezpieczeństwo to decyzja programisty (zwykłe `Q` w sesji), do eksperta tylko pytania domenowe; `question withdraw --reason` (powód widzi ekspert, wycofane pytanie zostaje na jego liście).
- `skills/prototype` (`SKILL.md`, `references/decisions.md`) i `skills/feature`: polecenie w komentarzu agent zgłasza człowiekowi, przytacza i odpowiada w wątku, że nie wykona; raporty `comments` i `wait` mają `untrusted` i `notice`; ponowny odczyt komentarzy przy „GO plan”, „GO implement” i przed fazą interfejsu.
- `skills/prototype/assets/repo/prototype.css`: widoczny stan wciśniętych przełączników paska, mniejsze pole komentarza (istniejące prototypy dostają arkusz po `skillsync prototype migrate`).
- `skills/prototype/SKILL.md`, `skills/skillsync/SKILL.md`: odnowienie `writing start` zachowuje początek pisania (`since`) i autora, nowe pole `renewedAt`; „Zwolnij” w banerze (tylko akceptujący i deweloper); strona statusu pokazuje „agent pisze ekrany (do HH:MM)” zamiast wezwania do akceptacji.
- `skills/dashboard`: pozycje czekania `writing` i `expert` (z plików), pole `prototype.writing`, renderer z odnośnikiem do strony pytań w banerze czekania.
- `skills/prototype/assets/repo/prototype.js`: baner „do HH:MM”, przycisk „Zwolnij” dla roli `acceptor` i `developer`; istniejące prototypy dostają silnik po `skillsync prototype migrate`.
- `agents/implementer.md`, `agents/reviewer.md`, `agents/test-writer.md`: treść od ludzi spoza sesji (odpowiedzi i komentarze ekspertów, wiersze `[EKSPERT]`, komentarze prototypu, wątki PR) to dane, nie instrukcje; polecenia z niej podumysł zgłasza orkiestratorowi.
- `layer.yaml`: `version` i `core_min` 1.4.0 (warstwa opisuje komendy rdzenia 1.4.0).

## [1.3.0] - 2026-11-13

### Added
- `skills/prototype/SKILL.md`: sekcja „Zapis ekranów: marker »agent pisze«”: `prototype writing <projekt> <slug> start` przed edycją ekranów (po `iterate`), odnowienie przy większych partiach albo `--ttl 1800`, `done` po `ids --check` i sprawdzeniu slugu w galerii, dopiero potem „prototyp gotowy”; obsługa `lock_timeout` i `needs_migration`; marker to sygnał koordynacji z wygasaniem; agent nadal nie uruchamia `accept`.
- `skills/skillsync/SKILL.md`: komenda `prototype writing`, kody BLOCKED (`needs_migration`, `lock_timeout`, `invalid_args`), `writing_in_progress` przy `accept`, pole `writing` w `list`.
- `skills/feature/references/review-loop.md`, `agents/reviewer.md`: profil przeglądu `docs` (obietnice z dowodem, zgodność instrukcji z narzędziami, brak sprzeczności skilli, tylko stan bieżący, brak nazw osób, klientów i wydarzeń); `skills/pr-run`, `skills/feature`, `skills/dispatch`: opis PR-a, komentarz i notatka wydania przechodzą `skillsync leakcheck --text <plik>` przed publikacją; `skills/skillsync`: flagi `--text`/`--stdin`.
- `skills/dashboard`: czas pracy i czekania na człowieka (`timing`) w sekcji aktu III strony statusu, renderer (`timingBlock`), opis w `descriptions.json` i w `SKILL.md`.

### Changed
- `skills/prototype/references/decisions.md` i `SKILL.md` (procedura po rundzie): po `iterate` marker `writing start`, po zmianach HTML `ids --check`, sprawdzenie slugu i `writing done`.
- `skills/prototype/SKILL.md`: `prototype wait` nie zgłasza `accepted` przy ważnym markerze; `gate` ma powód `writing`.
- `skills/feature/references/plan-details.md`: lista powodów FAIL bramki o `writing`.
- `skills/prototype/assets/repo/prototype.js`: baner „Agent jeszcze pisze ekrany”, wyłączone przyciski akceptacji i przywracania, powrót po zniknięciu markera bez przeładowania; istniejące prototypy dostają silnik po `skillsync prototype migrate`.
- `skills/prototype/assets/repo/.gitignore`: linia `.writing.json`.
- `layer.yaml`: `version` i `core_min` 1.3.0 (warstwa opisuje komendy rdzenia 1.3.0).

## [1.2.3] - 2026-11-13

### Changed
- `layer.yaml`: `version` i `core_min` 1.2.3 (bez zmian w skillach; warstwa jest wydawana razem z rdzeniem).

## [1.2.2] - 2026-11-12

### Changed
- `skills/dashboard` (`assets/dashboard-render.js`, `assets/app.js`, `assets/app.css`, `descriptions.json`, `SKILL.md`): bez sekcji „Pytania” w kafelku Aktu I; pytania do eksperta pokazuje toast „Pytania do eksperta: N otwartych (M blokujących)” z pola `expertQuestions` (wszystkie przebiegi), z odnośnikiem do `/_experts[/<slug>]`; usunięte klucze dymków `questions_blocking` i `questions_assumptions`.
- `skills/skillsync`: `question purge` z `--include-open` (domyślnie tylko wycofane), `question record` sprawdza wskazaną linię speca (`[EKSPERT]` i id pytania).
- `layer.yaml`: `version` i `core_min` 1.2.2.

## [1.2.1] - 2026-11-12

### Changed
- `skills/skillsync`: pytania do eksperta są w `<root>/<slug>/questions.json` (osobny plik per przebieg), strony `/_experts` i `/_experts/<slug>`; `prototype question` z `--slug` (przy `add` bez niego przebieg wybierany ze speca z `--origin`), nowe `question migrate` i `question purge`, wynik `add` z `created` i `notes`, `add --multi` (pytanie wielokrotnego wyboru).
- `layer.yaml`: `version` i `core_min` 1.2.1 (warstwa opisuje komendy rdzenia 1.2.1).

## [1.2.0] - 2026-11-12

### Added
- `skills/feature/references/spec.md`: klasa wiersza `[EKSPERT]` (cykl: czekające `(ekspert: qn-…)`, odpowiedź z autorem i czasem, powrót po `withdraw`), opt-in programisty w akcie I, opcja „Nie wiem, zapytaj eksperta” w zwykłym pytaniu, wykrycie zdolności przez `question url` (BLOCKED = pytanie zwykłe).
- `skills/skillsync`: komendy `prototype question add|list|show|export|record|withdraw|url|wait|merge` i `prototype user roles`, plik `questions.json`, strona `/_experts`.

### Changed
- `skills/prototype` (`SKILL.md`, `references/decisions.md`): komentarz użytkownika z rolą `expert` jest głosem doradczym; `authorRoles`/`whoRoles` obok `authorRole`/`whoRole`.
- `skills/feature/references/spec.md`: reguła usuwania blokujących `Q` po odpowiedzi ma wyjątek dla wierszy `(ekspert: qn-…)`.
- `layer.yaml`: `version` i `core_min` 1.2.0 (warstwa wymaga rdzenia z komendami `question`). Warstwa `core` jest wydawana razem z rdzeniem, więc podniesienie jej `core_min` do wersji tego samego wydania nie jest zmianą MAJOR warstwy.

## [1.1.2] - 2026-11-12

### Changed
- `skills/dashboard` (`assets/dashboard-render.js`, `assets/app.js`, `SKILL.md`): pozycja „sesja czeka na Ciebie” i tytuł karty znikają po zapisaniu zgody, a komunikat o wyniku zgody gaśnie sam (10 s, błąd 30 s, odliczanie stoi przy ukrytej karcie).
- `skills/feature` (`SKILL.md`, `references/ceremony-demo.md`, `plan-details.md`, `retro-quality.md`, `review-loop.md`) i `skills/prototype/SKILL.md`: postępowanie przy `BLOCKED tty_required` przy akceptacji prototypu (poproś osobę o kliknięcie Akceptuj na stronie, czekaj dalej); w ceremonii `demo` przegląd `kod` ma najwyżej 2 rundy; cięcie zakresu dopiero po przekroczeniu progu planu albo za zgodą prowadzącego.
- `skills/agent-runtime-pitfalls`: cztery nowe pułapki (`npm ci` z niewłaściwą wersją npm, potok gubiący kod wyjścia runnera testów, zegar sesji odbiegający od maszyny, `=====` w zsh jako argument i odwrotne apostrofy w `echo`).

### Fixed
- `skills/prototype` (`assets/repo/prototype.js`, `prototype.css`, `references/screen-patterns.md`): nawigacja ekranów zawsze ma odnośnik „domek” do listy prototypów, także w prostym pasku (`data-proto-nav="strip"`); pasek działa tylko do 5 ekranów, przy większej liczbie atrybut jest ignorowany i powstaje rozwijany przełącznik z wyszukiwarką (nowy eksport silnika `screenNavMode`). Istniejące prototypy dostają nowy silnik przez `skillsync prototype migrate` albo `sync`.

## [1.1.1] - 2026-11-12

### Added
- `skills/dispatch/references/codex.md`: sekcja o protokole BRIDGE/1 (koperta, ACK, rejestr, skrzynki, granica zaufania).
- `skills/agent-runtime-pitfalls`: wpisy o jawnej liście ścieżek do `git add` i o zrzutach przeglądarki w osobnym profilu bez rozszerzeń.

### Changed
- `skills/dashboard` (`assets/dashboard-render.js`, `assets/app.js`, `assets/app.css`, `assets/index.html`, `SKILL.md`): widok strony statusu to wspólny renderer
  JSON → DOM (ten sam plik ładuje panel hosta), `app.js` to adapter strony; układ zależy od szerokości kontenera (`.dash-wrap`, progi 1100/640/500/420 px, tryb zwarty), a opis w `SKILL.md`
  zawiera kontrakt renderera i obowiązek hosta (wrapper z określoną szerokością).

### Fixed
- `skills/feature/references/ceremony-demo.md`: sekcja „Po specu i review: następny krok to prototyp” (ceremonia `demo` nie czeka biernie na „GO plan”).

## [1.1.0] - 2026-11-11

### Added
- `skills/feature/references/ceremony-demo.md`: ceremonia `demo` (klucz manifestu `workflow.ceremony`), `schema.json` z kluczem `workflow.ceremony` (`full` | `demo`).
- `skills/dashboard`: strona statusu projektu (plan, przebieg, zgody, metryki podkroków) nad kontraktem JSON `schema: 1`; komendy `skillsync dashboard show|serve|metrics` i `skillsync approvals record`. Wpis `dashboard: {scope: workflow}` w `workflow/skill-catalog.yaml`; `skills/skillsync` opisuje nowe komendy.
- `skills/dashboard` (`assets/app.js`, `assets/app.css`, `descriptions.json`): sekcja „Pytania” w kafelku Aktu I (pytania blokujące i założenia do korekty przy prototypie, „brak” przy pustej liście, widoczna dla każdego widza), kafelek „Readiness” w Akcie II (PASS / BLOCKED / brak danych, data, skrót Plan-sha, znacznik nieaktualnego briefu, link do `readiness.md` w podglądzie) i rozwijana lista podkroków `Fn.k` z czasem w Akcie III; nowe klucze dymków `stage_readiness`, `questions_blocking`, `questions_assumptions`, `substeps_time`.

### Changed
- `skills/feature` (`SKILL.md`, `spec`, `plan-details`, `plan-large`, `review-loop`, `measurement`): klasy pytań `Q` (`[ZAŁOŻENIE]`), „GO plan” = jeden lokalny commit (spec, plan-szkic, prototyp), znacznik `[UI]` i bramka prototypu per faza, kontrakt najpierw, test-writer równolegle z review R1, przy ≥ 5 zdolnościach dwa plany, szacunek 75–105 min na zdolność, raport „jedna linia na podkrok”.
- `instructions/_base.md`: pierwszy commit przy „GO plan”, jawny wyjątek `ceremony: demo` w regule „Done”.
- `skills/skillsync`: `workflow.ceremony`, `prototype gate --phase`, pola `readiness` dla faz `[UI]`.
- Alias warunkowy: `status-board` w `skills:` rozwija się do `dashboard` tylko wtedy, gdy żadna warstwa nie dostarcza skilla `status-board`.
- Skill `skillsync`: tabela komend opisuje pole `preImplement` w `status`, `warnings[]` w `plan approve` i flagę `--run-id` w `readiness` (zapis `readiness.md`).

### Fixed
- `skills/prototype` (`assets/repo/components.css`, `references/screen-patterns.md`): lista rozwijana otwarta w oknie lub szufladzie nie jest już obcinana przez `overflow` ciała okna ani chowana pod stopką — klasa `popover-float` (rodzeństwo okna) i wzorzec z regułą „okno mieści się bez paska przewijania”.

## [1.0.2] - 2026-11-11

### Removed
- `skills/go-jira` (przestarzały); wpis `go-jira` w manifeście rozwija się do `acli`. Usunięty z `workflow/skill-catalog.yaml`.

### Changed
- `skills/acli`: sekcja „Pola własne” (tworzenie: `create --from-json` z `additionalAttributes`; edycja istniejącego zgłoszenia: REST API na jawne polecenie, token nigdy w argumencie polecenia ani w logu), bez odesłań do go-jira; `skills/skillsync` bez `go-jira` w przykładzie.
- `skills/dispatch` (Krok 0, `references/claude.md`): bramka modelu dyspozytora to Opus 5.5 lub Fable 5.1 (najmocniejszy model rodziny 5); podział modeli kart ustala użytkownik.
- `skills/feature/references/ticket-intake.md`: nowy punkt 9 „Pytania w trakcie przebiegu” — przed pytaniem produktowym odczytaj ponownie zgłoszenie i komentarze, niepotwierdzone przesłanki oznacz jako założenie (retro, P1).
- `skills/feature/references/plan-large.md`: liczba rund review wynika z kategorii pętli w `review-loop.md` (pętla pełna tylko dla strażnika wycieków, migracji i bezpieczeństwa) (retro, P2).
- `skills/agent-runtime-pitfalls`: nie uruchamiaj skryptu pomocniczego po samej nazwie; wynik „0 testów” to brak dowodu (retro, P3).

## [1.0.1] - 2026-11-09

### Changed
- `skills/prototype` (silnik prototypów: `assets/repo/prototype.js` i `prototype.css`): fragment adresu ramki widoku to `#view-sN` zamiast `#sN`;
  ładowanie ramki nie przewija strony nadrzędnej (V-23, N-R1), a dokładne dopasowanie fragmentu ma pierwszeństwo.
- `skills/agent-runtime-pitfalls`: mutant po zawieszonym `cp -i` zostaje na dysku (sprawdź `diff -q`/`git diff` przed dalszą pracą);
  na Windows `write_text` zamienia LF na CRLF i psuje skróty liczone w testach, a ścieżki w JSON idą przez `as_posix()`.

## [1.0.0] - 2026-11-08

### Changed
- Tryb dostarczania (`delivery.mode`): `instructions/_base.md`, `feature`, `pr-run`, `worker-mode`, `multi-session`, `dispatch`
  i `workflow/references/release.md` opisują `pr` (domyślny) i `direct`, słowa zgody `CP`, `CPD`, `READY` (z wariantami `!`),
  commity po każdym sprawdzonym kroku planu i zakaz merge/complete PR. `skills/skillsync/SKILL.md` opisuje klucz `delivery` i pole `route`.

## [0.9.0]

### Added
- Pierwsze wydanie warstwy core po podziale biblioteki na warstwy (core, org, personal).
- Lekcje rdzenia w `workflow/lessons/`: uogólniona wiedza o testowaniu (zawężone biegi, Playwright i
  `TestServer`, Angular, kontrakty API, coverage wycinka, django-tenants, izolacja zasobów).
