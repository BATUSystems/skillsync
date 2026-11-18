# Katalog roboczy przebiegu (pytanie przy „GO plan”)

Czytaj przy „GO plan” (bez „GO plan”: przy „GO implement”), przy wznowieniu planu z linią `Katalog:` oraz gdy reguła katalogu dotyka `CP`, `pr-run` albo „TAK P<n>”.
Rdzeń instrukcji ma tylko regułę; warunki i ścieżki porażek są tutaj.

## Linia `Katalog:`

Wybór zapisuje linia nagłówka planu (obok `Przebieg:`, `Oddanie:`; [plan](plan-details.md)): `Katalog: worktree <nazwa-katalogu>` albo `Katalog: główny`.
`worktree` niesie sama nazwa katalogu, nie ścieżka bezwzględna; ścieżkę odnajdź przez `git worktree list` (po nazwie i gałęzi). Linia trafia do commita i PR-a, więc bez ścieżek lokalnych.
Linia powstaje w planie-szkicu przed commitem „GO plan”. Wznowienie ją czyta: brak linii `Katalog:` w planie = worktree jak dotąd;
zniknięty katalog worktree z linii planu = STOP i pytanie (odtworzyć z gałęzi?), bez cichego przełączenia na `główny`.

## Pytanie

Przy „GO plan” (bez „GO plan”: przy „GO implement”, pierwszym słowie zgody, które zakłada katalog) sesja zadaje jedno pytanie z dwiema opcjami (host z oknem wyboru: okno; bez niego pytanie tekstowe):
„Gdzie pracować nad tym przebiegiem?”: „własny worktree” (domyślnie, zalecane: izolacja plików, równoległe sesje, bez ryzyka dla bieżącej gałęzi) albo „główny checkout” (jeden katalog, bez drugiej kopii).
Pytanie o katalog podaje nazwę tej gałęzi (bieżącej w głównym checkoucie); przy `delivery.base` ostrzega, że commity i `CP` pójdą na bazę.
Brak odpowiedzi, odmowa albo anulowanie = brak „GO plan”: nic nie powstaje, spec zostaje `draft`.

Pytanie nie pada: (1) gdy plan ma już linię `Katalog:` (wznowienie, ponowne „GO plan” po zmianie speca); (2) w trybie karty (`ASSIGN`/`dispatch:`) katalog wynika ze zlecenia, domyślnie worktree paczki, karta nie pyta
(karta (worker-mode) pracuje zawsze w worktree ze zlecenia, [tryb roboczy](worker-mode.md)); (3) przy wejściu z klucza zgłoszenia (`pr-after-approval`): „rób PR” używa bieżącej gałęzi i nie zakłada worktree, bez zmian ([wejście z klucza](pr-after-approval.md)).

## `worktree`

Jak dotąd: osobne drzewo robocze i gałąź robocza (zakłada ją „GO plan”, jeśli jej nie ma), kopia nieśledzonego szkicu speca, manifest i `skillsync link` według [trybu roboczego](worker-mode.md).

## `główny`: warunki wejścia

Gałąź robocza to bieżąca gałąź głównego checkoutu, jakakolwiek, także `delivery.base`, bez `git switch`. Commit „GO plan” powstaje na niej w głównym checkoucie, `git add` wyłącznie z jawnymi ścieżkami, nigdy plików generowanych.

- HEAD odłączony = STOP z propozycją worktree (brak gałęzi, brak `CP`, brak PR-a).
- Zmiany w śledzonych plikach (poza plikami generowanymi przez `link`) = pytanie „kontynuować z tymi zmianami w drzewie?”; lista tych zmian trafia do baseline planu, a domyślna odpowiedź to „użyj worktree” (nie stash, nie `checkout --`).
  Nieśledzone obce pliki: baseline bez pytania.
- Żywa dzierżawa innego przebiegu w tym katalogu (`.skillsync/claims`) = STOP i propozycja worktree.
- W trybie `pr` z bieżącą gałęzią równą `delivery.base` „główny” nie jest przyjmowany (PR z bazy do bazy nie istnieje); pytanie oferuje wtedy tylko własny worktree albo przejście użytkownika na inną gałąź w głównym checkoucie.
- Tryb `direct` na bazie: bez pytania o `Oddanie:` i bez tej linii (robocza = baza, nie ma dokąd oddawać); `CP` pushuje bieżącą gałąź będącą bazą (reguła „nigdy na `delivery.base` … chyba że robocza nią jest”). Przy innej gałęzi niż baza pytanie i `Oddanie:` jak dotąd.

## Skutki dla innych reguł

- `pr-run` krok 3 przy `Katalog: główny` używa głównego checkoutu i jego bieżącej gałęzi (nie zakłada worktree ani gałęzi), pod warunkiem gałęzi innej niż `delivery.base`; krok 8 pomija `główny`.
- `CP` bez zmian (push bieżącej gałęzi roboczej na jej zdalny odpowiednik). Push na bazę, fast-forward `robocza:base`, działa także z głównego checkoutu; główny checkout aktualizowany `--ff-only` tylko gdy czysty i na `base` (gdy gałąź bieżąca to baza, `CP` pushuje ją wprost; przy innej gałęzi ta aktualizacja jest nieaktywna).
- Kopia przebiegu poza worktree i sprzątanie worktree dotyczą tylko `Katalog: worktree`; przy `główny` przebieg zostaje w katalogu projektu.
- W `direct` z `Katalog: główny` „TAK P<n>” działa (główny checkout); w `pr` propozycje `repo: project` zostają `deferred` jak dotąd ([retro-quality](retro-quality.md)). „TAK” po rekordzie końcowym zmienia HEAD i unieważnia `sha` zgody `CP` (istniejąca reguła: inny `sha` = pytanie).
- Odwrócenie: `główny` nie zmienia gałęzi; commit „GO plan” jest lokalny, odwracalny `git reset` tylko za zgodą użytkownika; po oddaniu na bazę w `direct` z gałęzi roboczej sesja proponuje `git switch <baza>`, nie robi tego sama.
- Druga sesja albo IDE w tym samym głównym katalogu (lekcja `zmiana-head-w-trakcie-biegu-uniewaznia-bieg`): commity trafiają na gałąź, którą ta sesja ma wybraną; dlatego pytanie ostrzega, a żywa dzierżawa kończy się STOP.
