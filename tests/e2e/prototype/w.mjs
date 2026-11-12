// Znacznik zapisu „agent jeszcze pisze ekrany”: W-01 … W-03.
// W-01 tor (a): marker bez zmiany plików, W-02 tor (b): pliki zmieniły się w trakcie zapisu (prototyp `beta` we własnym workspace skryptu),
// W-03: serwer odrzuca akceptację przez HTTP (409 writing_in_progress) i przyjmuje ją po `writing done`.
// Marker odnawiany z `--ttl 60`, żeby nie wygasł w trakcie scenariusza; czekania tylko na odpytywanie silnika (karta widoczna: ok. 5 s).
import {
  Suite, sleep, workspace, startServer, connect, PORT1, proto, apiOps, apiPost, session, sessionPage, cid, createThread,
  btnExact, DLG, dlgOpen,
} from './lib.mjs';

const suite = new Suite('w');
let ws; let srv; let c; let ACC; let ANN;
const url = (slug) => `${srv.base}/${slug}/`;
const acceptBtn = (sid) => `document.querySelector('#${sid} .anno-accept-open')`;
const chip = `document.getElementById('anno-accept-chip')`;
const disabled = (finder) => `(() => { const b = ${finder}; return !!b && b.getAttribute('aria-disabled') === 'true'; })()`;
const enabled = (finder) => `(() => { const b = ${finder}; return !!b && b.getAttribute('aria-disabled') === 'false'; })()`;
const banner = (key) => `(() => { const n = document.querySelector('[data-banner="${key}"]'); return n ? n.textContent.replace(/\\s+/g, ' ').trim() : null; })()`;
const marker = (action, slug) => proto(ws, 'writing', slug, action, ...(action === 'start' ? ['--ttl', '60', '--by', 'e2e-agent'] : []));
const acceptOps = async (slug) => (await apiOps(srv.base, slug)).operations.filter((o) => o.type === 'accept').length;
const validAccepted = async (slug, sid) => !!(((await apiOps(srv.base, slug)).acceptance || {})[sid] || {}).valid;
const reloadsOf = (p) => p.evaluate(`({ mark: window.__wMark || null, origin: performance.timeOrigin, navs: performance.getEntriesByType('navigation').length })`);

// próby uruchomienia wyłączonego przycisku: klik przez DOM, klik wskaźnikiem, Enter i Spacja z klawiatury
async function tryActivate(p, finder) {
  const tried = { dom: false, pointer: false, enter: false, space: false };
  await p.evaluate(`(() => { const b = ${finder}; if (b) b.click(); return 1; })()`); tried.dom = true; await sleep(300);
  try { await p.clickEl(finder); tried.pointer = true; } catch (e) { tried.pointer = 'niedostępny: ' + String(e.message).slice(0, 80); }
  await p.evaluate(`(() => { const b = ${finder}; if (b) b.focus(); return 1; })()`);
  await p.key('Enter'); tried.enter = true;
  await p.key(' '); tried.space = true;
  await sleep(400);
  return { tried, dialogOpened: await p.evaluate(dlgOpen) };
}

async function acceptViaUi(p, sid) {
  await p.domClick(acceptBtn(sid)); const open = await p.waitFor(dlgOpen, 5000);
  if (!open.ok) return { opened: false };
  await p.domClick(btnExact('Akceptuję', DLG));
  return { opened: true };
}

suite.setup(async () => {
  ws = workspace('w');
  srv = await startServer(ws, { port: PORT1 });
  c = await connect();
  ACC = await session(srv.base, 'celina'); ANN = await session(srv.base, 'anna');
  return {};
});
suite.teardown(async () => {
  for (const slug of ['demo', 'beta', 'alfa']) { try { await marker('done', slug); } catch (e) { /* sprzątanie markera */ } }
  if (c) await c.closeAll();
  if (srv) await srv.stop();
});

suite.add('W-01', 'Marker bez zmiany plików: baner, wyłączony przycisk, powrót bez przeładowania', async () => {
  const slug = 'demo';
  const { p } = await sessionPage(c, 'w01', srv.base, 'celina'); await p.go(url(slug));
  const ready = await p.waitFor(enabled(acceptBtn('s1')), 15000);
  await p.evaluate(`window.__wMark = 'w01-' + Date.now(); true`);
  const before = await reloadsOf(p);
  const start = await marker('start', slug);
  const shown = await p.waitFor(`${banner('writing')} !== null && ${disabled(acceptBtn('s1'))} && ${disabled(chip)}`, 20000, 250);
  const bannerText = await p.evaluate(banner('writing'));
  const noStale = (await p.evaluate(banner('stale'))) === null;
  const attempt = await tryActivate(p, acceptBtn('s1'));
  const chipAttempt = await tryActivate(p, chip);
  await marker('start', slug);   // odnowienie (ttl 60)
  const during = { acceptOps: await acceptOps(slug), accepted: await validAccepted(slug, 's1') };
  const done = await marker('done', slug);
  const back = await p.waitFor(`${banner('writing')} === null && ${enabled(acceptBtn('s1'))} && ${enabled(chip)}`, 25000, 250);
  const after = await reloadsOf(p);
  const sameDocument = before.mark === after.mark && before.origin === after.origin && after.mark !== null;
  const ui = await acceptViaUi(p, 's1');
  const accepted = await (async () => { for (let i = 0; i < 40; i++) { if (await validAccepted(slug, 's1')) return true; await sleep(250); } return false; })();
  await p.dispose();
  const restOk = ready.ok && start.code === 0 && shown.ok && /Agent jeszcze pisze ekrany/.test(bannerText || '') && noStale
    && !attempt.dialogOpened && !chipAttempt.dialogOpened && during.acceptOps === 0 && !during.accepted
    && done.code === 0 && back.ok && sameDocument && ui.opened && accepted;
  return { ok: restOk, ready: ready.ok, startExit: start.code, bannerShown: shown.ok, bannerText: (bannerText || '').slice(0, 80), noStaleBanner: noStale,
    buttonAttempt: attempt, chipAttempt, acceptDuringMarker: during, doneExit: done.code, backMs: back.ms, sameDocument, acceptedAfterDone: accepted };
}, { timeout: 120000 });

