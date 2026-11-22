# Prototype screen patterns

Ready-made HTML fragments to paste into `index.html`. They use only classes from the prototype vocabulary (the `components.css`
and `screens.css` stylesheets) and icons from `assets/icons/sprite.svg`. Compose the patterns; do not invent new classes if the vocabulary has an equivalent.
Write your own styles in `project.css` ([style.md](style.md)).

**Icons.** The `index.html` template has all the library's symbols in the sprite (`assets/icons/sprite.svg`, list and preview:
`assets/icons/ICONS.md`, `preview.html`), including those used by the patterns below (`ic-ban`, `ic-calendar-off`, `ic-list`).
`skillsync prototype migrate` adds to the prototype the symbols added in later versions of the library; custom symbols outside `ic-*`
stay unchanged.
Usage: `<svg width="16" height="16" aria-hidden="true"><use href="#ic-plus"/></svg>`; a `<use>` without a symbol in the sprite shows nothing.

## A screen in the document: section, header, frame

Every screen is a `section.screen` with an identifier `sN` (continuous numbering; once the review has started do not change the `id`, because the `#sN` addresses
and the threads depend on it). The `screen-meta` header contains the number and title, one sentence about the user's task and references to requirements.
The mock-up sits in a `frame` with a `frame-bar` bar (dots and address). Remarks about behavior that a static screen cannot show
go into `notes`.

```html
<section class="screen" id="s2" aria-labelledby="s2-h">
  <header class="screen-meta">
    <h2 id="s2-h">2. Customer list</h2>
    <p>The account manager browses customers and opens the card of the chosen one.</p>
    <ul class="screen-refs"><li class="ref">US-3</li><li class="ref">§4.1</li></ul>
  </header>
  <div class="frame">
    <div class="frame-bar">
      <span class="frame-dots" aria-hidden="true"><span></span><span></span><span></span></span>
      <span class="frame-url">/customers</span>
    </div>
    <div class="app">
      <!-- application shell: the "Shell" section -->
    </div>
  </div>
  <ul class="notes">
    <li>Deleting a customer can be undone within 10 seconds.</li>
  </ul>
</section>
```

A screen with several frames (e.g. a view and the same place with an expanded menu): each frame is preceded by a caption `<p class="frame-caption">description</p>`,
and the engine numbers it itself ("Frame 2 — expanded context menu"); a frame without a caption in such a screen gets a number on the top
edge, and there is a gap between frames. In notes and comments refer to the frame number, because that is what is visible on the page.

```html
<p class="frame-caption">Grid: rooms, menu collapsed</p>
<div class="frame">…</div>
<p class="frame-caption">Expanded context menu of a slot</p>
<div class="frame">…</div>
```

The height of the `.app` shell (40rem by default) is changed by the variants `app-tall` (52rem) and `app-taller` (64rem) for screens with long
content and `app-narrow` (34rem) for short ones. None of them changes the width.

## Navigation: data-goto and data-proto-nav

A transition to another screen is a `data-goto="sN"` attribute on any element (the engine adds `tabindex`, the button role and Enter and Space handling to it);
it works in the "Clickable" mode. Accessibility: where natural, use a native element (`<button>` for an action, `<a href="#sN">`
for a transition), because it has focus and keyboard support by definition; leave the attribute on a `div`, `tr` or card for elements that cannot be a button. An element with `data-goto` is commentable, so it gets a `data-proto-id`.

```html
<button type="button" class="btn btn-primary" data-goto="s3" data-proto-id="s2-nowy">New customer</button>
<a class="nav-item" href="#s2" data-goto="s2" data-proto-id="s3-wroc"><svg width="16" height="16" aria-hidden="true"><use href="#ic-chevron-left"/></svg>Back to the list</a>
```

The list of screens in the bar is a `nav.screen-nav` with links `<a href="#sN">N Name</a>`. The engine turns it into a switcher with a dropdown list and search;
**do not set `data-proto-nav` by default**. The `data-proto-nav="strip"` attribute keeps a simple strip of links only at the owner's explicit request and only up to 5 screens
(with more, the engine ignores it and builds the switcher). The link to the prototype list (the house ⌂) is in both modes (for screens with links in `nav`).

