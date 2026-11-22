import test from 'node:test';
import assert from 'node:assert/strict';

// Poprawki 4. rundy przegladu parsera CSS: stos nawiasow w preludium (fail-closed), NUL, escape w `!important`,
// CDO/CDC, limit glebokosci i komentarz w diff. Dynamiczne importy: brak modulu = czerwony test, nie blad pliku.
const LIB = '../../library/skills/ui-verify/scripts/lib/';
const parse = () => import(`${LIB}css-parse.mjs`);
const core = () => import(`${LIB}css-scope-core.mjs`);
const diff = () => import(`${LIB}css-diff.mjs`);

const CFG = () => ({ root: '.root', exceptions: [] });

// Wejscia, w ktorych przegladarka stosuje `:is(.evil)` poza korzeniem; parser nie moze dac ok:true.
const HOSTILE = [
  ['niedopasowany ] zamyka (', ':is(.evil, a] ; .root) { color: red }'],
  ['url( + cudzyslow pomija (, srednik w nawiasie', ':is(.evil, url("a") ; .root) { color: red }'],
  ['} w nawiasie wewnatrz @media', '@media x { :is(.evil, a } ; .root) {c:d} }'],
  ['srednik w nawiasie wewnatrz @media bez } grupy', '@media x { :is(.evil, a ; .root) {c:d} }'],
  ['ukrycie calej reguly', ':is(.evil, a] ; @keyframes x ) {color:red}'],
];

async function isParseError(fn) {
  const { CssParseError } = await parse();
  try {
    fn();
  } catch (e) {
    return e instanceof CssParseError;
  }
  return false;
}

for (const [name, css] of HOSTILE) {
  test(`scan: ${name} => CssParseError unbalanced-bracket z line/column`, async () => {
    const { scan, CssParseError } = await parse();
    assert.throws(
      () => scan(css),
      (e) => e instanceof CssParseError && e.code === 'unbalanced-bracket' && Number.isInteger(e.line) && e.line >= 1 && Number.isInteger(e.column) && e.column >= 1,
    );
  });

  test(`checkScope: ${name} => rzuca CssParseError albo ok:false, nigdy ok:true`, async () => {
    const { checkScope } = await core();
    let res = null;
    let threw = false;
    try {
      res = checkScope(css, CFG());
    } catch (e) {
      threw = true;
      assert.equal(e.name, 'CssParseError');
    }
    if (!threw) assert.equal(res.ok, false, 'ok:true to fail-open');
  });

  test(`diffCss: ${name} => rzuca CssParseError albo ok:false, nigdy ok:true`, async () => {
    const { diffCss } = await diff();
    let res = null;
    let threw = false;
    try {
      res = diffCss(css, '', CFG());
    } catch (e) {
      threw = true;
      assert.equal(e.name, 'CssParseError');
    }
    if (!threw) assert.equal(res.ok, false, 'ok:true to fail-open');
  });
}

test('scan: ; { } przy niepustym stosie nawiasow oraz niepasujacy zamykacz = unbalanced-bracket', async () => {
  const { scan, CssParseError } = await parse();
  for (const css of ['.root a:is(b ; c) {c:d}', '.root a:is(b { c) {c:d}', '.root a[b) {c:d}', '.root a:is(b] {c:d}', '.root a:is([b) ] {c:d}']) {
    assert.throws(() => scan(css), (e) => e instanceof CssParseError && e.code === 'unbalanced-bracket', css);
  }
});

// ---- poprawne wejscia nie moga zaczac rzucac ----

const VALID = [
  '@media (min-width: 1px) and (max-width: 2px) { .root a { c: d } }',
  '@supports (display: grid) and (not (display: inline-grid)) { .root a { c: d } }',
  '.root :is(a, b) c { c: d }',
  '.root :not([a=b]) x { c: d }',
  '.root a[href="x]"] { c: d }',
  ".root a[data-x='(;'] { c: d }",
  '@import url("a;b");',
  '@import url(a;b);',
  '@font-face { src: url("a;b") format("woff") }',
  '.root a { background: url( "x;y" ) }',
  '.root a { width: calc((1 + 2) * 3); color: red; margin: calc(1px + (2px * 3)) }',
  '.root:is(.a, .b) c { c: d }',
];
for (const css of VALID) {
  test(`poprawne nadal przechodzi scan: ${css.slice(0, 60)}`, async () => {
    const { scan } = await parse();
    assert.doesNotThrow(() => scan(css));
  });
}
test('poprawne: checkScope dla reguly w korzeniu z nawiasami daje ok:true', async () => {
  const { checkScope } = await core();
  for (const css of VALID.filter((c) => !c.startsWith('@import') && !c.startsWith('@font-face'))) {
    assert.equal(checkScope(css, CFG()).ok, true, css);
  }
});

