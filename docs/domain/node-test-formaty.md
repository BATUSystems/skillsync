---
title: "Format wyjścia `node --test` (TAP czy spec wg wersji Node i TTY) i adaptery `tap`/`node-test`"
tags: [skillsync, test-run, adapters, node, tap, output]
paths: ['skillsync_workflow/test_adapters/tap.py', 'skillsync_workflow/test_adapters/node_test.py', 'skillsync_workflow/test_run.py', 'skillsync_workflow/quality.py', 'tests/test_skillsync_adapter*.py', 'tests/test_skillsync_test_run*.py']
source: 'odczyt skillsync_workflow/test_adapters/tap.py (`TapParser`, `finish`, `_result`, `_flush`), node_test.py (`SpecParser`, `NodeTestAdapter.accepts`, `feed`, `scan`), test_run.py (`adapter_notice`), quality.py (`_test_counts`, gałąź `Bail out!`); pomiary: tests/fixtures/test-runs/node-test-*.jsonl i tap-node*.jsonl (Node 20.18.1, 22.22.0, 24.11.1, Linux)'
verified: 2026-11-18
---
Reguła: `node --test` nie ma jednego formatu wyjścia. Wersja Node i to, czy stdout jest terminalem (TTY), decydują o tym, czy drukuje TAP, czy `spec`: w potoku TAP na Node 20 i 22, `spec` od Node 24; na TTY `spec` (zmierzone na 22 i 24; Node 20 na TTY nie zweryfikowano). Adapter `node-test` nie zmienia polecenia ani środowiska, tylko czyta strumień, który Node już wypisał, i wybiera parser po jego pierwszej istotnej linii (`TAP version N` albo znacznik `▶ ✔ ✖ ﹣`). Nie zweryfikowano na Node 25 i na Windows. Opis użytkowy adapterów i ich reguł: `docs/test-adapters.md`.

Reguły odczytu (skrót; szczegóły w dokumencie użytkowym):
- TAP: grupa (`# Subtest`) nie emituje wyniku, chyba że jest porażką bez porażki potomka; liście idą od razu; id `grupa > podgrupa::nazwa`, duplikat dostaje ` (2)`.
- TAP: `# SKIP` to `skipped`; `# TODO`, także gdy zawiedzie, to `skipped` z notą `todo`.
- TAP: `Bail out!` zatrzymuje emisję i sprawia, że `end` jest False; plan `1..N` w `quality` przy `Bail out!` nie daje liczby testów (przerwany bieg nie daje PASS z planu).
- spec: `ℹ tests N` i `ℹ suites S` muszą spełniać N = liczba wyników − S, inaczej `end` jest False; logi bez znacznika są ignorowane.
- Bieg, który nie dał pewnego `end`, zostaje przy licznikach z podsumowania i raportu.

Słownik:
- **node-test**: adapter `node --test`; **nie oznacza** wszystkich narzędzi Node (`vitest`, `jest`, `npm test` mają własne adaptery albo nie mają żadnego).
- **tap**: adapter TAP wybierany tylko jawnie (`--format tap`); **nie oznacza** raportu TAP z pliku.
