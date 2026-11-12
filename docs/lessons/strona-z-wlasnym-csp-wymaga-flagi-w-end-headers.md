---
title: "Strona serwera z własnym CSP i Referrer-Policy: end_headers dubluje nagłówki bez flagi _strict_page"
tags: [skillsync, python, http, csp, security, prototype, server]
paths: ['skillsync_workflow/prototype_server.py', 'tests/test_skillsync_prototype_experts_page.py']
source: skillsync_workflow/prototype_server.py (`PrototypeRequestHandler.end_headers`, `_dispatch`, `_experts_page`, `_csp_hash`), tests/test_skillsync_prototype_experts_page.py, docs/prototypes.md
verified: 2026-11-12
---
**Objaw:** odpowiedź ma dwa nagłówki `Content-Security-Policy` albo dwa `Referrer-Policy` (domyślny i własny); przeglądarka bierze część wspólną polityk, więc strona z hashem skryptu przestaje działać albo działa na węższych zasadach, niż zapisano w kodzie.

**Przyczyna:** `PrototypeRequestHandler.end_headers` dopisuje do każdej odpowiedzi domyślny CSP i `Referrer-Policy`. Trasa, która sama ustawia te nagłówki (strona `/_experts`), dostaje je dwa razy.

**Reguła:** strona z własnymi nagłówkami ustawia `self._strict_page = True` przed wysłaniem, a `end_headers` pomija wtedy domyślne. Flagę zeruj w `finally` (wyjątek w `_send` nie może jej zostawić dla następnej odpowiedzi) i na początku `_dispatch`. Hash CSP licz z tego samego tekstu skryptu i stylu, który idzie w treści (`_csp_hash`), nie wpisuj go ręcznie. Dodając trasę z własnym CSP, sprawdź, że pozostałe trasy (także 405 i 404 tej samej ścieżki) nadal mają domyślne nagłówki.

**Jak wykryć:** `python3 -m unittest discover -s tests -p test_skillsync_prototype_experts_page.py` (klasy z nagłówkami, `StrictFlagExceptionTests`).
