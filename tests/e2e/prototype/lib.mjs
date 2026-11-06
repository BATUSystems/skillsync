// Sterownik CDP i narzędzia stałego zestawu E2E prototypów (node >= 18; WebSocket: globalny w node >= 22, w 18/20 pakiet `ws` nie jest wymagany,
// bo sterownik używa wbudowanego `WebSocket` albo, gdy go brak, kończy bieg z komunikatem).
// Środowisko: plik JSON wskazany w SKILLSYNC_E2E_ENV (patrz env.py). Hasła kont czytane są w pamięci z pliku 0600 i nigdy nie trafiają
// na stdout, do logów ani do zrzutów. Wynik skryptu: ostatnia linia stdout = JSON {"ids": {ID: {status, evidence}}}.
import { readFileSync, writeFileSync, mkdirSync, cpSync, rmSync, openSync, existsSync, readdirSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { basename } from 'node:path';

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const env = JSON.parse(readFileSync(process.env.SKILLSYNC_E2E_ENV, 'utf8'));
const SECRETS = JSON.parse(readFileSync(env.secrets, 'utf8'));
export const secret = (key) => SECRETS[key];
export const mail = (key) => env.accounts[key];
export const [PORT1, PORT2] = env.ports;

if (typeof WebSocket === 'undefined') { console.error('Brak globalnego WebSocket: potrzebny node >= 22'); process.exit(3); }

// ---------------------------------------------------------------- CDP
function rpc(url) {
  const ws = new WebSocket(url);
  let seq = 0; const pending = new Map(); const listeners = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const { ok, fail } = pending.get(m.id); pending.delete(m.id); m.error ? fail(new Error(m.error.message)) : ok(m.result); }
    else if (m.method) for (const l of [...listeners]) l(m);
  };
  const ready = new Promise((ok, fail) => { ws.onopen = ok; ws.onerror = () => fail(new Error('ws error')); });
  const send = (method, params = {}) => new Promise((ok, fail) => { const id = ++seq; pending.set(id, { ok, fail }); ws.send(JSON.stringify({ id, method, params })); });
  return { ws, ready, send, listeners };
}

const allPages = [];   // karty wszystkich połączeń (do sprzątania po limicie czasu scenariusza)
export const errors = [];   // wpisy konsoli i wyjątki ze wszystkich kart skryptu

export async function connect() {
  const ver = await (await fetch(`http://127.0.0.1:${env.chromePort}/json/version`)).json();
  const b = rpc(ver.webSocketDebuggerUrl); await b.ready;
  const pages = [];
  return {
    async page(name, { shared, session, width = 1440, height = 900, dark = false, url } = {}) {
      const browserContextId = shared || (await b.send('Target.createBrowserContext', { disposeOnDetach: true })).browserContextId;
      const { targetId } = await b.send('Target.createTarget', { url: 'about:blank', browserContextId });
      const p = rpc(`ws://127.0.0.1:${env.chromePort}/devtools/page/${targetId}`); await p.ready;
      const pg = await makePage(name, p, async () => {
        try { p.ws.close(); } catch (e) { /* zamknięta */ }
        await b.send('Target.closeTarget', { targetId }).catch(() => {});
        if (!shared) await b.send('Target.disposeBrowserContext', { browserContextId }).catch(() => {});
      });
      pg.ctx = browserContextId; pg.targetId = targetId; pages.push(pg); allPages.push(pg);
      if (dark) await pg.media('prefers-color-scheme', 'dark');
      if (session) await pg.setSession(session, url);
      await pg.size(width, height);
      return pg;
    },
    async closeAll() { for (const pg of pages) await pg.dispose().catch(() => {}); try { b.ws.close(); } catch (e) { /* ok */ } },
  };
}

