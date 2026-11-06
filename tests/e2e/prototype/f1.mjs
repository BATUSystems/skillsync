// F1: komentarze wspólne (F1-S01 … F1-S23, F1-S99). Prototyp `demo`, serwer na pierwszym porcie, konta anna/bartek/dawid/ewa.
import { spawn } from 'node:child_process';
import {
  Suite, sleep, workspace, startServer, connect, errors, env, PORT1, PORT2, session, apiOps, apiPost, apiRaw, secret, mail, cid, proto,
  btn, btnExact, byProto, DLG, dlgText, dlgOpen, saveState, panelText, panelIsOpen, bannerText, wasSaved, openPanel, closePanelIfOpen, loginUI,
  commentOn, commentMode, commentBtn, openThreadByPin, replyIn, queueLength, filtersState, countCards, pickFilter, consoleVerdict, readFileSync, writeFileSync, existsSync, rmSync, writeFileSync as wf,
} from './lib.mjs';

const suite = new Suite('f1');
const SLUG = 'demo';
let ws; let srv; let c; let staticSrv;
const S = {};   // współdzielone karty i fakty między scenariuszami
const url = () => `${srv.base}/${SLUG}/`;
const chip = (p) => p.evaluate(saveState);
const journal = () => readFileSync(ws.file(SLUG, 'comments.json'), 'utf8').split('\n').map((l) => { try { return JSON.parse(l.replace(/,\s*$/, '')); } catch (e) { return null; } }).filter((o) => o && o.type);
const countText = (ops, text) => ops.filter((o) => JSON.stringify(o.payload || {}).includes(text)).length;

suite.setup(async () => {
  ws = workspace('f1');
  srv = await startServer(ws, { port: PORT1 });
  c = await connect();
  return {};
});
suite.teardown(async () => {
  if (c) await c.closeAll();
  if (staticSrv) { try { process.kill(staticSrv.pid, 'SIGTERM'); } catch (e) { /* zakończony */ } }
  if (srv) await srv.stop();
});

suite.add('F1-S01', 'Tryb file://', async () => {
  const F = await c.page('file'); F.net.length = 0;
  await F.go('file://' + ws.file(SLUG));
  const chipText = await chip(F);
  await openPanel(F);
  const pt = await F.evaluate(panelText);
  await F.evaluate(`window.scrollTo(0,0); true`);
  await commentMode(F, true); await F.clickEl(byProto('s1-nowy'));
  const composer = await F.evaluate(`!!document.querySelector('.anno-composer-pop')`);
  const ls = await F.evaluate(`Object.keys(localStorage)`);
  const http = F.net.filter((u) => /\bhttp:/.test(u));
  await F.dispose();
  return { ok: /Bez serwera/.test(chipText) && /skillsync prototype serve/.test(pt) && !composer && !ls.some((k) => /queue|comments/.test(k)) && http.length === 0, chip: chipText, composer, localStorage: ls, httpRequests: http.length };
});

suite.add('F1-S02', 'Tryb statyczny', async () => {
  const child = spawn('python3', ['-m', 'http.server', String(PORT2), '--bind', '127.0.0.1', '--directory', `${ws.proj}/docs/prototypes`], { stdio: 'ignore', detached: true });
  child.unref(); staticSrv = child; const staticPid = `${ws.dir}/static-${PORT2}.pid`; writeFileSync(staticPid, String(child.pid));
  for (let i = 0; i < 40; i++) { try { await fetch(`http://127.0.0.1:${PORT2}/`); break; } catch (e) { await sleep(150); } }
  const P = await c.page('static'); const e0 = errors.length;
  await P.go(`http://127.0.0.1:${PORT2}/${SLUG}/`);
  const chipText = await chip(P);
  const login = await P.evaluate(`!!${btn('Zaloguj', `document.querySelector('.anno-user-slot') || document`)}`);
  const sections = await P.evaluate(`document.querySelectorAll('section.screen').length`);
  const js = errors.slice(e0).filter((e) => e.kind === 'exception' || e.kind === 'console.error');
  await P.dispose();
  try { process.kill(child.pid, 'SIGTERM'); } catch (e) { /* ok */ } staticSrv = null; rmSync(staticPid, { force: true });
  return { ok: /Tylko odczyt/.test(chipText) && !login && sections >= 2 && js.length === 0, chip: chipText, loginButton: login, sections, exceptions: js.length };
});

suite.add('F1-S03', 'Logowanie w oknie silnika', async () => {
  S.A = await c.page('A');
  await S.A.go(url());
  const before = await chip(S.A);
  await S.A.domClick(btn('Zaloguj', `document.querySelector('.anno-user-slot')`));
  await S.A.waitFor(`!!document.querySelector('#anno-login-email')`, 4000);
  const title = await S.A.evaluate(`(document.querySelector('#anno-dialog-title') || {}).textContent`);
  await S.A.evaluate(`document.querySelector('#anno-login-email').focus(); true`); await S.A.type(mail('anna'));
  await S.A.evaluate(`document.querySelector('#anno-login-password').focus(); true`); await S.A.type(secret('anna')); await S.A.key('Enter');
  const w = await S.A.waitFor(`!!document.querySelector('.anno-user')`, 8000);
  const user = await S.A.evaluate(`(document.querySelector('.anno-user') || {}).textContent`);
  const after = await S.A.waitFor(wasSaved, 6000);
  return { ok: w.ok && /Anna/.test(user) && /komentując/.test(user) && after.ok && /Zaloguj się, żeby komentować/.test(before), dialogTitle: title, userChip: user, stateBefore: before };
});

