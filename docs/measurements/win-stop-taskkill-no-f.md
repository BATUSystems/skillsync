# `taskkill /PID` bez `/F` na serwerze konsolowym (Windows)

Data: 2026-11-16
System: Windows 11, Python 3.12
Wersja: skillsync przed 1.7.0 (c332287)
Polecenie: `taskkill /PID <pid>` na procesie `skillsync dashboard serve`.
Wynik: nie działa, `taskkill` kończy się kodem 1 z komunikatem „This process can only be terminated forcefully (with /F option).”.
