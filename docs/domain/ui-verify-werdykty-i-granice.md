---
title: 'Weryfikacja UI (ui-verify): werdykty, kody wyjścia i granice dowodu'
tags: [skillsync, ui-verify, verification, exit-codes, testing]
paths: ['library/skills/ui-verify/**', 'tests/ui_verify/**', 'tests/test_skillsync_ui_verify.py']
source: plan zadania (poza rdzeniem)
verified: 2026-11-22
---
Reguła: wynik weryfikacji wyglądu ma trzy stany dowodu. PASS (kod 0) tylko wtedy, gdy każda wymagana pozycja ma dowód. FAIL (kod 1) tylko wtedy, gdy weryfikacja wykryła różnicę. Brak dowodu (brak asercji pozytywnej, brak elementu, brak manifestu, brak zależności lub przeglądarki) to UNVERIFIED albo NOT_RUN, kod 3. Błąd narzędzia (konfiguracja, padnięcie Chrome, timeout, nieobsłużony wyjątek) to kod 2, nigdy 1.

Dlaczego: kod 1 w bramce znaczy „wykryto różnicę”. Gdyby błąd narzędzia dawał 1, wyglądałby jak regresja. Gdyby brak dowodu dawał 0, wyglądałby jak zgodność. Zrzut bez asercji pozytywnej (strona mogła się nie wyrenderować) nie dowodzi zgodności.

Wyjątki i przykłady:
- `diff` daje SAME tylko wtedy, gdy oba zrzuty mają asercję PASS i równe wymiary. Brak `manifest.json` albo pusty zestaw to UNVERIFIED (kod 3).
- `measure` z elementem nieobecnym po jednej stronie daje UNVERIFIED (kod 3), nie PASS.
- Łączenie: FAIL wygrywa; PASS tylko przy niepustej liście samych PASS.
- Kształt SKIPPED (`box-mismatch`) nie wchodzi do łączenia. Pomiar PASS przy pominiętym kształcie daje PASS bez dowodu pikselowego. To zatwierdzona luka opisana w `references/boundaries.md` skilla.
- Brak zależności, przeglądarki lub sieci to kod 3 z instrukcją, nie FAIL.

Gdzie w kodzie:
- `library/skills/ui-verify/scripts/lib/verdict.mjs:3-22` (EXIT, combine, exitFor)
- `library/skills/ui-verify/scripts/lib/measure-core.mjs:206-257` (łączenie pomiaru i kształtu, SKIPPED, element nieobecny)
- `library/skills/ui-verify/scripts/lib/diff-core.mjs:16-58` (SAME tylko przy PASS po obu stronach, no-manifest, no-shots)
- `library/skills/ui-verify/scripts/lib/capture-core.mjs:242-266` (NONE → UNVERIFIED, FAIL asercji)
- `library/skills/ui-verify/scripts/pixel-diff.mjs:192` (kod wyjścia z werdyktu)
- testy: `tests/ui_verify/verdict.test.mjs`, `tests/test_skillsync_ui_verify.py`
