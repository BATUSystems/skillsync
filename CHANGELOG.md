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
- Skill `prototype` (SKILL.md): `prototype index` po `init`, usunięciu prototypu albo zmianie tytułu, gdy galerię ogląda się
  bez `serve` (plik statyczny); przed zgłoszeniem „gotowy do akceptacji” sprawdzenie sluga na stronie głównej `serve`
  (`prototype list` czyta katalogi, nie galerię).
- Alias skilla `go-jira` → `acli` w manifestach (`skills:`) w obu miejscach rozwijania skilli (`skillsync:expand_skills`, `common.expand_project_skills`); `skillsync doctor` ostrzega, żeby zamienić `go-jira` na `acli`. `skillsync skills list` pokazuje `acli` z pochodzeniem `alias`.
- `docs/development.md`: po sklonowaniu repo do rozwoju zainstaluj hooki strażnika (`skillsync leakguard install-hook <klon>`); worktree dzielą `.git/hooks`.
- `docs/workflow.md`, `docs/workflow-details.md`: bramka modelu dyspozytora to Opus 5.5 lub Fable 5.1 (zgodnie z `library/skills/dispatch`).

### Removed
- Skill `go-jira` (przestarzały; zastąpiony przez `acli`). Wpis w manifeście nadal działa jako alias; ręczne odesłania do `skills/go-jira/` trzeba zamienić na `skills/acli/`.

### Breaking
None.

### Fixed
None.

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

[Unreleased]: https://github.com/BATUSystems/skillsync/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/BATUSystems/skillsync/releases/tag/v1.0.1
[1.0.0]: https://github.com/BATUSystems/skillsync/releases/tag/v1.0.0
