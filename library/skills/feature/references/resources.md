# Zasoby i równoległość

Czytaj przed równoległym przydziałem kroków albo uruchomieniem testów w kilku rolach.

Równoległość wymaga **niezależnych zależności, plików i zasobów**, nie tylko innych ścieżek:
- Jeden właściciel zapisu danego pliku, także indeksów, routingu, tłumaczeń i lockfile.
  Nowy plik poza zakresem wymaga najpierw uzgodnienia z orkiestratorem, nie raportu po fakcie.
- Domyślnie jeden aktywny proces testowy na wspólną bazę, port i katalog build/coverage.
  Dotyczy także testów `test-writer`, implementera i hooków. Testy równoległe tylko po
  potwierdzeniu izolacji wszystkich tych zasobów. Osobny worktree nie izoluje bazy ani portów.
- Testy w tym workflow uruchamia przydzielony agent, nie automatyczny hook `Stop`.
  `skillsync` usuwa własny hook testowy w projektach z `feature`; cudze hooki pozostawia.
  Przed testami sprawdź, czy pozostałe hooki także nie uruchamiają tego samego zestawu.
- Agent bez przydziału zasobów raportuje `NOT_RUN` i oddaje komendę runnerowi. Nie czeka
  w nieskończoność i nie uruchamia testów na domyślnej bazie „na próbę”.
- W sesji z dyspozytorem pełne biegi testów i mutacje, także zlecane podumysłom, ruszają dopiero po
  `LEASE GRANT` na zajmowany zasób (zestaw, build, port, baza). Zlecenie podaje przydział albo zakaz
  takich biegów; testy punktowe na prywatnym TMPDIR idą bez dzierżawy ([wiele sesji](multi-session.md)).
- Nie zmieniaj plików objętych trwającą weryfikacją. Wstrzymaj zapisy na czas końcowych
  testów i review; zmiana testowanego stanu wymaga odświeżenia wyniku.
- Kolejność migracji oraz kontrakt backend/frontend ustal przed równoległą implementacją.
- Kilka **sesji** roboczych z dyspozytorem (nie podagentów jednej sesji): dzierżawy zasobów,
  PUSH SLOT, nadawanie identyfikatorów (np. SEC-*, ADR, scenariusze E2E) wyłącznie przez
  dyspozytora — patrz [wiele sesji](multi-session.md).
