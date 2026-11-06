---
title: "reason w BLOCKED nie zawiera ścieżek ani treści plików: stałe komunikaty także dla OSError i błędów configu"
tags: [skillsync, retro, redaction, privacy, blocked, errors]
paths: ['skillsync_workflow/retro.py', 'skillsync_workflow/retro_scan.py', 'tests/test_skillsync_retro.py']
source: 'retro.py:900-901 (_machine_config), :941-942 (_retro_all), :1232 (_decide_proposal, OSError), :925-926 (_project_entry, zamiana ścieżek); retro_scan.py:639-640 (OSError), :641-642 (ValueError); raporty 1.3t-mutation.log:2 (M02 test_reasons_never_contain_the_base_path), code-review-1.5-review-1.md:13, :32'
verified: 2026-11-06
---
**Objaw:** komunikat `BLOCKED` przy błędzie odczytu pliku albo configu zawiera pełną ścieżkę (np. do configu maszyny albo
do pliku w obcym projekcie) albo fragment treści JSON. Wynik trafia do Skippstera i do raportów, więc wycieka.

**Przyczyna:** `str(error)` z `OSError` i z parsera YAML zawiera ścieżkę albo fragment pliku. Wyjątek zamieniany na
tekst przechodzi do `reason` bez filtra. Zamiana ścieżek po `replace()` to obejście, nie reguła: pomija ścieżki
względne i przy błędzie systemowym nie zawsze się udaje.

**Reguła:**
- `reason` w `BLOCKED` to stały tekst zapisany w kodzie. Dla `OSError`, błędu configu, błędu odczytu rejestru i błędu
  zapisu używaj stałych komunikatów (np. „Błąd odczytu pliku”, „Konfiguracja maszyny jest nieczytelna”).
- Komunikat z `ValueError` wolno przekazać tylko wtedy, gdy sam jest stałym tekstem zbudowanym w kodzie. Wyjątki
  z bibliotek (`yaml`, `json`, `OSError`) nie przechodzą do wyniku.
- Test: `reason` nie zawiera `str(base)`, ścieżki katalogu, ani fragmentu pliku. Dla każdego błędu z listy
  (zły config, `OSError`, zły rejestr, zły przebieg) jest osobna asercja.

**Jak wykryć:**
- `grep -n "str(error)\|str(exc)" skillsync_workflow/retro*.py`: każde miejsce musi przechodzić przez filtr albo mieć
  stały komunikat.
- Test `test_reasons_never_contain_the_base_path` (mutacja M02 w logu 1.3t) musi zabić zmianę na `str(error)`.
- Ręcznie: `retro-decide --proposal` przy zablokowanym pliku w przebiegu: `reason` jest tekstem stałym.
