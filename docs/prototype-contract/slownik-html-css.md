# Słownik HTML i CSS prototypów

Lista identyfikatorów interfejsu, które prototypy i testy wykorzystują i które silnik oraz arkusze muszą obsługiwać.
Słownik podaje nazwy i rolę; wygląd (reguły CSS, kolory, odstępy) jest swobodny w granicach tokenów
(`required-tokens.txt`) i wymagań UI. Źródło wymagań: spec `docs/specs/2026-11-04-prototype-komentarze-wspolne.md`.

## A. Klasy autorskie (używane w HTML prototypów; arkusze komponentów i ekranów)

Każda klasa ma regułę stylu w arkuszach prototypu i zachowuje rolę opisaną niżej. Klasy stanu (`active`, `here`, `is-off`, `today`, …) zmieniają
wygląd elementu, do którego są dodane.

| Grupa | Klasy | Rola |
|---|---|---|
| Dokument prototypu | `doc`, `doc-head`, `doc-toolbar`, `screen`, `screen-meta`, `screen-nav`, `screen-refs`, `ref`, `note`, `notes`, `frame`, `frame-bar`, `frame-dots`, `frame-url` | strona z listą ekranów; ekran z nagłówkiem (numer, tytuł, opis, odnośniki do wymagań); ramka „okna przeglądarki” z paskiem i adresem |
| Szkielet aplikacji w ramce | `app`, `app-narrow`, `app-tall`, `app-taller`, `app-foot`, `app-mark`, `sidebar`, `sidebar-app`, `sidebar-nav`, `sidebar-search`, `sidebar-foot`, `nav`, `nav-group`, `nav-item`, `nav-items`, `nav-sep`, `topbar`, `topbar-left`, `topbar-right`, `topbar-search`, `main`, `page`, `page-head`, `page-actions`, `crumbs`, `here`, `back-link` | układ aplikacji: pasek boczny z nawigacją, górny pasek, treść, okruszki; warianty wysokości i szerokości ramki |
| Typografia i pomocnicze | `t-page-title`, `t-section`, `t-card-title`, `t-table-title`, `t-body`, `t-label`, `t-hint`, `t-mono`, `t-overline`, `muted`, `strong`, `ellipsis`, `num`, `right`, `grow`, `wrap`, `between`, `col`, `row`, `row-3`, `row-4`, `row-actions`, `stack-2`, `stack-3`, `stack-4`, `stack-6`, `separator`, `scroll-x`, `kbd`, `dot`, `danger` | style tekstu, odstępy, układ w wierszach i stosach |
| Przyciski i kontrolki | `btn`, `btn-primary`, `btn-outline`, `btn-ghost`, `btn-destructive`, `btn-destructive-outline`, `btn-sm`, `btn-xs`, `btn-icon`, `icon-btn`, `segmented`, `switch`, `check`, `input`, `input-group`, `input-invalid`, `addon`, `select`, `textarea`, `field`, `field-error`, `req`, `is-disabled`, `clickable`, `menu-item`, `popover`, `tabs`, `tab-count`, `view-switch` | przyciski (wariant główny, obrysowy, przezroczysty, destrukcyjny; rozmiary), pola formularza z błędem i wymaganiem, przełączniki, menu, zakładki |
| Formularze | `form-layout`, `form-head`, `form-col`, `form-group`, `form-grid`, `form-grid-2`, `form-foot` | układ formularza w kolumnach i siatce |
| Komunikaty i stany | `alert`, `alert-info`, `alert-success`, `alert-warning`, `alert-error`, `alert-title`, `badge`, `badge-info`, `badge-success`, `badge-warning`, `badge-error`, `badge-neutral`, `badge-count`, `chip`, `chip-active`, `chip-dashed`, `chip-highlight`, `empty`, `empty-icon`, `toast`, `progress`, `progress-warning`, `progress-error`, `pulse`, `timer-pill`, `timer-stop` | komunikaty w kolorach statusu, plakietki, żetony, stan pusty, powiadomienie, pasek postępu |
| Karty, awatary | `card`, `card-head`, `card-body`, `card-pad`, `avatar`, `avatar-sm`, `avatar-lg`, `avatar-stack` | karty treści, awatary z inicjałami |
| Tabele danych | `table`, `dt`, `dt-head`, `dt-head-row`, `dt-head-actions`, `dt-toolbar`, `dt-toolbar-row`, `dt-toolbar-right`, `dt-search`, `dt-chips`, `dt-foot`, `pager`, `bulk-bar`, `filter-count`, `count` | tabela z paskiem filtrów, wyszukiwaniem, żetonami filtrów, stopką i stronicowaniem, pasek akcji zbiorczych |
| Okna i szuflady | `modal`, `modal-head`, `modal-body`, `modal-foot`, `modal-scrim`, `drawer`, `drawer-head`, `drawer-body`, `drawer-foot`, `drawer-scrim` | okno modalne i szuflada z zasłoną (statyczne makiety w ekranach) |
| Siatka tygodnia | `week-grid`, `week-grid-scroll`, `week-grid-corner`, `week-grid-day`, `week-grid-slot`, `week-grid-slot-time`, `week-grid-cell`, `week-grid-stack`, `week-grid-event`, `week-grid-event-full`, `week-grid-event-abbr`, `week-grid-event-info`, `week-grid-event-success`, `week-grid-event-warning`, `week-grid-event-error`, `week-grid-event-neutral`, `week-grid-event-muted`, `week-grid-clash`, `week-grid-off`, `weekend`, `today`, `daybar` | dni × sloty, zdarzenie na N slotów w kolorach statusu, kolizja, dzień wolny, weekend, dziś |
| Tablica kanban | `kanban`, `kcol`, `kcol-head`, `kcol-title`, `kcol-count`, `kcol-hours`, `kcol-body`, `kcol-accent`, `kcard`, `kcard-title`, `kcard-meta`, `kcard-hours`, `kcard-rule`, `kcard-quick`, `kcard-dragging`, `kdrop`, `kquick`, `dropping` | kolumny z kartami, przeciąganie (stan) |
| Arkusz czasu i raporty | `ts-grid`, `ts-day`, `ts-day-head`, `ts-dow`, `ts-date`, `ts-entry`, `ts-load`, `ts-total`, `ts-add`, `report-sheet`, `report-head`, `report-group`, `report-group-head`, `report-total`, `kpi`, `kpis`, `delta`, `up`, `down`, `split`, `split-2`, `split-side`, `track`, `elapsed`, `nonbillable`, `marker`, `actions`, `title`, `desc`, `name`, `n`, `d`, `v`, `out`, `div`, `is-off`, `active` | wzorce ekranów raportowych i ewidencji czasu |

