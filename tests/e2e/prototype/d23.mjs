// Przełącznik ekranów (wzbogacony pasek ekranów) na prototypie z 28 ekranami, strona z dysku (file://). Szerokości 1920/1280/1000/375, oba motywy.
import { Suite, sleep, workspace, connect, errors, env, consoleVerdict } from './lib.mjs';

const suite = new Suite('d23');
let ws; let c; let p;
const url = () => 'file://' + ws.file('plan');
const q = (s) => `document.querySelector(${JSON.stringify(s)})`;
const MAIN = q('.proto-screens-main');
const state = `(() => { const m = ${MAIN}; const pop = ${q('.proto-screens-pop')}; return { num: (${q('.proto-screens-num')} || {}).textContent, name: (${q('.proto-screens-name')} || {}).textContent, expanded: m && m.getAttribute('aria-expanded'), popOpen: !!pop && !pop.hidden && getComputedStyle(pop).display !== 'none' && pop.getBoundingClientRect().height > 0, current: (document.querySelector('.screen.is-current') || {}).id, onMain: document.activeElement === m, activeClass: document.activeElement && (document.activeElement.className || document.activeElement.tagName) }; })()`;
const setTheme = async (theme) => {
  for (let i = 0; i < 3; i++) {
    if ((await p.evaluate(`document.documentElement.dataset.theme`)) === theme) return true;
    await p.evaluate(`(() => { const b = [...document.querySelectorAll('.proto-toolbar button')].find((x) => /Motyw/.test(x.textContent + (x.getAttribute('aria-label') || ''))); if (b) b.click(); return 1; })()`); await sleep(250);
  }
  return (await p.evaluate(`document.documentElement.dataset.theme`)) === theme;
};
const layout = () => p.evaluate(`(() => { const bar = ${q('.doc-toolbar')}; const nav = ${q('nav.screen-nav')}; const t = ${q('.proto-toolbar')};
  return { enhanced: nav.classList.contains('proto-screens'), links: nav.querySelectorAll(':scope > a').length, linksHidden: [...nav.querySelectorAll(':scope > a')].every((a) => getComputedStyle(a).display === 'none'), buttons: document.querySelectorAll('.proto-screens-bar button').length,
    areas: getComputedStyle(bar).gridTemplateAreas, barH: bar.offsetHeight, varH: getComputedStyle(document.documentElement).getPropertyValue('--proto-toolbar-height').trim(), scrollW: document.documentElement.scrollWidth, innerW: innerWidth,
    toolsClipped: t ? t.scrollWidth > t.clientWidth + 1 : false, mainH: ${MAIN}.getBoundingClientRect().height, stepH: document.querySelector('[data-screen-step]').getBoundingClientRect().height,
    prevDisabled: document.querySelector('[data-screen-step="prev"]').getAttribute('aria-disabled') === 'true', nextLabel: document.querySelector('[data-screen-step="next"]').getAttribute('aria-label'), bodyBg: getComputedStyle(document.body).backgroundColor }; })()`);
const openList = async () => { await p.evaluate(`${MAIN}.click(); true`); await p.waitFor(`${MAIN}.getAttribute('aria-expanded') === 'true'`, 3000); await sleep(200); };
const clearSearch = () => p.evaluate(`(() => { const i = ${q('.proto-screens-search')}; i.value = ''; i.dispatchEvent(new Event('input', { bubbles: true })); return 1; })()`);
const visibleOptions = `[...document.querySelectorAll('.proto-screens-option')].filter((o) => o.getClientRects().length > 0)`;

suite.setup(async () => { ws = workspace('d23'); c = await connect(); p = await c.page('d23'); await p.go(url()); return {}; });
suite.teardown(async () => { if (c) await c.closeAll(); });

suite.add('D23-ulepszenie', 'Ulepszenie nav.screen-nav', async () => {
  await p.size(1920, 1080); await p.go(url());
  const l = await layout();
  return { ok: l.enhanced && l.links === 28 && l.linksHidden && l.buttons >= 3 && l.prevDisabled && /^Następny ekran: 2 /.test(l.nextLabel || ''), links: l.links, hidden: l.linksHidden, buttons: l.buttons, prevDisabled: l.prevDisabled, nextLabel: l.nextLabel };
});

