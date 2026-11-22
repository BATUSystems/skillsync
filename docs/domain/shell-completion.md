---
title: "Autouzupełnianie powłok (completion): statyczny generator, bloki ze znacznikami, profile PowerShella, składnie bash, zsh i PowerShell"
tags: [skillsync, completion, shell, bash, zsh, powershell, install, startup-files]
paths: ['skillsync_workflow/completion.py', 'skillsync_workflow/completion_install.py', 'skillsync_workflow/setup_wizard.py', 'install.sh', 'tests/test_skillsync_completion_*.py', 'tests/fixtures/completion/**']
source: "spec paczki CORE-COMPLETION (Problem, Zakres, Decyzje i alternatywy, Kryteria K1-K17) oraz poprawki po przeglądzie (FIX2); skillsync_workflow/completion.py (render_bash, render_zsh, render_powershell, _PS_DRIVER); skillsync_workflow/completion_install.py (install_block, remove_block, zsh_block_lines, ps_block_lines, write_file, default_profile); install.sh (install_completion); raporty CORE-COMPLETION F1, F2.1 i F3 w .skillsync/runs/CORE-COMPLETION/"
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
6. **Profile PowerShella.** Używany jest `CurrentUserAllHosts` (`profile.ps1`) każdego znalezionego hosta (`pwsh`,
   `powershell`): czytają go wszystkie hosty, także konsola rozszerzenia VS Code, która ma własny host i własny profil hosta.
   Host dostaje jedno zapytanie z `-NoProfile -NonInteractive` (limit 10 s) i odpowiada dwiema liniami ASCII:
   `P64:<base64 z UTF-8 ścieżki>` i `POLICY:<efektywna ExecutionPolicy>`; base64 omija problem strony kodowej (polskie litery
   w ścieżce). Linia bez prefiksu `P64:` nie jest odpowiedzią (brak ścieżki). Ewentualny BOM UTF-8 na początku wyjścia jest zdejmowany przed parsowaniem. Host na PATH bez
   użytecznej odpowiedzi (czas, śmieci, ścieżka niebezwzględna, błąd dekodowania) jest pomijany (`skipped` plus ostrzeżenie),
   bez cichego powrotu do ścieżki domyślnej; domyślne ścieżki służą tylko, gdy na PATH nie ma żadnego hosta. Blok wczytuje
   skrypt przez kropkę, więc wymiana skryptu nie zmienia profilu. Domyślne profile: Windows `Documents\PowerShell\profile.ps1`
   (7) i `Documents\WindowsPowerShell\profile.ps1` (5.1), POSIX `${XDG_CONFIG_HOME:-~/.config}/powershell/profile.ps1`.
   Skrypt rejestruje uzupełnianie dla `skillsync` i `skillsync.exe`. Źródło: `ps_plan`, `query_host`, `Targets.default_profile`.
7. **`#compdef skillsync` w pierwszej linii pliku zsh.** `compinit` czyta do znacznika `#compdef` tylko pierwszą linię
   pliku funkcji. Nagłówek generatora jest więc w linii 2. `uninstall` rozpoznaje nasz plik po nagłówku w dwóch
   pierwszych liniach (`_is_ours`, `lines=2`). Źródło: raport F1 (test zsh).
8. **Dictionary Ordinal w PowerShellu.** Tabela danych to `Dictionary[string,object]` z `StringComparer::Ordinal`.
   Zwykła tabela (hashtable) PowerShella nie rozróżnia wielkości liter, a flagi `-v` i `-V` są różne. Źródło: raport F1
   (sprawdzone w `pwsh`, `TabExpansion2`).
