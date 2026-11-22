# Indexed lessons

Levels: product `docs/lessons` in the project (pitfalls of this repo); shared by the organization — Acme: the `Lessons` repo
(Azure `acme/Shop/Lessons`, format in its README; details: skill `acme`), others: the library
`workflow/lessons`; agent environment: skill `agent-runtime-pitfalls`. Classification: `knowledge.md`.
Lessons are read mainly by subagents — the orchestrator gives them the record paths from `skillsync route --files` in the brief.
index.yaml has version: 1 and a lessons: list of path/title/tags/paths. The path is relative
to the index and does not leave the directory. The task router reads the index and then the selected records.
Do not load all lessons and do not write the whole history into _base.md.

A Markdown record has YAML title, tags (a list of topics), paths (optional patterns).
Content (an executable form for agents): Symptom → Cause → Rule → How to detect; evidence (log, test, commit),
affected areas and limits of validity. The repo's first lesson creates `docs/lessons/index.yaml`.
Fix an existing record instead of creating a duplicate. Security rules must also stay
in the instruction of the nearest area. Doctor checks the index and the records.

Example:
```yaml
version: 1
lessons:
  - path: test-resource-isolation.md
    title: Test resource isolation
    tags: [testing, concurrency]
    paths: ['**/conftest.py', '**/migrations/**']
```

Record knowledge resulting from confirmed experience. Do not add guesses or secrets.
This is workflow documentation; updating Codex memory remains solely on an explicit command.

## Owner of the fix: one smallest place

A new lesson or a fix after an agent's error has one target place; every other place contains at most a pointer. A security exception still has one owner, but a shortened rule with a pointer is repeated in the instruction of the area it concerns. When knowledge comes from a dependency (library, tool, external skill), that dependency is its owner. An apparent need for two owners: split the rule into two or show that it is one contract in two places, and add a check guarding consistency.

| Kind of defect | Smallest owner |
|---|---|
| an invariant or a security boundary applying across the whole project | the project instruction file in the library (`instructions/<project>.md`) |
| wrong selection of instructions, lessons or knowledge for a task or files | the `routing` rule in `workflow/defaults.yaml` (or the manifest); the `skillsync route` tool |
| the procedure of a specific branch of a run or a phase | a single reference in `skills/<skill>/references/` |
| a documented technical pitfall | a record in `docs/lessons` (in the project) or `workflow/lessons` (shared by many) |
| a business rule or domain concept | a `docs/domain` record, the glossary |
| a machine-checkable fact about configuration or the repo | a check in `skillsync doctor` or a generated map, not prose |
| a tool makes a wrong write or bypasses a guard | a hook or a permission rule |
| lack of a repeatable scenario | a test or benchmark (`workflow/benchmark.yaml`) |

The table covers the owner types used in the CLI and the retrospective: `instruction`, `skill`, `route`, `test`, `hook` and `docs`.

**The failing case first.** Before you change a rule or a skill after an agent's error, prepare a reproducible case (a test, a `doctor` check or a benchmark scenario) that **fails first** before the change and passes after it. Recognize duplicates by the meaning of the error, not by the wording of the description.
Exception: a quality retrospective proposal with owner `skill`, `instruction`, `docs` or `route`, accepted with “YES P<n>” or “YES <run-id>/P<n>”, does not require such a case ([retro-quality](../../feature/references/retro-quality.md)).
A minor lesson without a reproducible case goes in as a record with evidence, not as a new core rule.
Record the decision on a `skillsync retro` candidate (`approved`/`rejected`, day, `owner`, `case`, `ref`) in the project's `docs/retro/decisions.yaml` with `skillsync retro-decide`;
`retro` then marks it `decided`, and a recurrence after the decision day `recurred`.
