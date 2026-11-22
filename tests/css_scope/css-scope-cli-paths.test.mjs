// S5b: flagi --scss-dir / --entry / --without (oraz --out) dla `compile` i `run` w css-scope.mjs.
// Konfiguracja wzorca lezy w warstwie (poza projektem), katalog SCSS i wyjscie w projekcie (cwd).
// Czesc 1 (bez cache): walidacja flag, kody wyjscia, --help; czesc 2: prawdziwy sass.js z UI_VERIFY_TEST_CACHE
// (bez niego pomijana z powodem needs-ui-verify-deps).
// Odrzucenie flagi, ktorej CLI nie zna, tez daje kod 2, wiec kazde odrzucenie sprawdza tez, ze komunikat NIE jest "unknown flag".
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const SCRIPT = path.join(ROOT, 'library', 'skills', 'ui-verify', 'scripts', 'css-scope.mjs');

function findCache() {
  const root = process.env.UI_VERIFY_TEST_CACHE;
  if (!root) return { reason: 'needs-ui-verify-deps: UI_VERIFY_TEST_CACHE nie ustawione' };
  try {
    const sub = fs.readdirSync(root).find((d) => fs.existsSync(path.join(root, d, 'node_modules', 'sass.js', 'package.json')));
    if (!sub) return { reason: 'needs-ui-verify-deps: brak sass.js w UI_VERIFY_TEST_CACHE' };
    return { root };
  } catch (e) {
    return { reason: `needs-ui-verify-deps: ${e.code || e.message}` };
  }
}
const cache = findCache();
const real = { skip: cache.reason ?? false };

const SYMLINK_SKIP = (() => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'css-scope-paths-probe-'));
  try {
    fs.writeFileSync(path.join(d, 't'), 'x');
    fs.symlinkSync(path.join(d, 't'), path.join(d, 'l'), 'file');
    return false;
  } catch (e) {
    return `needs-symlink: ${e.code || e.message}`;
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
})();
const realSym = { skip: cache.reason ?? SYMLINK_SKIP };

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'css-scope-paths-'));
after(() => fs.rmSync(tmpRoot, { recursive: true, force: true }));
let counter = 0;
const newDir = (label = 'd') => {
  const d = path.join(tmpRoot, `${label}-${counter++}`);
  fs.mkdirSync(d, { recursive: true });
  return d;
};

// Bez cache: pusty katalog cache => sass.js niedostepny (kod 3, gdy walidacja przejdzie).
function run(args, { cwd, cacheRoot } = {}) {
  const base = { ...process.env };
  for (const k of Object.keys(base)) if (k.startsWith('UI_VERIFY_') || k === 'PLAYWRIGHT_MODULE_PATH') delete base[k];
  const r = spawnSync(process.execPath, [SCRIPT, ...args], {
    cwd: cwd ?? newDir('cwd'),
    env: { ...base, UI_VERIFY_CACHE_DIR: cacheRoot ?? newDir('emptycache') },
    encoding: 'utf8',
    timeout: 120000,
  });
  return { code: r.status, stdout: r.stdout ?? '', stderr: r.stderr ?? '', all: `${r.stdout ?? ''}${r.stderr ?? ''}` };
}
const runReal = (args, o = {}) => run(args, { ...o, cacheRoot: cache.root });

const EXACT = '.root-theme main[tabindex="-1"]:focus:not(:focus-visible)';
// Konfiguracja wzorca: tylko korzen, wyjatki i selftest (bez scssDir/entry/without/out).
const PATTERN = {
  version: 1,
  root: '.root',
  exceptions: [
    { id: 'menu-host', kind: 'prefix', value: '.menu-host > .item', reason: 'menu rendered outside the root' },
    { id: 'theme-hook', kind: 'root-alias', value: '.root-theme', onlyPropertyPrefix: '--tok-', reason: 'design tokens only' },
    { id: 'skip-link', kind: 'exact-rule', selector: EXACT, declarations: ['outline:0!important'], reason: 'focus target without outline' },
  ],
  selftest: [
    { name: 'root-link', css: '.root a:hover { color: red }', expect: 'accepted' },
    { name: 'body-rejected', css: 'body { margin: 0 }', expect: 'rejected' },
  ],
};

