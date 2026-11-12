# Plan: wymagana zawartość i zatwierdzenie

Czytaj przy pisaniu planu `docs/plans/RRRR-MM-DD-<slug>.md`.

Nagłówek planu ma linię `Przebieg: <run-id>` z identyfikatorem użytym w `checkpoint`; hook i `doctor` sprawdzają po niej zamknięcie.
Opcjonalna linia `Oddanie: <gałąź>` zapisuje odpowiedź użytkownika, dokąd „CP” oddaje pracę w trybie `direct` (rdzeń AGENTS); bez niej
„CP” wypycha tylko gałąź roboczą.
Linia `Ceremonia: demo` (opcjonalna przy `full`) zapisuje wartość `workflow.ceremony` z `skillsync route`; rozbieżność z `route` przy „GO implement”
to pytanie do użytkownika ([ceremonia demo](ceremony-demo.md)).

Plan dużej zmiany ma w nagłówku linię `Spec: <docs/specs/RRRR-MM-DD-slug.md>` (jak `Przebieg:`, osobny wiersz, sama ścieżka),
a od „GO plan” `Spec-sha: <git hash-object speca>` (plan-szkic, [plan-large](plan-large.md) §5). Plan przy specu nie powtarza jego treści:
ma dosłowne nagłówki `Cel` (z odnośnikiem do speca), `Kryteria akceptacji (mapowanie)` (tabela kryterium speca → kroki → dowód; kryteria
i mapa wymagań FRD są w specu), `TESTY` (komendy), `Dokumentacja i wiedza` oraz kroki, zasoby, wersję, Progress i Wynik. Przy FRD plan
zapisuje sha pliku FRD i założenia z `LUKI` ([frd-intake](frd-intake.md)). Statusy, sha i wznowienie speca: [spec](spec.md).

Plan ze statusem `draft` zawiera: cel, kryteria akceptacji (przy specu: mapowanie), kroki z rolą, plikami i ryzykiem kroku, zależności,
instrukcje/skille do odczytu, TESTY, zasoby, ryzyka, **Dokumentacja i wiedza**, **Wersja i changelog**
(propozycja wersji; pytanie do właściciela) oraz otwarte pytania. Przy linii `Spec:` zakres, kontrakty, migracja i ryzyka są w specu, a ścieżka jest względna do katalogu projektu. Sekcja wiedzy wskazuje dokumenty
dezaktualizowane przez zmianę, spodziewaną wiedzę domenową i właściciela zapisu
([zasady](../../workflow/references/knowledge.md)); „nie dotyczy” wymaga uzasadnienia. Użyj też
rekordów `domain` zwróconych przez `route`. TESTY: komendy, cwd, środowisko, testy do dostosowania,
nowe testy i porównanie z baseline przy starych porażkach ([szczegóły](tests-baseline.md)).

Sekcja **Terminologia domenowa** zawiera potwierdzone terminy do dodania/aktualizacji,
kandydatów z hipotezą znaczenia i źródłem/pytaniem do wyjaśnienia albo uzasadniony brak nowych
terminów. Przed interpretacją zadania przeczytaj rekordy `load: bootstrap`; do glosariusza
trafiają tylko definicje potwierdzone ze źródłem ([zasady](../../workflow/references/knowledge.md)).

Wersja: propozycja i pytanie w akcie II, odpowiedź najpóźniej przy „GO implement”; numer trafia do diffu przed rundą 1 pętli końcowej
`kod` ([pętla](review-loop.md)).

Statusy szkicu w akcie II: `draft` (plan w pisaniu albo w pętli `plan`), `draft (czeka na GO implement)` (pętla zakończona, `readiness`
zrobione), `draft (czeka na spec)` (zmiana merytoryczna speca w akcie II; „GO plan” zdejmuje sufiks). Po „GO implement” przeczytaj ponownie ewentualne zmiany
użytkownika, porównaj `Spec-sha` i ustaw `approved`, potem `in_progress`. Plan zatwierdzony przez `skillsync plan approve` albo
przyciskiem (zmienia tylko plan): wznowienie według tabeli w [spec](spec.md), np. przy specu `draft` STOP i pytanie.
Plany sprzed tych zasad, także duże, pozostają poprawne bez speca: bez migracji, wznowienie jak dotąd.
Statusy: `draft`, `approved`, `in_progress`, `done`, `archived`; blokadę zapisz w nawiasie po wartości (`in_progress (blocked — powód)`), nie jako status. `skillsync plan archive|unarchive <projekt> <plik>` przełącza tylko `done` ↔ `archived`.
Dla kontraktów/API/danych/wysokiego ryzyka przed zapisem kodu użyj `pre-implement`: odczytaj
rzeczywistych konsumentów i zapisz sekcje Kontrakty, Migracja, Ryzyka: w specu, gdy istnieje (akt I, przed pętlą `spec`), inaczej w planie; TESTY zawsze w planie.
`skillsync readiness <projekt> --plan <plik> --risk high --files <ścieżki>` kontroluje strukturę;
poprawność merytoryczną ocenia orkiestrator/reviewer; przy specu `readiness` idzie w akcie II, przed „GO implement”. Plan `direct`
przy ryzyku planu high albo na prośbę właściciela przechodzi [pętlę przeglądu](review-loop.md) w profilu `plan`; pozostałe plany mogą dostać jedną rundę.

