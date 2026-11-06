---
name: web-a11y-css
description: Accessible CSS - contrast, focus styles, reduced motion, forced colors, zoom, target size and custom-styled controls. Use for UI styling and design-system changes.
user-invokable: true
---

# Web A11y CSS

## CSS Rules

1. Do not remove focus indicators without a visible replacement.
2. Do not rely on color alone for meaning.
3. Respect reduced motion and provide user control for moving content.
4. Preserve usability under zoom, reflow, large text, and forced-colors modes.
5. Avoid CSS that breaks semantics or operability.

## Risky CSS Checklist

```markdown
CSS accessibility checklist

- [ ] Focus styles are visible and high contrast
- [ ] Text, controls, and states meet contrast expectations
- [ ] Target sizes and spacing support touch and motor constraints
- [ ] Motion is reduced or disabled when users request it
- [ ] `display`, `visibility`, `content-visibility`, and `pointer-events` do not break semantics
- [ ] Hidden labels, legends, captions, and referenced text still name controls correctly
```

## Output

List preference modes tested, risky CSS properties used, and any semantic integrity mitigations.
