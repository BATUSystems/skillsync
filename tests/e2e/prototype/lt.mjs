// Nowości silnika 1.8.0 w prawdziwym Chrome: LT-01 `prototype.dark_theme: false` (zawsze jasny, bez przycisku „Motyw”), LT-02 motyw domyślny (przycisk jest,
// system ciemny daje ciemną stronę), DM-01 pokaz `prototype serve --demo` w przeglądarce (etykieta, brak narzędzi komentowania, logowania i akceptacji),
// DM-02 pokaz po HTTP wg kontraktu (405 demo_read_only dla zapisu, `comments.json` 404, `session` z `server.demo: true`, nic nie zapisane).
// Własne workspace'y (kopie projektu): `lt` (domyślny motyw), `lt-light` (klucz w manifeście + `prototype migrate alfa`), `lt-demo` (serwer pokazu).
import { Suite, sleep, workspace, startServer, connect, PORT1, proto, pageErrors, readdirSync, writeFileSync, existsSync, readFileSync } from './lib.mjs';
import { statSync } from 'node:fs';
import { join } from 'node:path';

const suite = new Suite('lt');
let wsDefault; let wsLight; let wsDemo; let srv; let c; let migrated;
const fileUrl = (ws, slug) => 'file://' + ws.file(slug);
const THEME_KEY = 'skillsync-prototype-theme';
// Jasność tła z obliczonego stylu (canvas rozwiązuje rgba i oklch); gdy tło jest przezroczyste, decyduje `color-scheme` strony.
const LUM = `(() => { const cv = document.createElement('canvas'); cv.width = cv.height = 1; const cx = cv.getContext('2d', { willReadFrequently: true });
  const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  for (const e of [document.body, document.documentElement]) { if (!e) continue; cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = getComputedStyle(e).backgroundColor; cx.fillRect(0, 0, 1, 1);
    const d = cx.getImageData(0, 0, 1, 1).data; if (d[3] >= 250) return 0.2126 * f(d[0]) + 0.7152 * f(d[1]) + 0.0722 * f(d[2]); }
  return getComputedStyle(document.documentElement).colorScheme.includes('dark') ? 0 : 1; })()`;
const themeButtons = `[...document.querySelectorAll('.proto-toolbar button, #proto-more-menu button')].filter((b) => /^Motyw/.test((b.textContent || '').trim()) || /^Motyw/.test(b.getAttribute('aria-label') || ''))`;
const snap = `(() => ({ theme: document.documentElement.getAttribute('data-theme'), off: document.documentElement.getAttribute('data-proto-dark'), scheme: document.documentElement.style.colorScheme,
  lum: ${LUM}, buttons: ${themeButtons}.length, stored: (() => { try { return localStorage.getItem(${JSON.stringify(THEME_KEY)}); } catch (e) { return 'brak dostępu'; } })() }))()`;
const shownNode = (find) => `(() => { const n = ${find}; if (!n) return 'brak'; return n.hidden || getComputedStyle(n).display === 'none' ? 'ukryty' : 'widoczny'; })()`;

suite.setup(async () => {
  wsDefault = workspace('lt');
  wsLight = workspace('lt-light');
  const manifest = `${wsLight.proj}/.github/.skillsync.yaml`;
  writeFileSync(manifest, readFileSync(manifest, 'utf8').replace('workflow: {}\n', 'workflow: {}\nprototype:\n  dark_theme: false\n'));
  migrated = await proto(wsLight, 'migrate', 'alfa');
  wsDemo = workspace('lt-demo');
  c = await connect();
  return {};
});
suite.teardown(async () => { if (c) await c.closeAll(); if (srv) await srv.stop(); });

