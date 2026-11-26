---
title: "Kontrola motywu prototypów (theme_check.py): jedno źródło, konfiguracja danymi, UNVERIFIED zamiast pominięć"
tags: [prototype, theme, tokens, dark-mode, contrast, check, config, exit-codes]
paths: ['library/skills/prototype/scripts/theme_check.py', 'library/skills/prototype/scripts/css_tokens.py', 'library/skills/prototype/scripts/theme_colors.py', 'library/skills/prototype/scripts/theme_proto.py', 'library/skills/prototype/scripts/check_tokens.py', 'tests/test_skillsync_theme_check.py', 'tests/test_skillsync_css_tokens.py']
source: 'library/skills/prototype/scripts/theme_check.py:176 (validate_config), :493 (check_tokens), :549 (check_dark), :759 (check_contrast), :824 (check_prototype), :961 (run); css_tokens.py:22-23 (selektory bloków), :269 (parse_theme); theme_colors.py (ColorFormat, MAX_ALIAS, próg 0,04045 w luminance); theme_proto.py:75 (decl_violations); defekty D-1..D-6 i decyzje: spec CORE-1140-T4 rewizja 3 (sekcje Defekty, Rozstrzygnięcia R1-R7, Interfejs); definicje bloków zgodne z extract_tokens silnika (skillsync_workflow/prototype.py), zgodność testowana w tests/test_skillsync_css_tokens.py'
verified: 2026-11-26
---
**Reguła:** kontrola motywu prototypów jest jedna, w rdzeniu skilla `prototype` (`scripts/theme_check.py`). Co sprawdzać, opisuje konfiguracja JSON (`version: 1`) podana w `--config` albo jako `run(config, project, skill_root)`. Projekt lub warstwa wywołuje ją z własną konfiguracją; część specyficzna dla projektu (mapa tokenów, generator, odstępstwa, kopie fontów) zostaje u niego. Pełny opis: `library/skills/prototype/references/theme-check.md`.

**Dlaczego jedno źródło:** trzy kopie tej samej kontroli (strażnik skilla, kopia w warstwie, skrypt projektowy) miały własne parsery CSS i własne wyszukiwanie listy wymaganych tokenów. Poprawka w jednym miejscu nie docierała do pozostałych. Parser jest wydzielony do `css_tokens.py` i używany przez `check_tokens.py` (strażnik) i `theme_check.py`.

**Defekty, które uzasadniają kształt kontroli** (do naprawy w rdzeniu, nie do powielania):

- D-1: token wymagany, zdefiniowany tylko w bloku ciemnym, był uznany za zdefiniowany (regex po całym pliku). Naprawa: pokrycie liczy tylko jasny blok, a token tylko w ciemnym to FAIL.
- D-2: brak pliku listy wymaganych tokenów po cichu pomijał kontrolę pokrycia. Naprawa: brak listy to kod 2, a klucz `tokens.required` jest obowiązkowy (`null` wyłącza pokrycie jawnie, z WARN).
- D-3: wyszukiwanie listy przez katalog użytkownika działało tylko na jednym systemie. Naprawa: brak zgadywania ścieżek (zob. niżej).
- D-4: parser brał tylko pierwszy blok `:root` do pierwszego `}`; drugi `:root`, `@theme` i zagnieżdżenia były niewidoczne. Naprawa: parser z zagnieżdżeniami i `@theme` (zob. definicje).
- D-5: konwersja koloru rzucała wyjątek dla każdego formatu poza `#rrggbb`, a wyjątek kończył się kodem 1 jak FAIL. Próg 0,03928 zamiast 0,04045. Naprawa: obsługa formatów, UNVERIFIED zamiast wyjątku, jeden próg 0,04045.
- D-6: deklaracja `--x:` w atrybucie `style` jednego elementu uznawana była za zdefiniowaną lokalnie dla wszystkich zasobów, więc użycie tokenu gdzie indziej bez definicji mogło przejść. Naprawa w `check_tokens.py` z nowymi fiksturami; wyniki na istniejących fiksturach bez zmian.
- D-7 (silnik, `sync --check` w `skillsync_workflow/prototype.py`) miał semantykę D-1; od 1.15.0 `tokens.css` liczy tylko definicję w jasnym bloku, jak ta kontrola (zob. `docs/prototypes.md`).

**Definicje bloków wg silnika** (nie wg intuicji): jasny blok to `:root` (także na liście po przecinku), `@theme` i `@theme inline`; może być w `@layer`, nie w `@media`, `@supports` ani innym bloku. Ciemny blok to dokładnie `.dark`, `[data-theme="dark"]` albo `:root[data-theme="dark"]`. Selektor ze słowem `dark` w innym znaczeniu (`.card-dark`) nie jest ciemnym blokiem. `@media (prefers-color-scheme: dark)` jest rozpoznawany osobno: silnik go pomija z ostrzeżeniem, a kontrola używa go w `dark` i `forbid`.

**Tryby `dark`:** `separate` (bloki ciemne mają te same tokeny i wartości; brak bloku to UNVERIFIED), `mirror` (blok ciemny identyczny z jasnym; uwaga: to inne znaczenie „ciemny wyłączony” niż klucz manifestu `prototype.dark_theme: false`: plik tokenów bez bloku ciemnego wymaga `none`; odpowiada `prototype.dark_theme: false`), `none` (blok ciemny nie może istnieć). Różne projekty mają uzasadnione, przeciwne decyzje o ciemnym motywie, więc tryb jest danymi konfiguracji, a nie stałą reguły.

**Kontrast:** liczony tylko z jasnego bloku. Para przechodząca w jasnym motywie, a padająca w ciemnym, daje PASS. Kontrast ciemnego motywu wymaga osobnego sprawdzenia. Formaty `oklch()`, `oklab()`, `lab()`, `lch()` są konwertowane do sRGB z flagą `outOfGamut`; `color()`, `color-mix()`, `hwb()` i pozostałe to UNVERIFIED.

**Decyzje:**

- UNVERIFIED zamiast pominięcia: pusta konfiguracja, zero plików, zero katalogów prototypu, brak bloków ciemnych w `separate`, brak pasujących plików w `include`, format koloru spoza listy. Próżnia nie jest dowodem. Kod 3 (UNVERIFIED bez FAIL) ma to samo znaczenie co w `docs/domain/statuses-and-exit-codes.md`.
- Brak zgadywania ścieżek po systemie: `--skill-root` jest jawny, a bez niego skille szukane są w projekcie w kolejności `.claude/skills`, `.agents/skills`, `.github/skills`. Skrypt nie liczy katalogu skilli przez `resolve()` własnego pliku ani z katalogów użytkownika. Brak skilla to kod 2.
- Ścieżki projektu są względne, bez `..`, bez dysku, i po `realpath` muszą leżeć w projekcie. Dowiązanie skilla do biblioteki jest stanem normalnym.
- Konfiguracja jest walidowana ściśle: nieznany klucz to kod 2, a komunikat nie cytuje treści pliku.
- Rdzeń nie zawiera nazw warstw ani projektów. Przykłady są neutralne (`<skill>`, `docs/design/...`, `docs/prototypes/*`). Test strażnik składa zakazane literały w czasie wykonania.
