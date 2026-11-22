import test from 'node:test';
import assert from 'node:assert/strict';

// Dynamiczne importy: brak modulu daje czerwony test, nie blad calego pliku.
const LIB = '../../library/skills/ui-verify/scripts/lib/';
const diff = () => import(`${LIB}css-diff.mjs`);
const parse = () => import(`${LIB}css-parse.mjs`);

// Neutralna konfiguracja zakresu. exact-rule ma selektor spoza korzenia, zeby odroznic go od prefiksu i aliasu.
const SC = () => ({
  root: '.root',
  exceptions: [
    { id: 'menu-host', kind: 'prefix', value: '.menu-host > .item', reason: 'menu outside root' },
    { id: 'theme-hook', kind: 'root-alias', value: '.root-theme', onlyPropertyPrefix: '--tok-', reason: 'tokens only' },
    { id: 'skip-link', kind: 'exact-rule', selector: '.other main:focus', declarations: ['outline:0!important'], reason: 'focus target' },
  ],
});
async function run(withCss, withoutCss, options) {
  const { diffCss } = await diff();
  return options === undefined ? diffCss(withCss, withoutCss, SC()) : diffCss(withCss, withoutCss, SC(), options);
}
const COUNTS = (c) => ({ with: 0, without: 0, added: 0, removed: 0, addedAllowed: 0, ...c });

// ---- selectorAllowed ----

test('selectorAllowed: korzen i jego rozszerzenia dozwolone, inne klasy i elementy nie', async () => {
  const { selectorAllowed } = await diff();
  for (const s of ['.root', '.root a', '.root.is-open a', '.root > a', '.root:hover', '.root[data-x]']) {
    assert.equal(selectorAllowed(s, SC()), true, s);
  }
  for (const s of ['a', 'body', '.rooted', '.root-theme-x', '.root1', 'a .root', '#root', '.other']) {
    assert.equal(selectorAllowed(s, SC()), false, s);
  }
});

test('selectorAllowed: prefiks dopuszcza selektory zaczynajace sie od value, nie srodkowe wystapienie', async () => {
  const { selectorAllowed } = await diff();
  assert.equal(selectorAllowed('.menu-host > .item', SC()), true);
  assert.equal(selectorAllowed('.menu-host > .item a:hover', SC()), true);
  assert.equal(selectorAllowed('.menu-host > .other', SC()), false);
  assert.equal(selectorAllowed('x .menu-host > .item', SC()), false);
});

test('selectorAllowed: root-alias dopuszcza aliasy bez sprawdzania ciala (nie zna onlyPropertyPrefix)', async () => {
  const { selectorAllowed } = await diff();
  assert.equal(selectorAllowed('.root-theme', SC()), true);
  assert.equal(selectorAllowed('.root-theme a', SC()), true);
  assert.equal(selectorAllowed('.root-theme-x', SC()), false);
});

test('selectorAllowed: exact-rule NIE dopuszcza selektora', async () => {
  const { selectorAllowed } = await diff();
  assert.equal(selectorAllowed('.other main:focus', SC()), false);
});

test('selectorAllowed: bez wyjatkow tylko korzen; zwraca boolean', async () => {
  const { selectorAllowed } = await diff();
  const sc = { root: '.menu-host', exceptions: [] };
  assert.strictEqual(selectorAllowed('.menu-host a', sc), true);
  assert.strictEqual(selectorAllowed('.root a', sc), false);
});

// ---- diffCss: podstawy ----

test('diffCss: bez zmian => ok, zerowe listy, liczniki rowne', async () => {
  const css = '.root a { color: red }\n.root b { color: blue }';
  const r = await run(css, css);
  assert.equal(r.ok, true);
  assert.deepEqual(r.counts, COUNTS({ with: 2, without: 2 }));
  assert.deepEqual(r.addedOutside, []);
  assert.deepEqual(r.removed, []);
  assert.deepEqual(r.examples, []);
});

test('diffCss: dwa puste wejscia => ok i zerowe liczniki', async () => {
  const r = await run('', '');
  assert.equal(r.ok, true);
  assert.deepEqual(r.counts, COUNTS());
});

test('diffCss: dodana regula pod korzeniem => ok, liczona jako added i addedAllowed', async () => {
  const r = await run('.root a { color: red } .root b { color: blue }', '.root a { color: red }');
  assert.equal(r.ok, true);
  assert.deepEqual(r.counts, COUNTS({ with: 2, without: 1, added: 1, addedAllowed: 1 }));
  assert.deepEqual(r.addedOutside, []);
  assert.deepEqual(r.removed, []);
});

