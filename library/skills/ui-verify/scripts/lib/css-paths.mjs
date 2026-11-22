// Sciezki wzgledne z konfiguracji i flag css-scope: tylko `/` jako separator, na kazdym hoscie. Konfiguracja to dane wspoldzielone
// miedzy maszynami, wiec `\` nie moze byc separatorem na Windows i zwyklym znakiem gdzie indziej. Reszta regul jak resolveInside.
import { PolicyError, resolveInside } from './policy.mjs';

export function resolveSlashOnly(root, rel, options = {}) {
  if (typeof rel === 'string' && rel.includes('\\')) throw new PolicyError('outside-root', 'path outside root');
  return resolveInside(root, rel, options);
}
