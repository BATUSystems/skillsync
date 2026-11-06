# Wiedza domenowa skillsync

Indeksowane rekordy z regułami generatora, których nie widać z samego kodu albo które łatwo zrozumieć źle (własność plików, rozwijanie skilli, rozwiązywanie ścieżek, semantyka statusów).
Czytaj przed zmianą zachowania generatora; `skillsync route . --topics domain` zwraca wszystkie rekordy, a po tagach/ścieżkach — pasujące. Format i zasady zapisu:
skill `workflow`, `library/skills/workflow/references/knowledge.md`; weryfikację frontmattera robi `skillsync doctor`.

## Indeks

| Rekord | Temat |
|---|---|
| [artifact-ownership.md](artifact-ownership.md) | które pliki generator może nadpisać/usunąć, a które zachowuje |
| [skill-expansion.md](skill-expansion.md) | rozwijanie `skills:` (alias, towarzysze, tagi, profile) i dwie implementacje |
| [config-resolution.md](config-resolution.md) | skąd generator bierze config, bibliotekę i listę projektów |
| [generated-instructions.md](generated-instructions.md) | skąd się biorą `CLAUDE.md`/`AGENTS.md`/`copilot-instructions.md` i jak działa `push` |
| [statuses-and-exit-codes.md](statuses-and-exit-codes.md) | PASS/FAIL/BLOCKED/NOT_RUN, kody wyjścia, semantyka `doctor` |
| [state-and-runtime-dirs.md](state-and-runtime-dirs.md) | `.github/.skillsync-state.json` i `.skillsync/` |
| [dispatch-manifest-section.md](dispatch-manifest-section.md) | sekcja `dispatch:` — kto ją czyta i co waliduje `doctor` |
| [bootstrap-glossary.md](bootstrap-glossary.md) | rekord `load: bootstrap`: glosariusz ładowany przed interpretacją zadania |
| [run-classes.md](run-classes.md) | klasy przebiegów (`no_data`…`clean`, `unreadable`), `final`, retrospektywa przebiegu, kandydaci zmian `retro` |
| [retro-proposals.md](retro-proposals.md) | propozycje retrospektywy: rejestr `proposals:`, stany, kolejność, skan progu, maskowanie między projektami |
| [prototype-manifest-and-cli.md](prototype-manifest-and-cli.md) | sekcja `prototype:` i komenda `skillsync prototype` (zapis w repo, komentarze, tokeny) |
| [prototype-comments-server.md](prototype-comments-server.md) | wspólne komentarze prototypów: serwer, dziennik `comments.json` v2, fold zgodny z JS, użytkownicy, sesje, odczyt publiczny, iteracje, przywracanie, decyzje agenta, scalanie, akceptacja i bramka prototypu, słownik |

Maszynowy indeks: [index.yaml](index.yaml) (`version: 1`, klucz `entries`; `paths` względne do korzenia repo).

## Jak dodać rekord

1. Najpierw sprawdź, czy istniejący rekord nie wymaga poprawki — duplikat zamiast poprawki to błąd.
2. Plik `docs/domain/<temat>.md` z frontmatterem `title`, `tags` (lista), `paths` (lista globów względem korzenia repo), `source` (kod/ticket/decyzja), `verified` (data).
3. Treść: reguła, dlaczego, wyjątki i przykłady, gdzie w kodzie (`plik:funkcja`). Tylko fakty potwierdzone; hipotezy oznacz jako hipotezy; bez sekretów.
4. Dopisz wpis do `index.yaml` i do tabeli powyżej. Potwierdzone awarie techniczne z regułą zapobiegawczą idą do [../lessons/](../lessons/index.yaml), nie tutaj.
