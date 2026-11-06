# Pisanie treści skilla

Rejestrację i linkowanie skilla opisuje [SKILL.md](../SKILL.md) skilla `skillsync`. Tutaj są zasady, co i jak pisać w treści.

## 1. Punkt wyjścia: realne prośby

Zanim zaczniesz, zbierz 2–3 konkretne sposoby, w jakie użytkownik albo inny skill poprosi o zadanie. Dla każdej ustal, jak zadanie szłoby bez skilla i co trzeba by za każdym razem tworzyć od nowa (skrypt, schemat, szablon). To kandydaci na zasoby, a prośby dają frazy do opisu. Przy oczywistym zastosowaniu krok może być krótki, ale przy niejednoznacznych wejściach go nie pomijaj.

## 2. Tylko wiedza, której agent nie ma

Okno kontekstu dzieli katalog opisów, historię rozmowy i zadanie. Model jest bystry, więc skill podaje kroki procedury, fakty o konkretnym repo, ograniczenia i pułapki. Akapit zostaje tylko wtedy, gdy opłaca się tokenami przy każdym użyciu; zwięzły przykład często zastępuje wyjaśnienie.

## 3. Swoboda dopasowana do kruchości

| Poziom | Forma | Kiedy |
|---|---|---|
| wysoki | proza i heurystyki | kilka poprawnych dróg, zależnych od kontekstu |
| średni | pseudokod lub parametryzowany skrypt | preferowany wzorzec, odchylenia dopuszczalne |
| niski | dokładny skrypt lub sekwencja poleceń | operacja łatwo się psuje, spójność krytyczna albo kolejność obowiązkowa |

Stała reguła: bramki, znaczniki i wszystko, co parsuje inny skill lub narzędzie, mają zawsze swobodę niską.

## 4. Zasoby

- `references/` — tekst doczytywany na żądanie: procedury, szablony, schematy.
- `scripts/` — kod deterministyczny; każdy uruchom przed oddaniem (przy wielu podobnych sprawdź próbkę).
- `assets/` — materiał kopiowany lub używany w produkcie pracy, którego agent nie czyta.

Puste lub zbędne katalogi usuń. Ścieżka wskazana w skillu bez istniejącego pliku to błąd.

## 5. Referencje i jedno miejsce na fakt

Każdy fakt ma jedno miejsce: w treści skilla albo w referencji, nigdy w obu. Poziom zagnieżdżenia to jeden: referencja jest wskazana wprost z `SKILL.md`, bez łańcuchów referencja → referencja. Wiele wariantów (dostawcy, trackery) wybierasz w treści, a szczegóły trzymasz w osobnych plikach. Plik dłuższy niż ok. 100 linii dostaje spis treści.

## 6. Szablony wyjścia

Wyjście parsowane dalej ma ścisły szablon (linie łańcuchowe, znaczniki, pola maszynowe). Raport czytany przez człowieka ma elastyczny szablon domyślny z jawnym zakresem adaptacji. Gdy chodzi o styl, pokaż przykład „takie wejście → taki wynik”. Procedurę o wielu etapach poprzedź ponumerowaną listą wszystkich etapów.

## 7. Opis — jedyna podstawa wyboru skilla

- Limit ≤ 200 B (UTF-8); przekroczenie zgłasza `doctor`. Każdy bajt opisu kosztuje w każdej rozmowie.
- Jedna linia: co skill robi i daje, kiedy go użyć, frazy w brzmieniu użytkownika, a przy podobnym sąsiednim skillu — czym się od niego różni. Skill wywoływany po polsku i po angielsku dostaje frazy w obu językach.
- Opis nie wyjaśnia działania; to należy do treści.
- Test zimnego odczytu: czytając sam opis, sprawdź 3 prośby pasujące i 3 niepasujące (zadania sąsiednich skilli). Skill ma zostać wybrany tylko dla pasujących; poprawiaj do skutku.
- Frontmatter: `name` = nazwa folderu, małe litery z myślnikami. `description` musi parsować się jako YAML; tekst zawierający „: ” ujmij w cudzysłów, inaczej `doctor` zgłosi „Invalid skill frontmatter”.

## 8. Czego nie dodawać do katalogu skilla

README, instrukcji instalacji, ściągawki, changelogu, notatek o powstaniu skilla. Dokumentacja zbiorcza jest poza `skills/`, a dodatkowe pliki w skillu tylko się dezaktualizują.

## 9. Iteracja po użyciu

Pierwsza wersja to hipoteza. Brakującą barierę dopisz do treści. Rozumowanie odtwarzane wielokrotnie zamień na skrypt lub referencję, a nieużywany akapit usuń. Po każdej zmianie uruchom `skillsync skills index --check` i `doctor`.
