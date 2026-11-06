# Własna warstwa: organizacja albo osobista

Warstwa to repo git z układem `skills/`, `instructions/`, `agents/`, `workflow/` i plikiem `layer.yaml`.
Warstwa **org** niesie wiedzę jednej organizacji; warstwa **personal** niesie Twoje nawyki, opcjonalną personę agenta
i własne narzędzia. Gotowy przykład: [examples/personal-layer/](examples/personal-layer/).
Jak warstwy składają się w projekcie i jaka jest semantyka scalania: [architecture](architecture.md).
Podpinanie warstw na maszynie: [configuration](configuration.md).

## 1. Załóż repo

```bash
skillsync layers init ~/Repos/moje-skillsync --kind personal --id moja   # albo --kind org
git -C ~/Repos/moje-skillsync add -A
git -C ~/Repos/moje-skillsync commit -m "Szkielet warstwy"
```

Komenda tworzy repo git z hookami strażnika, ale bez commita. `layers add` (§4) klonuje repo, więc szkielet trzeba
najpierw zatwierdzić. Opcje: `--id ID` (domyślnie nazwa katalogu; `id` musi być tym samym, które podasz w `layers add`),
`--json`.

`layers init` tworzy:

- `layer.yaml` (`version: 0.1.0`, `core_min` równe wersji generatora);
- `README.md` i `CHANGELOG.md`;
- `instructions/_base.d/10-persona.md` (bez persony, z komentarzem-podpowiedzią);
- `leakguard/identity.yaml` (`schema: 1`, `layer: <id>`, `terms: []`);
- `.gitignore`;
- repo git z hookami `pre-commit` i `pre-push`;
- wpis `leakguard.targets.<id>` w configu maszyny.

### Bez symlinków

Repo warstwy nie może zawierać symlinków, junctions ani reparse points. Zakaz obejmuje też ścieżkę do `subdir` warstwy
oraz pliki `layer.yaml` i `.leakguard/patterns.yaml`. W takim repo `skillsync layers check` zgłasza błąd
`symlink-in-layer`, a `leakcheck` kończy się BLOCKED. Symlinki, które generator sam tworzy w projektach i w cache,
są w porządku.

Znane ograniczenie: placeholdery chmury (OneDrive „pliki na żądanie”, iCloud Drive z optymalizacją miejsca) i pliki
deduplikacji mają atrybut reparse point i zostaną odrzucone. Nie trzymaj checkoutu warstwy w folderze synchronizowanym
w trybie „na żądanie”.

## 2. `layer.yaml`

```yaml
schema: 1
id: moja
kind: personal          # org | personal
version: 0.1.0
core_min: X.Y.Z         # minimalna wersja rdzenia
requires: []            # inne warstwy, których potrzebujesz
identity: leakguard/identity.yaml   # tylko warstwy prywatne
```

- `version` i `core_min` mają format `X.Y.Z`. `core_min` jest porównywane z wersją generatora.
- `layers check` zwraca BLOCKED (2), gdy brakuje `version` lub `core_min`, gdy `requires` albo `core_min` nie są spełnione,
  albo gdy zależności tworzą cykl.
- `layers check` ostrzega o braku `README.md`, `CHANGELOG.md` albo `version` w warstwie.

Pola `layer.yaml`: `schema` (wymagane, `1`), `id` (`[a-z0-9][a-z0-9-]*`), `kind` (`core`, `org` albo `personal`), `version`,
`core_min` (SemVer), `requires` (lista `id`), `identity` (ścieżka względna, domyślnie `leakguard/identity.yaml`). Nieznane
klucze są ignorowane. Brak `layer.yaml` daje BLOCKED.

Kody `layers check`:

| Poziom | Kody |
|---|---|
| BLOCKED (2) | `core-missing`, `layer-yaml-invalid`, `layer-yaml-missing`, `id-mismatch`, `kind-mismatch`, `version-missing`, `core-min-missing`, `core-min-unmet`, `requires-unmet`, `requires-cycle` |
| FAIL (1) | `symlink-in-layer`, `hook-missing`, `identity-invalid`, `case-collision`, `override-without-consent`, `merge-invalid`, `alias-collision` |
| ostrzeżenie | `readme-missing`, `changelog-missing`, `identity-layer-mismatch`, `core-version-mismatch`, `hook-legacy`, `instruction-collision`, `orphans` |
| informacja | `layer-absent`, `isolation-layer-absent` |

`doctor` zamienia kody BLOCKED na błędy, pozostałe na ostrzeżenia. Manifest z `layers` lub `layer_rules` daje błąd `doctor`.

### Tożsamość (tylko warstwy prywatne)

Plik `leakguard/identity.yaml` opisuje terminy, których nie może zawierać repo publiczne: nazwy organizacji, projektów,
osób i domen. Format jest zapisany w [security](security.md). Plik nie trafia do repo publicznego i nie jest składany
z innymi warstwami.

Format `identity.yaml`: `schema: 1`, `layer: <id>`, `terms: []`. Każdy termin ma pola `term`, `kind`
(`org`, `project`, `domain`, `person`, `domain-term`, `machine`, `owner-nick`, `owner-name`, `persona`, `publisher`), `case`
(`insensitive` domyślnie albo `sensitive`), `allow_in` (domyślnie `[layer]`), `path_classes` (podzbiór `instrukcje`, `atrybucje`,
`inne`) i `match` (`part` domyślnie albo `whole`).