suite.add('F1-S04', 'Komentarz do elementu', async () => {
  const A = S.A;
  await A.evaluate(`window.scrollTo(0,0); true`); await commentMode(A, true);
  await A.clickEl(byProto('s1-karta-tytul'));
  const w = await A.waitFor(`!!document.querySelector('.anno-composer-pop')`, 3000);
  const head = await A.evaluate(`(document.querySelector('.anno-composer-head') || {}).textContent || ''`);
  const focused = await A.evaluate(`document.activeElement && document.activeElement.closest('.anno-composer-pop') ? document.activeElement.tagName : null`);
  await A.type('Tytuł karty za mały'); await A.key('Enter', { ctrl: true });
  const saved = await A.waitFor(`!document.querySelector('.anno-composer-pop') && ${wasSaved}`, 8000);
  await A.key('Escape'); await A.key('Escape'); await closePanelIfOpen(A);
  const pin = await A.evaluate(`(() => { const p = [...document.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].find((x) => x.textContent.trim() === '1'); return p ? { cls: p.className, label: p.getAttribute('aria-label') } : null; })()`);
  const more = [];
  for (const [id, text] of [['s1-usuwany', 'Ten przycisk zniknie'], ['s2-zapisz', 'Wątek do usunięcia przez programistę'], ['s2-nazwa', 'Pole nazwy bez podpowiedzi']]) more.push(await commentOn(A, id, text));
  const ops = await apiOps(srv.base, SLUG);
  S.threads = {}; for (const o of ops.operations) if (o.type === 'create') S.threads[o.payload.anchor.protoId] = o.threadId;
  return { ok: w.ok && /s1-karta-tytul/.test(head) && /Karta podsumowania/.test(head) && focused === 'TEXTAREA' && saved.ok && !!pin && /anno-st-open/.test(pin.cls) && /Wątek 1: Otwarty/.test(pin.label || '') && more.every(Boolean) && Object.keys(S.threads).length === 4, head, pin, focused, threadsCreated: Object.keys(S.threads).length };
});

suite.add('F1-S05', 'Odczyt bez logowania', async () => {
  const N = await c.page('anon'); await N.go(url());
  const chipText = await chip(N);
  const pins = await N.evaluate(`document.querySelectorAll('button.anno-pin:not(.anno-pin-static)').length`);
  await openPanel(N); const pt = await N.evaluate(panelText);
  await N.key('Escape'); await closePanelIfOpen(N);
  await commentMode(N, true); await N.clickEl(byProto('s1-usuwany'));
  const dlg = await N.evaluate(`(document.querySelector('#anno-dialog-title') || {}).textContent || null`);
  const composer = await N.evaluate(`!!document.querySelector('.anno-composer-pop')`);
  await N.dispose();
  return { ok: /Zaloguj się, żeby komentować/.test(chipText) && pins >= 4 && /Tytuł karty za mały/.test(pt) && !composer && /Zaloguj/.test(dlg || ''), chip: chipText, pins, loginDialog: dlg, composer };
});

suite.add('F1-S06', 'Odpowiedź innej osoby na żywo', async () => {
  const B = S.B = await c.page('B'); await B.go(url());
  const lb = await loginUI(B, 'bartek');
  await openThreadByPin(S.A, 1);
  await openThreadByPin(B, 1);
  await B.evaluate(`document.querySelector('.anno-panel textarea[aria-label="Odpowiedź"]').focus(); true`);
  await B.type('Zgadzam się, większy tytuł'); await B.key('Enter', { ctrl: true });
  const saved = await B.waitFor(`${wasSaved} && /większy tytuł/.test(${panelText})`, 8000, 50);
  const seen = await S.A.waitFor(`/Zgadzam się, większy tytuł/.test(${panelText})`, 12000, 100);
  return { ok: lb.ok && saved.ok && seen.ok && seen.ms <= 6000, msToA: seen.ms };
});

suite.add('F1-S07', 'Głosy', async () => {
  const { A, B } = S;
  const vote = (p, idx, dir) => p.domClick(`document.querySelectorAll('.anno-panel .anno-msg')[${idx}].querySelector('.anno-vote[aria-label^="${dir === 'up' ? 'Głos za' : 'Głos przeciw'}"]')`);
  const state = (p, idx) => p.evaluate(`(() => { const m = document.querySelectorAll('.anno-panel .anno-msg')[${idx}]; return { voters: (m.querySelector('.anno-voters') || {}).textContent || '', pressed: [...m.querySelectorAll('.anno-vote')].map((v) => v.getAttribute('aria-pressed') + ':' + v.textContent.trim()).join(' ') }; })()`);
  await vote(A, 1, 'up'); await A.waitFor(wasSaved, 5000);
  const s1 = await state(A, 1);
  await vote(B, 0, 'down'); await B.waitFor(wasSaved, 5000);
  const seenA = await A.waitFor(`/👎 Bartek/.test(document.querySelectorAll('.anno-panel .anno-msg')[0].innerText)`, 8000);
  const s2 = await state(A, 0);
  await vote(A, 1, 'down'); await A.waitFor(wasSaved, 5000);
  const s3 = await state(A, 1);
  await vote(A, 1, 'down'); await A.waitFor(wasSaved, 5000);
  const s4 = await state(A, 1);
  return { ok: /👍 Anna/.test(s1.voters) && /true:1/.test(s1.pressed) && seenA.ok && /👎 Bartek/.test(s2.voters) && /👎 Anna/.test(s3.voters) && !/Anna/.test(s4.voters) && !/true/.test(s4.pressed), afterUp: s1, otherSeen: s2, changed: s3, cancelled: s4 };
});

