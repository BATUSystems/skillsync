---
title: "Strażnik wycieków (leakguard): identity.yaml, patterns.yaml, coverage, klasy ścieżek, hooki, pełne ścieżki w raporcie (D71)"
tags: [skillsync, leakguard, leakcheck, security, identity, coverage, hooks, privacy]
paths: ['skillsync_workflow/leakguard.py', 'skillsync_workflow/data/leakguard-patterns.yaml', 'skillsync_workflow/data/leakguard-suffixes-pl.txt', 'skillsync_workflow/layers.py', 'skillsync', 'tests/test_skillsync_leakguard*.py']
source: "spec zadania (poza rdzeniem) (K6–K8, D86, Format patterns.yaml, Klasy ścieżek, Hooki); skillsync_workflow/leakguard.py (zasoby i .leakguard/patterns.yaml, authors_scope, pole path w raporcie, --require full, core.hooksPath); skillsync_workflow/layers.py (parse_machine_config, leakguard_inputs); plan zadania (poza rdzeniem), sekcja Wynik (D71, D86, krok 1.1, 2.1)"
verified: 2026-11-12
---
Reguła: `skillsync leakcheck` i hooki git blokują treść, nazwy plików i metadane commitów, które zawierają tożsamości
niepubliczne (nazwy organizacji, projektów, osób, domen, nick i imię właściciela, nazwy persony) albo ślady ogólne
(ścieżki domowe, adresy e-mail spoza allowlisty). Wynik zawsze podaje `coverage` i **nigdy nie zwraca wartości wzorca**.

Reguła graniczna: jawne tożsamości żyją **tylko** w prywatnych warstwach (`leakguard/identity.yaml`) i w katalogu
`leakguard.identity_dir` na maszynie. Repo publiczne zawiera wyłącznie część ogólną (wzorce ogólne i wyjątki wydawcy).

## Denylista i źródła wzorców (K7)

Kolejność źródeł (spec A, „Źródła denylisty”):
1. Jawne `identity` warstw obecnych lokalnie, różnych od celu, plus pliki `leakguard.identity_dir/**/*.y*ml`,
   po wyjątkach macierzy (K8).
2. `.leakguard/patterns.yaml` w korzeniu repo celu (git root; nie w `subdir` warstwy).
3. Wzorce wbudowane: zasób generatora `skillsync_workflow/data/leakguard-patterns.yaml` (stała zasobu w `leakguard.py`).
   Zawiera wzorce D27 zapisane klasami znaków (tak, by plik nie niósł dosłownego śladu), ścieżki domowe i e-maile.
   Jest jedynym źródłem wzorców ogólnych; kod ich nie kopiuje.

Zasady testów: wyjątkami od skanu treści są dokładnie dwa pliki wzorców (zasób generatora i `.leakguard/patterns.yaml`),
oba po walidacji schematu. Testy składają nazwy w locie, a literał śladu D27 w innym pliku daje FAIL.

Dopasowanie odmian (K6): token równy terminowi albo termin + końcówka z zamkniętego zbioru polskich końcówek
(`leakguard-suffixes-pl.txt`: `a, u, e, y, i, ie, ze, ce, em, om, ów, ach, ami, owi, owie, ą, ę, ego, emu, ym, ich`).
Terminy krótsze niż 4 znaki i terminy `case: sensitive` trafiają tylko dokładnie, z zachowaniem wielkości liter.

## `identity.yaml` (tożsamość warstwy)

Źródło: spec A „Schemat identity.yaml” (D104); walidacja `schema` i `layer` w parserze identity (`leakguard.py`).

```yaml
schema: 1
layer: acme                  # id warstwy (wymagane)
terms:
  - term: Przykladowa Firma
    kind: org
    case: insensitive        # insensitive (domyślnie) | sensitive
    match: part              # part (domyślnie) | whole
    allow_in: [acme]         # domyślnie [layer]; id celów
    path_classes: [instrukcje, atrybucje, inne]   # klasy, w których działa zwolnienie z `allow_in` (domyślnie wszystkie); poza nimi tabela K8
```

Warstwa celowo bez terminów: `terms: []` oraz `empty: <powód>` (D190, niepusty napis). `empty: null` (albo puste `empty:`)
znaczy brak deklaracji. `empty` przy niepustym `terms` albo `empty` niebędące niepustym napisem = błędny plik identity:
`leakcheck` i hooki dają BLOCKED (`layers check` nie waliduje schematu identity).

