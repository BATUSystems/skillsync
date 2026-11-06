# Findings Examples

## Strong Finding

```markdown
High - account settings dialog
Impact: keyboard users cannot close the dialog because the close control is not focusable.
Fix: replace the clickable div with a button and ensure Escape closes the dialog.
Regression check: keyboard-only flow - open dialog, tab to close button, activate with Enter/Space, verify focus returns to trigger.
```

## Weak Finding

```markdown
Medium - dialog
Impact: not very accessible.
Fix: improve accessibility.
Regression check: manual test.
```

Why: strong findings are specific, actionable, and testable.
