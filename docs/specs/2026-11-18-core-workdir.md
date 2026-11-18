# Spec: Chip katalogu na stronie statusu i wybór katalogu roboczego przy „GO plan” (CORE-WORKDIR)

Status: approved
Data: 2026-11-18
Plan: docs/plans/2026-11-18-core-workdir.md
Wersja docelowa: 1.10.0 (MINOR: nowe pole kontraktu, zmiana procedury `feature`)

## Problem

1. Strona statusu pokazuje ścieżkę projektu tylko w stopce („projekt: <ścieżka>”). Nic w nagłówku nie mówi, czy panel pokazuje główny checkout, czy powiązany worktree,
   a przy kilku sesjach na jednym repozytorium (karty dispatch, worktree zadań) łatwo pomylić panele.
2. Rdzeń wymusza od „GO plan” osobny worktree i gałąź roboczą (`instructions/_base.md`, `feature/SKILL.md`, `plan-large.md`). Użytkownik, który pracuje sam w jednym katalogu,
   płaci za to kosztem (drugi katalog, kopiowanie `.github/.skillsync.yaml`, `skillsync link`, kopia przebiegu przed sprzątaniem), a nie ma wyboru.

Decyzje Barcisia z 18.11: chip w nagłówku (nie tylko stopka); pytanie o katalog dopiero przy „GO plan”.

## Zakres

W zakresie:
- A1: pole `project.worktree` w `dashboard show` i `GET /api/status` (addytywne), rozpoznawane bez uruchamiania gita; sekcja w `docs/dashboard-contract.md`.
- A2: chip „worktree: <nazwa>” albo „główny checkout” w nagłówku obok chipu gałęzi; pełna ścieżka w dymku; stopka bez zmian. **Kopiowanie kliknięciem: poza zakresem do decyzji Q4** (koliduje z celowym usunięciem kopiowania z renderera).
- A3: opis chipu w `library/skills/dashboard/descriptions.json`; fixtures i testy kontraktu; `library/skills/dashboard/SKILL.md` (kontrakt rendererowy).
- B1: przy „GO plan” sesja pyta o katalog: „własny worktree” (domyślnie, zalecane) albo „główny checkout”; wybór zapisany w planie linią `Katalog:`; wznowienie ją czyta.
- B2: poprawka reguły we WSZYSTKICH miejscach z „Raportu spójności” (rdzeń instrukcji biblioteki i `feature` jako dwa źródła, potem pochodne), testy treści.

Poza zakresem: zmiana Skippstera (zgłaszamy skutek); odczyt linii `Katalog:` przez `skillsync status`/`dashboard` (Q7); zmiana trybu karty `dispatch` (zostaje worktree z `ASSIGN`);
wydanie, wersja, tag (Dyspozytor, Barciś); nazwy klientów, ludzi i maszyn w rdzeniu (jest publiczny).

## Kryteria akceptacji

