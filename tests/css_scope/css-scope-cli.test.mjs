// Proces potomny `node scripts/css-scope.mjs` (S5): naglowek, --selftest, selftest/check/diff, --json, bledy uzycia (kod 2),
// brak zaleznosci (kod 3 dla compile/run, check/diff/selftest dzialaja bez cache), masker, symlink do skryptu, import bez efektow.
// Bez sass.js i bez sieci; scenariusze z prawdziwym sass.js sa w css-scope-cli-real.test.mjs.
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const SKILL = path.join(ROOT, 'library', 'skills', 'ui-verify');
const SCRIPT = path.join(SKILL, 'scripts', 'css-scope.mjs');
const HEADER_URL = pathToFileURL(path.join(SKILL, 'scripts', 'lib', 'header.mjs')).href;

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'css-scope-cli-'));
after(() => fs.rmSync(tmpRoot, { recursive: true, force: true }));
let counter = 0;
const newDir = (label = 'd') => {
  const d = path.join(tmpRoot, `${label}-${counter++}`);
  fs.mkdirSync(d, { recursive: true });
  return d;
};

const SYMLINK_SKIP = (() => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'css-scope-cli-probe-'));
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

function cleanEnv(extra = {}) {
  const base = { ...process.env };
  for (const k of Object.keys(base)) if (k.startsWith('UI_VERIFY_') || k === 'PLAYWRIGHT_MODULE_PATH') delete base[k];
  return { ...base, UI_VERIFY_CACHE_DIR: newDir('emptycache'), ...extra };
}
function run(args, { cwd, env, script = SCRIPT } = {}) {
  const r = spawnSync(process.execPath, [script, ...args], { cwd: cwd ?? newDir('cwd'), env: env ?? cleanEnv(), encoding: 'utf8', timeout: 120000 });
  return { code: r.status, stdout: r.stdout ?? '', stderr: r.stderr ?? '', all: `${r.stdout ?? ''}${r.stderr ?? ''}`, error: r.error };
}

const EXACT = '.root-theme main[tabindex="-1"]:focus:not(:focus-visible)';
const BASE_CONFIG = {
  version: 1,
  root: '.root',
  exceptions: [
    { id: 'menu-host', kind: 'prefix', value: '.menu-host > .item', reason: 'menu rendered outside the root' },
    { id: 'theme-hook', kind: 'root-alias', value: '.root-theme', onlyPropertyPrefix: '--tok-', reason: 'design tokens only' },
    { id: 'skip-link', kind: 'exact-rule', selector: EXACT, declarations: ['outline:0!important'], reason: 'focus target without outline' },
  ],
};

// Projekt: katalog z config.json i plikami CSS; zwraca { dir, cfg }.
function project(configOver = {}, files = {}, { replace = false } = {}) {
  const dir = newDir('proj');
  const cfg = path.join(dir, 'config.json');
  fs.writeFileSync(cfg, JSON.stringify(replace ? configOver : { ...BASE_CONFIG, ...configOver }));
  for (const [name, text] of Object.entries(files)) fs.writeFileSync(path.join(dir, name), text);
  return { dir, cfg };
}
const GOOD_CSS = '.root a { color: red }\n.root .b { margin: 0 }\n';
const lines = (out) => out.split('\n');

// --- naglowek, import, symlink ---

test('naglowek css-scope.mjs przechodzi parseHeader().ok i zawiera przypiete zaleznosci oraz granice', async () => {
  assert.ok(fs.existsSync(SCRIPT), 'brak scripts/css-scope.mjs');
  const { parseHeader } = await import(HEADER_URL);
  const text = fs.readFileSync(SCRIPT, 'utf8');
  const h = parseHeader(text);
  assert.equal(h.ok, true, `brakuje: ${h.missing}, kolejnosc: ${h.order}`);
  const block = /\/\*[\s\S]*?\*\//.exec(text)[0];
  assert.match(block, /sass\.js 0\.11\.1/);
  assert.match(block, /Node >= 20\.11/);
  assert.match(block, /LibSass 3\.6/);
  assert.match(block, /!important is counted, not judged/);
});

test('import css-scope.mjs nie uruchamia main() i nic nie wypisuje (bez cache)', () => {
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', `await import(${JSON.stringify(pathToFileURL(SCRIPT).href)}); process.stdout.write('imported');`], {
    cwd: newDir('imp'), encoding: 'utf8', timeout: 60000, env: cleanEnv(),
  });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout, 'imported');
  assert.equal(r.stderr, '');
});

test('isMain: node <symlink do pliku> --selftest => wyjscie niepuste i kod 0 (nie ciche 0)', { skip: SYMLINK_SKIP }, () => {
  const link = path.join(newDir('link'), 'css-scope.mjs');
  fs.symlinkSync(SCRIPT, link, 'file');
  const r = run(['--selftest'], { script: link });
  assert.equal(r.code, 0, r.all);
  assert.match(r.stdout, /selftest css-scope: \d+\/\d+ passed/);
});

