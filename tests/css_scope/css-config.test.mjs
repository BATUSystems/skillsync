import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { readFile } from 'node:fs/promises';

// Dynamiczne importy: brak modulu daje czerwony test, nie blad calego pliku.
const LIB = '../../library/skills/ui-verify/scripts/lib/';
const cfg = () => import(`${LIB}css-config.mjs`);
const scope = () => import(`${LIB}css-scope-core.mjs`);
const parse = () => import(`${LIB}css-parse.mjs`);

const CONFIG_DIR = path.resolve('/h/proj/config');
const EXACT_SEL = '.root-theme main[tabindex="-1"]:focus:not(:focus-visible)';

// undefined w nadpisaniu = brak klucza (nie klucz o wartosci undefined)
const clean = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined));
const base = (extra = {}) => clean({ version: 1, root: '.root', ...extra });
const prefixEx = (o = {}) => clean({ id: 'menu-host', kind: 'prefix', value: '.menu-host > .item', reason: 'menu outside root', ...o });
const aliasEx = (o = {}) => clean({ id: 'theme-hook', kind: 'root-alias', value: '.root-theme', onlyPropertyPrefix: '--tok-', reason: 'tokens only', ...o });
const exactEx = (o = {}) => clean({ id: 'skip-link', kind: 'exact-rule', selector: EXACT_SEL, declarations: ['outline:0!important'], reason: 'focus target', ...o });

async function check(obj) {
  const { validateConfig } = await cfg();
  return validateConfig(obj, { configDir: CONFIG_DIR });
}
const errText = (r) => r.errors.join('\n');
async function assertBad(obj, re, label = '') {
  const r = await check(obj);
  assert.equal(r.ok, false, `${label} should be rejected: ${JSON.stringify(obj)}`);
  assert.equal(r.config, null, label);
  assert.ok(r.errors.length > 0, label);
  assert.ok(r.errors.every((e) => typeof e === 'string'), label);
  if (re) assert.match(errText(r), re, label);
}
async function assertOk(obj, label = '') {
  const r = await check(obj);
  assert.equal(r.ok, true, `${label} should pass: ${JSON.stringify(r.errors)}`);
  assert.deepEqual(r.errors, [], label);
  assert.ok(r.config && typeof r.config === 'object', label);
  return r;
}

// ---- validateConfig: najwyzszy poziom ----

test('validateConfig: minimalna konfiguracja {version:1, root} jest poprawna i dostaje wartosci domyslne', async () => {
  const r = await assertOk(base());
  assert.equal(r.config.version, 1);
  assert.equal(r.config.root, '.root');
  assert.deepEqual(r.config.exceptions, []);
  assert.deepEqual(r.config.selftest, []);
  assert.deepEqual(r.config.diff.allowAtRules, ['font-face']);
  // brak progu !important domyslnie
  assert.ok(r.config.important == null || r.config.important.max == null);
});

test('validateConfig: pelna konfiguracja zachowuje podane wartosci', async () => {
  const r = await assertOk(
    base({
      scssDir: 'scss',
      entry: 'main.scss',
      without: 'base.scss',
      out: 'dist/overlay.css',
      exceptions: [prefixEx(), aliasEx(), exactEx()],
      selftest: [{ name: 'ok', css: '.root a { color: red }', expect: 'accepted' }],
      important: { max: 20 },
      diff: { allowAtRules: ['page', 'keyframes'] },
    }),
  );
  assert.equal(r.config.scssDir, 'scss');
  assert.equal(r.config.entry, 'main.scss');
  assert.equal(r.config.without, 'base.scss');
  assert.equal(r.config.out, 'dist/overlay.css');
  assert.equal(r.config.exceptions.length, 3);
  assert.equal(r.config.selftest.length, 1);
  assert.equal(r.config.important.max, 20);
  assert.deepEqual(r.config.diff.allowAtRules, ['page', 'keyframes']);
});

test('validateConfig: nie rzuca dla wejsc nie-obiektowych, zwraca ok:false i config:null', async () => {
  const { validateConfig } = await cfg();
  for (const v of [null, undefined, 5, 'x', [], true]) {
    let r;
    assert.doesNotThrow(() => {
      r = validateConfig(v, { configDir: CONFIG_DIR });
    }, JSON.stringify(v));
    assert.equal(r.ok, false);
    assert.equal(r.config, null);
    assert.ok(r.errors.length > 0);
  }
});

