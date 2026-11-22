import test from 'node:test';
import assert from 'node:assert/strict';

// Dynamiczne importy: brak modulu daje czerwony test, nie blad calego pliku.
const core = () => import('../../library/skills/ui-verify/scripts/lib/css-scope-core.mjs');
const parse = () => import('../../library/skills/ui-verify/scripts/lib/css-parse.mjs');

// Neutralna konfiguracja (odpowiednik trzech wyjatkow projektu).
const EXACT_SEL = '.root-theme main[tabindex="-1"]:focus:not(:focus-visible)';
const config = () => ({
  root: '.root',
  exceptions: [
    { id: 'detached-menu', kind: 'prefix', value: 'body:has(.root-menu) > .menu-host', reason: 'menu rendered outside root' },
    { id: 'theme-hook', kind: 'root-alias', value: '.root-theme', onlyPropertyPrefix: '--tok-', reason: 'theme hook, tokens only' },
    { id: 'focus-reset', kind: 'exact-rule', selector: EXACT_SEL, declarations: ['outline:0!important'], reason: 'programmatic focus target' },
  ],
});
const hitsOf = (r, id) => r.exceptions.find((e) => e.id === id).hits;

// ---- buildRootPattern ----

test('buildRootPattern: .root pasuje do korzenia i jego rozszerzen', async () => {
  const { buildRootPattern } = await core();
  const re = buildRootPattern('.root');
  assert.ok(re instanceof RegExp);
  for (const s of ['.root', '.root a', '.root.is-open a', '.root>a', '.root:hover', '.root[data-x]', '.root,a', '.root ']) {
    assert.ok(re.test(s), s);
  }
});

test('buildRootPattern: .root nie pasuje do .root-theme, .rooted, .root_x, .root1, a .root, #root', async () => {
  const { buildRootPattern } = await core();
  const re = buildRootPattern('.root');
  for (const s of ['.root-theme', '.root-theme a', '.rooted', '.rooted a', '.root_x', '.root1', 'a .root', '#root', ' .root a', 'root', '']) {
    assert.ok(!re.test(s), JSON.stringify(s));
  }
});

test('buildRootPattern: wzorzec jest zakotwiczony na poczatku i bez stanu (flaga g/y)', async () => {
  const { buildRootPattern } = await core();
  const re = buildRootPattern('.root');
  assert.ok(!re.global && !re.sticky);
  assert.ok(re.test('.root a'));
  assert.ok(re.test('.root a'));
});

test('buildRootPattern: inne poprawne klasy (myslnik, podkreslenie, cyfry) i trim', async () => {
  const { buildRootPattern } = await core();
  assert.ok(buildRootPattern('.menu-host').test('.menu-host a'));
  assert.ok(!buildRootPattern('.menu-host').test('.menu-host-x'));
  assert.ok(buildRootPattern('._x').test('._x'));
  assert.ok(buildRootPattern('.a-b_c1').test('.a-b_c1 d'));
  assert.ok(buildRootPattern('  .root  ').test('.root a'));
});

test('buildRootPattern: znaki specjalne regexu nie sa interpretowane (kropka literalna)', async () => {
  const { buildRootPattern } = await core();
  assert.ok(!buildRootPattern('.root').test('xroot a'));
  assert.ok(!buildRootPattern('.a-b').test('.axb'));
});

test('buildRootPattern: niepoprawne klasy korzenia => TypeError', async () => {
  const { buildRootPattern } = await core();
  const bad = ['root', '', '.', '.1a', '.-a', '.a b', '.a.b', '#id', 'a', '.a,b', '.a>b', '.a\\b', '.a(', '.a[x]', ' ', '.root-théme'];
  for (const b of bad) {
    assert.throws(() => buildRootPattern(b), TypeError, JSON.stringify(b));
  }
  for (const b of [null, undefined, 42, {}]) {
    assert.throws(() => buildRootPattern(b), TypeError, String(b));
  }
});

// ---- checkScope: ksztalt wyniku ----

test('checkScope: ksztalt wyniku dla pustego CSS', async () => {
  const { checkScope } = await core();
  const r = checkScope('', { root: '.root', exceptions: [] });
  assert.equal(r.ok, true);
  assert.deepEqual(r.bad, []);
  assert.equal(r.rules, 0);
  assert.equal(r.important, 0);
  assert.deepEqual(r.exceptions, []);
  assert.deepEqual(r.warnings, []);
});