- `kind` ∈ `org`, `project`, `domain`, `person`, `domain-term`, `machine`, `owner-nick`, `owner-name`, `persona`, `publisher`.
- `match: part` trafia także w część identyfikatora (segment snake_case/kebab-case/camelCase, np. nazwisko w nazwie pliku).
  Termin sklejony (np. prefiks jednoliterowy + camelCase, np. `xFoo`) trafia tylko w postać sklejoną (`xFooService`,
  `fooXFoo`), nie w rozdzielone słowa („x Foo”).
- `match: whole` trafia tylko w cały token: identyfikator liczony w całości albo osobne słowo w tekście; odmiana całego
  słowa (`xABCowi`) trafia, kontynuacja camelCase i część identyfikatora nie.
- Brak globalnej reguły „termin osoby nie trafia w część identyfikatora”: byłaby przeoczeniem nazwisk w nazwach plików
  (decyzja D104).
- `identity` czytane są także bez deklaracji w `layer.yaml` (fail-closed). Nieczytelny plik (błędny YAML) = `identity-invalid` (FAIL w `layers check`). Błąd schematu (np. `empty`, `kind`, `case`, `match`) = BLOCKED w `leakcheck` i hookach; `layers check` schematu identity nie waliduje. Zasady `empty`: zob. „Kanarek tożsamości”.

## `patterns.yaml` (format wspólny dla `leakcheck` i generatora dokumentacji, D52)

Źródło: spec A „Format patterns.yaml”; walidacja `schema` i wczytanie `.leakguard/patterns.yaml` w `PatternSet` (`leakguard.py`).

```yaml
schema: 1
patterns:
  - id: d27-source              # unikalne, [a-z0-9-]+
    category: d27               # d27 | owner-path | email | machine | publisher | custom
    regex: '\b[o]pen[ -]?[m]ercato\b'   # składnia Pythona re, bez flag inline
    flags: [ignorecase]         # ignorecase | multiline; nic innego
    applies_to: [content, filename, commit]
    path_classes: [instrukcje, atrybucje, inne]
allow:
  - id: publisher-urls
    regex: 'github\.com/BATUSystems/(skillsync|skippster)(\.git)?(/(issues|pull|releases|blob|tree)(/[\w.-]+)*)?'
    flags: [ignorecase]
  - id: publisher-license
    regex: '^Copyright \(c\) \d{4} BATUSystems$'
    paths: ['LICENSE*']
  - id: trailer-noreply
    regex: '^Co-Authored-By: [^<]+ <noreply@anthropic\.com>$'
    applies_to: [commit]
```

Reguły:
- `schema` > 1 = odmowa z komunikatem; nieznane klucze ignorowane. Brak zasobu lub błędny schemat = BLOCKED.
- Wartości domyślne: `flags: []`, `applies_to: [content, filename, commit]`, `path_classes: [instrukcje, atrybucje, inne]`,
  `paths`: wszystkie.
- Regex dopasowywany do każdej linii osobno (`re.search`; `^`/`$` = granice linii).
- Wpis `allow` wyłącza trafienie (z `patterns` i z `identity`) tylko wtedy, gdy zakres znaków trafienia mieści się **w całości**
  w zakresie dopasowania `allow` (ta sama linia, plik i powierzchnia, z uwzględnieniem `paths`/`path_classes`).
- Wpisy `allow` mają być wąskie. Dodatek w `.leakguard/patterns.yaml` nie może mieć `allow`, którego regex dopasowuje pusty
  ciąg albo całą linię; nie może zawierać jawnych terminów z `identity` (D37).
- W CI dla PR dodatki czyta się z gałęzi bazowej, nie z gałęzi PR.
- `leakguard patterns [--target ID] --json` wypisuje scaloną listę (wbudowana + dodatki celu) dla narzędzi zewnętrznych.

## Klasy ścieżek i dozwolone tożsamości (K8)

Źródło: spec A „Klasy ścieżek i dozwolone tożsamości”, D38, D40, D44, D51, D62.

- `instrukcje` = dowolny segment katalogu `instructions`, `skills`, `agents` albo `_base.d` na dowolnej głębokości (np. `docs/skills/x.md` i `tests/skills/a.py` są `instrukcje`; sprawdzane są tylko katalogi, nie nazwa pliku) oraz każda ścieżka niesklasyfikowana
  (domyślna klasa nieznanej ścieżki = najostrzejsza).
