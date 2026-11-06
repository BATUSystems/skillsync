# Dostawcy PR-ów

Operacje PR-ów idą przez strzeżone `skillsync tracker`, o ile deskryptor dostawcy je obejmuje. Azure: tak. GitHub i Jira mają w deskryptorze tylko odczyt oraz `update-title` ticketów, więc PR-y na GitHubie tworzy się przez `gh`.

Zapisy wykonuj wyłącznie z listy zgody skilla ([pr-run](../SKILL.md)). Organizację, projekt i repozytorium ustal z `git remote get-url origin`; każdą wartość sprawdź wyrażeniem `^[A-Za-z0-9._/-]+$` i cytuj w komendach.

## Azure DevOps

- Domyślna ścieżka to strzeżone operacje `skillsync tracker` z deskryptora `workflow/trackers/azure.yaml`. Parametr `organization` to sama nazwa (np. `example-org`), nie URL.
- Zapis wymaga `--run --allow-write` i przechodzi odczyt kontrolny. Wynik `BLOCKED` z polem `mutation_completed` znaczy, że zapis już się wykonał: sprawdź stan, nie ponawiaj.
- Uruchamiaj przy zalogowanym `az` (`az devops login` albo PAT); uwierzytelnienie nigdy nie trafia do parametrów.
- Python z pyenv nie ma PyYAML: wołaj `/usr/bin/python3` jak w bloku.
- `SKILLSYNC_DIR` to katalog repozytorium generatora skillsync; ustaw go przed użyciem bloku.

```bash
sk() { op="$1"; params="$2"; shift 2; /usr/bin/python3 "$SKILLSYNC_DIR/skillsync" tracker . --provider azure --operation "$op" --params "$params" "$@"; }
# 1. istnienie (pusta lista = brak PR-a)
sk pr-list '{"organization":"<organization>","project":"<project>","repository":"<repository>","source":"<source>","target":"<target>"}' --run
# 2. utworzenie (zawsze draft)
sk pr-create '{"organization":"<organization>","project":"<project>","repository":"<repository>","source":"<source>","target":"<target>","title":"<KEY>: <opis>","description":"<opis>"}' --run --allow-write
# 3. przełączenie na ready
sk pr-ready '{"organization":"<organization>","id":"<id>"}' --run --allow-write
# 4. komentarz; przed ponowieniem sprawdź pr-last-thread (parametr pr)
sk pr-comment '{"organization":"<organization>","project":"<project>","repository":"<repository>","pr":"<pr>","body_file":".skillsync/tmp/comment.json"}' --run --allow-write
```

Plik treści `.skillsync/tmp/comment.json` (katalog ignorowany przez git) ma postać `{"comments":[{"parentCommentId":0,"content":"…","commentType":1}],"status":1}`; `content` to markdown zaczynający się od znacznika `<!-- pr-run:summary -->`.

- Gdy `pr-latest` zwraca `BLOCKED` bez aktywnego PR-a, PR-a nie ma.
- W operacjach wątków numer PR-a podajesz parametrem `pr`, nie `id`.
- Reviewerów (Required) dodaj osobno przez `az repos pr reviewer add` według skilla [azure-devops](../../azure-devops/SKILL.md); deskryptor tego nie obejmuje.
- Ścieżka zastępcza: `az repos pr create/update --draft` albo REST z PAT ([azure-devops-pr-review](../../azure-devops-pr-review/SKILL.md)).
- Zakazane: `--auto-complete`, `--status completed`, zmiany polityk, usuwanie gałęzi.
- Stan weryfikacji: `az` 2.90, rozszerzenie azure-devops 1.0.2. Na żywo sprawdzono wszystkie operacje, także zapisy, na testowym PR (porzuconym po teście): `pr-create` (draft; wieloliniowy opis ze znakami specjalnymi przeszedł bez zmian), `pr-ready`, `pr-comment` oraz odczyty `pr-show`, `pr-list`, `pr-latest`, `pr-last-thread`.

## GitHub

```bash
gh pr create --draft --base <BASE> --head <BRANCH> --title "<KEY>: opis" --body-file <plik>
gh pr comment <n> --body-file <plik>
gh pr ready <n>
gh pr view <n> --json number,url,isDraft,headRefName
```

Zakazane: `gh pr merge`, `--admin`, zmiany ochrony gałęzi.

## Jira

Wyłącznie odczyt: `skillsync tracker . --provider jira --operation read --params '{"id":"ABC-123"}'`. Klucz wpisz w nazwę gałęzi, tytuł i opis PR-a. Aktualizacja ticketu albo komentarz w Jira wymaga osobnego polecenia użytkownika (zasady: [delivery-workflow-jira-azure](../../delivery-workflow-jira-azure/SKILL.md)).