test('validateConfig: nieznany klucz najwyzszego poziomu jest bledem z nazwa klucza', async () => {
  await assertBad(base({ bogusKey: 1 }), /bogusKey/);
  await assertBad(base({ Root: '.x' }), /Root/);
});

test('validateConfig: version wymagane i dokladnie 1', async () => {
  await assertBad({ root: '.root' }, /version/, 'brak');
  for (const v of [0, 2, '1', 1.5, null, true]) await assertBad({ version: v, root: '.root' }, /version/, String(v));
});

test('validateConfig: root wymagany i musi byc klasa jak w buildRootPattern', async () => {
  await assertBad({ version: 1 }, /root/, 'brak');
  for (const v of ['root', '.1a', '.a b', '#root', '.a.b', '', '.', '.root>a', 5, null, ['.root']]) {
    await assertBad({ version: 1, root: v }, /root/, JSON.stringify(v));
  }
  for (const v of ['.root', '.menu-host', '._x', '.a-b_c1']) await assertOk({ version: 1, root: v }, v);
});

test('validateConfig: scssDir przechodzi przez resolveInside (.., bezwzgledna, backslash, NUL, typ)', async () => {
  for (const v of ['../x', 'a/../../x', '/abs/dir', '..', 'a\\b', 'a\0b', 5, null]) {
    await assertBad(base({ scssDir: v }), /scssDir/, JSON.stringify(v));
  }
  for (const v of ['scss', 'a/b', './scss']) await assertOk(base({ scssDir: v }), v);
});

test('validateConfig: entry i without to nazwy plikow .scss', async () => {
  for (const key of ['entry', 'without']) {
    for (const v of ['main.css', 'a/b.scss', '../a.scss', 'a b.scss', '.scss', 'a.scss/', 'a\\b.scss', 'a\0.scss', '', 7, null]) {
      await assertBad(base({ [key]: v }), new RegExp(key), `${key}=${JSON.stringify(v)}`);
    }
    for (const v of ['main.scss', 'overlay-base_1.scss', 'a.b.scss']) await assertOk(base({ [key]: v }), `${key}=${v}`);
  }
});

test('validateConfig: out to sciezka wzgledna .css przez resolveInside', async () => {
  for (const v of ['../o.css', '/abs/o.css', 'a\\o.css', 'o\0.css', 'out.txt', 'out', 'out.scss', '', 3]) {
    await assertBad(base({ out: v }), /out/, JSON.stringify(v));
  }
  for (const v of ['o.css', 'dist/o.css', './dist/o.min.css']) await assertOk(base({ out: v }), v);
});

test('validateConfig: exceptions, selftest, important, diff musza miec wlasciwy typ', async () => {
  await assertBad(base({ exceptions: {} }), /exceptions/);
  await assertBad(base({ exceptions: 'x' }), /exceptions/);
  await assertBad(base({ selftest: {} }), /selftest/);
  await assertBad(base({ important: 5 }), /important/);
  await assertBad(base({ important: [] }), /important/);
  await assertBad(base({ diff: 'x' }), /diff/);
  await assertBad(base({ diff: [] }), /diff/);
});

test('validateConfig: important przyjmuje tylko max calkowite >= 0', async () => {
  for (const v of [-1, 1.5, '3', null, NaN, Infinity]) await assertBad(base({ important: { max: v } }), /important/, String(v));
  await assertBad(base({ important: { foo: 1 } }), /foo/);
  await assertBad(base({ important: { max: 1, foo: 1 } }), /foo/);
  for (const v of [0, 1, 18, 1000]) {
    const r = await assertOk(base({ important: { max: v } }), String(v));
    assert.equal(r.config.important.max, v);
  }
});

test('validateConfig: diff.allowAtRules to podzbior {font-face, keyframes, page}', async () => {
  await assertBad(base({ diff: { allowAtRules: ['font-face', 'media'] } }), /allowAtRules/);
  await assertBad(base({ diff: { allowAtRules: 'font-face' } }), /allowAtRules/);
  await assertBad(base({ diff: { allowAtRules: [1] } }), /allowAtRules/);
  await assertBad(base({ diff: { other: 1 } }), /other/);
  for (const v of [[], ['font-face'], ['keyframes'], ['page'], ['font-face', 'keyframes', 'page']]) {
    const r = await assertOk(base({ diff: { allowAtRules: v } }), JSON.stringify(v));
    assert.deepEqual(r.config.diff.allowAtRules, v);
  }
  const r = await assertOk(base({ diff: {} }));
  assert.deepEqual(r.config.diff.allowAtRules, ['font-face']);
});

