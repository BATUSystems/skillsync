---
title: "Skill feature-explainer: strona dla osoby spoza zespołu, reguły z kodu, publikacja domyślnie plikiem w repo, kontrola check_page.py"
tags: [feature-explainer, skills, ui, docs, explainer, check-page, artifact, exit-codes]
paths: ['library/skills/feature-explainer/**', 'layers/core-en/skills/feature-explainer/**', 'library/workflow/skill-profiles.yaml', 'library/workflow/skill-catalog.yaml', 'tests/test_skillsync_explainer_check.py']
source: 'library/skills/feature-explainer/SKILL.md (Reguły twarde, Kryteria akceptacji strony); scripts/check_page.py:79 (check), :125 (main; kody wyjścia 0/1/2); references/publish-static.md, references/publish-artifact.md; library/workflow/skill-profiles.yaml:7 (profil ui); spec i plan CORE-1170-EXPLAINER (rozstrzygnięcie 9, K3, K4, K9)'
verified: 2026-11-25
---
**Reguła:** skill `feature-explainer` prowadzi przygotowanie jednej strony HTML o jednej funkcji lub ekranie dla osoby spoza zespołu programistów (QA, biznes, wsparcie). Strona odpowiada na pytanie „dlaczego widzę to, co widzę”: reguły są potwierdzone w kodzie (notatka `plik:linia`), przykłady stoją na zrzutach z danych przykładowych i mają podpis „dlaczego”, pytania odbiorcy trafiają do FAQ ze stanem (reguła, konfiguracja, do analizy, błąd). Struktura to dziewięć sekcji ze spisem treści; szkielet to `assets/template.html`.

**Granice:**

- Skill nie startuje sam: proponuje się go jednym zdaniem i czeka na zgodę. Nie służy do dokumentacji API, instrukcji wdrożenia ani analizy błędów (analizę prowadzi kto inny, FAQ przyjmuje tylko jej wynik).
- Gdy dokument i kod się różnią, strona opisuje kod, a rozjazd jest zgłaszany osobno; dokumentu nie poprawia się po cichu.
- Domyślna publikacja to plik w repo `docs/explainers/<slug>/` (`index.html` i obrazy obok, ścieżki względne), w każdym hoście. Prywatna strona w hoście (Artifact) jest opcją na życzenie lub za zgodą, gdy odbiorca nie ma dostępu do repo; właściciel sam ją udostępnia, a agent nie publikuje bez decyzji.
- Publiczny rdzeń zawiera tylko metodę i syntetyczny przykład w szablonie. Dane projektu (nazwy, reguły konkretnej funkcji, zrzuty, adresy opublikowanych stron) zostają w repo projektu.
- Zrzuty wyłącznie z danych przykładowych; zrzutów od odbiorcy z danymi osób nie publikuje się.

**Dlaczego:** strony pisane z pamięci rozjeżdżają się z kodem, a zrzuty z produkcji ujawniają dane osób. Wymóg potwierdzenia reguły w kodzie i danych przykładowych usuwa oba ryzyka; domyślny plik w repo działa bez zależności od hosta i bez udostępniania na zewnątrz.

**Wyjątki i pułapki:**

- `check_page.py [--json] <page.html>` (stdlib, Python 3.11+) sprawdza mechaniczne kryteria strony: brak `data:` w obrazach i `url()`, brak zasobów zewnętrznych (skrypty, style, czcionki, `@import`), istniejące pliki obrazów obok strony, `alt` przy każdym obrazie, `meta viewport`, kotwice spisu treści, tokeny jasne w `:root` i motyw ciemny osiągalny zarówno przez `prefers-color-scheme`, jak i przez `data-theme` (jawny wybór hosta), co najmniej dziewięć sekcji. Kody wyjścia: 0 = PASS, 1 = FAIL (co najmniej jedno znalezisko), 2 = błąd użycia (brak lub nieczytelny plik). Kody znalezisk: `data-uri`, `external-resource`, `missing-image`, `missing-alt`, `external-script`, `no-viewport`, `toc-anchor`, `themes`, `sections`.
- Kontrola jest mechaniczna: nie dowodzi, że reguły zgadzają się z kodem ani że zrzuty nie zawierają danych osób. To zostaje krokiem człowieka i weryfikacją reguł z notatek.
- Droga Artifact nie ma automatycznego testu; zweryfikuj ją ręcznie albo zgłoś jako niesprawdzoną. Adresu opublikowanej strony nie zapisuje się do plików rdzenia.
- Budowa i zrzuty nie mogą zmieniać drzewa roboczego innych sesji: build do katalogu tymczasowego, kopia skryptu zrzutów.
- Skill jest w profilu `ui` (`library/workflow/skill-profiles.yaml`), więc nie jest linkowany domyślnie; angielskie lustro obejmuje pliki Markdown skilla (`layers/core-en/skills/feature-explainer/`), a szablon i skrypt nie są tłumaczone.

**Gdzie w kodzie:** skill `library/skills/feature-explainer/` (SKILL.md, references, assets, scripts), `scripts/check_page.py`, test `tests/test_skillsync_explainer_check.py`, wpis w `library/workflow/skill-catalog.yaml` i `skill-profiles.yaml`.