| # | Kryterium |
|---|---|
| K1 | `dashboard show <projekt> --json` i `GET /api/status` mają `project.worktree`: obiekt `{kind, name, path, mainPath}` albo `null`. `kind` = `main` (`.git` jest katalogiem; albo `.git` jest plikiem `gitdir:` do katalogu BEZ pliku `commondir`: submoduł, `--separate-git-dir`) albo `worktree` (`.git` plik, a wskazany katalog ma `commondir`). `null`: brak `.git`, katalog podrzędny repozytorium, a także każdy błąd odczytu, w tym uszkodzony powiązany worktree (brak katalogu z `gitdir:` albo nieczytelny `commondir`; wtedy NIE `main`). |
| K2 | Rozpoznanie nie uruchamia procesu `git` (jak `read_git`), czyta wyłącznie `.git` (plik albo katalog) i, dla powiązanego worktree, plik `commondir` w katalogu wskazanym przez `gitdir:`; odczyty z limitem rozmiaru, bez podążania dalej. Jedno miejsce odczytu `gitdir`/`commondir` wspólne z `read_git` (jego wynik bez zmian). Istnieją dwa inne rozpoznania oparte na gicie (`retro_scan.linked_worktree`, `layers.main_worktree`); spec ich nie zmienia i nazywa różnicę: `layers.main_worktree` zwraca wspólny katalog także wtedy, gdy nie nazywa się `.git`, a K3 daje wtedy `null`. |
| K3 | `name` = nazwa katalogu projektu (`root.name`); `path` = `posix(root)` (postać podana serwerowi, bez rozwiązywania dowiązań); `mainPath` = rodzic wspólnego katalogu git dla `worktree`, gdy ten nazywa się `.git`, i jest w postaci zapisanej przez git w `gitdir:`/`commondir` (rozwiązanej), więc przy dowiązaniu (macOS `/var` → `/private/var`) `path` i `mainPath` mogą mieć różne prefiksy: kontrakt to mówi; dla `main` i dla wspólnego katalogu o innej nazwie `null`. |
| K4 | Nagłówek renderera ma chip `worktree: <nazwa>` (dla `worktree`) albo `główny checkout` (dla `main`), po chipie gałęzi, a przed znacznikiem „N niewypchniętych” (gdy gałęzi brak: sam). Brak chipu dla `null` i dla serwera bez pola (stary rdzeń). Chip jest `span` (nie przycisk). |
| K5 | Dymek i opis: znaczenie chipu z `descriptions.json` (klucz `workdir_chip`, przez `desc()`/`data-desc`), pełna `path` (dla worktree także `mainPath`) jako `data-desc-extra`; teksty przez `h()`/`textContent`, zero `innerHTML`, zero stylu inline, CSP bez zmian; oba motywy, bez nowych tokenów (klasa `chip` istniejąca), wielokropek na wewnętrznym `span` nazwy (nie na elemencie z `[data-tip]::after`), bez poziomego przewijania w wąskiej kolumnie. |
| K6 | Renderer, `app.js`, `app.css`, `index.html` nadal bez `copyText`, `clipboard`, `navigator`, przycisków `Kopiuj`: `test_no_copy_buttons_anywhere` i testy statyczne przechodzą bez zmian (kopiowanie: Q4). |
| K7 | Fixtures: wszystkie `expected.json` bez repozytorium mają `project.worktree: null`; `cp-ready` (repozytorium zakładane w teście) ma `kind: main` w `expected.json` i `expected-after.json`; przypadek `worktree` powstaje W TEŚCIE (`git worktree add` w katalogu tymczasowym; `input/` nie może zawierać `.git`), po normalizacji `path` → `<root>`, `mainPath` → `<main>`, `name` = `demo`; `docs/dashboard-contract.md` („Normalizacja w fixtures”) to opisuje. Opisy i testy opisów przechodzą. |
| K8 | Przy „GO plan” bez linii `Katalog:` w planie i bez trybu karty sesja zadaje jedno pytanie z dwiema opcjami (domyślnie „własny worktree”) i zapisuje `Katalog: worktree <nazwa>` albo `Katalog: główny` w nagłówku planu-szkicu przed commitem „GO plan”. Brak odpowiedzi, odmowa albo „anuluj” = brak „GO plan” (nic nie powstaje, spec zostaje `draft`). Wznowienie czyta linię; brak linii w istniejącym planie = zachowanie dotychczasowe (worktree). |
| K9 | Wejście do `główny` (decyzje Q1, Q2 z 18.11): gałąź robocza to **bieżąca gałąź głównego checkoutu, jakakolwiek**, także `delivery.base`; pytanie o katalog pokazuje jej nazwę, a przy gałęzi bazowej ostrzega, że commity i `CP` pójdą na bazę. HEAD odłączony = STOP i propozycja worktree (brak gałęzi, brak `CP`). Zmiany w śledzonych plikach (poza generowanymi przez `link`) = pytanie z listą jako baseline i domyślną propozycją worktree. Tryb `pr` z gałęzią bieżącą = `delivery.base`: „główny” nie jest przyjmowany (propozycja worktree albo przejścia na inną gałąź; Q11). Tryb `direct` na bazie: bez pytania o `Oddanie:` (Q12). Testy treści skilla pilnują tych warunków dosłownie. |
| K10 | Reguła stoi spójnie we wszystkich miejscach z „Raportu spójności”; `instructions/_base.md` ma ≤ 8192 B (pomiar `wc -c` przed i po w raporcie planu); test treści sprawdza `Katalog:` oraz oba warianty w plikach reguły (nie sam napis), a brzmienia przypięte przez `test_skillsync_feature_demo.py` (k10-*) i `test_skillsync_delivery_mode.py` odpowiadają nowemu tekstowi. |
| K11 | `docs/dashboard-contract.md` ma sekcję `project.worktree` (typ, rozpoznanie, `null`, zgodność wsteczna, normalizacja), `library/skills/dashboard/SKILL.md` opis chipu bez nowych zależności, `CHANGELOG.md` wpis 1.10.0 (EN, Added/Changed, Breaking: None), `docs/upgrading.md` sekcja 1.9.1 → 1.10.0, `library/CHANGELOG.md` i wersja warstwy `library/layer.yaml` zgodnie z regułą wydań. |
| K12 | Testy punktowe PASS, bez nowych FAIL w pełnym biegu (po LEASE); `skillsync leakcheck --target core` i gitleaks osobno. |