// ---- validateConfig: wyjatki ----

test('validateConfig: poprawne rekordy wszystkich trzech rodzajow', async () => {
  await assertOk(base({ exceptions: [prefixEx()] }), 'prefix');
  await assertOk(base({ exceptions: [aliasEx()] }), 'root-alias z onlyPropertyPrefix');
  await assertOk(base({ exceptions: [aliasEx({ onlyPropertyPrefix: undefined })] }), 'root-alias bez onlyPropertyPrefix');
  await assertOk(base({ exceptions: [exactEx()] }), 'exact-rule');
  await assertOk(base({ exceptions: [prefixEx(), aliasEx(), exactEx()] }), 'wszystkie');
});

test('validateConfig: blad wyjatku wskazuje sciezke exceptions[i] (indeks, nie pierwszy rekord)', async () => {
  await assertBad(base({ exceptions: [prefixEx(), { ...aliasEx(), kind: 'weird' }] }), /exceptions\[1\]\.kind/);
  await assertBad(base({ exceptions: [prefixEx(), prefixEx({ id: 'b', value: '' })] }), /exceptions\[1\]\.value/);
});

test('validateConfig: element exceptions musi byc obiektem', async () => {
  for (const v of [null, 'x', 5, [], undefined]) await assertBad(base({ exceptions: [v] }), /exceptions\[0\]/, JSON.stringify(v));
});

test('validateConfig: id wyjatku [a-z0-9-]{1,40}, wymagane i unikalne', async () => {
  await assertBad(base({ exceptions: [prefixEx({ id: undefined })] }), /exceptions\[0\]\.id/, 'brak');
  for (const v of ['', 'Bad', 'a_b', 'a b', 'a.b', 'x'.repeat(41), 5, null]) {
    await assertBad(base({ exceptions: [prefixEx({ id: v })] }), /exceptions\[0\]\.id/, JSON.stringify(v));
  }
  for (const v of ['a', '0', 'a-b-1', 'x'.repeat(40)]) await assertOk(base({ exceptions: [prefixEx({ id: v })] }), v);
  const r = await check(base({ exceptions: [prefixEx({ id: 'dup' }), aliasEx({ id: 'dup' })] }));
  assert.equal(r.ok, false);
  assert.match(errText(r), /dup/);
  assert.match(errText(r), /exceptions\[1\]\.id/);
});

test('validateConfig: kind musi byc prefix|root-alias|exact-rule', async () => {
  await assertBad(base({ exceptions: [prefixEx({ kind: undefined })] }), /kind/);
  for (const v of ['other', 'Prefix', '', 5, null]) await assertBad(base({ exceptions: [prefixEx({ kind: v })] }), /kind/, JSON.stringify(v));
});

test('validateConfig: reason wymagany i niepusty (kazdy rodzaj)', async () => {
  for (const mk of [prefixEx, aliasEx, exactEx]) {
    await assertBad(base({ exceptions: [mk({ reason: undefined })] }), /reason/, `${mk().kind} brak`);
    for (const v of ['', 5, null, ['x']]) await assertBad(base({ exceptions: [mk({ reason: v })] }), /reason/, `${mk().kind} ${JSON.stringify(v)}`);
  }
});

test('validateConfig: prefix wymaga niepustego value bez znakow sterujacych; inne pola niedozwolone', async () => {
  await assertBad(base({ exceptions: [prefixEx({ value: undefined })] }), /value/);
  for (const v of ['', 5, null, 'a\nb', 'a\0b', 'a\tb']) await assertBad(base({ exceptions: [prefixEx({ value: v })] }), /value/, JSON.stringify(v));
  await assertBad(base({ exceptions: [prefixEx({ selector: 'a' })] }), /selector/);
  await assertBad(base({ exceptions: [prefixEx({ declarations: ['a:b'] })] }), /declarations/);
  await assertBad(base({ exceptions: [prefixEx({ onlyPropertyPrefix: '--x-' })] }), /onlyPropertyPrefix/);
});

