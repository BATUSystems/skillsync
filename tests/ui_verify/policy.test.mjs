import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import * as policy from '../../library/skills/ui-verify/scripts/lib/policy.mjs';

const { resolveInside, safeName, checkOrigin } = policy;

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

// Sciezki natywne dla platformy (na Windows file:///work/... nie jest sciezka bezwzgledna).
const CFG = path.resolve(os.tmpdir(), 'uv-policy', 'cfg');
const fileUrl = (...parts) => pathToFileURL(path.join(CFG, ...parts)).href;

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
  assert.equal(checkOrigin(fileUrl('page.html'), { configDir: CFG }).ok, true);
  assert.equal(checkOrigin(fileUrl('sub', 'page.html'), { configDir: CFG }).ok, true);
  assert.equal(checkOrigin(pathToFileURL(path.resolve(os.tmpdir(), 'uv-policy-other', 'passwd')).href, { configDir: CFG }).ok, false);
  assert.equal(checkOrigin(pathToFileURL(path.join(CFG, '..', 'secret.html')).href, { configDir: CFG }).ok, false);
  assert.equal(checkOrigin(pathToFileURL(path.join(`${CFG}-evil`, 'page.html')).href, { configDir: CFG }).ok, false);
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

// --- assertRealInside (B3): realpath najblizszego istniejacego przodka w realpath(root), cel nie jest symlinkiem ---

const assertRealInside = (...a) => policy.assertRealInside(...a);
const policyError = (e) => e && e.name === 'PolicyError';

// Tworzenie symlinkow bywa zablokowane (Windows bez uprawnien): wtedy testy sa pomijane z powodem needs-symlink.
const SYMLINK_SKIP = (() => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-policy-probe-'));
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

function realTree() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-policy-real-'));
  const root = path.join(base, 'root');
  const outside = path.join(base, 'outside');
  fs.mkdirSync(path.join(root, 'sub'), { recursive: true });
  fs.mkdirSync(outside);
  fs.writeFileSync(path.join(root, 'sub', 'page.html'), '<p>x</p>');
  fs.writeFileSync(path.join(outside, 'secret.html'), '<p>secret</p>');
  return { base, root, outside };
}

test('assertRealInside: istniejacy plik i nieistniejacy cel pod prawdziwym katalogiem w root => bez wyjatku', () => {
  const { base, root } = realTree();
  try {
    assert.doesNotThrow(() => assertRealInside(root, path.join(root, 'sub', 'page.html'), fs));
    assert.doesNotThrow(() => assertRealInside(root, path.join(root, 'sub', 'new', 'deep', 'a.png'), fs));
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('assertRealInside: symlink katalogu w root na katalog poza root => PolicyError (takze dla nieistniejacego pliku pod nim)', { skip: SYMLINK_SKIP }, () => {
  const { base, root, outside } = realTree();
  try {
    fs.symlinkSync(outside, path.join(root, 'link'), 'junction');
    assert.throws(() => assertRealInside(root, path.join(root, 'link', 'secret.html'), fs), policyError);
    assert.throws(() => assertRealInside(root, path.join(root, 'link', 'new.png'), fs), policyError);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('assertRealInside: sam cel bedacy symlinkiem do pliku poza root => PolicyError; do pliku w root tez (lstat)', { skip: SYMLINK_SKIP }, () => {
  const { base, root, outside } = realTree();
  try {
    fs.symlinkSync(path.join(outside, 'secret.html'), path.join(root, 'out.html'), 'file');
    fs.symlinkSync(path.join(root, 'sub', 'page.html'), path.join(root, 'in.html'), 'file');
    assert.throws(() => assertRealInside(root, path.join(root, 'out.html'), fs), policyError);
    assert.throws(() => assertRealInside(root, path.join(root, 'in.html'), fs), policyError);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('assertRealInside: sciezka leksykalnie poza root => PolicyError nawet bez symlinkow', () => {
  const { base, root, outside } = realTree();
  try {
    assert.throws(() => assertRealInside(root, path.join(outside, 'secret.html'), fs), policyError);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('assertRealInside: wstrzykiwane fs (atrapa realpathSync/lstatSync) - symlink na zewnatrz odrzucony, brak symlinku przyjety', () => {
  const root = path.resolve(os.tmpdir(), 'uv-policy-fake', 'root');
  const outside = path.resolve(os.tmpdir(), 'uv-policy-fake', 'outside');
  const enoent = () => Object.assign(new Error('ENOENT'), { code: 'ENOENT' });
  const make = (links) => ({
    realpathSync: (p) => {
      for (const [l, t] of Object.entries(links)) if (p === l) return t;
      if ([root, outside, path.join(root, 'sub')].includes(p)) return p;
      throw enoent();
    },
    lstatSync: (p) => {
      if (p in links) return { isSymbolicLink: () => true };
      throw enoent();
    },
  });
  assert.doesNotThrow(() => assertRealInside(root, path.join(root, 'sub', 'a.png'), make({})));
  assert.throws(() => assertRealInside(root, path.join(root, 'sub', 'a.png'), make({ [path.join(root, 'sub')]: outside })), policyError);
});

test('assertRealInside: wiszacy symlink (cel nie istnieje, poza root) => PolicyError (lstat celu zawsze)', { skip: SYMLINK_SKIP }, () => {
  const { base, root, outside } = realTree();
  try {
    fs.mkdirSync(path.join(root, 'out'));
    fs.symlinkSync(path.join(outside, 'nie-istnieje.txt'), path.join(root, 'out', 'capture.txt'), 'file');
    assert.throws(() => assertRealInside(root, path.join(root, 'out', 'capture.txt'), fs), policyError);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});
