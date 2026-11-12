---
title: 'Nowa reguła stanu w instrukcjach: przejdź wszystkich konsumentów i stany, których reguła nie przewiduje'
tags: [workflow, instrukcje, plan, review]
paths: ['docs/plans/*', 'skills/feature/*', 'skills/pr-run/*']
source: ".skillsync/runs/2026-11-01-proces-f3/ (plan-review-1.md, 3.5-blind.md, 3.7-review.md, 3.7-blind-2.md); plan zadania (poza rdzeniem)"
verified: 2026-11-01
---
**Objaw:** plan fazy 3 po dwóch przeglądach i ślepej próbie 26 scenariuszy nadal pozwalał zamrozić niezatwierdzony spec jako wdrożony, a `/pr-run` tworzył PR z linią `Spec:` wskazującą plik spoza gałęzi. Znalazł to dopiero przegląd pełnego diffu.

**Dlaczego:** scenariusze i przeglądy planu szły po poprawnej ścieżce stanów (`draft`, `approved`, `implemented`). Warunek „porównaj sha, gdy spec jest `approved`” po cichu wyłączał kontrolę przy każdym innym stanie. Lista plików planu pominęła `pr-run`, choć `skills/pr-run/SKILL.md:20` kieruje dużą zmianę do `plan-large`. Poprawka pierwszego z tych błędów sama dała nowy stan (plan z powrotem na `draft`, który `plan approve` mógł przepuścić z otwartymi pytaniami).

**Co działa:** przy regule stanu ustaw warunek jako listę dozwolonych stanów, a każdy inny stan to STOP. Rozpisz macierz stan speca × stan planu, nie tylko przejścia. Dla każdego trafienia grepa po zmienianym pojęciu przejdź kroki konsumenta od wejścia do końca. Ślepej próbie dawaj też scenariusze ze stanem niepoprawnym.
