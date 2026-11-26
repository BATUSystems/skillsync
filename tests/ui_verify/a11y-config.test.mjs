import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import os from 'node:os';
import { pathToFileURL } from 'node:url';
import {
  A11yConfigError, configDigest, loginEnvNames, missingLoginEnv, parseConfigText, validateA11yConfig,
} from '../../library/skills/ui-verify/scripts/lib/a11y-config.mjs';

const DIR = path.join(os.tmpdir(), 'a11y-cfg-test');
const base = (over = {}) => ({ version: 1, pages: [{ id: 'home', url: 'page.html', assert: { selector: 'main', text: 'Hello' } }], ...over });
const v = (cfg, opts = {}) => validateA11yConfig(cfg, { configDir: DIR, ...opts });

test('minimalna konfiguracja: wartosci domyslne', () => {
  const r = v(base());
  assert.equal(r.ok, true, r.errors.join('; '));
  const c = r.config;
  assert.deepEqual(c.viewport, { width: 1280, height: 800, deviceScaleFactor: 1 });
  assert.deepEqual(c.thresholds, { normal: 4.5, large: 3 });
  assert.equal(c.focus.minDiffPx, 4);
  assert.equal(c.focus.mouse, null);
  assert.deepEqual(c.tab, { maxStops: 500, positiveTabindex: 'fail' });
  assert.deepEqual(c.order, { mode: 'warn', tolerancePx: 32 });
  assert.equal(c.pages[0].url, pathToFileURL(path.join(DIR, 'page.html')).href);
  assert.deepEqual(c.pages[0].checks, ['contrast', 'target-size', 'focus', 'tab-order', 'console']);
});

test('nieznany klucz na kazdym poziomie to blad', () => {
  assert.equal(v(base({ bogus: 1 })).ok, false);
  assert.equal(v(base({ pages: [{ id: 'p', url: 'a.html', bogus: 1 }] })).ok, false);
  assert.equal(v(base({ focus: { bogus: 1 } })).ok, false);
  assert.equal(v(base({ tab: { bogus: 1 } })).ok, false);
  assert.equal(v(base({ order: { bogus: 1 } })).ok, false);
  assert.equal(v(base({ viewport: { bogus: 1 } })).ok, false);
  assert.equal(v(base({ pages: [{ id: 'p', url: 'a.html', assert: { selector: 'a', bogus: 1 } }] })).ok, false);
  assert.equal(v({ version: 2, pages: base().pages }).ok, false);
  assert.equal(v([]).ok, false);
});

test('id strony przez safeName, duplikaty i brak stron to bledy', () => {
  assert.equal(v(base({ pages: [{ id: '../x', url: 'a.html' }] })).ok, false);
  assert.equal(v(base({ pages: [{ id: 'a b', url: 'a.html' }] })).ok, false);
  assert.equal(v(base({ pages: [{ id: 'a', url: 'a.html' }, { id: 'a', url: 'b.html' }] })).ok, false);
  assert.equal(v(base({ pages: [] })).ok, false);
  assert.equal(v(base({ pages: [{ url: 'a.html' }] })).ok, false);
});

test('url poza katalogiem konfiguracji albo z obcego originu to blad, --allow-origin pomaga', () => {
  assert.equal(v(base({ pages: [{ id: 'p', url: '../outside.html' }] })).ok, false);
  assert.equal(v(base({ pages: [{ id: 'p', url: 'https://example.org/x' }] })).ok, false);
  assert.equal(v(base({ pages: [{ id: 'p', url: 'https://example.org/x' }] }), { allowOrigins: ['example.org'] }).ok, true);
  assert.equal(v(base({ pages: [{ id: 'p', url: 'http://localhost:3000/' }] })).ok, true);
});

test('checks: podzbior znanych kontroli', () => {
  assert.deepEqual(v(base({ pages: [{ id: 'p', url: 'a.html', checks: ['contrast', 'console', 'contrast'] }] })).config.pages[0].checks, ['contrast', 'console']);
  assert.equal(v(base({ pages: [{ id: 'p', url: 'a.html', checks: ['nope'] }] })).ok, false);
});

test('thresholds: ponizej 4,5 / 3,0 wymaga reason, z reason jest ostrzezenie', () => {
  assert.equal(v(base({ thresholds: { normal: 4.0 } })).ok, false);
  const ok = v(base({ thresholds: { normal: 4.0, reason: 'accepted' } }));
  assert.equal(ok.ok, true);
  assert.equal(ok.config.warnings[0].code, 'threshold-lowered');
  assert.equal(v(base({ thresholds: { large: 7 } })).config.thresholds.large, 7);
});

