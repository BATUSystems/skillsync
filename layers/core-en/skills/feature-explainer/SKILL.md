---
name: feature-explainer
description: "A page that explains how a screen works to QA and business people: rules confirmed in code, screenshots from sample data, an FAQ. Only on request or with consent."
---

# feature-explainer - how a screen works, explained to someone outside the team

The skill guides the preparation of **one page** about one feature or one screen for a person outside the developer team
(QA, business, support). The page answers the question "why do I see what I see": where the values come from, what the rules
and states are, and what depends on configuration. The rules are confirmed in code, the examples stand on screenshots from
sample data, and the reader's questions get answers.

## When to use it and when not

- Use it on a request to describe how a screen or feature works for someone outside the team ("prepare documentation with
  examples", "someone asks how progress is calculated").
- Use it also when QA remarks arrive for a feature: the page then gets an FAQ section (the analysis of defects is done by
  someone else; the section takes only its result).
- **The skill does not start on its own.** When one of the two situations above appears (a request to describe how a screen
  works for someone outside the team, or QA remarks), propose the skill in one sentence and wait for consent. Without consent
  create nothing.
- Do not use it for API and code documentation for developers, deployment instructions or defect analysis.

## Sources of truth (in order)

1. The project's domain knowledge record about the feature (rules, decisions, configuration).
2. **The code**: every rule is confirmed before it is described, with a `file:line` note ([references/evidence.md](references/evidence.md)).
3. A reference configuration, always marked as an example (identifiers differ in every installation).
4. The reader's report: questions and remarks, the content for the FAQ section.
5. Optionally an independent check by a second agent: facts consistent with the code, missing nuances.

When a document and the code differ: **describe the code** and report the mismatch as a remark for fixing the document.

## Procedure (7 steps)

1. **Scope.** Establish the feature or screen, the reader and their questions. The user's consent to start is a precondition.
2. **Sources.** Read the domain record and the code; keep working notes rule -> `file:line` ([evidence.md](references/evidence.md)).
3. **Verification.** Check every rule in the code, record mismatches, optionally ask a second agent for an independent check.
4. **Screenshots.** Prepare screenshots from sample data: one crop per variant plus one overview ([screenshots.md](references/screenshots.md)).
5. **Page.** Assemble the page from the template `assets/template.html` following [page-structure.md](references/page-structure.md): 9 sections, "why" captions, date and version in the header.
6. **Check.** Run `python3 scripts/check_page.py <index.html>` (path relative to the skill directory); it is a mechanical subset of the criteria. Check the rest (rules against the code, no people's data on screenshots, both themes, width 390 px, tables) by hand using the list below.
7. **Publishing.** By default a file in the repo ([publish-static.md](references/publish-static.md)); a private page in the host only on request or with consent ([publish-artifact.md](references/publish-artifact.md)).

## Hard rules

- **Only sample data** on screenshots (mock-up, fixture, test environment), never personal data. The page header says that the
  screenshots come from sample data. Do not publish the reader's screenshots that show people's data.
- **A rule without confirmation in the code does not go on the page.** Each one has a `file:line` note.
- **Document-code mismatch:** describe the code, report the mismatch.
- **Every example has a "why" caption** (the rule and the numbers), e.g. "67%: 2 of 3 required steps done".
- **Images as files next to the page**, not `data:` in HTML.
- **No promises of fixes** that nobody approved (the FAQ gives a state: rule, configuration, to analyse or defect).
- **Publishing:** by default a file in the repo `docs/explainers/<slug>/` (`index.html` with images next to it, relative paths),
  in every host. A private page in the host (see references/publish-artifact.md) is an option on request or on a proposal, when the reader has no
  access to the repo. Without the user's decision do not publish it.
- **Building and screenshots do not change the working tree of other sessions** (build into a temporary directory, a copy of the script).

## Page acceptance criteria

- Every rule is confirmed in the code; mismatches are reported.
- Screenshots only from sample data, every example has a "why" caption.
- All the reader's questions are in the FAQ with a state (rule, configuration, to analyse, defect).
- Nine sections, a table of contents with anchors, date and version in the header.
- Light and dark theme, width 390 px without horizontal scrolling of the page, tables in scrolling containers.
- No `data:` in images, no external scripts or fonts, every image has an `alt` and lies next to the page.

## Updating

After the rules change (e.g. after a defect fix) update the rules and FAQ sections and the date and version in the header,
then repeat the check and the publishing. Ageing of the page is watched by a human: nothing refreshes it on its own.

## References

- [references/page-structure.md](references/page-structure.md): the template and the language of the page.
- [references/evidence.md](references/evidence.md): notes, mismatches, verification, FAQ states.
- [references/screenshots.md](references/screenshots.md): screenshots.
- [references/publish-static.md](references/publish-static.md), [references/publish-artifact.md](references/publish-artifact.md): the two publishing routes.
- `assets/template.html`: a self-contained page with a synthetic example.
