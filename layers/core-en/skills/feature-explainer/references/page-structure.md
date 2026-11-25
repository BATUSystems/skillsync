# Page structure

The page has nine sections in this order. The ready skeleton: `assets/template.html` (a synthetic example to replace).

| # | Section | Content |
|---|---|---|
| 1 | Header | the feature name, a one-sentence purpose, the state as of a date and a branch or version, a note about sample data, a table of contents with anchors |
| 2 | Overview | an overview screenshot with a description of the elements; who sees the screen and what lands on the list |
| 3 | Anatomy | a crop of one element and a numbered list of its parts (what it is, where it comes from); separate concepts that are easy to confuse |
| 4 | Rules | "condition -> result" tables in the order of checking, formulas with a numeric example, exceptions and precedence |
| 5 | Actions | buttons and transitions: the selection rule |
| 6 | Example gallery | a grid of 3, 2 and 1 columns; each case with an explanation |
| 7 | Configuration | where it is set, an example marked as an example, the effect of missing configuration |
| 8 | Questions / FAQ | every reader question: a cause consistent with the rules, a state, what is needed to confirm |
| 9 | Footer | the source of the rules (document and code file), a contact |

## Content principles

- **The reader's language.** Interface labels in quotation marks, short sentences, no implementation jargon in the main text.
  Class and file names only in `code` and only where they help report defects.
- **Easily confused concepts** are separated explicitly (e.g. a raw status versus a simplified stage from configuration).
- **Order in rule tables:** number it only when it matters (the first matching rule wins).
- **Formulas** in a separate block, always with a numeric example.
- **A "why" caption** under every example: the rule and the numbers. A description of the look alone is not enough.
- **The header** always contains the date, the version or branch, and a sentence that screenshots come from sample data.
- **Configuration** always with a warning that the values are examples and that identifiers differ in every installation.
- **FAQ** without promises of fixes; states according to [evidence.md](evidence.md).

## Form

- One `index.html` file, CSS tokens for the light and dark theme (switched by `prefers-color-scheme`).
- Tables in containers with horizontal scrolling; the page at phone width does not scroll horizontally.
- No external scripts or fonts. Images as files next to the page, relative paths, each with an `alt`.
- You may shorten the template (a section without content gets one sentence on why it is absent), but do not change the order.
