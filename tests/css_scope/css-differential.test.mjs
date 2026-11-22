// Test roznicowy: STARY algorytm kontroli zakresu (regexy, bez parsera) zaimplementowany tutaj kontra nowe narzedzie
// na pseudolosowym korpusie PLASKIEGO CSS. Na plaskim CSS werdykty i liczniki musza byc identyczne;
// udokumentowane roznice (napis z klamra, [a="x,y"], @import ...; + regula, stray }) maja osobne testy.
// Korpus jest deterministyczny (generator z ziarnem), wiec porazka jest powtarzalna.
import test from 'node:test';
import assert from 'node:assert/strict';

const LIB = '../../library/skills/ui-verify/scripts/lib/';
const core = () => import(`${LIB}css-scope-core.mjs`);
const parse = () => import(`${LIB}css-parse.mjs`);

// ---------- konfiguracja zakresu (neutralne klasy) ----------

const EXACT_SEL = '.other main:focus';
const CONFIG = () => ({
  root: '.root',
  exceptions: [
    { id: 'menu-host', kind: 'prefix', value: '.menu-host > .item', reason: 'menu outside root' },
    { id: 'theme-hook', kind: 'root-alias', value: '.root-theme', onlyPropertyPrefix: '--tok-', reason: 'tokens only' },
    { id: 'skip-link', kind: 'exact-rule', selector: EXACT_SEL, declarations: ['outline:0!important'], reason: 'focus target' },
  ],
});

