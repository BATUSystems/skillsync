---
title: Glosariusz domenowy Skillsync (słownik warstw, strażnika i workflow)
tags: [terminology, skillsync, layers, leakguard, workflow, prototype]
paths: ['skillsync', 'skillsync_workflow/layers.py', 'skillsync_workflow/leakguard.py', 'skillsync_workflow/common.py', 'skillsync_workflow/prototype_server.py', 'skillsync_workflow/prototype.py']
source: "Definicje z docs/specs/2026-11-05-warstwy-generator.md (sekcja Terminologia domenowa, K1–K15, Kontrakty); AGENTS.md (podumysły); skillsync_workflow/layers.py (`match_rule`, `LayerSet`); plan docs/plans/2026-11-05-warstwy-generator.md (Terminologia domenowa)"
verified: 2026-11-06
---
Glosariusz: krótkie znaczenia pojęć projektu Skillsync. Szczegółowe reguły są w rekordach tematycznych
(odsyłacze w nawiasach). Każda definicja ma źródło i datę potwierdzenia. Nazwa klasy w kodzie sama nie potwierdza
znaczenia pojęcia. Termin, którego nie ma tutaj, nie jest zdefiniowany.

## Warstwy

### warstwa (layer)
- **Znaczenie:** katalog o układzie biblioteki (`skills/`, `instructions/`, `agents/`, `workflow/`, `codex.yaml`) z plikiem
  `layer.yaml`, dostarczany przez repo i składany z innymi warstwami na maszynie.
- **Nie oznacza:** „warstw workflow” (scalanie `defaults.yaml` → `projects/*.yaml` → `workflow:` manifestu).
- **Źródło:** spec A, Terminologia domenowa, 2026-11-05. Szczegóły: [layers](layers.md).

### core (rdzeń)
- **Znaczenie:** warstwa `kind: core`; dokładnie jedna na maszynie. Wspólny agent flow i skille obok generatora.
- **Nie oznacza:** profilu skilli `core` ani „rdzenia towarzyszy” `feature`.
- **Źródło:** spec A, 2026-11-05.

### org
- **Znaczenie:** warstwa `kind: org`; wiedza jednej organizacji. Może być kilka warstw org.
- **Nie oznacza:** identyfikatora organizacji w `skill-catalog` (`orgs.<id>`).
- **Źródło:** spec A, 2026-11-05.

### personal
- **Znaczenie:** warstwa `kind: personal` dowolnego użytkownika; przenośna, bez tożsamości klientów. Najwyżej jedna na projekt.
- **Nie oznacza:** „warstwy osoby X”; rdzeń nie zawiera niczego specyficznego dla jednego użytkownika.
- **Źródło:** spec A, 2026-11-05.

### layer.kind
- **Znaczenie:** rodzaj warstwy: `core`, `org`, `personal`.
- **Nie mylić z:** `term.kind` w `identity.yaml` (rodzaj terminu: `org`, `project`, `person`…). Spec A, 2026-11-05.

### LayerSet
- **Znaczenie:** obiekt zbioru aktywnych warstw jednego wywołania (lista warstw z configu, reguła doboru, kolejność).
  Jedyne źródło ścieżek biblioteki dla konsumentów generatora.
- **Nie oznacza:** pliku ani sekcji configu; to klasa w kodzie (`skillsync_workflow/layers.py`).
- **Źródło:** spec A, K1, 2026-11-05.

### layer_rules (reguły warstw)
- **Znaczenie:** lista w configu maszyny `match` → `use`. Pierwsza pasująca reguła wybiera warstwy projektu.
  Dopasowanie wyłącznie po rozwiązanej ścieżce projektu, nie po `project.name`.
- **Źródło:** spec A, K3, D36, 2026-11-05; `match_rule` w `skillsync_workflow/layers.py`. Szczegóły: [layers](layers.md).

### wpis `synthetic` (config 0.9.x)
- **Znaczenie:** wpis `core` z `synthetic: {version, core_min}` i `writable: false` w configu zapisanym przez generator 0.9.x.
  Generator go rozpoznaje (`link` i `layers` = BLOCKED `legacy-config`) i zastępuje prawdziwą warstwą core przez
  `layers migrate --layer-source core=PATH`.
- **Nie oznacza:** warstwy docelowej core ani poprawnej konfiguracji.
- **Źródło:** kod `skillsync_workflow/layers.py` (`_check_bridge`, `parse_layer_flag`, `migrate_config`), 2026-11-06. Szczegóły: [layers](layers.md).

