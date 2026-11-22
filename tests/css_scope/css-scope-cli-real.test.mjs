// Proces potomny `node scripts/css-scope.mjs` z prawdziwym sass.js (LibSass) z cache testowego: compile i run end-to-end.
// Bez UI_VERIFY_TEST_CACHE (albo bez sass.js w nim) kazdy test jest pomijany z powodem needs-ui-verify-deps.
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
const opts = { skip: cache.reason ?? false };

const SYMLINK_SKIP = (() => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'css-scope-real-probe-'));
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
const symOpts = { skip: cache.reason ?? SYMLINK_SKIP };

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'css-scope-cli-real-'));
after(() => fs.rmSync(tmpRoot, { recursive: true, force: true }));
let counter = 0;
const newDir = (label = 'd') => {
  const d = path.join(tmpRoot, `${label}-${counter++}`);
  fs.mkdirSync(d, { recursive: true });
  return d;
};

function run(args, { cwd, env = {} } = {}) {
  const base = { ...process.env };
  for (const k of Object.keys(base)) if (k.startsWith('UI_VERIFY_') || k === 'PLAYWRIGHT_MODULE_PATH') delete base[k];
  const r = spawnSync(process.execPath, [SCRIPT, ...args], {
    cwd: cwd ?? newDir('cwd'),
    env: { ...base, UI_VERIFY_CACHE_DIR: cache.root, ...env },
    encoding: 'utf8',
    timeout: 120000,
  });
  return { code: r.status, stdout: r.stdout ?? '', stderr: r.stderr ?? '', all: `${r.stdout ?? ''}${r.stderr ?? ''}` };
}

const EXACT = '.root-theme main[tabindex="-1"]:focus:not(:focus-visible)';
const VARS = '$brand: #336699;\n$size: 14px;\n';
const BASE = '@import \'vars\';\n.root a { color: $brand }\n.root p { margin: 0 }\n';
const MAIN = `${BASE}
.root {
  .title {
    font-size: $size;
    &:hover { color: red }
  }
  .list > li { padding: 0 }
}
.root-theme { --tok-brand: #336699; --tok-size: 14px }
.menu-host > .item { display: block }
${EXACT} { outline: 0 !important }
`;

const CONFIG = {
  version: 1,
  root: '.root',
  scssDir: 'scss',
  entry: 'main.scss',
  without: 'base.scss',
  exceptions: [
    { id: 'menu-host', kind: 'prefix', value: '.menu-host > .item', reason: 'menu rendered outside the root' },
    { id: 'theme-hook', kind: 'root-alias', value: '.root-theme', onlyPropertyPrefix: '--tok-', reason: 'design tokens only' },
    { id: 'skip-link', kind: 'exact-rule', selector: EXACT, declarations: ['outline:0!important'], reason: 'focus target without outline' },
  ],
  selftest: [
    { name: 'proj-root-link', css: '.root a:hover { color: red }', expect: 'accepted' },
    { name: 'proj-body-rejected', css: 'body { margin: 0 }', expect: 'rejected' },
  ],
  important: { max: 5 },
};

function scssProject({ main = MAIN, base = BASE, config = {}, drop = [] } = {}) {
  const dir = newDir('proj');
  const scss = path.join(dir, 'scss');
  fs.mkdirSync(scss);
  fs.writeFileSync(path.join(scss, '_vars.scss'), VARS);
  fs.writeFileSync(path.join(scss, 'base.scss'), base);
  fs.writeFileSync(path.join(scss, 'main.scss'), main);
  const cfgObj = { ...CONFIG, ...config };
  for (const k of drop) delete cfgObj[k];
  const cfg = path.join(dir, 'css-scope.json');
  fs.writeFileSync(cfg, JSON.stringify(cfgObj));
  return { dir, cfg, scss };
}
const parseJson = (r) => {
  let obj;
  assert.doesNotThrow(() => { obj = JSON.parse(r.stdout); }, `stdout nie jest jednym obiektem JSON: ${r.stdout}`);
  return obj;
};

// --- compile ---

