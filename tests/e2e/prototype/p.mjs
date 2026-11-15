// Tryb prezentacji (jeden ekran naraz): P-01 … P-05. Prototyp `plan` (28 ekranów, strona z dysku) dla P-01, P-02, P-03, P-05; prototyp `demo`
// z serwera i wątkami dla P-04. Widoczność liczona z obliczonego stylu (`display`), nie z klas silnika.
import { Suite, sleep, workspace, startServer, connect, PORT1, session, sessionPage, createThread, openPanel, closePanelIfOpen } from './lib.mjs';

const suite = new Suite('p');
let ws; let srv; let c; let ANN; let p;
const fileUrl = () => 'file://' + ws.file('plan');
const visibleScreens = `[...document.querySelectorAll('section.screen')].filter((s) => getComputedStyle(s).display !== 'none').map((s) => s.id)`;
const snapshot = `(() => ({ vis: ${visibleScreens}, hash: location.hash, search: location.search, cls: document.documentElement.classList.contains('proto-presentation'),
  count: (document.querySelector('.proto-presentation-count') || {}).textContent || '', total: document.querySelectorAll('section.screen').length }))()`;
const toggleButton = `[...document.querySelectorAll('.proto-toolbar button')].find((b) => /^Prezentacja/.test(b.textContent.trim()))`;
const barButton = (label) => `document.querySelector('.proto-presentation-bar [aria-label=${JSON.stringify(label)}]')`;
// Stan trybu pamięta `sessionStorage` karty (odświeżenie ma wracać na ekran): scenariusze na tej samej karcie zaczynają od czystej pamięci.
const fresh = async (page, url) => { await page.go(url); await page.evaluate(`sessionStorage.clear(); true`); await page.go(url); };
const enter = async (page) => { await page.evaluate(`${toggleButton}.click(); true`); await sleep(400); };
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

suite.setup(async () => {
  ws = workspace('p');
  srv = await startServer(ws, { port: PORT1 });
  c = await connect();
  ANN = await session(srv.base, 'anna');
  p = await c.page('p');
  await p.size(1280, 900);
  return {};
});
suite.teardown(async () => { if (c) await c.closeAll(); if (srv) await srv.stop(); });

suite.add('P-01', 'Prezentacja: jeden widoczny ekran po włączeniu przyciskiem', async () => {
  await fresh(p, fileUrl());
  const before = await p.evaluate(snapshot);
  await enter(p);
  const after = await p.evaluate(snapshot);
  return { ok: before.vis.length === before.total && before.total >= 20 && after.cls && same(after.vis, ['s1']) && after.count === '1 / ' + after.total, before: { visible: before.vis.length, total: before.total }, after };
});

suite.add('P-02', 'Prezentacja: strzałki, przyciski ‹ ›, Home/End, licznik i hash', async () => {
  await fresh(p, fileUrl()); await enter(p);
  const seen = [];
  const mark = async (step) => { const s = await p.evaluate(snapshot); seen.push({ step, vis: s.vis, hash: s.hash, count: s.count }); return s; };
  await p.key('ArrowRight'); await sleep(250); const right = await mark('→');
  await p.evaluate(`${barButton('Następny ekran')}.click(); true`); await sleep(250); const next = await mark('›');
  await p.evaluate(`${barButton('Poprzedni ekran')}.click(); true`); await sleep(250); const prev = await mark('‹');
  await p.key('End'); await sleep(250); const end = await mark('End');
  await p.key('ArrowRight'); await sleep(250); const pastEnd = await mark('→ na końcu');
  await p.key('Home'); await sleep(250); const home = await mark('Home');
  await p.key('ArrowLeft'); await sleep(250); const beforeFirst = await mark('← na początku');
  const total = right.total;
  const ok = same(right.vis, ['s2']) && right.hash === '#s2' && right.count === '2 / ' + total
    && same(next.vis, ['s3']) && next.hash === '#s3' && same(prev.vis, ['s2'])
    && same(end.vis, ['s' + total]) && end.count === total + ' / ' + total && end.hash === '#s' + total && same(pastEnd.vis, end.vis)
    && same(home.vis, ['s1']) && home.hash === '#s1' && same(beforeFirst.vis, ['s1']);
  return { ok, seen };
});

suite.add('P-03', 'Prezentacja: wyjście przywraca wszystkie ekrany; wejście z adresu i odświeżenie zostają na ekranie z hasha; klawisz P', async () => {
  await fresh(p, fileUrl()); await enter(p); await p.key('ArrowRight'); await sleep(250);
  await p.key('Escape'); await sleep(400);
  const exited = await p.evaluate(snapshot);
  await p.go(fileUrl() + '?mode=presentation#s4'); await sleep(500);
  const fromUrl = await p.evaluate(snapshot);
  await p.evaluate(`location.reload(); true`); await sleep(900);
  const reloaded = await p.evaluate(snapshot);
  await p.key('p'); await sleep(400);
  const toggledOff = await p.evaluate(snapshot);
  await p.evaluate(`location.reload(); true`); await sleep(900);
  const afterOffReload = await p.evaluate(snapshot);
  await p.key('p'); await sleep(400);
  const toggledOn = await p.evaluate(snapshot);
  const ok = exited.vis.length === exited.total && !exited.cls
    && fromUrl.cls && same(fromUrl.vis, ['s4']) && fromUrl.count === '4 / ' + fromUrl.total
    && reloaded.cls && same(reloaded.vis, ['s4']) && reloaded.hash === '#s4'
    && !toggledOff.cls && toggledOff.vis.length === toggledOff.total && !/mode=presentation/.test(toggledOff.search)
    && !afterOffReload.cls && afterOffReload.vis.length === afterOffReload.total
    && toggledOn.cls && same(toggledOn.vis, ['s4']);
  return { ok, exited, fromUrl, reloaded, toggledOff, afterOffReload, toggledOn };
});

