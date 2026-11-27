---
name: dashboard
description: "Strona statusu przebiegu feature: akty, postęp, checkpointy, prototypy, podgląd speca i planu; komenda skillsync dashboard serve, zgody tylko z sesji konta z rolą approver."
user-invokable: true
---

# dashboard: status przebiegu `feature` na żywo

Serwer jest częścią generatora (`skillsync dashboard serve`, stdlib Pythona 3.11+, bez pip, bez CDN, działa offline);
ten skill dostarcza stronę (`assets/`: HTML, CSS, JS) i opisy dymków (`descriptions.json`). Serwer czyta pliki skilla
`feature` w projekcie i pokazuje je jako stronę dla widzów (rzutnik, przeglądarka przez tunel SSH), odświeżaną na żywo,
gdy agenci pracują. Stan strony to ten sam JSON co `skillsync dashboard show` (kontrakt `schema: 1`,
`docs/dashboard-contract.md` w repo skillsync). Nie wykonuje poleceń z danych. Zapisuje tylko działania z zalogowanej
sesji: zgody (niżej: „Przyciski zgód i słów”), statusy planów w Historii i akcje na prototypach.

## Uruchomienie

```bash
skillsync dashboard serve <projekt> [--page-port N | --port N | --read-port N --approve-port M] [--read-bind ADR] [--approve-bind ADR] [--host H] [--read-host NAZWA ...] [--approve-host NAZWA ...] [--local-only] [--allow-remote-approvals] [--allow-plain-login] [--trust-proxy --proxy-address ADR] [--secure-cookies] [--public-url URL] [--prototype-url URL] [--prototype-port N | --single-origin] [--no-approvals] [--plan docs/plans/<plik>.md] [--session-url https://…] [--layer id=ścieżka ...] [--standalone] [--all-tabs] [--detach] [--json]
skillsync dashboard url [<projekt>] [--port N] [--host NAZWA] [--read | --lan] [--all-tabs] [--json]   # adres działającego serwera
skillsync dashboard stop [<projekt>] [--port N] [--timeout S] [--force] [--dry-run] [--json]   # łagodne zatrzymanie
skillsync dashboard show <projekt> [--json] [--plan docs/plans/<plik>.md]   # jednorazowy zrzut JSON (jak /api/status, bez pól serwera)
```

- Serwer działa na pierwszym planie (Ctrl+C albo SIGTERM kończy; na Windows Ctrl+C albo CTRL_BREAK do grupy procesów, zob. docs/dashboard-contract.md „Jak zatrzymać serwer na Windows”, wynik `{"status": "PASS", "stopped": true}`); odmowa startu
  (port spoza zakresu, brak manifestu, brak `assets/index.html` w aktywnych warstwach, zły `--session-url` albo `--plan`)
  = `BLOCKED`, kod 2. Strona i `descriptions.json` pochodzą ze skilla `dashboard` aktywnych warstw (`--layer` jak w innych
  komendach; późniejsza warstwa wygrywa per plik).
- `--detach` uruchamia serwer w tle (czeka do 30 s na stronę, wypisuje adres, konsola idzie do logu w katalogu stanu maszyny; `BLOCKED` `detach_failed` / `detach_timeout`). `dashboard stop` zatrzymuje serwery strony tego projektu, także z pierwszego planu (kody: 0 zatrzymano albo nie było czego, 1 nadal działa, 2 odmowa, 3 `--dry-run`); serwer projektu jest wspólny, więc zamyka stronę wszystkim zakładkom.
- Bez flag portów jedno gniazdo strony na **5140** (wszystkie interfejsy): czyta każdy, kto do niego dotrze, a zgody daje tylko zalogowana sesja konta z rolą `approver`, lokalnie albo przez TLS. Prototypy ten sam proces serwuje z **drugiego originu** (`--prototype-port`; domyślnie port strony plus 2, a gdy zajęty, następny wolny). Porty strony **5100–5199** (nigdy 4444–4448, 9222, 9333); inny numer jedynego gniazda: `--page-port`. Jeden serwer
  projektu na projekt git (niżej: „Zakładki worktree i Historia”); port wybiera się per projekt. Zajęty port = BLOCKED z numerem
  portu i właścicielem (bez przejścia na inny port; zajęty port strony plus 2 nie blokuje startu, a ostrzeżenie nazywa właściciela i port originu prototypów); serwer starszej wersji = `legacy_server` (zatrzymaj go; repo: `docs/upgrading.md`).
- Nasłuch strony: `--read-bind`, potem `dashboard:` (`local_only`, `read_bind`, `approve_bind`) w manifeście, potem w configu maszyny, na końcu `0.0.0.0`. `approve_bind` działa tylko z `--approve-port`; ustawiony w trybie domyślnym nic nie zmienia i `serve` wypisuje ostrzeżenie.
  Telefon w tej samej sieci otwiera `http://<IP maszyny>:5140/` (adresy: `dashboard url <projekt> --lan`), ale tylko czyta: zgody i logowanie `approver` wymagają loopbacku albo TLS
  (`--allow-remote-approvals` przyjmuje zgodę bez TLS, lecz sesja `approver` i tak powstaje tylko tak; odradzane). W nieufnej sieci: `dashboard.local_only: true` (z innej maszyny tunel `ssh -L 5140:127.0.0.1:5140 <host>`); `--local-only` odrzuca każdego peera spoza loopbacku.
- Za odwrotnym proxy z TLS: `--trust-proxy` z `--proxy-address` (bez listy serwer nie startuje), zwykle też `--public-url` i `--prototype-url`; `--allow-proxy-headers` = BLOCKED.
  `--allow-plain-login` dopuszcza logowanie `commenter` i `expert` przez HTTP z sieci, nigdy `approver`.
- **`--port N`** (jedno gniazdo): `127.0.0.1`, chyba że `--host`. **Dwa gniazda** (`--read-port N` i/lub `--approve-port M`): gniazdo odczytu nie ma tras zapisu ani logowania
  (do tunelu i linków: `dashboard url <projekt> --read`), gniazdo zgód (domyślnie `127.0.0.1`, `--approve-bind`) ma logowanie i zgody; `--host` rusza tylko odczyt.
  `--host`, `--approve-host` i `--approve-bind` bez portów są BLOCKED.
- `--single-origin` (bez izolacji): prototypy na originie strony; zgody i akcje na prototypach wyłączone, konto `approver` się nie loguje, stały czerwony pasek.
- Kontrola `Host`: loopback, literały IP tej maszyny i jej własne nazwy (poza `--port`), `--read-host`, `--approve-host`, nazwa z `--public-url`; inny = 403 (nazwę tunelu podaj w `--read-host`). Nazwa nie daje prawa zgody. Sesja należy do nazwy hosta: `localhost`, `127.0.0.1` i IP logują się osobno.
- Konto `approver` zakłada **człowiek** w swoim terminalu: `skillsync prototype user add|roles <projekt> <e-mail> --role approver --password-prompt --confirm-mixed-fleet` (hasło od 14 znaków).
  Agent nie uruchamia `prototype user add|roles` ani `prototype login-link`, nie zakłada ani nie zmienia konta `approver` i nie loguje się na nie; użytkownikowi podaje adres z `dashboard url`.
- Restart działającej instancji: flagi bierz z zapisu, nie z wiersza poleceń zastanego procesu: `dashboard:` w manifeście i config maszyny dają tylko `local_only`, `read_bind` i `approve_bind`, a hosty, porty i adresy za proxy tylko polecenie zapisane w instrukcjach projektu; bez zapisu zapytaj użytkownika. Po restarcie sprawdź, że każdy dotychczasowy widz (telefon, tunel) nadal dostaje stronę, a nie 403.
- Bez `--plan`: bieżący plan = najnowszy `docs/plans/*.md` ze `Status: in_progress`, inaczej `approved`, inaczej `draft`, inaczej ostatni `done`
  (nazwa z datą, potem mtime). Na stronie przełącznik planów (zapamiętany w przeglądarce).
