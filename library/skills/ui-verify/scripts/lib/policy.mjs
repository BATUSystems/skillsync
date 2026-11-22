// Polityka bezpieczenstwa: sciezki pod root, bezpieczne nazwy, dozwolone originy.
import nodeFs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export class PolicyError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'PolicyError';
    this.code = code;
  }
}

function defaultPlatform() {
  return process.platform === 'win32' ? 'win32' : 'posix';
}

export function resolveInside(root, rel, { platform = defaultPlatform() } = {}) {
  const fail = () => new PolicyError('outside-root', 'path outside root');
  if (typeof rel !== 'string' || rel.includes('\0')) throw fail();
  const win = platform === 'win32';
  const p = win ? path.win32 : path.posix;
  if (win ? /^([a-zA-Z]:|[\\/])/.test(rel) : (rel.startsWith('/') || rel.includes('\\'))) throw fail();
  const base = p.resolve(root);
  const full = p.resolve(base, rel);
  const norm = (s) => (win ? s.toLowerCase() : s);
  const prefix = base.endsWith(p.sep) ? base : base + p.sep;
  if (norm(full) !== norm(base) && !norm(full).startsWith(norm(prefix))) throw fail();
  return full;
}

// Symlinki: realpath najblizszego istniejacego przodka celu musi lezec w realpath(root), a sam istniejacy cel nie moze
// byc symlinkiem (lstat). Sciezka leksykalnie poza root to blad nawet bez symlinkow.
export function assertRealInside(root, absPath, fs = nodeFs) {
  const fail = () => new PolicyError('outside-root', 'path outside root');
  const base = path.resolve(root);
  const target = path.resolve(absPath);
  const norm = (s) => (process.platform === 'win32' ? s.toLowerCase() : s);
  const within = (child, parent) => {
    const prefix = parent.endsWith(path.sep) ? parent : parent + path.sep;
    return norm(child) === norm(parent) || norm(child).startsWith(norm(prefix));
  };
  if (!within(target, base)) throw fail();
  let realBase;
  try {
    realBase = fs.realpathSync(base);
  } catch {
    realBase = base;
  }
  let cur = target;
  let real = null;
  for (;;) {
    try {
      real = fs.realpathSync(cur);
      break;
    } catch (e) {
      const parent = path.dirname(cur);
      if (parent === cur || !e || (e.code !== 'ENOENT' && e.code !== 'ENOTDIR')) throw fail();
      cur = parent;
    }
  }
  if (!within(real, realBase)) throw fail();
  let st = null;
  try {
    st = fs.lstatSync(target);
  } catch (e) {
    if (!e || (e.code !== 'ENOENT' && e.code !== 'ENOTDIR')) throw fail();
  }
  if (st && typeof st.isSymbolicLink === 'function' && st.isSymbolicLink()) throw fail();
  return target;
}

export function safeName(name) {
  if (typeof name !== 'string' || !/^[A-Za-z0-9._-]{1,120}$/.test(name) || name === '.' || name === '..') {
    throw new PolicyError('bad-name', 'bad name');
  }
  return name;
}

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

function matchAllow(url, pattern) {
  const m = /^(?:(https?):\/\/)?([^/:]+)(?::(\d+))?(?:\/.*)?$/i.exec(String(pattern).trim());
  if (!m) return false;
  const [, scheme, host, port] = m;
  if (scheme && url.protocol !== `${scheme.toLowerCase()}:`) return false;
  if (port && url.port !== port) return false;
  const h = host.toLowerCase();
  if (h.startsWith('*.')) return url.hostname.endsWith(h.slice(1)) && url.hostname.length > h.length - 1;
  return !h.includes('*') && url.hostname === h;
}

export function checkOrigin(url, { configDir, allow = [] } = {}) {
  let u;
  try { u = new URL(String(url)); } catch { return { ok: false, reason: 'unparsable URL' }; }
  if (u.protocol === 'file:') {
    try {
      const rel = path.relative(path.resolve(configDir), fileURLToPath(u));
      if (rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel))) return { ok: true };
    } catch { /* nieparsowalna sciezka */ }
    return { ok: false, reason: 'file URL outside config directory' };
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return { ok: false, reason: `scheme ${u.protocol} not allowed` };
  if (LOCAL_HOSTS.has(u.hostname)) return { ok: true };
  if ((allow || []).some((p) => matchAllow(u, p))) return { ok: true };
  return { ok: false, reason: `origin ${u.hostname} not allowed` };
}