async function makePage(name, p, dispose) {
  const { send, listeners } = p;
  const net = [];
  listeners.push((m) => {
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push({ page: name, kind: 'console.error', text: m.params.args.map((a) => a.value ?? a.description).join(' ').slice(0, 300) });
    if (m.method === 'Runtime.exceptionThrown') errors.push({ page: name, kind: 'exception', text: (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text).slice(0, 300) });
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') errors.push({ page: name, kind: 'log:' + m.params.entry.source, text: (m.params.entry.text + ' ' + (m.params.entry.url || '')).slice(0, 300) });
    if (m.method === 'Network.requestWillBeSent') net.push(m.params.request.method + ' ' + m.params.request.url);
  });
  await send('Network.enable'); await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable');
  await send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
  const pg = {
    name, send, net, listeners,
    async evaluate(expression) {
      const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error('eval: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text).slice(0, 300));
      return r.result.value;
    },
    async size(width, height = 900) { await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }); await sleep(300); },
    async media(feature, value) { await send('Emulation.setEmulatedMedia', { media: '', features: [{ name: feature, value }] }); },
    async front() { await send('Page.bringToFront'); await sleep(150); },
    async setSession(session, url) {
      const i = session.cookie.indexOf('=');
      await send('Network.setCookie', { name: session.cookie.slice(0, i), value: session.cookie.slice(i + 1), url: url || session.base, path: '/' });
    },
    async go(url, wait = 900) {
      const loaded = new Promise((ok) => { const l = (m) => { if (m.method === 'Page.loadEventFired') { listeners.splice(listeners.indexOf(l), 1); ok(); } }; listeners.push(l); });
      await send('Page.navigate', { url });
      await Promise.race([loaded, sleep(20000)]); await sleep(wait);
    },
    async reload(wait = 900) {
      const loaded = new Promise((ok) => { const l = (m) => { if (m.method === 'Page.loadEventFired') { listeners.splice(listeners.indexOf(l), 1); ok(); } }; listeners.push(l); });
      await send('Page.reload', { ignoreCache: true }); await Promise.race([loaded, sleep(20000)]); await sleep(wait);
    },
    // Zmiana samego hash: nie przez Page.navigate (nie emituje loadEventFired).
    async hash(value) { await pg.evaluate(`location.hash = ${JSON.stringify(value)}; true`); await sleep(500); },
    async click(x, y) {
      await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
      await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
      await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
      await sleep(300);
    },
    // Klik wskaźnikiem w środek elementu (przewija do widoku). finder: wyrażenie JS zwracające element.
    async clickEl(finder, what = finder) {
      const r = await pg.evaluate(`(async () => { const el = (${finder}); if (!el) return null; el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
        await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); await new Promise((r) => setTimeout(r, 60)); const el2 = (${finder}) || el; const b = el2.getBoundingClientRect();
        const x = b.left + b.width / 2, y = b.top + b.height / 2; const hit = document.elementFromPoint(x, y); return { x, y, hit: !!hit && (hit === el2 || el2.contains(hit)) }; })()`);
      if (!r) throw new Error('brak elementu: ' + what.slice(0, 120));
      if (!r.hit) throw new Error('element zasłonięty lub poza widokiem: ' + what.slice(0, 120));
      await pg.click(r.x, r.y); return r;
    },
    // Klik przez DOM (bez przewijania ekranu i bez zmiany ekranu bieżącego).
    async domClick(finder, what = finder) {
      const r = await pg.evaluate(`(() => { const el = (${finder}); if (!el) return 'NOEL'; el.click(); return 'OK'; })()`);
      if (r !== 'OK') throw new Error('brak elementu: ' + what.slice(0, 120));
      await sleep(250);
    },
    // Klawisze: Tab/Esc jako rawKeyDown z kodami vk (Shift = 8, Ctrl = 2), Enter z text.
    async key(key, { ctrl = false, shift = false, alt = false } = {}) {
      const map = { Escape: ['Escape', 27], Enter: ['Enter', 13], Tab: ['Tab', 9], ArrowLeft: ['ArrowLeft', 37], ArrowUp: ['ArrowUp', 38], ArrowRight: ['ArrowRight', 39], ArrowDown: ['ArrowDown', 40], Home: ['Home', 36], End: ['End', 35], ' ': ['Space', 32], Backspace: ['Backspace', 8] };
      const upper = key.length === 1 ? key.toUpperCase() : key;
      const [code, vk] = map[key] || (key.length === 1 && /[a-z]/i.test(key) ? ['Key' + upper, upper.charCodeAt(0)] : [key, key.length === 1 ? key.toUpperCase().charCodeAt(0) : 0]);
      const modifiers = (alt ? 1 : 0) | (ctrl ? 2 : 0) | (shift ? 8 : 0);
      const raw = ['Tab', 'Escape', 'ArrowLeft', 'ArrowUp', 'ArrowRight', 'ArrowDown', 'Home', 'End', 'Backspace'].includes(key) || ctrl;
      const text = !raw && key.length === 1 ? key : (key === 'Enter' && !ctrl ? '\r' : undefined);
      await send('Input.dispatchKeyEvent', { type: raw ? 'rawKeyDown' : 'keyDown', key, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers, text });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers });
      await sleep(220);
    },
    async type(text) { await send('Input.insertText', { text }); await sleep(100); },
    async waitFor(expr, timeout = 8000, step = 120) {
      const t0 = Date.now();
      while (Date.now() - t0 < timeout) { try { const v = await pg.evaluate(expr); if (v) return { ok: true, ms: Date.now() - t0, v }; } catch (e) { /* strona w trakcie ładowania */ } await sleep(step); }
      return { ok: false, ms: Date.now() - t0 };
    },
    async shot(name) {
      try { mkdirSync(env.shots, { recursive: true }); const r = await send('Page.captureScreenshot', { format: 'png' }); writeFileSync(`${env.shots}/${name}.png`, Buffer.from(r.data, 'base64')); } catch (e) { /* zrzut nieobowiązkowy */ }
    },
    async offline(on) { await send('Network.emulateNetworkConditions', { offline: on, latency: 0, downloadThroughput: -1, uploadThroughput: -1 }); },
    // Przechwytywanie żądań: handler(ev, api) wywoływany dla każdego żądania pasującego do wzorców. api.continue(), api.fulfill(status, body), api.fail().
    async intercept(patterns, handler) {
      const l = async (m) => {
        if (m.method !== 'Fetch.requestPaused') return;
        const ev = m.params; const id = ev.requestId;
        const api = {
          continue: () => send('Fetch.continueRequest', { requestId: id }).catch(() => {}),
          fulfill: (status, body, headers = []) => send('Fetch.fulfillRequest', { requestId: id, responseCode: status, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }, ...headers], body: Buffer.from(typeof body === 'string' ? body : JSON.stringify(body)).toString('base64') }).catch(() => {}),
          fail: () => send('Fetch.failRequest', { requestId: id, errorReason: 'Failed' }).catch(() => {}),
        };
        try { await handler(ev, api); } catch (e) { api.continue(); }
      };
      pg._fetchListener = l; listeners.push(l);
      await send('Fetch.enable', { patterns: patterns.map((urlPattern) => ({ urlPattern })) });
    },
    async interceptOff() { if (pg._fetchListener) listeners.splice(listeners.indexOf(pg._fetchListener), 1); pg._fetchListener = null; await send('Fetch.disable').catch(() => {}); },
    dispose,
  };
  return pg;
}