suite.add('P-04', 'Prezentacja: pinezki i wątki ukrytych ekranów znikają z ekranem; wybór wątku z panelu przełącza ekran; akceptacja zostaje', async () => {
  await createThread(ANN, 'demo', 's1', 's1-karta-tytul', 'P-04 wątek na s1');
  await createThread(ANN, 'demo', 's2', 's2-nazwa', 'P-04 wątek na s2');
  const { p: q } = await sessionPage(c, 'p04', srv.base, 'celina');
  await q.size(1280, 900); await q.go(`${srv.base}/demo/`);
  const pins = (page) => page.evaluate(`[...document.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].filter((b) => b.getClientRects().length > 0).map((b) => b.closest('section.screen') ? b.closest('section.screen').id : null)`);
  await q.waitFor(`document.querySelectorAll('button.anno-pin:not(.anno-pin-static)').length >= 2`, 15000);
  const normal = await pins(q);
  await enter(q);
  const onS1 = await pins(q);
  await q.key('ArrowRight'); await sleep(400);
  const onS2 = await pins(q);
  const chip = await q.evaluate(`(() => { const x = document.getElementById('anno-accept-chip'); return !!x && x.getClientRects().length > 0; })()`);
  await q.key('Home'); await sleep(300);
  await openPanel(q); await sleep(300);
  await q.domClick(`[...document.querySelectorAll('.anno-panel .anno-card')].find((e) => /P-04 wątek na s2/.test(e.innerText))`, 'karta wątku s2').catch(() => {});
  await sleep(700);
  const afterPick = await q.evaluate(snapshot);
  await closePanelIfOpen(q);
  const ok = normal.includes('s1') && normal.includes('s2') && same(onS1, ['s1']) && same(onS2, ['s2']) && chip && same(afterPick.vis, ['s2']) && afterPick.hash === '#s2';
  return { ok, normal, onS1, onS2, chip, afterPick };
});

suite.add('P-05', 'Prezentacja: klawisze pomijają pola tekstowe, modyfikatory i wyłączone skróty; Esc zamyka najpierw listę ekranów', async () => {
  await fresh(p, fileUrl());
  await p.evaluate(`(() => { const i = document.createElement('input'); i.id = 'p05-probe'; i.type = 'text'; document.body.insertBefore(i, document.body.firstChild); i.focus(); return true; })()`);
  await p.key('p'); await sleep(250);
  const typing = await p.evaluate(snapshot);
  await p.evaluate(`document.getElementById('p05-probe').blur(); document.body.focus(); true`);
  await p.key('p', { ctrl: true }); await sleep(250);
  const withCtrl = await p.evaluate(snapshot);
  await p.evaluate(`localStorage.setItem('skillsync-prototype-shortcuts', 'off'); true`);
  await p.go(fileUrl());
  await p.key('p'); await sleep(250);
  const disabled = await p.evaluate(snapshot);
  await p.evaluate(`localStorage.removeItem('skillsync-prototype-shortcuts'); true`);
  await fresh(p, fileUrl()); await enter(p); await p.key('ArrowRight'); await sleep(250);
  await p.evaluate(`document.querySelector('.proto-screens-main').click(); true`); await sleep(300);
  await p.key('Escape'); await sleep(300);
  const listClosedFirst = await p.evaluate(`(() => ({ expanded: document.querySelector('.proto-screens-main').getAttribute('aria-expanded'), cls: document.documentElement.classList.contains('proto-presentation') }))()`);
  await p.key('Escape'); await sleep(300);
  const thenExit = await p.evaluate(snapshot);
  const ok = !typing.cls && !withCtrl.cls && !disabled.cls && listClosedFirst.expanded === 'false' && listClosedFirst.cls === true && !thenExit.cls;
  return { ok, typing, withCtrl, disabled, listClosedFirst, thenExit };
});

suite.add('P-06', 'Prezentacja: po kliknięciu myszą klawisze nadal działają; Shift+strzałka nie nawiguje; lista ekranów zachowuje strzałki', async () => {
  await fresh(p, fileUrl());
  await p.clickEl(toggleButton, 'przycisk Prezentacja'); await sleep(400);
  const entered = await p.evaluate(snapshot);
  await p.key('ArrowRight'); await sleep(250);
  const afterKey = await p.evaluate(snapshot);
  await p.clickEl(`document.querySelector('.proto-presentation-bar [aria-label="Następny ekran"]')`, 'przycisk ›'); await sleep(250);
  const afterClick = await p.evaluate(snapshot);
  await p.key('ArrowLeft'); await sleep(250);
  const afterBack = await p.evaluate(snapshot);
  await p.key('ArrowRight', { shift: true }); await sleep(250);
  const afterShift = await p.evaluate(snapshot);
  const ok = entered.cls && same(entered.vis, ['s1']) && same(afterKey.vis, ['s2']) && same(afterClick.vis, ['s3']) && same(afterBack.vis, ['s2']) && same(afterShift.vis, ['s2']);
  return { ok, entered, afterKey, afterClick, afterBack, afterShift };
});

await suite.main();
