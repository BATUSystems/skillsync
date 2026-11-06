---
title: "html.parser w Pythonie 3.11 nie traktuje textarea jako RCDATA: liczenie elementów zlicza je w środku"
tags: [skillsync, python, html-parser, prototype, tests]
paths: ['skillsync_workflow/prototype.py', 'tests/test_skillsync_prototype.py']
source: .skillsync/runs/2026-11-03-prototype-cli/3-test-writer.md (Odkrycia, Luki; obserwacja na Pythonie 3.11.8)
verified: 2026-11-04
---
**Objaw:** `count_screens` liczy `<section class="screen">` wewnątrz `<textarea>` (obserwacja test-writera), choć przeglądarka traktuje tę zawartość jako tekst. Ten sam parser stoi za `stylesheet_links`, więc analogiczny wpływ na `<link>` jest prawdopodobny, ale nie sprawdzony.

**Przyczyna:** `html.parser.HTMLParser` w Pythonie 3.11.8 nie traktuje `<textarea>` jako RCDATA (HTML5), więc zagnieżdżone znaczniki są parsowane jak zwykłe. Zachowanie zależy od wersji Pythona (obserwowane tylko na 3.11.8; nowsze wersje nie były sprawdzane).

**Reguła:** nie pisz testów ani asercji zależnych od tego, jak `html.parser` tnie zawartość `textarea`; liczby `screens` i ostrzeżenia o `<link>` są heurystyką tokenów, nie renderowaniem. Gdy potrzebna ścisła zgodność z przeglądarką, potrzebny jest inny parser, nie łatka w teście.

**Jak wykryć:** `python3 -c "from html.parser import HTMLParser as P; ..."` z `<textarea><section class=screen></textarea>` i `handle_starttag`; w `tests/test_skillsync_prototype.py` brak testów tego przypadku (celowo pominięte jako zależne od wersji).