## Kroki, Exec i Progress

Krok planu to faza: spójny kawałek, który kończy się działającą aplikacją, zielonym szczeblem testów i commitem (zwykle 5–8 na
plan, np. model i migracja; reguły i silnik; API; UI; testy integracyjne i e2e; domknięcie). Kilkanaście drobnych kroków mnoży
przekazania między rolami (implementer → test-writer → review → verifier → commit) i to one, nie zakres, wydłużają przebieg.
Krok ma stały id i wiersz w tabeli o kolumnach `Id | Krok | Rola | Exec | Ryzyko kroku | Pliki`:

| Id | Krok | Rola | Exec | Ryzyko kroku | Pliki |
|---|---|---|---|---|---|

Faza z FE i BE zaczyna od kroku kontraktu (DTO, endpointy, migawka kontraktu w commicie); potem FE i BE idą równolegle na tym samym
kontrakcie, bez tury „dopasowania”. Test-writer pracuje równolegle z review R1 fazy (jego testy sprawdza runda 2 albo, w pętli jednorundowej, świeży `verifier` przed odhaczeniem kroku sprawdza testy test-writera względem kryteriów kroku (czy je pokrywają i czy potrafią paść), a dowód (raport weryfikatora) trafia do rekordu checkpointu kroku), ale tylko
w fazach, które mają test-writera w planie. Szacunek kalibruj na ok. 75–105 min pracy na zdolność z pełną pętlą review; spec
z co najmniej 5 zdolnościami to dwa plany ([plan-large](plan-large.md)).

Ryzyko kroku to `low`, `medium` albo `high`. Ryzyko `high` dostaje krok dotykający kontraktu, danych, uprawnień lub migracji i ma zaraz po sobie własną [pętlę przeglądu](review-loop.md) w profilu `kod`. Najwyższe ryzyko kroków to ryzyko planu: przekaż je do `readiness --risk` i ono wyznacza zakres diffu końcowej pętli. Bez kolumny ryzyka plan bierze ryzyko z sekcji `Ryzyka`, a gdy jej brak, zadaj pytanie.

`Exec` ustalaj raz, przy planowaniu: `inline` (sesja główna), `dispatch[:tier]` (osobny podumysł na każdy krok), `group:<id>[:tier]` (jeden podumysł na serię kroków). Brak wartości znaczy `inline`. `tier` to klasa modelu: `cheap` dla kroku w pełni wyspecyfikowanego, `standard`, `capable` dla kroku trudnego; klasę na model tłumaczy adapter hosta, a modelu, którego dostępności nie potwierdzono, nie deklaruj.

Sekcja `## Progress` jest obowiązkowa, parsowana przez narzędzia i stoi na końcu planu; jej wzór:
```markdown
## Progress
Pole puste (`- [ ]`) = do zrobienia, zaznaczone (`- [x]`) = zrobione; po wylądowaniu kroku dopisz jego sha po ` — `, a id ani tytułów nie zmieniaj.
PR: #123 (link: https://example.com/pr/123)

### Faza 1: Przykład
- [x] F1.1 Kod BE ‖ FE (implementer) — moduł 631/631
- [x] F1.2 Szczebel testów i commit kodu — 1a2b3c4
- [ ] F1.3 Testy integracyjne (test-writer) i commit
- [ ] F1.4 Review i poprawki
- [ ] F1.5 `--fast` końca fazy i checkpoint fazy
```
- Faza jest jednostką review i commitów, a **podkrok fazy jednostką raportowania**: każda faza ma podkroki `Fn.1…Fn.k`
  (typowo: kod BE ‖ FE, szczebel testów i commit kodu, testy integracyjne i commit, review i poprawki, `--fast` końca
  fazy i checkpoint), odhaczane z sha albo wynikiem; raport postępu po każdym podkroku albo co 30 minut, co nastąpi
  pierwsze (jedna linia na podkrok: podkrok, wynik albo sha, czas). Starsze plany z krokami bez kropki (`S1`, `S2a`, `K1`) są poprawne.
