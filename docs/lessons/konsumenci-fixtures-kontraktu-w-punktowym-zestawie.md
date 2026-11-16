---
title: Punktowy zestaw testów pomija konsumentów fixtures kontraktu
tags: [skillsync, tests, dashboard, fixtures, ci]
paths: ['tests/fixtures/dashboard/**', 'tests/test_skillsync_approvals_record.py', 'tests/test_skillsync_dashboard*.py']
---
Dowód: paczka A strony statusu (15.11) dołożyła pola do kontraktu i zregenerowała `tests/fixtures/dashboard/*/expected.json`; moduły `dashboard*` przechodziły, a CI (wszystkie 11 jobów Linux i macOS) padło na jednym teście
`test_skillsync_approvals_record.CpReadyFixture`, który porównuje wynik z drugim plikiem fixture, `cp-ready/expected-after.json`. Plik nie nazywał się `expected.json`, a moduł konsumenta nie ma w nazwie `dashboard`.

Reguła: po zmianie kontraktu albo fixture wyszukaj konsumentów po NAZWIE PLIKU i katalogu fixture (`grep -l "expected-after\|fixtures.*dashboard" tests/*.py`), nie po nazwach modułów, i uruchom je przed pushem. Regeneruj wszystkie warianty
(`expected*.json`), nie tylko podstawowy; dopuszczalna jest wyłącznie zmiana przez dołożenie kluczy (`git diff --numstat` bez usuniętych linii).

Dotknięte obszary: `tests/fixtures/dashboard/`, moduły czytające fixtures (`dashboard`, `dashboard_contract`, `approvals_record`, `dashboard_renderer`). Granice aktualności: do czasu jednego pomocnika, który zbiera konsumentów fixture.
