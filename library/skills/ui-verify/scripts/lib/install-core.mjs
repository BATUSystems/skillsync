// Instalacja zaleznosci skilla do cache (czysta logika ze wstrzykiwanym fs/spawn/clock/proc): blokada, atomowy rename, znacznik gotowosci.
import nodeFs from 'node:fs';
import nodePath from 'node:path';
import { createLoader } from './deps.mjs';
import { cacheDirFor, computeHash, defaultCacheRoot } from './paths.mjs';

export const READY_MARKER = '.ui-verify-ready';
const POLL_MS = 500;
const DEFAULT_LOCK_TIMEOUT_MS = 120_000;
const STALE_LOCK_MS = 10 * 60 * 1000;
const NPM_TIMEOUT_MS = 10 * 60 * 1000;
const MAX_OUTPUT = 4000;
const DEP_NAMES = ['playwright-core', 'pngjs', 'pixelmatch'];
const NPM_ARGS = ['ci', '--ignore-scripts', '--omit=dev', '--no-audit', '--no-fund'];

class InstallError extends Error {
  constructor(exit, message) {
    super(message);
    this.name = 'InstallError';
    this.exit = exit;
  }
}

const pathFor = (platform) => (platform === 'win32' ? nodePath.win32 : nodePath.posix);

// Sciezka npm-cli.js (uruchamiana przez `node`, bez powloki) albo null.
export function findNpmCli({ env = {}, execPath = process.execPath, platform = process.platform, fs = nodeFs } = {}) {
  const p = pathFor(platform);
  const fromEnv = typeof env.npm_execpath === 'string' ? env.npm_execpath : '';
  if (fromEnv.endsWith('npm-cli.js') && fs.existsSync(fromEnv)) return fromEnv;
  const dir = p.dirname(execPath);
  const candidates = [
    p.join(dir, 'node_modules', 'npm', 'bin', 'npm-cli.js'),
    p.join(dir, '..', 'lib', 'node_modules', 'npm', 'bin', 'npm-cli.js'),
  ];
  return candidates.find((c) => fs.existsSync(c)) ?? null;
}

function isInside(p, root, child) {
  const a = p.resolve(root);
  const b = p.resolve(child);
  const norm = (s) => (p === nodePath.win32 ? s.toLowerCase() : s);
  const prefix = a.endsWith(p.sep) ? a : a + p.sep;
  return norm(b) === norm(a) || norm(b).startsWith(norm(prefix));
}

// Rzuca InstallError(2), gdy cache lezy w katalogu skilla albo (POSIX) jest cudzy / zapisywalny dla g/o.
export function checkCacheSafety({ root, skillDir, platform = process.platform, fs = nodeFs, proc = process }) {
  const p = pathFor(platform);
  if (isInside(p, skillDir, root)) {
    throw new InstallError(2, `cache dir ${root} lies inside the skill directory; the cache must never live in a project repository`);
  }
  if (platform === 'win32') return;
  if (!fs.existsSync(root)) return;
  const st = fs.statSync(root);
  const uid = typeof proc.getuid === 'function' ? proc.getuid() : process.getuid?.();
  if (uid !== undefined && st.uid !== uid) {
    throw new InstallError(2, `cache dir ${root} is not owned by the current user (code from the cache would run with your rights)`);
  }
  if ((st.mode & 0o022) !== 0) {
    throw new InstallError(2, `cache dir ${root} is writable by group/others (mode ${(st.mode & 0o777).toString(8)}); fix with chmod go-w`);
  }
}

// Zwraca null, gdy instalacja w `dir` jest poprawna, w przeciwnym razie powod (tekst).
async function verifyDir({ dir, hash, fs, requireMarker }) {
  if (!fs.existsSync(dir)) return 'missing directory';
  if (requireMarker) {
    const mp = nodePath.join(dir, READY_MARKER);
    if (!fs.existsSync(mp)) return 'missing readiness marker';
    try {
      const m = JSON.parse(String(fs.readFileSync(mp)));
      if (!m || m.hash !== hash) return 'readiness marker for a different hash';
    } catch {
      return 'unreadable readiness marker';
    }
  }
  const loader = createLoader({ cacheDir: dir, env: {}, fs });
  for (const name of DEP_NAMES) {
    try {
      await loader.load(name);
    } catch (e) {
      return `${name}: ${e && e.message ? e.message : e}`;
    }
  }
  return null;
}

