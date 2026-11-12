---
title: Klasy przebiegów, final, kandydaci zmian (metrics --classify, retro)
tags: [skillsync, workflow, retro, classify, checkpoints, runs]
paths: ['skillsync_workflow/runs.py', 'skillsync_workflow/retro.py', 'tests/test_skillsync_metrics_classify.py', 'tests/test_skillsync_retro.py']
source: runs.py:_classify_run, _read, _is_problem, _label, _extra_fields, _owner_dir, retrospective; retro.py:retro, _candidates, _failing_case, _mask, _decide_proposal; retro_scan.py:retro_scan; plan zadania (poza rdzeniem) (Decyzje D57, N1-N7; Kontrakty; Terminologia domenowa) 2026-10-30
verified: 2026-11-06
---
Reguła: klasę przebiegu wyznaczają wyłącznie rekordy `checkpoint` w `.skillsync/runs/<id>.json`. Raporty `.md` w `.skillsync/runs/<id>/` dają tylko sygnały pomocnicze (`provenance: report-heuristic`) i nigdy nie podnoszą klasy.

Przebieg (jednostka klasyfikacji): plik `<id>.json`, `id` = nazwa pliku bez `.json` (N5). W dispatch jednostką jest paczka: `<run>.<paczka>`. Katalog `<id>/` bez przypisanego pliku to przebieg `no_data`; jego granulacja nie jest ustalona (np. poc-a: katalog z 21 paczkami bez rekordów to jeden przebieg `no_data`).

Tożsamość logiczna: `run_id` z treści pliku, gdy jest poprawnym identyfikatorem, inaczej nazwa pliku; w obu przypadkach bez końcowego `.<liczba>` (`id` w `runs[]` zawsze z nazwy pliku). `x` i `x.2` to dwa przebiegi w `runs[]` (ponowienie w nowym worktree), ale jedna tożsamość przy progu kandydata. Próg: ten sam `cause_key` w co najmniej dwóch tożsamościach.

Klasy, pierwsza pasująca wygrywa (`runs.py:_classify_run`):
1. `no_data`: brak rekordów; także katalog bez pliku.
2. `incomplete`: ostatni rekord któregokolwiek kroku nie jest `PASS`, albo ostatni rekord przebiegu nie ma `final`.
3. `unexplained`: krok z problemem bez przyczyny. `checkpoint` ostrzega o tym w polu `warnings` przy zapisie, gdy można jeszcze dopisać rekord z przyczyną.
4. `recovery`: są kroki z problemem i każdy ma przyczynę.
5. `clean`: brak problemów (tylko z `final`).
`unreadable` występuje tylko w `retro`: plik nieczytelny, kategoria ze stałej listy (`invalid_name`, `not_regular`, `symlink`, `malformed`, `io_error`). Nigdy nie jest `clean`; ustawia `candidates_complete: false`.

Rekord problemu: `FAIL`, `BLOCKED`, `NOT_RUN`, powtórzenie kroku albo `attempt` > 1.
Przyczyna kroku: co najmniej jeden rekord problemu tego kroku ma niepusty `cause` (po `strip`) albo `cause_key`. Przyczyna przy czystym, niepowtórzonym `PASS` nic nie tłumaczy.

Ponowny dowód (N7): krok, którego końcowy rekord `PASS` ma dowody, a wszystkie ich `sha256` występują w dowodach wcześniejszych rekordów tego kroku o statusie innym niż `PASS`, jest `unexplained` nawet z przyczyną, bo te same bajty nie dowodzą odzysku. Porównanie po samym `sha256`, nie po ścieżce; nadzbiór dowodów lub inne bajty pod tą samą ścieżką to nie jest ponowne użycie. `retro` liczy takie kroki w `rubric.evidence.reused_from_problem`.

Benchmark (N7): plik z kluczem `benchmark` i niepustym `checkpoints` jest uszkodzony (`malformed` w `retro`, błąd w `metrics`); `checkpoint` odmawia dopisania do pliku benchmarku. Benchmark nie jest przebiegiem ani właścicielem katalogu: `x/` obok benchmarku `x.json` to przebieg `no_data`.

Przykłady (sekwencje rekordów danego kroku):
- `[FAIL cause=x, FAIL, PASS final]` → `recovery`.
- `[FAIL, PASS final]` → `unexplained` (brak przyczyny).
- `[PASS]` → `incomplete`, `final_status: PASS`.
- `[final, b]` → `incomplete` (przebieg ponownie otwarty); `[final, b final]` → zamknięty.

`final` a `final_status`: `final` to znacznik zamknięcia przebiegu, ważny tylko na ostatnim rekordzie i tylko przy `PASS`. `final_status` to status ostatniego rekordu, niezależnie od `final`.

