# Contract of the mock-up page

Template: [../assets/template.html](../assets/template.html). The file is the content of the Artifact (without `<html>`,
`<head>`, `<body>`); `<title>` and `<style>` at the top.

## Screen

```html
<section class="screen" id="s3" aria-labelledby="s3-h">
  <header class="screen-meta">
    <h2 id="s3-h">3. Screen name</h2>
    <p>One sentence: the user's task.</p>
    <ul class="screen-refs"><li class="ref">US-2</li><li class="ref">§4</li></ul>
  </header>
  <div class="frame"> …mock-up… </div>
  <ul class="notes"><li>Behaviour that a static screen will not show.</li></ul>
</section>
```

- `id="sN"` is not changed after the review has started (the `#sN` addresses and the threads depend on it).
- `.notes`: undo, optimistic update, recalculation, locks, permissions.
- Navigation: the template has no script, and `data-goto="s4"` gives only a pointer cursor in it. The jump to a screen
  is provided by a link `<a href="#s4">` or the page's own script; such a script must give the element with `data-goto` the role of a button,
  handle Enter/Space and scroll to `#s4`.
- `data-comment-target` on an element makes a comment clicked inside it in the shell's comment mode
  attach to it (cards, rows, fields); `data-uncommentable` switches commenting off for the subtree. Both attributes
  are handled by the claude.ai shell.
- All screens are visible one under another (threads always have an anchor).

## Comments

Declaration: `capabilities: {comments: {"composer_only": true}}`; without consent, the page does not write
the content, it only opens the shell's composer for the clicked element (`openComposer({element})`),
from a user gesture. The threads are displayed and refreshed by the claude.ai shell; do not build your own list.
When `claude.use("comments")` returns `null`, the page hides its own comment button, and commenting happens
in the shell's comment mode (if the viewer has it). `openComposer` returns `{opened: false}` without an error, e.g. inside
`data-uncommentable`; do not repeat the call in a loop. Comment contents are final and cannot be edited.

## Style

Tokens in `:root` (light) and two dark blocks according to the theme contract. Mapping a project =
overriding the tokens (`--bg`, `--fg`, `--accent`, `--font-ui`, `--radius`…), not rewriting the CSS.
Status colours: the tokens `--ok`, `--warn`, `--err`; do not use hard-coded colours.
