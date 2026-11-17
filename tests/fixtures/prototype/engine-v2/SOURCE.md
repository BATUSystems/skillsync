# Kopia silnika prototypów w testach

`prototype.js` w tym katalogu to kopia silnika z biblioteki skilli: `skills/prototype/assets/repo/prototype.js`
(rdzeń, commit `2bc5cf3` (180-P; wcześniej `035588e` na gałęzi `feat/prototype-wait-reply`): plakietka „AI” przy wpisach agenta, autor decyzji w UI; PROTO-NAV: domek w pasku strip i `screenNavMode`; writing marker: powód `writing` w blokadach akceptacji i przywracania, baner „Agent jeszcze pisze ekrany (do HH:MM)” z przyciskiem „Zwolnij” dla akceptującego i dewelopera; 1.6.1: uwaga „Treść jest widoczna dla innych i nie jest maskowana…” pod polem komentarza, odpowiedzi w wątku, edycji wpisu i powodu przywrócenia, tylko w trybie z serwerem (sam JS, CSS bez zmian); 180-P: znaczniki `prototype.dark_theme` na `<html>` (silnik bez przycisku Motyw), silnik w pokazie z etykietą i bez narzędzi komentowania; sha256 poniżej to suma tej kopii (po zmianie 180-P), `81e88d7190b91729dee518ab32c7d3348970a098e70903c10997691838a3305d`). Testy silnika ładują ją domyślnie; inną ścieżkę wskazuje
`SKILLSYNC_PROTOTYPE_ENGINE_V2_PATH`.

Świeżość kopii: test `test_v2_copy_matches_library_source` porównuje ją z plikiem wskazanym przez
`SKILLSYNC_PROTOTYPE_ENGINE_V2_SOURCE` (np. `<biblioteka>/skills/prototype/assets/repo/prototype.js`); jawne pominięcie tej jednej
kontroli: `SKILLSYNC_PROTOTYPE_ENGINE_SKIP_FRESHNESS=1`. Po zmianie silnika w bibliotece skopiuj plik tutaj i zaktualizuj
`ENGINE_V2_SHA256` w `tests/test_skillsync_prototype_engine.py`.

Pomocnicze skrypty `diff.cjs` i `queue.cjs` uruchamiają eksporty silnika na danych wejściowych w JSON (testy różnicowe).