```html
<nav class="screen-nav" aria-label="Screens" data-proto-nav="strip">
  <a href="#s1">1 Start</a>
  <a href="#s2">2 Customer list</a>
</nav>
```

## Application shell: sidebar and topbar

A sidebar with navigation and a top bar with breadcrumbs; content in `main` > `page`.

```html
<div class="app">
  <aside class="sidebar">
    <div class="sidebar-app"><span class="app-mark"></span><span class="strong">Application</span></div>
    <nav class="sidebar-nav" aria-label="Main navigation">
      <a class="nav-item here" href="#s2" aria-current="page"><svg width="16" height="16" aria-hidden="true"><use href="#ic-users"/></svg>Customers</a>
      <a class="nav-item" href="#s5"><svg width="16" height="16" aria-hidden="true"><use href="#ic-settings"/></svg>Settings</a>
    </nav>
    <div class="sidebar-foot t-hint">v1.0</div>
  </aside>
  <div class="main">
    <header class="topbar">
      <div class="topbar-left"><span class="crumbs">Customers</span></div>
      <div class="topbar-right">
        <button type="button" class="icon-btn" aria-label="Notifications" data-proto-id="s2-powiadomienia"><svg width="16" height="16" aria-hidden="true"><use href="#ic-bell"/></svg></button>
        <span class="avatar avatar-sm">AK</span>
      </div>
    </header>
    <main class="page">
      <div class="page-head">
        <h3 class="t-page-title" data-proto-id="s2-tytul">Customers</h3>
        <div class="page-actions"><button type="button" class="btn btn-primary" data-proto-id="s2-dodaj">Add</button></div>
      </div>
      <!-- page content -->
    </main>
  </div>
</div>
```

The active menu item has the class `here` and `aria-current="page"`.

## Data table: dt, filters, pager

A table with a toolbar (search, filter chips), bulk actions and pagination.

```html
<div class="dt">
  <div class="dt-toolbar">
    <div class="dt-toolbar-row">
      <div class="dt-search input-group"><svg width="16" height="16" aria-hidden="true"><use href="#ic-search"/></svg><input class="input" type="search" placeholder="Search customers" aria-label="Search customers" data-proto-id="s2-szukaj"></div>
      <div class="dt-toolbar-right">
        <button type="button" class="btn btn-outline btn-sm" data-proto-id="s2-filtr"><svg width="16" height="16" aria-hidden="true"><use href="#ic-filter"/></svg>Filters <span class="filter-count">2</span></button>
      </div>
    </div>
    <div class="dt-chips"><span class="chip chip-active">Status: active</span><span class="chip chip-dashed">+ Add filter</span></div>
  </div>
  <table class="table">
    <thead><tr><th data-proto-id="s2-th-nazwa">Name</th><th data-proto-id="s2-th-status">Status</th><th class="right">Value</th><th></th></tr></thead>
    <tbody>
      <tr class="clickable" data-goto="s3" data-proto-id="s2-wiersz-1">
        <td class="strong">Alpha Ltd.</td>
        <td><span class="badge badge-success">Active</span></td>
        <td class="num right">$12,400</td>
        <td class="right"><button type="button" class="icon-btn" aria-label="More actions" data-proto-id="s2-wiersz-1-menu"><svg width="16" height="16" aria-hidden="true"><use href="#ic-more"/></svg></button></td>
      </tr>
    </tbody>
  </table>
  <div class="dt-foot">
    <span class="t-hint">1–20 of 134</span>
    <div class="pager">
      <button type="button" class="btn btn-outline btn-xs" aria-label="Previous page" data-proto-id="s2-poprzednia"><svg width="16" height="16" aria-hidden="true"><use href="#ic-chevron-left"/></svg></button>
      <button type="button" class="btn btn-outline btn-xs" aria-label="Next page" data-proto-id="s2-nastepna"><svg width="16" height="16" aria-hidden="true"><use href="#ic-chevron-right"/></svg></button>
    </div>
  </div>
</div>
```

After rows are selected, show a `bulk-bar` with the number of selected rows and bulk actions.

## Form: form-grid and field-error

A field is a `field` with a label, a control and an optional `field-error`; mark required ones with an asterisk `req`. Put the error message in the mock-up
together with the `input-invalid` state, so the reviewer sees the error path.

