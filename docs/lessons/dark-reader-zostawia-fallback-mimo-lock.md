---
title: "Dark Reader 4.9.125 zostawia style.darkreader--fallback mimo darkreader-lock: meta w HTML i zdjęcie fallbacku w silniku"
tags: [prototype, darkreader, theme, css, browser, engine]
paths: ['skillsync_workflow/prototype.py', 'tests/test_skillsync_prototype.py', 'tests/test_skillsync_prototype_engine.py', 'tests/fixtures/prototype/engine-v2/**']
source: '.skillsync/runs/2026-11-04-prototype-komentarze.karta-2/C-dr-leftover-implementer.md (Odkrycia 1 i 3, tabela dowodu padania, testy EngineV2DarkReaderLeftoverTests); A-f1fix-implementer.md (Odkrycie 3); CHANGELOG.md (2026-11-04, Naprawiono: migrate, theme_lock_meta); tests/test_skillsync_prototype_engine.py:1220; .skillsync/runs/2026-11-04-prototype-komentarze.karta-2/tools/dr-leftover-probe.mjs i logs/dr-leftover{,-control}/result.json (symulacja fallbacku na document_start: nowy silnik usuwa, kontrola na c2b5e9a zostaje ciemna); obserwacja przez CDP w Chrome Barcisia (profil Praca, Dark Reader 4.9.125) i jego potwierdzenie po wdrożeniu na podglądzie 4444 (2026-11-04).'
verified: 2026-11-04
---
**Objaw:** w prototypie na silniku v2, przy włączonym Dark Readerze, w `<head>` i `<html>` zostaje `<style class="darkreader darkreader--fallback" media="screen">` z `background-color: #181a1b !important` na `html, body, body :not(iframe)`, mimo że strona ma `<meta name="darkreader-lock">`. Ciemne tło może mignąć, zanim silnik wystartuje.

**Przyczyna:** Dark Reader 4.9.125 wstawia fallback na `document_start`, czyli przed skryptem silnika na końcu `<body>`. Meta dodawane przez silnik w JS przychodzą za późno (stąd krok `theme_lock_meta` w `migrate`). Blokada nie usuwa fallbacku, który rozszerzenie już wstawiło, więc zostaje w DOM.

**Reguła:**
- Blokada ma być statyczna w HTML: `<meta name="color-scheme" content="light dark">` i `<meta name="darkreader-lock">` w `<head>` szablonu `index.html` oraz w `migrate` (krok `theme_lock_meta`). Brak któregokolwiek meta w szablonie to błąd `check_tokens.py`.
- Silnik zdejmuje fallback tylko przy obecnym `darkreader-lock`: przy starcie i w `MutationObserver` (`childList`, bez `subtree`, na `<html>` i `<head>`); obserwator odpinany na `pagehide`. Zdejmuje wyłącznie `style` z klasą zaczynającą się od `darkreader`.
- Nie zdejmować `link.darkreader…`, `style` bez klasy, `style` z „dark” w innej klasie (`dark-theme`, `my-darkreader`) ani atrybutów `data-darkreader-*`. Nie ruszać treści ekranów.

**Jak wykryć:**
- `python3 library/skills/prototype/scripts/check_tokens.py` w bibliotece: exit 1 przy braku meta w `<head>` szablonu (`check_tokens.py:193-196`).
- `python3 -m unittest tests.test_skillsync_prototype_engine.EngineV2DarkReaderLeftoverTests -v` (5 testów). Na silniku bez zdejmowania: 5 FAIL (tabela w raporcie C).
- W przeglądarce z Dark Readerem: `document.querySelectorAll('style.darkreader').length === 0` i jasne `getComputedStyle(document.body).backgroundColor` na prototypie (w W1 sprawdzone ręcznie w profilu Praca; headless bez rozszerzenia: `tools/dr-leftover-probe.mjs`).

**Granice aktualności:** objaw zaobserwowany w Dark Reader 4.9.125; w profilu z 4.9.134 fallback nie zostawał. Inne wersje nie są zweryfikowane.
