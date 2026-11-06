// F3: akceptacja ekranów i bramka (E3-01 … E3-21, E3-99). Prototypy alfa (s1, s2, s3 przykładowy) i beta (s1, s2), delta (v1).
import { rmSync, mkdirSync, readFileSync as rf } from 'node:fs';
import {
  Suite, sleep, workspace, startServer, connect, errors, env, PORT1, session, apiOps, apiPost, apiRaw, secret, mail, cid, proto, createThread, setAcceptance,
  btn, btnExact, byProto, DLG, DLG_LIST, dlgText, dlgOpen, inDlg, isBody, saveState, bannerText, wasSaved, openPanel, closePanelIfOpen, loginUI,
  openThreadByPin, replyIn, queueLength, windowCycle, consoleVerdict, sessionPage, acceptCli, skillsync, writeFileSync, existsSync,
} from './lib.mjs';

const suite = new Suite('f3');
let ws; let srv; let c; let ACC; let DEV; let ANN;
const url = (slug = 'alfa', hash = '') => `${srv.base}/${slug}/${hash}`;
const opener = (sid) => `document.querySelector('#${sid} .anno-accept-open')`;
const chipInfo = `(() => { const c = document.querySelector('.anno-accept'); const n = document.querySelector('.anno-accept-count'); return c ? { tag: c.tagName, role: c.getAttribute('role'), disabled: c.getAttribute('aria-disabled'), count: n ? n.textContent.trim() : null, label: c.getAttribute('aria-label') } : null; })()`;
const openerInfo = (sid) => `(() => { const o = ${opener(sid)}; if (!o) return null; const d = o.getAttribute('aria-describedby'); return { disabled: o.getAttribute('aria-disabled'), reason: d ? ((document.getElementById(d) || {}).textContent || null) : (o.title || null) }; })()`;
const parseCount = (s) => { const m = /(\d+)\/(\d+)/.exec(s || ''); return m ? { k: +m[1], n: +m[2] } : null; };
const resetAcc = async (slug, sids) => { for (const sid of sids) await setAcceptance(ACC, DEV, srv.base, slug, sid, false); };
const acceptor = async (name, who = 'celina', slug = 'alfa', hash = '#s2') => { const { p } = await sessionPage(c, name, srv.base, who); await p.go(url(slug, hash)); return p; };
const luminance = (css) => { const m = /oklch\(\s*([\d.]+)/.exec(css || ''); if (m) return Number(m[1]); const r = /rgba?\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+)/.exec(css || ''); if (r) return (0.2126 * r[1] + 0.7152 * r[2] + 0.0722 * r[3]) / 255;
  const k = /color\(\s*srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/.exec(css || ''); return k ? 0.2126 * k[1] + 0.7152 * k[2] + 0.0722 * k[3] : null; };
// tło elementu: pierwsze nieprzezroczyste w górę od elementu (przezroczyste nie liczy się jako ciemne); null, gdy żadnego
const OPAQUE_BG = (el) => `(() => { for (let e = ${el}; e; e = e.parentElement) { const v = getComputedStyle(e).backgroundColor; const a = /\\/\\s*([\\d.]+)(%?)\\s*\\)\\s*$/.exec(v) || /rgba\\(.*,\\s*([\\d.]+)\\s*\\)\\s*$/.exec(v); const alpha = v === 'transparent' ? 0 : a ? (a[2] ? Number(a[1]) / 100 : Number(a[1])) : 1; if (alpha > 0.99) return v; } return null; })()`;

suite.setup(async () => {
  ws = workspace('f3');
  for (const slug of ['demo', 'plan', 'migr', 'shell', 'goto']) rmSync(`${ws.proj}/docs/prototypes/${slug}`, { recursive: true, force: true });
  srv = await startServer(ws, { port: PORT1 });
  c = await connect();
  ACC = await session(srv.base, 'celina'); DEV = await session(srv.base, 'dawid'); ANN = await session(srv.base, 'anna');
  return {};
});
suite.teardown(async () => { if (c) await c.closeAll(); if (srv) await srv.stop(); });

suite.add('E3-01', 'Chip i przycisk dla akceptującego', async () => {
  const p = await acceptor('celina', 'celina', 'alfa', '');
  const chip = await p.evaluate(chipInfo); const o1 = await p.evaluate(`!!${opener('s1')}`); const o2 = await p.evaluate(`!!${opener('s2')}`);
  const text = await p.evaluate(`${opener('s1')} && ${opener('s1')}.textContent.trim()`);
  await p.dispose();
  return { ok: !!chip && chip.count === '0/2' && o1 && o2 && /Akceptuj/.test(text || ''), chip, openerS1: o1, openerS2: o2, openerText: text };
});

suite.add('E3-02', 'Komentujący bez akceptacji', async () => {
  const { p } = await sessionPage(c, 'anna', srv.base, 'anna'); await p.go(url('alfa'));
  const chip = await p.evaluate(chipInfo); const any = await p.evaluate(`document.querySelectorAll('.anno-accept-open').length`);
  await p.dispose();
  return { ok: !!chip && chip.tag === 'SPAN' && chip.role === 'note' && any === 0, chip, openers: any };
});

suite.add('E3-03', 'file:// bez akceptacji', async () => {
  const p = await c.page('file'); await p.go('file://' + ws.file('alfa'));
  const chip = await p.evaluate(chipInfo); const any = await p.evaluate(`document.querySelectorAll('.anno-accept-open').length`);
  const screens = await p.evaluate(`document.querySelectorAll('section.screen').length`);   // strona ma treść (nie pusta)
  await p.dispose();
  return { ok: !chip && any === 0 && screens > 0, chip, openers: any, screens };
});

suite.add('E3-04', 'Cel okna i ekran przykładowy', async () => {
  const p = await acceptor('celina', 'celina', 'alfa', '');
  const s3Open = await p.evaluate(`!!${opener('s3')}`);
  await p.hash('#s3'); await sleep(700);
  await p.domClick(`document.querySelector('.anno-accept')`); await p.waitFor(dlgOpen, 4000);
  const t3 = await p.evaluate(dlgText); await p.key('Escape');
  await p.hash('#s2'); await sleep(700);
  await p.domClick(`document.querySelector('.anno-accept')`); await p.waitFor(dlgOpen, 4000);
  const t2 = await p.evaluate(dlgText); await p.key('Escape');
  await p.hash('#s3'); await sleep(700);
  await p.domClick(opener('s2')); await p.waitFor(dlgOpen, 4000);
  const tBtn = await p.evaluate(dlgText); await p.key('Escape'); await sleep(300);
  const closed = !(await p.evaluate(dlgOpen));
  await p.dispose();
  return { ok: !s3Open && /Ten ekran \(s1/.test(t3 || '') && /Ten ekran \(s2/.test(t2 || '') && /Ten ekran \(s2/.test(tBtn || '') && closed, s3HasOpener: s3Open, chipOnS3: (t3 || '').slice(0, 60), chipOnS2: (t2 || '').slice(0, 60), buttonS2: (tBtn || '').slice(0, 60), escClosed: closed };
});

suite.add('E3-05', 'Akceptacja ekranu', async () => {
  await resetAcc('alfa', ['s1', 's2']);
  const p = await acceptor('celina', 'celina', 'alfa', '');
  await p.domClick(opener('s1')); await p.waitFor(dlgOpen, 4000); await p.domClick(btnExact('Akceptuję', DLG));
  const t0 = Date.now(); const a = await p.waitFor(`/^1\\/2$/.test(((document.querySelector('.anno-accept-count') || {}).textContent || '').trim())`, 4000, 50); const ms1 = Date.now() - t0;
  await p.waitFor(`!${DLG}`, 4000);
  await p.domClick(opener('s2')); await p.waitFor(dlgOpen, 4000); await p.domClick(btnExact('Akceptuję', DLG));
  const b = await p.waitFor(`/^2\\/2$/.test(((document.querySelector('.anno-accept-count') || {}).textContent || '').trim())`, 4000, 50);
  await p.dispose();
  return { ok: a.ok && b.ok && ms1 < 1500, msToFirst: ms1, second: b.ok };
});

suite.add('E3-06', 'Cofnięcie własnej akceptacji', async () => {
  const p = await acceptor('celina', 'celina', 'alfa', '');
  await p.domClick(`document.querySelector('.anno-accept')`); await p.waitFor(dlgOpen, 4000);
  const t = await p.evaluate(dlgText);
  await p.domClick(btnExact('Cofnij akceptację', DLG));
  const r = await p.waitFor(`/^1\\/2$/.test(((document.querySelector('.anno-accept-count') || {}).textContent || '').trim())`, 5000);
  await p.dispose();
  return { ok: /Zaakceptowano: [^,]+, [^,]+, iteracja \d+ \(UI\)/.test(t || '') && r.ok, dialog: (t || '').slice(0, 160) };
});

suite.add('E3-07', 'Zmiana pliku po wczytaniu', async () => {
  await resetAcc('alfa', ['s1', 's2']);
  const p = await acceptor('celina', 'celina', 'alfa', '');
  const before = await apiOps(srv.base, 'alfa');
  const orig = ws.read('alfa');
  ws.write('alfa', 'index.html', orig.replace('Treść s2.', 'Treść s2 zmieniona.'));
  const bar = await p.waitFor(`/Prototyp zmieniono, przeładuj\\./.test(document.body.innerText)`, 45000);
  const info = await p.evaluate(openerInfo('s2'));
  const stale = await apiRaw(ACC, 'POST', '/_api/v1/prototypes/alfa/operations', { cid: cid('a'), type: 'accept', payload: { scope: 'screen', screens: { s2: before.screens.s2 }, version: before.version } });
  const staleNoVersion = await apiRaw(ACC, 'POST', '/_api/v1/prototypes/alfa/operations', { cid: cid('a'), type: 'accept', payload: { scope: 'screen', screens: { s2: before.screens.s2 } } });
  ws.write('alfa', 'index.html', orig);
  await p.dispose();
  return { ok: bar.ok && !!info && info.disabled === 'true' && /Prototyp zmieniono od wczytania strony: przeładuj/.test(info.reason || '') && stale.status === 409 && stale.json && stale.json.error === 'stale_view' && staleNoVersion.status >= 400 && staleNoVersion.status < 500, bar: bar.ok, opener: info, staleWithVersion: stale.status, staleWithoutVersion: staleNoVersion.status };
});

suite.add('E3-08', 'Akceptacja offline', async () => {
  await resetAcc('alfa', ['s1', 's2']); await sleep(1500);
  const p = await acceptor('celina', 'celina', 'alfa', '');
  await p.waitFor(`${chipInfo}`, 4000);
  const posts = []; await p.intercept(['*/_api/v1/prototypes/alfa/operations*'], async (ev, api) => { if (ev.request.method === 'POST') posts.push(ev.request.url); api.continue(); });
  await p.domClick(opener('s1')); await p.waitFor(dlgOpen, 4000);
  await p.offline(true);
  const reason = 'Akceptacja wymaga połączenia z serwerem i nie trafia do kolejki.';
  const blocked = await p.waitFor(`(() => { const b = ${btnExact('Akceptuję', DLG)}; return !!b && b.getAttribute('aria-disabled') === 'true'; })()`, 8000);
  const reasonDlg = await p.evaluate(`(() => { const b = ${btnExact('Akceptuję', DLG)}; const d = b && b.getAttribute('aria-describedby'); return d ? (document.getElementById(d) || {}).textContent : (b && b.title); })()`);
  await p.domClick(btnExact('Akceptuję', DLG)).catch(() => {}); await sleep(500);
  await p.key('Escape'); await sleep(300);
  const chip = await p.evaluate(chipInfo); const op = await p.evaluate(openerInfo('s1'));
  await p.offline(false);
  const back = await p.waitFor(`(() => { const o = ${opener('s1')}; return !!o && o.getAttribute('aria-disabled') !== 'true'; })()`, 20000);
  await p.domClick(opener('s1')); await p.waitFor(dlgOpen, 4000);
  const reopened = await p.evaluate(`(() => { const b = ${btnExact('Akceptuję', DLG)}; return !!b && b.getAttribute('aria-disabled') !== 'true'; })()`);
  const postsBefore = posts.length;
  await p.domClick(btnExact('Akceptuję', DLG));
  const acc = await p.waitFor(`/^1\\/2$/.test(((document.querySelector('.anno-accept-count') || {}).textContent || '').trim())`, 8000);
  await p.interceptOff(); await p.dispose();
  return { ok: blocked.ok && (reasonDlg || '').includes('nie trafia do kolejki') && postsBefore === 0 && back.ok && reopened && acc.ok && !!chip && !!op && op.disabled === 'true', dialogReason: reasonDlg, postsWhileOffline: postsBefore, chipWhileOffline: chip, openerWhileOffline: op, reopenedActive: reopened };
});

suite.add('E3-09', 'Sesja wygasła i mustChange', async () => {
  await resetAcc('alfa', ['s1', 's2']);
  // (a) wygaśnięcie sesji: ciasteczko usunięte po wczytaniu strony
  const { p } = await sessionPage(c, 'celina-exp', srv.base, 'celina'); await p.go(url('alfa'));
  await p.send('Network.deleteCookies', { name: ACC.cookie.slice(0, ACC.cookie.indexOf('=')), url: srv.base });
  await p.domClick(opener('s1')); await p.waitFor(dlgOpen, 4000);
  const beforeBlock = await p.evaluate(`(() => { const b = ${btnExact('Akceptuję', DLG)}; return b ? b.getAttribute('aria-disabled') : null; })()`);
  await p.domClick(btnExact('Akceptuję', DLG)).catch(() => {});
  const login = await p.waitFor(`/Sesja wygasła/.test(${dlgText}) && !!document.querySelector('#anno-login-password')`, 8000);
  const loginText = login.ok ? (await p.evaluate(dlgText)).slice(0, 120) : null;
  await p.dispose();
  // (b) konto z mustChange
  const m = await session(srv.base, 'marta', secret('default'));
  const q = await c.page('marta', { session: m, url: srv.base }); await q.go(url('alfa'));
  const form = await q.waitFor(`/Ustaw własne hasło/.test(${dlgText})`, 8000);
  const reason = await q.evaluate(openerInfo('s1'));
  const before = await apiOps(srv.base, 'alfa');
  const api = await apiRaw(m, 'POST', '/_api/v1/prototypes/alfa/operations', { cid: cid('a'), type: 'accept', payload: { scope: 'screen', screens: { s1: before.screens.s1 }, version: before.version } });
  await q.dispose();
  const cliBlocked = await skillsync(ws, ['accept', ws.proj, 'alfa', '--all', '--by', mail('marta'), '--json']);
  return { ok: beforeBlock !== 'true' && login.ok && form.ok && !!reason && reason.disabled === 'true' && /Najpierw ustaw własne hasło/.test(reason.reason || '') && api.status === 403 && api.json && api.json.error === 'password_change_required' && cliBlocked.code === 2, enabledBeforeSave: beforeBlock !== 'true', loginDialog: loginText, mustChangeForm: form.ok, opener: reason, apiStatus: api.status, apiError: api.json && api.json.error, cliExit: cliBlocked.code };
});

suite.add('E3-10', 'Zakres Wszystkie prototypy', async () => {
  await resetAcc('alfa', ['s1', 's2']); await resetAcc('beta', ['s1', 's2']);
  const p = await acceptor('celina', 'celina', 'alfa', '');
  p.net.length = 0;
  await p.domClick(opener('s2')); await p.waitFor(dlgOpen, 4000);
  const beforeChoice = p.net.filter((u) => /\/_api\/v1\/project(\?|$)/.test(u)).length;
  await p.domClick(`document.getElementById('anno-accept-project')`);
  const listed = await p.waitFor(`/beta/.test(${dlgText}) && /pominięto 1/.test(${dlgText})`, 10000);
  const text = await p.evaluate(dlgText);
  const afterChoice = p.net.filter((u) => /\/_api\/v1\/project(\?|$)/.test(u)).length;
  await p.domClick(btnExact('Akceptuję', DLG));
  const toast = await p.waitFor(`/Zaakceptowano 2 prototypy; pominięto 1\\./.test(document.body.innerText)`, 10000);
  const posts = p.net.filter((u) => /POST .*\/project\/accept/.test(u)).length;
  await p.dispose();
  return { ok: beforeChoice === 0 && afterChoice >= 1 && listed.ok && /delta/.test(text) && /wymaga migracji/.test(text) && toast.ok && posts === 1, projectGetBeforeChoice: beforeChoice, projectGetAfterChoice: afterChoice, dialog: text.slice(0, 260), toast: toast.ok, projectPosts: posts };
});

suite.add('E3-11', 'Nieaktualny prototyp w akceptacji projektu', async () => {
  const proj = await apiRaw(ACC, 'GET', '/_api/v1/project');
  const list = proj.json.prototypes; const v = Object.fromEntries(list.filter((x) => x.format === 2).map((x) => [x.slug, x.version]));
  const orig = ws.read('beta');
  ws.write('beta', 'index.html', orig.replace('Treść s2.', 'Treść s2 zmieniona.'));
  await sleep(500);
  const r = await apiRaw(ACC, 'POST', '/_api/v1/project/accept', { cid: cid('p'), versions: v });
  ws.write('beta', 'index.html', orig);
  return { ok: r.status === 409 && r.json && r.json.error === 'stale_view' && Array.isArray(r.json.stale) && r.json.stale.includes('beta'), httpStatus: r.status, body: r.json };
}, { critical: false });

suite.add('E3-12', 'Zapis częściowy i Ponów', async () => {
  await resetAcc('alfa', ['s1', 's2']); await resetAcc('beta', ['s1', 's2']);
  const p = await acceptor('celina', 'celina', 'alfa', '#s2');
  const bodies = []; let first = true;
  await p.intercept(['*/_api/v1/project/accept*'], async (ev, api) => {
    if (ev.request.method !== 'POST') return api.continue();
    bodies.push(ev.request.postData || '');
    if (first) { first = false; return api.fulfill(409, { error: 'partial', accepted: ['alfa'], pending: ['beta'] }); }
    return api.continue();
  });
  await p.domClick(opener('s2')); await p.waitFor(dlgOpen, 4000);
  await p.domClick(`document.getElementById('anno-accept-project')`);
  await p.waitFor(`/beta/.test(${dlgText})`, 8000);
  await p.domClick(btnExact('Akceptuję', DLG));
  const retry = await p.waitFor(`!!${btnExact('Ponów', DLG)}`, 8000);
  const frame = await p.evaluate(`(() => { const el = document.querySelector('dialog[open] [role=alert]'); return el ? el.textContent.replace(/\\s+/g, ' ').trim().slice(0, 200) : null; })()`);
  await p.domClick(btnExact('Ponów', DLG)); await sleep(3000);
  const cids = bodies.map((b) => { try { return JSON.parse(b).cid; } catch (e) { return null; } });
  const bOps = await apiOps(srv.base, 'beta');
  const valid = Object.keys(bOps.acceptance || {}).filter((k) => bOps.acceptance[k].valid === true);
  await p.interceptOff(); await p.dispose();
  return { ok: retry.ok && /Zapisano część akceptacji/.test(frame || '') && /alfa/.test(frame || '') && /beta/.test(frame || '') && cids.length === 2 && cids[0] === cids[1] && valid.length === 2, frame, sameCid: cids.length === 2 && cids[0] === cids[1], betaAcceptedAfterRetry: valid.length };
});

suite.add('E3-13', 'Odmowa serwera w oknie', async () => {
  await resetAcc('alfa', ['s2']);
  const p = await acceptor('celina', 'celina', 'alfa', '#s2');
  const queue = [{ code: 403, body: { error: 'forbidden', message: 'forbidden' } }, { code: 400, body: { error: 'unknown_screen', message: 'unknown screen' } }];
  await p.intercept(['*/_api/v1/prototypes/alfa/operations*'], async (ev, api) => { if (ev.request.method === 'POST' && queue.length) { const s = queue.shift(); return api.fulfill(s.code, s.body); } return api.continue(); });
  await p.domClick(opener('s2')); await p.waitFor(dlgOpen, 4000);
  const alertSel = `(() => { const el = document.querySelector('dialog[open] [role=alert], dialog[open] [role=status]'); return el ? el.textContent.replace(/\\s+/g, ' ').trim().slice(0, 200) : null; })()`;
  await p.domClick(btnExact('Akceptuję', DLG)); await sleep(1500);
  const d403 = { open: await p.evaluate(dlgOpen), alert: await p.evaluate(alertSel) };
  await p.domClick(btnExact('Akceptuję', DLG)); await sleep(1500);
  const d400 = { open: await p.evaluate(dlgOpen), alert: await p.evaluate(alertSel) };
  await p.interceptOff(); await p.key('Escape'); await p.dispose();
  return { ok: d403.open && /Serwer odmówił akceptacji: brak uprawnień\./.test(d403.alert || '') && d400.open && /ekranu nie ma w bieżącej wersji albo jest przykładowy\./.test(d400.alert || ''), after403: d403, after400: d400 };
});

suite.add('E3-14', 'Klawiatura i fokus we wszystkich oknach', async () => {
  await resetAcc('alfa', ['s1', 's2']);
  const results = {};
  const open = async (p, openerExpr) => { await p.evaluate(`(${openerExpr}).focus(); true`); await p.key('Enter'); return p.waitFor(dlgOpen, 5000); };
  // akceptujący: przycisk s1, s2 oraz chip
  const pa = await acceptor('celina', 'celina', 'alfa', '');
  for (const [name, expr] of [['akceptacja-s1', opener('s1')], ['akceptacja-s2', opener('s2')]]) {
    const o = await open(pa, expr); results[name] = o.ok ? await windowCycle(pa, { opener: expr, inject: name === 'akceptacja-s1' }) : { ok: false, why: 'okno się nie otworzyło' };
  }
  await setAcceptance(ACC, DEV, srv.base, 'alfa', 's1', true); await pa.reload();
  const chipExpr = `document.querySelector('.anno-accept')`;
  { const o = await open(pa, chipExpr); results['akceptacja-chip'] = o.ok ? await windowCycle(pa, { opener: chipExpr }) : { ok: false, why: 'okno się nie otworzyło' }; }
  await pa.dispose();
  // programista: Przywróć…, Porównaj, Usunąć wątek?
  const { p: pd } = await sessionPage(c, 'dawid', srv.base, 'dawid');
  await proto(ws, 'iterate', 'alfa');
  await createThread(ANN, 'alfa', 's2', 'alfa-s2-tytul', 'Wątek do okien');
  await pd.go(url('alfa'));
  const restoreExpr = `[...document.querySelectorAll('section#s2 button')].find((b) => b.textContent.includes('Przywróć'))`;
  { const o = await open(pd, restoreExpr); results['przywroc'] = o.ok ? await windowCycle(pd, { opener: restoreExpr }) : { ok: false, why: 'okno się nie otworzyło' }; }
  const cmpExpr = `[...document.querySelectorAll('button')].find((b) => b.textContent.includes('Porównaj z iteracją') && b.getClientRects().length > 0)`;
  { const o = await open(pd, cmpExpr); results['porownaj'] = o.ok ? await windowCycle(pd, { opener: cmpExpr }) : { ok: false, why: 'okno się nie otworzyło' }; }
  await openThreadByPin(pd, 1);
  const delExpr = btn('Usuń wątek', `document.querySelector('.anno-panel')`);
  { const o = await open(pd, delExpr); results['usun-watek'] = o.ok ? await windowCycle(pd, { opener: delExpr }) : { ok: false, why: 'okno się nie otworzyło' }; }
  await pd.dispose();
  // anonim: logowanie
  const pn = await c.page('anon'); await pn.go(url('alfa'));
  const loginExpr = btn('Zaloguj', `document.querySelector('.anno-user-slot')`);
  { const o = await open(pn, loginExpr); results['logowanie'] = o.ok ? await windowCycle(pn, { opener: loginExpr }) : { ok: false, why: 'okno się nie otworzyło' }; }
  await pn.dispose();
  // autor: Wycofać wpis?
  const { p: pw } = await sessionPage(c, 'anna', srv.base, 'anna'); await pw.go(url('alfa'));
  await openThreadByPin(pw, 1);
  const retractExpr = `[...document.querySelectorAll('.anno-panel .anno-msg')][0] && [...[...document.querySelectorAll('.anno-panel .anno-msg')][0].querySelectorAll('button')].find((b) => b.textContent.trim() === 'Wycofaj')`;
  { const o = await open(pw, retractExpr); results['wycofaj'] = o.ok ? await windowCycle(pw, { opener: retractExpr }) : { ok: false, why: 'okno się nie otworzyło' }; }
  // odrzucone wpisy: wpis do usuniętego wątku offline
  await closePanelIfOpen(pw);
  const th = await createThread(ANN, 'alfa', 's2', 'alfa-s2-przycisk', 'Wątek do odrzucenia');
  await pw.reload(); await openThreadByPin(pw, 2);
  await pw.offline(true);
  await pw.evaluate(`document.querySelector('.anno-panel textarea[aria-label="Odpowiedź"]').focus(); true`); await pw.type('Wpis do odrzucenia'); await pw.key('Enter', { ctrl: true });
  await pw.waitFor(`/w kolejce/.test(${saveState})`, 8000);
  await apiPost(DEV, 'alfa', { cid: cid('d'), type: 'delete', threadId: th.threadId, payload: {} });
  await pw.offline(false);
  await pw.waitFor(`/Błąd zapisu/.test(${saveState})`, 40000);
  const rejExpr = `[...document.querySelectorAll('.anno-banners button')].find((b) => /Pokaż|odrzucone/i.test(b.textContent))`;
  { const o = await open(pw, rejExpr); results['odrzucone-wpisy'] = o.ok ? await windowCycle(pw, { opener: rejExpr }) : { ok: false, why: 'okno się nie otworzyło' }; }
  // sesja wygasła: okno „Zaloguj się ponownie” otwiera się samo po próbie zapisu
  await pw.send('Network.deleteCookies', { name: ANN.cookie.slice(0, ANN.cookie.indexOf('=')), url: srv.base });
  await pw.evaluate(`window.scrollTo(0,0); true`);
  await pw.reload().catch(() => {});
  await pw.dispose();
  const { p: px } = await sessionPage(c, 'anna-exp', srv.base, 'anna'); await px.go(url('alfa'));
  await px.send('Network.deleteCookies', { name: ANN.cookie.slice(0, ANN.cookie.indexOf('=')), url: srv.base });
  await openThreadByPin(px, 1).catch(() => {});
  await px.evaluate(`(() => { const t = document.querySelector('.anno-panel textarea[aria-label="Odpowiedź"]'); if (t) t.focus(); return 1; })()`); await px.type('Po wygaśnięciu'); await px.key('Enter', { ctrl: true });
  const relog = await px.waitFor(dlgOpen, 10000);
  results['zaloguj-ponownie'] = relog.ok ? await windowCycle(px, { opener: `(document.querySelector('.anno-user') && document.querySelector('.anno-user').contains(document.activeElement) ? document.activeElement : null)` }) : { ok: false, why: 'okno się nie otworzyło' };
  await px.dispose();
  // wymuszone hasło: Esc nie zamyka
  const m = await session(srv.base, 'marta', secret('default'));
  const pm = await c.page('marta', { session: m, url: srv.base }); await pm.go(url('alfa'));
  const forced = await pm.waitFor(dlgOpen, 8000);
  results['wymuszone-haslo'] = forced.ok ? await windowCycle(pm, { escBlocked: true }) : { ok: false, why: 'okno się nie otworzyło' };
  await pm.dispose();
  // tytuł (początek tekstu) każdego okna zgodny z kontraktem (docs/prototype-contract/scenariusze-e2e.md, E3-14)
  const titles = { 'akceptacja-s1': /Akcept/i, 'akceptacja-s2': /Akcept/i, 'akceptacja-chip': /Akcept/i, przywroc: /Przywr/i, porownaj: /Porówn|Iteracja/i, logowanie: /Zaloguj|Logowanie/i, 'usun-watek': /Usunąć wątek/, wycofaj: /Wycofać wpis/, 'odrzucone-wpisy': /Odrzucone wpisy/, 'zaloguj-ponownie': /Zaloguj się ponownie/, 'wymuszone-haslo': /Ustaw własne hasło/ };
  for (const [k, re] of Object.entries(titles)) if (results[k] && results[k].ok && !re.test(results[k].title || '')) { results[k].ok = false; results[k].badTitle = results[k].title; }
  const summary = Object.fromEntries(Object.entries(results).map(([k, v]) => [k, v.ok ? 'PASS' : 'FAIL']));
  // spis rodzajów elementów w oknach (które z elementów filtru fokusu występują na żywym prototypie)
  const kindsSum = {};
  const addKinds = (into, from) => { for (const [k, n] of Object.entries(from || {})) { if (n && typeof n === 'object') addKinds(into[k] = into[k] || {}, n); else if (typeof n === 'number') into[k] = (into[k] || 0) + n; } };
  for (const v of Object.values(results)) addKinds(kindsSum, v.audit?.kinds);
  const kindsByWindow = Object.fromEntries(Object.entries(results).map(([k, v]) => [k, v.audit?.kinds]));
  return { ok: Object.values(results).every((v) => v.ok), focusKindsTotal: kindsSum, focusKinds: kindsByWindow, windows: summary, details: Object.fromEntries(Object.entries(results).filter(([, v]) => !v.ok)) };
}, { timeout: 280000 });

suite.add('E3-15', 'Imię przy akceptacji z CLI', async () => {
  await resetAcc('beta', ['s1', 's2']);
  const r = await acceptCli('celina', [ws.proj, 'beta', '--all', '--by', mail('celina'), '--json']);
  const p = await acceptor('celina', 'celina', 'beta', '');
  await p.domClick(`document.querySelector('.anno-accept')`); await p.waitFor(dlgOpen, 4000);
  const t = await p.evaluate(dlgText); await p.dispose();
  return { ok: r.code === 0 && r.promptSent && /Zaakceptowano: Celina Akcept, [^,]+, iteracja \d+ \(CLI\)/.test(t || ''), cliExit: r.code, dialog: (t || '').slice(0, 160) };
});

suite.add('E3-16', 'Ciemny motyw', async () => {
  await resetAcc('alfa', ['s1', 's2']);
  const { p } = await sessionPage(c, 'dark', srv.base, 'celina', { dark: true }); await p.go(url('alfa'));
  const bodyBg = await p.evaluate(OPAQUE_BG('document.body'));
  const chip = await p.evaluate(`(() => { const c = document.querySelector('.anno-accept'); return c ? getComputedStyle(c).color : null; })()`);
  await p.domClick(opener('s1')); await p.waitFor(dlgOpen, 4000);
  const dlgBg = await p.evaluate(OPAQUE_BG(DLG));
  await p.dispose();
  const lb = luminance(bodyBg); const ld = luminance(dlgBg);
  return { ok: lb !== null && lb < 0.3 && ld !== null && ld < 0.35, bodyBackground: bodyBg, dialogBackground: dlgBg, chipColor: chip };
});

suite.add('E3-17', 'Akceptacja przez CLI widoczna w UI', async () => {
  await resetAcc('beta', ['s1', 's2']);
  const r = await acceptCli('celina', [ws.proj, 'beta', '--all', '--by', mail('celina'), '--json']);
  const p = await acceptor('celina', 'celina', 'beta', '');
  const chip = await p.evaluate(chipInfo);
  await p.domClick(`document.querySelector('.anno-accept')`); await p.waitFor(dlgOpen, 4000);
  const t = await p.evaluate(dlgText); await p.dispose();
  return { ok: r.code === 0 && r.promptSent && r.json && r.json.status === 'PASS' && /cli/i.test(JSON.stringify(r.json)) && !!chip && chip.count === '2/2' && /\(CLI\)/.test(t || ''), cliExit: r.code, cliVia: r.json && JSON.stringify(r.json).includes('cli'), chip: chip && chip.count };
});

suite.add('E3-18', 'Bramka prototypu', async () => {
  mkdirSync(`${ws.proj}/docs/plans`, { recursive: true });
  writeFileSync(`${ws.proj}/docs/plans/plan.md`, '# Plan\n\nSpec: docs/specs/x.md\nPrototyp: docs/prototypes/alfa\n\n## Etap 1\nTreść.\n');
  await resetAcc('alfa', ['s1', 's2']);
  const gate = () => proto(ws, 'gate', '--plan', 'docs/plans/plan.md');
  const g1 = await gate();
  await setAcceptance(ACC, DEV, srv.base, 'alfa', 's1', true); await setAcceptance(ACC, DEV, srv.base, 'alfa', 's2', true);
  const g2 = await gate();
  const orig = ws.read('alfa');
  ws.write('alfa', 'index.html', orig.replace('Treść s2.', 'Treść s2 inna.'));
  const g3 = await gate();
  const rd = await new Promise((ok) => { import('node:child_process').then(({ spawn }) => { const ch = spawn('python3', [env.cli, 'readiness', ws.proj, '--plan', 'docs/plans/plan.md', '--files', 'docs/prototypes/alfa/index.html', '--json'], { env: { ...process.env, SKILLSYNC_CONFIG: env.config } }); let o = ''; ch.stdout.on('data', (d) => { o += d; }); ch.on('close', (code) => ok({ code, out: o })); }); });
  ws.write('alfa', 'index.html', orig);
  const miss1 = JSON.stringify(g1.json?.missing || []); const miss3 = JSON.stringify(g3.json?.missing || []);
  return { ok: g1.code === 1 && /brak/.test(miss1) && g2.code === 0 && /via\W{0,6}ui\b/i.test(JSON.stringify(g2.json?.acceptances || [])) && g3.code === 1 && /nieaktualna/.test(miss3) && rd.code === 2, gateBefore: g1.code, gateAccepted: g2.code, gateAfterChange: g3.code, readinessExit: rd.code };
}, { critical: false });

suite.add('E3-19', 'Narzędzia ekranu programisty', async () => {
  await proto(ws, 'iterate', 'beta');
  const { p } = await sessionPage(c, 'dawid', srv.base, 'dawid'); await p.go(url('beta'));
  const texts = await p.evaluate(`[...document.querySelectorAll('#s1 button, #s1 .anno-screen-tools *')].map((b) => b.textContent.trim()).filter(Boolean)`);
  const has = (t) => texts.some((x) => x.includes(t));
  await p.evaluate(`[...document.querySelectorAll('button')].find((b) => b.textContent.includes('Porównaj z iteracją') && b.getClientRects().length > 0).focus(); true`); await p.key('Enter');
  const frames = await p.waitFor(`document.querySelectorAll('iframe[src*="embed=compare"]').length >= 2`, 8000);
  const urlSame = await p.evaluate(`location.pathname`);
  await p.key('Escape');
  await p.go(`${srv.base}/beta/iterations/1/`);
  const frozen = await p.evaluate(`document.documentElement.hasAttribute('data-prototype-frozen')`);
  const noChip = await p.evaluate(`!document.querySelector('.anno-accept') && !document.querySelector('.anno-accept-open')`);
  await p.dispose();
  return { ok: has('Porównaj z iteracją') && has('Akceptuj') && has('Przywróć') && frames.ok && /\/beta\/$/.test(urlSame) && frozen && noChip, tools: texts.slice(0, 8), compareFrames: frames.ok, frozenFlag: frozen, frozenNoAcceptance: noChip };
});

suite.add('E3-20', 'Chip na szerokościach', async () => {
  await resetAcc('alfa', ['s1', 's2']);
  const p = await acceptor('celina', 'celina', 'alfa', '');
  const res = []; let ok = true;
  for (const w of [1440, 900, 768, 375]) {
    await p.size(w, w < 600 ? 812 : 900); await sleep(400);
    const r = await p.evaluate(`(() => { const c = document.querySelector('.anno-accept'); const t = document.querySelector('.proto-toolbar'); if (!c || !t) return null; const a = c.getBoundingClientRect(), b = t.getBoundingClientRect(); const sib = [...t.children].filter((e) => e !== c && !e.contains(c) && e.getClientRects().length); const overlap = sib.some((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && !(r.right <= a.left + 1 || r.left >= a.right - 1 || r.bottom <= a.top + 1 || r.top >= a.bottom - 1); }); return { inside: a.left >= b.left - 1 && a.right <= b.right + 1, overlap, scrollLeft: t.scrollLeft, label: c.getAttribute('aria-label'), text: c.textContent.trim() }; })()`);
    res.push({ w, ...r });
    if (!r || !r.inside || r.overlap || r.scrollLeft !== 0 || !r.label) ok = false;
  }
  await p.dispose();
  return { ok, widths: res };
});

suite.add('E3-21', 'Zduplikowany id ekranu', async () => {
  const orig = ws.read('alfa');
  mkdirSync(`${ws.proj}/docs/plans`, { recursive: true });
  writeFileSync(`${ws.proj}/docs/plans/plan.md`, '# Plan\n\nSpec: docs/specs/x.md\nPrototyp: docs/prototypes/alfa\n\n## Etap 1\nTreść.\n');
  ws.write('alfa', 'index.html', orig.replace('<section class="screen" id="s1"', '<section class="screen" id="s2"'));
  const bad = await proto(ws, 'gate', '--plan', 'docs/plans/plan.md');
  ws.write('alfa', 'index.html', orig);
  const good = await proto(ws, 'gate', '--plan', 'docs/plans/plan.md');
  return { ok: bad.code === 1 && /niejednoznaczny_ekran/.test(bad.out) && !/niejednoznaczny_ekran/.test(good.out), duplicateGate: bad.code, restoredGate: good.code };
}, { critical: false });

suite.add('E3-99', 'Konsola', async () => consoleVerdict());

await suite.main();
