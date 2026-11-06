# Ikony prototypów

Ikony narysowane od zera na potrzeby tego skilla, bez wzorowania się na zewnętrznych zestawach; bez licencji zewnętrznej.

Plik `sprite.svg` zawiera jeden ukryty `<svg>` z symbolami `ic-*`. Podgląd wszystkich ikon: `preview.html`.

## Lista ikon

Nazwy są kontraktem (`docs/prototype-contract/slownik-html-css.md`, część B, tabela „Symbole ikon”). Grupy: podstawowe (18),
wzorce ekranów (8, kontraktowe, z `references/screen-patterns.md`) i dodatkowe (poza kontraktem). Szablon repo `index.html`
zawiera cały `sprite.svg`, a `skillsync prototype migrate` dopisuje do prototypu symbole dodane w kolejnych wersjach biblioteki.
W `preview.html` gwiazdka (*) oznacza wzorce ekranów, plus (+) ikony dodatkowe.

| id | Znaczenie | Grupa |
|---|---|---|
| `ic-bell` | powiadomienia | podstawowa (18, szablon) |
| `ic-check` | zatwierdzenie | podstawowa (18, szablon) |
| `ic-chevron-down` | rozwiń | podstawowa (18, szablon) |
| `ic-chevron-left` | wstecz, poprzednia strona | podstawowa (18, szablon) |
| `ic-chevron-right` | dalej | podstawowa (18, szablon) |
| `ic-filter` | filtr | podstawowa (18, szablon) |
| `ic-home` | strona główna | podstawowa (18, szablon) |
| `ic-info` | informacja | podstawowa (18, szablon) |
| `ic-layout` | układ, szuflada, panel boczny | podstawowa (18, szablon) |
| `ic-more` | więcej akcji | podstawowa (18, szablon) |
| `ic-pencil` | edytuj | podstawowa (18, szablon) |
| `ic-plus` | dodaj | podstawowa (18, szablon) |
| `ic-search` | wyszukiwanie | podstawowa (18, szablon) |
| `ic-settings` | ustawienia | podstawowa (18, szablon) |
| `ic-trash` | usuń | podstawowa (18, szablon) |
| `ic-users` | zespół, użytkownicy | podstawowa (18, szablon) |
| `ic-warn` | ostrzeżenie | podstawowa (18, szablon) |
| `ic-x` | zamknij | podstawowa (18, szablon) |
| `ic-ban` | brak dostępu, zakaz | wzorce ekranów |
| `ic-building` | budynek, organizacja | wzorce ekranów |
| `ic-calendar` | kalendarz, termin | wzorce ekranów |
| `ic-calendar-off` | dzień wolny | wzorce ekranów |
| `ic-check-circle` | powodzenie | wzorce ekranów |
| `ic-door` | sala, pomieszczenie | wzorce ekranów |
| `ic-list` | lista | wzorce ekranów |
| `ic-lock` | blokada, brak uprawnień | wzorce ekranów |
| `ic-user` | Użytkownik, konto | dodatkowa |
| `ic-menu` | Menu (trzy kreski) | dodatkowa |
| `ic-error` | Błąd | dodatkowa |
| `ic-clock` | Czas, historia | dodatkowa |
| `ic-minus` | Odejmij, zwiń | dodatkowa |
| `ic-chevron-up` | Zwiń w górę | dodatkowa |
| `ic-arrow-left` | Strzałka w lewo | dodatkowa |
| `ic-arrow-right` | Strzałka w prawo | dodatkowa |
| `ic-inbox` | Stan pusty, skrzynka | dodatkowa |
| `ic-refresh` | Odśwież, spróbuj ponownie | dodatkowa |
| `ic-table` | Widok tabeli | dodatkowa |
| `ic-kanban` | Widok tablicy kanban | dodatkowa |
| `ic-download` | Pobierz | dodatkowa |
| `ic-eye` | Podgląd | dodatkowa |
| `ic-comment` | Komentarz | dodatkowa |
| `ic-sort` | Sortowanie | dodatkowa |
| `ic-sun` | Motyw jasny | dodatkowa |
| `ic-moon` | Motyw ciemny | dodatkowa |

## Zasady konstrukcji

- Siatka 24×24, `viewBox="0 0 24 24"`, zawartość mieści się w polu 3–21 (margines 3 jednostki).
- Atrybuty na każdym `<symbol>`: `fill="none"`, `stroke="currentColor"`, `stroke-width="2"`, `stroke-linecap="round"`, `stroke-linejoin="round"`. Kolor ikony to kolor tekstu (`color`) elementu.
- Tylko prymitywy: `line`, `polyline`, `circle`, `rect`, `path` (proste odcinki i łuki). Bez wypełnień, gradientów, tekstu i obrazów.
- Kropki to odcinki o zerowej długości z zaokrąglonym zakończeniem (lub małe okręgi), nie wypełnione kształty.
- Jedna grubość linii we wszystkich ikonach; ikona ma jeden motyw, bez szczegółów drobniejszych niż 2 jednostki.
- Nazwy: `ic-<nazwa>` małymi literami, myślnik jako separator; identyfikatory unikalne w sprite.

## Wstawianie do szablonów

1. Sprite wklej raz, tuż po `<body>`: `<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">…</svg>`; na stronie jednoplikowej (Artifact) skopiuj z `sprite.svg` tylko symbole, których strona używa; szablon repo `index.html` ma już cały sprite.
2. Użycie: `<svg class="ico" width="16" height="16" aria-hidden="true"><use href="#ic-search"/></svg>`.
3. Rozmiar i kolor ustaw w CSS (`.ico{width:1em;height:1em;color:inherit}`); ikona przejmuje `currentColor`.
4. Przycisk samą ikoną dostaje `aria-label` lub `title`; ikona dekoracyjna `aria-hidden="true"`.
5. Każde `<use href="#ic-…">` musi mieć odpowiadający symbol w sprite'ie tej samej strony.