test('validateConfig: root-alias wymaga klasy .nazwa roznej od root; onlyPropertyPrefix zaczyna sie od --', async () => {
  await assertBad(base({ exceptions: [aliasEx({ value: undefined })] }), /value/);
  for (const v of ['root-theme', '.1a', '.a b', '', 5, '.root-theme a', '#x']) {
    await assertBad(base({ exceptions: [aliasEx({ value: v })] }), /value/, JSON.stringify(v));
  }
  const same = await check(base({ exceptions: [aliasEx({ value: '.root' })] }));
  assert.equal(same.ok, false, 'alias rowny korzeniowi');
  assert.match(errText(same), /exceptions\[0\]\.value/);
  // alias liczony wzgledem root z tej samej konfiguracji
  await assertOk({ version: 1, root: '.other', exceptions: [aliasEx({ value: '.root' })] }, 'alias rozny od innego korzenia');

  for (const v of ['tok-', '-tok-', '', 5, null, '-x']) {
    await assertBad(base({ exceptions: [aliasEx({ onlyPropertyPrefix: v })] }), /onlyPropertyPrefix/, JSON.stringify(v));
  }
  for (const v of ['--', '--tok-', '--a']) await assertOk(base({ exceptions: [aliasEx({ onlyPropertyPrefix: v })] }), v);
  await assertBad(base({ exceptions: [aliasEx({ selector: 'a' })] }), /selector/);
  await assertBad(base({ exceptions: [aliasEx({ declarations: ['a:b'] })] }), /declarations/);
});

test('validateConfig: exact-rule wymaga selector i niepustych declarations z dwukropkiem', async () => {
  await assertBad(base({ exceptions: [exactEx({ selector: undefined })] }), /selector/);
  for (const v of ['', 5, null]) await assertBad(base({ exceptions: [exactEx({ selector: v })] }), /selector/, JSON.stringify(v));
  await assertBad(base({ exceptions: [exactEx({ declarations: undefined })] }), /declarations/);
  for (const v of [[], 'outline:0', ['outline'], [''], ['a:b', 'nocolon'], [5], [null], {}]) {
    await assertBad(base({ exceptions: [exactEx({ declarations: v })] }), /declarations/, JSON.stringify(v));
  }
  const r = await check(base({ exceptions: [exactEx({ declarations: ['a:b', 'nocolon'] })] }));
  assert.match(errText(r), /exceptions\[0\]\.declarations/);
  await assertOk(base({ exceptions: [exactEx({ declarations: ['outline:0!important', 'color: red'] })] }));
  await assertBad(base({ exceptions: [exactEx({ value: '.x' })] }), /value/);
  await assertBad(base({ exceptions: [exactEx({ onlyPropertyPrefix: '--x-' })] }), /onlyPropertyPrefix/);
});

test('validateConfig: nieznany klucz w rekordzie wyjatku = blad z nazwa klucza i sciezka', async () => {
  for (const mk of [prefixEx, aliasEx, exactEx]) {
    const r = await check(base({ exceptions: [mk({ surprise: 1 })] }));
    assert.equal(r.ok, false, mk().kind);
    assert.match(errText(r), /exceptions\[0\]/);
    assert.match(errText(r), /surprise/);
  }
});

// ---- validateConfig: selftest ----

const caseOf = (o = {}) => clean({ name: 'n1', css: '.root a { color: red }', expect: 'accepted', ...o });

test('validateConfig: poprawne przypadki selftest (accepted i rejected, granice dlugosci)', async () => {
  await assertOk(base({ selftest: [caseOf(), caseOf({ name: 'n2', css: 'a { x: y }', expect: 'rejected' })] }));
  await assertOk(base({ selftest: [caseOf({ name: 'x'.repeat(120) })] }), 'nazwa 120');
  await assertOk(base({ selftest: [caseOf({ css: 'a'.repeat(20000) })] }), 'css 20000');
  await assertOk(base({ selftest: [caseOf({ css: '' })] }), 'pusty css');
});

test('validateConfig: selftest[i].name 1..120 znakow, tekst', async () => {
  for (const v of ['', 'x'.repeat(121), 5, null, undefined]) {
    await assertBad(base({ selftest: [caseOf({ name: v })] }), /selftest\[0\]\.name/, JSON.stringify(v)?.slice(0, 20));
  }
});

test('validateConfig: selftest[i].css tekst do 20000 znakow', async () => {
  for (const v of ['a'.repeat(20001), 5, null, undefined]) {
    await assertBad(base({ selftest: [caseOf({ css: v })] }), /selftest\[0\]\.css/, String(v).slice(0, 10));
  }
});

test('validateConfig: selftest[i].expect tylko accepted|rejected', async () => {
  for (const v of ['maybe', 'ACCEPTED', true, '', 5, null, undefined]) {
    await assertBad(base({ selftest: [caseOf({ expect: v })] }), /selftest\[0\]\.expect/, String(v));
  }
});

