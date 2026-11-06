---
title: Task folder-open miał twardą rozwiązaną ścieżkę skryptu i nie był odświeżany
tags: [skillsync, vscode, tasks, relocation]
paths: ['skillsync_workflow/cli.py', 'skillsync_workflow/selfcmd.py', 'tests/test_skillsync_folder_open.py']
---
Dowód: do 2026-10-02 `skillsync:ensure_link_on_open` wpisywał do `.vscode/tasks.json` task `skillsync: link` z `args[0]` = `str(Path(__file__).resolve())`
(po rozwiązaniu symlinków, nie `~/.local/bin/skillsync`) i kończył działanie, gdy task o tej etykiecie już istniał.
Po przeniesieniu generatora z `~/.local/scripts/dev/skillsync` do repo Skillsync (przegląd `~/Repos/**/.vscode/tasks.json`, 2026-10-02) zarządzaną postać taska ze starą ścieżką `~/.local/scripts/dev/skillsync` miały tylko
Globex/Academy (`.vscode/tasks.json:9`) i Acme/Shop/shop-web (`.vscode/tasks.json:160`, plik śledzony w git); taski te wywołują nieistniejący plik, a ówczesny `link` ich nie naprawiał.
Pozostałe projekty mają ręczne taski typu shell przez `~/.local/bin/skillsync`, które działają po przepięciu symlinka. (Liczba 19 to liczba manifestów z `agentflow`, nie tasków.)

Obecne zachowanie: task to `<wpis skillsync> link ${workspaceFolder}` bez interpretera (wpis: `~/.local/bin/skillsync` albo bezwzględne trafienie z `PATH`) (`selfcmd.link_task_argv()`): wpis użytkownika nierozwiązany, bez niego wrapper checkoutu na POSIX, inaczej `self_argv()`.
Polecenie nie zależy od interpretera ani checkoutu, który uruchomił `link`, więc śledzony `tasks.json` nie zmienia się przy `link` z innego interpretera.
Task o postaci generowanej przez skillsync (`is_managed_link_task`) jest odświeżany przy każdym `link` (`command` i początek `args`, z backupem, idempotentnie);
task niestandardowy z tą samą etykietą nie jest ruszany.

Reguła: po przeniesieniu repo przepnij symlinki `~/.local/bin/skillsync*`; zarządzany task nie wymaga zmian, bo wskazuje wpis, a nie repo. Task, który wskazuje nieistniejący plik (postać z konkretną ścieżką skryptu), poprawia dopiero `skillsync link` w projekcie.
Nie zmieniaj postaci generowanego taska bez aktualizacji `is_managed_link_task`, bo starsze taski przestaną być rozpoznawane jako własne.
Kompromis: wrapper checkoutu i wpis będący symlinkiem do niego mają shebang `#!/usr/bin/env python3`, więc task używa `python3` z `PATH` procesu VS Code (z PyYAML; skrypt konsolowy `uv tool` ma interpreter narzędzia); przy innym `PATH` niż w powłoce `link` przy otwarciu folderu zgłosi brak PyYAML.

Dotknięte obszary: `.vscode/tasks.json` projektów z `feature` (chyba że `vscode.link_on_open: false`). Granice aktualności: wzorce postaci (`args` kończące się `link ${workspaceFolder}`, `runOptions.runOn: folderOpen`, nazwa polecenia `skillsync`/`skillsync.exe`) pochodzą z `skillsync_workflow/cli.py` (`is_managed_link_task`); `${userHome}` w tasku jest w długu.
