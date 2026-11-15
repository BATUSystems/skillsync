---
title: "Trzy czerwone obroty CI 1.4.1: macierz przed pushem (Python 3.11, Windows 3.13, macOS, check_skips na logach wszystkich systemów)"
tags: [skillsync, ci, windows, macos, python, tests, skips, process]
paths: ['tests/test_skillsync_runs_rooted.py', 'tests/test_skillsync_isabs.py', 'tests/skip-baseline.json', 'scripts/check_skips.py', 'skillsync_workflow/fsutil.py']
source: 'wydanie 1.4.1: PR #70 (Python 3.11), run e2c0189 (Windows 3.13, isabs), run 907bd78 (macOS, kategoria skipa)'
verified: 2026-11-15
---
**Objaw:** trzy kolejne obroty CI po lokalnie zielonych biegach. (1) Python 3.11: nowy test podmieniał `os.name` na `nt`, a `Path(text)` w `runs._is_rooted` próbował wtedy utworzyć `WindowsPath` (na 3.12+ nie): sześć błędów. (2) Windows z Pythonem 3.13: `ntpath.isabs('/x')` jest od 3.13 False (do 3.12 True), więc reguły warstw ze ścieżką od `/` przestały pasować i hook ze ścieżką `/…` nie był sprawdzany. (3) macOS: dopisane przeze mnie fragmenty kategorii skipów (`posix-only`) przechwyciły powód „Linux ownership backend” przed właściwą kategorią (`linux-only-env`), bo `check_skips` bierze pierwszą pasującą kategorię.
**Przyczyna:** biegi szły na interpreterze stacji (3.14) i na jednym systemie, a baseline skipów nie był sprawdzony na logach pozostałych systemów.
**Reguła:** przed pushem macierz z `library/skills/feature/references/prepush-matrix.md`: Linux na 3.11 i na interpreterze stacji, Windows 3.11/3.12/3.13 w środowisku jak runner (pusty profil, PATH bez `skillsync`), macOS dla zmian HTTP/plików/sygnałów, `check_skips` z baseline po zmianie na logach `-v` wszystkich systemów; nowe fragmenty kategorii skipów sprawdź na samospójność (każdy fragment klasyfikuje się do własnej kategorii). Ścieżki rootowane na Windows rozstrzyga `fsutil.isabs`, nie `os.path.isabs`.
**Dodatek (paczka D 1.5.0):** po zmianie funkcji produktu powtórz WSZYSTKIE moduły, które ją dotykają (grep po nazwie w `tests/`): strażnik AST przeciw nowym `os.path.isabs` wyszedł czerwony dopiero w przeglądzie, bo po zmianie hooków uruchomiłem tylko moduł hooków.
**Jak wykryć:** `python3 -m unittest discover -s tests -p test_skillsync_<moduł>.py` na `python3.11`; grep po `os.path.isabs` w produkcie; `scripts/check_skips.py <log> --system <os> --mode <tryb>` na logach CI (`gh api --allow-escape-sequences repos/<org>/<repo>/actions/jobs/<id>/logs`).

**Biegi na wspólnej maszynie:** procesy zatrzymuj tylko po własnym PID (albo własnej grupie lub scope `systemd-run`), nigdy `pkill -f` po wzorcu nazwy (zabija cudze biegi); równoległe biegi dostają rozłączne plasterki portów (`SKILLSYNC_TEST_DASHBOARD_PORTS`).

**Zakres macierzy:** pełna macierz idzie raz, na gałąź wydania; gałąź robocza dostaje dowód punktowy (zmienione moduły na Windows 3.11/3.12/3.13 i macOS dla HTTP, plików i sygnałów), bo maszyny są wspólne dla projektów.