- `atrybucje` = `CHANGELOG*`, `README*`, `docs/**`, `LICENSE*`, `NOTICE*` oraz komunikaty i trailery commitów.
- Pliki kodu (`*.py`, `*.ts`, `*.js`, `*.mjs`, `*.cjs`, `*.ps1`, `*.sh`, `skillsync`, `skillsync-ctl`) poza `tests/**`
  i `.github/**` mają klasę `instrukcje`, także w `docs/**` i w korzeniu (D238): kod nie jest atrybucją. Nazwy i sufiksy plików kodu porównuje się po `casefold` (`docs/Build.PY` to kod), bo to tylko zaostrza klasę.
- `inne` = `tests/**`, `.github/**`, pliki konfiguracyjne w korzeniu. **`inne` ≡ `instrukcje`** w każdej kolumnie.
- Klasy ścieżek: segmenty klas `instrukcje` (`instructions/`, `skills/`, `agents/`, `_base.d/`) i `inne` (`tests/`, `.github/`) porównywane są po `casefold` na wszystkich systemach (`Skills/x/run.py` = `instrukcje`); dopasowania klasy `atrybucje` (`docs/`, `README*`, `CHANGELOG*`) zostają dosłowne, bo `casefold` poluzowałby klasę.
  Kolejność: segmenty `instrukcje`, `tests/`/`.github/` (`inne`), pliki kodu (`instrukcje`), `atrybucje`, pliki w korzeniu (`inne`).
- Kolumny tabeli wybiera `kind` warstwy celu (core/org/personal), nie `scope`. `scope` wpływa tylko na CI i `coverage`.

| Termin | core: instrukcje | core: atrybucje | org: instrukcje | org: atrybucje | personal |
|---|---|---|---|---|---|
| nick właściciela | zakazany | dozwolony | zakazany | dozwolony | dozwolony |
| nazwy persony i kart | zakazane | zakazane | zakazane | zakazane | dozwolone |
| imię i nazwisko właściciela | zakazane | dozwolone | dozwolone | dozwolone | dozwolone |
| `github.com/BATUSystems/skillsync*`, `…/skippster*`, linia wydawcy w `LICENSE` | dozwolone | dozwolone | dozwolone (wyjątek wydawcy) | dozwolone | dozwolone |
| inne wzmianki o BATUSystems | dozwolone | dozwolone | zakazane (poza warstwą wydawcy) | zakazane | zakazane |
| tożsamości klientów | zakazane | zakazane | wg macierzy celu | wg macierzy celu | zakazane |

Atrybucje nie są blokowane: trailery `Co-Authored-By` oraz wzmianki o właścicielu w `CHANGELOG`, `docs/**`
i komunikatach commitów są dozwolone; komentarze i docstringi w kodzie są klasą `instrukcje` (D238). Wyjątek wydawcy (URL-e i linia `LICENSE`) jest jawną regułą `allow` z testem.

Nazwy plików i nazwy tagów (`tag-name`) są zawsze sprawdzane jako klasa `instrukcje` (najostrzejsza), także w `docs/**`
i `CHANGELOG*`. Tożsamość w nazwie pliku w `atrybucje` jest więc blokowana według kolumny `instrukcje`.

## Cele (`leakguard.targets`) i poziomy `coverage`

Źródło: spec A (K7, K9), stałe `authors_scope` i rozpoznanie celu w `leakguard.py`, parsowanie configu w `layers.py`; fakty F-G10.

Config maszyny, sekcja `leakguard`:

```yaml
leakguard:
  identity_dir: ~/ops/leakguard          # katalog z plikami identity (repo prywatne, poza publicznymi)
  trailer_allow: ["noreply@example.com"] # adresy dozwolone w trailerach Co-Authored-By
  targets:
    acme: {scope: org-private, remote_match: ["git@example.com:acme/*"], authors: ["jan@acme.example"]}
    core: {scope: public, remote_match: ["example.com/owner/skillsync*"], authors: ["owner@example.com"]}
```

- Klucze czytane przez `layers.py`: `identity_dir`, `targets`, `trailer_allow`. Inny klucz w `leakguard` = ostrzeżenie
  `Unknown key ignored`.
- `scope` celu: `public`, `org-private`, `private`. Cele `public` nie mają tożsamości; pozostałe cele wymagane do `coverage: full`
  mają `identity`.