## B. Atrybuty i struktura

| Identyfikator | Rola |
|---|---|
| `html[data-prototype-id]` | slug prototypu; silnik używa go do API i kluczy `localStorage` |
| `html[data-prototype-iteration]`, `html[data-prototype-frozen]` | strona zamrożonej iteracji (tylko do odczytu) |
| `section.screen#sN` | ekran; `sN` = identyfikator ekranu (parser sekcji serwera i generatora) |
| `[data-prototype-example]` | ekran przykładowy z szablonu: pomijany w liczeniu ekranów, `ids`, akceptacji i bramce |
| `[data-proto-id]` | stały uchwyt elementu komentowalnego (kotwica wątku) |
| `[data-goto="sN"]` | przejście do ekranu po kliknięciu (tryb „Klikalny”) |
| `[data-proto-nav="strip"]` | wymuszenie prostego paska linków ekranów zamiast przełącznika |
| `[data-theme]` | motyw jasny/ciemny na `html` |
| `meta[name=color-scheme]`, `meta[name=darkreader-lock]` | blokada motywu (zdejmowanie pozostałości rozszerzeń przyciemniających tylko przy tym meta) |
| `aria-current`, `aria-checked`, `aria-pressed`, `aria-selected`, `aria-expanded`, `aria-disabled`, `disabled`, `hidden` | stany używane w HTML prototypów i w UI silnika |
| role w HTML prototypów: `alert`, `alertdialog`, `checkbox`, `dialog`, `group`, `listbox`, `none`, `option`, `status`, `switch`, `button`, `grid` | semantyka elementów makiet |
| sprite `svg > symbol#ic-*` i użycia `<use href="#ic-…">` | ikony: kontrakt obejmuje nazwy symboli `ic-*`, ich rolę i odwołanie przez `<use href>` |
| placeholdery `{{TITLE}}`, `{{SLUG}}`, `{{REQUIREMENTS}}` | wypełniane przez generator (`init`) |


