---
title: 'Instrukcja bazowa ma budżet 8192 B: nowa reguła rdzenia wymusza cięcie, więc zaplanuj miejsce w akcie I'
tags: [workflow, instrukcje, doctor, budzet, review]
paths: ['library/instructions/_base.md', 'library/workflow/defaults.yaml', 'tests/test_skillsync_library_smoke.py']
source: 'FEAT-DEMO (rdzeń 1.0.3): F3, dwie rundy pętli kod (blocker, 2 majory); retro S1/S4'
verified: 2026-11-11
---
**Objaw:** podumysł dopisał regułę do `_base.md` i `doctor` zgłosił „Instruction budget exceeded” (`library_smoke` FAIL; zapas wyjściowy 92 B). Żeby się zmieścić, skracał cudze zdania: znikła reguła ogólna o osobnym worktree, reguła pierwszego commita dla planu bez „GO plan”, ścieżka pliku `retro-quality.md` i słowo „surowych”. Recenzent znalazł blocker (granica zgody), potem dwa majory; poprawki zajęły dwie rundy.

**Przyczyna:** limit `base_max_bytes: 8192` jest wymuszany przez `doctor`, a plan i spec uznały go za „niewymuszany” i zakładały pełne wyliczenie nowej reguły w rdzeniu. Zlecenie implementerowi zawierało zakaz zmiany cudzych zdań, więc dwa ograniczenia się wykluczały, a podumysł rozstrzygnął to sam.

**Reguła:** zmiana `instructions/_base.md` ma w raporcie spójności speca zapas bajtów i miejsce każdej nowej reguły. Szczegóły reguły idą do referencji skilla z krótkim odesłaniem z rdzenia; cudzych zdań nie zagęszcza się, a brak miejsca to pytanie przed zatwierdzeniem. Zlecenie dla `implementer` mówi, co robić przy konflikcie ograniczeń: przerwać i zgłosić. Po każdej zmianie `wc -c library/instructions/_base.md` i `test_skillsync_library_smoke.py`.

**Jak wykryć:** `doctor`/`test_skillsync_library_smoke` z „Instruction budget exceeded”; diff `_base.md` względem `main` z usuniętymi zdaniami niezwiązanymi z zadaniem.