suite.add('F1-S08', 'Edycja i wycofanie wpisu', async () => {
  const { A, B } = S;
  await A.domClick(`[...document.querySelectorAll('.anno-panel .anno-msg')[0].querySelectorAll('button')].find((b) => b.textContent.trim() === 'Edytuj')`);
  await A.waitFor(`document.activeElement && document.activeElement.matches('textarea')`, 2000);
  await A.evaluate(`document.activeElement.select(); true`); await A.type('Tytuł karty za mały (poprawione)'); await A.key('Enter', { ctrl: true });
  const e = await A.waitFor(`/poprawione/.test(${panelText}) && /edytowano/.test(${panelText}) && ${wasSaved}`, 6000);
  await A.evaluate(`document.querySelector('.anno-panel textarea[aria-label="Odpowiedź"]').focus(); true`); await A.type('Wpis do wycofania'); await A.key('Enter', { ctrl: true });
  await A.waitFor(`/Wpis do wycofania/.test(${panelText}) && ${wasSaved}`, 6000);
  await A.domClick(`[...[...document.querySelectorAll('.anno-panel .anno-msg')].find((m) => /Wpis do wycofania/.test(m.innerText)).querySelectorAll('button')].find((b) => b.textContent.trim() === 'Wycofaj')`);
  const dlg = await A.waitFor(`/Wycofać wpis\\?/.test(${dlgText})`, 3000);
  await A.domClick(btnExact('Wycofaj', DLG));
  const r = await A.waitFor(`/Wpis wycofany przez autora/.test(${panelText}) && !/Wpis do wycofania/.test(${panelText}) && ${wasSaved}`, 6000);
  const seenB = await B.waitFor(`/Wpis wycofany przez autora/.test(${panelText}) && /poprawione/.test(${panelText})`, 10000);
  return { ok: e.ok && dlg.ok && r.ok && seenB.ok, edited: e.ok, dialog: dlg.ok, retracted: r.ok, seenByOther: seenB.ok };
});

suite.add('F1-S09', 'Filtry panelu', async () => {
  const { A, B } = S;
  await B.go('about:blank');
  await A.evaluate(`document.querySelector('.anno-panel textarea[aria-label="Odpowiedź"]').focus(); true`); await A.type('Nowy wpis pod nieobecność B'); await A.key('Enter', { ctrl: true });
  await A.waitFor(wasSaved, 6000);
  await B.go(url()); await openPanel(B);
  const f0 = await filtersState(B);
  const res = {};
  for (const name of ['Otwarte', 'Nowe', 'Decyzje agenta', 'Bez uchwytu', 'Wszystkie']) {
    await pickFilter(B, name);
    res[name] = { cards: await countCards(B), pressed: await B.evaluate(`[...document.querySelectorAll('.anno-filters button')].filter((b) => b.getAttribute('aria-pressed') === 'true').map((b) => b.textContent.trim())`) };
  }
  const counts = f0.map((f) => (/(\d+)/.exec(f.t) || [])[1]);
  await pickFilter(B, 'Otwarte');
  const ok = res['Otwarte'].cards === 4 && res['Nowe'].cards === 1 && res['Decyzje agenta'].cards === 0 && res['Bez uchwytu'].cards === 0 && res['Wszystkie'].cards === 4
    && Object.entries(res).every(([k, v]) => v.pressed.length === 1 && v.pressed[0].startsWith(k));
  return { ok, counters: counts, perFilter: res };
});

suite.add('F1-S10', 'Wątek bez uchwytu', async () => {
  const { A, B } = S;
  const html = ws.read(SLUG);
  const cut = html.replace(/\s*<button class="btn" type="button" data-proto-id="s1-usuwany">Do usunięcia<\/button>/, '');
  if (cut === html) throw new Error('nie znaleziono elementu do usunięcia');
  ws.write(SLUG, 'index.html', cut);
  await B.reload(); await openPanel(B);
  const f1 = await filtersState(B);
  await pickFilter(B, 'Bez uchwytu');
  const lost = await B.evaluate(panelText); const lostCards = await countCards(B);
  const pinOnPage = await B.evaluate(`!![...document.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].find((p) => /Ten przycisk zniknie/.test(p.getAttribute('aria-label') || ''))`);
  await pickFilter(B, 'Otwarte'); const open = await B.evaluate(panelText); const openCards = await countCards(B);
  await A.reload();
  return { ok: /Ten przycisk zniknie/.test(lost) && lostCards === 1 && !/Ten przycisk zniknie/.test(open) && openCards === 3 && /Bez uchwytu\s*1/.test(f1.map((f) => f.t).join(' | ')), filters: f1.map((f) => f.t), lostCards, openCards, pinOnPage };
});

