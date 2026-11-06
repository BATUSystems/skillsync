# Worktree do code review

Cel: przeglądać dokładny kod PR/zmian bez dotykania głównego working tree. Wszystko poniżej poza
`git fetch` i `git worktree add` jest tylko odczytem. Ścieżkę worktree trzymaj **poza** repozytorium
(`<katalog nadrzędny>/<repo>-review-<id>`), by nie wpadła do indeksu ani do diffów.

## Tryb PR

```bash
cd <repo>                                   # istniejący klon repozytorium z PR
git fetch origin <źródło-PR> <cel-PR>       # nazwy z sourceRefName/targetRefName (bez refs/heads/)
SRC=$(git rev-parse FETCH_HEAD^{commit})    # albo: git rev-parse origin/<źródło-PR> po fetchu
BASE=$(git merge-base origin/<cel-PR> origin/<źródło-PR>)
git worktree add --detach ../<repo>-review-pr<ID> origin/<źródło-PR>
git -C ../<repo>-review-pr<ID> diff --stat $BASE...HEAD
git -C ../<repo>-review-pr<ID> diff $BASE...HEAD > <scratch>/pr<ID>.diff
```

- Commit źródła PR z API (`lastMergeSourceCommit`) powinien równać się `HEAD` worktree; rozbieżność zgłoś.
- PR z forka lub gałęzi niedostępnej dla `git fetch`: zbuduj stan z plików pobranych REST-em
  (`items?...&version=<commit>`), oznacz to w raporcie jako ograniczenie.
- Duży diff: zapisz do pliku i przekaż reviewerom ścieżkę oraz listę plików w kolejności ryzyka.

## Tryb przed PR (po `/feature` / `/bugfix`)

1. Baza: `base_commit` z planu/checkpointu; inaczej `--base <ref>`; inaczej `git merge-base HEAD origin/<domyślna>`.
2. Commity brancha bez zmian roboczych: `git worktree add --detach ../<repo>-review-<branch> HEAD`.
3. Są zmiany niezacommitowane (staged/unstaged/untracked): snapshot bez ruszania źródła:
   ```bash
   git worktree add --detach ../<repo>-review-wip HEAD
   git diff HEAD --binary | git -C ../<repo>-review-wip apply --index   # staged + unstaged
   git ls-files --others --exclude-standard -z | xargs -0 -I{} cp --parents {} ../<repo>-review-wip/
   ```
   Alternatywnie, gdy snapshot się nie powiedzie: review w miejscu (reviewerzy i tak są tylko do odczytu),
   z diffem `git diff <baza>` + listą untracked przygotowanymi przez orkiestratora.
4. Diff do review: `git diff <baza>` w snapshotcie (lub `git diff <baza>...HEAD` dla samych commitów).
   Wyklucz zmiany sprzed zadania (stan początkowy zapisany przez `/feature`).

## Sprzątanie

`git worktree remove ../<repo>-review-<id>` (bez `--force`; niezapisane zmiany → zapytaj) i
`git worktree prune`. Nie usuwaj worktree, których ta sesja nie utworzyła.
