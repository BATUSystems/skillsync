# Oracle regresji

Czytaj przed napisaniem testu regresji, czyli w kroku 3 skilla `bugfix`. Wybierz rodzinę pasującą do usterki i korzystaj z runnera oraz konwencji projektu.

**Reguła nadrzędna: oracle zawodzi na kodzie sprzed poprawki i to z właściwej przyczyny.** Porażkę pokaż w raporcie: komenda, exit code, komunikat. Oracle sprząta po sobie dane, które utworzył.
Rodziny oracle:
- **zakres danych (tenant/organizacja/użytkownik):** co najmniej dwa zakresy; dostęp dozwolony, odmowa, zakres `null`, „wszystkie”, wildcard; brak lub zły zakres nigdy nie rozszerza dostępu (fail-closed).
- **zapis atomowy:** wymuszona awaria między etapami; po niej brak częściowych danych i przedwczesnych skutków ubocznych (zdarzenie, cache, indeks).
- **konflikt współbieżny:** aktualizacja na bieżącej wersji przechodzi; nieaktualna aktualizacja, usunięcie lub akcja dostaje ustrukturyzowany konflikt, który UI pokazuje użytkownikowi.
- **pole w pełnym cyklu:** utworzenie, przeładowanie, edycja, wyczyszczenie do `null`, przeładowanie; na każdym kroku zgodne żądanie, magazyn, odpowiedź i UI.
- **środowisko uruchomieniowe:** ta sama konfiguracja i zachowanie usługi w serwerze, CLI, workerze/kolejce i przeglądarce; gdy błąd występuje tylko w jednym miejscu, test na obrazie lub zainstalowanym pakiecie.
- **cache i odczyt po zapisie:** odczyt zaraz po zapisie zgodny z przyjętym modelem spójności; czekanie na zbieżność z limitem czasu, bez arbitralnego `sleep`.
- **migracja i dane istniejące:** migracja na danych w starym kształcie w przód, a wstecz, jeśli projekt to wspiera; nic nie ginie; zmiana obejmuje tylko poprawiane encje.
- **brak testu automatycznego (konfiguracja, środowisko, UI):** nie fabrykuj oracle; zamiast tego ręczny dowód (kroki, wynik oczekiwany i rzeczywisty, stan kodu), znacznik `LOW_CONFIDENCE` i jawne ograniczenie.
- **integracje:** doręczenia zdublowane, równoległe, ponowione, po timeoucie lub od niewłaściwej strony; poprawna idempotencja, stan kursora i mapowania; brak sekretów w logach i fixture'ach.

Stosy (konwencje projektu mają pierwszeństwo):
- Django: test w `TestCase` z rollbackiem; dla danych wielotenantowych odpowiednik `TenantTestCase`.
- .NET: test w izolowanej transakcji albo na bazie in-memory, zgodnie z konwencją projektu.
- Angular: test komponentu lub serwisu z mockiem wyłącznie granic (HTTP), nigdy testowanej jednostki.

Zasady:
- Zielony wynik nie może pochodzić z osłabienia asercji, `sleep`, tłumienia błędu, zmiany plików generowanych ani zainstalowanych zależności.
- Poprawka trafia w rzeczywiste miejsce wywołania, nie w opakowanie objawu; bez refaktorów spoza zakresu poprawki.
- Gdy nie wiadomo, czyj to zakres (tenant, organizacja, konto, użytkownik), kod odmawia, zanim sięgnie po dane.
