# Testy: szczegóły, runner, baseline

Czytaj przy planowaniu testów końcowych, porażce testu albo starych porażkach.

## Dobór i wykonanie

Najpierw testy zmienionego obszaru, potem wymagany zestaw końcowy obejmujący wszystkie
dotknięte warstwy (np. backend, frontend, build, migracje). `tests.command` z manifestu
jest fallbackiem, nie dowodem kompletności. Nie uruchamiaj pełnego zestawu po każdej edycji.
`skillsync smart-test` dobiera kontrole i uzasadnia wybór, `validate --profile final`
plan końcowy. `--run` wykonuje je dopiero po przydziale zasobów. Nie uruchamiaj drugiego
runnera/hooka dla tej samej kontroli. Przy braku CLI stosuj te same zasady ręcznie.

Raport runnera: `PASS`, `FAIL`, `BLOCKED` lub `NOT_RUN`; komenda, cwd, exit code,
liczba wykonanych/pominiętych testów, krótka diagnoza i ścieżka logu. Zero zebranych testów,
brak zależności, timeout albo sam build nie oznaczają zaliczonych testów.

Asercja negatywna (czegoś nie ma, nic się nie wydarzyło, pusta lista) i smoke sprawdzający reakcję
na zdarzenie wymagają dowodu, że potrafią paść: celowe uszkodzenie badanego mechanizmu (mutacja na kopii
albo z przywróceniem pliku) lub podłożony przypadek, który je wywraca, z logiem w dowodach. Asercja, która
przechodzi także bez badanego mechanizmu, to luka (`test-gap`), nie zaliczenie. Mutacja nie zapisuje
niczego poza katalogiem tymczasowym; po niej `git status` bez zmian.

## Porażki

Przy porażce kieruj poprawkę do właściciela kodu/testu. Dostosowanie testu wymaga
uzasadnienia zmianą kontraktu. Nowe xfail nie spełnia kryterium akceptacji. Nowa faza review
nie zeruje licznika tej samej usterki (limit 3 cykli w rdzeniu `feature`); inne niezależne
kroki mogą być kontynuowane. Przy `blocked` podaj dowody, hipotezy i potrzebną decyzję/dostęp.

### Triage porażki

Triage jest obowiązkowy po każdym nieudanym uruchomieniu, także dla testów napisanych w tej pracy. Tabelę zapisz przed poprawkami:

| Test | Dowód (pierwszy błąd, log) | Diagnoza (produkt / test / środowisko) | Pewność (wysoka / średnia / niska) | Właściciel (produkt / `test-writer` / wspólny) | Następny krok (poprawka albo decyzja) |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

Każda porażka albo grupa o wspólnej przyczynie dostaje dokładnie jedną z klas: regresja produktu; usterka testu (np. kruchy lokator, błędna asercja, fixture, sprzątanie); środowisko lub dane (np. usługa niedostępna, dryf uwierzytelnienia, kolizja współdzielonego stanu). Wiersz to jeden test, grupę wolno zapisać w jednym wierszu. Zbiorcze zdanie, że testy nie przechodzą, bez dowodu i diagnozy każdej pozycji jest niedopuszczalne. Raport roli: `.skillsync/runs/<run-id>/<krok>-test-runner.md` (zob. [raporty](reports.md)); orkiestrator dostaje skrót.

### Testy integracyjne i E2E pisane przez `test-writer`

Konwencje testów jednostkowych projektu (np. `TestCase` z rollbackiem i fixtures w Django, mechanizmy izolacji w .NET) mają pierwszeństwo. Zasady poniżej są wzorcem dla testów integracyjnych i E2E:
- Przed napisaniem testu zbadaj działającą aplikację lub ścieżkę: rzeczywiste żądania i odpowiedzi. Nie zgaduj lokatorów ani kształtów danych.
- Nie wpisuj na sztywno id encji i nie polegaj na danych seed; test tworzy własne dane i sprząta je w `finally`/teardown, także po porażce.
- Timeout i ponowienia ustawia konfiguracja runnera, nie kod testu; testy są deterministyczne i niezależne od kolejności uruchomienia.
- Jeden scenariusz na test; nazwa opisuje zachowanie.

## Baseline i stare porażki

Stare porażki porównuj według identyfikatorów testów i przyczyn w tym samym środowisku,
nie liczby błędów. Bazowy commit nie odtwarza wcześniejszych niezacommitowanych zmian:
uwzględnij stan początkowy. Niepełny baseline ogranicza wniosek — nie deklaruj braku regresji
dla całego systemu. Znane stare błędy zapisz jako dług poza zakresem.