- Kroki to pozycje `- [ ]` / `- [x]` z id i tytułem; krok odhacz dopiero po sprawdzeniu dowodów (checkpoint, wynik polecenia), nigdy na samej deklaracji podumysłu.
- Ostatni krok `Progress` to zamknięcie przebiegu (rola: orkiestrator): „Wynik”, `retro-quality`, checkpoint `--final`,
  `Status: done` planu i `implemented` speca. Bez niego plan po ostatnim `[x]` zostaje `in_progress` i `skillsync status`
  zgłasza `needsHuman: closure`. Przy `ceremony: demo` `retro-quality` jest odroczone do podsekcji „Dług (ceremony: demo)” w „Wyniku” ([ceremonia demo](ceremony-demo.md)).
- Fazy to nagłówki poziomu 3 lub niższego; nagłówek poziomu 2 zamyka sekcję `Progress`.
- Linia `PR: #<n> (link: <url>)` zaczyna wiersz (nie jest punktem listy ani cytatem) i stoi pod `## Progress`; opcjonalna `Ticket: <KEY> (link: <url>)` ma klucz Jira, np. `ABC-123`.
- Plany bez `Progress` (starsze) są poprawne i nie wymagają migracji; opcjonalny przebieg do PR-a opisuje [pr-run](../../pr-run/SKILL.md).

## Bramka prototypu

Plan ze zmianą UI ma w nagłówku (jak `Spec:`, osobny wiersz, sama ścieżka względna do katalogu projektu) linię
`Prototyp: <root>/<slug>` dla każdego prototypu, który powstał do tej zmiany.

- `skillsync prototype gate <projekt> --plan <plan>` uruchom przed prośbą o „GO implement” i ponownie po jej otrzymaniu.
  `readiness` z linią `Prototyp:` daje BLOCKED, gdy bramka ma FAIL.
- FAIL bramki = odmowa startu aktu III z listą brakujących akceptacji (`slug`, ekran, powód: brak, nieaktualna,
  needs_migration, brak_ekranow, niejednoznaczny_ekran). BLOCKED (np. `prototype_not_found`, `invalid_prototype_line`) = zgłoszenie, nie obejście.
- Wyjątek (D16-Q2): tylko właściciel w karcie słowami „GO implement bez akceptacji prototypu”. Agent dopisuje wtedy w nagłówku
  linię `Bramka prototypu: pominięta — <powód> — <osoba>, <RRRR-MM-DD>`, cytując powód. Bez tych słów właściciela agent
  tej linii nie dopisuje i nie usuwa linii `Prototyp:`. Dyspozytor ani agent nie obchodzą bramki.
- Akceptuje człowiek (UI albo CLI z hasłem z TTY); agent nigdy nie uruchamia `skillsync prototype accept` ani `unaccept`.
  `gate` pokazuje `via` i `who`, więc akceptację z CLI widać przy prośbie o „GO implement”.
  Gdy `accept` mimo to zostanie uruchomione (przez pomyłkę albo inną sesję) i zwróci `BLOCKED` `error: tty_required`, mów wprost „kliknij Akceptacja na stronie prototypu” i czekaj (`prototype wait`); zabezpieczenie zostaje.
- Akceptacja prototypu to nie zatwierdzenie planu („GO implement”, `plan approve`); jedno nie zastępuje drugiego.

### Bramka per faza (`[UI]`)

Faza dotykająca ekranu dostaje znacznik `[UI]` w komórce `Id` albo `Krok` tabeli kroków (np. `F2 [UI]`). Plan z fazami `[UI]` ma bramkę
per faza: faza `[UI]` wymaga akceptacji prototypu przed startem, a fazy bez znacznika startują po „GO implement”.
Przed startem fazy `[UI]` uruchom `skillsync prototype gate <projekt> --plan <plan> --phase <Fn>`; FAIL albo BLOCKED = faza nie startuje.
Brak JSON-a przy kodzie wyjścia 2 (starszy `skillsync` bez `--phase`) = BLOCKED.
Znacznik jest rozpoznawany dosłownie: `[UI]` w środku komórki `Krok`, w apostrofach albo poza tabelą kroków nie liczy się, a zdublowane id fazy wyłączają fazy planu (plan wraca do bramki całego planu); pisz znacznik na początku albo końcu komórki `Krok` albo w komórce `Id`.
Faza `[UI]` wymaga linii `Prototyp:`; bez niej `gate` i `readiness` dają BLOCKED `ui_phase_without_prototype` (chyba że jest obejście właściciela).
Plan z linią `Prototyp:` bez żadnej fazy `[UI]` działa jak dotąd: cały plan za bramką.
Przy planie z fazami `[UI]` FAIL bramki całego planu nie blokuje faz bez znacznika ani `readiness`; o starcie fazy decyduje `prototype gate --phase <Fn>`.
Prośba o „GO implement” dla planu z fazami `[UI]` wymienia jawnie fazy `[UI]` i fazy bez znacznika (pola `uiPhases` i `nonUiPhases`
z `skillsync readiness`), żeby podział zatwierdzał użytkownik.

