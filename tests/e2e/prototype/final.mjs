// Zestaw końcowy: R3-02a, R3-02b, Z1 … Z4. Prototyp alfa (s1, s2), konta anna, bartek, celina, dawid.
import {
  Suite, sleep, workspace, startServer, connect, errors, PORT1, session, apiOps, apiPost, apiRaw, cid, proto, createThread, setAcceptance,
  btn, btnExact, DLG, DLG_LIST, dlgText, dlgOpen, inDlg, isBody, saveState, bannerText, wasSaved, openPanel, closePanelIfOpen, commentOn, openThreadByPin,
  secret, mail, sessionPage, panelIsOpen, queueLength, loginUI,
} from './lib.mjs';

const suite = new Suite('final');
let ws; let srv; let c; let ACC; let DEV; let ANN;
const url = (hash = '#s2') => `${srv.base}/alfa/${hash}`;
const opener = (sid) => `document.querySelector('#${sid} .anno-accept-open')`;
const REPLY = `document.querySelector('.anno-panel textarea[aria-label="Odpowiedź"]')`;
const CHIP = `document.querySelector('[data-anno-focus="user-chip"]')`;
const draftVal = `(() => { const t = document.querySelector('.anno-panel textarea'); return t ? t.value : null; })()`;
const titleFocus = `(() => { const a = document.activeElement; return !!a && a.classList.contains('anno-panel-title'); })()`;
const storeInfo = (prefix, login) => `(() => { const k = Object.keys(localStorage).find((x) => x.startsWith(${JSON.stringify(prefix)}) && x.endsWith(':' + ${JSON.stringify(login)})); if (!k) return { present: false, count: 0 }; let v; try { v = JSON.parse(localStorage.getItem(k)); } catch (e) { return { present: true, count: -1 }; } const items = Array.isArray(v) ? v : (v && Array.isArray(v.items) ? v.items : []); return { present: true, count: items.length }; })()`;
const QUEUE = 'skillsync-prototype-queue:v2:alfa:'; const REJ = 'skillsync-prototype-rejected:v2:alfa:';

async function openAnyThread(p) {
  const n = await p.evaluate(`document.querySelectorAll('button.anno-pin:not(.anno-pin-static)').length`);
  for (let i = 0; i < Math.min(n, 8); i++) {
    await p.evaluate(`(() => { const b = [...document.querySelectorAll('button.anno-pin:not(.anno-pin-static)')][${i}]; if (b) b.click(); return 1; })()`);
    await sleep(700);
    if (await p.evaluate(`!!${REPLY}`)) return { ok: true, pin: i };
  }
  return { ok: false };
}

suite.setup(async () => {
  ws = workspace('final');
  srv = await startServer(ws, { port: PORT1 });
  c = await connect();
  ACC = await session(srv.base, 'celina'); DEV = await session(srv.base, 'dawid'); ANN = await session(srv.base, 'anna');
  for (const t of ['Z wątek jeden', 'Z wątek dwa']) await createThread(ANN, 'alfa', 's2', t.endsWith('jeden') ? 'alfa-s2-tytul' : 'alfa-s2-przycisk', t);
  await proto(ws, 'iterate', 'alfa');
  return {};
});
suite.teardown(async () => { if (c) await c.closeAll(); if (srv) await srv.stop(); });

suite.add('R3-02a', 'Pełny obieg Tab w Porównaniu', async () => {
  const { p } = await sessionPage(c, 'dawid', srv.base, 'dawid'); await p.go(url('#s1'));
  await p.evaluate(`[...document.querySelectorAll('#s1 button, #s1 .anno-screen-tools button')].find((b) => b.textContent.includes('Porównaj z iteracją') && b.getClientRects().length > 0).focus(); true`);
  await p.key('Enter'); await p.waitFor(dlgOpen, 5000);
  await sleep(1500);
  const desc = `(() => { const a = document.activeElement; return a ? a.tagName + ':' + ((a.getAttribute('aria-label') || a.textContent || a.getAttribute('title') || '').trim().slice(0, 20)) : null; })()`;
  await p.evaluate(`(() => { const L = ${DLG_LIST}; L[0].focus(); return 1; })()`);
  const first = await p.evaluate(desc);
  const seq = []; let leftWindow = 0; let bodyHits = 0;
  for (let i = 0; i < 14; i++) { await p.key('Tab'); seq.push(await p.evaluate(desc)); if (!(await p.evaluate(inDlg))) leftWindow++; if (await p.evaluate(isBody)) bodyHits++; }
  const closeIdx = seq.findIndex((x) => /Zamknij/.test(x || ''));
  const wraps = closeIdx >= 0 && closeIdx + 1 < seq.length && /^SELECT/.test(seq[closeIdx + 1] || '');
  await p.evaluate(`(() => { const L = ${DLG_LIST}; L[0].focus(); return 1; })()`); await p.key('Tab', { shift: true });
  const shiftFromFirst = await p.evaluate(desc);
  await p.key('Escape'); await sleep(400);
  const closed = !(await p.evaluate(dlgOpen));
  await p.dispose();
  return { ok: /^SELECT/.test(first) && leftWindow === 0 && bodyHits === 0 && wraps && /Zamknij/.test(shiftFromFirst || '') && closed, firstElement: first, sequence: seq, leftWindow, bodyHits, wrapsToSelect: wraps, shiftTabFromFirst: shiftFromFirst, escClosed: closed };
});