test('valueEnv: tylko UI_VERIFY_* albo nazwa z UI_VERIFY_SECRET_ENV, inne zmienne to blad bez cytowania nazwy', () => {
  const cfg = (name) => base({ login: { url: 'login.html', steps: [{ fill: { selector: '#u', valueEnv: name } }] } });
  const bad = v(cfg('GITHUB_TOKEN'));
  assert.equal(bad.ok, false);
  assert.ok(bad.errors.some((e) => e.includes('valueEnv')) && !bad.errors.join('\n').includes('GITHUB_TOKEN'));
  assert.equal(v(cfg('GITHUB_TOKEN'), { env: { UI_VERIFY_SECRET_ENV: 'OTHER, GITHUB_TOKEN' } }).ok, true);
  assert.equal(v(cfg('UI_VERIFY_ANY')).ok, true);
});

test('login: fill tylko z valueEnv, literal to blad bez cytowania wartosci', () => {
  const literal = v(base({ login: { url: 'login.html', steps: [{ fill: { selector: '#u', value: 'hunter2-literal' } }] } }));
  assert.equal(literal.ok, false);
  assert.ok(literal.errors.some((e) => e.includes('valueEnv')));
  assert.ok(!literal.errors.join('\n').includes('hunter2-literal'));
  const good = v(base({ login: { url: 'login.html', steps: [{ fill: { selector: '#u', valueEnv: 'UI_VERIFY_A11Y_USER' } }, { click: '#go' }] } }));
  assert.equal(good.ok, true, good.errors.join('; '));
  assert.deepEqual(loginEnvNames(base({ login: { url: 'x', steps: [{ fill: { selector: '#u', valueEnv: 'UI_VERIFY_A11Y_USER' } }] } })), ['UI_VERIFY_A11Y_USER']);
  assert.deepEqual(missingLoginEnv(good.config, { UI_VERIFY_A11Y_USER: 'x' }), []);
  assert.deepEqual(missingLoginEnv(good.config, {}), ['UI_VERIFY_A11Y_USER']);
  assert.equal(v(base({ login: { url: 'login.html', steps: [{ fill: { selector: '#u', valueEnv: 'UI_VERIFY_A11Y_USER' }, click: '#x' }] } })).ok, false);
  assert.equal(v(base({ login: { url: 'login.html', steps: [] } })).ok, false);
});

test('focus: minDiffPx i mouse z lista selektorow do 20', () => {
  const ok = v(base({ focus: { minDiffPx: 6, mouse: { mode: 'forbid', selectors: ['#a', '#b'] } } }));
  assert.equal(ok.ok, true);
  assert.deepEqual(ok.config.focus.mouse, { mode: 'forbid', selectors: ['#a', '#b'] });
  assert.equal(ok.config.focus.minDiffPx, 6);
  assert.equal(v(base({ focus: { minDiffPx: 0 } })).ok, false);
  assert.equal(v(base({ focus: { mouse: { mode: 'forbid', selectors: Array.from({ length: 21 }, (_, i) => `#a${i}`) } } })).ok, false);
});

test('tab i order: zakresy i wartosci', () => {
  assert.equal(v(base({ tab: { maxStops: 501 } })).ok, false);
  assert.equal(v(base({ tab: { maxStops: 50, positiveTabindex: 'warn' } })).config.tab.positiveTabindex, 'warn');
  assert.equal(v(base({ tab: { positiveTabindex: 'maybe' } })).ok, false);
  assert.equal(v(base({ order: { mode: 'fail', tolerancePx: 10 } })).config.order.mode, 'fail');
  assert.equal(v(base({ order: { mode: 'x' } })).ok, false);
});

test('exceptionsFile: tylko pod katalogiem konfiguracji', () => {
  assert.equal(v(base({ exceptionsFile: 'exceptions.json' })).config.exceptionsFile, path.join(DIR, 'exceptions.json'));
  assert.equal(v(base({ exceptionsFile: '../exceptions.json' })).ok, false);
  assert.equal(v(base({ exceptionsFile: '/etc/passwd' })).ok, false);
});

test('secretEnv: nazwy zmiennych', () => {
  assert.deepEqual(v(base({ secretEnv: ['TOKEN_A'] })).config.secretEnv, ['TOKEN_A']);
  assert.equal(v(base({ secretEnv: ['bad name'] })).ok, false);
});

