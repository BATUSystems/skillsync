---
title: "Kontrola izolacji stanu pada na cudzym `skillsync link`: wyklucz szum z migawki, ale rozpoznawaj rejestr po miejscu, nie po środowisku"
tags: [testing, layers, guard, flaky, windows, macos]
paths: ['tests/layers_helpers.py', 'tests/test_skillsync_state_guard.py']
source: 'CORE-STATEGUARD 2026-11-21: dwie porażki bez winy testu (13:38 i 14:00), przyczyna: `link` innej sesji przepisał composed/.projects.json; pętla pełna reguł 1-2'
verified: 2026-11-21
---
**Objaw:** „test touched real Skillsync state dirs” pada na teście, który niczego nie zapisał; powtórka jest zielona. W komunikacie czas
modyfikacji katalogu `composed` (potem pliku `.projects.json`) zmienił się w oknie testu.

**Przyczyna:** `skillsync link` każdej sesji przepisuje rejestr kompozytów (zapis atomowy: plik tymczasowy + `os.replace`), co zmienia
czas katalogu `composed` i plik rejestru. Migawka porównywała oba, więc każdy równoległy `link` mógł wywrócić cudzy test.

**Reguła:**
- Szum cudzych procesów usuwaj z migawki wąsko: metadane katalogów tylko pod `composed` / `cache/composed` (nie wszystkie katalogi),
  rejestr tylko we wpisach projektów spod katalogu tymczasowego. Nowy i usunięty katalog oraz pliki zostają widoczne.
- Rejestr rozpoznawaj po ścieżce względnej pod `REAL_STATE_DIRS`, nie przez `composed_root()`: cleanup testu biegnie przy podstawionym
  środowisku (`HOME`, `TMPDIR`, `XDG_*`) i wskazałby katalog testowy. Katalog tymczasowy licz raz przy imporcie (`resolve`: macOS `/var` →
  `/private/var`, Windows 8.3) i porównuj wpisy przez `Path.resolve().is_relative_to`. Wpis ze złego JSON-a to pusty rejestr.
- Typ wpisu bierz z `lstat` i `S_ISDIR` (symlink do katalogu jest plikiem; jego podmiana jest widoczna).
- Zawężenie kontroli wymaga dowodu różnicowego: skrypt ładuje starą i nową funkcję pod innymi nazwami modułów i na syntetycznym
  `REAL_STATE_DIRS` pokazuje, że nowa widzi wszystko, co stara, poza opisanym szumem i jawnie przyjętymi stratami (przejściowy zapis w `composed`,
  prawdziwy projekt spoza tmp w rejestrze).

**Jak wykryć:** porażka kontroli z jedną linią `composed: dir ... -> dir ...` albo `.projects.json`; `git log` zmian `tests/layers_helpers.py`.
