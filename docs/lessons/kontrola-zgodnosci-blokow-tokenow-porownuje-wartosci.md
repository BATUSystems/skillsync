---
title: "Kontrola zgodności dwóch bloków tokenów musi porównywać wartości, nie tylko zbiór nazw"
tags: [prototype, theme, tokens, dark-mode, check-tokens, e2e, no-js]
paths: ['tests/test_skillsync_prototype_library.py', 'tests/test_skillsync_prototype_e2e.py', 'tests/e2e/prototype/**', 'tests/fixtures/prototype/check-tokens/**']
source: 'skills/prototype/scripts/check_tokens.py:157-167 (porównanie wartości bloków ciemnych, biblioteka, stan 2026-11-06); skills/prototype/assets/theme/default.css:74 i 143-146 (dwa zduplikowane celowo bloki ciemne); docs/prototype-contract/scenariusze-e2e.md, wiersz R-07 (motyw systemowy bez zapisanego wyboru, wariant bez JS); zgłoszenie w zleceniu dokumentacji 2026-11-06 (blok @media usunięto jako duplikat, strona bez JS straciła tryb ciemny); tests/test_skillsync_prototype_library.py (`test_token_only_in_dark_block_fails`, `test_dark_blocks_with_different_values_fail`); skillsync_workflow/prototype.py `compose_tokens_css` (ciemne tokeny projektu w obu blokach)'
verified: 2026-11-06
---
**Objaw:** strona prototypu bez JS (wyłączony skrypt) przy ciemnym motywie systemowym jest jasna. Kontrola motywu przechodzi. Blok `@media (prefers-color-scheme: dark)` w motywie został wcześniej usunięty jako „duplikat” bloku `:root[data-theme="dark"]`.

**Przyczyna:** motyw ma dwa celowo zduplikowane bloki ciemne: `:root[data-theme="dark"]` (wybór użytkownika, z JS) i `@media (prefers-color-scheme: dark) { :root:not([data-theme]) }` (wybór systemu, bez JS). Kontrola porównywała tylko zbiory nazw tokenów w każdym bloku ciemnym, więc różne wartości przechodziły jako zgodne. Ten sam zbiór nazw przy różnych wartościach wygląda jak duplikat do usunięcia.

**Reguła:**
- Dwa bloki ciemne motywu muszą mieć te same tokeny **i te same wartości** (po normalizacji białych znaków). `check_tokens.py` zgłasza różnicę wartości jako błąd, a token obecny tylko w jednym bloku jako osobny błąd.
- Bloku `@media` nie usuwaj jako duplikatu bez testu z wariantem bez JS (R-07). Zmieniając wartość tokenu ciemnego, zmień ją w obu blokach; komentarz w `default.css` (linie 143-144) to wskazuje.

**Jak wykryć:**
- `python3 skills/prototype/scripts/check_tokens.py` w bibliotece: błąd `token X ma różne wartości w blokach ciemnych: …` przy rozjeździe, kod wyjścia niezerowy.
- `grep -n 'prefers-color-scheme\|data-theme="dark"' skills/prototype/assets/theme/default.css`: muszą być dwa bloki ciemne.
- E2E R-07 w Chrome (`tests/e2e/prototype/`, `SKILLSYNC_E2E_CHROME`): wariant (a) bez JS sprawdza tło przy emulowanym `prefers-color-scheme: dark`.
- Testy: `test_dark_blocks_with_different_values_fail` (różne wartości w blokach ciemnych → exit 1) i
  `test_token_only_in_dark_block_fails`. Ciemne tokeny projektu (`tokens_from`) generator zapisuje w `tokens.css` w obu
  blokach, więc strona bez JS też dostaje paletę projektu.

**Granice aktualności:** dotyczy `check_tokens.py` i `default.css` w stanie z 2026-11-06, z dwoma blokami ciemnymi. Wariant R-07 bez JS obowiązuje, dopóki motyw ma dwa bloki.