suite.add('R3-02b', 'Esc w trakcie zapisu akceptacji', async () => {
  await setAcceptance(ACC, DEV, srv.base, 'alfa', 's1', false); await setAcceptance(ACC, DEV, srv.base, 'alfa', 's2', false); await sleep(1500);
  const { p } = await sessionPage(c, 'celina', srv.base, 'celina'); await p.go(url(''));
  const before = await p.evaluate(`((document.querySelector('.anno-accept-count') || {}).textContent || '').trim()`);
  const held = []; const status = [];
  await p.intercept(['*/_api/v1/prototypes/alfa/operations*'], async (ev, api) => { if (ev.request.method === 'POST') { held.push({ id: ev.requestId, api }); } else api.continue(); });
  await p.domClick(opener('s1')); await p.waitFor(dlgOpen, 4000);
  await p.domClick(btnExact('Akceptuję', DLG)); await sleep(500);
  await p.key('Escape'); await sleep(500);
  const closedBefore = !(await p.evaluate(dlgOpen));
  const postHeld = held.length;
  held.forEach((h) => h.api.continue());
  const after = await p.waitFor(`((document.querySelector('.anno-accept-count') || {}).textContent || '').trim() === '1/2'`, 8000);
  const toast = await p.waitFor(`/Zaakceptowano ekran/.test(document.body.innerText)`, 6000);
  const saved = await p.waitFor(wasSaved, 6000);
  await p.interceptOff(); await p.dispose();
  return { ok: before === '0/2' && closedBefore && postHeld === 1 && after.ok && toast.ok && saved.ok, countBefore: before, closedBeforeResponse: closedBefore, heldPosts: postHeld, countAfter: after.ok, toast: toast.ok, saveState: saved.ok };
});

