---
title: "Aliasy angielskie tokenów protokołu: czytamy oba języki, zapisujemy po polsku"
tags: [skillsync, protocol, tokens, i18n, aliases, parser, plan, spec]
paths: ['skillsync_workflow/protocol_tokens.py', 'skillsync_workflow/quality.py', 'skillsync_workflow/status.py', 'skillsync_workflow/prototype.py', 'skillsync_workflow/test_gate.py', 'skillsync_workflow/plan_phases.py', 'skillsync_workflow/common.py', 'skillsync_workflow/retro.py', 'library/hooks/check-closure.py', 'tests/test_skillsync_run_alias.py']
source: "docs/specs/2026-11-22-angielskie-aliasy-tokenow-protokolu.md (status approved; decyzja Barcisia 22.11 „(b) Angielskie aliasy”); skillsync_workflow/protocol_tokens.py:30 (TOKENS), :79 (READINESS_PAIRS), :85 (PL_TO_EN), :112-120 (bare_names, bare_alternation), :131-155 (RunLine, RUN_LINE), :158-169 (risk_section_title), :172-180 (ACCEPTANCE_PATTERN, acceptance_title); skillsync_workflow/prototype.py:3180-3186, :3217-3225, :3296-3298; review 22.11 (runda 1)"
verified: 2026-11-22
---
Reguła: parsery generatora czytają polskie **i** angielskie tokeny protokołu planu, speca i raportów. Rdzeń zapisuje własne wiersze po polsku, także w dokumencie angielskim. Jedyna lista tokenów to `skillsync_workflow/protocol_tokens.py` (`TOKENS`, `PL_TO_EN`, `READINESS_PAIRS`, `RUN_LINE`, `risk_section_title`, `acceptance_title`, `bare_names`, `bare_alternation`); moduły importują ją, nie kopiują reguł. Jedyny wyjątek to hook `check-closure.py` (zob. niżej).

Dlaczego: wersja wielojęzyczna skilli i instrukcji ma pozwolić pisać dokumenty po angielsku bez zmiany parserów. Polski zapis rdzenia jest stały, bo testy porównują istniejące wiersze bajtowo.

Pary (polski / angielski):
- `Przebieg:` / `Run:` — w nagłówku planu (przed pierwszym `## `); polskie w całym tekście.
- `Werdykt:` / `Verdict:` — raport pre-implement i raport retro.
- `Odbiór` / `Acceptance` — tytuł kroku planu: cały tytuł albo przed `:`, `–`, `—`, `(`, albo przed `-` poprzedzonym spacją.
- `Streszczenie dla człowieka` / `Summary for humans`; `Wynik` / `Result`.
- `Kontrakty` / `Contracts`, `Migracja` / `Migration`, `Dane` / `Data` — sekcje ryzyka speca.
- `nie dotyczy` / `not applicable` — treść pustej sekcji ryzyka.
- kolumna `Krok` / `Step` — tabela kroków planu.
- `[EKSPERT]` / `[EXPERT]` — wiersz pytania do eksperta.
- `Prototyp:` / `Prototype:`; `Bramka prototypu: pominięta — …` / `Prototype gate: skipped — …`.
- sekcje `readiness`: Cel/Goal, Kryteria/Criteria, TESTY/TESTS, Wiedza/Knowledge, Ryzyka/Risks, Kontrakty/Contracts, Migracja/Migration (zasady dopasowania niżej).
- `review --profile`: `code` = `kod`, `prototype` = `prototyp`.

