# Screenshots

## Data

- **Sample data only**: a mock-up, a fixture, a test environment. Never personal data.
- The page header says the screenshots come from sample data.
- Do not publish the reader's screenshots (chat, lists with surnames, production). Recreate the situation on sample data.
- Before publishing, look at every image: no first names, addresses, numbers, tokens, names of private environments.

## Source of screenshots

1. **Prefer the project's existing, hermetic screenshot script**: a local server with a fixture, Playwright, all variants
   of the component on one page. Do not invent a new one when a working one exists.
2. When the script needs a built application and building in the current directory could disturb another session: build into
   a temporary directory (the build's output directory option) and run a **copy** of the script that points to that directory. Do not edit the original.
3. When there is no script: write a small one-off script in a temporary directory, on a fixture, with no write to the repo (unless the user decides otherwise).

## Crops

- **A separate crop for each variant or state** (one card = one image), plus **one overview screenshot** of the whole screen.
- A crop of an element is a Playwright `locator.screenshot` call in the project's script. The `capture` command of `ui-verify` takes
  screenshots of whole pages and widths, not crops of single elements.
- Before the screenshot wait for a positive assertion (an element that proves the screen has rendered). A screenshot of an empty page is not evidence.

## Format and scale

- JPEG, quality about 80-85%, scale 0.7-0.8; PNG only for images with sharp, flat edges that are to stay small.
- Descriptive and stable file names (`card-required-2of3.jpg`), because the paths go into `index.html`.
- Images lie **next to the page** (the same directory or an `img/` subdirectory), relative paths. Do not put `data:` into HTML.
- Every image has an `alt` (what it shows) and a "why" caption (the rule and the numbers).

## References to other skills (when available)

- When the `ui-verify` skill is available: use it for screenshots of whole pages, for finding the system Chrome and for the
  security rules of the screenshot configuration. `capture` in dark mode and at a width of 390 px (`modes`, `widths`) and
  `a11y` help with the step 6 check.
- When the `prototype` skill is available: take the light and dark theme and the style tokens from its `references/style.md` and `theme-check.md`.
- When they are absent, apply your own rule: you take screenshots with a browser driven by a script on a fixture, and the theme
  is two sets of colour tokens (light and dark) with text contrast of at least 4.5:1.
