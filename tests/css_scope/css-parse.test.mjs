import test from 'node:test';
import assert from 'node:assert/strict';

// Dynamiczny import: brak modulu daje czerwony test, nie blad calego pliku.
const load = () => import('../../library/skills/ui-verify/scripts/lib/css-parse.mjs');

const thrown = (fn) => {
  try {
    fn();
  } catch (e) {
    return e;
  }
  return null;
};

// ---- scan: drzewo wezlow ----

test('scan: zwraca clean, rules, items; pusta regula a{} ma body pusty', async () => {
  const { scan } = await load();
  const r = scan('a{}');
  assert.ok('clean' in r && 'rules' in r && 'items' in r);
  assert.equal(r.items.length, 1);
  const n = r.items[0];
  assert.equal(n.kind, 'rule');
  assert.equal(n.selector, 'a');
  assert.equal(n.body, '');
  assert.equal(n.line, 1);
  assert.deepEqual(n.context, []);
  assert.equal(n.inKeyframes, false);
});

test('scan: pusty tekst i same biale znaki => brak wezlow', async () => {
  const { scan } = await load();
  assert.deepEqual(scan('').items, []);
  assert.deepEqual(scan('  \n\t ').items, []);
});

test('scan: forma skompresowana, selektor zwiniety, body surowe bez klamr', async () => {
  const { scan } = await load();
  const { items } = scan('.root a{color:red}.root b{color:blue}');
  assert.equal(items.length, 2);
  assert.equal(items[0].selector, '.root a');
  assert.equal(items[0].body, 'color:red');
  assert.equal(items[1].selector, '.root b');
  assert.equal(items[1].body, 'color:blue');
});

test('scan: biale znaki w selektorze zwiniete do jednej spacji i przyciete', async () => {
  const { scan } = await load();
  const { items } = scan('\n  .root   a\n\t>   b ,\n  .root c   {\n  color: red;\n}\n');
  assert.equal(items[0].selector, '.root a > b , .root c');
  assert.match(items[0].body, /color:\s*red;/);
});

test('scan: numery linii regul (LF)', async () => {
  const { scan } = await load();
  const { items } = scan('a{}\n\nb{\n  c:d\n}\ne{}');
  assert.deepEqual(items.map((i) => i.line), [1, 3, 6]);
});

test('scan: numery linii regul (CRLF)', async () => {
  const { scan } = await load();
  const { items } = scan('a{}\r\n\r\nb{\r\n  c:d\r\n}\r\ne{}');
  assert.deepEqual(items.map((i) => i.line), [1, 3, 6]);
});

test('scan: grupa @media z regulami i kontekstem', async () => {
  const { scan } = await load();
  const { items } = scan('@media   (min-width: 1px) {\n  .root a { color: red }\n  .root b{}\n}');
  assert.equal(items.length, 1);
  const g = items[0];
  assert.equal(g.kind, 'group');
  assert.equal(g.head, '@media (min-width: 1px)');
  assert.equal(g.line, 1);
  assert.equal(g.children.length, 2);
  assert.equal(g.children[0].kind, 'rule');
  assert.equal(g.children[0].selector, '.root a');
  assert.equal(g.children[0].line, 2);
  assert.deepEqual(g.children[0].context, ['@media (min-width: 1px)']);
  assert.equal(g.children[0].inKeyframes, false);
});

test('scan: zagniezdzone @supports w @media; kontekst zachowuje kolejnosc od zewnetrznej', async () => {
  const { scan } = await load();
  const { items } = scan('@media (a:b){@supports (display:grid){.root x{y:z}}}');
  const outer = items[0];
  assert.equal(outer.kind, 'group');
  const inner = outer.children[0];
  assert.equal(inner.kind, 'group');
  assert.equal(inner.head, '@supports (display:grid)');
  const rule = inner.children[0];
  assert.equal(rule.selector, '.root x');
  assert.deepEqual(rule.context, ['@media (a:b)', '@supports (display:grid)']);
});