test('compile: zapisuje skompilowany CSS pod --out (wzgledem biezacego katalogu), PASS compile, kod 0', opts, () => {
  const { dir, cfg } = scssProject();
  const r = run(['compile', '--config', cfg, '--out', 'build/out.css'], { cwd: dir });
  assert.equal(r.code, 0, r.all);
  assert.match(r.stdout, /^PASS compile: main\.scss -> \d+ chars$/m);
  const css = fs.readFileSync(path.join(dir, 'build', 'out.css'), 'utf8');
  assert.match(css, /\.root a/);
  assert.match(css, /#336699/i, 'zmienna SCSS (z czesciowego pliku) rozwinieta');
  assert.match(css, /\.root \.title/, 'zagniezdzenie rozwiniete');
  assert.doesNotMatch(css, /\$brand/);
});

test('compile: bez --out zapis do config.out, gdy ustawione', opts, () => {
  const { dir, cfg } = scssProject({ config: { out: 'dist/styles.css' } });
  const r = run(['compile', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 0, r.all);
  assert.ok(fs.existsSync(path.join(dir, 'dist', 'styles.css')));
});

test('compile: blad SCSS => FAIL compile z nazwa pliku, kod 1 (nie 2), bez zapisu', opts, () => {
  const { dir, cfg } = scssProject({ main: '.root { .a { color: $undefined-variable } }\n' });
  const r = run(['compile', '--config', cfg, '--out', 'build/out.css'], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  assert.match(r.stdout, /^FAIL compile: /m);
  assert.match(r.all, /main\.scss/);
  assert.equal(fs.existsSync(path.join(dir, 'build', 'out.css')), false);
});

test('compile: --out poza katalogiem konfiguracji (".." lub bezwzgledna) => kod 2, nic nie zapisane', opts, () => {
  const { dir, cfg } = scssProject();
  const outside = newDir('outside');
  for (const out of ['../escaped.css', path.join(outside, 'abs.css')]) {
    const r = run(['compile', '--config', cfg, '--out', out], { cwd: dir });
    assert.equal(r.code, 2, `${out}: ${r.all}`);
    assert.notEqual(r.stderr.trim(), '');
  }
  assert.equal(fs.existsSync(path.join(dir, '..', 'escaped.css')), false);
  assert.deepEqual(fs.readdirSync(outside), []);
});

test('compile: wiszacy symlink w katalogu wyjscia => kod 2, plik docelowy poza korzeniem nie powstaje', symOpts, () => {
  const { dir, cfg } = scssProject();
  const outside = newDir('outside');
  const target = path.join(outside, 'created.css');
  fs.mkdirSync(path.join(dir, 'build'));
  fs.symlinkSync(target, path.join(dir, 'build', 'out.css'), 'file');
  const r = run(['compile', '--config', cfg, '--out', 'build/out.css'], { cwd: dir });
  assert.equal(r.code, 2, r.all);
  assert.equal(fs.existsSync(target), false, 'zapis przez wiszacy symlink poza korzen');
});

test('compile: zwykly symlink do pliku poza korzeniem => kod 2, zawartosc celu bez zmian', symOpts, () => {
  const { dir, cfg } = scssProject();
  const outside = newDir('outside');
  const target = path.join(outside, 'victim.css');
  fs.writeFileSync(target, 'original');
  fs.mkdirSync(path.join(dir, 'build'));
  fs.symlinkSync(target, path.join(dir, 'build', 'out.css'), 'file');
  const r = run(['compile', '--config', cfg, '--out', 'build/out.css'], { cwd: dir });
  assert.equal(r.code, 2, r.all);
  assert.equal(fs.readFileSync(target, 'utf8'), 'original');
});

test('compile: katalog wyjscia bedacy symlinkiem poza korzen => kod 2, nic nie zapisane na zewnatrz', symOpts, () => {
  const { dir, cfg } = scssProject();
  const outside = newDir('outside');
  fs.symlinkSync(outside, path.join(dir, 'build'), 'dir');
  const r = run(['compile', '--config', cfg, '--out', 'build/out.css'], { cwd: dir });
  assert.equal(r.code, 2, r.all);
  assert.deepEqual(fs.readdirSync(outside), []);
});

// --- run ---

const idx = (text, name) => text.search(new RegExp(`^(?:PASS|FAIL|INFO|WARN) ${name}\\b`, 'm'));

test('run end-to-end: wszystkie kontrole PASS, ostatnia linia RESULT: PASS, kod 0', opts, () => {
  const { dir, cfg } = scssProject();
  const r = run(['run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 0, r.all);
  for (const name of ['selftest', 'compile', 'scope', 'diff']) assert.match(r.stdout, new RegExp(`^PASS ${name}\\b`, 'm'), `brak PASS ${name}\n${r.stdout}`);
  assert.match(r.stdout, /^(?:PASS|INFO) important\b/m);
  assert.doesNotMatch(r.stdout, /^FAIL/m);
  assert.equal(r.stdout.trimEnd().split('\n').pop(), 'RESULT: PASS');
  assert.equal(fs.existsSync(path.join(dir, 'build')), false, 'bez --out i config.out nic nie jest zapisywane');
});

test('run: kolejnosc linii kontroli selftest, compile, scope, important, diff', opts, () => {
  const { dir, cfg } = scssProject();
  const r = run(['run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 0, r.all);
  const order = ['selftest', 'compile', 'scope', 'important', 'diff'].map((n) => idx(r.stdout, n));
  assert.ok(order.every((i) => i >= 0), `brak linii: ${order}\n${r.stdout}`);
  assert.deepEqual(order, [...order].sort((a, b) => a - b), `zla kolejnosc: ${order}`);
  assert.ok(r.stdout.indexOf('RESULT:') > order[4]);
});

test('run: selftest obejmuje przypadki projektu (config.selftest) i wbudowane', opts, () => {
  const { dir, cfg } = scssProject();
  const r = run(['run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 0, r.all);
  assert.match(r.stdout, /^PASS selftest/m);
  const bad = scssProject({ config: { selftest: [{ name: 'wrong-expectation', css: 'body { margin: 0 }', expect: 'accepted' }] } });
  const f = run(['run', '--config', bad.cfg], { cwd: bad.dir });
  assert.equal(f.code, 1, f.all);
  assert.match(f.stdout, /^FAIL selftest.*wrong-expectation/m);
  assert.equal(f.stdout.trimEnd().split('\n').pop(), 'RESULT: FAIL');
});

test('run: zapis CSS pod --out, gdy podane; zawartosc przechodzi check bez bledu', opts, () => {
  const { dir, cfg } = scssProject();
  const r = run(['run', '--config', cfg, '--out', 'build/out.css'], { cwd: dir });
  assert.equal(r.code, 0, r.all);
  const outFile = path.join(dir, 'build', 'out.css');
  assert.ok(fs.existsSync(outFile));
  const c = run(['check', '--config', cfg, outFile], { cwd: dir });
  assert.equal(c.code, 0, c.all);
  assert.match(c.stdout, /scope OK: \d+ rules, all under \.root \(\+ 3 exceptions\), !important: 1/);
});

test('run: regula poza korzeniem w SCSS => FAIL scope z selektorem, RESULT: FAIL, kod 1', opts, () => {
  const { dir, cfg } = scssProject({ main: `${MAIN}\n.stray-widget a { color: red }\n` });
  const r = run(['run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  assert.match(r.stdout, /^FAIL scope\b/m);
  assert.match(r.stdout, /\.stray-widget a/);
  assert.match(r.stdout, /^PASS compile\b/m);
  assert.equal(r.stdout.trimEnd().split('\n').pop(), 'RESULT: FAIL');
});

test('run: nakladka usuwa regule bazy => FAIL diff z REMOVED/CHANGED, scope nadal PASS, kod 1', opts, () => {
  const main = `${VARS.replace(/^/, '')}.root { .title { font-size: 14px } }\n.root a { color: green }\n.root p { margin: 0 }\n`;
  const { dir, cfg } = scssProject({ main: `@import 'vars';\n${main}` });
  const r = run(['run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  assert.match(r.stdout, /^PASS scope\b/m);
  assert.match(r.stdout, /^FAIL diff\b/m);
  assert.match(r.stdout, /^REMOVED\/CHANGED: .*\.root a/m);
  assert.equal(r.stdout.trimEnd().split('\n').pop(), 'RESULT: FAIL');
});

test('run: important.max przekroczone => FAIL important, RESULT: FAIL, kod 1', opts, () => {
  const { dir, cfg } = scssProject({ config: { important: { max: 0 } } });
  const r = run(['run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  assert.match(r.stdout, /^FAIL important\b.*1 > 0/m);
  assert.equal(r.stdout.trimEnd().split('\n').pop(), 'RESULT: FAIL');
});

test('run bez "without": brak kontroli diff, reszta PASS, kod 0', opts, () => {
  const { dir, cfg } = scssProject({ drop: ['without'] });
  const r = run(['run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 0, r.all);
  assert.equal(idx(r.stdout, 'diff'), -1, `linia diff mimo braku without:\n${r.stdout}`);
  assert.equal(r.stdout.trimEnd().split('\n').pop(), 'RESULT: PASS');
});

test('run: blad SCSS => FAIL compile, kod 1, brak kontroli scope/diff', opts, () => {
  const { dir, cfg } = scssProject({ main: '.root { a { color: $nope } }\n' });
  const r = run(['run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  assert.match(r.stdout, /^FAIL compile\b/m);
  assert.match(r.all, /main\.scss/);
  assert.match(r.stdout, /^RESULT: FAIL$/m);
});

test('--json run: obiekt {command, verdict, exit, checks, counts} z kontrolami w kolejnosci; FAIL daje exit 1', opts, () => {
  const { dir, cfg } = scssProject();
  const r = run(['--json', 'run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 0, r.all);
  const obj = parseJson(r);
  assert.equal(obj.command, 'run');
  assert.equal(obj.verdict, 'PASS');
  assert.equal(obj.exit, 0);
  assert.ok(obj.counts && typeof obj.counts === 'object');
  const names = obj.checks.map((c) => c.name);
  const pos = ['selftest', 'compile', 'scope', 'important', 'diff'].map((n) => names.indexOf(n));
  assert.ok(pos.every((i) => i >= 0), `brak kontroli w ${names}`);
  assert.deepEqual(pos, [...pos].sort((a, b) => a - b));
  for (const c of obj.checks) assert.ok(['PASS', 'FAIL', 'INFO', 'WARN'].includes(c.verdict));
  const bad = scssProject({ main: `${MAIN}\n.stray-widget a { color: red }\n` });
  const f = run(['--json', 'run', '--config', bad.cfg], { cwd: bad.dir });
  assert.equal(f.code, 1, f.all);
  const fo = parseJson(f);
  assert.equal(fo.verdict, 'FAIL');
  assert.equal(fo.exit, 1);
  assert.ok(fo.checks.some((c) => c.name === 'scope' && c.verdict === 'FAIL'));
});

test('--json compile: obiekt z kontrola compile', opts, () => {
  const { dir, cfg } = scssProject();
  const r = run(['--json', 'compile', '--config', cfg, '--out', 'build/out.css'], { cwd: dir });
  assert.equal(r.code, 0, r.all);
  const obj = parseJson(r);
  assert.equal(obj.command, 'compile');
  assert.equal(obj.verdict, 'PASS');
  assert.ok(obj.checks.some((c) => c.name === 'compile' && c.verdict === 'PASS'));
});

test('--json compile i run: @debug i @warn w SCSS nie psuja wyjscia (stdout to dokladnie jeden dokument JSON)', opts, () => {
  const noisy = `@debug "debug-marker-text";\n@warn "warn-marker-text";\n${BASE}`;
  for (const cmd of ['compile', 'run']) {
    const { dir, cfg } = scssProject({ main: noisy, base: noisy });
    const r = run(['--json', cmd, '--config', cfg], { cwd: dir });
    assert.equal(r.code, 0, `${cmd}: ${r.all}`);
    const obj = parseJson(r);
    assert.equal(obj.command, cmd);
    assert.equal(obj.verdict, 'PASS');
    assert.equal(r.stdout.trimEnd().split('\n').length, 1, `${cmd}: stdout ma wiecej niz jedna linie:\n${r.stdout}`);
    assert.doesNotMatch(r.stdout.replace(/^\{.*\}\n?$/s, ''), /marker-text/, `${cmd}: smieci poza dokumentem JSON`);
  }
});

test('run: niezgodny selftest przy zainstalowanym sass.js => FAIL/1 jak dotad (kontrola dla FAIL wygrywa nad NOT_RUN)', opts, () => {
  const { dir, cfg } = scssProject({ config: { selftest: [{ name: 'wrong-expectation', css: 'body { margin: 0 }', expect: 'accepted' }] } });
  const r = run(['--json', 'run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  assert.equal(parseJson(r).verdict, 'FAIL');
});

test('run: wynik z exact-rule, aliasem tokenow i prefiksem przechodzi diff (A7: spojna ocena z check)', opts, () => {
  const { dir, cfg } = scssProject();
  const r = run(['--json', 'run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 0, r.all);
  const obj = parseJson(r);
  assert.equal(obj.checks.find((c) => c.name === 'diff').verdict, 'PASS');
  assert.ok(obj.counts.added >= 3, JSON.stringify(obj.counts));
  assert.equal(obj.counts.addedAllowed, obj.counts.added);
});

test('run: alias korzenia z deklaracja spoza prefiksu tokenow w nakladce => FAIL diff (A7)', opts, () => {
  const { dir, cfg } = scssProject({ main: `${MAIN}\n.root-theme { color: red }\n` });
  const r = run(['run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  assert.match(r.stdout, /^FAIL scope\b/m);
  assert.match(r.stdout, /^FAIL diff\b/m);
  assert.match(r.stdout, /^ADDED OUTSIDE SCOPE: .*\.root-theme/m);
});
