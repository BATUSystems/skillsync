# {{TITLE}}

Slug: {{SLUG}}

A clickable mock-up of screens for review with the team before implementation. Requirements: {{REQUIREMENTS}}

## Requirements-to-screens map

Fill in the table once the screens exist. The example screen `s1` (attribute `data-prototype-example`) is not counted.

| Screen (sN) | Name | Requirement |
|---|---|---|
| s1 | Example screen | — |

## How to open the prototype

- File: open `index.html` in a browser. From a `file://` address the bar shows „Bez serwera” (No server): it is only a preview of the screens,
  without commenting, login and acceptance.
- A plain static server (e.g. `python3 -m http.server` in this directory): look and navigation as from the file, and the bar shows
  „Tylko odczyt” (Read only): the engine reads `comments.json` and shows the threads, but writes nothing.
- Comment server: `skillsync prototype serve <project>` with user accounts; comments, votes, decisions and acceptance
  are shared by everyone with access.

## Comments

Commenting (only with `skillsync prototype serve`, after logging in) is switched on by the „Komentarze (C)” (Comments (C)) button on the bar or the C key. A clicked element gets a thread; you can reply to a thread, vote
and change its status. Writes go to `comments.json` (an operation log that only grows, so it merges well in git).
When the server is unavailable, entries wait in the browser's offline queue and can be retried; entries rejected by the server
stay on the „Pokaż odrzucone” (Show rejected) list.

## Desktop / mobile view

The „Desktop” / „Mobile” switch in the header of a framed screen changes the view of that screen only; after the page loads, every screen
is in Desktop. The mobile view shows the screen frame in an iframe 375 px wide, so the
mock-up's media queries work. A comment gets the view in which it was created, and its pin is visible only in that view; the
„Wątki” (Threads) panel has a badge and a view filter. Acceptance of a screen covers both views.

## Iterations and restoring

After a round of findings the agent freezes the viewed version in the `iterations/` subdirectory (`skillsync prototype iterate`, consecutive numbers N) and only then changes
`index.html`. On a screen you can compare the current state with a chosen iteration. An earlier look of a single screen comes back
with the command `skillsync prototype restore <project> <slug> --screen sN --from N --reason "…" --who <login>` (first with `--dry-run`).

## Acceptance

A screen or the whole project is accepted by a human with the acceptor or developer role: in the interface or with the command `skillsync prototype accept`
(password typed in the terminal). A change to a screen after acceptance invalidates it. The gate `skillsync prototype gate` checks before
implementation that every screen has a current acceptance.

## Where to change content, styles and theme

- Screen content: `index.html`; every screen is a `<section class="screen" id="sN">`.
- Project styles: `project.css` (loaded as the last stylesheet, after `screens.css`; `sync` and `migrate` do not touch it). The library files `prototype.css`, `components.css` and `screens.css` are overwritten by `migrate`, so do not edit them by hand.
- Theme (colours, fonts, rounding): the files named in the project manifest (`prototype.theme.tokens_from`, `prototype.theme.css`).
  `tokens.css` and `theme.css` in this directory are generated from them by `skillsync prototype sync` (and `migrate`); do not edit them by hand.
- The engine (`prototype.js`, `prototype.css`) is a copy from the library; do not change it locally.

## Engine update

`skillsync prototype migrate <project> <slug>` copies the engine files (`prototype.js`, `prototype.css`, `components.css`,
`screens.css`) from the library when they differ, and moves a prototype in the old format (`comments.js`) to `comments.json`.
The copy overwrites these files entirely, so keep your own styles in `project.css`, which the migration does not touch. For an older prototype `migrate` creates `project.css` and warns about classes from the old `screens.css` that the new one lacks; move those rules to `project.css`.
With the `--dry-run` option it shows the changes without writing. `skillsync prototype sync` refreshes only `tokens.css` and `theme.css`.

## Element handles

Every commentable element in a screen frame has a fixed handle `data-proto-id` (e.g. `s2-zapisz`). A thread holds on to the handle, so
do not change it once assigned. Missing handles are checked by `skillsync prototype ids <project> <slug> --check`, and `--fix` adds
them without changing the look.
