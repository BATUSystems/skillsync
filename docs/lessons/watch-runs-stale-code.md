---
title: Usługa watch uruchamia kod z chwili startu
tags: [skillsync, watch, service, library]
paths: ['skillsync', 'skillsync-ctl']
---
Dowód: `skillsync-watch.service` wykonuje `~/.local/bin/skillsync watch` (`Restart=on-failure`); `skillsync:cmd_watch` to pętla w jednym procesie Pythona, więc zmiana `skillsync`/`skillsync_workflow/`
po starcie usługi nie działa do restartu. Przy przepięciu symlinków `~/.local/bin/skillsync*` na nowe repo (2026-10-02, plan `docs/plans/2026-10-02-skillsync-dispatch-feature-split.md`) usługa nadal trzymała stary kod.
Druga strona: watch linkuje projekty po **każdej** zmianie mtime w bibliotece, więc wieloetapowa zmiana (rename skilla, nowy protokół) może rozesłać stan pośredni do wszystkich projektów.

Reguła: po zmianie generatora wykonaj `skillsync-ctl restart`; przy wieloetapowej zmianie biblioteki wykonaj `skillsync-ctl stop`, zakończ zmiany, sprawdź `skillsync link <projekt> --dry-run`, potem `skillsync-ctl start`.

Dotknięte obszary: `skillsync-ctl`, `skillsync:cmd_watch`, każda edycja biblioteki. Granice aktualności: do czasu zmiany implementacji watch (np. przeładowanie kodu albo debounce).
