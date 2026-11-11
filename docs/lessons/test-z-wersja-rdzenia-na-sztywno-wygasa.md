---
title: "Test z wersją rdzenia na sztywno wygasa przy podbiciu wersji: wyliczaj ją od generator_version()"
tags: [skillsync, tests, version, release, core-min]
paths: ['tests/test_skillsync_layers_config.py', 'tests/test_skillsync_layers_doctor.py', 'tests/test_skillsync_layers_commands.py']
source: 'commit 37362f8 (naprawa testu test_core_min_above_the_generator_version_is_blocked po podbiciu do 1.1.0)'
verified: 2026-11-11
---
**Objaw:** po podbiciu wersji rdzenia na master padł test, który nie dotykał zmienionego kodu: `test_core_min_above_the_generator_version_is_blocked`.
**Przyczyna:** test zakładał na sztywno, że wersja „wyższa niż generator” to konkretny literał (np. `1.1.0`). Gdy generator doszedł do tej wersji, „wyższa” przestała być wyższa i asercja BLOCKED nie zachodziła.
**Reguła:** test porównujący z wersją generatora wylicza wartości od `layers.generator_version()` (np. `major + 1`, `major + 2`), a nie wpisuje literałów. Literał wolno tylko tam, gdzie wersja jest częścią fikstury niezwiązanej z generatorem (stary rdzeń `0.9.0`, celowo odległe `99.0.0`).
**Jak wykryć:** przed wydaniem `grep -rnE "'[0-9]+\.[0-9]+\.[0-9]+'" tests/` w testach warstw i wersji; po podbiciu wersji pełny bieg testów na gałęzi wydania, zanim powstanie tag.
