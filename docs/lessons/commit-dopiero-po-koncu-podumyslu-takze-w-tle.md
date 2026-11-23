---
title: "Commit dopiero po zgłoszeniu końca przez podumysł, także w tle: w historię trafia jego mutacja kontrolna"
tags: [skillsync, workflow, subagents, commit, mutation-testing, race]
paths: ['library/skills/ui-verify/scripts/lib/css-parse.mjs']
source: "przebieg CORE-1140-T5 (22.11): commit 12b5f62 objął mutację kontrolną implementera, naprawa w 191d748; uogólnione w skillu agent-runtime-pitfalls (sekcja „Podumysły w tle”)"
verified: 2026-11-22
---
Objaw: w commicie krok-po-kroku znalazły się linie z mutacji kontrolnej podumysłu (`const e = -3; void skipWord;` w trzech funkcjach parsera). Testy w tym stanie były czerwone lub zawodziły sprawdzenie, a reviewer zauważył aktywną mutację dopiero w następnej rundzie.
Przyczyna: sesja główna zrobiła `git add` + `git commit` w chwili, gdy implementer nadal pracował (mutacje kontrolne wprowadza w plikach produkcyjnych i cofa po serii testów). Ścieżki były jawne, więc `git add` nie zadziałał błędnie; zły był moment: stan plików nie był jeszcze stanem końcowym.
Reguła: commit kroku tylko po zgłoszeniu końca przez podumysł, który dotykał tych plików, także gdy pracował w tle lub miał jeszcze polecenie do wykonania. Przed `git add` sprawdź `git diff` pod kątem śladów mutacji (stałe i `void` bez użycia, zakomentowane warunki, zmienione progi) i uruchom testy na tym stanie, który zapisujesz. Zlecając podumysłowi mutacje kontrolne, każ je cofać i potwierdzić `git diff` w raporcie.
Jak wykryć: `git show <sha> -- <plik>` i szukaj linii, których nie ma w opisie kroku; `grep -n "void [a-zA-Z]*;" <pliki produkcyjne>`; po commicie `git diff <sha>~1 <sha>` porównaj z zakresem kroku.