test('validateConfig: selftest: nieznany klucz, nazwy unikalne, element musi byc obiektem', async () => {
  const r = await check(base({ selftest: [caseOf(), caseOf({ name: 'n2', reason: 'x' })] }));
  assert.equal(r.ok, false);
  assert.match(errText(r), /selftest\[1\]/);
  assert.match(errText(r), /reason/);
  const d = await check(base({ selftest: [caseOf(), caseOf()] }));
  assert.equal(d.ok, false);
  assert.match(errText(d), /selftest\[1\]\.name/);
  for (const v of [null, 'x', 5, []]) await assertBad(base({ selftest: [v] }), /selftest\[0\]/, JSON.stringify(v));
});

test('validateConfig: kilka bledow naraz jest zglaszanych razem', async () => {
  const r = await check({ version: 2, root: 'x', bogus: 1 });
  assert.equal(r.ok, false);
  assert.match(errText(r), /version/);
  assert.match(errText(r), /root/);
  assert.match(errText(r), /bogus/);
});

// ---- parseConfig ----

test('parseConfig: poprawny JSON przechodzi walidacje i zwraca config z domyslnymi', async () => {
  const { parseConfig } = await cfg();
  const r = parseConfig(JSON.stringify(base({ exceptions: [prefixEx()] })), { configDir: CONFIG_DIR });
  assert.equal(r.ok, true);
  assert.deepEqual(r.errors, []);
  assert.equal(r.config.root, '.root');
  assert.deepEqual(r.config.diff.allowAtRules, ['font-face']);
});

test('parseConfig: blad skladni JSON => errors[0] zaczyna sie od "invalid JSON:" i nie powtarza tresci', async () => {
  const { parseConfig } = await cfg();
  const secret = 'SECRET-VALUE-XYZ';
  const r = parseConfig(`{"root": "${secret}", `, { configDir: CONFIG_DIR });
  assert.equal(r.ok, false);
  assert.equal(r.config, null);
  assert.ok(r.errors[0].startsWith('invalid JSON:'), r.errors[0]);
  assert.ok(!r.errors.join('\n').includes(secret));
  const r2 = parseConfig(`not json ${secret}`, { configDir: CONFIG_DIR });
  assert.ok(r2.errors[0].startsWith('invalid JSON:'));
  assert.ok(!r2.errors.join('\n').includes(secret));
});

test('parseConfig: poprawny JSON o zlym ksztalcie trafia do validateConfig', async () => {
  const { parseConfig } = await cfg();
  for (const text of ['[]', 'null', '5', '"x"', '{}']) {
    const r = parseConfig(text, { configDir: CONFIG_DIR });
    assert.equal(r.ok, false, text);
    assert.equal(r.config, null, text);
    assert.ok(r.errors.length > 0, text);
    assert.ok(!r.errors[0].startsWith('invalid JSON:'), text);
  }
  const r = parseConfig(JSON.stringify(base({ scssDir: '../x' })), { configDir: CONFIG_DIR });
  assert.equal(r.ok, false);
  assert.match(r.errors.join('\n'), /scssDir/);
});

test('parseConfig: limit 1 000 000 znakow (dokladnie limit przechodzi, wiecej odrzucone bez echa)', async () => {
  const { parseConfig } = await cfg();
  const json = JSON.stringify(base());
  const atLimit = json + ' '.repeat(1_000_000 - json.length);
  assert.equal(atLimit.length, 1_000_000);
  assert.equal(parseConfig(atLimit, { configDir: CONFIG_DIR }).ok, true);
  const over = atLimit + ' ';
  const r = parseConfig(over, { configDir: CONFIG_DIR });
  assert.equal(r.ok, false);
  assert.equal(r.config, null);
  assert.ok(r.errors.length > 0);
  assert.ok(r.errors.join('\n').length < 1000, 'komunikat nie moze zawierac tresci pliku');
});

// ---- toScopeConfig ----

