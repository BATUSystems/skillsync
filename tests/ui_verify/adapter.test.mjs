// Adaptery Playwright (S10) z atrapa przegladarki, bez Chrome i bez zaleznosci z cache:
// polityka origin dla nawigacji (B1), symlinki file:// (B3), acceptDownloads (N7), czas oczekiwania asercji (A10),
// zly stan strony (C5). Atrapa odtwarza tylko kontrakt Playwrighta uzywany przez adapter.
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LIB = path.join(HERE, '..', '..', 'library', 'skills', 'ui-verify', 'scripts', 'lib');
const lib = (name) => import(pathToFileURL(path.join(LIB, name)).href);

const { createCaptureAdapter, createPlaywrightAdapter } = await lib('playwright-adapter.mjs');
const { validateCaptureConfig } = await lib('capture-core.mjs');
const { validateConfig } = await lib('measure-core.mjs');

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-adapter-'));
after(() => fs.rmSync(tmpRoot, { recursive: true, force: true }));
let counter = 0;
const newDir = (label = 'd') => {
  const d = path.join(tmpRoot, `${label}-${counter++}`);
  fs.mkdirSync(d, { recursive: true });
  return d;
};
const CONFIG_DIR = newDir('cfg');

const SYMLINK_SKIP = (() => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-adapter-probe-'));
  try {
    fs.writeFileSync(path.join(d, 't'), 'x');
    fs.symlinkSync(path.join(d, 't'), path.join(d, 'l'), 'file');
    return false;
  } catch (e) {
    return `needs-symlink: ${e.code || e.message}`;
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
})();

const HOME_URL = 'http://localhost:3000/home';
const EVIL_URL = 'https://evil.example/path?token=abc123#frag';
const policyError = (e) => e && e.name === 'PolicyError';

// --- atrapa przegladarki ---

const PNG_BYTES = Buffer.from([137, 80, 78, 71, 1, 2, 3, 4]);

// opts.finalUrl(requestedUrl) => adres po nawigacji (domyslnie zadany); opts.onGoto({ ctx, page, url }) uruchamiane w goto;
// opts.urlOverride(page) => adres zwracany przez page.url() (np. po przekierowaniu w trakcie krokow logowania).
function fakeBrowser(opts = {}) {
  const contexts = [];
  const browser = {
    contexts,
    async newContext(options) {
      const ctx = {
        options,
        routes: [],
        pages: [],
        route(pattern, handler) { this.routes.push({ pattern, handler }); return Promise.resolve(); },
        setDefaultTimeout() {},
        tracing: { async start() {}, async stop() {} },
        async storageState() { return {}; },
        async newPage() {
          const page = fakePage(ctx, opts);
          ctx.pages.push(page);
          return page;
        },
        async close() {},
      };
      contexts.push(ctx);
      return ctx;
    },
  };
  return browser;
}

function fakePage(ctx, opts) {
  const page = {
    gotos: [], fills: [], clicks: [], waits: [], shots: 0, locatorShots: 0,
    missing: new Set(opts.missingSelectors || []),
    _url: 'about:blank',
    url() { return opts.urlOverride ? opts.urlOverride(page) : page._url; },
    setDefaultTimeout() {},
    async setViewportSize() {},
    async emulateMedia() {},
    async waitForLoadState() {},
    async goto(url) {
      page.gotos.push(url);
      page._url = opts.finalUrl ? opts.finalUrl(url) : url;
      if (opts.onGoto) await opts.onGoto({ ctx, page, url });
    },
    async evaluate() { return [0, 0]; },
    async screenshot() { page.shots++; return PNG_BYTES; },
    locator(selector) {
      const loc = {
        selector,
        first() { return loc; },
        async all() { return []; },
        async count() { return page.missing.has(selector) ? 0 : 1; },
        async waitFor(o) {
          page.waits.push({ selector, options: o });
          if (page.missing.has(selector)) throw Object.assign(new Error('Timeout exceeded'), { name: 'TimeoutError' });
        },
        async fill(value, o) { page.fills.push({ selector, value, options: o }); },
        async click(o) { page.clicks.push({ selector, options: o }); },
        async boundingBox() { return page.missing.has(selector) ? null : { x: 10, y: 10, width: 40, height: 40 }; },
        async evaluate() { return {}; },
        async evaluateAll() { return !page.missing.has(selector); },
        async screenshot() { page.locatorShots++; return PNG_BYTES; },
      };
      return loc;
    },
    async close() {},
  };
  return page;
}

