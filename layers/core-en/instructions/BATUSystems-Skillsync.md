# Skillsync — the workflow artifact generator (repo-specific)
- This is the **code of the `skillsync` generator** (Python 3 + PyYAML), not a library. The library of skills, roles and instructions is a separate repo `~/.local/copilot-library` (CopilotSkills); project manifest: `<project>/.github/.skillsync.yaml`; machine config: `~/.config/skillsync/config.yaml` (override `SKILLSYNC_CONFIG`).
- Layout: `skillsync` (wrapper) and `skillsync_workflow/cli.py` (commands link/instructions/push/pull/watch/add/list), `skillsync_workflow/` (workflow v2: doctor, route, validate, smart-test, checkpoint, env, e2e, claim, ...), `skillsync-ctl` (service `skillsync-watch.service`), `skillsync-windows.ps1`, `tests/`, `docs/`.
- `~/.local/bin/skillsync` and `skillsync-ctl` are symlinks to this repo — editing the source takes effect at once, except for a running watch service.
- Start: `README.md` (documentation map). Details: `docs/architecture.md`, `docs/cli.md`, `docs/manifest.md`, `docs/layers-authoring.md`, `docs/workflow.md`, `docs/development.md`; knowledge: `docs/domain/`, `docs/lessons/`.

## Critical workflow
- Tests (cwd repo): `python3 -m unittest discover -s tests -p 'test_skillsync*.py'`. After changing Python: `python3 -m py_compile skillsync skillsync_workflow/*.py`; after changing `skillsync-ctl`: `bash -n skillsync-ctl`.
- The skill expansion rules (alias `agentflow`→`feature`, companions, tags) exist in TWO places: `skillsync_workflow/cli.py:expand_skills` and `skillsync_workflow/common.py:expand_project_skills`. Change both; `tests/test_skillsync_dispatch.py` guards them.
- Check new `link` behaviour first with `skillsync link <test-project> --dry-run` (recipe in `docs/development.md`). `link`/`instructions`/`push` without a path touch ALL projects from `discover_paths`.
- After changing the generator: `skillsync-ctl restart` (the service runs on the code from the moment it started). For multi-stage library changes: `skillsync-ctl stop`, then `start` — watch links every change automatically.
- Do not hand-edit generated files in projects (`CLAUDE.md`, `AGENTS.md`, `.github/copilot-instructions.md`, `.codex/*`, `.claude/agents|skills`). Change the library and run `skillsync link`; `push` moves local instructions to the library.
- A contract change (skill names, artifact paths, manifest keys, exit codes) = tests + an update of `docs/` + `skills/skillsync/SKILL.md` in the library. The library has a separate git — commit separately.
- The `skillsync: link` task in the projects' `.vscode/tasks.json` points to `~/.local/bin/skillsync`; after moving the repo re-point the symlinks and run `skillsync link` — the generator refreshes the path of its own (generated) task and does not touch others'.