suite.add('F1-S11', 'Offline i kolejka', async () => {
  const A = S.A;
  await A.offline(true);
  await replyIn(A, 1, 'Wpis offline 1');
  const off = await A.waitFor(`/Offline · 1 w kolejce/.test(${saveState})`, 8000);
  const q = await queueLength(A);
  const banner = await A.evaluate(bannerText);
  const t0 = Date.now(); await A.offline(false);
  const back = await A.waitFor(wasSaved, 40000, 200);
  const ops = await apiOps(srv.base, SLUG);
  const onServer = countText(ops.operations, 'Wpis offline 1') === 1;
  return { ok: off.ok && q === 1 && /Ponów teraz/.test(banner) && /Pobierz kolejkę/.test(banner) && back.ok && onServer && (await queueLength(A)) <= 0, queueWhileOffline: q, banner: banner.slice(0, 160), recoveryMs: Date.now() - t0 };
});

suite.add('F1-S12', 'Odrzucenie wpisu z kolejki', async () => {
  const A = S.A;
  const dev = await session(srv.base, 'dawid');
  await A.offline(true);
  await replyIn(A, 3, 'Odpowiedź do usuniętego');
  await A.waitFor(`/w kolejce/.test(${saveState})`, 8000);
  await replyIn(A, 4, 'Odpowiedź, która przejdzie');
  const q = await A.waitFor(`/Offline · 2 w kolejce/.test(${saveState})`, 8000);
  const del = await apiPost(dev, SLUG, { cid: cid('del'), type: 'delete', threadId: S.threads['s2-zapisz'], payload: {} });
  await A.offline(false);
  const r = await A.waitFor(`/Błąd zapisu \\(1\\)/.test(${saveState})`, 40000, 200);
  const ops = await apiOps(srv.base, SLUG);
  const passed = countText(ops.operations, 'która przejdzie') === 1; const rejected = countText(ops.operations, 'do usuniętego');
  const banner = await A.evaluate(bannerText);
  await A.domClick(`[...document.querySelectorAll('.anno-banners button')].find((b) => /Pokaż|odrzucone/i.test(b.textContent))`);
  const dlg = await A.waitFor(`/Odrzucone wpisy/.test(${dlgText})`, 4000);
  await A.key('Escape');
  return { ok: q.ok && del.status < 300 && r.ok && passed && rejected === 0 && /Serwer odrzucił 1 wpis/.test(banner) && dlg.ok, deleteStatus: del.status, banner: banner.slice(0, 200), dialogOpened: dlg.ok };
});

suite.add('F1-S13', 'Wygaśnięcie sesji po restarcie serwera', async () => {
  const A = S.A;
  await srv.stop(); await sleep(800);
  let off;
  try { await replyIn(A, 4, 'Wpis w czasie restartu'); off = await A.waitFor(`/w kolejce|Offline/.test(${saveState})`, 10000); }
  finally { await srv.restart(); }
  const exp = await A.waitFor(`/Sesja wygasła/.test(${saveState}) || /Zaloguj się ponownie/.test(${dlgText})`, 40000, 250);
  const q = await queueLength(A);
  const dlg = await A.waitFor(`/Zaloguj się ponownie/.test(${dlgText})`, 8000);
  if (!dlg.ok) await A.domClick(btn('Zaloguj', `document.querySelector('.anno-user-slot')`));
  await A.waitFor(`!!document.querySelector('#anno-login-password')`, 4000);
  const prefill = await A.evaluate(`document.querySelector('#anno-login-email').value`);
  if (!prefill) { await A.evaluate(`document.querySelector('#anno-login-email').focus(); true`); await A.type(mail('anna')); }
  await A.evaluate(`document.querySelector('#anno-login-password').focus(); true`); await A.type(secret('anna')); await A.key('Enter');
  await A.waitFor(`!${DLG}`, 8000);
  await A.waitFor(wasSaved, 15000);
  const ops = await apiOps(srv.base, SLUG);
  const onServer = countText(ops.operations, 'Wpis w czasie restartu') === 1;
  return { ok: off.ok && exp.ok && q === 1 && dlg.ok && prefill === mail('anna') && onServer, queueWhileExpired: q, emailPrefilled: prefill === mail('anna'), dialogShown: dlg.ok, onServer };
});