test('blad JSON nie cytuje tresci pliku', () => {
  assert.throws(() => parseConfigText('{"password": "hunter2-literal", oops}'), (e) => e instanceof A11yConfigError && !e.message.includes('hunter2') && !e.message.includes('password'));
  assert.deepEqual(parseConfigText('{"a":1}'), { a: 1 });
});

test('skrot konfiguracji jest stabilny i nie zalezy od kolejnosci kluczy', () => {
  assert.equal(configDigest({ a: 1, b: { c: 2, d: 3 } }), configDigest({ b: { d: 3, c: 2 }, a: 1 }));
  assert.notEqual(configDigest({ a: 1 }), configDigest({ a: 2 }));
  assert.match(configDigest({ a: 1 }), /^[0-9a-f]{12}$/);
});

test('deviceScaleFactor: tylko 1 albo pominiety', () => {
  assert.equal(v(base({ viewport: { deviceScaleFactor: 2 } })).ok, false);
  assert.equal(v(base({ viewport: { deviceScaleFactor: 1, width: 800 } })).ok, true);
});

test('configDigest daje ten sam wynik dla exceptionsFile zapisanego sciezka Windows i POSIX', () => {
  const mk = (exceptionsFile) => ({ version: 1, exceptionsFile });
  const posix = configDigest(mk('/cfg/sub/ex.json'), '/cfg', { pathImpl: path.posix });
  const windows = configDigest(mk('C:\\work\\cfg\\sub\\ex.json'), 'C:\\work\\cfg', { pathImpl: path.win32 });
  assert.equal(windows, posix);
  // another file still gives another digest
  assert.notEqual(configDigest(mk('C:\\work\\cfg\\sub\\other.json'), 'C:\\work\\cfg', { pathImpl: path.win32 }), posix);
  // on POSIX a backslash is part of the file name and is kept
  assert.notEqual(configDigest(mk('/cfg/sub\\ex.json'), '/cfg', { pathImpl: path.posix }), posix);
});

test('configDigest nie zalezy od sciezki bezwzglednej katalogu konfiguracji', () => {
  const mk = (dir) => validateA11yConfig(base({ exceptionsFile: 'ex.json' }), { configDir: dir }).config;
  const d1 = path.join(os.tmpdir(), 'a11y-one');
  const d2 = path.join(os.tmpdir(), 'a11y-two');
  assert.equal(configDigest(mk(d1), d1), configDigest(mk(d2), d2));
  assert.notEqual(configDigest(mk(d1)), configDigest(mk(d2)));
});

test('focus.contrast i focus.appearance: off, report, fail sa przyjmowane i zachowane', () => {
  for (const key of ['contrast', 'appearance']) {
    for (const mode of ['off', 'report', 'fail']) {
      const r = v(base({ focus: { [key]: mode } }));
      assert.equal(r.ok, true, r.errors.join('; '));
      assert.equal(r.config.focus[key], mode);
      assert.deepEqual(Object.keys(r.config.focus).sort(), ['minDiffPx', 'mouse', key].sort());
    }
  }
});

test('focus.contrast i focus.appearance: inna wartosc to blad bez cytowania tresci', () => {
  for (const key of ['contrast', 'appearance']) {
    for (const bad of ['warn-hunter2', 'FAIL', '', null, true, 1, ['fail'], { mode: 'hunter2' }]) {
      const r = v(base({ focus: { [key]: bad } }));
      assert.equal(r.ok, false, `${key}=${JSON.stringify(bad)}`);
      assert.equal(r.config, null);
      assert.deepEqual(r.errors, [`focus.${key}: expected "off", "report" or "fail"`]);
    }
  }
});

test('digest bez zmian: bez nowych kluczy focus nic nie jest dopisywane do konfiguracji', () => {
  // recorded with the code from before the new keys; a http URL keeps the digest independent of the directory
  const cfg = (focus) => v(base({ pages: [{ id: 'p', url: 'http://localhost:3000/', assert: { selector: 'main' } }], ...(focus ? { focus } : {}) })).config;
  for (const [focus, expected] of [[null, '62daa0377524'], [{}, '62daa0377524'], [{ minDiffPx: 9 }, 'd5627b6381c9']]) {
    const c = cfg(focus);
    assert.deepEqual(Object.keys(c.focus), ['minDiffPx', 'mouse']);
    assert.equal(configDigest(c, DIR), expected);
  }
  assert.notEqual(configDigest(cfg({ contrast: 'off' }), DIR), '62daa0377524');
});
