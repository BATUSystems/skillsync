# Dokumentacja, changelog i wersja wydania

Obowiązuje `feature`, `bugfix` (oraz każdy skill kończący zmianę kodu). Uzupełnia
[knowledge.md](knowledge.md); nie zastępuje zasad dostarczania (tryb `delivery.mode`, słowa `CP`, `CPD`, `READY`; patrz AGENTS).

1. **Rozpoznaj** (w planie/briefie, sekcja „Dokumentacja i wiedza” + „Wersja”): changelog projektu
   (`CHANGELOG*`, `docs/**/changelog*`, `RELEASE*`, wpisy w `docs/`), źródła numeru wersji
   (`package.json`, `*.csproj`/`Directory.Build.props`, `pyproject.toml`, `VERSION`, manifesty
   aplikacji, pliki konfiguracyjne UI) i schemat wersjonowania z instrukcji projektu (np. SemVer,
   `X.Y.Z` z hotfixem). Niczego nie zakładaj. Brak changeloga/wersji = jawna informacja i pytanie,
   czy go założyć; nie twórz go po cichu.
2. **Dokumentacja i changelog w ramach zmiany** — zawsze, w tym samym zakresie co kod:
   zaktualizuj dotknięte dokumenty oraz dodaj wpis do changeloga w istniejącym formacie i języku
   (kategoria Added/Changed/Fixed/Security lub odpowiednik projektu; numer ticketu/PR, jeśli znany;
   opis dla czytelnika wydania, nie dla autora kodu). Wpis trafia do sekcji „Unreleased”/„Do wydania”,
   dopóki wersja nie jest potwierdzona. Bez wpisu (albo uzasadnionego „nie dotyczy”) zmiana nie jest `done`;
   reviewer sprawdza to jak kod.
3. **Wersja — zawsze zaproponuj i zapytaj.** Przedstaw: aktualną wersję (ze źródła i pliku:linii),
   proponowaną nową z uzasadnieniem (poprawka błędu → patch; nowa funkcja zgodna wstecz → minor;
   zmiana łamiąca kontrakt/dane → major; wyjątki według schematu projektu), listę plików, które
   zmienią numer, oraz opcję „bez zmiany wersji”. **Zapytaj użytkownika, jaką wersję nadać temu wydaniu**
   (Claude: `AskUserQuestion` lub zwykłe pytanie; Codex: zwykłe pytanie) i zaczekaj na odpowiedź.
   Pytaj w momencie planu, jeśli to możliwe (żeby wersja była znana przed implementacją), a najpóźniej
   przed `done`. Nie zmieniaj numeru wersji bez odpowiedzi; po odpowiedzi ustaw ją we wszystkich
   źródłach spójnie, zamień „Unreleased” na `## <wersja> — <data>` i sprawdź zgodność (grep starego numeru).
   Jedno pytanie na wydanie: kolejne zmiany tego samego wydania zachowują już ustaloną wersję.
4. **Granice:** tag, release, publikacja pakietu i deploy — tylko na osobne polecenie; commit i push według trybu dostarczania.
   W pracy wielosesyjnej (`dispatch`) karty nie zmieniają numeru wersji ani nagłówka changeloga;
   wpisy dodają pod „Unreleased”, a o wersję pyta dyspozytor jednokrotnie dla całego przebiegu.
5. **Raport końcowy** wymienia: zaktualizowane dokumenty, wpis changeloga, ustaloną wersję (albo „bez zmiany”,
   albo „oczekuje na decyzję” → wtedy zadanie jest `blocked` na tym punkcie, nie `done`).
