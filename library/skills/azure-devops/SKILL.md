---
name: azure-devops
description: Neutralny przewodnik po Azure DevOps CLI i REST - PAT, az devops configure, tworzenie pull requestów, wymagani reviewerzy, auto-complete, praca z pipeline'ami i typowe błędy.
---

## Kiedy używać tego skill-a
Używaj, gdy zadanie dotyczy:
- konfiguracji `az devops` (organizacja, projekt, PAT),
- tworzenia i aktualizacji pull requestów z CLI, dodawania reviewerów (required/optional), auto-complete,
- uruchamiania i diagnozowania pipeline'ów (`az pipelines`),
- błędów uwierzytelniania i polityk PR.

Wartości konkretnego projektu (organizacja, projekt, repo, reviewerzy, branche, pipeline'y) są w skillu
projektu lub w instrukcjach projektu (np. skill `<projekt>`). Przegląd i komentowanie PR: skill `azure-devops-pr-review`.
Boards i work items: skill `azure-devops-boards`.

# Azure DevOps CLI - przewodnik

## Wymagania i uwierzytelnianie

```bash
az extension add --name azure-devops      # jednorazowo
az --version                              # sprawdź, że rozszerzenie jest zainstalowane
```

PAT (Personal Access Token) z uprawnieniami Code (Read & Write), Build (Read & Execute), ewentualnie Work Items:

```bash
# bash / zsh
export AZURE_DEVOPS_EXT_PAT='<PAT>'
```
```powershell
# PowerShell
$env:AZURE_DEVOPS_EXT_PAT = "<PAT>"
```

- Zmienna `AZURE_DEVOPS_EXT_PAT` ma pierwszeństwo przed `az devops login`; ustaw ją w tej samej sesji, z której
  wywołujesz polecenia (agenty i skrypty nie dziedziczą zmiennych z innego terminala).
- Nie wklejaj PAT do plików w repo, logów ani komunikatów; do skryptów przekazuj go z magazynu sekretów.
- Alternatywa: `az devops login --organization https://dev.azure.com/<ORG>` (zapyta o PAT).

## Konfiguracja domyślnych wartości

```bash
az devops configure --defaults organization=https://dev.azure.com/<ORG> project=<PROJECT>
az devops configure --list
```

Bez defaults dodawaj `--org https://dev.azure.com/<ORG> --project <PROJECT>` do każdego polecenia.
W repozytorium z remote Azure Repos `az repos` potrafi wykryć kontekst przez `--detect true`.

## Pull requesty

```bash
# Utworzenie PR
az repos pr create \
  --repository <REPO> \
  --source-branch <SOURCE_BRANCH> \
  --target-branch <TARGET_BRANCH> \
  --title "<KEY>-123: Krótki opis zmiany" \
  --description "Szczegółowy opis" \
  --draft false

# Reviewer wymagany (--required) lub opcjonalny (bez flagi)
az repos pr reviewer add --id <PR_ID> --reviewers "<EMAIL_ALBO_ID_ALBO_GRUPA>" --required

# Grupa jako reviewer: nazwa w nawiasach kwadratowych z odwrotnym ukośnikiem
az repos pr reviewer add --id <PR_ID> --reviewers "[<PROJECT>]\<GRUPA>" --required

# Podgląd PR i polityk
az repos pr show --id <PR_ID>
az repos pr policies list --id <PR_ID>
az repos pr reviewer list --id <PR_ID>

# Auto-complete (merge po spełnieniu polityk)
az repos pr update --id <PR_ID> --auto-complete true --merge-strategy squash   # lub rebase, noFastForward
```

Wskazówki:
- Dodawaj reviewerów od razu po utworzeniu PR i sprawdź, że są oznaczeni jako Required, nie Optional.
- Reviewer może być wskazany e-mailem, GUID-em użytkownika/grupy albo nazwą grupy; GUID jest najpewniejszy.
- `--merge-strategy` przyjmuje `squash`, `rebase`, `rebaseMerge`, `noFastForward` (zależnie od wersji rozszerzenia);
  repo może wymuszać strategię polityką brancha.
- Ręczne dokończenie: `az repos pr update --id <PR_ID> --status completed` (gdy polityki są spełnione).
  Nie używaj nieistniejących flag; w razie wątpliwości `az repos pr update --help`.

## Pipeline'y

```bash
# Lista i szczegóły definicji
az pipelines list --output table
az pipelines show --name "<PIPELINE_NAME>"

# Uruchomienie na branchu
az pipelines run --name "<PIPELINE_NAME>" --branch <BRANCH>
az pipelines run --id <PIPELINE_ID> --branch <BRANCH> --variables KEY=value

# Przebiegi (runs/builds)
az pipelines runs list --top 10 --branch <BRANCH> --output table
az pipelines runs show --id <RUN_ID>
az pipelines build list --top 10 --branch <BRANCH>
az pipelines build show --id <BUILD_ID>

# Logi
az pipelines build log list --id <BUILD_ID>
az pipelines build log show --id <BUILD_ID> --log-id <LOG_ID>
```

Wskazówki:
- Przy awarii zacznij od `runs show` (status, `result`, branch, commit), potem logi nieudanego zadania.
- Pipeline PR uruchamia się polityką brancha; ręczne `run` na branchu PR nie zastępuje polityki.
- Zmienne sekretne w variable group nie są widoczne w logach; wartość sprawdzisz tylko w portalu (uprawnienia).

## REST (gdy CLI nie wystarcza)

```bash
curl -u :"$AZURE_DEVOPS_EXT_PAT" \
  "https://dev.azure.com/<ORG>/<PROJECT>/_apis/git/repositories/<REPO>/pullrequests/<PR_ID>?api-version=7.1"
```

- Uwierzytelnianie Basic z pustym użytkownikiem i PAT jako hasłem.
- Zawsze podawaj `api-version`. Portal i REST pozwalają na operacje, których CLI nie ma (np. część polityk).

## Typowe błędy

| Objaw | Przyczyna / rozwiązanie |
|-------|-------------------------|
| "credential store" / prośba o logowanie | Brak `AZURE_DEVOPS_EXT_PAT` w bieżącej sesji; ustaw zmienną przed każdym poleceniem |
| `TF400813` / 401 / 203 | PAT wygasł, ma za wąski zakres albo wskazuje inną organizację |
| `VS30063` | Brak dostępu do organizacji/projektu; poproś administratora o dostęp |
| `Project not found` / `The following project was not found` | Zły `--project` albo brak defaults; sprawdź `az devops configure --list` |
| PR nie robi auto-complete | Trwa build, polityki nie są spełnione albo brakuje wymaganego approvala; sprawdź `az repos pr policies list` |
| Grupa reviewerów nie jest znaleziona | Użyj pełnej nazwy `[<PROJECT>]\<GRUPA>` albo GUID grupy |
| Błąd `AADSTS7000222` w pipeline | Wygasły sekret klienta service connection; wymaga administratora organizacji (zwykłe konta go nie widzą) |
| Polecenia zachowują się inaczej niż w dokumentacji | Zaktualizuj rozszerzenie: `az extension update --name azure-devops` |

## Powiązane Skills

- `azure-devops-pr-review` - przegląd i komentarze PR
- `azure-devops-boards` - work items i boards
- Skill projektu (np. `<projekt>`) - organizacja, repo, reviewerzy, pipeline'y i agenty konkretnego projektu