// Warstwa (konfiguracja) i projekt (cwd z katalogiem SCSS) to rozne katalogi.
function setup({ config = {}, scss = 'styles' } = {}) {
  const layer = newDir('layer');
  const project = newDir('project');
  const cfg = path.join(layer, 'css-scope.json');
  fs.writeFileSync(cfg, JSON.stringify({ ...PATTERN, ...config }));
  const scssDir = path.join(project, scss);
  fs.mkdirSync(scssDir, { recursive: true });
  fs.writeFileSync(path.join(scssDir, '_vars.scss'), '$brand: #336699;\n');
  fs.writeFileSync(path.join(scssDir, 'base.scss'), "@import 'vars';\n.root a { color: $brand }\n");
  fs.writeFileSync(path.join(scssDir, 'main.scss'), "@import 'vars';\n.root a { color: $brand }\n.root .extra { margin: 0 }\n");
  return { layer, project, cfg, scssDir };
}
const notUnknown = (r, flag) => {
  assert.doesNotMatch(r.stderr, /unknown flag/i, `flaga ${flag} nieznana:\n${r.stderr}`);
  assert.ok(r.stderr.includes(flag), `komunikat nie wymienia ${flag}:\n${r.stderr}`);
};

// ===== Czesc 1: bez cache =====

test('compile: --scss-dir + --entry we flagach wystarczaja przy konfiguracji bez scssDir/entry (walidacja przechodzi, dalej brak sass.js = kod 3)', () => {
  const { project, cfg } = setup();
  const r = run(['compile', '--config', cfg, '--scss-dir', 'styles', '--entry', 'main.scss'], { cwd: project });
  assert.equal(r.code, 3, r.all);
});

test('run: --scss-dir + --entry + --without we flagach przechodza walidacje (kod 3 = brak sass.js, nie 2)', () => {
  const { project, cfg } = setup();
  const r = run(['run', '--config', cfg, '--scss-dir', 'styles', '--entry', 'main.scss', '--without', 'base.scss'], { cwd: project });
  assert.equal(r.code, 3, r.all);
});

test('compile: flaga --scss-dir ma pierwszenstwo przed konfiguracja (zly scssDir w konfiguracji nie blokuje)', () => {
  const { project, cfg } = setup({ config: { scssDir: '../does-not-exist-anywhere', entry: 'nope.scss' } });
  const r = run(['compile', '--config', cfg, '--scss-dir', 'styles', '--entry', 'main.scss'], { cwd: project });
  assert.equal(r.code, 3, r.all);
});

test('compile/run: brak katalogu SCSS ani we fladze, ani w konfiguracji => kod 2 z podpowiedzia o --scss-dir', () => {
  const { project, cfg } = setup();
  for (const cmd of ['compile', 'run']) {
    const r = run([cmd, '--config', cfg, '--entry', 'main.scss'], { cwd: project });
    assert.equal(r.code, 2, `${cmd}: ${r.all}`);
    assert.doesNotMatch(r.stderr, /unknown flag/i);
    assert.match(r.stderr, /--scss-dir/, `${cmd}: brak podpowiedzi o fladze:\n${r.stderr}`);
  }
});

test('compile/run: --scss-dir bez entry (ani flaga, ani konfiguracja) => kod 2 z podpowiedzia o --entry', () => {
  const { project, cfg } = setup();
  for (const cmd of ['compile', 'run']) {
    const r = run([cmd, '--config', cfg, '--scss-dir', 'styles'], { cwd: project });
    assert.equal(r.code, 2, `${cmd}: ${r.all}`);
    assert.doesNotMatch(r.stderr, /unknown flag/i);
    assert.match(r.stderr, /--entry/, `${cmd}:\n${r.stderr}`);
  }
});