suite.add('Z1', 'Szkic odpowiedzi i Esc', async () => {
  const { p } = await sessionPage(c, 'anna', srv.base, 'anna'); await p.go(url());
  const S = {}; const STAMP = 'Z1 szkic ' + Date.now().toString(36);
  const open = await openAnyThread(p); if (!open.ok) throw new Error('nie otwarto wątku');
  await p.evaluate(`${REPLY}.focus(); true`); await p.type(STAMP);
  await p.key('Escape'); await sleep(400);
  S.esc1 = { titleFocus: await p.evaluate(titleFocus), panelOpen: await p.evaluate(panelIsOpen), draftKept: (await p.evaluate(draftVal)) === STAMP, dialog: await p.evaluate(dlgText) };
  await p.key('Escape'); await sleep(700);
  S.esc2 = await p.evaluate(dlgText);
  await p.domClick(btnExact('Anuluj', DLG)); await sleep(700);
  S.anuluj = { closed: !(await p.evaluate(dlgOpen)), panelOpen: await p.evaluate(panelIsOpen), draftKept: (await p.evaluate(draftVal)) === STAMP, fieldFocus: await p.evaluate(`document.activeElement === ${REPLY}`) };
  await p.key('Escape'); await sleep(300); await p.key('Escape'); await sleep(700);
  S.esc3 = await p.evaluate(dlgText);
  await p.domClick(btnExact('Porzuć szkic', DLG)); await sleep(700);
  S.potwierdzenie = { closed: !(await p.evaluate(dlgOpen)), panelOpen: await p.evaluate(panelIsOpen), replyVisible: await p.evaluate(`!!${REPLY}`) };
  const open2 = await openAnyThread(p);
  await p.evaluate(`${REPLY}.value = ''; ${REPLY}.focus(); true`); await p.key('Escape'); await sleep(600);
  S.pusty = { panelOpen: await p.evaluate(panelIsOpen), dialog: await p.evaluate(dlgText) };
  const open3 = await openAnyThread(p);
  await p.evaluate(`${REPLY}.focus(); true`); await p.type(STAMP + ' x');
  await p.domClick(`[...document.querySelectorAll('.anno-panel-head button')].find((b) => /zamknij/i.test((b.getAttribute('aria-label') || '') + (b.title || '') + b.textContent))`); await sleep(700);
  S.xDialog = await p.evaluate(dlgText);
  const n = await p.evaluate(`${DLG_LIST}.length`);
  await p.evaluate(`(() => { const L = ${DLG_LIST}; L[L.length - 1].focus(); return 1; })()`); await p.key('Tab');
  const lastToFirst = await p.evaluate(`${DLG_LIST}.indexOf(document.activeElement) === 0`);
  await p.evaluate(`(() => { const L = ${DLG_LIST}; L[0].focus(); return 1; })()`); await p.key('Tab', { shift: true });
  const firstToLast = await p.evaluate(`(() => { const L = ${DLG_LIST}; return L.indexOf(document.activeElement) === L.length - 1; })()`);
  let cycle = true; await p.evaluate(`(() => { ${DLG_LIST}[0].focus(); return 1; })()`);
  for (let i = 0; i < n + 2; i++) { await p.key('Tab'); if (!(await p.evaluate(inDlg)) || (await p.evaluate(isBody))) cycle = false; }
  S.pulapka = { lastToFirst, firstToLast, cycle };
  await p.domClick(btnExact('Anuluj', DLG)); await sleep(700);
  S.xAnuluj = { closed: !(await p.evaluate(dlgOpen)), panelOpen: await p.evaluate(panelIsOpen), draftKept: (await p.evaluate(draftVal)) === STAMP + ' x' };
  await p.domClick(`[...document.querySelectorAll('.anno-panel-head button')].find((b) => /zamknij/i.test((b.getAttribute('aria-label') || '') + (b.title || '') + b.textContent))`); await sleep(700);
  await p.domClick(btnExact('Porzuć szkic', DLG)); await sleep(700);
  S.xPotwierdzenie = { panelOpen: await p.evaluate(panelIsOpen) };
  // Esc w widoku wątku bez szkicu, fokus poza polem: wraca do listy (panel zostaje otwarty, bez okna)
  const open4 = await openAnyThread(p);
  const leftover = await p.evaluate(draftVal);   // dowód: czy szkic po „Porzuć szkic” (×) został w polu po ponownym otwarciu wątku
  await p.evaluate(`(() => { const t = ${REPLY}; if (t) { t.focus(); t.value = ''; t.dispatchEvent(new Event('input', { bubbles: true })); } return 1; })()`); await sleep(200);
  await p.evaluate(`(document.activeElement && document.activeElement.blur && document.activeElement.blur(), 1)`);
  const draftBefore = await p.evaluate(draftVal);
  await p.key('Escape'); await sleep(700);
  S.escWatekBezSzkicu = { leftoverDraftAfterDiscard: leftover, draftBefore, panelOpen: await p.evaluate(panelIsOpen), replyGone: !(await p.evaluate(`!!${REPLY}`)), cards: await p.evaluate(`document.querySelectorAll('.anno-panel .anno-card').length`), dialog: await p.evaluate(dlgText) };
  await closePanelIfOpen(p);
  await p.dispose();
  const checks = {
    esc1: S.esc1.titleFocus && S.esc1.panelOpen && S.esc1.draftKept && !S.esc1.dialog,
    esc2: /Porzucić szkic/.test(S.esc2 || ''),
    anuluj: S.anuluj.closed && S.anuluj.panelOpen && S.anuluj.draftKept && S.anuluj.fieldFocus,
    porzuc: /Porzucić szkic/.test(S.esc3 || '') && S.potwierdzenie.closed && S.potwierdzenie.panelOpen && !S.potwierdzenie.replyVisible,
    pusty: open2.ok && S.pusty.panelOpen === false && !S.pusty.dialog,
    xOkno: open3.ok && /Porzucić szkic/.test(S.xDialog || ''),
    pulapka: lastToFirst && firstToLast && cycle,
    xAnuluj: S.xAnuluj.panelOpen && S.xAnuluj.draftKept && S.xAnuluj.closed,
    xPorzuc: S.xPotwierdzenie.panelOpen === false,
    escWatekBezSzkicu: open4.ok && S.escWatekBezSzkicu.draftBefore === '' && S.escWatekBezSzkicu.panelOpen && S.escWatekBezSzkicu.replyGone && S.escWatekBezSzkicu.cards > 0 && !S.escWatekBezSzkicu.dialog,
  };
  return { ok: Object.values(checks).every(Boolean), checks, watekBezSzkicu: S.escWatekBezSzkicu };
});

