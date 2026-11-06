// F2: iteracje, porównanie, przywrócenie ekranu, przełącznik ekranów (F01 … F2-S99). Prototyp `demo`, konta anna (komentujący), dawid (programista).
import { spawn, spawnSync } from 'node:child_process';
import { mkdirSync, cpSync, readFileSync as rf } from 'node:fs';
import { createHash, randomBytes } from 'node:crypto';
import {
  Suite, sleep, workspace, startServer, connect, errors, env, PORT1, session, apiOps, apiPost, apiRaw, secret, mail, cid, proto, createThread,
  btn, btnExact, byProto, DLG, dlgText, dlgOpen, saveState, panelText, bannerText, wasSaved, openPanel, closePanelIfOpen, loginUI, commentMode,
  pickFilter, consoleVerdict, sessionPage, readFileSync, writeFileSync,
} from './lib.mjs';

const suite = new Suite('f2');
const SLUG = 'demo';
let ws; let srv; let c; let dev; let ann;
const url = () => `${srv.base}/${SLUG}/`;
const sect = (h, id) => new RegExp('<section[^>]*id="' + id + '"[\\s\\S]*?</section>').exec(h)[0];
const sha = (s) => createHash('sha256').update(s).digest('hex').slice(0, 12);
const ops = async () => (await apiOps(srv.base, SLUG)).operations;
const cur = () => ws.read(SLUG);
const openRestore = (p) => p.domClick(`[...document.querySelectorAll('section#s2 button')].find((b) => b.textContent.includes('Przywróć') && b.getClientRects().length > 0)`, 'Przywróć… s2');
const restoreBtnExists = (p) => p.evaluate(`!![...document.querySelectorAll('section#s2 button')].find((b) => b.textContent.includes('Przywróć'))`);
const pickFrom = (p, n) => p.evaluate(`(() => { const s = document.querySelector('#anno-restore-from'); s.value = '${n}'; s.dispatchEvent(new Event('change', { bubbles: true })); return s.value; })()`);
const submitBtn = `[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent.includes('Przywróć ekran'))`;
const submitState = (p) => p.evaluate(`(() => { const x = ${submitBtn}; return x ? { disabled: x.disabled, ariaDisabled: x.getAttribute('aria-disabled') } : null; })()`);
const fillReason = async (p, text) => { await p.evaluate(`document.querySelector('#anno-restore-why').focus(); true`); await p.type(text); };
const restoreApi = (s, body) => apiRaw(s, 'POST', `/_api/v1/prototypes/${SLUG}/restore`, body);
const ulid = () => { const a = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'; const b = randomBytes(26); return [...b].map((x) => a[x % 32]).join(''); };

suite.setup(async () => {
  ws = workspace('f2');
  srv = await startServer(ws, { port: PORT1 });
  c = await connect();
  dev = await session(srv.base, 'dawid'); ann = await session(srv.base, 'anna');
  for (const [key, id, text] of [['T1', 's2-nazwa', 'F2 seed T1 pole nazwa'], ['T2', 's2-zapisz', 'F2 seed T2 przycisk zapisz'], ['T3', 's2-typ', 'F2 seed T3 pole typ']]) {
    const r = await createThread(ann, SLUG, 's2', id, text);
    if (r.status !== 201) throw new Error('zasiew wątku ' + key + ': ' + r.status);
  }
  const ids = await proto(ws, 'ids', SLUG, '--check');
  if (ids.code !== 0) await proto(ws, 'ids', SLUG, '--fix');
  const it = await proto(ws, 'iterate', SLUG);
  if (it.code !== 0) throw new Error('iterate: ' + it.out.slice(0, 200));
  return {};
});
suite.teardown(async () => { if (c) await c.closeAll(); if (srv) await srv.stop(); });

suite.add('F01', 'Zamrożona iteracja', async () => {
  const p = (await sessionPage(c, 'dawid', srv.base, 'dawid')).p;
  await p.go(`${srv.base}/${SLUG}/iterations/1/`);
  const attrs = await p.evaluate(`({ iteration: document.documentElement.hasAttribute('data-prototype-iteration'), frozen: document.documentElement.hasAttribute('data-prototype-frozen') })`);
  const label = await p.evaluate(`(document.querySelector('.anno-iteration') || {}).textContent || ''`);
  const bannerOk = await p.waitFor(`/zamrożona · tylko do odczytu/.test(${bannerText}) && /Wróć do bieżącej/.test(${bannerText})`, 6000);
  await commentMode(p, true).catch(() => {});
  await p.clickEl(byProto('s2-nazwa')).catch(() => {});
  const composer = await p.evaluate(`!!document.querySelector('.anno-composer-pop')`);
  await p.dispose();
  return { ok: attrs.iteration && attrs.frozen && /Iteracja 1 · zamrożona/.test(label) && bannerOk.ok && !composer, attrs, label, composerOpened: composer };
});

suite.add('F02', 'Pokaż w iteracji N', async () => {
  const html = cur();
  ws.write(SLUG, 'index.html', html.replace('data-proto-id="s2-typ"', 'data-proto-id="s2-typ-nowy"'));
  const p = (await sessionPage(c, 'dawid', srv.base, 'dawid')).p;
  await p.go(url()); await openPanel(p); await pickFilter(p, 'Bez uchwytu');
  await p.domClick(`[...document.querySelectorAll('.anno-panel .anno-card')].find((e) => /F2 seed T3/.test(e.innerText))`, 'karta T3');
  const link = await p.waitFor(`!!document.querySelector('a.anno-iter-link')`, 5000);
  const info = link.ok ? await p.evaluate(`({ text: document.querySelector('a.anno-iter-link').textContent.trim(), href: document.querySelector('a.anno-iter-link').getAttribute('href') })`) : null;
  await p.dispose();
  return { ok: link.ok && /Pokaż w iteracji 1/.test(info.text) && /iterations\/1\/index\.html#thread=th-/.test(info.href || ''), link: info };
});

suite.add('F03', 'Porównaj z iteracją', async () => {
  const it2 = await proto(ws, 'iterate', SLUG);
  const p = await c.page('anon'); await p.go(url());
  await p.evaluate(`[...document.querySelectorAll('button')].find((x) => x.textContent.includes('Porównaj z iteracją') && x.getClientRects().length > 0).focus(); true`);
  await p.key('Enter');
  const two = await p.waitFor(`document.querySelectorAll('iframe[src*="embed=compare"]').length >= 2`, 8000);
  await sleep(1200);
  const frames = await p.evaluate(`[...document.querySelectorAll('iframe')].map((f) => { let d = null; try { d = f.contentDocument; } catch (e) { /* inny origin */ } return { src: f.getAttribute('src'), bar: !!(d && d.querySelector('.anno-save, .anno-threads-btn, .anno-panel, .anno-user-slot, .proto-screens')) }; })`);
  const select = await p.evaluate(`(() => { const s = document.querySelector('.anno-compare-pick select'); return s ? { value: s.value, opts: [...s.options].map((o) => o.value) } : null; })()`);
  let changed = null;
  if (select && select.opts.length > 1) {
    const other = select.opts.find((v) => v !== select.value);
    await p.evaluate(`(() => { const s = document.querySelector('.anno-compare-pick select'); s.value = ${JSON.stringify(other)}; s.dispatchEvent(new Event('change', { bubbles: true })); })()`);
    await sleep(1200);
    changed = await p.evaluate(`[...document.querySelectorAll('iframe[src*="embed=compare"]')].map((f) => f.getAttribute('src'))`);
  }
  await p.key('Escape'); await sleep(500);
  const closed = await p.evaluate(`!document.querySelector('.anno-compare-pick')`);
  const focusBack = await p.evaluate(`!!document.activeElement && ((document.activeElement.textContent || '') + (document.activeElement.getAttribute('aria-label') || '')).includes('Porównaj')`);
  const focusDesc = await p.evaluate(`document.activeElement ? document.activeElement.tagName + ':' + (document.activeElement.textContent || '').trim().slice(0, 40) : null`);
  await p.dispose();
  return { ok: two.ok && frames.filter((f) => /embed=compare/.test(f.src || '')).length === 2 && frames.every((f) => !f.bar) && Array.isArray(changed) && changed.length === 2 && JSON.stringify(changed) !== JSON.stringify(frames.map((f) => f.src).filter((s) => /embed=compare/.test(s || ''))) && closed && focusBack, frames: frames.length, select, changed, closedByEsc: closed, focusBack, focusDesc, secondIterationExit: it2.code };
});

suite.add('F04', 'Przywróć ekran', async () => {
  const before = cur(); const it1 = rf(ws.file(SLUG, 'iterations/1/index.html'), 'utf8');
  const p = (await sessionPage(c, 'dawid', srv.base, 'dawid')).p; await p.go(url());
  await openRestore(p); await p.waitFor(dlgOpen, 4000);
  await pickFrom(p, 1); await fillReason(p, 'F04 powrót do pierwszej wersji ekranu');
  const effects = await p.waitFor(`document.querySelectorAll('.anno-effects li').length > 0`, 6000);
  const n0 = (await ops()).length;
  await p.domClick(submitBtn);
  await p.waitFor(`!${DLG}`, 10000); await sleep(800);
  const added = (await ops()).slice(n0).map((o) => o.type); const after = cur();
  await p.dispose();
  return { ok: effects.ok && added.includes('restore') && sha(sect(after, 's2')) === sha(sect(it1, 's2')) && before.replace(sect(before, 's2'), '') === after.replace(sect(after, 's2'), ''), addedOps: added, s2EqualsIteration1: sha(sect(after, 's2')) === sha(sect(it1, 's2')), restOfFileUnchanged: before.replace(sect(before, 's2'), '') === after.replace(sect(after, 's2'), '') };
});

suite.add('F05', 'Brak Przywróć dla komentującego', async () => {
  const p = (await sessionPage(c, 'anna', srv.base, 'anna')).p; await p.go(url());
  const has = await restoreBtnExists(p);
  const screens = await p.evaluate(`document.querySelectorAll('section.screen').length`);   // strona ma treść: brak przycisku nie wynika z pustej strony
  const ver = (await apiOps(srv.base, SLUG)).version;
  const r = await restoreApi(ann, { cid: cid('r'), screen: 's2', from: 1, reason: 'F05 komentujący nie może', version: ver });
  await p.dispose();
  return { ok: !has && screens > 0 && r.status === 403, screens, restoreButton: has, apiStatus: r.status };
});

suite.add('F06-A', 'Przywrócenie przez świeżą kartę', async () => {
  ws.write(SLUG, 'index.html', cur().replace('Pierwsza', 'Druga wersja')); // edycja s2 na dysku
  S.b = (await sessionPage(c, 'dawid-B', srv.base, 'dawid')).p; await S.b.go(url());   // stara karta (widok sprzed kolejnej zmiany)
  await openRestore(S.b); await S.b.waitFor(dlgOpen, 4000); await pickFrom(S.b, 1); await fillReason(S.b, 'F06 karta B stara wersja');
  ws.write(SLUG, 'index.html', cur().replace('Druga wersja', 'Trzecia wersja'));          // zmiana po wczytaniu karty B
  const it1 = rf(ws.file(SLUG, 'iterations/1/index.html'), 'utf8');
  const A = (await sessionPage(c, 'dawid-A', srv.base, 'dawid')).p; await A.go(url());
  await openRestore(A); await A.waitFor(dlgOpen, 4000); await pickFrom(A, 1); await fillReason(A, 'F06 karta A świeża');
  const pre = cur(); const n0 = (await ops()).length;
  await A.domClick(submitBtn);
  await A.waitFor(`!${DLG}`, 10000); await sleep(800);
  const post = cur(); const added = (await ops()).slice(n0).map((o) => o.type);
  await A.dispose();
  return { ok: added.includes('restore') && sha(sect(post, 's2')) === sha(sect(it1, 's2')) && pre.replace(sect(pre, 's2'), '') === post.replace(sect(post, 's2'), ''), addedOps: added };
});

suite.add('F06-B', 'Stara karta po zmianie', async () => {
  const B = S.b; await B.front();
  const banner = await B.waitFor(`/Prototyp zmieniono, przeładuj/.test(document.body.innerText)`, 40000);
  const st = await submitState(B);
  const n0 = (await ops()).length;
  await B.evaluate(`(() => { const x = ${submitBtn}; if (x && x.getAttribute('aria-disabled') !== 'true' && !x.disabled) x.click(); return true; })()`); await sleep(1200);
  const n1 = (await ops()).length;
  await B.dispose();
  return { ok: banner.ok && !!st && (st.disabled === true || st.ariaDisabled === 'true') && n1 === n0, banner: banner.ok, submit: st, opsAdded: n1 - n0 };
});
const S = {};

suite.add('D27-UI', 'Przywrócenie identycznego ekranu', async () => {
  const p = (await sessionPage(c, 'dawid-C', srv.base, 'dawid')).p; await p.go(url());
  await openRestore(p); await p.waitFor(dlgOpen, 4000);
  await pickFrom(p, 1); await sleep(900); await fillReason(p, 'D27 identyczny');
  const msg = await p.waitFor(`/jest taki sam jak bieżący/.test(${dlgText})`, 5000);
  const same = await submitState(p);
  const n0 = (await ops()).length;
  await p.evaluate(`(() => { const x = ${submitBtn}; if (x && !x.disabled) x.click(); return true; })()`); await sleep(1000);
  const n1 = (await ops()).length;
  const options = await p.evaluate(`[...document.querySelectorAll('#anno-restore-from option')].map((o) => o.value)`);
  await pickFrom(p, options.find((v) => v !== '1') || '2'); await sleep(1000);
  const diff = await submitState(p);
  await p.dispose();
  return { ok: msg.ok && !!same && same.disabled === true && same.ariaDisabled === 'true' && n1 === n0 && !!diff && diff.disabled === false, sameChoice: same, opsAdded: n1 - n0, otherChoice: diff, options };
});

suite.add('D27-API', 'Identyczny restore przez API', async () => {
  const lst = await apiOps(srv.base, SLUG);
  const n0 = lst.operations.length;
  const r = await restoreApi(dev, { cid: cid('r'), screen: 's2', from: 1, reason: 'D27 identyczny przez API', version: lst.version });
  const n1 = (await ops()).length;
  return { ok: r.status === 200 && r.json && r.json.unchanged === true && !!r.json.version && n1 === n0, httpStatus: r.status, unchanged: r.json && r.json.unchanged, opsAdded: n1 - n0 };
}, { critical: false });

suite.add('F2-K38', 'Pinezki po przywróceniu', async () => {
  const lst = await apiOps(srv.base, SLUG);
  const r = await restoreApi(dev, { cid: cid('r'), screen: 's2', from: 2, reason: 'K38 przywrócenie z iteracji 2', version: lst.version });
  const p = await c.page('anon'); await p.go(url());
  const pinsOnS2 = await p.evaluate(`[...document.querySelectorAll('#s2 button.anno-pin:not(.anno-pin-static)')].length`);
  await openPanel(p); await pickFilter(p, 'Bez uchwytu');
  await p.domClick(`[...document.querySelectorAll('.anno-panel .anno-card')].find((e) => /F2 seed T3/.test(e.innerText))`, 'karta T3').catch(() => {});
  const link = await p.waitFor(`(document.querySelector('a.anno-iter-link') || {}).textContent`, 5000);
  await p.dispose();
  return { ok: r.status === 201 && pinsOnS2 >= 2 && link.ok && /Pokaż w iteracji \d/.test(link.v), restoreStatus: r.status, pinsOnS2, link: link.v };
});

suite.add('F07', 'Dry-run i odtworzenie restore', async () => {
  const hashes = () => ['index.html', 'comments.json'].map((f) => sha(ws.read(SLUG, f))).join('/');
  const h0 = hashes();
  const dry = await proto(ws, 'restore', SLUG, '--screen', 's2', '--from', '1', '--reason', 'F07 próba', '--who', mail('dawid'), '--dry-run');
  const h1 = hashes();
  const resume = await proto(ws, 'restore', SLUG, '--resume');
  await srv.restart();
  const h2 = hashes();
  // dry-run bez zapisu, resume bez znacznika i restart bez zmian plików są sprawdzane; odtworzenie przerwanego restore („dokończono przywrócenie ekranu s2”)
  // wymaga znacznika wewnętrznego silnika, którego nie da się wywołać z UI ani z publicznego CLI: ten scenariusz nie jest więc zaliczeniem PASS
  const partial = dry.code === 0 && h0 === h1 && resume.code === 0 && h1 === h2;
  return { status: partial ? 'NOT_RUN' : 'FAIL', reason: 'odtworzenie przerwanego przywracania nie jest wywoływalne bez UI i bez publicznego CLI (tylko znacznik wewnętrzny silnika); sprawdzone częściowo: dry-run bez zapisu, resume bez znacznika, restart serwera bez zmian plików', partialChecksOk: partial, dryRunExit: dry.code, dryRunWrites: h0 !== h1, resumeResult: resume.json && resume.json.resume };
}, { critical: false });

suite.add('F08', 'Sterownik scalania git', async () => {
  const dir = `${ws.dir}/merge-f08`; mkdirSync(dir, { recursive: true });
  const git = (...a) => spawnSync('git', a, { cwd: dir, encoding: 'utf8', env: { ...process.env, GIT_AUTHOR_NAME: 'T', GIT_AUTHOR_EMAIL: 't@example.com', GIT_COMMITTER_NAME: 'T', GIT_COMMITTER_EMAIL: 't@example.com' } });
  git('init', '-q', '-b', 'main');
  cpSync(ws.file(SLUG, 'comments.json'), `${dir}/comments.json`);
  writeFileSync(`${dir}/.gitattributes`, 'comments.json merge=skillsync-comments\n');
  git('config', 'merge.skillsync-comments.driver', `python3 ${env.cli} prototype merge-comments --driver %O %A %B`);
  git('add', '.'); git('commit', '-q', '-m', 'S0');
  const base = rf(`${dir}/comments.json`, 'utf8').trimEnd().split('\n');
  const total0 = base.filter((l) => l.startsWith('{"id"')).length;
  const mk = (tag) => { const th = 'th-' + ulid(); return `{"id":"op-${ulid()}","type":"create","threadId":"${th}","at":"2026-11-05T12:0${tag}:00.000Z","by":"${mail('anna')}","cid":"c-${ulid()}","payload":{"message":{"id":"m-${ulid()}","text":"Gałąź ${tag}"},"screen":"s1","anchor":{"kind":"screen","x":0.5,"y":0.1,"iteration":1}}}`; };
  const withOp = (line) => [...base.slice(0, -1).map((l, i, a) => (l.endsWith(',') || !l.startsWith('{"id"') ? l : l + ',')), line, base[base.length - 1]];
  const fix = (lines) => lines.map((l, i) => { if (!l.startsWith('{"id"')) return l; const next = lines.slice(i + 1).some((x) => x.startsWith('{"id"')); return l.replace(/,$/, '') + (next ? ',' : ''); }).join('\n') + '\n';
  git('checkout', '-q', '-b', 'b2'); writeFileSync(`${dir}/comments.json`, fix(withOp(mk('2')))); git('commit', '-qam', 'Y');
  git('checkout', '-q', 'main'); writeFileSync(`${dir}/comments.json`, fix(withOp(mk('1')))); git('commit', '-qam', 'X');
  const merge = git('merge', '-q', 'b2', '-m', 'merge');
  const lines = rf(`${dir}/comments.json`, 'utf8').split('\n').filter((l) => l.startsWith('{"id"'));
  const idsU = new Set(lines.map((l) => JSON.parse(l.replace(/,$/, '')).id));
  return { ok: merge.status === 0 && lines.length === total0 + 2 && idsU.size === lines.length, mergeExit: merge.status, operations: lines.length, unique: idsU.size, expected: total0 + 2 };
}, { critical: false });

suite.add('F2-migrate', 'Migracja silnika F1→F2', async () => {
  const slug = 'delta';
  const first = await proto(ws, 'migrate', slug);
  const second = await proto(ws, 'migrate', slug);
  const steps = JSON.stringify(first.json || {});
  const again = JSON.stringify(second.json || {});
  return { ok: first.code === 0 && second.code === 0 && /already_migrated/.test(again) && /copy_engine/.test(steps) && /theme_lock_meta/.test(steps), firstExit: first.code, secondExit: second.code, stepsFirst: steps.slice(0, 300), stepsSecond: again.slice(0, 200) };
}, { critical: false });

suite.add('F2-przełącznik', 'Przełącznik ekranów na serwowanym prototypie', async () => {
  const p = await c.page('switch'); await p.go(url());
  const MAIN = `document.querySelector('.proto-screens-main')`;
  const r = {};
  await p.evaluate(`${MAIN}.focus(); true`); await p.key('Enter');
  r.open = await p.evaluate(`${MAIN}.getAttribute('aria-expanded') === 'true'`);
  r.focusSearch = await p.evaluate(`document.activeElement && document.activeElement.classList.contains('proto-screens-search')`);
  await p.key('ArrowDown'); r.activedescendant = await p.evaluate(`!!document.querySelector('.proto-screens-search').getAttribute('aria-activedescendant')`);
  await p.type('ab'); await p.key('Home'); r.homeInText = await p.evaluate(`document.querySelector('.proto-screens-search').selectionStart === 0`);
  await p.key('End'); r.endInText = await p.evaluate(`document.querySelector('.proto-screens-search').selectionStart === document.querySelector('.proto-screens-search').value.length`);
  await p.key('ArrowLeft'); r.leftInText = await p.evaluate(`document.querySelector('.proto-screens-search').selectionStart === document.querySelector('.proto-screens-search').value.length - 1`);
  await p.evaluate(`(() => { const s = document.querySelector('.proto-screens-search'); s.value = ''; s.dispatchEvent(new Event('input', { bubbles: true })); return 1; })()`);
  await p.key('End'); r.endEmpty = await p.evaluate(`document.querySelector('.proto-screens-search').getAttribute('aria-activedescendant') || ''`);
  await p.key('Home'); r.homeEmpty = await p.evaluate(`document.querySelector('.proto-screens-search').getAttribute('aria-activedescendant') || ''`);
  r.endHomeDiffer = !!r.endEmpty && !!r.homeEmpty && r.endEmpty !== r.homeEmpty;
  await p.key('Escape'); r.escClosed = await p.evaluate(`${MAIN}.getAttribute('aria-expanded') === 'false'`);
  r.focusOnMain = await p.evaluate(`document.activeElement === ${MAIN}`);
  r.prevDisabled = await p.evaluate(`document.querySelector('[data-screen-step="prev"]').getAttribute('aria-disabled') === 'true' && !document.querySelector('[data-screen-step="prev"]').disabled`);
  // Tab z listy na „›”; na ostatnim ekranie › ma aria-disabled
  await p.key('g'); await p.waitFor(`${MAIN}.getAttribute('aria-expanded') === 'true'`, 2000);
  await p.key('Tab'); r.tabToNext = await p.evaluate(`document.activeElement && document.activeElement.getAttribute('data-screen-step') === 'next'`);
  await p.evaluate(`document.querySelector('[data-screen-step="next"]').click(); true`); await sleep(600);
  r.nextDisabledOnLast = await p.evaluate(`document.querySelector('[data-screen-step="next"]').getAttribute('aria-disabled') === 'true' && !document.querySelector('[data-screen-step="next"]').disabled`);
  await p.media('prefers-reduced-motion', 'reduce');
  await p.evaluate(`document.querySelector('[data-screen-step="prev"]').click(); true`); await sleep(120);
  r.reducedMotionInstant = await p.evaluate(`Math.abs(document.querySelector('#s1').getBoundingClientRect().top) < 220`);
  await p.dispose();
  const ok = r.open && r.focusSearch && r.activedescendant && r.homeInText && r.endInText && r.leftInText && r.endHomeDiffer && r.escClosed && r.focusOnMain && r.prevDisabled && r.tabToNext && r.nextDisabledOnLast && r.reducedMotionInstant;
  return { ok, ...r };
});

suite.add('E1-2', 'Tab i Shift+Tab z listy ekranów', async () => {
  const p = await c.page('tab'); await p.go(url());
  const MAIN = `document.querySelector('.proto-screens-main')`;
  await p.evaluate(`${MAIN}.focus(); true`); await p.key('Enter'); await p.waitFor(`${MAIN}.getAttribute('aria-expanded') === 'true'`, 2000);
  await p.key('Tab');
  const t = await p.evaluate(`({ closed: ${MAIN}.getAttribute('aria-expanded') === 'false', next: document.activeElement.getAttribute('data-screen-step') })`);
  await p.evaluate(`${MAIN}.focus(); true`); await p.key('Enter'); await p.waitFor(`${MAIN}.getAttribute('aria-expanded') === 'true'`, 2000);
  await p.key('Tab', { shift: true });
  const st = await p.evaluate(`({ closed: ${MAIN}.getAttribute('aria-expanded') === 'false', main: document.activeElement === ${MAIN} })`);
  await p.dispose();
  return { ok: t.closed && t.next === 'next' && st.closed && st.main, tab: t, shiftTab: st };
});

suite.add('E1-5', 'Podpowiedzi i blokada Przywróć', async () => {
  const D = (await sessionPage(c, 'dawid-E', srv.base, 'dawid')).p; await D.go(url());
  await openRestore(D); await D.waitFor(dlgOpen, 4000);
  const hintBefore = await D.evaluate(`(document.querySelector('#anno-restore-hint') || {}).textContent || null`);
  await D.waitFor(`/Podaj powód/.test((document.querySelector('#anno-restore-hint') || {}).textContent || '')`, 5000);
  const hint = await D.evaluate(`(document.querySelector('#anno-restore-hint') || {}).textContent || null`);
  const described = await D.evaluate(`(${submitBtn} || {getAttribute: () => null}).getAttribute('aria-describedby')`);
  const n0 = (await ops()).length;
  await D.evaluate(`document.querySelector('#anno-restore-why').focus(); true`); await D.key('Enter', { ctrl: true }); await sleep(800);
  const n1 = (await ops()).length;
  await D.key('Escape'); await sleep(300);
  // zmiana pliku poza s1 po wczytaniu strony → nagłówek s2: aria-disabled z powodem
  ws.write(SLUG, 'index.html', cur().replace('Pierwsza', 'Czwarta wersja').replace('Trzecia wersja', 'Czwarta wersja'));
  await D.waitFor(`/Prototyp zmieniono, przeładuj/.test(document.body.innerText)`, 40000);
  const head = await D.evaluate(`(() => { const b = [...document.querySelectorAll('section#s2 button')].find((x) => x.textContent.includes('Przywróć')); if (!b) return null; const d = b.getAttribute('aria-describedby'); return { ariaDisabled: b.getAttribute('aria-disabled'), reason: d ? ((document.getElementById(d) || {}).textContent || '') : (b.title || '') }; })()`);
  await openRestore(D).catch(() => {}); await sleep(500);
  const opens = await D.evaluate(dlgOpen);
  await D.evaluate(`(() => { const b = [...document.querySelectorAll('section#s2 button')].find((x) => x.textContent.includes('Przywróć')); b.focus(); return 1; })()`); await D.key('Enter'); await sleep(400);
  const opensEnter = await D.evaluate(dlgOpen);
  await D.dispose();
  return { ok: !!hintBefore && /Podaj powód przywrócenia\./.test(hint || '') && !!described && n1 === n0 && !!head && head.ariaDisabled === 'true' && /Prototyp zmieniono od wczytania strony: przeładuj\./.test(head.reason || '') && !opens && !opensEnter, hintBefore, hint, ariaDescribedBy: !!described, opsAdded: n1 - n0, header: head, opensOnClick: opens, opensOnEnter: opensEnter };
});

suite.add('E2-1', 'Ponowienie podglądu skutków', async () => {
  ws.write(SLUG, 'index.html', cur()); // bez zmian; nowa karta ma świeży widok
  const D = (await sessionPage(c, 'dawid-E2', srv.base, 'dawid')).p; await D.go(url());
  let blocked = true;
  await D.intercept(['*/restore-preview*'], async (ev, api) => { if (blocked) api.fulfill(503, { error: 'unavailable' }); else api.continue(); });
  await openRestore(D); await D.waitFor(dlgOpen, 4000); await pickFrom(D, 1); await fillReason(D, 'E2-1 podgląd');
  const msg = await D.waitFor(`/Nie udało się pobrać podglądu/.test(${dlgText})`, 8000);
  const st = await submitState(D);
  const retry = await D.evaluate(`!!${btn('Ponów podgląd', DLG)}`);
  blocked = false;
  await D.domClick(btn('Ponów podgląd', DLG));
  const effects = await D.waitFor(`document.querySelectorAll('.anno-effects li').length > 0`, 8000);
  const retryGone = await D.evaluate(`!${btn('Ponów podgląd', DLG)}`);
  await D.interceptOff(); await D.dispose();
  return { ok: msg.ok && !!st && (st.disabled === true || st.ariaDisabled === 'true') && retry && effects.ok && retryGone, message: msg.ok, submitWhileFailed: st, retryButton: retry, effects: effects.ok, retryHiddenAfter: retryGone };
});

suite.add('S1-1-UI', 'Wyścig dziennik ↔ index.html', async () => ({ status: 'NOT_RUN', reason: 'nie da się wywołać z przeglądarki; pokryte testami serwera (docs/prototype-contract/scenariusze-e2e.md, sekcja 2)' }), { critical: false });

suite.add('F2-S99', 'Konsola', async () => consoleVerdict());

await suite.main();