// ---- NUL ----

test('buildRootPattern: NUL zaraz po klasie korzenia nie konczy korzenia', async () => {
  const { buildRootPattern } = await core();
  const re = buildRootPattern('.root');
  assert.equal(re.test('.root\u0000x a'), false);
  assert.equal(re.test('.root\u0000'), false);
  assert.equal(re.test('.root a'), true);
  assert.equal(re.test('.root'), true);
});

test('checkScope: .root\\u0000x a to nie korzen', async () => {
  const { checkScope } = await core();
  assert.equal(checkScope('.root\u0000x a { c: d }', CFG()).ok, false);
});

test('NUL: ident z NUL nie jest url-tokenem, klamra w nim liczona strukturalnie (fail-closed)', async () => {
  const { scan, CssParseError } = await parse();
  assert.throws(() => scan('.root a { background: \\0url(x{y) }'), (e) => e instanceof CssParseError);
  assert.throws(() => scan('.root a { background: \u0000url(x{y) }'), (e) => e instanceof CssParseError);
});

// ---- countImportant ----

test('countImportant: escape w slowie important oraz biale znaki po !', async () => {
  const { countImportant } = await parse();
  assert.equal(countImportant('a { c: d !\\69mportant }'), 1);
  assert.equal(countImportant('a { c: d !\\69 mportant }'), 1);
  assert.equal(countImportant('a { c: d !\\000069mportant }'), 1);
  assert.equal(countImportant('a { c: d ! important }'), 1);
  assert.equal(countImportant('a { c: d !important; e: f !IMPORTANT }'), 2);
});

test('countImportant: !important-x i escape w innych slowach nie sa liczone', async () => {
  const { countImportant } = await parse();
  assert.equal(countImportant('a { c: d !important-x }'), 0);
  assert.equal(countImportant('a { c: d !\\69mportantx }'), 0);
  assert.equal(countImportant('a { c: d !\\62mportant }'), 0);
  assert.equal(countImportant('a { c: "!important" }'), 0);
});

// ---- CDO / CDC ----

test('CDO/CDC na poziomie arkusza sa pomijane jak biale znaki', async () => {
  const { checkScope } = await core();
  const { scan } = await parse();
  for (const css of ['<!-- .root a { c: d } -->', '--> .root a { c: d }', '.root a { c: d } <!-- .root b { c: d } -->', '<!--\n.root a { c: d }\n-->']) {
    const r = checkScope(css, CFG());
    assert.equal(r.ok, true, css);
  }
  assert.equal(scan('<!-- .root a { c: d } -->').rules[0].selector, '.root a');
});

test('CDO w srodku preludium reguly nie jest pomijane (fail-closed)', async () => {
  const { checkScope } = await core();
  const r = checkScope('a <!-- b { c: d }', CFG());
  assert.equal(r.ok, false);
  assert.ok(r.bad.length > 0);
});

// ---- glebokosc ----

test('zagniezdzenie glebsze niz 256 = CssParseError too-deep, szybko', async () => {
  const { scan, CssParseError } = await parse();
  const t0 = Date.now();
  assert.throws(
    () => scan('@a{'.repeat(300) + '}'.repeat(300)),
    (e) => e instanceof CssParseError && e.code === 'too-deep' && e.line >= 1 && e.column >= 1,
  );
  assert.ok(Date.now() - t0 < 2000, 'za wolno');
});

test('too-deep takze w checkScope i diffCss (bez RangeError)', async () => {
  const { checkScope } = await core();
  const { diffCss } = await diff();
  const css = '@a{'.repeat(5000) + '}'.repeat(5000);
  assert.throws(() => checkScope(css, CFG()), (e) => e.name === 'CssParseError' && e.code === 'too-deep');
  assert.throws(() => diffCss(css, '', CFG()), (e) => e.name === 'CssParseError' && e.code === 'too-deep');
});

