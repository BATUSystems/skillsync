---
title: 'Liczniki biegu z wyrażeń regularnych kontra zdarzenia adaptera: jedno źródło prawdy dla wyniku, bo zielony bieg bywał BLOCKED'
tags: [skillsync, test-run, adapters, counts, junit, evidence, karma]
paths: ['skillsync_workflow/test_run.py', 'skillsync_workflow/test_events.py', 'skillsync_workflow/quality.py', 'skillsync_workflow/test_adapters/karma_line.py', 'tests/test_skillsync_adapter_web_counts.py']
source: 'prace nad wydaniem 1.8.0: żywe biegi test-run z Vitest, Karma, Playwright, Cypress i ng test; odczyt skillsync_workflow/test_adapters/karma_line.py, quality.py (`_test_counts`, `_failure_count`) i test_run.py (czytnik na żywo)'
verified: 2026-11-19
---
**Objaw:** w żywych biegach rekord `end` miał liczniki niezgodne ze zdarzeniami, które adapter zapisał: Playwright 9 testów zamiast 13, Karma `skipped 11` zamiast 7/2/2, zielony bieg Cypressa `BLOCKED` z powodem „Test count unknown” (bramka kroku odrzuciłaby zielony bieg). Ten sam rozjazd wystąpił wewnątrz samej Karmy: czytnik na żywo, `quality._test_counts` i `_failure_count` liczyły linię `Executed N of M` każdy własnym wyrażeniem (pominięte odejmowane dwa razy, `(N FAILED)` nieczytane, linia logu `Job: Executed 5 of 8 steps` brana za licznik).

**Przyczyna:** liczniki `end` brał stary czytnik podsumowania wyjścia (regex), niezależny od adaptera. Dwa źródła tej samej liczby różniły się dla tego samego biegu (flaky liczony jak porażka, domyślny reporter bez linii testów, format podsumowania spoza regexu). Trzy czytniki Karmy rozjechały się, bo semantyka linii (co jest licznikiem, co znaczy `Executed`) nie miała jednego właściciela.

**Reguła:** jedno źródło prawdy dla wyniku biegu, z oznaczeniem źródła w `end` (`adapter`, `junit`, `regex`). Kompletny strumień adaptera daje liczniki z ostatnich stanów testów; adapter licznikowy daje swoje ostatnie liczniki, gdy nic nie biegnie i suma równa się całości; raport JUnit, jeśli jest, wygrywa i daje listę. Bieg przerwany, ucięty albo bez kompletu zostaje przy regex jako ostatnia deska. Powtórzony identyfikator testu (np. `it.each` z jednym tytułem) to NIE kompletny strumień, bo stany końcowe policzyłyby jeden test zamiast dwóch. Semantyka jednej linii (Karma `Executed`) ma jeden moduł bez zależności od produktu (`test_adapters/karma_line.py`), z którego korzystają wszyscy czytelnicy; nowy czytelnik nie pisze własnego wyrażenia.

**Jak wykryć:** test przez prawdziwy rejestrator na nagraniu strumienia (nie na samym adapterze): status, liczniki i źródło w `end`; test parytetu czytników na tych samych fixturach (czytnik na żywo, `_test_counts`, `_failure_count` i moduł wspólny dają te same liczby, w tym dla linii-pułapek z logu i nazw testów); żywy bieg każdego narzędzia i porównanie `end` ze zdarzeniami `test`.