Doprecyzowania układu (wynikają z istniejących prototypów):
- `app` wypełnia całą szerokość ramki: pasek boczny o stałej szerokości, główna część zajmuje resztę; `app-narrow`, `app-tall`,
  `app-taller` zmieniają wyłącznie WYSOKOŚĆ powłoki (niższa, wyższa), nie jej szerokość.
- Gdy okno jest węższe niż 600 px, pasek boczny `.sidebar` w `.app` jest ukryty: nie zajmuje miejsca i nie dostaje fokusu.
  Główna część zajmuje wtedy całą szerokość ramki. Od 600 px pasek boczny jest widoczny z pełną treścią.
- `modal-scrim` i `drawer-scrim` mogą stać jako rodzeństwo okna/szuflady albo jako dodatkowa klasa na `app` (`app modal-scrim`):
  w obu przypadkach przyciemniają powłokę bez wyjmowania jej z układu, a okno/szuflada leży nad zasłoną.
- `nav-group` to nagłówek grupy nawigacji w pasku bocznym (tekst grupy + opcjonalny szewron w jednym wierszu); pozycje grupy to
  następujące po nim `nav-item`.
Style projektu: plik `project.css` w katalogu prototypu należy do projektu — `init` tworzy go z jednym komentarzem, szablon `index.html`
podpina go jako ostatni arkusz (po `screens.css`), a `sync` i `migrate` nigdy go nie zmieniają. Pliki `prototype.css`, `components.css`
i `screens.css` należą do biblioteki i są nadpisywane przez `migrate`.

Symbole ikon (nazwy są kontraktem: istniejące prototypy odwołują się do nich przez `<use href>`, a `migrate` podmienia symbole o tych
id). Szablon `index.html` zawiera w sprite wszystkie symbole biblioteki (`assets/icons/sprite.svg`); `migrate` podmienia symbole `ic-*`
o tych samych id i dopisuje nowe z kolejnych wersji biblioteki, a własne symbole projektu zostawia.

| Grupa | Id symboli | Rola |
|---|---|---|
| podstawowe (szablon) | `ic-bell`, `ic-check`, `ic-chevron-down`, `ic-chevron-left`, `ic-chevron-right`, `ic-filter`, `ic-home`, `ic-info`, `ic-layout`, `ic-more`, `ic-pencil`, `ic-plus`, `ic-search`, `ic-settings`, `ic-trash`, `ic-users`, `ic-warn`, `ic-x` | powiadomienia, zatwierdzenie, rozwinięcie i nawigacja, filtr, start, informacja, układ/panel, więcej akcji, edycja, dodanie, wyszukiwanie, ustawienia, usunięcie, użytkownicy, ostrzeżenie, zamknięcie |
| wzorce ekranów (sprite biblioteki, także w szablonie) | `ic-ban`, `ic-building`, `ic-calendar`, `ic-calendar-off`, `ic-check-circle`, `ic-door`, `ic-list`, `ic-lock` | brak dostępu, budynek/organizacja, kalendarz, dzień wolny, sukces, sala/pomieszczenie, lista, blokada |

## C. Haki UI silnika (używane przez stały zestaw E2E)

Elementy tworzone przez silnik, na których opierają się scenariusze E2E (`tests/e2e/prototype/`); silnik zapewnia je
z tą samą rolą (wygląd dowolny). Teksty UI są kontraktem w brzmieniu z kontraktu UI W1.

