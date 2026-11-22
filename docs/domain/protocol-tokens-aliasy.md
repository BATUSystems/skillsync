---
title: "Aliasy angielskie tokenów protokołu: czytamy oba języki, zapisujemy po polsku"
tags: [skillsync, protocol, tokens, i18n, aliases, parser, plan, spec]
paths: ['skillsync_workflow/protocol_tokens.py', 'skillsync_workflow/quality.py', 'skillsync_workflow/status.py', 'skillsync_workflow/prototype.py', 'skillsync_workflow/test_gate.py', 'skillsync_workflow/plan_phases.py', 'skillsync_workflow/common.py', 'skillsync_workflow/retro.py', 'library/hooks/check-closure.py', 'tests/test_skillsync_run_alias.py']
source: "docs/specs/2026-11-22-angielskie-aliasy-tokenow-protokolu.md (status approved; decyzja Barcisia 22.11 „(b) Angielskie aliasy”); skillsync_workflow/protocol_tokens.py:30 (TOKENS), :79 (READINESS_PAIRS), :90 (PL_TO_EN), :124-149 (RunLine), :152-162 (risk_section_title), :165-173 (acceptance_title)"
verified: 2026-11-22
---
Reguła: parsery generatora czytają polskie **i** angielskie tokeny protokołu planu, speca i raportów. Rdzeń zapisuje własne wiersze po polsku, także w dokumencie angielskim. Jedyna lista tokenów to `skillsync_workflow/protocol_tokens.py` (`TOKENS`, `PL_TO_EN`, `READINESS_PAIRS`, `RUN_LINE`, `risk_section_title`, `acceptance_title`); moduły importują ją, nie kopiują reguł.

Dlaczego: wersja wielojęzyczna skilli i instrukcji ma pozwolić pisać dokumenty po angielsku bez zmiany parserów. Polski zapis rdzenia jest stały, bo testy porównują istniejące wiersze bajtowo.

Pary (polski / angielski):
- `Przebieg:` / `Run:` — w nagłówku planu (przed pierwszym `## `); polskie w całym tekście.
- `Werdykt:` / `Verdict:` — raport pre-implement i raport retro.
- `Odbiór` / `Acceptance` — tytuł kroku planu: cały tytuł albo przed `:`, `–`, `—`, `-`, `(`.
- `Streszczenie dla człowieka` / `Summary for humans`; `Wynik` / `Result`.
- `Kontrakty` / `Contracts`, `Migracja` / `Migration`, `Dane` / `Data` — sekcje ryzyka speca.
- `nie dotyczy` / `not applicable` — treść pustej sekcji ryzyka.
- kolumna `Krok` / `Step` — tabela kroków planu.
- `[EKSPERT]` / `[EXPERT]` — wiersz pytania do eksperta.
- `Prototyp:` / `Prototype:`; `Bramka prototypu: pominięta — …` / `Prototype gate: skipped — …`.
- sekcje `readiness`: Cel/Goal, Kryteria/Criteria, TESTY/TESTS, Wiedza/Knowledge, Ryzyka/Risks, Kontrakty/Contracts, Migracja/Migration.
- `review --profile`: `code` = `kod`, `prototype` = `prototyp`.

Wyjątki i zasady:
- Wiele wariantów w jednym dokumencie: wygrywa pierwsza linia od góry; wartości się nie mieszają.
- Alias nie zmienia wartości tokenów już angielskich (`READY`, `PASS`, statusy).
- Angielskie `Contracts|Migration|Data` są sekcją ryzyka tylko na początku tytułu, po którym jest koniec tytułu albo separator; „Data model” nie jest sekcją ryzyka. Polskie słowa działają jak dotąd, gdziekolwiek w tytule. Lekcja: `docs/lessons/alias-sekcji-ryzyka-tylko-na-poczatku-tytulu.md`.
- `Acceptance tests` i `Acceptance criteria` nie są krokiem odbioru.
- `Step` działa tylko w tabeli z kolumną Risk/Role/Files albo Ryzyko/Rola/Pliki; przy obu kolumnach `Krok` i `Step` wygrywa `Krok`.
- Bliska forma linii `Prototyp:` albo obejścia bramki daje `BLOCKED` (fail closed), w obu językach. Lekcja: `docs/lessons/alias-regexu-fail-closed-zamyka-literowki.md`.
- `waiting for` / `czeka na` po `Status:` w `approve` jest tylko dopiskiem do wyczyszczenia; nie jest zgodą. Lekcja: `docs/lessons/waiting-for-w-approve-to-tylko-dopisek.md`.
- `plan_section(text, name)` przyjmuje listę nazw: wygrywa pierwszy nagłówek od góry, także pusty.
- Hook `check-closure.py` (tylko biblioteka standardowa) ma lokalną kopię reguły `Run:`; zgodność z `protocol_tokens.py` pilnuje `tests/test_skillsync_run_alias.py`. Lekcja: `docs/lessons/tokeny-protokolu-jedno-zrodlo.md`.

Poza zakresem tej zmiany: strona statusu (dashboard), serwer prototypów i decyzje wątku w skillach nie czytają jeszcze angielskich aliasów; `TAK`/`NIE` (propozycje retro) pozostaje po polsku, a alias `YES`/`NO` należy do warstwy skilli. Kod tych czytników nie był zmieniany.

Dlaczego to ważne: jedna lista tokenów oznacza, że nowy token nie rozjedzie się między modułami; reguła „czytamy oba, zapisujemy po polsku” chroni istniejące dokumenty przed zmianą wyniku.

Gdzie w kodzie: `skillsync_workflow/protocol_tokens.py` (źródło); importy w `quality.py` (`RUN_LINE`, `READINESS_PAIRS`), `test_gate.py` (`RUN_LINE`), `status.py` (`ACCEPTANCE_PATTERN`, `risk_section_title`, `token_alternation`), `plan_phases.py`, `common.py` (`plan_section`), `retro.py`, `prototype.py` (`GATE_PROTOTYPE_LINE`, `GATE_BYPASS_LINE`); hook `library/hooks/check-closure.py`; test zgodności `tests/test_skillsync_run_alias.py`.
