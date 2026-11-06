---
title: "Skrypt mutacji bez limitu pamięci zjada sesję, a bieg zabity limitem wygląda jak mutant „przeżył”"
tags: [skillsync, tests, mutation, memory, systemd, evidence, agents]
paths: ['.skillsync/runs/**/mutate.py', '.skillsync/runs/**/mut/**', 'tests/test_skillsync*.py']
source: "docs/plans/2026-11-05-warstwy-generator.md, sekcja Wynik, krok 2.2at (naruszenie zasady limitu, zgłoszenie koordynatora przebiegu 2026-11-05 ok. 11:45Z; poprawka skryptu mutate.py ok. 11:50Z); sekcja TESTY (limity MemoryMax=4G dla pełnego zestawu) i sekcja Zasoby (ciężkie biegi pod systemd-run) tego samego planu; skrypt mutate.py kroku 2.2at (biegi owinięte w systemd-run, rc 124/137 jako ZABITY-LIMIT)"
verified: 2026-11-05
---
**Objaw:** biegi mutacji kroków 1.1t, 2.1t (r1–r9) i 2.2at (r1–r9 oraz bieżące) szły bez limitu pamięci, w scope VS Code
z `memory.max = max`. Zgłoszenie koordynatora przebiegu (2026-11-05 ok. 11:45Z) wykryło to dopiero po fakcie. Bieg przerwany limitem nie miał
odrębnego wyniku, więc mógł zostać policzony jako „przeżył”; dlatego wynik takich biegów nie jest dowodem.

**Przyczyna:** `mutate.py` wołał `subprocess.run` bez cgroup i bez `timeout`. Testy modułów orkiestratora szły pod
`systemd-run`, ale same skrypty mutacji nie. Gdy bieg zabijał limit (rc 124 po `timeout` albo 137 po SIGKILL), skrypt
traktował go jak zwykły wynik testu: brak porażki = „przeżył”. Taki wynik fałszywie zaniża skuteczność testów.

**Reguła:**
- Każdy bieg mutacji, także baseline, idzie przez `systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 timeout 300 <polecenie>`.
  Pełny zestaw testów: `systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 timeout 1800 …` (tylko z dzierżawą
  współdzielonego zasobu, zob. plan, sekcja TESTY).
- Bieg zabity limitem (rc 124 albo 137) zapisuj jako **`ZABITY-LIMIT`**, nigdy jako „przeżył” ani jako „zabity przez mutanta”.
  Taki wpis nie jest dowodem ani za, ani przeciw mutantowi; powtórz go z większym limitem albo z mniejszym zakresem.
- Równoległość w skrypcie mutacji: najwyżej 4 biegi naraz i tylko przy `load` poniżej 8.
- Wynik mutacji liczy się wyłącznie z biegu poprawionym skryptem. Biegi starym skryptem nie wchodzą do dowodu.
- Zlecenie dla test-writera i implementera mutacji przenosi tę samą regułę (nie tylko skrypt).

**Jak wykryć:**
- `grep -rn "subprocess.run" .skillsync/runs/**/mutate.py` — każde wywołanie testu bez `systemd-run` jest błędem.
- W logu mutacji: `grep -c ZABITY-LIMIT <log>`; wpisy rc 124/137 bez tego znacznika są błędem skryptu.
- `journalctl --user --since … --until …` pokazuje scope `systemd-run` z limitem; brak wpisów przy biegu = bieg bez limitu.
- Po zdarzeniu sprawdź, czy wynik mutanta ma w logu rc; bez rc wynik jest niewiarygodny.
