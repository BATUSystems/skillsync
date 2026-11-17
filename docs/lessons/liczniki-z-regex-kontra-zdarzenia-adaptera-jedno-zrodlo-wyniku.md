---
title: 'Liczniki biegu z wyrażeń regularnych kontra zdarzenia adaptera: jedno źródło prawdy dla wyniku, bo zielony bieg bywał BLOCKED'
tags: [skillsync, test-run, adapters, counts, junit, evidence]
paths: ['skillsync_workflow/test_run.py', 'skillsync_workflow/test_events.py', 'tests/test_skillsync_adapter_web_counts.py']
source: 'prace nad wydaniem 1.8.0: żywe biegi test-run z Vitest, Karma, Playwright, Cypress i ng test'
verified: 2026-11-17
---
**Objaw:** w żywych biegach rekord `end` miał liczniki niezgodne ze zdarzeniami, które adapter zapisał: Playwright 9 testów zamiast 13, Karma `skipped 11` zamiast 7/2/2, zielony bieg Cypressa `BLOCKED` z powodem „Test count unknown” (bramka kroku odrzuciłaby zielony bieg).

**Przyczyna:** liczniki `end` brał stary czytnik podsumowania wyjścia (regex), niezależny od adaptera. Dwa źródła tej samej liczby różniły się dla tego samego biegu (flaky liczony jak porażka, domyślny reporter bez linii testów, format podsumowania spoza regexu).

**Reguła:** jedno źródło prawdy dla wyniku biegu, z oznaczeniem źródła w `end` (`adapter`, `junit`, `regex`). Kompletny strumień adaptera daje liczniki z ostatnich stanów testów; adapter licznikowy daje swoje ostatnie liczniki, gdy nic nie biegnie i suma równa się całości; raport JUnit, jeśli jest, wygrywa i daje listę. Bieg przerwany, ucięty albo bez kompletu zostaje przy regex jako ostatnia deska. Powtórzony identyfikator testu (np. `it.each` z jednym tytułem) to NIE kompletny strumień, bo stany końcowe policzyłyby jeden test zamiast dwóch.

**Jak wykryć:** test przez prawdziwy rejestrator na nagraniu strumienia (nie na samym adapterze): status, liczniki i źródło w `end`; żywy bieg każdego narzędzia i porównanie `end` ze zdarzeniami `test`.