### właściciel pliku
- **Znaczenie:** warstwa, z której plik został rozwiązany. Zapis (`push`) trafia do właściciela.
- **Nie oznacza:** autora pliku.
- **Źródło:** spec A, K4, 2026-11-05.

### kompozyt (skilla)
- **Znaczenie:** skill dostarczany przez co najmniej dwie warstwy (albo z linkiem w środku), składany w cache
  (`<cache>/composed/<skill>/<hash>`) z symlinków do plików źródłowych.
- **Nie oznacza:** kopii skilla w `.github/skills`.
- **Źródło:** spec A, K4 i „Kompozyt skilla”, 2026-11-05.

### adres docelowy symlinka
- **Znaczenie:** ścieżka, na którą wskazuje symlink (kompozyt albo skill jedynego dostawcy).
- **Nie oznacza:** „cel” strażnika (repo docelowe). Spec A, 2026-11-05.

### `_base.d` (fragmenty bazowe)
- **Znaczenie:** katalog `instructions/_base.d/*.md` w warstwach; pliki sumowane w kolejności warstw z `use`, potem nazwy pliku.
  Fragmenty `00-`…`09-` idą przed tekstem `_base` rdzenia, pozostałe po nim.
- **Źródło:** spec A, „Kontrakty” i K5, 2026-11-05; kontrakt kolejności zgodny z kodem (plan Wynik, 2.2a).

### `core_min`
- **Znaczenie:** minimalna wersja generatora wymagana przez warstwę; porównywana z `__version__`.
- **Źródło:** spec A, K12, 2026-11-05.

### `allow_override`
- **Znaczenie:** lista elementów rdzenia i ścieżek plików, które warstwa `org` może nadpisać mimo modelu zaufania.
- **Źródło:** spec A, „Kontrakty” (model zaufania), 2026-11-05.

### `legacy_library`
- **Znaczenie:** ścieżka starej biblioteki zapisana przez `layers migrate`; służy tylko do rozpoznawania zarządzanych symlinków
  i do `layers migrate --finish`.
- **Źródło:** spec A, K11, 2026-11-05.

### orphans (`<hash>.orphans`)
- **Znaczenie:** katalog nowych plików kompozytu, których nie udało się adoptować do właściciela. Nic nie usuwa go automatycznie;
  `layers check` go wypisuje.
- **Źródło:** spec A, K4, 2026-11-05.

### izolacja (`isolation`)
- **Znaczenie:** `isolation.allowed_layers` wymienia jedyne warstwy, które mogą istnieć na maszynie; sprawdza
  `layers check --isolation`.
- **Źródło:** spec A, K10, 2026-11-05. Szczegóły: [layers](layers.md).

## Strażnik wycieków

### strażnik (leakguard)
- **Znaczenie:** mechanizm `leakcheck` i hooki git, które sprawdzają treść, nazwy plików i metadane commitów przed zapisem
  lub wypchnięciem do repo celu; wyciek = FAIL albo BLOCKED.
- **Źródło:** spec A, K6–K8, 2026-11-05. Szczegóły: [leakguard](leakguard.md).

### cel (target)
- **Znaczenie:** repo docelowe sprawdzane przez strażnika (id w `leakguard.targets` configu maszyny).
- **Nie oznacza:** „projekt” (repo, w którym działa `link`).
- **Źródło:** spec A, 2026-11-05.

### coverage
- **Znaczenie:** stopień pełności strażnika dla celu: `full` (tożsamości wszystkich celów niepublicznych), `partial`
  (część), `generic` (tylko wzorce ogólne, także cel nieznany).
- **Źródło:** spec A, K7, 2026-11-05. Szczegóły: [leakguard](leakguard.md).

### identity (tożsamość)
- **Znaczenie:** plik z terminami, których nie może zawierać repo niepubliczne: organizacje, projekty, osoby, domeny, nick
  i imię właściciela. Czytany z każdej obecnej lokalnie warstwy (domyślnie `leakguard/identity.yaml`, ścieżka nadpisywalna
  polem `identity` w `layer.yaml`) oraz z plików w `leakguard.identity_dir`.
- **Źródło:** spec A, K7, 2026-11-05; `skillsync_workflow/layers.py` (`DEFAULT_IDENTITY`, `leakguard_inputs`), 2026-11-06.
  Szczegóły: [leakguard](leakguard.md).

### klasa ścieżki
- **Znaczenie:** `instrukcje`, `atrybucje` lub `inne` (K8); określa, które tożsamości są dozwolone w danym pliku.
  `inne` ≡ `instrukcje`.