// Zadanie nawigacji przechodzace przez handler zarejestrowany w context.route (jak robi to Playwright).
async function navigate(ctx, url, { navigation = true } = {}) {
  const request = { url: () => url, isNavigationRequest: () => navigation, resourceType: () => (navigation ? 'document' : 'script') };
  const route = {
    aborted: 0, continued: 0, fallbacks: 0,
    async abort() { this.aborted++; },
    async continue() { this.continued++; },
    async fallback() { this.fallbacks++; },
    request() { return request; },
  };
  for (const { handler } of ctx.routes) await handler(route, request);
  return route;
}

function captureSetup({ pages, extra = {}, browserOpts = {}, allowOrigins = [], login } = {}) {
  const v = validateCaptureConfig({ version: 1, ...(login ? { login } : {}), pages, ...extra }, { configDir: CONFIG_DIR, allowOrigins, env: {} });
  assert.equal(v.ok, true, `konfiguracja testowa niepoprawna: ${v.errors}`);
  const browser = fakeBrowser(browserOpts);
  const adapter = createCaptureAdapter({ browser, PNG: null, config: v.config, configDir: CONFIG_DIR, allowOrigins });
  return { v, browser, adapter, page: v.config.pages[0] };
}
const PAGE = (over = {}) => ({ id: 'home', url: HOME_URL, assert: { selector: 'h1' }, ...over });

function measureSetup({ browserOpts = {}, urls = [HOME_URL, HOME_URL] } = {}) {
  const v = validateConfig({ version: 1, pairs: [{ id: 'p', reference: { url: urls[0], selector: '#t' }, candidate: { url: urls[1], selector: '#t' } }] }, { configDir: CONFIG_DIR, allowOrigins: [] });
  assert.equal(v.ok, true, `konfiguracja testowa niepoprawna: ${v.errors}`);
  const browser = fakeBrowser(browserOpts);
  const adapter = createPlaywrightAdapter({ browser, PNG: null, config: v.config, configDir: CONFIG_DIR, allowOrigins: [] });
  return { v, browser, adapter, side: v.config.pairs[0].reference };
}

// --- B1: polityka origin dla nawigacji ---

test('B1: kontekst capture i measure rejestruje context.route("**/*")', async () => {
  const c = captureSetup({ pages: [PAGE()] });
  await c.adapter.capture({ page: c.page, width: 400, mode: 'light' });
  assert.ok(c.browser.contexts.length >= 1);
  for (const ctx of c.browser.contexts) assert.ok(ctx.routes.some((r) => r.pattern === '**/*'), JSON.stringify(ctx.routes.map((r) => r.pattern)));
  const m = measureSetup();
  await m.adapter.measure(m.side);
  assert.ok(m.browser.contexts.length >= 1);
  for (const ctx of m.browser.contexts) assert.ok(ctx.routes.some((r) => r.pattern === '**/*'));
});

test('B1 handler: nawigacja na niedozwolony origin => route.abort(); dozwolona (localhost) i file pod configDir => dalej', async () => {
  const c = captureSetup({ pages: [PAGE()] });
  await c.adapter.capture({ page: c.page, width: 400, mode: 'light' });
  const ctx = c.browser.contexts[0];
  const evil = await navigate(ctx, EVIL_URL);
  assert.equal(evil.aborted, 1);
  assert.equal(evil.continued + evil.fallbacks, 0);
  const local = await navigate(ctx, 'http://localhost:3000/next');
  assert.equal(local.aborted, 0);
  assert.equal(local.continued + local.fallbacks, 1);
  const inside = await navigate(ctx, pathToFileURL(path.join(CONFIG_DIR, 'page.html')).href);
  assert.equal(inside.aborted, 0);
  const outside = await navigate(ctx, pathToFileURL(path.join(os.tmpdir(), 'uv-adapter-elsewhere', 'x.html')).href);
  assert.equal(outside.aborted, 1);
});

test('B1 handler: zadania subzasobow (nie-nawigacje) nie sa blokowane przez polityke nawigacji (granica opisana w dokumentacji)', async () => {
  const c = captureSetup({ pages: [PAGE()] });
  await c.adapter.capture({ page: c.page, width: 400, mode: 'light' });
  const sub = await navigate(c.browser.contexts[0], EVIL_URL, { navigation: false });
  assert.equal(sub.aborted, 0);
  assert.equal(sub.continued + sub.fallbacks, 1);
});

test('B1 handler: dozwolony --allow-origin przepuszcza nawigacje na ten host', async () => {
  const c = captureSetup({ pages: [PAGE()], allowOrigins: ['app.example.test'] });
  await c.adapter.capture({ page: c.page, width: 400, mode: 'light' });
  const ok = await navigate(c.browser.contexts[0], 'https://app.example.test/x');
  assert.equal(ok.aborted, 0);
});

