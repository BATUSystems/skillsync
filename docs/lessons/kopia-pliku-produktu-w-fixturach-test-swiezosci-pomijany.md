---
title: 'Kopia pliku produktu w fixturach i test świeżości pomijany bez zmiennej środowiska: punktowy bieg niczego nie dowodzi'
tags: [skillsync, tests, fixtures, merge-modules, freshness, skip]
paths: ['scripts/merge_modules.py', 'tests/fixtures/prototype/engine-v2/SOURCE.md', 'tests/test_skillsync_prototype_engine.py']
source: 'prace nad wydaniem 1.8.0: kopia silnika prototypów w fixturach i dobór modułów skryptem scalania'
verified: 2026-11-17
---
**Objaw:** zmieniono plik produktu, którego kopia leży w fixturach testowych, a punktowy zestaw testów jest zielony. Test świeżości kopii istnieje, ale pomija się, gdy nie ustawiono zmiennej wskazującej źródło, więc nikt go nie uruchomił z efektem.

**Przyczyna:** zależność „plik produktu → jego kopia w fixturze” nie jest ani importem, ani nazwą w teście, więc dobór modułów jej nie widzi, a pominięty test wygląda jak zielony. Pominięcie bez zmiennej to brak dowodu, nie zaliczenie.

**Reguła:** skrypt doboru modułów zna takie zależności jawną regułą (zmiana pliku produktu albo jego kopii dobiera test świeżości) i ostrzega, gdy zmiennej brak. Bieg, który ma coś udowodnić, ustawia zmienną; `check_skips` na logach liczy pominięcie takiego testu jako lukę. Kopia w fixturze ma plik `SOURCE` z miejscem pochodzenia.

**Jak wykryć:** w logu testów `skipped` przy teście świeżości; `merge_modules --names` dla zmiany pliku produktu musi wymienić test świeżości; porównanie kopii z oryginałem poza testem (`diff`).