test('diffCss: dodana regula poza korzeniem => addedOutside z selektorem, ok=false', async () => {
  const r = await run('.root a { color: red } footer { color: blue }', '.root a { color: red }');
  assert.equal(r.ok, false);
  assert.equal(r.addedOutside.length, 1);
  assert.match(r.addedOutside[0], /footer/);
  assert.deepEqual(r.counts, COUNTS({ with: 2, without: 1, added: 1, addedAllowed: 0 }));
  assert.deepEqual(r.removed, []);
  assert.ok(r.examples.some((e) => e.startsWith('ADDED OUTSIDE SCOPE: ') && e.includes('footer')));
});

test('diffCss: regula ze zgrupowanymi selektorami wymaga, by KAZDY byl dopuszczony', async () => {
  const ok = await run('.root a, .root b { c: d }', '');
  assert.equal(ok.ok, true);
  const bad = await run('.root a, footer { c: d }', '');
  assert.equal(bad.ok, false);
  assert.equal(bad.addedOutside.length, 1);
  assert.match(bad.addedOutside[0], /footer|\.root a/);
  const mixed = await run('.menu-host > .item a, .root-theme b { c: d }', '');
  assert.equal(mixed.ok, true, 'prefiks i alias w jednej regule');
});

test('diffCss: ciala dodanych regul pod korzeniem nie sa sprawdzane', async () => {
  const r = await run('.root a { position: fixed !important; background: url(x) }', '');
  assert.equal(r.ok, true);
});

test('diffCss: usunieta regula => removed, ok=false', async () => {
  const r = await run('.root a { color: red }', '.root a { color: red } .root b { color: blue }');
  assert.equal(r.ok, false);
  assert.equal(r.removed.length, 1);
  assert.match(r.removed[0], /\.root b/);
  assert.deepEqual(r.counts, COUNTS({ with: 1, without: 2, removed: 1 }));
  assert.deepEqual(r.addedOutside, []);
  assert.ok(r.examples.some((e) => e.startsWith('REMOVED/CHANGED: ') && e.includes('.root b')));
});

test('diffCss: zmienione cialo reguly z "bez" => jedno removed i jedno added (FAIL, nawet pod korzeniem)', async () => {
  const r = await run('.root a { color: blue }', '.root a { color: red }');
  assert.equal(r.ok, false);
  assert.deepEqual(r.counts, COUNTS({ with: 1, without: 1, added: 1, removed: 1, addedAllowed: 1 }));
  assert.equal(r.removed.length, 1);
  assert.deepEqual(r.addedOutside, []);
});

test('diffCss: zmieniony selektor (to samo cialo) => removed + added', async () => {
  const r = await run('.root b { color: red }', '.root a { color: red }');
  assert.equal(r.ok, false);
  assert.equal(r.counts.removed, 1);
  assert.equal(r.counts.added, 1);
});

test('diffCss: wartosc wazna dla ciala (!important, wielkosc liter wartosci) odroznia reguly', async () => {
  const r = await run('.root a { color: red !important }', '.root a { color: red }');
  assert.equal(r.ok, false);
  assert.equal(r.counts.removed, 1);
  assert.equal(r.counts.added, 1);
});

// ---- krotnosci ----

test('diffCss: ta sama regula dwa razy w "bez" i raz w "z" => removed (krotnosc)', async () => {
  const rule = '.root a { color: red }';
  const r = await run(rule, `${rule}\n${rule}`);
  assert.equal(r.ok, false);
  assert.deepEqual(r.counts, COUNTS({ with: 1, without: 2, removed: 1 }));
  assert.equal(r.removed.length, 1);
});

test('diffCss: ta sama regula dwa razy w "z" i raz w "bez" => added (pod korzeniem ok)', async () => {
  const rule = '.root a { color: red }';
  const r = await run(`${rule}\n${rule}`, rule);
  assert.equal(r.ok, true);
  assert.deepEqual(r.counts, COUNTS({ with: 2, without: 1, added: 1, addedAllowed: 1 }));
  assert.deepEqual(r.removed, []);
});

test('diffCss: duplikat poza korzeniem dodany w "z" ponad liczbe w "bez" => addedOutside', async () => {
  const rule = 'footer { color: red }';
  const r = await run(`${rule}\n${rule}`, rule);
  assert.equal(r.ok, false);
  assert.equal(r.addedOutside.length, 1);
  assert.deepEqual(r.counts, COUNTS({ with: 2, without: 1, added: 1 }));
});

