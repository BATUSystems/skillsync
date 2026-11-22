# Prototype icons

Icons drawn from scratch for this skill, not modelled on any external set; no external licence.

The file `sprite.svg` contains one hidden `<svg>` with the `ic-*` symbols. Preview of all icons: `preview.html`.

## Icon list

The names are a contract (`docs/prototype-contract/slownik-html-css.md`, part B, table "Icon symbols"). Groups: basic (18),
screen patterns (8, contractual, from `references/screen-patterns.md`) and additional (outside the contract). The repo template `index.html`
contains the whole `sprite.svg`, and `skillsync prototype migrate` adds to a prototype the symbols added in later versions of the library.
In `preview.html` an asterisk (*) marks screen patterns, a plus (+) marks additional icons.

| id | Meaning | Group |
|---|---|---|
| `ic-bell` | notifications | basic (18, template) |
| `ic-check` | approval | basic (18, template) |
| `ic-chevron-down` | expand | basic (18, template) |
| `ic-chevron-left` | back, previous page | basic (18, template) |
| `ic-chevron-right` | forward | basic (18, template) |
| `ic-filter` | filter | basic (18, template) |
| `ic-home` | home page | basic (18, template) |
| `ic-info` | information | basic (18, template) |
| `ic-layout` | layout, drawer, side panel | basic (18, template) |
| `ic-more` | more actions | basic (18, template) |
| `ic-pencil` | edit | basic (18, template) |
| `ic-plus` | add | basic (18, template) |
| `ic-search` | search | basic (18, template) |
| `ic-settings` | settings | basic (18, template) |
| `ic-trash` | delete | basic (18, template) |
| `ic-users` | team, users | basic (18, template) |
| `ic-warn` | warning | basic (18, template) |
| `ic-x` | close | basic (18, template) |
| `ic-ban` | no access, prohibition | screen patterns |
| `ic-building` | building, organisation | screen patterns |
| `ic-calendar` | calendar, deadline | screen patterns |
| `ic-calendar-off` | day off | screen patterns |
| `ic-check-circle` | success | screen patterns |
| `ic-door` | room, premises | screen patterns |
| `ic-list` | list | screen patterns |
| `ic-lock` | lock, no permissions | screen patterns |
| `ic-user` | User, account | additional |
| `ic-menu` | Menu (three bars) | additional |
| `ic-error` | Error | additional |
| `ic-clock` | Time, history | additional |
| `ic-minus` | Subtract, collapse | additional |
| `ic-chevron-up` | Collapse upwards | additional |
| `ic-arrow-left` | Left arrow | additional |
| `ic-arrow-right` | Right arrow | additional |
| `ic-inbox` | Empty state, inbox | additional |
| `ic-refresh` | Refresh, try again | additional |
| `ic-table` | Table view | additional |
| `ic-kanban` | Kanban board view | additional |
| `ic-download` | Download | additional |
| `ic-eye` | Preview | additional |
| `ic-comment` | Comment | additional |
| `ic-sort` | Sorting | additional |
| `ic-sun` | Light theme | additional |
| `ic-moon` | Dark theme | additional |

## Construction rules

- 24×24 grid, `viewBox="0 0 24 24"`, content fits within the 3–21 area (margin of 3 units).
- Attributes on every `<symbol>`: `fill="none"`, `stroke="currentColor"`, `stroke-width="2"`, `stroke-linecap="round"`, `stroke-linejoin="round"`. The icon colour is the text colour (`color`) of the element.
- Primitives only: `line`, `polyline`, `circle`, `rect`, `path` (straight segments and arcs). No fills, gradients, text or images.
- Dots are zero-length segments with a round cap (or small circles), not filled shapes.
- One line weight across all icons; an icon has a single motif, with no details finer than 2 units.
- Names: `ic-<name>` in lower case, hyphen as the separator; identifiers unique within the sprite.

## Inserting into templates

1. Paste the sprite once, right after `<body>`: `<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">…</svg>`; on a single-file page (Artifact) copy from `sprite.svg` only the symbols the page uses; the repo template `index.html` already has the whole sprite.
2. Usage: `<svg class="ico" width="16" height="16" aria-hidden="true"><use href="#ic-search"/></svg>`.
3. Set size and colour in CSS (`.ico{width:1em;height:1em;color:inherit}`); the icon takes over `currentColor`.
4. A button with only an icon gets `aria-label` or `title`; a decorative icon gets `aria-hidden="true"`.
5. Every `<use href="#ic-…">` must have a matching symbol in the sprite of the same page.
