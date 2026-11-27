# Testy przeglądarkowe CORE-2000-UNIFIED (U-7)

Prawdziwy `skillsync dashboard serve` projektu tymczasowego, własny headless Chrome (profil tymczasowy, port wybrany przez Chrome) i scenariusze node
przez CDP (sterownik `../prototype/lib.mjs`). Fikstury i konta: `tests/unified_helpers.py`. Nie wchodzą do `discover -s tests`.

Wymagania: Linux, macOS albo Windows; node >= 22; Chrome albo Edge (zwykłe miejsca instalacji albo `SKILLSYNC_E2E_CHROME`). Brak któregoś to błąd, nie skip.

```bash
python3 -m unittest discover -s tests/e2e/unified -p '*_e2e.py'
```

- krótki katalog na gniazdo przeglądarki przy długim TMPDIR (macOS): `SKILLSYNC_E2E_SHORT_DIR` (domyślnie `/tmp`);
- uruchomiona przeglądarka: `browser.json` obok wyników;
- porty serwera: `SKILLSYNC_TEST_DASHBOARD_PORTS` (domyślnie 5180-5189);
- wyniki scenariuszy jako JSON: `SKILLSYNC_E2E_EVIDENCE=<katalog>`;
- na maszynie współdzielonej: pod blokadą ciężkich biegów i z limitem pamięci (ok. 4 GB).

| Plik | Scenariusze | Kryteria |
|---|---|---|
| `unified_xss_e2e.py` | XSS-P1…P4 (strona, z oknem zgody), XSS-O1…O3 (origin prototypów) | K8, K21 |
| `unified_isolation_e2e.py` | ISO-1…ISO-6, SO-1…SO-2 (`--single-origin`, kontrola negatywna) | K17, K20 |
| `unified_markdown_inject_e2e.py` | MD-1…3 (treść nie przykrywa strony ani przycisków zgody) | K21 |
| `unified_handoff_e2e.py` | HO-1…HO-6 | K8, K18 |
| `unified_consent_e2e.py` | CP-1…9, READY-1…9, GO plan-1…7, GO implement-1…7 | zgody w przeglądarce (K3, K4, K13) |
| `unified_anonymous_e2e.py` | ANON-1…3 | K2 w przeglądarce; treść dokumentów jest anonimowi jawna i tylko odnotowana |
| `unified_anonymous_more_e2e.py` | ANON-4 (zakładka „Testy”), ANON-5 (origin prototypów) | K2; surowe wyniki testów są jawne i tylko odnotowane, pola składane przez serwer są sprawdzane |

Poza zakresem: ramka prototypu w zakładce strony (`top`, sandbox; K17(d), K20) i nazwy oraz komentarze prototypów na stronie (tych widoków strona jeszcze
nie ma); przyciski zakładki „Historia” (serwer z wieloma zakładkami).
