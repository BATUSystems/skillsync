// Niecytowany url(...) wg CSS Syntax to jeden token: `{`, `}`, `/*`, `"`, `'`, `,`, `;`, `!important` w srodku
// sa zwyklymi znakami. Obejscia fail-open (przegladarka widzi regule, parser nie) maja tu testy czerwone przed poprawka.
import test from 'node:test';
import assert from 'node:assert/strict';

const LIB = '../../library/skills/ui-verify/scripts/lib/';
const parse = () => import(`${LIB}css-parse.mjs`);
const core = () => import(`${LIB}css-scope-core.mjs`);
const diff = () => import(`${LIB}css-diff.mjs`);

const CONFIG = { root: '.root', exceptions: [] };

const thrown = (fn) => {
  try {
    fn();
  } catch (e) {
    return e;
  }
  return null;
};
const selectors = (items) => {
  const out = [];
  const walk = (list) => {
    for (const it of list) {
      if (it.kind === 'rule') out.push(it.selector);
      if (it.children) walk(it.children);
      if (it.items) walk(it.items);
    }
  };
  walk(items);
  return out;
};

// ---- obejscia fail-open (1-4) przez checkScope ----

const EVASIONS = {
  'font-face z url(x{y)': '@font-face { src: url(x{y) } .evil { color: red }',
  'keyframes z url(x{y)': '@keyframes k { from { a: url(x{y) } } .evil { color: red }',
  'komentarz w url': '.root a { background: url(x/*) } .evil { color: red } .root b { background: url(y*/) }',
  'cudzyslow w url': '.root a { background: url(a"b) } .evil { color: red } .root c { content: "}" }',
};
for (const [name, css] of Object.entries(EVASIONS)) {
  test(`checkScope: ${name} => .evil widoczne jako regula poza korzeniem (ok:false)`, async () => {
    const { checkScope } = await core();
    const r = checkScope(css, CONFIG);
    assert.equal(r.ok, false, `ok:true dla ${css}`);
    assert.ok(r.bad.some((b) => b.includes('.evil')), `bad bez .evil: ${JSON.stringify(r.bad)}`);
  });
}

test('scan: font-face z url(x{y) - .evil jest regula najwyzszego poziomu (nie wchlonieta przez at-block)', async () => {
  const { scan } = await parse();
  const r = scan(EVASIONS['font-face z url(x{y)']);
  assert.ok(selectors(r.items).includes('.evil'), JSON.stringify(r.items));
});

test('diffCss: wejscie 1 (font-face z url(x{y) + .evil) daje addedOutside z .evil', async () => {
  const { diffCss } = await diff();
  const r = diffCss(EVASIONS['font-face z url(x{y)'], '', CONFIG);
  assert.equal(r.ok, false);
  assert.ok(r.addedOutside.some((d) => d.includes('.evil')), JSON.stringify(r.addedOutside));
});

test('diffCss: wejscie 3 (komentarz w url) daje addedOutside z .evil', async () => {
  const { diffCss } = await diff();
  const r = diffCss(EVASIONS['komentarz w url'], '', CONFIG);
  assert.equal(r.ok, false);
  assert.ok(r.addedOutside.some((d) => d.includes('.evil')), JSON.stringify(r.addedOutside));
});

// ---- legalny niecytowany url nie psuje struktury ----

const LEGAL = {
  klamra: '.root a { background: url(x{y) } .root b { c: d }',
  'klamra zamykajaca': '.root a { background: url(x}y) } .root b { c: d }',
  'otwarcie komentarza': '.root a { background: url(x/*y) } .root b { c: d }',
  cudzyslow: '.root a { background: url(a"b) } .root b { c: d }',
  apostrof: ".root a { background: url(a'b) } .root b { c: d }",
};
for (const [name, css] of Object.entries(LEGAL)) {
  test(`legalny niecytowany url (${name}): scan bez bledu, 2 reguly, bez nested; checkScope ok`, async () => {
    const { scan } = await parse();
    const { checkScope } = await core();
    const r = scan(css);
    assert.deepEqual(selectors(r.items), ['.root a', '.root b']);
    assert.equal(r.items.every((i) => !i.nested || i.nested.length === 0), true);
    const c = checkScope(css, CONFIG);
    assert.equal(c.ok, true, JSON.stringify(c.bad));
  });
}

test('url z `*/` po `/*` (komentarz w url) nie zjada reguly: obie reguly .root widoczne', async () => {
  const { scan } = await parse();
  const r = scan('.root a { background: url(x/*) } .root b { background: url(y*/) }');
  assert.deepEqual(selectors(r.items), ['.root a', '.root b']);
});

test('countBraces nie liczy `{` wewnatrz niecytowanego url', async () => {
  const { countBraces } = await parse();
  assert.equal(countBraces('.root a { background: url(x{y{z) }'), 1);
  assert.equal(countBraces('.root a { background: url("x{y") }'), 1);
});