- Akt I bez planu: `feature` zakłada plan dopiero przy „GO plan”. Gdy najnowszy spec `draft`/`approved`, którego nie wskazuje
  żaden plan (`Spec:`), jest nowszy niż plan wybrany automatycznie, strona pokazuje akt I (`mode: "spec"`): spec, przebieg
  (linia `Przebieg:` speca, potem katalog o nazwie planu z linii `Plan:`, potem najnowszy `<data>-<slug speca>`), prototyp
  z `docs/prototypes/*/prototype.json` (`requirements` = spec) z akceptacją ekranów liczoną jak w `skillsync prototype list`
  (w procesie, tylko odczyt, w pamięci do zmiany `comments.json`/`index.html`); akt II = „brak planu (czeka na GO plan)”.
  Akt I przed specem (`mode: "run"`): gdy speca jeszcze nie ma, a najnowszy katalog przebiegu ma `checkpoints.md` nowszy
  niż plan wybrany automatycznie i nie należy do żadnego planu (`Przebieg:`) ani speca (`<data>-<slug speca>`), strona
  pokazuje „Akt I: specyfikacja w przygotowaniu”, run-id, ostatni checkpoint i baner `czeka:`; po zapisaniu speca działa
  tryb `spec`.
  Jawne `--plan` albo wybór planu na stronie wyłącza ten tryb.
- Kafelek Akt I ma przycisk „Otwórz specyfikację”, Akt II „Otwórz plan” (gdy plik istnieje): przejście do zakładki
  **Dokumenty** z otwartym plikiem.
- Zakładka **Prototypy** (gdy są prototypy; bez logowania): tytuł, iteracja, ekrany zaakceptowane i nieaktualne, znacznik zapisu, wątki, komentarze po akceptacji, bramka planu i „Otwórz” (nowa karta). Przy `prototype.frame` także „Pokaż w zakładce” (ramka w sandboxie; dopóki jest na ekranie, przycisków zgód nie ma).
  Zalogowany ma „Zaloguj w prototypach” (przekazanie sesji bez ról `approver`, `acceptor`, `developer`) i widok „Akceptacje i wątki” bez ramki (akcje z oknem potwierdzenia, prawo według ról).
- Wrapper projektu (np. `scripts/status.sh`) i port w tunelu dodaje projekt, nie ten skill.

## Zakładki worktree i Historia

Jedna strona (jeden serwer projektu) pokazuje wszystkie katalogi robocze projektu git: główny katalog i każdy jego worktree to zakładka na górze strony,
a na końcu paska stoi zakładka „Historia”. Wnętrze zakładki to zwykła strona statusu tego katalogu (postęp, zgody, pytania, testy, dokumenty), z własnym planem,
i przebiegiem. Rejestr zakładek odświeża się sam, więc worktree założony po starcie serwera pojawia się bez restartu.

- **Dopisanie zakładki.** `skillsync dashboard serve` bez flag ustawień serwera, uruchomione w katalogu projektu git z działającym serwerem projektu, nie otwiera gniazd:
  dopisuje zakładkę tego katalogu i wypisuje jej adres (`/?tab=<id>`) jako **ostatnią linię stdout**
  (komunikaty idą na stderr). Bez działającego serwera to samo polecenie startuje serwer projektu: korzeniem jest główny katalog, zakładką startową katalog uruchomienia.
  Zakładkę dostaje tylko katalog, który jest głównym katalogiem albo worktree z poprawnym powiązaniem zwrotnym gita i nie jest wykluczony; wykluczenie to plik
  `.skillsync/no-dashboard` albo lista `dashboard.worktrees.exclude` w manifeście. Dopisanie przypina katalog uruchomienia (zawsze widoczny), a jego zakładka liczy się do limitu 16.
  Adres własnej zakładki podaje też `skillsync dashboard url` uruchomione w jej katalogu; katalog bez zakładki dostaje `BLOCKED`.
- **Widoczność zakładek.** Pozostałe worktree mają zakładkę, gdy mają ślad pracy skillsync (wpis w `.skillsync/runs/` albo plan lub spec różny od głównego katalogu) i były aktywne
  w ostatnich 7 dniach (`dashboard.worktrees.inactive_days`); reszta jest pod „pokaż wszystkie” razem z nadmiarem ponad 16 widocznych zakładek.
  Zakładki są posortowane według ostatniej aktywności, główny katalog pierwszy; zakładka z oczekującą zgodą jest oznaczona.
- **Zgody.** Zgoda jest zawsze dla worktree i sha swojej zakładki (pole `tab` żądania); inne zakładki jej nie widzą. Jedna sesja `approver` działa na wszystkich zakładkach.
- **Serwer osobny (standalone).** Gdy serwer projektu działa, a podano którąkolwiek flagę ustawień serwera (porty, nasłuch, hosty, proxy i adresy, `--allow-*`, `--local-only`, `--secure-cookies`, `--single-origin`, `--session-url`, `--poll`, `--verbose`),
  startuje osobny serwer z jedną zakładką, do którego inne katalogi się nie dopisują i który `dashboard url` bez `--port` wskazuje tylko wtedy, gdy serwera projektu nie ma
  (adres: `dashboard url --port N`). Gdy żaden serwer projektu nie działa, te flagi startują serwer projektu. Standalone jest zawsze przy `--standalone`, `--no-approvals`, `--layer`
  i `--read-port` bez `--approve-port`. Rodzaj serwera ustala start i się nie zmienia.
  Podgląd z cudzą warstwą (`--port <port> --no-approvals --layer core=<worktree>/library`) jest zawsze serwerem standalone na własnym porcie.
- **Karty (agenty)** nie przydzielają ręcznie portów: `skillsync dashboard serve` w swoim worktree dopisuje zakładkę i wypisuje
  adres. `--all-tabs` (adresy wszystkich zakładek, własna ostatnia) jest dla człowieka; karty go nie używają.
- **Odczyt.** Strona (i gniazdo odczytu) udostępnia status, dokumenty i testy wszystkich zakładek projektu (ścieżki, gałęzie, HEAD, treść planów); granicą jest wykluczenie worktree.
- **Terminal serwera.** Serwer projektu działa dopóki działa jego proces: zamknięcie terminala, w którym go uruchomiono, zamyka stronę wszystkim zakładkom (strona i `url` ostrzegają).
  Po utracie połączenia wystarczy ponownie uruchomić `skillsync dashboard serve` w dowolnym katalogu projektu.
- **Historia** to zakładka z zakończonymi planami (`done` i `archived`) wszystkich zakładek, pogrupowanymi według zakładki (adres `/?tab=history`); pokazuje też porzucone plany `approved`
  (żadnego `[x]`, brak lokalnego przebiegu, ponad 14 dni bez zmian według gita) z akcją „Porzuć”. Podgląd planu jest tylko do odczytu, ale z akcjami „Archiwizuj”, „Porzuć” i „Przywróć” (dla sesji `approver`). Ten sam plik widoczny w kilku
  worktree jest pokazany raz z liczbą kopii. Plany historyczne są tylko w Historii, nie w menu planów zakładki, ale tylko na serwerze z więcej niż jedną zakładką (serwer z jedną zakładką pokazuje menu jak dotąd).
  Plan `done` ma przycisk „Archiwizuj”, porzucony `approved` „Porzuć”, plan `archived` „Przywróć” (porzucony wraca do `approved`, inny do `done`); każdy z potwierdzeniem, inne statusy nie mają przycisku. Licznik przy zakładce to `count` z `GET /api/history`, czytanego w tle tylko przy widocznej karcie
  i nie częściej niż co 60 s (od razu po otwarciu Historii i po udanej zmianie statusu); pole `tabs[].history` z `/api/tabs` jest tylko wskazówką (do pierwszego odczytu pasek pokazuje jego największą wartość). Zapis zmienia linię `Status` planu (porzucenie i przywrócenie także linię `Porzucony:`) (trasa `POST /api/plan-status`, sesja `approver` z CSRF, `tab` właściciela pliku, kontrola sha, wiersz audytu w
  `.skillsync/audit/plan-status.jsonl` katalogu zakładki) i zabrudza `git status` tego worktree; nie dotyka `approvals.jsonl`. Wykluczone worktree nie mają planów w Historii.