- **Nie mylić z:** rodzajem warstwy (`layer.kind`).
- **Źródło:** spec A, K8, 2026-11-05.

### scope celu
- **Znaczenie:** `public`, `org-private` albo `private`; wpływa na CI i `coverage`, nie na wybór kolumny macierzy zakazów.
- **Źródło:** spec A, K8, 2026-11-05.

### atrybucja
- **Znaczenie:** wzmianka o autorze lub wydawcy w dozwolonej klasie (trailer `Co-Authored-By`, komentarze kodu, `CHANGELOG`,
  `docs/**`, `README`, `LICENSE`, `NOTICE`). Atrybucje nie są blokowane.
- **Źródło:** spec A, K8 i D40, 2026-11-05.

## Workflow i agenci

### podagent (podumysł)
- **Znaczenie:** wykonawca delegowany przez sesję główną. W dokumentacji i rozmowie nazywany **podumysłem**.
  Nazwy techniczne narzędzi (`Agent`, `subagent_type`) pozostają bez zmian.
- **Źródło:** AGENTS.md projektu (sekcja „Workflow agentowy”), 2026-11-05.

### karta
- **Znaczenie:** sesja robocza skilla `dispatch`, wykonująca paczkę przez `feature` (`dispatch/SKILL.md:9`), przydzielana
  przez dyspozytora; w zapisach występuje z aliasem `karta-N`.
- **Źródło:** skill `dispatch`, `library/skills/dispatch/SKILL.md:9, :65, :101` (biblioteka), 2026-11-05; alias `karta-N` i brak
  imion poza warstwą personal: spec A, K5, 2026-11-05.

## Prototypy

### widok
- **Znaczenie:** stan oglądania ramki ekranu prototypu: `desktop` (pełna szerokość) albo `mobile` (iframe 375 px z media
  queries makiety). Ustawia go przełącznik przy ekranie (nagłówek ekranu); start strony zawsze `desktop`.
- **Nie oznacza:** „trybu” (Klikalny / Komentarze). Spec widoku makiety, Terminologia, 2026-11-06.

### widok wątku
- **Znaczenie:** `thread.view`: `mobile` tylko, gdy `create` wątku ma `payload.view: "mobile"`; inaczej `desktop`.
- **Źródło:** `library/skills/prototype/references/comments-format.md` (biblioteka), 2026-11-06.

### iframe widoku, instancja osadzona
- **Znaczenie:** iframe w ramce ekranu w widoku `mobile` (`?embed=view#sN`) i silnik działający w nim bez zapisów i sesji;
  dane i zapisy ma strona. Haki: [słownik](../prototype-contract/slownik-html-css.md), część C. Spec, D1–D2, 2026-11-06.

Gdzie w kodzie: `skillsync_workflow/layers.py` (`match_rule`, `LayerSet`, `check_layers`, `leakguard_inputs`);
`skillsync_workflow/leakguard.py` (strażnik).

## Retrospektywa

### pętla jednorundowa
- **Znaczenie:** pętla przeglądu bez pełnej kontroli; kończy się weryfikacją zamknięć, a druga runda tylko po blockerze.
- **Źródło:** `library/skills/feature/references/review-loop.md`, sekcja „Kategoria pętli i przejścia” (biblioteka), 2026-11-06. Szczegóły: [retro-proposals](retro-proposals.md).

### nadmiar ceremonii (`ceremony-excess`)
- **Znaczenie:** kontrola wykraczająca ponad to, czego wymaga kategoria pętli (pełna albo jednorundowa); klucz przyczyny w retrospektywie.
- **Nie oznacza:** `review-rounds` (rundy z uwagami bez wspólnej przyczyny merytorycznej).
- **Źródło:** `library/skills/feature/references/retro-quality.md`, wyzwalacz 7 (biblioteka), 2026-11-06.

### propozycja retrospektywy
- **Znaczenie:** gotowa zmiana jednego pliku z id `<run-id>/P<n>`, zatwierdzana lub odrzucana decyzją.
- **Nie oznacza:** kandydata z `skillsync retro`.
- **Źródło:** [retro-proposals](retro-proposals.md), 2026-11-06.

### rejestr propozycji
- **Znaczenie:** sekcja `proposals:` pliku rejestru decyzji `docs/retro/decisions.yaml` (cały plik to rejestr decyzji); decyzje końcowe, źródło stanu pary.
- **Nie oznacza:** `retro-decisions.json` przebiegu (historia stanów).
- **Źródło:** [retro-proposals](retro-proposals.md), 2026-11-06.