Wyjątki i zasady:
- Wiele wariantów w jednym dokumencie: wygrywa pierwsza linia od góry; wartości się nie mieszają.
- Alias nie zmienia wartości tokenów już angielskich (`READY`, `PASS`, statusy).
- Angielskie `Contracts|Migration|Data` są sekcją ryzyka tylko na początku tytułu, po którym jest koniec tytułu albo separator, albo ` and`; dopasowanie jest bez względu na wielkość liter (`Data And Migration` jest sekcją). „Data model” nie jest sekcją ryzyka. Polskie słowa działają jak dotąd, gdziekolwiek w tytule. Lekcja: `docs/lessons/alias-sekcji-ryzyka-tylko-na-poczatku-tytulu.md`.
- `Acceptance tests` i `Acceptance criteria` nie są krokiem odbioru. Myślnik bez spacji przed nim nie kończy tytułu: `Acceptance-tests suite` nie jest krokiem odbioru (`protocol_tokens.py:174`, `[ \t]+-`).
- Sekcje `readiness`: nagłówek pasuje do sekcji, gdy zawiera którąkolwiek nazwę pary (bez względu na wielkość liter). Konfigurowane `Cel` spełnia też `## Goal`, `TESTY` także `## TESTS`; `contracts` przyjmuje też `Kontrakty`, `risks` przyjmuje `Ryzyka`. `evidence` nie ma pary w `READINESS_PAIRS` — spełnia go tylko nagłówek `evidence` (stan kodu; do decyzji, czy dodać parę).
- Sekcje `readiness` z dwiema nazwami: wygrywa pierwszy nagłówek od góry, także pusty; pusty nie ustępuje późniejszemu. Polski plan z pustym `## Risks of …` przed `## Ryzyka` daje `Empty plan section: risks`.
- `Step` działa tylko w tabeli z kolumną Risk/Role/Files albo Ryzyko/Rola/Pliki; przy obu kolumnach `Krok` i `Step` wygrywa `Krok`.
- Bliska forma linii `Prototyp:` albo obejścia bramki daje `BLOCKED` (fail closed), w obu językach. Każda linia nagłówka planu (przed pierwszym `## `, bez `#` na początku), która zaczyna się od `Prototype gate` albo `Bramka prototypu` i nie jest pełną linią obejścia, także angielska proza, daje `BLOCKED` `invalid_bypass_line` (`prototype.py:3186`, `:3296-3298`). Lekcja: `docs/lessons/alias-regexu-fail-closed-zamyka-literowki.md`.
- `waiting for` / `czeka na` po `Status:` w `approve` jest tylko dopiskiem do wyczyszczenia; nie jest zgodą. Lekcja: `docs/lessons/waiting-for-w-approve-to-tylko-dopisek.md`.
- `plan_section(text, name)` przyjmuje listę nazw: wygrywa pierwszy nagłówek od góry, także pusty.
- Hook `check-closure.py` (tylko biblioteka standardowa) ma lokalną kopię reguły `Run:`/`Przebieg:` (`_RUN_PL`, `_RUN_EN`); zgodność z `protocol_tokens.py` pilnuje `tests/test_skillsync_run_alias.py`. Lekcja: `docs/lessons/tokeny-protokolu-jedno-zrodlo.md`.

Poza zakresem tej zmiany: strona statusu (dashboard), serwer prototypów i decyzje wątku w skillach nie czytają jeszcze angielskich aliasów; `TAK`/`NIE` (propozycje retro) pozostaje po polsku, a alias `YES`/`NO` należy do warstwy skilli. Kod tych czytników nie był zmieniany.

Dlaczego to ważne: jedna lista tokenów oznacza, że nowy token nie rozjedzie się między modułami; reguła „czytamy oba, zapisujemy po polsku” chroni istniejące dokumenty przed zmianą wyniku.

Gdzie w kodzie: `skillsync_workflow/protocol_tokens.py` (źródło). Importują go: `quality.py` (`RUN_LINE`, `READINESS_PAIRS`, `alternation`), `test_gate.py` (`RUN_LINE`), `status.py` (`ACCEPTANCE_PATTERN`, `risk_section_title`, `token_alternation`, `names`), `prototype.py` (`token_alternation`, `GATE_PROTOTYPE_LINE`, `GATE_BYPASS_LINE`), `retro.py` (`bare_alternation`, np. `_SIGNAL`), `plan_phases.py` (`bare_names`). `common.py` tokenów nie importuje (`plan_section` przyjmuje nazwy jako argument). Hook `library/hooks/check-closure.py`; test zgodności `tests/test_skillsync_run_alias.py`.
