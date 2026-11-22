// compileScss na prawdziwym sass.js (LibSass) z cache testowego; bez cache pomijane z powodem needs-ui-verify-deps.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const COMPILE_URL = '../../library/skills/ui-verify/scripts/lib/css-compile.mjs';
const DEPS_URL = '../../library/skills/ui-verify/scripts/lib/deps.mjs';
const CORE_URL = '../../library/skills/ui-verify/scripts/lib/css-scope-core.mjs';

function findCache() {
  const root = process.env.UI_VERIFY_TEST_CACHE;
  if (!root) return { reason: 'needs-ui-verify-deps: UI_VERIFY_TEST_CACHE nie ustawione' };
  try {
    const sub = fs.readdirSync(root).find((d) => fs.existsSync(path.join(root, d, 'node_modules', 'sass.js', 'package.json')));
    if (!sub) return { reason: 'needs-ui-verify-deps: brak sass.js w UI_VERIFY_TEST_CACHE' };
    return { cacheDir: path.join(root, sub) };
  } catch (e) {
    return { reason: `needs-ui-verify-deps: ${e.code || e.message}` };
  }
}

const cache = findCache();
let Sass = null;
let loadError = null;
if (cache.cacheDir) {
  try {
    const { createLoader } = await import(DEPS_URL);
    Sass = await createLoader({ cacheDir: cache.cacheDir }).load('sass.js');
  } catch (e) {
    // Brak/niesprawna instalacja = pominiecie; inny blad (np. loader nie zna sass.js) = czerwony test.
    if (e && e.name === 'DepsError') cache.reason = `needs-ui-verify-deps: ${e.code}`;
    else loadError = e;
  }
}
const SKIP = Sass || loadError ? false : cache.reason;
const opts = { skip: SKIP };

const tmpDirs = [];
function project(files) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'css-scope-real-'));
  tmpDirs.push(dir);
  const scss = path.join(dir, 'scss');
  fs.mkdirSync(scss);
  for (const [name, text] of Object.entries(files)) fs.writeFileSync(path.join(scss, name), text);
  return { dir, scss };
}
test.after(() => {
  for (const d of tmpDirs) fs.rmSync(d, { recursive: true, force: true });
});

async function compile(files, entry = 'main.scss', extra = {}) {
  if (loadError) throw loadError;
  const { compileScss } = await import(COMPILE_URL);
  const { scss } = project(files);
  return compileScss({ Sass, dir: scss, entry, fs, ...extra });
}

const checkWith = async (css) => {
  const { checkScope } = await import(CORE_URL);
  return checkScope(css, { root: '.root', exceptions: [] });
};