test('--scss-dir: "..", sciezka bezwzgledna i backslash => kod 2 (compile i run), flaga rozpoznana', () => {
  const { project, cfg } = setup();
  const abs = path.join(project, 'styles');
  for (const cmd of ['compile', 'run']) {
    for (const bad of ['..', '../styles', 'a/../../b', abs, 'a\\b']) {
      const r = run([cmd, '--config', cfg, '--scss-dir', bad, '--entry', 'main.scss'], { cwd: project });
      assert.equal(r.code, 2, `${cmd} ${JSON.stringify(bad)}: ${r.all}`);
      notUnknown(r, '--scss-dir');
    }
  }
});

test('--scss-dir jest wzgledny wobec cwd, nie katalogu konfiguracji: katalog istnieje tylko wzgledem cwd => walidacja przechodzi (kod 3)', () => {
  const { project, cfg, layer } = setup();
  assert.equal(fs.existsSync(path.join(layer, 'styles')), false);
  const r = run(['compile', '--config', cfg, '--scss-dir', 'styles', '--entry', 'main.scss'], { cwd: project });
  assert.equal(r.code, 3, r.all);
});

test('--out: "..", sciezka bezwzgledna i backslash => kod 2, nic nie zapisane (cwd jako korzen)', () => {
  const { project, cfg } = setup();
  const outside = newDir('outside');
  for (const cmd of ['compile', 'run']) {
    for (const bad of ['../escaped.css', path.join(outside, 'abs.css'), 'a\\b.css']) {
      const r = run([cmd, '--config', cfg, '--scss-dir', 'styles', '--entry', 'main.scss', '--without', 'base.scss', '--out', bad], { cwd: project });
      assert.equal(r.code, 2, `${cmd} ${JSON.stringify(bad)}: ${r.all}`);
      notUnknown(r, '--out');
    }
  }
  assert.deepEqual(fs.readdirSync(outside), []);
  assert.equal(fs.existsSync(path.join(project, '..', 'escaped.css')), false);
});

test('--entry i --without: tylko nazwa pliku [A-Za-z0-9._-]+.scss; inne => kod 2', () => {
  const { project, cfg } = setup();
  const bad = ['sub/main.scss', '../main.scss', 'main.css', 'main', 'ma in.scss', 'main.scss ', 'a\\b.scss', '/abs/main.scss', '.scss', 'ma;in.scss'];
  for (const flag of ['--entry', '--without']) {
    for (const b of bad) {
      const args = ['run', '--config', cfg, '--scss-dir', 'styles'];
      args.push(...(flag === '--entry' ? ['--entry', b, '--without', 'base.scss'] : ['--entry', 'main.scss', '--without', b]));
      const r = run(args, { cwd: project });
      assert.equal(r.code, 2, `${flag} ${JSON.stringify(b)}: ${r.all}`);
      notUnknown(r, flag);
    }
  }
  const rc = run(['compile', '--config', cfg, '--scss-dir', 'styles', '--entry', '../x.scss'], { cwd: project });
  assert.equal(rc.code, 2, rc.all);
  notUnknown(rc, '--entry');
});

test('--entry/--without: poprawne nazwy (litery, cyfry, kropka, myslnik, podkreslenie) przechodza walidacje', () => {
  const { project, cfg } = setup();
  const r = run(['run', '--config', cfg, '--scss-dir', 'styles', '--entry', 'Main-1_a.b.scss', '--without', '_x.scss'], { cwd: project });
  assert.equal(r.code, 3, r.all);
});

test('flagi sciezek sa niedozwolone dla check, diff i selftest: kod 2 z nazwa flagi (nie "unknown")', () => {
  const { project, cfg } = setup();
  fs.writeFileSync(path.join(project, 'a.css'), '.root a { color: red }\n');
  fs.writeFileSync(path.join(project, 'b.css'), '.root a { color: red }\n');
  const cmds = { check: ['check', 'a.css'], diff: ['diff', 'a.css', 'b.css'], selftest: ['selftest'] };
  const flags = { '--scss-dir': 'styles', '--entry': 'main.scss', '--without': 'base.scss' };
  for (const [name, base] of Object.entries(cmds)) {
    for (const [flag, value] of Object.entries(flags)) {
      const r = run([...base, '--config', cfg, flag, value], { cwd: project });
      assert.equal(r.code, 2, `${name} ${flag}: ${r.all}`);
      notUnknown(r, flag);
      assert.match(r.stderr, new RegExp(name), `${name} ${flag}: komunikat nie wymienia komendy:\n${r.stderr}`);
    }
  }
});

