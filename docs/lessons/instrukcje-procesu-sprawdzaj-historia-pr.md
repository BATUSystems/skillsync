---
title: 'Instrukcja procesu zestarzała się bez sygnału: przed oparciem na niej automatu sprawdź historię PR-ów'
tags: [workflow, proces, pr, azure, agent-flow]
paths: ['library/skills/feature/references/pr-after-approval.md']
source: "Historia PR-ów DEMO (az repos pr list, 2026-10-31); decyzja Barcisia: merge do RC i develop ręczny; plan zadania (poza rdzeniem)"
verified: 2026-10-31
---
**Objaw:** instrukcja projektu opisywała hotfix jako 3 gałęzie i 6 PR-ów z cherry-pickami oraz `--project DEMO`. Ostatnie hotfiksy miały po jednym PR-ze do `master`, propagacja szła ręcznym merge'em, a projekt Azure ma nazwę repozytorium.

**Dlaczego groźne:** agent wykonujący instrukcję dosłownie utworzyłby niepotrzebne gałęzie i PR-y. Ludzie znają praktykę i omijają nieaktualny opis, więc nic tego nie sygnalizuje.

**Co działa:** przed oparciem automatu na opisie procesu porównaj go z ostatnimi przypadkami (`az repos pr list --status all`, `git log --first-parent`, pola zgłoszeń). Rozbieżność pokaż człowiekowi jako pytanie, a dopiero potem popraw instrukcję.

**Pułapka pokrewna:** `git branch -r` pokazuje gałęzie już usunięte z `origin`; stan zdalny daje `git ls-remote --heads origin`.
