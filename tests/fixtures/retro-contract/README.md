# Fixture kontraktu retro (R2)

Pliki w tym katalogu to wzorcowe wyniki poleceń generatora Skillsync, z których korzysta Skippster:

| Plik | Polecenie | Co pokazuje |
|---|---|---|
| `retro-json-proposals.json` | `skillsync retro --json --all-states` | `proposals[]` we wszystkich stanach (z diffem i bez, tylko z rejestru, rejestr przed plikiem przebiegu, tytuł z tokenem), `proposals_truncated`, `coverage.retro` |
| `retro-json-not-run.json` | `skillsync retro --json --all-states` w projekcie bez checkpointów | `status: NOT_RUN` przy niepustym `proposals[]` (propozycja z przebiegu i z rejestru); atrapa nie może opierać się na `status` |
| `retro-json-truncated.json` | `skillsync retro --json --all-states` przy obniżonym limicie listy (3) | `proposals_truncated: true` i obcięta lista |
| `retro-decide-proposal-rejected.json` | `skillsync retro-decide --proposal <id> --decision rejected --json` | wynik bez poprzedniej decyzji (`previous: null`) |
| `retro-decide-proposal-applied.json` | `skillsync retro-decide --proposal <id> --decision applied --ref <sha> --replace --json` | wynik z `previous` z rejestru |

## Skąd są dane

Wyników nie pisze się ręcznie. Test `RetroContractFixtures` w `tests/test_skillsync_retro.py` buduje deterministyczny
projekt w katalogu tymczasowym, uruchamia prawdziwy kod (data zamrożona na 2026-11-05, bez ścieżek bezwzględnych
i nazw osób) i porównuje wynik z tymi plikami. Zwykłe uruchomienie testu kończy się porażką, gdy kod i pliki się różnią.

## Skippster

Skippster kopiuje te pliki do swojego repozytorium (`test/fixtures/retro-contract/`) razem z sumami `sha256`, a atrapa CLI
oraz testy parsera czytają kopie. Po każdym odświeżeniu skopiuj pliki ponownie i zaktualizuj sumy `sha256`:

```
sha256sum tests/fixtures/retro-contract/*.json
```

## Odświeżanie

Gdy zmiana kontraktu jest zamierzona:

```
SKILLSYNC_UPDATE_RETRO_CONTRACT=1 python3 -m unittest discover -s tests -p 'test_skillsync_retro.py' -k RetroContractFixtures
```

Zmienna `SKILLSYNC_UPDATE_RETRO_CONTRACT=1` każe testowi przepisać pliki zamiast je porównywać. Po odświeżeniu sprawdź
`git diff` (zmiana kształtu to zmiana kontraktu dla Skippstera), a potem uruchom test bez zmiennej.
