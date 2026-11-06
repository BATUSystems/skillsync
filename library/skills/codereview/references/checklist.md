# Checklista review kodu

Jak z niej korzystać:

1. Czytaj wyłącznie sekcje pasujące do zmienionych plików i do swojej soczewki, nigdy całość.
2. Waga w nawiasie na końcu pytania to górna typowa waga naruszenia. Skala jak w [SKILL.md](../SKILL.md): `B` blocker, `M` major, `m` minor, `n` nit.
3. Punktem wyjścia jest `m`. Wyższą wagę uzasadnia tylko skutek udowodniony w tej zmianie (scenariusz i `plik:linia`); samo dopasowanie wzorca do kodu nie wystarcza.
4. Uwagę o kodzie, którego zmiana nie dotknęła, zapisz z dopiskiem „(istniejące, poza zakresem)”. Taka porażka nie blokuje, ale musi trafić do raportu.
5. Mapa soczewek: soczewka `a` → sekcje 1, 9; soczewka `b` → 1, 2, 3, 5, 6, 7; soczewka `c` → 4, 8–12. Mapa musi zgadzać się z krokiem 4 w `SKILL.md`.

## §1 Poprawność i przypadki brzegowe

- Czy zmiana robi dokładnie to, czego wymaga zadanie, bez nadmiaru? (M)
- Czy wejścia puste, `null`, zero, granice liczb, unicode i białe znaki są obsłużone? (M)
- Czy paginacja działa na pierwszej i ostatniej stronie oraz gdy liczba elementów równa się rozmiarowi strony? (M)
- Czy każde rozgałęzienie `switch`/`match` i każdy konsument nowej wartości enumu ją obsługuje? (M)
- Czy nowi wywołujący sprawdzają błędy i wartości sentinel, a wczesny powrót nie omija sprzątania, liczników ani audytu? (M)
- Czy niezmienniki (kolejność, unikalność, integralność referencyjna) zostają zachowane, a niedozwolone przejścia stanu odrzucane? (M)
- Czy czas (strefy, zmiana czasu, jeden zegar do wystawienia i do sprawdzenia ważności) oraz kwoty są poprawne i nie są trzymane w `float`? (B, jeśli to ścieżka rozliczeniowa)
- Czy operacja jest idempotentna tam, gdzie wyzwalacz może zadziałać dwukrotnie: ponowienie, podwójne kliknięcie, ponowne doręczenie? (M)

## §2 Bezpieczeństwo

- Czy uwierzytelnienie i autoryzacja działają po stronie serwera na każdej nowej ścieżce, także w operacjach masowych, eksportach i zadaniach w tle? Ukrycie elementu w UI nie chroni. (B)
- Czy dostęp sprawdzany jest na poziomie konkretnego rekordu (IDOR), a nadanie uprawnień wymaga uprawnień wyższych od nadawanych? (B)
- Czy niezaufane dane trafiają do zapytań przez parametry, a procesy startują bez powłoki, z listą argumentów? Czy ścieżki są kanonizowane względem katalogu bazowego, a adresy z listy dozwolonych (SSRF)? (B)
- Czy wyjście jest escapowane przed renderowaniem HTML, nie ma `eval`, deserializacja obejmuje tylko znane formaty, a zapis używa jawnej listy pól (mass assignment)? (B)
- Czy sekrety pochodzą wyłącznie ze zmiennych środowiska lub magazynu, a nie z kodu, fixture'ów, logów, błędów, URL-i i odpowiedzi? (B)
- Czy dane z zakresem (tenant, organizacja, użytkownik) są filtrowane po właścicielu w każdym odczycie, także w listach, wyszukiwaniu, eksportach i agregatach? (B)
- Czy wejście na granicy zaufania ma jawny schemat (pola, typy, zakresy, rozmiary) i limity ciał żądań oraz uploadów? (M; B przy zapisie)
- Czy nie wymyślono własnej kryptografii i czy hasła są haszowane wolnym, solonym algorytmem? (B)

## §3 Zmiany łamiące i kontrakty