Klasy: `anno-accept`, `anno-accept-count`, `anno-accept-open`, `anno-banners`, `anno-card`, `anno-compare-pick`, `anno-composer-head`, `anno-composer-pop`, `anno-effects`, `anno-filters`, `anno-iteration`, `anno-iter-link`, `anno-msg`, `anno-panel` (stan `open`), `anno-panel-head`, `anno-panel-title`, `anno-pin`, `anno-pin-static`, `anno-st-open|awaiting|decided|closed|deferred`, `anno-save`, `anno-screen-comment`, `anno-screen-tools`, `anno-thread`, `anno-threads-btn`, `anno-user`, `anno-user-menu`, `anno-user-slot`, `anno-vote`, `anno-voters`, `proto-toolbar`, `proto-screens` (na `nav.screen-nav`), `proto-screens-bar`, `proto-screens-main`, `proto-screens-num`, `proto-screens-name`, `proto-screens-option` (stan `is-active`), `proto-screens-pop`, `proto-screens-search`, `proto-screens-empty`, `is-current` (ekran bieżący), oraz klasy strony prototypu: `doc-toolbar`, `screen-nav`, `screen`, `screen-meta`, `frame`, `btn-outline`, `segmented`.
Atrybuty i identyfikatory: `id` `anno-login-email`, `anno-login-password`, `anno-dialog-title`, `anno-restore-from`, `anno-restore-why`, `anno-restore-hint`, `anno-accept-project`; `data-anno-focus="user-chip|user-login"`; `data-screen-step="prev|next"`; `data-proto-nav="strip"`; `data-prototype-iteration`, `data-prototype-frozen`, `data-theme`; `role="group"` z `aria-label="Tryb"` (przyciski „Klikalny”, „Komentarze”); `aria-pressed`, `aria-expanded`, `aria-activedescendant`, `aria-disabled`, `aria-describedby`, `aria-current`, `aria-label` pinezek („Wątek N: <status>”); `role="note"` (chip bez akceptacji), `role="alert"`/`role="status"` w oknie; `dialog[open]`; `iframe[src*="embed=compare"]` (parametr `embed=compare`); `data-proto-id` uchwyty; klucze `localStorage`: `skillsync-prototype-queue:v2:<proto>:<login>`, `skillsync-prototype-rejected:v2:<proto>:<login>`, `skillsync-prototype-comments:v1:<slug>`.
Teksty UI używane jako uchwyty: „Zaloguj”, „Wyloguj”, „Akceptuj…”, „Akceptuję”, „Cofnij akceptację”, „Ponów”, „Ponów teraz”, „Pobierz kolejkę”, „Pokaż odrzucone”, „Ponów podgląd”, „Przywróć…”, „Przywróć ekran”, „Porównaj z iteracją”, „Usuń wątek”, „Wycofaj”, „Anuluj”, „Porzuć szkic”, „Zamknij”, „Spróbuj ponownie”, „Wyślij jako”, „Pobierz plik”, „Usuń z listy”, „Motyw”, „Prezentacja”, „Wątki”, filtry („Otwarte”, „Nowe od ostatniej wizyty”, „Decyzje agenta”, „Bez uchwytu”, „Wszystkie iteracje”), `textarea[aria-label="Odpowiedź"]`.

