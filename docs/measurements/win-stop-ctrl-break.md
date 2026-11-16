# Zatrzymanie serwera sygnałem CTRL_BREAK (Windows)

Data: 2026-11-16
System: Windows 11, Python 3.12.16
Wersja: skillsync po zmianie (cd7503a, wydane w 1.7.0); stan przed zmianą na c332287 (wersja Pythona w tym pomiarze niezapisana)
Polecenie: `skillsync dashboard serve <projekt-testowy>` uruchomiony z `CREATE_NEW_PROCESS_GROUP` we wspólnej konsoli, izolowany `LOCALAPPDATA`; `os.kill(pid, signal.CTRL_BREAK_EVENT)`.
Wynik: po zmianie kod wyjścia 0 po 0,49 s, plik stanu usunięty, port wolny, drugi start na tym porcie powiódł się. Tryb dwóch portów: kod 0, oba pliki stanu usunięte. Dwa sygnały (drugi po 0,15 s): kod 0, sprzątanie nieprzerwane. Przed zmianą (c332287): twardo, kod 3221225786 (0xC000013A), plik stanu zostawał.
