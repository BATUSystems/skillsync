---
name: build-deployment
description: Cross-project build/deploy patterns, versioning, changelog hygiene and CI/CD workflow guidance.
user-invocable: true
---

# Build & Deployment (Cross-Project)

Ten skill zawiera neutralne wzorce procesu build/deploy niezależne od konkretnego stacku projektu.

## Kiedy używać

Użyj tego skilla, gdy potrzebujesz:

- uporządkować proces release,
- zdefiniować zasady wersjonowania,
- przygotować checklistę przed wdrożeniem,
- doprecyzować role CI/CD,
- ustandaryzować changelog.

## Release flow (baseline)

1. Ustal scope wydania i freeze zmian.
2. Uruchom testy i build w trybie produkcyjnym.
3. Zaktualizuj wersję zgodnie z polityką semver/projektową.
4. Zaktualizuj changelog (`Added`/`Changed`/`Fixed`/`Removed`).
5. Zbuduj artefakt(y) i opublikuj je w pipeline.
6. Wykonaj smoke test po deploy.
7. Przygotuj rollback plan.

## Versioning rules

- Zawsze trzymaj pojedyncze źródło prawdy wersji.
- Wersję podnoś tylko raz per release commit.
- Release candidate oznaczaj jawnie (`-rc.N`) jeśli proces to przewiduje.

## Changelog rules

- Pisz zmiany zorientowane na efekt biznesowy i techniczny.
- Grupuj wpisy według kategorii (`Added`, `Changed`, `Fixed`, `Removed`).
- Unikaj wpisów typu „misc fixes”.

## CI/CD hygiene

- Pipeline PR: lint + test + build.
- Pipeline release: pełna walidacja + publikacja artefaktów.
- Każdy krok powinien mieć czytelny log i kod wyjścia.

## Project-specific variants

Pliki `references/org-*.md`, jeśli są, to rozszerzenia warstwy organizacji: przeczytaj te, które pasują do projektu.