Uzupełnienia (rola i wymagane zachowanie, wygląd dowolny):
- `--proto-toolbar-height`: silnik ustawia tę zmienną na `:root` jako wysokość paska w px (np. `104px`); wartość nadąża za zmianą wysokości paska (zmiana szerokości okna), bo `screens.css` autora może używać jej do pozycjonowania pod paskiem.
- Pinezka: element `button.anno-pin` (pinezka statyczna: dodatkowo `anno-pin-static`, nie jest liczona jako interaktywna); tekst pinezki to numer wątku, `aria-label` zaczyna się od „Wątek N: <status>”.
- Przycisk zamykania panelu: `button` w `.anno-panel-head`, którego nazwa dostępna (`aria-label`, `title` lub tekst) zawiera „Zamknij”; przy niepustym szkicu otwiera okno „Porzucić szkic?”.
- Nagłówek panelu z wątkiem zawiera tekst „Wątek #N” (numer pinezki).
- Wiadomość (`.anno-msg`) autora zawiera przycisk „Edytuj” (kontrakt UI W1) i „Wycofaj”; głosy to przyciski `.anno-vote` z `aria-label` zaczynającym się od „Głos za” albo „Głos przeciw”.
- Przycisk trybu: w grupie `role="group"` `aria-label="Tryb"` przycisk „Komentarze” z `aria-pressed`; drugi Esc po zamknięciu okienka komentarza wyłącza tryb (`aria-pressed="false"`), a po pierwszym Esc fokus wraca na element, do którego był komentarz.
- Chip wymuszonej zmiany hasła ma tekst „Ustaw własne hasło”.
- Opcja listy ekranów (`.proto-screens-option`) zaczyna się od numeru ekranu (`<N> <nazwa>`); Enter na zaznaczonej opcji przechodzi do `#s<N>`.
- Okno „Zaloguj się ponownie” (po wygaśnięciu sesji) oddaje po Esc fokus w `.anno-user` (chip konta lub jego potomek).

## Narzędzie layout-probe

Narzędzie `layout-probe.js` to jedna samodzielna deklaracja funkcji `async function layoutProbe(options)` bez zależności. Nie ma wiersza poleceń ani kodu wyjścia: uruchamia się je w stronie prototypu (chrome-devtools `evaluate_script` albo konsola; stały zestaw E2E wstrzykuje tekst pliku przez CDP i wywołuje `await layoutProbe({...})`). Zwraca obiekt JSON; wynik jest obiektem także przy błędzie wejścia, bez wyjątku. Stały zestaw: `tests/e2e/prototype/layout-probe.mjs` (LP-01 do LP-06).

Opcje (wszystkie opcjonalne, domyślnie `{}`):
- `target: 'sN'`: przewija do ekranu `sN` (`scrollIntoView`, początek) i mierzy nagłówek; strona zostaje przewinięta.
- `measureCurrent: true`: mierzy bieżący ekran (`.screen.is-current`) bez przewijania, przed innymi pomiarami.
- `a1: false`, `a2: false`: wyłączają domyślne pomiary A1 (okna/szuflady/toasty pod paskiem) i A2 (pinezka na wierzchu w oknie/szufladzie).
- `focusRing: true` i `focusMargin: <liczba>` (domyślnie 4): wolne miejsce wokół przycisków `.proto-toolbar`.
- `compareSingle: true`: porównuje wysokość paska z wszystkimi ekranami i z jednym; zmienia DOM (usuwa ekrany poza pierwszym i ich linki), więc po pomiarze stronę trzeba przeładować.

Wynik (pola zawsze obecne): `ok` (boolean; false, gdy którykolwiek pomiar ma `ok:false`; pomiary z `ok:null` są pomijane), `viewport {width, height: liczby; theme: napis albo null; panelOpen: boolean}`, `toolbar {height, bottom, share, screens: liczby; cssVar: napis albo null}` (`height` = wysokość `.doc-toolbar`, `share` = height / wysokość okna zaokrąglone do 0,001, `cssVar` = wartość `--proto-toolbar-height`, `screens` = liczba `.screen`). Pola pomiarów występują tylko, gdy pomiar wykonano:
- `current` i `target`: `{ok, screen, h2Top, h2Bottom, toolbarBottom, ariaCurrent}` albo `{ok:false, error}`; `ok` = nagłówek `.screen-meta h2` leży pod paskiem i w oknie (dla `current` dodatkowo `ariaCurrent` = `location`).
- `a1`: `{ok, screens[{screen, element: modal|drawer|toast, ok, buttons[{ok,label,x,y,hit,overlaps}], skipped?}]}`; `a2`: `{ok, pins[{ok,screen,index,label,bodyScrollTop,x,y,hit}]}`. `ok:null` z `skipped` (napis) = nie było czego sprawdzić.
- `focusRing`: `{ok, margin, buttons[{ok, label, space{left,right,top,bottom}}]}`; `ok` każdego przycisku = wszystkie cztery wartości `>= margin`.
- `compareSingle`: `{ok, before{height,screens,links}, after{height,screens,links}, diff, note}`; `ok` = równe wysokości.
Błędy wejścia (kody wyjścia nie istnieją): brak `.doc-toolbar` daje `{ok:false, error:'Brak .doc-toolbar'}`; nieznany albo nie-ekranowy `target` daje `target{ok:false, error:'Brak ekranu <id>'}` i `ok:false`; brak `.screen.is-current` daje `current{ok:false, error}`.

