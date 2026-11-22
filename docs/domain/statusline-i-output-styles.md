---
title: "Pasek statusu sesji (statusLine) i style odpowiedzi (output-styles) w Claude Code: fakty z wersjami, opt-in, granice"
tags: [skillsync, claude-code, statusline, output-styles, settings, manifest, opt-in]
paths: ['library/hooks/statusline.py', 'library/output-styles/**', 'skillsync_workflow/hooksync.py', 'skillsync_workflow/claude_manifest.py', 'skillsync_workflow/cli.py', 'tests/test_skillsync_statusline*.py', 'tests/test_skillsync_output_styles.py', 'tests/test_skillsync_claude_manifest.py']
source: 'Fakty z dokumentacji i changelogu Claude Code (odczyt 2026-11-21, bez wywołania modelu; URL-e niżej); decyzje właściciela 2026-11-21 (opt-in, sesja bez tożsamości); kod: library/hooks/statusline.py, skillsync_workflow/hooksync.py (sync_statusline, bar_blockers), skillsync_workflow/cli.py (build_claude_output_styles), skillsync_workflow/claude_manifest.py'
verified: 2026-11-21
---
Reguła: pasek statusu i styl odpowiedzi są **opcjonalne i domyślnie wyłączone**. Włącza je wyłącznie
manifest projektu: `claude.statusline: true` i `claude.output_styles: true`. Bez tych kluczy `link` nie
zapisuje ani nie czyta niczego nowego (zachowanie jak przed wprowadzeniem). Generator nigdy nie nadpisuje
cudzego paska i nigdy nie ustawia `outputStyle`; wybór stylu zostaje przy użytkowniku.

## Fakty z dokumentacji Claude Code

Źródła (odczyt 2026-11-21, cytaty z podsumowań, nie dosłowne): statusline.md, output-styles.md,
settings.md, settings-reference.md (w `code.claude.com/docs/en/`) oraz CHANGELOG.md w repozytorium
`anthropics/claude-code` (gałąź main).

| Fakt | Wersja Claude Code |
|---|---|
| `statusLine` jako `{type: "command", command, padding?, refreshInterval?}`; komenda dostaje JSON na stdin; wynik widoczny tylko przy kodzie 0, inaczej pusty pasek; stderr w `claude --debug`; każda linia wyjścia to wiersz; ANSI i OSC 8 | 1.0.71 |
| `cost`, `exceeds_200k_tokens` w JSON | 1.0.85, 1.0.88 |
| `worktree.{name,path,branch,original_cwd,original_branch}` w JSON (tylko sesja uruchomiona z `--worktree`) | 2.1.69 |
| `workspace.added_dirs` | 2.1.47 |
| `refreshInterval` (sekundy, minimum 1) | 2.1.97 |
| `workspace.git_worktree` | 2.1.97 / 2.1.98 |
| `session_name`: nazwa ustawiona przez `--name` albo `/rename`; gdy jej nie ma, tytuł wygenerowany przez AI; domyślna nazwa wyświetlana (np. `my-app-3f`) nie trafia do pola; pole opcjonalne | brak daty w źródle |
| `output-styles` (katalogi `.claude/output-styles`, `~/.claude/output-styles`) | 1.0.81 |
| `keep-coding-instructions` (domyślnie `false`; działa tylko z pełnym promptem systemowym) | 2.0.37 |
| `/output-style <nazwa>` | 2.1.269 |
| picker stylów w VS Code | 2.1.257 |
| zmiana `outputStyle` działa od następnej wiadomości | 2.1.251 |

Pozostałe fakty z tej samej dokumentacji:
- odświeżanie paska: debounce 300 ms; wyzwalacze: start sesji, nowa wiadomość asystenta, koniec `/compact`,
  zmiana trybu uprawnień, `refreshInterval`; nowy wyzwalacz anuluje działający skrypt;
- **limit czasu komendy paska nie jest udokumentowany**;
- pasek działa dopiero po zaakceptowaniu workspace trust;
- `disableAllHooks` i `allowManagedHooksOnly` wyłączają pasek (poza ustawieniami managed);
- klucz `statusLine` ma zakres „any file”; priorytet: managed > `--settings` > `.claude/settings.local.json`
  > `.claude/settings.json` > `~/.claude/settings.json` (wnioskowane: cały obiekt wyższego poziomu zastępuje niższy);