## Co czyta (formaty skilla `feature`)

| Plik | Co | Na stronie |
|---|---|---|
| `docs/plans/<plan>.md` | `# tytuł`, `Status:`, `Spec:`, `Spec-sha:`, `Prototyp:`, `Przebieg:`; sekcja `## Progress` z `### Faza …` i krokami `- [ ] S1 Tytuł` / `- [x] S1 Tytuł — sha` (id `S2b`, `A0`, `A-1`) | nagłówek, Akt II, Akt III, fazy |
| spec z `Spec:` | `Status:` (draft → w trakcie; approved/implemented → zrealizowany; brak → zaplanowany) | Akt I |
| `.skillsync/runs/<run-id>/checkpoints.md` | `RUN=<iso>` (czas od startu), linie `- HH:MM tekst` | feed (najnowsze u góry), „w toku” |
| `.skillsync/runs/<run-id>/*.md` | nazwy `spec-review*`, `plan-review*`, `pre-implement*`, `*prototyp*` | etapy aktów I i II, lista dokumentów |
| `.git/HEAD`, `.git/config` (`[remote "origin"] url`), `refs/remotes/origin/<gałąź>` | gałąź, HEAD, adres repozytorium (Azure DevOps, GitHub, gitlab.com), czy gałąź jest na serwerze | nagłówek: gałąź jako link do repozytorium (tylko gdy jest na `origin`; inny host = sam tekst) |
| linia `PR: #n (link: https://…)` pod `## Progress` planu (zapisuje ją `pr-run`) | numer i adres PR-a | nagłówek: znaczek „PR #n” z linkiem |

- **Akt III:** % = zaznaczone kroki / wszystkie kroki z `Progress`. „Teraz wykonywane” = id po frazie „w toku” (do `;`)
  z najnowszego checkpointu, który ją zawiera; liczą się tylko pozycje zaczynające się od id (`S2b/S2d (test-writer)` tak,
  `szczebel 3 S1 na migawce` nie). Brak frazy: pierwszy niezaznaczony krok. Akt zakończony przy `Status: done`.
- Kroki wymienione po „zrobione” w checkpointach, a niezaznaczone w planie, mają przerywaną ikonę „zrobiony wg checkpointu”
  i osobny, jaśniejszy odcinek paska z podpisem „17% (+11% do weryfikacji)” (globalnie i per faza). **Procent główny liczy
  tylko `[x]`** (źródło prawdy); odcinek znika, gdy krok zostanie zaznaczony po review.

### Baner „sesja czeka na Ciebie”

Raport postępu (linia `- HH:MM …` w `checkpoints.md`) sesja dopisuje **po każdym kroku ALBO co 30 minut, co nastąpi
pierwsze** (agent nie ma zegara, sprawdza godzinę sam; skill `feature`, `references/measurement.md`). Gdy plan jest
`in_progress`, najnowszy checkpoint nie ma `czeka:`, a od ostatniego zapisu `checkpoints.md` minęło ponad 35 minut
(`SILENCE_MIN`), strona pokazuje „Brak raportu postępu: od N min” — sygnał, że sesja mogła się zawiesić. Minuty liczy
przeglądarka (pole `silence {since, thresholdMin}` w `/api/status`), więc baner pojawia się bez zmiany plików i znika po
następnym raporcie.

Trwały baner pod nagłówkiem (i `⏳ Czeka ·` w tytule karty) pokazuje, na co sesja czeka, co zrobić i odnośnik.
Znika, gdy zniknie powód.

- **Znacznik w checkpoincie** (pierwszeństwo): segment `czeka: <co>` w najnowszym checkpoincie, w tej samej linii co
  `zrobione: …; w toku: …; ryzyko: …` (segmenty rozdziela `;`):

  ```
  - 11:20 zrobione: S9; w toku: —; ryzyko: brak; czeka: CP
  ```

  Wartości: `GO plan`, `GO implement`, `akceptacja prototypu <slug>`, `odpowiedź Q1–Qn`, `CP`, `READY`, `TAK/NIE <run-id>/P<n>`
  (inna wartość = ogólny komunikat). Najnowszy checkpoint bez `czeka:` (albo `czeka: —`) zdejmuje baner, czyli „sesja ruszyła”.
  Luźne „czeka na …” w treści nie jest znacznikiem.
- **Fakty z plików** (bez znacznika): `Status: draft (czeka na GO implement)` planu albo `… (czeka na GO plan)` speca;
  prototyp z niezaakceptowanymi ekranami, zanim plan wejdzie w realizację (akceptacja jak w `skillsync prototype list`).
- Odnośnik: spec albo plan w zakładce **Dokumenty**, prototyp pod originem prototypów. Sam baner niczego nie
  zatwierdza: zgodę daje przycisk zgody w kafelku aktu lub banerze (sekcja „Przyciski zgód i słów”) albo słowo wpisane
  przez użytkownika w sesji.

Zdanie do instrukcji/promptów (zgodne z formatem „zrobione / w toku / ryzyko”): *„Gdy zatrzymujesz się na decyzji
człowieka, dopisz do checkpointu segment `czeka: <GO plan|GO implement|akceptacja prototypu <slug>|odpowiedź Q1–Qn|CP|READY|TAK/NIE <run-id>/P<n>>`;
następny checkpoint po wznowieniu pisz już bez `czeka:`.”*

### Szacowany koniec (`eta`)

Przed długą operacją sesja dopisuje do checkpointu segment `eta: <operacja> do HH:MM` (godzina z `date`):

```
- 14:05 w toku: F1.2; eta: rysowanie 12 ekranów prototypu do 15:10
```

Strona pokazuje „<operacja>: koniec ok. 15:10” z żywym odliczaniem („zostało MM:SS”, po terminie „przekroczono o …”) w kafelku aktywnego aktu i przy aktywnym
kroku z linii `w toku:`; paska powiadomień to już nie dotyczy. Szacunek z krokami (`w toku: F1.2; eta: …`) przechodzi przez linie z `czeka:`, a termin
przesuwa się o czas czekania na użytkownika (czekanie na CI i zasoby zapisuj `czeka: … (kto: agent)`, wtedy się nie liczy). Dopóki operacja trwa, powtarzaj `eta:`
w kolejnych liniach (po reakcji użytkownika pierwsza linia może być bez `eta:`); przy przekroczeniu podaj nowy termin (bez zatrzymywania pracy). Szacunek znika po linii
bez `eta:` (poza wyjątkami z kontraktu), po `eta: -` i po zamknięciu planu; widz widzi go tak samo. Pola JSON: sekcje `eta` i `estimates` kontraktu.

### Przyciski zgód i słów

**Reguła:** zgodę zapisuje wyłącznie użytkownik (przycisk po zalogowaniu albo `skillsync approvals record` uruchomione przez
niego); agent jej nie zapisuje, nie wywołuje, nie symuluje ani nie podsuwa wpisów (jedyny wyjątek: ślad odbioru w skillu `feature`). Komenda przyjmuje `--source cli`
albo `ide`; `source: "dashboard"` zapisuje tylko serwer strony we własnym procesie. Granice zaufania: sekcja „Bezpieczeństwo”.
Każdy przycisk zgody (Akt I „GO plan”, Akt II „GO implement”, Akt III „CP” i — w trybie `pr` — „READY”) ma jeden z
czterech stanów (`approvals.actions[].state`):