// Strona jasna przy ciemnym systemie: przed i po JS (bez JS stan wynika ze znaczników `<html>`), zapisany wybór „ciemny” jest ignorowany i niczego nie nadpisuje.
suite.add('LT-01', 'dark_theme: false: strona zawsze jasna, bez przycisku „Motyw”', async () => {
  const p = await c.page('lt01', { dark: true });
  const url = fileUrl(wsLight, 'alfa');
  await p.go(url); await p.evaluate(`localStorage.clear(); true`);
  await p.send('Emulation.setScriptExecutionDisabled', { value: true });
  await p.go(url, 600);
  const noJs = await p.evaluate(`({ theme: document.documentElement.getAttribute('data-theme'), off: document.documentElement.getAttribute('data-proto-dark'), lum: ${LUM} })`);
  await p.send('Emulation.setScriptExecutionDisabled', { value: false });
  await p.go(url);
  const wide = await p.evaluate(snap);
  const hasBar = await p.evaluate(`!!document.querySelector('.proto-toolbar')`);
  await p.evaluate(`localStorage.setItem(${JSON.stringify(THEME_KEY)}, 'dark'); true`);
  await p.reload();
  const stored = await p.evaluate(snap);
  await p.size(500, 800); await sleep(300);
  const more = await p.evaluate(`(() => { const b = document.querySelector('.proto-more'); if (!b) return { menu: false }; b.click(); return { menu: true }; })()`);
  await sleep(300);
  const narrow = await p.evaluate(`({ buttons: ${themeButtons}.length, items: [...document.querySelectorAll('#proto-more-menu [role^="menuitem"]')].map((b) => b.textContent.trim()) })`);
  const restOk = migrated.code === 0 && hasBar && noJs.theme === 'light' && noJs.off === 'off' && noJs.lum > 0.7 && wide.theme === 'light' && wide.off === 'off' && wide.lum > 0.7
    && stored.theme === 'light' && stored.lum > 0.7 && stored.stored === 'dark' && more.menu && narrow.items.length > 0;
  return { ok: restOk && wide.buttons === 0 && stored.buttons === 0 && narrow.buttons === 0, migrateExit: migrated.code, hasBar, noJs, wide, stored, more, narrow };
});

// Przeciwna strona: bez klucza przycisk jest, ciemny system daje ciemną stronę, klik przełącza i zapisuje wybór.
suite.add('LT-02', 'Motyw domyślny: przycisk „Motyw” jest, system ciemny daje ciemną stronę', async () => {
  const p = await c.page('lt02', { dark: true });
  const url = fileUrl(wsDefault, 'beta');
  await p.go(url); await p.evaluate(`localStorage.clear(); true`); await p.go(url);
  const first = await p.evaluate(snap);
  await p.evaluate(`${themeButtons}[0].click(); true`); await sleep(400);
  const toggled = await p.evaluate(snap);
  await p.reload();
  const again = await p.evaluate(snap);
  return { ok: first.off === null && first.buttons === 1 && first.theme === 'dark' && first.lum < 0.3 && toggled.theme === 'light' && toggled.lum > 0.7 && toggled.stored === 'light'
    && again.theme === 'light' && again.buttons === 1, first, toggled, again };
});

const treeOf = (dir) => { const out = {}; const walk = (d) => { for (const name of readdirSync(d)) { const f = join(d, name); const s = statSync(f); if (s.isDirectory()) walk(f); else out[f.slice(dir.length)] = `${s.size}:${s.mtimeMs}`; } }; walk(dir); return out; };
const startDemo = async () => { if (!srv) srv = await startServer(wsDemo, { port: PORT1, extra: ['--demo', '--host', '127.0.0.1'] }); return srv; };