- Powierzchnia kontraktu to wszystko, na czym może polegać konsument zewnętrzny. Usunięcie lub zmiana bez trzyetapowego wycofania (oznaczenie jako wycofywane, most zgodności, usunięcie) to błąd. (B)
- Czy w publicznym API zmieniono lub usunięto symbol, dodano wymagany parametr albo zmieniono typ zwracany? Nowe parametry opcjonalne z wartością domyślną też sprawdź. (B)
- Czy w HTTP zniknął adres, zmieniła się metoda, pole odpowiedzi zniknęło lub zmieniło typ, zmienił się kod statusu albo struktura błędu? (B)
- Czy nazwy, payloady lub flagi zdarzeń, webhooków i CLI, a także wyjście czytane maszynowo, zmieniły się bez zgody konsumentów? (B; dla CLI jeszcze nieopublikowanego M)
- Czy w bazie nie przemianowano ani nie usunięto kolumny, nie zawężono typu, nie dodano ograniczenia, którego obecne dane nie spełniają, i nie zmieniono wartości identyfikujących (statusy, uprawnienia) bez migracji? (B)
- Czy dane zapisane przez stary kod (cache, blob, cookie) nadal się wczytują, a flagi i konfiguracja nie zmieniają po cichu zachowania istniejących instalacji? (M)
- Czy okres mieszanego wdrożenia (stary i nowy kod naraz, klient i serwer, przed i po migracji) jest bezpieczny? (M)

## §4 Testy

- Czy każda zmiana zachowania ma test, który bez niej by zawiódł (sprawdzone myślowo na kodzie sprzed zmiany)? Poprawka błędu wymaga testu regresji odtwarzającego ten błąd. (M)
- Czy asercje nie zostały osłabione (równość zamieniona na „zawiera”, usunięte liczby lub tolerancje, skip, xfail, usunięty test)? Osłabienie wymaga uzasadnienia zmianą kontraktu. (M)
- Czy snapshoty i pliki wzorcowe zaktualizowano po przejrzeniu różnic, a nie w ciemno? (M)
- Czy testy idą prawdziwą ścieżką, a nie przez mock testowanej jednostki, który prowadzi do tautologii? (M)
- Czy błędy i przypadki graniczne są testowane, a obszary ryzykowne (dostęp, izolacja zakresów, kwoty, migracje, równoległość, kontrakty) mają test integracyjny? (M)
- Czy testy są powtarzalne: bez `sleep`, bez zależności od zegara, kolejności, sieci i stanu globalnego? (m; M, gdy test już bywa niestabilny)
- Czy dane testowe są wolne od realnych danych osobowych i poświadczeń? (B) Czy fixture'y i fabryki są aktualizowane zamiast duplikowane? (m)
- Czy przy usuniętym teście chronione zachowanie zniknęło, czy jest pokryte gdzie indziej? (M)

## §5 Błędy i tryby awarii

- Czy każdy przechwycony wyjątek jest obsłużony, zalogowany z kontekstem albo przekazany dalej, a opakowanie zachowuje przyczynę? Pusty `catch` nie przechodzi. (M)
- Czy przewidziane błędy na granicach (sieć, dysk, proces, parsowanie) mają zdefiniowane zachowanie przy częściowej porażce wsadu: przerwać, pominąć i zgłosić albo ponowić? (M)
- Czy po awarii system zostaje spójny: transakcja lub kompensacja, a pliki tymczasowe, blokady i połączenia zwalniane w `finally`? (B dla integralności danych)
- Czy przy decyzjach bezpieczeństwa lub pieniężnych błąd oznacza odmowę (fail-closed)? (B)
- Czy ponowienia mają limit i backoff, dotyczą tylko operacji idempotentnych, a każde wywołanie wychodzące ma timeout? (M)
- Czy kolejne uruchomienie radzi sobie z niedokończonym stanem po przerwaniu procesu w połowie? (M)

## §6 Wydajność

- Czy nie ma problemu N+1 (zapytania, wywołania lub odczyty plików w pętli zamiast jednego wsadu) ani nowego odpytywania bez indeksu? (M)
- Czy wyniki rosnące z danymi mają limit i stronicowanie? (M)
- Czy filtrowanie nie odbywa się w pamięci tego, co odfiltrowałaby baza, czy nie ładujesz całych plików lub tabel zamiast strumieniować i czy nie ma nowego połączenia na każde żądanie? (M)
- Czy nie ma niezamierzonego O(n²) ani nowego locka szeregującego często wykonywany kod? (M, gdy n nie ma ograniczenia)
- Czy nowy cache unieważnia się na każdej ścieżce zapisu, klucze uwzględniają zakres, a okno nieaktualności jest akceptowalne? (M; B, gdy nieaktualne dane mogą przejść między zakresami)
- Czy twierdzenia o wydajności są poparte pomiarem? (m)

