// Login helper of the accessibility audit: replays the configured fill and click steps in a page.
// Fill values come only from environment variables (valueEnv); a failure names the step, never the value or the page text.

export class A11yLoginError extends Error {
  constructor(message) {
    super(message);
    this.name = 'A11yLoginError';
  }
}

// login: { steps: [{ fill: { selector, valueEnv } } | { click: selector }] } (validated by a11y-config).
// The caller has already navigated the page to the login URL and checked its origin.
export async function runLoginSteps({ page, login, env = {}, actionTimeoutMs = 10000, loadTimeoutMs = 10000 }) {
  let index = 0;
  for (const step of login.steps) {
    index += 1;
    const kind = step.fill ? 'fill' : 'click';
    try {
      if (step.fill) {
        const value = env[step.fill.valueEnv];
        if (typeof value !== 'string' || value === '') throw new Error('value not set');
        await page.locator(step.fill.selector).first().fill(value, { timeout: actionTimeoutMs });
      } else {
        await page.locator(step.click).first().click({ timeout: actionTimeoutMs });
      }
    } catch {
      // The browser error text can quote the typed value or the page content, so it is dropped here.
      throw new A11yLoginError(`login step ${index} (${kind}) failed`);
    }
  }
  try {
    await page.waitForLoadState('load', { timeout: loadTimeoutMs });
  } catch {
    // The navigation after the last step may already be finished.
  }
}
