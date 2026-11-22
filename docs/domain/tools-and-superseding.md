---
title: "Indeks narzędzi agentów i zastępowanie lokalnych narzędzi przez warstwę (`supersedes`, `tools/index.yaml`)"
tags: [skillsync, tools, supersedes, layers, route, doctor, retro]
paths: ['skillsync_workflow/tools_index.py', 'skillsync_workflow/tools_route.py', 'skillsync_workflow/tools_check.py', 'skillsync_workflow/supersedes.py', 'skillsync_workflow/layers.py', 'library/skills/workflow/references/tools.md', 'library/skills/workflow/references/knowledge.md', 'tests/test_skillsync_tools*.py', 'tests/test_skillsync_supersedes*.py', 'tests/test_skillsync_route_tools.py']
source: "spec i plan CORE-1140-SUP-TOOLS (22.11.2026, decyzja „całość w 1.14.0”); kod i testy jak w polu paths"
verified: 2026-11-22
---
Reguła: dwa mechanizmy, oba addytywne (brak kluczy i plików = zachowanie 1.13.0, koszt zerowy).

**A. `supersedes` w `layer.yaml`.** Warstwa deklaruje, że jej narzędzie zastępuje lokalne pliki projektu. `doctor` ostrzega `superseded` (wersja warstwy co najmniej
`since` i plik pasuje do `matches`); `superseded_keep: {id: powód}` w manifeście zostawia lokalne narzędzie celowo; `layers update`/`pull` mówią jedną linią,
ile wpisów zadeklarowała sama aktualizacja (wersje czytane z `layer.yaml` przed i po, przedział `(przed, po]`). Skan plików: lista z gita (`--cached --others
--exclude-standard`) albo przejście bez dowiązań, najwyżej 20 000 plików (przy ucięciu ostrzeżenie `superseded-scan-truncated`), jeden skan na `doctor`, tylko gdy
jakiś wpis obowiązuje. Dopasowanie to `fnmatch` na nazwach (`*` przekracza `/`, `**/x` pasuje też do `x` w korzeniu), nigdy `Path.glob`.

**B. Indeks narzędzi.** `tools/index.yaml` (warstwa; agregat `tools-index`) i `docs/tools/index.yaml` (projekt; osobna przestrzeń `id`, poza agregatem). Schemat 1:
`id`, `path`, `purpose`, `topics`, `usage` wymagane; `files`, `selftest`, `limits`, `status` (`active|candidate|superseded|removed`), `supersedes`, `origin`
opcjonalne. Limity: plik 256 KB, 500 rekordów, teksty 2000 znaków, ścieżka 300. `route` podsuwa rekordy (sekcja `tools`), `doctor` i `layers check` kontrolują
schemat, ścieżki i nagłówki skryptów (etykiety `Purpose`, `Usage`, `Inputs`, `Outputs`, `Exit codes`, `Dependencies`, `Limits`, `Security`). Retro ma właściciela `tool`
i klucze przyczyn `tool-duplication`, `tool-candidate`.

Dlaczego: podumysły pisały jednorazowe skrypty, bo nie miały jak znaleźć istniejących, a lokalne kopie narzędzi nie miały sygnału, że warstwa dostarcza zamiennik.

Zasady bezpieczeństwa (nie do poluzowania bez nowej decyzji):
- Teksty z warstwy (`replacement`, `parity`, `migration`, rekordy narzędzi) są DANYMI: czyszczone ze znaków sterujących, ANSI, bidi i zero-width, cięte, cytowane
  i oznaczane warstwą; generator niczego z nich nie uruchamia, `selftest` i `parity` uruchamia agent według procedury (core bez pytania, org i personal po
  potwierdzeniu użytkownika).
- Błędny wpis albo indeks nigdy nie blokuje warstwy, `route` ani `doctor`: jest pomijany z ostrzeżeniem (`supersedes-invalid`, `tools-index-invalid`).
- Link zabroniony w warstwie na całej drodze do `tools/index.yaml` i skryptu (D73); w projekcie `docs/` i `docs/tools` nie mogą być linkami.
- `tools:<id>` to element chroniony: warstwa `org` zacieniająca albo ukrywająca rekord rdzenia potrzebuje `allow_override`.
- Rekordy warstwy `personal` mają w `route` `local: true` i nie wchodzą do dokumentów projektu ani rdzenia.

Cztery znaczenia „superseded”: status speca, klucz `supersedes` w `layer.yaml` (pliki projektu), klucz `supersedes` w rekordzie narzędzia (id narzędzi niższych warstw),
`status: superseded` rekordu (ręczne oznaczenie). Procedury agenta: `library/skills/workflow/references/knowledge.md` (zastąpienie) i `tools.md` (szukanie, tworzenie, awans).
