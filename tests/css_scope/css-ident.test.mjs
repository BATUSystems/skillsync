// Granica url-tokenu (CSS Syntax): `u` zaczyna url-token tylko gdy zdekodowany identyfikator (escape rozwiniete,
// bez wielkosci liter) to `url` i bezposrednio nastepuje `(`. `#url` (hash), `@url` (at-keyword) i identyfikator
// z escape-poprzednikiem (`\{url`, `\61 url`) NIE sa url-tokenem; `\75rl(` jest. Wejscia poza korzeniem to
// obejscia fail-open (w przegladarce `.evil` jest aktywna regula, parser mial ok:true).
import test from 'node:test';
import assert from 'node:assert/strict';

const LIB = '../../library/skills/ui-verify/scripts/lib/';
const parse = () => import(`${LIB}css-parse.mjs`);
const core = () => import(`${LIB}css-scope-core.mjs`);
const diff = () => import(`${LIB}css-diff.mjs`);

const CONFIG = { root: '.root', exceptions: [] };

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

// ---- N-A: `u` poprzedzone znakiem, ktory czyni z url zwykly identyfikator / czesc innej jednostki ----

const NA = {
  'at-keyword @url': '@url(a")") {} .evil{color:red} /* " */',
  'hash #url': '.root a{ x: #url(a")") } .evil{color:red}/* " */',
  'escape \\{url': '.root a{ x: \\{url(a")") } .evil{color:red}/* " */',
  'escape \\)url': '.root a{ x: \\)url(a")") } .evil{color:red}/* " */',
  'escape \\ url (spacja)': '.root a{ x: \\ url(a")") } .evil{color:red}/* " */',
  'hex-escape z spacja \\61 url': '.root a{ x: \\61 url(a")") } .evil{color:red}/* " */',
};
for (const [name, css] of Object.entries(NA)) {
  test(`N-A checkScope: ${name} => ok:false i .evil w bad`, async () => {
    const { checkScope } = await core();
    const r = checkScope(css, CONFIG);
    assert.equal(r.ok, false, `ok:true dla ${css}`);
    assert.ok(r.bad.some((b) => b.includes('.evil')), `bad bez .evil: ${JSON.stringify(r.bad)}`);
  });
}

test('N-A diffCss: @url(a")") {} .evil{...} daje addedOutside z .evil', async () => {
  const { diffCss } = await diff();
  const r = diffCss(NA['at-keyword @url'], '', CONFIG);
  assert.equal(r.ok, false);
  assert.ok(r.addedOutside.some((d) => d.includes('.evil')), JSON.stringify(r.addedOutside));
});

test('N-A scan: #url(a")") - .evil jest regula najwyzszego poziomu', async () => {
  const { scan } = await parse();
  const r = scan('.root a{ x: #url(a")") } .evil{color:red} /* " */ ');
  assert.deepEqual(selectors(r.items), ['.root a', '.evil']);
});

const NOT_URL_PREFIX = { '#url': '#url', '@url': '@url', '\\{url': '\\{url', '\\61 url': '\\61 url' };
for (const [name, pre] of Object.entries(NOT_URL_PREFIX)) {
  test(`${name}(a{b) NIE jest url-tokenem: \`{\` liczy sie strukturalnie`, async () => {
    const { countBraces } = await parse();
    assert.equal(countBraces(`.root a { x: ${pre}(a{b) }`), 2);
  });
}

test('hash #url(a) to hash + blok: struktura i liczniki jak dla zwyklego tekstu', async () => {
  const { scan, countBraces, countImportant, splitDeclarations } = await parse();
  const { checkScope } = await core();
  const css = '.root a { x: #url(a) } .root b { c: d }';
  const r = scan(css);
  assert.deepEqual(selectors(r.items), ['.root a', '.root b']);
  assert.equal(r.items.every((i) => !i.nested || i.nested.length === 0), true);
  assert.equal(countBraces(css), 2);
  assert.equal(countImportant('.root a { x: #url(a) !important }'), 1);
  assert.deepEqual(splitDeclarations('x: #url(a;b); c: d'), ['x: #url(a;b)', 'c: d']);
  assert.equal(checkScope(css, CONFIG).ok, true);
});

// ---- N-B: url zapisane z escape jest url-tokenem ----

const NB = {
  '\\75rl(': '\\75rl(x{y)',
  'u\\72l(': 'u\\72l(x{y)',
  '\\url(': '\\url(x{y)',
  'ur\\6c ( (hex + spacja)': 'ur\\6c (x{y)',
  '\\55 RL( (wielkie)': '\\55 RL(x{y)',
};
for (const [name, tok] of Object.entries(NB)) {
  test(`N-B ${name}: url-token, klamra w srodku nie liczy sie (scan, countBraces)`, async () => {
    const { scan, countBraces } = await parse();
    const css = `.root a { b: ${tok} } .root c { d: e }`;
    assert.deepEqual(selectors(scan(css).items), ['.root a', '.root c']);
    assert.equal(countBraces(css), 2);
  });
}

