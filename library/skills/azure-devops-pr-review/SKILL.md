---
name: azure-devops-pr-review
description: Workflow do code review pull requestów Azure DevOps przez REST API z PAT auth — kiedy używać curl zamiast az CLI, endpointy, kolejność wywołań, co sprawdzać w plikach
user-invokable: true
---

# Azure DevOps PR Code Review

## Kiedy używać

Zawsze gdy użytkownik podaje link do PR na `dev.azure.com` i prosi o code review. Pełny przepływ (worktree, podumysły, podsumowanie, rekomendacja) prowadzi skill [codereview](../codereview/SKILL.md); ten plik jest referencją API.

## Uwierzytelnianie — używaj curl, nie az CLI

w części organizacji `az repos pr` ma problemy (błędy `--project`, konieczność reconfigure, brak tokenu AAD). **Niezawodna metoda to curl z PAT.**

```bash
PAT_B64=$(printf ":%s" "$AZURE_DEVOPS_EXT_PAT" | base64 -w 0)
# Używaj tej zmiennej we wszystkich wywołaniach curl poniżej
```

**WAŻNE:** `base64 -w 0` (bez łamania linii) — bez tego nagłówek Basic Auth jest nieprawidłowy.

`az repos pr show --id <ID>` działa jako fallback do pobrania metadanych, ale tylko po uprzednim `az devops configure -d organization=https://dev.azure.com/<org> project=<projekt>`.

## Workflow krok po kroku

### 1. Wyciągnij metadane PR

```bash
# Dla nieznanego repo — bez repo-id, przez projekt
curl -s -H "Authorization: Basic $PAT_B64" \
  "https://dev.azure.com/<org>/<projekt>/_apis/git/pullrequests/<PR_ID>?api-version=7.1"
```

Z odpowiedzi wyciągnij:
- `repository.id` → `REPO_ID`
- `lastMergeSourceCommit.commitId` → `SRC` (branch z PR)
- `lastMergeTargetCommit.commitId` → `TGT` (target branch, np. develop)
- `title`, `sourceRefName`, `createdBy.displayName`

### 2. Pobierz listę zmienionych plików

```bash
curl -s -H "Authorization: Basic $PAT_B64" \
  "https://dev.azure.com/<org>/<projekt>/_apis/git/repositories/$REPO_ID/pullRequests/$PR_ID/iterations/1/changes?api-version=7.1" \
  | python3 -c "
import sys,json
data=json.load(sys.stdin)
for item in sorted(data.get('changeEntries',[]), key=lambda x: x.get('item',{}).get('path','')):
    print(f'{item[\"changeType\"]:12} {item[\"item\"][\"path\"]}')
print('Total:', len(data.get('changeEntries',[])))
"
```

### 3. Pobierz zawartość pliku (nowa lub stara wersja)

```bash
# Nowa wersja (SRC branch)
curl -s -H "Authorization: Basic $PAT_B64" \
  "https://dev.azure.com/<org>/<projekt>/_apis/git/repositories/$REPO_ID/items?path=<ŚCIEŻKA>&versionType=commit&version=$SRC&api-version=7.1"

# Stara wersja (TGT branch, dla porównania)
curl -s -H "Authorization: Basic $PAT_B64" \
  "https://dev.azure.com/<org>/<projekt>/_apis/git/repositories/$REPO_ID/items?path=<ŚCIEŻKA>&versionType=commit&version=$TGT&api-version=7.1"
```

## Co sprawdzać podczas review

### Bezpieczeństwo (OWASP / sekrety)
- Klucze kryptograficzne, certyfikaty lub pliki z `keys/` w zmianach → zawsze sprawdź czy nie trafiły do repo
- Hasła hardcoded w migracjach — SHA-256 bez salt to nie algorytm hashowania haseł
- Dane uwierzytelniające lub connection stringi z prawdziwymi wartościami (nie placeholderami)
- Pliki `appsettings.json` — sprawdź czy zawierają tylko placeholdery

### Konfiguracja OIDC / auth
- Zmiany w `auth.clientConfiguration.json` lub `appsettings.json` dotyczące OIDC
- Wyłączenie `silent_renew`, `start_checksession` — celowe czy workaround?
- Zmiany redirect URI — czy są spójne między serwerem a klientem

### Jakość kodu
- TODO/FIXME bez śladu rozwiązania
- Zduplikowane mappingi (AutoMapper, itp.)
- Zmiany w `app-routing` — czy ścieżki są spójne między front i back

### Upgrade dependencies
- Bump wersji w `package.json` lub `*.csproj` — czy wszystkie powiązane pakiety zostały zaktualizowane
- `--openssl-legacy-provider` w skryptach npm → sygnał starego Node/OpenSSL, dług techniczny
- Deprecated pakiety w dependencies (np. `@angular/http` w Angular 8+)

## Struktura URL

- Org: `https://dev.azure.com/<org>` (nazwa organizacji z kontekstu projektu / skilla projektowego)
- Projekty: wg organizacji (`<projekt>`)
- API version: `7.1`

## Wskazówki

- Gdy plik jest duży, użyj `| head -N` lub `| tail -n +N | head -M` do paginacji
- Dla plików binarnych (np. `.json` z zaszyfrowanymi kluczami) sprawdź pole `DataProtected`
- Iteracja PR to zazwyczaj `1` — sprawdź przez `/iterations` endpoint tylko gdy PR miał wiele push
