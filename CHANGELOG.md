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

### Changed
None.

### Breaking
None.

### Fixed
- Prototype mockups: an open dropdown, autocomplete, calendar or menu placed inside `.modal-body`/`.drawer-body` was clipped by the body's `overflow` and hidden under the footer. New class `popover-float` (a sibling of the window, above it) and a pattern in `screen-patterns.md`.

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

[Unreleased]: https://github.com/BATUSystems/skillsync/compare/v1.0.2...HEAD
[1.0.2]: https://github.com/BATUSystems/skillsync/releases/tag/v1.0.2
[1.0.1]: https://github.com/BATUSystems/skillsync/releases/tag/v1.0.1
[1.0.0]: https://github.com/BATUSystems/skillsync/releases/tag/v1.0.0