9. **Kolejność w zsh: `fpath`, `autoload`, potem `compdef` albo `compinit`; dwa warianty.** Blok ustawia `fpath` na katalog
   z `_skillsync` i woła `autoload -Uz _skillsync`. Wariant A wybiera wyłącznie treść pliku poza blokiem, w liniach niebędących komentarzem (pomijane są linie
   zaczynające się od `#` po białych znakach i tekst po ` #`): osobne słowo `compinit`, albo załadowanie `oh-my-zsh`
   (wyłącznie skrypt `oh-my-zsh.sh`; inny plik z katalogu `~/.oh-my-zsh/` nie liczy się), albo prezto z modułem `completion` na liście tego samego
   `zstyle ':prezto:load' pmodule` (także po `\` w kolejnych liniach; słowo `completion` w innym `zstyle`, np. `':completion:*'`, nie liczy się); te dwa frameworki same wołają `compinit`.
   Samo słowo w komentarzu ani nazwa `zinit`, `antidote`, `zplug` wariantu A nie wybierają: podwójny `compinit` kosztuje milisekundy,
   brak uzupełniania jest gorszy. Wariant A: `if (( $+functions[compdef] )); then compdef _skillsync skillsync; …; fi` (nie `A && B`, bo ta
   forma zwraca 1 pod `setopt ERR_EXIT`), bez `compinit`; gdy `compdef` jeszcze nie istnieje, blok rejestruje jednorazowy hook `precmd`
   (`_skillsync_register`), który po starcie, gdy `compdef` nadal nie istnieje, najpierw uruchamia `autoload -Uz compinit && compinit -i`, potem rejestruje funkcję,
   sam się usuwa z `precmd_functions` i gdy `compdef` mimo to brakuje, wypisuje ostrzeżenie na stderr. Wariant B: gdy `compdef` nie istnieje, blok wywołuje `compinit -i`. Drugi `compinit` spowalnia start i daje ostrzeżenia. Wariant liczy się z treści pliku bez naszego bloku, a jego
   zmiana to `updated` w miejscu. Źródło: `zsh_block_lines`, `zsh_calls_compinit_elsewhere`; testy `ZshVariantTest`, `ZshRuntimeTest`.
10. **ExecutionPolicy.** `Restricted` i `AllSigned` blokują wczytanie niepodpisanego skryptu z profilu (na kliencie Windows domyślną
    Windows PowerShella 5.1 jest `Restricted`). Wtedy dla tego hosta nie zapisujemy nic (ani profilu, ani skryptu): `skipped`
    z poleceniem `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` i wpis w `warnings`; przy `--auto` jedna linia informacji,
    bez ostrzeżenia i bez `FAIL`. Polityki nie zmieniamy za użytkownika. Nieznana polityka nie blokuje; `uninstall` jej nie sprawdza.
11. **Bezpieczeństwo zapisu.** Plik startowy jest czytany ponownie tuż przed zapisem (bajty, rozmiar, mtime); zmiana w międzyczasie to
    `failed` („changed during the operation, run it again”) bez zapisu. Wiszący symlink to `failed` bez tworzenia czegokolwiek.
    Plik z `st_nlink` > 1 jest zapisywany w miejscu (rename zerwałby dowiązania), a taki zapis nie jest atomowy; w pozostałych
    przypadkach właściciel i xattr (tylko na Linuksie) trafiają do pliku zastępczego (w miarę możliwości). Usunięcie pliku, który sami
    utworzyliśmy (`uninstall`), przechodzi tę samą kontrolę niezmienności. Wynik `failed` po przerwanym zapisie (zmiana pliku w międzyczasie, błąd I/O) wspomina o kopii `<plik>.skillsync-bak`, gdy istnieje; odmowa przed zapisem (zły blok, cudzy plik) jej nie wspomina.
    `uninstall` próbuje też profilu domyślnego obok profili podanych przez hosty. Źródło: `write_file`, `_check_unchanged`, `_carry_attributes`.
12. **bash: tryb nazw plików i `compopt`.** Skrypt rejestruje `complete -o filenames`, a dla gałęzi bez ścieżek wywołuje
    `compopt +o filenames`, żeby bash nie dopisywał `/` do kandydatów zgodnych z katalogiem. Strażnik to `type compopt`, nie
    `declare -F compopt`: ten drugi wylicza tylko funkcje, więc dla wbudowanego `compopt` bash 4+ zwraca 1 (sprawdzone w bash 5.3);
    bash 3.2 z macOS nie ma `compopt`, więc `type` kończy się 1. Kandydaci plików są czytani pętlą `while read -r` (bez `mapfile` i bez
    rozwijania nazw). `--flag=value` nie jest uzupełniane: `COMP_WORDBREAKS` rozbija słowo na `=`. W gałęzi, w której kandydatami są
   podkomendy razem ze ścieżkami, tryb nazw plików zostaje włączony, więc bash nadal dopisuje `/` do kandydata, którego nazwa jest też nazwą
   katalogu w bieżącym katalogu (np. podkomenda `docs` przy katalogu `docs/`).
13. **Windows PowerShell 5.1: samo `-` i `--` nie wywołuje completera.** Host 5.1 nie przekazuje do `Register-ArgumentCompleter -Native` słowa złożonego
    z samych myślników: `skillsync link --d` daje `--dry-run`, a `skillsync link --` daje pustą listę (PowerShell 7 podaje wtedy flagi). Trzeba wpisać
    co najmniej jedną literę po myślnikach. To zachowanie hosta, nie skryptu; sprawdzone na PowerShell 5.1.26100.

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
  `[System.Management.Automation.CompletionResult]::new`, `$commandAst`, profil `$PROFILE.CurrentUserAllHosts`, `Get-ExecutionPolicy`.
  Dokumentacja Microsoft: https://learn.microsoft.com/powershell/module/microsoft.powershell.core/register-argumentcompleter
  oraz about_Profiles: https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_profiles .
  Praktyka: Windows PowerShell 5.1 czyta skrypt bez BOM jako ANSI (stąd BOM w decyzji 5). Polityki wykonywania: about_Execution_Policies, https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_execution_policies .

## Ograniczenia i pułapki

- zsh: blok trafia do `${ZDOTDIR:-~}/.zshrc`. `ZDOTDIR` jest widoczne dla instalatora tylko, gdy jest w środowisku
  procesu. Ustawione wyłącznie w `~/.zshenv` powoduje, że blok trafia do pliku, którego zsh nie czyta. Spec, „Znane ograniczenie”.
- bash: bez pakietu bash-completion plik się nie ładuje; wtedy działa ręczny `source` z `installation.md`. Zapis `--flag=value` nie jest uzupełniany.
- PowerShell: start powłoki z blokiem w profilu rośnie o ok. 38 ms (pomiar pwsh 7 na Linuksie, dot-source skryptu ok. 50 KB).
- PowerShell: `uninstall` usuwa blok z profili hostów znalezionych na PATH w chwili wywołania (gdy nie ma żadnego,
  z domyślnych profili). Profil hosta usuniętego z PATH po instalacji nie jest wtedy odwiedzany i blok zostaje do
  ręcznego usunięcia. Host bez użytecznej odpowiedzi na zapytanie o profil jest pomijany (`skipped` plus ostrzeżenie), więc jego blok też zostaje.
- POSIX: gdy zmiennej `HOME` nie ma w środowisku, katalog domowy bierze się z konta; pusta `HOME` daje `BLOCKED`.
  Na Windows brak `HOME` i `USERPROFILE` daje `BLOCKED`.
- Flaga `--auto` działa tylko przy `install`; przy `uninstall` nie ma skutku.
- Skrypt starzeje się po aktualizacji narzędzia; wykrywanie nieaktualnego skryptu w `doctor` jest poza zakresem.
- Dynamiczne podpowiedzi (ścieżki projektów, id warstw) są poza zakresem: skrypt jest statyczny.