test('diffCss: kolejnosc regul nie ma znaczenia (multiset)', async () => {
  const r = await run('.root b { c: 2 } .root a { c: 1 }', '.root a { c: 1 } .root b { c: 2 }');
  assert.equal(r.ok, true);
  assert.deepEqual(r.counts, COUNTS({ with: 2, without: 2 }));
});

// ---- kontekst @media i zagniezdzenia ----

test('diffCss: ten sam selektor i cialo w roznych grupach @media to rozne trojki', async () => {
  const r = await run(
    '@media (min-width: 1px) { .root a { color: red } }',
    '@media (max-width: 1px) { .root a { color: red } }',
  );
  assert.equal(r.ok, false);
  assert.equal(r.counts.removed, 1);
  assert.equal(r.counts.added, 1);
  assert.equal(r.counts.addedAllowed, 1);
});

test('diffCss: regula w @media vs na najwyzszym poziomie to rozne trojki; dodanie wariantu pod korzeniem ok', async () => {
  const media = '@media print { .root a { color: red } }';
  const top = '.root a { color: red }';
  const added = await run(`${top}\n${media}`, media);
  assert.equal(added.ok, true);
  assert.deepEqual(added.counts, COUNTS({ with: 2, without: 1, added: 1, addedAllowed: 1 }));
  const removed = await run(media, `${top}\n${media}`);
  assert.equal(removed.ok, false);
  assert.equal(removed.counts.removed, 1);
});

test('diffCss: kolejnosc zagniezdzonych grup jest czescia kontekstu', async () => {
  const r = await run(
    '@media a { @supports (x: y) { .root a { c: d } } }',
    '@supports (x: y) { @media a { .root a { c: d } } }',
  );
  assert.equal(r.ok, false);
  assert.equal(r.counts.removed, 1);
  assert.equal(r.counts.added, 1);
});

test('diffCss: dodana regula poza korzeniem wewnatrz @media jest zgloszona z kontekstem i selektorem', async () => {
  const r = await run('@media print { .other { c: d } }', '');
  assert.equal(r.ok, false);
  assert.equal(r.addedOutside.length, 1);
  assert.match(r.addedOutside[0], /@media print/);
  assert.match(r.addedOutside[0], /\.other/);
});

test('diffCss: regula pod korzeniem wewnatrz @media jest dozwolona', async () => {
  const r = await run('@media print { .root a { c: d } }', '');
  assert.equal(r.ok, true);
  assert.deepEqual(r.counts, COUNTS({ with: 1, added: 1, addedAllowed: 1 }));
});

test('diffCss: usuniety statement (np. @import) liczony jako removed', async () => {
  const r = await run('.root a { c: d }', '@import url(a.css);\n.root a { c: d }');
  assert.equal(r.ok, false);
  assert.equal(r.counts.removed, 1);
  assert.match(r.removed[0], /@import/);
});

// ---- @font-face / @keyframes / @page i allowAtRules ----

const FONT = '@font-face { font-family: X; src: url(x.woff2) }';
const KEYF = '@keyframes spin { from { opacity: 0 } to { opacity: 1 } }';
const PAGE = '@page { margin: 1cm }';

test('diffCss: dodany @font-face dozwolony domyslnie (liczony w addedAllowed)', async () => {
  const r = await run(`.root a { c: d }\n${FONT}`, '.root a { c: d }');
  assert.equal(r.ok, true);
  assert.deepEqual(r.counts, COUNTS({ with: 2, without: 1, added: 1, addedAllowed: 1 }));
  assert.deepEqual(r.addedOutside, []);
});

test('diffCss: dodany @font-face niedozwolony przy allowAtRules: []', async () => {
  const r = await run(`.root a { c: d }\n${FONT}`, '.root a { c: d }', { allowAtRules: [] });
  assert.equal(r.ok, false);
  assert.equal(r.addedOutside.length, 1);
  assert.match(r.addedOutside[0], /@font-face/);
  assert.equal(r.counts.addedAllowed, 0);
});

test('diffCss: allowAtRules podane jawnie zastepuje domyslne (font-face nie jest dokladany)', async () => {
  const r = await run(FONT, '', { allowAtRules: ['keyframes'] });
  assert.equal(r.ok, false);
  assert.equal(r.addedOutside.length, 1);
});

test('diffCss: dodany @keyframes niedozwolony domyslnie, dozwolony z allowAtRules: [keyframes]', async () => {
  const denied = await run(KEYF, '');
  assert.equal(denied.ok, false);
  assert.equal(denied.addedOutside.length, 1);
  assert.match(denied.addedOutside[0], /@keyframes spin/);
  assert.deepEqual(denied.counts, COUNTS({ with: 1, added: 1 }));
  const allowed = await run(KEYF, '', { allowAtRules: ['keyframes'] });
  assert.equal(allowed.ok, true);
  assert.deepEqual(allowed.counts, COUNTS({ with: 1, added: 1, addedAllowed: 1 }));
  const vendor = await run('@-webkit-keyframes spin { from { opacity: 0 } }', '', { allowAtRules: ['keyframes'] });
  assert.equal(vendor.ok, true, 'wariant z prefiksem producenta jest @keyframes');
});

