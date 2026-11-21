---
title: "Frontmatter skilli (SKILL.md): klucze hostów, lint doctor, czego nie potwierdzono"
tags: [skillsync, skills, frontmatter, doctor, claude-code, copilot, codex]
paths: ['skillsync_workflow/skill_frontmatter.py', 'skillsync_workflow/quality.py', 'tests/test_skillsync_skill_frontmatter.py', 'library/skills/copilot-skills-yaml/SKILL.md']
source: dokumentacja skilli Claude Code (https://code.claude.com/docs/en/skills, "Frontmatter reference", 21.11); skillsync_workflow/skill_frontmatter.py; quality.py:doctor; codex app-server skills/list 0.171.0 (21.11, CLAUDE-0)
verified: 2026-11-21
---
Reguła: frontmatter `SKILL.md` ma klucze trzech hostów. Claude Code: `name`, `description`, `when_to_use`, `argument-hint`, `arguments`, `disable-model-invocation`, `user-invocable`, `allowed-tools`, `disallowed-tools`, `model`, `effort`, `context`, `agent`, `background`, `hooks`, `paths`, `shell`, `metadata`, `license`, `compatibility`. Copilot: `user-invokable`. Host nieustalony: `applyTo` (2 skille biblioteki). `skillsync doctor` zgłasza nieznany klucz, zły typ i złą wartość jako `warning` (nigdy `error`: skille warstw org i personal nie są nasze); test biblioteki rdzenia nie akceptuje żadnej uwagi. Moduł: `skillsync_workflow/skill_frontmatter.py` (czysta, całkowita funkcja `check`).

Dlaczego: Claude Code ignoruje nieznane klucze bez błędu i ostrzeżenia, więc literówka (`argument-hnit`) nigdzie się nie ujawnia. Pisownia `user-invokable` (25 skilli rdzenia, Copilot) jest dla Claude Code nieznana i ignorowana: skill zostaje wywoływalny przez użytkownika (domyślne `user-invocable: true`); obie pisownie są znane i bez uwagi w `doctor` (inaczej stały, nienaprawialny przez projekt szum).

Wyjątki i granice:
- Poza Claude Code (claude.ai, Skills API) dozwolone są tylko `name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`.
- `allowed-tools` to PRE-APPROVAL narzędzi w turze skilla (nie ograniczenie); w skillach projektowych działa też w `-p` w niezaufanym folderze. `context: fork` uruchamia podagenta, który NIE widzi historii rozmowy. `paths` dopasowuje pliki w bieżącym katalogu roboczym. `disable-model-invocation` wyłącza też preładowanie w podagentach i użycie w zadaniach zaplanowanych.
- NIEPOTWIERDZONE w dokumentacji (stan 21.11): czy opis znika z listingu przy `disable-model-invocation`; czy opis skilla z `paths` jest w listingu przed dopasowaniem pliku; czy model/podagent może wywołać skill z flagą narzędziem Skill; zachowanie zaplanowanego promptu (`/loop`) z `/dispatch` przy fladze. Pola `disable-model-invocation`, `paths`, `allowed-tools`, `context: fork` nie są używane w skillach rdzenia do czasu dowodu (CLAUDE-1, decyzje Barcisia 21.11).
- Codex 0.171.0 ładuje skille z nieznanymi kluczami bez błędu; generator czyta z frontmattera tylko `name` i `description`, kopie hostów to symlinki do tego samego `SKILL.md`; `claude plugin validate` nie wykrywa błędnych kluczy skilli (próba 20.11).
