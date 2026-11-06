# Fixture bazowy K1 (rownowaznosc artefaktow `link`)

Opis artefaktow `skillsync link` wygenerowany na **niezmienionym** kodzie generatora
(sprzed zmian warstw). Test z kroku 2.4t porownuje z nim artefakty nowego generatora.

- HEAD worktree przy generowaniu: `be8e719e8155cb6b6316f770bf91a894beeedc2b`
- Ostatni commit dotykajacy kodu (`git log -1 --format=%H -- skillsync skillsync_workflow`): `608b7f11cdf8836b92e9f69fee62dd940756d4ad`
- Stan kodu jest identyczny ze stanem `@741f0f9`, na który powołuje się spec A (K1): `git diff 608b7f1 741f0f9 be8e719 -- skillsync skillsync_workflow` jest pusty; fixture = „kod sprzed warstw @741f0f9”.
- Data generowania: 2026-11-05
- Regeneracja (cwd = korzen repo): `python3 tests/fixtures/layers/make_baseline.py [--out KATALOG]`

## Pliki

`{with-layer-yaml,legacy}.{symlink,copy}.json`: wariant biblioteki (z `layer.yaml` albo bez, most K15)
razy tryb (`symlink` / `copy` przez `SKILLSYNC_NO_SYMLINK=1`). Klucze JSON: `files` (sciezka -> SHA-256),
`symlinks` (sciezka -> cel wzgledem korzenia biblioteki), `variant`, `mode`, `link_stdout`.

## Normalizacja

- Sciezki wzgledne wobec korzenia projektu; symlinkowane katalogi nie sa rozwijane.
- Cele symlinkow: prefiks biblioteki obcinany do adresu wzglednego (`skills/feature`).
- Tresc plikow: CRLF -> LF; sciezki tymczasowe zastapione tokenami `<LIB>`, `<PROJ>`, `<HOME>`
  oraz `<GEN>` (korzen repo generatora, trafia do `.vscode/tasks.json`) przed liczeniem SHA-256.
- Srodowisko izolowane: `HOME`, `XDG_*`, `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_NOSYSTEM=1`, `SKILLSYNC_CONFIG`.

## Zastrzezenie

Generuj wylacznie na kodzie sprzed zmian warstw. Regeneracja na nowym generatorze zniweczy sens testu
rownowaznosci (porownanie sam ze soba). Zmiana fixture wymaga uzasadnienia kontraktem K1.

## Wyjątek: `.vscode/tasks.json`

Task `skillsync: link` ma od instalatora (I1, K6 zmienione) postać niezależną od interpretera i checkoutu uruchamiającego `link`:
jego `command` to wpis użytkownika, wrapper albo `self_argv()`, zależnie od maszyny, a `args` to `["link", "${workspaceFolder}"]`.
Test równoważności zamienia więc `command` na `<ENTRY>` przed liczeniem SHA-256, a zapisany skrót tego pliku dotyczy nowej postaci
(różnica wobec bazy sprzed warstw ogranicza się do `command` i do usuniętego argumentu `<GEN>/skillsync`; wszystkie pozostałe artefakty bez zmian).
