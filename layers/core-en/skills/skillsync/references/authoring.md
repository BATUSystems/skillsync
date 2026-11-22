# Writing skill content

Registering and linking a skill is described in [SKILL.md](../SKILL.md) of the `skillsync` skill. This file covers what to write in the content and how.

## 1. Starting point: real requests

Before you start, collect 2–3 concrete ways in which a user or another skill will ask for the task. For each one, work out how the task would go without the skill and what would have to be created from scratch every time (script, schema, template). These are the candidates for resources, and the requests supply the phrases for the description. For an obvious use the step can be short, but do not skip it for ambiguous inputs.

## 2. Only knowledge the agent does not have

The context window is shared by the description catalog, the conversation history and the task. The model is sharp, so the skill provides procedure steps, facts about the specific repo, constraints and pitfalls. A paragraph stays only if it pays for its tokens on every use; a concise example often replaces an explanation.

## 3. Freedom matched to fragility

| Level | Form | When |
|---|---|---|
| high | prose and heuristics | several correct paths, depending on context |
| medium | pseudocode or a parameterized script | a preferred pattern, deviations acceptable |
| low | an exact script or command sequence | the operation breaks easily, consistency is critical or the order is mandatory |

Fixed rule: gates, markers and anything that another skill or tool parses always get low freedom.

## 4. Resources

- `references/` — text read on demand: procedures, templates, schemas.
- `scripts/` — deterministic code; run each one before handing it over (for many similar ones, check a sample).
- `assets/` — material copied or used in the product of the work, which the agent does not read.

Remove empty or redundant directories. A path named in the skill without an existing file is an error.

## 5. References and one place per fact

Every fact has one place: in the skill content or in a reference, never in both. The nesting depth is one: a reference is pointed to directly from `SKILL.md`, with no reference → reference chains. For many variants (providers, trackers) you choose in the content and keep the details in separate files. A file longer than about 100 lines gets a table of contents.

## 6. Output templates

Output that is parsed further has a strict template (string lines, markers, machine fields). A report read by a human has a flexible default template with an explicit scope of adaptation. When style is the point, show an example “this input → this result”. Precede a multi-stage procedure with a numbered list of all the stages.

## 7. The description — the only basis for choosing a skill

- Limit ≤ 200 B (UTF-8); exceeding it is reported by `doctor`. Every byte of the description costs in every conversation.
- One line: what the skill does and delivers, when to use it, phrases in the user's wording, and for a similar neighboring skill, how it differs. A skill invoked in both Polish and English gets phrases in both languages.
- The description does not explain how it works; that belongs in the content.
- Cold-read test: reading only the description, check 3 matching and 3 non-matching requests (tasks of neighboring skills). The skill must be chosen only for the matching ones; fix until it holds.
- Frontmatter: `name` = folder name, lowercase with hyphens. `description` must parse as YAML; text containing “: ” goes in quotes, otherwise `doctor` reports “Invalid skill frontmatter”.

## 8. What not to add to the skill directory

A README, installation instructions, a cheat sheet, a changelog, notes on how the skill came to be. Collective documentation lives outside `skills/`, and extra files in a skill only go stale.

## 9. Iteration after use

The first version is a hypothesis. Add a missing safeguard to the content. Replace reasoning that is reproduced many times with a script or a reference, and remove an unused paragraph. After every change run `skillsync skills index --check` and `doctor`.