- niestandardowy pasek chowa podpowiedzi stopki; powiadomienia dzielą wiersz i na wąskim terminalu skracają
  pasek (`COLUMNS`, `LINES` w środowisku od 2.1.153);
- pliki stylów są czytane przy starcie sesji; sam plik stylu niczego nie aktywuje (wybór: `/output-style`,
  `/config`, klucz `outputStyle`; wartość rozróżnia wielkość liter, komenda `/output-style` nie);
- błąd YAML we frontmatterze stylu = styl ładuje się bez pól (czyli z `keep-coding-instructions: false`);
- komenda i menu zapisują `outputStyle` do `.claude/settings.local.json`;
- zmiany ustawień są przeładowywane bez restartu (wnioskowane).

**Nierozstrzygnięte w dokumentacji** (nie twierdzić inaczej bez próby na żywo): czy pasek renderuje się
w rozszerzeniu VS Code i w trybie `-p`; limit czasu komendy; działanie dowiązań symbolicznych w `.claude/output-styles`.

## Co robi generator

- **Pasek** (`library/hooks/statusline.py`, kopia w `.claude/hooks/statusline.py`): jedna linia, tylko biblioteka
  standardowa, bez procesów (bez `git`), bez sieci, kod wyjścia zawsze 0. Sekcje (każda pomijana, gdy brak danych):
  `sesja: <session_name>` (bez twierdzenia o tożsamości), nazwa katalogu projektu, gałąź i worktree, tryb
  dostarczania (`pr`/`direct` z manifestu, czytany liniowo), `plan <slug> N/M` dla najnowszego planu w statusie
  `in_progress`. Wszystkie pola są oczyszczane z sekwencji sterujących, a długość jest ograniczona (szerokość
  `min(120, COLUMNS)`, `NO_COLOR` wyłącza kolory).
- Plany: czytane są najwyżej 200 plików `docs/plans/*.md`, wybranych po nazwie malejąco (nie po dacie); pliki
  większe niż 512 KiB, symlinki i `README.md` są pomijane. Spośród planów w `in_progress` wybierany jest ten
  o najnowszym czasie modyfikacji. Postęp to zaznaczone pozycje sekcji `## Progress` (przy jej braku `## Kroki`
  albo `## Steps`); plan bez pozycji pokazuje samą nazwę bez `N/M`.
- **Wpis** w `.claude/settings.local.json` (plik ignorowany przez git, lokalny dla worktree):
  `{"type": "command", "command": "<interpreter generatora> <ścieżka bezwzględna>/.claude/hooks/statusline.py", "refreshInterval": 15}`.
  Komenda nie zawiera powłoki ani `git`.
- **Rozpoznanie „naszego” wpisu**: komenda wskazuje dokładnie `<projekt>/.claude/hooks/statusline.py` po `realpath`.
  Wpis z inną ścieżką (także globalny skrypt użytkownika) jest cudzy.
- **Cudzy pasek** (`statusLine` w `.claude/settings.local.json`, `.claude/settings.json`, `~/.claude/settings.json`
  albo w `$CLAUDE_CONFIG_DIR/settings.json`, gdy zmienna jest bezwzględna), `disableAllHooks: true` w którymkolwiek z tych
  plików albo nieczytelny plik ustawień: `link` nie zapisuje (`[warn]` z nazwą pliku i priorytetem). Po wcześniejszej
  instalacji taki stan usuwa nasz wpis i kopię skryptu, z ostrzeżeniem.
- **Styl** `library/output-styles/karta.md` (`name: karta`, `keep-coding-instructions: true`): instalowany jako
  zarządzana **kopia** w `.claude/output-styles/` (nie dowiązanie: dowiązania są nieudokumentowane). Styl nie definiuje
  własnego formatu: odsyła do gramatyki protokołu kart (`library/skills/feature/references/multi-session.md`), czyli pierwsza
  linia `date: <wynik date>`, potem `STATUS <paczka> <akt> <czeka-na>` (`<czeka-na>`: użytkownik, zasób, dyspozytor lub nic) albo
  `REPORT DONE|BLOCKED <paczka> commit=… checks=… debt=… retro=…`. Dopowiada formę: zwięzłość, dowody z komendą i kodem
  wyjścia, `NOT_RUN` wprost. Bez osobowości i tajemnic.
