---
title: "Samotne surogaty z argv, nazw plików i manifestu dają UnicodeEncodeError przy print poza try"
tags: [skillsync, python, unicode, surrogates, cli, json]
paths: ['skillsync_workflow/prototype.py', 'skillsync_workflow/common.py', 'tests/test_skillsync_prototype.py']
source: .skillsync/runs/2026-11-03-prototype-cli/code-review-2-1.md (R1-2), code-review-2-1-decyzje.md (RC2-2), code-review-2-2-decyzje.md (RC2-3); 1-implementer.md; tests/test_skillsync_prototype.py (UnicodeSafetyTests)
verified: 2026-11-04
---
**Objaw:** komenda kończy się Tracebackiem `UnicodeEncodeError` i exit 1 zamiast JSON-a `BLOCKED` (exit 2), gdy argument, nazwa pliku, pole JSON albo YAML manifestu zawiera samotny surogat (np. `"title": "\ud800"` w `prototype.json`, bajt `\xff` w `--requirements`, `guidelines: "x\ud800.md"`).

**Przyczyna:** Python dekoduje argv i nazwy plików przez `surrogateescape` (bajty spoza UTF-8 → `\udcxx`), `json.loads` i YAML przepuszczają `\ud800`; dopiero `print`/zapis UTF-8 w `common.py:emit`, poza blokiem `try` handlera, rzuca wyjątek.

**Reguła:** na granicy wyjścia przepuść cały raport przez `safe_report` (surogaty jako literalne `\udcxx`, `safe_text`), a wartości, które trafią do pliku, sprawdzaj `encodable` przy odczycie (pole z surogatem = wpis `error`, nie zapis). Komunikaty z danymi użytkownika buduj przez `safe_text` albo `repr`. Taka ścieżka w JSON nie odwzorowuje się z powrotem na plik. Nie zakładaj, że druga warstwa wystarczy: każdą warstwę osobno dowodź mutantem.

**Jak wykryć:** `UnicodeSafetyTests` (`tests/test_skillsync_prototype.py`: `prototype.json` z `\ud800`, `--requirements` z bajtem `\xff`, manifest z `\udcff` w `guidelines` i `root`); ręcznie `skillsync prototype list <projekt>` na takim `prototype.json` nie może dać `Traceback`.