test('toScopeConfig: zwraca tylko {root, exceptions} i dziala w checkScope', async () => {
  const { toScopeConfig, validateConfig } = await cfg();
  const { checkScope } = await scope();
  const v = validateConfig(
    base({ scssDir: 'scss', out: 'o.css', important: { max: 3 }, exceptions: [prefixEx(), aliasEx(), exactEx()] }),
    { configDir: CONFIG_DIR },
  );
  assert.equal(v.ok, true, JSON.stringify(v.errors));
  const sc = toScopeConfig(v.config);
  assert.deepEqual(Object.keys(sc).sort(), ['exceptions', 'root']);
  assert.equal(sc.root, '.root');
  assert.deepEqual(sc.exceptions, [prefixEx(), aliasEx(), exactEx()]);
  const r = checkScope(
    `.root a { color: red }\n.menu-host > .item a { x: y }\n.root-theme { --tok-a: 1 }\n${EXACT_SEL} { outline: 0 !important }\nb { c: d }`,
    sc,
  );
  assert.deepEqual(r.bad, ['b']);
  assert.deepEqual(r.exceptions.map((e) => e.hits), [1, 1, 1]);
});

test('toScopeConfig: konfiguracja bez wyjatkow daje exceptions: []', async () => {
  const { toScopeConfig, validateConfig } = await cfg();
  const v = validateConfig(base(), { configDir: CONFIG_DIR });
  const sc = toScopeConfig(v.config);
  assert.deepEqual(sc, { root: '.root', exceptions: [] });
});

// ---- builtinCases / builtinConfig / runCases ----

test('builtinConfig: korzen .root i trzy wyjatki o ustalonych id (poprawna konfiguracja)', async () => {
  const { builtinConfig, validateConfig } = await cfg();
  const c = builtinConfig();
  assert.equal(c.root, '.root');
  const by = Object.fromEntries(c.exceptions.map((e) => [e.id, e]));
  assert.deepEqual(Object.keys(by).sort(), ['menu-host', 'skip-link', 'theme-hook']);
  assert.equal(by['menu-host'].kind, 'prefix');
  assert.equal(by['menu-host'].value, '.menu-host > .item');
  assert.equal(by['theme-hook'].kind, 'root-alias');
  assert.equal(by['theme-hook'].value, '.root-theme');
  assert.equal(by['theme-hook'].onlyPropertyPrefix, '--tok-');
  assert.equal(by['skip-link'].kind, 'exact-rule');
  assert.equal(by['skip-link'].selector, EXACT_SEL);
  assert.deepEqual(by['skip-link'].declarations, ['outline:0!important']);
  for (const e of c.exceptions) assert.ok(typeof e.reason === 'string' && e.reason.length > 0, e.id);
  const v = validateConfig(c, { configDir: CONFIG_DIR });
  assert.equal(v.ok, true, JSON.stringify(v.errors));
  // kazde wywolanie zwraca swiezy obiekt (mutacja nie przecieka)
  c.exceptions.length = 0;
  assert.equal(builtinConfig().exceptions.length, 3);
});

test('builtinCases: min. 13 przypadkow w ksztalcie selftest, nazwy unikalne, oba rodzaje expect', async () => {
  const { builtinCases, validateConfig } = await cfg();
  const cases = builtinCases();
  assert.ok(Array.isArray(cases));
  assert.ok(cases.length >= 13, `got ${cases.length}`);
  const names = new Set();
  for (const c of cases) {
    assert.deepEqual(Object.keys(c).sort(), ['css', 'expect', 'name']);
    assert.ok(typeof c.name === 'string' && c.name.length >= 1 && c.name.length <= 120);
    assert.ok(typeof c.css === 'string' && c.css.length <= 20000);
    assert.ok(c.expect === 'accepted' || c.expect === 'rejected');
    names.add(c.name);
  }
  assert.equal(names.size, cases.length, 'nazwy unikalne');
  assert.ok(cases.some((c) => c.expect === 'accepted'));
  assert.ok(cases.filter((c) => c.expect === 'rejected').length >= 6);
  // te same przypadki musza byc poprawnym selftest w konfiguracji
  const v = validateConfig({ version: 1, root: '.root', selftest: cases }, { configDir: CONFIG_DIR });
  assert.equal(v.ok, true, JSON.stringify(v.errors));
  assert.notEqual(builtinCases(), cases, 'swieza tablica');
});

test('runCases(builtinCases(), toScopeConfig(builtinConfig())): wszystkie przypadki zgodne z expect', async () => {
  const { builtinCases, builtinConfig, toScopeConfig, runCases } = await cfg();
  const cases = builtinCases();
  const results = runCases(cases, toScopeConfig(builtinConfig()));
  assert.equal(results.length, cases.length);
  results.forEach((r, i) => {
    assert.equal(r.name, cases[i].name);
    assert.equal(r.expect, cases[i].expect);
    assert.ok(r.got === 'accepted' || r.got === 'rejected');
    assert.equal(r.got, cases[i].expect, `${r.name}: ${r.error ?? ''}`);
    assert.equal(r.pass, true, r.name);
  });
});

