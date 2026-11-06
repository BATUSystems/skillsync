---
title: "Propozycje retrospektywy: rejestr proposals:, stany, kolejność, skan progu i maskowanie"
tags: [skillsync, retro, proposals, registry, workflow, privacy]
paths: ['skillsync_workflow/retro_scan.py', 'skillsync_workflow/retro.py', 'tests/test_skillsync_retro_scan.py', 'tests/test_skillsync_retro.py']
source: 'retro_scan.py:328-352 (_proposals), 355-356 (_order), 545 (sources), 587-596 (krok d), 323-325; retro.py:305, 1104-1105, 1211-1212, 646-660, 679-691, 989-994; spec docs/specs/2026-11-05-retro-narzedzia.md (Kontrakty, Migracja, Ryzyka); raporty 1.3, 1.4 w .skillsync/runs/2026-11-05-retro-narzedzia/'
verified: 2026-11-06
---
**Reguła:** propozycja retrospektywy to gotowa zmiana jednego pliku z id `<run-id>/P<n>`. Jej stan trzymają dwa miejsca:
plik `retro-decisions.json` w katalogu przebiegu (historia stanów, pod gitem nie jest) i sekcja `proposals:` rejestru
`docs/retro/decisions.yaml` (pod gitem). Rejestr ma pierwszeństwo.

**Rejestr `proposals:`:**
- Sekcja jest opcjonalna. Rejestr bez niej działa jak dotąd.
- Wpis: `proposal` (`<run-id>/P<n>`, unikalne), `repo` (`library` albo `project`), `target` (te same reguły co
  w wejściu skanu), `decision` (`rejected` albo `applied`), `date` (`RRRR-MM-DD`), `ref` (pełny sha, 40 znaków hex).
  `ref` jest wymagany przy `applied` i zabroniony przy `rejected`.
- Nieznane pole wpisu albo zła wartość daje rejestr `invalid`. Rejestr nie przechowuje tytułów, treści diffów ani kluczy.
- Zapis (`retro-decide --proposal`) zachowuje resztę pliku, ale normalizuje YAML (`safe_dump`, kolejność kluczy zachowana).
  Komentarze w pliku znikają.
- Starszy generator, sprzed tej sekcji, traktuje `proposals:` jako nieznane pole. Rejestr jest wtedy `invalid`, a `retro`
  zwraca `BLOCKED`. Dane nie giną. Ochrona: zaktualizować generator przed pierwszym `retro-decide --proposal`.
- Wpisy powstają dopiero przy nowych decyzjach. Przebiegi sprzed tej sekcji działają przez `retro-decisions.json`.
  Migracja wsteczna nie istnieje.

**Stany i pierwszeństwo:**
- Stan propozycji to ostatni wpis w `retro-decisions.json` jej przebiegu albo wpis rejestru. Gdy oba są, decyduje rejestr.
- Propozycja przebiegu bez wpisu stanu nie ma stanu i odpada.
- Wpis rejestru bez przebiegu na dysku też jest propozycją. W skanie jej czas to `date` o `T23:59:59Z`.
- Stany nierozstrzygnięte: `open`, `revising`, `approved`, `deferred`, `stale`, `failed`, `handoff`. Stany końcowe:
  `rejected`, `applied`.
- Ta sama propozycja w przebiegu i w rejestrze liczy się raz. Źródła są w kolejności pierwszeństwa, więc główny
  checkout wygrywa przy tym samym id.

**Kolejność:**
- W skanie (`retro-scan`): czas `created_at` rosnąco. Remis rozstrzyga id przebiegu rosnąco: większe id to późniejsza
  propozycja. Potem numer P rosnąco.
- W `retro --json`: najpierw wg stanu, w obrębie stanu od najnowszego `created_at`. Remis: id przebiegu i numer P malejąco.
- Krok (b) skanu bierze „najnowszą” propozycję jako ostatnią w tej kolejności.