- `remote_match` rozpoznaje cel po adresie remote; cel `leakcheck` bez dopasowania = warstwa, której checkout jest git rootem skanu.
- Lokalne ścieżki celu (`remote_match`, wybór celu) są porównywane: Windows `normcase`, macOS NFD + `casefold`, Linux dosłownie; URL-e i adresy scp zawsze dosłownie.
- `authors`: adresy dozwolonych autorów i committerów celu.
- `authors_scope` (D86): `all` lub `committer`. Domyślnie `all` dla `private` i `org-private`; `committer` dla `public`.
  `all`: każdy commit w zakresie musi mieć autora i committera z `authors`. `committer`: kontrola dotyczy commitów, których
  committerem jest lokalny użytkownik (autor i committer); commit, którego autorem jest lokalny użytkownik, a committerem
  ktoś inny (cherry-pick, rebase, przycisk merge), ma sprawdzanego autora, a obcy committer jest informacją (D239);
  commity innych osób są informacją.
- Adres autora i committera nie jest sprawdzany przez wzorce `email` (tylko przez `authors`); bez `authors` (cel nieznany)
  adres autora nie jest sprawdzany i jest to ostrzeżenie w `warnings`.

Poziomy `coverage` (K7):

| Poziom | Znaczenie |
|---|---|
| `full` | dla każdego id z `leakguard.targets` różnego od wybranego celu i o `scope` innym niż `public` wczytana `identity` tej warstwy ma co najmniej jeden termin albo deklaruje `empty: <powód>` (D190). Bez takich celów wynik to `full` |
| `partial` | co najmniej jedna warstwa poza celem liczy się (ma termin albo `empty`), ale nie wszystkie wymagane cele mają taką `identity` |
| `generic` | żadna warstwa poza celem się nie liczy: brak wybranego celu, cel nieznany w `leakguard.targets`, brak `leakguard.targets`, albo wszystkie wczytane `identity` są puste bez `empty` (nieznany cel daje ostrzeżenie, nie BLOCKED) |

`--require full` (obsługa w `leakcheck`): przy `coverage` innym niż `full` wynik to **BLOCKED** (exit 2) z powodem
`Coverage is <poziom>; --require full needs identity files for every non-public target`. Publikacja (spec B) używa tej flagi. Wbudowany `build.py`
dokumentacji wymaga jedynie części ogólnej (`coverage: generic`), lokalnie może żądać `full`.

## Kanarek tożsamości (D190)

- Warstwa niepubliczna, której wczytane pliki `identity` mają zero terminów, nie liczy się do `coverage: full`, chyba że
  jeden z jej plików deklaruje `empty: <powód>` (niepusty napis). `empty: null` albo puste `empty:` = brak deklaracji.
- Gdy pokrycie potrzebuje takiej warstwy (jest niepublicznym celem różnym od wybranego), wynik dostaje ostrzeżenie
  `Identity of layer <id> has no terms; it does not count toward full coverage (declare empty: <reason> if intended)`.
- `--require full` przy takim pokryciu daje BLOCKED (exit 2), jak każdy poziom inny niż `full`.
- Błędny plik: `empty` razem z niepustym `terms`, albo `empty` niebędące niepustym napisem (pusty napis, liczba, lista,
  `false`) = błąd wczytania identity. `leakcheck` i hooki dają BLOCKED. `layers check` tego nie waliduje.
- Kod: `parse_identity` (walidacja i pole `empty`), `prepare` (warstwy liczące się i puste), `compute_coverage` (poziom i ostrzeżenie).

## Raport: pełna ścieżka pliku (D71)

Decyzja D71 (QK1, plan Wynik): pole `path` w raporcie strażnika jest **zawsze pełne**, także w CI, bez maskowania i bez
flagi ograniczającej ścieżki. Kod: budowa `findings: {path, category, location}` w `leakguard.py`; wyjście JSON
przechodzi przez `redact`, który maskuje wzorce sekretów, nie ścieżki.

Uzasadnienie (plan Wynik): w repo publicznym ścieżka jest i tak widoczna w drzewie, więc log niczego dodatkowo nie ujawnia.
Raport nie zawiera wartości trafienia ani wzorca, tylko `path`, `category` i `location`.

## `leakcheck`

```bash
skillsync leakcheck <katalog|projekt> [--target ID] [--worktree|--staged|--range A..B|--history] \
  [--identity FILE]... [--targets-json JSON] [--trailer-allow ADRES]... [--target-patterns PLIK] \
  [--require full] [--no-config] [--json]
skillsync leakcheck --text <plik>|--stdin [--target ID] [--require full] [--json]   # tekst poza gitem (PR, notatka wydania)
```