// ---------------------------------------------------------------- Serwer prototypów (po PID)
export function workspace(name) {
  const dir = `${env.workdir}/run-${name}`;
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  cpSync(env.base, `${dir}/proj`, { recursive: true });
  const proj = `${dir}/proj`;
  return { dir, proj, file: (slug, f = 'index.html') => `${proj}/docs/prototypes/${slug}/${f}`, read: (slug, f = 'index.html') => readFileSync(`${proj}/docs/prototypes/${slug}/${f}`, 'utf8'), write: (slug, f, text) => writeFileSync(`${proj}/docs/prototypes/${slug}/${f}`, text) };
}

export function skillsync(ws, args, { input, expectCode } = {}) {
  return new Promise((ok) => {
    const child = spawn('python3', [env.cli, 'prototype', ...args], { env: { ...process.env, SKILLSYNC_CONFIG: env.config }, stdio: ['pipe', 'pipe', 'pipe'] });
    let out = ''; let err = '';
    child.stdout.on('data', (d) => { out += d; }); child.stderr.on('data', (d) => { err += d; });
    child.on('close', (code) => { let json = null; try { json = JSON.parse(out); } catch (e) { /* nie JSON */ } ok({ code, out, err, json }); });
    child.stdin.end(input ?? '');
  });
}
export const proto = (ws, action, ...rest) => skillsync(ws, [action, ws.proj, ...rest, '--json']);

export async function startServer(ws, { port = PORT1, extra = [] } = {}) {
  const log = openSync(`${ws.dir}/serve-${port}.log`, 'a');
  const child = spawn('python3', [env.cli, 'prototype', 'serve', ws.proj, '--port', String(port), ...extra],
    { env: { ...process.env, SKILLSYNC_CONFIG: env.config }, stdio: ['ignore', log, log], detached: true });
  child.unref();
  writeFileSync(`${ws.dir}/serve-${port}.pid`, String(child.pid));
  const srv = { pid: child.pid, port, base: `http://127.0.0.1:${port}`, ws };
  const t0 = Date.now();
  for (;;) {
    try { const r = await fetch(`${srv.base}/_api/v1/session`); if (r.status < 500) break; } catch (e) { /* jeszcze nie słucha */ }
    if (Date.now() - t0 > 20000) throw new Error('serwer prototypów nie wystartował (port ' + port + ')');
    await sleep(200);
  }
  srv.stop = async () => {
    const done = () => { try { rmSync(`${ws.dir}/serve-${port}.pid`, { force: true }); } catch (e) { /* brak pliku */ } };
    try { process.kill(srv.pid, 'SIGTERM'); } catch (e) { done(); return; }
    for (let i = 0; i < 30; i++) { try { process.kill(srv.pid, 0); } catch (e) { done(); return; } await sleep(200); }
    try { process.kill(srv.pid, 'SIGKILL'); } catch (e) { /* ok */ }
    done();
  };
  srv.restart = async () => { await srv.stop(); const n = await startServer(ws, { port, extra }); Object.assign(srv, { pid: n.pid }); srv.stop = n.stop; return srv; };
  return srv;
}

