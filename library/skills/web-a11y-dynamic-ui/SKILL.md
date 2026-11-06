---
name: web-a11y-dynamic-ui
description: Accessible dynamic UI - filtering, sorting, pagination, progress, live regions, status messages, data tables and client-rendered updates without full page load.
user-invokable: true
---

# Web A11y Dynamic UI

## Rules

1. Client-side updates need non-visual feedback.
2. Use live regions for status updates that should not move focus.
3. Move focus only when it improves task flow and orientation.
4. Use table semantics for tabular relationships, not layout.
5. Communicate sort, current page, result count, and progress state semantically and visually.

## Checklist

```markdown
Dynamic UI checklist

- [ ] Active filters and result counts are discoverable
- [ ] Loading, empty, error, and success states are announced when needed
- [ ] Pagination exposes current page and available movement
- [ ] Progress indicators expose current step/value where meaningful
- [ ] Tables have captions or labels, headers, and sort state
- [ ] Interactive controls inside data views preserve context
```

## Output

Document update announcement strategy, focus strategy, table/result semantics, and regression checks.