suite.add('F1-S14', 'Wymuszona zmiana hasła', async () => {
  const E = await c.page('E'); await E.go(url());
  await loginUIForced(E);
  const pw = await E.waitFor(`/Ustaw własne hasło/.test(${dlgText})`, 8000);
  const fields = await E.evaluate(`[...document.querySelectorAll('dialog[open] input')].map((i) => i.id)`);
  await E.key('Escape'); await E.key('Escape');
  const escKeepsDialog = await E.evaluate(dlgOpen);
  const chipText = await E.evaluate(`(document.querySelector('.anno-user-slot') || {}).textContent || ''`);
  // C + klik pod oknem wymuszonym: bez okienka komentarza, z komunikatem
  await E.domClick(commentBtn).catch(() => {});
  await E.domClick(byProto('s2-typ')).catch(() => {});
  const composer = await E.evaluate(`!!document.querySelector('.anno-composer-pop')`);
  const toast = await E.evaluate(`(document.body.innerText.match(/Najpierw ustaw własne hasło\\./) || [''])[0]`);
  const np = 'Nw' + Math.random().toString(36).slice(2, 12) + 'X9';
  for (const id of fields) { await E.evaluate(`document.getElementById(${JSON.stringify(id)}).focus(); true`); await E.type(np); }
  await E.key('Enter');
  const changed = await E.waitFor(`!${DLG} && ${wasSaved}`, 8000);
  await E.dispose();
  const restOk = pw.ok && fields.length === 2 && escKeepsDialog && !composer && /Najpierw ustaw własne hasło\./.test(toast) && changed.ok;
  const chipExact = /Ustaw własne hasło/.test(chipText);   // kontrakt (docs/prototype-contract/scenariusze-e2e.md, F1-S14): chip „Ustaw własne hasło”
  return { ok: restOk && chipExact, restOk, defectObserved: restOk && !chipExact && /Ustaw/.test(chipText), dialogShown: pw.ok, fields: fields.length, escKeepsDialog, chip: chipText, composerOpened: composer, toast, changed: changed.ok };
});
async function loginUIForced(E) {
  await E.domClick(btn('Zaloguj', `document.querySelector('.anno-user-slot')`));
  await E.waitFor(`!!document.querySelector('#anno-login-email')`, 4000);
  await E.evaluate(`document.querySelector('#anno-login-email').focus(); true`); await E.type(mail('ewa'));
  await E.evaluate(`document.querySelector('#anno-login-password').focus(); true`); await E.type(secret('default')); await E.key('Enter');
}

suite.add('F1-S15', 'Usunięcie wątku przez programistę', async () => {
  const D = await c.page('Dev'); await D.go(url());
  const l = await loginUI(D, 'dawid');
  const pinNums = () => D.evaluate(`[...document.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].map((x) => x.textContent.trim())`);
  const pinsBefore = await pinNums();
  await openThreadByPin(D, 4);
  await D.domClick(btn('Usuń wątek', `document.querySelector('.anno-panel')`));
  const t = await D.waitFor(`/Usunąć wątek #4\\?/.test(${dlgText})`, 4000);
  await D.domClick(btn('Usuń wątek', DLG));
  const gone = await D.waitFor(`![...document.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].some((x) => x.textContent.trim() === '4')`, 6000);
  const pinsLeft = await D.evaluate(`[...document.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].map((x) => x.textContent.trim())`);
  const ops = await apiOps(srv.base, SLUG);
  const del = ops.operations.some((o) => o.type === 'delete' && o.threadId === S.threads['s2-nazwa']);
  await D.dispose();
  const numberingKept = pinsBefore.includes('4') && JSON.stringify(pinsLeft) === JSON.stringify(pinsBefore.filter((x) => x !== '4'));   // numeracja pozostałych bez zmian
  return { ok: l.ok && t.ok && gone.ok && del && !pinsLeft.includes('4') && numberingKept, pinsBefore, numberingKept, pinsLeft, deleteInJournal: del };
});

suite.add('F1-S16', 'Motywy i szerokości', async () => {
  const A = S.A; const det = []; let restOk = true; let defect = false;
  const setTheme = async (theme) => {
    for (let i = 0; i < 3; i++) {
      if ((await A.evaluate(`document.documentElement.getAttribute('data-theme')`)) === theme) return true;
      await A.domClick(`[...document.querySelectorAll('button')].find((b) => /Motyw/.test(b.getAttribute('aria-label') || '') || /^Motyw/.test(b.textContent.trim()))`);
      await sleep(250);
    }
    return (await A.evaluate(`document.documentElement.getAttribute('data-theme')`)) === theme;
  };
  for (const theme of ['light', 'dark']) {
    for (const w of [1440, 900, 768, 375]) {
      await A.size(w, w < 600 ? 812 : 900); await A.reload();
      const set = await setTheme(theme);
      const o = [];
      // przepełnienie dokumentu albo wiersza paska (.doc-toolbar, .proto-toolbar): pasek ma się mieścić bez przewijania poziomego
      const overflow = () => A.evaluate(`(() => { const de = document.documentElement; const worst = [...document.querySelectorAll('.doc-toolbar, .proto-toolbar')].map((e) => ({ sw: e.scrollWidth, cw: e.clientWidth })).sort((a, b) => (b.sw - b.cw) - (a.sw - a.cw))[0]; return worst && worst.sw > worst.cw ? worst : { sw: de.scrollWidth, cw: de.clientWidth }; })()`);
      o.push(await overflow());
      await openPanel(A); await sleep(250); o.push(await overflow());
      await closePanelIfOpen(A); await A.evaluate(`window.scrollTo(0,0); true`);
      await commentMode(A, true); await A.domClick(byProto('s1-nowy'));
      const comp = await A.evaluate(`(() => { const c = document.querySelector('.anno-composer-pop'); if (!c) return null; const r = c.getBoundingClientRect(); return { left: Math.round(r.left), right: Math.round(r.right), vw: document.documentElement.clientWidth }; })()`);
      o.push(await overflow());
      await A.key('Escape'); await A.key('Escape');
      const restGood = set && comp && comp.left >= 0 && comp.right <= comp.vw;   // motyw i okienko w widoku; bez warunku defektu
      const overflowed = o.some((x) => x.sw > x.cw);
      if (!restGood) restOk = false;
      if (overflowed) defect = true;
      det.push(`${theme}/${w}:${set ? 'motyw' : 'BRAK-motywu'}/${o.map((x) => x.sw + '≤' + x.cw).join(',')}${comp ? '' : '/brak-okienka'}`);
    }
  }
  await A.size(1440); await A.reload();
  return { ok: restOk && !defect, restOk, defectObserved: restOk && defect, cases: det };
});

