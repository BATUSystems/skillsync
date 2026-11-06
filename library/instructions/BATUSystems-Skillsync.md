# Skillsync — generator artefaktów workflow (repo-specific)
- To **kod generatora** `skillsync` (Python 3 + PyYAML), nie biblioteka. Biblioteka skilli, ról i instrukcji to osobne repo `~/.local/copilot-library` (CopilotSkills); manifest projektu: `<projekt>/.github/.skillsync.yaml`; config maszyny: `~/.config/skillsync/config.yaml` (override `SKILLSYNC_CONFIG`).
- Układ: `skillsync` (wrapper) i `skillsync_workflow/cli.py` (komendy link/instructions/push/pull/watch/add/list), `skillsync_workflow/` (workflow v2: doctor, route, validate, smart-test, checkpoint, env, e2e, claim, ...), `skillsync-ctl` (usługa `skillsync-watch.service`), `skillsync-windows.ps1`, `tests/`, `docs/`.
- `~/.local/bin/skillsync` i `skillsync-ctl` to symlinki do tego repo — edycja źródła działa od razu, z wyjątkiem uruchomionej usługi watch.
- Start: `README.md` (mapa dokumentacji). Szczegóły: `docs/architecture.md`, `docs/cli.md`, `docs/manifest.md`, `docs/library.md`, `docs/workflow.md`, `docs/development.md`; wiedza: `docs/domain/`, `docs/lessons/`.

## Krytyczne workflow
- Testy (cwd repo): `python3 -m unittest discover -s tests -p 'test_skillsync*.py'`. Po zmianie Pythona: `python3 -m py_compile skillsync skillsync_workflow/*.py`; po zmianie `skillsync-ctl`: `bash -n skillsync-ctl`.
- Reguły rozwijania skilli (alias `agentflow`→`feature`, towarzysze, tagi) istnieją w DWÓCH miejscach: `skillsync_workflow/cli.py:expand_skills` i `skillsync_workflow/common.py:expand_project_skills`. Zmieniaj obie; pilnują ich `tests/test_skillsync_dispatch.py`.
- Nowe zachowanie `link` sprawdzaj najpierw `skillsync link <projekt-testowy> --dry-run` (receptura w `docs/development.md`). `link`/`instructions`/`push` bez ścieżki dotykają WSZYSTKICH projektów z `discover_paths`.
- Po zmianie generatora: `skillsync-ctl restart` (usługa działa na kodzie z chwili startu). Przy wieloetapowych zmianach biblioteki: `skillsync-ctl stop`, potem `start` — watch linkuje automatycznie każdą zmianę.
- Nie edytuj ręcznie plików generowanych w projektach (`CLAUDE.md`, `AGENTS.md`, `.github/copilot-instructions.md`, `.codex/*`, `.claude/agents|skills`). Zmieniaj bibliotekę i uruchom `skillsync link`; `push` przenosi lokalne instrukcje do biblioteki.
- Zmiana kontraktu (nazwy skilli, ścieżki artefaktów, klucze manifestu, kody wyjścia) = testy + aktualizacja `docs/` + `skills/skillsync/SKILL.md` w bibliotece. Biblioteka ma osobny git — commit osobno.
- Task `skillsync: link` w `.vscode/tasks.json` projektów wskazuje `~/.local/bin/skillsync`; po przeniesieniu repo przepnij symlinki i wykonaj `skillsync link` — generator odświeża ścieżkę własnego (generowanego) taska, cudzych nie rusza.
