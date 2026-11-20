---
title: 'Strażnik ścieżek tmp: skażenie nazw trzeba ograniczyć do funkcji, a zmianę skanera sprawdzić skryptem różnicowym stary/nowy'
tags: [skillsync, tests, tmp-path-guard, ast, scope]
paths: ['tests/test_skillsync_tmp_path_guard.py', 'docs/development.md']
source: "tests/test_skillsync_tmp_path_guard.py: scan (zbiory own/attrs, collect/scope_of, kolejność trzech przebiegów po ast.walk); skrypt różnicowy stary/nowy skaner na 758 plikach testów repozytorium i trzech starszych wydań (przed/po 34/32 trafień, 2 utracone przejrzane ręcznie)"
verified: 2026-11-20
---
**Objaw:** nowy test ze zmienną lokalną o nazwie `directory` wywołał trafienie strażnika w NIEZMIENIONEJ linii innej funkcji
(fałszywe trafienie zależne od nazw zmiennych, nie od kodu, który je składa).

**Przyczyna:** skaner trzymał jeden wspólny zbiór „skażonych” nazw na plik (zmienne i atrybuty razem, trzy przebiegi po `ast.walk`).
Ten sam zbiór służył do uznawania nazw pętli po literałach za bezpieczne (`bind`), więc skażenie nazwy w jednej funkcji przenosiło się do
innych i odbierało nazwie pętli status bezpiecznej.

**Reguła:**
- Skażenie nazw lokalnych działa w zasięgu funkcji (zbiór `own[zasięg]` wybierany według zasięgu węzła); atrybuty i parametry w zasięgu pliku (`attrs`).
- Analiza w zasięgu może tylko usuwać trafienia: używaj tych samych trzech przebiegów po `ast.walk`, a zbiór wybieraj według węzła. Kolejność
  przebiegów musi być ta sama, inaczej inwariant „po ⊆ przed” nie zachodzi.
- Zmiana skanera przechodzi skrypt różnicowy (poniżej); sam zielony test nie wystarcza.

**Jak wykryć:**
- Skrypt różnicowy: stary skaner ze stałego sha bazy (nie z HEAD), ładowany przez `importlib` pod inną nazwą niż nowy; porównanie `scan(tree)`
  jako multiset na plikach testów repozytorium i starszych wydań. Na obecnym repozytorium są tylko dwa trafienia, na starszych wydaniach
  wiele, więc sam obecny skan nie ma siły dowodowej.
- Każde utracone trafienie przejrzyj ręcznie. Wynik: 758 plików, 34 → 32 trafień; 2 utracone to PRAWDZIWE złączenie tmp z danymi, złapane przez
  stary skaner przypadkiem (kolizja nazw z inną funkcją), dziś naprawione w repozytorium. Reguła „źródło w innej funkcji = fałszywe”
  jest za mocna jako automatyczna klasyfikacja.
- Objaw nowego fałszywego trafienia: zmiana nazwy zmiennej w innej funkcji zmienia wynik strażnika.

**Granice:** skaner nie śledzi przepływu przez wywołania (fabryka zwracająca ścieżkę tmp) ani `global`/`nonlocal`; atrybut o tej samej nazwie
w niezwiązanej klasie skazi plik.
