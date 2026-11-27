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
| `unified_xss_e2e.py` | XSS-P1…P3 (strona), XSS-O1…O3 (origin prototypów) | K8, K21 |
| `unified_isolation_e2e.py` | ISO-1…ISO-6, SO-1…SO-2 (`--single-origin`, kontrola negatywna) | K17, K20 |
| `unified_handoff_e2e.py` | HO-1…HO-6 | K8, K18 |

Poza zakresem do czasu widoków U-4: zgody GO/CP/READY klikane w Chrome, widok anonimowy z pseudonimami, nazwy i komentarze prototypów na stronie,
ramka prototypu w zakładce (w tym `top` i sandbox, K17(d) i K20).
