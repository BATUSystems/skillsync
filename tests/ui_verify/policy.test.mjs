import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveInside, safeName, checkOrigin } from '../../library/skills/ui-verify/scripts/lib/policy.mjs';

const POSIX_ROOT = '/work/proj';
const WIN_ROOT = 'C:\\work\\proj';

function outside(fn) {
  assert.throws(fn, (e) => e && e.name === 'PolicyError' && e.code === 'outside-root');
}

test('resolveInside posix: zwykle sciezki wewnatrz', () => {
  assert.equal(resolveInside(POSIX_ROOT, 'a/b.png', { platform: 'posix' }), '/work/proj/a/b.png');
  assert.equal(resolveInside(POSIX_ROOT, './a/../b.png', { platform: 'posix' }), '/work/proj/b.png');
});

test('resolveInside posix: .., bezwzgledna, backslash, null byte', () => {
  outside(() => resolveInside(POSIX_ROOT, '../x', { platform: 'posix' }));
  outside(() => resolveInside(POSIX_ROOT, 'a/../../x', { platform: 'posix' }));
  outside(() => resolveInside(POSIX_ROOT, '/etc/passwd', { platform: 'posix' }));
  outside(() => resolveInside(POSIX_ROOT, '..\\x', { platform: 'posix' }));
  outside(() => resolveInside(POSIX_ROOT, 'a\0b', { platform: 'posix' }));
});

test('resolveInside win32: wewnatrz, .., litera dysku, backslash, null byte', () => {
  const ok = resolveInside(WIN_ROOT, 'a/b.png', { platform: 'win32' });
  assert.ok(/^C:\\work\\proj\\a\\b\.png$/i.test(ok), ok);
  outside(() => resolveInside(WIN_ROOT, '..\\x', { platform: 'win32' }));
  outside(() => resolveInside(WIN_ROOT, '../x', { platform: 'win32' }));
  outside(() => resolveInside(WIN_ROOT, 'D:\\x', { platform: 'win32' }));
  outside(() => resolveInside(WIN_ROOT, '\\\\server\\share\\x', { platform: 'win32' }));
  outside(() => resolveInside(WIN_ROOT, 'a\0b', { platform: 'win32' }));
});

test('resolveInside: sama sciezka root jest dozwolona, sasiedni katalog o wspolnym prefiksie nie', () => {
  assert.equal(resolveInside(POSIX_ROOT, '.', { platform: 'posix' }), '/work/proj');
  outside(() => resolveInside(POSIX_ROOT, '../proj-evil/x', { platform: 'posix' }));
});

test('safeName: dozwolone znaki i dlugosc', () => {
  assert.equal(safeName('shot-1_a.b.png'), 'shot-1_a.b.png');
  assert.equal(safeName('a'.repeat(120)), 'a'.repeat(120));
});

test('safeName: bledne => PolicyError bad-name', () => {
  for (const bad of ['', 'a'.repeat(121), '.', '..', 'a b', 'a/b', 'a\\b', 'zażółć', 'a\0', 'x:y']) {
    assert.throws(() => safeName(bad), (e) => e.name === 'PolicyError' && e.code === 'bad-name', JSON.stringify(bad));
  }
  assert.throws(() => safeName(undefined), (e) => e.code === 'bad-name');
});

const CFG = '/work/proj/cfg';

test('checkOrigin: localhost, 127.0.0.1, [::1] z dowolnym portem', () => {
  for (const u of ['http://localhost', 'https://localhost:8443/x', 'http://127.0.0.1:3000/', 'http://[::1]:5173/a?b=1']) {
    assert.equal(checkOrigin(u, { configDir: CFG }).ok, true, u);
  }
});

test('checkOrigin: obcy host odrzucony z reason', () => {
  for (const u of ['https://example.com', 'http://localhost.evil.example', 'http://127.0.0.2', 'http://192.168.0.5']) {
    const r = checkOrigin(u, { configDir: CFG });
    assert.equal(r.ok, false, u);
    assert.equal(typeof r.reason, 'string');
  }
});

test('checkOrigin: file:// pod configDir tak, poza nim nie', () => {
  assert.equal(checkOrigin('file:///work/proj/cfg/page.html', { configDir: CFG }).ok, true);
  assert.equal(checkOrigin('file:///work/proj/cfg/sub/page.html', { configDir: CFG }).ok, true);
  assert.equal(checkOrigin('file:///etc/passwd', { configDir: CFG }).ok, false);
  assert.equal(checkOrigin('file:///work/proj/cfg/../secret.html', { configDir: CFG }).ok, false);
  assert.equal(checkOrigin('file:///work/proj/cfg-evil/page.html', { configDir: CFG }).ok, false);
});

test('checkOrigin: javascript:, data:, chrome: zawsze odrzucone nawet z allow', () => {
  const allow = ['*', 'javascript:*', 'data:*', 'chrome:*', '*.example'];
  for (const u of ['javascript:alert(1)', 'data:text/html,<b>x</b>', 'chrome://settings', 'chrome://version']) {
    assert.equal(checkOrigin(u, { configDir: CFG }).ok, false, u);
    assert.equal(checkOrigin(u, { configDir: CFG, allow }).ok, false, `allow ${u}`);
  }
});

test('checkOrigin: wzorce allow (dokladny host i *.domena)', () => {
  assert.equal(checkOrigin('https://app.example', { configDir: CFG }).ok, false);
  assert.equal(checkOrigin('https://app.example/x', { configDir: CFG, allow: ['https://app.example'] }).ok, true);
  assert.equal(checkOrigin('https://other.example', { configDir: CFG, allow: ['https://app.example'] }).ok, false);
  assert.equal(checkOrigin('https://a.example', { configDir: CFG, allow: ['*.example'] }).ok, true);
  assert.equal(checkOrigin('https://x.y.example', { configDir: CFG, allow: ['*.example'] }).ok, true);
  assert.equal(checkOrigin('https://example.org', { configDir: CFG, allow: ['*.example'] }).ok, false);
  assert.equal(checkOrigin('https://evilexample', { configDir: CFG, allow: ['*.example'] }).ok, false);
});

test('checkOrigin: nieparsowalny URL => ok false', () => {
  assert.equal(checkOrigin('not a url', { configDir: CFG }).ok, false);
  assert.equal(checkOrigin('', { configDir: CFG }).ok, false);
});