function readSkillFiles(skillDir, fs) {
  return ['package.json', 'package-lock.json'].map((name) => {
    try {
      return String(fs.readFileSync(nodePath.join(skillDir, name)));
    } catch {
      throw new InstallError(2, `skill file ${name} is missing in ${skillDir}`);
    }
  });
}

function lockIsStale({ lockPath, fs, clock, proc }) {
  let pid = null;
  let time = null;
  try {
    const j = JSON.parse(String(fs.readFileSync(lockPath)));
    pid = Number.isInteger(j.pid) ? j.pid : null;
    time = typeof j.time === 'number' ? j.time : (typeof j.createdAt === 'number' ? j.createdAt : null);
  } catch {
    // czesciowo zapisany plik: oceniamy po mtime
  }
  if (time === null) {
    try {
      time = fs.statSync(lockPath).mtimeMs;
    } catch {
      return true; // zniknela miedzy sprawdzeniami
    }
  }
  if (clock.now() - time > STALE_LOCK_MS) return true;
  if (pid !== null) {
    try {
      proc.kill(pid, 0);
    } catch (e) {
      if (e && e.code === 'ESRCH') return true;
    }
  }
  return false;
}

// Blokada wylacznie przez openSync(path, 'wx'); zwraca funkcje zwalniajaca albo rzuca InstallError(3) po limicie.
async function acquireLock({ lockPath, fs, clock, proc, timeoutMs }) {
  const start = clock.now();
  for (;;) {
    let fd;
    try {
      fd = fs.openSync(lockPath, 'wx', 0o600);
    } catch (e) {
      if (!e || e.code !== 'EEXIST') throw e;
      if (lockIsStale({ lockPath, fs, clock, proc })) {
        try {
          fs.unlinkSync(lockPath);
        } catch (u) {
          if (!u || u.code !== 'ENOENT') throw u;
        }
        continue;
      }
      if (clock.now() - start >= timeoutMs) {
        throw new InstallError(3, `timeout waiting for the install lock ${lockPath} (another session is installing; retry later or raise --lock-timeout)`);
      }
      await clock.sleep(POLL_MS);
      continue;
    }
    try {
      const t = clock.now();
      fs.writeSync(fd, JSON.stringify({ pid: proc.pid, time: t, createdAt: t }));
    } finally {
      fs.closeSync(fd);
    }
    return () => {
      try {
        fs.unlinkSync(lockPath);
      } catch {
        // juz zdjeta
      }
    };
  }
}

function tail(text) {
  return text.length > MAX_OUTPUT ? `...${text.slice(text.length - MAX_OUTPUT)}` : text;
}

