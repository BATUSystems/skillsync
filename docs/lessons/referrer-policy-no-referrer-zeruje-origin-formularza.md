---
title: "`no-referrer` na stronie z formularzem daje `Origin: null`; testy po HTTP z ręcznie wpisanym Origin tego nie zobaczą"
tags: [skillsync, security, browser, testing, origin, referrer-policy, forms, handoff]
paths: ['skillsync_workflow/prototype_server.py', 'skillsync_workflow/dashboard_http.py', 'tests/test_skillsync_prototype_handoff.py', 'tests/test_skillsync_unified_e2e_handoff_page.py', 'tests/e2e/unified/**']
source: 'zdarzenie z 2026-11-27: przekazanie sesji na origin prototypów nie działało w przeglądarce (403 `invalid_origin`) przy zielonych testach serwera; wykrył je pierwszy test w prawdziwej przeglądarce'
verified: 2026-11-27
---
**Objaw:** nawigacja formularzem `POST` w przeglądarce dostaje 403 `invalid_origin`, a testy serwera tej samej trasy są zielone.

**Przyczyna:** strona z formularzem miała `Referrer-Policy: no-referrer` (nagłówek i `<meta name="referrer">`). Przy takiej polityce przeglądarka wysyła
z formularzem `Origin: null`, a serwer porównujący `Origin` z `Host` odmawia. Testy po HTTP same wpisywały poprawny `Origin`, więc sprawdzały
żądanie, którego przeglądarka nigdy nie wyśle.

**Reguła:**
- Stronę, która wysyła formularz `POST` na trasę sprawdzającą `Origin`, serwuj z `same-origin`, w nagłówku i w `<meta>` (znacznik nadpisuje nagłówek).
  Sekret we fragmencie adresu i tak nie trafia do `Referer`. Odmowy dla `Origin: null` nie rozluźniaj.
- Protokół oparty na zachowaniu przeglądarki (przekierowania, formularze, ciasteczka, ramki) ma co najmniej jeden bieg w prawdziwej przeglądarce.
  Test serwera powtarza nagłówki zmierzone w przeglądarce, nie wymyślone.

**Jak wykryć:** `Origin: null` w nagłówkach żądania w narzędziach przeglądarki (CDP: `Network.requestWillBeSentExtraInfo`); `no-referrer` na stronie
z `<form method="post">`.