## Decyzje i alternatywy

**A. Rozpoznanie przez pliki, nie przez `git rev-parse`.** Dyspozytor wskazał porównanie `--git-dir` z `--git-common-dir`. Ta sama informacja leży w plikach: w powiązanym worktree `.git` jest plikiem `gitdir: <repo>/.git/worktrees/<n>`, a ten katalog ma `commondir`
(potwierdzone wobec `read_git`, submodułu, `--separate-git-dir`, bare i względnych `gitdir:` w przeglądzie). Serwer statusu odpytuje stan co kilka sekund i dziś nie uruchamia gita do odczytu gałęzi; proces `git` przy każdym odczycie to koszt, zależność od `PATH` i powierzchnia zmiennych `GIT_*`.
Alternatywa: `git rev-parse` raz na start serwera: odrzucona, bo katalog może się zmienić pod serwerem (`git worktree move`).

**A. Chip obok chipu gałęzi, nie w stopce.** Decyzja Barcisia. Stopka zostaje (pełna ścieżka do zrzutów ekranu i logów).

**A. Pole w `project`, nie w `git`.** `git.*` opisuje gałąź i origin; `project` opisuje katalog, który serwer obsługuje.

**A. Bez kopiowania w tej paczce.** Renderer nie ma schowka celowo: `actions.copyText` usunięto jako zmianę łamiącą (CHANGELOG 1.x, „Copy … buttons are removed entirely”), `SKILL.md` dashboardu mówi „brak `actions.copyText` i schowka”, a nowy callback to „zmiana kontraktu ze Skippsterem (zgłoszenie przed zapisem)”.
Test `test_no_copy_buttons_anywhere` i testy statyczne (zakaz `navigator`) tego pilnują. Cofnięcie to osobna decyzja (Q4), nie dodatek do chipu.

**B. Pytanie dopiero przy „GO plan”.** Decyzja Barcisia. Akt I (spec, pętla `spec`) nie tworzy commita, więc nie potrzebuje katalogu.

**B. Wybór jako linia planu, nie klucz manifestu.** Katalog dotyczy jednego przebiegu, nie projektu; linia `Katalog:` w nagłówku planu (definicje linii: `plan-details.md`) jest jawna i przeżywa wznowienie. Alternatywa: `workflow.workdir` w manifeście: odrzucona na tę wersję (nowy klucz, `doctor`, `route`).

**B. Linia niesie nazwę, nie ścieżkę bezwzględną.** Plan trafia do commita i PR-a; ścieżka bezwzględna wynosi nazwę katalogu domowego do repozytorium i nie jest przenośna między maszynami. Zapis: `Katalog: worktree <nazwa-katalogu>`; ścieżkę sesja odnajduje przez `git worktree list` (po nazwie i gałęzi). Odstępstwo od zlecenia (`Katalog: worktree <ścieżka>`), do potwierdzenia: Q10.

## Kontrakty

### A. `project.worktree`

```json
"project": { "name": "demo", "path": "/work/app-wt/F1",
             "worktree": { "kind": "worktree", "name": "F1", "path": "/work/app-wt/F1", "mainPath": "/work/app" } }
```

| Pole | Typ | Opis |
|---|---|---|
| `worktree` | obiekt lub `null` | `null`: brak `.git`, katalog podrzędny repozytorium, błąd odczytu (K1) |
| `worktree.kind` | `"main"` \| `"worktree"` | wyliczenie; nowe wartości addytywnie |
| `worktree.name` | string | nazwa katalogu projektu |
| `worktree.path` | string | `posix(root)` |
| `worktree.mainPath` | string lub `null` | K3 |