- **Usuwanie stylu**: przy `claude: false` albo `claude.output_styles: false` `link` usuwa wyłącznie
  `.claude/output-styles/karta.md`, i to tylko gdy plik jest nietknięty w stanie albo bajtowo zgodny z biblioteką i nie jest
  śledzony przez git. Cudze pliki i dowiązania w tym katalogu nigdy nie są ruszane; bez klucza `link` niczego tam nie czyta.
- **Cudze dowiązanie** pod ścieżką `karta.md` nie jest zastępowane (`[warn]`). Lokalnie zmieniona kopia zostaje, a `link`
  wypisuje zwykłe `[info]`. Push nie obsługuje stylów.
- `doctor` przy `claude.output_styles: true` podaje `info` o dostępnym stylu (`/output-style karta`); bez klucza nic nie zgłasza.
- Reguła scalania warstw dla `output-styles/` to `core-only` (jak `hooks/`); katalog jest objęty skanem zakazanej
  atrybucji (`ATTRIBUTION_DIRS`).

## Decyzje

- Opt-in w manifeście, nowe klucze domyślnie wyłączone; brak kluczy = zachowanie bez zmian.
- Skrypt paska bez procesów: limit czasu nie jest znany, a nowy wyzwalacz anuluje działający skrypt.
- Pasek pokazuje `session_name` jako „sesja: …” bez wnioskowania o tożsamości karty. Tożsamość sprawdza się
  według instrukcji (`AGENTS.md`), nie z paska.
- Styl `dyspozytor` odłożony; w pierwszym wydaniu tylko `karta`.
- Wyświetlanie paska w VS Code i w `-p` jest niepotwierdzone; dowód to ręczna próba (`docs/development.md`).

## Granice i pułapki

- `session_name` bywa tytułem wygenerowanym przez AI, nie nazwą nadaną przez użytkownika (zob. lekcję
  `session-name-w-pasku-to-tytul-ai`). Nie wolno z niego wnioskować, kim jest sesja.
- `CLAUDE_CONFIG_DIR` jest sprawdzany tylko wtedy, gdy jest ustawiony (bezwzględny) w procesie `link`.
  Usługa `skillsync watch` jej nie widzi, więc nie wykrywa cudzego paska w tym katalogu. Skutek: interaktywny `link`
  z `CLAUDE_CONFIG_DIR` wskazującym katalog z cudzym `statusLine` może usunąć nasz wpis i kopię (z `[warn]`); `watch`
  przy najbliższej zmianie w warstwach zainstaluje je ponownie, aż do kolejnego `link` w tej samej powłoce.
- Cytowanie komendy na Windows: `"<python>" "<skrypt>"` działa w cmd i Git Bash; w PowerShellu wymaga operatora `&`.
  **Nie sprawdzone** na natywnym Windows.
  Komenda paska zakłada więc powłokę POSIX (Git Bash, cmd); w PowerShellu `"py" "skrypt"` daje błąd parsowania.
- Edytowana kopia skryptu jest usuwana razem z naszym wpisem przy wyłączeniu. Kopia bajt w bajt z biblioteką
  jest usuwana także bez wpisu. Obcy plik o tej samej nazwie zostaje.
- Kopia stylu śledzona przez git nie jest usuwana przy wyłączeniu; zostaje do ręcznego usunięcia.
- Pasek działa dopiero po zaakceptowaniu workspace trust (dokumentacja Claude Code). Generator tego stanu nie sprawdza
  i nie ostrzega o nim.

## Gdzie w kodzie

`library/hooks/statusline.py` (`build_segments`, `current_plan`, `render`, `main`); `skillsync_workflow/hooksync.py`
(`merge_setting`, `bar_is_ours`, `bar_blockers`, `sync_statusline`, `_remove_bar`, `statusline_findings`, `BAR_VERSION_NOTE`);
`skillsync_workflow/cli.py` (`build_claude_output_styles`, `OUTPUT_STYLES`); `skillsync_workflow/claude_manifest.py`
(`enabled`, `statusline`, `output_styles`, `findings`); `skillsync_workflow/layers.py` (`merge_rule`, `ATTRIBUTION_DIRS`).
