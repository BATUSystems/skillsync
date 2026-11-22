// Werdykty i kody wyjscia ui-verify: laczenie werdyktow, mapowanie na exit code, straznicy procesu.

export const EXIT = Object.freeze({ PASS: 0, FAIL: 1, ERROR: 2, NOT_RUN: 3 });

// FAIL wygrywa; PASS tylko gdy lista niepusta i wszystkie PASS; reszta to UNVERIFIED.
export function combine(verdicts) {
  const list = Array.isArray(verdicts) ? verdicts : [];
  if (list.includes('FAIL')) return 'FAIL';
  if (list.length > 0 && list.every((v) => v === 'PASS')) return 'PASS';
  return 'UNVERIFIED';
}

// Brak dowodu (UNVERIFIED, NOT_RUN) nigdy nie daje 0; nieznany werdykt to blad (2).
export function exitFor(verdict) {
  switch (verdict) {
    case 'PASS': return EXIT.PASS;
    case 'FAIL': return EXIT.FAIL;
    case 'UNVERIFIED':
    case 'NOT_RUN': return EXIT.NOT_RUN;
    default: return EXIT.ERROR;
  }
}

// Nieobsluzony wyjatek/odrzucenie konczy proces kodem 2 (nigdy 1, bo 1 = FAIL werdyktu).
export function installGuards(proc, hooks = {}) {
  const handler = (err) => {
    try {
      if (hooks && typeof hooks.onError === 'function') hooks.onError(err);
    } finally {
      proc.exit(EXIT.ERROR);
    }
  };
  proc.on('uncaughtException', handler);
  proc.on('unhandledRejection', handler);
  return () => {
    const off = typeof proc.off === 'function' ? 'off' : 'removeListener';
    proc[off]('uncaughtException', handler);
    proc[off]('unhandledRejection', handler);
  };
}