test('checkScope: raport exceptions ma wpis dla kazdego wyjatku w kolejnosci konfiguracji', async () => {
  const { checkScope } = await core();
  const r = checkScope('.root a{b:c}', config());
  assert.deepEqual(r.exceptions.map((e) => e.id), ['detached-menu', 'theme-hook', 'focus-reset']);
  assert.ok(r.exceptions.every((e) => e.hits === 0));
});

// ---- checkScope: 13 neutralnych przypadkow (rownowaznosc z narzedziem projektu) ----

test('przypadek 1: regula pod korzeniem OK', async () => {
  const { checkScope } = await core();
  const r = checkScope('.root a{color:red}', config());
  assert.equal(r.ok, true);
  assert.deepEqual(r.bad, []);
});

test('przypadek 2: regula poza korzeniem odrzucona (bad = selektor)', async () => {
  const { checkScope } = await core();
  const r = checkScope('a{color:red}', config());
  assert.equal(r.ok, false);
  assert.deepEqual(r.bad, ['a']);
});

test('przypadek 3: wyjatek prefiksowy (dluzszy selektor z dokladnym prefiksem) OK', async () => {
  const { checkScope } = await core();
  const r = checkScope('body:has(.root-menu) > .menu-host .item{color:red}', config());
  assert.equal(r.ok, true);
  assert.equal(hitsOf(r, 'detached-menu'), 1);
});

test('przypadek 3b: prefiks musi byc dokladny, inny poczatek odrzucony', async () => {
  const { checkScope } = await core();
  for (const sel of ['body:has(.root-menu) > .other-host .item', 'html body:has(.root-menu) > .menu-host', 'body > .menu-host', 'body:has(.root-menu)>.menu-host']) {
    const r = checkScope(`${sel}{color:red}`, config());
    assert.equal(r.ok, false, sel);
    assert.deepEqual(r.bad, [sel], sel);
  }
});

test('przypadek 4: alias korzenia tylko z wlasciwosciami o prefiksie OK', async () => {
  const { checkScope } = await core();
  const r = checkScope('.root-theme{--tok-a:1;--tok-b:#fff}', config());
  assert.equal(r.ok, true);
  assert.equal(hitsOf(r, 'theme-hook'), 1);
});

test('przypadek 4b: alias takze z dluzszym selektorem zaczynajacym sie od klasy aliasu', async () => {
  const { checkScope } = await core();
  const r = checkScope('.root-theme .x{--tok-a:1}', config());
  assert.equal(r.ok, true);
  assert.equal(hitsOf(r, 'theme-hook'), 1);
});

