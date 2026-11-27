# Podgląd na żywo (preview): szablon zlecenia dla karty

Czytaj, gdy zlecasz karcie (albo sam uruchamiasz) żywy podgląd strony statusu (`skillsync dashboard serve`) albo serwera prototypów, żeby użytkownik mógł go obejrzeć przed
scaleniem. Podgląd w osobnym serwerze **zapisuje plik stanu** i trzyma port; bez izolacji psuje cudze biegi testów.

## Dlaczego izolacja jest obowiązkowa

`dashboard serve` zapisuje plik stanu w katalogu danych użytkownika (Linux `$XDG_DATA_HOME/skillsync/dashboard`, macOS `~/Library/Application Support/skillsync/dashboard`, Windows
`%LOCALAPPDATA%\skillsync\dashboard`). Testy rdzenia mają strażnika „test touched real Skillsync state dirs”: podgląd uruchomiony na prawdziwym stanie wywraca pełny bieg dopiero po jego
końcu (dziesiątki minut stacji, potem powtórka). Dlatego podgląd zawsze dostaje własny, izolowany stan w katalogu worktree karty, a przed szerokim biegiem sprawdza się, że żaden żywy podgląd
nie pisze do prawdziwego stanu.

## Strona statusu worktree karty: zakładka, nie osobny port

Gdy karta pracuje w worktree tego samego projektu git, co działający serwer strony statusu projektu, nie dostaje portu z puli: `skillsync dashboard serve` w jej worktree, bez flag ustawień serwera,
dopisuje zakładkę tego worktree do strony projektu i wypisuje adres własnej zakładki (`/?tab=<id>`, ostatnia linia stdout). Nie startuje procesu (brak PID do zatrzymania) i nie otwiera portu.
Karta podaje ten adres w raporcie; `--all-tabs` (adresy wszystkich zakładek) jest dla człowieka i karty go nie używają. Bez działającego serwera to samo polecenie startuje serwer projektu
(wtedy jest jego procesem, tak jak w szablonie niżej). Zakładka pokazuje kod strony z warstw serwera, nie z worktree karty.
Dopisanie zakładki zapisuje plik przypięcia w prawdziwym katalogu danych (`<data home>/dashboard/<serverId>-pins`), więc działa tylko bez izolowanego `XDG_DATA_HOME` (katalogu danych); podgląd z izolacją to serwer standalone.

Podgląd z cudzą warstwą (kod strony z `<worktree>/library`), bez zgód albo z jakąkolwiek flagą ustawień serwera to osobny serwer (standalone) na porcie z puli karty; dopisanie zakładki go nie zastępuje.
Szablon niżej dotyczy właśnie takiego podglądu i serwera prototypów.

## Szablon zlecenia (wklej do ASSIGN i uzupełnij `<…>`)

```
PODGLĄD <co>: uruchom z izolowanym stanem i portem z puli tej karty; nic nie wychodzi poza 127.0.0.1, dopóki dyspozytor nie zgodzi się inaczej.
Linux:   S=<worktree>/.preview-state; mkdir -p "$S"
         XDG_DATA_HOME="$S/data" XDG_CACHE_HOME="$S/cache" SKILLSYNC_CONFIG="$S/config.yaml" \
           skillsync dashboard serve <projekt> --port <port z puli> --no-approvals --layer core=<worktree>/library & echo $! > "$S/serve.pid"
macOS:   to samo, plus HOME="$S/home" (stan leży w ~/Library/Application Support, nie w XDG)
Windows: $env:LOCALAPPDATA="$S\data"; $env:USERPROFILE="$S\home"; $env:SKILLSYNC_CONFIG="$S\config.yaml"; Start-Process … (PID zapisz do $S\serve.pid)
Zmienne jak `home_env` w testach: XDG_DATA_HOME, XDG_CACHE_HOME, LOCALAPPDATA, HOME, USERPROFILE (wszystkie systemy naraz nie szkodzą).
Sprawdź: (1) odpowiedź na http://127.0.0.1:<port>/healthz, (2) że `$S/data/skillsync/dashboard/` ma plik stanu, a prawdziwy katalog stanu nie dostał nowego pliku
         (Linux: find ~/.local/share/skillsync/dashboard -newer "$S/serve.pid"; macOS i Windows: odpowiednie ścieżki z akapitu wyżej).
Zatrzymanie: kill "$(cat "$S/serve.pid")" (po PID zapisanym przy starcie; nie `pkill` po nazwie: zabiłbyś cudzy serwer), potem sprawdź, że port jest wolny.
Raport: adres, port, PID, katalog stanu, wynik sprawdzenia (1) i (2).
```

## Zasady

- **Port z puli karty** (osobny serwer: podgląd z cudzą warstwą, serwer prototypów). Dyspozytor przydziela kartom puli portów (np. zakres kilkunastu kolejnych numerów na kartę); podgląd nie bierze portu spoza puli i nie szuka „pierwszego wolnego”.
- **Domyślnie tylko ta maszyna** (`127.0.0.1`). Strona statusu pokazuje plany, specyfikacje i wyniki (sekrety maskowane heurystycznie, ruch bez szyfrowania). Wystawienie w sieci lokalnej (`--read-bind 0.0.0.0` albo
  odczyt z telefonu) tylko na wyraźne życzenie użytkownika i tylko dla odczytu. Zgody wymagają zalogowania konta `approver` (lokalnie albo przez TLS) i daje je tylko użytkownik: karta nie zakłada kont, nie loguje się i nie klika zgód. Szczegóły nasłuchu: `docs/cli.md` rdzenia skillsync (sekcja `dashboard serve`).
- **Jeden podgląd na zadanie i na port.** Po obejrzeniu zatrzymaj go (po PID) i usuń katalog stanu; nie zostawiaj serwera po zakończeniu paczki.
- **Przed szerokim biegiem testów** dyspozytor (albo karta, która go zleca) sprawdza, czy nie działa podgląd na prawdziwym stanie: w prawdziwym katalogu stanu nie ma pliku stanu żywego procesu
  (`kill -0 <pid>` z pliku). Żywy podgląd na prawdziwym stanie: zatrzymaj go albo uruchom ponownie z izolacją, dopiero potem bieg.
- **Podgląd z kodem sprzed scalenia** pokazuje stan gałęzi, nie wydania: napisz w raporcie, z jakiego SHA działa.
- Serwer prototypów (`prototype serve`) ma własny katalog stanu i plik kont: tak samo izolowany katalog, port z puli i zatrzymanie po PID; pokaz bez kont to `prototype serve --demo`
  (tylko do odczytu, domyślnie na wszystkich interfejsach: dla podglądu tylko lokalnego dodaj `--host 127.0.0.1`).