Cztery znaczenia `status` (zawsze z kontekstem):
- wynik operacji w korzeniu odpowiedzi `retro`: `PASS`, `NOT_RUN`, `BLOCKED`; `PASS` znaczy, że raport powstał, nie że przebiegi były dobre;
- status rekordu checkpointu: `PASS`, `FAIL`, `BLOCKED`, `NOT_RUN`;
- stan kryterium rubryki: `available`, `partial`, `unavailable`;
- stan kandydata: `recurred`, `ready`, `needs_case`, `needs_owner`, `decided` (kolejność sortowania).

Rubryka: brak danych to `unavailable` z `reason`, nie zero; `0` podane jawnie jest liczbą. Częściowe pola to `partial` z `records_with_value` i `records_total`. Zmieniony dowód (`changed`) obniża rubrykę, nie klasę.

Kandydat zmiany: propozycja narzędzia, nie zmiana. C1 (ostrzeżenie: dowód `PASS` starszy niż ostatnia zmiana kodu) jest kandydatem ręcznym poza MVP; raport oznacza go jako nie pochodzący z narzędzia. `ready` znaczy „komplet do oceny” (jeden właściciel i zweryfikowany dowód z `FAIL`), nie „przypadek odtwarza błąd”; sprawdza to człowiek. `needs_case`: brak dowodu `verified`. `needs_owner`: zero albo kilku właścicieli; przy obu brakach status `needs_owner`, a `missing` wymienia oba. Wiele porażek w jednym przebiegu nie wchodzi do progu.

Decyzja: wpis w rejestrze `docs/retro/decisions.yaml` (`approved`/`rejected`, dzień). `decided` = rozstrzygnięty i bez nowych rekordów problemu po dniu decyzji (UTC); `recurred` = po decyzji ten sam `cause_key` wrócił, więc poprawka nie zadziałała albo odrzucenie trzeba ponownie rozważyć. Rekordy z dnia decyzji nie są nawrotem, bo mogą poprzedzać poprawkę. `recurred` z `decision: approved` i `fixed: false` znaczy, że poprawka jeszcze czeka, a nawrót po `rejected` każe ponownie rozważyć odrzucenie; `retro-decide --replace --ref` przesuwa datę na dzień poprawki.

`failing_case` a powtarzalny przypadek: `failing_case` to skrót pliku dowodu zapisanego przy rekordzie `FAIL` (`check: verified`). Nie jest to test ani przypadek z reguły „najpierw nieudany przypadek” (`lessons.md`, sekcja Właściciel poprawki). Przypadek powtarzalny (test, `doctor`, benchmark) dopiero trzeba napisać przed zmianą reguły.

Właściciele (`checkpoint --owner`): `instruction`, `skill`, `hook`, `route`, `test`; wymaga `--cause-key`. Zasada N3: właściciele `lesson` i `domain` z tabeli `lessons.md` nie wchodzą do CLI. Wiedza bez reguły nie jest zmianą harnessu; kontrola `doctor` lub mapa liczy się jako `test`. Tabela wyboru: [workflow.md](../workflow.md#mapowanie-właścicieli-n3).

Czego narzędzie nie wykrywa:
- porażek i kroków niezapisanych jako checkpoint, także luki dowodu, która nie trafiła do rekordu;
- częściowej utraty plików przebiegu: przy zgubionej paczce wynik pokazuje tylko pozostałe, także jako `clean`;
- czy `PASS` jest prawdziwy: `checkpoint` nie waliduje treści dowodu (`provenance: operator-recorded`);
- zgodności z planem (`plan_coverage: unavailable`) i przyczyn zdarzeń;
- różnicy czasu logu i czasu zmiany kodu (kandydat C1, poza MVP);
- tajnego tekstu bez znanego wzorca w `cause_key` (filtr łapie tylko wzorce tokenów z prefiksem i ciągi >= 32 znaki).

`clean` znaczy tylko, że zapisane rekordy nie mają problemów. Nie znaczy, że przebieg był dobry.

Retrospektywa przebiegu: rekord `retro-quality` o statusie `PASS` z `recorded_at` wcześniejszym niż rekord `final` (w tożsamości przebiegu, także w plikach `<id>.N.json`). Brak takiego rekordu przy `checkpoint --final` daje ostrzeżenie `retro-quality-missing:`; nieczytelny plik siostrzany daje `retro-quality-unverified:`. Kodu wyjścia to nie zmienia.

`final_without_retro` (`coverage.retro` w `retro`): liczba przebiegów z `final` bez retrospektywy, których `final` ma datę UTC nie wcześniejszą niż 2026-11-05. Starsze przebiegi nie są liczone.

Propozycja retrospektywy (`retro-scan`, `retro-decide --proposal`) to gotowa zmiana jednego pliku z id `<run-id>/P<n>`. Nie jest kandydatem z `retro` i nie zmienia klasy przebiegu. Stany i kolejność: [retro-proposals.md](retro-proposals.md).
