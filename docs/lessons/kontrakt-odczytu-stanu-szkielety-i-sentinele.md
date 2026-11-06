---
title: 'Kontrakt odczytu stanu z ręcznie pisanych plików: fixtury ze szkieletów i sentinel w każdym polu'
tags: [skillsync, dispatch, ide-contract, redaction, tests, fixtures]
paths: ['skillsync_workflow/dispatch_state.py', 'tests/test_skillsync_dispatch_state.py']
source: "tests/test_skillsync_dispatch_state.py (state(), test_sentinel_*, test_fifo_*, test_filename_*); skillsync_workflow/dispatch_state.py (_Reader, _claims, _dispatch_entry); review adwersaryjne R3c (Opus, 2026-10-31): FIFO w claims, nazwa pliku jako wolny tekst, enum na niehashowalnej wartości; .skillsync/runs/2026-10-31-skillsync-r3c.R3c-dispatch-json/logs/live-real-1.log"
verified: 2026-10-31
---
**Objaw (ryzyko):** widok stanu czytany z plików pisanych ręcznie przez dyspozytora może wyciec wolny tekst (incydenty, poświadczenia) albo się zawiesić/wysypać na dziwnym kształcie, mimo „białej listy”.

**Metoda, która zadziałała:** (1) fixtury odtworzone ze SZKIELETÓW prawdziwych plików (klucze i typy bez wartości; skrypt wypisuje tylko nazwy i liczniki), z sentinelem w polach wyłączonych i nieznanych; (2) test, że sentinel nie występuje w wyjściu, także w polach z białej listy o złym kształcie, w nazwie pliku, w treści zepsutego YAML i w polach tekstowych rekordów przebiegu; (3) sprawdzenie na prawdziwych plikach tylko liczbą trafień napisów ze spacjami z pól wyłączonych (0); (4) adwersaryjne review z konkretnymi wejściami.

**Pułapki znalezione przez review:** nazwa pliku to wolny tekst (pokazuj tylko, gdy jest zwykłym id, a w findings numer wpisu); `read_text()` na FIFO wisi (`lstat` + `S_ISREG`); `dict.get(lista)` rzuca `TypeError` dla wartości niehashowalnych (sprawdź typ przed wyszukaniem); `json.dumps` bez `allow_nan=False` wypisze `Infinity`; wzorzec id nie wyklucza tokenów (odrzucaj długie ciągi bez przerw); brak informacji ma być `null`, nie 0.
