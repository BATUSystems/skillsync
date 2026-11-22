// Sciezki cache zestawu zaleznosci: korzen, hash, katalog per hash, nadpisanie Playwrighta.
import crypto from 'node:crypto';
import path from 'node:path';

function nonEmpty(v) {
  return typeof v === 'string' && v !== '' ? v : null;
}

export function defaultCacheRoot({ platform, env = {}, homedir }) {
  const override = nonEmpty(env.UI_VERIFY_CACHE_DIR);
  if (override) return override;
  if (platform === 'win32') {
    const base = nonEmpty(env.LOCALAPPDATA) ?? path.win32.join(homedir, 'AppData', 'Local');
    return path.win32.join(base, 'skillsync', 'cache', 'ui-verify');
  }
  if (platform === 'darwin') {
    return path.posix.join(homedir, 'Library', 'Caches', 'skillsync', 'ui-verify');
  }
  const base = nonEmpty(env.XDG_CACHE_HOME) ?? path.posix.join(homedir, '.cache');
  return path.posix.join(base, 'skillsync', 'ui-verify');
}

export function computeHash(files) {
  const h = crypto.createHash('sha256');
  files.forEach((f, i) => {
    if (i > 0) h.update('\0');
    h.update(f);
  });
  return h.digest('hex').slice(0, 16);
}

export function cacheDirFor(root, hash, platform) {
  const sep = platform === 'win32' ? '\\' : '/';
  return `${root}${sep}${hash}`;
}

export function playwrightOverride(env = {}) {
  return nonEmpty(env.PLAYWRIGHT_MODULE_PATH);
}
