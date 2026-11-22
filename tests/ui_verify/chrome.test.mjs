import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';

const load = () => import('../../library/skills/ui-verify/scripts/lib/chrome.mjs');

function mk({ existing = [], whichMap = {} } = {}) {
  const exists = new Set(existing);
  const checked = [];
  const whichCalls = [];
  return {
    fs: { existsSync: (p) => { checked.push(p); return exists.has(p); } },
    which: (n) => { whichCalls.push(n); return whichMap[n] ?? null; },
    checked, whichCalls,
  };
}

test('findChrome: UI_VERIFY_CHROME ma pierwszenstwo przed CHROME_EXECUTABLE_PATH', async () => {
  const { findChrome } = await load();
  const m = mk({ existing: ['/a/chrome', '/b/chrome'] });
  const r = findChrome({ platform: 'linux', env: { UI_VERIFY_CHROME: '/a/chrome', CHROME_EXECUTABLE_PATH: '/b/chrome' }, ...m });
  assert.equal(r.path, '/a/chrome');
  assert.equal(r.source, 'UI_VERIFY_CHROME');
});

test('findChrome: nieistniejacy plik ze zmiennej trafia do tried i nie przerywa', async () => {
  const { findChrome } = await load();
  const m = mk({ existing: ['/b/chrome'] });
  const r = findChrome({ platform: 'linux', env: { UI_VERIFY_CHROME: '/nie/ma', CHROME_EXECUTABLE_PATH: '/b/chrome' }, ...m });
  assert.equal(r.path, '/b/chrome');
  assert.equal(r.source, 'CHROME_EXECUTABLE_PATH');
  assert.ok(r.tried.includes('/nie/ma'));
});

test('findChrome: pusta zmienna jest pomijana (nie trafia do tried)', async () => {
  const { findChrome } = await load();
  const r = findChrome({ platform: 'linux', env: { UI_VERIFY_CHROME: '' }, ...mk() });
  assert.ok(!r.tried.includes(''));
});

test('findChrome linux: which w kolejnosci, source system, pomija brakujace', async () => {
  const { findChrome } = await load();
  const m = mk({ whichMap: { chromium: '/usr/bin/chromium', 'chromium-browser': '/usr/bin/chromium-browser' } });
  const r = findChrome({ platform: 'linux', env: {}, ...m });
  assert.deepEqual(m.whichCalls, ['google-chrome', 'google-chrome-stable', 'chromium']);
  assert.equal(r.path, '/usr/bin/chromium');
  assert.equal(r.source, 'system');
});

test('findChrome linux: pelna kolejnosc nazw gdy nic nie znaleziono', async () => {
  const { findChrome } = await load();
  const m = mk();
  const r = findChrome({ platform: 'linux', env: {}, ...m });
  assert.deepEqual(m.whichCalls, ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']);
  assert.deepEqual({ path: r.path, source: r.source }, { path: null, source: null });
});

test('findChrome linux: zmienna wygrywa z which (which nie jest wolany)', async () => {
  const { findChrome } = await load();
  const m = mk({ existing: ['/c'], whichMap: { chromium: '/usr/bin/chromium' } });
  findChrome({ platform: 'linux', env: { UI_VERIFY_CHROME: '/c' }, ...m });
  assert.deepEqual(m.whichCalls, []);
});

test('findChrome darwin: standardowa sciezka aplikacji', async () => {
  const { findChrome } = await load();
  const p = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const r = findChrome({ platform: 'darwin', env: {}, ...mk({ existing: [p] }) });
  assert.equal(r.path, p);
  assert.equal(r.source, 'system');
  const none = findChrome({ platform: 'darwin', env: {}, ...mk() });
  assert.equal(none.path, null);
  assert.ok(none.tried.includes(p));
});

test('findChrome win32: kolejnosc PROGRAMFILES, PROGRAMFILES(X86), LOCALAPPDATA, potem Edge', async () => {
  const { findChrome } = await load();
  const env = { PROGRAMFILES: 'C:\\PF', 'PROGRAMFILES(X86)': 'C:\\PF86', LOCALAPPDATA: 'C:\\LA' };
  const w = (...s) => path.win32.join(...s);
  const chrome = (b) => w(b, 'Google', 'Chrome', 'Application', 'chrome.exe');
  const edge = (b) => w(b, 'Microsoft', 'Edge', 'Application', 'msedge.exe');
  const m0 = mk();
  const none = findChrome({ platform: 'win32', env, ...m0 });
  assert.equal(none.path, null);
  const order = [chrome('C:\\PF'), chrome('C:\\PF86'), chrome('C:\\LA'), edge('C:\\PF86'), edge('C:\\PF')];
  assert.deepEqual(none.tried.filter((t) => order.includes(t)), order);
  const r1 = findChrome({ platform: 'win32', env, ...mk({ existing: [chrome('C:\\LA'), edge('C:\\PF')] }) });
  assert.equal(r1.path, chrome('C:\\LA'));
  const r2 = findChrome({ platform: 'win32', env, ...mk({ existing: [edge('C:\\PF'), edge('C:\\PF86')] }) });
  assert.equal(r2.path, edge('C:\\PF86'));
  assert.equal(r2.source, 'system');
});

test('findChrome win32: brak zmiennych srodowiska nie rzuca', async () => {
  const { findChrome } = await load();
  const r = findChrome({ platform: 'win32', env: {}, ...mk() });
  assert.equal(r.path, null);
});

test('launchOptions: domyslnie z sandboxem, bez --no-sandbox', async () => {
  const { launchOptions } = await load();
  const o = launchOptions({ chromePath: '/c', env: {} });
  assert.equal(o.executablePath, '/c');
  assert.equal(o.chromiumSandbox, true);
  assert.equal(o.headless, true);
  assert.ok(!o.args.includes('--no-sandbox'));
});

test('launchOptions: UI_VERIFY_NO_SANDBOX=1 wylacza sandbox i dodaje flage; inne wartosci nie', async () => {
  const { launchOptions } = await load();
  const o = launchOptions({ chromePath: '/c', env: { UI_VERIFY_NO_SANDBOX: '1' } });
  assert.equal(o.chromiumSandbox, false);
  assert.ok(o.args.includes('--no-sandbox'));
  for (const v of ['0', 'true', '']) {
    const x = launchOptions({ chromePath: '/c', env: { UI_VERIFY_NO_SANDBOX: v } });
    assert.equal(x.chromiumSandbox, true, v);
    assert.ok(!x.args.includes('--no-sandbox'), v);
  }
});

test('chromeMissing: kod 3, lista prob i wskazanie UI_VERIFY_CHROME', async () => {
  const { chromeMissing } = await load();
  const r = chromeMissing({ path: null, tried: ['/nie/ma', 'google-chrome'], source: null });
  assert.equal(r.exit, 3);
  const text = r.lines.join('\n');
  assert.match(text, /UI_VERIFY_CHROME/);
  assert.match(text, /\/nie\/ma/);
  assert.match(text, /google-chrome/);
});
