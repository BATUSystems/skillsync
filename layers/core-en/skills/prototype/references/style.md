# Prototype style

The look of a prototype is made of six stylesheet layers. Each next one can override the previous, so the loading order
in `index.html` is part of the contract.

## Layers and loading order

| Order | File | Source | Editing |
|---|---|---|---|
| 1 | `tokens.css` | generated: the library's default theme (`assets/theme/default.css`) plus the tokens from the project's `prototype.theme.tokens_from` | do not edit; `skillsync prototype sync` and `migrate` regenerate it |
| 2 | `theme.css` | generated: a copy of `prototype.theme.css` from the manifest | do not edit; change the file the manifest points to (`sync` and `migrate` regenerate it) |
| 3 | `prototype.css` | library (engine): the bar, the screen switcher, commenting, the thread panel, windows, the account | do not edit; `skillsync prototype migrate` updates it |
| 4 | `components.css` | library: the vocabulary components (buttons, fields, tables, cards, kanban, week grid…) | do not edit; `migrate` overwrites it entirely |
| 5 | `screens.css` | library: the document layout (page, screen header, frame) | do not edit; `migrate` overwrites it entirely |
| 6 | `project.css` | prototype: the styles of the project's screens (`init` creates it without rules; `migrate` creates it and links it when missing), loaded last | write your own style here; `sync` and `migrate` do not change an existing file |

```html
<link rel="stylesheet" href="tokens.css">
<link rel="stylesheet" href="theme.css">
<link rel="stylesheet" href="prototype.css">
<link rel="stylesheet" href="components.css">
<link rel="stylesheet" href="screens.css">
<link rel="stylesheet" href="project.css">
```

What follows for the project theme: change the look with **tokens** (`components.css` uses only `var(--…)`); write overriding rules in `project.css`, because with equal specificity the file loaded later wins, so a rule in `theme.css`
loses to `components.css` and `prototype.css`. From `tokens_from` the engine reads the light `:root`, `@theme`, `@theme inline` and the dark `.dark`, `[data-theme="dark"]` and `:root[data-theme="dark"]`; an `@media (prefers-color-scheme: dark)` block is skipped with a warning
(the engine always sets `data-theme`): rewrite it as `[data-theme="dark"] { … }`. `sync --check` ends with FAIL when tokens from `required-tokens.txt` are missing. A prototype without a dark theme: `prototype.dark_theme: false` in the manifest (the `<html>` attributes are written by `init`/`migrate`).

`init` warns when the library's `index.html` template does not link `tokens.css` or `theme.css`; `sync` and `migrate` do not check the links
in the prototype's `index.html`. Always take colors, spacing, fonts and radii
from tokens (`var(--primary)`, `var(--radius-md)`), do not hard-code values: then the prototype changes together with the project's theme,
also in dark mode. The vocabulary's own classes already have rules in `components.css`; in `project.css` add only what
the vocabulary does not cover (the layout of a specific screen), with a selector narrowed to the screen (`#s3 .my-class`).

The mobile view is an iframe 375 px wide (or less when the window is narrower) with the same stylesheets, so media queries
in `project.css` work as on a phone: an element visible only below 600 px appears in the mobile view and disappears
in desktop. A mock-up with `min-height: 100vh` or `height: 100vh` gets the frame's initial height and does not grow with the window; instead
set the height by content (`min-height` in units relative to the content, not to the window).

## z-index: stacking order

The values come from the `--z-index-*` tokens, not from numbers in CSS. From the bottom:

1. the screen content;
2. comment pins and the thread panel (just below the bar, `sticky - 1`);
3. the prototype bar `.doc-toolbar` (`--z-index-sticky`);
4. the comment popup (`--z-index-overlay`);
5. the engine's dialog windows (the browser's topmost layer, `dialog`);
6. notifications (`--z-index-toast`).

Menus opened from the bar (account, the screen list, „Więcej” (More)) have `--z-index-top` inside the bar's context, so they lie above its
content and above the screens.

The `.frame` creates its own stacking context, so the `z-index` of screen components (`modal`, `drawer`, `toast`, scrims; the tokens `--z-index-modal`, `--z-index-overlay`,
`--z-index-toast` in `components.css`) does not
win over the bar or the pins; do not raise values to "break through" something. The thread panel starts below the bar
(`--proto-toolbar-height`, set by the engine), so it does not cover the account chip. The bar does not use `transform`, `filter`,
`backdrop-filter` or `contain`, so that `position: fixed` elements (the screen list on a phone) are computed relative to the window.
In `project.css` do not set your own `z-index` on elements that can go under the bar.