| Stan | Kiedy | Wygląd |
|---|---|---|
| przed | akt jeszcze nie nadszedł (np. „GO implement” w akcie I, „CP” przed startem realizacji) | brak przycisku |
| `running` (trwa) | akt trwa, ale stan jeszcze nie czeka na zgodę | widoczny, nieaktywny, podpowiedź „czeka na…” |
| `waiting` (czeka) | GO plan: spec `draft` z sygnałem `czeka na GO plan` (status speca/szkicu albo `czeka:`), prototyp zaakceptowany; GO implement: plan `draft` z `czeka na GO implement`, spec `approved`; CP/READY: plan `done`, `czeka: CP|READY` albo niewypchnięte commity gałęzi roboczej | aktywny dla sesji `approver` |
| `after` (po) | zgoda już dana (spec/plan `approved`+, wpis CP/READY dla obecnego HEAD) | nieaktywny, „zgoda dana HH:MM”; drugi zapis = odmowa |

Sygnał „czeka” pisze sesja (status pliku albo `czeka:` w checkpoincie): pliki przebiegu mówią tylko, że runda review
była, a nie, że pętla dała PASS.

- Strona z sesją: przyciski widzi tylko zalogowany (nieaktywne z powodem bez roli `approver` albo przed zmianą hasła), a kliknięcie otwiera okno potwierdzenia bez hasła (słowo, dokument, zakładka, sha, gałąź, cel, tryb); okno zamyka się bez wysłania, gdy któraś z tych wartości się zmieni.
- **„GO plan”** (spec `draft`, bez planu albo z planem-szkicem) i **„GO implement”** (plan `draft` bez sufiksu szkicu;
  spec, jeśli jest, już `approved`): przycisk w kafelku aktu i w banerze. Po potwierdzeniu serwer
  w swoim procesie zmienia status tą samą funkcją rdzenia co `skillsync spec approve` / `plan approve` z `--expect-sha`
  (`status.set_doc_status`, sha = wersja pokazana na stronie; zmieniony plik = odmowa „przeczytaj ponownie”); bez
  podprocesu `skillsync`, więc wersja CLI w PATH nie ma znaczenia. To zgoda użytkownika, równoważna wpisaniu słowa; procedura
  `feature` uznaje ten status i sesja działa według tabeli wznowienia przy następnej turze (strona podpowiada: napisz
  „dalej”). Przycisk jest wyłączony z powodem, gdy prototyp nie jest zaakceptowany albo „GO implement” wyprzedza „GO plan”.
- Ślad zgód GO: wpis `status.record_approval` (`source: "dashboard"`) w `approvals.jsonl` przebiegu (`sha` = pokazana
  wersja pliku, `shaAfter`, `file`, `run`). Bez katalogu przebiegu śladu nie ma (odpowiedź `trail: null`): zgoda zostaje
  w linii `Status:` pliku, którą sesja i `approvals wait --file` i tak czytają. Serwer nie ma własnego dziennika.
- **„CP” i „READY” (tryb `pr`) w kafelku Aktu III** — przycisk zapisuje zgodę, nie otwiera sesji.
  Po potwierdzeniu serwer zapisuje przez `status.record_approval` (ta sama funkcja co `skillsync approvals record`: blokada
  plikowa, odmowa powtórzenia, 409 `plan_state_changed` przy zmienionym statusie planu i `delivery_changed` przy zmienionej gałęzi, celu albo trybie) do `.skillsync/runs/<run-id>/approvals.jsonl` wpis
  `{"schema": 1, "at", "word": "CP"|"READY", "sha": <pełny HEAD pokazany na stronie>, "run", "plan", "target", "branch", "ahead", "mode", "planState", "by", "role", "session", "via", "source": "dashboard"}` (serwer projektu dodaje `worktree`).
  Zgoda dotyczy tego HEAD: nowy commit = nowa zgoda; ponowny zapis dla tego samego HEAD i statusu planu = odmowa. Przycisk aktywny, gdy
  plan jest `done`, najnowszy checkpoint ma `czeka: CP|READY` albo gałąź robocza ma commity, których nie ma na origin
  (`git rev-list --count origin/<robocza>..HEAD`, a bez gałęzi na origin `HEAD --not --remotes=origin`; tylko odczyt).
  „CP” oddaje na `origin/<robocza>` (rdzeń AGENTS: nigdy na bazę); w trybie `direct` linia planu `Oddanie: <gałąź>`
  zmienia cel na wskazaną gałąź (wartość musi być samą poprawną nazwą gałęzi; inny tekst jest ignorowany, a podpowiedź to mówi). Przycisk i wpis pokazują cel (`target`, „CP → main”); podpowiedź
  podaje liczbę commitów i bazę. Plakietka „N niewypchniętych” w nagłówku jest przyciskiem: panel pokazuje te commity (`delivery.commits`:
  krótki skrót i pierwsza linia opisu, najnowsze pierwsze, do 20, reszta jako „i N starszych” z `commitsMore`); opisy są maskowane przed cięciem
  i wstawiane jako tekst. Wymaga katalogu przebiegu. „READY” tylko w trybie `pr` (`delivery.mode` z
  `.github/.skillsync.yaml`; brak klucza = `pr`, zła wartość = brak przycisków). Dziennika `checkpoints.md` strona nie
  zmienia (pisze go sesja); po zapisie kafelek pokazuje „CP zapisane HH:MM”, a baner „zgoda jest już w pliku”.
- Sesja (`feature`) czekająca na zgodę nasłuchuje (`skillsync approvals wait`) z budzikiem co 30 min i po pobudce sprawdza zgodę
  w pliku według skilla `feature` („Czekanie na zgodę” i „Akty i zgody”). CPD, TAK/NIE
  i odpowiedzi `Q` zostają w sesji (strona nie kopiuje słów zgody do schowka).
- `--session-url` (opcjonalny): adres sesji podany przy starcie (np. Remote Control), tylko `https` bez danych logowania;
  baner dokłada wtedy „Otwórz sesję”. Nie jest czytany z plików projektu, więc agent nie podmieni odnośnika.
- `--no-approvals` = strona wyłącznie do odczytu (brak przycisków, zapis zgody = 403 `approvals_disabled`).

### Podkroki faz, dymki z opisem i sygnały aktu I

- Kroki `Progress` mogą być podkrokami faz (`- [ ] F1.2 …`): liczą się osobno (pasek, „teraz”, lista „w toku”),
  `w toku: F1` wskazuje pierwszy niezaznaczony podkrok fazy, faza pokazuje własne k/n i po zamknięciu zwija się
  (kliknięcie albo Enter rozwija). Stare id bez kropki (`S1`, `S2a`, `A-1`) działają jak dotąd.
- Każdy akt, etap, bramka (przyciski GO/CP/READY), faza, podkrok, baner i nagłówek checkpointów ma dymek ze statycznym
  opisem: co się dzieje, kto wykonuje, wynik i skąd strona wie, że gotowe. Opisy są w `descriptions.json` skilla
  (`{klucz: {title, text}}`, zgodne z procedurą `feature`), serwowane pod `/api/descriptions`; klucz etapu podaje serwer
  (`stages[].key`). Dymek: najechanie, fokus z klawiatury (`aria-describedby`, `role="tooltip"`), dotyk = stuknięcie
  otwiera i zamyka, Esc zamyka.
- Review speca: pliki `spec-review*` w katalogu przebiegu.
- Ostrzeżenie w kafelku Aktu I „brak pre-implement przed GO plan”, gdy spec ma niepuste sekcje `## Kontrakty`, `## Migracja`
  albo `## Dane` (nie „nie dotyczy”), a w przebiegu nie ma `pre-implement*`.
- Kafelek Aktu I nie ma listy pytań: pytania blokujące i założenia ze speca są w zakładce „Pytania” (grupa „Pytania do realizacji”), a pytania do eksperta pokazuje toast w banerze „Pytania do eksperta: N otwartych (M blokujących)”
  z pola `expertQuestions` (pliki `docs/prototypes/<slug>/questions.json` wszystkich prototypów projektu, nie tylko bieżącego przebiegu), z odnośnikiem do
  `/_experts` albo `/_experts/<slug>`; toast nie ma timera i znika, gdy otwartych nie ma. Widzi go każdy, także bez logowania.
- Kafelek Aktu II ma „Readiness” z `readiness.md` przebiegu: PASS zielony, BLOCKED ostrzeżenie, bez pliku neutralny; data,
  skrót Plan-sha (ostrzeżenie, gdy różni się od sha pliku planu) i link do podglądu pliku.