Rozpoznanie: (1) `root/.git` katalog → `main`; (2) plik → treść `gitdir: <ścieżka>` (względna liczona od `root`, parser jak w `read_git`); (3) katalog z `commondir` → `worktree`; (4) katalog bez `commondir` → `main`; (5) każdy błąd odczytu → `null`.
Stary serwer bez pola: renderer nie rysuje chipu. „Zasady zmian”: pole opcjonalne dla konsumenta, w fixtures zawsze obecne.

### A. Renderer i Skippster

Chip: `h("span", {class: "m"}, icon("i-folder"), h("span", {class: "chip s-planned workdir"}, h("span", {class: "workdir-name"}, tekst)))`; nowa ikona `i-folder` w `ICONS` (ten sam format tablicowy, Q9); CSS: `max-width`, `overflow: hidden`, `text-overflow: ellipsis` na wewnętrznym `span` nazwy.
Tekst: `worktree: F1`, `główny checkout`. Opis (`descriptions.json`, klucz `workdir_chip`): co oznacza chip i czym różni się worktree od głównego checkoutu; `data-desc-extra` = ścieżki. Render nie trzyma stanu chipu (brak kopiowania, więc brak fokusu do odtwarzania przy `update()` co 5 s).

Skippster (renderer współdzielony przez kopię plików, `ASSETS.json`): nie zmieniamy. Skutek, do zgłoszenia przy jego następnej synchronizacji: (a) chip pojawi się w panelu bez zmian po stronie wtyczki, jako opis bez kliknięcia; (b) nowy token motywu nie powstaje; (c) parser wtyczki ma przepuszczać nieznane klucze (**niezweryfikowane po naszej stronie**: wtyczka sprawdza to własnymi testami przy synchronizacji); (d) `library/skills/dashboard/SKILL.md` wymaga zgłoszenia przed zapisem tylko przy nowym callbacku: tu go nie ma.

### B. Linia `Katalog:` i pytanie przy „GO plan”

Nagłówek planu (`plan-details.md`, obok `Przebieg:`, `Oddanie:`, `Ceremonia:`, `Spec:`, `Spec-sha:`): `Katalog: worktree <nazwa-katalogu>` albo `Katalog: główny`. Brak linii w planie sprzed zmiany = `worktree` jak dotąd. Parser nagłówków planu czyta linie generycznie (`plan_fields`), więc kod nie wymaga zmiany (Q7).

Pytanie przy „GO plan” (host z oknem wyboru: okno; bez niego jedno pytanie tekstowe): „Gdzie pracować nad tym przebiegiem?”: **własny worktree** (domyślnie, zalecane: izolacja plików, równoległe sesje, bez ryzyka dla bieżącej gałęzi) albo **główny checkout** (jeden katalog, bez drugiej kopii; pytanie podaje nazwę bieżącej gałęzi checkoutu i, gdy to `delivery.base`, ostrzega, że commity i `CP` pójdą na bazę; w `pr` przy gałęzi bazowej oferuje tylko worktree, Q11).
Pytanie nie pada: (1) gdy plan ma już `Katalog:` (wznowienie, ponowne GO plan po zmianie speca); (2) w trybie karty (`ASSIGN`/`dispatch:`): katalog wynika ze zlecenia, domyślnie worktree paczki; (3) przy wejściu z klucza zgłoszenia (`pr-after-approval`): „rób PR” używa bieżącej gałęzi i nie zakłada worktree (bez zmian); (4) przy „GO implement” bez „GO plan” pytanie pada właśnie tam (pierwsze słowo zgody, które zakłada katalog).
Brak odpowiedzi, odmowa albo anulowanie = brak „GO plan”.

Procedura po wyborze: `worktree`: jak dziś (worktree i gałąź robocza, kopia nieśledzonego szkicu speca, manifest i `skillsync link` według `feature/references/worker-mode.md`); `główny`: warunki z K9 (bieżąca gałąź checkoutu, bez `git switch`), commit „GO plan” na tej gałęzi w głównym checkoucie, `git add` wyłącznie z jawnymi ścieżkami; plan dostaje linię `Katalog:` przed commitem.

