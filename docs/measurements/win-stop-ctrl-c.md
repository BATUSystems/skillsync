# Zatrzymanie serwera Ctrl+C w konsoli (Windows)

Data: 2026-11-16
System: Windows 11, Python 3.12
Wersja: skillsync przed 1.7.0 (c332287); Python 3.12 (wersja poprawkowa niezapisana)
Polecenie: `skillsync dashboard serve <projekt-testowy>` uruchomiony w nowej konsoli (`CREATE_NEW_CONSOLE`), izolowany `LOCALAPPDATA`, Ctrl+C do konsoli serwera.
Wynik: zatrzymanie łagodne, kod wyjścia 0, plik stanu usunięty.
Uwagi: serwer odziedziczył ustawienie „ignoruj CTRL+C” po rodzicu, gdy był uruchomiony z takiego procesu; wtedy Ctrl+C go nie zatrzymuje.
