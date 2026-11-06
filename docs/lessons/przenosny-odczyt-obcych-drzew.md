---
title: "Odczyt obcych drzew: O_NOFOLLOW, realpath i samestat na deskryptorze, a git w obcym drzewie z oczyszczonym GIT_*"
tags: [skillsync, security, filesystem, git, worktree, retro, portability]
paths: ['skillsync_workflow/retro.py', 'skillsync_workflow/retro_scan.py', 'tests/test_skillsync_retro_scan.py', 'tests/test_skillsync_retro.py']
source: 'retro.py:338-368 (_open_regular), 797-805 (_GIT_ENV_DROP, _git_env), 868-871 (_identity); retro_scan.py:369-395 (_Git._call, env=_git_env()); raporty code-review-1.2-review-1.md:6, :39-42, :58; 1.2t-mutation.log:6 (M02), :46 (M45), :48 (M47); 1.2t-test-writer.md:65-66; code-review-1.2-decisions-1.md:8'
verified: 2026-11-06
---
**Objaw:** skan czyta projekt, którego drzewo nie jest nasze (główny checkout, inny projekt z `discover_paths`).
Symlink w `.skillsync/runs/` albo podmieniony katalog pośredni kieruje odczyt poza projekt. Zmienne `GIT_DIR` albo
`GIT_WORK_TREE` z otoczenia przestawiają `git` na inne repozytorium i zmieniają wybór głównego checkoutu.

**Przyczyna:** `O_NOFOLLOW` chroni tylko ostatni segment ścieżki. Sprawdzenie `lstat` przed `open` jest wyścigiem: między
nimi plik albo katalog pośredni można podmienić. Otoczenie `git` dziedziczy zmienne przestawiające repozytorium.

**Reguła:**
- Plik z obcego drzewa otwieraj flagami `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` (brak `getattr` na platformie oznacza
  zero, nie błąd). Po `open` porównaj `realpath` ścieżki z jej `normpath` i sprawdź `os.path.samestat` między
  deskryptorem a `os.stat(realpath)`. Każde odstępstwo to `ValueError` ze stałym tekstem.
- Każde wywołanie `git` w obcym drzewie idzie z oczyszczonym środowiskiem (`_git_env()`: bez `GIT_DIR`, `GIT_WORK_TREE`,
  `GIT_COMMON_DIR`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_NAMESPACE`,
  `GIT_CEILING_DIRECTORIES`) i z `GIT_TERMINAL_PROMPT=0`. Dotyczy też pomocników jak `_identity`.
- Wyjście `git` nie trafia do wyniku ani do komunikatu. Tam, gdzie liczy się tylko kod wyjścia, stdout nie jest
  dekodowany (`_Git._run`, `retro_scan.py`). Tam, gdzie potrzebny jest tekst, dekoduj z obsługą błędów:
  `decode("utf-8", "replace")` w `_main_source` (`retro_scan.py:442`) i `_Git.output` (`retro_scan.py:378`).
  `_identity` (`retro.py:868-871`) używa `text=True` i łapie `ValueError` (w tym `UnicodeDecodeError`), co daje
  własną tożsamość repozytorium. Zasada: błąd dekodowania nigdy nie przerywa polecenia.
- Wynik `git` z rc 1 i 128 czytaj osobno: rc 128 to „nie repozytorium” tylko przy braku `.git` w korzeniu i przodkach.

**Jak wykryć:**
- Testy wyścigu przez hak na `os.path.realpath` (`CrossProjectRaceAfterOpen` w `test_skillsync_retro.py`): podmiana
  katalogu na dowiązanie z tym samym inode, podmiana pliku po otwarciu. Mutacje: bez `samestat` (M47), bez porównania
  `realpath` (M48), bez czyszczenia `GIT_*` (M02, M45).
- Test `GIT_DIR` z otoczenia: wybór głównego checkoutu i deduplikacja worktree nie zmieniają się.
- Uwaga: `realpath` i `samestat` częściowo się pokrywają. Każde z osobna łapie inny scenariusz, więc oba zostają.