suite.add('F1-S17', 'Esc i widoczny fokus', async () => {
  const A = S.A;
  await A.evaluate(`window.scrollTo(0,0); true`); await commentMode(A, true); await A.domClick(byProto('s1-dalej'));
  const c1 = await A.evaluate(`!!document.querySelector('.anno-composer-pop')`);
  await A.key('Escape');
  const c2 = await A.evaluate(`!!document.querySelector('.anno-composer-pop')`);
  const focusBack = await A.evaluate(`document.activeElement && document.activeElement.getAttribute('data-proto-id')`);
  await A.key('Escape');
  const modeAfter = await A.evaluate(`(() => { const b = [...document.querySelectorAll('[role="group"][aria-label="Tryb"] button')].find((x) => /Komentarze/.test(x.textContent)); return b ? b.getAttribute('aria-pressed') : null; })()`);
  await openPanel(A); const p1 = await A.evaluate(panelIsOpen);
  await A.key('Escape'); const p2 = await A.evaluate(panelIsOpen);
  const focusAfterPanel = await A.evaluate(`document.activeElement ? (document.activeElement.textContent || '').trim().slice(0, 20) : null`);
  const fvOf = async (finder) => A.evaluate(`(() => { const el = ${finder}; if (!el) return null; el.focus({ focusVisible: true }); const cs = getComputedStyle(el); return { fv: el.matches(':focus-visible'), outline: cs.outlineStyle + ' ' + cs.outlineWidth }; })()`);
  const seg = await fvOf(`[...document.querySelectorAll('[aria-pressed="true"]')].find((b) => b.closest('.doc-toolbar'))`);
  const outline = await fvOf(`document.querySelector('.btn-outline')`);
  const visible = (x) => !!x && x.fv && /solid 2px/.test(x.outline);
  // kontrakt (scenariusze-e2e.md, F1-S17): Esc zamyka okienko i oddaje fokus elementowi, drugi Esc wyłącza tryb komentowania
  const restOk = c1 && !c2 && p1 && !p2 && /Wątki/.test(focusAfterPanel || '') && visible(seg) && visible(outline);
  const escContract = focusBack === 's1-dalej' && modeAfter === 'false';
  return { ok: restOk && escContract, restOk, defectObserved: restOk && modeAfter !== null && !escContract, modeAfter, composerOpened: c1, composerAfterEsc: c2, focusBackOn: focusBack, panelAfterEsc: p2, focusAfterPanel, activeSegment: seg, btnOutline: outline };
});

suite.add('F1-S18', 'Klawiatura na elementach', async () => {
  const A = S.A;
  await A.size(1440); await A.reload(); await commentMode(A, true);
  await A.evaluate(`${byProto('s1-nowy')}.focus(); true`); await A.key('Tab');
  const f1 = await A.evaluate(`document.activeElement.getAttribute('data-proto-id') + ' ' + document.activeElement.tagName`);
  await A.key('Enter');
  const c1 = await A.waitFor(`(document.querySelector('.anno-composer-pop .anno-composer-head') || {}).textContent`, 3000);
  await A.key('Escape');
  await A.evaluate(`${byProto('s1-nowy')}.focus(); true`); await A.key('Tab'); await A.key('Tab');
  const f2 = await A.evaluate(`document.activeElement.getAttribute('data-proto-id') + ' ' + document.activeElement.tagName`);
  const sy0 = await A.evaluate(`window.scrollY`);
  await A.key(' ');
  const c2 = await A.waitFor(`(document.querySelector('.anno-composer-pop .anno-composer-head') || {}).textContent`, 3000);
  const sy1 = await A.evaluate(`window.scrollY`);
  await closeAllComposer(A);
  return { ok: /^s1-karta DIV/.test(f1) && c1.ok && /s1-karta\s*$/.test(c1.v) && /^s1-karta-tytul H3/.test(f2) && c2.ok && /s1-karta-tytul/.test(c2.v) && sy0 === sy1, focus1: f1, focus2: f2, scrollBeforeAfter: [sy0, sy1] };
});
async function closeAllComposer(p) { for (let i = 0; i < 3; i++) await p.key('Escape'); await closePanelIfOpen(p); }

