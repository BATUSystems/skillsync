# Code review summary template

File: `.skillsync/reviews/<YYYY-MM-DD>-<PR-id|branch>.md`. The content below is ready to paste into Azure DevOps
(Markdown). Skip empty sections (except "Verification": `NOT_RUN` is also a result and stays). Do not paste secrets, personal data or raw logs.

```markdown
## Code review — PR #<id>: <title>  (or: changes before PR — <branch>)
**Reviewed state:** <source commit> → <target>, <N> files, +<a>/−<d>  ·  **Tasks:** <Jira KEYS with titles>

### Recommendation: APPROVE | APPROVE WITH COMMENTS | DO NOT APPROVE
<1–3 sentences of justification tied to the task scope>

### Task scope vs. the change
| Requirement / criterion | Status | Notes |
|---|---|---|
| <from Jira / plan> | ✅ met / ⚠️ partially / ❌ missing / ❓ cannot be assessed | <file:line or comment> |

Outside the scope (if any): <changes unrelated to the task>

### To fix before merge (blocking)
1. **`file:line`** — <problem> → <effect/scenario> → <proposed fix>

### Worth fixing
1. **`file:line`** — <finding> → <proposal>

### Minor / nit
- `file:line` — <finding>

### Questions to the author
- <a question arising from an ambiguity, not from the reviewer's lack of effort>

### What is good
- <concretely, only if true>

### Verification
| Command / check | cwd | Status | Exit | Evidence | New / pre-existing |
|---|---|---|---|---|---|
| <e.g. `skillsync validate . --profile final`> | <path> | PASS / FAIL / BLOCKED / NOT_RUN | <code> | <log/path> | <for FAIL> |

- Analyzed: <diff, consumers, project instructions>. PR CI: <status + source; this is not evidence of correctness>.
- Limitations: <what was not checked: no access to Jira, no environment, UI not run, etc.>
```

Content rules: every finding has a location, an effect and a proposal; mark findings about pre-existing code "(existing, outside the scope)".
In the "before PR" mode the heading reads "PR readiness" and the recommendation: ready for PR / after minor fixes / back to fixes;
add a proposed PR description (3–5 sentences: what, why, how tested) only when the user asks for it.
