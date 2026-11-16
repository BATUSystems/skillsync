# Twarde zatrzymanie serwera: Stop-Process, taskkill /F, Popen.terminate() (Windows)

Data: 2026-11-16
System: Windows 11, Python 3.12.16
Wersja: skillsync przed 1.7.0 (c332287; Stop-Process, taskkill /F, terminate) i po zmianie (cd7503a, Python 3.12.16; taskkill /F, terminate); wynik ten sam
Polecenie: `skillsync dashboard serve <projekt-testowy>` w izolowanym `LOCALAPPDATA`; kolejno `Stop-Process -Id <pid> -Force`, `taskkill /F /PID <pid>`, `Popen.terminate()`.
Wynik: `TerminateProcess` bez sprzątania, kod wyjścia 1 (terminate, taskkill /F), plik stanu zostaje.
