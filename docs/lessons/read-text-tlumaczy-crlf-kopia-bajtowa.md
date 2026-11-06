---
title: "Path.read_text tłumaczy CRLF na LF: kopia „bez zmian” musi czytać bajty"
tags: [skillsync, python, encoding, crlf, prototype]
paths: ['skillsync_workflow/prototype.py', 'tests/test_skillsync_prototype.py']
source: ".skillsync/runs/2026-11-03-prototype-cli/1-implementer.md (Odkrycia, sondy: theme.css z CRLF); 3-test-writer.md (fikstura project-theme.css z CRLF; BOM, mutant N12); code-review-2 runda 2 (RC2-4)"
verified: 2026-11-04
---
**Objaw:** plik kopiowany „bez zmian” (zasób silnika albo `theme.css` projektu) różni się od źródła: końce linii CRLF zamieniły się na LF, a przy pliku projektu z BOM znak U+FEFF trafiłby do środka `tokens.css`/`theme.css` (po nagłówku).

**Przyczyna:** `Path.read_text()` i `open(..., 'r')` mają domyślnie tłumaczenie nowych linii (`\r\n` → `\n`); `utf-8` zostawia BOM jako znak U+FEFF.

**Reguła:** czytaj `Path.read_bytes().decode('utf-8')` (zasoby, bez BOM-u: `_read_asset`) albo `.decode('utf-8-sig')` (pliki projektu: `_read_project_text`); zapisuj z `open(..., 'w', newline='\n')` albo przez bajty. Pliki statyczne kopiuj `shutil.copyfile`. Porównania zawartości (`sync --check`) rób na bajtach.

**Jak wykryć:** test z fikstury `tests/fixtures/prototype/css/project-theme.css` (CRLF) oraz test BOM w `tokens_from` i `theme.css` (`tests/test_skillsync_prototype.py`); `grep -n 'read_text' skillsync_workflow/prototype.py` nie powinno zwracać odczytu plików kopiowanych bez zmian.
