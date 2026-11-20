# Tryb nieśledzonych speców i planów (`specs.tracked: false`)

Część repozytoriów ignoruje `docs/specs` i `docs/plans` (reguła w śledzonym `.gitignore`). Tam „plan i spec = commit” nie działa: pliki nie trafią do historii.
Ta referencja opisuje tylko odstępstwa od procedury; tryb śledzony (`tracked: true`, domyślny) działa bez zmian.

## Wykrycie

Źródłem prawdy jest `skillsync route <projekt>`: pole `specs` (`tracked`, `archive`, `source`). Wartości `source`: `manifest` (klucz `workflow.specs.tracked`/`archive` w `.github/.skillsync.yaml`),
`gitignore` (wynik `git check-ignore --no-index -v` dla `docs/specs/x.md` i `docs/plans/x.md`; ignorowanie obu = `tracked: false`), `default` (`tracked: true`), `invalid` (błędny klucz manifestu albo `tracked: true` przy regule ignorującej oba katalogi).
`invalid` = nie zakładaj trybu, zapytaj. Bez CLI sprawdź te dwie ścieżki ręcznie tym poleceniem, ograniczając źródła do `.gitignore` w drzewie repozytorium (bez globalnych i `info/exclude`), oraz klucz manifestu; manifest wygrywa z `.gitignore`.

## Gdzie żyją spec i plan

Spec i plan zostają w drzewie roboczym przebiegu (`docs/specs`, `docs/plans` worktree albo katalogu `Katalog: główny`); nie ma ich w historii gita.
Szkic speca z głównego checkoutu przenosisz do worktree przy „GO plan”: kopia, kontrola sha źródła i kopii, potem źródło do kosza (nigdy `rm`). Rozbieżne sha = STOP i pytanie.

## Trwałość: migawka

Trwałość daje `skillsync spec snapshot <projekt> --run-id <id> [--spec <plik>] [--manifest <plik>] [--replace] [--json]`: kopiuje spec (z linii `Spec:` planu o `Przebieg: <id>`) i plan do
`.skillsync/runs/<id>/docs/specs|plans/` oraz, gdy jest `workflow.specs.archive`, do archiwum poza repozytorium (`<archiwum>/<projekt>/<id>/specs|plans/`). Ponowienie jest idempotentne;
cel zmieniony przez kogoś innego to `BLOCKED` (nadpisanie tylko z `--replace`, po decyzji człowieka). Migawka leży w worktree, więc sama nie chroni przed jego usunięciem.
Archiwum speców jest opcjonalne i nie jest ani magazynem przebiegów, ani `plan archive`.

Kiedy robisz migawkę: przy „GO plan” (po `Spec-sha` i szkicu planu), po każdej zmianie speca lub planu wartej zachowania (zamknięcie fazy, poprawka speca z nowym `Spec-sha`) i obowiązkowo przy zamknięciu przebiegu, przed kopią przebiegu poza worktree.

## „GO plan” bez commita speca i planu

Przy „GO plan” robisz wszystko jak zwykle poza commitem plików ignorowanych: zakładasz gałąź i katalog roboczy (`Katalog:`), zapisujesz `Spec-sha` w planie, robisz migawkę i checkpoint `go-plan`
(`skillsync checkpoint <projekt> --run-id <id> --step go-plan --status PASS --evidence <plik migawki> --next <następny krok>`; `go-plan` to konwencja nazwy kroku, dowodem jest plik migawki w `.skillsync/runs/<id>/docs/`). Commit lokalny obejmuje tylko to, co jest śledzone (np. prototyp); gdy nie ma śledzonych zmian, commita nie ma i nie robisz pustego.
Pierwszy commit przebiegu powstaje wtedy w kroku kodu. Przy „GO plan” sprawdzasz zgodność `Spec-sha` i istnienie migawki; `spec snapshot --check --to <korzeń kopii>` robisz dopiero przy sprzątaniu worktree (gołe `--check` w połączonym worktree bez `--to` daje `BLOCKED`).

## Odbiór i zgody

Wpis `CP`/`READY` w `approvals.jsonl` ma pole `planState` (status planu przebiegu w chwili zgody; `approvals record` wypełnia je sam). Odbiór (`needsHuman: acceptance`) zamyka ostatni wpis
z `planState: done` przy planie `done`; historia gita nie jest potrzebna. Wpis bez pola przy planie `done` nie daje stanu „zgoda dana”: po zamknięciu wpisz słowo jeszcze raz. To samo dotyczy `CP`/`READY` zapisanego przy planie `in_progress`: nie zamyka odbioru po `done`, słowo trzeba powtórzyć przy planie `done`. `approvals wait` nie rozróżnia `planState`: czekając na zgodę po zamknięciu planu podaj `--since <czas STOP>`, inaczej stary `CP` z realizacji kończy czekanie.

## `pr-run` i wejście z klucza

- Opis PR bez ścieżki planu: streszczenie speca i `Spec-sha`.
- Commity „plan”, „plan z linią `PR:`” i ostatni nie obejmują planu ani speca (zamiast commita planu jest migawka).
- Brak commita w kroku 4 `pr-run` jest stanem oczekiwanym: draft PR powstaje dopiero po pierwszym commicie kodu (PR bez commitów przed bazą jest odrzucany, puste commity są zakazane).
- Linia `PR:` trafia tylko do lokalnego planu i migawki; `--resume` na innej maszynie wymaga ręcznego przeniesienia migawki.
- `pr-after-approval`: spec i plan pomijasz przy `git add` (są ignorowane), a opis PR streszcza spec z `Spec-sha`.

## Sprzątanie worktree

Przed usunięciem worktree (`Katalog: worktree`) zrób migawkę i kopię przebiegu poza worktree (zawiera `docs/`), potem uruchom
`skillsync spec snapshot <projekt> --run-id <id> --check [--to <korzeń kopii>]`. Warunek sprzątania: `PASS` dla celu poza worktree (archiwum albo `--to`). `BLOCKED` (także przy zapomnianym `--to`
w połączonym worktree, albo gdy archiwum jest niedostępne) = nie usuwaj; napraw kopię i powtórz. Przy `Katalog: główny` ignorowane pliki są narażone na `git clean -x`/`-X`: nie uruchamiaj ich bez migawki.
