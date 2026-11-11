---
name: dashboard
description: "Strona statusu przebiegu feature: akty, postęp, checkpointy, podgląd speca i planu; komenda skillsync dashboard serve, jedyny zapis to zgody z localhost."
user-invokable: true
---

# dashboard: status przebiegu `feature` na żywo

Serwer jest częścią generatora (`skillsync dashboard serve`, stdlib Pythona 3.11+, bez pip, bez CDN, działa offline);
ten skill dostarcza stronę (`assets/`: HTML, CSS, JS) i opisy dymków (`descriptions.json`). Serwer czyta pliki skilla
`feature` w projekcie i pokazuje je jako stronę dla widzów (rzutnik, przeglądarka przez tunel SSH), odświeżaną na żywo,
gdy agenci pracują. Stan strony to ten sam JSON co `skillsync dashboard show` (kontrakt `schema: 1`,
`docs/dashboard-contract.md` w repo skillsync). Nie wykonuje poleceń z danych. Jedyny zapis to zgoda użytkownika z
przycisku (niżej: „Przyciski zgód”).

## Uruchomienie

```bash
skillsync dashboard serve <projekt> [--port 5140] [--host 127.0.0.1] [--approve-host NAZWA ...] [--read-host NAZWA ...] [--no-approvals] [--plan docs/plans/<plik>.md] [--session-url https://…] [--layer id=ścieżka ...]
skillsync dashboard show <projekt> [--json] [--plan docs/plans/<plik>.md]   # jednorazowy zrzut JSON (to samo co /api/status)
```

- Serwer działa na pierwszym planie (Ctrl+C albo SIGTERM kończy, wynik `{"status": "PASS", "stopped": true}`); odmowa startu
  (port spoza zakresu, brak manifestu, brak `assets/index.html` w aktywnych warstwach, zły `--session-url` albo `--plan`)
  = `BLOCKED`, kod 2. Strona i `descriptions.json` pochodzą ze skilla `dashboard` aktywnych warstw (`--layer` jak w innych
  komendach; późniejsza warstwa wygrywa per plik).

- Port domyślny **5140**, dozwolony zakres **5100–5199** (nigdy 4444–4448, 9222, 9333). Jeden proces na projekt;
  port wybiera się per projekt (`--port`), żeby kilka projektów mogło działać równolegle.
- Domyślnie nasłuch na `127.0.0.1`; dalej wystawia go tunel (`ssh -R 5140:127.0.0.1:5140 …`; przeglądarka po drugiej
  stronie wysyła `Host: localhost`, więc działa bez flag). `--host 0.0.0.0` = wszystkie interfejsy (podgląd z telefonu lub
  innej maszyny w **zaufanej** sieci); każdy endpoint odpowiada jednak tylko pod nagłówkiem `Host` z loopbacku albo z
  `--approve-host`, więc adres z paska przeglądarki innego urządzenia (nazwa lub IP) trzeba podać w `--approve-host`
  (inaczej 403). `--approve-host` dopuszcza odczyt i zgody; `--read-host <adres>` (powtarzalny) dopuszcza tylko odczyt:
  pokaz przez tunel, w którym widzowie wysyłają `Host: <nazwa>:5140`, widzą wszystko (także założenia i baner „czeka:”),
  ale `POST /api/approve` z przeglądarki widza dostaje 403, a `/api/status` ma `approvals.viewer: true` i `here: false`; zgody daje
  prowadzący pod localhost. To nie uwierzytelnienie: klient podrabiający nagłówek `Host` (np. `curl -H 'Host: localhost'`
  przez tunel) zapisze zgodę, a tunel przepisujący `Host` na localhost daje zapis każdemu widzowi; przy nieufnych
  widzach `--no-approvals`. Model dwóch instancji dla nieufnych widzów: prowadzący na loopbacku ze zgodami, widzowie na osobnej instancji `skillsync dashboard serve <projekt> --port <inny port> --no-approvals --read-host <host>` (bez zapisu, ten sam projekt; wspólny jest tylko `approvals.jsonl`). Widz widzi też podgląd `/api/doc` bez maskowania sekretów (surowy). Podgląd bez zgód dla wszystkich = `--no-approvals`. Serwer
  wypisuje przy starcie ostrzeżenie i adresy LAN do otwarcia.
  Odnośnik do prototypu używa hosta, pod którym otwarto stronę, więc działa także z innego urządzenia (gdy prototypy też słuchają na LAN).
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
  Jawne `--plan` albo wybór planu na stronie wyłącza ten tryb. Alternatywa (szkielet planu na starcie aktu I w `feature`) to
  zmiana rdzenia, poza tym skillem.
