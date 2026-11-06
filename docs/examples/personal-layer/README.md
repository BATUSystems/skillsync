# Przykład: warstwa personal

Kompletny, minimalny przykład własnej warstwy `personal` dla skillsync. Warstwa daje Ci:

- **personę agenta**: imię, ton, język odpowiedzi i sposób zwracania się do Ciebie,
- **nawyki pracy**: stałe zasady, których agent ma się trzymać w każdym projekcie,
- **imiona kart podagentów** (podumysłów): aliasy, którymi dyspozytor nazywa karty robocze.

Wszystkie dane w przykładzie są fikcyjne: persona nazywa się „Asystent”, właściciel to „Alex”.

Warstwa nie zmienia rdzenia skillsync. Dokłada fragmenty instrukcji i nakładki, które rdzeń składa w pliki instrukcji projektu.

## Układ katalogu

| Ścieżka | Po co | Wymagane |
|---|---|---|
| `layer.yaml` | identyfikator, rodzaj (`personal`), wersja, minimalna wersja rdzenia, plik tożsamości | tak |
| `CHANGELOG.md` | historia wersji warstwy | tak |
| `README.md` | ten opis (w Twojej warstwie możesz go skrócić) | tak |
| `instructions/_base.d/10-persona.md` | persona: imię, ton, język, zwracanie się do użytkownika | nie (bez niej rdzeń pracuje w trybie neutralnym) |
| `instructions/_base.d/20-habits.md` | nawyki pracy | nie |
| `skills/dispatch/references/imiona.md` | nakładka imion kart | nie (bez niej karty mają aliasy `karta-1`, `karta-2`…) |
| `skills/dispatch/references/personas/` | opisy charakterów kart: `README.md` i jeden plik na kartę | nie |
| `leakguard/identity.yaml` | ślady właściciela dla strażnika `leakcheck` | zalecane |
| `.gitignore` | pliki lokalne, których nie wersjonujesz | nie |

Skilla `dispatch` (`skills/dispatch/SKILL.md`) w tej warstwie nie ma. Pochodzi z rdzenia. Warstwa dokłada tylko pliki w `references/`, a pliki skilli warstw składają się z rdzeniowym skillem per plik.

## Jak skopiować przykład i dostosować

1. Skopiuj katalog przykładu do nowego katalogu poza repo rdzenia:

   ```bash
   cp -r <ścieżka-do-skillsync>/docs/examples/personal-layer ~/Repos/moja-warstwa
   ```

2. Załóż repozytorium git i hooki strażnika w katalogu, w którym będziesz pracować:

   ```bash
   cd ~/Repos/moja-warstwa
   git init
   skillsync leakguard install-hook ~/Repos/moja-warstwa --target moja   # moja = id warstwy z kroku 3
   ```

3. Zmień `id` w `layer.yaml` (np. `moja`) i w `leakguard/identity.yaml` pole `layer:` na to samo `id`. Inaczej strażnik uzna własne terminy za obce i zablokuje. Wartość `kind` zostaw jako `personal`. `core_min` ustaw na wersję swojego generatora (pole `generator_version` w `skillsync layers list --json`; `layers init` wpisuje ją sam): wyższa niż wersja generatora daje `BLOCKED` (`core-min-unmet`).
4. Podmień terminy w `leakguard/identity.yaml` na własne ślady (nick, imię, nazwy maszyn). Nie wpisuj nazw organizacji (patrz „Prywatność”).
5. Napisz personę w `instructions/_base.d/10-persona.md` i nawyki w `20-habits.md`.
6. Opcjonalnie nadaj imiona kartom w `skills/dispatch/references/imiona.md`.
7. Zatwierdź zmiany. `layers add` klonuje commit, więc niezatwierdzone zmiany z kroków 3–6 nie trafią do warstwy:

   ```bash
   git -C ~/Repos/moja-warstwa add -A
   git -C ~/Repos/moja-warstwa commit -m "init: warstwa personal"
   ```

   Hook ostrzega wtedy, że cel `moja` jest nieznany (`coverage generic`), a commit przechodzi. To oczekiwane: cel zna
   config dopiero po `layers add` w kroku 8.