// ---------------------------------------------------------------- Klient API (rola programisty itd.)
export async function apiLogin(base, who, password) {
  const login = who.includes('@') ? who : mail(who);
  const r = await fetch(base + '/_api/v1/login', { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: base }, body: JSON.stringify({ login, password: password ?? secret(who) }) });
  let body = null; try { body = await r.json(); } catch (e) { /* brak treści */ }
  return { status: r.status, csrf: body?.csrf, cookie: (r.headers.get('set-cookie') || '').split(';')[0], base, body };
}
export async function session(base, who, password) { const s = await apiLogin(base, who, password); if (s.status !== 200) throw new Error(`logowanie ${who}: HTTP ${s.status}`); return s; }
export async function apiOps(base, slug) { return (await fetch(`${base}/_api/v1/prototypes/${slug}/operations?after=0`)).json(); }
export async function apiPost(s, slug, op) {
  const r = await fetch(`${s.base}/_api/v1/prototypes/${slug}/operations`, { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: s.base, Cookie: s.cookie, 'X-Skillsync-CSRF': s.csrf }, body: JSON.stringify(op) });
  let body = null; try { body = await r.json(); } catch (e) { /* brak treści */ }
  return { status: r.status, body };
}
export async function apiRaw(s, method, path, body) {
  const headers = { 'Content-Type': 'application/json', Origin: s.base };
  if (s.cookie) { headers.Cookie = s.cookie; headers['X-Skillsync-CSRF'] = s.csrf; }
  const r = await fetch(s.base + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), redirect: 'manual' });
  let json = null; try { json = await r.clone().json(); } catch (e) { /* nie JSON */ }
  return { status: r.status, json, headers: r.headers };
}
let cidCounter = 0;
export const cid = (tag = 'e2e') => `c-${tag}-${Date.now().toString(36)}-${++cidCounter}`;
export async function createThread(s, slug, screen, protoId, text, { kind = 'element', iteration } = {}) {
  const ops = await apiOps(s.base, slug);
  const it = iteration ?? (ops.iteration?.current ?? ops.iteration ?? 1);
  const threadId = 'th-' + cid('t').slice(2);
  const anchor = kind === 'screen' ? { kind: 'screen', x: 0.5, y: 0.05, iteration: it } : { kind: 'element', protoId, x: 0.5, y: 0.5, iteration: it };
  const r = await apiPost(s, slug, { cid: cid('c'), type: 'create', threadId, payload: { screen, anchor, message: { id: 'm-' + threadId.slice(3), text } } });
  return { threadId, status: r.status, body: r.body };
}
export async function setAcceptance(ACC, DEV, base, slug, sid, want) {
  const ops = await apiOps(base, slug);
  const e = ops.acceptance && ops.acceptance[sid];
  if (want && e && e.valid === true) return 'noop';
  if (e) { const u = await apiPost(DEV, slug, { cid: cid('u'), type: 'unaccept', payload: { screens: [sid] } }); if (u.status !== 201) throw new Error('unaccept ' + u.status); }
  if (!want) return 'unaccepted';
  const cur = await apiOps(base, slug);
  const a = await apiPost(ACC, slug, { cid: cid('a'), type: 'accept', payload: { scope: 'screen', screens: { [sid]: cur.screens[sid] }, version: cur.version } });
  if (a.status !== 201) throw new Error('accept ' + a.status + ' ' + JSON.stringify(a.body).slice(0, 120));
  return 'accepted';
}

// ---------------------------------------------------------------- Wyrażenia DOM (etykiety i teksty UI w pierwszej kolejności)
export const norm = `(s) => (s || '').replace(/\\s+/g, ' ').trim()`;
// widoczny przycisk o tekście zaczynającym się od `text` (w obrębie `scope`, domyślnie dokument)
export const btn = (text, scope = 'document') => `[...(${scope}).querySelectorAll('button')].find((b) => (b.textContent || '').replace(/\\s+/g, ' ').trim().startsWith(${JSON.stringify(text)}) && b.getClientRects().length > 0)`;
export const btnExact = (text, scope = 'document') => `[...(${scope}).querySelectorAll('button')].find((b) => (b.textContent || '').replace(/\\s+/g, ' ').trim() === ${JSON.stringify(text)} && b.getClientRects().length > 0)`;
export const byProto = (id) => `document.querySelector('[data-proto-id=${JSON.stringify(id)}]')`;
export const byLabel = (label, scope = 'document') => `${scope}.querySelector('[aria-label=${JSON.stringify(label)}]')`;
export const DLG = `document.querySelector('dialog[open]')`;
export const dlgText = `(() => { const d = ${DLG}; return d ? d.textContent.replace(/\\s+/g, ' ').trim() : null; })()`;
export const dlgOpen = `!!${DLG}`;
export const inDlg = `(() => { const d = ${DLG}; return !!d && d.contains(document.activeElement); })()`;
export const isBody = `(document.activeElement === document.body || document.activeElement === document.documentElement || !document.activeElement)`;
export const DLG_LIST = `[...${DLG}.querySelectorAll('button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])')].filter((e) => !e.disabled && e.getClientRects().length > 0)`;
export const saveState = `(document.querySelector('.anno-save') || {}).textContent || ''`;           // klasa silnika: stan zapisu
export const panelText = `(document.querySelector('.anno-panel') || {}).innerText || ''`;            // klasa silnika: panel „Wątki”
export const panelIsOpen = `!!document.querySelector('.anno-panel.open')`;
export const bannerText = `(document.querySelector('.anno-banners') || {}).innerText || ''`;
export const wasSaved = `/Zapisano/.test(${saveState})`;