- Kafelek Akt I ma przycisk „Otwórz specyfikację”, Akt II „Otwórz plan” (gdy plik istnieje): przejście do zakładki
  **Dokumenty** z otwartym plikiem.
- Link do prototypu (z licznikiem zaakceptowanych ekranów) stoi przy zakładkach **Postęp**/**Dokumenty**; na telefonie
  sama ikona z licznikiem.
- Wrapper projektu (np. `scripts/status.sh`) i port w tunelu dodaje projekt, nie ten skill.

## Co czyta (formaty skilla `feature`)

| Plik | Co | Na stronie |
|---|---|---|
| `docs/plans/<plan>.md` | `# tytuł`, `Status:`, `Spec:`, `Spec-sha:`, `Prototyp:`, `Przebieg:`; sekcja `## Progress` z `### Faza …` i krokami `- [ ] S1 Tytuł` / `- [x] S1 Tytuł — sha` (id `S2b`, `A0`, `A-1`) | nagłówek, Akt II, Akt III, fazy |
| spec z `Spec:` | `Status:` (draft → w trakcie; approved/implemented → zrealizowany; brak → zaplanowany) | Akt I |
| `.skillsync/runs/<run-id>/checkpoints.md` | `RUN=<iso>` (czas od startu), linie `- HH:MM tekst` | feed (najnowsze u góry), „w toku” |
| `.skillsync/runs/<run-id>/*.md` | nazwy `spec-review*`, `plan-review*`, `pre-implement*`, `*prototyp*` | etapy aktów I i II, lista dokumentów |
| `.git/HEAD`, `.git/config` (`[remote "origin"] url`), `refs/remotes/origin/<gałąź>` | gałąź, HEAD, adres repozytorium (Azure DevOps, GitHub), czy gałąź jest na serwerze | nagłówek: gałąź jako link do repozytorium (tylko gdy jest na `origin`; inny host = sam tekst) |
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
- Odnośnik: spec albo plan w zakładce **Dokumenty**, prototyp na porcie serwera prototypów. Sam baner niczego nie
  zatwierdza: zgodę daje przycisk zgody w kafelku aktu lub banerze (sekcja „Przyciski zgód i słów”) albo słowo wpisane
  przez użytkownika w sesji.

Zdanie do instrukcji/promptów (zgodne z formatem „zrobione / w toku / ryzyko”): *„Gdy zatrzymujesz się na decyzji
człowieka, dopisz do checkpointu segment `czeka: <GO plan|GO implement|akceptacja prototypu <slug>|odpowiedź Q1–Qn|CP|READY|TAK/NIE <run-id>/P<n>>`;
następny checkpoint po wznowieniu pisz już bez `czeka:`.”*

### Przyciski zgód i słów

**Reguła:** agent nie wywołuje zgód. Zgodę zapisuje wyłącznie użytkownik (przycisk na stronie albo
`skillsync approvals record` uruchomione przez niego); agent jej nie wywołuje, nie symuluje ani nie podsuwa wpisów.
Komenda przyjmuje `--source cli` albo `--source ide`; `source: "dashboard"` zapisuje tylko serwer strony we własnym
procesie. Reguła nie jest egzekwowana technicznie (proces tego samego użytkownika mógłby zapisać wpis); jej śladem są
pola `source` i `by` wpisu.

Każdy przycisk zgody (Akt I „GO plan”, Akt II „GO implement”, Akt III „CP” i — w trybie `pr` — „READY”) ma jeden z
czterech stanów (`approvals.actions[].state`):

| Stan | Kiedy | Wygląd |
|---|---|---|
| przed | akt jeszcze nie nadszedł (np. „GO implement” w akcie I, „CP” przed startem realizacji) | brak przycisku |
| `running` (trwa) | akt trwa, ale stan jeszcze nie czeka na zgodę | widoczny, nieaktywny, podpowiedź „czeka na…” |
| `waiting` (czeka) | GO plan: spec `draft` z sygnałem `czeka na GO plan` (status speca/szkicu albo `czeka:`), prototyp zaakceptowany; GO implement: plan `draft` z `czeka na GO implement`, spec `approved`; CP/READY: plan `done`, `czeka: CP|READY` albo niewypchnięte commity gałęzi roboczej | aktywny (dwa kliknięcia) |
| `after` (po) | zgoda już dana (spec/plan `approved`+, wpis CP/READY dla obecnego HEAD) | nieaktywny, „zgoda dana HH:MM”; drugi zapis = odmowa |

Sygnał „czeka” pisze sesja (status pliku albo `czeka:` w checkpoincie): pliki przebiegu mówią tylko, że runda review
była, a nie, że pętla dała PASS.

- **„GO plan”** (spec `draft`, bez planu albo z planem-szkicem) i **„GO implement”** (plan `draft` bez sufiksu szkicu;
  spec, jeśli jest, już `approved`): przycisk w kafelku aktu i w banerze. Dwa kliknięcia (drugie w ciągu 5 s), potem serwer
  w swoim procesie zmienia status tą samą funkcją rdzenia co `skillsync spec approve` / `plan approve` z `--expect-sha`
  (`status.set_doc_status`, sha = wersja pokazana na stronie; zmieniony plik = odmowa „przeczytaj ponownie”); bez
  podprocesu `skillsync`, więc wersja CLI w PATH nie ma znaczenia. To zgoda użytkownika sesji, równoważna wpisaniu słowa; procedura
  `feature` uznaje ten status i sesja działa według tabeli wznowienia przy następnej turze (strona podpowiada: napisz
  „dalej”). Przycisk jest wyłączony z powodem, gdy prototyp nie jest zaakceptowany albo „GO implement” wyprzedza „GO plan”.
- Ślad zgód GO: wpis `status.record_approval` (`source: "dashboard"`) w `approvals.jsonl` przebiegu (`sha` = pokazana
  wersja pliku, `shaAfter`, `file`, `run`). Bez katalogu przebiegu śladu nie ma (odpowiedź `trail: null`): zgoda zostaje
  w linii `Status:` pliku, którą sesja i `approvals wait --file` i tak czytają. Serwer nie ma własnego dziennika.
- **„CP” i „READY” (tryb `pr`) w kafelku Aktu III** — przycisk zapisuje zgodę, nie otwiera sesji.
  Dwa kliknięcia; serwer zapisuje przez `status.record_approval` (ta sama funkcja co `skillsync approvals record`: blokada
  plikowa, odmowa drugiego wpisu dla tego samego HEAD) do `.skillsync/runs/<run-id>/approvals.jsonl` wpis
  `{"schema": 1, "at", "word": "CP"|"READY", "sha": <pełny HEAD pokazany na stronie>, "run", "plan", "target", "branch", "ahead", "mode", "by", "source": "dashboard"}`.
  Zgoda dotyczy tego HEAD: nowy commit = nowa zgoda; ponowny zapis dla tego samego HEAD = odmowa. Przycisk aktywny, gdy
  plan jest `done`, najnowszy checkpoint ma `czeka: CP|READY` albo gałąź robocza ma commity, których nie ma na origin
  (`git rev-list --count origin/<robocza>..HEAD`, a bez gałęzi na origin `HEAD --not --remotes=origin`; tylko odczyt).
  „CP” oddaje na `origin/<robocza>` (rdzeń AGENTS: nigdy na bazę); w trybie `direct` linia planu `Oddanie: <gałąź>`
  zmienia cel na wskazaną gałąź. Przycisk i wpis pokazują cel (`target`, „CP → main”); podpowiedź
  podaje liczbę commitów i bazę. Wymaga katalogu przebiegu. „READY” tylko w trybie `pr` (`delivery.mode` z
  `.github/.skillsync.yaml`; brak klucza = `pr`, zła wartość = brak przycisków). Dziennika `checkpoints.md` strona nie
  zmienia (pisze go sesja); po zapisie kafelek pokazuje „CP zapisane HH:MM”, a baner „zgoda jest już w pliku”.
- Sesja (`feature`) czekająca na zgodę nasłuchuje zmian (`skillsync status`, pole `approvals`) z budzikiem co 30 min i po
  pobudce sprawdza zgodę w pliku: GO = status speca/planu i sha, CP/READY = wpis z `sha` równym jej HEAD. CPD, TAK/NIE
  i odpowiedzi `Q` zostają w sesji (baner: „Kopiuj”).
- **Założenie zaufania (jawne):** granicą jest maszyna. Strona nie ma logowania; zapis tylko z localhost albo
  `--approve-host`. Lokalny proces, który podrobiłby wpis, mógłby równie dobrze sam wykonać push, więc wpis nie jest
  słabszy niż „CP” wpisane w terminalu. Nie wystawiaj strony z `--approve-host` w niezaufanej sieci.
- `--session-url` (opcjonalny): adres sesji podany przy starcie (np. Remote Control), tylko `https` bez danych logowania;
  baner dokłada wtedy „Otwórz sesję”. Nie jest czytany z plików projektu, więc agent nie podmieni odnośnika.
- `--no-approvals` = strona wyłącznie do odczytu (brak przycisków, `POST /api/approve` = 403).

### Podkroki faz, dymki z opisem i sygnały aktu I

- Kroki `Progress` mogą być podkrokami faz (`- [ ] F1.2 …`): liczą się osobno (pasek, „teraz”, lista „w toku”),
  `w toku: F1` wskazuje pierwszy niezaznaczony podkrok fazy, faza pokazuje własne k/n i po zamknięciu zwija się
  (kliknięcie albo Enter rozwija). Stare id bez kropki (`S1`, `S2a`, `A-1`) działają jak dotąd.
- Każdy akt, etap, bramka (przyciski GO/CP/READY), faza, podkrok, baner i nagłówek checkpointów ma dymek ze statycznym
  opisem: co się dzieje, kto wykonuje, wynik i skąd strona wie, że gotowe. Opisy są w `descriptions.json` skilla
  (`{klucz: {title, text}}`, zgodne z procedurą `feature`), serwowane pod `/api/descriptions`; klucz etapu podaje serwer
  (`stages[].key`). Dymek: najechanie, fokus z klawiatury (`aria-describedby`, `role="tooltip"`), dotyk = stuknięcie
  otwiera i zamyka, Esc zamyka. Test pilnuje, żeby każdy użyty klucz miał opis.
- Review speca: pliki `spec-review*` w katalogu przebiegu.
- Ostrzeżenie w kafelku Aktu I „brak pre-implement przed GO plan”, gdy spec ma niepuste sekcje `## Kontrakty`, `## Migracja`
  albo `## Dane` (nie „nie dotyczy”), a w przebiegu nie ma `pre-implement*`.
- Kafelek Aktu I ma sekcję „Pytania”: pytania blokujące i założenia do korekty przy prototypie z sekcji
  `## Pytania otwarte` speca (pusta lista = „brak”); widzi ją każdy, także widz `--read-host`. Założenia nie są akcją
  „Odpowiedz na pytania”.
- Kafelek Aktu II ma „Readiness” z `readiness.md` przebiegu: PASS zielony, BLOCKED ostrzeżenie, bez pliku neutralny; data,
  skrót Plan-sha (ostrzeżenie, gdy różni się od sha pliku planu) i link do podglądu pliku.
- Kafelek Aktu III ma rozwijaną listę podkroków `Fn.k` z czasem z checkpointów albo „brak danych”.

## Interfejs i API

- `/` strona (zakładki **Postęp** i **Dokumenty**, klawisze `P`/`D`; przełącznik motywu jasny/ciemny w nagłówku, klawisz `T`, wybór w `localStorage`, domyślnie motyw systemu), `/assets/*` CSS/JS.
- `/api/status[?plan=docs/plans/x.md]` JSON całego widoku; `/api/doc?path=…` podgląd Markdown (renderer własny, surowy HTML escapowany).
- `/events` SSE: `hello`, `change` (obserwator mtime co 1 s: plany, speki, design, domain, katalogi runów, `.git/HEAD` i ref gałęzi),
  `ping` co 15 s. Klient po `change` pobiera `/api/status` bez przeładowania; gdy SSE pada albo milknie > 40 s, odpytuje co 5 s
  i sam wraca do trybu na żywo. Nagłówki `Cache-Control: no-cache`, `X-Accel-Buffering: no` (tunel/proxy bez buforowania).
- Prototyp: odnośnik `http://<host strony>:5130/<slug>/` tylko gdy serwer prototypów na 5130 odpowiada.
- `POST /api/approve` `{kind: spec|plan, path, sha}`: tylko akcja, którą strona właśnie oferuje; `/api/status` ma
  `approvals {enabled, here, viewer, actions[]}`, `session {url}`, `spec.fileSha`, `plan.fileSha`, a pozycje banera `word`/`approve`.

## Bezpieczeństwo

- Strona nie ma logowania: z `--approve-host` przy `0.0.0.0` widzi ją każdy w tej sieci, kto otworzy podany adres (plany,
  speki, checkpointy).
- Kontrola `Host` na wszystkich endpointach (strona, assety, `/api/*`, `/events`, `/healthz`, `POST`): nagłówek `Host` musi
  być `localhost`/`127.0.0.1`/`[::1]` (dowolny port) albo nazwą podaną jawnie w `--approve-host` lub `--read-host` (np. nazwa
  maszyny przy tunelu, IP w LAN); inny = 403 bez treści (ochrona przed DNS rebinding), odmowa w logu przy `--verbose`.
  Zapis zgody tylko z loopbacku albo `--approve-host`; host z `--read-host` czyta, ale nie zapisuje z przeglądarki (klienta podrabiającego `Host` nie powstrzyma; nieufni widzowie = `--no-approvals`).
- Zapis zgody dodatkowo: wymagany nagłówek `X-Status-Board: 1` (preflight CORS, którego
  serwer nie obsługuje, blokuje obce strony), `Origin` — jeśli jest — z tej samej listy hostów, `Content-Type: application/json`, limit 4 KB.
  Bez uwierzytelnienia z decyzji właściciela: narzędzie programisty na jego maszynie; agent i tak może wywołać CLI.
- Whitelist podglądu: `*.md` w `docs/specs`, `docs/plans`, `docs/design`, `docs/domain` oraz `.skillsync/runs/<run>/*.md`;
  odrzucane `..`, ścieżki absolutne, ukryte katalogi, symlinki prowadzące poza whitelistę, pliki inne niż `.md` (np. `.env`).
- CSP `script-src 'self'`, poza zgodami (linia `Status:` przy GO, wpis `approvals.jsonl`) brak zapisu na dysk (logi tylko na stderr), limit 2 MB na dokument i 64 połączeń SSE.

## Wskaźniki przebiegów

Generyczne wskaźniki przebiegów (czasy kroków, liczby review i testów z `checkpoints.md` i raportów) liczy polecenie
`skillsync dashboard metrics`, a nie strona; serwer nie ma opcji `--metrics`.

## Testy

```bash
cd <repo skillsync> && python3 -m unittest tests.test_skillsync_dashboard_server tests.test_skillsync_dashboard
```

Testy HTTP wybierają wolny port z zakresu 5100–5199 (inny niż 5140) i zamykają serwer po teście. Fixtury w `tests/fixtures/`
są zanonimizowanymi kopiami formatów.

## Ograniczenia

- Renderer Markdown jest prosty (nagłówki, akapity, listy z checkboxami, tabele, kod, cytaty, linki); brak obrazów i HTML.
- Etapy aktów I i II są wnioskowane z nazw plików w katalogu przebiegu; inne nazewnictwo da „niezaliczone” etapy, nie błąd.
- Procent zależy od dyscypliny zaznaczania `[x]` w planie po każdym kroku.