```html
<form class="form-layout" data-proto-id="s3-formularz">
  <div class="form-head"><h3 class="t-section" data-proto-id="s3-naglowek">Customer details</h3></div>
  <div class="form-col">
    <div class="form-grid form-grid-2">
      <div class="field">
        <label class="t-label" for="s3-nazwa">Name <span class="req">*</span></label>
        <input class="input input-invalid" id="s3-nazwa" type="text" aria-invalid="true" aria-describedby="s3-nazwa-err" data-proto-id="s3-nazwa">
        <p class="field-error" id="s3-nazwa-err" role="alert">Name is required.</p>
      </div>
      <div class="field">
        <label class="t-label" for="s3-typ">Type</label>
        <select class="select" id="s3-typ" data-proto-id="s3-typ"><option>Company</option><option>Person</option></select>
      </div>
    </div>
    <div class="field">
      <label class="t-label" for="s3-opis">Description</label>
      <textarea class="textarea" id="s3-opis" rows="3" data-proto-id="s3-opis"></textarea>
      <p class="t-hint">Visible to the team only.</p>
    </div>
  </div>
  <div class="form-foot">
    <button type="button" class="btn btn-outline" data-goto="s2" data-proto-id="s3-anuluj">Cancel</button>
    <button type="button" class="btn btn-primary" data-proto-id="s3-zapisz">Save</button>
  </div>
</form>
```

## Drawer and window: drawer and modal

Static mock-ups, inserted into the screen frame next to the content they cover: a scrim (`*-scrim`) and a panel. They show the "open" state;
describe the opening behavior in `notes`.

```html
<div class="drawer-scrim"></div>
<aside class="drawer" role="dialog" aria-label="Customer details" data-proto-id="s2-szuflada">
  <div class="drawer-head"><h3 class="t-card-title" data-proto-id="s2-szuflada-tytul">Alpha Ltd.</h3>
    <button type="button" class="icon-btn" aria-label="Close" data-proto-id="s2-szuflada-zamknij"><svg width="16" height="16" aria-hidden="true"><use href="#ic-x"/></svg></button></div>
  <div class="drawer-body"><p class="t-body">Drawer content.</p></div>
  <div class="drawer-foot"><button type="button" class="btn btn-primary" data-proto-id="s2-szuflada-zapisz">Save</button></div>
</aside>

<div class="modal-scrim"></div>
<div class="modal" role="alertdialog" aria-label="Delete the customer?" data-proto-id="s2-okno">
  <div class="modal-head"><h3 class="t-card-title" data-proto-id="s2-okno-tytul">Delete the customer?</h3></div>
  <div class="modal-body"><p class="t-body">This operation cannot be undone.</p></div>
  <div class="modal-foot">
    <button type="button" class="btn btn-outline" data-proto-id="s2-okno-anuluj">Cancel</button>
    <button type="button" class="btn btn-destructive" data-proto-id="s2-okno-usun">Delete</button>
  </div>
</div>
```

A window, drawer and panel fit their content at 1280×800 without a scrollbar of their own (`.modal-body` scrolls only when the window
has reached `max-height`, and that is a last resort). Show an open dropdown list, autocomplete, calendar or menu as a
`.popover.popover-float` **next to** the window (a sibling of `.modal`/`.drawer` in the frame), never inside `.modal-body`/`.drawer-body`:
their `overflow` clips the list and hides it under the footer. When there is not enough room below the field, open the list upward (`bottom` instead of `top`);
the list shows at least 5 items and scrolls by itself.

```html
<div class="modal" role="dialog" aria-label="New class" data-proto-id="s2-okno">…the "Room" field in .modal-body, a footer with buttons…</div>
<ul class="popover popover-float" role="listbox" style="left:calc(50% - 15rem); bottom:9rem; width:30rem" data-proto-id="s2-sala-lista">
  <li role="option"><button type="button" class="menu-item">A1 · 24 seats</button></li>
  <li role="option"><button type="button" class="menu-item">A2 · 30 seats</button></li>
</ul>
```

## Kanban board

Columns with cards and counters; show the dragging state with the class `kcard-dragging` on one card and `dropping` on the target column.