- Kafelek Aktu III ma rozwijaną listę podkroków `Fn.k` z czasem z checkpointów albo „brak danych”.
- Kafelek etapu z `state: active` ma pulsujący wskaźnik (animacja wyłączona przy `prefers-reduced-motion`) i dymek „trwa od HH:MM” (`since`);
  etap „Prototyp” przy ważnym znaczniku zapisu pokazuje „N z M ekranów” z paskiem (bez `screensPlanned`: „N ekranów”) i szacowany koniec z `eta`.
- Baner czekania przy `waiting[].who` pisze „Czeka na: <label> (login)” (wpis `writing` zostaje „Sesja pracuje”); brak `who` = „Sesja czeka na Ciebie”.
- Pasek postępu Aktu III przy `progress.activePercent` > 0 ma dwa odcienie (ciemny = zweryfikowane, jasny = zgłoszone i w toku) i podpis
  „N% zweryfikowane, M% zgłoszone lub w toku”; `run.parseWarnings` daje jedną linię „N fragmentów raportu pominięto” nad checkpointami (szczegóły w dymku).
  Brak każdego z tych pól daje wygląd jak dotąd.

## Zakładka „Testy”

Zakładka „Testy” (ukryta do pierwszego biegu) pokazuje wyniki testów kroków planu uruchomionych przez `skillsync test-run`: sekcja na krok (nagłówek = id i tytuł kroku, najnowsza pierwsza), rozwinięta tylko trwająca, zakończone zwinięte (czerwony chip w nagłówku i licznik czerwonych na zakładce); na zakładce „Postęp” przy kroku jest pasek „ile przeszło z ilu” i przycisk do sekcji (`#tests:<krok>`). Dane to pole `tests` JSON-a i trasa `GET /api/tests` (tylko odczyt, oba gniazda). Próba pokazuje najwyżej 200 grup (najpierw nieudane, potem w toku, reszta po nazwie); gdy `groupsOmitted` > 0, przy grupach próby pokazuje się notka `role="note"` „Pokazano N z M grup (najpierw nieudane i w toku); liczniki powyżej obejmują wszystkie testy.”, a pełne testy ucięte grupy są w `GET /api/tests`. Gdy `tests.skipped` nie jest puste, zakładka pokazuje notkę (`role="note"`) „Pominięto N plików lub katalogów wyników testów (nie dało się ich uwzględnić): ścieżka (powód), …” (do 5 ścieżek, „i N więcej”, „co najmniej 20” przy 20 wpisach; przy pustych sekcjach także „Brak odczytanych wyników.”). **Wyniki testów nie są maskowane** (także dla czytelnika bez sesji w sieci; `tests.mask: on` je maskuje), reszta strony tak; zob. `docs/security.md`.

## Interfejs i API

- `/` strona (zakładki **Postęp**, **Testy**, **Dokumenty**, **Pytania**, **Prototypy**; `D` Dokumenty, `P` Prototypy, a bez nich Postęp; przełącznik motywu jasny/ciemny w nagłówku, klawisz `T`, wybór w `localStorage`, domyślnie motyw systemu), `/assets/*` CSS/JS.
- `/api/status[?plan=docs/plans/x.md]` JSON całego widoku; `/api/doc?path=…` podgląd Markdown (renderer własny, surowy HTML escapowany).
- `/events` SSE: `hello`, `change` (obserwator mtime co 1 s: plany, speki, design, domain, katalogi runów, `.git/HEAD` i ref gałęzi),
  `ping` co 15 s. Klient po `change` pobiera `/api/status` bez przeładowania; gdy SSE pada albo milknie > 40 s, odpytuje co 5 s
  i sam wraca do trybu na żywo. Nagłówki `Cache-Control: no-cache`, `X-Accel-Buffering: no` (tunel/proxy bez buforowania).
- Pola `prototype` tylko z `GET /api/status` (serwer z originem prototypów): `url` (origin widziany przez tego żądającego; `null` = nie do ustalenia, więc bez odnośnika, ramki i przekazania sesji), `served` (origin pokazuje prototypy TEJ zakładki; `false` = bez odnośników i akcji), `frame` (wolno osadzić w ramce); `singleOrigin: true` tylko z `--single-origin`. Adres prototypu: `url` + `/<slug>/`. `up` sprawdza, czy serwer prototypów odpowiada.
- `POST /api/approve` `{kind, path, sha, plan, branch, target, mode[, planState][, tab]}`: tylko akcja, którą strona właśnie oferuje; `tab` obowiązkowy przy kilku zakładkach. `/api/status` ma
  `approvals {enabled, here, viewer, actions[], last}`, `session {url}` (zalogowanemu także jego konto), `spec.fileSha`, `plan.fileSha`, a pozycje banera `word`/`approve`.
- Sesje: `/_login`, `/_api/v1/session|login|logout|password|handoff`. Prototypy dla zalogowanych: `GET /api/prototypes[/<slug>]`, `POST /api/prototypes/<slug>/accept|unaccept|restore|resolve|delete`, `POST /api/project/accept` (gniazdo odczytu: 404).
- Czytelnik bez sesji (także przed zmianą hasła i na gnieździe odczytu) dostaje pseudonimy zamiast loginów i adresów oraz nazwy zamiast ścieżek bezwzględnych, a we wpisach zgód tylko `word`, `at`, `sha`, `source`, `target`, `planState`; **treść dokumentów i wyniki testów są jawne**.
### Assety strony i renderer wspólny ze Skippsterem

| Plik | Rola |
| --- | --- |
| `index.html` | korzenie `<div id="wtabs">` (pasek zakładek, `createTabBar`; ukryty przy jednej zakładce), `<div id="whistory">` (Historia, `createArchiveView`; ukryty, dopóki nie wybrana) i `<div id="app">` (strona zakładki); w `<head>` `app.css` i `theme.js`; skrypty: `dashboard-render.js`, potem `app.js` |
| `dashboard-render.js` | **renderer**: czysty widok JSON (`schema: 1`) → DOM, cały szkielet strony, sprite ikon, dymki, zgody; ten sam plik wczytuje webview Skippstera |
| `app.js` | **adapter strony**: `fetch` `/api/status`, `/api/doc`, `/api/approve` (nagłówki `X-Status-Board: 1` i `X-Skillsync-CSRF`), `/api/descriptions`, sesja (`/_api/v1/*`) i `/api/prototypes`; na serwerze z zakładkami także `/api/tabs`, `/api/history`, `/api/plan-status`, `/api/feedback-approve` i parametr `?tab=<id>` (status, dokumenty, testy, `/events`); SSE `/events` z pollingiem co 5 s i watchdogiem 40 s (dla usuniętej zakładki ani strumienia, ani odpytywania); `localStorage` (`dashboard:plan`, `dashboard:theme`, a przy zakładkach `dashboard:plan:<tab>`, `dashboard:view:<tab>` i `dashboard:terminalNote`); motyw; `#hash` adresu; zegar co 1 s; klawisze na poziomie dokumentu |
| `theme.js` | motyw przed pierwszym rysowaniem (zapamiętany albo systemowy) |
| `app.css` | wygląd; korzeń renderera ma klasę `dashboard-root` (`display: contents`) |
| `descriptions.json`, `descriptions.en.json` | opisy dymków: polski i angielski; serwer wybiera plik wg `lang` statusu (brak pliku = polski). Host kopiujący assety kopiuje oba |

Układ zależy od szerokości kolumny hosta, nie okna: szkielet (nagłówek, zakładki, `main`, stopka) jest w wrapperze `.dash-wrap`
(`container: dash / inline-size`), a progi to `@container dash` 1100, 640, 500 i 420 px (tryb zwarty: panel obok sesji).
Dymek `#descTip` jest poza wrapperem (containment psuje `position: fixed`). Host z własnym CSS przejmuje te reguły z `app.css` i daje `.dash-wrap` określoną szerokość (kontener inline-size nie ma szerokości własnej treści).

