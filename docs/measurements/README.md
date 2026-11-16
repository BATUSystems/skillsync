# Zapisy pomiarów

Tabela w dokumentacji, która twierdzi, że coś **zmierzono**, odsyła do zapisu w tym katalogu (kolumna „Pomiar”: `zmierzone: [id](measurements/id.md)`).
Wiersz bez zapisu jest oznaczony `niezmierzone` albo `oczekiwane`. Pilnuje tego `tests/test_skillsync_measurements.py`.

Zapis `<id>.md` ma pola, każde w osobnej linii: `Data:`, `System:`, `Wersja:` (produktu i Pythona), `Polecenie:`, `Wynik:`; może mieć `Uwagi:`.
Bez nazw maszyn i osób: system w formie „Windows 11, Python 3.12.16”.