Uzupełnienia (bieżący ekran, LP-03): bieżący ekran ma `aria-current="location"` zarówno na sekcji `section.screen.is-current`, jak i na odpowiadającym mu linku w `.screen-nav`.

Uzupełnienia (panel komentarzy, N-R1 do N-R8):
- Ramka ekranu `.frame` w `section.screen` służy jako odniesienie skali makiety: jej szerokość po i przed otwarciem panelu daje skalę (bez zakładania techniki skalowania).
- Stan otwarty panelu: `.anno-panel.open`; jego prostokąt służy do sprawdzenia zakrycia kontrolek i pinezek; przycisk zamykania w `.anno-panel-head` ma nazwę dostępną zawierającą „Zamknij”.
- Menu „⋯” paska (okno węższe niż 600 px). Pasek ma wtedy najwyżej 2 rzędy kontrolek; jego wysokość nie przekracza
  dwukrotności wysokości najwyższej kontrolki plus 32 px. Każda widoczna kontrolka paska ma wtedy co najmniej 24 × 24 px
  (WCAG 2.2 AA, 2.5.8), a przełącznik ekranów (poprzedni, lista, następny) co najmniej 44 px wysokości. W pasku zostają:
  - przełącznik ekranów (poprzedni, lista, następny);
  - grupa „Tryb”;
  - „Wątki”;
  - chip konta albo „Zaloguj”;
  - chip akceptacji.

  Przyciski „Pokaż hotspoty”, „Prezentacja”, „Ukryj pinezki” i „Motyw” przechodzą do menu. Otwiera je `button` w
  `.doc-toolbar` z `aria-haspopup="menu"` i `aria-expanded` oraz nazwą dostępną „Więcej” (tekst przycisku „⋯”).
  Menu ma `role="menu"`, a pozycje `role="menuitem"` z tymi samymi tekstami co przyciski (stan przełączników przez
  `aria-checked` na `menuitemcheckbox` albo przez zmianę tekstu, jak w przycisku).
  Działanie:
  - Enter, Spacja albo strzałka w dół na przycisku otwiera menu i daje fokus pierwszej pozycji.
  - Strzałki góra i dół przechodzą po pozycjach w kółko, Home i End skaczą na początek i koniec.
  - Enter albo Spacja wykonuje to samo co przycisk paska i zamyka menu.
  - Esc zamyka menu i oddaje fokus przyciskowi „⋯”; Tab, klik poza menu i zmiana szerokości okna na co najmniej 600 px
    też je zamykają.
  - Przy otwartym menu globalne skróty jednoklawiszowe silnika (np. `C`, strzałki ekranów) nie działają; menu
    pochłania obsługiwane klawisze, a Tab i Shift+Tab zamykają je bez blokowania fokusu. Przycisk „Więcej” ma
    `aria-controls` wskazujące menu.
  - Menu nie wychodzi poza okno.

  Od 600 px te cztery przyciski są w pasku, a przycisk „⋯” jest ukryty i poza kolejnością Tab.
- Przycisk „Więcej” i menu dziedziczą motyw: tło i tekst z tokenów, kontrast WCAG AA w obu motywach.
- Pasek ma też inne przyciski z `aria-haspopup="menu"`, np. chip konta. Przycisk menu paska rozpoznaje się wyłącznie po
  nazwie dostępnej „Więcej”. Każde menu otwierane z paska, także menu konta, mieści się w oknie przy szerokości 375 px.

