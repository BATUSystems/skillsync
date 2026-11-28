// Dwuklik i okno zgody (review B, m6) w prawdziwej przeglądarce: prawdziwe zdarzenia myszy z `clickCount`.
// ctx: { page, approvals }
import { readFileSync, existsSync } from 'node:fs';
import { Suite, connect } from '../prototype/lib.mjs';
import { ctx, sleep, newPage, loginPage } from './common.mjs';

const BUTTON = `document.querySelector('button[data-approve="cp"]')`;
const CONFIRM = `document.querySelector('[data-consent-confirm]')`;
const WINDOW = `document.querySelector('[data-dialog="consent"]')`;
const rows = () => (existsSync(ctx.approvals) ? readFileSync(ctx.approvals, 'utf8').split('\n').filter((l) => l.trim()).length : 0);
const rect = (p, what) => p.evaluate(`(() => { const r = ${what}.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height, cx: r.x + r.width / 2, cy: r.y + r.height / 2 }; })()`);
const mouse = async (p, x, y, clickCount) => {
  for (const type of ['mousePressed', 'mouseReleased']) await p.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount });
};
async function show(p) {
  await p.go(ctx.page + '/', 1500);
  const ready = await p.waitFor(`(() => { const b = ${BUTTON}; return !!b && !b.disabled; })()`, 15000);
  if (!ready.ok) throw new Error('brak aktywnego przycisku zgody CP');
}

const suite = new Suite('consent_dblclick');
suite.setup(async () => ({ c: await connect() }));
suite.teardown(async (s) => { if (s) await s.c.closeAll(); });

suite.add('DBL-1', 'geometria: czy przycisk otwierający może leżeć pod „Zatwierdź” w kilku rozmiarach okna; dwuklik w tym miejscu niczego nie zapisuje', async (s) => {
  const p = await newPage(s.c, 'approver'); s.p = p;
  await loginPage(p, ctx.page, 'approver');
  const seen = [];
  for (const [width, height] of [[1440, 900], [1280, 600], [900, 500], [390, 700]]) {
    await p.size(width, height); await show(p);
    await p.domClick(BUTTON); await p.waitFor(`!!${CONFIRM}`, 4000);
    const confirm = await rect(p, CONFIRM);
    await p.key('Escape'); await sleep(200);
    // przewinięcie tak, by przycisk otwierający stanął na wysokości „Zatwierdź” (o ile strona na to pozwala)
    await p.evaluate(`(() => { const r = ${BUTTON}.getBoundingClientRect(); window.scrollBy(0, r.y + r.height / 2 - ${confirm.cy}); return true; })()`);
    await sleep(200);
    const opener = await rect(p, BUTTON);
    const left = Math.max(opener.x, confirm.x), right = Math.min(opener.x + opener.w, confirm.x + confirm.w);
    const top = Math.max(opener.y, confirm.y), bottom = Math.min(opener.y + opener.h, confirm.y + confirm.h);
    const overlap = right > left + 1 && bottom > top + 1;
    const point = overlap ? [(left + right) / 2, (top + bottom) / 2] : [opener.cx, opener.cy];
    await mouse(p, point[0], point[1], 1); await sleep(80); await mouse(p, point[0], point[1], 2); await sleep(700);
    seen.push({ width, height, overlap, opener, confirm, rows: rows() });
    await p.evaluate(`!!${WINDOW}`) && await p.key('Escape'); await sleep(200);
  }
  return { ok: rows() === 0 && seen.length === 4, seen };
});

suite.add('DBL-2', 'drugi klik dwukliku prosto w „Zatwierdź” niczego nie zapisuje, pojedynczy klik zapisuje (kontrola)', async ({ p }) => {
  await p.size(1440, 900); await show(p);
  await p.domClick(BUTTON); await p.waitFor(`!!${CONFIRM}`, 4000);
  const at = await rect(p, CONFIRM);
  await mouse(p, at.cx, at.cy, 2); await sleep(800);
  const afterSecond = { rows: rows(), open: await p.evaluate(`!!${WINDOW}`) };
  await mouse(p, at.cx, at.cy, 1); await sleep(1500);
  return { ok: afterSecond.rows === 0 && afterSecond.open && rows() === 1, afterSecond, rows: rows() };
});

await suite.main();