test('diffCss: dodany @page niedozwolony domyslnie, dozwolony z allowAtRules: [page]', async () => {
  const denied = await run(PAGE, '');
  assert.equal(denied.ok, false);
  assert.equal(denied.addedOutside.length, 1);
  assert.match(denied.addedOutside[0], /@page/);
  const allowed = await run(PAGE, '', { allowAtRules: ['page'] });
  assert.equal(allowed.ok, true);
  assert.equal(allowed.counts.addedAllowed, 1);
});

test('diffCss: wszystkie trzy at-reguly dozwolone, gdy podane razem; poza lista nadal odrzucane', async () => {
  const opts = { allowAtRules: ['font-face', 'keyframes', 'page'] };
  const r = await run(`${FONT}\n${KEYF}\n${PAGE}`, '', opts);
  assert.equal(r.ok, true);
  assert.deepEqual(r.counts, COUNTS({ with: 3, added: 3, addedAllowed: 3 }));
  const bad = await run(`${FONT}\n${KEYF}\nfooter { c: d }`, '', { allowAtRules: ['font-face'] });
  assert.equal(bad.addedOutside.length, 2);
});

test('diffCss: zmiana ciala @font-face lub kroku @keyframes z "bez" to removed (FAIL), mimo dozwolonego added', async () => {
  const f = await run('@font-face { font-family: X; src: url(y.woff2) }', FONT);
  assert.equal(f.ok, false);
  assert.equal(f.counts.removed, 1);
  assert.equal(f.counts.added, 1);
  assert.equal(f.counts.addedAllowed, 1);
  const k = await run('@keyframes spin { from { opacity: 0 } to { opacity: 0.5 } }', KEYF, { allowAtRules: ['keyframes'] });
  assert.equal(k.ok, false);
  assert.equal(k.counts.removed, 1);
});

test('diffCss: @keyframes to jedna trojka (kroki w ciele), nie osobne reguly from/to', async () => {
  const r = await run(KEYF, '', { allowAtRules: ['keyframes'] });
  assert.equal(r.counts.with, 1);
  assert.equal(r.counts.added, 1);
});

// ---- wyjatki w diff ----

test('diffCss: wyjatek prefiksowy i alias korzenia sa dopuszczone (alias z dowolnymi wlasciwosciami)', async () => {
  const r = await run('.menu-host > .item a { c: d }\n.root-theme { color: red }\n.root-theme a { --tok-a: 1 }', '');
  assert.equal(r.ok, true);
  assert.deepEqual(r.counts, COUNTS({ with: 3, added: 3, addedAllowed: 3 }));
});

test('diffCss: exact-rule NIE dopuszcza w diff (tylko korzen, prefiks, alias), choc checkScope go akceptuje', async () => {
  const { checkScope } = await import(`${LIB}css-scope-core.mjs`);
  const css = '.other main:focus { outline: 0 !important }';
  assert.equal(checkScope(css, SC()).ok, true, 'zalozenie: kontrola zakresu pelnego CSS akceptuje exact-rule');
  const r = await run(css, '');
  assert.equal(r.ok, false);
  assert.equal(r.addedOutside.length, 1);
  assert.match(r.addedOutside[0], /\.other main:focus/);
  assert.equal(r.counts.addedAllowed, 0);
});

// ---- opisy i przyklady ----

test('diffCss: opis addedOutside skrocony do 160 znakow', async () => {
  const longSel = `${'x'.repeat(300)}`;
  const r = await run(`${longSel} { c: d }`, '');
  assert.equal(r.addedOutside.length, 1);
  assert.ok(r.addedOutside[0].length <= 160, String(r.addedOutside[0].length));
  assert.ok(r.addedOutside[0].includes('xxxxxxxxxx'));
  // krotki opis nie jest ucinany
  const short = await run('footer { c: d }', '');
  assert.ok(short.addedOutside[0].length < 160);
  assert.match(short.addedOutside[0], /footer/);
});

