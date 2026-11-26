# Publishing: a static file in the repo (the default route)

The default route in all hosts. The page is a directory with static HTML and images next to it, no server and no build step.

## Layout

    docs/explainers/<slug>/
      index.html
      img/            (optional; images may also lie next to index.html)
        overview.jpg
        card-required-2of3.jpg

- `<slug>`: a short feature name in lowercase with hyphens.
- All image paths **relative** (`img/overview.jpg`), so the directory works after copying and when opened from disk.
- No `data:`, no external scripts or fonts.
- The user may point to another output directory (e.g. outside the repo, when the page should not enter history); then write
  there and say where it lies.

## Steps

1. Copy `assets/template.html` to `docs/explainers/<slug>/index.html` and fill it in following [page-structure.md](page-structure.md).
2. Copy the images into the page directory.
3. Open the page locally (from disk or with a static file server on a free port) and check: both themes, width 390 px
   without horizontal scrolling, all images visible, the table-of-contents anchors work.
   Before publishing run `python3 scripts/check_page.py <index.html>` (path relative to the skill directory): it is a mechanical
   subset; rules against the code, no people's data on screenshots, both themes, 390 px and tables are checked by hand.
4. Save the changes in the repo according to the project's delivery mode (commit and further steps per project rules; the skill does not commit by itself).

## Handing over to the reader

- A reader with repo access: give the path `docs/explainers/<slug>/index.html` and the branch or commit.
- A reader without repo access: send the whole directory (an archive) or propose the private-page route in the host
  ([publish-artifact.md](publish-artifact.md)); the user chooses.
- Say explicitly that the screenshots come from sample data and that the page has a date and a version.

## Updating

Edit the same `index.html`, replace images under the same names, change the date and version in the header, repeat the check.