// ---------- STARY algorytm (referencja) ----------
// Usuniecie komentarzy regexem, `([^{}]+)\{` jako selektory, pominiecie `@...` oraz from|to|\d+%,
// podzial po przecinkach najwyzszego poziomu w nawiasach OKRAGLYCH, korzen `^\.root(?![\w-])`, prefiks startsWith,
// alias z ograniczeniem ciala do --tok- i wyjatkiem exact-rule, liczniki `{` i `!important`.
// `breakExact: true` to celowo zepsuta wersja (kontrola, ze harness potrafi wykryc rozbieznosc).
function oldCheck(css, config, { breakExact = false } = {}) {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const rootRe = /^\.root(?![\w-])/;
  const aliasRe = /^\.root-theme(?![\w-])/;
  const prefix = config.exceptions.find((e) => e.kind === 'prefix').value;
  const exactSel = config.exceptions.find((e) => e.kind === 'exact-rule').selector;
  const exactDecls = new Set(config.exceptions.find((e) => e.kind === 'exact-rule').declarations);
  const norm = (d) => {
    const i = d.indexOf(':');
    if (i < 0) return d.replace(/\s+/g, '');
    return d.slice(0, i).replace(/\s+/g, '').toLowerCase() + ':' + d.slice(i + 1).replace(/\s+/g, '');
  };
  const splitTop = (s, sep) => {
    const out = [];
    let depth = 0;
    let cur = '';
    for (const ch of s) {
      if (ch === '(') depth++;
      else if (ch === ')') depth = Math.max(0, depth - 1);
      if (ch === sep && depth === 0) {
        out.push(cur);
        cur = '';
      } else cur += ch;
    }
    out.push(cur);
    return out.map((x) => x.trim()).filter(Boolean);
  };
  const bad = [];
  const re = /([^{}]+)\{/g;
  let m;
  while ((m = re.exec(text))) {
    const selText = m[1].trim();
    const end = text.indexOf('}', re.lastIndex);
    const body = text.slice(re.lastIndex, end < 0 ? text.length : end);
    if (selText.startsWith('@')) continue;
    if (/^(from|to|\d+%)(\s*,\s*(from|to|\d+%))*$/.test(selText)) continue;
    const sels = splitTop(selText, ',');
    const decls = splitTop(body, ';');
    if (sels.length === 1 && !breakExact && sels[0].replace(/\s+/g, ' ') === exactSel) {
      const n = decls.map(norm);
      const ok = n.length === exactDecls.size && n.every((d) => exactDecls.has(d)) && new Set(n).size === exactDecls.size;
      if (!ok) bad.push(sels[0]);
      continue;
    }
    for (const sel of sels) {
      if (rootRe.test(sel)) continue;
      if (sel.startsWith(prefix)) continue;
      if (aliasRe.test(sel) && decls.every((d) => d.slice(0, d.indexOf(':') < 0 ? d.length : d.indexOf(':')).trim().startsWith('--tok-'))) continue;
      bad.push(sel);
    }
  }
  return {
    ok: bad.length === 0,
    bad: bad.length,
    rules: (text.match(/\{/g) || []).length,
    important: (text.match(/!\s*important/gi) || []).length,
  };
}

// ---------- generator pseudolosowy z ziarnem (mulberry32) ----------

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function makeGenerator(seed) {
  const rand = rng(seed);
  const int = (n) => Math.floor(rand() * n);
  const pick = (arr) => arr[int(arr.length)];
  const chance = (p) => rand() < p;
  const ws = (compact) => (compact ? '' : pick([' ', '  ', '\n', '\n  ', '\t']));

  const goodHeads = ['.root', '.root a', '.root > a', '.root:hover', '.root.is-open a', '.root[data-x]', '.root [data-k="v"]', '.root::before',
    '.root :is(a, b)', '.root :where(.x, .y) c', '.root :not(.a, .b)', '.root-theme', '.root-theme a', '.menu-host > .item', '.menu-host > .item a:hover', EXACT_SEL];
  const badHeads = ['body', 'a', 'footer', '.rooted', '.root-theme-x', '.root1', '.other a', '#root', 'a .root', 'html, body', '.menu-host > .other', ':is(.root, b) a', '.other main:hover', 'x .menu-host > .item'];
  const props = ['color', 'margin', 'padding', 'display', 'background', 'font-size', 'z-index', '--tok-a', '--tok-b', '--other'];
  const vals = ['red', '0', '1px solid blue', 'none', 'url(x.png)', 'calc(1px + 2px)', 'rgb(1, 2, 3)', '#fff', '"text value"', 'var(--tok-a, 1px)'];

  const decl = (compact, withImportant) => {
    const prop = pick(props);
    const val = pick(vals);
    const imp = withImportant ? (compact ? '!important' : pick([' !important', ' ! important', ' !IMPORTANT'])) : '';
    return compact ? `${prop}:${val}${imp}` : `${prop}${pick(['', ' '])}:${pick([' ', '  '])}${val}${imp}`;
  };
  const decls = (compact, kind) => {
    if (kind === 'tokens') return Array.from({ length: 1 + int(3) }, () => `--tok-${pick(['a', 'b', 'c'])}${compact ? ':' : ': '}${pick(['1', 'red', '2px'])}`);
    if (kind === 'exact-good') return [compact ? 'outline:0!important' : pick(['outline: 0 !important', 'OUTLINE : 0 ! important', 'outline:0 !important'])];
    if (kind === 'exact-bad') return pick([['outline:none!important'], ['outline:0'], ['outline: 0 !important', 'color: red'], []]);
    return Array.from({ length: int(4) }, () => decl(compact, chance(0.25)));
  };
  const joinDecls = (list, compact) => {
    if (list.length === 0) return '';
    const sep = compact ? ';' : pick([';', ';\n  ', '; ']);
    return list.join(sep) + (chance(0.5) ? ';' : '');
  };

  const rule = (compact) => {
    const roll = rand();
    let head;
    let list;
    if (roll < 0.5) {
      head = pick(goodHeads.filter((h) => h !== EXACT_SEL));
      list = decls(compact, head.startsWith('.root-theme') ? 'tokens' : 'plain');
      if (head.startsWith('.root-theme') && chance(0.3)) list = decls(compact, 'plain');
    } else if (roll < 0.62) {
      head = EXACT_SEL;
      list = decls(compact, chance(0.6) ? 'exact-good' : 'exact-bad');
    } else if (roll < 0.8) {
      head = pick(badHeads);
      list = decls(compact, head === '.other main:hover' ? 'exact-good' : 'plain');
    } else {
      const n = 2 + int(3);
      head = Array.from({ length: n }, () => pick(chance(0.8) ? goodHeads.filter((h) => h !== EXACT_SEL) : badHeads)).join(compact ? ',' : pick([', ', ' , ', ',\n']));
      list = decls(compact, 'plain');
    }
    const comment = chance(0.15) ? `/* ${pick(['note', 'x { y }', '} {', 'a, b'])} */${ws(compact)}` : '';
    const mid = chance(0.1) ? `/* c */` : '';
    return `${comment}${head}${mid}${ws(compact)}{${ws(compact)}${joinDecls(list, compact)}${ws(compact)}}`;
  };

  const group = (compact) => {
    const wrapper = pick(['@media (min-width: 10px)', '@media print', '@media screen and (max-width:5px)', '@supports (display: grid)']);
    const inner = Array.from({ length: 1 + int(3) }, () => rule(compact));
    return `${wrapper}${ws(compact)}{${ws(compact)}${inner.join(ws(compact))}${ws(compact)}}`;
  };

  const atLeaf = (compact) =>
    pick([
      `@font-face${ws(compact)}{font-family:x;src:url(a.woff2)}`,
      `@keyframes k${int(5)}${ws(compact)}{from{opacity:0}${ws(compact)}to{opacity:1}}`,
      `@keyframes k${int(5)} { 0%{a:b} 50%{a:c} 100%{a:d} }`,
      `@page{margin:0}`,
      `@property --x${int(3)}{syntax:"*";inherits:false}`,
    ]);

  return (index) => {
    const compact = chance(0.4);
    const n = 1 + int(12);
    const parts = [];
    for (let i = 0; i < n; i++) {
      const roll = rand();
      if (roll < 0.72) parts.push(rule(compact));
      else if (roll < 0.88) parts.push(group(compact));
      else parts.push(atLeaf(compact));
    }
    const lead = chance(0.2) ? `/* file ${index} */${chance(0.5) ? '\n' : ''}` : '';
    return lead + parts.join(compact ? '' : pick(['\n', '\n\n', ' ']));
  };
}

const CORPUS_SIZE = 300;
const corpus = (() => {
  const gen = makeGenerator(20240611);
  return Array.from({ length: CORPUS_SIZE }, (_, i) => gen(i));
})();

const newResult = async (css, config) => {
  const { checkScope } = await core();
  const r = checkScope(css, config);
  return { ok: r.ok, bad: r.bad.length, rules: r.rules, important: r.important };
};

// ---------- korpus: zgodnosc na plaskim CSS ----------

test('korpus: generator jest deterministyczny i zroznicowany (min. 200 plikow, poprawne i niepoprawne, skompresowane i rozwiniete)', async () => {
  assert.ok(corpus.length >= 200);
  const again = (() => {
    const g = makeGenerator(20240611);
    return Array.from({ length: CORPUS_SIZE }, (_, i) => g(i));
  })();
  assert.deepEqual(again, corpus, 'ten sam ziarn => ten sam korpus');
  const verdicts = corpus.map((css) => oldCheck(css, CONFIG()).ok);
  const okCount = verdicts.filter(Boolean).length;
  assert.ok(okCount >= 20, `za malo poprawnych plikow: ${okCount}`);
  assert.ok(corpus.length - okCount >= 100, `za malo niepoprawnych plikow: ${corpus.length - okCount}`);
  assert.ok(corpus.some((c) => !c.includes('\n')), 'brak skompresowanych plikow');
  assert.ok(corpus.some((c) => c.includes('\n')), 'brak rozwinietych plikow');
  assert.ok(corpus.some((c) => /@media/.test(c)) && corpus.some((c) => /:where\(/.test(c)) && corpus.some((c) => /!important/i.test(c)));
});

test('korpus: werdykt (ok), liczba zlych selektorow, rules i important identyczne ze starym algorytmem w KAZDYM pliku', async () => {
  const mismatches = [];
  for (let i = 0; i < corpus.length; i++) {
    const css = corpus[i];
    const expected = oldCheck(css, CONFIG());
    let actual;
    try {
      actual = await newResult(css, CONFIG());
    } catch (e) {
      mismatches.push(`#${i}: nowe narzedzie rzucilo ${e.message}\n${css}`);
      continue;
    }
    const want = { ok: expected.ok, bad: expected.bad, rules: expected.rules, important: expected.important };
    if (JSON.stringify(actual) !== JSON.stringify(want)) mismatches.push(`#${i}: nowe ${JSON.stringify(actual)} vs stare ${JSON.stringify(want)}\n${css}`);
  }
  assert.deepEqual(mismatches.slice(0, 3), [], `${mismatches.length} rozbieznosci z ${corpus.length}`);
});

test('korpus: dodatkowo rozne konfiguracje korzenia/wyjatkow nie zmieniaja zgodnosci (bez wyjatkow: tylko korzen)', async () => {
  // Bez wyjatkow stary algorytm = tylko korzen; porownanie na plikach bez at-regul poza @media
  const { checkScope } = await core();
  const bare = { root: '.root', exceptions: [] };
  let compared = 0;
  for (const css of corpus) {
    const sel = [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{/g)].map((m) => m[1].trim()).filter((s) => !s.startsWith('@') && !/^(from|to|\d+%)/.test(s));
    const expectedBad = sel.flatMap((s) => {
      const parts = [];
      let depth = 0;
      let cur = '';
      for (const ch of s) {
        if (ch === '(') depth++;
        else if (ch === ')') depth--;
        if (ch === ',' && depth === 0) { parts.push(cur); cur = ''; } else cur += ch;
      }
      parts.push(cur);
      return parts.map((p) => p.trim()).filter(Boolean);
    }).filter((p) => !/^\.root(?![\w-])/.test(p)).length;
    assert.equal(checkScope(css, bare).bad.length, expectedBad);
    compared++;
  }
  assert.equal(compared, corpus.length);
});

test('harness potrafi padac: zepsuty stary algorytm (bez exact-rule) rozni sie od nowego narzedzia na korpusie', async () => {
  let differing = 0;
  for (const css of corpus) {
    const broken = oldCheck(css, CONFIG(), { breakExact: true });
    const actual = await newResult(css, CONFIG());
    if (broken.ok !== actual.ok || broken.bad !== actual.bad) differing++;
  }
  assert.ok(differing >= 5, `korpus nie wykrywa uszkodzenia (rozbieznosci: ${differing})`);
});

// ---------- udokumentowane roznice (nowe narzedzie jest poprawniejsze niz stary algorytm) ----------

test('roznica: napis z klamra otwierajaca w ciele - nowe: 1 regula, ok; stare: fantomowa regula i bledne liczniki', async () => {
  const css = '.root a { content: "{" }';
  const now = await newResult(css, CONFIG());
  assert.deepEqual(now, { ok: true, bad: 0, rules: 1, important: 0 });
  const old = oldCheck(css, CONFIG());
  assert.notDeepEqual({ ok: old.ok, bad: old.bad, rules: old.rules }, { ok: true, bad: 0, rules: 1 }, 'stary algorytm ma sie tu roznic (inaczej test nic nie dowodzi)');
});

test('roznica: dwie reguly z klamra i !important w napisie - nowe liczy 2 reguly i 0 important', async () => {
  const css = '.root a { content: "{ !important }" } .root b { c: d }';
  const now = await newResult(css, CONFIG());
  assert.deepEqual(now, { ok: true, bad: 0, rules: 2, important: 0 });
  assert.notEqual(oldCheck(css, CONFIG()).rules, 2);
});

test('roznica: [a="x,y"] - przecinek w napisie atrybutu nie dzieli selektora (nowe ok, stare odrzuca "y\\"]")', async () => {
  const css = '.root [a="x,y"] { c: d }';
  assert.deepEqual(await newResult(css, CONFIG()), { ok: true, bad: 0, rules: 1, important: 0 });
  const old = oldCheck(css, CONFIG());
  assert.equal(old.ok, false);
  assert.equal(old.bad, 1);
});

test('roznica: `@import ...;` bezposrednio przed regula poza zakresem - nowe odrzuca regule, stare ja pomija', async () => {
  const css = '@import url(a.css);\nbody { color: red }';
  const now = await newResult(css, CONFIG());
  assert.equal(now.ok, false);
  assert.equal(now.bad, 1);
  assert.equal(oldCheck(css, CONFIG()).ok, true, 'stary algorytm uznawal selektor "@import ...; body" za at-regule');
  const { checkScope } = await core();
  assert.deepEqual(checkScope(css, CONFIG()).bad, ['body']);
});

test('roznica: stray } - nowe rzuca CssParseError (fail-closed), stare ignoruje', async () => {
  const { checkScope } = await core();
  const { CssParseError } = await parse();
  const css = '.root a { c: d } }\nbody { color: red }';
  assert.throws(() => checkScope(css, CONFIG()), (e) => e instanceof CssParseError && e.code === 'unexpected-close');
  const old = oldCheck(css, CONFIG());
  assert.equal(old.ok, false, 'stary algorytm widzial body, ale nie zglaszal bledu skladni');
});

test('roznica: klamra w komentarzu zgodna w obu (kontrola, ze komentarze sa usuwane tak samo)', async () => {
  const css = '/* } { body { x: y } */ .root a { c: d }';
  assert.deepEqual(await newResult(css, CONFIG()), { ok: true, bad: 0, rules: 1, important: 0 });
  const old = oldCheck(css, CONFIG());
  assert.deepEqual({ ok: old.ok, bad: old.bad, rules: old.rules }, { ok: true, bad: 0, rules: 1 });
});
