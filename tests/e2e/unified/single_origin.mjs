// K17/K20, kontrola negatywna: w --single-origin ten sam attack.js czyta hasło wpisane na /_login i dostaje token CSRF; baner jest widoczny.
// ctx: { page, slug }
import { Suite, connect } from '../prototype/lib.mjs';
import { ctx, J, sleep, newPage, gesture, loginPage, secret, mail } from './common.mjs';

const suite = new Suite('single_origin');
suite.setup(async () => ({ c: await connect() }));
suite.teardown(async (s) => { if (s) await s.c.closeAll(); });

const bannerOf = (p) => p.evaluate(`(() => { const b = document.querySelector('[data-single-origin]'); return b ? { visible: b.getClientRects().length > 0, text: b.textContent.slice(0, 160) } : null; })()`);

suite.add('SO-1', 'baner na stronie i na /_login', async ({ c }) => {
  const p = await newPage(c, 'banner');
  await p.go(ctx.page + '/', 1500); const board = await bannerOf(p);
  await p.go(ctx.page + '/_login', 800); const login = await bannerOf(p);
  return { ok: !!board?.visible && !!login?.visible, board, login };
});

suite.add('SO-2', 'kontrola negatywna: skrypt prototypu czyta hasło wpisane pod adresem /_login i token CSRF', async ({ c }) => {
  const user = await newPage(c, 'victim');
  await loginPage(user, ctx.page, 'commenter');
  const evil = await newPage(c, 'prototype', { shared: user.ctx });
  await evil.go(`${ctx.page}/${ctx.slug}/attack.html`, 1000);
  const stolen = await evil.evaluate(`window.attack.read('/_api/v1/session')`);
  const csrf = stolen.read ? JSON.parse(stolen.body).csrf : null;
  // druga, niezalogowana przeglądarka: ofiara trafia na prototyp, który przejmuje adres /_login
  const fresh = await newPage(c, 'prototype-anon');
  await fresh.go(`${ctx.page}/${ctx.slug}/attack.html`, 1000);
  // droga przez okno potomne: strona wysyła Cross-Origin-Opener-Policy, więc otwierający nie czyta jej dokumentu (odnotowane w wyniku)
  await gesture(fresh, `window.attack.open('/_login')`); await sleep(1200);
  const popup = await fresh.evaluate('window.attack.probe()');
  await fresh.evaluate('window.attack.close()');
  const taken = await fresh.evaluate(`window.attack.takeOver('/_login')`);
  await fresh.front();
  await fresh.evaluate(`document.querySelector('#login').focus(); true`); await fresh.type(mail('commenter'));
  await fresh.evaluate(`document.querySelector('#password').focus(); true`); await fresh.type(secret('commenter'));
  const matches = (await fresh.evaluate('window.attack.captured')) === secret('commenter');   // sam napis nie trafia do wyniku
  const path = await fresh.evaluate('location.pathname');
  return { ok: !!csrf && matches && path === '/_login', csrfRead: !!csrf, passwordRead: matches, address: path, takenOver: taken.taken, popupAccess: popup };
});

await suite.main();