test('real: zmienne, zagniezdzanie i @import czesciowego pliku => CSS w stylu expanded', opts, async () => {
  const r = await compile({
    '_vars.scss': '$accent: #ff0000;\n$pad: 4px;\n',
    'main.scss': '@import "vars";\n.root {\n  color: $accent;\n  .item {\n    padding: $pad;\n    &:hover { color: blue; }\n  }\n}\n',
  });
  assert.equal(r.ok, true, r.error);
  assert.equal(r.error, null);
  assert.equal(r.files, 2);
  assert.equal(typeof r.css, 'string');
  assert.match(r.css, /\.root\s*\{\s*\n\s+color:\s*#f00|\.root\s*\{\s*\n\s+color:\s*#ff0000/);
  assert.match(r.css, /\.root \.item\s*\{\s*\n\s+padding:\s*4px;/);
  assert.match(r.css, /\.root \.item:hover\s*\{/);
  // expanded: kazda deklaracja w osobnej linii, brak zmiennych i dyrektyw.
  assert.ok(r.css.split('\n').length > 6);
  assert.doesNotMatch(r.css, /\$accent|\$pad|@import/);
});

test('real: wynik przechodzi przez checkScope (buildRootPattern) bez bledu rozbioru; poprawny zakres => ok', opts, async () => {
  const r = await compile({ 'main.scss': '$c: red;\n.root { a { color: $c; } .b { margin: 0; } }\n' });
  assert.equal(r.ok, true, r.error);
  const { buildRootPattern } = await import(CORE_URL);
  assert.doesNotThrow(() => buildRootPattern('.root'));
  const s = await checkWith(r.css);
  assert.equal(s.ok, true, JSON.stringify(s.bad));
  assert.equal(s.rules, 2);
});

test('real: selektor poza korzeniem jest widoczny w checkScope (potok odroznia dobre od zlych)', opts, async () => {
  const r = await compile({ 'main.scss': '.root a { color: red; }\n.stray { color: blue; }\n' });
  assert.equal(r.ok, true, r.error);
  const s = await checkWith(r.css);
  assert.equal(s.ok, false);
  assert.deepEqual(s.bad, ['.stray']);
});

test('real: blad skladni SCSS => ok:false, css null, komunikat zawiera nazwe pliku', opts, async () => {
  const r = await compile({ 'main.scss': '.root {\n  color: red;\n' });
  assert.equal(r.ok, false);
  assert.equal(r.css, null);
  assert.match(r.error, /main\.scss/);
  assert.ok(r.error.length <= 4000);
});

test('real: blad w importowanym pliku wskazuje ten plik', opts, async () => {
  const r = await compile({ 'main.scss': '@import "broken";\n.root { a { b: c; } }\n', '_broken.scss': '.x { color: ;;; }}}\n' });
  assert.equal(r.ok, false);
  assert.equal(r.css, null);
  assert.match(r.error, /broken/);
});

test('real: brak importowanego pliku => ok:false z nazwa brakujacego pliku', opts, async () => {
  const r = await compile({ 'main.scss': '@import "nonexistent";\n.root { a { b: c; } }\n' });
  assert.equal(r.ok, false);
  assert.equal(r.css, null);
  assert.match(r.error, /nonexistent/);
});

test('real: pusty plik wejsciowy => ok:true i pusty CSS, checkScope ok', opts, async () => {
  const r = await compile({ 'main.scss': '' });
  assert.equal(r.ok, true, r.error);
  assert.equal(typeof r.css, 'string');
  assert.equal(r.css.trim(), '');
  const s = await checkWith(r.css);
  assert.equal(s.ok, true);
  assert.equal(s.rules, 0);
});

test('real: @import spoza katalogu nie czyta plikow z dysku => ok:false mimo ze plik istnieje obok', opts, async () => {
  if (loadError) throw loadError;
  const { compileScss } = await import(COMPILE_URL);
  const { dir, scss } = project({ 'main.scss': '@import "../outside";\n.root { a { b: c; } }\n' });
  fs.writeFileSync(path.join(dir, '_outside.scss'), '.leaked { color: red; }\n');
  fs.writeFileSync(path.join(dir, 'outside.scss'), '.leaked { color: red; }\n');
  const r = await compileScss({ Sass, dir: scss, entry: 'main.scss', fs });
  assert.equal(r.ok, false);
  assert.equal(r.css, null);
  assert.match(r.error, /outside/);
});

test('real: kompilacja jest powtarzalna na tej samej instancji Sass (dwa biegi, ten sam CSS)', opts, async () => {
  const files = { '_v.scss': '$c: red;', 'main.scss': '@import "v";\n.root { a { color: $c; } }\n' };
  const a = await compile(files);
  const b = await compile(files);
  assert.equal(a.ok, true, a.error);
  assert.equal(a.css, b.css);
});

test('real: pliki spoza filtra nazw sa pomijane z ostrzezeniem, kompilacja nadal dziala', opts, async () => {
  const r = await compile({ 'main.scss': '.root a { b: c; }\n', 'readme.txt': 'x', 'two words.scss': '$a: 1;' });
  assert.equal(r.ok, true, r.error);
  assert.equal(r.files, 1);
  assert.equal(r.warnings.length, 2);
});