Skutki dla reguł (rozstrzygnięcia w Q): `CP` bez zmian (push bieżącej gałęzi roboczej); „Push na bazę: fast-forward `robocza:base`” działa także z głównego, a „główny checkout aktualizowany `--ff-only` tylko gdy czysty i na `base`” działa, gdy gałąź bieżąca to baza (wtedy `CP` pushuje bazę wprost, bez `robocza:base`), a przy innej gałęzi jest nieaktywne;
„kopia przebiegu poza worktree” i „sprzątanie worktree po `CP`” nie dotyczą `główny`.

## Dane

Nowe pole `project.worktree` (kontrakt wyżej), nowa linia nagłówka planu `Katalog:` (tekst); brak nowych plików stanu, kluczy manifestu ani zmian schematu (`schema: 1` bez zmian).

## Migracja

Plany bez linii `Katalog:` działają jak dotąd (worktree). Serwer 1.10.0 + stary renderer: pole zignorowane. Renderer 1.10.0 + stary serwer (Skippster z nowym rendererem i starszym rdzeniem): brak pola = brak chipu. `docs/upgrading.md` ma sekcję 1.9.1 → 1.10.0: nowe pole, nowa linia planu, pytanie przy „GO plan”, brak kroków obowiązkowych.

## Bezpieczeństwo

Odczyt `.git` i `commondir` jak `read_git` (limit rozmiaru, brak procesów, ścieżki nie wychodzą do odpowiedzi poza `path`/`mainPath`; ścieżki lokalne są już w `project.path`, gniazdo odczytu bez logowania pokazuje je dziś). Chip: tylko `textContent`, CSP bez zmian, brak schowka i nowych zależności. Linia `Katalog:` bez ścieżek bezwzględnych (publiczny plan i PR). `Katalog: główny` nie zmienia reguł zgód: GO plan i CP pochodzą od użytkownika. Praca na gałęzi bazowej (dopuszczona decyzją użytkownika) oznacza, że `CP` pushuje bazę zgodnie z regułą „nigdy na `delivery.base` … chyba że robocza nią jest”; dlatego pytanie o katalog pokazuje nazwę gałęzi i ostrzega przy bazie.

## Odwrócenie zmian

Serwer i renderer: zwykły revert commitów (pole addytywne, brak migracji danych). Procedura B: revert reguł; plany z `Katalog: główny` zostają czytelne (linia ignorowana, wznowienie traktuje je jak brak). `główny`: żadnych zmian gałęzi (praca na bieżącej); commit „GO plan” jest lokalny i odwracalny `git reset` tylko za zgodą użytkownika; po oddaniu na bazę w `direct` z gałęzi roboczej sesja proponuje `git switch <baza>`, nie robi tego sama.

## Strategia testów

Kontrakt: testy `tests/test_skillsync_dashboard*.py` (K1–K3 z repozytoriami tworzonymi w katalogu tymczasowym: katalog, plik `gitdir`, `commondir`, submoduł, uszkodzony worktree, dowiązanie), fixtures K7. Renderer: testy renderera i dostępności (chip, `null`, brak pola, obcięcie nazwy, `test_no_copy_buttons_anywhere` bez zmian). Procedura: testy treści (`k10-*`, `delivery_mode`, nowy test `Katalog:`), `library_smoke` (budżet). Ścieżki porażek `główny`: brak odpowiedzi na pytanie, brudne drzewo, cudza gałąź, zniknięcie worktree z linii planu. Pełny bieg i macierz tylko po LEASE.

## Raport spójności