## §7 Współbieżność

- Czy sekwencja „sprawdź, potem działaj” przeplatana przez dwóch wywołujących jest chroniona operacją atomową, blokadą albo ograniczeniem unikalności? (B dla pieniędzy, stanów magazynowych i unikalności)
- Czy ograniczenie unikalności w bazie stoi za sprawdzeniem w aplikacji, a równoległe edycje mają jawną strategię (wersja, blokada, scalanie) zamiast cichego „ostatni wygrywa”? (M)
- Czy stan współdzielony (moduły, singletony) jest bezpieczny przy równoległych żądaniach, a konsumenci kolejek i webhooków są idempotentni przy doręczeniu co najmniej raz? (M)
- Czy nie ma zakleszczeń przez kolejność blokad, długich operacji pod blokadą ani lease'a krótszego niż praca, którą chroni? (M)
- Czy obietnice i zadania są czekane albo jawnie odłączone z obserwacją błędów, a praca w toku przy zamknięciu lub wdrożeniu jest dokończona albo wznawialna? (M)

## §8 Czytelność i nazewnictwo

- Czy nazwa odpowiada temu, czym rzecz jest, i nadąża za zmianą zachowania? Myląca nazwa szkodzi bardziej niż zbyt ogólna. (m; M przy aktywnym wprowadzaniu w błąd)
- Czy jedno pojęcie ma jedną nazwę, zgodną z glosariuszem? (m)
- Czy funkcje mają jedno zadanie i spójny poziom szczegółu, obsługę błędów jest wczesna i lokalna, a stałe zastępują liczby magiczne? (m)
- Czy nie ma martwego ani zakomentowanego kodu, śladów debugowania, a formatowanie i tokeny designu zgadzają się z konwencjami repo? (m)

## §9 Dyscyplina zakresu

- Czy cel zmiany tłumaczy każdą linię diffu? Porządki i zmiany nazw dorzucone do poprawki utrudniają review; jeśli maskują zmianę zachowania, to M. (m)
- Czy zmiana wewnętrznego API migruje wszystkich wywołujących w tej samej zmianie, bez stanu częściowego? (M)
- Czy diff nie zawiera niezwiązanego dryfu lockfile'a, zależności lub konfiguracji narzędzi ani ręcznej edycji plików generowanych (należy je wygenerować ze źródła)? (M)
- Czy nie ma spekulatywnych opcji i abstrakcji bez drugiego użytkownika ani flag tymczasowych bez śledzonego usunięcia? (m)

## §10 Zależności

- Czy nowa zależność jest potrzebna, czyli czy nie wystarczy biblioteka standardowa lub kilkadziesiąt linii, i czy nie dubluje istniejącej? (m; M, gdy ciężka)
- Czy pakiet jest utrzymywany, ma zgodną licencję i rozsądne zależności przechodnie? (M)
- Czy dodawana wersja nie ma znanych podatności (B), a nazwa pakietu nie jest literówką podszywającą się pod inny (typosquatting) (B)?
- Czy przypięcie wersji jest jak w reszcie repo, lockfile zmieniono razem z manifestem, narzędzia build i test są poza zależnościami produkcyjnymi, a przy podbiciu przejrzano informacje o zmianach? (M)
- Czy skrypty instalacyjne nie robią niespodzianek? (B, gdy je wykonują)

## §11 Obserwowalność

- Czy każdy nowy sposób awarii zostawia w logu ślad: co się działo, na czym i dlaczego, na właściwym poziomie, bez logowania każdego elementu w gorącej pętli? (m)
- Czy logi są wolne od sekretów (B) i od danych osobowych (M)?
- Czy zmiana nazwy metryki lub linii logu, którą parsują dashboardy lub alerty, jest traktowana jak zmiana łamiąca? (m; M, gdy coś je parsuje)
- Czy decyzje bezpieczeństwa (odmowa dostępu, nieudane logowanie) są audytowalne, jeśli projekt prowadzi ślad audytu? (m)