Renderer to UMD: `window.DashboardRenderer` w przeglądarce, `module.exports` w Node. Eksport:
`create(root, deps)` (alias `createDashboard`), `createTabBar(root, deps)` (pasek zakładek worktree z alertami), `createArchiveView(root, deps)` (widok Historii: lista, podgląd, potwierdzenie), `buildSprite(doc)`, `SPRITE` (dane symboli `i-…`), `version`, `defaultTexts` (wbudowany polski katalog `ui.*`, `DEFAULT_TEXTS`; generowany z `pl.json` przez `scripts/sync_renderer_defaults.py`, nie edytować ręcznie).
Pasek zakładek i Historia używają ok. 90 kluczy `ui.wt.*` i `ui.history.*` (są w `defaultTexts`, więc host z własnym katalogiem, jak Skippster, dostaje je od razu); host bez zakładek (jedna zakładka, webview edytora) tych funkcji nie wywołuje i niczego nie dostarcza, a kto je wywołuje, podaje `deps.t` i własne korzenie (`createTabBar(root, {t, panel, panelId, archivePanel, onSelect, onToggleAll, onDismiss})`, `createArchiveView(root, {t, mountHtml, onSelect, onAction, onConfirm, onCancel, onMore, onReload, onDismiss, onLogin, panelId})`).
`create` zmienia tylko własny korzeń: dodaje do `root` klasę `dashboard-root`, sprite (pomijany przy `deps.sprite === false`,
gdy host ma własny) i szkielet ze stałymi identyfikatorami (`title`, `meta`, `live`, `planBtn`, `planMenu`, `waitBanner`,
`tab-progress`, `tab-docs`, `tab-prototypes`, `view-prototypes`, `pCount` (te trzy dopiero przy niepustym `prototypes[]`), `acts`, `phases`, `feed`, `feedTitle`, `docList`, `docView`, `foot`, `descTip`,
`elapsed`, `ago`, `retryDoc`, `themeBtn` tylko z `deps.theme`; symbole `i-…`). Identyfikatory są globalne w dokumencie:
dwa egzemplarze renderera w jednym dokumencie się zderzą (jeden korzeń na dokument/webview).