test('scan: pozostale grupy (@document, @-moz-document, @layer, @container, @scope, @starting-style)', async () => {
  const { scan } = await load();
  const heads = ['@document url(x)', '@-moz-document url-prefix()', '@layer base', '@container (min-width: 1px)', '@scope (.root)', '@starting-style'];
  for (const h of heads) {
    const { items } = scan(`${h}{.root a{b:c}}`);
    assert.equal(items.length, 1, h);
    assert.equal(items[0].kind, 'group', h);
    assert.equal(items[0].head, h, h);
    assert.equal(items[0].children[0].selector, '.root a', h);
    assert.deepEqual(items[0].children[0].context, [h], h);
  }
});

test('scan: @keyframes z krokami (from, to, procent, lista)', async () => {
  const { scan } = await load();
  const { items } = scan('@keyframes spin{from{a:b}50%{c:d}0%, 100%{e:f}to{g:h}}');
  assert.equal(items.length, 1);
  const k = items[0];
  assert.equal(k.kind, 'keyframes');
  assert.equal(k.head, '@keyframes spin');
  assert.deepEqual(k.steps.map((s) => s.selector), ['from', '50%', '0%, 100%', 'to']);
  assert.deepEqual(k.steps.map((s) => s.body), ['a:b', 'c:d', 'e:f', 'g:h']);
  assert.ok(k.steps.every((s) => typeof s.line === 'number'));
});

test('scan: warianty prefiksow @-webkit-keyframes i @-moz-keyframes', async () => {
  const { scan } = await load();
  for (const head of ['@-webkit-keyframes x', '@-moz-keyframes x']) {
    const { items } = scan(`${head}{from{a:b}}`);
    assert.equal(items[0].kind, 'keyframes', head);
    assert.equal(items[0].head, head);
    assert.equal(items[0].steps.length, 1);
  }
});

test('scan: @keyframes wewnatrz @media pozostaje wezlem keyframes w dzieciach grupy', async () => {
  const { scan } = await load();
  const { items } = scan('@media x{@keyframes k{from{a:b}}.root a{c:d}}');
  const kinds = items[0].children.map((c) => c.kind);
  assert.deepEqual(kinds, ['keyframes', 'rule']);
});

test('scan: at-block (@font-face, @page, @property, @counter-style, @font-feature-values)', async () => {
  const { scan } = await load();
  const cases = [
    ['@font-face', 'font-family:x;src:url(a.woff)'],
    ['@page :first', 'margin:0'],
    ['@property --x', "syntax:'*'; inherits:false"],
    ['@counter-style c', 'system:cyclic'],
    ['@font-feature-values Foo', '@swash{ornate:1}'],
  ];
  for (const [head, body] of cases) {
    const { items } = scan(`${head}{${body}}`);
    assert.equal(items.length, 1, head);
    assert.equal(items[0].kind, 'at-block', head);
    assert.equal(items[0].head, head);
    assert.equal(items[0].body, body);
    assert.equal(items[0].line, 1);
  }
});

test('scan: statement dla @charset, @import, @namespace', async () => {
  const { scan } = await load();
  const { items } = scan('@charset "utf-8";\n@import url("a.css") screen;\n@namespace svg url(x);\n.root a{b:c}');
  assert.equal(items.length, 4);
  assert.deepEqual(items.slice(0, 3).map((i) => i.kind), ['statement', 'statement', 'statement']);
  assert.match(items[0].text, /^@charset\s+"utf-8"/);
  assert.match(items[1].text, /^@import\s+url\("a\.css"\)\s+screen/);
  assert.match(items[2].text, /^@namespace\s+svg/);
  assert.deepEqual(items.slice(0, 3).map((i) => i.line), [1, 2, 3]);
  assert.equal(items[3].kind, 'rule');
});

test('scan: srednik w napisie statementu nie konczy go', async () => {
  const { scan } = await load();
  const { items } = scan('@import "a;b.css";\n.root x{y:z}');
  assert.equal(items.length, 2);
  assert.equal(items[0].kind, 'statement');
  assert.match(items[0].text, /a;b\.css/);
  assert.equal(items[1].selector, '.root x');
});

// ---- scan: komentarze, napisy, escape, nawiasy ----