Miejsca reguły „od GO plan osobny worktree i gałąź robocza” do zmiany (grep, 18.11): `library/instructions/_base.md` (l.61, 69, 72), `library/skills/feature/SKILL.md` (l.72–75, 283), `feature/references/plan-large.md` (l.59), `plan-details.md` (linia `Katalog:`), `spec.md` (l.66), `pr-run/SKILL.md` (krok 3 i 8), `pr-after-approval.md` (l.15), `retro-quality.md` (l.148, 201, 235, 284–303, 366), `worker-mode.md` (zdanie: karta zawsze w worktree), `library/skills/skillsync/SKILL.md`, `docs/workflow.md` (l.103, 125–126), `docs/workflow-details.md` (l.719), `library/skills/dashboard/SKILL.md` (opis chipu), `CHANGELOG.md`, `library/CHANGELOG.md`, `library/layer.yaml`, `docs/upgrading.md`; testy: `test_skillsync_feature_demo.py` (k10-*, l.86–96), `test_skillsync_delivery_mode.py` (l.273–281).
Budżet `_base.md`: 8187 B przy limicie 8192 (`wc -c`, 18.11; zapas 5 B). Dziś „osobny worktree i gałąź robocza;” = 35 B; propozycja „katalog roboczy (pytanie; `Katalog:` w planie) i gałąź robocza;” = 66 B, czyli +31 B, plus zmiany w l.69 („z worktree”) i l.72 („kopia przebiegu poza worktree; worktree sprzątasz po CP”) do policzenia w planie. Realne miejsce: zdanie „Push na bazę: … (baza rozbieżna = STOP …) ; w `pr` nigdy bazy.” (212 B) przenosimy do `feature/SKILL.md` (§CP), zostawiając w rdzeniu odesłanie ok. 60 B (zysk ≈150 B). Bez zagęszczania cudzych zdań; wynik pomiaru w planie przed „GO implement”.

## Ryzyka

| # | Ryzyko | Postępowanie | Rezydualne |
|---|---|---|---|
| R1 | `główny` + brudne drzewo: zmiany użytkownika mieszają się z commitami przebiegu | K9: pytanie z listą jako baseline, domyślnie worktree; `git add` tylko jawne ścieżki | niskie |
| R2 | `główny` na gałęzi bazowej lub cudzej (świadoma decyzja użytkownika): commity „GO plan” i `CP` trafiają na tę gałąź, w `pr` brak PR-a z bazy do bazy | K9: nazwa gałęzi i ostrzeżenie w pytaniu; `pr` + baza = odmowa „główny” (Q11); `direct` + baza bez `Oddanie:` (Q12); `git add` tylko jawne ścieżki | średnie (konsekwencja wyboru) |
| R3 | Druga sesja lub IDE w tym samym głównym katalogu: commity przebiegu trafiają na gałąź, którą ta sesja ma wybraną (lekcja `zmiana-head-w-trakcie-biegu-uniewaznia-bieg`) | pytanie ostrzega; przy żywej dzierżawie innego przebiegu w tym katalogu (`.skillsync/claims`) STOP i propozycja worktree | średnie (nie do wykrycia dla edytora bez dzierżawy) |
| R4 | Budżet 8192 B w `_base.md` | „Raport spójności”, pomiar w planie | niskie |
| R5 | Skippster pokazuje chip bez kliknięcia; parser wtyczki a nieznane klucze niezweryfikowane | zgłoszone Dyspozytorowi, sprawdza wtyczka przy synchronizacji | niskie |
| R6 | Reguła w wielu miejscach rozjedzie się | test treści z oboma wariantami we wszystkich plikach „Raportu spójności” | niskie |
| R7 | Wznowienie z `Katalog: worktree <nazwa>`, którego katalogu nie ma | STOP i pytanie (odtworzyć z gałęzi?), bez cichego przełączenia na główny | niskie |
| R8 | `TAK P<n>` w `główny` robi commit po `--final` i zmienia HEAD względem `sha` zgody (Q3) | istniejąca reguła „inny `sha` = pytanie” przy CP; opisać w `retro-quality.md` | niskie |

## Decyzje użytkownika z 18.11 (dawne pytania Q)

Odpowiedzi użytkownika przekazane przez Dyspozytora (delegacja z 16.11); pytania zamknięte, treść przeniesiona do kryteriów i kontraktów.