- Tekst poza gitem (`--text FILE`, `--stdin`, do 1 MiB): te same tożsamości i wzorce co `--range`, ale cel wybiera się z katalogu bieżącego (config maszyny) albo `--target`, nie z repozytorium docelowego publikacji: tożsamości warstwy równej celowi nie są „obce”, więc przy złym celu własne terminy przejdą; podawaj `--target <id repo docelowego>` albo uruchamiaj w jego checkoucie, a `--require full` blokuje niepełne pokrycie; tekst jest skanowany w klasie ścieżki `skills/text` (`instrukcje`, najostrzejsza), więc nick właściciela dozwolony w `CHANGELOG`/`docs` w opisie PR-a jest trafieniem. Nie wymaga repozytorium git; etykieta w raporcie to nazwa pliku albo `<stdin>`, tekstu raport nie powtarza. Tekst nigdy nie jest argumentem komendy (błąd wypisałby go); skille `pr-run`, `feature`, `dispatch` wymagają tej kontroli przed `gh pr create`, `gh pr edit --body` i `gh release create`.

- Tryby: `--worktree` (domyślnie), `--staged`, `--range A..B`, `--history`. Skan obejmuje treść, nazwy plików i metadane
  commitów (autor, committer, trailery, komunikat). W `--range` i w `pre-push` skanowane są też adnotowane tagi wskazujące
  na wypychane commity: komunikat tagu (podpis odcinany od ostatniego markera) i nazwa tagu (tak jak nazwa pliku), w raporcie
  ze ścieżką `@tag:<sha>`. Pozostała obsługa tagów (tagger i `authors` dla taggera, łańcuch tag→tag, notice `foreign-tag`)
  działa, ale jest nieobjęta gwarancją 1.0.0.
- Kody: PASS 0, FAIL 1 (wyciek: `{path, category, location}`), BLOCKED 2 (`--require full` przy niepełnym pokryciu,
  brak lub błędny plik wzorców, niepoprawny `layer.yaml` z configu: fail-closed dla tożsamości; D73: link (symlink)
  w warstwie lub repo celu, link jako plik identity, `.leakguard/patterns.yaml` jako link). W trybie `--text`/`--stdin`
  BLOCKED także dla pustego tekstu, binarnego (NUL w pierwszych 8 KiB), niepoprawnego UTF-8, tekstu ponad 1 MiB, braku
  pliku albo nieczytelnego wejścia (fail-closed: bramka publikacji nie przepuszcza tekstu, którego nie przeskanowała).
- Pole `notices` w wyniku zawiera informacje niebędące wyciekiem, np. `foreign-commit` (commit innej osoby przy
  `authors_scope: committer`) i własny commit z adresem committera różnym od `git config user.email`. Tag adnotowany innej osoby przy `authors_scope: committer` daje `foreign-tag` (bez gwarancji 1.0.0).
- Gdy adres remote pasuje do kilku `remote_match` (kilka trafień), cel nie jest rozstrzygany samodzielnie: wymagane jest
  jawne `--target`; bez niego wynik to `coverage: generic` z ostrzeżeniem.
- Źródła wejścia. Bez jawnych `--identity`, `--targets-json`, `--trailer-allow` i bez `--no-config` tożsamości, cele i trailer
  allowlist biorą się z configu maszyny (`leakguard_inputs`). `--target` tego nie zmienia: wybiera cel z configu.
  Jawne `--identity`, `--targets-json` lub `--trailer-allow` wyłączają config; wtedy czytane są tylko podane wejścia
  (bez `--targets-json` brak celów, czyli `coverage: generic`). `--no-config` działa tak samo.
- Brak celu (bez `--target`, bez dopasowania `remote_match` i bez checkoutu warstwy) = `coverage: generic`.
- Flagi `--identity`, `--targets-json` i `--trailer-allow` (`leakcheck`, `leakguard install-hook`) oraz `--target-patterns`
  (`leakcheck`, `leakguard patterns`) są wejściami dla narzędzi operacyjnych i diagnostyki. Działają, ale są ukryte w `--help` (D200).

## Hooki git

```bash
skillsync leakguard install-hook <repo> [--remove] [--json]
```

- Zarządzany blok między znacznikami w `pre-commit` (zmiany staged) i `pre-push` (zakres wypychanych commitów i adnotowane
  tagi z wypychanych refów: komunikat i nazwa).
  Istniejące hooki zostają.
