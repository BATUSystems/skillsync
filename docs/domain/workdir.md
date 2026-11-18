---
title: Katalog roboczy przebiegu (worktree, główny checkout, wybór przy GO plan, rozpoznanie na stronie statusu)
tags: [skillsync, dashboard, workflow, worktree, git, workdir, terminology]
paths: ['skillsync_workflow/dashboard.py', 'library/skills/feature/references/workdir.md', 'library/skills/dashboard/assets/dashboard-render.js', 'library/instructions/_base.md', 'tests/test_skillsync_dashboard_workdir.py', 'tests/test_skillsync_workdir_rule.py']
source: "odczyt skillsync_workflow/dashboard.py (`git_locations`, `read_worktree`, `read_git`, `build_status`, pole `project`); docs/dashboard-contract.md, sekcja „project.worktree”; docs/specs/2026-11-18-core-workdir.md (K1, K2, K9, decyzje Q1–Q13 z 18.11); library/skills/feature/references/workdir.md"
verified: 2026-11-18
---
Reguła: każdy przebieg `feature` pracuje w katalogu roboczym, który wybiera się przy „GO plan” i zapisuje w nagłówku planu linią `Katalog:`.
Wartość linii to `worktree <nazwa-katalogu>` (własny worktree, domyślnie) albo `główny` (główny checkout, bieżąca gałąź).
Plan bez tej linii znaczy `worktree`, jak przed wersją 1.10.0. Warunki i ścieżki porażek: `library/skills/feature/references/workdir.md`.
Strona statusu pokazuje, który katalog obsługuje: pole `project.worktree` (chip w nagłówku). Wybór nie jest zapisany w manifeście.

Definicje:
- **Powiązany worktree** (`kind: worktree`): `.git` jest plikiem `gitdir: <ścieżka>`, a katalog wskazany przez `gitdir:` ma plik `commondir`.
  Źródło: spec K1; `skillsync_workflow/dashboard.py:git_locations`. Potwierdzono 2026-11-18.
- **Główny checkout** (`kind: main`): `.git` jest katalogiem, albo plikiem `gitdir:` do katalogu BEZ pliku `commondir` (submoduł, `--separate-git-dir`).
  Plik `commondir` wewnątrz katalogu `.git` nie zmienia tego: `.git` będący katalogiem to zawsze główny checkout.
  Źródło: spec K1; `dashboard.py:git_locations` (docstring); potwierdzono 2026-11-18.
- **Katalog roboczy przebiegu**: wartość linii planu `Katalog:` (`worktree <nazwa>` albo `główny`), wybierana przy „GO plan”.
  Linia niesie nazwę katalogu, nie ścieżkę bezwzględną: plan trafia do commita i PR-a. Ścieżkę worktree odnajduje się przez `git worktree list` (po nazwie i gałęzi).
  Źródło: spec, decyzja B i Q10 (18.11); `library/skills/feature/references/workdir.md`.
- **Nie oznacza**: `project.worktree` (opis katalogu obsługiwanego przez serwer statusu) nie jest tym samym co `layers.main_worktree` i `retro_scan.linked_worktree`.
  Tamte dwa zwracają wspólny katalog git także wtedy, gdy nie nazywa się `.git`; `project.worktree.mainPath` wtedy daje `null`.
  Źródło: spec K2 (18.11).

Rozpoznanie `project.worktree` (serwer statusu, bez procesu `git`):
- wynik `null`: brak `.git`; katalog podrzędny repozytorium (`.git` wyżej nie jest szukany); każdy błąd odczytu, w tym uszkodzony worktree (brak katalogu z `gitdir:` albo nieczytelny `commondir`). Uszkodzony worktree nie jest `main`.
- `commondir` liczy się tylko przy `.git` będącym plikiem `gitdir:`. `commondir` wskazujący nieistniejący katalog daje nadal `worktree`; nic poza odczytem plików nie jest sprawdzane.
- `name` = nazwa katalogu projektu; `path` = ścieżka podana serwerowi (bez rozwiązywania dowiązań); `mainPath` = rodzic wspólnego katalogu git, gdy ten nazywa się `.git` (ścieżka rozwiązana, więc przy dowiązaniu, np. macOS `/var` → `/private/var`, prefiksy mogą się różnić).
- odczyt `gitdir`/`commondir` jest wspólny z polami `git.*` (`read_git` daje ten sam wynik co przed zmianą).
Źródło: `dashboard.py:read_worktree`; `docs/dashboard-contract.md`, sekcja „project.worktree”; testy `tests/test_skillsync_dashboard_workdir.py`.

Wybór `główny` (K9, decyzje Q1, Q2, Q11–Q13 z 18.11):
- Gałąź robocza to bieżąca gałąź głównego checkoutu, jakakolwiek, także `delivery.base`. Nie robi się `git switch`.
- HEAD odłączony: STOP z propozycją worktree (brak gałęzi, brak `CP`, brak PR-a).
- Śledzone zmiany poza plikami generowanymi przez `link`: pytanie „kontynuować z tymi zmianami?” z listą jako baseline; domyślnie worktree. Nieśledzone obce pliki: baseline bez pytania.
- Żywa dzierżawa innego przebiegu w tym katalogu (`.skillsync/claims`): STOP z propozycją worktree.
- Tryb `pr`, gałąź równa `delivery.base`: „główny” nie jest przyjmowany (PR z bazy do bazy nie istnieje).
- Tryb `direct` na gałęzi bazowej: bez pytania o `Oddanie:`; `CP` pushuje bieżącą gałąź, którą jest baza (reguła „nigdy na bazę, chyba że robocza nią jest”).
- Pytanie o katalog pokazuje nazwę bieżącej gałęzi i przy bazie ostrzega, że commity i `CP` pójdą na bazę.

Ścieżki porażek:
- Brak odpowiedzi, odmowa lub anulowanie: brak „GO plan”, nic nie powstaje, bez linii `Katalog:`.
- Wznowienie z `Katalog: główny` sprawdza warunki ponownie przed pierwszym commitem: gałąź mogła się zmienić między sesjami; zmiana gałęzi względem poprzednich commitów przebiegu = STOP z pytaniem, które nazywa obie gałęzie.
- Wznowienie z `Katalog: worktree <nazwa>`, którego katalogu nie ma (R7): STOP i pytanie (odtworzyć z gałęzi?), bez cichego przełączenia na główny checkout.
- Plan bez linii `Katalog:` w istniejącym przebiegu = worktree jak dotąd.
- Pytanie nie pada w trybie karty (`ASSIGN`/`dispatch:`; katalog wynika ze zlecenia) ani przy wejściu z klucza zgłoszenia (`pr-after-approval`: „rób PR” używa bieżącej gałęzi).

Gdzie w kodzie: `skillsync_workflow/dashboard.py:git_locations`, `:read_worktree`, `:read_git`; pole `project` w `build_status` (`'worktree': read_worktree(root)`).
Front (chip): `library/skills/dashboard/assets/dashboard-render.js` (`pushWorkdir`, klucz opisu `workdir_chip`).
Instrukcja: `library/instructions/_base.md` (jedna reguła i odesłanie do `workdir.md`).

Nie oznacza tego samego co: „worktree” w `dispatch` (karta zawsze pracuje w worktree paczki ze zlecenia, `worker-mode.md`) ani ścieżka `Katalog:` w `skillsync status` (linii nie czyta ani `status`, ani `dashboard` w 1.10.0).
