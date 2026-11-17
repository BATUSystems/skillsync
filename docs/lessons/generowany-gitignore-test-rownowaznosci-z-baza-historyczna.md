---
title: 'Generowany plik (.gitignore artefaktów) a test równoważności z bazą historyczną: nowa funkcja dopisująca wiersz wymaga udokumentowanego wyjątku, nie regeneracji skrótów'
tags: [skillsync, tests, gitignore, link, baseline, equivalence]
paths: ['skillsync_workflow/layers.py', 'tests/test_skillsync_layers_equivalence.py', 'tests/test_skillsync_worktree_links.py']
source: 'prace nad wydaniem 1.8.0: test równoważności wyniku `link` z historycznym stanem bazowym po dopisaniu wiersza do generowanego .gitignore'
verified: 2026-11-17
---
**Objaw:** nowa funkcja dopisuje wiersz do generowanego `.gitignore` artefaktów, a test równoważności z bazą historyczną (skróty zapisane przy poprzednich wersjach) robi się czerwony dla każdego projektu bazowego.

**Przyczyna:** test porównuje wynik `link` z zamrożonym stanem. Dopisanie wiersza zmienia wynik z założenia, więc „poprawka” przez regenerację skrótów zatarłaby kontrolę: test przestałby cokolwiek mówić o zgodności wstecznej.

**Reguła:** nowy wiersz generowanego pliku idzie do jawnej, opisanej listy wyjątków testu (co, od jakiej wersji, dlaczego), a skróty bazowe zostają. Zmiana wyniku `link` jest zmianą kontraktu: wpis w CHANGELOG i dokumentacji, nie cicha regeneracja. Plik użytkownika dopisujemy bez zmiany istniejących bajtów.

**Jak wykryć:** czerwony test równoważności po dopisaniu wiersza; przegląd diffu testu: jeśli zmieniły się skróty bazowe, a nie lista wyjątków, to regeneracja zamiast wyjątku.
