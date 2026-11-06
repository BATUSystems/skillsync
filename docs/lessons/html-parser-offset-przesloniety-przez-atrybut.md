---
title: "HTMLParser.reset() ustawia atrybuty lineno i offset: metoda podklasy o tej nazwie jest przesłonięta"
tags: [skillsync, python, html-parser, prototype]
paths: ['skillsync_workflow/prototype_server.py']
source: .skillsync/runs/2026-11-04-prototype-komentarze.karta-2/1.1-1.2-implementer.md (Odkrycia 1); Python 3.11.8 _markupbase.py (reset)
verified: 2026-11-04
---
**Objaw:** `TypeError: 'int' object is not callable` przy wywołaniu własnej metody `offset()` w podklasie `html.parser.HTMLParser`.

**Przyczyna:** `HTMLParser.reset()` (z `_markupbase.ParserBase`) ustawia atrybuty instancji `self.lineno = 1` i `self.offset = 0`, które przesłaniają metodę klasy o tej samej nazwie.

**Reguła:** w podklasach `HTMLParser` nie nazywaj metod ani pól `lineno`, `offset`, `rawdata`, `cdata_elem` i innych atrybutów bazy; używaj prefiksu własnego (w kodzie: `_SourceParser.source_offset`). Pozycję znacznika w źródle bierz z `getpos()` i własnej tabeli początków wierszy.

**Jak wykryć:** `grep -n "def offset\|def lineno" skillsync_workflow/prototype_server.py` nie powinno nic zwrócić; test `tests/test_skillsync_prototype_cli.py` (`ids`, `migrate`) pada na pierwszym parsowanym HTML.
