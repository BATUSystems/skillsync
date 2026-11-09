---
title: 'Windows: ścieżki w JSON przez as_posix, a redact nie może przemianowywać kluczy krótkim sekretem z env'
tags: [skillsync, windows, json, redact, sekrety, ci]
paths: ['skillsync_workflow/common.py', 'skillsync_workflow/delivery.py', 'skillsync_workflow/status.py', 'skillsync_workflow/runs.py', 'skillsync_workflow/e2e.py', 'skillsync_workflow/catalog.py', 'tests/test_skillsync_workflow.py']
source: 'WIN-1 (1.0.1); CI windows-latest 3.12: run 42534245564 czerwony (123F/46E, klucz `root` zamaskowany), run 42536817942 po poprawce 119F/44E bez tych przypadków; review D113 tej paczki'
verified: 2026-11-09
---
**Objaw:** na Windows 3.12 `plan archive` (BLOCKED), `layers list` i `prototype list` gubiły klucze `root` i `composed_root` (w JSON stał `[REDACTED]`), a klucze `path` w wyjściach JSON miały `\`.

**Przyczyna:** (1) `str(path.relative_to(root))` daje natywny separator. (2) `redact` podmieniał sekrety ze zmiennych środowiska (nazwa z token/secret/password/...) także w kluczach słowników; runner Windows ma taką zmienną z wartością `root`.

**Reguła:** ścieżki w wyjściach JSON i w zapisywanym stanie idą przez `.as_posix()`. W `redact` klucze dostają podmianę z env tylko dla sekretów od 8 znaków (`_key=True`), wartości od 4. Każde nowe wywołanie `redact` na kluczu przekazuje `True`. Ryzyko rezydualne: sekret 4–7 znaków użyty jako klucz nie jest maskowany; w repo nie ma słownika indeksowanego sekretem. Wartości zawierające krótki sekret jako podciąg są nadal przekłamywane (na runnerze Windows tekst z podciągiem `root` staje się `[REDACTED]`, np. komunikat `prototype.root`); to osobny, otwarty temat WIN-5. Granicę pilnują testy `redact_keeps_structural_keys_when_a_short_env_secret_equals_one`, `redact_key_threshold`, `redact_masks_a_long_env_secret_used_as_key_and_as_value` i `delivery_scrub_keeps_structural_keys`.

**Jak wykryć:** `FAILED` na joby windows z `'root' not found in {... '[REDACTED]': ...}`; grep `str(.*relative_to` w kodzie, który trafia do JSON.