export const commentBtn = `[...document.querySelectorAll('[role="group"][aria-label="Tryb"] button')].find((b) => /^Komentarze/.test(b.textContent.trim()))`;
export const clickBtn = `[...document.querySelectorAll('[role="group"][aria-label="Tryb"] button')].find((b) => /^Klikalny/.test(b.textContent.trim()))`;
// Tryb komentowania (przełącznik „Tryb”): want=true włącza (klawisz C), false wraca do „Klikalny”.
export async function commentMode(p, want) {
  const state = () => p.evaluate(`(() => { const b = ${commentBtn}; return !!b && b.getAttribute('aria-pressed') === 'true'; })()`);
  const on = await state();
  if (want && !on) { await p.evaluate(`window.scrollTo(0,0); document.activeElement && document.activeElement.blur(); true`); await p.key('c'); }
  if (!want && on) await p.domClick(clickBtn);
  return state();
}
export async function openPanel(p) { if (!(await p.evaluate(panelIsOpen))) await p.domClick(`document.querySelector('.anno-threads-btn')`); await sleep(300); }
export async function closePanelIfOpen(p) { if (await p.evaluate(panelIsOpen)) { await p.key('Escape'); await sleep(200); } }
export async function loginUI(p, who, password) {
  await p.domClick(btn('Zaloguj', `document.querySelector('.anno-user-slot')`));
  await p.waitFor(`!!document.querySelector('#anno-login-email')`, 4000);
  await p.domClick(`document.querySelector('#anno-login-email')`); await p.evaluate(`document.querySelector('#anno-login-email').focus(); true`); await p.type(who.includes('@') ? who : mail(who));
  await p.evaluate(`document.querySelector('#anno-login-password').focus(); true`); await p.type(password ?? secret(who)); await p.key('Enter');
  return p.waitFor(`!!document.querySelector('.anno-user') && !${DLG}`, 8000);
}
export async function sessionPage(c, name, base, who, opts = {}) {
  const s = await session(base, who);
  const p = await c.page(name, { ...opts, session: s, url: base });
  return { p, s };
}
// Tryb komentowania C, klik elementu, wpis, Ctrl+Enter.
export async function commentOn(p, protoId, text) {
  await p.evaluate(`window.scrollTo(0,0); true`);
  await commentMode(p, true);
  await p.clickEl(byProto(protoId), protoId).catch(() => p.domClick(byProto(protoId), protoId));
  const w = await p.waitFor(`!!document.querySelector('.anno-composer-pop')`, 4000);
  if (!w.ok) throw new Error('okienko komentarza się nie otworzyło dla ' + protoId);
  await p.type(text); await p.key('Enter', { ctrl: true });
  const saved = await p.waitFor(`!document.querySelector('.anno-composer-pop') && ${wasSaved}`, 8000);
  await p.key('Escape'); await closePanelIfOpen(p); await commentMode(p, false);
  return saved.ok;
}
export async function openThreadByPin(p, num) {
  await p.evaluate(`window.scrollTo(0,0); true`);
  await p.evaluate(`(() => { const b = [...document.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].find((x) => x.textContent.trim() === ${JSON.stringify(String(num))}); if (b) { b.scrollIntoView({block:'center'}); } return !!b; })()`);
  await sleep(300);
  await p.domClick(`[...document.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].find((x) => x.textContent.trim() === ${JSON.stringify(String(num))})`, 'pinezka ' + num);
  return p.waitFor(`!!document.querySelector('.anno-panel.open textarea[aria-label="Odpowiedź"], .anno-panel.open .anno-thread')`, 4000);
}
export async function replyIn(p, num, text) {
  await p.front(); await openThreadByPin(p, num);
  await p.evaluate(`document.querySelector('.anno-panel textarea[aria-label="Odpowiedź"]').focus(); true`);
  await p.type(text); await p.key('Enter', { ctrl: true });
}
export async function queueLength(p) {
  return p.evaluate(`(() => { const k = Object.keys(localStorage).find((x) => x.startsWith('skillsync-prototype-queue:v2:')); if (!k) return -1; try { const v = JSON.parse(localStorage.getItem(k)); return (Array.isArray(v) ? v : v.items).length; } catch (e) { return -2; } })()`);
}
export const filtersState = (p) => p.evaluate(`[...document.querySelectorAll('.anno-filters button')].map((b) => ({ t: b.textContent.trim(), pressed: b.getAttribute('aria-pressed') }))`);
export const countCards = (p) => p.evaluate(`document.querySelectorAll('.anno-panel .anno-card').length`);
export async function pickFilter(p, name) {
  await p.domClick(`[...document.querySelectorAll('.anno-filters button')].find((b) => b.textContent.trim().startsWith(${JSON.stringify(name)}))`, 'filtr ' + name);
  await sleep(250);
}
export const pageErrors = (since = 0) => errors.slice(since).filter((e) => e.kind === 'exception' || e.kind === 'console.error');