for (const via of ['goto rzuca po abort', 'goto konczy sie normalnie']) {
  test(`B1 capture: zablokowana nawigacja (${via}) => PolicyError "navigation blocked: <origin>" bez query i fragmentu`, async () => {
    const throwing = via === 'goto rzuca po abort';
    const c = captureSetup({
      pages: [PAGE()],
      browserOpts: {
        async onGoto({ ctx }) {
          await navigate(ctx, EVIL_URL);
          if (throwing) throw new Error('net::ERR_FAILED');
        },
      },
    });
    await assert.rejects(c.adapter.capture({ page: c.page, width: 400, mode: 'light' }), (e) => {
      assert.ok(policyError(e), `${e && e.name}: ${e && e.message}`);
      assert.match(e.message, /navigation blocked: https:\/\/evil\.example/);
      for (const leak of ['token', 'abc123', 'frag', '?', '#']) assert.ok(!e.message.includes(leak), `${leak} w komunikacie: ${e.message}`);
      return true;
    });
    assert.equal(c.browser.contexts[0].pages[0].shots, 0, 'zrzut nie moze powstac po zablokowanej nawigacji');
  });
}

test('B1 measure: zablokowana nawigacja => PolicyError (nie wynik pomiaru)', async () => {
  const m = measureSetup({ browserOpts: { async onGoto({ ctx }) { await navigate(ctx, EVIL_URL); } } });
  await assert.rejects(m.adapter.measure(m.side), (e) => policyError(e) && /navigation blocked: https:\/\/evil\.example/.test(e.message) && !e.message.includes('token'));
});

test('B1 login: przekierowanie poza polityke PRZED pierwszym fill => PolicyError, nic nie wpisane ani kliknieto', async () => {
  const secret = ['login', 'pw', String(process.pid), 'abcd'].join('-');
  const c = captureSetup({
    pages: [PAGE()],
    login: { url: 'http://localhost:3000/login', steps: [{ fill: { selector: '#u', valueEnv: 'UI_VERIFY_LOGIN_PW' } }, { click: '#go' }] },
    browserOpts: { finalUrl: () => 'https://evil.example/login?next=1' },
  });
  await assert.rejects(c.adapter.login({ login: c.v.config.login, env: { UI_VERIFY_LOGIN_PW: secret } }), policyError);
  const page = c.browser.contexts[0].pages[0];
  assert.equal(page.fills.length, 0, 'sekret nie moze zostac wpisany na obcym origin');
  assert.equal(page.clicks.length, 0);
});

test('B1 login: przekierowanie w trakcie krokow (po pierwszym fill) blokuje kolejny fill/click', async () => {
  const c = captureSetup({
    pages: [PAGE()],
    login: { url: 'http://localhost:3000/login', steps: [{ fill: { selector: '#u', valueEnv: 'UI_VERIFY_A' } }, { fill: { selector: '#p', valueEnv: 'UI_VERIFY_B' } }, { click: '#go' }] },
    browserOpts: { urlOverride: (page) => (page.fills.length >= 1 ? 'https://evil.example/phish' : 'http://localhost:3000/login') },
  });
  await assert.rejects(c.adapter.login({ login: c.v.config.login, env: { UI_VERIFY_A: 'user-1234', UI_VERIFY_B: 'pass-1234' } }), policyError);
  const page = c.browser.contexts[0].pages[0];
  assert.equal(page.fills.length, 1, 'tylko pierwszy krok (jeszcze na dozwolonym origin)');
  assert.equal(page.clicks.length, 0);
});

test('B1 login: kontrola - bez przekierowania wszystkie kroki sa wykonane', async () => {
  const c = captureSetup({
    pages: [PAGE()],
    login: { url: 'http://localhost:3000/login', steps: [{ fill: { selector: '#u', valueEnv: 'UI_VERIFY_A' } }, { click: '#go' }] },
  });
  await c.adapter.login({ login: c.v.config.login, env: { UI_VERIFY_A: 'user-1234' } });
  const page = c.browser.contexts[0].pages[0];
  assert.equal(page.fills.length, 1);
  assert.equal(page.clicks.length, 1);
});