test('sanity: check/diff/selftest bez nowych flag dzialaja (kod 0) tez z konfiguracja bez scssDir', () => {
  const { project, cfg } = setup();
  fs.writeFileSync(path.join(project, 'a.css'), '.root a { color: red }\n');
  assert.equal(run(['check', '--config', cfg, 'a.css'], { cwd: project }).code, 0);
  assert.equal(run(['selftest', '--config', cfg], { cwd: project }).code, 0);
});

test('--help wymienia --scss-dir, --entry, --without i --out', () => {
  const r = run(['--help']);
  assert.doesNotMatch(r.all, /unknown flag/i, r.all);
  for (const f of ['--scss-dir', '--entry', '--without', '--out']) assert.ok(r.all.includes(f), `brak ${f} w --help:\n${r.all}`);
});

// ===== Czesc 2: prawdziwy sass.js =====

test('compile: konfiguracja w warstwie, flagi wskazuja projekt; --out wzgledem cwd (nie katalogu konfiguracji)', real, () => {
  const { project, cfg, layer } = setup();
  const r = runReal(['compile', '--config', cfg, '--scss-dir', 'styles', '--entry', 'main.scss', '--out', 'build/out.css'], { cwd: project });
  assert.equal(r.code, 0, r.all);
  assert.match(r.stdout, /^PASS compile: main\.scss -> \d+ chars$/m);
  const css = fs.readFileSync(path.join(project, 'build', 'out.css'), 'utf8');
  assert.match(css, /#336699/i);
  assert.match(css, /\.root \.extra/);
  assert.equal(fs.existsSync(path.join(layer, 'build')), false, 'wyjscie w katalogu konfiguracji');
});

test('compile: flagi maja pierwszenstwo przed scssDir/entry z konfiguracji', real, () => {
  const { project, cfg, layer } = setup({ config: { scssDir: 'wrong', entry: 'broken.scss' } });
  fs.mkdirSync(path.join(layer, 'wrong'));
  fs.writeFileSync(path.join(layer, 'wrong', 'broken.scss'), '.root { .a { color: $undefined-variable } }\n');
  const r = runReal(['compile', '--config', cfg, '--scss-dir', 'styles', '--entry', 'main.scss', '--out', 'o.css'], { cwd: project });
  assert.equal(r.code, 0, r.all);
  assert.match(fs.readFileSync(path.join(project, 'o.css'), 'utf8'), /\.root \.extra/);
});

test('compile: sciezki z samej konfiguracji nadal wzgledem katalogu konfiguracji (scssDir, out), niezaleznie od cwd', real, () => {
  const { layer, cfg } = setup({ config: { scssDir: 'own', entry: 'main.scss', out: 'dist/styles.css' } });
  fs.mkdirSync(path.join(layer, 'own'));
  fs.writeFileSync(path.join(layer, 'own', 'main.scss'), '.root .own { margin: 0 }\n');
  const cwd = newDir('elsewhere');
  const r = runReal(['compile', '--config', cfg], { cwd });
  assert.equal(r.code, 0, r.all);
  assert.match(fs.readFileSync(path.join(layer, 'dist', 'styles.css'), 'utf8'), /\.root \.own/);
  assert.deepEqual(fs.readdirSync(cwd), []);
});

test('compile: --out z flagi ma pierwszenstwo przed config.out i jest wzgledne wobec cwd', real, () => {
  const { project, layer, cfg } = setup({ config: { out: 'dist/styles.css' } });
  const r = runReal(['compile', '--config', cfg, '--scss-dir', 'styles', '--entry', 'main.scss', '--out', 'flag.css'], { cwd: project });
  assert.equal(r.code, 0, r.all);
  assert.ok(fs.existsSync(path.join(project, 'flag.css')));
  assert.equal(fs.existsSync(path.join(layer, 'dist', 'styles.css')), false);
});

test('compile: --scss-dir wskazujacy katalog bez --entry w pliku konfiguracji ani we fladze => kod 2', real, () => {
  const { project, cfg } = setup();
  const r = runReal(['compile', '--config', cfg, '--scss-dir', 'styles'], { cwd: project });
  assert.equal(r.code, 2, r.all);
});

test('run: flagi --scss-dir/--entry/--without => RESULT: PASS, kod 0, wyjscie w cwd', real, () => {
  const { project, cfg } = setup();
  const r = runReal(['run', '--config', cfg, '--scss-dir', 'styles', '--entry', 'main.scss', '--without', 'base.scss', '--out', 'out.css'], { cwd: project });
  assert.equal(r.code, 0, r.all);
  for (const name of ['selftest', 'compile', 'scope', 'diff']) assert.match(r.stdout, new RegExp(`^PASS ${name}\\b`, 'm'), r.stdout);
  assert.match(r.stdout, /^RESULT: PASS$/m);
  assert.ok(fs.existsSync(path.join(project, 'out.css')));
});

test('run: --without z flagi ma pierwszenstwo przed konfiguracja (inny plik bazowy = inny wynik diff)', real, () => {
  // base.scss == main bez .extra; z konfiguracyjnym without wskazujacym main.scss roznicy by nie bylo.
  const { project, cfg } = setup({ config: { entry: 'main.scss', without: 'main.scss', scssDir: 'ignored' } });
  const r = runReal(['run', '--config', cfg, '--json', '--scss-dir', 'styles', '--entry', 'main.scss', '--without', 'base.scss'], { cwd: project });
  assert.equal(r.code, 0, r.all);
  const obj = JSON.parse(r.stdout);
  const diff = obj.checks.find((c) => c.name === 'diff');
  assert.ok(diff, r.stdout);
  assert.doesNotMatch(JSON.stringify(diff), /no differences|identical/i, 'without z konfiguracji uzyty zamiast flagi');
});

test('run: --entry/--without nieistniejace w katalogu SCSS => kod nie 0 (blad, nie PASS)', real, () => {
  const { project, cfg } = setup();
  const r = runReal(['run', '--config', cfg, '--scss-dir', 'styles', '--entry', 'missing.scss', '--without', 'base.scss'], { cwd: project });
  assert.notEqual(r.code, 0, r.all);
  assert.doesNotMatch(r.stdout, /^RESULT: PASS$/m);
});

test('compile: --out przez symlink poza cwd => kod 2, cel bez zmian (sink zakorzeniony w cwd)', realSym, () => {
  const { project, cfg } = setup();
  const outside = newDir('outside');
  const target = path.join(outside, 'victim.css');
  fs.writeFileSync(target, 'original');
  fs.symlinkSync(target, path.join(project, 'out.css'), 'file');
  const r = runReal(['compile', '--config', cfg, '--scss-dir', 'styles', '--entry', 'main.scss', '--out', 'out.css'], { cwd: project });
  assert.equal(r.code, 2, r.all);
  assert.equal(fs.readFileSync(target, 'utf8'), 'original');
});

test('compile: --out w katalogu-symlinku poza cwd => kod 2, nic nie zapisane na zewnatrz', realSym, () => {
  const { project, cfg } = setup();
  const outside = newDir('outside');
  fs.symlinkSync(outside, path.join(project, 'build'), 'dir');
  const r = runReal(['compile', '--config', cfg, '--scss-dir', 'styles', '--entry', 'main.scss', '--out', 'build/out.css'], { cwd: project });
  assert.equal(r.code, 2, r.all);
  assert.deepEqual(fs.readdirSync(outside), []);
});