suite.add('D23-układ-przewijanie', 'Brak poziomego przewijania', async () => {
  const bad = []; const cases = [];
  for (const theme of ['light', 'dark']) for (const [w, h] of [[1920, 1080], [1280, 800], [1000, 800], [375, 812]]) {
    await p.size(w, h); await p.go(url()); const t = await setTheme(theme); await sleep(300);
    const l = await layout(); const min = (w < 600 ? 44 : 36) - 0.5;
    const good = t && l.scrollW <= l.innerW && l.varH === l.barH + 'px' && l.mainH >= min && l.stepH >= min && (w < 1000 || !l.toolsClipped);
    cases.push(`${theme}/${w}:${good ? 'ok' : 'BAD'}`); if (!good) bad.push({ theme, w, scrollW: l.scrollW, innerW: l.innerW, varH: l.varH, barH: l.barH, mainH: l.mainH, stepH: l.stepH, toolsClipped: l.toolsClipped, themeSet: t });
  }
  return { ok: bad.length === 0, cases, bad };
});

suite.add('D23-układ-wiersze', 'Pasek mieści się, elementy nie nachodzą', async () => {
  // kontrakt: pasek bez przewijania poziomego i bez nachodzenia elementów na siebie (liczba wierszy dowolna)
  const rows = {};
  for (const [w, h] of [[1920, 1080], [1280, 800], [1000, 800]]) {
    await p.size(w, h); await p.go(url());
    rows[w] = await p.evaluate(`(() => { const bar = ${q('.doc-toolbar')}; const items = [...bar.querySelectorAll(':scope > *, .proto-toolbar > *')].filter((e) => e.getClientRects().length && e.getBoundingClientRect().width > 0);
      const leaf = items.filter((e) => !items.some((o) => o !== e && e.contains(o))); let overlap = 0;
      for (let i = 0; i < leaf.length; i++) for (let j = i + 1; j < leaf.length; j++) { const a = leaf[i].getBoundingClientRect(), b = leaf[j].getBoundingClientRect(); if (!(a.right <= b.left + 1 || b.right <= a.left + 1 || a.bottom <= b.top + 1 || b.bottom <= a.top + 1)) overlap++; }
      return { overlap, items: leaf.length, barOverflow: [bar, ${q('.proto-toolbar')}].some((e) => e && e.scrollWidth > e.clientWidth + 1), docOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth }; })()`);
  }
  const good = (x) => x.items > 0 && x.overlap === 0 && !x.barOverflow && !x.docOverflow;
  return { ok: Object.values(rows).every(good), rows };
}, { critical: false });

suite.add('D23-lista', 'Lista ekranów', async () => {
  await p.size(1920, 1080); await p.go(url()); await openList();
  const g = await p.evaluate(`(() => { const o = [...document.querySelectorAll('.proto-screens-option')]; const cols = new Set(o.map((e) => Math.round(e.getBoundingClientRect().left))).size; const inView = o.every((e) => { const r = e.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }); return { n: o.length, cols, inView, focusSearch: document.activeElement === ${q('.proto-screens-search')} }; })()`);
  const s = await p.evaluate(state); S.cols = g.cols;
  return { ok: s.expanded === 'true' && s.popOpen && g.focusSearch && g.n === 28 && g.inView && g.cols >= 3, ...g };
});
const S = {};

suite.add('D23-szukaj', 'Wyszukiwanie', async () => {
  await p.type('tydzien'); await sleep(200);
  const t = await p.evaluate(`${visibleOptions}.map((o) => o.textContent.trim())`);
  await clearSearch(); await p.type('1'); await sleep(200);
  const one = await p.evaluate(`${visibleOptions}.length`);
  await clearSearch(); await p.type('zzz'); await sleep(200);
  const empty = await p.evaluate(`(${q('.proto-screens-empty')} || {}).textContent`);
  await clearSearch();
  return { ok: t.length === 2 && one === 11 && /Brak ekranu „zzz”\./.test(empty || ''), tydzien: t, prefix1: one, empty };
});

suite.add('D23-klawiatura-listy', 'Klawiatura listy', async () => {
  const act = `[...document.querySelectorAll('.proto-screens-option')].findIndex((o) => o.classList.contains('is-active'))`;
  const a0 = await p.evaluate(act); await p.key('ArrowDown'); const a1 = await p.evaluate(act); await p.key('ArrowRight'); const a2 = await p.evaluate(act);
  await p.key('End'); const a3 = await p.evaluate(act); await p.key('Home'); const a4 = await p.evaluate(act);
  const ad = await p.evaluate(`(() => { const i = ${q('.proto-screens-search')}; const id = i.getAttribute('aria-activedescendant'); return !!id && !!document.getElementById(id); })()`);
  await p.key('ArrowDown'); await p.key('ArrowDown');
  const pickedNum = await p.evaluate(`(() => { const o = [...document.querySelectorAll('.proto-screens-option')].find((x) => x.classList.contains('is-active')); return o ? parseInt(o.textContent.trim(), 10) : null; })()`);
  await p.key('Enter'); await sleep(1500);
  const after = await p.evaluate(state);
  await openList(); await p.key('Escape'); const esc = await p.evaluate(state);
  return { ok: a1 > a0 && a2 !== a1 && a3 === 27 && a4 === 0 && ad && Number.isInteger(pickedNum) && after.current === 's' + pickedNum && !after.popOpen && after.onMain && !esc.popOpen && esc.onMain, moves: [a0, a1, a2, a3, a4], afterEnter: after.current, escClosed: !esc.popOpen, focusOnMain: esc.onMain };
});

