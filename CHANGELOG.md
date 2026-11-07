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
None.

### Changed
None.

### Breaking
None.

### Fixed
None.

## [1.0.0] - 2026-11-07

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
- v1.0.0 supports Linux and WSL; native Windows and macOS are planned for v1.0.1 (their CI jobs do not block).
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

### Breaking

- None against earlier public releases; this is the first public release.
- The `library` key of the machine config is not read. Run `skillsync layers migrate` (dry-run, then `--apply`);
  see [docs/upgrading.md](docs/upgrading.md).

### Fixed
None.

[Unreleased]: https://github.com/BATUSystems/skillsync/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/BATUSystems/skillsync/releases/tag/v1.0.0
