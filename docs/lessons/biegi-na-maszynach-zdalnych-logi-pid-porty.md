---
title: "Biegi macierzy na maszynach zdalnych: logi przyrostowo, śmierć drzewa po PID-ach, jedna pula portów na proces, lista modułów bez aliasów, tło na macOS bez setsid"
tags: [testing, remote, windows, macos, ports, processes, evidence]
paths: ['library/skills/feature/references/prepush-matrix.md', 'tests/**']
source: 'zdarzenie z nocy 2026-11-15/16: biegi macierzy punktowej na maszynach Windows i macOS (porażki środowiskowe, nie produktu)'
verified: 2026-11-16
---
**Objaw:** bieg na maszynie zdalnej kończy się bez logu, z zajętym portem albo z błędnymi nazwami modułów; sprzątanie katalogów zawodzi, bo coś jeszcze je trzyma.

**Przyczyna i reguła (po jednej na objaw):**
- Log kopiowany dopiero po biegu ginie razem z ubitym biegiem: kopiuj logi modułów przyrostowo w trakcie biegu.
- `kill` jednego PID-u nie kończy drzewa (runner, interpreter, serwery testów): po kill sprawdź po PID-ach, że całe drzewo nie żyje, dopiero potem usuwaj katalogi.
- Plasterek portów testowych należy do jednego procesu naraz; drugi proces na tej samej puli daje zajęty port, który wygląda jak porażka produktu.
- Lista modułów zbierana przez alias powłoki (kolory, ikony) zawiera kody sterujące: używaj `command ls`, ścieżki bezwzględnej albo `glob` w skrypcie.
- macOS nie ma `setsid`: start w tle przez `nohup` z przekierowaniem i plikiem PID, a w ciągu minuty sprawdź, że proces żyje i log rośnie.

**Jak wykryć:** brak pliku logu na stacji w trakcie biegu; `OSError`/`WinError 10048` przy starcie serwera testowego; nazwy modułów z sekwencjami `ESC[` w komunikacie unittest; `setsid: command not found`.