- Q1: „główny checkout” = bieżąca gałąź głównego checkoutu, jakakolwiek, także baza (odrzucona rekomendacja „gałąź tego przebiegu”). Skutki: K9, R2, Q11, Q12.
- Q2: brudne drzewo = pytanie z listą jako baseline (rekomendacja przyjęta; domyślnie worktree; pliki generowane przez `link` nie liczą się; nieśledzone obce pliki: baseline bez pytania).
- Q3: `TAK P<n>`: bez zmiany kodu; opis w `retro-quality.md` i `SKILL.md`: w `direct` z `Katalog: główny` działa, w `pr` jak dziś; `TAK` po `--final` zmienia HEAD i unieważnia `sha` zgody CP (R8).
- Q4: bez kopiowania w 1.10.0 (K6).
- Q5: `pr-run` krok 3 honoruje `Katalog:`: przy `główny` używa głównego checkoutu i jego bieżącej gałęzi (nie zakłada worktree ani gałęzi), pod warunkiem z Q11 (gałąź ≠ `delivery.base`); przy `worktree` jak dziś; krok 8 pomija `główny`. „Rób PR” bez zmian. `CP` bez zmian.
- Q8: `mainPath` w kontrakcie: tak. Q9: ikona `i-folder`: tak. Q10: nazwa katalogu zamiast ścieżki w linii `Katalog:`: tak.

## Założenia (nieblokujące; do korekty użytkownika)

- Q6 [ZAŁOŻENIE]: Karty pod Dyspozytorem pytają o katalog? — przyjęte: nie pytają; o worktree decyduje `ASSIGN` (domyślnie worktree paczki), a zlecenie bez wskazania daje worktree.
- Q7 [ZAŁOŻENIE]: Czy `skillsync status`/`dashboard` czytają linię `Katalog:`? — przyjęte: nie w 1.10.0; linię czyta sesja przy wznowieniu; parser nagłówków planu przepuszcza nieznane linie (`plan_fields`).
- Q11 [ZAŁOŻENIE]: `Katalog: główny` w trybie `pr`, gdy bieżąca gałąź to `delivery.base` (PR z bazy do bazy nie istnieje)? — przyjęte: sesja nie przyjmuje „główny”, mówi dlaczego i proponuje worktree albo przejście użytkownika na inną gałąź w głównym checkoucie; pytanie o katalog w `pr` przy gałęzi bazowej od razu ma tylko opcję worktree.
- Q12 [ZAŁOŻENIE]: Pierwsze `CP` w `direct` na gałęzi bazowej: czy pytanie o bazę i linia `Oddanie:` mają sens? — przyjęte: nie (robocza = baza, nie ma dokąd oddawać): bez pytania i bez linii `Oddanie:`; `CP` pushuje bieżącą gałąź, którą jest baza, zgodnie z regułą („nigdy na `delivery.base` … chyba że robocza nią jest”). Przy `główny` na innej gałęzi niż baza pytanie i `Oddanie:` jak dotąd.
- Q13 [ZAŁOŻENIE]: `główny` z odłączonym HEAD? — przyjęte: STOP z propozycją worktree (bez gałęzi nie ma `CP` ani PR-a).

## Plan wykonania (zarys; pełny plan po „GO plan”)

| Id | Krok | Pliki |
|---|---|---|
| F1 | Kontrakt i serwer: wspólny pomocnik `gitdir`/`commondir`, `project.worktree`, fixtures (w tym przypadek budowany w teście), testy, sekcja w `dashboard-contract.md` | `skillsync_workflow/dashboard.py`, `tests/fixtures/dashboard/**`, `tests/test_skillsync_dashboard*.py`, `docs/dashboard-contract.md` |
| F2 | Renderer [UI]: chip, opis, ikona, CSS, `descriptions.json`, testy renderera i dostępności; opcjonalnie statyczny podgląd chipu w obu motywach | `library/skills/dashboard/assets/*`, `descriptions.json`, `library/skills/dashboard/SKILL.md`, `tests/test_skillsync_dashboard_renderer.py`, `…a11y.py` |
| F3 | Procedura B: reguła w miejscach z „Raportu spójności”, budżet `_base.md`, testy treści | `library/**`, `docs/workflow*.md`, `tests/test_skillsync_feature_demo.py`, `…delivery_mode.py` |
| F4 | Dokumentacja i wiedza: CHANGELOG, `upgrading.md`, `library/CHANGELOG.md`, `layer.yaml`, lekcje, zamknięcie przebiegu | `CHANGELOG.md`, `docs/**`, `library/**` |

Testy: punktowe moduły dashboard i `feature_demo`, `delivery_mode`, `library_smoke`; pełny bieg i macierz przed pushem tylko po LEASE GRANT.
