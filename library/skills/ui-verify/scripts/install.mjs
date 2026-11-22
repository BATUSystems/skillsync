/**
 * Purpose: Install the pinned ui-verify dependencies (playwright-core, pngjs, pixelmatch) into the user cache so the other tools can load them.
 * Usage: node install.mjs [--check] [--lock-timeout <seconds>] [--json] | node install.mjs --selftest
 * Inputs: package.json and package-lock.json of the skill; env UI_VERIFY_CACHE_DIR (cache root override), UI_VERIFY_SECRET_ENV (names of env vars masked in output);
 *   --check verifies only (no install); --lock-timeout seconds to wait for another installing session (default 120).
 * Outputs: one status line on stdout (installed / already installed) or the reason on stderr; with --json one object { exit, status, message, hash, dir }.
 * Exit codes: 0 installed or already installed (--check: installed and verified), 2 usage/unsafe cache/internal error, 3 npm, network, proxy missing or lock timeout (--check: not installed).
 * Dependencies: npm (shipped with Node, run as `node npm-cli.js`, no shell); Node >= 20.11 (tested on 22); network access to the npm registry (not needed for --check and --selftest).
 * Limits: installs into <cache>/<hash>/ atomically (tmp directory, readiness marker last, rename); one installer per hash thanks to an exclusive lock file; no browsers are downloaded.
 * Security: runs `npm ci --ignore-scripts --omit=dev` from the lockfile only; refuses a cache that is foreign, writable by group/others (POSIX) or inside the skill directory; npm output is masked and truncated.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { parseArgs, runSelftest } from './lib/cli.mjs';
import { createMasker, collectSecretsFromEnv, setGuardMasker } from './lib/out.mjs';
import { installGuards, isMain } from './lib/verdict.mjs';
import { skillDirOf } from './lib/runtime.mjs';
import { computeHash } from './lib/paths.mjs';
import { checkCacheSafety, findNpmCli, installDeps } from './lib/install-core.mjs';

const SPEC = { string: ['lock-timeout'], boolean: ['selftest', 'check', 'json'] };
const USAGE = 'usage: node install.mjs [--check] [--lock-timeout <seconds>] [--json] | node install.mjs --selftest';

class UsageError extends Error {}

// --- selftest: atrapy spawn/zegara, prawdziwy fs w katalogu tymczasowym (bez npm, sieci i cache uzytkownika) ---

const VERSIONS = { 'playwright-core': '1.0.1', pngjs: '2.0.0', pixelmatch: '3.0.0' };
const PKG = JSON.stringify({ name: 'fixture', version: '1.0.0', private: true, dependencies: VERSIONS });
const LOCK = JSON.stringify({
  name: 'fixture',
  lockfileVersion: 3,
  packages: { '': { name: 'fixture' }, ...Object.fromEntries(Object.entries(VERSIONS).map(([n, v]) => [`node_modules/${n}`, { version: v }])) },
});

function populate(dir) {
  for (const [name, version] of Object.entries(VERSIONS)) {
    const root = path.join(dir, 'node_modules', name);
    fs.mkdirSync(root, { recursive: true });
    fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({ name, version, main: 'index.js' }));
    fs.writeFileSync(path.join(root, 'index.js'), 'module.exports = {};\n');
  }
}

function fakeSpawn({ code = 0 } = {}) {
  const calls = [];
  const fn = (cmd, args, opts) => {
    calls.push({ cmd, args, opts });
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.kill = () => true;
    setImmediate(() => {
      if (code === 0) populate(opts.cwd);
      else child.stderr.emit('data', 'npm ERR! network');
      child.emit('close', code, null);
    });
    return child;
  };
  return { fn, calls };
}

function sandbox() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-install-selftest-'));
  const skillDir = path.join(base, 'skill');
  fs.mkdirSync(skillDir);
  fs.writeFileSync(path.join(skillDir, 'package.json'), PKG);
  fs.writeFileSync(path.join(skillDir, 'package-lock.json'), LOCK);
  const npmCli = path.join(base, 'npm', 'bin', 'npm-cli.js');
  fs.mkdirSync(path.dirname(npmCli), { recursive: true });
  fs.writeFileSync(npmCli, '// stub\n');
  const cacheRoot = path.join(base, 'cache');
  fs.mkdirSync(cacheRoot, { mode: 0o700 });
  if (process.platform !== 'win32') fs.chmodSync(cacheRoot, 0o700);
  const hash = computeHash([PKG, LOCK]);
  const clock = { t: 1_700_000_000_000, now() { return clock.t; }, async sleep(ms) { clock.t += ms; } };
  const sb = {
    base, skillDir, cacheRoot, npmCli, hash, clock,
    hashDir: path.join(cacheRoot, hash),
    lockPath: path.join(cacheRoot, `${hash}.lock`),
    proc: { pid: 4321, getuid: process.getuid ? () => process.getuid() : undefined, kill: (p) => { if (p === 4321 || p === 777) return true; const e = new Error('ESRCH'); e.code = 'ESRCH'; throw e; } },
    run(extra = {}) {
      return installDeps({
        skillDir, env: { UI_VERIFY_CACHE_DIR: cacheRoot, npm_execpath: npmCli }, platform: process.platform, homedir: base,
        execPath: path.join(base, 'node'), fs, spawn: extra.spawn ?? fakeSpawn().fn, clock, proc: sb.proc, ...extra,
      });
    },
    leftovers: () => fs.readdirSync(cacheRoot),
    cleanup: () => fs.rmSync(base, { recursive: true, force: true }),
  };
  return sb;
}

function check(cond, msg) {
  if (!cond) throw new Error(msg);
}

function sandboxCase(name, body) {
  return {
    name,
    fn: async () => {
      const sb = sandbox();
      try {
        await body(sb);
      } finally {
        sb.cleanup();
      }
    },
  };
}

function selftestCases() {
  const posix = { skip: process.platform === 'win32' };
  const cases = [
    sandboxCase('fresh install writes marker and leaves only <hash>', async (sb) => {
      const r = await sb.run();
      check(r.exit === 0 && r.status === 'installed', r.message);
      check(fs.existsSync(path.join(sb.hashDir, '.ui-verify-ready')), 'no marker');
      check(JSON.stringify(sb.leftovers()) === JSON.stringify([sb.hash]), `leftovers: ${sb.leftovers()}`);
    }),
    sandboxCase('idempotent: second run does not call npm', async (sb) => {
      const sp = fakeSpawn();
      await sb.run({ spawn: sp.fn });
      const r = await sb.run({ spawn: sp.fn });
      check(r.exit === 0 && r.status === 'already-installed' && /already installed/.test(r.message), r.message);
      check(sp.calls.length === 1, `npm calls: ${sp.calls.length}`);
    }),
    sandboxCase('corrupted directory (no marker) is reinstalled', async (sb) => {
      fs.mkdirSync(sb.hashDir);
      fs.writeFileSync(path.join(sb.hashDir, 'junk'), 'x');
      const r = await sb.run();
      check(r.exit === 0 && r.status === 'installed', r.message);
      check(!fs.existsSync(path.join(sb.hashDir, 'junk')), 'junk left');
    }),
    sandboxCase('atomic: failing npm leaves neither <hash> nor tmp nor lock (exit 3)', async (sb) => {
      const r = await sb.run({ spawn: fakeSpawn({ code: 1 }).fn });
      check(r.exit === 3, `exit ${r.exit}`);
      check(sb.leftovers().length === 0, `leftovers: ${sb.leftovers()}`);
    }),
    sandboxCase('rename race: valid target created by another session => exit 0', async (sb) => {
      const real = fs.renameSync;
      let raced = false;
      const racing = { ...fs, renameSync: (from, to) => {
        if (!raced && to === sb.hashDir) {
          raced = true;
          fs.cpSync(from, to, { recursive: true });
          const e = new Error('exists');
          e.code = 'ENOTEMPTY';
          throw e;
        }
        return real(from, to);
      } };
      const r = await sb.run({ fs: racing });
      check(raced && r.exit === 0, `${r.exit} ${r.message}`);
      check(JSON.stringify(sb.leftovers()) === JSON.stringify([sb.hash]), `leftovers: ${sb.leftovers()}`);
    }),
    sandboxCase('stale lock of a dead process is taken over', async (sb) => {
      const t = sb.clock.now();
      fs.writeFileSync(sb.lockPath, JSON.stringify({ pid: 999, time: t }));
      const r = await sb.run();
      check(r.exit === 0, r.message);
      check(!fs.existsSync(sb.lockPath), 'lock not released');
    }),
    sandboxCase('live lock: wait until the limit, then exit 3 and leave the foreign lock', async (sb) => {
      fs.writeFileSync(sb.lockPath, JSON.stringify({ pid: 777, time: sb.clock.now() }));
      const sp = fakeSpawn();
      const r = await sb.run({ lockTimeoutMs: 2000, spawn: sp.fn });
      check(r.exit === 3 && sp.calls.length === 0, `${r.exit} ${r.message}`);
      check(fs.existsSync(sb.lockPath), 'foreign lock removed');
    }),
    sandboxCase('spawn is never given a shell, bare npm or .cmd', async (sb) => {
      const sp = fakeSpawn();
      await sb.run({ spawn: sp.fn });
      const { cmd, args, opts } = sp.calls[0];
      check(opts.shell === false, 'shell is not false');
      check([cmd, ...args].every((x) => x !== 'npm' && !/\.(cmd|bat)$/i.test(String(x))), 'bare npm or .cmd');
      check(opts.env.PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD === '1' && opts.env.npm_config_ignore_scripts === 'true', 'child env');
    }),
    sandboxCase('missing npm => exit 3 without spawning', async (sb) => {
      const sp = fakeSpawn();
      const r = await sb.run({ env: { UI_VERIFY_CACHE_DIR: sb.cacheRoot }, spawn: sp.fn });
      check(r.exit === 3 && sp.calls.length === 0, `${r.exit} ${r.message}`);
    }),
    sandboxCase('cache inside the skill directory is refused (exit 2)', async (sb) => {
      const inside = path.join(sb.skillDir, 'cache');
      const r = await sb.run({ env: { UI_VERIFY_CACHE_DIR: inside, npm_execpath: sb.npmCli } });
      check(r.exit === 2 && !fs.existsSync(inside), `${r.exit} ${r.message}`);
    }),
    {
      name: 'findNpmCli handles the Linux, macOS and Windows layouts',
      fn: async () => {
        const fsx = (list) => ({ existsSync: (p) => list.includes(p) });
        const lin = '/m/n/lib/node_modules/npm/bin/npm-cli.js';
        const win = 'D:\\h\\node\\node_modules\\npm\\bin\\npm-cli.js';
        check(findNpmCli({ env: {}, execPath: '/m/n/bin/node', platform: 'linux', fs: fsx([lin]) }) === lin, 'linux');
        check(findNpmCli({ env: {}, execPath: '/m/n/bin/node', platform: 'darwin', fs: fsx([lin]) }) === lin, 'darwin');
        check(findNpmCli({ env: {}, execPath: 'D:\\h\\node\\node.exe', platform: 'win32', fs: fsx([win]) }) === win, 'win32');
        check(!findNpmCli({ env: {}, execPath: '/m/n/bin/node', platform: 'linux', fs: fsx([]) }), 'missing');
      },
    },
    {
      name: 'checkCacheSafety skips uid and mode on Windows but still refuses cache inside the skill',
      fn: async () => {
        const wfs = { existsSync: () => true, statSync: () => ({ uid: 0, mode: 0o40777 }) };
        checkCacheSafety({ root: 'D:\\h\\cache', skillDir: 'D:\\h\\skill', platform: 'win32', fs: wfs, proc: {} });
        let refused = false;
        try { checkCacheSafety({ root: 'D:\\h\\skill\\c', skillDir: 'D:\\h\\skill', platform: 'win32', fs: wfs, proc: {} }); } catch { refused = true; }
        check(refused, 'inside skill accepted');
      },
    },
  ];
  if (!posix.skip) {
    cases.push(
      sandboxCase('cache writable by others is refused (exit 2)', async (sb) => {
        fs.chmodSync(sb.cacheRoot, 0o777);
        const sp = fakeSpawn();
        const r = await sb.run({ spawn: sp.fn });
        check(r.exit === 2 && sp.calls.length === 0, `${r.exit} ${r.message}`);
      }),
      sandboxCase('cache owned by another user is refused (exit 2)', async (sb) => {
        const foreign = { ...sb.proc, getuid: () => process.getuid() + 1 };
        const r = await sb.run({ proc: foreign });
        check(r.exit === 2, `${r.exit} ${r.message}`);
      }),
    );
  }
  return cases;
}

// --- CLI ---

export async function main(argv, env, io) {
  const secrets = collectSecretsFromEnv(env, String(env.UI_VERIFY_SECRET_ENV || '').split(',').map((s) => s.trim()).filter(Boolean));
  const masker = createMasker({ secrets });
  setGuardMasker(masker);
  const out = (text) => io.stdout.write(masker.mask(text));
  const err = (text) => io.stderr.write(masker.mask(text));
  try {
    const { values, positionals, errors } = parseArgs(argv, SPEC);
    if (errors.length) throw new UsageError(errors.join('; '));
    if (positionals.length) throw new UsageError(`unexpected argument "${positionals[0]}"`);
    if (values.selftest) {
      return await runSelftest('install', selftestCases(), { stdout: io.stdout, stderr: io.stderr, masker });
    }
    let lockTimeoutMs;
    if (values['lock-timeout'] !== undefined) {
      const n = String(values['lock-timeout']).trim() === '' ? NaN : Number(values['lock-timeout']);
      if (!Number.isFinite(n) || n < 0) throw new UsageError(`bad --lock-timeout "${values['lock-timeout']}" (expected seconds >= 0)`);
      lockTimeoutMs = n * 1000;
    }
    const r = await installDeps({
      skillDir: skillDirOf(import.meta.url),
      env,
      platform: process.platform,
      homedir: os.homedir(),
      execPath: process.execPath,
      fs,
      spawn,
      proc: process,
      masker,
      check: Boolean(values.check),
      ...(lockTimeoutMs !== undefined ? { lockTimeoutMs } : {}),
    });
    if (values.json) {
      out(`${JSON.stringify({ exit: r.exit, status: r.status, message: r.message, hash: r.hash, dir: r.dir })}\n`);
    } else if (r.exit === 0) {
      out(`${r.message}\n`);
    } else {
      err(`install: ${r.message}\n`);
    }
    return r.exit;
  } catch (e) {
    if (e instanceof UsageError) {
      err(`install: ${e.message}\n${USAGE}\n`);
      return 2;
    }
    throw e;
  }
}

if (isMain(import.meta.url, process.argv[1])) {
  installGuards(process);
  const code = await main(process.argv.slice(2), process.env, { stdout: process.stdout, stderr: process.stderr });
  process.exit(code);
}