## Light and dark theme, the dimming lock

The dark theme is chosen by the `data-theme="dark"` attribute on `<html>` (the „Motyw” (Theme) switch in the bar); the attribute is set by the engine after the user's choice and takes precedence. Without a saved choice the page follows `prefers-color-scheme` already in CSS, also without JS and before the engine loads (without a flash of the light theme).
The dark tokens are defined by two blocks with the same values (compared by `check_tokens.py`): `:root[data-theme="dark"]` (from the project also `.dark` and `[data-theme=dark]`) and `@media (prefers-color-scheme: dark) { :root:not([data-theme]) }`; the light ones are defined by the `:root` block. A token
must have a value in the light block; a definition in the dark one alone is not enough.

Extensions that darken pages (e.g. Dark Reader) run before scripts and spoil the mock-up's colors. So the lock must be in the HTML,
in `<head>`:

```html
<meta name="color-scheme" content="light dark">
<meta name="darkreader-lock">
```

The engine removes the leftovers of such extensions only when this pair is in the document. The `index.html` template contains it;
`skillsync prototype migrate` adds it to older prototypes. Do not remove it.

## Project guidelines and required tokens

The project's visual guidelines are a Markdown file pointed to in the manifest (`prototype.guidelines`); the generator does not read it, but the agent must
read it before drawing and stick to the patterns and vocabulary described there. The list of tokens the theme must define
is in `assets/theme/required-tokens.txt` (background, text, card, popover, primary and accent colors, border, field, focus ring,
scrims, `destructive`, `highlight`, `muted`, the statuses `info|success|warning|error|neutral` in the variants `bg`, `border` and `text`
(`success|warning|error` also `icon`), radii, shadows, fonts, the `--z-index-*` layers). `prototype sync` and `init` warn `Missing required tokens` when `tokens.css` and `theme.css` together
do not define any of them. A project that has its own `tokens_from` supplies the missing tokens in the file it points to or in `theme.css`.

## Token and template check: check_tokens.py

`python3 scripts/check_tokens.py [<skill directory>]` (standard library only) checks on the library side:

- every token from `required-tokens.txt` is defined in the light `:root` block of the default theme, and none only in the dark one;
- the list of required tokens equals the set of tokens used (`var(--name)`) by the stylesheets and templates in `assets/repo`
  (local variables and the `--proto-*` ones set by the engine are skipped); `--print-used` prints this set;
- the v2 templates: `comments.json` is an empty log with `{{SLUG}}`, `.gitignore` and `.gitattributes` have the required entries,
  there is no `comments.js`, and `index.html` has the theme lock in `<head>`;
- the comment fold fixtures give the expected results (`--export` limits the v1 check to one export; the v2 fixtures
  are always checked).

Exit codes: 0 consistent, 1 mismatch (messages on stderr), 2 invocation error. Run it after every change to the engine stylesheets,
the default theme or the library templates.

There are two tools and they do not replace each other. `check_tokens.py` is the guard of the skill itself (the default theme and the library templates).
The check of a project's theme is `theme_check.py`, driven by a JSON configuration: token coverage, the dark theme mode, file copies,
contrast and the ban on literals in prototype styles. Description, configuration schema and exit codes: [theme-check.md](theme-check.md).

## Layout probe: layout-probe.js

`scripts/layout-probe.js` is a single declaration `async function layoutProbe(options)`. Inject the content of the file into an open prototype
page (e.g. via `Runtime.evaluate` in Chrome DevTools) and call `layoutProbe({...})`; the result is JSON with measurements
(`viewport`, `toolbar`, `measures`). Options:

- `a1` (on by default): buttons in the mock-up windows (`.modal`, `.drawer`, `.toast`): whether they do not lie under the bar and whether
  they can be hit;
- `a2` (on by default): comment pins inside the `.modal` and `.drawer` mock-ups: whether they can be hit;
- `target: "sN"`: going to a screen and the position of its title under the bar;
- `measureCurrent: true`: the title of the `.screen.is-current` screen;
- `focusRing: true` and `focusMargin` (default 4): the distance of the bar's buttons from its edge (room for the focus outline);
- `compareSingle: true`: the bar height before and after removing from the DOM all screens except the first (together with their links).
  This option breaks the page: run it last and then reload the page.

The probe does not click, but it scrolls the page (`target`, window measurements); the scroll returns to its place only without `target`. Pointer
clicks and the evaluation of the result belong to the agent.