test('B1 capture/measure: adres strony po nawigacji poza polityka (przekierowanie bez route) => PolicyError PRZED zrzutem', async () => {
  const c = captureSetup({ pages: [PAGE()], browserOpts: { finalUrl: () => 'https://evil.example/landing' } });
  await assert.rejects(c.adapter.capture({ page: c.page, width: 400, mode: 'light' }), policyError);
  assert.equal(c.browser.contexts[0].pages[0].shots, 0);
  const m = measureSetup({ browserOpts: { finalUrl: () => 'https://evil.example/landing' } });
  await assert.rejects(m.adapter.measure(m.side), policyError);
  assert.equal(m.browser.contexts[0].pages[0].locatorShots, 0);
});

// --- N7 ---

test('N7: kontekst measure ma acceptDownloads: false (jak capture)', async () => {
  const m = measureSetup();
  await m.adapter.measure(m.side);
  assert.equal(m.browser.contexts[0].options.acceptDownloads, false);
  const c = captureSetup({ pages: [PAGE()] });
  await c.adapter.capture({ page: c.page, width: 400, mode: 'light' });
  assert.equal(c.browser.contexts[0].options.acceptDownloads, false);
});

// --- B3: symlinki dla file:// ---

function symlinkedPage() {
  const dir = newDir('proj');
  const outside = newDir('elsewhere');
  fs.writeFileSync(path.join(outside, 'secret.html'), '<h1>secret</h1>');
  fs.writeFileSync(path.join(dir, 'real.html'), '<h1>ok</h1>');
  fs.symlinkSync(path.join(outside, 'secret.html'), path.join(dir, 'link.html'), 'file');
  return { dir, linkUrl: pathToFileURL(path.join(dir, 'link.html')).href, realUrl: pathToFileURL(path.join(dir, 'real.html')).href };
}

function captureIn(dir, pageUrl, browserOpts = {}) {
  const v = validateCaptureConfig({ version: 1, pages: [{ id: 'home', url: pageUrl, assert: { selector: 'h1' } }] }, { configDir: dir, allowOrigins: [], env: {} });
  assert.equal(v.ok, true, String(v.errors));
  const browser = fakeBrowser(browserOpts);
  return { browser, v, adapter: createCaptureAdapter({ browser, PNG: null, config: v.config, configDir: dir, allowOrigins: [] }) };
}

test('B3 capture: strona HTML bedaca symlinkiem na plik poza configDir => PolicyError i brak goto', { skip: SYMLINK_SKIP }, async () => {
  const { dir, linkUrl } = symlinkedPage();
  const { browser, v, adapter } = captureIn(dir, linkUrl);
  await assert.rejects(adapter.capture({ page: v.config.pages[0], width: 400, mode: 'light' }), policyError);
  for (const ctx of browser.contexts) for (const pg of ctx.pages) assert.deepEqual(pg.gotos, [], 'goto nie moze sie wykonac');
});

test('B3 capture, kontrola: zwykly plik pod configDir jest otwierany', { skip: SYMLINK_SKIP }, async () => {
  const { dir, realUrl } = symlinkedPage();
  const { browser, v, adapter } = captureIn(dir, realUrl);
  await adapter.capture({ page: v.config.pages[0], width: 400, mode: 'light' });
  assert.deepEqual(browser.contexts[0].pages[0].gotos, [realUrl]);
});

test('B3 measure: strona HTML bedaca symlinkiem na plik poza configDir => PolicyError i brak goto', { skip: SYMLINK_SKIP }, async () => {
  const { dir, linkUrl } = symlinkedPage();
  const v = validateConfig({ version: 1, pairs: [{ id: 'p', reference: { url: linkUrl, selector: '#t' }, candidate: { url: linkUrl, selector: '#t' } }] }, { configDir: dir, allowOrigins: [] });
  assert.equal(v.ok, true, String(v.errors));
  const browser = fakeBrowser();
  const adapter = createPlaywrightAdapter({ browser, PNG: null, config: v.config, configDir: dir, allowOrigins: [] });
  await assert.rejects(adapter.measure(v.config.pairs[0].reference), policyError);
  for (const ctx of browser.contexts) for (const pg of ctx.pages) assert.deepEqual(pg.gotos, []);
});

// --- A10: czas oczekiwania na asercje pozytywna ---

test('A10: asercja pozytywna czeka locator.waitFor({ state: "attached", timeout }) - domyslnie 5000 ms', async () => {
  const c = captureSetup({ pages: [PAGE()] });
  const r = await c.adapter.capture({ page: c.page, width: 400, mode: 'light' });
  const waits = c.browser.contexts[0].pages[0].waits.filter((w) => w.selector === 'h1');
  assert.equal(waits.length, 1, JSON.stringify(c.browser.contexts[0].pages[0].waits));
  assert.equal(waits[0].options.state, 'attached');
  assert.equal(waits[0].options.timeout, 5000);
  assert.equal(r.assertionFound, true);
});

