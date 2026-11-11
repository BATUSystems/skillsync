# Wzorce ekranów prototypu

Gotowe fragmenty HTML do wklejenia w `index.html`. Używają wyłącznie klas ze słownika prototypu (arkusze `components.css`
i `screens.css`) oraz ikon z `assets/icons/sprite.svg`. Wzorce składaj; nie wymyślaj nowych klas, jeśli słownik ma odpowiednik.
Styl własny wpisuj w `project.css` ([style.md](style.md)).

**Ikony.** Szablon `index.html` ma w sprite wszystkie symbole biblioteki (`assets/icons/sprite.svg`, lista i podgląd:
`assets/icons/ICONS.md`, `preview.html`), w tym te, których używają wzorce niżej (`ic-ban`, `ic-calendar-off`, `ic-list`).
`skillsync prototype migrate` dopisuje do prototypu symbole dodane w kolejnych wersjach biblioteki; własne symbole spoza `ic-*`
zostają bez zmian.
Użycie: `<svg width="16" height="16" aria-hidden="true"><use href="#ic-plus"/></svg>`; `<use>` bez symbolu w sprite nic nie pokaże.

## Ekran w dokumencie: section, nagłówek, ramka

Każdy ekran to `section.screen` z identyfikatorem `sN` (numeracja ciągła; po starcie przeglądu `id` nie zmieniaj, bo adresy `#sN`
i wątki od niego zależą). Nagłówek `screen-meta` zawiera numer i tytuł, jedno zdanie o zadaniu użytkownika i odnośniki do wymagań.
Makieta siedzi w `frame` z paskiem `frame-bar` (kropki i adres). Uwagi o zachowaniu, którego statyczny ekran nie pokaże,
idą do `notes`.

```html
<section class="screen" id="s2" aria-labelledby="s2-h">
  <header class="screen-meta">
    <h2 id="s2-h">2. Lista klientów</h2>
    <p>Opiekun przegląda klientów i otwiera kartę wybranego.</p>
    <ul class="screen-refs"><li class="ref">US-3</li><li class="ref">§4.1</li></ul>
  </header>
  <div class="frame">
    <div class="frame-bar">
      <span class="frame-dots" aria-hidden="true"><span></span><span></span><span></span></span>
      <span class="frame-url">/klienci</span>
    </div>
    <div class="app">
      <!-- powłoka aplikacji: sekcja „Powłoka” -->
    </div>
  </div>
  <ul class="notes">
    <li>Usunięcie klienta można cofnąć przez 10 sekund.</li>
  </ul>
</section>
```

Ekran z kilkoma ramkami (np. widok i to samo miejsce z rozwiniętym menu): przed każdą ramką stoi podpis `<p class="frame-caption">opis</p>`,
a silnik numeruje go sam („Ramka 2 — rozwinięte menu kontekstowe”); ramka bez podpisu w takim ekranie dostaje numer w górnej
krawędzi, a między ramkami jest odstęp. W notatkach i komentarzach odwołuj się do numeru ramki, bo to on jest widoczny na stronie.

```html
<p class="frame-caption">Siatka: sale, menu zwinięte</p>
<div class="frame">…</div>
<p class="frame-caption">Rozwinięte menu kontekstowe terminu</p>
<div class="frame">…</div>
```

Wysokość powłoki `.app` (domyślnie 40rem) zmieniają warianty `app-tall` (52rem) i `app-taller` (64rem) dla ekranów z długą
treścią oraz `app-narrow` (34rem) dla krótkich. Żaden z nich nie zmienia szerokości.

## Nawigacja: data-goto i data-proto-nav

Przejście do innego ekranu to atrybut `data-goto="sN"` na dowolnym elemencie (silnik dodaje mu `tabindex`, rolę przycisku i obsługę Enter oraz Spacji);
działa w trybie „Klikalny”. Dostępność: tam, gdzie to naturalne, użyj elementu natywnego (`<button>` dla akcji, `<a href="#sN">`
dla przejścia), bo ma fokus i klawiaturę z definicji; atrybut na `div`, `tr` czy karcie zostaw dla elementów, które nie mogą być przyciskiem. Element z `data-goto` jest komentowalny, więc dostaje `data-proto-id`.

```html
<button type="button" class="btn btn-primary" data-goto="s3" data-proto-id="s2-nowy">Nowy klient</button>
<a class="nav-item" href="#s2" data-goto="s2" data-proto-id="s3-wroc"><svg width="16" height="16" aria-hidden="true"><use href="#ic-chevron-left"/></svg>Wróć do listy</a>
```