test('przypadek 5: alias z inna wlasciwoscia odrzucony; komunikat zawiera selektor, deklaracje i powod', async () => {
  const { checkScope } = await core();
  const r = checkScope('.root-theme{--tok-a:1;color:red}', config());
  assert.equal(r.ok, false);
  assert.equal(r.bad.length, 1);
  assert.ok(r.bad[0].startsWith('.root-theme'), r.bad[0]);
  assert.match(r.bad[0], /\{/);
  assert.match(r.bad[0], /color/);
  assert.match(r.bad[0], /theme hook, tokens only/);
  assert.equal(hitsOf(r, 'theme-hook'), 0);
});

test('przypadek 5b: alias bez wlasciwosci-prefiksu (zwykla wlasciwosc, wlasciwosc o podobnej nazwie) odrzucony', async () => {
  const { checkScope } = await core();
  for (const body of ['color:red', '--other-a:1', 'tok-a:1', '-tok-a:1']) {
    const r = checkScope(`.root-theme{${body}}`, config());
    assert.equal(r.ok, false, body);
  }
});

test('przypadek 5c: alias bez onlyPropertyPrefix akceptuje dowolne wlasciwosci', async () => {
  const { checkScope } = await core();
  const cfg = { root: '.root', exceptions: [{ id: 'free-alias', kind: 'root-alias', value: '.root-theme', reason: 'r' }] };
  const r = checkScope('.root-theme a{color:red}', cfg);
  assert.equal(r.ok, true);
  assert.equal(hitsOf(r, 'free-alias'), 1);
});

test('przypadek 5d: alias nie obejmuje klas o wspolnym prefiksie (.root-theme-x)', async () => {
  const { checkScope } = await core();
  const r = checkScope('.root-theme-x{--tok-a:1}', config());
  assert.equal(r.ok, false);
  assert.deepEqual(r.bad, ['.root-theme-x']);
});

test('przypadek 6: exact-rule w formie rozwinietej OK', async () => {
  const { checkScope } = await core();
  const css = `${EXACT_SEL} {\n  outline: 0 !important;\n}\n`;
  const r = checkScope(css, config());
  assert.equal(r.ok, true, JSON.stringify(r.bad));
  assert.equal(hitsOf(r, 'focus-reset'), 1);
});

test('przypadek 6b: exact-rule w formie rozwinietej z CRLF OK', async () => {
  const { checkScope } = await core();
  const css = `${EXACT_SEL}\r\n{\r\n  outline: 0 !important;\r\n}\r\n`;
  const r = checkScope(css, config());
  assert.equal(r.ok, true, JSON.stringify(r.bad));
  assert.equal(hitsOf(r, 'focus-reset'), 1);
});

test('przypadek 7: exact-rule w formie skompresowanej OK', async () => {
  const { checkScope } = await core();
  const r = checkScope(`${EXACT_SEL}{outline:0!important}`, config());
  assert.equal(r.ok, true, JSON.stringify(r.bad));
  assert.equal(hitsOf(r, 'focus-reset'), 1);
  assert.equal(r.important, 1);
});

test('przypadek 7b: selektor exact-rule z innymi bialymi znakami i wielkoscia liter nazwy wlasciwosci OK', async () => {
  const { checkScope } = await core();
  const css = '.root-theme   main[tabindex="-1"]:focus:not(:focus-visible)\n{ OUTLINE : 0   !important ; }';
  const r = checkScope(css, config());
  assert.equal(r.ok, true, JSON.stringify(r.bad));
  assert.equal(hitsOf(r, 'focus-reset'), 1);
});

test('przypadek 7c: deklaracje w konfiguracji exact-rule zapisane z bialymi znakami sa normalizowane', async () => {
  const { checkScope } = await core();
  const cfg = config();
  cfg.exceptions[2].declarations = ['outline: 0 !important'];
  const r = checkScope(`${EXACT_SEL}{outline:0!important}`, cfg);
  assert.equal(r.ok, true, JSON.stringify(r.bad));
  assert.equal(hitsOf(r, 'focus-reset'), 1);
});

test('przypadek 8: exact-rule z dodatkowa deklaracja odrzucony', async () => {
  const { checkScope } = await core();
  const r = checkScope(`${EXACT_SEL}{outline:0!important;color:red}`, config());
  assert.equal(r.ok, false);
  assert.ok(r.bad.length >= 1);
  assert.equal(hitsOf(r, 'focus-reset'), 0);
});

test('przypadek 8b: exact-rule bez wymaganej deklaracji (pusta regula) odrzucony', async () => {
  const { checkScope } = await core();
  const r = checkScope(`${EXACT_SEL}{}`, config());
  assert.equal(r.ok, false);
  assert.equal(hitsOf(r, 'focus-reset'), 0);
});

test('przypadek 8c: exact-rule z dwiema deklaracjami w konfiguracji wymaga obu (zbior, nie kolejnosc)', async () => {
  const { checkScope } = await core();
  const cfg = config();
  cfg.exceptions[2].declarations = ['outline:0!important', 'box-shadow:none'];
  assert.equal(checkScope(`${EXACT_SEL}{box-shadow:none;outline:0!important}`, cfg).ok, true);
  assert.equal(checkScope(`${EXACT_SEL}{outline:0!important}`, cfg).ok, false);
});

test('przypadek 9: exact-rule z outline: none !important odrzucony', async () => {
  const { checkScope } = await core();
  const r = checkScope(`${EXACT_SEL}{outline:none!important}`, config());
  assert.equal(r.ok, false);
  assert.equal(hitsOf(r, 'focus-reset'), 0);
});

test('przypadek 9b: exact-rule z outline: 0 bez !important odrzucony', async () => {
  const { checkScope } = await core();
  const r = checkScope(`${EXACT_SEL}{outline:0}`, config());
  assert.equal(r.ok, false);
});

test('przypadek 10: exact-rule zgrupowany z innym selektorem odrzucony', async () => {
  const { checkScope } = await core();
  const r = checkScope(`${EXACT_SEL}, .root-theme footer{outline:0!important}`, config());
  assert.equal(r.ok, false);
  assert.equal(hitsOf(r, 'focus-reset'), 0);
  const r2 = checkScope(`.root a, ${EXACT_SEL}{outline:0!important}`, config());
  assert.equal(r2.ok, false);
  assert.equal(hitsOf(r2, 'focus-reset'), 0);
});

test('przypadek 11: inny element aliasu odrzucony', async () => {
  const { checkScope } = await core();
  const r = checkScope('.root-theme section[tabindex="-1"]:focus:not(:focus-visible){outline:0!important}', config());
  assert.equal(r.ok, false);
  assert.equal(hitsOf(r, 'focus-reset'), 0);
});

test('przypadek 12: inna wartosc atrybutu odrzucona', async () => {
  const { checkScope } = await core();
  const r = checkScope('.root-theme main[tabindex="0"]:focus:not(:focus-visible){outline:0!important}', config());
  assert.equal(r.ok, false);
  assert.equal(hitsOf(r, 'focus-reset'), 0);
});

test('przypadek 13: :focus-visible zamiast :focus:not(:focus-visible) odrzucone', async () => {
  const { checkScope } = await core();
  const r = checkScope('.root-theme main[tabindex="-1"]:focus-visible{outline:0!important}', config());
  assert.equal(r.ok, false);
  assert.equal(hitsOf(r, 'focus-reset'), 0);
});

// ---- checkScope: korzen, listy selektorow, bad ----

test('korzen: .root vs .root-theme vs .rooted bez wyjatkow', async () => {
  const { checkScope } = await core();
  const none = { root: '.root', exceptions: [] };
  assert.equal(checkScope('.root a{b:c}', none).ok, true);
  assert.equal(checkScope('.root{b:c}', none).ok, true);
  assert.equal(checkScope('.root.x a{b:c}', none).ok, true);
  const t = checkScope('.root-theme a{b:c}', none);
  assert.deepEqual(t.bad, ['.root-theme a']);
  const d = checkScope('.rooted a{b:c}', none);
  assert.deepEqual(d.bad, ['.rooted a']);
});

test('korzen: selektor z korzeniem w srodku (nie na poczatku) odrzucony', async () => {
  const { checkScope } = await core();
  const r = checkScope('body .root a{b:c}', { root: '.root', exceptions: [] });
  assert.deepEqual(r.bad, ['body .root a']);
});

test('korzen: inna klasa korzenia z konfiguracji', async () => {
  const { checkScope } = await core();
  const cfg = { root: '.menu-host', exceptions: [] };
  assert.equal(checkScope('.menu-host a{b:c}', cfg).ok, true);
  assert.equal(checkScope('.root a{b:c}', cfg).ok, false);
});

test('lista selektorow: kazdy selektor sprawdzany osobno, bad zawiera tylko zle', async () => {
  const { checkScope } = await core();
  const r = checkScope('.root a, b, .root c, d{e:f}', { root: '.root', exceptions: [] });
  assert.equal(r.ok, false);
  assert.deepEqual(r.bad, ['b', 'd']);
});

test('lista selektorow: przecinek w :where() nie dzieli, w atrybucie tez', async () => {
  const { checkScope } = await core();
  const cfg = { root: '.root', exceptions: [] };
  assert.equal(checkScope('.root :where(a, b) c{d:e}', cfg).ok, true);
  assert.equal(checkScope('.root [data-x="a,b"]{d:e}', cfg).ok, true);
  const r = checkScope(':where(.root a, .root b) c{d:e}', cfg);
  assert.deepEqual(r.bad, [':where(.root a, .root b) c']);
});

test('bad: ten sam zly selektor w wielu regulach raportowany dla kazdej reguly', async () => {
  const { checkScope } = await core();
  const r = checkScope('a{b:c}a{d:e}', { root: '.root', exceptions: [] });
  assert.deepEqual(r.bad, ['a', 'a']);
});

test('bad: selektor wewnatrz napisu i komentarza nie jest reguly', async () => {
  const { checkScope } = await core();
  const css = '/* a{b:c} */ .root x{content:"a{b:c}"}';
  const r = checkScope(css, { root: '.root', exceptions: [] });
  assert.equal(r.ok, true);
  assert.deepEqual(r.bad, []);
  assert.equal(r.rules, 1);
});

// ---- checkScope: wyjatki, hits, ostrzezenia ----

test('hits: liczba regul zaakceptowanych przez wyjatek', async () => {
  const { checkScope } = await core();
  const css = [
    'body:has(.root-menu) > .menu-host .a{x:y}',
    'body:has(.root-menu) > .menu-host .b{x:y}',
    '.root-theme{--tok-a:1}',
    `${EXACT_SEL}{outline:0!important}`,
    '.root c{x:y}',
  ].join('\n');
  const r = checkScope(css, config());
  assert.equal(r.ok, true);
  assert.equal(hitsOf(r, 'detached-menu'), 2);
  assert.equal(hitsOf(r, 'theme-hook'), 1);
  assert.equal(hitsOf(r, 'focus-reset'), 1);
  assert.deepEqual(r.warnings, []);
});

test('ostrzezenie: nieuzyty wyjatek => "exception <id> was not used", ok pozostaje true', async () => {
  const { checkScope } = await core();
  const r = checkScope('.root a{b:c}\n.root-theme{--tok-a:1}', config());
  assert.equal(r.ok, true);
  assert.equal(hitsOf(r, 'theme-hook'), 1);
  assert.deepEqual(r.warnings.slice().sort(), ['exception detached-menu was not used', 'exception focus-reset was not used']);
});

test('ostrzezenie: wyjatek uzyty w regule odrzuconej nie liczy sie jako uzyty', async () => {
  const { checkScope } = await core();
  const r = checkScope('.root-theme{color:red}', config());
  assert.equal(r.ok, false);
  assert.equal(hitsOf(r, 'theme-hook'), 0);
  assert.ok(r.warnings.includes('exception theme-hook was not used'));
});

test('wyjatek prefiksowy dziala dla listy selektorow (kazdy selektor osobno)', async () => {
  const { checkScope } = await core();
  const r = checkScope('body:has(.root-menu) > .menu-host .a, .root b, c{x:y}', config());
  assert.deepEqual(r.bad, ['c']);
  assert.equal(hitsOf(r, 'detached-menu'), 1);
});

test('exact-rule wygrywa przed aliasem (ten sam selektor zaczyna sie od klasy aliasu)', async () => {
  const { checkScope } = await core();
  const r = checkScope(`${EXACT_SEL}{outline:0!important}`, config());
  assert.equal(r.ok, true);
  assert.equal(hitsOf(r, 'focus-reset'), 1);
  assert.equal(hitsOf(r, 'theme-hook'), 0);
});

test('konfiguracja bez wyjatkow: aliasy i prefiksy odrzucone', async () => {
  const { checkScope } = await core();
  const r = checkScope('.root-theme{--tok-a:1}body:has(.root-menu) > .menu-host a{x:y}', { root: '.root', exceptions: [] });
  assert.equal(r.bad.length, 2);
});

// ---- checkScope: grupy, keyframes, at-block, kompresja ----

test('grupa @media: reguly sprawdzane (dobra i zla)', async () => {
  const { checkScope } = await core();
  const cfg = { root: '.root', exceptions: [] };
  assert.equal(checkScope('@media (min-width:1px){.root a{b:c}}', cfg).ok, true);
  const r = checkScope('@media (min-width:1px){.root a{b:c} d{e:f}}', cfg);
  assert.deepEqual(r.bad, ['d']);
});

test('zagniezdzone @supports w @media: zle reguly wykrywane', async () => {
  const { checkScope } = await core();
  const cfg = { root: '.root', exceptions: [] };
  const r = checkScope('@media x{@supports (display:grid){.root a{b:c} z{b:c}}}', cfg);
  assert.deepEqual(r.bad, ['z']);
  assert.equal(r.rules, 4);
});

test('wyjatki dzialaja takze wewnatrz grup @media', async () => {
  const { checkScope } = await core();
  const r = checkScope(`@media x{${EXACT_SEL}{outline:0!important}.root-theme{--tok-a:1}}`, config());
  assert.equal(r.ok, true);
  assert.equal(hitsOf(r, 'focus-reset'), 1);
  assert.equal(hitsOf(r, 'theme-hook'), 1);
});

test('@keyframes: kroki (from/to/%/lista) pomijane', async () => {
  const { checkScope } = await core();
  const r = checkScope('@keyframes k{from{a:b}50%{a:b}0%, 100%{a:b}to{a:b}}.root a{b:c}', { root: '.root', exceptions: [] });
  assert.equal(r.ok, true);
  assert.deepEqual(r.bad, []);
});

test('@font-face i inne at-block pomijane przy kontroli', async () => {
  const { checkScope } = await core();
  const r = checkScope('@font-face{font-family:x;src:url(a.woff)}@page{margin:0}@property --x{syntax:"*";inherits:false}.root a{b:c}', { root: '.root', exceptions: [] });
  assert.equal(r.ok, true);
});

test('statementy (@charset, @import) nie sa selektorami', async () => {
  const { checkScope } = await core();
  const r = checkScope('@charset "utf-8";@import url("a.css");.root a{b:c}', { root: '.root', exceptions: [] });
  assert.equal(r.ok, true);
  assert.equal(r.rules, 1);
});

test('reguly skompresowane w jednej linii', async () => {
  const { checkScope } = await core();
  const r = checkScope('.root a{color:red}.root b{color:blue!important}x{y:z}.root c{d:e}', { root: '.root', exceptions: [] });
  assert.deepEqual(r.bad, ['x']);
  assert.equal(r.rules, 4);
  assert.equal(r.important, 1);
});

test('rules = liczba { poza komentarzami i napisami (grupy i keyframes wliczone)', async () => {
  const { checkScope } = await core();
  const css = '@media x{.root a{content:"{"}}/* { */@keyframes k{from{a:b}to{a:b}}@font-face{a:b}';
  const r = checkScope(css, { root: '.root', exceptions: [] });
  assert.equal(r.rules, 1 + 1 + 1 + 2 + 1 + 0 + 0);
  const { countBraces } = await parse();
  assert.equal(r.rules, countBraces(css));
});

test('important: liczone poza komentarzami i napisami, rozne zapisy', async () => {
  const { checkScope } = await core();
  const css = '.root a{b:c !important;d:e! IMPORTANT;f:"!important"}/* !important */.root b{g:h!important}';
  const r = checkScope(css, { root: '.root', exceptions: [] });
  assert.equal(r.important, 3);
});

// ---- duzy syntetyczny CSS ----

test('duzy syntetyczny CSS: 1200 regul, liczby rules i important sie zgadzaja', async () => {
  const { checkScope } = await core();
  const parts = [];
  let importantExpected = 0;
  for (let i = 0; i < 1200; i++) {
    const imp = i % 7 === 0;
    if (imp) importantExpected++;
    parts.push(`.root .c${i} > .d${i}{color:#${(i % 4096).toString(16).padStart(3, '0')}${imp ? ' !important' : ''}}`);
    if (i % 100 === 0) parts.push(`/* c${i} { x:y !important } */`);
    if (i % 250 === 0) parts.push(`.root .s${i}::before{content:"{ !important }"}`);
  }
  const strays = Math.floor((1200 - 1) / 250) + 1;
  const css = parts.join('\n');
  const r = checkScope(css, { root: '.root', exceptions: [] });
  assert.equal(r.ok, true);
  assert.equal(r.rules, 1200 + strays);
  assert.equal(r.important, importantExpected);
  assert.deepEqual(r.bad, []);
});

test('duzy syntetyczny CSS: reguly w @media/@supports i zle selektory wsrod tysiecy poprawnych', async () => {
  const { checkScope } = await core();
  const parts = ['@media (min-width:1px){'];
  for (let i = 0; i < 1000; i++) parts.push(`.root .m${i}{a:b}`);
  parts.push('@supports (display:grid){');
  for (let i = 0; i < 500; i++) parts.push(i === 321 ? `.leak${i}{a:b}` : `.root .g${i}{a:b}`);
  parts.push('}}');
  const r = checkScope(parts.join(''), { root: '.root', exceptions: [] });
  assert.equal(r.rules, 1 + 1000 + 1 + 500);
  assert.deepEqual(r.bad, ['.leak321']);
  assert.equal(r.ok, false);
});

test('duzy syntetyczny CSS: kilka tysiecy regul bez progu czasowego (tylko poprawnosc)', async () => {
  const { checkScope } = await core();
  const n = 6000;
  const css = Array.from({ length: n }, (_, i) => `.root .x${i}{a:b}`).join('\n');
  const r = checkScope(css, { root: '.root', exceptions: [] });
  assert.equal(r.rules, n);
  assert.equal(r.ok, true);
});

// ---- blad rozbioru ----

test('blad rozbioru propagowany jako CssParseError (nie polykany)', async () => {
  const { checkScope } = await core();
  const { CssParseError } = await parse();
  const cfg = { root: '.root', exceptions: [] };
  for (const [css, code] of [
    ['.root a{', 'unbalanced-brace'],
    ['.root a{}}', 'unexpected-close'],
    ['.root a{content:"x', 'unterminated-string'],
    ['.root a{} /* x', 'unterminated-comment'],
  ]) {
    let err = null;
    try {
      checkScope(css, cfg);
    } catch (e) {
      err = e;
    }
    assert.ok(err instanceof CssParseError, css);
    assert.equal(err.code, code, css);
    assert.equal(typeof err.line, 'number');
  }
});
