---
title: 'Strażnik ścieżek tmp: skażenie nazw trzeba ograniczyć do funkcji, a zmianę skanera sprawdzić skryptem różnicowym stary/nowy'
tags: [skillsync, tests, tmp-path-guard, ast, scope]
paths: ['tests/test_skillsync_tmp_path_guard.py', 'tests/layers_helpers.py', 'tests/tmp_paths.py', 'docs/development.md']
source: "tests/test_skillsync_tmp_path_guard.py: scan (zbiory own/attrs, collect/scope_of, kolejność trzech przebiegów po ast.walk), reguła `X.name` dla zmiennych przypisanych z TemporaryDirectory; skrypt różnicowy stary/nowy skaner na 758 plikach testów repozytorium i trzech starszych wydań (przed/po 34/32 trafień, 2 utracone przejrzane ręcznie); class_registry/merge_registry/scan(tree, inherited) i skrypt różnicowy z rejestrem z drzewa tagu (3 dodane trafienia w test_skillsync_codex.py); tests/layers_helpers.py (IsolatedTestCase: self.root, self.work); różnicówka stary/nowy skaner na drzewie 56f6887 i tagach v1.6.0, v1.7.0, v1.9.0 (utracone 0; layers_merge.py:696 w v1.6.0 i v1.7.0); tests/test_skillsync_retro.py (CorruptRecords.test_nul_in_evidence_path_is_malformed)"
verified: 2026-11-21
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

**Dziedziczenie z klas bazowych w innych plikach:**
- Reguła: rejestr klas (klasa → skażone atrybuty i bazy) powstaje w pierwszym przebiegu po wszystkich plikach testów; `scan(tree, inherited=rejestr)`
  dodaje podklasie atrybuty baz po prostej nazwie klasy, przechodnio. Atrybuty pochodne od odziedziczonych mają zasięg (nazwa: funkcja,
  atrybut: klasa) i nie wchodzą do wspólnego zbioru pliku ani rejestru. `scan(tree)` bez rejestru nie dziedziczy.
- Jak wykryć: skrypt różnicowy buduje rejestr z drzewa danego tagu (nie z bieżącego repozytorium). Wynik: bieżące repozytorium bez zmiany
  (2 trafienia w `ALLOWED`); na drzewach tagów dodane trafienia to 3 w `test_skillsync_codex.py`, prawdziwe według reguł strażnika i naprawione
  przez `tmp_paths.child`. Złączenie w `layers_merge` (`IsolatedTestCase.work` z `Path(temp.name)`) wraca w v1.6.0 i v1.7.0 (`layers_merge.py:696`),
  bo `X.name` obiektu `TemporaryDirectory` jest ścieżką tmp (zbiór plikowy `X`, nie `NamedTemporaryFile`, `mkdtemp` ani `Path(x).name`).
  Różnicówka stary/nowy skaner na drzewie 56f6887 i tagach v1.6.0, v1.7.0, v1.9.0: utracone 0.
- Skutek: 129 trafień w 47 plikach na `release/1.12.0` (23 to `self.work`), wszystkie naprawione przez `tmp_paths.child`/`descend`
  (w helperach u źródła: `make_repo`, `make_layer`, `layer_dir`, `LayersCase.project`, `WorkflowFixture.write`, `PrototypeFixture.proto_dir`,
  `retro` `write`/`past`/`put_input`). Wyjątki w `ALLOWED`: 1 wpis `test_skillsync_dispatch_state.py` `put` (jedyna wroga nazwa z `\n` pod
  `os.name != 'nt'`) i 2 wpisy z CORE-TESTDEBT2 (`test_run_hardening`, `test_gate`). Wpis do `ALLOWED` wolno dodać tylko, gdy WSZYSTKIE
  wystąpienia klucza (plik, tekst wyrażenia) spełniają: (a) nazwy celowo wrogie i test zabezpieczony przed Windows albo `child` odrzuciłby
  dokładnie to, co test sprawdza; (b) prawa strona to wynik walidatora `tmp_paths`/testu, a nie kodu produktu; (c) semantyka testu zmieniłaby
  się mimo `descend`. Stałe, liczby, `'x-%d.json' % n`, `FILES[0]` i pętle po nie-literałach naprawia się `child`/`descend`, nie `ALLOWED`.
  Helpery z parametrem ścieżki względnej używają domyślnie `descend`.
- Lekcja: sprawdzaj regułę skażenia na obu stronach (czy `X.name` wchodzi do rejestru) eksperymentem na prawdziwym kodzie (`IsolatedTestCase`),
  nie intuicją o klasie bazowej.
- Naprawa ujawnia skutki uboczne helperów (zob. „Wiedza z naprawy”).

**Wiedza z naprawy:**
1. `child`/`descend` odrzucają nazwy zastrzeżone Windows także z rozszerzeniem (`nul`, `con`, `aux`, `prn`, `com1`, `nul.json`). Testy na Linuksie tego
   nie widzą: naprawa ujawniła `CorruptRecords.test_nul_in_evidence_path_is_malformed` w `tests/test_skillsync_retro.py` (identyfikator `nul`
   zmieniony na `nulbyte`). Po zmianie helperów sprawdzaj punktowo moduły, które używają nazw podobnych do urządzeń.
2. `descend` odrzuca `..`. Test, który celowo zapisuje poza rootem projektu (`tests/test_skillsync_knowledge_init.py`, lekcje w `<base>/docs/lessons`,
   gdzie `self.root` to `self.base / 'project'`), zapisuje względem `self.base` zamiast przez helper.
3. Moduły testów importują rodzeństwo po prostej nazwie (`from test_skillsync_workflow import ...`), więc bieg modułowy to
   `python3 -m unittest discover -s tests -p <moduł>.py`, a nie `tests.<moduł>`. `ModuleNotFoundError`, rc=1 i `ran=1` w 0 s to błąd runnera, nie wynik testu.
4. Metoda: różnicówka stary/nowy skaner na zamrożonym SHA i tagach; skan kontrolny `Attribute(name)` poza regułą (wynik 0); mutacja „każde `.name`”
   czerwieni 3 z 7 testów `ScannerNameRule`.
5. Kontrola izolacji „test touched real Skillsync state dirs” bywa czerwona, gdy inna karta wykonuje `skillsync link` w czasie biegu. Powtórz moduł
   punktowo: przyczyna jest zewnętrzna.

**Granice:** skaner nie śledzi przepływu przez wywołania (fabryka zwracająca ścieżkę tmp) ani `global`/`nonlocal`; atrybut o tej samej nazwie
w niezwiązanej klasie skazi plik; klasy rozpoznaje prosta nazwa (kolizje nazw z różnych plików dają sumę atrybutów).