Lista ekranów w pasku to `nav.screen-nav` z linkami `<a href="#sN">N Nazwa</a>`. Silnik zamienia ją na przełącznik z wyszukiwaniem;
atrybut `data-proto-nav="strip"` na `nav.screen-nav` zostawia prosty pasek linków (przy kilku ekranach bywa wygodniejszy).

```html
<nav class="screen-nav" aria-label="Ekrany" data-proto-nav="strip">
  <a href="#s1">1 Start</a>
  <a href="#s2">2 Lista klientów</a>
</nav>
```

## Powłoka aplikacji: sidebar i topbar

Pasek boczny z nawigacją i górny pasek z okruszkami; treść w `main` > `page`.

```html
<div class="app">
  <aside class="sidebar">
    <div class="sidebar-app"><span class="app-mark"></span><span class="strong">Aplikacja</span></div>
    <nav class="sidebar-nav" aria-label="Główna nawigacja">
      <a class="nav-item here" href="#s2" aria-current="page"><svg width="16" height="16" aria-hidden="true"><use href="#ic-users"/></svg>Klienci</a>
      <a class="nav-item" href="#s5"><svg width="16" height="16" aria-hidden="true"><use href="#ic-settings"/></svg>Ustawienia</a>
    </nav>
    <div class="sidebar-foot t-hint">v1.0</div>
  </aside>
  <div class="main">
    <header class="topbar">
      <div class="topbar-left"><span class="crumbs">Klienci</span></div>
      <div class="topbar-right">
        <button type="button" class="icon-btn" aria-label="Powiadomienia" data-proto-id="s2-powiadomienia"><svg width="16" height="16" aria-hidden="true"><use href="#ic-bell"/></svg></button>
        <span class="avatar avatar-sm">AK</span>
      </div>
    </header>
    <main class="page">
      <div class="page-head">
        <h3 class="t-page-title" data-proto-id="s2-tytul">Klienci</h3>
        <div class="page-actions"><button type="button" class="btn btn-primary" data-proto-id="s2-dodaj">Dodaj</button></div>
      </div>
      <!-- treść strony -->
    </main>
  </div>
</div>
```

Aktywna pozycja menu ma klasę `here` i `aria-current="page"`.

## Tabela danych: dt, filtry, pager

Tabela z paskiem narzędzi (wyszukiwanie, żetony filtrów), akcjami zbiorczymi i stronicowaniem.

```html
<div class="dt">
  <div class="dt-toolbar">
    <div class="dt-toolbar-row">
      <div class="dt-search input-group"><svg width="16" height="16" aria-hidden="true"><use href="#ic-search"/></svg><input class="input" type="search" placeholder="Szukaj klienta" aria-label="Szukaj klienta" data-proto-id="s2-szukaj"></div>
      <div class="dt-toolbar-right">
        <button type="button" class="btn btn-outline btn-sm" data-proto-id="s2-filtr"><svg width="16" height="16" aria-hidden="true"><use href="#ic-filter"/></svg>Filtry <span class="filter-count">2</span></button>
      </div>
    </div>
    <div class="dt-chips"><span class="chip chip-active">Status: aktywny</span><span class="chip chip-dashed">+ Dodaj filtr</span></div>
  </div>
  <table class="table">
    <thead><tr><th data-proto-id="s2-th-nazwa">Nazwa</th><th data-proto-id="s2-th-status">Status</th><th class="right">Wartość</th><th></th></tr></thead>
    <tbody>
      <tr class="clickable" data-goto="s3" data-proto-id="s2-wiersz-1">
        <td class="strong">Alfa sp. z o.o.</td>
        <td><span class="badge badge-success">Aktywny</span></td>
        <td class="num right">12 400 zł</td>
        <td class="right"><button type="button" class="icon-btn" aria-label="Więcej akcji" data-proto-id="s2-wiersz-1-menu"><svg width="16" height="16" aria-hidden="true"><use href="#ic-more"/></svg></button></td>
      </tr>
    </tbody>
  </table>
  <div class="dt-foot">
    <span class="t-hint">1–20 z 134</span>
    <div class="pager">
      <button type="button" class="btn btn-outline btn-xs" aria-label="Poprzednia strona" data-proto-id="s2-poprzednia"><svg width="16" height="16" aria-hidden="true"><use href="#ic-chevron-left"/></svg></button>
      <button type="button" class="btn btn-outline btn-xs" aria-label="Następna strona" data-proto-id="s2-nastepna"><svg width="16" height="16" aria-hidden="true"><use href="#ic-chevron-right"/></svg></button>
    </div>
  </div>
</div>
```

Po zaznaczeniu wierszy pokaż `bulk-bar` z liczbą zaznaczonych i akcjami zbiorczymi.

## Formularz: form-grid i field-error

