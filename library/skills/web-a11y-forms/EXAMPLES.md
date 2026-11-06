# Forms Examples

## Labeling

Good:

```html
<label for="email">Email address</label>
<input id="email" name="email" type="email" aria-describedby="email-help" />
<p id="email-help">Use your work email.</p>
```

Bad:

```html
<input placeholder="Email address" />
```

## Validation Feedback

Good:

```html
<label for="zip">ZIP code</label>
<input id="zip" name="zip" aria-invalid="true" aria-describedby="zip-error" />
<p id="zip-error">Enter a 5-digit ZIP code.</p>
```

Bad:

```html
<input id="zip" />
<span style="color: red">Invalid</span>
```

## Error Recovery

- Keep the user input after validation fails.
- Move focus to the first error summary item or invalid field.
- Provide clear next steps to fix each error.