function runNpm({ spawn, execPath, npmCli, cwd, env, masker }) {
  return new Promise((resolve, reject) => {
    let out = '';
    let settled = false;
    const finish = (fn, v) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      fn(v);
    };
    const child = spawn(execPath, [npmCli, ...NPM_ARGS], {
      shell: false,
      cwd,
      windowsHide: true,
      env: { ...env, PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD: '1', npm_config_ignore_scripts: 'true' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const timer = setTimeout(() => {
      try { child.kill(); } catch { /* ignore */ }
      finish(reject, new InstallError(3, 'npm ci timed out after 10 minutes (network or proxy problem?)'));
    }, NPM_TIMEOUT_MS);
    if (typeof timer.unref === 'function') timer.unref();
    for (const stream of [child.stdout, child.stderr]) {
      if (!stream) continue;
      if (typeof stream.setEncoding === 'function') stream.setEncoding('utf8');
      stream.on('data', (d) => { out += String(d); });
    }
    child.on('error', (e) => {
      finish(reject, new InstallError(3, `cannot run npm: ${masker.mask(e && e.message ? e.message : e)}`));
    });
    child.on('close', (code) => {
      if (code === 0) finish(resolve, undefined);
      else finish(reject, new InstallError(3, `npm ci failed (exit ${code}); check network, proxy and .npmrc\n${tail(masker.mask(out))}`));
    });
  });
}

const identityMasker = { mask: (t) => String(t) };

// Wynik: { exit, status: 'installed'|'already-installed'|'missing'|'error', message, hash, dir }.
export async function installDeps({
  skillDir,
  env = {},
  platform = process.platform,
  homedir,
  execPath = process.execPath,
  fs = nodeFs,
  spawn,
  clock = { now: () => Date.now(), sleep: (ms) => new Promise((r) => setTimeout(r, ms)) },
  proc = process,
  masker = identityMasker,
  lockTimeoutMs = DEFAULT_LOCK_TIMEOUT_MS,
  check = false,
}) {
  let hash = null;
  let dir = null;
  const result = (exit, status, message) => ({ exit, status, message, hash, dir });
  try {
    const root = defaultCacheRoot({ platform, env, homedir });
    checkCacheSafety({ root, skillDir, platform, fs, proc });
    hash = computeHash(readSkillFiles(skillDir, fs));
    dir = cacheDirFor(root, hash, platform);

    const ready = async () => (fs.existsSync(dir) ? verifyDir({ dir, hash, fs, requireMarker: true }) : 'missing directory');
    if ((await ready()) === null) return result(0, 'already-installed', `ui-verify dependencies already installed in ${dir}`);
    if (check) return result(3, 'missing', `ui-verify dependencies are not installed (expected ${dir}); run: node scripts/install.mjs`);

    const npmCli = findNpmCli({ env, execPath, platform, fs });
    if (!npmCli) {
      throw new InstallError(3, 'npm-cli.js not found next to the Node binary; install Node with npm (https://nodejs.org) or run npm ci manually in the skill directory');
    }

    if (!fs.existsSync(root)) fs.mkdirSync(root, { mode: 0o700, recursive: true });
    const p = pathFor(platform);
    const lockPath = `${root}${p.sep}${hash}.lock`;
    const tmpDir = `${root}${p.sep}${hash}.tmp-${proc.pid}`;
    const release = await acquireLock({ lockPath, fs, clock, proc, timeoutMs: lockTimeoutMs });
    try {
      if ((await ready()) === null) return result(0, 'already-installed', `ui-verify dependencies already installed in ${dir}`);
      if (fs.existsSync(dir)) fs.rmSync(dir, { recursive: true, force: true });
      const rmTmp = () => fs.rmSync(tmpDir, { recursive: true, force: true });
      try {
        rmTmp();
        fs.mkdirSync(tmpDir, { recursive: true });
        const [pkgText, lockText] = readSkillFiles(skillDir, fs);
        fs.writeFileSync(nodePath.join(tmpDir, 'package.json'), pkgText);
        fs.writeFileSync(nodePath.join(tmpDir, 'package-lock.json'), lockText);
        await runNpm({ spawn, execPath, npmCli, cwd: tmpDir, env, masker });
        const bad = await verifyDir({ dir: tmpDir, hash, fs, requireMarker: false });
        if (bad) throw new InstallError(2, `installed modules do not load: ${masker.mask(bad)}`);
        fs.writeFileSync(nodePath.join(tmpDir, READY_MARKER), JSON.stringify({ hash, installedAt: new Date(clock.now()).toISOString() }));
        try {
          fs.renameSync(tmpDir, dir);
        } catch (e) {
          if (e && ['ENOTEMPTY', 'EEXIST', 'EPERM'].includes(e.code) && fs.existsSync(dir)) {
            rmTmp();
            const why = await verifyDir({ dir, hash, fs, requireMarker: true });
            if (why === null) return result(0, 'already-installed', `ui-verify dependencies already installed in ${dir} (by another session)`);
            throw new InstallError(2, `target ${dir} appeared concurrently but is not valid: ${why}`);
          }
          throw e;
        }
        return result(0, 'installed', `ui-verify dependencies installed in ${dir}`);
      } catch (e) {
        try { rmTmp(); } catch { /* best effort */ }
        throw e;
      }
    } finally {
      release();
    }
  } catch (e) {
    if (e instanceof InstallError) return result(e.exit, 'error', e.message);
    return result(2, 'error', `internal error: ${masker.mask(e && e.message ? e.message : e)}`);
  }
}
