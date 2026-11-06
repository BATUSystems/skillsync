---
title: "E2E z logowaniem: podumysł czytający w całości config.yaml lub secrets.json środowiska testowego wypisuje hasła do transkryptu"
tags: [testing, e2e, secrets, agents, security, prototype]
paths: ['.skillsync/runs/**/tools/e2e-*.mjs', 'skillsync_workflow/prototype_server.py', 'skillsync_workflow/prototype.py']
source: .skillsync/runs/2026-11-04-prototype-komentarze.karta-2/3.4-e2e-test-runner.md (odkrycie 5, sekcja „Bezpieczeństwo”); wcześniejszy przypadek z secrets.json W1 (2026-11-04, raport karty karta-2 do Dyspozytora); decyzja Dyspozytora 2026-11-05
verified: 2026-11-05
---
**Objaw:** w jednej paczce dwa razy podumysł (test-runner) przy przygotowaniu E2E odczytał w całości plik środowiska testowego
(`secrets.json` z hasłami kont testowych, potem `config.yaml` z polem `default_password`), więc wartości trafiły do jego
transkryptu, choć zlecenie zakazywało wypisywania haseł. Zakaz „nie wypisuj w logach i raporcie” nie obejmuje samego odczytu
narzędziem, którego wynik i tak widzi model.

**Przyczyna:** sprawdzenie „co jest w pliku” (`cat`, `Read`, `json.load` + `print`) to naturalny pierwszy krok przy nowym
środowisku. Pliki konfiguracji testowej mieszają klucze potrzebne do zestawienia środowiska (ścieżki, porty) z sekretami.

**Reguła (każde zlecenie dla test-runnera i implementera z dostępem do środowisk testowych):**
- Nie czytaj w całości `config.yaml`, `secrets.json`, `users.json`, `demo-secrets.json` ani `*.env` środowisk testowych.
- Pojedyncze pola wyciągaj bez wartości sekretów: tylko klucze (`list(d)`), obecność (`bool`) albo `len(...)`;
  porównanie dwóch sekretów bez wypisania (`a == b`).
- Hasło do logowania w E2E bierze skrypt (z pliku albo zmiennej) i przekazuje bezpośrednio do przeglądarki/pty; nigdy na stdout,
  do logu ani do zrzutu.
- Gdy sekret jednak trafi do transkryptu: zgłoś od razu orkiestratorowi bez powtarzania wartości; orkiestrator sprawdza bez
  wypisywania, czy to sekret syntetyczny czy prawdziwy, i czy wartość nie leży w plikach runu.

**Jak wykryć:** przed raportem `python3 -I` z porównaniem wartości sekretów do treści plików w `.skillsync/runs/<run>/` (wypisuje
tylko nazwy plików z trafieniem); przegląd zleceń: każde zlecenie E2E zawiera powyższe trzy zakazy wprost.
