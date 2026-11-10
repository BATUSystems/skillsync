---
title: Mapa repo (.skillsync/map.md) i jej świeżość
tags: [skillsync, map, context, fingerprint]
paths: ['skillsync_workflow/repomap.py']
source: odczyt skillsync_workflow/repomap.py; tests/test_skillsync_catalog_map.py 2026-10-21; plan docs/plans/2026-10-21-agent-optimization.md (G4)
verified: 2026-10-21
---
Reguła: `skillsync map <projekt>` zapisuje generowany `.skillsync/map.md` (nie edytować ręcznie): komendy build/test, entry pointy, katalogi testów/migracji/`docs/domain`/`docs/lessons`,
drzewo do głębokości 3 z liczbą plików śledzonych i celem modułu (pierwsza niepusta linia `README*.md`). Limit 12 KB: najpierw płytsze drzewo z adnotacją, w ostateczności obcięcie.
Nagłówek niesie `fingerprint` = sha256 z `git ls-files` + HEAD (poza gitem: lista plików). `map --check`: 0 świeża, 1 nieaktualna/brak, 2 błąd.

Dlaczego: `explore` i orkiestrator mają czytać mapę przed Glob/Grep, zamiast płacić za przeszukiwanie repo w każdej sesji.

Wyjątki i pułapki:
- Pliki nieśledzone i zmiana treści śledzonego pliku (bez zmiany listy i HEAD) nie zmieniają fingerprintu — `--check` ich nie widzi.
- Dzieci katalogu sortowane po krotce segmentów ścieżki, inaczej `Foo/Sub` wylądowałby po `Foo.Tests`.
- `.skillsync/` musi być w `.gitignore` projektu; `map` tylko ostrzega (`note`), nie edytuje pliku.

Gdzie w kodzie: `skillsync_workflow/repomap.py` (`build_map`, `fingerprint`, `_tree`).