suite.add('F1-S19', 'Komentarz do ekranu', async () => {
  const A = S.A; await A.front();
  await commentMode(A, true);
  const display = await A.evaluate(`getComputedStyle(document.querySelector('#s2 .anno-screen-comment')).display`);
  await A.domClick(`document.querySelector('#s2 .anno-screen-comment')`);
  const head = await A.waitFor(`(document.querySelector('.anno-composer-pop .anno-composer-head') || {}).textContent`, 3000);
  await A.type('Uwaga do całego ekranu 2'); await A.key('Enter', { ctrl: true });
  await A.waitFor(wasSaved, 6000); await sleep(500);
  const op = journal().find((o) => o.type === 'create' && JSON.stringify(o.payload).includes('Uwaga do całego ekranu 2'));
  await closeAllComposer(A);
  const a = op && op.payload.anchor;
  return { ok: head.ok && /Cały ekran/.test(head.v) && !!a && a.kind === 'screen' && op.payload.screen === 's2' && a.x >= 0 && a.x <= 1 && a.y >= 0 && a.y <= 1 && typeof a.iteration === 'number' && display !== 'none', head: head.v, anchor: a, buttonDisplay: display };
});

suite.add('F1-S22', 'Wpisy v1 z localStorage', async () => {
  const A = S.A;
  const msg = (id, text, at) => ({ id, author: 'Recenzent', text, at });
  const ops1 = [
    { id: 'op-0001', type: 'create', threadId: 'thread-a', at: '2026-11-03T10:00:00.000Z', payload: { screen: 's1', anchor: 'div:2>div', label: 'Nowy', message: msg('msg-0001', 'Przycisk Nowy; czy ma otwierać formularz?', '2026-11-03T10:00:00.000Z') } },
    { id: 'op-0002', type: 'reply', threadId: 'thread-a', at: '2026-11-03T10:05:00.000Z', payload: { message: msg('msg-0002', 'Tak, formularz w szufladzie.', '2026-11-03T10:05:00.000Z') } },
    { id: 'op-0100', type: 'create', threadId: 'thread-c', at: '2026-11-03T11:00:00.000Z', payload: { screen: 's2', anchor: 'div', label: 'Formularz', message: msg('msg-0100', 'Drugi wątek v1 do ekranu 2', '2026-11-03T11:00:00.000Z') } },
  ];
  const v1 = JSON.stringify({ version: 1, prototypeId: SLUG, operations: ops1 });
  const KEY = `skillsync-prototype-comments:v1:${SLUG}`;
  const put = () => A.evaluate(`localStorage.setItem(${JSON.stringify(KEY)}, ${JSON.stringify(v1)}); true`);
  const before = await apiOps(srv.base, SLUG);
  await put();
  const held = [];
  await A.intercept(['*/_api/v1/prototypes/*/operations*'], async (ev) => { held.push(ev); });
  await A.send('Page.reload', { ignoreCache: true }); await sleep(1800);
  const bannerHeld = await A.evaluate(bannerText);
  await A.interceptOff(); for (const ev of held) await A.send('Fetch.continueRequest', { requestId: ev.requestId }).catch(() => {});
  const sendBtn = await A.waitFor(`!!${btn('Wyślij jako', `document.querySelector('.anno-banners')`)}`, 8000);
  await A.domClick(btn('Wyślij jako', `document.querySelector('.anno-banners')`));
  await A.waitFor(wasSaved, 10000); await sleep(800);
  const keyAfter = await A.evaluate(`localStorage.getItem(${JSON.stringify(KEY)})`);
  const after1 = await apiOps(srv.base, SLUG);
  const added = after1.operations.slice(before.operations.length);
  const t1 = { create: added.filter((o) => o.type === 'create').length, reply: added.filter((o) => o.type === 'reply').length };
  await put(); await A.reload();
  const send2 = await A.waitFor(`!!${btn('Wyślij jako', `document.querySelector('.anno-banners')`)}`, 8000);
  if (send2.ok) await A.domClick(btn('Wyślij jako', `document.querySelector('.anno-banners')`));
  const toast = await A.waitFor(`(document.body.innerText.match(/[^\\n]*(starej wersji|już na serwerze|Wysłano|wysłan)[^\\n]*/g) || []).join(' | ')`, 4000);
  await sleep(1500);
  const after2 = await apiOps(srv.base, SLUG);
  const dup = after2.operations.length - after1.operations.length;
  const sentCount = (/(\d+)/.exec(toast.v || '') || [])[1];
  await put(); await A.reload();
  await A.waitFor(`!!${btn('Pobierz plik', `document.querySelector('.anno-banners')`)}`, 6000);
  await A.send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: `${env.workdir}/downloads` }).catch(() => {});
  await A.domClick(btn('Pobierz plik', `document.querySelector('.anno-banners')`)); await sleep(800);
  const keyKept = await A.evaluate(`!!localStorage.getItem(${JSON.stringify(KEY)})`);
  const forget = await A.evaluate(`!!${btn('Usuń z przeglądarki', `document.querySelector('.anno-banners')`)}`);
  await A.evaluate(`localStorage.removeItem(${JSON.stringify(KEY)}); true`); await A.reload();
  const ok = /Wpisy ze starej wersji/.test(bannerHeld) && !/Wyślij jako/.test(bannerHeld) && sendBtn.ok && t1.create === 2 && t1.reply === 1 && keyAfter === null && dup === 0 && keyKept && forget;
  const countMatches = !(sentCount && Number(sentCount) !== dup);
  return { ok: ok && countMatches, sent: t1, keyRemovedAfterSend: keyAfter === null, resendNewOps: dup, downloadKeepsKey: keyKept, forgetButton: forget, messageCount: sentCount === undefined ? null : Number(sentCount), countMatchesServer: countMatches, toast: (toast.v || '').slice(0, 200) };
});

