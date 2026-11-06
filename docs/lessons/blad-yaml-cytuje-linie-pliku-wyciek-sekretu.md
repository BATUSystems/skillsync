---
title: "Błąd PyYAML cytuje linię pliku: syntax error w configu z hasłem ujawnia sekret, a redact w emit tego nie łapie"
tags: [skillsync, python, yaml, secrets, redaction, config, prototype]
paths: ['skillsync_workflow/prototype.py', 'skillsync_workflow/common.py', 'tests/test_skillsync_prototype_cli.py']
source: .skillsync/runs/2026-11-04-prototype-komentarze.karta-2/1.3-implementer.md (Odkrycia 1-2), 1.6b-test-writer.md (Odkrycia 2, mutacja M4); PyYAML error.py Mark.get_snippet; common.py emit/redact
verified: 2026-11-04
---
**Objaw:** komunikat `yaml.MarkedYAMLError` zawiera fragment wiersza pliku ze znacznikiem `^`. Błąd składni w `~/.config/skillsync/config.yaml` z `prototype.default_password` trafiłby do JSON-a `BLOCKED` razem z hasłem.

**Przyczyna:** `Mark.get_snippet` cytuje źródło. `common.emit` przepuszcza raport przez `redact`, ale ta funkcja maskuje tylko znane wzorce sekretów (np. `password: wartość`, tokeny), nie dowolną wartość; hasło pod innym kluczem (`default_password: …` w cytacie w innej postaci) przechodzi.

**Reguła:** ochrona sekretu jest **u źródła**: kod czytający config z sekretem łapie wyjątek YAML i zgłasza komunikat ogólny bez cytatu (`raise … from None`, `prototype.py:default_password`). Nie polegaj na `redact`. Dotyczy każdego czytania pliku z sekretami przez `yaml.safe_load`. Hasło domyślne nie trafia też do stdout/stderr żadnej komendy; `UserTests` sprawdza to w `tearDown` dla każdego wywołania i ma przypadek kontrolny, że detektor potrafi wykryć wyciek.

**Jak wykryć:** `python3 -m unittest tests.test_skillsync_prototype_cli.UserTests` (test `yaml_error_in_the_config_does_not_quote_the_file`; sekret umieszczony pod kluczem innym niż `password`, bo ten `redact` maskuje).