suite.add('Z1-b', 'Esc w widoku listy ze szkicem zostawionym w wątku', async () => {
  // kontrakt: Esc w liście ze szkicem pyta „Porzucić szkic?”; „Porzuć szkic” usuwa szkic (po ponownym otwarciu wątku pole jest puste)
  const { p } = await sessionPage(c, 'anna', srv.base, 'anna'); await p.go(url());
  const STAMP = 'Z1b szkic ' + Date.now().toString(36);
  const open = await openAnyThread(p);
  let hasBack = false; let inList = false; let ask = false; let gone = null; let backLabel = null; let reopened = false;
  if (open.ok) {
    await p.evaluate(`${REPLY}.focus(); true`); await p.type(STAMP);
    const backCtl = `[...document.querySelectorAll('.anno-panel button, .anno-panel [role="button"]')].find((b) => !/zamknij/i.test((b.getAttribute('aria-label') || '') + (b.title || '')) && /lista|wróć|wszystkie wątki|←|‹/i.test((b.getAttribute('aria-label') || '') + (b.title || '') + b.textContent))`;
    hasBack = await p.evaluate(`!!(${backCtl})`);
    if (hasBack) { backLabel = await p.evaluate(`(() => { const b = ${backCtl}; return (b.getAttribute('aria-label') || b.title || b.textContent).trim().slice(0, 40); })()`); await p.domClick(backCtl); await sleep(700); }
    inList = hasBack && await p.evaluate(`!${REPLY}`);
    if (inList) {
      await p.evaluate(`(document.activeElement && document.activeElement.blur && document.activeElement.blur(), 1)`);
      await p.key('Escape'); await sleep(700);
      ask = /Porzucić szkic/.test((await p.evaluate(dlgText)) || '');
      if (ask) { await p.domClick(btnExact('Porzuć szkic', DLG)).catch(() => {}); await sleep(700); }
      await closePanelIfOpen(p);
      const again = await openAnyThread(p); reopened = again.ok;
      gone = again.ok ? (await p.evaluate(draftVal)) === '' : null;
    }
  }
  await p.dispose();
  const setupOk = open.ok && hasBack && inList && reopened;   // przygotowanie (wątek, szkic, przejście do listy, ponowne otwarcie) się udało
  return { ok: setupOk && ask && gone === true, restOk: setupOk, defectObserved: setupOk && (!ask || gone !== true), askedAboutDraft: ask, draftGoneAfterDiscard: gone, backLabel, setup: { threadOpened: open.ok, hasBack, inList, reopened } };
});

suite.add('Z2', 'Fokus przy odpytywaniu z błędem', async () => {
  const { p } = await sessionPage(c, 'anna', srv.base, 'anna');
  let gets = 0;
  await p.intercept(['*/_api/v1/prototypes/*/operations*'], async (ev, api) => { if (ev.request.method === 'GET') { gets++; api.fulfill(409, { error: 'invalid_log' }); } else api.continue(); });
  await p.go(url());
  const BTN = `[...document.querySelectorAll('.anno-banners button')].find((b) => /Spróbuj ponownie/.test(b.textContent))`;
  const banner = await p.waitFor(`!!${BTN}`, 30000);
  if (!banner.ok) throw new Error('baner „Spróbuj ponownie” nie pojawił się');
  const g0 = gets;
  const focus = await p.evaluate(`(() => { const c = ${CHIP}; if (!c) return 'NOEL'; c.focus(); window.__chip = c; return document.activeElement === c; })()`);
  await sleep(12500);
  const chip = { still: await p.evaluate(`document.activeElement === window.__chip`), inDom: await p.evaluate(`!!window.__chip && document.body.contains(window.__chip)`), polls: gets - g0 };
  await p.evaluate(`(() => { const x = ${BTN}; x.focus(); window.__btn = x; window.__mut = 0; window.__obs = new MutationObserver((r) => { window.__mut += r.length; }); window.__obs.observe(document.querySelector('.anno-banners'), { childList: true, subtree: true, attributes: true, characterData: true }); return 1; })()`);
  const g1 = gets; await sleep(12000);
  const bt = { still: await p.evaluate(`document.activeElement === window.__btn`), inDom: await p.evaluate(`!!window.__btn && document.body.contains(window.__btn)`), mutations: await p.evaluate(`window.__mut`), polls: gets - g1 };
  await p.interceptOff(); await p.dispose();
  return { ok: focus === true && chip.still && chip.inDom && chip.polls >= 1 && bt.still && bt.inDom && bt.mutations === 0, chip, button: bt };
}, { timeout: 120000 });