**Krok (d) skanu:** ostatnia propozycja pary jest `applied`, a jej `ref` należy do historii celu
(`git merge-base --is-ancestor`). Wtedy powstaje nawrót (`recurrence_of`). Gdy `ref` nie należy do historii, skan daje
ostrzeżenie `ref-not-in-history:<id>`. Gdy nie da się go sprawdzić, daje `ref-unverified:<id>`. W obu przypadkach skan
postępuje jak bez `applied` i przechodzi do kroków (e)–(f). Korzeń celu `repo: library` to jedyna warstwa zawierająca
`target`; brak albo kilka takich warstw daje `ref-unverified`.

**Źródła skanu:** lokalny magazyn przebiegów; w worktree także główny checkout (tylko odczyt). Inne projekty czyta
tylko `--cross-project` i tylko dla sygnałów `repo: library`. Z nich bierze wyłącznie pary (klucz, cel) i liczy je.

**Maskowanie międzyprojektowe:**
- Wynik `retro-scan` z `--cross-project` zawiera tylko liczby: `cross_project_repeats` i `sources.projects`.
- `retro --all` w `library_pairs[]` zawiera tylko klucz (ze wzorca klucza), cel (jako ścieżkę dowodu, z maskowaniem)
  i liczbę projektów. Bez nazw projektów, ścieżek i id przebiegów.
- Klucz z obcego pliku, który nie pasuje do wzorca, jest pomijany, bo trafiłby do wyniku.

**Wynik `retro --json`:**
- `keys` to tylko klucze zgodne ze wzorcem klucza przyczyny i niebędące tokenami; reszta jest pomijana.
- `signals` to liczba id z propozycji, bez deduplikacji.
- Przy niepoprawnym rejestrze (`BLOCKED`) lista `proposals` jest pusta.
- `proposals_truncated` jest `true` także przy obcięciu czytnika (limit przebiegów albo bajtów).
- Status raportu nie określa listy: bez checkpointów jest `NOT_RUN`, a `proposals[]` może być niepuste.
- `coverage.retro.runs` liczy przebiegi z poprawnymi plikami retro, `invalid_files` liczy pliki; przy `--run-id` cała tożsamość (`x.json`, `x.N.json`).

**Propozycja tylko z rejestru:** w `retro --json` mają `owner`, `title` i `diff` równe `null`, `keys: []`, `signals: 0`.
Wiersz w widoku musi to obsłużyć (tytuł zastępczy, bez „Pokaż diff”).

**Diff:** pochodzi z ostatniego wpisu `retro-decisions.json`. `retro --json` pokazuje go tylko jako zwykły plik
`retro-P<n>.diff` albo `retro-P<n>-<k>.diff` w katalogu przebiegu; inna nazwa daje `null`.

**Ryzyka rezydualne:**
- Brak blokady przy równoległych zapisach `retro-decide`: wygrywa ostatni zapis. Dwa wywołania naraz mogą nadpisać
  sobie wpisy rejestru.
- Kod git 128 traktuje się jako „nie repozytorium” tylko wtedy, gdy w korzeniu i przodkach nie ma `.git`. W
  `linked_worktree` to jest strażnik zapisu; w `_main_source` rc 128 nie sprawdza `.git` (dług do zgłoszenia).

**Jak wykryć:**
- Rejestr z sekcją `proposals:` w starym generatorze daje `BLOCKED`, a nie utratę danych.
- `retro-scan` na dwóch przebiegach o tym samym czasie: kolejność po id, większe id później.
- Wynik `retro --all` lub `--cross-project` nie zawiera żadnej nazwy projektu ani id przebiegu. Test: `LibraryPairs`
  i asercja braku nazw w `tests/test_skillsync_retro_scan.py`.

Gdzie w kodzie: `retro_scan.py:_proposals`, `_order`, `_scan`, `_repeat_of`; `retro.py:_registry_full`,
`_parse_proposals`, `_plan_proposal`, `_decide_proposal`, `_retro_section`, `_proposal_diff`, `_retro_all`.
