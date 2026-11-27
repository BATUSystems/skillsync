// K18 w przeglądarce: przekazanie sesji strony na origin prototypów (begin -> logowanie -> kod -> wymiana) i jego odmowy.
// ctx: { page, proto, slug }
import { Suite, connect } from '../prototype/lib.mjs';
import { ctx, J, sleep, newPage, signInOnLoginPage, sessionOf, mail } from './common.mjs';

const DENIED = ['approver', 'acceptor', 'developer'];
const suite = new Suite('handoff');
suite.setup(async () => ({ c: await connect() }));
suite.teardown(async (s) => { if (s) await s.c.closeAll(); });

// Adresy (z fragmentem), nagłówki i ciała żądań karty.
function recorder(p) {
  const log = [];
  p.listeners.push((m) => {
    if (m.method === 'Network.requestWillBeSent') { const r = m.params.request; log.push({ method: r.method, url: r.url, fragment: r.urlFragment || '', body: r.postData || '', referer: r.headers.Referer || '', redirectedFrom: m.params.redirectResponse?.url || '' }); }
  });
  return log;
}
const codeOf = (log) => { for (const r of log) { const m = /#code=([^&]+)/.exec(r.fragment || r.url); if (m) return decodeURIComponent(m[1]); } return null; };
const onProto = (p) => p.waitFor(`location.origin === ${J(ctx.proto)} && location.pathname === '/' && !location.hash && !location.search && document.readyState === 'complete'`, 15000);
const proto = (p) => p.evaluate(`fetch('/_api/v1/session').then((r) => r.json())`);

suite.add('HO-1', 'anonim: begin -> logowanie na stronie -> powrót na origin prototypów z sesją bez approver/acceptor/developer', async (s) => {
  const p = await newPage(s.c, 'user'); const log = recorder(p);
  await p.go(ctx.proto + '/_handoff/begin', 1200);
  const atLogin = await p.evaluate('({ origin: location.origin, path: location.pathname })');
  await signInOnLoginPage(p, 'approver');
  const arrived = await onProto(p);
  if (!arrived.ok) return { ok: false, atLogin, stuck: await p.evaluate('location.origin + location.pathname') };
  const handed = await proto(p);
  await p.go(ctx.page + '/', 800); const board = await sessionOf(p);
  const roles = handed.user?.roles || [];
  const code = codeOf(log);
  s.handed = p; s.log = log; s.code = code;
  const inReferer = !!code && log.some((r) => r.referer.includes(code));
  const inQuery = !!code && log.some((r) => r.url.split('#')[0].includes(code));
  const history = await p.send('Page.getNavigationHistory');
  const inHistory = !!code && history.entries.some((e) => e.url.includes(code));
  const boardRoles = board.user?.roles || [];
  return { ok: atLogin.origin === ctx.page && atLogin.path === '/_login' && !!handed.user && !!code && boardRoles.includes('approver') && boardRoles.includes('acceptor')
    && roles.includes('commenter') && !DENIED.some((r) => roles.includes(r)) && !inReferer && !inQuery && !inHistory, atLogin, handedRoles: roles, boardRoles, codeSeen: !!code, inReferer, inQuery, inHistory };
});

suite.add('HO-2', 'ciasteczka sesji: HttpOnly, SameSite=Strict, osobne dla originów, niewidoczne dla skryptu', async (s) => {
  const { cookies } = await s.handed.send('Network.getCookies', { urls: [ctx.page + '/', ctx.proto + '/'] });
  const sessions = cookies.filter((c) => !c.name.includes('handoff') && c.value);
  const shape = sessions.map((c) => ({ name: c.name, httpOnly: c.httpOnly, sameSite: c.sameSite, path: c.path, session: c.session }));
  const visible = await s.handed.evaluate('document.cookie');
  return { ok: shape.length === 2 && shape.every((c) => c.httpOnly && c.sameSite === 'Strict') && new Set(shape.map((c) => c.name)).size === 2 && !sessions.some((c) => visible.includes(c.value)), shape };
});

suite.add('HO-3', 'kod działa raz: ponowne użycie w tej samej przeglądarce i w obcej nie daje sesji', async (s) => {
  const out = {};
  for (const [name, shared] of [['same', s.handed.ctx], ['other', undefined]]) {
    const p = await newPage(s.c, 'replay-' + name, { shared });
    if (name === 'same') await p.send('Network.getCookies');
    const log = recorder(p);
    const statuses = []; p.listeners.push((m) => { if (m.method === 'Network.responseReceived' && m.params.response.url === ctx.proto + '/_handoff') statuses.push(m.params.response.status); });
    await p.go(`${ctx.proto}/_handoff#code=${encodeURIComponent(s.code)}`, 1500);
    out[name] = { statuses, posted: log.some((r) => r.method === 'POST' && r.url === ctx.proto + '/_handoff'), user: name === 'other' ? (await proto(p)).user || null : undefined };
  }
  return { ok: ['same', 'other'].every((n) => out[n].posted && out[n].statuses.includes(400) && !out[n].statuses.includes(303)) && !out.other.user, ...out };
});

suite.add('HO-4', 'login CSRF: świeży kod atakującego w odnośniku nie loguje ofiary (brak nonce), także gdy ofiara ma własny', async (s) => {
  const attacker = await newPage(s.c, 'attacker');
  await attacker.go(ctx.page + '/_login', 600); await signInOnLoginPage(attacker, 'commenter');
  await sleep(1200);
  // przekazanie atakującego zatrzymane tuż przed wymianą: kod zostaje niezużyty
  let taken = null;
  await attacker.intercept([ctx.proto + '/_handoff'], async (ev, api) => {
    if (ev.request.method === 'POST') { taken = new URLSearchParams(ev.request.postData || '').get('code'); api.fail(); } else api.continue();
  });
  const grab = async () => { taken = null; await attacker.go(ctx.proto + '/_handoff/begin', 1500); return taken; };
  const first = await grab(); const second = await grab();
  const out = { codes: [!!first, !!second] };
  const victims = [['bez nonce', first, false], ['z własnym nonce', second, true]];
  for (const [name, code, ownNonce] of victims) {
    const p = await newPage(s.c, 'victim ' + name);
    if (ownNonce) await p.go(ctx.proto + '/_handoff/begin', 1000);   // ofiara ma ciasteczko nonce SWOJEGO rozpoczęcia
    const statuses = []; p.listeners.push((m) => { if (m.method === 'Network.responseReceived' && m.params.response.url === ctx.proto + '/_handoff' ) statuses.push([m.params.type, m.params.response.status]); });
    await p.go(`${ctx.proto}/_handoff#code=${encodeURIComponent(code || 'x')}`, 1500);
    out[name] = { statuses: statuses.map((x) => x[1]), user: (await proto(p)).user || null };
  }
  return { ok: out.codes.every(Boolean) && victims.every(([n]) => out[n].statuses.includes(400) && !out[n].user), ...out };
});

suite.add('HO-5', '/_login na originie prototypów nie pokazuje formularza hasła, tylko prowadzi na stronę', async (s) => {
  const p = await newPage(s.c, 'proto-login');
  await p.go(ctx.proto + '/_login', 1200);
  const at = await p.evaluate('({ origin: location.origin, path: location.pathname })');
  return { ok: at.origin === ctx.page, at };
});

suite.add('HO-6', 'wylogowanie na stronie kończy przekazaną sesję originu prototypów', async (s) => {
  const p = s.handed;
  await p.go(ctx.proto + '/', 800); const before = (await proto(p)).user;
  await p.go(ctx.page + '/', 800);
  const status = await p.evaluate(`fetch('/_api/v1/session').then((r) => r.json()).then((x) => fetch('/_api/v1/logout', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Skillsync-CSRF': x.csrf }, body: '{}' })).then((r) => r.status)`);
  await p.go(ctx.proto + '/', 800); const after = (await proto(p)).user;
  return { ok: !!before && status < 300 && !after, before: !!before, logout: status, after: after || null };
});

await suite.main();