test('countImportant nie liczy `!important` wewnatrz niecytowanego url, liczy poza nim', async () => {
  const { countImportant } = await parse();
  assert.equal(countImportant('.root a { background: url(x!important) }'), 0);
  assert.equal(countImportant('.root a { background: url(x!important); color: red !important }'), 1);
});

test('splitSelectors / splitDeclarations nie dziela po , i ; wewnatrz niecytowanego url', async () => {
  const { splitSelectors, splitDeclarations } = await parse();
  assert.deepEqual(splitDeclarations('background: url(data:image/png;base64,AAA); color: red'), ['background: url(data:image/png;base64,AAA)', 'color: red']);
  assert.deepEqual(splitSelectors('.root [a=url(x,y)], .root b'), ['.root [a=url(x,y)]', '.root b']);
  assert.deepEqual(splitDeclarations('a: url(x;y);b: c'), ['a: url(x;y)', 'b: c']);
  assert.deepEqual(splitDeclarations('a: url(x"y;z); b: c'), ['a: url(x"y;z)', 'b: c']);
  assert.deepEqual(splitDeclarations("a: url(x'y;z); b: c"), ["a: url(x'y;z)", 'b: c']);
  assert.deepEqual(splitSelectors('.root [a=url(x"y,z)], .root b'), ['.root [a=url(x"y,z)]', '.root b']);
  assert.deepEqual(splitDeclarations('a: url(x/*y;z); b: c'), ['a: url(x/*y;z)', 'b: c']);
});

// ---- url z napisem pozostaje zwykla funkcja ----

test('url( "a{b" ) i url(\'x\') to napisy: struktura jak dotad, klamra w napisie nie liczy sie', async () => {
  const { scan, countBraces } = await parse();
  const css = '.root a { background: url( "a{b" ) } .root b { background: url(\'x\') }';
  assert.deepEqual(selectors(scan(css).items), ['.root a', '.root b']);
  assert.equal(countBraces(css), 2);
});

test('url("x") z `}` po napisie: napis konczy sie normalnie, regula zamknieta', async () => {
  const { scan } = await parse();
  assert.deepEqual(selectors(scan('.root a { b: url("x)y") } .evil { c: d }').items), ['.root a', '.evil']);
});

// ---- wielkosc liter, escape, granica identyfikatora ----

test('URL(x{y) wielkimi literami to url-token', async () => {
  const { scan } = await parse();
  assert.deepEqual(selectors(scan('.root a { b: URL(x{y) } .root c { d: e }').items), ['.root a', '.root c']);
  assert.deepEqual(selectors(scan('.root a { b: Url(x}y) } .evil { d: e }').items), ['.root a', '.evil']);
});

test('url(x\\)y{) - escapowany `)` nie konczy tokenu', async () => {
  const { scan, countBraces } = await parse();
  const css = '.root a { b: url(x\\)y{) } .evil { d: e }';
  assert.deepEqual(selectors(scan(css).items), ['.root a', '.evil']);
  assert.equal(countBraces(css), 2);
});

test('myurl(x{y) NIE jest url-tokenem: klamra otwiera zagniezdzenie jak zwykle', async () => {
  const { scan } = await parse();
  const { checkScope } = await core();
  const css = '.root a { b: myurl(x{y) }';
  const e = thrown(() => scan(css));
  const bad = e ? [] : checkScope(css, CONFIG).bad;
  assert.ok(e !== null || bad.some((b) => /nested rule|unbalanced/.test(b)) || scan(css).items.some((i) => i.nested && i.nested.length), 'struktura jak dla zwyklej klamry');
});

test('xurl-podobne: `-url(x{y)` tez nie jest url-tokenem (liczy sie jako identyfikator)', async () => {
  const { countBraces } = await parse();
  assert.equal(countBraces('.root a { b: -url(x{y) }'), 2);
  assert.equal(countBraces('.root a { b: myurl(x{y) }'), 2);
});

// ---- brak zamkniecia ----

test('url( bez ) => CssParseError unterminated-url z line/column', async () => {
  const { scan, CssParseError } = await parse();
  const e = thrown(() => scan('.root a {\n  background: url(abc{ }'));
  assert.ok(e instanceof CssParseError, String(e));
  assert.equal(e.code, 'unterminated-url');
  assert.equal(e.line, 2);
  assert.ok(e.column >= 15 && e.column <= 19, `column ${e.column}: oczekiwana pozycja url( (15) .. pierwszy znak zawartosci (19)`);
});

test('url( bez ) z escape na koncu => unterminated-url (escape `\\)` nie zamyka)', async () => {
  const { scan } = await parse();
  const e = thrown(() => scan('.root a { b: url(x\\) }'));
  assert.ok(e && e.code === 'unterminated-url', String(e));
});

test('CssParseError unterminated-url niesie sie przez checkScope (fail-closed, nie ok:true)', async () => {
  const { checkScope } = await core();
  const e = thrown(() => checkScope('.root a { b: url(x }', CONFIG));
  assert.ok(e && e.code === 'unterminated-url', String(e));
});