suite.add('D23-zamykanie', 'Zamknięcie listy', async () => {
  await openList(); await p.click(960, 1000); await sleep(250);
  const out = (await p.evaluate(state)).popOpen;
  await openList();
  await p.evaluate(`(() => { const b = [...document.querySelectorAll('.proto-toolbar button')].find((x) => /Wątki/.test(x.textContent + (x.getAttribute('aria-label') || ''))); b.click(); return 1; })()`); await sleep(300);
  const panel = (await p.evaluate(state)).popOpen;
  await p.key('Escape'); await p.key('Escape');
  return { ok: !out && !panel, openAfterOutsideClick: out, openAfterPanel: panel };
});

suite.add('D23-skróty', 'Skróty globalne', async () => {
  await p.go(url());
  const blur = () => p.evaluate(`(document.activeElement && document.activeElement.blur && document.activeElement.blur(), window.scrollTo(0, 0), 1)`);
  await blur(); await sleep(800);
  const n0 = (await p.evaluate(state)).num; await blur(); await p.key('ArrowRight'); await sleep(1400);
  const n1 = (await p.evaluate(state)).num; await blur(); await p.key('ArrowLeft'); await sleep(1400);
  const n2 = (await p.evaluate(state)).num; await blur(); await p.key('g'); const g = (await p.evaluate(state)).popOpen; await p.key('Escape');
  // te same klawisze w polu tekstowym nie zmieniają ekranu
  await p.evaluate(`(() => { const i = document.createElement('input'); i.id = 'e2e-field'; i.style.cssText = 'position:fixed;top:60px;left:10px;z-index:9'; document.body.appendChild(i); i.focus(); return 1; })()`);
  await p.key('ArrowRight'); await sleep(900); const inField = (await p.evaluate(state)).num;
  await p.key('g'); const gField = (await p.evaluate(state)).popOpen;
  await p.evaluate(`document.getElementById('e2e-field').remove(); true`);
  return { ok: /^1 \//.test(n0) && /^2 \//.test(n1) && /^1 \//.test(n2) && g && /^1 \//.test(inField) && !gField, sequence: [n0, n1, n2], gOpens: g, inField, gInField: gField };
});

suite.add('D23-scroll-spy', 'Śledzenie przewijania', async () => {
  await p.go(url()); const before = await p.evaluate(`location.hash`);
  await p.evaluate(`document.getElementById('s5').scrollIntoView({ behavior: 'instant', block: 'start' }); true`); await sleep(1500);
  const s = await p.evaluate(`({ num: ${q('.proto-screens-num')}.textContent, cur: ${q('nav.screen-nav a[aria-current], .proto-screens-option[aria-current]')} && 1, curId: (document.querySelector('.screen.is-current') || {}).id, hash: location.hash })`);
  return { ok: /^5 \/ 28/.test(s.num) && s.curId === 's5' && s.hash === before, ...s };
});

suite.add('D23-ostatni', 'Skok na ostatni ekran', async () => {
  await p.go(url()); await openList();
  await p.key('End'); await p.key('Enter'); await sleep(2000);
  const s = await p.evaluate(state);
  return { ok: /^28 \/ 28/.test(s.num), num: s.num };
});

suite.add('D23-fokus', 'Widoczny fokus', async () => {
  const f = await p.evaluate(`(() => { const out = {}; for (const [k, el] of [['step', document.querySelector('[data-screen-step]')], ['main', ${MAIN}]]) { el.focus({ focusVisible: true }); const cs = getComputedStyle(el); out[k] = { fv: el.matches(':focus-visible'), outline: cs.outlineStyle + ' ' + cs.outlineWidth }; } return out; })()`);
  await openList();
  f.search = await p.evaluate(`(() => { const el = ${q('.proto-screens-search')}; const cs = getComputedStyle(el); return { fv: el.matches(':focus-visible'), outline: cs.outlineStyle + ' ' + cs.outlineWidth, shadow: cs.boxShadow }; })()`);
  await p.key('Escape');
  const vis = (x) => x.fv && /solid 2px/.test(x.outline);
  return { ok: vis(f.step) && vis(f.main) && (vis(f.search) || f.search.shadow !== 'none'), focus: f };
});

suite.add('D23-home', 'Link do galerii prototypów', async () => {
  await p.size(1920, 1080); await p.go(url());
  const r = await p.evaluate(`(() => { const a = ${q('.proto-screens-home')}; if (!a) return { present: false }; const bar = ${q('.proto-screens-bar')}; a.focus({ focusVisible: true }); const cs = getComputedStyle(a);
    return { present: true, href: a.getAttribute('href'), label: a.getAttribute('aria-label'), first: bar && bar.firstElementChild === a, isLink: a.tagName === 'A', fv: a.matches(':focus-visible'), outline: cs.outlineStyle + ' ' + cs.outlineWidth, h: a.getBoundingClientRect().height }; })()`);
  const ok = r.present && r.isLink && r.href === '../' && r.label === 'Lista prototypów' && r.first && r.fv && /solid 2px/.test(r.outline) && r.h >= 35.5;
  return { ok, ...r };
});

suite.add('D23-375', 'Telefon', async () => {
  await p.size(375, 812); await p.go(url()); await openList();
  const r = await p.evaluate(`(() => { const pop = ${q('.proto-screens-pop')}.getBoundingClientRect(); const bars = [...document.querySelectorAll('.doc-toolbar, .proto-toolbar')].map((e) => ({ sw: e.scrollWidth, cw: e.clientWidth })); const de = document.documentElement; return { left: Math.round(pop.left), width: Math.round(pop.width), vw: de.clientWidth, docSw: de.scrollWidth, bars }; })()`);
  await p.key('Escape'); await p.size(1920, 1080);
  const noHScroll = r.docSw <= r.vw && r.bars.every((b) => b.sw <= b.cw);
  const restOk = r.left <= 1 && r.width >= r.vw - 2;
  return { ok: restOk && noHScroll, restOk, defectObserved: restOk && !noHScroll, noHScroll, ...r };
});

suite.add('D23-motywy', 'Oba motywy', async () => {
  const res = [];
  for (const theme of ['light', 'dark']) for (const w of [1920, 1280, 1000, 375]) {
    await p.size(w, w < 600 ? 812 : 900); await p.go(url()); const set = await setTheme(theme); await sleep(200);
    const r = await p.evaluate(`(() => { const bg = getComputedStyle(document.body).backgroundColor; const bar = getComputedStyle(${q('.doc-toolbar')}).backgroundColor; const fg = getComputedStyle(${MAIN}).color; return { bg, bar, fg }; })()`);
    const lum = (css) => { const m = /oklch\(\s*([\d.]+)/.exec(css); if (m) return +m[1]; const x = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(css); return x ? (+x[1] + +x[2] + +x[3]) / 765 : null; };
    const lb = lum(r.bg); const lf = lum(r.fg);
    res.push({ theme, w, set, contrast: lb !== null && lf !== null && Math.abs(lb - lf) > 0.4, blackFields: theme === 'light' && lb !== null && lb < 0.2 });
  }
  return { ok: res.every((x) => x.set && x.contrast && !x.blackFields), cases: res.filter((x) => !(x.set && x.contrast && !x.blackFields)).concat([{ checked: res.length }]) };
});

suite.add('D23-prezentacja', 'Tryb Prezentacja', async () => {
  await p.size(1920, 1080); await p.go(url());
  await p.evaluate(`[...document.querySelectorAll('.proto-toolbar button')].find((b) => /^Prezentacja/.test(b.textContent.trim())).click(); true`); await sleep(500);
  await p.evaluate(`document.querySelector('[data-screen-step="next"]').click(); true`); await sleep(1500);
  const s = await p.evaluate(state);
  return { ok: /^2 \/ 28/.test(s.num) && s.current === 's2', num: s.num, current: s.current };
});

suite.add('D23-strip', 'Wyłącznik paska ekranów', async () => {
  const html = ws.read('plan'); ws.write('plan', 'index.html', html.replace('<nav class="screen-nav"', '<nav class="screen-nav" data-proto-nav="strip"'));
  const q2 = await c.page('strip'); await q2.go(url());
  const r = await q2.evaluate(`(() => { const n = document.querySelector('nav.screen-nav'); return { enhanced: n.classList.contains('proto-screens'), bar: !!document.querySelector('.proto-screens-bar'), links: [...n.querySelectorAll(':scope > a')].filter((a) => getComputedStyle(a).display !== 'none').length }; })()`);
  await q2.dispose(); ws.write('plan', 'index.html', html);
  return { ok: !r.enhanced && !r.bar && r.links === 28, ...r };
});


await suite.main();