suite.add('F1-S20', 'Dwie karty, restart serwera', async () => {
  const A = S.A;
  const A2 = await c.page('A2', { shared: A.ctx }); await A2.go(url());
  await closeAllComposer(A);
  await srv.stop(); await sleep(800);
  try { await replyIn(A2, 1, 'Wpis z karty 2 w czasie restartu'); await A2.waitFor(`/w kolejce|Offline/.test(${saveState})`, 10000); }
  finally { await srv.restart(); }
  await A.front();
  await A.waitFor(`/Sesja wygasła/.test(${saveState}) || /Zaloguj się ponownie/.test(${dlgText})`, 40000, 250);
  if (!(await A.evaluate(`!!document.querySelector('#anno-login-password')`))) await A.domClick(btn('Zaloguj', `document.querySelector('.anno-user-slot')`));
  await A.waitFor(`!!document.querySelector('#anno-login-password')`, 4000);
  if (!(await A.evaluate(`document.querySelector('#anno-login-email').value`))) { await A.evaluate(`document.querySelector('#anno-login-email').focus(); true`); await A.type(mail('anna')); }
  await A.evaluate(`document.querySelector('#anno-login-password').focus(); true`); await A.type(secret('anna')); await A.key('Enter');
  await A.waitFor(`!${DLG}`, 8000);
  const t0 = Date.now(); let onServer = -1;
  while (Date.now() - t0 < 45000) { const d = await apiOps(srv.base, SLUG); if (countText(d.operations, 'Wpis z karty 2') >= 1) { onServer = Date.now() - t0; break; } await sleep(500); }
  await sleep(1500);
  const rej = await A2.evaluate(`Object.keys(localStorage).filter((k) => k.includes('rejected')).map((k) => k + ':' + localStorage.getItem(k)).join(' ').slice(0, 300)`);
  const chipA2 = await chip(A2);
  const n = countText((await apiOps(srv.base, SLUG)).operations, 'Wpis z karty 2');
  await A2.dispose();
  return { ok: onServer >= 0 && n === 1 && !/karty 2/.test(rej) && !/Błąd zapisu/.test(chipA2), msToServer: onServer, copiesOnServer: n, chipOtherTab: chipA2 };
});

suite.add('F1-S21', 'Dwie karty offline', async () => {
  const A = S.A; await closeAllComposer(A); await A.reload();
  const A3 = await c.page('A3', { shared: A.ctx }); await A3.go(url());
  await A.offline(true); await A3.offline(true);
  await replyIn(A, 1, 'Offline z karty 1'); await A.waitFor(`/w kolejce/.test(${saveState})`, 8000);
  await replyIn(A3, 1, 'Offline z karty 3'); await A3.waitFor(`/w kolejce/.test(${saveState})`, 8000);
  const c1 = await chip(A); const c3 = await chip(A3);
  await A.offline(false); await A3.offline(false);
  await A.waitFor(wasSaved, 40000, 250); await A3.waitFor(wasSaved, 40000, 250);
  await sleep(6000);
  const d = await apiOps(srv.base, SLUG);
  const n1 = countText(d.operations, 'Offline z karty 1'); const n3 = countText(d.operations, 'Offline z karty 3');
  const f1 = countText(journal(), 'Offline z karty 1'); const f3 = countText(journal(), 'Offline z karty 3');
  await A3.dispose();
  return { ok: /Offline · 2 w kolejce/.test(c1) && /Offline · 2 w kolejce/.test(c3) && n1 === 1 && n3 === 1 && f1 === 1 && f3 === 1, chipsOffline: [c1, c3], apiCopies: [n1, n3], fileCopies: [f1, f3] };
});

suite.add('F1-S23', 'Migracja kopii prototypu v1', async () => {
  const slug = 'migr';
  const dry = await proto(ws, 'migrate', slug, '--dry-run');
  const idsBefore = ws.read(slug).match(/data-proto-id=/g)?.length || 0;
  const mig = await proto(ws, 'migrate', slug);
  await proto(ws, 'ids', slug, '--fix');
  const ids = await proto(ws, 'ids', slug, '--check');
  const html = ws.read(slug);
  const idCount = html.match(/data-proto-id=/g)?.length || 0;
  const listed = await proto(ws, 'list');
  const entry = listed.json?.prototypes?.find((p) => p.slug === slug);
  const P = await c.page('migr'); const e0 = errors.length;
  await P.go(`${srv.base}/${slug}/`);
  const sections = await P.evaluate(`document.querySelectorAll('section.screen').length`);
  const chipText = await chip(P);
  const js = errors.slice(e0).filter((e) => e.kind === 'exception' || e.kind === 'console.error');
  await P.dispose();
  return { ok: dry.code === 0 && mig.code === 0 && ids.code === 0 && idCount >= 28 * 3 && sections === 28 && !!entry && entry.commentsFormat === 2 && js.length === 0 && /Zaloguj|Tylko|komentować/.test(chipText), dryRunExit: dry.code, migrateExit: mig.code, idsCheckExit: ids.code, dataProtoIds: idCount, screens: sections, format: entry && (entry.commentsFormat ?? entry.format), exceptions: js.length };
}, { critical: false });

suite.add('F1-S99', 'Konsola', async () => consoleVerdict());

await suite.main();
