---
name: skill-profiles
description: Select additive core and optional skill packages and inspect the discovery
  catalog budget without expanding instruction bodies.
---

# skill-profiles

Profile w workflow/skill-profiles.yaml dodają skille do jawnych skills manifestu.
Wspólne defaults wybierają core/testing/delivery; opcjonalne ui/security/migration/infra
włączaj według projektu. Jawne selected: [] zachowuje dystrybucję legacy. Nie usuwaj
ręcznie wybranych skilli. Profil zawiera tylko literalne nazwy, tags/globs należą do manifestu.
`skillsync profiles .` pokazuje wynik i rozmiar katalogu name/description; budget nie mierzy
całych treści skilli. `doctor` kontroluje limit i instalację, `link` aktualizuje hosty.
Opis skilla musi mówić kiedy go używać; szczegóły czytaj na żądanie, nie w rdzeniu.

Kontrakty i przykłady: [workflow v2](../workflow/references/eight.md).
