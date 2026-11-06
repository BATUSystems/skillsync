---
title: "tempfile.mkstemp i mkdtemp tworzą 0600/0700: generowane pliki repo są nieczytelne dla grupy"
tags: [skillsync, python, tempfile, permissions, prototype, atomic-write]
paths: ['skillsync_workflow/prototype.py', 'tests/test_skillsync_prototype.py']
source: .skillsync/runs/2026-11-03-prototype-cli/1-implementer.md (Odkrycia), code-review-2-1 (m1); tests/test_skillsync_prototype.py (FileModeTests, umask 0o027 w podprocesie)
verified: 2026-11-04
---
**Objaw:** pliki albo katalog zapisane do repo przez „plik tymczasowy + rename” mają tryb 0600 (katalog 0700) niezależnie od umask, więc prototyp jest nieczytelny dla grupy (współpracownicy, serwer WWW na wspólnym katalogu).

**Przyczyna:** `tempfile.mkstemp` tworzy plik 0600, `tempfile.mkdtemp` katalog 0700; `os.replace`/`os.rename` zachowują ten tryb.

**Reguła:** katalog stagingu twórz `os.mkdir` (tryb wg umask), pliki otwieraj zwykłym `open(..., 'x')`; przy zapisie przez plik tymczasowy ustaw tryb jawnie przed `os.replace`: nowy plik `0o666 & ~umask`, istniejący zachowuje swój tryb (`prototype.py:atomic_write`, `_make_staging`). Nie używaj `mkdtemp` dla katalogu, który trafi do repo.

**Jak wykryć:** `FileModeTests` w `tests/test_skillsync_prototype.py` (podproces z `umask 0o027`: `init` daje pliki 0o640 i katalog 0o750); mutacje „bez chmod” i „mkdtemp” są przez ten test zabite.