test('builtinCases: pokrywa wymagane rodziny (korzen, poza korzeniem, prefix, alias, exact-rule w dwoch formach i odrzucenia)', async () => {
  const { builtinCases, builtinConfig, toScopeConfig, runCases } = await cfg();
  const { checkScope } = await scope();
  const sc = toScopeConfig(builtinConfig());
  const cases = builtinCases();
  // Korzystamy z silnika, nie z nazw przypadkow: zbior css przypadkow musi zawierac konkretne wzorce.
  const all = cases.map((c) => c.css).join('\n');
  assert.match(all, /\.menu-host\s*>\s*\.item/);
  assert.match(all, /\.root-theme/);
  assert.match(all, /--tok-/);
  assert.match(all, /:focus:not\(:focus-visible\)/);
  assert.match(all, /outline:\s*0\s*!important/, 'forma rozwinieta lub skompresowana');
  assert.ok(/outline:0!important/.test(all) && /outline:\s+0\s+!important/.test(all), 'exact-rule w obu formach zapisu');
  assert.match(all, /outline:\s*none/, 'none zamiast 0');
  assert.match(all, /:focus-visible\s*\)?\s*\{/, 'wariant z :focus-visible');
  // wlasne przypadki wzgledem tej samej konfiguracji (zaleznosc od silnika, nie od nazw)
  const mine = [
    { name: 'a', css: '.root a { color: red }', expect: 'accepted' },
    { name: 'b', css: 'a { color: red }', expect: 'rejected' },
    { name: 'c', css: `.root-theme { --tok-a: 1 }`, expect: 'accepted' },
    { name: 'd', css: `.root-theme { color: red }`, expect: 'rejected' },
  ];
  assert.ok(runCases(mine, sc).every((r) => r.pass));
  assert.equal(checkScope('.root-theme{--tok-a:1}', sc).ok, true);
});

test('runCases: zepsucie jednego wyjatku w konfiguracji powoduje niezgodnosc (test negatywny)', async () => {
  const { builtinCases, builtinConfig, toScopeConfig, runCases } = await cfg();
  const mutate = (fn) => {
    const c = builtinConfig();
    fn(c);
    return runCases(builtinCases(), toScopeConfig(c));
  };
  const exOf = (c, id) => c.exceptions.find((e) => e.id === id);
  const variants = {
    'skip-link declarations': (c) => { exOf(c, 'skip-link').declarations = ['outline:1px solid red']; },
    'skip-link selector': (c) => { exOf(c, 'skip-link').selector = `${EXACT_SEL}x`; },
    'theme-hook prefix': (c) => { exOf(c, 'theme-hook').onlyPropertyPrefix = '--zzz-'; },
    'menu-host value': (c) => { exOf(c, 'menu-host').value = '.menu-host > .other'; },
    'wyjatek usuniety': (c) => { c.exceptions = c.exceptions.filter((e) => e.id !== 'menu-host'); },
  };
  for (const [name, fn] of Object.entries(variants)) {
    const res = mutate(fn);
    assert.ok(res.some((r) => r.pass === false), `${name}: brak niezgodnosci`);
    for (const r of res.filter((x) => !x.pass)) assert.notEqual(r.got, r.expect, name);
  }
  // kontrola: niezmieniona konfiguracja daje komplet zgodnosci
  assert.ok(mutate(() => {}).every((r) => r.pass));
});

test('runCases: ksztalt wyniku, kolejnosc, pass = got === expect', async () => {
  const { runCases } = await cfg();
  const sc = { root: '.root', exceptions: [] };
  const res = runCases(
    [
      { name: 'ok', css: '.root a { c: d }', expect: 'accepted' },
      { name: 'wrong-expect', css: 'a { c: d }', expect: 'accepted' },
      { name: 'rej', css: 'a { c: d }', expect: 'rejected' },
    ],
    sc,
  );
  assert.deepEqual(res.map((r) => r.name), ['ok', 'wrong-expect', 'rej']);
  assert.deepEqual(res.map((r) => r.got), ['accepted', 'rejected', 'rejected']);
  assert.deepEqual(res.map((r) => r.pass), [true, false, true]);
  assert.deepEqual(res.map((r) => r.expect), ['accepted', 'accepted', 'rejected']);
  assert.equal(res[0].error, undefined);
  assert.deepEqual(runCases([], sc), []);
});

