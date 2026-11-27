// K17 / K18: skrypt prototypu (kod agenta) przeciw originowi strony, w przeglądarce, w której zatwierdzający jest zalogowany.
// Skrypt atakującego to attack.js serwowany z katalogu prototypu (patrz unified_isolation_e2e.py).
// ctx: { page, proto, slug, consent: {...ciało zgody CP} }
import { Suite, connect } from '../prototype/lib.mjs';
import { ctx, J, sleep, newPage, gesture, loginPage, sessionOf, errors } from './common.mjs';

const suite = new Suite('isolation');
suite.setup(async () => {
  const c = await connect();
  const approver = await newPage(c, 'approver');
  await loginPage(approver, ctx.page, 'approver');
  // druga karta TEJ SAMEJ przeglądarki: prototyp
  const evil = await newPage(c, 'prototype', { shared: approver.ctx });
  await evil.go(`${ctx.proto}/${ctx.slug}/attack.html`, 1000);
  const loaded = await evil.waitFor(`typeof window.attack === 'object'`, 5000);
  if (!loaded.ok) throw new Error('attack.js się nie wczytał');
  return { c, approver, evil };
});
suite.teardown(async (s) => { if (s) await s.c.closeAll(); });

const attack = (p, call) => p.evaluate(`window.attack.${call}`);

suite.add('ISO-1', 'fetch z originu prototypów nie czyta sesji ani statusu strony (CORS)', async ({ evil }) => {
  const session = await attack(evil, `read(${J(ctx.page + '/_api/v1/session')})`);
  const status = await attack(evil, `read(${J(ctx.page + '/api/status')})`);
  const own = await attack(evil, `read(${J(ctx.proto + '/_api/v1/session')})`);
  const user = own.read ? JSON.parse(own.body).user : 'nieodczytane';
  return { ok: !session.read && !status.read && own.read && !user, sessionRead: session.error, boardRead: status.error, ownOriginReadable: own.read, ownUser: user || null };
});

suite.add('ISO-2', 'zgoda, przekazanie i wylogowanie wysłane z prototypu (no-cors, formularz, preflight) nic nie robią', async ({ evil, approver }) => {
  const sent = {};
  sent.noCors = await attack(evil, `noCors(${J(ctx.page + '/api/approve')}, ${J(ctx.consent)})`);
  sent.preflight = await attack(evil, `json(${J(ctx.page + '/api/approve')}, ${J(ctx.consent)})`);
  sent.form = await attack(evil, `form(${J(ctx.page + '/api/approve')}, ${J(ctx.consent)})`);
  sent.handoff = await attack(evil, `noCors(${J(ctx.page + '/_api/v1/handoff')}, ${J({ challenge: 'a'.repeat(64) })})`);
  sent.logout = await attack(evil, `noCors(${J(ctx.page + '/_api/v1/logout')}, {})`);
  await sleep(800);
  const still = await sessionOf(approver);
  const wire = evil.net.filter((l) => l.startsWith('POST ' + ctx.page));
  // dowód, że żądania naprawdę wyszły z przeglądarki do strony; skutek (approvals.jsonl, audyt) sprawdza strona pythonowa
  return { ok: wire.length >= 4 && !sent.preflight.read && !!still.user, sent, postsToPage: wire.length, approverStillSignedIn: !!still.user };
});

suite.add('ISO-3', 'prototyp nie osadza / ani /_login strony w ramce; własną ramkę osadza (kontrola)', async ({ evil }) => {
  const since = errors.length;
  for (const url of [ctx.page + '/', ctx.page + '/_login', `${ctx.proto}/${ctx.slug}/index.html`]) await attack(evil, `frame(${J(url)})`);
  await sleep(1500);
  const tree = await evil.send('Page.getFrameTree');
  const frames = (tree.frameTree.childFrames || []).map((f) => f.frame.url);
  // przeglądarka sama mówi, czemu ramki strony nie ma: wpis bezpieczeństwa na każdą z dwóch prób
  const refused = errors.slice(since).filter((e) => e.page === 'prototype' && e.text.includes(`Framing '${ctx.page}/'`) && e.text.includes("frame-ancestors 'none'")).length;
  const own = `${ctx.proto}/${ctx.slug}/index.html`;
  return { ok: refused >= 2 && frames.includes(own) && !frames.some((u) => u.startsWith(ctx.page)), refused, frames };
});

suite.add('ISO-4', 'okno /_login otwarte przez prototyp: brak dostępu do dokumentu, hasła i adresu; postMessage bez skutku', async ({ evil, approver }) => {
  const before = approver.net.length;
  const opened = await gesture(evil, `window.attack.open(${J(ctx.page + '/_login')})`);
  await sleep(1200);
  const probe = await attack(evil, 'probe()');
  const toPage = await gesture(evil, `window.attack.open(${J(ctx.page + '/')})`);
  await sleep(1500);
  await attack(evil, `post(${J([{ type: 'approve', ...ctx.consent }, { action: 'approve', kind: 'cp' }, 'approve'])})`);
  await sleep(1000);
  const stillOpen = await attack(evil, 'probe()');
  await attack(evil, 'close()');
  return { ok: opened && toPage && Object.values(probe).every((v) => v === 'SecurityError') && Object.values(stillOpen).every((v) => v === 'SecurityError') && approver.net.length === before, opened, probe, afterMessages: stillOpen };
});

suite.add('ISO-6', 'prototyp nie podstawi własnego formularza pod adresem logowania strony', async ({ evil }) => {
  const taken = await attack(evil, `takeOver(${J(ctx.page + '/_login')})`);
  const where = await evil.evaluate('location.origin');
  return { ok: !taken.taken && where === ctx.proto, taken, origin: where };
});

suite.add('ISO-5', 'kontrola pozytywna: ta sama zgoda wysłana przez kartę strony (sesja, CSRF, Origin) zostaje zapisana', async ({ approver }) => {
  const reply = await approver.evaluate(`fetch('/_api/v1/session').then((r) => r.json()).then((s) => fetch('/api/approve', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Status-Board': '1', 'X-Skillsync-CSRF': s.csrf }, body: ${J(J(ctx.consent))} })).then(async (r) => ({ status: r.status, body: (await r.text()).slice(0, 200) }))`);
  return { ok: reply.status === 200, reply };
});

await suite.main();
