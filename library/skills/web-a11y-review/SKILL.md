---
name: web-a11y-review
description: Review frontend changes for accessibility regressions - semantics, keyboard and focus, forms, dynamic updates, styling, ARIA, tests. For PRs, diffs and pre-merge sign-off.
user-invokable: true
---

# Web A11y Review

## Review Checklist

For strong finding examples, see [FINDINGS.md](FINDINGS.md).

```markdown
Review checklist

- [ ] Semantic structure, landmarks, and headings support orientation
- [ ] Links, buttons, and custom controls expose correct role/name/state
- [ ] Images, SVG, media, downloads, and external/context-changing links are named and communicated correctly
- [ ] Keyboard operation is complete with visible focus
- [ ] Forms have labels, descriptions, grouping, errors, and recovery paths
- [ ] Dynamic updates are announced or focus-managed appropriately
- [ ] CSS preserves contrast, focus visibility, zoom, reduced motion, and semantics
- [ ] Tests include automation plus relevant manual smoke checks
```

## Findings Format

Report findings first, ordered by severity:

```markdown
<Severity> - <file/component>
Impact: <who is blocked or confused>
Fix: <smallest practical change>
Regression check: <test or manual step>
```

## Review Rules

- Prioritize behavioral blockers over stylistic preferences.
- Treat click-only behavior, focus traps, missing labels, and unsynchronized ARIA state as high risk.
- If no issues are found, state that clearly and list residual testing gaps.