suite.add('W-02', 'Pliki zmieniły się w trakcie zapisu: „przeładuj”, po przeładowaniu i done akceptacja działa', async () => {
  const slug = 'beta';
  const { p } = await sessionPage(c, 'w02', srv.base, 'celina'); await p.go(url(slug));
  const ready = await p.waitFor(enabled(acceptBtn('s1')), 15000);
  const start = await marker('start', slug);
  const writing = await p.waitFor(`${banner('writing')} !== null && ${disabled(acceptBtn('s1'))}`, 20000, 250);
  const orig = ws.read(slug);
  ws.write(slug, 'index.html', orig.replace('Treść s2.', 'Treść s2 dopisana przez agenta.'));
  await marker('start', slug);   // odnowienie (ttl 60)
  const stale = await p.waitFor(`/Prototyp zmieniono, przeładuj/.test(${banner('stale')} || '') && ${banner('writing')} === null && ${disabled(acceptBtn('s1'))}`, 25000, 250);
  const staleText = await p.evaluate(banner('stale'));
  const attempt = await tryActivate(p, acceptBtn('s1'));
  const during = { acceptOps: await acceptOps(slug), accepted: await validAccepted(slug, 's1') };
  await marker('start', slug);   // odnowienie przed przeładowaniem (wolny runner)
  await p.reload();
  const again = await p.waitFor(`${banner('writing')} !== null && ${banner('stale')} === null && ${disabled(acceptBtn('s1'))}`, 20000, 250);
  const done = await marker('done', slug);
  const back = await p.waitFor(`${banner('writing')} === null && ${banner('stale')} === null && ${enabled(acceptBtn('s1'))}`, 25000, 250);
  const ui = await acceptViaUi(p, 's1');
  const accepted = await (async () => { for (let i = 0; i < 40; i++) { if (await validAccepted(slug, 's1')) return true; await sleep(250); } return false; })();
  await p.dispose();
  const restOk = ready.ok && start.code === 0 && writing.ok && stale.ok && !attempt.dialogOpened && during.acceptOps === 0 && !during.accepted
    && again.ok && done.code === 0 && back.ok && ui.opened && accepted;
  return { ok: restOk, ready: ready.ok, writingShown: writing.ok, staleShown: stale.ok, staleText: (staleText || '').slice(0, 80), buttonAttempt: attempt,
    acceptDuringStale: during, writingAfterReload: again.ok, doneExit: done.code, backMs: back.ms, acceptedAfterReload: accepted };
}, { timeout: 150000 });

suite.add('W-03', 'Serwer: 409 writing_in_progress na accept przy markerze, po done 201', async () => {
  const slug = 'alfa';
  const start = await marker('start', slug);
  const ops = await apiOps(srv.base, slug);
  const body = (cur) => ({ cid: cid('a'), type: 'accept', payload: { scope: 'screen', screens: { s1: cur.screens.s1 }, version: cur.version } });
  const refused = await apiPost(ACC, slug, body(ops));
  const comment = await createThread(ANN, slug, 's1', 'alfa-s1-tytul', 'W-03 komentarz przy markerze');   // marker blokuje tylko akceptację
  const listed = ops.writing || null;
  const noBy = !!listed && !('by' in listed) && Number.isFinite(listed.since) && Number.isFinite(listed.expiresAt);
  const accepted0 = await validAccepted(slug, 's1');
  const done = await marker('done', slug);
  const cur = await apiOps(srv.base, slug);
  const ok2 = await apiPost(ACC, slug, body(cur));
  const restOk = start.code === 0 && refused.status === 409 && refused.body && refused.body.error === 'writing_in_progress' && comment.status === 201
    && noBy && !accepted0 && done.code === 0 && cur.writing == null && ok2.status === 201 && await validAccepted(slug, 's1');
  return { ok: restOk, startExit: start.code, refusedStatus: refused.status, refusedError: refused.body && refused.body.error, commentStatus: comment.status,
    writingField: listed && Object.keys(listed).sort(), noBy, acceptedDuringMarker: accepted0, doneExit: done.code, writingAfterDone: cur.writing ?? null, acceptAfterDone: ok2.status };
});

await suite.main();