test('N-B: .root a { x: \\75rl(/*) } .evil { c: r } /* */ } => .evil widoczne (ok:false)', async () => {
  const { checkScope } = await core();
  const { diffCss } = await diff();
  const { scan } = await parse();
  const css = '.root a { x: \\75rl(/*) } .evil { c: r } /* */';
  const r = checkScope(css, CONFIG);
  assert.equal(r.ok, false, `ok:true dla ${css}`);
  assert.ok(r.bad.some((b) => b.includes('.evil')), JSON.stringify(r.bad));
  assert.ok(selectors(scan('.root a { x: \\75rl(/*) } .evil { c: r } /* */ ').items).includes('.evil'));
  const d = diffCss(css, '', CONFIG);
  assert.ok(d.addedOutside.some((x) => x.includes('.evil')), JSON.stringify(d.addedOutside));
});

test('N-B: separatory w \\75rl( nie dziela; unterminated-url z escape-nazwa', async () => {
  const { splitDeclarations, scan, CssParseError } = await parse();
  assert.deepEqual(splitDeclarations('a: \\75rl(x;y); b: c'), ['a: \\75rl(x;y)', 'b: c']);
  let e = null;
  try {
    scan('.root a { b: \\75rl(abc }');
  } catch (x) {
    e = x;
  }
  assert.ok(e instanceof CssParseError && e.code === 'unterminated-url', String(e));
});

test('granica: myurl(x{y) i -url(x{y) nadal nie sa url (dwie klamry)', async () => {
  const { countBraces } = await parse();
  assert.equal(countBraces('.root a { b: myurl(x{y) }'), 2);
  assert.equal(countBraces('.root a { b: -url(x{y) }'), 2);
  assert.equal(countBraces('.root a { b: url(x{y) }'), 1);
});

// ---- miejsca dotad niepokryte: preludium (scan), collapse, normalizeDeclaration ----

test('preludium: `@import url(a{b);` - url-token, `;` konczy instrukcje, .root c widoczne', async () => {
  const { scan } = await parse();
  const r = scan('@import url(a{b); .root c { d: e }');
  assert.deepEqual(r.items.map((i) => i.kind), ['statement', 'rule']);
  assert.equal(r.items[0].text, '@import url(a{b)');
  assert.deepEqual(selectors(r.items), ['.root c']);
});

test('preludium: `@supports (background:url(x{y))` - klamra w url nie otwiera bloku, grupa zawiera .root a', async () => {
  const { scan } = await parse();
  const r = scan('@supports (background:url(x{y)) { .root a { b: c } } .evil { c: d }');
  assert.equal(r.items.length, 2);
  assert.equal(r.items[0].kind, 'group');
  assert.equal(r.items[0].head, '@supports (background:url(x{y))');
  assert.deepEqual(selectors(r.items[0].children), ['.root a']);
  assert.deepEqual(selectors(r.items.slice(1)), ['.evil']);
});

test('collapseWhitespace: wnetrze niecytowanego url zachowane bez zwijania, poza url zwija', async () => {
  const { collapseWhitespace } = await parse();
  assert.equal(collapseWhitespace('  a   url( x   y )   b  '), 'a url( x   y ) b');
  assert.equal(collapseWhitespace('  a   \\75rl( x   y )   b  '), 'a \\75rl( x   y ) b');
});

test('normalizeDeclaration: bialych znakow w niecytowanym url nie usuwa, poza url usuwa (kontrakt: wnetrze url nietkniete)', async () => {
  const { normalizeDeclaration } = await parse();
  assert.equal(normalizeDeclaration('b: url( x y ) !important'), 'b:url( x y )!important');
  assert.equal(normalizeDeclaration('b: a  c  !important'), 'b:ac!important');
  assert.equal(normalizeDeclaration('b: \\75rl( x y ) z'), 'b:\\75rl( x y )z');
  assert.equal(normalizeDeclaration('b: #url( x y ) z'), 'b:#url(xy)z');
});

// ---- `url(` w komentarzu i napisie nie jest url; `{` po nich liczy sie strukturalnie ----

test('`/* url( */` to komentarz: po nim `{` liczy sie, regula widoczna', async () => {
  const { scan, countBraces } = await parse();
  const css = '/* url( */ .root a { b: c } .evil { d: e }';
  assert.deepEqual(selectors(scan(css).items), ['.root a', '.evil']);
  assert.equal(countBraces(css), 2);
});

test('`"url("` to napis: po nim `{` liczy sie, regula widoczna', async () => {
  const { scan, countBraces } = await parse();
  const css = '.root a { content: "url(" } .evil { d: e }';
  assert.deepEqual(selectors(scan(css).items), ['.root a', '.evil']);
  assert.equal(countBraces(css), 2);
  assert.equal(countBraces(".root a { content: 'url(' } .b { d: e }"), 2);
});
