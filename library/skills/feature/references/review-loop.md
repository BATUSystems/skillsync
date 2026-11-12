# Pętla przeglądu: spec, plan, kod

Czytaj przed przeglądem speca, planu albo kodu. Każdy akt `feature` kończy się pętlą: rundy przeglądu w świeżym
podumyśle, aż runda zwróci same drobiazgi; liczbę rund wyznacza kategoria pętli ([Kategoria pętli i przejścia](#kategoria-pętli-i-przejścia)). Wspólne są runda, wagi, bezpieczniki, stany i zapis; profil ustala materiał,
checklistę i warunek stopu. Przykład: specyfikację recenzuje inny model w nowej sesji, a autor nanosi blockery
i ważne uwagi, drobne tylko gdy są tanie, a każde odrzucenie uzasadnia.

## Profile i zakres

Ryzyko planu = najwyższe ryzyko kroku w planie (kolumna ryzyka, [plan](plan-details.md)); to samo idzie do `readiness --risk`.

| Profil | Akt | Krok checkpointu | Pętla obowiązkowa | Inaczej |
|---|---|---|---|---|
| `spec` | I | `spec-review` | zawsze przy `plan-large` | — |
| `plan` | II | `plan-review` | `plan-large`, ryzyko planu high, prośba użytkownika | jedna runda opcjonalna, bez pętli |
| `kod` | III | `code-review` (końcowa), `code-review-<krok>` (po fazie) | końcowa zawsze; raz na fazę po jej zielonym szczeblu, bez warunku ryzyka (ryzyko decyduje o kategorii pętli) | — |
| `docs` | III | `code-review` (zamiast profilu `kod`, gdy zmiana dotyka wyłącznie dokumentacji) | jedna runda; pętla pełna tylko przy poprawkach dotykających strażnika wycieków, migracji lub bezpieczeństwa (jak w „Kategorii pętli”) | — |

Kolumna „Pętla obowiązkowa” mówi, kiedy pętla jest; ile ma rund (pełna albo jednorundowa), wyznacza sekcja „Kategoria pętli i przejścia”.

`direct`, który odsłoni zmianę kontraktu, przechodzi do speca (`feature`, krok 4), więc dostaje profil `spec`.
Profil `docs` wybiera `skillsync review` (pole `profile`), gdy zmiana dotyka wyłącznie `docs/**`, plików `.md` skilli biblioteki, `CHANGELOG*` albo notatek wydania; przy zmianie mieszanej profil to `kod`, a pozycje `docs` z checklisty dostają pliki dokumentacji (`docsFiles`). Ścieżki i pozycje można nadpisać w manifeście (`workflow.review.docs_paths`, `workflow.review.docs_checklist`).
Challenger pomysłu i `pre-implement` są poza licznikiem rund. Kolejność w akcie I: odpowiedzi na `Q`, `pre-implement`
(gdy dotyczy), pętla `spec`, „GO plan”; w akcie II: plan, pętla `plan`, `readiness`, „GO implement”.

## Materiał i checklista profilu

- **`spec`**: spec, instrukcje projektu, plik FRD (gdy jest). Jedna niezależnie wdrażalna zdolność (inaczej „podziel”),
  decyzje z alternatywami, kontrakty, dane, migracja, odwrócenie, ryzyka z ryzykiem rezydualnym, kryteria akceptacji
  testowalne i zmapowane na wymagania FRD, tabela stanów i ścieżki porażek, pojęcia o dwóch znaczeniach (glosariusz),
  zgodność z instrukcjami projektu.
- **`plan`**: plan i spec z linii `Spec:` (bez speca: sam plan z kryteriami). Kryteria ↔ kroki ↔ TESTY (mapowanie),
  ryzyko kroków, zasoby i równoległość, konflikt stanów między krokami (zmiana w A łamie warunek wstępny B), ścieżki
  porażek, ukryte założenia, dokumentacja i wiedza, pytanie o wersję.
- **`docs`**: zmienione dokumenty, CHANGELOG, notatki wydania i skille, plus dokumenty i narzędzia, do których się odwołują. Każda obietnica (zachowanie, komenda, kod wyjścia, wersja, „działa na X”) ma dowód: test, CI, zrzut albo log; bez dowodu musi być oznaczona jako niepotwierdzona. Instrukcje są zgodne z narzędziami, które je sprawdzają (`check_*`, walidatory, `doctor`): uruchom opisaną komendę na przykładzie albo wskaż jej test. Skille i dokumenty nie przeczą sobie (ceremonia a sygnały retro, nazwy komend i flag, ścieżki). Tylko bieżący stan: bez „dawniej X”, starych aliasów i opisu sprzed zmiany. Bez nazw osób, klientów i wydarzeń w publicznym rdzeniu; tekst PR i notatek przechodzi `skillsync leakcheck --text`.
- **`kod`**: materiał z [review-material](review-material.md) (diff roboczy i staged, nowe pliki, stan początkowy, plan,
  spec, FRD, logi testów) i checklista roli `reviewer` z `skillsync review`. Zakres diffu:
  - pętla po kroku: diff plików kroku z listy planu;
  - pętla końcowa przy ryzyku planu high: pełny diff w każdej rundzie;
  - pętla końcowa przy niższym ryzyku: runda 1 pełny diff, od rundy 2 poprawki i pliki, których dotknęły, a pełny diff do wglądu.

  W pętli pełnej zamknięcie uwag sprawdza recenzent następnej rundy; w pętli jednorundowej zamknięcie majorów sprawdza świeży `verifier` (tabela przejść). Poza pętlą `verifier` służy do punktowych twierdzeń.

## Runda

- Każda runda to **nowy podumysł `reviewer`** w profilu pętli, jedno zlecenie. Nigdy nie wznawiaj autora ani poprzedniego
  recenzenta (wyjątek od zasady „do poprawek wznawiaj tego samego agenta” z adaptera hosta).
- Model: ustal model sesji wyłącznie z kontekstu systemowego, bez zgadywania. Claude: sesja na innym modelu niż domyślny
  recenzent (`sonnet`) → domyślny; sesja na `sonnet` → `model: opus` w wywołaniu; model sesji nieznany → domyślny
  i zapis „model sesji nieustalony”. Profil `kod` przy ryzyku planu high może dostać `model: opus` także w innych sesjach.
  Codex: model recenzenta ustawia tylko `codex.agent_overrides` w manifeście, więc zwykle „ten sam model” i zapis tego
  w raporcie. Stop nie zależy od różnicy modeli.
- Zlecenie: profil, ścieżki materiału i instrukcji (w projekcie pełne ścieżki, np. `.claude/skills/feature/…`), a od rundy 2
  także plików decyzji wcześniejszych rund (`Id | Waga | Opis | Rozstrzygnięcie | Powroty`, przy odrzuconym blocker/major
  z uzasadnieniem autora) z poleceniem, by otworzyć je dopiero po niezależnym przeglądzie (kolejność deklaratywna).
- Test-writer fazy pracuje równolegle z rundą R1 tej fazy (jego testy integracyjne sprawdza runda 2 albo, w pętli jednorundowej, świeży `verifier` przed odhaczeniem kroku sprawdza testy test-writera względem kryteriów kroku (czy je pokrywają i czy potrafią paść), a dowód (raport weryfikatora) trafia do rekordu checkpointu kroku), ale tylko w fazach, które mają
  test-writera w planie; bez niego runda R1 idzie sama.
- Przy `ceremony: demo` [ceremonia demo](ceremony-demo.md) zmienia liczbę pętli: jedna pętla `kod` na przebieg zamiast pętli po każdej fazie, plus pętla pełna
  dla kroków ze strażnikiem wycieków, migracją albo bezpieczeństwem; pętla końcowa pozostaje pełna, gdy cała zmiana należy do kategorii pętli pełnej z tej sekcji. Tylko w `demo` jednorundowa pętla `kod` ma najwyżej 2 rundy ([ceremonia demo](ceremony-demo.md)): druga wyłącznie przy blockerze, co najmniej 3 majorach, poprawkach dotykających strażnika wycieków, migracji lub bezpieczeństwa albo po drugim `REFUTED`; w pozostałych przypadkach majory zamyka `verifier`. Pętle pełne i tryb `full` bez zmian.
- Recenzent zwraca: status, nowe uwagi (waga, miejsce, problem, propozycja) i od rundy 2 tabelę zamknięć, **jedna linia na
  każde id**, poza limitem długości raportu. Zamknięcie sprawdza na plikach, nie na deklaracji autora. Dla odrzuconego
  blocker/major orzeka „uzasadnienie przyjęte / nieprzyjęte”. Powrót uwagi dopasowuje recenzent: nowa uwaga podaje
  id wcześniejszej, do której wraca, a wiersz zamknięcia ma status „otwarta”; orkiestrator zwiększa wtedy „Powroty”.
- Wagi i status: rola `reviewer`, sekcja „Profile pętli”. Autor nie obniża wag.

## Po rundzie (autor, czyli orkiestrator)

- Każdy blocker i major: nanieś albo odrzuć z uzasadnieniem. Minor: nanieś albo odrzuć z uzasadnieniem. Nit: można pominąć.
- Id uwag nadaje orkiestrator: `R<numer pliku>-<n>`. Rozstrzygnięcia w tabeli `Id | Waga | Opis | Rozstrzygnięcie | Powroty`.
- Profil `kod`: poprawki robi właściciel kodu albo testu (wznowiony agent kroku), potem testy dotkniętych obszarów
  (szerzej, gdy zmienił się wspólny kontrakt), a w pętli końcowej zestaw końcowy, zanim ruszy kolejna runda.

## Stop i bezpieczniki

Limit 4 rund i STOP dotyczą pętli pełnej. Pętla jednorundowa kończy się według tabeli przejść w „Kategoria pętli i przejścia”.

**PASS** wymaga łącznie: statusu recenzenta `PASS`, zera blocker i major wśród uwag nowych i otwartych (zamknięte
i odrzucenia z przyjętym uzasadnieniem się nie liczą), pełnej tabeli zamknięć (brak wiersza dla id = uwaga otwarta)
oraz braku pytań Q/C i werdyktu „podziel”. Profil `kod` dodatkowo:
- dowód, że asercje negatywne i smoke potrafią paść;
- **po kroku**: runda startuje po ustabilizowaniu plików kroku (koniec pracy `implementer` i `test-writer` tego kroku; wyjątek: test-writer pracujący równolegle z R1 nie wstrzymuje rundy, bo R1 dotyczy kodu produkcyjnego, a jego testy sprawdza runda 2 albo świeży `verifier` (czy testy pokrywają kryteria kroku i potrafią paść; dowód w rekordzie checkpointu kroku)),
  testy obszaru kroku są zielone na przeglądanym stanie; krok w Progress odhacza się po PASS jego pętli (krok bez pętli: po testach);
- **końcowa**: zestaw końcowy zielony (logi nowsze niż ostatnia zmiana), a dokumentacja (`scribe`), changelog i ustalony
  numer wersji są w diffie przed rundą 1. Zmiana dokumentacji, changeloga, wersji albo kodu po PASS unieważnia dowód
  (nowa runda), z dwoma wyjątkami: zapisy księgowe (Progress, Wynik i `Przebieg` planu, status speca, rekordy
  `.skillsync/runs`, rekordy wiedzy z „Odkryć” w `docs/lessons` i `docs/domain`, „Zdobyta wiedza”) nie unieważniają;
  poprawka minor albo mechaniczna po PASS z minorami nie wymaga rundy, jeśli zestaw końcowy zostaje zielony na nowym
  stanie, a poprawka jest wymieniona w Wyniku.

PASS istnieje tylko z raportu podumysła: gdy nie da się go uruchomić (brak delegacji, limit), runda jest `BLOCKED`
i pytanie do użytkownika; orkiestrator nie przegląda sam.

Runda `BLOCKED`, przerwana albo z uciętym raportem jest **nieważna**: nie daje PASS, powtarza się ze świeżym podumysłem
i nie liczy się do limitu.

**STOP i pytanie do użytkownika** z obiema racjami, bez kolejnej rundy, gdy: 4 ważne rundy bez PASS (osobny licznik
każdej pętli, także każdej `code-review-<krok>`); nieprzyjęte uzasadnienie odrzucenia blocker/major; drugi powrót tej
samej uwagi blocker/major po naniesieniu (pierwszy powrót to zwykła poprawka); dwie nieważne rundy z rzędu. Po STOP decyduje użytkownik:
- `spec` i `plan`: GO, kolejna runda z licznikiem od 1 albo zmiana planu. GO po STOP bez rundy PASS zostawia krok pętli
  na `BLOCKED` (przebieg `incomplete`): powód i decyzję użytkownika opisz w „Wyniku” planu; nie zapisuj sztucznego `PASS`.
- `kod`: STOP wyklucza `done`; użytkownik wybiera kolejną rundę (licznik od 1), zawężenie zakresu (zmiana planu) albo plan `blocked`.

Prośba o „GO plan” albo „GO implement” wymienia jawnie każde odrzucenie blocker/major z uzasadnieniem i orzeczeniem
recenzenta oraz pozostałe minory; „Wynik” planu robi to samo dla pętli kodu.

## Kategoria pętli i przejścia

Pętlę pełną (do 4 rund, warunek PASS i STOP jak wyżej) dostają tylko zmiany w strażniku wycieków, migracje
i bezpieczeństwo. Pozostałe zmiany dostają jedną rundę, a drugą tylko wtedy, gdy runda znajdzie blocker.
Bezpieczeństwo to zmiany granic dostępu: uprawnień i słów zgody, uwierzytelniania, sekretów, odczytu albo zapisu
poza projektem oraz wykonywania poleceń z danych zewnętrznych.

Pętla `kod` biegnie raz na fazę planu, po jej zielonym szczeblu testów; runda po każdym drobnym kroku to nadmiar ceremonii
(`ceremony-excess`). Plan zapisuje kategorię pętli każdego kroku (przy kroku albo w opisie pętli po krokach). Krok dotykający strażnika wycieków,
migracji albo bezpieczeństwa dostaje pętlę pełną `code-review-<krok>` niezależnie od kolumny ryzyka. Spec, plan i pętla końcowa dostają pętlę pełną tylko wtedy, gdy zmiana w całości należy do jednej
z tych kategorii; kroki z pętlą pełną mają ją już po kroku. Runda po zmianie przeglądanego materiału, gdy pętla dała
już PASS, zaczyna nową pętlę tego kroku, a nie jest jej kolejną rundą.

| Wynik rundy | Rekord checkpointu | Dalej |
|---|---|---|
| `PASS` recenzenta, zero blocker i major | `PASS` | koniec pętli |
| pętla jednorundowa: major bez blockera | `FAIL` z kluczem przyczyny | autor nanosi albo odrzuca uwagi z uzasadnieniem i zapisuje tabelę decyzji; świeży `verifier` sprawdza zamknięcie każdego majora i każde odrzucenie majora; potem rekord `PASS` tego samego kroku (`--attempt` + 1) z dowodami: tabela decyzji i raport weryfikatora; „Wynik” wymienia odrzucone majory |
| weryfikator: `REFUTED` dla zamknięcia | — | poprawka i ponowna weryfikacja tej uwagi, bez nowej rundy; drugi `REFUTED` tej samej uwagi oznacza rundę 2 w świeżym recenzencie |
| blocker (dowolna pętla) | `FAIL` z kluczem przyczyny | poprawki i runda 2 w świeżym recenzencie; runda bez blockera kończy się jak w wierszu drugim |
| pętla pełna (strażnik wycieków, migracja, bezpieczeństwo) | według zasad „Stop i bezpieczniki” i „Zapis” | do 4 rund, warunek PASS i STOP bez zmian |

Runda 2 i dalsze w pętli jednorundowej, choć poprzednia runda nie znalazła blockera, oraz powtórzenie kontroli na
niezmienionym stanie to sygnał `ceremony-excess` retrospektywy ([retro-quality](retro-quality.md)). Wyjątek: runda 2
po drugim `REFUTED` tej samej uwagi nie jest nadmiarem ceremonii, bo weryfikator już wykazał, że zamknięcie nie działa. Tak samo w `demo` runda 2 z powodów z [ceremonii demo](ceremony-demo.md) (blocker, co najmniej 3 majory, poprawki w strażniku wycieków, migracji lub bezpieczeństwie).

## Stany

| Sytuacja | Postępowanie |
|---|---|
| Pytania Q/C albo „podziel” z uwag (także przy statusie `PASS`) | pauza; runda, która je zgłosiła, liczy się do limitu, sama pauza nie; po odpowiedzi kolejna runda |
| Użytkownik zmienia kryteria akceptacji albo zakres | licznik od 1 w pętlach, których to dotyczy |
| Edycja speca albo planu po rundzie PASS | tylko mechaniczna albo minor; zmiana merytoryczna = nowa runda |
| Inna korekta użytkownika przy GO | jego decyzja, bez rundy, zapis w planie |
| Zmiana speca w akcie II, mechaniczna albo minor (redakcja, doprecyzowanie bez zmiany decyzji, kontraktu, zakresu ani kryteriów) | poprawka speca, przeliczony `Spec-sha` w planie i lista zmian od „GO plan” w prośbie o „GO implement”, bez nowej pętli `spec` |
| Zmiana speca w akcie II, merytoryczna (decyzje, kontrakty, zakres, kryteria) | akt I od nowa: spec `draft`, plan `draft (czeka na spec)`, pętla `spec` od 1, „GO plan” (zdejmuje sufiks, plan wraca do `draft`), nowy `Spec-sha` w tym samym planie, pętla `plan` od 1; PASS `plan-review` sprzed tej zgody jest nieważny |
| Zmiana speca w akcie III | plan `in_progress (blocked — czeka na spec)`, spec wraca do `draft`, stara linia `Spec-sha` znika z planu; pętla `spec` od 1, potem jedno GO zdejmujące blokadę, `approved` i nowy `Spec-sha` ([spec](spec.md)); pętla `plan` tylko, jeśli plan zmienia się merytorycznie |
| Poprawki po code review człowieka ([pr-after-approval](pr-after-approval.md)) | pętla `kod` z licznikiem od 1; przy ryzyku planu niższym niż high już runda 1 dostaje poprawki i dotknięte pliki (pełny diff do wglądu), przy high pełny diff |

## Zapis

- Zamrożony raport recenzenta `.skillsync/runs/<run-id>/<krok>-N.md` (`spec-review-N.md`, `plan-review-N.md`,
  `code-review-N.md`, `code-review-<krok>-N.md`; dowód rekordu, bez późniejszych zmian; N sekwencyjne w kroku przebiegu,
  także po resecie licznika) i osobno `<krok>-N-decyzje.md` z licznikiem limitu `k/4`.
- Checkpoint pod jednym krokiem na pętlę, `--attempt` rośnie monotonicznie w kroku przebiegu. Runda z blocker/major =
  `FAIL` z `--cause`; runda z blocker/major i pytaniami = `FAIL`, a pauza osobnym rekordem; runda nieważna i STOP =
  `BLOCKED` z `--cause` (dowodem jest to, co zwrócono, albo notatka orkiestratora o przerwaniu); pauza = `NOT_RUN`
  z `--cause`; runda końcowa `PASS` (także z minorami) z własnym plikiem dowodu.
- Klucze przyczyn: rundy `spec-review` i `plan-review` **bez `--cause-key`** (uwagi przeglądu to normalna praca przeglądu,
  a klucz o samym fakcie uwag, np. `plan-review-changes`, po decyzji `rejected` w rejestrze projektu daje w retro fałszywe
  `recurred`; klucz merytoryczny tylko, gdy uwagi rundy mają jedną wspólną przyczynę). Rundy `code-review*` **z kluczem
  merytorycznym** z `workflow/cause-keys.yaml` (`docs-behind-code`, `test-gap`, `state-rule-gap`, …), bo zasilają
  kandydatów retro; rekord `PASS` po rundach `FAIL` ma kolejny `--attempt` i klucz ostatniej rundy `FAIL`. Pomiar: [measurement](measurement.md).
- „Wynik” planu podaje liczbę rund na akt i liczbę uwag według wag w każdej rundzie.