Pole to `field` z etykietą, kontrolką i opcjonalnym `field-error`; wymagane oznacz gwiazdką `req`. Komunikat błędu daj w makiecie
razem ze stanem `input-invalid`, żeby recenzent widział ścieżkę błędu.

```html
<form class="form-layout" data-proto-id="s3-formularz">
  <div class="form-head"><h3 class="t-section" data-proto-id="s3-naglowek">Dane klienta</h3></div>
  <div class="form-col">
    <div class="form-grid form-grid-2">
      <div class="field">
        <label class="t-label" for="s3-nazwa">Nazwa <span class="req">*</span></label>
        <input class="input input-invalid" id="s3-nazwa" type="text" aria-invalid="true" aria-describedby="s3-nazwa-err" data-proto-id="s3-nazwa">
        <p class="field-error" id="s3-nazwa-err" role="alert">Nazwa jest wymagana.</p>
      </div>
      <div class="field">
        <label class="t-label" for="s3-typ">Typ</label>
        <select class="select" id="s3-typ" data-proto-id="s3-typ"><option>Firma</option><option>Osoba</option></select>
      </div>
    </div>
    <div class="field">
      <label class="t-label" for="s3-opis">Opis</label>
      <textarea class="textarea" id="s3-opis" rows="3" data-proto-id="s3-opis"></textarea>
      <p class="t-hint">Widoczny tylko dla zespołu.</p>
    </div>
  </div>
  <div class="form-foot">
    <button type="button" class="btn btn-outline" data-goto="s2" data-proto-id="s3-anuluj">Anuluj</button>
    <button type="button" class="btn btn-primary" data-proto-id="s3-zapisz">Zapisz</button>
  </div>
</form>
```

## Szuflada i okno: drawer i modal

Makiety statyczne, wstawiane w ramkę ekranu obok treści, którą przykrywają: zasłona (`*-scrim`) i panel. Pokazują stan „otwarte”;
zachowanie otwierania opisz w `notes`.

```html
<div class="drawer-scrim"></div>
<aside class="drawer" role="dialog" aria-label="Szczegóły klienta" data-proto-id="s2-szuflada">
  <div class="drawer-head"><h3 class="t-card-title" data-proto-id="s2-szuflada-tytul">Alfa sp. z o.o.</h3>
    <button type="button" class="icon-btn" aria-label="Zamknij" data-proto-id="s2-szuflada-zamknij"><svg width="16" height="16" aria-hidden="true"><use href="#ic-x"/></svg></button></div>
  <div class="drawer-body"><p class="t-body">Treść szuflady.</p></div>
  <div class="drawer-foot"><button type="button" class="btn btn-primary" data-proto-id="s2-szuflada-zapisz">Zapisz</button></div>
</aside>

<div class="modal-scrim"></div>
<div class="modal" role="alertdialog" aria-label="Usunąć klienta?" data-proto-id="s2-okno">
  <div class="modal-head"><h3 class="t-card-title" data-proto-id="s2-okno-tytul">Usunąć klienta?</h3></div>
  <div class="modal-body"><p class="t-body">Operacji nie można cofnąć.</p></div>
  <div class="modal-foot">
    <button type="button" class="btn btn-outline" data-proto-id="s2-okno-anuluj">Anuluj</button>
    <button type="button" class="btn btn-destructive" data-proto-id="s2-okno-usun">Usuń</button>
  </div>
</div>
```

Okno, szuflada i panel mieszczą treść przy 1280×800 bez własnego paska przewijania (`.modal-body` przewija się dopiero wtedy, gdy okno
dobiło do `max-height`, i to jest ostateczność). Otwartą listę rozwijaną, autouzupełnianie, kalendarz albo menu pokaż jako
`.popover.popover-float` **obok** okna (rodzeństwo `.modal`/`.drawer` w ramce), nigdy wewnątrz `.modal-body`/`.drawer-body`:
ich `overflow` obcina listę i chowa ją pod stopką. Gdy pod polem brakuje miejsca, otwórz listę w górę (`bottom` zamiast `top`);
lista pokazuje co najmniej 5 pozycji i sama się przewija.

```html
<div class="modal" role="dialog" aria-label="Nowe zajęcia" data-proto-id="s2-okno">…pole „Sala” w .modal-body, stopka z przyciskami…</div>
<ul class="popover popover-float" role="listbox" style="left:calc(50% - 15rem); bottom:9rem; width:30rem" data-proto-id="s2-sala-lista">
  <li role="option"><button type="button" class="menu-item">A1 · 24 miejsca</button></li>
  <li role="option"><button type="button" class="menu-item">A2 · 30 miejsc</button></li>
</ul>
```

## Tablica kanban

Kolumny z kartami i licznikami; stan przeciągania pokaż klasą `kcard-dragging` na jednej karcie i `dropping` na kolumnie docelowej.