test('runCases: CssParseError w przypadku => got "error" i pass:false niezaleznie od expect, z polem error (tekst); reszta nadal liczona', async () => {
  const { runCases } = await cfg();
  const { CssParseError, scan } = await parse();
  assert.throws(() => scan('.root a {'), CssParseError, 'zalozenie: niezamkniety blok to CssParseError');
  const sc = { root: '.root', exceptions: [] };
  const res = runCases(
    [
      { name: 'broken-rejected', css: '.root a {', expect: 'rejected' },
      { name: 'broken-accepted', css: '.root a {', expect: 'accepted' },
      { name: 'fine', css: '.root a { c: d }', expect: 'accepted' },
    ],
    sc,
  );
  for (const i of [0, 1]) {
    assert.equal(res[i].got, 'error', res[i].name);
    assert.equal(res[i].pass, false, `${res[i].name}: uszkodzony CSS nie moze przejsc jako "rejected"`);
    assert.equal(typeof res[i].error, 'string', res[i].name);
    assert.ok(res[i].error.length > 0, res[i].name);
  }
  assert.equal(res[0].expect, 'rejected');
  assert.equal(res[2].got, 'accepted');
  assert.equal(res[2].pass, true);
});

test('runCases: kazdy rodzaj bledu rozbioru (nawias, nadmiarowa }, napis z nowa linia, komentarz) daje got "error"', async () => {
  const { runCases } = await cfg();
  const sc = { root: '.root', exceptions: [] };
  const css = ['.root a {', '.root a { } }', '.root a{content:"x\n}body{c:d}', '.root a { /* x'];
  const res = runCases(css.map((c, i) => ({ name: `e${i}`, css: c, expect: 'rejected' })), sc);
  for (const r of res) {
    assert.equal(r.got, 'error', r.name);
    assert.equal(r.pass, false, r.name);
  }
});

test('runCases: przypadek zagniezdzony (poprawny rozbior) to zwykle "rejected", nie "error"', async () => {
  const { runCases } = await cfg();
  const res = runCases([{ name: 'n', css: '.root a { & b { c: d } }', expect: 'rejected' }], { root: '.root', exceptions: [] });
  assert.equal(res[0].got, 'rejected');
  assert.equal(res[0].pass, true);
});

// ---- B8: BOM i limit rozmiaru ----

test('parseConfig: BOM UTF-8 na poczatku tekstu jest zdejmowany przed parsowaniem', async () => {
  const { parseConfig } = await cfg();
  const text = JSON.stringify({ version: 1, root: '.root' });
  const plain = parseConfig(text, { configDir: CONFIG_DIR });
  assert.equal(plain.ok, true, JSON.stringify(plain.errors));
  const bom = parseConfig(`\uFEFF${text}`, { configDir: CONFIG_DIR });
  assert.equal(bom.ok, true, JSON.stringify(bom.errors));
  assert.deepEqual(bom.config, plain.config);
});

test('parseConfig: BOM nie ukrywa bledu skladni; BOM w srodku tekstu nadal blad', async () => {
  const { parseConfig } = await cfg();
  assert.equal(parseConfig('\uFEFF{ not json', { configDir: CONFIG_DIR }).ok, false);
  assert.equal(parseConfig('{"version":1,\uFEFF"root":".root"}', { configDir: CONFIG_DIR }).ok, false);
});

// ---- neutralnosc rdzenia ----

const LEAK_RE = new RegExp('/home/|\\\\Users\\\\|[A-Za-z0-9._-]+@[A-Za-z0-9-]+\\.[a-z]{2,}|' + [String.fromCharCode(112, 99, 103) + 'academia', 'ba' + 'tu', 'bar' + 'cis', 'skip' + 'pster'].join('|'), 'i');

test('rdzen: wbudowane przypadki i konfiguracja nie zawieraja nazw projektu, sciezek domowych ani e-maili', async () => {
  const { builtinCases, builtinConfig } = await cfg();
  const text = JSON.stringify([builtinCases(), builtinConfig()]);
  assert.doesNotMatch(text, LEAK_RE);
});

test('rdzen: zrodlo css-config.mjs nie zawiera nazw projektu, sciezek domowych ani e-maili', async () => {
  const src = await readFile(new URL(`${LIB}css-config.mjs`, import.meta.url), 'utf8');
  assert.doesNotMatch(src, LEAK_RE);
});