test('isMain: sciezka wzgledna z cwd => selftest sie wykonuje', () => {
  const r = run(['--selftest'], { script: path.relative(ROOT, SCRIPT), cwd: ROOT });
  assert.equal(r.code, 0, r.all);
  assert.match(r.stdout, /selftest css-scope: \d+\/\d+ passed/);
});

// --- --selftest ---

test('--selftest: kod 0 bez cache i bez sieci, format "selftest css-scope: N/N passed", dokladnie 21 przypadkow wewnetrznych', () => {
  const env = cleanEnv({ HTTP_PROXY: 'http://127.0.0.1:1', HTTPS_PROXY: 'http://127.0.0.1:1', http_proxy: 'http://127.0.0.1:1', https_proxy: 'http://127.0.0.1:1' });
  const r = run(['--selftest'], { env });
  assert.equal(r.code, 0, r.all);
  const m = /^selftest css-scope: (\d+)\/(\d+) passed$/m.exec(r.stdout);
  assert.ok(m, `brak linii podsumowania: ${r.stdout}`);
  assert.equal(m[1], m[2], 'nie wszystkie przypadki zaliczone');
  assert.equal(Number(m[2]), 21, `liczba przypadkow wewnetrznych: ${m[2]}`);
  assert.equal(r.stderr, '');
});

// --- selftest ---

test('selftest bez --config: wbudowane przypadki silnika, linie PASS i podsumowanie "selftest: N/N PASS", kod 0', () => {
  const r = run(['selftest']);
  assert.equal(r.code, 0, r.all);
  const m = /^selftest: (\d+)\/(\d+) PASS$/m.exec(r.stdout);
  assert.ok(m, r.stdout);
  assert.equal(m[1], m[2]);
  assert.equal(Number(m[2]), 19, `liczba wbudowanych przypadkow: ${m[2]}`);
  assert.match(r.stdout, /^PASS root-element \(expected accepted, got accepted\)$/m);
  assert.match(r.stdout, /^PASS element-outside-root \(expected rejected, got rejected\)$/m);
});

