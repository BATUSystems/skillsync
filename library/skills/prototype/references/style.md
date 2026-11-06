# Styl prototypu

Wygląd prototypu składa się z sześciu warstw arkuszy. Każda następna może nadpisać poprzednią, więc kolejność ładowania
w `index.html` jest częścią kontraktu.

## Warstwy i kolejność ładowania

| Kolejność | Plik | Skąd | Edycja |
|---|---|---|---|
| 1 | `tokens.css` | generowany: motyw domyślny z biblioteki (`assets/theme/default.css`) plus tokeny z `prototype.theme.tokens_from` projektu | nie edytuj; odtwarzają go `skillsync prototype sync` i `migrate` |
| 2 | `theme.css` | generowany: kopia `prototype.theme.css` z manifestu | nie edytuj; zmieniaj plik wskazany w manifeście (odtwarzają go `sync` i `migrate`) |
| 3 | `prototype.css` | biblioteka (silnik): pasek, przełącznik ekranów, komentowanie, panel wątków, okna, konto | nie edytuj; aktualizuje go `skillsync prototype migrate` |
| 4 | `components.css` | biblioteka: komponenty słownika (przyciski, pola, tabele, karty, kanban, siatka tygodnia…) | nie edytuj; `migrate` nadpisuje go w całości |
| 5 | `screens.css` | biblioteka: układ dokumentu (strona, nagłówek ekranu, ramka) | nie edytuj; `migrate` nadpisuje go w całości |
| 6 | `project.css` | prototyp: style ekranów projektu (`init` tworzy go bez reguł; `migrate` tworzy go i linkuje, gdy go brak), ładowany jako ostatni | tu wpisuj własny styl; `sync` i `migrate` nie zmieniają istniejącego pliku |

```html
<link rel="stylesheet" href="tokens.css">
<link rel="stylesheet" href="theme.css">
<link rel="stylesheet" href="prototype.css">
<link rel="stylesheet" href="components.css">
<link rel="stylesheet" href="screens.css">
<link rel="stylesheet" href="project.css">
```

`init` ostrzega, gdy szablon `index.html` z biblioteki nie linkuje `tokens.css` albo `theme.css`; `sync` i `migrate` linków
w `index.html` prototypu nie sprawdzają. Kolory, odstępy, czcionki i promienie
bierz zawsze z tokenów (`var(--primary)`, `var(--radius-md)`), nie wpisuj wartości na sztywno: wtedy prototyp zmienia się razem z motywem
projektu, także w trybie ciemnym. Klasy autorskie ze słownika mają już reguły w `components.css`; w `project.css` dopisuj tylko to,
czego słownik nie pokrywa (układ konkretnego ekranu), z selektorem zawężonym do ekranu (`#s3 .moja-klasa`).

Widok mobile to iframe o szerokości 375 px (lub mniej, gdy okno jest węższe) z tymi samymi arkuszami, więc media queries
w `project.css` działają jak na telefonie: element widoczny tylko poniżej 600 px pojawia się w widoku mobile, a w desktop
znika. Makieta z `min-height: 100vh` lub `height: 100vh` dostaje wysokość początkową ramki i nie rośnie z oknem; zamiast tego
ustaw wysokość treścią (`min-height` w jednostkach względnych do treści, nie do okna).

## z-index: kolejność nakładania

Wartości pochodzą z tokenów `--z-index-*`, nie z liczb w CSS. Od spodu:

1. treść ekranu;
2. pinezki komentarzy i panel wątków (tuż pod paskiem, `sticky - 1`);
3. pasek prototypu `.doc-toolbar` (`--z-index-sticky`);
4. okienko komentarza (`--z-index-overlay`);
5. okna dialogowe silnika (warstwa najwyższa przeglądarki, `dialog`);
6. powiadomienia (`--z-index-toast`).

Menu otwierane z paska (konto, lista ekranów, „Więcej”) mają `--z-index-top` wewnątrz kontekstu paska, więc leżą nad jego
treścią i nad ekranami.

Ramka `.frame` tworzy własny kontekst nakładania, więc `z-index` komponentów ekranu (`modal`, `drawer`, `toast`, zasłony; tokeny `--z-index-modal`, `--z-index-overlay`,
`--z-index-toast` w `components.css`) nie
wygrywa z paskiem ani z pinezkami; nie podnoś wartości, żeby coś „przebić”. Panel wątków zaczyna się pod paskiem
(`--proto-toolbar-height`, ustawiany przez silnik), więc nie zasłania chipu konta. Pasek nie używa `transform`, `filter`,
`backdrop-filter` ani `contain`, żeby elementy `position: fixed` (lista ekranów na telefonie) liczyły się względem okna.
W `project.css` nie ustawiaj własnych `z-index` na elementach, które mogą wejść pod pasek.

## Motyw jasny i ciemny, blokada przyciemniania