- `pre-push` przy zdalnym sha z samych zer: zakres to commity gałęzi od `merge-base` z gałęzią domyślną albo cała historia.
- `core.hooksPath` wskazujący katalog spoza lokalnego katalogu git (np. śledzony wspólny katalog hooków): odmowa z komunikatem
  i instrukcją ręcznego wpięcia (obsługa `core.hooksPath` w `install-hook`); wynik to `NOT_RUN` (exit 0, komunikat `[info]`),
  a wspólnych hooków zespołu się nie zmienia.
- Hook `pre-push` kończy się błędem, gdy nie da się przygotować wejścia (fail-closed).
- `pre-push`: ref usunięty (lokalny sha z samych zer) jest pomijany. Zdalny sha, którego nie ma lokalnie, daje ten sam
  fallback zakresu co sha z samych zer.
- `install-hook` raportuje `action` per hook: `created`, `updated`, `unchanged`, `inserted`, `absent`, `removed`, `deleted`
  albo `skipped`. `skipped` = hook jest dowiązaniem symbolicznym albo nie jest skryptem POSIX shell; blok nie jest wpisany,
  wywołanie trzeba dodać ręcznie. Gdy oba hooki są `skipped`, wynik to `NOT_RUN`.
- `--remove` kasuje plik hooka (`deleted`), gdy po usunięciu bloku zostaje tylko `#!/bin/sh` lub nic; w innym przypadku usuwa
  sam blok (`removed`).
- `pre-commit` (`--staged`), gdy cel ma `authors`, sprawdza `GIT_AUTHOR_IDENT` i `GIT_COMMITTER_IDENT` wobec `authors` niezależnie
  od `authors_scope`. Nieczytelna tożsamość daje ostrzeżenie; adres spoza `authors` daje wyciek kategorii `author`.
- `--identity` i `--targets-json` w `install-hook` to tryb diagnostyczny: wpisują blok bez configu, który `layers check`
  oznacza jako `hook-legacy`. W normalnej pracy hook czyta wyłącznie config.
- Brak hooka w zapisywalnej warstwie to `hook-missing` (FAIL w `layers check`, `_hook_findings`). Wykrywanie, czy hook
  wywołuje `leakcheck`, jest heurystyczne; bramką pozostaje sam hook i CI.
- Hooki instalują `layers add` i `layers init` w checkoutach warstw zapisywalnych (zob. [layers](layers.md)).

## CI

Dla zewnętrznych PR workflow uruchamia `leakcheck` z wzorcami ogólnymi i testy (macierz ubuntu, windows, macos).
Macierz GitHub jest `NOT_RUN`, dopóki nie wykonano push repo (decyzja D13 w planie). Konkretny przykład CI nie jest
dołączony do repo.

## Znane ograniczenia i ryzyka rezydualne

- Klasa ścieżki w `push` liczona jest względem korzenia warstwy (`path/subdir`), a w hooku względem korzenia repo git.
  Przy `subdir` ta sama ścieżka może mieć w hooku inną klasę (znane ograniczenie).
- Nazwy refów innych niż adnotowane tagi (gałęzie, tagi lekkie) nie są skanowane. Tag (także lekki) wskazujący na drzewo
  albo blob jest wypychany bez skanu jego treści. Przed publicznym wydaniem nazwę i treść tagu sprawdza ręczna bramka runbooka.
- `pre-push` z `--tags` do zdalnego repo bez `refs/remotes/<r>/HEAD` (albo na URL) skanuje historię osobno dla każdego tagu:
  koszt rośnie z liczbą tagów.
- Rozwiązanie końców zakresu używa `git rev-parse --end-of-options` (nowsze wersje git); na git bez tej opcji zakres z tagiem
  albo końcówka, której nie da się rozwiązać, daje `BLOCKED` (fail-closed).
- Zapasowa ścieżka pliku tymczasowego hooka (`$TMPDIR/skillsync-pre-push.$$`, gdy `mktemp` zawiedzie) jest przewidywalna.
- Przy `authors_scope: committer` własny commit z adresem autora i committera innym niż `git config user.email` trafia do
  `notices`, a jego adresy nie są sprawdzane (właściciel jest rozpoznawany po `user.email`).
- Bajt `\x1e` w komunikacie commitu kończy skanowany rekord; reszta komunikatu nie jest skanowana.

Gdzie w kodzie: `skillsync_workflow/leakguard.py`: zasoby wzorców i `.leakguard/patterns.yaml`, domyślne `authors_scope`,
budowa raportu (`findings`, `redact`), obsługa `--require full`, instalacja hooków (`core.hooksPath`);
`skillsync_workflow/layers.py`: parsowanie configu `leakguard` (`parse_machine_config`), `leakguard_inputs` (`identity_dir`).
