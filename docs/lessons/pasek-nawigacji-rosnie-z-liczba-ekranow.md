---
title: "Pasek nawigacji rośnie z liczbą ekranów; stały scroll-margin chowa tytuł"
tags: [prototype, css, layout, navigation, scroll-margin, responsive, layout-probe]
paths: ['tests/fixtures/prototype/engine/**', 'tests/test_skillsync_prototype_engine.py']
source: '<projekt>/.skillsync/runs/prototyp-plan-zajec/screens/11-ZNALEZISKO-dark-375-pasek-70proc.png (raport plan-zajec); docs/plans/2026-11-04-prototype-poprawki.md (E3, sekcja Projekt rozwiązania); pomiary akceptacyjne kroku 8 (headless Chrome, CDP, 2026-11-04: silnik 801ab23 i worktree biblioteki, 28 sekcji i 1 sekcja na tej samej stronie); .skillsync/runs/2026-11-04-prototype-poprawki.Q1-prototype-poprawki/lib-1-2-3-5-implementer.md (krok 2, Odkrycie 1)'
verified: 2026-11-04
---
**Objaw:** wysokość `.doc-toolbar` zależy od liczby ekranów i od szerokości okna. Przed poprawką: 1440 px, 28 ekranów 209 px, 1 ekran 59 px; 900 px 243 px; 375×812 px 567 px (70% wysokości okna). Po skoku linkiem `#sN` albo wejściu z adresem `…/#sN` tytuł `h2` ekranu leżał 80 px pod paskiem o dolnej krawędzi 209 px, czyli był schowany.

**Przyczyna:** trzy niezależne mechanizmy:
1. `.doc-toolbar` z `flex-wrap`: liczba wierszy zależy od szerokości linków w `.screen-nav`, czyli od liczby ekranów i od szerokości okna.
2. Klasyczny pasek przewijania nawigacji pojawia się tylko, gdy linki się nie mieszczą, i zmienia wysokość paska.
3. Stały `scroll-margin-top` (np. 4rem) nie nadąża za zmienną wysokością paska, więc tytuł wchodzi pod pasek.

**Reguła:**
- Układ paska to CSS grid z nazwanymi obszarami, a liczba wierszy wynika z obszarów, nie z szerokości treści. Wąsko: jedna kolumna, obszary `"title" "nav" "tools"`. Szeroko (od 48.0625rem): `"title tools" "nav nav"`. Dzieci paska: `strong` (tytuł), `nav.screen-nav`, `.proto-toolbar`.
- Nawigacja ekranów: `flex-wrap: nowrap; overflow-x: scroll; overflow-y: hidden`. Tor przewijania jest zarezerwowany zawsze, więc wysokość nie zmienia się przy 1 ekranie. Przełączniki `.proto-toolbar`: `overflow-x: auto`, przyciski `flex: none`.
- W siatce nie używaj `margin-left: auto` na elemencie z przyciskami `flex: none`: szerokość zwęża się do max-content i przełączniki nie przewijają się. Przyczyna to analiza CSS Grid, nie osobny pomiar.
- Wysokość mierz `ResizeObserver` na `.doc-toolbar` (zapas: `resize`), wartość `offsetHeight` (nie `contentRect`, który pomija padding i obramowanie) zapisuj do `--proto-toolbar-height` na `<html>`. Ekrany: `.screen { scroll-margin-top: calc(var(--proto-toolbar-height, 4rem) + 1rem) }`. Nie wpisuj stałego `scroll-margin`.
- Znane ograniczenie: późna zmiana wysokości (np. doładowanie fontu) aktualizuje zmienną, ale nie przewija ponownie.

**Jak wykryć:**
- Sonda `library/skills/prototype/scripts/layout-probe.js`: `await layoutProbe({target: 'sN'})` zwraca wysokość paska (`toolbar.height`), pozycję `h2` po przewinięciu do `#sN` (`target`) i `compareSingle` (wysokość przy 28 sekcjach i po usunięciu pozostałych na tej samej stronie). `diff` musi wynosić 0 px; potem przeładować stronę.
- Ręcznie: `document.querySelector('.doc-toolbar').offsetHeight` przed i po usunięciu sekcji, plus `getBoundingClientRect().bottom` paska i `top` `h2` po `location.hash = '#sN'`.
- Pomiary wykonuj na klasycznych paskach przewijania (headless Chrome na Linuksie); nakładkowe paski dają wynik inny.