test('scan: komentarz z klamra i selektorem jest ignorowany', async () => {
  const { scan } = await load();
  const { items, clean } = scan('/* outside{ color:red } */ .root a{b:c} /* tail } */');
  assert.equal(items.length, 1);
  assert.equal(items[0].selector, '.root a');
  assert.ok(!clean.includes('outside'));
  assert.ok(!clean.includes('tail'));
});

test('scan: komentarz miedzy czesciami selektora i w ciele nie psuje wyniku', async () => {
  const { scan } = await load();
  const { items } = scan('.root /* x{y} */ a{color:red;/* z:w; */ b:c}');
  assert.equal(items[0].selector, '.root a');
  assert.ok(!items[0].body.includes('z:w'));
  assert.match(items[0].body, /color:red/);
  assert.match(items[0].body, /b:c/);
});

test('scan: komentarze wieloliniowe zachowuja numery linii (clean i reguly po nich)', async () => {
  const { scan } = await load();
  const text = '/* a\n b\r\n c */\n/*\n\n*/\n.root a{b:c}';
  const { items, clean } = scan(text);
  assert.equal(items[0].line, 7);
  assert.equal(clean.split('\n').length, text.split('\n').length);
  assert.ok(!/[abc] \*\//.test(clean));
});

test('scan: clean ma te sama dlugosc co wejscie (komentarze zastapione spacjami)', async () => {
  const { scan } = await load();
  const text = 'a{b:c}/* hello\nworld */d{e:f}';
  const { clean } = scan(text);
  assert.equal(clean.length, text.length);
  assert.equal(clean.indexOf('\n'), text.indexOf('\n'));
  assert.ok(!clean.includes('hello'));
});

test('scan: napis z klamra w ciele nie zmienia struktury', async () => {
  const { scan } = await load();
  const { items } = scan('.root a::before{content:"{"}.root b::after{content:"}"}');
  assert.equal(items.length, 2);
  assert.equal(items[0].selector, '.root a::before');
  assert.equal(items[0].body, 'content:"{"');
  assert.equal(items[1].body, 'content:"}"');
});

test('scan: napis z przecinkiem i nawiasem; apostrofy', async () => {
  const { scan } = await load();
  const { items } = scan(".root a{content:\",\"}.root b{content:')'}.root c{content:'(' }");
  assert.equal(items.length, 3);
  assert.equal(items[0].body, 'content:","');
  assert.equal(items[1].body, "content:')'");
  assert.equal(items[2].selector, '.root c');
});

test('scan: escape \\ wewnatrz napisu (cudzyslow i odwrotny ukosnik)', async () => {
  const { scan } = await load();
  const a = scan(String.raw`.root a{content:"x\"}"}.root b{c:d}`);
  assert.equal(a.items.length, 2);
  assert.equal(a.items[0].body, String.raw`content:"x\"}"`);
  const b = scan(String.raw`.root a{content:"x\\"}.root b{c:d}`);
  assert.equal(b.items.length, 2);
  assert.equal(b.items[0].body, String.raw`content:"x\\"`);
  assert.equal(b.items[1].selector, '.root b');
});

test('scan: escape \\ poza napisami pomija nastepny znak (klamra w selektorze)', async () => {
  const { scan } = await load();
  const { items } = scan(String.raw`.root .a\{b{c:d}`);
  assert.equal(items.length, 1);
  assert.equal(items[0].selector, String.raw`.root .a\{b`);
  assert.equal(items[0].body, 'c:d');
});

test('scan: klamra i przecinek w napisie atrybutu selektora', async () => {
  const { scan } = await load();
  const { items } = scan('.root [data-x="{"] a{b:c}[data-y="a,b"], .root d{e:f}');
  assert.equal(items.length, 2);
  assert.equal(items[0].selector, '.root [data-x="{"] a');
  assert.equal(items[1].selector, '[data-y="a,b"], .root d');
});

test('scan: nawiasy zagniezdzone i :where(a, b) w selektorze', async () => {
  const { scan } = await load();
  const { items } = scan('.root :where(a, b) c, .root :not(:is(d, e)) f{g:h}');
  assert.equal(items.length, 1);
  assert.equal(items[0].selector, '.root :where(a, b) c, .root :not(:is(d, e)) f');
});

test('scan: srednik i klamra w url(...) i nawiasie ciala', async () => {
  const { scan } = await load();
  const { items } = scan('.root a{background:url(data:image/png;base64,AAA)}.root b{c:d}');
  assert.equal(items.length, 2);
  assert.equal(items[0].body, 'background:url(data:image/png;base64,AAA)');
});

test('scan: CRLF w ciele i miedzy regulami nie psuje wyniku', async () => {
  const { scan } = await load();
  const { items } = scan('.root a {\r\n  color: red;\r\n}\r\n@media x {\r\n  .root b {\r\n    c: d;\r\n  }\r\n}\r\n');
  assert.equal(items.length, 2);
  assert.equal(items[0].line, 1);
  assert.equal(items[1].kind, 'group');
  assert.equal(items[1].line, 4);
  assert.equal(items[1].children[0].line, 5);
});

// ---- scan: bledy ----

test('CssParseError: klasa dziedziczy po Error, ma name i pola', async () => {
  const { scan, CssParseError } = await load();
  const e = thrown(() => scan('a{'));
  assert.ok(e instanceof CssParseError);
  assert.ok(e instanceof Error);
  assert.equal(typeof e.line, 'number');
  assert.equal(typeof e.column, 'number');
  assert.equal(typeof e.code, 'string');
});

test('blad unbalanced-brace: brak klamry zamykajacej, pozycja niedomknietego {', async () => {
  const { scan, CssParseError } = await load();
  const e = thrown(() => scan('.a{\n.b{}\n'));
  assert.ok(e instanceof CssParseError);
  assert.equal(e.code, 'unbalanced-brace');
  assert.equal(e.line, 1);
  assert.equal(e.column, 3);
  assert.match(e.message, /line 1/i);
});

test('blad unbalanced-brace: niedomknieta grupa @media', async () => {
  const { scan } = await load();
  const e = thrown(() => scan('@media x{ a{} '));
  assert.equal(e.code, 'unbalanced-brace');
  assert.equal(e.line, 1);
  assert.equal(e.column, 9);
});

test('blad unexpected-close: nadmiarowa } (linia i kolumna, LF)', async () => {
  const { scan } = await load();
  const e = thrown(() => scan('a{}  }'));
  assert.equal(e.code, 'unexpected-close');
  assert.equal(e.line, 1);
  assert.equal(e.column, 6);
  assert.match(e.message, /line 1/i);
});

test('blad unexpected-close: numer linii przy CRLF', async () => {
  const { scan } = await load();
  const e = thrown(() => scan('a{}\r\n\r\n}'));
  assert.equal(e.code, 'unexpected-close');
  assert.equal(e.line, 3);
  assert.equal(e.column, 1);
  assert.match(e.message, /line 3/i);
});

test('blad unexpected-close: sama } na poczatku', async () => {
  const { scan } = await load();
  const e = thrown(() => scan('}'));
  assert.equal(e.code, 'unexpected-close');
  assert.equal(e.line, 1);
  assert.equal(e.column, 1);
});

test('blad unterminated-string: pozycja otwierajacego cudzyslowu', async () => {
  const { scan } = await load();
  const e = thrown(() => scan('a{content:"abc'));
  assert.equal(e.code, 'unterminated-string');
  assert.equal(e.line, 1);
  assert.equal(e.column, 11);
  assert.match(e.message, /line 1/i);
});

test('blad unterminated-string: apostrof w drugiej linii; escape zjada zamykajacy znak', async () => {
  const { scan } = await load();
  const e1 = thrown(() => scan("a{}\nb{content:'x"));
  assert.equal(e1.code, 'unterminated-string');
  assert.equal(e1.line, 2);
  const e2 = thrown(() => scan(String.raw`a{content:"x\"`));
  assert.equal(e2.code, 'unterminated-string');
});

test('blad unterminated-comment: pozycja poczatku komentarza', async () => {
  const { scan } = await load();
  const e = thrown(() => scan('a{}\n/* x'));
  assert.equal(e.code, 'unterminated-comment');
  assert.equal(e.line, 2);
  assert.equal(e.column, 1);
  assert.match(e.message, /line 2/i);
});

test('klamra w komentarzu i napisie nie liczy sie do bilansu (brak bledu)', async () => {
  const { scan } = await load();
  assert.doesNotThrow(() => scan('/* { */ a{content:"{"} /* } */'));
});

// ---- splitSelectors ----

test('splitSelectors: podzial po przecinkach najwyzszego poziomu, przyciecie', async () => {
  const { splitSelectors } = await load();
  assert.deepEqual(splitSelectors('.a, .b ,  .c'), ['.a', '.b', '.c']);
  assert.deepEqual(splitSelectors('.a'), ['.a']);
});

test('splitSelectors: przecinek w nawiasie jest ignorowany (:where, :is, zagniezdzone)', async () => {
  const { splitSelectors } = await load();
  assert.deepEqual(splitSelectors(':where(a, b) c, d'), [':where(a, b) c', 'd']);
  assert.deepEqual(splitSelectors(':not(:is(a, b), c) d, e'), [':not(:is(a, b), c) d', 'e']);
});

test('splitSelectors: przecinek w atrybucie i w napisie atrybutu', async () => {
  const { splitSelectors } = await load();
  assert.deepEqual(splitSelectors('[data-x="a,b"], c'), ['[data-x="a,b"]', 'c']);
  assert.deepEqual(splitSelectors("[data-x='a,b'],[y=\"]\"] z"), ["[data-x='a,b']", '[y="]"] z']);
  assert.deepEqual(splitSelectors('[a=b,c] d, e'), ['[a=b,c] d', 'e']);
});

test('splitSelectors: puste elementy pomijane; pusty wejsciowy => []', async () => {
  const { splitSelectors } = await load();
  assert.deepEqual(splitSelectors('a,, ,b,'), ['a', 'b']);
  assert.deepEqual(splitSelectors(''), []);
  assert.deepEqual(splitSelectors('  ,  '), []);
});

// ---- countBraces / countImportant ----

test('countBraces: liczy tylko otwarcia { (grupy, reguly, keyframes)', async () => {
  const { countBraces } = await load();
  assert.equal(countBraces(''), 0);
  assert.equal(countBraces('a{}'), 1);
  assert.equal(countBraces('a{}b{}c{}'), 3);
  assert.equal(countBraces('@media x{a{}b{}}'), 3);
  assert.equal(countBraces('@keyframes k{from{a:b}to{c:d}}'), 3);
});

test('countBraces: komentarze i napisy poza liczeniem', async () => {
  const { countBraces } = await load();
  assert.equal(countBraces('/* { { { */ a{}'), 1);
  assert.equal(countBraces('a{content:"{{"}'), 1);
  assert.equal(countBraces("a{content:'{'}b{}"), 2);
  assert.equal(countBraces(String.raw`a{content:"\"{"}`), 1);
  assert.equal(countBraces('a{} /* b{} */\n/*\n c{}\n*/ d{}'), 2);
});

test('countImportant: liczy !important, spacje i wielkosc liter', async () => {
  const { countImportant } = await load();
  assert.equal(countImportant(''), 0);
  assert.equal(countImportant('a{b:c !important}'), 1);
  assert.equal(countImportant('a{b:c!important}'), 1);
  assert.equal(countImportant('a{b:c ! important}'), 1);
  assert.equal(countImportant('a{b:c !IMPORTANT;d:e!Important}'), 2);
  assert.equal(countImportant('a{b:c!important}d{e:f !important}'), 2);
});

test('countImportant: komentarze i napisy poza liczeniem', async () => {
  const { countImportant } = await load();
  assert.equal(countImportant('/* !important */ a{b:c}'), 0);
  assert.equal(countImportant('a{content:"!important"}'), 0);
  assert.equal(countImportant("a{content:'x !important'}"), 0);
  assert.equal(countImportant('a{b:c !important; /* d:e !important */ f:g !important}'), 2);
  assert.equal(countImportant('/*\n!important\n*/ a{b:c !important}'), 1);
});

test('countImportant: bez wykrzyknika nie liczy slowa important', async () => {
  const { countImportant } = await load();
  assert.equal(countImportant('a{b:important}'), 0);
  assert.equal(countImportant('.important{b:c}'), 0);
});

// ---- splitDeclarations / normalizeDeclaration ----

test('splitDeclarations: srednik najwyzszego poziomu, przyciecie, puste pominiete', async () => {
  const { splitDeclarations } = await load();
  assert.deepEqual(splitDeclarations('color: red; background: blue;'), ['color: red', 'background: blue']);
  assert.deepEqual(splitDeclarations(' a:b ;; c:d '), ['a:b', 'c:d']);
  assert.deepEqual(splitDeclarations(''), []);
  assert.deepEqual(splitDeclarations(' ; '), []);
  assert.deepEqual(splitDeclarations('a:b'), ['a:b']);
});

test('splitDeclarations: srednik w nawiasie i napisie nie dzieli', async () => {
  const { splitDeclarations } = await load();
  assert.deepEqual(
    splitDeclarations('background:url(data:image/png;base64,AAA); content:";"; d:e'),
    ['background:url(data:image/png;base64,AAA)', 'content:";"', 'd:e'],
  );
  assert.deepEqual(splitDeclarations("content:';'; x:y"), ["content:';'", 'x:y']);
});

test('splitDeclarations: wielolinijkowe ciało z CRLF', async () => {
  const { splitDeclarations } = await load();
  assert.deepEqual(splitDeclarations('\r\n  outline: 0 !important;\r\n  color: red\r\n'), ['outline: 0 !important', 'color: red']);
});

test('normalizeDeclaration: usuwa biale znaki, zachowuje wartosci', async () => {
  const { normalizeDeclaration } = await load();
  assert.equal(normalizeDeclaration('outline: 0 !important'), 'outline:0!important');
  assert.equal(normalizeDeclaration('outline:0!important'), 'outline:0!important');
  assert.equal(normalizeDeclaration('  outline :\n 0\t! important '), 'outline:0!important');
  assert.equal(normalizeDeclaration('margin: 0  auto'), 'margin:0auto');
});

test('normalizeDeclaration: nazwa wlasciwosci malymi literami, wartosc bez zmian', async () => {
  const { normalizeDeclaration } = await load();
  assert.equal(normalizeDeclaration('COLOR: Red'), 'color:Red');
  assert.equal(normalizeDeclaration('Outline : 0 !important'), 'outline:0!important');
  assert.equal(normalizeDeclaration('Background-Color: #FFF'), 'background-color:#FFF');
});

test('normalizeDeclaration: napisy zachowane w calosci', async () => {
  const { normalizeDeclaration } = await load();
  assert.equal(normalizeDeclaration('content: "a  b"'), 'content:"a  b"');
  assert.equal(normalizeDeclaration("font-family: 'A B' , serif"), "font-family:'A B',serif");
});

// ---- walkRules ----

test('walkRules: odwiedza reguly top-level i w grupach rekurencyjnie, w kolejnosci dokumentu', async () => {
  const { scan, walkRules } = await load();
  const { items } = scan('.a{x:y}@media m{.b{x:y}@supports s{.c{x:y}}.d{x:y}}.e{x:y}');
  const seen = [];
  walkRules(items, (r) => seen.push(r.selector));
  assert.deepEqual(seen, ['.a', '.b', '.c', '.d', '.e']);
});

test('walkRules: nie odwiedza keyframes, at-block i statementow', async () => {
  const { scan, walkRules } = await load();
  const { items } = scan('@charset "x";@font-face{a:b}@keyframes k{from{a:b}to{a:b}}@page{a:b}.r{a:b}@media m{@keyframes k2{from{a:b}}.g{a:b}}');
  const seen = [];
  walkRules(items, (r) => seen.push(r.selector));
  assert.deepEqual(seen, ['.r', '.g']);
});

test('walkRules: visitor dostaje wezel regul z kontekstem', async () => {
  const { scan, walkRules } = await load();
  const { items } = scan('@media m{.g{a:b}}');
  const ctx = [];
  walkRules(items, (r) => {
    assert.equal(r.kind, 'rule');
    ctx.push(r.context);
  });
  assert.deepEqual(ctx, [['@media m']]);
});

test('walkRules: pusta lista nie wola visitora', async () => {
  const { walkRules } = await load();
  let n = 0;
  walkRules([], () => n++);
  assert.equal(n, 0);
});
