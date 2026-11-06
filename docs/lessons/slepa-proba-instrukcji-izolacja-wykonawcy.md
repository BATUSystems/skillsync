---
title: 'Ślepa próba instrukcji: wykonawca widzi więcej niż w produkcji, a wyrocznia dopasowuje się do wyniku'
tags: [workflow, testy, slepa-proba, instrukcje, podagenci, wyrocznia]
paths: ['library/skills/feature/references/retro-quality.md', 'library/skills/feature/references/*.md', 'docs/plans/*']
source: '.skillsync/runs/2026-11-05-retro-jakosciowe: trials-decyzje-1.md (sekcja „Runda 2 prób”), git-procedure-2.log (P1), blind-round2.log, raporty wykonawców prób'
verified: 2026-11-05
---
**Objaw:** ślepa próba procedury opisanej w instrukcji (podagent wykonuje ją na spreparowanym materiale, wyrocznia
ocenia wynik) daje PASS, który słabo przenosi się na prawdziwe sesje: wykonawca zna materiał niedostępny w produkcji,
podgląda pliki prowadzącego albo wyrocznia zostaje dopasowana do tego, co wykonawca faktycznie napisał.

**Przyczyna:** (1) materiał przebiegu leżał obok plików, które w produkcji nie istnieją (zastępnik rozmowy `events.md`
pasował do „raportów `*.md`” i czytał go recenzent, który w produkcji widzi tylko fakty); (2) pliki prowadzącego
(`state.env`, oczekiwane sha) leżały w bazie próby, a zakaz ich czytania był tylko w zleceniu; (3) jeden wykonawca
obsługiwał kilka przypadków po kolei, więc mógł przenosić wnioski; (4) przy porażce poprawiano wyrocznię razem
z procedurą, więc część porażek „znikała” przez poluzowanie wzorca, a nie przez zmianę zachowania.

**Reguła:** w bazie próby trzymaj wyłącznie pliki, które istnieją w produkcji; materiał zastępczy (zdarzenia zamiast
rozmowy) dawaj tylko roli, która w produkcji ma ten kontekst, a pliki prowadzącego (wyrocznia, oczekiwania) trzymaj
poza bazą. Jeden przypadek na świeżego wykonawcę, gdy przypadki dotyczą tych samych reguł. Każdą zmianę wyroczni po
próbie zapisz jako osobną decyzję z powodem (wada wyroczni, wada materiału albo niedookreślenie procedury) i nie
licz takiego przypadku jako dowodu procedury bez ponownej próby na niezmienionej wyroczni.

**Jak wykryć:** wykonawca w raporcie przyznaje się do odczytu spoza listy; porażka wyroczni znika po zmianie
oczekiwań, a nie procedury; raport wykonawcy cytuje treść, której jego rola w produkcji nie dostaje.
