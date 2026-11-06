# Dokumentacja Skillsync

Zacznij od ścieżki, która pasuje do Ciebie. Linki są względne i działają na GitHubie.

## Ścieżki czytania

**Programista organizacji.** Zacznij od README warstwy swojej organizacji: podaje polecenia podłączenia i kontakt.
Potem: [installation](installation.md) (instalacja i weryfikacja) → [configuration](configuration.md) (warstwy na Twojej maszynie) → [manifest](manifest.md) (konfiguracja projektu).

**Użytkownik z zewnątrz.** [README rdzenia](../README.md) (instalacja jedną linią i szybki start bez warstwy organizacji) → [installation](installation.md) → [layers-authoring](layers-authoring.md) (własna warstwa osobista).

**Autor warstwy.** [layers-authoring](layers-authoring.md) → [przykład warstwy personal](examples/personal-layer/) → [security](security.md) (`leakcheck`, hooki git).

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
| [manifest.md](manifest.md) | manifest projektu `.github/.skillsync.yaml` |
| [cli.md](cli.md) | referencja komend i kodów wyjścia |
| [workflow.md](workflow.md) | agent flow: akty I–III, role, karty i dyspozycja, komendy kontrolne |
| [workflow-details.md](workflow-details.md) | pełny cykl orkiestratora i podagenta (podumysł) |
| [prototypes.md](prototypes.md) | klikalne prototypy HTML: `init`, `sync`, `index`, `list`, galeria, komentarze |
| [layers-authoring.md](layers-authoring.md) | jak założyć i utrzymywać warstwę organizacji lub osobistą |
| [examples/personal-layer/](examples/personal-layer/) | kompletny przykład warstwy personal |
| [security.md](security.md) | model izolacji, `leakcheck`, hooki git, CI |
| [upgrading.md](upgrading.md) | SemVer, `core_min`, zmiany niezgodne, migracja maszyn |
| [architecture.md](architecture.md) | budowa generatora, składanie warstw, artefakty dla hostów agentów |
| [development.md](development.md) | środowisko deweloperskie, testy, dry-run, pułapki |
| [site/](site/) | generator strony dokumentacji: Markdown → HTML w CI, HTML nie jest commitowany |
| [domain/](domain/) | wiedza domenowa dla agentów (indeks `index.yaml`) |
| [lessons/](lessons/) | lekcje z pracy agentów (indeks `index.yaml`) |

`domain/` i `lessons/` zawierają rekordy bez treści organizacji i osób; ich spis jest w `index.yaml` każdego katalogu.

`site/` zawiera generator strony dokumentacji; strona nie jest publikowana.