suite.add('Z3', 'Regresja końcowa', async () => {
  const { p } = await sessionPage(c, 'anna', srv.base, 'anna'); await p.go(url());
  const saved = await commentOn(p, 'alfa-s2-tytul', 'Z3 komentarz komentującego');
  await p.reload();
  const pins = await p.evaluate(`document.querySelectorAll('button.anno-pin:not(.anno-pin-static)').length`);
  const ops = (await apiOps(srv.base, 'alfa')).operations;
  const own = ops.filter((o) => JSON.stringify(o.payload || {}).includes('Z3 komentarz')).length;
  await p.dispose();
  const q = await c.page('anon'); await q.go(url());
  const chip = await q.evaluate(`(() => { const c = document.querySelector('.anno-accept'); return c ? { tag: c.tagName, role: c.getAttribute('role'), count: (document.querySelector('.anno-accept-count') || {}).textContent } : null; })()`);
  await q.dispose();
  const bad = errors.filter((e) => e.kind === 'exception' || e.kind === 'console.error').length;
  return { ok: saved && pins >= 3 && own === 1 && !!chip && bad === 0, saved, pins, copiesOnServer: own, readModeChip: chip, consoleErrors: bad };
});

suite.add('Z4', 'Właściciel kolejki', async () => {
  const { p } = await sessionPage(c, 'anna', srv.base, 'anna');
  let held = null;
  await p.intercept(['*/_api/v1/prototypes/*/operations*'], async (ev, api) => { if (ev.request.method === 'POST' && held === null) held = api; else api.continue(); });
  await p.go(url());
  const open = await openAnyThread(p); if (!open.ok) throw new Error('nie otwarto wątku');
  await p.evaluate(`${REPLY}.focus(); true`); await p.type('Z4 odpowiedź ' + Date.now().toString(36)); await p.key('Enter', { ctrl: true });
  await p.waitFor('true', 1000); await sleep(1500);
  const queueA = await p.evaluate(storeInfo(QUEUE, 'anna@example.com'));
  if (held === null) throw new Error('POST nie został wstrzymany');
  await p.domClick(CHIP); await sleep(500);
  await p.domClick(btn('Wyloguj', `document.querySelector('.anno-user-menu')`)); await sleep(1200);
  const chipGone = await p.evaluate(`!${CHIP} || !/anna/i.test(${CHIP}.textContent)`);
  await p.domClick(`document.querySelector('[data-anno-focus="user-login"]') || ${CHIP}`); await sleep(600);
  const loginUp = await p.evaluate(`!!document.getElementById('anno-login-email')`);
  await p.evaluate(`document.getElementById('anno-login-email').focus(); true`); await p.type(mail('bartek'));
  await p.evaluate(`document.getElementById('anno-login-password').focus(); true`); await p.type(secret('bartek')); await p.key('Enter');
  await p.waitFor(`!${DLG} && !!${CHIP} && ${CHIP}.textContent.indexOf('Zaloguj') === -1`, 10000); await sleep(800);
  held.fulfill(409, { error: 'stale_view' }); await sleep(2500);
  const rejB = await p.evaluate(storeInfo(REJ, 'bartek@example.com')); const rejA = await p.evaluate(storeInfo(REJ, 'anna@example.com'));
  const queueAafter = await p.evaluate(storeInfo(QUEUE, 'anna@example.com')); const queueB = await p.evaluate(storeInfo(QUEUE, 'bartek@example.com'));
  await p.interceptOff(); await p.dispose();
  return { ok: queueA.count === 1 && chipGone && loginUp && rejB.count === 0 && queueB.count === 0 && queueAafter.count >= 1, queueBeforeLogout: queueA.count, rejectedForB: rejB.count, rejectedForA: rejA.count, queueAAfter: queueAafter.count, queueB: queueB.count };
});

await suite.main();