Chip katalogu roboczego (`project.worktree`; host z własnym sprite'em, `deps.sprite === false`, musi mieć symbol `i-folder`, bez niego ikona jest pusta, ale chip działa): w nagłówku, po chipie gałęzi (bez gałęzi sam) i przed znacznikiem „N niewypchniętych”, `span` z ikoną `i-folder` (`worktree: <nazwa>` albo `główny checkout`; `null`, brak pola i nieznany `kind` = bez chipu). Opis z `descriptions.json` (`workdir_chip`), pełna ścieżka (dla worktree także `Główny checkout: <mainPath>`) w `data-desc-extra`; nazwa obcinana wielokropkiem na wewnętrznym `span`. Chip nie ma akcji, więc `deps` bez zmian i bez kopiowania: host bez schowka nic nie traci.
Chip trybu dostarczania (`delivery.mode`, `base`, `source`): w nagłówku, po bloku gałęzi i przed chipem PR; „tryb: pr”, „tryb: direct, baza: develop”, dymek z trybem, bazą i źródłem; zły tryb (`invalid`) to ostrzegawczy chip „tryb dostarczania: błąd”, a nie cichy `pr`; brak pola `delivery` = bez chipu. Host z sesją pokazuje obok, którą zgodą oddaje się pracę, a w nagłówku konto, „Zaloguj”, „Wyloguj”, „Zmień hasło” i pasek z powodem, dla którego zgody dać nie można.

Treść z JSON-a (addytywnie, brak klucza = zachowanie jak dotąd): w kafelkach aktów I i II wiersz „Pomiar:” z `timing.acts` (czas pracy i czekania; zgody nie ma w pliku = czas nieznany), w akcie III wiersz „Prognoza:” z `forecast` (domyślnie zakres pracy do końca kroków i orientacyjna godzina końca z dopiskiem „orientacyjnie”; opcja hosta `tempo` pokazuje samo tempo na krok; bez danych powód; prognoza ma przerywaną ramkę, pomiar ciągłą); tytuły kroków (plan, „teraz”, podkroki), wiersze „Ostatnie checkpointy”, pozycje „czeka na” i treść pytań do eksperta z formatowaniem w linii (`titleHtml`, `whatHtml`, `actionHtml`, `textHtml` także w `run.checkpoints[]`, wstawiane przez `deps.mountHtml`; bez pola zwykły tekst; dymki i atrybuty zawsze zwykły tekst); pod aktami blok „Wynik” (`result`: sformatowany Markdown z `result.html` i `result.moreHtml`, wstawiany przez `deps.mountHtml`, reszta w `<details>` „Pokaż resztę (N linii)”; bez `html` tekst planu w `<pre>`, powyżej 12 linii reszta w `<details>`) i zwijana legenda „Pojęcia” (zawsze; teksty z `legend_*` w descriptions.json, awaryjnie z renderera); w toście pytań adresaci (`expertQuestions.runs[].addressees`: „do wszystkich ekspertów” albo „do: login, …”, widz nic); toast „N nowych komentarzy po akceptacji” z `prototype.commentsAfterAcceptance` (bez timera i „×”, odnośnik tylko z `deps.prototypeUrl`); zakładka „Pytania” (`tab-questions`, `view-questions`, `qCount`) z dwiema grupami: „Pytania do realizacji” (pytania blokujące i założenia ze speca widocznego przebiegu, znacznik „sesja czeka” wg `waiting[]` ze `source: "checkpoint"`, „u eksperta” przy pytaniu oddanym ekspertowi) i „Pytania do eksperta domenowego” (bieżący przebieg: `expertQuestions.current` = `{spec, open, blocking, items}`, pytania powiązane ze ścieżką speca, `null` bez nich; przy braku klucza `current`, czyli starszym serwerze, filtr lokalny po `prototype.slug`); licznik `qCount` = blokujące do realizacji + `current.open`, z rozbiciem w dymku; zakładka ukryta, gdy żadna grupa nie ma treści; listy do 50 wierszy, nawigacją strzałkami/Home/End w liście zakładek i hashem `questions`; lista dokumentów to grupy `<details class="doc-group">` z nagłówkiem `<summary>` (etykieta i licznik): `task` i `run` domyślnie otwarte (można je zwinąć), `specs`, `plans`, `design`, `domain` domyślnie zwinięte (stan w stanie renderera, bez `localStorage` i bez nowych callbacków; wybór dokumentu rozwija jego grupę).

**Tokeny motywu (część kontraktu renderera).** Wygląd strony zależy od zmiennych CSS z bloków `:root` (jasny) oraz `:root[data-theme="dark"]` i `@media (prefers-color-scheme: dark)` (ciemny; oba bloki ciemne mają te same wartości) w `app.css`. Host z własnym motywem musi zdefiniować każdy z nich, inaczej element zostaje bez koloru:
`--bg --panel --panel-2 --line --text --muted --faint --accent --accent-2 --on-accent --accent-soft --glow --done --on-done --done-soft --pending-a --pending-b --warn --warn-soft --planned --planned-soft --bad --bad-soft --radius --shadow --mono --sans`.
Nowy token jest dla takiego hosta zmianą widoczną bez ostrzeżenia, więc **każdy nowy token trafia do CHANGELOG** i do tej listy; pilnuje tego test (`test_skillsync_dashboard_theme_tokens`).

Kontrakt `deps` (wymagane tylko `now`):

| Pole | Typ | Wymagane | Znaczenie |
| --- | --- | --- | --- |
| `now` | `() => number` | tak | czas w ms; renderer nie czyta zegara sam (brak = `TypeError`) |
| `actions.approve` | `({kind, sha, path, word, planState, branch, target, mode}) => Promise<{ok, word, text[, code]} \| void>` | nie | zgoda po dwóch kliknięciach w 5 s, a przy `session` po oknie potwierdzenia; wynik = komunikat w banerze (brak wyniku: host może dać `setNotice`); wyjątek = komunikat `texts.noConnection`; potem `actions.refresh()` |
| `actions.refresh` | `() => void` | nie | prośba o świeży stan (po zgodzie) |
| `actions.selectPlan` | `(path \| null) => void` | nie | wybór planu z menu (`null` = automatycznie); renderer zapisuje go też w `storage` (`plan`) |
| `actions.loadDoc` | `(path) => Promise<{path, status, mtime, truncated, html}>` | nie | jak `/api/doc`; obecność włącza podgląd w zakładce Dokumenty |
| `actions.openDoc` | `(path) => void` | nie | otwarcie dokumentu przez hosta (np. edytor), używane, gdy nie ma `loadDoc` |
| `actions.openLink` | `(kind, id, url) => boolean` | nie | `kind`: `branch`, `pr`, `session`, `prototype`, `experts` (toast pytań do eksperta); `true` = host otworzył sam (renderer robi `preventDefault`), inaczej domyślne `<a target=_blank>` |
| `canApprove` | `(json) => boolean` | nie | czy ten host może dawać zgody; domyślnie `approvals.here` z JSON-a |
| `session` | `() => null \| {state: "loading"\|"anonymous"\|"user", name, roles, approver, mustChange, loginAllowed, expired, legacy, tab, passwordMin, passwordSignIn}` | nie | sesja hosta: nagłówek konta, zgody tylko dla `approver`; bez niej renderer działa bez kont |
| `actions.login`, `actions.logout`, `actions.changePassword` | `()`, `() => {ok}`, `({current, next}) => {ok, code}` | nie | przyciski konta i okno zmiany hasła (bez hasła w `deps`) |
| `prototypeFrame` | `boolean` | nie | `true` przy `prototype.frame` włącza ramkę w zakładce Prototypy |
| `actions.loadPrototype`, `actions.prototypeAction` | `({slug}) => {ok, data}`, `({slug, action, body}) => {ok, code, text}` | nie | widok „Akceptacje i wątki” (z `session`); wysyłane są hashe i wersja z tego widoku |
| `storage` | `{get(k) => string \| null, set(k, v)}` | nie | preferencje (`v == null` = usuń); bez niego wybór planu nie jest zapamiętywany |
| `theme` | `{isDark() => boolean, toggle()}` | nie | przełącznik motywu i klawisz `T`; bez niego przycisku nie ma |
| `texts` | `{live?, liveTip?, noConnection?}` albo `(lang) => {live?, liveTip?, noConnection?}` | nie | funkcja jest wołana przy każdym odczycie z aktualnym językiem statusu (wyjątek lub wynik inny niż obiekt = brak nadpisania); nadpisanie tekstów wskaźnika (`live`, `polling`, `down`, `connecting`), ich dymków i komunikatu braku połączenia; tylko te klucze, pozostałe `ui.*` idą z pola `texts` statusu albo z polskiego katalogu |
| `prototypeUrl` | `(port, slug) => string` | nie | adres prototypu, gdy status nie ma klucza `prototype.url` (`show`, starszy serwer); bez niego odnośnik bez `href` |
| `prototypeOrigin` | `(port) => string` | nie | w tym samym przypadku origin bez ścieżki (toast pytań do eksperta: `/_experts[/<slug>]`) |
| `mountHtml` | `(el, html) => void` | nie | wstawienie HTML podglądu Markdown i pól `…Html` z `GET /api/status` (escapowanych przez serwer); renderer sam HTML nie parsuje. Bez `mountHtml` (np. wtyczka edytora, która czyta `dashboard show` bez tych pól) strona pokazuje zwykły tekst |
| `forecastView` | `"tempo"` albo `"range"` | nie | widok prognozy końca: `range` (domyślnie, stała `FORECAST_VIEW`: przedział pracy i orientacyjna godzina końca; powtórka na prawdziwych przebiegach: rząd wielkości, nie obietnica, zob. kontrakt) albo `tempo` (samo tempo na krok i liczba pozostałych kroków) |
| `onTitle` | `(text) => void` | nie | tytuł okna (`⏳ Czeka · 40% · … — status`) |
| `onNavigate` | `(hash) => void` | nie | adres widoku: `progress`, `tests`, `docs`, `docs:<ścieżka>`, `questions`, `prototypes` |
| `scroll` | `{save() => any, restore(v), reveal(el, inDocs), toTop?: {subscribe(cb) => unsubscribe, go()}}` | nie | przewijanie przy odświeżeniu i otwarciu dokumentu; opcjonalny `toTop` włącza pływający przycisk „na górę” w widoku dokumentu i w widoku Testy: host woła `cb({y, viewport})` przy przewinięciu i zmianie rozmiaru okna, renderer pokazuje przycisk powyżej 0,75 wysokości okna i chowa poniżej 0,5, a `go()` przewija okno na górę; bez `toTop` (webview) przycisku nie ma |
| `timers` | `{set(fn, ms) => id, clear(id)}` | nie | zegar uzbrojenia (5 s); domyślnie `setTimeout` |
| `raf`, `cancelRaf` | `(fn) => id`, `(id) => void` | nie | klatka animacji pasków; domyślnie `requestAnimationFrame`/`cancelAnimationFrame`; `destroy()` anuluje oczekujące |
| `viewport` | `() => {width, height}` | nie | rozmiar okna dla dymku; domyślnie `ownerDocument.defaultView` |
| `sprite` | `boolean` | nie | `false` = nie tworzyć sprite'a (host ma własny) |

Metody obiektu z `create`:

| Metoda | Znaczenie |
| --- | --- |
| `update(json)`, alias `render(json)` | rysuje stan; poprzedni stan służy do podświetlenia zmian; w widoku Dokumenty odświeża podgląd |
| `setDescriptions(map)` | opisy dymków `{klucz: {title, text}}` (`descriptions.json` albo `descriptions.en.json`, wg języka) |
| `t(key, params, n)` | tekst klucza `ui.*` w języku ostatniego statusu: katalog ze statusu `texts`, a brak klucza → wbudowany polski; `n` wybiera formę liczby mnogiej, `{nazwa}` z `params`; nieznany klucz zwraca klucz |
| `lang()` | język ostatniego statusu (`pl`, gdy pola `lang` nie ma) |
| `setNotice({ok, word, text} \| null)` | komunikat w banerze; gaśnie sam (ok po 10 s, błąd po 30 s), „×” zamyka od razu |
| `setVisible(bool)` | host: karta widoczna/ukryta (`visibilitychange`); odliczanie komunikatu stoi, gdy karta jest w tle, i startuje od nowa po powrocie (domyślnie widoczna) |
| `setLive(mode)` | wskaźnik `live` / `polling` / `down` / `connecting` |
| `setView(view, push = true)`, `setDoc(path)` | zakładka (jak w `onNavigate`); wybrany dokument (bez wczytania) |
| `tick()` | host woła co 1 s: czas aktu III, „x min temu”, próg ciszy |
| `handleKey(event)` | host podpina `keydown` dokumentu: `P`/`D`/`T`/`Esc` |
| `sessionChanged()`, `closeDialogs()` | po zmianie sesji; gdy host chowa stronę (zamyka okna i ramkę) |
| `closeMenu()`, `hideTip()` | host: kliknięcie poza korzeniem, przewinięcie |
| `syncTheme()` | po zmianie motywu systemu |
| `state()` | `{view, doc, armed, approving, notice}` (testy, diagnostyka) |
| `destroy()` | anuluje zegary i klatki, zdejmuje nasłuch, czyści korzeń |

Wymagania wobec hosta webview: `approvals.here` jest tylko w `/api/status` (`dashboard show` go nie zwraca), więc host, który
pozwala na zgody, podaje `canApprove` (inaczej przyciski są nieaktywne z powodem „Zgoda ze strony tylko lokalnie albo przez TLS…”). Host zawsze
przejmuje `actions.openLink` (zwraca `true`) i nie zostawia domyślnej nawigacji w webview. Renderer wstawia do `href` tylko adresy
`http:`/`https:` (inny schemat, np. `javascript:`, `data:` = odnośnik bez `href`, tekst bez zmian).

Czytelnik bez prawa zgody: pole `approvals.viewer` (gniazdo odczytu, `--no-approvals` albo żądanie ani lokalne, ani przez TLS; brak pola = false; `approvals.here` jest wtedy false). Przy
`approvals.viewer === true` każdy przycisk zgody poza
„zgoda dana” jest nieaktywny, a jego dymek ma powód „Zgody daje prowadzący na komputerze z sesją.” (zamiast powodu stanu i
„Zgoda ze strony tylko lokalnie…”); renderer nie rysuje przycisków kopiowania słowa zgody (nigdy, także poza trybem widza; brak `actions.copyText` i schowka) i nie wywołuje
`actions.approve` (także z `canApprove`). Baner (`waiting`, cisza), sekcja Readiness, toast pytań oraz stopka „tylko odczyt” bez zmian.

Sekcje aktów rysuje renderer (`readinessTile`, `substepsBlock`, funkcje modułu z kontekstem egzemplarza):
Readiness w akcie II (`readiness`; link `readiness.source` jak inne dokumenty: podgląd z `loadDoc`, inaczej `openDoc`), Podkroki i czas w akcie III
(`substeps`, „brak danych” przy `missingData`; rozwinięcie `details` pamiętane w stanie renderera między `update()`),
Baner czekania ma też pozycje z plików: „agent pisze ekrany (do HH:MM)” (`source: writing`, ważny znacznik `.writing.json`, zastępuje wezwanie do akceptacji prototypu) i „odpowiedź eksperta (N blokujących)” (`source: expert`, gdy `expertQuestions.blocking > 0` bez `czeka:` w checkpoincie), z odnośnikiem jak w toaście pytań; pole `prototype.writing` niesie `{since, expiresAt, renewedAt}`.
Czas pracy i czekania w akcie III (`timing`: czas pracy i czekania na człowieka per faza i suma przebiegu z checkpointów `czeka:` i `approvals.jsonl`; okno bez zamknięcia i krok bez znaczników to „nieznane”, nigdy zero ani szacunek; sekcja `timingBlock`).
Poniżej 500 px szerokości akty I i II stoją w jednej kolumnie.

Zasady renderera (CSP webview: `default-src 'none'`, skrypty i style z nonce, bez `connect-src` i `unsafe-inline`), pilnowane
testem statycznym `tests/test_skillsync_dashboard_renderer.py`: bez `fetch`, `EventSource`, `XMLHttpRequest`, `WebSocket`,
`postMessage`, `localStorage`/`sessionStorage`, ciasteczek, globalnych `window`/`document`/`location`/`history`/`navigator`,
dostępu do dokumentu poza korzeniem (`ownerDocument.querySelector`/`getElementById`/`body`/`documentElement` …),
`setInterval`, `Date.now()`; DOM tylko przez `createElement`/`createElementNS`/`textContent`/`setAttribute` (pomocnik `h()`,
dane zawsze jako tekst: węzłem jest tylko obiekt z `nodeType`, inny obiekt staje się tekstem), bez `innerHTML`/`outerHTML`/
`insertAdjacentHTML`, `eval`/`Function`, atrybutu `style` i `cssText` (szerokość pasków i pozycja dymku przez CSSOM
`el.style.width`), bez handlerów inline (tylko `addEventListener`) i bez znaczników HTML w napisach. Nowy callback renderera =
zmiana kontraktu ze Skippsterem (zgłoszenie przed zapisem).

## Bezpieczeństwo

- Zgoda (i zmiana statusu w Historii): kolejno żądanie lokalne albo TLS (wyjątek: `--allow-remote-approvals`), `X-Status-Board: 1`, `Content-Type: application/json`, dokładny `Origin` zgodny z `Host`, `Sec-Fetch-Site: same-origin`,
  ciasteczko sesji, token CSRF, rola `approver`, hasło bez wymuszonej zmiany, zgody włączone; ciało do 4 KB. Każda zgoda i odmowa trafia do `.skillsync/prototype/run/audit.jsonl`; bez wiersza audytu zgody nie ma (503 `audit_unavailable`).
  Nagłówek pośrednika (`Forwarded`, `X-Forwarded-*`, `Via` i podobne) sprawia, że żądanie nie jest lokalne. Sesja `approver` wygasa po 2 h bez zapisu, najpóźniej po 12 h.
- **Granice zaufania (jawne):** granicą jest maszyna i użytkownik systemu. Pola `by`, `role`, `session` (i `source`) w `approvals.jsonl` to ślad audytowy, nie weryfikacja: kod czytelników ich nie sprawdza (warunki, które sesja stawia wpisom `dashboard`, to reguła procedury, nie uwierzytelnienie),
  a proces tego samego użytkownika systemu może dopisać wpis do pliku albo uruchomić `approvals record`. Reguła „agent nie zapisuje zgód” nie jest egzekwowana technicznie.
- Odczyt nie wymaga logowania: widzi go każdy, kto dociera do portu (plany, specyfikacje, checkpointy, wyniki, raporty); maska sekretów to heurystyka, ruch bez TLS jest jawny.
  Kontrola `Host` na wszystkich endpointach chroni przed DNS rebinding.
- Whitelist podglądu: `*.md` w `docs/specs`, `docs/plans`, `docs/design`, `docs/domain` oraz `.skillsync/runs/<run>/*.md`;
  odrzucane `..`, ścieżki absolutne, ukryte katalogi, symlinki prowadzące poza whitelistę, pliki inne niż `.md` (np. `.env`).
- CSP `script-src 'self'`, ramki tylko z originu prototypów; strona nie zapisuje niczego z danych (zapisy: zgody i ich audyt, status planu, akcje na prototypach, stan serwera), limit 2 MB na dokument i 64 połączeń SSE.

## Wskaźniki przebiegów

Generyczne wskaźniki przebiegów (czasy kroków, liczby review i testów z `checkpoints.md` i raportów) liczy polecenie
`skillsync dashboard metrics`, a nie strona; serwer nie ma opcji `--metrics`.

## Testy

```bash
cd <repo skillsync> && python3 -m unittest tests.test_skillsync_dashboard_server tests.test_skillsync_dashboard tests.test_skillsync_dashboard_renderer
```

`test_skillsync_dashboard_renderer` sprawdza renderer statycznie (zasady wyżej, z kontrolą ujemną) i w Node ≥ 18
(`SKILLSYNC_TEST_NODE` albo `node` z PATH; inaczej pominięty): rysuje każdy `tests/fixtures/dashboard/*/expected*.json` na
cieniu DOM, który rzuca wyjątek przy wstrzykiwaniu HTML i stylu. Wyglądu (CSS, układ) nie sprawdza: to zadanie przeglądarki.

Testy HTTP wybierają wolny port z zakresu 5100–5199 (inny niż 5140) i zamykają serwer po teście. Fixtury w `tests/fixtures/`
są zanonimizowanymi kopiami formatów.

## Ograniczenia

- Renderer Markdown jest prosty (nagłówki, akapity, listy z checkboxami, tabele, kod, cytaty, linki); brak obrazów i HTML.
- Etapy aktów I i II są wnioskowane z nazw plików w katalogu przebiegu; inne nazewnictwo da „niezaliczone” etapy, nie błąd.
- Procent zależy od dyscypliny zaznaczania `[x]` w planie po każdym kroku.

- Kafelek etapu `active` pulsuje tylko, dopóki przebieg nie jest cichy (`silence`, a bez niego `run.lastReportAt` i próg 35 min); potem pusta kropka i „brak aktywności od N min”, stan w danych zostaje. Jasny odcień paska postępu (zgłoszone lub w toku) ma kreskowanie i obrys w kolorze akcentu (kontrast akcentu z torem ≥ 3:1 w obu motywach), a próbka w legendzie ma tę samą definicję (`--bar-light`).