// Filtr osiągalnych elementów okna, sprawdzany zachowaniem: Tab i Shift+Tab w otwartym oknie nie trafiają na elementy z `disabled`,
// `tabindex="-1"` ani ukryte; element z `aria-disabled="true"` zostaje w obiegu; grupa radio to jeden przystanek: zaznaczona pozycja,
// a gdy żadna nie jest zaznaczona, pierwsza. Dla Tab wymagana jest dokładnie ta pozycja. Dla Shift+Tab bez zaznaczenia przyjmujemy dowolną
// jedną pozycję grupy: silnik interweniuje tylko przy zawinięciu (cel z listy `dialogFocusable`, w której grupa ma jednego przedstawiciela,
// kontrakt `dialogTabTarget`), a środek obiegu wstecz robi przeglądarka, która w niezaznaczonej grupie ląduje na ostatniej pozycji.
// Zwraca też spis rodzajów elementów w oknie (`kinds`).
// `inject`: elementy wstawiane przez DOM do otwartego okna na POCZĄTKU i na KOŃCU (disabled, ukryty `hidden`, ukryty `display:none`,
// `aria-disabled="true"`, grupa radio z dwiema pozycjami: na początku bez zaznaczenia, na końcu z zaznaczoną drugą) oraz przycisk
// `tabindex="-1"` na końcu; wymagane jest, by spis `kinds` miał każdy rodzaj i by zawinięcie Tab/Shift+Tab omijało wykluczone, a trafiało
// na `aria-disabled` i właściwą pozycję radio. Wstrzyknięte elementy są po audycie usuwane.
export async function focusAudit(p, { inject = false } = {}) {
  if (inject) await p.evaluate(`(() => { const d = ${DLG}; const mk = (tag, attrs, text) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); e.setAttribute('data-e2e-injected', '1'); if (text) e.textContent = text; return e; };
    const batch = (end) => { const t = end ? 'koniec' : 'początek'; const r = [mk('button', { type: 'button', disabled: '' }, 'disabled ' + t), mk('button', { type: 'button', hidden: '' }, 'hidden ' + t), mk('button', { type: 'button', style: 'display:none' }, 'display-none ' + t),
      mk('button', { type: 'button', 'aria-disabled': 'true' }, 'aria-disabled ' + t)];
      const g = end ? 'e2e-inj-end' : 'e2e-inj-start'; const a = mk('input', { type: 'radio', name: g, value: 'a', 'aria-label': 'radio A ' + t }); const b = mk('input', { type: 'radio', name: g, value: 'b', 'aria-label': 'radio B ' + t });
      if (end) b.checked = true; r.push(a, b); if (end) r.push(mk('button', { type: 'button', tabindex: '-1' }, 'tabindex-1 koniec')); return r; };
    const first = d.firstChild; for (const e of batch(false)) d.insertBefore(e, first); for (const e of batch(true)) d.appendChild(e); return 1; })()`);
  try {
    const rows = await p.evaluate(`(() => { const d = ${DLG}; const all = [...d.querySelectorAll('button, a[href], input, select, textarea, [tabindex]')]; window.__fa = all;
      const rows = all.map((e, i) => { const ti = e.getAttribute('tabindex'); let ex = null;
        if (e.disabled) ex = 'disabled'; else if (ti === '-1') ex = 'tabindex-1'; else if (e.type === 'hidden' || e.closest('[hidden]') || !e.getClientRects().length) ex = 'hidden';
        return { i, ex, radio: e.tagName === 'INPUT' && e.type === 'radio', name: e.name || '', checked: !!e.checked, ariaDisabled: e.getAttribute('aria-disabled') === 'true', inj: e.hasAttribute('data-e2e-injected'), rep: i }; });
      const groups = {}; for (const r of rows) if (r.radio && !r.ex) (groups[r.name] = groups[r.name] || []).push(r);
      for (const g of Object.values(groups)) { const rep = g.find((r) => r.checked) || g[0]; for (const r of g) { r.rep = rep.i; if (r !== rep) r.ex = 'radio-other'; } }
      return rows; })()`);
    const E = rows.filter((r) => !r.ex).map((r) => r.i);
    const kinds = { total: rows.length, reachable: E.length, disabled: rows.filter((r) => r.ex === 'disabled').length, tabindexMinus1: rows.filter((r) => r.ex === 'tabindex-1').length,
      hidden: rows.filter((r) => r.ex === 'hidden').length, ariaDisabledInTour: rows.filter((r) => !r.ex && r.ariaDisabled).length,
      radioGroups: new Set(rows.filter((r) => r.radio).map((r) => r.name)).size, radioOther: rows.filter((r) => r.ex === 'radio-other').length };
    // wstrzyknięte rodzaje muszą być widoczne w spisie (każdy >= 1; na początku i na końcu po jednym z każdego, grupy radio: 2 z jedną pozycją poza przystankiem)
    const inj = rows.filter((r) => r.inj); kinds.injected = { disabled: inj.filter((r) => r.ex === 'disabled').length, hidden: inj.filter((r) => r.ex === 'hidden').length, tabindexMinus1: inj.filter((r) => r.ex === 'tabindex-1').length,
      ariaDisabledInTour: inj.filter((r) => !r.ex && r.ariaDisabled).length, radioGroups: new Set(inj.filter((r) => r.radio).map((r) => r.name)).size, radioOther: inj.filter((r) => r.ex === 'radio-other').length };
    const ki = kinds.injected; const kindsOk = !inject || (ki.disabled >= 2 && ki.hidden >= 4 && ki.tabindexMinus1 >= 1 && ki.ariaDisabledInTour >= 2 && ki.radioGroups === 2 && ki.radioOther === 2);
    if (E.length === 0) return { ok: false, kinds, why: 'brak osiągalnych elementów' };
    // okna z ramkami (Porównanie): każda ramka zajmuje kilka kroków Tab wewnątrz własnego dokumentu; obieg sprawdza R3-02a
    if (await p.evaluate(`!!${DLG}.querySelector('iframe')`)) return { ok: kindsOk, kinds, skipped: 'okno z ramkami (obieg: R3-02a)' };
    const norm = (i) => { const r = rows[i]; return r ? r.rep : i; };
    const walk = async (shift) => {
      await p.evaluate(`(() => { window.__fa[${E[0]}].focus(); return 1; })()`);
      const seen = [];
      for (let k = 0; k < E.length; k++) { await p.key('Tab', shift ? { shift: true } : {}); seen.push(await p.evaluate(`window.__fa.indexOf(document.activeElement)`)); }
      return seen;
    };
    const fwd = await walk(false); const back = await walk(true);
    const wantF = E.slice(1).concat(E[0]); const wantBack = [E[E.length - 1]].concat(E.slice(0, -1).reverse());
    const eqExact = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
    const eqNorm = (a, b) => a.length === b.length && a.every((x, i) => norm(x) === norm(b[i]));
    const excludedHit = (seq) => seq.filter((i) => i >= 0 && rows[i] && rows[i].ex && rows[i].ex !== 'radio-other');
    const ok = kindsOk && eqExact(fwd, wantF) && eqNorm(back, wantBack) && excludedHit(fwd).length === 0 && excludedHit(back).length === 0;
    return { ok, kinds, kindsOk, fwd, back, wantF, wantBack, excluded: rows.filter((r) => r.ex).map((r) => r.i + ':' + r.ex) };
  } finally {
    if (inject) await p.evaluate(`(() => { document.querySelectorAll('[data-e2e-injected]').forEach((e) => e.remove()); return 1; })()`);
  }
}

