# Dokumentacja Skillsync

Zacznij od ścieżki, która pasuje do Ciebie. Linki są względne i działają na GitHubie.

## Ścieżki czytania

**Programista organizacji.** Zacznij od README warstwy swojej organizacji: podaje polecenia podłączenia i kontakt.
Potem: [installation](installation.md) (instalacja i weryfikacja) → [configuration](configuration.md) (warstwy na Twojej maszynie) → [manifest](manifest.md) (konfiguracja projektu).

**Użytkownik z zewnątrz.** [README rdzenia](../README.md) (instalacja jedną linią i szybki start bez warstwy organizacji) → [installation](installation.md) → [layers-authoring](layers-authoring.md) (własna warstwa osobista).

**Autor warstwy.** [layers-authoring](layers-authoring.md) → [przykład warstwy personal](examples/personal-layer/) → [security](security.md) (`leakcheck`, hooki git).

**Użytkownik agent flow.** [workflow](workflow.md) (akty, role, zgody) → [manifest](manifest.md) (klucze projektu, hooki, język interfejsu) → [dashboard-contract](dashboard-contract.md) (strona statusu) → [prototypes](prototypes.md) (makiety HTML).

**Współtwórca rdzenia.** [CONTRIBUTING](../CONTRIBUTING.md) → [development](development.md) (środowisko, testy, wydanie) → [architecture](architecture.md); dowody pomiarów i sond testowych: [measurements/](measurements/), [probes/](probes/).

## Spis

| Plik | Zawartość |
|---|---|
| [../README.md](../README.md) i [../README.pl.md](../README.pl.md) | strona wejściowa: co to jest, dla kogo, instalacja, szybki start, mapa dokumentacji |
| [../LICENSE](../LICENSE) | licencja MIT |
| [../CHANGELOG.md](../CHANGELOG.md) | zmiany według wydań, z sekcją **Breaking** |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | budowa, testy, zasady treści, `leakcheck` przed PR |
| [../SECURITY.md](../SECURITY.md) | zgłaszanie podatności |
| [installation.md](installation.md) | wymagania, Linux / macOS / Windows, weryfikacja, aktualizacja, odinstalowanie, migracja, rozwiązywanie problemów |
| [configuration.md](configuration.md) | config maszyny: `layers`, `layer_rules`, `isolation`, `leakguard`, `discover_paths`, zmienne środowiskowe |
| [manifest.md](manifest.md) | manifest projektu `.github/.skillsync.yaml`: skille, `language`, `hooks.*`, `claude.*`, `workflow.*` (w tym `specs`), `dispatch`, `delivery` |
| [cli.md](cli.md) | referencja komend i kodów wyjścia |
| [workflow.md](workflow.md) | agent flow: akty I–III, role, karty i dyspozycja, bramka prototypu, tryb dostarczania, zgody, retrospektywa, biegi podumysłów i rundy review, komendy kontrolne |
| [workflow-details.md](workflow-details.md) | pełny cykl orkiestratora i podumysłów |
| [prototypes.md](prototypes.md) | klikalne prototypy HTML: tworzenie i synchronizacja silnika, serwer z komentarzami, akceptacja, bramka, pytania do eksperta |
| [prototype-contract/](prototype-contract/) | kontrakt silnika prototypów: scenariusze E2E, słownik HTML i CSS, API eksportów |
| [test-adapters.md](test-adapters.md) | adaptery wyjścia narzędzi testowych dla `test-run`: stan pojedynczych testów zapisywany do pliku przebiegu i pokazywany w zakładce Testy |
| [dashboard-contract.md](dashboard-contract.md) | kontrakt JSON `schema: 1` strony statusu: `dashboard show\|serve\|metrics`, `approvals record`, endpointy serwera, szacunki końca (`eta`, `estimates`), biegi podumysłów (`agents`), zgłoszenia do warstw |
| [layers-authoring.md](layers-authoring.md) | jak założyć i utrzymywać warstwę organizacji lub osobistą |
| [examples/personal-layer/](examples/personal-layer/) | kompletny przykład warstwy personal |
| [security.md](security.md) | model izolacji, `leakcheck`, hooki git, CI |
| [upgrading.md](upgrading.md) | SemVer, `core_min`, zmiany niezgodne, migracja maszyn |
| [architecture.md](architecture.md) | budowa generatora, składanie warstw, artefakty dla hostów agentów |
| [development.md](development.md) | środowisko deweloperskie, testy, dry-run, pułapki |
| [site/](site/) | generator strony HTML z `workflow.md` i `workflow-details.md`: budowany lokalnie, HTML nie jest commitowany ani publikowany |
| [measurements/](measurements/) | zapisy pomiarów, do których odsyłają tabele „zmierzone” w dokumentacji |
| [probes/](probes/) | dowody sond testowych narzędzi (SQL Server): skrypty i wyniki |
| [domain/](domain/) | wiedza domenowa dla agentów (indeks `index.yaml`) |
| [lessons/](lessons/) | lekcje z pracy agentów (indeks `index.yaml`) |

`domain/` i `lessons/` zawierają rekordy bez treści organizacji i osób; ich spis jest w `index.yaml` każdego katalogu.

`site/` zawiera generator strony dokumentacji; strona nie jest publikowana.

