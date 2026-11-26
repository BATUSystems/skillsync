# Evidence: from code to page

## Working notes

Before writing, keep a table (a working file outside the page, e.g. in a temporary directory):

| Rule (in the reader's words) | File:line | Confirmed | Notes |
|---|---|---|---|
| "Progress counts only required steps" | `path/to/file:42` | yes | optional steps skipped |

Principles:

- A rule goes on the page only after it is confirmed in the code (read the execution path, not just the function name).
- Record the line where the rule actually takes effect (the condition, the formula), not only the file.
- Confirm rules from configuration both in the code that reads it and in the example configuration.
- A skipped edge case (no data, an empty list, division by zero) also gets a note.

## Document-code mismatch

When a knowledge record or older documentation says something different than the code:

1. Describe on the page what the **code** does.
2. Report the mismatch separately (to the user, in the report): what the document says, what the code does, where (`file:line`).
3. Do not fix the document silently as part of this page; fixing the document is a separate decision.

## Independent verification

Optionally a second agent gets the page and access to the code, but not your notes. Ask it about: consistency of the facts with
the code, missing nuances, places a layperson would not understand. Add only what it contributes and mark the changes in the report.

## FAQ answer states

| State | Meaning | What to add |
|---|---|---|
| Rule | the behaviour matches the rules | which rule and the numbers of the example |
| Configuration | depends on the installation's settings | where it is set, which value gives the effect |
| To analyse | there is no explanation yet | what is needed from the reader to confirm (a screenshot from sample data, a reproduction step) |
| Defect | the behaviour contradicts the rules | a description of the discrepancy without a promise of a fix date |

The state "Defect" or "To analyse" is not a commitment: the page gives no dates and no decisions that nobody approved.
After a defect is fixed, change the state, update the rule and the date.