```html
<div class="kanban">
  <div class="kcol">
    <div class="kcol-head"><span class="kcol-title">Do zrobienia</span><span class="kcol-count">2</span></div>
    <div class="kcol-body">
      <div class="kcard" data-proto-id="s4-karta-1">
        <div class="kcard-title">Przygotować ofertę</div>
        <div class="kcard-meta"><span class="badge badge-info">Oferta</span><span class="kcard-hours">4 h</span></div>
      </div>
      <div class="kcard kcard-dragging" data-proto-id="s4-karta-2">
        <div class="kcard-title">Zebrać wymagania</div>
      </div>
    </div>
  </div>
  <div class="kcol dropping">
    <div class="kcol-head"><span class="kcol-title">W toku</span><span class="kcol-count">0</span></div>
    <div class="kcol-body"><div class="kdrop t-hint">Upuść tutaj</div></div>
  </div>
</div>
```

## Siatka tygodnia: week-grid

Dni w kolumnach, sloty czasu w wierszach. Zdarzenie rozciąga się na N slotów (`grid-row: span N` w `style`), kolor statusu daje
wariant `week-grid-event-*`. Kolizję oznacza `week-grid-clash`, dzień wolny `week-grid-off` (z ikoną `ic-calendar-off`), dzisiejszy dzień `today`.

```html
<div class="week-grid-scroll">
  <div class="week-grid" role="grid" aria-label="Tydzień 12">
    <div class="week-grid-corner"></div>
    <div class="week-grid-day today">Pn 17</div>
    <div class="week-grid-day">Wt 18</div>
    <div class="week-grid-day weekend">Sb 22</div>

    <div class="week-grid-slot week-grid-slot-time">08:00</div>
    <div class="week-grid-cell">
      <div class="week-grid-stack">
        <div class="week-grid-event week-grid-event-info" style="grid-row: span 2" data-proto-id="s5-zdarzenie-1">
          <span class="week-grid-event-full">Warsztat</span><span class="week-grid-event-abbr">Wrk</span>
          <span class="t-hint">sala 12</span>
        </div>
      </div>
    </div>
    <div class="week-grid-cell week-grid-off"><svg width="16" height="16" aria-hidden="true"><use href="#ic-calendar-off"/></svg></div>
    <div class="week-grid-cell weekend"></div>

    <div class="week-grid-slot week-grid-slot-time">09:00</div>
    <div class="week-grid-cell">
      <div class="week-grid-event week-grid-event-error week-grid-clash" data-proto-id="s5-zdarzenie-2">Kolizja z warsztatem</div>
    </div>
    <div class="week-grid-cell"></div>
    <div class="week-grid-cell weekend"></div>
  </div>
</div>
```

Wariant koloru: `week-grid-event-info|success|warning|error|neutral|muted`.

## Stany: empty, alert, ładowanie, błąd, brak uprawnień

Każdy ekran z danymi pokaż w stanie pustym, błędu i braku uprawnień (osobne ekrany albo osobne ramki), żeby przegląd objął ścieżki
poza „szczęśliwą”.

```html
<!-- pusty -->
<div class="empty">
  <span class="empty-icon"><svg width="24" height="24" aria-hidden="true"><use href="#ic-list"/></svg></span>
  <h3 class="t-card-title" data-proto-id="s2-pusty-tytul">Brak klientów</h3>
  <p class="t-hint">Dodaj pierwszego klienta, żeby zacząć.</p>
  <button type="button" class="btn btn-primary" data-proto-id="s2-pusty-dodaj">Dodaj klienta</button>
</div>

<!-- ładowanie -->
<div class="card card-pad" role="status" aria-label="Ładowanie">
  <div class="progress"><div class="pulse"></div></div>
  <p class="t-hint">Wczytywanie klientów…</p>
</div>

<!-- błąd -->
<div class="alert alert-error" role="alert">
  <span class="alert-title">Nie udało się wczytać listy.</span>
  <p class="t-body">Sprawdź połączenie i spróbuj ponownie.</p>
  <button type="button" class="btn btn-outline btn-sm" data-proto-id="s2-blad-ponow">Spróbuj ponownie</button>
</div>

<!-- brak uprawnień -->
<div class="empty">
  <span class="empty-icon"><svg width="24" height="24" aria-hidden="true"><use href="#ic-ban"/></svg></span>
  <h3 class="t-card-title" data-proto-id="s2-brak-tytul">Brak dostępu</h3>
  <p class="t-hint">Poproś administratora o rolę „Opiekun”.</p>
</div>
```

Komunikaty informacyjne i ostrzeżenia to `alert-info`, `alert-warning`, `alert-success`; plakietki statusu `badge-*`.