suite.add('DM-01', 'Pokaz (serve --demo): etykieta, bez narzędzi komentowania, logowania i akceptacji', async () => {
  await startDemo();
  const p = await c.page('dm01');
  const before = errorsCount();
  await p.go(`${srv.base}/demo/`, 1500);
  const bar = await p.evaluate(`(() => { const save = document.querySelector('.anno-save'); const names = (sel) => [...document.querySelectorAll(sel)].filter((b) => !b.hidden && getComputedStyle(b).display !== 'none').map((b) => b.textContent.trim());
    return { label: save ? save.textContent.trim() : null, saveShown: ${shownNode(`document.querySelector('.anno-save')`)},
      modeGroup: ${shownNode(`document.querySelector('[role="group"][aria-label="Tryb"]')`)}, threads: ${shownNode(`document.querySelector('.anno-threads-btn')`)},
      login: names('.proto-toolbar button').filter((t) => /^(Zaloguj|Wyloguj)/.test(t)), accept: names('button, a').filter((t) => /^Akcept/.test(t)),
      pins: [...document.querySelectorAll('.anno-pin')].filter((n) => getComputedStyle(n).display !== 'none').length,
      screens: document.querySelectorAll('section.screen').length }; })()`);
  const gallery = await (await fetch(`${srv.base}/`)).text();
  const restOk = bar.label === 'Pokaz · tylko do odczytu' && bar.saveShown === 'widoczny' && bar.login.length === 0 && bar.accept.length === 0 && bar.pins === 0 && bar.screens > 0
    && /demo/.test(gallery) && !/docs\/req\.md/.test(gallery) && !/\/_login/.test(gallery);
  const errs = pageErrors(before);
  return { ok: restOk && bar.modeGroup !== 'widoczny' && bar.threads !== 'widoczny' && errs.length === 0, bar, galleryBytes: gallery.length, errors: errs.slice(0, 3) };
});

const errorsCount = () => pageErrors(0).length;

suite.add('DM-02', 'Pokaz po HTTP: zapis 405, comments.json 404, session z server.demo', async () => {
  await startDemo();
  const base = srv.base;
  const raw = async (method, path, body) => {
    const r = await fetch(base + path, { method, headers: { 'Content-Type': 'application/json' }, body, redirect: 'manual' });
    const text = await r.text(); let json = null; try { json = JSON.parse(text); } catch (e) { /* nie JSON */ }
    return { status: r.status, text, json, allow: r.headers.get('allow'), cookie: r.headers.get('set-cookie') };
  };
  // plik z komentarzami musi istnieć, żeby 404 coś znaczyło
  const comments = wsDemo.file('demo', 'comments.json');
  const marker = 'DM02-MARKER-' + Date.now().toString(36);
  if (!existsSync(comments)) writeFileSync(comments, JSON.stringify({ schema: 1, operations: [{ text: marker }] }));
  const before = treeOf(wsDemo.proj);
  const writes = [];
  for (const [method, path] of [['POST', '/_api/v1/prototypes/demo/operations'], ['PUT', '/_api/v1/prototypes/demo/operations'], ['DELETE', '/demo/index.html'],
    ['PATCH', '/_api/v1/project/accept'], ['POST', '/_api/v1/login'], ['POST', '/_api/v1/prototypes/demo/restore']]) {
    const r = await raw(method, path, '{"type":"create"}');
    writes.push({ method, path, status: r.status, error: r.json && r.json.error, allow: r.allow, cookie: !!r.cookie });
  }
  const writesOk = writes.every((w) => w.status === 405 && w.error === 'demo_read_only' && w.allow === 'GET, HEAD' && !w.cookie);
  const staticFiles = [];
  for (const path of ['/demo/comments.json', '/demo/comments.js', '/demo/README.md', '/demo/questions.json']) {
    const r = await raw('GET', path); const h = await raw('HEAD', path);
    staticFiles.push({ path, status: r.status, head: h.status, echo: r.text.includes(path) || r.text.includes('comments.json') || r.text.includes(marker) });
  }
  const staticOk = staticFiles.every((s) => s.status === 404 && s.head === 404 && !s.echo);
  const session = await raw('GET', '/_api/v1/session');
  const sessionOk = session.status === 200 && session.json && session.json.server && session.json.server.demo === true && session.json.user === null && session.json.csrf === null && !session.cookie;
  const ops = await raw('GET', '/_api/v1/prototypes/demo/operations?after=0');
  const opsOk = ops.status === 404 && !ops.text.includes(marker);
  const same = JSON.stringify(treeOf(wsDemo.proj)) === JSON.stringify(before);
  return { ok: writesOk && staticOk && sessionOk && opsOk && same, writes, staticFiles, session: { status: session.status, server: session.json && session.json.server, user: session.json && session.json.user, csrf: session.json && session.json.csrf },
    operations: { status: ops.status }, treeUnchanged: same };
});

await suite.main();