// Okno modalne: pełny obieg Tab / Shift+Tab / Esc (zapis wyniku bez wypisywania sekretów).
export async function windowCycle(p, { opener = null, escBlocked = false, inject = false } = {}) {
  const r = {};
  r.title = (await p.evaluate(dlgText) || '').slice(0, 60);
  r.focusInsideOnOpen = await p.evaluate(inDlg);
  r.audit = await focusAudit(p, { inject });
  const n = r.n = await p.evaluate(`${DLG_LIST}.length`);
  const idx = `${DLG_LIST}.indexOf(document.activeElement)`;
  const focusAt = (i) => `(() => { ${DLG_LIST}[${i}].focus(); return 1; })()`;
  await p.evaluate(focusAt(n - 1)); await p.key('Tab'); r.tabLastToFirst = (await p.evaluate(idx)) === 0;
  await p.evaluate(focusAt(0)); await p.key('Tab', { shift: true }); r.shiftTabFirstToLast = (await p.evaluate(idx)) === n - 1;
  await p.evaluate(focusAt(0)); r.cycleAllInside = true;
  for (let i = 0; i < n + 2; i++) { await p.key('Tab'); if (!(await p.evaluate(inDlg)) || (await p.evaluate(isBody))) r.cycleAllInside = false; }
  await p.key('Escape'); await sleep(300);
  if (escBlocked) {
    await p.key('Escape'); await sleep(300);
    r.stillOpenAfterEsc = await p.evaluate(dlgOpen); r.focusInside = await p.evaluate(inDlg); r.notBody = !(await p.evaluate(isBody));
    r.ok = r.audit.ok && r.focusInsideOnOpen && r.tabLastToFirst && r.shiftTabFirstToLast && r.cycleAllInside && r.stillOpenAfterEsc && r.focusInside && r.notBody;
    return r;
  }
  r.closedByEsc = !(await p.evaluate(dlgOpen)); r.notBody = !(await p.evaluate(isBody));
  r.focusAfter = await p.evaluate(`(() => { const a = document.activeElement; return a ? a.tagName + ':' + (a.getAttribute('aria-label') || a.textContent || '').trim().slice(0, 24) : null; })()`);
  r.focusOnOpener = opener ? await p.evaluate(`document.activeElement === (${opener})`) : null;
  r.ok = r.audit.ok && r.focusInsideOnOpen && r.tabLastToFirst && r.shiftTabFirstToLast && r.cycleAllInside && r.closedByEsc && r.notBody && (opener ? r.focusOnOpener : true);
  return r;
}

