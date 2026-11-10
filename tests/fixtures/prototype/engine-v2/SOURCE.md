# Kopia silnika prototypów w testach

`prototype.js` w tym katalogu to kopia silnika z biblioteki skilli: `skills/prototype/assets/repo/prototype.js`
(rdzeń, commit `74aa7ff` na gałęzi `feat/proto-ux`: link ⌂ do galerii w pasku ekranów, sha256 `e990f17fe7d10a93113fb7768d46c6800b7a0f3504f341bdc18f89dd1c779684`). Testy silnika ładują ją domyślnie; inną ścieżkę wskazuje
`SKILLSYNC_PROTOTYPE_ENGINE_V2_PATH`.

Świeżość kopii: test `test_v2_copy_matches_library_source` porównuje ją z plikiem wskazanym przez
`SKILLSYNC_PROTOTYPE_ENGINE_V2_SOURCE` (np. `<biblioteka>/skills/prototype/assets/repo/prototype.js`); jawne pominięcie tej jednej
kontroli: `SKILLSYNC_PROTOTYPE_ENGINE_SKIP_FRESHNESS=1`. Po zmianie silnika w bibliotece skopiuj plik tutaj i zaktualizuj
`ENGINE_V2_SHA256` w `tests/test_skillsync_prototype_engine.py`.

Pomocnicze skrypty `diff.cjs` i `queue.cjs` uruchamiają eksporty silnika na danych wejściowych w JSON (testy różnicowe).