test('diffCss: examples to do 10 pierwszych linii z prefiksami REMOVED/CHANGED i ADDED OUTSIDE SCOPE', async () => {
  const many = (n, mk) => Array.from({ length: n }, (_, i) => mk(i)).join('\n');
  const without = many(15, (i) => `.root r${i} { c: ${i} }`);
  const r = await run('', without);
  assert.equal(r.removed.length, 15);
  assert.equal(r.examples.length, 10);
  assert.ok(r.examples.every((e) => e.startsWith('REMOVED/CHANGED: ')));

  const added = await run(many(12, (i) => `t${i} { c: ${i} }`), '');
  assert.equal(added.addedOutside.length, 12);
  assert.equal(added.examples.length, 10);
  assert.ok(added.examples.every((e) => e.startsWith('ADDED OUTSIDE SCOPE: ')));

  const both = await run('.root a { c: new } footer { c: d }', '.root a { c: old }');
  assert.equal(both.examples.length, 2);
  assert.ok(both.examples.some((e) => e.startsWith('REMOVED/CHANGED: ')));
  assert.ok(both.examples.some((e) => e.startsWith('ADDED OUTSIDE SCOPE: ')));
  for (const e of both.examples) assert.ok(typeof e === 'string');
});

test('diffCss: wyniki list sa w pelni zwracane (removed/addedOutside nie sa ucinane do 10)', async () => {
  const many = Array.from({ length: 12 }, (_, i) => `t${i} { c: ${i} }`).join('\n');
  const r = await run(many, many.replace(/t/g, 'u'));
  assert.equal(r.removed.length, 12);
  assert.equal(r.addedOutside.length, 12);
  assert.equal(r.counts.added, 12);
  assert.equal(r.counts.removed, 12);
  assert.equal(r.examples.length, 10);
});

// ---- skompresowane vs rozwiniete ----

test('diffCss: skompresowane i rozwiniete wejscie sa rownowazne (biale znaki, komentarze)', async () => {
  const expanded = `
/* naglowek */
.root a,
.root   b {
    color: red;
    margin:   0;
}

@media (min-width: 10px) {
  .root c {
    padding: 1px;
  }
}
`;
  const compact = '.root a, .root b { color: red; margin: 0; }@media (min-width: 10px){.root c { padding: 1px; }}';
  const r1 = await run(expanded, compact);
  assert.equal(r1.ok, true);
  assert.deepEqual(r1.counts, COUNTS({ with: 2, without: 2 }));
  assert.deepEqual(r1.removed, []);
  const r2 = await run(compact, expanded);
  assert.equal(r2.ok, true);
  assert.deepEqual(r2.counts, COUNTS({ with: 2, without: 2 }));
});

test('diffCss: biale znaki w selektorze (nowe linie, wielokrotne spacje) nie tworza roznicy', async () => {
  const r = await run('.root   a\n>   b { c: d }', '.root a > b { c: d }');
  assert.equal(r.ok, true);
  assert.deepEqual(r.counts, COUNTS({ with: 1, without: 1 }));
});

// ---- bledy rozbioru ----

test('diffCss: blad rozbioru w "z" lub w "bez" propaguje CssParseError', async () => {
  const { diffCss } = await diff();
  const { CssParseError } = await parse();
  const isParseError = (e) => e instanceof CssParseError;
  assert.throws(() => diffCss('.root a {', '.root a { c: d }', SC()), isParseError, 'z');
  assert.throws(() => diffCss('.root a { c: d }', '.root a {', SC()), isParseError, 'bez');
  assert.throws(() => diffCss('.root a { c: d } }', '', SC()), isParseError, 'nadmiarowa klamra');
});

test('diffCss: zwraca czyste dane (JSON) z pelnym zestawem pol', async () => {
  const r = await run('.root a { c: d }', '');
  assert.deepEqual(Object.keys(r).sort(), ['addedOutside', 'counts', 'examples', 'ok', 'removed']);
  assert.deepEqual(Object.keys(r.counts).sort(), ['added', 'addedAllowed', 'removed', 'with', 'without']);
  assert.deepEqual(JSON.parse(JSON.stringify(r)), r);
});

// ---- neutralnosc rdzenia ----

test('rdzen: zrodlo css-diff.mjs nie zawiera nazw projektu, sciezek domowych ani e-maili', async () => {
  const { readFile } = await import('node:fs/promises');
  const src = await readFile(new URL(`${LIB}css-diff.mjs`, import.meta.url), 'utf8');
  assert.doesNotMatch(src, new RegExp('/home/|\\\\Users\\\\|[A-Za-z0-9._-]+@[A-Za-z0-9-]+\\.[a-z]{2,}|' + [String.fromCharCode(112, 99, 103) + 'academia', 'ba' + 'tu', 'bar' + 'cis', 'skip' + 'pster'].join('|'), 'i'));
});
