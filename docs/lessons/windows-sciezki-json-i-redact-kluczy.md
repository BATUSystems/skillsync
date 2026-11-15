---
title: 'Windows: ścieżki w JSON przez as_posix, a redact nie może przemianowywać kluczy krótkim sekretem z env'
tags: [skillsync, windows, json, redact, sekrety, ci]
paths: ['skillsync_workflow/common.py', 'skillsync_workflow/delivery.py', 'skillsync_workflow/status.py', 'skillsync_workflow/runs.py', 'skillsync_workflow/e2e.py', 'skillsync_workflow/catalog.py', 'tests/test_skillsync_workflow.py']
source: 'WIN-1 (1.0.1); CI windows-latest 3.12: run 42534245564 czerwony (123F/46E, klucz `root` zamaskowany), run 42536817942 po poprawce 119F/44E bez tych przypadków; review D113 tej paczki'
verified: 2026-11-09
---
**Objaw:** na Windows 3.12 `plan archive` (BLOCKED), `layers list` i `prototype list` gubiły klucze `root` i `composed_root` (w JSON stał `[REDACTED]`), a klucze `path` w wyjściach JSON miały `\`.

**Przyczyna:** (1) `str(path.relative_to(root))` daje natywny separator. (2) `redact` podmieniał sekrety ze zmiennych środowiska (nazwa z token/secret/password/...) także w kluczach słowników; runner Windows ma taką zmienną z wartością `root`.

**Reguła:** ścieżki w wyjściach JSON i w zapisywanym stanie idą przez `.as_posix()`. W `redact` podmiana sekretów z env (klucze i wartości) działa tylko dla sekretów od 8 znaków (`SECRET_MIN_LENGTH`; od 1.5.0, wcześniej wartości od 4); maskowanie wzorcowe (adresy z hasłem, `Authorization`, `password=`) nie ma progu. Każde nowe wywołanie `redact` na kluczu przekazuje `True`. Ryzyko rezydualne: sekret z env krótszy niż 8 znaków (4–7) nie jest maskowany nigdzie, ani w kluczach, ani w tekście; ochronę zapewniają wtedy tylko wzorce. Wartości z podciągiem krótkiego sekretu nie są już przekłamywane (na runnerze Windows tekst z `root` zostaje). Granicę pilnują testy `test_redact_ignores_a_short_env_secret_in_keys_and_values`, `test_redact_text_threshold_is_eight_characters`, `test_redact_key_threshold_is_eight_characters`, `test_redact_still_masks_pattern_based_secrets_whatever_the_length`, `test_redact_masks_a_long_env_secret_used_as_key_and_as_value` i `test_delivery_scrub_keeps_structural_keys_like_redact`.

**Jak wykryć:** `FAILED` na joby windows z `'root' not found in {... '[REDACTED]': ...}`; grep `str(.*relative_to` w kodzie, który trafia do JSON.