```html
<div class="kanban">
  <div class="kcol">
    <div class="kcol-head"><span class="kcol-title">To do</span><span class="kcol-count">2</span></div>
    <div class="kcol-body">
      <div class="kcard" data-proto-id="s4-karta-1">
        <div class="kcard-title">Prepare an offer</div>
        <div class="kcard-meta"><span class="badge badge-info">Offer</span><span class="kcard-hours">4 h</span></div>
      </div>
      <div class="kcard kcard-dragging" data-proto-id="s4-karta-2">
        <div class="kcard-title">Gather requirements</div>
      </div>
    </div>
  </div>
  <div class="kcol dropping">
    <div class="kcol-head"><span class="kcol-title">In progress</span><span class="kcol-count">0</span></div>
    <div class="kcol-body"><div class="kdrop t-hint">Drop here</div></div>
  </div>
</div>
```

## Week grid: week-grid

Days in columns, time slots in rows. An event spans N slots (`grid-row: span N` in `style`), the status color is given by the
`week-grid-event-*` variant. A clash is marked with `week-grid-clash`, a day off with `week-grid-off` (with the `ic-calendar-off` icon), today with `today`.

```html
<div class="week-grid-scroll">
  <div class="week-grid" role="grid" aria-label="Week 12">
    <div class="week-grid-corner"></div>
    <div class="week-grid-day today">Mon 17</div>
    <div class="week-grid-day">Tue 18</div>
    <div class="week-grid-day weekend">Sat 22</div>

    <div class="week-grid-slot week-grid-slot-time">08:00</div>
    <div class="week-grid-cell">
      <div class="week-grid-stack">
        <div class="week-grid-event week-grid-event-info" style="grid-row: span 2" data-proto-id="s5-zdarzenie-1">
          <span class="week-grid-event-full">Workshop</span><span class="week-grid-event-abbr">Wrk</span>
          <span class="t-hint">room 12</span>
        </div>
      </div>
    </div>
    <div class="week-grid-cell week-grid-off"><svg width="16" height="16" aria-hidden="true"><use href="#ic-calendar-off"/></svg></div>
    <div class="week-grid-cell weekend"></div>

    <div class="week-grid-slot week-grid-slot-time">09:00</div>
    <div class="week-grid-cell">
      <div class="week-grid-event week-grid-event-error week-grid-clash" data-proto-id="s5-zdarzenie-2">Clash with the workshop</div>
    </div>
    <div class="week-grid-cell"></div>
    <div class="week-grid-cell weekend"></div>
  </div>
</div>
```

Color variant: `week-grid-event-info|success|warning|error|neutral|muted`.

## States: empty, alert, loading, error, no permissions

Show every screen with data in the empty, error and no-permissions states (separate screens or separate frames), so the review covers the paths
beyond the "happy" one.

```html
<!-- empty -->
<div class="empty">
  <span class="empty-icon"><svg width="24" height="24" aria-hidden="true"><use href="#ic-list"/></svg></span>
  <h3 class="t-card-title" data-proto-id="s2-pusty-tytul">No customers</h3>
  <p class="t-hint">Add the first customer to get started.</p>
  <button type="button" class="btn btn-primary" data-proto-id="s2-pusty-dodaj">Add a customer</button>
</div>

<!-- loading -->
<div class="card card-pad" role="status" aria-label="Loading">
  <div class="progress"><div class="pulse"></div></div>
  <p class="t-hint">Loading customers…</p>
</div>

<!-- error -->
<div class="alert alert-error" role="alert">
  <span class="alert-title">Failed to load the list.</span>
  <p class="t-body">Check the connection and try again.</p>
  <button type="button" class="btn btn-outline btn-sm" data-proto-id="s2-blad-ponow">Try again</button>
</div>

<!-- no permissions -->
<div class="empty">
  <span class="empty-icon"><svg width="24" height="24" aria-hidden="true"><use href="#ic-ban"/></svg></span>
  <h3 class="t-card-title" data-proto-id="s2-brak-tytul">No access</h3>
  <p class="t-hint">Ask an administrator for the "Account manager" role.</p>
</div>
```

Informational messages and warnings are `alert-info`, `alert-warning`, `alert-success`; status badges are `badge-*`.
