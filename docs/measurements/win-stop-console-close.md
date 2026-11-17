# Zamknięcie okna konsoli serwera (Windows, klasyczna konsola)

Data: 2026-11-16
System: Windows 11 (klasyczny host konsoli, klasa okna `ConsoleWindowClass`), Python 3.12 oraz 3.11
Wersja: przed zmianą 1.7.0 (6b6f8df); po zmianie gałąź integracyjna 1.8.0 (1710d81)
Polecenie: `skillsync dashboard serve <projekt-testowy>` w nowej konsoli, izolowany `LOCALAPPDATA`; `WM_CLOSE` do okna konsoli, wysłany po sprawdzeniu, że właścicielem okna jest proces serwera, a w konsoli działa tylko serwer i jego pomocnik.
Wynik: przed zmianą (1.7.0) proces kończy się w mniej niż 10 ms z kodem 3221225786 (0xC000013A), plik stanu zostaje. Po zmianie plik stanu jest usunięty po 0,47 s (Python 3.12) i 0,40 s (Python 3.11). Limit systemowy bezczynnej aplikacji (`SPI_GETHUNGAPPTIMEOUT`) = 5000 ms.
Uwagi: Windows Terminal (pseudokonsola) niezmierzony; Menedżer zadań („Zakończ zadanie”) niezmierzony.
