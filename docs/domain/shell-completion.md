---
title: "Autouzupełnianie powłok (completion): statyczny generator, bloki ze znacznikami, profile PowerShella, składnie bash, zsh i PowerShell"
tags: [skillsync, completion, shell, bash, zsh, powershell, install, startup-files]
paths: ['skillsync_workflow/completion.py', 'skillsync_workflow/completion_install.py', 'skillsync_workflow/setup_wizard.py', 'install.sh', 'tests/test_skillsync_completion_*.py', 'tests/fixtures/completion/**']
source: "docs/specs/2026-11-21-core-completion-autouzupelnianie-powlok.md (Problem, Zakres, Decyzje i alternatywy, Kryteria K1-K17); skillsync_workflow/completion.py (render_bash, render_zsh, render_powershell, _PS_DRIVER); skillsync_workflow/completion_install.py (install_block, remove_block, zsh_block_lines, ps_block_lines, write_file, default_profile); install.sh (install_completion); raporty CORE-COMPLETION F1, F2.1 i F3 w .skillsync/runs/CORE-COMPLETION/"
verified: 2026-11-21
---
Reguła: `skillsync` podpowiada podkomendy, flagi i wartości przy Tab w bash, zsh i PowerShellu. Skrypt jest generowany ze
stanu parsera przez `skillsync completion <powłoka>` i zapisywany jako plik w katalogu danych użytkownika. Plik startowy
powłoki (`.zshrc`, profil PowerShella) dostaje co najwyżej jeden blok ze znacznikami. Komendy i ścieżki: [cli](../cli.md#uzupełnianie-powłoki-completion);
instalacja: [installation](../installation.md#uzupełnianie-poleceń-w-powłoce).

## Definicje

- **Uzupełnianie powłoki (completion):** funkcja powłoki, która podpowiada podkomendy, flagi i wartości `skillsync` przy
  Tab. Źródło: spec, „Problem”; `skillsync_workflow/completion.py`.
- **Blok ze znacznikami:** fragment pliku startowego między wierszami `# >>> skillsync completion >>>` i
  `# <<< skillsync completion <<<`. Źródło: `skillsync_workflow/completion_install.py`, stałe `START` i `END`.
- **Kopia zapasowa (`.skillsync-bak`):** kopia pliku startowego sprzed pierwszej edycji, bajt w bajt, nigdy nie
  nadpisywana. Źródło: `_make_backup`.

## Decyzje

1. **Generator statyczny z drzewa parsera.** Drzewo czytane jest przez `choices` zarejestrowanego `_SubParsersAction`
   (`build_model`), bez edycji `cli.py`. Podkomenda dodana przez inny moduł trafia do skryptu sama. Odrzucone:
   `argcomplete` (nowa zależność, start Pythona przy każdym Tab) i ręczna lista (rozjeżdża się z parserem; pilnuje tego
   test parytetu).
2. **Plik zamiast `eval` w pliku startowym.** Skrypt leży w katalogu danych i nie wymaga uruchamiania `skillsync` przy
   starcie powłoki. Cena: skrypt starzeje się po aktualizacji, do czasu ponownego `completion install`.
3. **Bash bez edycji pliku rc.** Plik w `bash-completion/completions/`, ładowany przez bash-completion na żądanie.
4. **Bloki ze znacznikami i odmowa przy niejasnym stanie.** Znaczniki pozwalają usunąć blok bez parsowania reszty pliku.
   Dwa bloki albo niedomknięty znacznik daje `failed` i brak zapisu. Zmieniony blok jest podmieniany w miejscu.
   Zmiana znaczników byłaby zmianą kontraktu: stary blok przestałby być rozpoznany.
5. **Końce linii i BOM.** Odczyt i zapis bajtowy. Dominujący koniec linii pliku zostaje, BOM zostaje. Nowy profil
   PowerShella i skrypt PowerShella powstają jako UTF-8 z BOM. Blok w profilu jest ASCII; ścieżka spoza ASCII w profilu
   bez BOM daje `failed`, bo Windows PowerShell 5.1 czytałby ją jako ANSI. Źródło: test K10 w
   `tests/test_skillsync_completion_install.py` i praktyka.
6. **Profile PowerShella.** Używany jest `CurrentUserCurrentHost` każdego znalezionego hosta (`pwsh`, `powershell`).
   Ścieżkę pyta host poleceniem `$PROFILE.CurrentUserCurrentHost` z `-NoProfile -NonInteractive` (limit 10 s); błąd
   daje ostrzeżenie i wartość domyślną. Blok wczytuje skrypt przez kropkę, więc wymiana skryptu nie zmienia profilu.
   Domyślne profile na Windows: `Documents\PowerShell\` (PowerShell 7) i `Documents\WindowsPowerShell\` (5.1).
7. **`#compdef skillsync` w pierwszej linii pliku zsh.** `compinit` czyta do znacznika `#compdef` tylko pierwszą linię
   pliku funkcji. Nagłówek generatora jest więc w linii 2. `uninstall` rozpoznaje nasz plik po nagłówku w dwóch
   pierwszych liniach (`_is_ours`, `lines=2`). Źródło: raport F1 (test zsh).
8. **Dictionary Ordinal w PowerShellu.** Tabela danych to `Dictionary[string,object]` z `StringComparer::Ordinal`.
   Zwykła tabela (hashtable) PowerShella nie rozróżnia wielkości liter, a flagi `-v` i `-V` są różne. Źródło: raport F1
   (sprawdzone w `pwsh`, `TabExpansion2`).
9. **Kolejność w zsh: `fpath`, `autoload`, potem `compdef` albo `compinit`.** Blok ustawia `fpath` na katalog z
   `_skillsync`, woła `autoload -Uz _skillsync`, a `compinit -i` wywołuje tylko, gdy `compdef` jeszcze nie istnieje
   (`$+functions[compdef]`). Gdy `compdef` istnieje, blok tylko rejestruje `compdef _skillsync skillsync`. Drugi
   `compinit` spowalnia start i daje ostrzeżenia. Źródło: `zsh_block_lines`; test K8 (`zsh -fc`).

## Składnia każdej powłoki i źródła

Adresy poniżej spisano bez dostępu do sieci; przed przywołaniem w PR sprawdź je.

- **bash:** `complete -o filenames -F`, `COMP_WORDS`, `COMP_CWORD`, `COMPREPLY`. Podręcznik Bash, rozdział „Programmable
  Completion”: https://www.gnu.org/software/bash/manual/bash.html#Programmable-Completion . Ładowanie plików z katalogu
  `bash-completion/completions`: projekt bash-completion, https://github.com/scop/bash-completion . Skrypt nie używa
  `_init_completion`, więc zależy tylko od tego, że plik jest ładowany.
- **zsh:** `#compdef` w pierwszej linii, `fpath`, `autoload -Uz`, `compdef`, `compinit`, `compadd`, `_files`, `$words`,
  `$CURRENT`, `$PREFIX`. Dokumentacja systemu uzupełniania zsh (zshcompsys):
  https://zsh.sourceforge.io/Doc/Release/Completion-System.html . Praktyka: funkcja z `#compdef` w pierwszej linii jest
  ładowana przy pierwszym Tab.
- **PowerShell:** `Register-ArgumentCompleter -Native -CommandName`,
  `[System.Management.Automation.CompletionResult]::new`, `$commandAst`, profil `$PROFILE.CurrentUserCurrentHost`.
  Dokumentacja Microsoft: https://learn.microsoft.com/powershell/module/microsoft.powershell.core/register-argumentcompleter
  oraz about_Profiles: https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_profiles .
  Praktyka: Windows PowerShell 5.1 czyta skrypt bez BOM jako ANSI (stąd BOM w decyzji 5).

## Ograniczenia i pułapki

- zsh: blok trafia do `${ZDOTDIR:-~}/.zshrc`. `ZDOTDIR` jest widoczne dla instalatora tylko, gdy jest w środowisku
  procesu. Ustawione wyłącznie w `~/.zshenv` powoduje, że blok trafia do pliku, którego zsh nie czyta. Spec, „Znane ograniczenie”.
- bash: bez pakietu bash-completion plik się nie ładuje; wtedy działa ręczny `source` z `installation.md`.
- PowerShell: `uninstall` usuwa blok z profili hostów znalezionych na PATH w chwili wywołania (gdy nie ma żadnego,
  z domyślnych profili). Profil hosta usuniętego z PATH po instalacji nie jest wtedy odwiedzany i blok zostaje do
  ręcznego usunięcia.
- POSIX: gdy zmiennej `HOME` nie ma w środowisku, katalog domowy bierze się z konta; pusta `HOME` daje `BLOCKED`.
  Na Windows brak `HOME` i `USERPROFILE` daje `BLOCKED`.
- Flaga `--auto` działa tylko przy `install`; przy `uninstall` nie ma skutku.
- Skrypt starzeje się po aktualizacji narzędzia; wykrywanie nieaktualnego skryptu w `doctor` jest poza zakresem.
- Dynamiczne podpowiedzi (ścieżki projektów, id warstw) są poza zakresem: skrypt jest statyczny.
