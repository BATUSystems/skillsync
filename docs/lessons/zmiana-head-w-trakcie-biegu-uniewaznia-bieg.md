---
title: "Zmiana HEAD lub plików testowanych w trakcie biegu unieważnia wynik: PASS z mieszanym stanem nie dowodzi żadnego commita"
tags: [testing, unittest, e2e, git, evidence, runs]
paths: ['tests/**', 'tests/e2e/prototype/**', 'tests/fixtures/**']
source: 'zdarzenie z 2026-11-05: pełny bieg unittest Skillsync (exit 0, Ran 1942 OK) z HEAD zmienionym w trakcie biegu (reflog: commit d8fab80 o 17:41:20, 9a9026b o 17:43:30, bieg ok. 17:35–17:47); powtórka pełnego E2E prototypów na stanie 9a9026b z HEAD zapisanym przed i po biegu'
verified: 2026-11-05
---
**Objaw:** pełny bieg `unittest` kończy się `OK` (exit 0), a w jego trakcie HEAD zmienia się dwa razy: commit d8fab80
(przywrócenie fikstur v1) w połowie biegu, potem 9a9026b. Wynik nie odpowiada żadnemu z tych stanów.

**Przyczyna:** moduły testów są importowane na starcie, więc pochodzą z jednego stanu. Podprocesy, skrypty uruchamiane
przez `subprocess` i odczyty fikstur idą na pliki z dysku w chwili odczytu, czyli z innego stanu. Edycja worktree w trakcie
biegu tworzy stan mieszany, którego żaden commit nie opisuje.

**Reguła:**
- Od startu dzierżawy do RELEASE zero edycji w worktree biegu: kodu, testów, fikstur i skryptów. Zero zmian HEAD
  (commit, amend, checkout, reset, rebase, pull) w tym samym oknie.
- Naruszenie unieważnia bieg. Pełny bieg powtórz od zera na stanie końcowym. Poprzedni PASS nie jest dowodem
  ani dla starego, ani dla nowego HEAD.
- W logu i raporcie zapisz HEAD przed i po biegu, wynik `git status --porcelain` po biegu (ma być pusty) i numer dzierżawy.

**Jak wykryć:**
- `git rev-parse HEAD` przed i po biegu: różne wartości = bieg unieważniony.
- `git reflog --date=iso -n 10`: wpisy z godzinami w oknie biegu (start i koniec).
- `git status --porcelain` po biegu: niepuste wyjście = edycja w trakcie lub po biegu, sprawdź, czy była w oknie.