test('selftest --config: przypadki projektu dochodza do wbudowanych i sa liczone na konfiguracji pliku (nie wbudowanej)', () => {
  const builtin = /^selftest: (\d+)\/(\d+) PASS$/m.exec(run(['selftest']).stdout);
  const { dir, cfg } = project({
    root: '.app',
    exceptions: [],
    selftest: [
      { name: 'proj-app-root', css: '.app a { color: red }', expect: 'accepted' },
      { name: 'proj-old-root-rejected', css: '.root a { color: red }', expect: 'rejected' },
    ],
  });
  const r = run(['selftest', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 0, r.all);
  const m = /^selftest: (\d+)\/(\d+) PASS$/m.exec(r.stdout);
  assert.ok(m, r.stdout);
  assert.equal(Number(m[2]), Number(builtin[2]) + 2);
  assert.equal(m[1], m[2]);
  assert.match(r.stdout, /^PASS proj-app-root \(expected accepted, got accepted\)$/m);
  assert.match(r.stdout, /^PASS proj-old-root-rejected \(expected rejected, got rejected\)$/m);
});

test('selftest --config: niezgodny przypadek projektu => linia FAIL z nazwa i kod 1', () => {
  const { dir, cfg } = project({ selftest: [{ name: 'should-pass-but-does-not', css: 'a { color: red }', expect: 'accepted' }] });
  const r = run(['selftest', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  assert.match(r.stdout, /^FAIL should-pass-but-does-not \(expected accepted, got rejected\)$/m);
  const m = /^selftest: (\d+)\/(\d+) PASS$/m.exec(r.stdout);
  assert.ok(m, r.stdout);
  assert.equal(Number(m[1]) + 1, Number(m[2]));
});

// --- check ---

test('check: CSS w zakresie => PASS scope, kod 0, format "scope OK: N rules, all under .root (+ 3 exceptions), !important: 0"', () => {
  const { dir, cfg } = project({}, { 'a.css': GOOD_CSS });
  const r = run(['check', '--config', cfg, 'a.css'], { cwd: dir });
  assert.equal(r.code, 0, r.all);
  assert.match(r.stdout, /^PASS scope: /m);
  assert.match(r.stdout, /scope OK: 2 rules, all under \.root \(\+ 3 exceptions\), !important: 0/);
  assert.match(r.stdout, /^INFO important-count: 0$/m);
  assert.doesNotMatch(r.stdout, /^FAIL/m);
});

test('check: wszystkie trzy rodzaje wyjatkow akceptowane; nieuzyty wyjatek => WARN z id, ale kod 0', () => {
  const css = `${GOOD_CSS}.menu-host > .item a { color: red }\n.root-theme { --tok-a: 1 }\n${EXACT} { outline: 0 !important }\n`;
  const used = project({}, { 'a.css': css });
  const ok = run(['check', '--config', used.cfg, 'a.css'], { cwd: used.dir });
  assert.equal(ok.code, 0, ok.all);
  assert.doesNotMatch(ok.stdout, /^WARN/m);
  const unused = project({}, { 'a.css': GOOD_CSS });
  const r = run(['check', '--config', unused.cfg, 'a.css'], { cwd: unused.dir });
  assert.equal(r.code, 0, r.all);
  for (const id of ['menu-host', 'theme-hook', 'skip-link']) assert.match(r.stdout, new RegExp(`^WARN .*${id}`, 'm'), `brak WARN o ${id}`);
});

test('check: regula poza zakresem => FAIL scope, kod 1, lista bad zawiera selektor; wlasciwa regula nie trafia na liste', () => {
  const { dir, cfg } = project({}, { 'a.css': `${GOOD_CSS}.stray-thing a { color: red }\n` });
  const r = run(['check', '--config', cfg, 'a.css'], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  assert.match(r.stdout, /^FAIL scope: /m);
  assert.match(r.stdout, /\.stray-thing a/);
  assert.doesNotMatch(r.stdout, /scope OK/);
});

test('check: lista bad ograniczona do 50 pozycji, reszta jako "and N more"', () => {
  const css = Array.from({ length: 60 }, (_, i) => `.bad${i} a { color: red }`).join('\n');
  const { dir, cfg } = project({}, { 'a.css': css });
  const r = run(['check', '--config', cfg, 'a.css'], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  const shown = new Set(r.stdout.match(/\.bad\d+(?!\d)/g) ?? []);
  assert.equal(shown.size, 50, `pokazano ${shown.size}`);
  assert.match(r.stdout, /and 10 more/);
});

test('check --root zamiast --config: bez wyjatkow, "(+ 0 exceptions)"; poza korzeniem => FAIL kod 1', () => {
  const dir = newDir('root');
  fs.writeFileSync(path.join(dir, 'ok.css'), GOOD_CSS);
  fs.writeFileSync(path.join(dir, 'bad.css'), `${GOOD_CSS}body { margin: 0 }\n`);
  const ok = run(['--root', '.root', 'check', 'ok.css'], { cwd: dir });
  assert.equal(ok.code, 0, ok.all);
  assert.match(ok.stdout, /all under \.root \(\+ 0 exceptions\)/);
  const bad = run(['--root', '.root', 'check', 'bad.css'], { cwd: dir });
  assert.equal(bad.code, 1, bad.all);
  assert.match(bad.stdout, /^FAIL scope: /m);
  // wyjatek z konfiguracji nie dziala przy samym --root
  fs.writeFileSync(path.join(dir, 'menu.css'), '.menu-host > .item a { color: red }\n');
  assert.equal(run(['--root', '.root', 'check', 'menu.css'], { cwd: dir }).code, 1);
});

test('check bez --config i bez --root => blad uzycia, kod 2', () => {
  const dir = newDir('none');
  fs.writeFileSync(path.join(dir, 'a.css'), GOOD_CSS);
  const r = run(['check', 'a.css'], { cwd: dir });
  assert.equal(r.code, 2, r.all);
  assert.notEqual(r.stderr.trim(), '');
});

test('check: important.max przekroczone => FAIL important, kod 1; rowne max => PASS kod 0; licznik INFO zawsze', () => {
  const css = '.root a { color: red !important; margin: 0 !important }\n';
  const over = project({ important: { max: 1 } }, { 'a.css': css });
  const r = run(['check', '--config', over.cfg, 'a.css'], { cwd: over.dir });
  assert.equal(r.code, 1, r.all);
  assert.match(r.stdout, /^FAIL important: 2 > 1$/m);
  assert.match(r.stdout, /^INFO important-count: 2$/m);
  assert.match(r.stdout, /^PASS scope: /m, 'zakres jest poprawny, pada tylko important');
  const equal = project({ important: { max: 2 } }, { 'a.css': css });
  const ok = run(['check', '--config', equal.cfg, 'a.css'], { cwd: equal.dir });
  assert.equal(ok.code, 0, ok.all);
  assert.match(ok.stdout, /^INFO important-count: 2$/m);
  assert.doesNotMatch(ok.stdout, /^FAIL/m);
  const none = project({}, { 'a.css': css });
  assert.equal(run(['check', '--config', none.cfg, 'a.css'], { cwd: none.dir }).code, 0, 'bez progu !important jest tylko liczone');
});

test('check: !important w komentarzu i napisie nie jest liczony', () => {
  const css = '/* x !important */\n.root a { content: "!important"; color: red }\n';
  const { dir, cfg } = project({}, { 'a.css': css });
  const r = run(['check', '--config', cfg, 'a.css'], { cwd: dir });
  assert.equal(r.code, 0, r.all);
  assert.match(r.stdout, /^INFO important-count: 0$/m);
});

// --- diff ---

const DIFF_WITHOUT = '.root a { color: red }\n';
test('diff: nakladka tylko dodaje reguly w zakresie => PASS diff z licznikami, kod 0', () => {
  const { dir, cfg } = project({}, { 'with.css': `${DIFF_WITHOUT}.root b { margin: 0 }\n`, 'without.css': DIFF_WITHOUT });
  const r = run(['diff', '--config', cfg, 'with.css', 'without.css'], { cwd: dir });
  assert.equal(r.code, 0, r.all);
  assert.match(r.stdout, /^PASS diff: with 2, without 1; added 1 \(allowed 1\), removed 0$/m);
});

test('diff: regula bazy usunieta/zmieniona => FAIL diff, przyklad REMOVED/CHANGED, kod 1', () => {
  const { dir, cfg } = project({}, { 'with.css': '.root a { color: blue }\n', 'without.css': DIFF_WITHOUT });
  const r = run(['diff', '--config', cfg, 'with.css', 'without.css'], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  assert.match(r.stdout, /^FAIL diff: /m);
  assert.match(r.stdout, /^REMOVED\/CHANGED: .*\.root a/m);
});

test('diff: dodana regula poza korzeniem => FAIL, przyklad ADDED OUTSIDE SCOPE, kod 1; dodana wyjatkiem prefix jest dozwolona', () => {
  const bad = project({}, { 'with.css': `${DIFF_WITHOUT}.stray a { color: red }\n`, 'without.css': DIFF_WITHOUT });
  const r = run(['diff', '--config', bad.cfg, 'with.css', 'without.css'], { cwd: bad.dir });
  assert.equal(r.code, 1, r.all);
  assert.match(r.stdout, /^ADDED OUTSIDE SCOPE: .*\.stray a/m);
  const ok = project({}, { 'with.css': `${DIFF_WITHOUT}.menu-host > .item a { color: red }\n`, 'without.css': DIFF_WITHOUT });
  const good = run(['diff', '--config', ok.cfg, 'with.css', 'without.css'], { cwd: ok.dir });
  assert.equal(good.code, 0, good.all);
  assert.match(good.stdout, /added 1 \(allowed 1\)/);
});

test('diff: przykladow co najwyzej 10, kazdy w osobnej linii', () => {
  const extra = Array.from({ length: 14 }, (_, i) => `.stray${i} a { color: red }`).join('\n');
  const { dir, cfg } = project({}, { 'with.css': `${DIFF_WITHOUT}${extra}\n`, 'without.css': DIFF_WITHOUT });
  const r = run(['diff', '--config', cfg, 'with.css', 'without.css'], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  assert.equal(lines(r.stdout).filter((l) => l.startsWith('ADDED OUTSIDE SCOPE:')).length, 10);
});

test('diff: dodany @font-face jest dozwolony domyslnie, a @keyframes dopiero po diff.allowAtRules', () => {
  const kf = '@keyframes spin { from { opacity: 0 } to { opacity: 1 } }\n';
  const files = { 'with.css': `${DIFF_WITHOUT}@font-face { font-family: x; src: local(x) }\n`, 'without.css': DIFF_WITHOUT, 'with-kf.css': `${DIFF_WITHOUT}${kf}` };
  const a = project({}, files);
  assert.equal(run(['diff', '--config', a.cfg, 'with.css', 'without.css'], { cwd: a.dir }).code, 0);
  assert.equal(run(['diff', '--config', a.cfg, 'with-kf.css', 'without.css'], { cwd: a.dir }).code, 1);
  const b = project({ diff: { allowAtRules: ['font-face', 'keyframes'] } }, files);
  assert.equal(run(['diff', '--config', b.cfg, 'with-kf.css', 'without.css'], { cwd: b.dir }).code, 0);
});

test('diff bez --config => blad uzycia, kod 2', () => {
  const dir = newDir('d');
  fs.writeFileSync(path.join(dir, 'a.css'), GOOD_CSS);
  fs.writeFileSync(path.join(dir, 'b.css'), GOOD_CSS);
  const r = run(['diff', 'a.css', 'b.css'], { cwd: dir });
  assert.equal(r.code, 2, r.all);
});

// --- --json ---

function parseJson(r) {
  let obj;
  assert.doesNotThrow(() => { obj = JSON.parse(r.stdout); }, `stdout nie jest jednym obiektem JSON: ${r.stdout}`);
  assert.ok(obj && typeof obj === 'object' && !Array.isArray(obj));
  return obj;
}
function assertShape(obj, command, verdict, exit) {
  assert.equal(obj.command, command);
  assert.equal(obj.verdict, verdict);
  assert.equal(obj.exit, exit);
  assert.ok(Array.isArray(obj.checks) && obj.checks.length > 0, 'checks: niepusta tablica');
  for (const c of obj.checks) {
    assert.equal(typeof c.name, 'string');
    assert.ok(['PASS', 'FAIL', 'INFO', 'WARN'].includes(c.verdict), `verdict ${c.verdict}`);
    assert.equal(typeof c.detail, 'string');
  }
  assert.ok(obj.counts && typeof obj.counts === 'object' && !Array.isArray(obj.counts));
}

test('--json check: jeden obiekt {command, verdict, exit, checks, counts}; PASS i FAIL z kodami procesu', () => {
  const ok = project({}, { 'a.css': GOOD_CSS });
  const r = run(['--json', 'check', '--config', ok.cfg, 'a.css'], { cwd: ok.dir });
  assert.equal(r.code, 0, r.all);
  const obj = parseJson(r);
  assertShape(obj, 'check', 'PASS', 0);
  assert.ok(obj.checks.some((c) => c.name === 'scope' && c.verdict === 'PASS'));
  assert.equal(obj.counts.rules, 2);
  assert.equal(obj.counts.important, 0);
  const bad = project({}, { 'a.css': 'a { color: red }\n' });
  const f = run(['--json', 'check', '--config', bad.cfg, 'a.css'], { cwd: bad.dir });
  assert.equal(f.code, 1, f.all);
  assertShape(parseJson(f), 'check', 'FAIL', 1);
  assert.ok(parseJson(f).checks.some((c) => c.name === 'scope' && c.verdict === 'FAIL'));
});

test('--json diff: kształt obiektu i liczniki', () => {
  const { dir, cfg } = project({}, { 'with.css': `${DIFF_WITHOUT}.root b { margin: 0 }\n`, 'without.css': DIFF_WITHOUT });
  const r = run(['--json', 'diff', '--config', cfg, 'with.css', 'without.css'], { cwd: dir });
  assert.equal(r.code, 0, r.all);
  const obj = parseJson(r);
  assertShape(obj, 'diff', 'PASS', 0);
  assert.ok(obj.checks.some((c) => c.name === 'diff' && c.verdict === 'PASS'));
  assert.deepEqual({ with: obj.counts.with, without: obj.counts.without, added: obj.counts.added, removed: obj.counts.removed }, { with: 2, without: 1, added: 1, removed: 0 });
});

test('--json selftest: obiekt z przypadkami jako checks; niezgodny przypadek => FAIL, exit 1', () => {
  const ok = run(['--json', 'selftest']);
  assert.equal(ok.code, 0, ok.all);
  const obj = parseJson(ok);
  assertShape(obj, 'selftest', 'PASS', 0);
  assert.equal(obj.checks.length, 19, 'wbudowane przypadki jako checks');
  assert.ok(obj.checks.every((c) => c.verdict === 'PASS'));
  const { dir, cfg } = project({ selftest: [{ name: 'wrong-expectation', css: 'a { color: red }', expect: 'accepted' }] });
  const bad = run(['--json', 'selftest', '--config', cfg], { cwd: dir });
  assert.equal(bad.code, 1, bad.all);
  const badObj = parseJson(bad);
  assertShape(badObj, 'selftest', 'FAIL', 1);
  assert.ok(badObj.checks.some((c) => c.verdict === 'FAIL' && c.name.includes('wrong-expectation')));
});

// --- bledy uzycia i konfiguracji: kod 2, jedna linia bez sladu stosu ---

function assertUsageError(r, pattern) {
  assert.equal(r.code, 2, r.all);
  assert.notEqual(r.stderr.trim(), '', 'blad powinien byc na stderr');
  assert.doesNotMatch(r.stderr, /\n\s+at .*\(.*:\d+:\d+\)/, `slad stosu na stderr: ${r.stderr}`);
  if (pattern) assert.match(r.stderr, pattern);
}

test('nieznana flaga => kod 2 z nazwa flagi', () => {
  assertUsageError(run(['--bogus-flag', 'selftest']), /bogus-flag/);
});

test('brak polecenia (same flagi) => kod 2; nieznane polecenie => kod 2', () => {
  assertUsageError(run([]));
  assertUsageError(run(['--json']));
  assertUsageError(run(['frobnicate']), /frobnicate/);
});

test('check bez argumentu pliku => kod 2', () => {
  assertUsageError(run(['--root', '.root', 'check']));
});

test('konfiguracja: nieznany klucz => kod 2 z nazwa klucza', () => {
  const { dir, cfg } = project({ surprise: 1 }, { 'a.css': GOOD_CSS });
  assertUsageError(run(['check', '--config', cfg, 'a.css'], { cwd: dir }), /surprise/);
});

test('konfiguracja: wyjatek bez reason => kod 2 z "reason"; dotyczy tez selftest', () => {
  const exceptions = [{ id: 'no-reason', kind: 'prefix', value: '.x > .y' }];
  const { dir, cfg } = project({ exceptions }, { 'a.css': GOOD_CSS });
  assertUsageError(run(['check', '--config', cfg, 'a.css'], { cwd: dir }), /reason/);
  assertUsageError(run(['selftest', '--config', cfg], { cwd: dir }), /reason/);
});

test('konfiguracja: niepoprawny JSON, brak pliku i out poza katalogiem konfiguracji => kod 2', () => {
  const dir = newDir('badcfg');
  fs.writeFileSync(path.join(dir, 'broken.json'), '{ not json');
  fs.writeFileSync(path.join(dir, 'a.css'), GOOD_CSS);
  assertUsageError(run(['check', '--config', 'broken.json', 'a.css'], { cwd: dir }));
  assertUsageError(run(['check', '--config', 'missing.json', 'a.css'], { cwd: dir }));
  const escape = project({ out: '../escape.css' });
  assertUsageError(run(['selftest', '--config', escape.cfg], { cwd: escape.dir }), /out/);
});

test('CssParseError: kod 2 i numer linii w komunikacie', () => {
  const { dir, cfg } = project({}, { 'a.css': '.root a { color: red }\n\n}\n' });
  const r = run(['check', '--config', cfg, 'a.css'], { cwd: dir });
  assertUsageError(r, /line 3/);
});

test('plik CSS: nie istnieje, nie jest zwyklym plikiem, wiekszy niz 20 MB => kod 2', () => {
  const { dir, cfg } = project({}, {});
  assertUsageError(run(['check', '--config', cfg, 'missing.css'], { cwd: dir }));
  fs.mkdirSync(path.join(dir, 'folder.css'));
  assertUsageError(run(['check', '--config', cfg, 'folder.css'], { cwd: dir }));
  const big = path.join(dir, 'big.css');
  fs.writeFileSync(big, '');
  fs.truncateSync(big, 20 * 1024 * 1024 + 1);
  assertUsageError(run(['check', '--config', cfg, 'big.css'], { cwd: dir }));
  const exact = path.join(dir, 'exact.css');
  fs.writeFileSync(exact, '.root a { color: red }\n');
  fs.truncateSync(exact, 20 * 1024 * 1024);
  const r = run(['check', '--config', cfg, 'exact.css'], { cwd: dir });
  assert.notEqual(r.code, 2, `plik dokladnie 20 MB nie jest "zbyt duzy": ${r.all}`);
});

// --- brak zaleznosci i cache ---

function scssProject() {
  const { dir, cfg } = project({ scssDir: 'scss', entry: 'main.scss' });
  fs.mkdirSync(path.join(dir, 'scss'));
  fs.writeFileSync(path.join(dir, 'scss', 'main.scss'), '.root a { color: red }\n');
  return { dir, cfg };
}

test('brak sass.js w cache: compile => kod 3 z instrukcja "node install.mjs"; nic nie zapisuje', () => {
  const { dir, cfg } = scssProject();
  const r = run(['compile', '--config', cfg, '--out', 'build/out.css'], { cwd: dir });
  assert.equal(r.code, 3, r.all);
  assert.match(r.all, /install\.mjs/);
  assert.equal(fs.existsSync(path.join(dir, 'build')), false);
});

test('brak sass.js w cache: run => kod 3, kontrola selftest wypisana przed NOT_RUN, brak RESULT: PASS', () => {
  const { dir, cfg } = scssProject();
  const r = run(['run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 3, r.all);
  assert.match(r.stdout, /^PASS selftest/m);
  assert.doesNotMatch(r.stdout, /RESULT: PASS/);
  assert.match(r.all, /install\.mjs/);
});

test('--json run bez sass.js: verdict NOT_RUN, exit 3', () => {
  const { dir, cfg } = scssProject();
  const r = run(['--json', 'run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 3, r.all);
  const obj = parseJson(r);
  assert.equal(obj.command, 'run');
  assert.equal(obj.verdict, 'NOT_RUN');
  assert.equal(obj.exit, 3);
});

test('check, diff i selftest dzialaja bez cache (puste UI_VERIFY_CACHE_DIR) i nie tworza w nim plikow', () => {
  const cacheDir = newDir('nocache');
  const env = cleanEnv({ UI_VERIFY_CACHE_DIR: cacheDir });
  const { dir, cfg } = project({}, { 'a.css': GOOD_CSS, 'b.css': GOOD_CSS });
  assert.equal(run(['check', '--config', cfg, 'a.css'], { cwd: dir, env }).code, 0);
  assert.equal(run(['diff', '--config', cfg, 'a.css', 'b.css'], { cwd: dir, env }).code, 0);
  assert.equal(run(['selftest', '--config', cfg], { cwd: dir, env }).code, 0);
  assert.deepEqual(fs.readdirSync(cacheDir), []);
});

test('wzgledny UI_VERIFY_CACHE_DIR => kod 2 dla compile i run (nie 3)', () => {
  const { dir, cfg } = scssProject();
  for (const cmd of ['compile', 'run']) {
    const r = run([cmd, '--config', cfg], { cwd: dir, env: cleanEnv({ UI_VERIFY_CACHE_DIR: 'wzgledny/cache' }) });
    assert.equal(r.code, 2, `${cmd}: ${r.all}`);
  }
});

test('compile/run bez scssDir lub entry w konfiguracji => kod 2', () => {
  const { dir, cfg } = project({}, {});
  for (const cmd of ['compile', 'run']) assertUsageError(run([cmd, '--config', cfg], { cwd: dir }));
});

// --- B1: FAIL wygrywa nad NOT_RUN ---

test('run bez sass.js, ale z niezgodnym przypadkiem selftestu => FAIL, kod 1 (nie 3), RESULT: FAIL', () => {
  const { dir, cfg } = project({ scssDir: 'scss', entry: 'main.scss', selftest: [{ name: 'wrong-expectation', css: 'body { margin: 0 }', expect: 'accepted' }] });
  fs.mkdirSync(path.join(dir, 'scss'));
  fs.writeFileSync(path.join(dir, 'scss', 'main.scss'), '.root a { color: red }\n');
  const r = run(['run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  assert.match(r.stdout, /^FAIL selftest.*wrong-expectation/m);
  assert.equal(r.stdout.trimEnd().split('\n').pop(), 'RESULT: FAIL');
  assert.doesNotMatch(r.stdout, /NOT_RUN/);
});

test('--json run bez sass.js z niezgodnym selftestem: verdict FAIL, exit 1 (FAIL wygrywa nad NOT_RUN)', () => {
  const { dir, cfg } = project({ scssDir: 'scss', entry: 'main.scss', selftest: [{ name: 'wrong-expectation', css: 'body { margin: 0 }', expect: 'accepted' }] });
  fs.mkdirSync(path.join(dir, 'scss'));
  fs.writeFileSync(path.join(dir, 'scss', 'main.scss'), '.root a { color: red }\n');
  const r = run(['--json', 'run', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  const obj = parseJson(r);
  assert.equal(obj.verdict, 'FAIL');
  assert.equal(obj.exit, 1);
  assert.ok(obj.checks.some((c) => c.name === 'selftest' && c.verdict === 'FAIL'));
});

// --- A5: uszkodzony CSS w przypadku selftestu ---

test('selftest --config: przypadek z uszkodzonym CSS => FAIL "got error: <komunikat>", kod 1, niezaleznie od expect', () => {
  for (const expect of ['rejected', 'accepted']) {
    const { dir, cfg } = project({ selftest: [{ name: 'broken-case', css: '.root a {', expect }] });
    const r = run(['selftest', '--config', cfg], { cwd: dir });
    assert.equal(r.code, 1, `${expect}: ${r.all}`);
    assert.match(r.stdout, /^FAIL broken-case \(.*got error: .+\)$/m, r.stdout);
    assert.doesNotMatch(r.stdout, /^PASS broken-case/m);
  }
});

test('--json selftest: przypadek z uszkodzonym CSS => FAIL, exit 1, detail zawiera "got error"', () => {
  const { dir, cfg } = project({ selftest: [{ name: 'broken-case', css: '.root a{content:"x\n}', expect: 'rejected' }] });
  const r = run(['--json', 'selftest', '--config', cfg], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  const obj = parseJson(r);
  const c = obj.checks.find((x) => x.name === 'broken-case');
  assert.ok(c, r.stdout);
  assert.equal(c.verdict, 'FAIL');
  assert.match(c.detail, /got error/);
});

// --- A1/A3 przez CLI ---

test('check: regula zagniezdzona => FAIL scope, kod 1, wpis "nested rule is not supported"', () => {
  const { dir, cfg } = project({}, { 'a.css': '.root a { & + b { color: red } }\n' });
  const r = run(['check', '--config', cfg, 'a.css'], { cwd: dir });
  assert.equal(r.code, 1, r.all);
  assert.match(r.stdout, /^FAIL scope: /m);
  assert.match(r.stdout, /nested rule is not supported/);
});

test('check: napis z nowa linia (`content:"x<LF>}body{color:red}`) => kod 2 (CSS parse error, unterminated string), nie PASS', () => {
  const { dir, cfg } = project({}, { 'a.css': '.root a{content:"x\n}body{color:red}\n' });
  const r = run(['check', '--config', cfg, 'a.css'], { cwd: dir });
  assert.equal(r.code, 2, r.all);
  assert.match(r.stderr, /CSS parse error/i);
  assert.match(r.stderr, /string/i);
});

// --- B8: BOM i rozmiar pliku konfiguracji ---

test('konfiguracja z BOM UTF-8 jest przyjmowana (check => kod 0)', () => {
  const dir = newDir('bom');
  fs.writeFileSync(path.join(dir, 'config.json'), Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from(JSON.stringify({ version: 1, root: '.root' }))]));
  fs.writeFileSync(path.join(dir, 'a.css'), GOOD_CSS);
  const r = run(['check', '--config', 'config.json', 'a.css'], { cwd: dir });
  assert.equal(r.code, 0, r.all);
});

test('konfiguracja wieksza niz 1 000 000 B => kod 2 z komunikatem o rozmiarze; rowna limitowi przechodzi', () => {
  const dir = newDir('bigcfg');
  const head = JSON.stringify({ version: 1, root: '.root' });
  fs.writeFileSync(path.join(dir, 'a.css'), GOOD_CSS);
  fs.writeFileSync(path.join(dir, 'exact.json'), head + ' '.repeat(1000000 - head.length));
  assert.equal(fs.statSync(path.join(dir, 'exact.json')).size, 1000000);
  assert.equal(run(['check', '--config', 'exact.json', 'a.css'], { cwd: dir }).code, 0);
  fs.writeFileSync(path.join(dir, 'big.json'), head + ' '.repeat(1000001 - head.length));
  const r = run(['check', '--config', 'big.json', 'a.css'], { cwd: dir });
  assertUsageError(r, /larger|too large|exceed|limit/i);
});

test('konfiguracja ogromna (600 MiB, plik rzadki): odrzucona na podstawie stat PRZED odczytem => kod 2 z komunikatem o rozmiarze, nie ERR_STRING_TOO_LONG', (t) => {
  const dir = newDir('hugecfg');
  const file = path.join(dir, 'huge.json');
  try {
    fs.writeFileSync(file, '{}');
    fs.truncateSync(file, 600 * 1024 * 1024);
  } catch (e) {
    t.skip(`needs-sparse-files: ${e.code || e.message}`);
    return;
  }
  fs.writeFileSync(path.join(dir, 'a.css'), GOOD_CSS);
  const started = Date.now();
  const r = run(['check', '--config', 'huge.json', 'a.css'], { cwd: dir });
  assertUsageError(r, /larger|too large|exceed|limit/i);
  assert.doesNotMatch(r.all, /ERR_STRING_TOO_LONG|Cannot create a string/i);
  assert.ok(Date.now() - started < 60000, 'odczyt calego pliku zamiast stat');
});

// --- masker ---

const fakeSecret = () => ['cssscope', 'secret', String(process.pid), 'wxyz'].join('-');

test('masker: sekret z UI_VERIFY_SECRET_ENV w nazwie pliku nie trafia na stdout/stderr (kod 2, zamaskowane ***)', () => {
  const secret = fakeSecret();
  const { dir, cfg } = project({}, {});
  const r = run(['check', '--config', cfg, `${secret}.css`], { cwd: dir, env: cleanEnv({ UV_CSS_SECRET: secret, UI_VERIFY_SECRET_ENV: 'UV_CSS_SECRET' }) });
  assert.equal(r.code, 2, r.all);
  assert.ok(!r.all.includes(secret), `sekret w wyjsciu: ${r.all}`);
  assert.match(r.stderr, /\*\*\*/);
});

test('masker, kontrola: bez deklaracji w UI_VERIFY_SECRET_ENV ta sama nazwa pliku jest widoczna (test wycieku potrafi paść)', () => {
  const secret = fakeSecret();
  const { dir, cfg } = project({}, {});
  const r = run(['check', '--config', cfg, `${secret}.css`], { cwd: dir, env: cleanEnv({ UV_CSS_SECRET: secret }) });
  assert.equal(r.code, 2, r.all);
  assert.ok(r.all.includes(secret), `kontrola: nazwa pliku powinna byc widoczna: ${r.all}`);
});

test('masker: nazwa pliku z sekretem w udanym check nie wycieka (plik istnieje, wynik PASS)', () => {
  const secret = fakeSecret();
  const { dir, cfg } = project({}, { [`${secret}.css`]: GOOD_CSS });
  const r = run(['--json', 'check', '--config', cfg, `${secret}.css`], { cwd: dir, env: cleanEnv({ UV_CSS_SECRET: secret, UI_VERIFY_SECRET_ENV: 'UV_CSS_SECRET' }) });
  assert.equal(r.code, 0, r.all);
  assert.ok(!r.all.includes(secret), `sekret w wyjsciu: ${r.all}`);
});

// --- rdzen bez nazw projektu/klienta ---

test('skrypt i biblioteka css-* nie zawieraja nazw projektu/klienta, sciezek domowych ani e-maili', () => {
  const frag = (...codes) => String.fromCharCode(...codes);
  const forbidden = [
    frag(47, 104, 111, 109, 101, 47), // katalog domowy
    frag(64, 98, 97, 116, 117), // fragment adresu e-mail firmy
    frag(98, 97, 116, 117, 115, 121, 115, 116, 101, 109, 115), // nazwa organizacji
    frag(112, 99, 103, 97, 99, 97, 100, 101, 109, 121), // nazwa klienta
  ];
  const lib = path.join(SKILL, 'scripts', 'lib');
  const files = [SCRIPT, ...fs.readdirSync(lib).filter((f) => f.startsWith('css-')).map((f) => path.join(lib, f))];
  assert.ok(fs.existsSync(SCRIPT), 'brak scripts/css-scope.mjs');
  for (const f of files) {
    const text = fs.readFileSync(f, 'utf8').toLowerCase();
    for (const word of forbidden) assert.ok(!text.includes(word), `${path.basename(f)} zawiera zabroniony fragment`);
  }
  assert.doesNotMatch(fs.readFileSync(SCRIPT, 'utf8'), /\bimport\s*['"]/, 'literal import tuz przed cudzyslowem (straznik)');
});