## 3. Co można dodać

| Element | Gdzie | Uwagi |
|---|---|---|
| nowy skill | `skills/<nazwa>/SKILL.md` | nazwa nie może kolidować z rdzeniem bez `allow_override` (org) |
| fragment instrukcji bazowych | `instructions/_base.d/NN-nazwa.md` | sumowane w kolejności warstw, potem nazw plików |
| szablon instrukcji | `instructions/<nazwa>.md` | późniejsza warstwa wygrywa |
| rola agenta | `agents/<nazwa>.md` | |
| ustawienia workflow | `workflow/defaults.yaml`, `workflow/projects/*.yaml` | zasady scalania: [architecture](architecture.md) |
| persona i imiona kart (tylko personal) | `instructions/_base.d/10-persona.md`, nakładka imion | |

Zasady treści:

- persona agenta i imiona kart należą wyłącznie do warstwy personal; warstwy core i org ich nie zawierają;
- warstwa org niesie treść jednej organizacji; wiedza wspólna kilku organizacji trafia do warstwy core tylko wtedy,
  gdy jest ogólna i neutralna;
- warstwa personal nie zawiera nazw klientów ani ich projektów;
- każda warstwa ma `README.md`, `CHANGELOG.md` i `version` w `layer.yaml`.

Kolejność fragmentów `_base.d`: fragmenty `00-`…`09-` idą przed tekstem bazowym rdzenia, pozostałe po nim, a wszystkie przed
znacznikiem `SKILLSYNC:BASE-END`. Każdy fragment jest w znacznikach `SKILLSYNC:FRAGMENT begin/end <warstwa>/<plik>`.

Plik `imiona.md` (w skillu `dispatch` w warstwie personal) nie jest czytany przez kod. Czyta go agent; nakładka działa per plik
w kompozycie skilla.

## 4. Podłącz warstwę

```bash
skillsync layers add moja --kind personal --source ~/Repos/moje-skillsync
skillsync layers use moja --match "**"
skillsync layers check
```

`layers add` klonuje warstwę, rejestruje ją w configu, instaluje hooki `pre-commit` i `pre-push` ze strażnikiem wycieków
w checkoucie (bez nadpisywania istniejących hooków) i dopisuje wpis celu strażnika. Nowa warstwa bez tożsamości ma ostrzeżenie `coverage: generic`.
`layers add` nie tworzy reguły `**`: tę dodaje `layers use`.

Strażnik na katalogu, w którym edytujesz warstwę, wymaga jawnego celu: `skillsync leakcheck ~/Repos/moje-skillsync --target moja`.

`link` czyta checkout warstwy (`~/.local/share/skillsync/layers/<id>`), a nie katalog, w którym edytujesz. Po każdej
zmianie: commit, `skillsync layers update moja`, potem `skillsync link <projekt>`.

## 5. Kilka organizacji

Każda organizacja to osobna warstwa i osobna reguła `match` na jej katalog projektów. Nie łącz treści dwóch organizacji
w jednej warstwie. Zobacz [configuration](configuration.md) i [security](security.md).

## 6. Wersje i aktualizacja

Podbijaj `version` zgodnie z tabelą poniżej, taguj `vX.Y.Z` i prowadź `CHANGELOG.md`. Użytkownicy aktualizują warstwy
przez `skillsync pull` (albo `skillsync layers update`).

| Kiedy podbić | Warstwa ze skillami (org, personal) |
|---|---|
| **MAJOR** | usunięcie lub zmiana nazwy skilla, szablonu instrukcji albo roli; zmiana ścieżki pliku nadpisywanego przez inne warstwy; zmiana `id` elementów scalanych; zmiana semantyki skilla łamiąca istniejące manifesty; podniesienie `core_min` o major |
| **MINOR** | nowy skill, szablon, referencja albo fragment; podniesienie `core_min` o minor; rozszerzenie skilla bez zmiany istniejącego zachowania |
| **PATCH** | poprawki tekstu, aktualizacja danych (osoby, kontakty, adresy), literówki |

Podbicie major wymaga wpisu **Breaking** w `CHANGELOG.md` warstwy. Wersje rdzenia i jego zasady: [upgrading](upgrading.md).

## 7. Kontakt i autorstwo

Warstwa org może podawać osobę kontaktową w `README.md`. Strażnik dopuszcza imię i nazwisko w warstwie org, a w rdzeniu
i w warstwie personal nie. Adresy autorów commitów muszą należeć do listy celu w configu.

## 8. Przeniesienie zmian z projektu do warstwy

`skillsync push [ścieżka] [--dry-run] [--to-layer ID]` przenosi lokalne zmiany skilli i instrukcji do właściwej warstwy,
po `leakcheck` celu. Zapis do właściciela; nowe pliki trafiają do warstwy wskazanej przez `--to-layer` (domyślnie personal).
Zapis do `core` wymaga `--to-layer core`.

Przy wielu warstwach `push` zapisuje każdy zmieniony plik do warstwy, z której ten plik pochodzi w bieżącym zestawie warstw.
Pliki warstwy `core` bez `--to-layer core` są pomijane z `[info]`. Warstwa z `writable: false` (także `core` z
`--to-layer core`, bo `core` jest domyślnie tylko do odczytu) albo na odłączonym HEAD daje BLOCKED; wtedy `push` nie
zapisuje niczego w tym projekcie.