test('A10: assertTimeoutMs z konfiguracji i ze strony (strona wygrywa) trafia do waitFor', async () => {
  const top = captureSetup({ pages: [PAGE()], extra: { assertTimeoutMs: 1234 } });
  await top.adapter.capture({ page: top.page, width: 400, mode: 'light' });
  assert.equal(top.browser.contexts[0].pages[0].waits.find((w) => w.selector === 'h1').options.timeout, 1234);
  const both = captureSetup({ pages: [PAGE({ assertTimeoutMs: 777 })], extra: { assertTimeoutMs: 1234 } });
  await both.adapter.capture({ page: both.page, width: 400, mode: 'light' });
  assert.equal(both.browser.contexts[0].pages[0].waits.find((w) => w.selector === 'h1').options.timeout, 777);
});

test('A10: element, ktory nie pojawia sie w limicie (waitFor rzuca timeout) => assertionFound false', async () => {
  const c = captureSetup({ pages: [PAGE()], browserOpts: { missingSelectors: ['h1'] } });
  const r = await c.adapter.capture({ page: c.page, width: 400, mode: 'light' });
  assert.equal(r.assertionFound, false);
  assert.ok(r.png instanceof Uint8Array);
});

// --- C5: zly stan strony (przekierowanie) ---

test('C5 measure: koncowy adres inny niz zadany (przekierowanie) => { found: false, reason: "page-state-mismatch" }', async () => {
  const m = measureSetup({ browserOpts: { finalUrl: () => 'http://localhost:3000/login' } });
  const r = await m.adapter.measure(m.side);
  assert.equal(r.found, false);
  assert.equal(r.reason, 'page-state-mismatch');
});

test('C5 measure: query i fragment sa pomijane w porownaniu adresu (brak mismatch)', async () => {
  const m = measureSetup({ urls: ['http://localhost:3000/home?tab=1#top', 'http://localhost:3000/home?tab=1#top'], browserOpts: { finalUrl: () => 'http://localhost:3000/home?tab=2#other' } });
  const r = await m.adapter.measure(m.side);
  assert.equal(r.found, true, JSON.stringify(r));
  assert.notEqual(r.reason, 'page-state-mismatch');
});

test('C5 measure: file:// - przekierowanie na inny plik pod configDir => page-state-mismatch; ten sam plik => found', async () => {
  const dir = newDir('c5');
  fs.writeFileSync(path.join(dir, 'a.html'), '<p>a</p>');
  fs.writeFileSync(path.join(dir, 'b.html'), '<p>b</p>');
  const aUrl = pathToFileURL(path.join(dir, 'a.html')).href;
  const bUrl = pathToFileURL(path.join(dir, 'b.html')).href;
  const build = (finalUrl) => {
    const v = validateConfig({ version: 1, pairs: [{ id: 'p', reference: { url: aUrl, selector: '#t' }, candidate: { url: aUrl, selector: '#t' } }] }, { configDir: dir, allowOrigins: [] });
    assert.equal(v.ok, true, String(v.errors));
    return { v, adapter: createPlaywrightAdapter({ browser: fakeBrowser({ finalUrl }), PNG: null, config: v.config, configDir: dir, allowOrigins: [] }) };
  };
  const moved = build(() => bUrl);
  const r = await moved.adapter.measure(moved.v.config.pairs[0].reference);
  assert.equal(r.reason, 'page-state-mismatch');
  const same = build((u) => u);
  const ok = await same.adapter.measure(same.v.config.pairs[0].reference);
  assert.equal(ok.found, true);
});

test('C5 capture: przekierowanie => { stateMismatch: true } z PNG; brak przekierowania (takze z query/fragmentem) => bez stateMismatch', async () => {
  const moved = captureSetup({ pages: [PAGE()], browserOpts: { finalUrl: () => 'http://localhost:3000/login' } });
  const r = await moved.adapter.capture({ page: moved.page, width: 400, mode: 'light' });
  assert.equal(r.stateMismatch, true);
  assert.ok(r.png instanceof Uint8Array);
  const same = captureSetup({ pages: [PAGE({ url: `${HOME_URL}?a=1#x` })], browserOpts: { finalUrl: () => `${HOME_URL}?a=2#y` } });
  const ok = await same.adapter.capture({ page: same.page, width: 400, mode: 'light' });
  assert.ok(!ok.stateMismatch, JSON.stringify(ok));
});
