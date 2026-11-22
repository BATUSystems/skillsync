# Documentation and domain knowledge

Every implementation task leaves the project better documented than it found it.
This is part of the definition of `done`, not an add-on after completion.

## What is what

| Kind | Place | Content |
|---|---|---|
| Project documentation | README, `docs/`, module instructions, contract docstrings | How the system works now: startup, API, configuration, decisions |
| Domain knowledge | one shared `docs/domain/` of the project + `index.yaml` | Business concepts, rules, exceptions, glossary, sources of truth |
| Lessons | `docs/lessons/` (project), `workflow/lessons/` (library) | Confirmed technical failures and the rules that rule them out |
| Knowledge shared across projects | a skill in the library (e.g. `globex`, `acme`) | The client's/organization's domain extending beyond a single repo |
| Organization-wide lessons | Acme: the `Lessons` repo (skill `acme`); others: `workflow/lessons/` in the library | Portable technical pitfalls of the stack (upgrade, CI, libraries), without project details |

## Classification of discoveries (each discovery goes to exactly one place)

| Discovery | Place |
|---|---|
| Business rule, concept, exception, source of truth | `docs/domain/` |
| Technical pitfall of this repo (build, tests, migrations, configuration, library behavior in this code) | `docs/lessons/` |
| Pitfall portable to other projects of this stack | Acme → the `Lessons` repo; other organizations → the library's `workflow/lessons/` |
| Agent environment pitfall (shell, session tools, processes) | skill `agent-runtime-pitfalls` |
| How the system works now | documentation of the area (`README`, `docs/`) |

Lessons are written **for subagents and future sessions**: briefly, in an executable form — `Symptom` → `Cause` →
`Rule` (what to do / what not to do) → `How to detect` (command, test, grep), with `paths` pointing to the code area,
so that `skillsync route --files` selects the record for a task. Before the first knowledge write the orchestrator runs
`skillsync knowledge init <project>` (creates the missing `docs/domain/index.yaml`, `docs/lessons/index.yaml`,
`docs/README.md`; never overwrites), and only then assigns the write to `scribe`. A missing directory is not a reason
to write a pitfall into `docs/domain` or to skip it.

Domain knowledge is everything that cannot be read from the code alone and that changes the meaning
of a change: why a rule exists, who owns it, what the exceptions and examples are,
what a concept means in the client's language, which data is the source of truth. It is discovered in tickets,
user answers, data, system behavior and code consumers.

## One shared place

Domain knowledge does not differ between backend and frontend — a project has **one** location:

- one repo (also a backend+frontend monorepo): `docs/domain/` in the project directory;
- a directory with separate backend/frontend repos (e.g. DEMO, Northwind — manifest in the parent directory):
  `docs/domain/` in that directory, not in either repo;
- separate skillsync projects for backend and frontend (e.g. Shop: `shop-web` + `shop-api`):
  a shared parent directory, in both manifests
  `workflow.knowledge.domain: ../docs/domain`.