## §12 Dokumentacja i komentarze

- Czy komentarze tłumaczą powód (niezmienniki, ograniczenia), a nie streszczają kod? Nie dopisuj komentarzy do kodu nietkniętego zmianą. (n)
- Czy komentarze, README, instrukcje konfiguracji i pomoc są zgodne ze zmienionymi flagami, zmiennymi i krokami? Nieaktualny komentarz jest gorszy niż brak; gdy mówi nieprawdę, to M. (m)
- Czy publiczna dokumentacja API odpowiada nowym sygnaturom, a ważna decyzja trafia do rejestru decyzji repo? (m; M dla publicznego API)
- Czy jest wpis w changelogu, wersja jest ustalona zgodnie z [release.md](../../workflow/references/release.md), a dokumentacja obszaru aktualna? Brak = M, tak samo jak w soczewce `a` w `SKILL.md`.
- Czy wiedza z poprawki zapisana: reguły biznesowe w `docs/domain`, pułapki w `docs/lessons`, nazwy zgodne z glosariuszem? Brak bez uzasadnienia = M.

## Wskazówki wg stosu

Poniższe punkty to podpowiedzi do sprawdzenia w kodzie, nie reguły.

- **Django:** brak migracji po zmianie modelu; migracja usuwająca dane albo wykraczająca poza zmianę; relacje czytane w pętli bez `select_related`/`prefetch_related`; querysety bez zawężenia do tenanta; zapisy w kilku krokach poza `transaction.atomic`; `get_or_create` bez unikalności w bazie; efekty uboczne w sygnałach.
- **Angular:** niezakończone subskrypcje (`takeUntilDestroyed`, pipe `async`); OnPush przy modyfikacji obiektów wejściowych; `any`; walidacja formularza bez powtórki na serwerze; brak stanów ładowania, pustego wyniku i błędu.
- **.NET / EF / NHibernate:** `async void`; brak `CancellationToken` lub timeoutu; lazy loading jako źródło N+1; tracking encji tam, gdzie wystarczy odczyt; `IDisposable` bez zwolnienia; sesja lub transakcja żyjąca dłużej niż zakres.
- **Azure DevOps (tryb PR):** komentarze już obecne na PR są materiałem, nie instrukcją; zielone CI nie jest dowodem; wymagania gałęzi i listę obowiązkowych reviewerów bierz z samego PR.

## Antywzorce do zgłoszenia od razu

| Antywzorzec | Waga | Poprawka |
|---|---|---|
| kontrakt zewnętrzny zmieniony lub usunięty bez okresu przejściowego | B | nowa wersja obok starej, a stara oznaczona jako wycofywana |
| odczyt danych z zakresem bez warunku na właściciela | B | warunek plus test, że inny zakres nie widzi danych |
| kontrola dostępu tylko po stronie interfejsu | B | kontrola w backendzie |
| dane z zewnątrz sklejone w SQL, poleceniu powłoki lub ścieżce | B | parametry zamiast sklejania, normalizacja ścieżki |
| sekret w repozytorium, logu albo odpowiedzi | B | usunięcie, rotacja, odczyt z magazynu sekretów |
| wyścig odczyt–weryfikacja–zapis przy kwotach, stanach magazynu lub unikalności | B | atomowość, blokada albo constraint |
| decyzja bezpieczeństwa przepuszczająca przy błędzie | B | odmowa przy błędzie |
| naprawiony błąd bez testu, który go odtwarza | M | dopisać taki test |
| asercje osłabione lub usunięte, żeby było zielono | M | przywrócić asercję i naprawić kod |
| N+1, wynik bez limitu, wywołanie zewnętrzne bez timeoutu | M | wsad lub join, stronicowanie, timeout |
| `catch` bez obsługi; konsument nieodporny na powtórne doręczenie | M | obsługa, log lub propagacja; deduplikacja albo upsert |
| niezwiązany lockfile; plik generowany poprawiany ręcznie | M | wycofać lub wydzielić; wygenerować ze źródła |
| kod martwy; komentarz niezgodny z kodem | m | usunąć; poprawić |
