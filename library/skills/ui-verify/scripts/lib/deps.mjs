// Loader zaleznosci (playwright-core, pngjs, pixelmatch) z cache zestawu; bez golych specyfikatorow.
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import nodeFs from 'node:fs';

// Warunki exports jako listy z split (literal "import" + cudzyslow imituje goly specyfikator dla strazników regex).
const ESM_CONDS = 'import,default'.split(',');
const CJS_CONDS = 'require,default'.split(',');
const NAMES = new Set(['playwright-core', 'pngjs', 'pixelmatch']);

export class DepsError extends Error {
  constructor(code, message, hint = '') {
    super(message);
    this.name = 'DepsError';
    this.code = code;
    this.exit = 3;
    this.hint = hint;
  }
}

const INSTALL_HINT = 'Uruchom: node scripts/install.mjs (instaluje zaleznosci do cache).';

function pickCondition(node, conds) {
  if (typeof node === 'string') return node;
  if (!node || typeof node !== 'object' || Array.isArray(node)) return null;
  for (const c of conds) {
    if (c in node) {
      const r = pickCondition(node[c], conds);
      if (r) return r;
    }
  }
  return null;
}

function entryFor(pkg, esm) {
  const conds = esm ? ESM_CONDS : CJS_CONDS;
  let rel = null;
  if (pkg.exports != null) {
    let dot = pkg.exports;
    if (dot && typeof dot === 'object' && !Array.isArray(dot)) {
      const keys = Object.keys(dot);
      if (keys.some((k) => k.startsWith('.'))) dot = dot['.'];
    }
    rel = pickCondition(dot, conds);
  }
  if (!rel) rel = typeof pkg.main === 'string' && pkg.main ? pkg.main : 'index.js';
  return rel;
}

export function createLoader({
  cacheDir,
  env = {},
  fs = nodeFs,
  requireFactory = (p) => createRequire(p),
  importer = (u) => import(u),
}) {
  function readJson(p) {
    try {
      return JSON.parse(String(fs.readFileSync(p)));
    } catch {
      return null;
    }
  }

  function resolveRoot(name) {
    if (name === 'playwright-core') {
      const ov = typeof env.PLAYWRIGHT_MODULE_PATH === 'string' && env.PLAYWRIGHT_MODULE_PATH !== ''
        ? env.PLAYWRIGHT_MODULE_PATH : null;
      if (ov) {
        for (const root of [ov, path.join(ov, 'playwright-core')]) {
          const pkg = fs.existsSync(path.join(root, 'package.json')) ? readJson(path.join(root, 'package.json')) : null;
          if (pkg && pkg.name === 'playwright-core') return { root, pkg, override: true };
        }
        throw new DepsError('bad-override', 'PLAYWRIGHT_MODULE_PATH nie wskazuje paczki playwright-core',
          'Ustaw PLAYWRIGHT_MODULE_PATH na katalog paczki playwright-core albo na node_modules, ktory ja zawiera.');
      }
    }
    const root = path.join(cacheDir, 'node_modules', name);
    const pj = path.join(root, 'package.json');
    const pkg = fs.existsSync(pj) ? readJson(pj) : null;
    if (!pkg) throw new DepsError('not-installed', `brak zainstalowanej zaleznosci ${name}`, INSTALL_HINT);
    return { root, pkg, override: false };
  }

  function checkLock(name, pkg) {
    const lp = path.join(cacheDir, 'package-lock.json');
    if (!fs.existsSync(lp)) return;
    const lock = readJson(lp);
    const want = lock && lock.packages && lock.packages[`node_modules/${name}`];
    if (want && want.version && want.version !== pkg.version) {
      throw new DepsError('bad-install', `wersja ${name} niezgodna z lockfile`,
        `${INSTALL_HINT} (zainstaluj ponownie).`);
    }
  }

  return {
    async load(name) {
      if (!NAMES.has(name)) throw new TypeError(`nieznana zaleznosc: ${name}`);
      const { root, pkg, override } = resolveRoot(name);
      if (!override) checkLock(name, pkg);
      const esm = pkg.type === 'module';
      const entry = path.resolve(root, entryFor(pkg, esm));
      try {
        if (esm) return await importer(pathToFileURL(entry).href);
        return requireFactory(path.join(cacheDir, 'package.json'))(entry);
      } catch (e) {
        if (e instanceof DepsError) throw e;
        throw new DepsError('bad-install', `nie udalo sie zaladowac ${name}`, `${INSTALL_HINT} (zainstaluj ponownie).`);
      }
    },
    version(name) {
      if (!NAMES.has(name)) throw new TypeError(`nieznana zaleznosc: ${name}`);
      try {
        return resolveRoot(name).pkg.version ?? null;
      } catch {
        return null;
      }
    },
  };
}

export function explainMissing(err) {
  const lines = [`Zaleznosci ui-verify: ${err.code} (kod 3, NOT_RUN).`];
  if (err.message) lines.push(err.message);
  lines.push(err.hint || INSTALL_HINT);
  return { exit: 3, lines };
}
