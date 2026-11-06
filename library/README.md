# Warstwa core biblioteki skillsync

To warstwa rdzenia publicznego `skillsync`. Zawiera wspólną część biblioteki, którą generator składa
w pliki projektów:

- `skills/`: skille (np. `feature`, `bugfix`, `workflow`, `dispatch`) z referencjami,
- `instructions/`: instrukcje bazowe i fragmenty dla typów projektów,
- `agents/`: role podumysłów (Explore, implementer, test-writer, test-runner, reviewer i inne),
- `workflow/`: mapa routingu (`defaults.yaml`), benchmarki i lekcje (`workflow/lessons/`).

Treść jest ogólna: bez nazw klientów, organizacji i danych osobistych. Rzeczy specyficzne dla organizacji
dokładasz w warstwie `org`, a osobiste (persona, nawyki, imiona kart) w warstwie `personal`. Warstwa core
jest w configu użytkownika tylko do odczytu: nie edytujesz jej w miejscu, zmiany wprowadza się w repozytorium
rdzenia.

Podłączenie: `skillsync layers add core --kind core --source <adres-repo> --subdir library --ref vX.Y.Z`, potem `skillsync layers check`
i `skillsync link <projekt>` (szczegóły: [README repozytorium](../README.md) oraz
[docs/layers-authoring.md](../docs/layers-authoring.md)).

Wersja warstwy jest w `layer.yaml` (`version`) i odpowiada wersji generatora. Historia zmian:
[CHANGELOG.md](CHANGELOG.md).