test('glebokosc 200 nadal dziala', async () => {
  const { checkScope } = await core();
  const css = '@media x{'.repeat(200) + '.root a{c:d}' + '}'.repeat(200);
  assert.equal(checkScope(css, CFG()).ok, true);
});

// ---- diff: komentarz a biale znaki ----

test('diffCss: a/**/b to inny selektor niz "a b" (usunieta + dodana)', async () => {
  const { diffCss } = await diff();
  const r = diffCss('.root a/**/b {c:d}', '.root a b {c:d}', CFG());
  assert.equal(r.ok, false);
  assert.equal(r.counts.removed, 1);
  assert.equal(r.counts.added, 1);
});

test('diffCss: komentarz oddzielony bialymi znakami nadal rowny', async () => {
  const { diffCss } = await diff();
  for (const w of ['.root a /* x */ b {c:d}', '.root a /* x */b {c:d}', '.root a/* x */ b {c:d}', '.root a  /**/  b {c:d}']) {
    const r = diffCss(w, '.root a b {c:d}', CFG());
    assert.equal(r.ok, true, w);
    assert.equal(r.counts.added, 0, w);
    assert.equal(r.counts.removed, 0, w);
  }
});

// ---- runda 5: granice i znaczniki komentarzy ----

test('granica glebokosci: 256 poziomow przechodzi, 257 = too-deep', async () => {
  const { checkScope } = await core();
  const mk = (d) => '@a{'.repeat(d) + '.root a{c:d}' + '}'.repeat(d);
  assert.equal(checkScope(mk(256), CFG()).ok, true);
  assert.throws(() => checkScope(mk(257), CFG()), (e) => e.name === 'CssParseError' && e.code === 'too-deep');
});

test('zbedny ] przy pustym stosie: .evil nadal oflagowany (fail-closed)', async () => {
  const { checkScope } = await core();
  const r = checkScope('.evil] , .root a {c:d}', CFG());
  assert.equal(r.ok, false);
  assert.ok(r.bad.some((b) => b.includes('.evil')));
});

test('zbedny ) przy pustym stosie: nie rzuca nieprzechwyconego bledu', async () => {
  const { checkScope } = await core();
  const r = checkScope('.root a) {c:d}', CFG());
  assert.equal(typeof r.ok, 'boolean');
});

test('diffCss: /**/<!-- .root a{c:d} jest flagowane (konserwatywnie)', async () => {
  const { diffCss } = await diff();
  const r = diffCss('/**/<!-- .root a{c:d}', '.root a{c:d}', CFG());
  assert.equal(r.ok, false);
  assert.ok(r.counts.added >= 1);
});

test('diffCss MED-1: spacja bedaca terminatorem hex-escape nie zlewa sie ze znacznikiem komentarza', async () => {
  const { diffCss } = await diff();
  const r = diffCss('.root a\\75 /**/b{c:d}', '.root a\\75 b{c:d}', CFG());
  assert.equal(r.ok, false);
  assert.equal(r.counts.added, 1);
  assert.equal(r.counts.removed, 1);
});

test('diffCss LOW-2: literalny U+0001 nie udaje znacznika komentarza', async () => {
  const { diffCss } = await diff();
  let ok;
  try {
    ok = diffCss('.root a\u0001 b{c:d}', '.root a/**/ b{c:d}', CFG()).ok;
  } catch (e) {
    ok = e.name === 'CssParseError' ? false : 'blad';
  }
  assert.equal(ok, false);
});

test('nawias w url( bez zamkniecia / escape: nigdy ok:true ani niekontrolowany blad', async () => {
  const { checkScope } = await core();
  for (const css of [':is(.evil, url(a ; .root) ) {c:d}', ':is(.evil, \\) ; .root) {c:d}']) {
    let ok = false;
    try {
      ok = checkScope(css, CFG()).ok;
    } catch (e) {
      assert.equal(e.name, 'CssParseError', css);
    }
    assert.notEqual(ok, true, css);
  }
});
