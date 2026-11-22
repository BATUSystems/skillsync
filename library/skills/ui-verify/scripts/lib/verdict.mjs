// Werdykty i kody wyjscia ui-verify: laczenie werdyktow, mapowanie na exit code, straznicy procesu.
import nodeFs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { guardMask } from './out.mjs';

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

// Czy modul jest uruchomiony jako skrypt glowny. Porownuje realpath obu stron (wywolanie przez symlink nie moze dac
// cichego kodu 0 bez wyniku); blad realpath => porownanie leksykalne.
export function isMain(importMetaUrl, argv1, fs = nodeFs) {
  if (typeof argv1 !== 'string' || argv1 === '') return false;
  const self = fileURLToPath(importMetaUrl);
  const norm = (s) => (process.platform === 'win32' ? s.toLowerCase() : s);
  try {
    return norm(fs.realpathSync(self)) === norm(fs.realpathSync(argv1));
  } catch {
    return norm(path.resolve(self)) === norm(path.resolve(argv1));
  }
}

// Nieobsluzony wyjatek/odrzucenie konczy proces kodem 2 (nigdy 1, bo 1 = FAIL werdyktu); komunikat idzie na stderr
// przez maskowanie (domyslnie guardMask: sekrety z UI_VERIFY_SECRET_ENV i maskera ustawionego przez skrypt).
export function installGuards(proc, hooks = {}) {
  const mask = typeof hooks.mask === 'function' ? hooks.mask : guardMask;
  const handler = (err) => {
    try {
      if (hooks && typeof hooks.onError === 'function') hooks.onError(err);
      if (proc.stderr && typeof proc.stderr.write === 'function') {
        const msg = err instanceof Error ? err.message : String(err);
        proc.stderr.write(mask(`ui-verify: ${msg}\n`));
      }
    } catch { /* zgloszenie bledu nie moze zmienic kodu wyjscia */ } finally {
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