Motyw ciemny wybiera atrybut `data-theme="dark"` na `<html>` (przełącznik „Motyw” w pasku); atrybut ustawia silnik po wyborze użytkownika i ma pierwszeństwo. Bez zapisanego wyboru strona idzie za `prefers-color-scheme` już w CSS, także bez JS i przed załadowaniem silnika (bez mignięcia jasnym motywem).
Tokeny ciemne definiują dwa bloki o tych samych wartościach (porównuje je `check_tokens.py`): `:root[data-theme="dark"]` (z projektu także `.dark` i `[data-theme=dark]`) oraz `@media (prefers-color-scheme: dark) { :root:not([data-theme]) }`; jasne definiuje blok `:root`. Token
musi mieć wartość w bloku jasnym; sama definicja w ciemnym nie wystarcza.

Rozszerzenia przyciemniające strony (np. Dark Reader) działają przed skryptami i psują kolory makiety. Blokada musi być więc w HTML,
w `<head>`:

```html
<meta name="color-scheme" content="light dark">
<meta name="darkreader-lock">
```

Silnik zdejmuje pozostałości takich rozszerzeń tylko wtedy, gdy ta para jest w dokumencie. Szablon `index.html` ją zawiera;
`skillsync prototype migrate` dopisuje ją do starszych prototypów. Nie usuwaj jej.

## Wytyczne projektu i tokeny wymagane

Wytyczne wizualne projektu to plik Markdown wskazany w manifeście (`prototype.guidelines`); generator go nie czyta, ale agent ma
go przeczytać przed rysowaniem i trzymać się opisanych tam wzorców i słownictwa. Lista tokenów, które motyw musi zdefiniować,
jest w `assets/theme/required-tokens.txt` (tło, tekst, karta, popover, kolory główne i akcentu, ramka, pole, pierścień fokusu,
zasłony, `destructive`, `highlight`, `muted`, statusy `info|success|warning|error|neutral` w wariantach `bg`, `border` i `text`
(`success|warning|error` także `icon`), promienie, cienie, czcionki, warstwy `--z-index-*`). `prototype sync` i `init` ostrzegają `Missing required tokens`, gdy `tokens.css` i `theme.css` razem
nie definiują któregoś z nich. Projekt, który ma własny `tokens_from`, uzupełnia brakujące tokeny we wskazanym pliku albo w `theme.css`.

## Kontrola tokenów i szablonów: check_tokens.py

`python3 scripts/check_tokens.py [<katalog skilla>]` (tylko biblioteka standardowa) sprawdza po stronie biblioteki:

- każdy token z `required-tokens.txt` jest zdefiniowany w jasnym bloku `:root` motywu domyślnego, a żaden nie tylko w ciemnym;
- lista wymaganych tokenów jest równa zbiorowi tokenów używanych (`var(--nazwa)`) przez arkusze i szablony w `assets/repo`
  (zmienne lokalne i `--proto-*` ustawiane przez silnik są pomijane); `--print-used` wypisuje ten zbiór;
- szablony v2: `comments.json` jest pustym dziennikiem z `{{SLUG}}`, `.gitignore` i `.gitattributes` mają wymagane wpisy,
  nie ma `comments.js`, a `index.html` ma blokadę motywu w `<head>`;
- fikstury foldu komentarzy dają oczekiwane wyniki (`--export` ogranicza kontrolę v1 do jednego eksportu; fikstury v2
  są sprawdzane zawsze).

Kody wyjścia: 0 zgodne, 1 rozjazd (komunikaty na stderr), 2 błąd wywołania. Uruchom po każdej zmianie arkuszy silnika,
motywu domyślnego albo szablonów biblioteki.

## Sonda układu: layout-probe.js

`scripts/layout-probe.js` to jedna deklaracja `async function layoutProbe(options)`. Wstrzyknij treść pliku do otwartej strony
prototypu (np. przez `Runtime.evaluate` w Chrome DevTools) i wywołaj `layoutProbe({...})`; wynik to JSON z pomiarami
(`viewport`, `toolbar`, `measures`). Opcje:

- `a1` (domyślnie włączone): przyciski w oknach makiet (`.modal`, `.drawer`, `.toast`): czy nie leżą pod paskiem i czy
  można w nie trafić;
- `a2` (domyślnie włączone): pinezki komentarzy wewnątrz `.modal` i `.drawer` makiet: czy można w nie trafić;
- `target: "sN"`: przejście do ekranu i pozycja jego tytułu pod paskiem;
- `measureCurrent: true`: tytuł ekranu `.screen.is-current`;
- `focusRing: true` i `focusMargin` (domyślnie 4): odstęp przycisków paska od jego krawędzi (miejsce na obrys fokusu);
- `compareSingle: true`: wysokość paska przed i po usunięciu z DOM wszystkich ekranów poza pierwszym (razem z ich linkami).
  Ta opcja psuje stronę: uruchamiaj ją na końcu i potem przeładuj stronę.

Sonda nie klika, ale przewija stronę (`target`, pomiary okien); przewinięcie wraca na miejsce tylko bez `target`. Kliknięcia
wskaźnikiem i ocena wyniku należą do agenta.