`paths` in records and in the index are relative to the directory containing `docs/`
(e.g. `demo-shop/Domain/**`, `shop-web/src/app/**`), so one record covers
both repos. A product made of several repositories, where one of them holds the knowledge (repo A), points to its directory with a list:
`workflow.knowledge.domain: [docs/domain, ../repo-a/docs/domain]`; a record in repo A writes `paths` in its own
coordinates: `src/**` (A's code) and `repo-b/src/**` (B's code, the clone's directory name). The clone directory names must be identical
on every machine and in CI, otherwise a record with `repo-b/...` will not match files. Do not assume `docs/domain` in sub-repositories — `doctor` reports such indexes as
scattered knowledge. The parent directory may not be in git; then records have no history
and no synchronization between machines.

## Domain record

`docs/domain/<topic>.md` with YAML frontmatter:

```markdown
---
title: Numbering of correcting invoices
tags: [billing, invoices]
paths: ['billing/**', 'invoices/**']
source: DEMO-1234, user answer 2026-10-02
verified: 2026-10-02
---
Rule: ...
Why: ...
Exceptions and examples: ...
Where in code: billing/numbering.py:42
```

`docs/domain/index.yaml`:

```yaml
version: 1
entries:
  - path: billing-numbering.md
    title: Numbering of correcting invoices
    tags: [billing, invoices]
    paths: ['billing/**']
```

`skillsync route` returns the matching records from the shared location in the `domain` field (by tags/paths;
`--topics domain` returns all of them). A record with `load: bootstrap` in the index is always returned,
even without topics and paths. A missing `load` keeps the existing matching; the only allowed
explicit value is `bootstrap` (null and an empty value are errors). `skillsync doctor` checks the frontmatter and `load` and warns
when a project with `feature` has no index yet — create it with the first record.

## Domain glossary on input

Before interpreting a ticket and choosing files, run `skillsync route <project>` and read the returned
`domain` records with `load: bootstrap`. The CLI selects the paths; reading the content is done by the agent.
Without the CLI, read `docs/domain/index.yaml` from the project's shared location and the bootstrap records
listed there. Do not assume that a project's slang has an everyday meaning or one known from another project.

The project glossary is `docs/domain/glossary.md`, with the frontmatter of an ordinary domain record.
Index it like this:

```yaml
  - path: glossary.md
    title: Project domain glossary
    tags: [terminology]
    load: bootstrap
```

One heading per term: **meaning in the project**, **does not mean** (when there is a risk of confusion),
**forms and synonyms**, **names in code** (if confirmed), **source and date of confirmation**,
optionally a pointer to the detailed rules. The source may be an explicit user definition,
a ticket, code or data; a class name alone does not confirm a business meaning or the equivalence of synonyms.

Record concepts that change the interpretation of a requirement, differ from the everyday meaning
and may come back in later tasks. Skip obvious technical terms, one-off abbreviations
and duplicates. A candidate from a conversation or analysis goes to the plan's “Domain terminology” section
with a hypothesis of the meaning and a question/source to check. It goes into the glossary only after
confirmation; do not require consent again when the user has already given the definition explicitly.
Conflicting definitions require clarification, not a silent overwrite.

Aim for at most 8 KB UTF-8; when approaching 12 KB move details to topical
domain records, leaving short definitions and pointers. 12 KB is a limit of the procedure,
not an automatic CLI check. Do not load the whole history of rules into bootstrap and do not append
the glossary to the instruction core. No new terms is a valid outcome of a task.

Take a YAML value with `: ` (colon and space) in quotes (`title: 'File policy: limits'`); tags are
strings (`'409'`, not `409`). Without that the record is unreadable to the router and `doctor` reports an error
(records without a header or with an unquoted colon). In a worktree without
`.github/.skillsync.yaml` (an ignored directory) `doctor` returns `BLOCKED: Missing manifest` — this is not an error;
check the headers there with a YAML parser, and run the full `doctor` in the repo's root directory.

## Writing rules

- Record confirmed facts (code, data, ticket, user decision) with the source indicated.
  Mark a hypothesis as a hypothesis or do not record it. No secrets and no personal data.
- First update an existing record/document; a duplicate instead of a correction is an error.
  Remove or correct content that the change made outdated.
- One topic per record, briefly: rule, rationale, exceptions, example, place in the code.
- Domain shared by several repos goes to a skill in the library (`push`/`link`), not to a copy
  in every project. Security rules also stay in the instruction of the nearest area.
- The instruction core (`_base.md`, `CLAUDE.md`, `AGENTS.md`) is not a knowledge log —
  at most a pointer goes there.

## In the task cycle

1. Plan: the “Documentation and knowledge” section — which documents the change makes outdated, what
   knowledge we expect, who will record it. “Not applicable” only with a justification.
   The “Domain terminology” section separates confirmed terms to add/update,
   candidates to clarify and a justified “no new terms”.
2. A brief for a subagent contains the paths of the matching lessons and domain records (`skillsync route <project> --files
   <step files>` → fields lessons/domain); the subagent reads only those. The subagent reports “Discoveries” (domain,
   outdated documentation, pitfalls) with the source.
3. Before review the orchestrator collects the discoveries, classifies them by the table above and assigns the write (by default `scribe`).
   `scribe` updates the glossary only for terms confirmed with a source; candidates
   stay in the plan. Knowledge confirmed during planning may be persisted already then.
   A pitfall discovery without a lesson requires a justification in the plan; a write to the `Lessons` repo (Acme) — the file locally,
   commit/push only on the user's command (the repo is shared with the team).
4. The reviewer checks documentation and knowledge like code, including the sources of definitions, deduplication,
   separating candidates from facts, the bootstrap index and the glossary limit;
   a missing required write without a justification = `CHANGES_REQUIRED`.
5. The “Result” in the plan has a “Knowledge gained” subsection with links to the written files.
