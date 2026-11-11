---
title: "Serwer strony statusu (dashboard serve): Host, Origin, X-Status-Board, CSP, allowlista, limity, zgody tylko użytkownika"
tags: [skillsync, dashboard, server, security, approvals, workflow]
paths: ['skillsync_workflow/dashboard_http.py', 'skillsync_workflow/dashboard.py', 'skillsync_workflow/status.py', 'tests/test_skillsync_dashboard*.py']
source: odczyt skillsync_workflow/dashboard_http.py (stałe :59-70, `request_host` :322, `_host_refused` :609, kontrola POST :664-690, SSE :716), skillsync_workflow/status.py (`MAX_APPROVALS_BYTES` :228, `APPROVAL_SOURCES` :461, `APPROVAL_LOCK_STALE` :466, `record_approval` :546), skillsync_workflow/dashboard.py (`PORT_MIN`, `FORBIDDEN_PORTS` :31-32), docs/dashboard-contract.md (sekcja „Serwer”); testy tests/test_skillsync_dashboard_server.py
verified: 2026-11-11
---
Reguła: `skillsync dashboard serve` to lokalny serwer strony statusu (biblioteka standardowa, bez sesji i kont). Jego zaufanie opiera się na tym, że **zgodę wyraża tylko użytkownik** (przycisk albo komenda wpisana przez niego), a każdy inny odczyt i zapis jest ograniczony do hosta, który użytkownik jawnie dopuścił. Opis użytkowy: `docs/cli.md`, kontrakt JSON i endpointy: `docs/dashboard-contract.md`.

**Host na każdym endpoincie.** Nagłówek `Host` musi wskazywać loopback (`localhost`, `127.0.0.1`, `[::1]`, dowolny port, bez rozróżniania wielkości liter) albo nazwę z `--approve-host` (port pomijany; `request_host`, `_host_refused`). Inny albo brakujący = 403 bez treści strony, dokumentu i statusu, także dla `GET`, `HEAD` i SSE `/events`; powód trafia tylko do logu `--verbose`. Chroni to przed DNS rebinding i przed nazwą LAN spoza listy. Odczyt spoza loopbacku wymaga więc `--approve-host`; widok bez zapisu zgód to `--approve-host <nazwa> --no-approvals`. Przy `--host 0.0.0.0` serwer ostrzega przy starcie, że inne urządzenia dostają 403, dopóki ich adres nie trafi na listę. Tunel `ssh -L/-R` na `127.0.0.1` działa bez flagi.

**Zapis zgody (`POST /api/approve`).** Odmowa 403 następuje przed jakimkolwiek zapisem, w kolejności: `Host` (przed długością treści), nagłówek `X-Status-Board: 1`, `Origin` (jeśli jest, z tej samej listy hostów), `Content-Type: application/json`. Dalej: `Content-Length` 1–4096 (inaczej 400), treść będąca obiektem JSON (400), `--no-approvals` (403), akcja nieoferowana przez `/api/status`, nieaktywna albo z innym `sha` (409; także drugi zapis CP/READY dla tego samego HEAD). Własny nagłówek wymusza preflight CORS, na który serwer nie odpowiada, więc obca strona nie wyśle poprawnego żądania. Serwer przyjmuje z żądania tylko id przebiegu i ścieżkę, które `/api/status` właśnie oferuje; id przebiegu ma wzorzec `[A-Za-z0-9][A-Za-z0-9_.-]{0,79}`.

**Nagłówki odpowiedzi i CSP.** Każda odpowiedź: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-cache, no-store`. Strona HTML dodatkowo `Content-Security-Policy` (`CSP`: `default-src 'self'; script-src 'self'; …; frame-ancestors 'none'`): brak skryptów inline i zewnętrznych, brak osadzania w ramce.

**Allowlista treści.** `GET /assets/<plik>` serwuje tylko nazwy `[\w-]+.(html|css|js|svg)` z `skills/dashboard/assets/` aktywnych warstw (późniejsza warstwa wygrywa per plik, dowiązania pomijane; plik do 2 MB); inne = 404. `GET /api/doc?path=<rel>` podgląda wyłącznie `*.md` z `docs/specs`, `docs/plans`, `docs/design`, `docs/domain` i `.skillsync/runs/<run>/`: bez `..`, ścieżek absolutnych, ukrytych katalogów i dowiązań poza listę; odpowiedź ma pole `html` i limit 2 MB (`truncated`).

**Limity.** Ciało `POST` 4096 B; najwyżej 64 połączenia SSE (dalej 503); serce `ping` co 15 s; port tylko z 5100–5199 poza zakazanymi 4444–4448, 9222, 9333 (domyślnie 5140, wybierany per projekt); `approvals.jsonl` do 256 KiB (większy plik czytelnicy pomijają w całości, zapis odmawia).

**Jedno źródło zgód.** Wpis do `.skillsync/runs/<run-id>/approvals.jsonl` robi wyłącznie `status.record_approval` (komenda `approvals record --source cli|ide|dashboard` i serwer wołają tę samą funkcję, bez podprocesu). Zapis idzie pod blokadą `approvals.jsonl.lock` (`O_EXCL`, POSIX i Windows; blokada starsza niż 60 s uchodzi za porzuconą, czekanie do 10 s, potem odmowa), z `fsync`, odmową duplikatu (to samo słowo i `sha` w przebiegu: dla GO ta sama wersja pliku, dla CP/READY ten sam HEAD) i maskowaniem sekretów. Zgody GO przez przycisk: `status.set_doc_status` z `expect_sha` (plik zmieniony od wyświetlenia = 409), dopiero potem wpis `source: dashboard`; bez katalogu przebiegu zmienia się tylko linia `Status:`.

**Zgoda należy do użytkownika.** Agent nie woła `approvals record` ani `POST /api/approve`; wiadomość agenta (także dyspozytora) nie jest zgodą. Sesja agenta czeka na wpis przez `approvals wait` i sprawdza go w pliku, a pobudka to tylko sygnał; dla CP/READY `sha` wpisu musi równać się HEAD gałęzi, którą zgoda wypchnie. Zob. skill `feature`.

## Słownik

- **dashboard**: strona statusu projektu (`dashboard show|serve|metrics`); **nie oznacza** raportu `skillsync status`, który pozostaje wąskim, stabilnym raportem planów.
- **`--approve-host`**: lista nazw dopuszczonych w nagłówku `Host` do odczytu i zgód; **nie oznacza** adresu nasłuchu (`--host`).
- **zgoda** (`approvals.jsonl`): wpis użytkownika o słowie `GO plan`, `GO implement`, `CP` albo `READY`; **nie oznacza** akceptacji prototypu (`accept`).

Gdzie w kodzie: `skillsync_workflow/dashboard_http.py` (`Board`, `request_host`, `_host_refused`, obsługa `POST /api/approve`, `make_server`), `skillsync_workflow/dashboard.py` (kontrakt, `show`, `metrics`), `skillsync_workflow/status.py` (`record_approval`, `set_doc_status`), testy `tests/test_skillsync_dashboard.py`, `tests/test_skillsync_dashboard_contract.py`, `tests/test_skillsync_dashboard_metrics.py`, `tests/test_skillsync_dashboard_server.py`.