// ---------------------------------------------------------------- Zestaw scenariuszy
export class Suite {
  constructor(name) { this.name = name; this.items = []; this.setupFn = null; this.teardownFn = null; }
  setup(fn) { this.setupFn = fn; }
  teardown(fn) { this.teardownFn = fn; }
  // opts: { critical: bool, timeout: ms }
  add(id, title, fn, opts = {}) { this.items.push({ id, title, fn, critical: opts.critical !== false, timeout: opts.timeout ?? 150000 }); }
  async main() {
    if (process.argv.includes('--list')) { console.log(JSON.stringify({ suite: this.name, ids: this.items.map((i) => ({ id: i.id, title: i.title, critical: i.critical })) })); return; }
    const only = (process.env.SKILLSYNC_E2E_ONLY || '').split(',').filter(Boolean);
    const out = {}; let ctx = null;
    const save = () => { try { mkdirSync(env.results, { recursive: true }); writeFileSync(`${env.results}/${this.name}.json`, JSON.stringify({ ids: out }, null, 1)); } catch (e) { /* katalog wyników */ } };
    try {
      ctx = await this.setupFn();
      for (const item of this.items) {
        if (only.length && !only.includes(item.id)) continue;
        const t0 = Date.now(); const e0 = errors.length; const pages0 = allPages.length;
        let timer;
        try {
          const r = await Promise.race([item.fn(ctx), new Promise((_, no) => { timer = setTimeout(() => no(new Error('limit czasu scenariusza ' + item.timeout + ' ms')), item.timeout); })]);
          const { ok, status, ...evidence } = r || {};
          out[item.id] = { status: status || (ok ? 'PASS' : 'FAIL'), evidence, ms: Date.now() - t0 };
        } catch (e) {
          out[item.id] = { status: 'FAIL', evidence: { error: String(e.message || e).slice(0, 400) }, ms: Date.now() - t0 };
        } finally {
          clearTimeout(timer);
          // po limicie czasu: karty otwarte przez scenariusz są zamykane, więc jego dalsze kroki kończą się błędem zamiast działać w tle
          // (karty współdzielone z wcześniejszymi scenariuszami zostają; ich los zależy od kolejnych scenariuszy)
          if (out[item.id] && /limit czasu scenariusza/.test(out[item.id].evidence?.error || '')) for (const pg of allPages.slice(pages0)) await pg.dispose().catch(() => {});
        }
        if (out[item.id].status === 'FAIL') out[item.id].evidence.errorsSeen = errors.slice(e0).filter((x) => x.kind !== 'log:network').slice(0, 3);
        console.error(`[${this.name}] ${item.id} ${out[item.id].status} ${out[item.id].ms} ms`);
        save();
      }
    } catch (e) {
      for (const item of this.items) if (!out[item.id]) out[item.id] = { status: 'FAIL', evidence: { error: 'przygotowanie zestawu: ' + String(e.message || e).slice(0, 300) } };
    } finally {
      try { if (this.teardownFn) await this.teardownFn(ctx); } catch (e) { console.error('teardown:', e.message); }
    }
    save();
    console.log(JSON.stringify({ suite: this.name, ids: out }));
    process.exit(0);
  }
}

// Scenariusz „czysta konsola”: 0 wyjątków i 0 console.error w całym biegu skryptu.
// Dozwolone są tylko wpisy sieciowe (`log:network`) dla celowo przerwanych lub odrzuconych żądań; inne `log:*` (security, javascript, …)
// dają błąd, chyba że scenariusz wymieni je w `allowKinds`.
export function consoleVerdict({ allowKinds = [] } = {}) {
  const bad = errors.filter((e) => e.kind === 'exception' || e.kind === 'console.error');
  const net = errors.filter((e) => e.kind === 'log:network');
  const other = errors.filter((e) => String(e.kind).startsWith('log:') && e.kind !== 'log:network' && !allowKinds.includes(e.kind));
  return { ok: bad.length === 0 && other.length === 0, exceptions: errors.filter((e) => e.kind === 'exception').length, consoleErrors: errors.filter((e) => e.kind === 'console.error').length, networkEntriesAllowed: net.length, otherLogEntries: other.length, sample: bad.concat(other).slice(0, 3) };
}
export { existsSync, readdirSync, readFileSync, writeFileSync, rmSync, basename };

// `prototype accept` przez pty (hasło konta z pliku sekretów, nigdy na stdout).
export function acceptCli(key, args) {
  return new Promise((ok) => {
    const child = spawn('python3', ['-I', `${import.meta.dirname}/accept_cli.py`, process.env.SKILLSYNC_E2E_ENV, key, ...args], { stdio: ['ignore', 'pipe', 'pipe'] });
    let out = ''; child.stdout.on('data', (d) => { out += d; });
    child.on('close', () => {
      const m = /\[pty\] prompt_sent=(\w+) exit=(\d+)/.exec(out);
      const jsonText = out.slice(out.indexOf('{'), out.lastIndexOf('}') + 1); let json = null; try { json = JSON.parse(jsonText); } catch (e) { /* nie JSON */ }
      ok({ code: m ? Number(m[2]) : -1, promptSent: m ? m[1] === 'True' : false, json, out: out.slice(0, 600) });
    });
  });
}
