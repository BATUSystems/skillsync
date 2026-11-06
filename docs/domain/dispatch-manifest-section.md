---
title: Sekcja dispatch manifestu
tags: [skillsync, dispatch, manifest]
paths: ['skillsync_workflow/dispatch.py', 'skillsync_workflow/quality.py']
source: odczyt skillsync_workflow/dispatch.py:validate_dispatch i quality.py:doctor; tests/test_skillsync_dispatch.py 2026-10-02
verified: 2026-10-21
---
Reguła: sekcja `dispatch:` w `.github/.skillsync.yaml` jest opcjonalna i konfiguruje wyłącznie skill `dispatch` (dyspozytor równoległych sesji `feature`). Generator jej **nie używa** do artefaktów;
jedyną jego rolą jest walidacja w `doctor`: błąd typu/zakresu → `error` (FAIL), nieznany klucz → `warning`; brak sekcji = poprawny.
Znane klucze: `max_parallel`, `worktree_root`, `setup`, `isolated_resources`, `leased_resources`, `owned_files`, `additive_files`, `id_registries`, `guards` (`min_free_disk_gb`, `max_load`).

Dlaczego: błędna konfiguracja dyspozytora (np. port poza 1–65535, duplikat nazwy zasobu) ma wychodzić na jaw statycznie, zanim karty zaczną pracę.

Wyjątki i przykłady: `leased_resources[].name` musi być unikalne; `ports` — liczby całkowite 1–65535; `isolated_resources.env` to mapa string→string. Skutek uboczny po stronie generatora: `.skillsync/` w `.gitignore` projektu z `feature`.
`dispatch` nie jest już towarzyszem `feature`: dołącza go profil `workflow-extended` (albo jawny wpis w `skills:`), a `doctor` wymaga jego adapterów `library/skills/dispatch/references/claude.md` i `library/skills/dispatch/references/codex.md`, gdy host jest włączony.

Gdzie w kodzie: `skillsync_workflow/dispatch.py:validate_dispatch`, `skillsync_workflow/quality.py:doctor`.