Uzupełnienia (widok makiety desktop / mobile, V-01 do V-22; spec `docs/specs/2026-11-06-prototyp-tryb-widoku.md`):
- „Widok” (desktop, mobile) to stan oglądania ramki ekranu, odrębny od grupy „Tryb” (Klikalny / Komentarze).
- Pasek i menu „⋯” nie mają kontrolki widoku: widok ekranu ustawia wyłącznie jego przełącznik lokalny. Start strony zawsze „Desktop”.
- Przełącznik lokalny: w `.screen-meta` każdego ekranu z ramką element `[data-proto-view-switch]` z przyciskami „Desktop”
  i „Mobile” (`aria-pressed`). Ekran bez ramki nie ma przełącznika i ma zawsze widok desktop.
- Widok mobile: w `.frame` element `iframe[data-proto-view="mobile"]` (iframe widoku) z tym samym `index.html` w roli
  osadzonej `?embed=view#sN`; szerokość min(375 px, dostępna); wysokość według treści, bez wewnętrznego przewijania.
  Treść ramki na stronie jest wtedy ukryta (`display: none`); iframe stoi w kontenerze `.anno-view-host`, a ramka
  ekranu ma atrybut `data-proto-view-on="mobile"` (w desktop atrybutu nie ma). Dokument iframe widoku ma na `<html>` te same klasy stanu co
  strona (`proto-hotspots`, `proto-pins-hidden`, `proto-presentation`) i ten sam `data-theme`; pinezki wątków mobile są
  w dokumencie iframe (`button.anno-pin`).
- Makieta z `min-height: 100vh` dostaje w iframe widoku wysokość początkową ramki; wysokości zależnej od okna w makiecie
  mobile należy unikać.
- Komunikat iframe widoku: gdy iframe nie zgłosi gotowości w 5 s od rozpoczęcia ładowania, w ramce jest tekst
  „Nie udało się wczytać widoku mobile” i przycisk „Spróbuj ponownie”; w konsoli strony jeden wpis błędu z id ekranu.
- Panel: na karcie wątku plakietka `[data-proto-view-badge]` z tekstem „Desktop” albo „Mobile”; w panelu grupa
  `role="group"` `aria-label="Widok wątków"` z przyciskami „Wszystkie”, „Desktop”, „Mobile” (`aria-pressed`, start
  „Wszystkie”), łączona z filtrami statusu iloczynem; liczniki filtrów statusu liczą wątki po filtrze widoku.
- Porównanie z iteracją i podgląd przywracania: w `dialog[open]` grupa `role="group"` `aria-label="Widok"` (Desktop
  domyślnie); „Mobile” ustawia iframe podglądu na min(375 px, dostępna) dla każdej iteracji. Okno „Przywróć…” ma podgląd
  wybranej iteracji (`iframe` z `?embed=compare#sN` w `.anno-restore-preview`); okno porównania dostaje w mobile klasę
  `anno-view-mobile`.
- Skróty jednoklawiszowe strony (C, G, [, ]) działają, gdy fokus jest na stronie; z fokusem wewnątrz iframe widoku
  przekazywany jest tylko Esc (zamknięcie okienka i powrót fokusu).
- W iframe widoku linki `#…` nie zmieniają ekranu: `#sN` przechodzi na stronę, pozostałe są pomijane.
- Serwer bez obsługi widoków (sesja bez `"views"` w `server.features`): komentowanie w widoku mobile jest zablokowane z
  komunikatem; desktop działa.
- Wiadomości strona ↔ iframe widoku (`postMessage`, typy z prefiksem `skillsync-proto:`): `ready`, `height`, `state`,
  `threads`, `pick`, `pin-open`, `navigate`, `escape`, `focus-return`; odbiorca sprawdza `event.source` i typ, a poza
  `file://` także `origin`. `threads` bez treści wiadomości, `navigate` tylko `#sN`.