8. Podłącz warstwę i sprawdź ją (warstwa `core` musi być już podłączona: [szybki start](../../../README.pl.md#szybki-start)):

   ```bash
   skillsync layers add moja --kind personal --source ~/Repos/moja-warstwa
   skillsync layers use moja --match '**'
   skillsync layers check
   ```

9. Sprawdź wyciek, zawsze z jawnym celem (w katalogu źródłowym cel nie jest wykrywany sam):

   ```bash
   skillsync leakcheck ~/Repos/moja-warstwa --target moja
   ```

10. Zlinkuj projekt:

    ```bash
    skillsync link --dry-run <projekt>
    skillsync link <projekt>
    ```

`layers add` przyjmuje też adres URL repozytorium. Zamiast kopiować przykład, możesz od zera założyć szkielet: `skillsync layers init <katalog> --kind personal --id <id>` (potem commit, jak w kroku 7).

### Po każdej zmianie warstwy

`link` czyta checkout warstwy (`~/.local/share/skillsync/layers/<id>`), a nie katalog, w którym edytujesz. Po zmianie:

```bash
git -C ~/Repos/moja-warstwa add -A
git -C ~/Repos/moja-warstwa commit -m "persona: …"
skillsync layers update moja
skillsync link <projekt>
```

### Co tworzy `layers init`

`skillsync layers init <katalog> --kind personal --id <id>` tworzy:

- `layer.yaml` (`version` 0.1.0, `core_min` = wersja generatora),
- `README.md`, `CHANGELOG.md`, `.gitignore`,
- `instructions/_base.d/10-persona.md` (bez persony, z komentarzem-podpowiedzią),
- `leakguard/identity.yaml` (`schema` 1, `layer`, `terms: []`).

Dodatkowo wykonuje `git init`, instaluje hooki `pre-commit` i `pre-push` i dopisuje wpis `leakguard.targets.<id>` w configu.

W porównaniu z tym przykładem szkielet nie zawiera `20-habits.md`, `skills/dispatch/references/` (także `imiona.md` i `personas/`) i wypełnionej persony. Te elementy dodajesz ręcznie.

### Schemat `layer.yaml`

- wymagane: `schema` (= 1), `id` (`[a-z0-9][a-z0-9-]*`), `kind` (`core`, `org` albo `personal`),
- `version` i `core_min` (SemVer), `requires` (lista id), `identity` (ścieżka względna, domyślnie `leakguard/identity.yaml`),
- nieznane klucze są ignorowane,
- brak `version` lub `core_min` daje `BLOCKED`; brak pliku daje `BLOCKED` (`layer-yaml-missing`).

### Gdzie trafiają fragmenty

Pliki `instructions/_base.d/NN-nazwa.md` składają się w instrukcje projektu w tej kolejności:

- fragmenty `00-`…`09-` trafiają **przed** tekst bazowy rdzenia,
- pozostałe (np. `10-` i `20-`) trafiają **po** tekście bazowym,
- wszystkie są przed znacznikiem końca bazy,
- przy kilku warstwach najpierw idą warstwy w kolejności z `layers use`, potem nazwa pliku,
- każdy fragment jest otoczony znacznikami `<!-- SKILLSYNC:FRAGMENT begin <warstwa>/<plik> -->` … `end`.

Gdzie zobaczysz personę w projekcie:

| Tryb instrukcji (`instructions.entrypoint`) | Claude Code | Codex | GitHub Copilot |
|---|---|---|---|
| `inline` (domyślny) | `CLAUDE.md` | `.codex/skillsync-instructions.md` (`AGENTS.md` tylko wskazuje Codexowi ten plik) | `.github/copilot-instructions.md`, gdy manifest ma `instructions.template` |
| `agents` | `CLAUDE.md` importuje `AGENTS.md` | `AGENTS.md` | jak wyżej |

Do `.github/copilot-instructions.md` persona trafia tylko wtedy, gdy `instructions.merge` zawiera `_base`. Tryb `agents` wraca do `inline`, gdy `AGENTS.md` jest dowiązaniem albo istnieje i jest współdzielony (w repo git: nie jest ignorowany; poza gitem: ma własną treść).

Nie edytuj ręcznie `CLAUDE.md`, `AGENTS.md` ani `.github/copilot-instructions.md` w projekcie: to pliki generowane. Zmieniaj fragment w warstwie. `skillsync push` zapisuje zmiany bloku z powrotem do pliku fragmentu w jego warstwie.

## Jak stworzyć własną personę

Persona to krótki fragment (5–10 linii), bo trafia do instrukcji każdego projektu. Zwróć uwagę na:

- **Imię.** Jedno, krótkie, podane wprost w pierwszym zdaniu. Unikaj imion znanych postaci i istniejących person, żeby agent nie mylił się z innymi personami.
- **Ton.** Jedno lub dwa zdania, np. „rzeczowy i życzliwy”.
- **Język odpowiedzi.** Wskaż jeden język, np. polski. Osobno zapisz język kodu i commitów (w `20-habits.md`).
- **Zwracanie się do użytkownika.** Po imieniu albo „Ty”. Przykład: „Zwracaj się do użytkownika po imieniu z konfiguracji albo mów „Ty”.”
- **Granice.** Napisz wprost, że reguły bezpieczeństwa i poprawności są ważniejsze niż styl. Persona nie może nakazać pominięcia testów, ukrycia błędu ani zmyślenia wyniku.

Przykładowa treść `10-persona.md` (fragment z przykładu):

```markdown
## Persona

- Nazywasz się **Asystent**.
- Zwracaj się do użytkownika po imieniu z konfiguracji albo mów „Ty”.
- Ton: rzeczowy i życzliwy.
- Odpowiadaj po polsku.
- Poprawność i szczerość są ważniejsze niż styl.
```

Gdy nie chcesz persony, usuń `10-persona.md`. Rdzeń wtedy pracuje w trybie neutralnym.

## Imiona kart

Dyspozytor nadaje kartom roboczym aliasy. Bez nakładki używa aliasów `karta-1`, `karta-2`…. Nakładka `skills/dispatch/references/imiona.md` w Twojej warstwie zastępuje te aliasy Twoimi imionami.

Format pliku (struktura, przykład z tej warstwy ma treść fikcyjną):

- nagłówek,
- akapit „Dyspozytor nadaje kartom roboczym aliasy z tej listy”,
- podpis wiadomości w postaci `<Alias>/<paczka>`,
- lista imion z rodzajem gramatycznym (potrzebnym do poprawnej odmiany w polskim tekście),
- sekcja „Zasady”: kolejność nadawania, format aliasu, zachowanie po wyczerpaniu listy, pierwszeństwo nazw nadanych przez użytkownika.

Zasady z sekcji „Zasady” w skrócie:

- alias jest ASCII bez spacji i pasuje do wzorca `[A-Za-z0-9][A-Za-z0-9_.-]{0,79}`,
- imiona nadawane są w kolejności listy,
- po wyczerpaniu listy kolejne karty dostają `<Imię>-2`,
- alias nie wraca w obrębie jednego przebiegu,
- nazwy nadane przez użytkownika mają pierwszeństwo przed listą.

Opisy charakterów kart są opcjonalne. Leżą w `skills/dispatch/references/personas/`: `README.md` zawiera tabelę `alias | rodzaj gramatyczny | plik | rola`, a każdy plik opisuje jedną kartę. Przykład: `personas/Ada.md`.

Imię użytkownika możesz wstawić zmienną: każdy klucz z `variables` w configu maszyny (albo z `instructions.variables`
w manifeście, który wygrywa) zastępuje w instrukcjach `{{ klucz }}`, np. `{{ username }}`. Zmienna bez wartości zostaje
w tekście dosłownie, więc przykładowa persona mówi „Ty” i zmiennej nie używa.

## Nawyki

`20-habits.md` zawiera zasady pracy, które nie są personą: kiedy commitować, kiedy pytać, jak planować większe zmiany. Trzy przykładowe nawyki w tym pliku są punktem wyjścia. Zamień je na własne i trzymaj krótko.

## Prywatność

- **Repo warstwy jest prywatne.** Ustaw prywatność na hostingu. Nie dodawaj pliku `LICENSE`.
- **Nie wpisuj nazw organizacji ani klientów**, ich projektów ani tożsamości. Należą do warstw `org`. Warstwa personal może zawierać nick, imię i nazwisko właściciela oraz personę i imiona kart.
- **Nie wpisuj sekretów**: tokenów, haseł, kluczy ani danych uwierzytelniających.
- **Ścieżkę katalogu domowego zapisuj jako `~`**, a nie pełnym prefiksem systemowym (wzorzec ścieżek domowych jest wbudowany w strażnika).
- **Strażnik `leakcheck`** sprawdza treść, nazwy plików i commity. Uruchom go przed publikacją: `skillsync leakcheck <katalog> --target <id>`. Hooki git strażnika zakłada `layers init` w nowym repo, a `layers add` w checkoucie warstwy. Repo, w którym edytujesz skopiowany przykład, dostaje je ręcznie: `skillsync leakguard install-hook <repo> --target <id>` (krok 2; bez `--target` hook w repo bez remote nie zna celu i blokuje własne terminy warstwy).
- **`leakguard/identity.yaml`** zawiera ślady (`terms`: termin i rodzaj, np. `owner-nick`, `owner-name`, `machine`, `persona`), których strażnik nie wpuści do repo innych celów. Ścieżek domowych i adresów e-mail nie wpisuj: łapią je wzorce wbudowane. Termin persony ma być unikalnym imieniem. Pospolite słowo da fałszywe trafienia w innych warstwach. Przykład używa `Asystent` z `case: sensitive` i `match: whole`, ale przy własnej personie dobierz imię tak samo unikalne.
- **`.gitignore`** wyklucza pliki lokalne (`*.local.*`) i katalog `.skillsync/`.
